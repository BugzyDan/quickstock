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

The Blueprint currently declares a Free Render Postgres database. Free databases expire after 30 days and do not include backups, so use durable paid database storage for production records. Do not replace a suspended database before confirming whether its data can be restored.

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
