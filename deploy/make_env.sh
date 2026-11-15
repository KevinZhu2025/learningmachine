#!/usr/bin/env bash
set -euo pipefail
ENV_FILE=/opt/learningmachine/deploy/learningmachine.env
cp /opt/learningmachine/deploy/learningmachine.env.example "$ENV_FILE"
SECRET="$(/opt/learningmachine/.venv/bin/python -c 'import secrets;print(secrets.token_urlsafe(48))')"
sed -i "s|^SESSION_CSRF_SECRET=.*|SESSION_CSRF_SECRET=${SECRET}|" "$ENV_FILE"
sed -i 's|^IMPORT_MODE=.*|IMPORT_MODE=preview|' "$ENV_FILE"
chmod 600 "$ENV_FILE"
grep -E 'SESSION_CSRF_SECRET|IMPORT_MODE|APP_ENV' "$ENV_FILE"
