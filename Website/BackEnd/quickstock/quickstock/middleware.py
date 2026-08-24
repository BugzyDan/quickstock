from datetime import datetime
import hashlib
import re
from django.conf import settings
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone


class SecuritySessionMiddleware(MiddlewareMixin):
    """
    Protect authenticated browser sessions by expiring idle sessions and
    forcing logout if the session starts presenting from a different browser
    fingerprint or an inactive account.
    """

    EXEMPT_PATH_PREFIXES = (
        "/login/",
        "/logout/",
        "/signup/",
        "/activate/",
        "/password-reset/",
        "/about/",
        "/privacy/",
        "/terms/",
        "/status/",
        "/support/",
        "/pricing/",
        "/admin/",
        "/static/",
        "/media/",
        "/api/",
        "/service-worker.js",
        "/manifest.webmanifest",
        "/favicon.ico",
    )

    def _fingerprint(self, request):
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        return hashlib.sha256(user_agent.encode("utf-8")).hexdigest()

    def _legacy_fingerprint(self, request):
        user_agent = request.META.get("HTTP_USER_AGENT", "")
        accept_language = request.META.get("HTTP_ACCEPT_LANGUAGE", "")
        return hashlib.sha256(f"{user_agent}|{accept_language}".encode("utf-8")).hexdigest()

    def _wants_json(self, request):
        accept = (request.headers.get("Accept") or "").lower()
        requested_with = (request.headers.get("X-Requested-With") or "").lower()
        return "application/json" in accept or requested_with == "xmlhttprequest"

    def _logout_response(self, request, user, reason, message_text):
        from inventory.views import _log_action

        _log_action(
            user,
            "logout",
            message_text,
            severity="warn",
            metadata={"reason": reason, "path": request.path},
        )
        from django.contrib.auth import logout

        logout(request)
        if self._wants_json(request):
            return JsonResponse(
                {"ok": False, "logout": True, "reason": reason, "redirect_url": settings.LOGIN_URL if settings.LOGIN_URL.startswith("/") else "/login/"},
                status=401,
            )
        return redirect("login")

    def process_view(self, request, view_func, view_args, view_kwargs):
        path = request.path or "/"
        if path == "/":
            return None
        if any(path.startswith(prefix) for prefix in self.EXEMPT_PATH_PREFIXES):
            return None

        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return None

        if not user.is_active:
            return self._logout_response(
                request,
                user,
                "account_inactive",
                "Automatic logout triggered because the account is inactive.",
            )

        now = timezone.now()
        timeout_seconds = int(getattr(settings, "QUICKSTOCK_IDLE_TIMEOUT_SECONDS", 900))
        current_fingerprint = self._fingerprint(request)
        current_legacy_fingerprint = self._legacy_fingerprint(request)
        stored_fingerprint = request.session.get("qs_session_fingerprint")
        stored_fingerprint_version = request.session.get("qs_session_fingerprint_version")
        last_activity_iso = request.session.get("qs_last_activity_at")
        last_activity = None
        enforce_fingerprint = bool(getattr(settings, "QUICKSTOCK_ENFORCE_SESSION_FINGERPRINT", False))

        if enforce_fingerprint:
            if (
                stored_fingerprint
                and stored_fingerprint_version == 2
                and stored_fingerprint != current_fingerprint
            ):
                return self._logout_response(
                    request,
                    user,
                    "fingerprint_mismatch",
                    "Automatic logout triggered because the session fingerprint changed.",
                )

            if (
                stored_fingerprint
                and stored_fingerprint_version not in {None, 2}
                and stored_fingerprint not in {current_fingerprint, current_legacy_fingerprint}
            ):
                return self._logout_response(
                    request,
                    user,
                    "fingerprint_mismatch",
                    "Automatic logout triggered because the session fingerprint changed.",
                )

        if last_activity_iso:
            try:
                last_activity = datetime.fromisoformat(last_activity_iso)
            except (TypeError, ValueError):
                last_activity = None
            if last_activity is not None:
                if timezone.is_naive(last_activity):
                    last_activity = timezone.make_aware(last_activity, timezone.get_current_timezone())
                if (now - last_activity).total_seconds() > timeout_seconds:
                    return self._logout_response(
                        request,
                        user,
                        "idle_timeout",
                        "Automatic logout triggered after inactivity timeout.",
                    )

        session_updates = {}
        if stored_fingerprint != current_fingerprint:
            session_updates["qs_session_fingerprint"] = current_fingerprint
        if stored_fingerprint_version != 2:
            session_updates["qs_session_fingerprint_version"] = 2

        touch_seconds = int(getattr(settings, "QUICKSTOCK_SESSION_TOUCH_SECONDS", 60))
        should_touch = last_activity is None or (now - last_activity).total_seconds() >= touch_seconds
        if should_touch:
            session_updates["qs_last_activity_at"] = now.isoformat()
        if not request.session.get("qs_session_authenticated_at"):
            session_updates["qs_session_authenticated_at"] = now.isoformat()

        if session_updates:
            for key, value in session_updates.items():
                request.session[key] = value
            request.session.modified = True
        return None


