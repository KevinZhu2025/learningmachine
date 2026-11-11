#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${AZURE_SQL_SERVER:-}${DATABASE_URL:-}" ]]; then
  if ! python -c 'import pyodbc; raise SystemExit("ODBC Driver 18 for SQL Server" not in pyodbc.drivers())'; then
    if [[ "$(id -u)" != "0" ]]; then
      echo "ODBC Driver 18 is missing; provision it in the runtime image." >&2
      exit 1
    fi
    . /etc/os-release
    if [[ "$ID" != "debian" && "$ID" != "ubuntu" ]]; then
      echo "Automatic ODBC provisioning supports Debian and Ubuntu only." >&2
      exit 1
    fi
    distribution_version="$VERSION_ID"
    if [[ "$ID" == "debian" ]]; then
      distribution_version="${VERSION_ID%%.*}"
    fi
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ca-certificates
    repository_package="$(mktemp --suffix=.deb)"
    trap 'rm -f "$repository_package"' EXIT
    curl --fail --silent --show-error --location \
      "https://packages.microsoft.com/config/$ID/$distribution_version/packages-microsoft-prod.deb" \
      --output "$repository_package"
    dpkg -i "$repository_package"
    apt-get update -qq
    ACCEPT_EULA=Y DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends msodbcsql18
    python -c 'import pyodbc; assert "ODBC Driver 18 for SQL Server" in pyodbc.drivers()'
  fi
fi

exec python -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port "${PORT:-8000}"