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
    vault_url = os.getenv("AZURE_KEY_VAULT_URL", "").rstrip("/")
    secret_name = os.getenv("ARK_KEY_VAULT_SECRET", "")
    parsed = urlsplit(vault_url)
    if parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(".vault.azure.net") or parsed.username or parsed.query or parsed.path:
        raise ImportServiceError("Key Vault 地址必须是不带凭证的 Azure Vault HTTPS 地址。")
    if not secret_name:
        raise ImportServiceError("缺少 Ark Key Vault Secret 名称。")
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    try:
        with SecretClient(vault_url, DefaultAzureCredential(process_timeout=60)) as client:
            value = client.get_secret(secret_name).value
        if not value:
            raise ImportServiceError("Key Vault 的 Ark Secret 没有可用值。")
        return value
    except ImportServiceError:
        raise
    except Exception as exc:
        raise ImportServiceError("无法从 Key Vault 获取 Ark 密钥，请检查 Azure 身份和 Secret 读取权限。") from exc


class ObjectStorage(Protocol):
    container_name: str

    def put(self, key: str, content: bytes, content_type: str) -> None: ...
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


class AzureBlobStorage:
    def __init__(self, account_url: str, container: str, connection_string: str | None = None):
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        if connection_string:
            service = BlobServiceClient.from_connection_string(connection_string)
        else:
            parsed = urlsplit(account_url)
            if parsed.scheme != "https" or parsed.query or parsed.username or not parsed.hostname:
                raise ImportServiceError("Blob 账号地址必须是不带凭证的 HTTPS 地址。")
            service = BlobServiceClient(account_url, credential=DefaultAzureCredential(process_timeout=60))
        self.container_name = container
        self.container = service.get_container_client(container)
        try:
            properties = self.container.get_container_properties(timeout=15)
        except Exception as exc:
            raise ImportServiceError("无法访问配置的 Blob 容器，请检查身份及权限。") from exc
        if properties.get("public_access"):
            raise ImportServiceError("导入来源只能保存在私有 Blob 容器。")

    def put(self, key: str, content: bytes, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        try:
            self.container.upload_blob(key, content, overwrite=False, timeout=30, content_settings=ContentSettings(content_type=content_type))
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            if status_code in {401, 403}:
                raise ImportServiceError(f"Blob 上传失败（HTTP {status_code}），请核对身份认证及容器写入权限。") from exc
            if isinstance(status_code, int):
                raise ImportServiceError(f"Blob 上传失败（HTTP {status_code}），请检查存储服务状态。") from exc
            raise ImportServiceError("Blob 上传失败，存储服务未返回 HTTP 状态码；请检查连接及写入权限。") from exc

    def delete(self, key: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            self.container.delete_blob(key, timeout=15)
        except ResourceNotFoundError:
            return
        except Exception as exc:
            raise ImportServiceError("Blob 清理失败，需要管理员核对该批次对象。") from exc


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
    if not os.getenv("AZURE_STORAGE_ACCOUNT_URL") and not os.getenv("AZURE_STORAGE_CONNECTION_STRING"):
        missing.append("AZURE_STORAGE_ACCOUNT_URL 或 AZURE_STORAGE_CONNECTION_STRING")
    for name in ("AZURE_STORAGE_CONTAINER",):
        if not os.getenv(name):
            missing.append(name)
    if not os.getenv("ARK_API_KEY") and not (os.getenv("AZURE_KEY_VAULT_URL") and os.getenv("ARK_KEY_VAULT_SECRET")):
        missing.append("ARK_API_KEY 或 Key Vault 配置")
    if missing:
        raise HTTPException(503, "集成模式缺少配置: " + ", ".join(missing))
    try:
        from .vision import ArkVisionProvider
        key = load_ark_api_key()
        return ImportServices(
            AzureBlobStorage(os.getenv("AZURE_STORAGE_ACCOUNT_URL", ""), os.environ["AZURE_STORAGE_CONTAINER"], os.getenv("AZURE_STORAGE_CONNECTION_STRING")),
            ArkModelProvider(key, os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3"), os.getenv("ARK_MODEL", "ark-code-latest")),
            ArkVisionProvider(key, os.getenv("ARK_VISION_BASE_URL", os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3")), os.getenv("ARK_VISION_MODEL", os.getenv("ARK_MODEL", "ark-code-latest"))),
        )
    except ImportServiceError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ImportError as exc:
        raise HTTPException(503, "集成模式缺少云服务 SDK，请安装后端依赖。") from exc