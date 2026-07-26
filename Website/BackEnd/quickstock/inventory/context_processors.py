from django.conf import settings
from django.urls import reverse

from .models import Item, UserProfile


def _get_starter_item_limit():
    try:
        return int(getattr(settings, "STARTER_ITEM_LIMIT", 100))
    except Exception:
        return 100


def starter_plan_context(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    profile = getattr(user, "profile", None)
    if profile is None:
        profile = UserProfile.for_user(user)

    is_pro_user = profile.is_pro_active()
    if is_pro_user:
        return {
            "is_pro_user": True,
            "show_starter_banner": False,
        }

    owner = profile.effective_owner
    starter_item_limit = _get_starter_item_limit()
    starter_items_used = Item.objects.filter(owner=owner, is_deleted=False).count()

    return {
        "is_pro_user": False,
        "show_starter_banner": True,
        "starter_item_limit": starter_item_limit,
        "starter_items_used": starter_items_used,
        "starter_items_remaining": max(0, starter_item_limit - starter_items_used),
    }


def session_security_context(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    return {
        "quickstock_idle_timeout_seconds": int(getattr(settings, "QUICKSTOCK_IDLE_TIMEOUT_SECONDS", 900)),
        "quickstock_security_heartbeat_seconds": int(
            getattr(settings, "QUICKSTOCK_SECURITY_HEARTBEAT_SECONDS", 300)
        ),
        "quickstock_session_heartbeat_url": reverse("session_heartbeat"),
        "quickstock_session_expire_url": reverse("expire_session"),
        "quickstock_login_url": reverse("login"),
    }
