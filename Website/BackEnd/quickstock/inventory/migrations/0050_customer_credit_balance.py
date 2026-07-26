from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0049_dailycashcount_machine_amounts"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="credit_balance",
            field=models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12),
        ),
    ]
