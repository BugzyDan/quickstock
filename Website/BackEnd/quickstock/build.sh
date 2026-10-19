#!/usr/bin/env bash
set -o errexit
set -o nounset
set -o pipefail

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

# Fail fast on misconfiguration before touching the database.
python3 manage.py check --deploy --fail-level ERROR
python3 manage.py makemigrations --check --dry-run

# Back up before any schema change. QUICKSTOCK_BACKUP_ON_BUILD=false disables
# this (e.g. when the platform provides its own PITR backups).
if [[ "${QUICKSTOCK_BACKUP_ON_BUILD:-true}" == "true" ]]; then
    BACKUP_DIR="${QUICKSTOCK_BACKUP_DIR:-/tmp/quickstock-backups}"
    if ! python3 manage.py backup_database --output-dir "$BACKUP_DIR" --retain 7; then
        echo "WARNING: pre-migration backup failed. Set QUICKSTOCK_BACKUP_ON_BUILD=false only if the platform has verified backups." >&2
        if [[ "${QUICKSTOCK_REQUIRE_BACKUP:-true}" == "true" ]]; then
            exit 1
        fi
    fi
fi

python3 manage.py collectstatic --noinput
python3 manage.py migrate --noinput
python3 manage.py createcachetable
python3 manage.py bootstrap_superuser
python3 manage.py check_migrations
python3 manage.py check_runtime_health
if [[ "${QUICKSTOCK_EMAIL_PROVIDER:-}" == "resend" ]]; then
    python3 manage.py check_email_delivery
fi
