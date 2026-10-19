"""Restore customers stranded by the archive-instead-of-delete bug.

`delete_customer` used to archive any customer that had financial history,
because `SalesInvoicePayment.customer` and `CustomerCreditMovement.customer` were
both `on_delete=PROTECT`. Those customers kept `is_archived=True`, and the
customer list (and every customer picker) hides archived rows by default, so they
silently vanished from the UI with no way back except the "Show archived" toggle.

Migration 0083 fixed the underlying delete path. This un-archives anything the
old code stranded, so those customers become visible again. No rows are
deleted -- this only clears the archive flags.
"""

from django.db import migrations

# archive_reason values written by the old buggy delete_customer branch.
LEGACY_ARCHIVE_REASONS = [
    "Customer retained because financial history is protected.",
    "protected_financial_history",
    "Financial history retained; record archived.",
]


def restore_stranded_customers(apps, schema_editor):
    Customer = apps.get_model("inventory", "Customer")

    Customer.objects.filter(
        is_archived=True,
        archive_reason__in=LEGACY_ARCHIVE_REASONS,
    ).update(
        is_archived=False,
        archived_at=None,
        archived_by=None,
        archive_reason="",
    )

    # Some rows were archived without a reason string (e.g. via the duplicate
    # merge path or a partial earlier cleanup). Only restore customers that are
    # clearly stranded: archived, no reason recorded, and carrying financial
    # history that the new delete path now handles correctly.
    Customer.objects.filter(
        is_archived=True,
        archive_reason="",
        sales_invoices__isnull=False,
    ).distinct().update(
        is_archived=False,
        archived_at=None,
        archived_by=None,
        archive_reason="",
    )

    Customer.objects.filter(
        is_archived=True,
        archive_reason="",
        sales_invoice_payment_ledger__isnull=False,
    ).distinct().update(
        is_archived=False,
        archived_at=None,
        archived_by=None,
        archive_reason="",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0083_customer_deletion_snapshots"),
    ]

    operations = [
        migrations.RunPython(
            restore_stranded_customers,
            migrations.RunPython.noop,
        ),
    ]