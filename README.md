# QuickStock JA

QuickStock JA is an inventory, point-of-sale, cash-register, and accounting system for Caribbean businesses. It includes a Django web application and API, plus a Python desktop client with offline inventory and sale synchronization.

## Components

- `Website/BackEnd/quickstock`: Django web app, REST API, migrations, and deployment scripts.
- `Desktop_UI`: cross-platform desktop client and its tests.
- `render.yaml`: Render web service and PostgreSQL Blueprint configuration.

The backend uses SQLite by default in local development. It supports PostgreSQL and MySQL deployments; Render is configured for PostgreSQL.

## Run Locally

Use Python 3.10 or newer. The backend's local defaults use Django debug mode and SQLite, so a production environment file is not needed for a basic development run.

```bash
cd Website/BackEnd/quickstock
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

On Windows, activate the environment with `.venv\Scripts\activate` instead of `source .venv/bin/activate`.

To run the desktop client, start the backend first, then open another terminal:

```bash
cd Desktop_UI
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python main.py
```

Set `API_BASE_URL` in `Desktop_UI/.env` to the backend URL. The example defaults to `http://localhost:8000`.

## Deployment

Render is configured from the repository-root `render.yaml`; deployment and required environment variables are documented in [the backend deployment guide](Website/BackEnd/quickstock/DEPLOY.md). The guide covers health checks, migrations, email delivery, database handling, and the separate dedicated-server deployment path.

The Blueprint deploys from `main` and declares a paid Render Postgres plan with automated backups. Free databases expire after 30 days and do not include backups, so they must never hold production records. Do not replace a suspended database before confirming whether its data can be restored.

For self-hosted deployments, `docker compose -f docker-compose.prod.yml up -d --build` builds the backend and nginx images in `docker/`, runs migrations and static collection behind a health gate, and terminates TLS. `docker-compose.yml` remains the development/staging stack.

## CI

`.github/workflows/ci.yml` runs on every push and pull request to `main`:

- Django `check --deploy`, migration drift check, lint, and the full test suite on SQLite
- The same suite against PostgreSQL 16, plus a real `backup_database` run
- The desktop client test suite

A red build now blocks the change, including any payment, billing, or tenant-scoping regression.

## Subscriptions and Billing

Paid access is derived from the billing ledger (`billing.Subscription`), not from a mutable plan flag. A `Payment` that reaches `paid` extends the tenant's subscription exactly once per order id, so replayed provider callbacks cannot grant a second period. `UserProfile.is_pro_active()` consults that subscription, which means a manually set `plan = "PRO"` without a paid charge no longer unlocks premium modules.

| Setting | Default | Purpose |
| --- | --- | --- |
| `QUICKSTOCK_BILLING_ENFORCED` | `true` | Enforce subscription coverage for premium features |
| `QUICKSTOCK_BILLING_GRACE_DAYS` | `3` | Days of access retained after a renewal lapses |

Operational commands:

```bash
python manage.py billing_status --expiring-within 14
python manage.py backup_database --output-dir /var/backups/quickstock
```

## Tenant Isolation

`inventory/tenant.py` provides one audited entry point for tenant-scoped queries: `scoped(Model, user)`. It resolves the effective owner (a staff member's `parent_admin`) and refuses models that are not registered in `TENANT_PATHS`, so a new model cannot be queried cross-tenant by accident. A guard test asserts every registered path resolves against the schema.

## Project Checks

```bash
# Backend
cd Website/BackEnd/quickstock
python manage.py check
python manage.py test

# Desktop
cd Desktop_UI
python -m pytest
```

## Documentation

- [Backend deployment guide](Website/BackEnd/quickstock/DEPLOY.md)
- [Desktop and backend sync guide](SYNC_IMPLEMENTATION_GUIDE.md)
- [Checkout integrity notes](QS_002_CHECKOUT_INTEGRITY.md)
- [Production readiness checklist](PRODUCTION_READINESS_CHECKLIST.md)
- [Security audit report](SECURITY_AUDIT_REPORT.md)
- [Production hardening summary](PRODUCTION_HARDENING_SUMMARY.md)

## Secrets and Local Data

Keep real environment files, local databases, uploaded media, and database backups out of Git. The repository ignores these files; review `git status --ignored` carefully before removing local data.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
