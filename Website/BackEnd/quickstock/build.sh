#!/usr/bin/env bash
set -o errexit
set -o nounset
set -o pipefail

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

python3 manage.py check --deploy --fail-level ERROR
python3 manage.py makemigrations --check --dry-run
python3 manage.py collectstatic --noinput
python3 manage.py migrate --noinput
python3 manage.py createcachetable
python3 manage.py bootstrap_superuser
python3 manage.py check_migrations
python3 manage.py check_runtime_health

if [[ "${QUICKSTOCK_EMAIL_PROVIDER:-}" == "resend" ]]; then
    python3 manage.py check_email_delivery
fi
