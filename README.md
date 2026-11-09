# LearningMachine

AI 学习助手：知识导入与整合 MVP。

## 当前开发范围

- React + TypeScript + Fluent UI 的前端（包含 PWA manifest）。
- FastAPI 后端提供导入批次、暂存编辑及来源查询；首期仅暂存，不进行人工审批或写入正式知识库/题库。
- 集成模式已实现私有 Azure Blob 保存、Ark 请求组装/响应校验、请求与响应审计、知识/题目暂存及失败重试。文本解析支持 TXT、Markdown、DOCX 和文本 PDF；PNG/JPEG/WEBP 及扫描或混合 PDF 使用通用多模态模型加转写提示词，后端整理识别片段。
- 暂存编辑支持标题、学科、章节、正文和 Header 题目数组，使用版本冲突检测；批准/拒绝和确认正式入库返回 `409`。暂存完成时批次为 `COMPLETED`，记录仍为 `PENDING`；预留 `KnowledgeID` 不表示正式知识已经创建。
- “配置管理”可筛选、新增和编辑本地 `PromptLeading`；时间按 UTC 展示，新增时由服务端生成且不可修改，用户留空表示共享。保存时检测并发修改；已保存的导入提示词快照不随配置修改。
- 本地预览模式用于验证界面与端到端流程。识别结果和生成提示词均为明确标记的预览内容，不代表真实大模型处理。
- 本地可导入 TXT、Markdown、DOCX、文本 PDF、纯文本及公开网页链接；网页抓取具有 SSRF 防护和大小/超时限制，导入 API 使用用户范围的幂等键防止重复批次。
- 数据库支持 SQLite 和受控 Azure SQL 连接；Blob/Ark 适配器已实现且替身闭环测试通过。真实云端端到端联调仍需本机 Azure 登录、权限和 Ark 密钥；这不等于真实服务已经验证。

### 本地暂存结构同步

2026-11-08 已同步 SQLite 迁移、后端 ORM 和接口：新增版本、资料类别、用户、JSON 题目数组及 AI 响应关联字段；知识专用字段可空，支持只有题目的承载记录。接口返回 `questionItemsJson` / `optionsJson` 为数组、`associatedImage` / `aiProposal` 为对象或空值；未知置信度及知识空列返回 JSON `null`，不返回字符串 `Null`。

- `Version` 是知识版本，默认 `0`；`versionNo` 仍是暂存编辑并发版本，从 `1` 起。
- `ImportItem.sourceType` 表示识别的资料类别；本地预览未调用模型，使用 `UNKNOWN`。`ImportSource.sourceType` 仍为文件/网页/手工来源类型。
- SQLite 使用 `KnowledgeIDCounter` 原子预分配编号，并保留已有编号；云端使用已建的 `KnowledgeIDSequence`。仅题目记录不取知识编号。
- 已有 SQLite 在启动时事务迁移，回填批次用户、缺失知识编号并校验外键；有数据但缺少必要来源或存在未映射字段时停止，不编造来源。已同步根目录和 `backend/.data` 的两个开发库，迁移前备份保存在各自 `.data/backups/`。
- 修改同份接受结果的 `questionItemsJson` 会在事务内同步副本并更新 `versionNo`；历史审核记录不允许被此接口修改。客户端不能指定候选所有者，题目来源须属于当前批次。
- 前端已移除首期批准/拒绝操作，支持知识空值、去重题目展示和失败集成任务重试。默认 `IMPORT_MODE=preview` 保留本地文本预览；必须显式配置集成模式才会调用 Blob/Ark，缺配置返回 `503`，不回退为预览。

### 集成模式配置与验证

**最新验证（2026-11-08）：** Azure 交互登录/MFA 已完成；后端已通过 Azure 身份读取 `https://mpwebpage.vault.azure.net` 的 `ark-api-key`，密钥未输出或保存到文件。Python 到 MEMPALACE 的身份连接和 `math` 私有容器访问通过。`ark-code-latest` 在现有 Coding Plan 地址已通过一次合成图片真实识字测试；这不表示所有材料或复杂表格的识别精度均已验收。

实际本机 `backend/.env` 已配置集成模式、Vault 标识与视觉模型，不含密钥；`AZURE_KEY_VAULT_URL` / `ARK_KEY_VAULT_SECRET` 可替代 `ARK_API_KEY`。仍支持环境密钥覆盖，但不要把密钥发送到聊天或提交仓库。Windows Azure CLI 冷启动可能超过默认身份进程超时，已设置 60 秒并验证读取成功。