class ThemeMiddleware(MiddlewareMixin):
    """
    Ensures every HTML page carries the user's theme preference so
    dark mode CSS (`data-theme-applied="dark"`) activates everywhere,
    not only on pages that manually added the attribute.
    """

    def process_response(self, request, response):
        try:
            # Only touch HTML responses
            if response.get("Content-Type", "").split(";")[0].strip() != "text/html":
                return response

            content = response.content.decode(response.charset)

            # Resolve theme preference
            theme = "system"
            applied = None
            user = getattr(request, "user", None)
            if user and user.is_authenticated and hasattr(user, "profile"):
                theme = getattr(user.profile, "theme", "system") or "system"

            if theme in {"light", "dark"}:
                applied = theme

            # Skip if the template already declares a theme attribute on <html>
            if re.search(r"<html[^>]*data-theme", content, flags=re.IGNORECASE):
                return response

            # Inject attributes on first <html ...> occurrence
            attrs = f'data-theme=\"{theme}\"'
            if applied:
                attrs += f' data-theme-applied=\"{applied}\"'

            patched = re.sub(
                r"<html(\s|>)",
                lambda m: f"<html {attrs}{m.group(1)}",
                content,
                count=1,
            )

            # If dark mode is applied, also add a helper class for selectors
            if applied == "dark":
                if "force-dark" not in patched:
                    if re.search(r'<html[^>]*class="', patched, flags=re.IGNORECASE):
                        patched = re.sub(
                            r'(<html[^>]*class=")([^"]*)"',
                            r'\1\2 force-dark"',
                            patched,
                            count=1,
                            flags=re.IGNORECASE,
                        )
                    else:
                        patched = re.sub(
                            r"<html",
                            '<html class="force-dark"',
                            patched,
                            count=1,
                            flags=re.IGNORECASE,
                        )

            # Only update if we actually injected
            if patched != content:
                response.content = patched.encode(response.charset)
                if response.has_header("Content-Length"):
                    response["Content-Length"] = str(len(response.content))

        except Exception:
            # Fail silent; theming shouldn't break the page
            return response

        return response


class SubscriptionEnforcementMiddleware(MiddlewareMixin):
    """
    Enforce subscription expiry on every authenticated request.
    If trial/pro time is over, user is redirected to upgrade page.
    """

    EXEMPT_PATH_PREFIXES = (
        "/login/",
        "/logout/",
        "/signup/",
        "/activate/",
        "/password-reset/",
        "/upgrade/",
        "/pricing/",
        "/about/",
        "/privacy/",
        "/terms/",
        "/status/",
        "/support/",
        "/admin/",
        "/static/",
        "/media/",
        "/service-worker.js",
        "/manifest.webmanifest",
        "/favicon.ico",
    )

    def process_view(self, request, view_func, view_args, view_kwargs):
        path = request.path or "/"
        if path == "/":
            return None
        if any(path.startswith(prefix) for prefix in self.EXEMPT_PATH_PREFIXES):
            return None

        user = getattr(request, "user", None)
        if not user or not user.is_authenticated or user.is_superuser:
            return None

        # Avoid hard dependency at import time.
        from inventory.models import UserProfile

        profile = getattr(user, "profile", None) or UserProfile.for_user(user)
        owner = profile.get_effective_plan_owner()
        now = timezone.now()
        today = now.date()

        must_upgrade = False
        reason = "Subscription required. Please upgrade to continue."

        if owner.status == "pending":
            must_upgrade = True
            reason = "Complete payment to activate your account."
        elif owner.status == "suspended":
            must_upgrade = True
            reason = "Account is suspended. Please renew your subscription."
        elif owner.plan == "TRIAL":
            # No trial end = invalid trial state, enforce upgrade.
            if not owner.plan_end or now > owner.plan_end:
                must_upgrade = True
                reason = "Your trial has expired. Please subscribe to continue."
        elif owner.plan == "PRO":
            # Prefer date-based expiry when present.
            if owner.pro_expires and owner.pro_expires < today:
                must_upgrade = True
                reason = "Your subscription expired. Please renew to continue."
            elif not owner.pro_expires and owner.plan_end and now > owner.plan_end:
                must_upgrade = True
                reason = "Your subscription expired. Please renew to continue."
        else:
            # Any non-trial/non-pro plan cannot access protected app screens.
            must_upgrade = True
            reason = "A paid subscription is required. Please upgrade to continue."

        if must_upgrade:
            # Keep message from duplicating every request.
            if request.session.get("upgrade_notice") != reason:
                messages.error(request, reason)
                request.session["upgrade_notice"] = reason
            return redirect("upgrade")

        request.session.pop("upgrade_notice", None)
        return None
