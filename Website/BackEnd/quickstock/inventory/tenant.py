"""Central tenant scoping for QuickStock JA.

Business data is scoped by the owning account (``owner``), which for staff is
their ``parent_admin``. Historically every view wrote ``.filter(owner=...)`` by
hand, so one omitted filter becomes a cross-tenant data leak. This module gives
each tenant-owned model one audited entry point.

Usage in views::

    from inventory.tenant import scoped

    invoices = scoped(SalesInvoice, request.user).filter(status="issued")

``TENANT_PATHS`` records how each model reaches its tenant. Top-level records
have a direct ``owner``; child records (invoice payments, sale lines, stock
rows) reach the tenant through their parent. A test asserts every entry is a
real, traversable lookup, so this table cannot silently drift from the schema.

Superusers receive an unfiltered queryset on purpose: platform oversight is a
deliberate, audited capability rather than an accidental bypass.
"""

from django.db.models import QuerySet

from .models import UserProfile


# model name -> LLM-safe lookup path from the model to the owning account.
TENANT_PATHS = {
    # Direct owner.
    "Brand": "owner",
    "Category": "owner",
    "Supplier": "owner",
    "Customer": "owner",
    "Location": "owner",
    "Item": "owner",
    "CashShift": "owner",
    "CashMovement": "owner",
    "CashReconciliation": "owner",
    "DailyCashCount": "owner",
    "Sale": "owner",
    "AuditLog": "user",
    "AccountingIntegration": "owner",
    "SalesQuotation": "owner",
    "SalesInvoice": "owner",
    # Platform billing lives in its own app; kept here so Payment is not forgotten.
    "Payment": "user",
    # Child records that inherit the tenant from their parent.
    "SaleItem": "sale__owner",
    "StockRecord": "item__owner",
    "StockTransfer": "item__owner",
    "PurchaseOrder": "supplier__owner",
    "SupplierInvoice": "supplier__owner",
    "SupplierInvoicePayment": "invoice__supplier__owner",
    "SalesInvoicePayment": "invoice__owner",
    "SalesInvoiceCreditNote": "invoice__owner",
    "CustomerCreditMovement": "customer__owner",
}

TENANT_OWNED_MODELS = tuple(TENANT_PATHS)


def effective_owner(user):
    """Return the business owner whose data ``user`` may touch."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    if getattr(user, "is_superuser", False):
        return user
    profile = UserProfile.for_user(user)
    if profile and profile.role in {"cashier", "manager"} and profile.parent_admin_id:
        parent = UserProfile.objects.select_related("user").filter(pk=profile.parent_admin_id).first()
        if parent and parent.user_id:
            return parent.user
    return user


def scoped(model, user) -> QuerySet:
    """Return a queryset limited to ``user``'s tenant."""
    queryset = model.objects.all()
    if getattr(user, "is_superuser", False):
        return queryset
    owner = effective_owner(user)
    if owner is None:
        return queryset.none()
    path = TENANT_PATHS.get(model.__name__)
    if not path:
        raise LookupError(
            f"{model.__name__} is not registered in TENANT_PATHS. "
            "Add its path to the owning account before querying it in a view."
        )
    return queryset.filter(**{path: owner})
