from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0053_salesinvoicepayment_credit_tracking"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomerNote",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("body", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="customer_notes_created", to=settings.AUTH_USER_MODEL)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="note_entries", to="inventory.customer")),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="customer_note_entries", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="customernote",
            index=models.Index(fields=["customer", "-created_at"], name="cust_note_customer_created_idx"),
        ),
        migrations.AddIndex(
            model_name="customernote",
            index=models.Index(fields=["owner"], name="cust_note_owner_idx"),
        ),
    ]
