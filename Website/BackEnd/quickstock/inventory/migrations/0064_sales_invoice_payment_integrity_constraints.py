from decimal import Decimal

from django.db import migrations, models
import django.db.models.expressions


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0063_customer_addresses"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="salesinvoicepayment",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount__gt", Decimal("0.00"))),
                name="sales_pay_amount_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="salesinvoicepayment",
            constraint=models.CheckConstraint(
                condition=models.Q(("applied_customer_credit__gte", Decimal("0.00"))),
                name="sales_pay_credit_nonnegative",
            ),
        ),
        migrations.AddConstraint(
            model_name="salesinvoicepayment",
            constraint=models.CheckConstraint(
                condition=models.Q(("credited_customer_overpayment__gte", Decimal("0.00"))),
                name="sales_pay_overpay_nonnegative",
            ),
        ),
        migrations.AddConstraint(
            model_name="salesinvoicepayment",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "applied_customer_credit__lte",
                        django.db.models.expressions.F("amount"),
                    )
                ),
                name="sales_pay_credit_lte_amount",
            ),
        ),
        migrations.AddConstraint(
            model_name="salesinvoicepayment",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "credited_customer_overpayment__lte",
                        django.db.models.expressions.F("amount"),
                    )
                ),
                name="sales_pay_overpay_lte_amount",
            ),
        ),
    ]
