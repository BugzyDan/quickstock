# inventory/signals.py

from datetime import timedelta
import logging
import secrets
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.utils import timezone
from django.db.models.deletion import ProtectedError
# Ensure this line is at the top of your signals.py
from .models import UserVerification

from django.db.models import Q

from .models import (
    UserProfile,
    Sale,
    Supplier,
    SupplierInvoice,
    Location,
    Item,
    PurchaseOrder,
    SalesInvoice,
    SalesQuotation,
    SalesInvoicePayment,
    SalesInvoiceCreditNote,
    SalesInvoicePaymentReversal,
    CustomerCreditMovement,
    SupplierInvoicePayment,
    SupplierInvoicePaymentReversal,
    SupplierInvoiceAdjustment,
    SupplierInvoiceRefund,
)


logger = logging.getLogger("inventory")


@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    if kwargs.get("raw"):
        return

    # THE PERMANENT FIX: Use get_or_create instead of create
    # This prevents 'IntegrityError' if a profile accidentally exists
    # and prevents 'AttributeError' if a profile is missing.
    profile, profile_created = UserProfile.objects.get_or_create(user=instance)

    # Sync user changes to profile
    profile.save()

@receiver(pre_delete, sender=User)
def purge_user_data(sender, instance, **kwargs):
    """
    When a user account is deleted, remove all data owned by that user so the
    workspace is reset for that identity.
    """
    uid = instance.id
    if not uid:
        return

    has_financial_history = any(
        queryset.exists()
        for queryset in (
            SalesInvoice.objects.filter(owner_id=uid),
            SalesInvoicePayment.objects.filter(Q(owner_id=uid) | Q(received_by_id=uid)),
            SalesInvoicePaymentReversal.objects.filter(reversed_by_id=uid),
            SalesInvoiceCreditNote.objects.filter(created_by_id=uid),
            CustomerCreditMovement.objects.filter(created_by_id=uid),
            SupplierInvoice.objects.filter(supplier__owner_id=uid),
            SupplierInvoicePayment.objects.filter(Q(owner_id=uid) | Q(paid_by_id=uid)),
            SupplierInvoicePaymentReversal.objects.filter(reversed_by_id=uid),
            SupplierInvoiceAdjustment.objects.filter(created_by_id=uid),
            SupplierInvoiceRefund.objects.filter(Q(owner_id=uid) | Q(received_by_id=uid)),
            PurchaseOrder.objects.filter(Q(item__owner_id=uid) | Q(supplier__owner_id=uid)),
            Sale.objects.filter(Q(owner_id=uid) | Q(cashier_id=uid)),
        )
    )
    if has_financial_history:
        raise ProtectedError(
            "Financial history is retained; archive the account instead of deleting it.",
            {instance},
        )

    # Remove documents that might protect Items or Locations via CASCADE
    SalesInvoice.objects.filter(owner_id=uid).delete()
    SalesQuotation.objects.filter(owner_id=uid).delete()

    # Remove sales first (they have PROTECT to locations)
    # Sales belong to the company owner/location, not to the cashier who rang
    # them up. Sale.cashier uses SET_NULL so staff revocation preserves the
    # financial and audit record as intended.
    Sale.objects.filter(
        Q(owner_id=uid) | Q(location__owner_id=uid)
    ).delete()

    # Remove supplier invoices tied to user's suppliers
    SupplierInvoice.objects.filter(supplier__owner_id=uid).delete()

    # Remove purchase orders tied to user's suppliers or items
    PurchaseOrder.objects.filter(
        Q(supplier__owner_id=uid) | Q(item__owner_id=uid)
    ).delete()

    # Remove suppliers (owner cascade handles invoices, but ensure any remnants)
    Supplier.objects.filter(owner_id=uid).delete()

    # Remove locations the user owned
    Location.objects.filter(owner_id=uid).delete()

    # Remove items owned by the user
    Item.objects.filter(owner_id=uid).delete()


from .email_utils import send_verification_email

@receiver(post_save, sender=User)
def create_user_verification(sender, instance, created, **kwargs):
    if kwargs.get("raw") or not created or not instance.email:
        return

    code = str(100000 + secrets.randbelow(900000))
    UserVerification.objects.update_or_create(user=instance, defaults={"code": code})
    try:
        send_verification_email(instance.email, instance.username, code)
    except Exception:
        # Account creation must remain durable when the email provider is down.
        logger.exception("Could not send account verification email to user %s", instance.pk)


from django.core.mail import send_mail
from django.conf import settings

# REMOVE the import from the top to prevent circular dependency
# from .models import UserVerification 

@receiver(post_save, sender=User)
def send_welcome_email(sender, instance, created, **kwargs):
    if kwargs.get("raw") or not created or not instance.email:
        return

    # This is intentionally a welcome message only. Verification state and its
    # single-use code are owned by create_user_verification above.
    try:
        send_mail(
            subject="Welcome to QuickStock",
            message=f"Hello {instance.username}, welcome to QuickStock JA.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[instance.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Could not send welcome email to user %s", instance.pk)
