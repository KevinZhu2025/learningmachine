# LearningMachine 阿里云迁移执行 Runbook

本文档记录从 Azure App Service 迁移到阿里云 ECS/RDS/OSS 的执行步骤，按顺序操作。

## 1. 已就绪的云资源

| 资源 | 值 |
|---|---|
| ECS | 华东1（杭州）可用区 B，Ubuntu 22.04，EIP `8.136.251.167`，私网 `172.16.244.52`，实例 `HaoWanTech` |
| RDS SQL Server | `rm-bp14dc5e96x6k826w.sqlserver.rds.aliyuncs.com:3433`，库 `LearningMachineDB`，VPC `vpc-bp1g7onzr73zflckg1niv`（172.16.0.0/12） |
| OSS | 私有 Bucket `learningmachineoss`，内网 Endpoint `oss-cn-hangzhou-internal.aliyuncs.com`，已开启阻止公共访问 |
| RAM 角色 | `LearningMachineRAM`（ECS 可信实体），策略 `LearningMachineOssPolicy` 仅授权该 Bucket |

## 2. RDS 建表与授权（空库冷启动）

1. RDS 控制台 → 数据库管理，确认 `LearningMachineDB` 排序规则为 `Chinese_PRC_CI_AS`。
2. 用高权限账号通过 DMS 连接到 `LearningMachineDB`，按顺序执行：
   - `TableCreation.sql`
   - `Migration-20261108-ImportItem.sql`
   - `Migration-20261108-ImportPipeline.sql`
3. 执行 `Rds-Grant-LearningMachineDB.sql`，先把脚本中的占位密码替换为强密码，创建应用账号 `learningmachine_app` 并授予最小权限。
4. 白名单只保留 ECS 私网 `172.16.244.52/32`，不开通外网地址。

## 3. ECS 首次手工部署

```bash
# 在本机把代码打包上传（或通过 git clone）
scp -i <密钥> learningmachine.tar.gz ubuntu@8.136.251.167:/tmp/
ssh -i <密钥> ubuntu@8.136.251.167
sudo mkdir -p /opt/learningmachine
sudo tar -xzf /tmp/learningmachine.tar.gz -C /opt/learningmachine
sudo DEPLOY_PATH=/opt/learningmachine APP_USER=ubuntu bash /opt/learningmachine/deploy/deploy.sh
```

部署脚本会安装 ODBC Driver 18、Python 虚拟环境、systemd 服务与 Nginx 配置。

## 4. 配置环境变量

```bash
sudo cp /opt/learningmachine/deploy/learningmachine.env.example /opt/learningmachine/deploy/learningmachine.env
sudo chmod 600 /opt/learningmachine/deploy/learningmachine.env
sudo nano /opt/learningmachine/deploy/learningmachine.env
# 必填：DATABASE_URL（密码 URL 编码）、SESSION_CSRF_SECRET、ALLOWED_ORIGINS、ARK_API_KEY
# 生成密钥：/opt/learningmachine/.venv/bin/python -c "import secrets;print(secrets.token_urlsafe(48))"
sudo systemctl restart learningmachine
```

初始化管理员：

```bash
cd /opt/learningmachine/backend
sudo -u ubuntu ../.venv/bin/python -m app.bootstrap_admin
```

## 5. 验证

```bash
systemctl status learningmachine
curl -s http://127.0.0.1:8000/api/health
# ECS 内验证 RAM 角色
curl "http://100.100.100.200/latest/meta-data/ram/security-credentials/LearningMachineRAM"
```

- `IMPORT_MODE=preview` 下 health 应返回 `{"status":"ok","mode":"preview"}`。
- 切到 `integrated` 前，确认 RDS、OSS（RAM 角色）、Ark 密钥三项均通，health 返回 200 且 mode=integrated；503 时查看 `journalctl -u learningmachine -f`。

## 6. 自动化部署

GitHub 仓库 Settings → Secrets and variables → Actions 添加：

| Secret | 值 |
|---|---|
| `ALICLOUD_ECS_HOST` | `8.136.251.167` |
| `ALICLOUD_ECS_USER` | `ubuntu` |
| `ALICLOUD_ECS_SSH_KEY` | SSH 私钥完整内容 |
| `ALICLOUD_DEPLOY_PATH` | `/opt/learningmachine` |

推送到 main 即自动构建、测试、打包并 SSH 部署。

## 7. 域名与备案

杭州属大陆地域，绑定自定义域名前完成 ICP 备案；备案号下发后在 Nginx 增加 443 server 块、80 跳转 HTTPS，更新 `ALLOWED_ORIGINS`，前端页脚悬挂备案号。

## 8. 回滚

Azure 旧资源（App Service、Azure SQL、Blob、Key Vault）建议并行保留 2–4 周。回滚时把 DNS/流量切回 Azure，并暂停 GitHub Actions 的 ECS 部署任务；RDS 与 Azure SQL 在并行期不做双向同步，回滚以 Azure 侧数据为准，切换窗口后产生的新数据需人工核对。
