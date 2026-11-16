import json
import os
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from fastapi import HTTPException


class ImportServiceError(RuntimeError):
    pass


def load_ark_api_key() -> str:
    key = os.getenv("ARK_API_KEY")
    if key:
        return key
    # 阿里云部署优先使用环境变量（SAE/ECS 加密配置或 KMS 凭据管家注入）。
    # 保留 KMS 凭据名称的预留入口，避免密钥落盘或进入仓库。
    kms_secret_name = os.getenv("ALICLOUD_KMS_SECRET_NAME", "")
    if kms_secret_name:
        try:
            from alibabacloud_secretsmanager20171028.client import Client as SecretsManagerClient
            from alibabacloud_tea_openapi.models import Config
            from alibabacloud_tea_util.models import RuntimeOptions
        except ImportError as exc:
            raise ImportServiceError(
                "已配置 ALICLOUD_KMS_SECRET_NAME，但缺少 KMS SDK；请安装凭据管家依赖，或改用 ARK_API_KEY 环境变量。"
            ) from exc
        try:
            config = Config(
                endpoint=os.getenv("ALICLOUD_KMS_ENDPOINT", "secretsmanager.cn-hangzhou.aliyuncs.com"),
                credential=os.getenv("ALICLOUD_KMS_CREDENTIAL"),
            )
            client = SecretsManagerClient(config)
            response = client.get_secret_value(kms_secret_name, RuntimeOptions())
            value = response.body.secret_data
            if not value:
                raise ImportServiceError("KMS 凭据管家返回的 Ark 密钥为空。")
            return value
        except ImportServiceError:
            raise
        except Exception as exc:
            raise ImportServiceError("无法从阿里云 KMS 凭据管家获取 Ark 密钥，请检查 RAM 角色与 Secret 读取权限。") from exc
    raise ImportServiceError("缺少 Ark 密钥：请配置 ARK_API_KEY 环境变量，或配置 ALICLOUD_KMS_SECRET_NAME。")


