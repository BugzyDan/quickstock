from decimal import Decimal

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0054_customernote"),
    ]

    operations = [
        migrations.CreateModel(
            name="SalesInvoiceCreditNote",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
                ("reason", models.CharField(choices=[("pricing_adjustment", "Pricing Adjustment"), ("returned_items", "Returned Items"), ("damaged_goods", "Damaged Goods"), ("billing_error", "Billing Error"), ("goodwill", "Goodwill Credit"), ("other", "Other")], default="pricing_adjustment", max_length=32)),
                ("notes", models.TextField(blank=True, default="")),
                ("credited_customer_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sales_invoice_credit_notes", to=settings.AUTH_USER_MODEL)),
                ("invoice", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="credit_notes", to="inventory.salesinvoice")),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="salesinvoicecreditnote",
            index=models.Index(fields=["invoice"], name="sales_credit_invoice_idx"),
        ),
        migrations.AddIndex(
            model_name="salesinvoicecreditnote",
            index=models.Index(fields=["created_at"], name="sales_credit_created_idx"),
        ),
    ]
