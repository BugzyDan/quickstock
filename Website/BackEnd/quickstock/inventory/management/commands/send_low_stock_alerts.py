from datetime import date

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand
from django.db.models import IntegerField, Sum, Value
from django.db.models.functions import Coalesce

from inventory.email_utils import email_delivery_status, get_delivery_connection
from inventory.models import Item


class Command(BaseCommand):
    help = "Send low stock email alerts to admins or configured recipients."

    def handle(self, *args, **options):
        if not getattr(settings, "LOW_STOCK_EMAIL_ENABLED", True):
            self.stdout.write(self.style.WARNING("Low stock alerts are disabled."))
            return

        try:
            threshold = int(getattr(settings, "LOW_STOCK_THRESHOLD", 10))
        except Exception:
            threshold = 10

        low_stock_items = list(
            Item.objects.filter(is_deleted=False)
            .annotate(
                stock_quantity=Coalesce(
                    Sum("stock_at_locations__quantity"),
                    Value(0),
                    output_field=IntegerField(),
                )
            )
            .filter(stock_quantity__lte=threshold)
            .select_related("owner")
            .order_by("stock_quantity", "name")
        )
        if not low_stock_items:
            self.stdout.write(self.style.SUCCESS("No low stock items found."))
            return

        delivery = email_delivery_status()
        if not delivery["ok"]:
            self.stdout.write(self.style.ERROR(delivery["detail"]))
            return

        recipients = list(getattr(settings, "LOW_STOCK_EMAIL_RECIPIENTS", []))
        if not recipients:
            recipients = list(
                User.objects.filter(
                    is_active=True,
                    profile__role="admin",
                )
                .exclude(email__isnull=True)
                .exclude(email__exact="")
                .values_list("email", flat=True)
            )

        if not recipients:
            self.stdout.write(self.style.WARNING("No recipients found for low stock alerts."))
            return

        today = date.today().isoformat()
        cache_key = f"low_stock_alert_sent:{today}"
        if cache.get(cache_key):
            self.stdout.write(self.style.WARNING("Low stock alert already sent today."))
            return

        subject = f"QuickStock JA Low Stock Alert ({today})"
        lines = [
            f"Low stock threshold: {threshold}",
            "",
            "Items:",
        ]
        for item in low_stock_items:
            lines.append(
                f"- {item.name} (SKU: {item.sku or 'N/A'}, "
                f"account: {item.owner.username}) - Qty: {item.stock_quantity or 0}"
            )

        body = "\n".join(lines)
        sent_count = EmailMessage(
            subject,
            body,
            to=recipients,
            connection=get_delivery_connection(),
        ).send(fail_silently=False)
        if sent_count != 1:
            self.stdout.write(self.style.ERROR("The email provider did not accept the low stock alert."))
            return

        cache.set(cache_key, True, timeout=60 * 60 * 24)
        self.stdout.write(self.style.SUCCESS(f"Low stock alert sent to {len(recipients)} recipient(s)."))