class ObjectStorage(Protocol):
    container_name: str

    def put(self, key: str, content: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class ModelProvider(Protocol):
    def complete(self, request: dict, instruction: str) -> str: ...


class VisionProvider(Protocol):
    def recognize(self, images: list[bytes]) -> str: ...


@dataclass
class ImportServices:
    storage: ObjectStorage
    model: ModelProvider
    vision: VisionProvider | None = None


class OssStorage:
    """阿里云 OSS 对象存储适配器，实现与原 Azure Blob 相同的安全契约。

    凭证优先级：ECS RAM 角色（无长期 AK）-> 显式 AccessKey 环境变量。
    """

    def __init__(self, endpoint: str, bucket: str):
        import oss2

        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or parsed.query or parsed.username or not parsed.hostname:
            raise ImportServiceError("OSS Endpoint 必须是不带凭证的有效地址。")
        self._oss2 = oss2
        self.container_name = bucket
        self.bucket_name = bucket
        auth = self._build_auth(oss2)
        self.bucket = oss2.Bucket(auth, endpoint, bucket, connect_timeout=15)
        try:
            # get_bucket_info 需要基础读权限；私有桶不会返回公共访问标记。
            self.bucket.get_bucket_info()
        except oss2.exceptions.OssError as exc:
            if exc.status in {401, 403}:
                raise ImportServiceError("无法访问配置的 OSS Bucket，请检查 RAM 角色及权限。") from exc
            raise ImportServiceError("OSS Bucket 访问失败，请检查 Endpoint、网络与 Bucket 名称。") from exc
        except Exception as exc:
            raise ImportServiceError("OSS 初始化失败，请检查 ECS RAM 角色绑定或 AccessKey 配置。") from exc
        # 阻止公共访问是 Bucket 级开关，创建时已强制私有；这里再确认 ACL。
        try:
            acl = self.bucket.get_bucket_acl().acl
            if acl and acl != oss2.BUCKET_ACL_PRIVATE:
                raise ImportServiceError("导入来源只能保存在私有 OSS Bucket。")
        except ImportServiceError:
            raise
        except oss2.exceptions.OssError:
            # 最小权限策略可能未授予 GetBucketACL，私有性已由创建开关保证，不阻断启动。
            pass

    @staticmethod
    def _build_auth(oss2):
        role_name = os.getenv("ALICLOUD_RAM_ROLE_NAME")
        if role_name:
            # ECS 实例绑定 RAM 角色后，通过实例元数据服务自动获取并轮换临时凭证。
            # EcsRamRoleCredentialsProvider 的参数是完整的元数据 auth_host URL。
            metadata_base = os.getenv("ALICLOUD_METADATA_BASE", "http://100.100.100.200")
            auth_host = f"{metadata_base.rstrip('/')}/latest/meta-data/ram/security-credentials/{role_name}"
            provider = oss2.EcsRamRoleCredentialsProvider(auth_host)
            return oss2.ProviderAuth(provider)
        access_key_id = os.getenv("ALICLOUD_ACCESS_KEY_ID")
        access_key_secret = os.getenv("ALICLOUD_ACCESS_KEY_SECRET")
        if access_key_id and access_key_secret:
            return oss2.Auth(access_key_id, access_key_secret)
        raise ImportServiceError("缺少 OSS 凭证：请绑定 ECS RAM 角色并配置 ALICLOUD_RAM_ROLE_NAME，或提供 AccessKey。")

    def put(self, key: str, content: bytes, content_type: str) -> None:
        try:
            # 禁止覆盖上传，与原 Blob 适配器的 overwrite=False 语义一致。
            self.bucket.put_object(
                key,
                content,
                headers={"Content-Type": content_type, "x-oss-forbid-overwrite": "true"},
            )
        except self._oss2.exceptions.OssError as exc:
            if exc.status in {401, 403}:
                raise ImportServiceError(f"OSS 上传失败（HTTP {exc.status}），请核对 RAM 角色及 Bucket 写权限。") from exc
            raise ImportServiceError(f"OSS 上传失败（HTTP {exc.status}），请检查存储服务状态。") from exc
        except Exception as exc:
            raise ImportServiceError("OSS 上传失败，存储服务未返回 HTTP 状态码；请检查内网连接及写入权限。") from exc

    def delete(self, key: str) -> None:
        try:
            self.bucket.delete_object(key)
        except self._oss2.exceptions.OssError as exc:
            # 对象已不存在视为清理成功，其余 OSS 错误上报人工核对。
            if getattr(exc, "status", None) == 404:
                return
            raise ImportServiceError("OSS 清理失败，需要管理员核对该批次对象。") from exc
        except Exception as exc:
            raise ImportServiceError("OSS 清理失败，需要管理员核对该批次对象。") from exc

    def get(self, key: str) -> bytes:
        try:
            return self.bucket.get_object(key).read()
        except self._oss2.exceptions.OssError as exc:
            status = getattr(exc, "status", None)
            if status == 404:
                raise ImportServiceError("OSS 对象不存在，无法后台识图，请重新导入该批次。") from exc
            raise ImportServiceError(f"OSS 读取失败（HTTP {status}），请检查存储服务状态。") from exc
        except Exception as exc:
            raise ImportServiceError("OSS 读取失败，存储服务未返回 HTTP 状态码；请检查内网连接及读取权限。") from exc


class ArkModelProvider:
    def __init__(self, api_key: str, base_url: str, model: str):
        from openai import OpenAI

        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or parsed.query or parsed.username or not parsed.hostname:
            raise ImportServiceError("模型地址必须是不带凭证的 HTTPS 地址。")
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=90, max_retries=0)
        self.model = model

    def complete(self, request: dict, instruction: str) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
                ],
                response_format={"type": "json_object"},
                max_tokens=8192,
            )
            choice = response.choices[0]
            if choice.finish_reason != "stop" or not choice.message.content:
                raise ImportServiceError("模型输出未完整结束，不能接受截断结果。")
            return choice.message.content
        except ImportServiceError:
            raise
        except Exception as exc:
            raise ImportServiceError("Ark 模型调用失败，请检查服务权限、模型及配额。") from exc


def configured_services() -> ImportServices | None:
    mode = os.getenv("IMPORT_MODE", "preview")
    if mode == "preview":
        return None
    if mode != "integrated":
        raise HTTPException(503, "IMPORT_MODE 必须是 preview 或 integrated。")
    missing = []
    if not os.getenv("OSS_ENDPOINT"):
        missing.append("OSS_ENDPOINT")
    if not os.getenv("OSS_BUCKET"):
        missing.append("OSS_BUCKET")
    if not os.getenv("ARK_API_KEY") and not os.getenv("ALICLOUD_KMS_SECRET_NAME"):
        missing.append("ARK_API_KEY 或 ALICLOUD_KMS_SECRET_NAME")
    if missing:
        raise HTTPException(503, "集成模式缺少配置: " + ", ".join(missing))
    try:
        from .vision import ArkVisionProvider
        key = load_ark_api_key()
        return ImportServices(
            OssStorage(os.environ["OSS_ENDPOINT"], os.environ["OSS_BUCKET"]),
            ArkModelProvider(key, os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3"), os.getenv("ARK_MODEL", "ark-code-latest")),
            ArkVisionProvider(key, os.getenv("ARK_VISION_BASE_URL", os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3")), os.getenv("ARK_VISION_MODEL", os.getenv("ARK_MODEL", "ark-code-latest"))),
        )
    except ImportServiceError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ImportError as exc:
        raise HTTPException(503, "集成模式缺少云服务 SDK，请安装后端依赖（oss2）。") from exc