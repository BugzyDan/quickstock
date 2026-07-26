from django.conf import settings
from django.db import migrations, models


def assign_location_owners(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Location = apps.get_model("inventory", "Location")

    if not Location.objects.exists():
        return

    owner = User.objects.order_by("id").first()
    if owner is None:
        owner = User.objects.create(
            username="system",
            is_staff=True,
            is_superuser=True,
            is_active=True,
        )
        owner.set_unusable_password()
        owner.save(update_fields=["password"])

    Location.objects.filter(owner__isnull=True).update(owner=owner)


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0008_item_owner"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.AddField(
            model_name="location",
            name="owner",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=models.CASCADE,
                related_name="locations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(assign_location_owners, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="location",
            name="owner",
            field=models.ForeignKey(
                on_delete=models.CASCADE,
                related_name="locations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
