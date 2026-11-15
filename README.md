# LearningMachine

AI 学习助手：知识导入与整合 MVP。

## 当前开发范围

### 文件格式与运行模式

- 2026-11-13 起目标平台由 Azure App Service 迁移到阿里云 ECS（华东1-杭州，Ubuntu 22.04）。生产通过 `deploy/deploy.sh` 预装 ODBC Driver 18，由 systemd 托管 uvicorn、Nginx 反向代理；GitHub Actions 经 SSH 部署。本地仍可用 `bash backend/startup.sh` 启动。
- 集成 `/api/health` 会验证 RDS `LearningMachineDB` 连接、Ark 密钥来源和私有 OSS Bucket 访问；返回 503 表示依赖未就绪。健康检查不请求 Ark 模型，200 不代表所有材料的识别精度或完整导入验收。
- 图片：JPG/JPEG（含大写扩展名）、PNG、WEBP、BMP、单帧 GIF 和 TIFF；按真实编码解码校验，不仅检查扩展名。多帧图片应拆页或转 PDF，防止静默丢帧。
- 文档：TXT、Markdown、DOCX（含表格）、PDF；DOC、PPT、PPTX 经 LibreOffice 转成 PDF 后解析，扫描页面在集成模式进入多模态识别。
- Office 转换服务器须安装 LibreOffice（Linux 通常为 `libreoffice-impress` 和 `libreoffice-writer`），确保 `soffice` 在 PATH 中，或设置 `LIBREOFFICE_PATH` 为可执行文件路径。转换使用独立临时目录、60 秒超时及转换结果 15 MB 上限。仅安装 Python 依赖不会安装 LibreOffice；App Service 需准备包含该系统依赖的运行环境。缺少转换器时明确报配置错误，不声称格式本身不支持。
- `IMPORT_MODE=preview` 不调用识图或知识抽取模型：有效图片/扫描 PDF 可以接收并保存来源，状态为 `UPLOADED`，提示待识别，不生成图片的虚构知识。要实际提取图片知识，仍需配置数据库、私有 Blob、模型/Key Vault 访问并启用 `integrated`；本次代码改动不自动更改云端模式或授予权限。
- 网页链接可独立提交，无需同时填写文字或附件；仍保留公网地址校验和 SSRF 防护。

- React + TypeScript + Fluent UI 的前端（包含 PWA manifest）。
- FastAPI 后端提供导入批次、暂存编辑及来源查询；首期仅暂存，不进行人工审批或写入正式知识库/题库。
- 集成模式已实现私有阿里云 OSS 保存、Ark 请求组装/响应校验、请求与响应审计、知识/题目暂存及失败重试。文本解析支持 TXT、Markdown、DOCX 和文本 PDF；PNG/JPEG/WEBP 及扫描或混合 PDF 使用通用多模态模型加转写提示词，后端整理识别片段。
- 暂存编辑支持标题、学科、章节、正文和 Header 题目数组，使用版本冲突检测；批准/拒绝和确认正式入库返回 `409`。暂存完成时批次为 `COMPLETED`，记录仍为 `PENDING`；预留 `KnowledgeID` 不表示正式知识已经创建。
- “配置管理”可筛选、新增和编辑本地 `PromptLeading`；时间按 UTC 展示，新增时由服务端生成且不可修改，用户留空表示共享。保存时检测并发修改；已保存的导入提示词快照不随配置修改。
- 本地预览模式用于验证界面与端到端流程。识别结果和生成提示词均为明确标记的预览内容，不代表真实大模型处理。
- 本地可导入 TXT、Markdown、DOCX、文本 PDF、纯文本及公开网页链接；网页抓取具有 SSRF 防护和大小/超时限制，导入 API 使用用户范围的幂等键防止重复批次。
- 数据库支持 SQLite 与受控 SQL Server（含阿里云 RDS）连接；OSS/Ark 适配器已实现且替身闭环测试通过。真实云端端到端联调需在 ECS 上配置 RDS 白名单、RAM 角色和 Ark 密钥；这不等于真实服务已经验证。

### 本地暂存结构同步

