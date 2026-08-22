#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${APP_DIR:-"/srv/quickstock/current/Website/BackEnd/quickstock"}
PYTHON_BIN=${PYTHON_BIN:-"/srv/quickstock/venv/bin/python"}
ENV_FILE=${ENV_FILE:-"/etc/quickstock/quickstock.env"}
RUN_COLLECTSTATIC=${RUN_COLLECTSTATIC:-"yes"}

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Environment file not found: $ENV_FILE" >&2
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python executable not found or not executable: $PYTHON_BIN" >&2
  exit 1
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "Application directory not found: $APP_DIR" >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

cd "$APP_DIR"

"$PYTHON_BIN" manage.py check --deploy
"$PYTHON_BIN" manage.py makemigrations --check --dry-run
"$PYTHON_BIN" manage.py migrate --plan
"$PYTHON_BIN" manage.py migrate
"$PYTHON_BIN" manage.py createcachetable
"$PYTHON_BIN" manage.py bootstrap_superuser
"$PYTHON_BIN" manage.py check_migrations
"$PYTHON_BIN" manage.py check_runtime_health

if [[ "$RUN_COLLECTSTATIC" == "yes" ]]; then
  "$PYTHON_BIN" manage.py collectstatic --noinput
fi

echo "QuickStock deploy verification completed successfully."
