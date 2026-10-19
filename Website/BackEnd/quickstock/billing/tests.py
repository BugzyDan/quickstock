"""SaaS billing tests: subscription state is derived from the payment ledger."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    STATUS_ACTIVE,
    STATUS_EXPIRED,
    STATUS_GRACE,
    STATUS_PENDING,
    Subscription,
    subscription_for,
)
from inventory.models import Payment, UserProfile


class SubscriptionLedgerTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("billing-owner", "billing@example.com", "pw-not-used")
        self.profile = UserProfile.for_user(self.owner)
        self.profile.role = "admin"
        self.profile.status = "active"
        self.profile.save(update_fields=["role", "status"])

    def _payment(self, order_id, cycle="yearly", amount="190.00"):
        return Payment.objects.create(
            order_id=order_id,
            user=self.owner,
            amount=Decimal(amount),
            response_payload={"billing_cycle": cycle},
        )

    def test_pending_subscription_has_no_access(self):
        subscription = subscription_for(self.owner)

        self.assertEqual(subscription.status, STATUS_PENDING)
        self.assertFalse(subscription.has_access)

    def test_paid_payment_activates_subscription(self):
        payment = self._payment("QS-1-aaaaaa")
        payment.mark_paid(transaction_id="TX-1")

        subscription = Subscription.objects.get(owner=self.owner)
        self.assertEqual(subscription.status, STATUS_ACTIVE)
        self.assertTrue(subscription.has_access)
        self.assertAlmostEqual(
            (subscription.access_ends_at - subscription.access_starts_at).days,
            365,
            delta=1,
        )

    def test_replayed_callback_does_not_extend_twice(self):
        payment = self._payment("QS-1-replay")
        payment.mark_paid(transaction_id="TX-1")
        first_end = Subscription.objects.get(owner=self.owner).access_ends_at

        # Provider retries the same order id.
        payment.status = "pending"
        payment.save(update_fields=["status"])
        payment.mark_paid(transaction_id="TX-1")
        subscription = Subscription.objects.get(owner=self.owner)

        self.assertEqual(subscription.access_ends_at, first_end)

    def test_monthly_renewal_extends_existing_period(self):
        yearly = self._payment("QS-1-year", cycle="yearly")
        yearly.mark_paid()
        yearly_end = Subscription.objects.get(owner=self.owner).access_ends_at

        monthly = self._payment("QS-1-month", cycle="monthly", amount="25.00")
        monthly.mark_paid()

        subscription = Subscription.objects.get(owner=self.owner)
        self.assertEqual(subscription.billing_cycle, "monthly")
        self.assertAlmostEqual(
            (subscription.access_ends_at - yearly_end).days,
            30,
            delta=1,
        )

    def test_grace_period_then_expiry(self):
        payment = self._payment("QS-1-grace", cycle="monthly")
        payment.mark_paid()
        subscription = Subscription.objects.get(owner=self.owner)

        # Simulate one day past the commercial renewal date.
        subscription.period_ends_at = timezone.now() - timedelta(days=1)
        subscription.access_ends_at = timezone.now() + timedelta(days=2)
        subscription.save(update_fields=["period_ends_at", "access_ends_at"])

        self.assertEqual(subscription.sync_status(), STATUS_GRACE)
        self.assertTrue(subscription.has_access)

        subscription.access_ends_at = timezone.now() - timedelta(minutes=1)
        subscription.save(update_fields=["access_ends_at"])
        self.assertEqual(subscription.sync_status(), STATUS_EXPIRED)
        self.assertFalse(subscription.has_access)

    def test_pro_flag_without_paid_charge_does_not_unlock_access(self):
        # Simulates a stale/manually edited plan flag with no billing record.
        self.profile.plan = "PRO"
        self.profile.pro_expires = timezone.localdate() + timedelta(days=30)
        self.profile.save(update_fields=["plan", "pro_expires"])
        subscription_for(self.owner)

        self.assertFalse(self.profile.is_pro_active())

    def test_paid_subscription_unlocks_pro(self):
        self.profile.plan = "PRO"
        self.profile.pro_expires = timezone.localdate() + timedelta(days=30)
        self.profile.save(update_fields=["plan", "pro_expires"])
        self._payment("QS-1-unlock").mark_paid()

        self.assertTrue(UserProfile.for_user(self.owner).is_pro_active())

    def test_staff_inherit_tenant_subscription(self):
        staff = User.objects.create_user("billing-staff", "staff@example.com", "pw-not-used")
        staff_profile = UserProfile.for_user(staff)
        staff_profile.role = "cashier"
        staff_profile.status = "active"
        staff_profile.parent_admin = self.profile
        staff_profile.save(update_fields=["role", "status", "parent_admin"])

        self.assertFalse(staff_profile.is_pro_active())

        self._payment("QS-1-staff").mark_paid()
        self.profile.refresh_from_db()

        self.assertTrue(UserProfile.for_user(staff).is_pro_active())