视觉路径先安全读取图片或用 PyMuPDF 渲染扫描/混合 PDF，再将图像发送到 `ARK_VISION_BASE_URL` / `ARK_VISION_MODEL`（默认复用现有模型）。识别响应保存到私有 Blob，引用在 `ImportSource.OcrResultObjectKey` / `OcrResultContainerName`；后端生成稳定片段 UUID、真实页码和阅读区块顺序，未知像素定位保持空值，后续知识请求只发送文本片段。

识图单次限制 10 页/15 MB，单图限制 2000 万像素，返回超限/页码错误/低置信度或无法辨认内容时拒绝继续。通用模型转写不保证与专用 OCR 完全等同的准确度；仍需以清晰原材料及错误/置信度提示为准。

非敏感配置模板见 [backend/.env.example](backend/.env.example)。本轮确认目标：Azure SQL `MEMPALACE`，Blob 账号 `https://mempalacewh.blob.core.windows.net`、容器 `math`；Ark 基础地址 `https://ark.cn-beijing.volces.com/api/plan/v3`，模型 `ark-code-latest`。该 Ark 地址是 Coding Plan 模型端点，不是专用 OCR 接口；真实接口对 JSON 输出参数的兼容性仍需有密钥后联调。

1. 后端使用 `AZURE_SQL_SERVER` / `AZURE_SQL_DATABASE` / `AZURE_SQL_AUTH=default` 及 Azure 默认身份；在本机完成 Azure 登录或应用身份配置，并授权访问 SQL、私有 Blob 容器。编辑器 MSSQL 的保存连接不等于 Python 已获认证。
2. 云端已执行 [Migration-20261108-ImportPipeline.sql](Migration-20261108-ImportPipeline.sql)，补齐 `VersionNo`、历史快照、请求/响应对象引用及来源解析片段。应用连接云端时只检查字段，不自动执行 DDL；本地 SQLite 可安全执行启动迁移。
3. `IMPORT_MODE=integrated` 启用真实服务。`ARK_API_KEY` 仅在本机环境或被忽略的配置文件设置，不能发到聊天或提交仓库；Blob 使用默认身份，也支持本机 `AZURE_STORAGE_CONNECTION_STRING`。模板不含密钥。后端可用 `uvicorn app.main:app --env-file .env` 加载本机配置。
4. Blob 适配器检查容器私有性，不创建资源、不生成公开 URL/SAS。原始文件先保存并建立来源；后续解析/模型失败仍保留已接收来源。未成功持久化来源的对象执行清理；服务清理失败需要管理员核对。
5. 每个来源保存稳定片段 UUID 和原文，未知页码/区块为 `null`。请求包括批次、会话用户、用户说明、选定提示词历史/哈希及来源片段；默认任务指令由服务端提供，不写入用户说明。
6. 非 General 课程加非空说明时先生成课程提示词，再固定抽取快照；请求/响应分别保存到私有 Blob 审计对象。输出严格校验 Schema、ID、来源覆盖、原文证据、解析版本和局部知识引用；非法结果不写候选，知识与 Header 题目数组在事务中暂存，不写正式表。
7. `POST /api/imports/{batch_id}/retry` 仅允许已解析来源的失败集成批次；普通重试复用说明、片段和抽取提示词快照，用新请求 ID，不重复已接受结果。尚未解析的扫描件不能直接重试模型。

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

知识导入请求由前端自动携带幂等键。公开网页首期只支持不需登录的 HTTP/HTTPS 页面；不支持登录墙、脚本浏览器渲染、扫描件 OCR 或图片识别。文件和网页目前仅在本地私有目录预览，并未接入 Azure Blob。

生产环境必须设置 `APP_ENV`（非 `development`）、`ADMIN_USER_ID`、至少 32 字节的 `SESSION_CSRF_SECRET` 和逗号分隔的 `ALLOWED_ORIGINS`。生产管理员账户不允许公开注册；部署完成数据库迁移并配置连接后，在后端运行 `python -m app.bootstrap_admin` 交互式创建管理员。生产会话 Cookie 自动启用 `Secure`。未完成 HTTPS、数据库迁移及生产环境检查前，不要将服务部署到公网。

FastAPI 已支持 Azure SQL 环境连接和默认身份令牌，并只读核验 `MEMPALACE` 的认证/导入所需字段；本轮兼容迁移已补齐云端 `VersionNo`，与知识 `Version` 分开。当前 Python Azure 默认身份检查未通过，尚未执行真实 SQL/Blob/Ark 端到端样本；必须配置身份/权限后验证，生产配置不代表部署已就绪。

## 云端接入前置

Azure CLI 安装在 D 盘，但 Azure 尚未登录。后续云端资源创建/部署前，需重新登录并核对目标订阅、资源组、SQL 数据库，以及成本、区域和网络设置。
