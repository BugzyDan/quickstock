# Cron: Audit Log Cleanup

Run the cleanup command daily to keep audit logs within retention:

```bash
0 2 * * * /usr/bin/env bash -lc 'set -a && source /etc/quickstock/quickstock.env && set +a && cd /srv/quickstock/current/Website/BackEnd/quickstock && /srv/quickstock/venv/bin/python manage.py cleanup_audit_logs'
```

- Adjust the time or paths as needed.
- The command above assumes the production env file lives at `/etc/quickstock/quickstock.env`.

# Cron: Low Stock Email Alerts

Send low stock alert emails daily:

```bash
30 7 * * * /usr/bin/env bash -lc 'set -a && source /etc/quickstock/quickstock.env && set +a && cd /srv/quickstock/current/Website/BackEnd/quickstock && /srv/quickstock/venv/bin/python manage.py send_low_stock_alerts'
```

- Adjust the time or path as needed.

# Cron: Database Backups

Run the database backup script daily and keep the log file outside the app directory:

```bash
30 2 * * * /usr/bin/env bash -lc 'set -a && source /etc/quickstock/quickstock.env && set +a && /srv/quickstock/current/Website/BackEnd/quickstock/scripts/backup_db.sh' >> /var/log/quickstock_backup.log 2>&1
```

Recommended environment variables:

- `BACKUP_DIR=/var/backups/quickstock`
- `BACKUP_RETENTION_DAYS=14`
- `BACKUP_POST_HOOK=/usr/local/bin/quickstock-upload-backup`
- `DJANGO_DB_ENGINE`, `DJANGO_DB_NAME`, `DJANGO_DB_USER`, `DJANGO_DB_PASSWORD`, `DJANGO_DB_HOST`, `DJANGO_DB_PORT`

`BACKUP_POST_HOOK` should be an executable script that accepts the newly-created backup path as its first argument. Use it to push backups to S3, DigitalOcean Spaces, or another off-site target.

Run one restore rehearsal with `scripts/restore_db.sh` before launch and after any major database change.
