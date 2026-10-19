"""SaaS subscription state for QuickStock JA.

Subscription state is *derived* from paid payment records rather than stored as
a separate mutable flag. A userProfile.plan value that says PRO while the
billing table shows no payment is a bug in every SaaS; computing coverage from
the payment ledger makes that class of bug impossible.

Renewal window: paid time begins at ``access_starts_at`` (when the charge was
recorded) and runs to ``access_ends_at``. The commercial ``period_ends_at`` is
earlier than access so a renewal lapse still leaves a short grace window in
which staff can close open cash shifts instead of being locked out mid-transaction.
"""

from datetime import datetime, timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


CYCLE_MONTHLY = "monthly"
CYCLE_YEARLY = "yearly"
CYCLE_CHOICES = (
    (CYCLE_MONTHLY, "Monthly"),
    (CYCLE_YEARLY, "Yearly"),
)

STATUS_PENDING = "pending"
STATUS_ACTIVE = "active"
STATUS_GRACE = "grace"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"
STATUS_CHOICES = (
    (STATUS_PENDING, "Pending"),
    (STATUS_ACTIVE, "Active"),
    (STATUS_GRACE, "Grace period"),
    (STATUS_EXPIRED, "Expired"),
    (STATUS_CANCELLED, "Cancelled"),
)


def cycle_days(cycle: str) -> int:
    return 30 if (cycle or "").strip().lower() == CYCLE_MONTHLY else 365


def grace_days() -> int:
    return int(getattr(settings, "QUICKSTOCK_BILLING_GRACE_DAYS", 3))


class Subscription(models.Model):
    """One subscription per paying account (the tenant owner user)."""

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    billing_cycle = models.CharField(max_length=10, choices=CYCLE_CHOICES, default=CYCLE_YEARLY)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    period_starts_at = models.DateTimeField(null=True, blank=True)
    period_ends_at = models.DateTimeField(null=True, blank=True)
    access_starts_at = models.DateTimeField(null=True, blank=True)
    access_ends_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    last_payment_order_id = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.owner_id}:{self.status}:{self.access_ends_at}"

    # -- state ------------------------------------------------------------

    def sync_status(self, *, save: bool = True) -> str:
        """Recompute status from clock and payment coverage."""
        now = timezone.now()
        if self.cancelled_at:
            new_status = STATUS_CANCELLED
        elif self.access_ends_at and now < self.access_ends_at:
            new_status = STATUS_ACTIVE if (self.period_ends_at and now < self.period_ends_at) else STATUS_GRACE
        elif self.access_ends_at:
            new_status = STATUS_EXPIRED
        else:
            new_status = STATUS_PENDING
        if new_status != self.status:
            self.status = new_status
            if save:
                self.save(update_fields=["status", "updated_at"])
        return new_status

    @property
    def has_access(self) -> bool:
        return self.sync_status(save=False) in {STATUS_ACTIVE, STATUS_GRACE}

    @property
    def in_grace(self) -> bool:
        return self.sync_status(save=False) == STATUS_GRACE

    @property
    def days_remaining(self) -> int:
        if not self.access_ends_at:
            return 0
        remaining = self.access_ends_at - timezone.now()
        return max(remaining.days, 0)

    # -- lifecycle --------------------------------------------------------

    def activate(self, *, order_id: str, cycle: str, paid_at: datetime | None = None) -> None:
        """Extend paid access for one billing period.

        Idempotent per order: replayed provider callbacks for the same order do
        not extend the subscription twice.
        """
        if order_id and order_id == self.last_payment_order_id:
            return
        start_from = self.access_ends_at if self.access_ends_at and self.access_ends_at > timezone.now() else None
        start = start_from or paid_at or timezone.now()
        end = start + timedelta(days=cycle_days(cycle))
        self.billing_cycle = cycle
        self.period_starts_at = start
        self.period_ends_at = end
        self.access_starts_at = self.access_starts_at or start
        self.access_ends_at = end
        self.cancelled_at = None
        self.last_payment_order_id = order_id or ""
        self.status = STATUS_ACTIVE
        self.save()
        self._sync_legacy_plan_flag()

    def _sync_legacy_plan_flag(self) -> None:
        """Keep UserProfile.plan in step with the billing ledger.

        Screens and older tests read ``plan``/``pro_expires`` directly. Paid
        access is the authority, so a paid subscription promotes the account and
        refreshes the visible expiry. ``is_pro_active`` still consults this
        subscription, so clearing the subscription immediately removes access.
        """
        from inventory.models import UserProfile

        profile = UserProfile.objects.filter(user_id=self.owner_id).first()
        if profile is None:
            return
        fields = []
        if profile.plan != "PRO":
            profile.plan = "PRO"
            fields.append("plan")
        if self.access_ends_at:
            new_expiry = timezone.localtime(self.access_ends_at).date()
            if profile.pro_expires != new_expiry:
                profile.pro_expires = new_expiry
                fields.append("pro_expires")
        if profile.plan_end is not None:
            profile.plan_end = None
            fields.append("plan_end")
        if fields:
            profile.save(update_fields=fields)

    def cancel(self) -> None:
        """Stop renewal. Paid access continues until it runs out."""
        self.cancelled_at = timezone.now()
        self.save(update_fields=["cancelled_at", "updated_at"])


def subscription_for(user):
    """Return the subscription row for a user, creating a pending one if needed."""
    if not user or not getattr(user, "pk", None):
        return None
    subscription, _ = Subscription.objects.get_or_create(owner=user)
    return subscription


def owner_has_paid_access(user) -> bool:
    """True when the tenant owning this user has current paid access."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser or getattr(settings, "QUICKSTOCK_BILLING_ENFORCED", True) is False:
        return True
    from inventory.models import UserProfile

    profile = UserProfile.for_user(user)
    billing_owner = (profile.get_effective_plan_owner() if profile else None) or profile
    owner_user = billing_owner.user if billing_owner and billing_owner.user_id else user
    subscription = Subscription.objects.filter(owner=owner_user).first()
    if not subscription:
        return False
    return subscription.has_access


def record_billing_events(payment) -> None:
    """Apply a paid Payment row to its tenant subscription exactly once."""
    if payment.status != "paid" or not payment.user_id:
        return
    payload = payment.response_payload if isinstance(payment.response_payload, dict) else {}
    cycle = payload.get("billing_cycle") or CYCLE_YEARLY
    subscription_for(payment.user).activate(
        order_id=payment.order_id,
        cycle=cycle,
        paid_at=payment.updated_at,
    )
