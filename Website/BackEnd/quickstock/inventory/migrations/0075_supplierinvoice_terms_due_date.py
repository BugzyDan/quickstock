from datetime import timedelta

from django.db import migrations, models


def populate_supplier_invoice_due_dates(apps, schema_editor):
    SupplierInvoice = apps.get_model("inventory", "SupplierInvoice")
    term_days = {
        "due_on_receipt": 0,
        "net_7": 7,
        "net_15": 15,
        "net_30": 30,
        "net_60": 60,
    }
    for invoice in SupplierInvoice.objects.all().only("id", "date_issued", "payment_terms", "due_date"):
        days = term_days.get(invoice.payment_terms or "due_on_receipt", 0)
        SupplierInvoice.objects.filter(pk=invoice.pk).update(
            due_date=invoice.date_issued + timedelta(days=days)
        )


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0074_customer_is_tax_exempt_customer_trn"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplierinvoice",
            name="payment_terms",
            field=models.CharField(
                choices=[
                    ("due_on_receipt", "Pending (Account Payable)"),
                    ("net_7", "Net 7"),
                    ("net_15", "Net 15"),
                    ("net_30", "Net 30"),
                    ("net_60", "Net 60"),
                ],
                default="due_on_receipt",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="supplierinvoice",
            name="due_date",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(populate_supplier_invoice_due_dates, migrations.RunPython.noop),
    ]
