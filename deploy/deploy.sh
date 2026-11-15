#!/usr/bin/env bash
# 阿里云 ECS（Ubuntu 22.04）部署脚本：安装系统依赖、Python 依赖并重启服务。
# 由 GitHub Actions 通过 SSH 以 sudo 调用，也可手工在服务器执行。
set -euo pipefail

DEPLOY_PATH="${DEPLOY_PATH:-/opt/learningmachine}"
APP_USER="${APP_USER:-ubuntu}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "==> 部署目录: $DEPLOY_PATH"
cd "$DEPLOY_PATH"

# 1. 系统依赖：ODBC Driver 18（连接 RDS SQL Server）。
# 检测：若 pyodbc 无法加载或驱动列表不含 ODBC Driver 18，则安装。
if ! "$PYTHON_BIN" -c 'import pyodbc; assert "ODBC Driver 18 for SQL Server" in pyodbc.drivers()' 2>/dev/null; then
  echo "==> 安装 Microsoft ODBC Driver 18"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y --no-install-recommends curl ca-certificates gnupg apt-transport-https
  if ! grep -q "packages.microsoft.com" /etc/apt/sources.list /etc/apt/sources.list.d/* 2>/dev/null; then
    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg
    echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/ubuntu/22.04/prod jammy main" \
      > /etc/apt/sources.list.d/mssql-release.list
    apt-get update -qq
  fi
  ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18
fi

# 2. Python 虚拟环境与依赖。
echo "==> 安装 Python 依赖"
if [[ ! -d "$DEPLOY_PATH/.venv" ]]; then
  "$PYTHON_BIN" -m venv "$DEPLOY_PATH/.venv"
fi
"$DEPLOY_PATH/.venv/bin/python" -m pip install --upgrade pip
"$DEPLOY_PATH/.venv/bin/pip" install -r "$DEPLOY_PATH/requirements.txt"

# 3. 环境变量文件由运维单独维护（不进 Git、不被部署覆盖）。
ENV_FILE="$DEPLOY_PATH/deploy/learningmachine.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$DEPLOY_PATH/deploy/learningmachine.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "==> 已从模板生成 $ENV_FILE，请填入 RDS/OSS/Ark 真实配置后重启服务"
fi

# 4. 安装 systemd 服务并重启。
echo "==> 安装 systemd 服务"
install -m 644 "$DEPLOY_PATH/deploy/learningmachine.service" /etc/systemd/system/learningmachine.service
systemctl daemon-reload
systemctl enable learningmachine
systemctl restart learningmachine
sleep 2
systemctl --no-pager --full status learningmachine | head -n 15

# 5. Nginx 反代配置（首次安装）。
if command -v nginx >/dev/null 2>&1; then
  install -m 644 "$DEPLOY_PATH/deploy/nginx-learningmachine.conf" /etc/nginx/sites-available/learningmachine.conf
  ln -sf /etc/nginx/sites-available/learningmachine.conf /etc/nginx/sites-enabled/learningmachine.conf
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl reload nginx
else
  echo "==> 提示：未安装 nginx，可执行 apt-get install -y nginx 后重跑本脚本"
fi

echo "==> 部署完成"
