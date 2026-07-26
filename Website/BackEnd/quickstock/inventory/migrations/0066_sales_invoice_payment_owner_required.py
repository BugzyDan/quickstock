import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def fill_missing_payment_owners(apps, schema_editor):
    SalesInvoicePayment = apps.get_model("inventory", "SalesInvoicePayment")

    for payment in (
        SalesInvoicePayment.objects.filter(owner__isnull=True)
        .select_related("invoice")
        .iterator()
    ):
        payment.owner_id = payment.invoice.owner_id
        payment.save(update_fields=["owner"])


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0065_customercreditmovement_salesinvoicepaymentreversal_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            fill_missing_payment_owners,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="salesinvoicepayment",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sales_invoice_payment_ledger",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
