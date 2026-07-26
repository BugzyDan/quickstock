#!/usr/bin/env bash
set -euo pipefail

if [[ "${CONFIRM_RESTORE:-}" != "yes" ]]; then
  echo "Set CONFIRM_RESTORE=yes to run a database restore." >&2
  exit 1
fi

if [[ $# -ne 1 ]]; then
  echo "Usage: CONFIRM_RESTORE=yes $0 /path/to/backup-file" >&2
  exit 1
fi

BACKUP_FILE=$1
DB_ENGINE=${DJANGO_DB_ENGINE:-"django.db.backends.sqlite3"}

if [[ ! -f "$BACKUP_FILE" ]]; then
  echo "Backup file not found: $BACKUP_FILE" >&2
  exit 1
fi

if [[ "$DB_ENGINE" == "django.db.backends.sqlite3" ]]; then
  DB_PATH=${DJANGO_DB_NAME:-"db.sqlite3"}
  if [[ "$BACKUP_FILE" == *.gz ]]; then
    gzip -dc "$BACKUP_FILE" > "$DB_PATH"
  else
    cp "$BACKUP_FILE" "$DB_PATH"
  fi
  echo "SQLite database restored to $DB_PATH"
  exit 0
fi

DB_NAME=${DJANGO_DB_NAME:-""}
DB_USER=${DJANGO_DB_USER:-""}
DB_PASSWORD=${DJANGO_DB_PASSWORD:-""}
DB_HOST=${DJANGO_DB_HOST:-"localhost"}
DB_PORT=${DJANGO_DB_PORT:-""}

if [[ -z "$DB_NAME" || -z "$DB_USER" || -z "$DB_PASSWORD" ]]; then
  echo "Missing DB credentials. Set DJANGO_DB_NAME, DJANGO_DB_USER, DJANGO_DB_PASSWORD." >&2
  exit 1
fi

if [[ "$DB_ENGINE" == "django.db.backends.postgresql" ]]; then
  DB_PORT=${DB_PORT:-"5432"}
  export PGPASSWORD="$DB_PASSWORD"
  if [[ "$BACKUP_FILE" == *.gz ]]; then
    gzip -dc "$BACKUP_FILE" | psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME"
  else
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" < "$BACKUP_FILE"
  fi
  unset PGPASSWORD
  echo "Postgres restore completed for $DB_NAME"
  exit 0
fi

DB_PORT=${DB_PORT:-"3306"}
export MYSQL_PWD="$DB_PASSWORD"
if [[ "$BACKUP_FILE" == *.gz ]]; then
  gzip -dc "$BACKUP_FILE" | mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME"
else
  mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME" < "$BACKUP_FILE"
fi
unset MYSQL_PWD

echo "MySQL restore completed for $DB_NAME"
