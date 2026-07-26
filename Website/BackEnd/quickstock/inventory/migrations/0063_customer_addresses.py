from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0062_alter_sale_tender"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="business_address",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="customer",
            name="physical_address",
            field=models.TextField(blank=True, default=""),
        ),
    ]
