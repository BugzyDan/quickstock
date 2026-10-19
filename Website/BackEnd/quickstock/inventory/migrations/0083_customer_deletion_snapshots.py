"""Allow customers to be permanently deleted instead of archived.

The invoice payment ledger and the customer credit ledger both hard-protected the
Customer FK, so every customer with financial history could only ever be archived.
Both ledgers now detach (SET_NULL / explicit null-out) and carry a name snapshot,
matching the existing actor-snapshot convention in this project.
"""

from django.db import migrations, models
import django.db.models.deletion


def backfill_customer_name_snapshots(apps, schema_editor):
    Customer = apps.get_model("inventory", "Customer")
    CustomerCreditMovement = apps.get_model("inventory", "CustomerCreditMovement")
    SalesInvoicePayment = apps.get_model("inventory", "SalesInvoicePayment")

    for customer in Customer.objects.all().only("id", "name"):
        CustomerCreditMovement.objects.filter(customer_id=customer.id).update(
            customer_name=customer.name
        )
        SalesInvoicePayment.objects.filter(customer_id=customer.id).update(
            customer_name=customer.name
        )


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0082_restore_protected_account_access"),
    ]

    operations = [
        migrations.AddField(
            model_name="salesinvoicepayment",
            name="customer_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="customercreditmovement",
            name="customer_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AlterField(
            model_name="customercreditmovement",
            name="customer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="credit_movements",
                to="inventory.customer",
            ),
        ),
        migrations.RunPython(
            backfill_customer_name_snapshots,
            migrations.RunPython.noop,
        ),
    ]