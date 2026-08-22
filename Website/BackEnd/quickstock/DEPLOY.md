# QuickStock JA Production Deployment

This guide assumes Ubuntu, Nginx, Gunicorn, and a dedicated server-side env file. Do not deploy directly from the repository root on a developer workstation.

## Render Deployment

The repository root `render.yaml` is the source of truth for the Render web service and PostgreSQL database.

1. Connect Render to the Git branch you intend to deploy and enable automatic deploys.
2. Add a domain you own to Resend and verify every DNS record Resend provides. The sender domain must resolve publicly; an unregistered or placeholder domain cannot deliver mail to Gmail.
3. Create a Resend API key with sending access and set it as the Render secret `RESEND_API_KEY`. Set `QUICKSTOCK_EMAIL_TEST_RECIPIENT` to an inbox you monitor.
4. Keep `QUICKSTOCK_EMAIL_PROVIDER=resend`. Free Render web services block SMTP ports, so Gmail SMTP is not a valid production delivery path on the free service.
5. Set `DJANGO_DEFAULT_FROM_EMAIL=QuickStock JA <noreply@your-verified-domain>` in Render. The address must belong to the domain Resend shows as verified.
6. Deploy. The build collects static files, applies migrations, creates the shared database cache table, checks runtime health, and sends a real email probe. A missing or rejected Resend configuration fails the deployment instead of leaving login stuck on a code that was never sent.
7. Confirm `/api/health/` returns `status: ok`, including `database`, `migrations`, `cache`, and `email` checks.
8. From a Render Shell, send a real delivery probe with `python manage.py check_email_delivery --to your-address@example.com`.

Render PostgreSQL stores production records; the local SQLite database is not copied during a Git deploy. Transfer local records separately with a private fixture or database migration process. Never commit a data fixture containing user, session, customer, or payment records.

To move an intentional local clone into a new, empty Render PostgreSQL database:

1. Locally run `python manage.py export_deployment_bundle --output /tmp/quickstock.quickstock-deploy.zip`.
2. Transfer that bundle to the Render Shell through a private channel. The bundle contains password hashes and business data and must never be added to Git or sent publicly.
3. In the Render Shell run `python manage.py import_deployment_bundle /path/to/quickstock.quickstock-deploy.zip --confirm IMPORT`.
4. Run `python manage.py check_runtime_health`, then compare user, item, location, sale, and stock counts between local and Render.

The import command only loads into an empty application database. Use the existing `import_desktop_inventory` command instead when only product and stock seed data should be merged into an existing account.

Free Render PostgreSQL databases expire after 30 days and have no backups. Upgrade the database before treating the service as durable production storage.

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

1. Start from the sanitized [`.env.example`](./.env.example) template.
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

From `/srv/quickstock/current/Website/BackEnd/quickstock`, run the scripted verification:

```bash
scripts/deploy_verify.sh
```

The script loads `/etc/quickstock/quickstock.env` by default and runs the same checks below. Override paths if your server layout differs:

```bash
APP_DIR=/srv/quickstock/current/Website/BackEnd/quickstock \
PYTHON_BIN=/srv/quickstock/venv/bin/python \
ENV_FILE=/etc/quickstock/quickstock.env \
scripts/deploy_verify.sh
```

Manual sequence:

1. `python manage.py check --deploy`
2. `python manage.py makemigrations --check --dry-run`
3. `python manage.py migrate --plan`
4. `python manage.py migrate`
5. `python manage.py check_migrations`
6. `python manage.py check_runtime_health`
7. `python manage.py collectstatic --noinput`

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
