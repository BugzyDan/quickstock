import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from inventory.models import UserProfile


class Command(BaseCommand):
    help = "Idempotently create or repair the deploy-configured QuickStock superuser."

    def handle(self, *args, **options):
        username = os.getenv("QUICKSTOCK_SUPERUSER_USERNAME", "").strip()
        email = os.getenv("QUICKSTOCK_SUPERUSER_EMAIL", "").strip()
        password = os.getenv("QUICKSTOCK_SUPERUSER_PASSWORD", "")
        reset_password = os.getenv("QUICKSTOCK_SUPERUSER_RESET_PASSWORD", "").strip().lower()
        should_reset_password = reset_password in {"1", "true", "yes", "on"}

        if not username:
            self.stdout.write("Superuser bootstrap is not configured; skipping.")
            return

        User = get_user_model()
        existing_user = User.objects.filter(username=username).first()
        if not existing_user and not password:
            raise CommandError(
                "QUICKSTOCK_SUPERUSER_PASSWORD must be set when creating the deploy-configured superuser."
            )
        if should_reset_password and not password:
            raise CommandError(
                "QUICKSTOCK_SUPERUSER_PASSWORD must be set when QUICKSTOCK_SUPERUSER_RESET_PASSWORD is true."
            )

        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": email},
        )
        changed_fields = []
        for field_name, value in (
            ("email", email or user.email),
            ("is_staff", True),
            ("is_superuser", True),
            ("is_active", True),
        ):
            if getattr(user, field_name) != value:
                setattr(user, field_name, value)
                changed_fields.append(field_name)

        if password and (created or should_reset_password):
            user.set_password(password)
            changed_fields.append("password")
        if changed_fields:
            user.save(update_fields=list(dict.fromkeys(changed_fields)))

        profile = UserProfile.for_user(user)
        profile.role = "admin"
        profile.status = "active"
        if not profile.terms_accepted_at:
            profile.terms_accepted_at = timezone.now()
        profile.terms_accepted = True
        profile.save(update_fields=["role", "status", "terms_accepted", "terms_accepted_at"])

        action = "created" if created else "verified"
        self.stdout.write(self.style.SUCCESS(f"Superuser '{username}' {action}."))
