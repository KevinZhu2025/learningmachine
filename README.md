# LearningMachine

AI 学习助手：知识导入与整合 MVP。

## 当前开发范围

- React + TypeScript + Fluent UI 的前端（包含 PWA manifest）。
- FastAPI 后端，提供导入批次、候选审核、确认入库和知识来源查询接口。
- “配置管理”可筛选、新增和编辑本地 `PromptLeading`；时间按 UTC 展示和编辑，新增时可留空由数据库生成，用户留空表示共享。保存时检测并发修改；已保存的导入提示词快照不随配置修改。
- 本地预览模式用于验证界面与端到端流程。识别结果和生成提示词均为明确标记的预览内容，不代表真实大模型处理。
- 本地开发数据库为 SQLite；Azure SQL、Blob Storage、Entra ID、火山方舟模型/OCR 尚未接入。

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

开发身份固定为 `demo-user`，仅供本地预览，不是认证实现；不要将当前模式部署到公网或生产环境。
本地配置页可以查看和修改本地库中所有用户及共享提示词；生产接入前必须实现身份认证及用户、共享和管理员配置的授权控制。

## 云端接入前置

Azure CLI 安装在 D 盘，但 Azure 尚未登录。VS Code MSSQL 扩展的 Azure SQL 访问令牌曾过期，需重新登录并核对目标订阅、`learningmachine-dev` 资源组及 SQL 数据库。任何云端创建/部署前需确认成本、区域和网络设置。
