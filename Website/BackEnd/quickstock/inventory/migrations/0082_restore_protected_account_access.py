from django.db import migrations


TARGET_USERNAME = "KeviiDan"
ACCOUNT_ARCHIVE_REASON = "Account purge blocked because financial history must be retained."


def restore_protected_account_access(apps, schema_editor):
    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("inventory", "UserProfile")

    user = User.objects.filter(username__iexact=TARGET_USERNAME).first()
    if not user:
        return

    User.objects.filter(pk=user.pk).update(is_active=True)
    UserProfile.objects.filter(user_id=user.pk).update(
        status="active",
        is_archived=False,
        archived_at=None,
        archived_by=None,
        archive_reason="",
    )

    for model_name in ("Supplier", "Customer", "Location"):
        model = apps.get_model("inventory", model_name)
        model.objects.filter(
            owner_id=user.pk,
            is_archived=True,
            archive_reason=ACCOUNT_ARCHIVE_REASON,
        ).update(
            is_archived=False,
            archived_at=None,
            archived_by=None,
            archive_reason="",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0081_restore_accidentally_archived_account"),
    ]

    operations = [
        migrations.RunPython(
            restore_protected_account_access,
            migrations.RunPython.noop,
        ),
    ]
