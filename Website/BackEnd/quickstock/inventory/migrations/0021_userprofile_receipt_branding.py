from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0020_userprofile_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="receipt_brand_name",
            field=models.CharField(blank=True, default="", max_length=150),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="receipt_logo_url",
            field=models.URLField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="receipt_contact_email",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="receipt_contact_phone",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="receipt_address",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
