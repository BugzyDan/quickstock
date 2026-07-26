from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0019_userprofile_terms_acceptance"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="status",
            field=models.CharField(
                choices=[("pending", "Pending"), ("active", "Active"), ("suspended", "Suspended")],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name="userprofile",
            index=models.Index(fields=["status"], name="inventory_u_status_idx"),
        ),
    ]
