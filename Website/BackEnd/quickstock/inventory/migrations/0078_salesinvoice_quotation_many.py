from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0077_salesinvoice_due_date"),
    ]

    operations = [
        migrations.AlterField(
            model_name="salesinvoice",
            name="quotation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="converted_invoices",
                to="inventory.salesquotation",
            ),
        ),
    ]
