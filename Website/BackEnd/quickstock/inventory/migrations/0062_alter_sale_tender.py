from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0061_salesinvoice_collection_lifecycle"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sale",
            name="tender",
            field=models.CharField(
                choices=[
                    ("cash", "Cash"),
                    ("jamdex", "JAM-DEX"),
                    ("card", "Debit/Credit"),
                    ("bank_transfer", "Bank Transfer"),
                    ("mobile_money", "Mobile Money"),
                    ("other", "Other"),
                ],
                default="cash",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="sale",
            constraint=models.UniqueConstraint(
                condition=models.Q(("sync_token__isnull", False), models.Q(("sync_token", ""), _negated=True)),
                fields=("owner", "sync_token"),
                name="sale_owner_sync_token_uniq",
            ),
        ),
        migrations.AddField(
            model_name="salesinvoice",
            name="client_reference",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AddConstraint(
            model_name="salesinvoice",
            constraint=models.UniqueConstraint(
                condition=models.Q(("client_reference", ""), _negated=True),
                fields=("owner", "client_reference"),
                name="sales_inv_owner_client_ref_uniq",
            ),
        ),
    ]
