from django.db import migrations


TARGET_USERNAME = "KeviiDan"
ACCOUNT_ARCHIVE_REASON = "Account purge blocked because financial history must be retained."


def restore_accidentally_archived_account(apps, schema_editor):
    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("inventory", "UserProfile")

    user = User.objects.filter(username=TARGET_USERNAME).first()
    if not user:
        return

    profile = UserProfile.objects.filter(user_id=user.pk).first()
    if not profile:
        return

    was_account_archived = bool(
        profile.is_archived and profile.archive_reason == ACCOUNT_ARCHIVE_REASON
    )
    was_disabled_by_deploy_cleanup = bool(
        not user.is_active and profile.status == "suspended"
    )
    if not was_account_archived and not was_disabled_by_deploy_cleanup:
        return

    User.objects.filter(pk=user.pk).update(is_active=True)
    UserProfile.objects.filter(pk=profile.pk).update(
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
        ("inventory", "0080_create_shared_cache_table"),
    ]

    operations = [
        migrations.RunPython(
            restore_accidentally_archived_account,
            migrations.RunPython.noop,
        ),
    ]
