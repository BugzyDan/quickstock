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
if [[ -n "${QUICKSTOCK_DELETE_USER_USERNAME:-}" ]]; then
    python3 manage.py delete_deploy_user
fi
python3 manage.py check_migrations
python3 manage.py check_runtime_health

if [[ "${QUICKSTOCK_EMAIL_PROVIDER:-}" == "resend" ]]; then
    python3 manage.py check_email_delivery
fi
