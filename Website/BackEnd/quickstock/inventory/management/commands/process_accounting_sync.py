from django.core.management.base import BaseCommand, CommandError

from inventory.accounting import process_pending_accounting_sync
from inventory.models import AccountingIntegration


class Command(BaseCommand):
    help = "Process pending QuickStock accounting sync records."

    def add_arguments(self, parser):
        parser.add_argument("--provider", default=AccountingIntegration.PROVIDER_XERO)
        parser.add_argument("--owner-id", type=int, default=None)
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        provider = options["provider"]
        integrations = AccountingIntegration.objects.filter(
            provider=provider,
            status__in=[AccountingIntegration.STATUS_CONFIGURED, AccountingIntegration.STATUS_ACTIVE],
        )
        if options["owner_id"]:
            integrations = integrations.filter(owner_id=options["owner_id"])

        if not integrations.exists():
            raise CommandError("No configured accounting integrations found.")

        total_synced = 0
        total_failed = 0
        for integration in integrations:
            result = process_pending_accounting_sync(integration=integration, limit=options["limit"])
            total_synced += result["synced"]
            total_failed += result["failed"]

        self.stdout.write(
            self.style.SUCCESS(
                f"Accounting sync complete: {total_synced} synced, {total_failed} failed."
            )
        )
