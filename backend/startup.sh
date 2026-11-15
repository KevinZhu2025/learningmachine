#!/usr/bin/env bash
# 本地/容器直接启动入口。生产 ECS 由 deploy/deploy.sh 预装 ODBC Driver 18，
# 并通过 systemd（deploy/learningmachine.service）管理进程，不再在启动时联网 apt 安装。
set -euo pipefail

if [[ -n "${DATABASE_URL:-}${SQL_SERVER:-}" ]]; then
  if ! python -c 'import pyodbc; raise SystemExit("ODBC Driver 18 for SQL Server" not in pyodbc.drivers())' 2>/dev/null; then
    echo "ODBC Driver 18 for SQL Server 缺失；请在镜像或主机预装 msodbcsql18（参见 deploy/deploy.sh）。" >&2
    exit 1
  fi
fi

exec python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port "${PORT:-8000}"