2026-11-08 已同步 SQLite 迁移、后端 ORM 和接口：新增版本、资料类别、用户、JSON 题目数组及 AI 响应关联字段；知识专用字段可空，支持只有题目的承载记录。接口返回 `questionItemsJson` / `optionsJson` 为数组、`associatedImage` / `aiProposal` 为对象或空值；未知置信度及知识空列返回 JSON `null`，不返回字符串 `Null`。

- `Version` 是知识版本，默认 `0`；`versionNo` 仍是暂存编辑并发版本，从 `1` 起。
- `ImportItem.sourceType` 表示识别的资料类别；本地预览未调用模型，使用 `UNKNOWN`。`ImportSource.sourceType` 仍为文件/网页/手工来源类型。
- SQLite 使用 `KnowledgeIDCounter` 原子预分配编号，并保留已有编号；云端使用已建的 `KnowledgeIDSequence`。仅题目记录不取知识编号。
- 已有 SQLite 在启动时事务迁移，回填批次用户、缺失知识编号并校验外键；有数据但缺少必要来源或存在未映射字段时停止，不编造来源。已同步根目录和 `backend/.data` 的两个开发库，迁移前备份保存在各自 `.data/backups/`。
- 修改同份接受结果的 `questionItemsJson` 会在事务内同步副本并更新 `versionNo`；历史审核记录不允许被此接口修改。客户端不能指定候选所有者，题目来源须属于当前批次。
- 前端已移除首期批准/拒绝操作，支持知识空值、去重题目展示和失败集成任务重试。默认 `IMPORT_MODE=preview` 保留本地文本预览；必须显式配置集成模式才会调用 Blob/Ark，缺配置返回 `503`，不回退为预览。

### 集成模式配置与验证

目标云资源（华东1-杭州）：RDS SQL Server 数据库 `LearningMachineDB`（内网端口 3433）、私有 OSS Bucket `learningmachineoss`（ECS 经 RAM 角色 `LearningMachineRAM` 访问内网 Endpoint）、火山方舟 Ark（第三方服务，保持不变）。

非敏感配置模板见 [backend/.env.example](backend/.env.example)，服务器运行模板见 [deploy/learningmachine.env.example](deploy/learningmachine.env.example)，二者均不含密钥。

1. 数据库：生产通过 `DATABASE_URL` 提供完整连接串，或用 `SQL_SERVER`/`SQL_SERVER_PORT`/`SQL_SERVER_USER`/`SQL_SERVER_PASSWORD`/`DATABASE_NAME` 分项配置；应用使用 SQL 账号密码，不再依赖任何 Azure AD 令牌。RDS 白名单仅放通 ECS 私网地址。
2. 建表：新空库按 `TableCreation.sql` → `Migration-20261108-ImportItem.sql` → `Migration-20261108-ImportPipeline.sql` 顺序在 `LearningMachineDB` 内执行；应用启动只校验 dbo schema 与字段，不自动执行云端 DDL。
3. 最小权限：执行 [Rds-Grant-LearningMachineDB.sql](Rds-Grant-LearningMachineDB.sql) 创建应用账号并授权（导入相关表读写、KnowledgeBase 只读、序列取号），不授予 db_owner。
4. 对象存储：`OSS_ENDPOINT`（ECS 用 `oss-cn-hangzhou-internal.aliyuncs.com`）+ `OSS_BUCKET` + `ALICLOUD_RAM_ROLE_NAME`。适配器校验私有性、禁止覆盖上传、错误脱敏，不创建资源、不生成公开 URL。
5. Ark 密钥：优先用环境变量 `ARK_API_KEY`（由 systemd EnvironmentFile 注入），或配置 `ALICLOUD_KMS_SECRET_NAME` 走凭据管家；密钥不入库、不入 Git、不输出到日志。
6. `IMPORT_MODE=integrated` 启用真实服务，缺少 OSS 或密钥配置时返回 503，不静默回退预览。

当前单请求输入限制为 60000 字符，超过时明确要求拆批，不静默截断；模型输出限制 2 MB 和 8192 token，截断输出拒绝接受。尚未实现持久后台任务/断点恢复、自动大文档分块或大规模真实材料验收；此前的“密钥/身份未配置、扫描件尚未接入”说明已被上述最新验证取代。

