from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0018_supplierinvoice_void_reason_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="terms_accepted",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="terms_accepted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="terms_version",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="signup_ip",
            field=models.GenericIPAddressField(blank=True, null=True),
        ),
    ]
