from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from inventory.models import AuditLog


class Command(BaseCommand):
    help = "Deletes audit logs older than the configured retention window."

    def handle(self, *args, **options):
        retention_days = cache.get("audit_retention_days")
        if retention_days is None:
            retention_days = getattr(settings, "AUDIT_LOG_RETENTION_DAYS", 90)

        try:
            retention_days = int(retention_days)
        except Exception:
            retention_days = 90

        if retention_days <= 0:
            self.stdout.write(self.style.WARNING("Retention disabled. No logs deleted."))
            return

        cutoff = timezone.now() - timedelta(days=retention_days)
        deleted, _ = AuditLog.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} audit log entries."))