## 模块设计文档

- [知识导入与整合流程](./知识导入与整合流程模块设计.md)
- [知识库管理](./知识库管理模块设计.md)
- [试卷分析与题库](./试卷分析与题库模块设计.md)
- [语音训练与答案评分](./语音训练与答案评分模块设计.md)
- [学习计划与复习](./学习计划与复习模块设计.md)
- [平台基础与云端服务接入](./平台基础与云端服务接入模块设计.md)

## 启动

首次准备 Node.js 24 LTS（便携版已安装到 `D:\Tools\node-v24.22.2-win-x64`）并创建 Python 环境：

```powershell
cd frontend
npm install
npm run dev
```

另开 PowerShell：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

访问 Vite 地址（默认 `http://127.0.0.1:5173`）。本地 API 默认将 SQLite 文件和私有上传文件保存在 `backend/.data/`。

本地开发支持用户名/密码注册与登录。密码由 Argon2id 哈希后保存；浏览器使用 HttpOnly 会话 Cookie，写请求校验 CSRF token。每个用户只能访问自己的导入数据和提示词；普通用户不能写入共享提示词，`ADMIN_USER_ID` 对应的管理员负责共享及 General 提示词。

知识导入请求由前端自动携带幂等键。公开网页首期只支持不需登录的 HTTP/HTTPS 页面；不支持登录墙、脚本浏览器渲染、扫描件 OCR 或图片识别。本地预览模式文件保存在私有目录；集成模式文件保存到私有 OSS Bucket。

生产环境必须设置 `APP_ENV`（非 `development`）、`ADMIN_USER_ID`、至少 32 字节的 `SESSION_CSRF_SECRET` 和逗号分隔的 `ALLOWED_ORIGINS`。生产管理员账户不允许公开注册；部署完成数据库迁移并配置连接后，在后端运行 `python -m app.bootstrap_admin` 交互式创建管理员。生产会话 Cookie 自动启用 `Secure`。未完成 HTTPS、数据库迁移及生产环境检查前，不要将服务部署到公网。

应用支持通过 `DATABASE_URL` 连接阿里云 RDS SQL Server，并只读核验 `LearningMachineDB` 的认证/导入所需字段；应用启动不自动执行云端 DDL，需先按迁移脚本建表。

## 阿里云 ECS 部署

1. 资源准备：ECS（Ubuntu 22.04，杭州，绑定 RAM 角色 `LearningMachineRAM`）、RDS SQL Server（白名单放通 ECS 私网地址）、私有 OSS Bucket `learningmachineoss`，三者同一 VPC。
2. 在 RDS 内按顺序执行建表/迁移脚本，再执行 `Rds-Grant-LearningMachineDB.sql` 建立最小权限应用账号。
3. 服务器上复制 `deploy/learningmachine.env.example` 为 `deploy/learningmachine.env`，填入连接串、`SESSION_CSRF_SECRET`、`ALLOWED_ORIGINS` 和 `ARK_API_KEY`（密钥不入库）。
4. 首次手工部署：`sudo DEPLOY_PATH=/opt/learningmachine APP_USER=ubuntu bash deploy/deploy.sh`，脚本安装 ODBC Driver 18、Python 依赖、systemd 服务与 Nginx 配置。
5. 管理员初始化：在部署目录执行 `cd /opt/learningmachine/backend && sudo -u ubuntu ../.venv/bin/python -m app.bootstrap_admin`（交互输入 12–128 位密码，依赖环境文件中的 `ADMIN_USER_ID`）。
6. 自动化：配置 GitHub Secrets（`ALICLOUD_ECS_HOST`、`ALICLOUD_ECS_USER`、`ALICLOUD_ECS_SSH_KEY`、`ALICLOUD_DEPLOY_PATH`），推送到 main 即构建、测试并 SSH 部署。
7. 大陆地域绑定自定义域名前需完成 ICP 备案；备案完成后在 Nginx 增加 443 配置并将 80 跳转 HTTPS，页脚悬挂备案号。
