from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied


class SubscriptionTokenAuthentication(TokenAuthentication):
    """
    Enforce token age and subscription access for desktop-facing API requests.
    """

    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)

        max_age_seconds = max(0, int(getattr(settings, "API_TOKEN_MAX_AGE", 0) or 0))
        if max_age_seconds:
            expires_at = token.created + timedelta(seconds=max_age_seconds)
            if expires_at <= timezone.now():
                token.delete()
                raise AuthenticationFailed("Token expired. Please sign in again.")

        if user.is_superuser:
            return user, token

        from inventory.models import UserProfile

        profile = UserProfile.for_user(user)
        if profile.is_pro_active() or profile.is_trial_active():
            return user, token

        owner = profile.get_effective_plan_owner()
        if owner.status == "suspended":
            raise PermissionDenied("Account is suspended. Please renew your subscription.")
        if owner.plan == "TRIAL":
            raise PermissionDenied("Your trial has expired. Please subscribe to continue.")
        if owner.plan == "PRO":
            raise PermissionDenied("Your subscription expired. Please renew to continue.")
        raise PermissionDenied("QuickStock PRO subscription required.")
