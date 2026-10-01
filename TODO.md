# Current Follow-ups

## Render Recovery

- [ ] Check why `quickstock-db` is suspended and restore it while its data is recoverable.
- [ ] Confirm the backend `DATABASE_URL` points to the restored database's internal URL, then deploy migrations and the shared cache table.
- [ ] Set `DJANGO_DEFAULT_FROM_EMAIL` to an address on a verified Resend domain and confirm email delivery.
- [ ] Confirm `/healthz/` and `/api/health/` report healthy status, then verify browser login and email-code delivery.
