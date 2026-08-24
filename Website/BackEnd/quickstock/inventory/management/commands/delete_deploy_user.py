import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db.models.deletion import ProtectedError

from inventory.models import UserProfile


class Command(BaseCommand):
    help = "Delete or disable a deploy-configured user account."

    def handle(self, *args, **options):
        username = os.getenv("QUICKSTOCK_DELETE_USER_USERNAME", "").strip()
        if not username:
            self.stdout.write("Deploy user deletion is not configured; skipping.")
            return

        User = get_user_model()
        user = User.objects.filter(username=username).first()
        if not user:
            self.stdout.write(f"User '{username}' does not exist; nothing to delete.")
            return

        try:
            user.delete()
        except ProtectedError as exc:
            user.is_active = False
            user.is_staff = False
            user.is_superuser = False
            user.save(update_fields=["is_active", "is_staff", "is_superuser"])
            UserProfile.objects.filter(user=user).update(status="suspended")
            self.stdout.write(
                self.style.WARNING(
                    f"User '{username}' has protected financial history and was disabled instead: {exc}"
                )
            )
            return

        self.stdout.write(self.style.SUCCESS(f"User '{username}' deleted."))
