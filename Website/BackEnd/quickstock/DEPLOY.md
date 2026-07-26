# QuickStock JA Production Deployment

This guide assumes Ubuntu, Nginx, Gunicorn, and a dedicated server-side env file. Do not deploy directly from the repository root on a developer workstation.

## Recommended Layout

Use a deployment layout similar to:

```text
/srv/quickstock/
  current/                     # checked-out app release
  venv/                        # production virtualenv
/etc/quickstock/quickstock.env # production secrets and env vars
/run/quickstock/gunicorn.sock  # gunicorn unix socket
```

## Prerequisites

1. DNS is pointed at your server.
2. TLS certificates are issued for your domain.
3. A production database exists.
4. You have created a server-side env file at `/etc/quickstock/quickstock.env`.

## Environment

1. Start from [`.env.example`](./.env.example) or the sanitized [`.env.production`](./.env.production) template.
2. Store real production secrets in `/etc/quickstock/quickstock.env`, not in the repository.
3. Set `DJANGO_DEBUG=False`.
4. Use a non-SQLite production database.
5. Set `WIPAY_ENVIRONMENT=live` before public launch.
6. Set `JAMDEX_CALLBACK_SECRET` if JAM-DEX callbacks are enabled.
7. Set `DOWNLOAD_SOFTWARE_URL` if you want the public desktop download route to redirect to a release artifact.
8. Set `SENTRY_DSN` and `SENTRY_ENVIRONMENT=production` to enable real-time error monitoring.

## Install Dependencies

1. Install system packages:
   - `python3`
   - `python3-venv`
   - `python3-pip`
   - `nginx`
   - `mysql-client` or `postgresql-client`
2. Create the virtual environment in `/srv/quickstock/venv`.
3. Install Python dependencies from `requirements.txt`.

## Django Release Steps

From `/srv/quickstock/current/Website/BackEnd/quickstock`:

1. `python manage.py check --deploy`
2. `python manage.py check_migrations`
3. `python manage.py migrate --plan`
4. `python manage.py migrate`
5. `python manage.py check_runtime_health`
6. `python manage.py collectstatic --noinput`

## Gunicorn

Use the config in [`deploy/gunicorn.conf.py`](./deploy/gunicorn.conf.py). It is set up for a Unix socket by default, which is the preferred pairing with Nginx in production.

## systemd

Install [`deploy/quickstock.service`](./deploy/quickstock.service) into `/etc/systemd/system/quickstock.service`, then run:

1. `sudo systemctl daemon-reload`
2. `sudo systemctl enable quickstock`
3. `sudo systemctl restart quickstock`
4. `sudo systemctl status quickstock`

## Nginx

Install [`deploy/nginx-quickstock.conf`](./deploy/nginx-quickstock.conf), update the domain and certificate paths, then enable the site and reload Nginx.

The supplied config assumes:

1. HTTP redirects to HTTPS
2. Gunicorn listens on `/run/quickstock/gunicorn.sock`
3. Static and media files are served directly by Nginx

## Backups

Use [`scripts/backup_db.sh`](./scripts/backup_db.sh) with the cron template in [`scripts/backup_db.cron`](./scripts/backup_db.cron). Test one restore before launch.

If you need off-site backups, set `BACKUP_POST_HOOK` to an executable upload script. The backup script will pass the generated archive path as the first argument so the hook can copy it to S3, DigitalOcean Spaces, or another remote target.

For restore drills, use [`scripts/restore_db.sh`](./scripts/restore_db.sh). It requires `CONFIRM_RESTORE=yes` to reduce accidental restores. Keep one documented dry-run restore procedure with your production credentials outside the repo.

## Smoke Tests

Use the rollout checklist in [`deploy/RELEASE_CHECKLIST.md`](./deploy/RELEASE_CHECKLIST.md) after each deploy.

## Monitoring

At minimum, production should have:

1. `SENTRY_DSN` configured for exception alerting
2. external uptime monitoring against `/status/` and `/api/health/`
3. writable log storage for `quickstock.log`
