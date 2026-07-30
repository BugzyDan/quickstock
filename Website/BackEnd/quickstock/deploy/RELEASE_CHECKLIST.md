# QuickStock Release Checklist

## Before Deploy

1. Confirm the release branch/tag is the intended version.
2. Confirm `/etc/quickstock/quickstock.env` is present and updated.
3. Confirm a fresh database backup completed successfully.
4. Confirm TLS certificates are valid.
5. Confirm `SENTRY_DSN` is set if exception alerting is expected for this environment.

## Deploy

From `/srv/quickstock/current/Website/BackEnd/quickstock`:

1. `scripts/deploy_verify.sh`
2. `sudo systemctl restart quickstock`
3. `sudo systemctl reload nginx`

Manual equivalent:

1. `python manage.py check --deploy`
2. `python manage.py makemigrations --check --dry-run`
3. `python manage.py migrate --plan`
4. `python manage.py migrate`
5. `python manage.py check_migrations`
6. `python manage.py check_runtime_health`
7. `python manage.py collectstatic --noinput`

## Smoke Test

1. Load `/` over HTTPS.
2. Load `/login/`.
3. Sign in with an admin account.
4. Open `/status/` and `/api/health/`.
5. Confirm static assets and media load correctly.
6. Complete one inventory lookup.
7. Complete one non-destructive API request, e.g. `/api/profile/`.
8. If payments are enabled, confirm checkout creation works in the configured environment.
9. Trigger or verify one monitoring signal path, e.g. a Sentry test event in staging or a successful uptime probe.

## Rollback Signals

Rollback immediately if:

1. the app fails `check --deploy`
2. Gunicorn does not come up cleanly
3. Nginx returns 502/504
4. login fails for valid users
5. sales or stock APIs error on normal requests

## Rollback Steps

1. Restore the previous release directory/symlink.
2. Restart Gunicorn.
3. Reload Nginx.
4. Restore the database from the pre-deploy backup if a migration caused data issues.
