from django.contrib import admin
from django.utils.html import format_html

from .models import Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("owner", "colored_status", "billing_cycle", "period_ends_at", "access_ends_at", "last_payment_order_id")
    list_filter = ("status", "billing_cycle")
    search_fields = ("owner__username", "owner__email", "last_payment_order_id")
    readonly_fields = ("created_at", "updated_at", "access_starts_at")
    actions = ("rebuild_from_payments", "cancel_renewal", "resync_status")

    @admin.display(description="Status")
    def colored_status(self, obj):
        status = obj.sync_status(save=False)
        colors = {"active": "#0f9d58", "grace": "#d97706", "expired": "#d93025", "cancelled": "#64748b"}
        return format_html(
            '<b style="color:{}">{}</b>', colors.get(status, "#333"), status.upper()
        )

    @admin.action(description="Rebuild subscription from paid payment records")
    def rebuild_from_payments(self, request, queryset):
        from .models import record_billing_events

        for subscription in queryset:
            paid = subscription.owner.payments.filter(status="paid").order_by("updated_at")
            subscription.access_ends_at = None
            subscription.last_payment_order_id = ""
            subscription.save(update_fields=["access_ends_at", "last_payment_order_id"])
            for payment in paid:
                record_billing_events(payment)
            subscription.sync_status()
        self.message_user(request, f"Rebuilt {queryset.count()} subscription(s) from the payment ledger.")

    @admin.action(description="Cancel renewal (paid access continues until it ends)")
    def cancel_renewal(self, request, queryset):
        for subscription in queryset:
            subscription.cancel()
        self.message_user(request, f"Cancelled renewal for {queryset.count()} subscription(s).")

    @admin.action(description="Resync status from the clock")
    def resync_status(self, request, queryset):
        for subscription in queryset:
            subscription.sync_status()
        self.message_user(request, "Status refreshed.")
