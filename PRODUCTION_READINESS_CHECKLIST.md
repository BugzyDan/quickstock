# Production Readiness Checklist

Use this checklist before a production release. Render-specific setup and recovery steps are in the [backend deployment guide](Website/BackEnd/quickstock/DEPLOY.md).

## Backend

- [ ] Deploy the intended Git branch and commit.
- [ ] For Render, confirm the web service and PostgreSQL database are available in the same account and region.
- [ ] Confirm `DATABASE_URL` references the intended database and that a current backup or recovery path exists.
- [ ] Use durable database storage for production records; Render Free Postgres expires after 30 days and has no backups.
- [ ] Configure a stable `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=False`, allowed hosts, and HTTPS CSRF origins.
- [ ] Configure Resend with a valid API key and a sender on a verified domain, or configure another supported production email provider.
- [ ] Confirm payment credentials and environment match the intended test or live mode.
- [ ] Confirm deployment migrations and static collection complete successfully.
- [ ] Confirm `/healthz/`, `/api/health/`, and `/status/` show the expected healthy state.
- [ ] Verify administrator login, email verification-code delivery, and a normal inventory read.
- [ ] Verify monitoring and database backup procedures.

## Desktop Client

- [ ] Set `API_BASE_URL` to the production HTTPS service.
- [ ] Confirm offline data is retained locally and synchronization recovers after a network interruption.
- [ ] Build and launch the client on each supported target operating system.
- [ ] Confirm application branding, configuration, and update instructions are included in the release.

## Rollback

- [ ] Identify the previous known-good application release.
- [ ] Take a database backup before any schema migration.
- [ ] Document the owner and steps for restoring the application and database.
- [ ] After rollback, verify login, inventory reads, static assets, and data integrity.
