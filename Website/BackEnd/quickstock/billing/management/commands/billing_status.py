"""Report SaaS billing state and optional expiring-soon warnings.

Usage:
    python manage.py billing_status
    python manage.py billing_status --expiring-within 14 --fail-if-expired

Intended for the daily operations check (cron or Render Cron Job). It is
read-only and never modifies subscriptions.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from billing.models import STATUS_ACTIVE, STATUS_GRACE, Subscription


class Command(BaseCommand):
    help = "Report subscription status and upcoming expirations."

    def add_arguments(self, parser):
        parser.add_argument("--expiring-within", type=int, default=7, help="Days ahead to flag renewals.")
        parser.add_argument(
            "--fail-if-expired",
            action="store_true",
            help="Exit non-zero when at least one subscription has lost access.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        subscriptions = list(Subscription.objects.select_related("owner").all())
        counts = {}
        expiring = []
        expired = []
        for subscription in subscriptions:
            status = subscription.sync_status()
            counts[status] = counts.get(status, 0) + 1
            if status in {STATUS_ACTIVE, STATUS_GRACE} and subscription.access_ends_at:
                remaining = (subscription.access_ends_at - now).days
                threshold = options["expiring_within"] + (3 if status == STATUS_GRACE else 0)
                if remaining <= threshold:
                    expiring.append((subscription, remaining))
            if status not in {STATUS_ACTIVE, STATUS_GRACE}:
                expired.append(subscription)

        self.stdout.write(f"Subscriptions: {len(subscriptions)}")
        for status in sorted(counts):
            self.stdout.write(f"  {status:<10} {counts[status]}")
        for subscription, remaining in expiring:
            self.stdout.write(
                self.style.WARNING(
                    f"  renewal due in {remaining} day(s): {subscription.owner.username} ({subscription.status})"
                )
            )
        for subscription in expired:
            self.stdout.write(
                self.style.ERROR(f"  no access: {subscription.owner.username} ({subscription.status})")
            )
        if options["fail_if_expired"] and expired:
            raise SystemExit(1)
