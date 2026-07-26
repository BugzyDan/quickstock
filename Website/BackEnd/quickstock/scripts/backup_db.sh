#!/usr/bin/env bash
set -euo pipefail

TIMESTAMP=$(date +"%Y%m%d%H%M%S")
BACKUP_DIR=${BACKUP_DIR:-"/var/backups/quickstock"}
DB_ENGINE=${DJANGO_DB_ENGINE:-"django.db.backends.sqlite3"}
RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-14}
BACKUP_POST_HOOK=${BACKUP_POST_HOOK:-""}

mkdir -p "$BACKUP_DIR"

run_post_hook() {
  local backup_file="$1"
  if [[ -z "$BACKUP_POST_HOOK" ]]; then
    return 0
  fi

  if [[ ! -x "$BACKUP_POST_HOOK" ]]; then
    echo "BACKUP_POST_HOOK is set but not executable: $BACKUP_POST_HOOK" >&2
    exit 1
  fi

  "$BACKUP_POST_HOOK" "$backup_file"
}

if [[ "$DB_ENGINE" == "django.db.backends.sqlite3" ]]; then
  DB_PATH=${DJANGO_DB_NAME:-"db.sqlite3"}
  cp "$DB_PATH" "$BACKUP_DIR/quickstock_sqlite_$TIMESTAMP.db"
  gzip -f "$BACKUP_DIR/quickstock_sqlite_$TIMESTAMP.db"
  BACKUP_FILE="$BACKUP_DIR/quickstock_sqlite_$TIMESTAMP.db.gz"
  echo "SQLite backup written to $BACKUP_FILE"
  run_post_hook "$BACKUP_FILE"
  find "$BACKUP_DIR" -type f -name "quickstock_sqlite_*.db.gz" -mtime +"$RETENTION_DAYS" -delete
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
  BACKUP_FILE="$BACKUP_DIR/quickstock_postgres_$TIMESTAMP.sql.gz"
  pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" \
    | gzip -c > "$BACKUP_FILE"
  unset PGPASSWORD
  echo "Postgres backup written to $BACKUP_FILE"
  run_post_hook "$BACKUP_FILE"
  find "$BACKUP_DIR" -type f -name "quickstock_postgres_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete
  exit 0
fi

DB_PORT=${DB_PORT:-"3306"}
export MYSQL_PWD="$DB_PASSWORD"
BACKUP_FILE="$BACKUP_DIR/quickstock_mysql_$TIMESTAMP.sql.gz"
mysqldump -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" "$DB_NAME" \
  | gzip -c > "$BACKUP_FILE"
unset MYSQL_PWD

echo "MySQL backup written to $BACKUP_FILE"
run_post_hook "$BACKUP_FILE"
find "$BACKUP_DIR" -type f -name "quickstock_mysql_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete
