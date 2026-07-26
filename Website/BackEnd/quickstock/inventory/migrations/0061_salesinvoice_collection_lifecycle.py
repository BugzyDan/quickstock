import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def mark_existing_invoices_untracked(apps, schema_editor):
    SalesInvoice = apps.get_model("inventory", "SalesInvoice")
    SalesInvoice.objects.all().update(collection_status="untracked")


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0060_supplier_origin_and_country"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="salesinvoice",
            name="collected_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="salesinvoice",
            name="collection_recorded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="sales_invoice_collections_recorded",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="salesinvoice",
            name="collection_status",
            field=models.CharField(
                choices=[
                    ("untracked", "Pickup not recorded"),
                    ("pending_payment", "Pending full payment"),
                    ("awaiting_collection", "Remaining in store"),
                    ("collected_immediately", "Collected same time"),
                    ("collected_after_hold", "Delivered from uncollected"),
                ],
                db_index=True,
                default="pending_payment",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="salesinvoice",
            name="collection_status_changed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_existing_invoices_untracked, migrations.RunPython.noop),
    ]
