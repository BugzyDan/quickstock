from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0021_userprofile_receipt_branding"),
    ]

    operations = [
        migrations.CreateModel(
            name="Customer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("email", models.EmailField(blank=True, max_length=254, null=True)),
                ("phone", models.CharField(blank=True, max_length=30, null=True)),
                ("notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "owner",
                    models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="customers", to=settings.AUTH_USER_MODEL),
                ),
            ],
            options={
                "ordering": ["name"],
                "indexes": [
                    models.Index(fields=["owner"], name="customer_owner_idx"),
                    models.Index(fields=["email"], name="customer_email_idx"),
                    models.Index(fields=["phone"], name="customer_phone_idx"),
                ],
            },
        ),
    ]
