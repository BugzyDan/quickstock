from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0052_alter_salesinvoicepayment_payment_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="salesinvoicepayment",
            name="applied_customer_credit",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12),
        ),
        migrations.AddField(
            model_name="salesinvoicepayment",
            name="credited_customer_overpayment",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12),
        ),
    ]
