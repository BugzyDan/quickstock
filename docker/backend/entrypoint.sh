#!/usr/bin/env bash
# QuickStock JA backend entrypoint: fail-fast release checks, then serve.
set -o errexit
set -o nounset
set -o pipefail

echo "[entrypoint] running Django deployment checks"
python manage.py check --deploy --fail-level ERROR

echo "[entrypoint] applying migrations"
python manage.py migrate --noinput

echo "[entrypoint] ensuring database cache table exists"
python manage.py createcachetable

echo "[entrypoint] collecting static files"
python manage.py collectstatic --noinput

echo "[entrypoint] starting: $*"
exec "$@"
