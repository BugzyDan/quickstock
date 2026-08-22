import os

from django.contrib.auth.hashers import make_password
from django.db import migrations
from django.utils import timezone


def bootstrap_superuser(apps, schema_editor):
    username = os.getenv("QUICKSTOCK_SUPERUSER_USERNAME", "").strip()
    email = os.getenv("QUICKSTOCK_SUPERUSER_EMAIL", "").strip()
    password = os.getenv("QUICKSTOCK_SUPERUSER_PASSWORD", "")

    if not username or not password:
        return

    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("inventory", "UserProfile")

    user, created = User.objects.get_or_create(
        username=username,
        defaults={
            "email": email,
            "is_staff": True,
            "is_superuser": True,
            "is_active": True,
            "password": make_password(password),
        },
    )

    update_fields = []
    if email and user.email != email:
        user.email = email
        update_fields.append("email")
    if not user.is_staff:
        user.is_staff = True
        update_fields.append("is_staff")
    if not user.is_superuser:
        user.is_superuser = True
        update_fields.append("is_superuser")
    if not user.is_active:
        user.is_active = True
        update_fields.append("is_active")

    reset_password = os.getenv("QUICKSTOCK_SUPERUSER_RESET_PASSWORD", "").strip().lower()
    if created or reset_password in {"1", "true", "yes", "on"}:
        user.password = make_password(password)
        if "password" not in update_fields:
            update_fields.append("password")

    if update_fields:
        user.save(update_fields=update_fields)

    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile_updates = []
    if profile.role != "admin":
        profile.role = "admin"
        profile_updates.append("role")
    if profile.status != "active":
        profile.status = "active"
        profile_updates.append("status")
    if not profile.terms_accepted:
        profile.terms_accepted = True
        profile.terms_accepted_at = timezone.now()
        profile_updates.extend(["terms_accepted", "terms_accepted_at"])
    if profile_updates:
        profile.save(update_fields=profile_updates)


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0078_salesinvoice_quotation_many"),
    ]

    operations = [
        migrations.RunPython(bootstrap_superuser, migrations.RunPython.noop),
    ]
