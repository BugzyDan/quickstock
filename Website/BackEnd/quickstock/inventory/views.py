import csv
import json
import logging
import calendar
import re
from types import SimpleNamespace
from .forms import CustomerForm
from decimal import Decimal, InvalidOperation
import secrets
import string
from . import _get_tax_rate_for_location
from django.db import models
from django.db.models import Q
import uuid
import zipfile

import requests
from PIL import Image, UnidentifiedImageError
from io import BytesIO

from datetime import datetime, timedelta
from xml.sax.saxutils import escape
from .decorators import pro_required
import hashlib
from django.contrib.sessions.models import Session
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import (
    authenticate,
    login,
    logout,
    update_session_auth_hash,
)
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import Http404
from django.core.mail import EmailMessage
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models.deletion import ProtectedError
from django.db import IntegrityError
from django.db.utils import OperationalError, ProgrammingError
from django.db.models import (
    Q,
    Sum,
    Count,
    Avg,
    Max,
    Min,
    F,
    OuterRef,
    DecimalField,
    ExpressionWrapper,
    IntegerField,
    Subquery,
)
from django.db.models.functions import TruncDay, TruncWeek, TruncMonth, TruncYear, Coalesce
from django.db.models import Value
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.utils.dateparse import parse_datetime
from django.utils.encoding import force_bytes, force_str
from django.utils.http import (
    url_has_allowed_host_and_scheme,
    urlsafe_base64_decode,
    urlsafe_base64_encode,
)
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import os

from urllib.parse import urlencode, urlsplit

# ---------------------------
# Local imports
# ---------------------------
from .decorators import role_required
from .accounting import get_or_create_accounting_integration, queue_existing_accounting_data
from .email_utils import email_delivery_status, get_delivery_connection
from .forms import SupplierForm
from .models import (
    AccountingIntegration,
    AccountingSyncRecord,
    CashShift,
    CashMovement,
    CashReconciliation,
    AuditLog,
    Brand,
    Category,
    COUNTRY_CHOICES,
    Customer,
    CustomerCreditMovement,
    DailyCashCount,
    Item,
    Location,
    Payment,
    ReceiptEmailLog,
    ReceiptRevision,
    PurchaseOrder,
    Sale,
    SaleItem,
    SalesInvoice,
    SalesInvoiceCreditNote,
    SalesInvoiceItem,
    SalesInvoicePayment,
    SalesInvoicePaymentReversal,
    SalesQuotation,
    SalesQuotationItem,
    StockRecord,
    StockTransfer,
    Supplier,
    SupplierInvoice,
    SupplierInvoiceAdjustment,
    SupplierInvoiceRefund,
    SupplierInvoicePayment,
    SupplierInvoicePaymentReversal,
    UserProfile,
    
)
from .health import get_runtime_health
from .sales import SaleWorkflowError, finalize_sale
from .storage import load_seed_products_for_owner
from .tokens import token_generator

# ---------------------------
# Constants
# ---------------------------
TAX_RATES_BY_COUNTRY = {
    "JM": Decimal("0.15"),
    "TT": Decimal("0.125"),
    "BB": Decimal("0.175"),
    "GY": Decimal("0.14"),
    "LC": Decimal("0.125"),
    "INT": Decimal("0.00"),
}



TAX_LABELS_BY_COUNTRY = {
    "JM": "GCT",
    "TT": "VAT",
    "BB": "VAT",
    "GY": "VAT",
    "LC": "VAT",
    "INT": "TAX",
}

CASH_DENOMINATIONS = (
    (5000, "qty_5000"),
    (2000, "qty_2000"),
    (1000, "qty_1000"),
    (500, "qty_500"),
    (100, "qty_100"),
    (50, "qty_50"),
    (20, "qty_20"),
    (10, "qty_10"),
    (5, "qty_5"),
    (1, "qty_1"),
)

AUTH_BILLING_MESSAGE_PATTERNS = (
    "welcome back",
    "welcome to quickstock ja",
    "trial active until",
    "trial expires",
    "trial expired",
    "subscription expired",
    "subscription overdue",
    "account suspended",
    "complete payment to activate your account",
    "paid subscription is required",
    "quickstock ja pro",
    "features are now unlocked",
    "payment successful",
)


def _prune_auth_billing_messages(request):
    """
    Remove stale auth/billing flashes so redirects don't stack contradictory notices.
    """
    storage = messages.get_messages(request)
    retained = []
    for msg in storage:
        text = str(getattr(msg, "message", "") or "").lower()
        if any(pattern in text for pattern in AUTH_BILLING_MESSAGE_PATTERNS):
            continue
        retained.append((msg.level, msg.message, getattr(msg, "extra_tags", "")))

    storage.used = True
    for level, text, extra_tags in retained:
        messages.add_message(request, level, text, extra_tags=extra_tags)


INVENTORY_CAPACITY = 1000
MAX_DB_QUANTITY = 2147483647

logger = logging.getLogger("inventory")

FINANCIAL_AUDIT_ACTIONS = {
    "sale",
    "payment",
    "invoice",
    "supplier_payment",
    "supplier_invoice",
    "customer",
    "account",
    "location",
    "user",
}

# ---------------------------
# Helper functions
# ---------------------------

def _cleanup_audit_logs(user=None):
    """Remove audit logs older than the retention period."""
    retention_days = cache.get("audit_retention_days")

    if retention_days is None:
        retention_days = getattr(settings, "AUDIT_LOG_RETENTION_DAYS", 90)
    try:
        retention_days = int(retention_days)
    except Exception:
        retention_days = 90

    if retention_days <= 0:
        return

    cutoff = timezone.now() - timedelta(days=retention_days)
    logs = AuditLog.objects.all() if user is None else _audit_log_queryset_for_user(user)
    logs.filter(created_at__lt=cutoff).exclude(action__in=FINANCIAL_AUDIT_ACTIONS).delete()


def _safe_decimal(value, default="0"):
    """Safely convert a value to Decimal."""
    try:
        return Decimal(str(value).replace(",", "").strip())
    except Exception:
        return Decimal(str(default))

def _safe_int(value, default=0):
    """Safely convert a value to integer."""
    try:
        return int(float(str(value)))
    except (ValueError, TypeError, Exception):
        return default


def _format_money(value):
    return f"{Decimal(value or '0.00'):,.2f}"


def _clean_inventory_capacity(value):
    return max(1, _safe_int(value, INVENTORY_CAPACITY))


def _normalize_email_address(value: str) -> str:
    """Accept any syntactically valid email address, regardless of provider."""
    email = (value or "").strip().lower()
    validate_email(email)
    return email


def _social_provider_settings(provider: str) -> dict:
    provider_key = str(provider or "").strip().lower()
    providers = getattr(settings, "SOCIAL_AUTH_PROVIDERS", {}) or {}
    raw_config = providers.get(provider_key, {}) or {}
    config = dict(raw_config)

    if provider_key == "microsoft":
        tenant_id = config.get("tenant_id") or "common"
        config.setdefault(
            "authorize_url",
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize",
        )
        config.setdefault(
            "token_url",
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        )
        config.setdefault("userinfo_url", "https://graph.microsoft.com/oidc/userinfo")

    config["provider"] = provider_key
    config["enabled"] = bool(config.get("client_id") and config.get("client_secret"))
    return config


def _available_social_login_providers():
    providers = []
    for provider_key in ("google", "microsoft"):
        config = _social_provider_settings(provider_key)
        providers.append(
            {
                "key": provider_key,
                "label": config.get("label") or provider_key.title(),
                "enabled": config.get("enabled", False),
                "start_url": reverse("social_login_start", args=[provider_key]),
            }
        )
    return providers


def _render_login(request, status=200):
    return render(
        request,
        "inventory/login.html",
        {
            "social_login_providers": _available_social_login_providers(),
        },
        status=status,
    )


def _username_for_login_identifier(identifier: str) -> str:
    value = (identifier or "").strip()
    if not value or "@" not in value:
        return value

    matches = list(User.objects.filter(email__iexact=value).values_list("username", flat=True)[:2])
    return matches[0] if len(matches) == 1 else value


DOCUMENT_META_PREFIX = "[[QS_META]]"
PAYMENT_CHANNEL_LABELS = {
    "pos": "POS",
    "cash": "Cash",
    "tap2pay": "Tap2Pay",
    "scan2pay": "Scan2Pay",
    "wipay2me": "WiPay",
    "jamdex": "JamDex",
    "invoice": "Invoice",
}


def _split_document_notes(notes: str) -> tuple[str, dict]:
    raw_notes = str(notes or "").strip()
    if not raw_notes.startswith(DOCUMENT_META_PREFIX):
        return raw_notes, {}

    first_line, _, remainder = raw_notes.partition("\n")
    payload = first_line[len(DOCUMENT_META_PREFIX):].strip()
    try:
        meta = json.loads(payload) if payload else {}
    except Exception:
        return raw_notes, {}
    return remainder.strip(), meta if isinstance(meta, dict) else {}


def _compose_document_notes(notes: str, meta: dict) -> str:
    clean_notes = str(notes or "").strip()
    compact_meta = {key: value for key, value in (meta or {}).items() if value not in (None, "", [], {})}
    if not compact_meta:
        return clean_notes

    meta_line = f"{DOCUMENT_META_PREFIX} {json.dumps(compact_meta, separators=(',', ':'))}"
    if clean_notes:
        return f"{meta_line}\n{clean_notes}"
    return meta_line


def _payment_channel_label(channel: str) -> str:
    return PAYMENT_CHANNEL_LABELS.get(str(channel or "").strip().lower(), "Cash Register")


def _daily_summary_comment_payload(notes: str) -> dict:
    public_notes, meta = _split_document_notes(notes)
    fallback_comment = ""
    if not public_notes and meta.get("source") == "cash_register":
        fallback_comment = _payment_channel_label(meta.get("payment_channel"))
    return {
        "display": public_notes or fallback_comment,
        "value": public_notes,
        "meta": meta,
        "locked": bool(public_notes),
        "is_generated": bool(fallback_comment) and not public_notes,
    }


def _document_adjustments_from_request(request, default_tax_rate: Decimal, customer=None) -> tuple[dict, Decimal]:
    tax_mode = (request.POST.get("tax_mode") or "default").strip().lower()
    if tax_mode not in {"default", "none", "custom"}:
        tax_mode = "default"

    tax_rate_percent = _safe_decimal(request.POST.get("tax_rate_percent", "0"), default="0")
    customer_tax_exempt = bool(getattr(customer, "is_tax_exempt", False))
    if customer_tax_exempt:
        tax_mode = "none"
        tax_rate = Decimal("0.00")
        tax_rate_percent = Decimal("0.00")
    elif tax_mode == "default":
        tax_rate = default_tax_rate
        tax_rate_percent = (default_tax_rate * Decimal("100")).quantize(Decimal("0.01"))
    elif tax_mode == "none":
        tax_rate = Decimal("0.00")
        tax_rate_percent = Decimal("0.00")
    else:
        tax_rate_percent = max(Decimal("0.00"), tax_rate_percent)
        tax_rate = (tax_rate_percent / Decimal("100")).quantize(Decimal("0.0001"))

    discount_type = (request.POST.get("discount_type") or "flat").strip().lower()
    if discount_type not in {"flat", "percent"}:
        discount_type = "flat"
    discount_value = max(Decimal("0.00"), _safe_decimal(request.POST.get("discount_value", "0"), default="0"))

    meta = {
        "tax_mode": tax_mode,
        "tax_rate_percent": str(tax_rate_percent.quantize(Decimal("0.01"))),
        "discount_type": discount_type,
        "discount_value": str(discount_value.quantize(Decimal("0.01"))),
    }
    if customer_tax_exempt:
        meta["customer_tax_exempt"] = "true"
    return meta, tax_rate


def _calculate_document_totals(
    subtotal: Decimal,
    taxable_subtotal: Decimal,
    tax_rate: Decimal,
    discount_type: str,
    discount_value: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    subtotal = (subtotal or Decimal("0.00")).quantize(Decimal("0.01"))
    taxable_subtotal = (taxable_subtotal or Decimal("0.00")).quantize(Decimal("0.01"))
    discount_value = max(Decimal("0.00"), (discount_value or Decimal("0.00")).quantize(Decimal("0.01")))

    if subtotal <= Decimal("0.00"):
        return Decimal("0.00"), Decimal("0.00"), Decimal("0.00")

    if discount_type == "percent":
        discount_amount = ((subtotal * discount_value) / Decimal("100")).quantize(Decimal("0.01"))
    else:
        discount_amount = discount_value

    discount_amount = max(Decimal("0.00"), min(discount_amount, subtotal))
    taxable_ratio = (taxable_subtotal / subtotal) if subtotal > Decimal("0.00") else Decimal("0.00")
    taxable_discount = (discount_amount * taxable_ratio).quantize(Decimal("0.01"))
    adjusted_taxable = max(Decimal("0.00"), taxable_subtotal - taxable_discount)
    tax_amount = (adjusted_taxable * tax_rate).quantize(Decimal("0.01"))
    total_amount = max(Decimal("0.00"), subtotal - discount_amount + tax_amount).quantize(Decimal("0.01"))
    return discount_amount, tax_amount, total_amount


def _document_summary_context(notes: str, subtotal: Decimal, tax_amount: Decimal, total_amount: Decimal, fallback_tax_rate: Decimal) -> dict:
    public_notes, meta = _split_document_notes(notes)
    discount_type = meta.get("discount_type", "flat")
    discount_value = _safe_decimal(meta.get("discount_value", "0"), default="0")
    tax_rate_percent = _safe_decimal(meta.get("tax_rate_percent", str((fallback_tax_rate or Decimal("0.00")) * Decimal("100"))), default="0")
    tax_mode = meta.get("tax_mode", "default")
    discount_amount, _, _ = _calculate_document_totals(
        subtotal or Decimal("0.00"),
        subtotal or Decimal("0.00"),
        Decimal("0.00"),
        discount_type,
        discount_value,
    )
    return {
        "notes_display": public_notes,
        "document_meta": meta,
        "discount_amount": discount_amount,
        "discount_type": discount_type,
        "discount_value": discount_value,
        "tax_mode": tax_mode,
        "tax_rate_percent": tax_rate_percent,
        "tax_amount": tax_amount or Decimal("0.00"),
        "total_amount": total_amount or Decimal("0.00"),
    }





def _send_expiry_reminder(user, expiry_date, plan_label):
    """
    Send a one-per-day reminder when a plan is close to expiring (<=7 days).
    Uses cache to avoid spamming.
    """

    if not user or not user.email or not expiry_date:
        return

    today = timezone.now().date()
    days_left = (expiry_date - today).days
    if days_left < 0 or days_left > 7:
        return

    cache_key = f"expiry_reminder_sent:{user.id}:{expiry_date.isoformat()}"
    if cache.get(cache_key):
        return

    subject = "QuickStock JA subscription expiring soon"
    body = (
        f"Hi {user.username},\n\n"
        f"Your QuickStock JA {plan_label} access expires in {days_left} day(s) on {expiry_date}.\n"
        f"Please renew via WiPay to avoid subscription pause.\n\n"
        "— QuickStock JA"
    )
    try:
        EmailMessage(subject, body, to=[user.email]).send(fail_silently=True)
    finally:
        # even if send fails, avoid tight loops
        cache.set(cache_key, True, 60 * 60 * 24)


def _device_fingerprint(request):
    """Bind an OTP to the browser without depending on a load balancer IP."""
    ua = request.META.get("HTTP_USER_AGENT", "")
    return hashlib.sha256(ua.encode("utf-8")).hexdigest()


def _client_ip_for_request(request):
    forwarded_for = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded_for:
        first_hop = forwarded_for.split(",")[0].strip()
        if first_hop:
            return first_hop
    return request.META.get("REMOTE_ADDR", "unknown")


def _consume_support_ticket_quota(request):
    """Return whether this user/IP may dispatch another support email."""
    limit = max(int(getattr(settings, "SUPPORT_TICKET_RATE_LIMIT", 5)), 1)
    window = max(int(getattr(settings, "SUPPORT_TICKET_RATE_WINDOW", 900)), 1)
    if request.user.is_authenticated:
        identity = f"user:{request.user.pk}"
    else:
        # REMOTE_ADDR cannot be replaced by a client-supplied forwarding header.
        identity = f"ip:{request.META.get('REMOTE_ADDR', 'unknown')}"
    identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    cache_key = f"support_ticket_rate:{identity_hash}"

    if cache.add(cache_key, 1, timeout=window):
        return True, window
    try:
        attempt_count = cache.incr(cache_key)
    except (ValueError, NotImplementedError):
        attempt_count = int(cache.get(cache_key, 1)) + 1
        cache.set(cache_key, attempt_count, timeout=window)
    return attempt_count <= limit, window


def _summarize_user_agent(user_agent):
    ua = (user_agent or "").strip()
    if not ua:
        return "Unknown device"

    browser = "Browser session"
    browser_signatures = [
        ("Edg/", "Microsoft Edge"),
        ("OPR/", "Opera"),
        ("Chrome/", "Google Chrome"),
        ("Firefox/", "Mozilla Firefox"),
        ("Safari/", "Safari"),
    ]
    for token, label in browser_signatures:
        if token in ua:
            browser = label
            break

    platform = "Unknown platform"
    platform_signatures = [
        ("Windows", "Windows"),
        ("Mac OS X", "macOS"),
        ("Android", "Android"),
        ("iPhone", "iPhone"),
        ("iPad", "iPad"),
        ("Linux", "Linux"),
    ]
    for token, label in platform_signatures:
        if token in ua:
            platform = label
            break

    return f"{browser} on {platform}"


def _prime_authenticated_session(request):
    now = timezone.now().isoformat()
    user_agent = request.META.get("HTTP_USER_AGENT", "")
    middleware_fingerprint = hashlib.sha256(user_agent.encode("utf-8")).hexdigest()

    request.session["qs_session_fingerprint"] = middleware_fingerprint
    request.session["qs_session_fingerprint_version"] = 2
    request.session["qs_last_activity_at"] = now
    request.session["qs_session_authenticated_at"] = now
    request.session.modified = True


def _deliver_login_otp_email(user_id, username, email, code):
    subject = "Your QuickStock JA login code"
    body = (
        f"Hi {username},\n\n"
        f"Use this code to complete your QuickStock JA login: {code}\n"
        f"The code expires in 10 minutes.\n\n"
        "If you did not attempt to sign in, please reset your password."
    )
    try:
        connection = get_delivery_connection()
        sent_count = EmailMessage(subject, body, to=[email], connection=connection).send(fail_silently=False)
        if sent_count <= 0:
            logger.error("Login OTP email was not accepted for delivery for user %s.", user_id)
            return False
        return True
    except Exception:
        logger.exception("Could not send login OTP email to user %s", user_id)
        return False


def _send_login_otp(user):
    if not user.email:
        logger.warning("Cannot send login OTP for user %s without an email address.", user.pk)
        return False
    delivery_status = email_delivery_status()
    if not delivery_status["ok"]:
        logger.error(
            "Cannot send login OTP for user %s: %s",
            user.pk,
            delivery_status["detail"],
        )
        return False

    code = f"{secrets.randbelow(900000) + 100000:06d}"
    cache.set(f"login_otp:{user.id}", code, timeout=600)  # 10 minutes
    if _deliver_login_otp_email(user.id, user.username, user.email, code):
        return True
    cache.delete(f"login_otp:{user.id}")
    return False


def _login_otp_failure_key(request, user):
    # Keep the guess limit account-scoped. Proxy headers can change between
    # requests and must not let an attacker reset the OTP attempt counter.
    return f"login_otp_fail:user:{user.id}"


def _superuser_otp_bypass_allowed(user):
    configured_username = str(getattr(settings, "QUICKSTOCK_SUPERUSER_USERNAME", "") or "").strip()
    recovery_usernames = {name.lower() for name in (configured_username, "KeviiDan") if name}
    if user.is_active and user.username.lower() in recovery_usernames:
        return True
    if not getattr(settings, "QUICKSTOCK_ALLOW_SUPERUSER_OTP_BYPASS", False):
        return False
    return bool(
        configured_username
        and user.is_active
        and user.username.lower() == configured_username.lower()
    )


def _complete_superuser_otp_bypass(request, user):
    login(request, user)
    _prime_authenticated_session(request)
    _log_action(
        user,
        "login_otp_bypass",
        "Configured superuser bypassed email OTP recovery gate.",
        severity="warn",
    )
    messages.warning(
        request,
        "Emergency admin recovery mode is active. Turn off OTP bypass after fixing email delivery.",
    )
    return redirect("login_redirect")


def _begin_login_otp_challenge(request, user):
    if not _send_login_otp(user):
        if _superuser_otp_bypass_allowed(user):
            return _complete_superuser_otp_bypass(request, user)
        messages.error(
            request,
            "We could not send your verification email. Please contact support or try again shortly.",
        )
        return _render_login(request, status=503)
    request.session["otp_user_id"] = user.id
    request.session["otp_pending_ip"] = _client_ip_for_request(request)
    request.session["otp_pending_fp"] = _device_fingerprint(request)
    messages.info(request, "Verification code sent to your email.")
    return render(request, "inventory/login_otp.html", {"username": user.username})


def _clear_login_otp_challenge(request, user):
    cache.delete(f"login_otp:{user.id}")
    for session_key in ("otp_user_id", "otp_pending_ip", "otp_pending_fp"):
        request.session.pop(session_key, None)


def _handle_inactive_login_attempt(request, user):
    profile = UserProfile.for_user(user)

    if profile.plan == "TRIAL":
        resend_key = f"activation_resend:{user.username}".lower()
        if not cache.get(resend_key):
            if _send_activation_email(request, user):
                cache.set(resend_key, True, timeout=300)
                messages.info(request, "Trial not activated. A new verification link has been sent.")
            else:
                messages.error(
                    request,
                    "Your account is awaiting activation, but email delivery is unavailable. Please contact support.",
                )
        else:
            messages.info(request, "Please check your email to activate your 14-day free trial.")
    else:
        messages.error(request, "Payment is required before your account can be activated. Please complete checkout.")
    return _render_login(request)


def _send_new_device_alert(user, ip_addr, ua):
    subject = "New device sign-in to QuickStock JA"
    body = (
        f"Hi {user.username},\n\n"
        f"A new device just signed in to your account.\n"
        f"IP: {ip_addr}\n"
        f"User-Agent: {ua[:200]}\n\n"
        "If this wasn’t you, reset your password immediately."
    )
    connection = get_delivery_connection()
    EmailMessage(subject, body, to=[user.email], connection=connection).send(fail_silently=True)


def _get_active_profile(request):
    """
    Returns the Admin's Profile for branding (logos/business names).
    If the user is staff, it pulls the parent_admin's profile.
    """
    if not request.user.is_authenticated:
        return None
        
    profile = UserProfile.for_user(request.user)
    
    # Return the branding owner (the boss) or the user's own profile
    return profile.parent_admin if profile.parent_admin else profile

def _get_tax_rate_for_country(country_code):
    code = (country_code or "JM").upper()
    cache_key = f"tax_rate:{code}"
    cached = cache.get(cache_key)
    if cached is None:
        return TAX_RATES_BY_COUNTRY.get(code, TAX_RATES_BY_COUNTRY["JM"])
    try:
        return Decimal(str(cached))
    except Exception:
        return TAX_RATES_BY_COUNTRY.get(code, TAX_RATES_BY_COUNTRY["JM"])

def _get_tax_label_for_location(location):
    if not location:
        return TAX_LABELS_BY_COUNTRY["JM"]
    country_code = (location.country_code or "JM").upper()
    return TAX_LABELS_BY_COUNTRY.get(country_code, "Tax")


def _normalize_billing_cycle(value):
    return "monthly" if str(value).lower() == "monthly" else "yearly"


def _billing_cycle_days(cycle):
    return 30 if _normalize_billing_cycle(cycle) == "monthly" else 365


def _get_pro_price(cycle="yearly"):
    """
    Get plan price by billing cycle.
    - yearly: WIPAY_PRO_PRICE
    - monthly: WIPAY_PRO_PRICE_MONTHLY (or yearly/12 fallback)
    """
    yearly = _safe_decimal(getattr(settings, "WIPAY_PRO_PRICE", "190.00"), default="190.00")
    monthly_raw = getattr(settings, "WIPAY_PRO_PRICE_MONTHLY", None)
    if monthly_raw is None:
        monthly = (yearly / Decimal("12")).quantize(Decimal("0.01"))
    else:
        monthly = _safe_decimal(monthly_raw, default=(yearly / Decimal("12")))
    return monthly if _normalize_billing_cycle(cycle) == "monthly" else yearly


def _get_low_stock_threshold():
    try:
        return int(getattr(settings, "LOW_STOCK_THRESHOLD", 10))
    except Exception:
        return 10

def _get_starter_item_limit():
    try:
        return int(getattr(settings, "STARTER_ITEM_LIMIT", 100))
    except Exception:
        return 100

def _is_pro_user(user):
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    return bool(profile and hasattr(profile, "is_pro_active") and profile.is_pro_active())


def _company_admin_queryset():
    """
    Returns top-level company owners only.
    Superusers are excluded so the SaaS dashboard counts real subscribers.
    """
    return (
        UserProfile.objects.select_related("user")
        .filter(role="admin", parent_admin__isnull=True)
        .exclude(user__is_superuser=True)
    )

def _inventory_owner_for_user(user):
    """
    Return the business owner whose inventory, locations, and sales data
    should be visible to the current user. Ensures staff and admins share data.
    """
    if not user or not user.is_authenticated:
        return None

    if user.is_superuser:
        return user

    profile = UserProfile.for_user(user)
    # Hierarchy check: If staff, return the boss. If boss, return self.
    if profile and profile.role in {"cashier", "manager"} and profile.parent_admin_id:
        parent_admin = (
            UserProfile.objects.select_related("user")
            .filter(pk=profile.parent_admin_id)
            .first()
        )
        if parent_admin and parent_admin.user_id:
            return parent_admin.user

    return user


def _audit_log_queryset_for_user(user):
    if not user or not user.is_authenticated:
        return AuditLog.objects.none()
    if user.is_superuser:
        return AuditLog.objects.select_related("user").all()

    owner = _inventory_owner_for_user(user)
    business_user_ids = UserProfile.objects.filter(
        Q(user=owner) | Q(parent_admin__user=owner)
    ).values_list("user_id", flat=True)
    return AuditLog.objects.select_related("user").filter(user_id__in=business_user_ids)


def _csv_safe_cell(value):
    """Prevent spreadsheet software from evaluating exported user text."""
    text = str(value if value is not None else "")
    if text and (text[0] in {"=", "+", "-", "@", "\t", "\r"} or text.lstrip()[:1] in {"=", "+", "-", "@"}):
        return f"'{text}"
    return text

def _item_queryset_for_user(user):
    owner = _inventory_owner_for_user(user)
    # If owner is None (unauthenticated), return none
    if not owner:
        return Item.objects.none()
    
    # Return active items belonging to the business owner
    # This injects 'total_quantity' dynamically from StockRecords for all downstream views
    return Item.objects.filter(owner=owner, is_deleted=False).annotate(
        total_quantity=Coalesce(Sum("stock_at_locations__quantity"), Value(0))
    )


def _seed_inventory_from_shared_json(owner):
    """
    Seed the tenant's inventory from the shared JSON file when the database is empty.
    """
    if Item.objects.filter(owner=owner).exists():
        return False

    products = load_seed_products_for_owner(owner)
    if not products:
        return False

    location, _ = Location.objects.get_or_create(
        owner=owner,
        name="Main Store",
        defaults={"country_code": "JM"},
    )

    created_any = False
    for item_data in products:
        if not isinstance(item_data, dict):
            continue

        sku = str(item_data.get("sku") or item_data.get("SKU") or "").strip()
        name = str(item_data.get("name") or item_data.get("Name") or "").strip()
        if not name:
            continue

        category_name = str(item_data.get("category") or item_data.get("Category") or "General").strip() or "General"
        brand_name = str(item_data.get("brand") or item_data.get("Brand") or "Generic").strip() or "Generic"
        cost_price = Decimal(str(item_data.get("cost_price") or item_data.get("Cost") or 0))
        price = Decimal(str(item_data.get("selling_price") or item_data.get("price") or item_data.get("Price") or 0))
        quantity = int(item_data.get("quantity_on_hand") or item_data.get("quantity") or item_data.get("Amount") or 0)

        category, _ = Category.objects.get_or_create(owner=owner, name=category_name)
        brand, _ = Brand.objects.get_or_create(owner=owner, name=brand_name)

        item = Item.objects.create(
            sku=sku or None,
            name=name,
            price=price,
            cost_price=cost_price,
            brand=brand,
            category=category,
            owner=owner,
            sync_source="json-seed",
        )
        StockRecord.objects.update_or_create(
            item=item,
            location=location,
            defaults={"quantity": quantity},
        )
        created_any = True

    return created_any


def _customer_queryset_for_user(user):
    owner = _inventory_owner_for_user(user)
    if not owner:
        return Customer.objects.none()
    return Customer.objects.filter(owner=owner)

def _stock_queryset_for_user(user):
    owner = _inventory_owner_for_user(user)
    if not owner:
        return StockRecord.objects.none()
    return StockRecord.objects.filter(item__owner=owner)


def _pos_items_queryset_for_user(user, profile=None):
    profile = profile or UserProfile.objects.select_related("default_location").get(user=user)
    items = _item_queryset_for_user(user).only("id", "name", "price", "sku", "barcode").order_by("name")

    if profile.role != "cashier":
        return items

    if not profile.default_location:
        return items.none()

    location_stock = StockRecord.objects.filter(location=profile.default_location, quantity__gt=0)
    location_item_ids = location_stock.values_list("item_id", flat=True)

    # Main Store legacy compatibility:
    # include Item.quantity-backed inventory even when only some items have StockRecord rows.
    if (profile.default_location.name or "").strip().lower() == "main store":
        return items.filter(Q(id__in=location_item_ids) | Q(total_quantity__gt=0)).distinct()

    if location_stock.exists():
        return items.filter(id__in=location_item_ids)

    return items.none()


def _pos_stock_quantity_for_item(item, location=None):
    if location:
        quantity = (
            item.stock_at_locations.filter(location=location).aggregate(total=Sum("quantity"))["total"]
            or 0
        )
        if quantity:
            return int(quantity)
        if (location.name or "").strip().lower() == "main store":
            return int(item.total_quantity or 0)
        return 0
    return int(item.total_quantity or 0)


def _pos_stock_badge(quantity):
    quantity = int(quantity or 0)
    if quantity <= 0:
        return {"label": "Out of stock", "tone": "out"}
    if quantity <= 3:
        return {"label": f"Low Stock: {quantity:,}", "tone": "low"}
    return {"label": f"{quantity:,} in stock", "tone": "stocked"}


def _location_queryset_for_user(user):
    owner = _inventory_owner_for_user(user)
    if owner:
        _dedupe_default_locations(owner)

    # We always scope to the effective owner's dataset to prevent cross-tenant leakage.
    # This ensures that even superusers only see locations belonging to their workspace on the dashboard.
    if not owner:
        return Location.objects.none()
        
    qs = Location.objects.filter(owner=owner)

    profile = getattr(user, "profile", None)
    # Cashier: restrict to their default location only
    if profile and profile.role == "cashier":
        default_loc = getattr(profile, "default_location", None)
        if default_loc:
            return qs.filter(pk=default_loc.pk)
        return qs.none()

    # Manager/Admin (non-superuser owner): all owner locations
    if _is_pro_user(owner):
        return qs
    # Non-PRO owners: limit to core locations
    return (qs.filter(name__iexact="Main Store") | qs.filter(name__iexact="Unassigned")).distinct()


def _get_main_store(user):
    return Location.objects.get_or_create(
        owner=user,
        name="Main Store",
        defaults={"address": "Primary Location", "is_warehouse": False},
    )[0]


def _get_unassigned_location(user):
    return Location.objects.get_or_create(
        owner=user,
        name="Unassigned",
        defaults={"address": "Unassigned", "is_warehouse": False},
    )[0]


def _dedupe_default_locations(user):
    """
    Ensure only one 'Main Store' and one 'Unassigned' per user.
    Reassigns references from duplicates to the primary.
    """
    # Deduplication maintains data integrity for all account types, including superusers.

    for name in ("Main Store", "Unassigned"):
        qs = Location.objects.filter(owner=user, name__iexact=name).order_by("id")
        if qs.count() <= 1:
            continue
        primary = qs.first()
        duplicates = qs.exclude(id=primary.id)
        # Only non-financial references may be consolidated. Historical rows
        # retain their original location and the duplicate is archived.
        UserProfile.objects.filter(default_location__in=duplicates).update(default_location=primary)
        StockRecord.objects.filter(location__in=duplicates).update(location=primary)
        for duplicate in duplicates:
            has_history = any(
                queryset.exists()
                for queryset in (
                    Sale.objects.filter(location=duplicate),
                    CashShift.objects.filter(location=duplicate),
                    SalesInvoice.objects.filter(location=duplicate),
                    SalesInvoicePayment.objects.filter(location=duplicate),
                    SupplierInvoice.objects.filter(location=duplicate),
                    SupplierInvoicePayment.objects.filter(location=duplicate),
                )
            )
            if has_history:
                duplicate.is_archived = True
                duplicate.archived_at = timezone.now()
                duplicate.archive_reason = "Duplicate location retained because financial history is protected."
                duplicate.save(update_fields=["is_archived", "archived_at", "archive_reason"])
                continue
            PurchaseOrder.objects.filter(location=duplicate).update(location=primary)
            SupplierInvoice.objects.filter(location=duplicate).update(location=primary)
            StockTransfer.objects.filter(from_location=duplicate).update(from_location=primary)
            StockTransfer.objects.filter(to_location=duplicate).update(to_location=primary)
            duplicate.delete()


def _ensure_default_location_for_profile(profile, auto_assign=True):
    if not profile:
        return None

    owner_user = _inventory_owner_for_user(profile.user)
    if profile.default_location_id:
        existing_location = Location.objects.filter(
            pk=profile.default_location_id,
            owner=owner_user,
            is_archived=False,
        ).first()
        if existing_location:
            return existing_location
        profile.default_location = None
        profile.save(update_fields=["default_location"])

    if not auto_assign:
        return None

    fallback_location = _get_main_store(owner_user)
    if not fallback_location:
        fallback_location = _get_unassigned_location(owner_user)

    if fallback_location:
        profile.default_location = fallback_location
        profile.save(update_fields=["default_location"])

    return fallback_location

def _get_location_for_user(user, location_id):
    qs = _location_queryset_for_user(user)
    return get_object_or_404(qs, pk=location_id)


def _get_wipay_config():
    env = (getattr(settings, "WIPAY_ENVIRONMENT", "sandbox") or "sandbox").strip().lower()
    if env not in {"sandbox", "live"}:
        env = "sandbox"

    account_by_env = getattr(settings, f"WIPAY_ACCOUNT_NUMBER_{env.upper()}", "")
    api_key_by_env = getattr(settings, f"WIPAY_API_KEY_{env.upper()}", "")

    account_number = str(
        account_by_env or getattr(settings, "WIPAY_ACCOUNT_NUMBER", "") or ""
    ).strip()
    api_key = str(
        api_key_by_env or getattr(settings, "WIPAY_API_KEY", "") or ""
    ).strip()

    # ======== Add this for immediate sandbox testing ========
    if env == "sandbox" and account_number == "1119293480":
        # Use WiPay’s universal sandbox test account
        account_number = "1234567890"
        api_key = "123"

    return env, account_number, api_key


def _wipay_origin(default="QuickStock_JA"):
    """
    WiPay rejects origin values containing spaces or punctuation.
    Allowed characters: letters, numbers, dashes, and underscores.
    """
    raw_origin = str(getattr(settings, "WIPAY_ORIGIN", "") or default).strip()
    safe_origin = re.sub(r"[^A-Za-z0-9_-]+", "_", raw_origin).strip("_-")
    return safe_origin or default


def _probe_wipay_availability():
    """
    Perform a lightweight reachability check before redirecting users to WiPay.
    This helps us fail gracefully when the upstream host is timing out.
    """
    endpoint = getattr(
        settings,
        "WIPAY_ENDPOINT",
        "https://jm.wipayfinancial.com/plugins/payments/request",
    ).strip()
    if not endpoint:
        return False, "WiPay endpoint is not configured."

    parsed = urlsplit(endpoint)
    if not parsed.scheme or not parsed.netloc:
        return False, "WiPay endpoint is invalid."

    probe_url = f"{parsed.scheme}://{parsed.netloc}/"
    timeout = getattr(settings, "WIPAY_PRECHECK_TIMEOUT", 5)

    try:
        response = requests.get(probe_url, timeout=timeout, allow_redirects=True)
        if response.status_code >= 500:
            return False, f"WiPay returned status {response.status_code}."
        return True, ""
    except requests.Timeout:
        return False, "WiPay timed out before checkout could begin."
    except requests.ConnectionError:
        return False, "WiPay could not be reached right now."
    except requests.RequestException as exc:
        return False, f"WiPay request failed: {exc}"


def _checkout_phone_for_user(user):
    profile = getattr(user, "profile", None) or UserProfile.for_user(user)
    phone = (getattr(profile, "receipt_contact_phone", "") or "").strip()
    if phone:
        return phone
    return (getattr(settings, "WIPAY_CONTACT_PHONE_DEFAULT", "") or "").strip()


def _validate_receipt_logo_upload(uploaded_file):
    """Validate and safely rename an uploaded receipt logo before storage."""
    max_bytes = max(1, int(getattr(settings, "RECEIPT_LOGO_MAX_BYTES", 2 * 1024 * 1024)))
    max_dimension = max(1, int(getattr(settings, "RECEIPT_LOGO_MAX_DIMENSION", 4096)))
    file_size = int(getattr(uploaded_file, "size", 0) or 0)
    if file_size <= 0:
        raise ValidationError("The selected logo file is empty.")
    if file_size > max_bytes:
        raise ValidationError(f"Logo files must be {max_bytes // (1024 * 1024)} MB or smaller.")

    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            image_format = (image.format or "").upper()
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValidationError("Upload a valid PNG, JPEG, or WebP image.") from exc
    finally:
        uploaded_file.seek(0)

    extensions = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}
    if image_format not in extensions:
        raise ValidationError("Receipt logos must use PNG, JPEG, or WebP format.")
    if width <= 0 or height <= 0 or width > max_dimension or height > max_dimension:
        raise ValidationError(f"Receipt logos cannot exceed {max_dimension} pixels in either dimension.")

    uploaded_file.name = f"receipt-logo-{uuid.uuid4().hex}.{extensions[image_format]}"
    return uploaded_file

def _log_action(user, action, message, metadata=None, severity="info", required=False):
    """Write an action to AuditLog and Django logger."""
    try:
        AuditLog.objects.create(
            user=user if user and user.is_authenticated else None,
            action=action,
            message=message,
            metadata=metadata or {},
            severity=severity,
        )
        log_msg = f"{action} - {message} - {metadata or {}}"
        if severity == "error":
            logger.error(log_msg)
        elif severity == "warn":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)
    except Exception:
        if required:
            raise


def _owner_financial_history_summary(user):
    """Return durable financial dependencies that make account deletion unsafe."""
    return {
        "sales": Sale.objects.filter(Q(owner=user) | Q(location__owner=user)).exclude(receipt_status="draft").count(),
        "sales_invoices": SalesInvoice.objects.filter(owner=user).exclude(status="draft").count(),
        "sales_payments": SalesInvoicePayment.objects.filter(
            Q(owner=user) | Q(invoice__owner=user)
        ).count(),
        "sales_credit_notes": SalesInvoiceCreditNote.objects.filter(invoice__owner=user).count(),
        "customer_credit_movements": CustomerCreditMovement.objects.filter(customer__owner=user).count(),
        "supplier_invoices": SupplierInvoice.objects.filter(supplier__owner=user).count(),
        "supplier_payments": SupplierInvoicePayment.objects.filter(
            Q(owner=user) | Q(invoice__supplier__owner=user)
        ).count(),
        "supplier_adjustments": SupplierInvoiceAdjustment.objects.filter(
            Q(owner=user) | Q(invoice__supplier__owner=user)
        ).count(),
        "supplier_refunds": SupplierInvoiceRefund.objects.filter(
            Q(owner=user) | Q(invoice__supplier__owner=user)
        ).count(),
        "purchase_orders": PurchaseOrder.objects.filter(
            Q(item__owner=user) | Q(supplier__owner=user)
        ).count(),
        "cash_shifts": CashShift.objects.filter(
            Q(owner=user) | Q(cashier=user) | Q(location__owner=user)
        ).count(),
        "cash_movements": CashMovement.objects.filter(owner=user).count(),
        "cash_reconciliations": CashReconciliation.objects.filter(owner=user).count(),
    }


def _discard_user_draft_records(user):
    """
    Remove unposted drafts so account deletion is not converted into archive mode
    by records that never became durable financial history.
    """
    if not user or not getattr(user, "pk", None):
        return

    owned_location_ids = list(Location.objects.filter(owner=user).values_list("id", flat=True))
    draft_sales = Sale.objects.filter(
        Q(owner=user) | Q(location_id__in=owned_location_ids) | Q(cashier=user),
        receipt_status="draft",
    )
    draft_invoice_ids = list(
        SalesInvoice.objects
        .filter(owner=user, status="draft", payments__isnull=True, credit_notes__isnull=True)
        .values_list("id", flat=True)
    )
    draft_quotation_ids = list(
        SalesQuotation.objects.filter(owner=user, status="draft").values_list("id", flat=True)
    )

    if draft_invoice_ids:
        SalesInvoiceItem.objects.filter(invoice_id__in=draft_invoice_ids)._raw_delete(connection.alias)
        SalesInvoice.objects.filter(id__in=draft_invoice_ids)._raw_delete(connection.alias)
    if draft_quotation_ids:
        SalesQuotationItem.objects.filter(quotation_id__in=draft_quotation_ids).delete()
        SalesQuotation.objects.filter(id__in=draft_quotation_ids).delete()
    draft_sales.delete()


def _user_has_financial_history(user):
    return any(
        queryset.exists()
        for queryset in (
            SalesInvoice.objects.filter(created_by=user).exclude(status="draft"),
            SalesInvoicePayment.objects.filter(received_by=user),
            SalesInvoicePaymentReversal.objects.filter(reversed_by=user),
            SalesInvoiceCreditNote.objects.filter(created_by=user),
            CustomerCreditMovement.objects.filter(created_by=user),
            SupplierInvoice.objects.filter(voided_by=user),
            SupplierInvoicePayment.objects.filter(paid_by=user),
            SupplierInvoicePaymentReversal.objects.filter(reversed_by=user),
            SupplierInvoiceAdjustment.objects.filter(created_by=user),
            SupplierInvoiceRefund.objects.filter(received_by=user),
            Sale.objects.filter(cashier=user).exclude(receipt_status="draft"),
            CashShift.objects.filter(Q(owner=user) | Q(cashier=user)),
            CashMovement.objects.filter(Q(owner=user) | Q(recorded_by=user)),
            CashReconciliation.objects.filter(Q(owner=user) | Q(reconciled_by=user)),
        )
    )


def _archive_record(record, actor, reason):
    record.is_archived = True
    record.archived_at = timezone.now()
    record.archived_by = actor
    record.archive_reason = (reason or "Financial history retained; record archived.").strip()
    record.save(update_fields=["is_archived", "archived_at", "archived_by", "archive_reason"])


def _archive_user_profile(profile, actor, reason):
    profile.is_archived = True
    profile.archived_at = timezone.now()
    profile.archived_by = actor
    profile.archive_reason = (reason or "Financial history retained; account archived.").strip()
    profile.status = "suspended"
    profile.save(update_fields=["is_archived", "archived_at", "archived_by", "archive_reason", "status"])
    profile.user.is_active = False
    profile.user.save(update_fields=["is_active"])


_sales_invoice_payment_table_available = None
_sales_invoice_credit_note_table_available = None


def _sales_invoice_payments_available():
    global _sales_invoice_payment_table_available
    if _sales_invoice_payment_table_available is not None:
        return _sales_invoice_payment_table_available

    try:
        table_names = set(connection.introspection.table_names())
        _sales_invoice_payment_table_available = SalesInvoicePayment._meta.db_table in table_names
    except Exception:
        _sales_invoice_payment_table_available = False
    return _sales_invoice_payment_table_available


def _sales_invoice_credit_notes_available():
    global _sales_invoice_credit_note_table_available
    if _sales_invoice_credit_note_table_available is not None:
        return _sales_invoice_credit_note_table_available

    try:
        table_names = set(connection.introspection.table_names())
        _sales_invoice_credit_note_table_available = SalesInvoiceCreditNote._meta.db_table in table_names
    except Exception:
        _sales_invoice_credit_note_table_available = False
    return _sales_invoice_credit_note_table_available


def _sales_invoice_credit_note_total(invoice):
    total_amount = (invoice.total_amount or Decimal("0.00")).quantize(Decimal("0.01"))
    if not _sales_invoice_credit_notes_available():
        return Decimal("0.00")

    prefetched_credit_notes = getattr(invoice, "_prefetched_objects_cache", {}).get("credit_notes")
    if prefetched_credit_notes is not None:
        credit_total = sum((note.amount or Decimal("0.00")) for note in prefetched_credit_notes)
    else:
        try:
            credit_total = invoice.credit_notes.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        except (OperationalError, ProgrammingError):
            credit_total = Decimal("0.00")
    credit_total = Decimal(credit_total).quantize(Decimal("0.01"))
    return max(Decimal("0.00"), min(credit_total, total_amount))


def _sales_invoice_effective_total(invoice):
    total_amount = (invoice.total_amount or Decimal("0.00")).quantize(Decimal("0.01"))
    credit_total = _sales_invoice_credit_note_total(invoice)
    return max(total_amount - credit_total, Decimal("0.00")).quantize(Decimal("0.01"))


def _sales_invoice_payment_totals(invoice):
    total_amount = _sales_invoice_effective_total(invoice)
    if _sales_invoice_payments_available():
        try:
            paid_total = invoice.payments.unreversed().aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            paid_total = paid_total.quantize(Decimal("0.01"))
        except (OperationalError, ProgrammingError):
            paid_total = Decimal("0.00")
    else:
        paid_total = Decimal("0.00")

    balance_due = max(total_amount - paid_total, Decimal("0.00")).quantize(Decimal("0.01"))

    return paid_total, balance_due


def _customer_credit_balance(customer):
    if not customer:
        return Decimal("0.00")
    try:
        return customer.ledger_credit_balance
    except (OperationalError, ProgrammingError):
        return (customer.credit_balance or Decimal("0.00")).quantize(Decimal("0.01"))


def _sales_invoice_overpayment_amount(invoice, paid_total=None):
    if paid_total is None:
        paid_total, _ = _sales_invoice_payment_totals(invoice)
    total_amount = _sales_invoice_effective_total(invoice)
    return max(paid_total - total_amount, Decimal("0.00")).quantize(Decimal("0.01"))


def _credit_applicable_to_invoice(invoice, customer=None):
    customer_obj = customer if customer is not None else getattr(invoice, "customer", None)
    if not customer_obj:
        return Decimal("0.00")
    return min(
        _customer_credit_balance(customer_obj),
        (invoice.balance_due or Decimal("0.00")).quantize(Decimal("0.01")),
    ).quantize(Decimal("0.01"))


def _can_revert_sales_invoice_payments(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return getattr(getattr(user, "profile", None), "role", "") == "admin"


def _can_issue_sales_invoice_credit_notes(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return getattr(getattr(user, "profile", None), "role", "") in {"admin", "manager"}


def _can_email_sales_documents(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return getattr(getattr(user, "profile", None), "role", "") in {"admin", "manager"}


PICKUP_STATUS_COLLECTED_NOW = "collected_now"
PICKUP_STATUS_LEAVE_IN_STORE = "leave_in_store"
VALID_PICKUP_STATUSES = {
    PICKUP_STATUS_COLLECTED_NOW,
    PICKUP_STATUS_LEAVE_IN_STORE,
}


def _normalize_sales_invoice_pickup_status(raw_value, default=PICKUP_STATUS_COLLECTED_NOW):
    pickup_status = (raw_value or "").strip().lower()
    return pickup_status if pickup_status in VALID_PICKUP_STATUSES else default


def _set_sales_invoice_collection_status(invoice, collection_status, *, actor=None, occurred_at=None):
    valid_statuses = {value for value, _label in SalesInvoice.COLLECTION_STATUS_CHOICES}
    if collection_status not in valid_statuses:
        raise ValueError("Choose a valid invoice collection status.")

    event_time = occurred_at or timezone.now()
    collected_statuses = {"collected_immediately", "collected_after_hold"}
    invoice.collection_status = collection_status
    invoice.collection_status_changed_at = event_time
    invoice.collected_at = event_time if collection_status in collected_statuses else None
    invoice.collection_recorded_by = actor
    invoice.save(
        update_fields=[
            "collection_status",
            "collection_status_changed_at",
            "collected_at",
            "collection_recorded_by",
        ]
    )


def _apply_sales_invoice_pickup_choice(invoice, pickup_status, *, actor=None, occurred_at=None):
    collection_status = (
        "awaiting_collection"
        if pickup_status == PICKUP_STATUS_LEAVE_IN_STORE
        else "collected_immediately"
    )
    _set_sales_invoice_collection_status(
        invoice,
        collection_status,
        actor=actor,
        occurred_at=occurred_at,
    )
    return collection_status


def _can_manage_sales_invoice_at_active_location(user, invoice):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    profile = UserProfile.for_user(user)
    if profile.role == "admin":
        return True
    if profile.role in {"manager", "cashier"}:
        return bool(
            profile.default_location_id
            and invoice.location_id == profile.default_location_id
        )
    return False


def _refresh_sales_invoice_status(invoice):
    recorded_total = (
        invoice.payments.unreversed().aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    ).quantize(Decimal("0.01"))
    total_amount = _sales_invoice_effective_total(invoice)
    balance_due = max(total_amount - recorded_total, Decimal("0.00")).quantize(Decimal("0.01"))
    new_status = "paid" if balance_due <= Decimal("0.00") else "issued"
    update_fields = []
    if invoice.status != new_status:
        invoice.status = new_status
        update_fields.append("status")

    if new_status != "paid" and invoice.collection_status not in {"untracked", "pending_payment"}:
        invoice.collection_status = "pending_payment"
        invoice.collection_status_changed_at = timezone.now()
        invoice.collected_at = None
        invoice.collection_recorded_by = None
        update_fields.extend(
            [
                "collection_status",
                "collection_status_changed_at",
                "collected_at",
                "collection_recorded_by",
            ]
        )

    if update_fields:
        invoice.save(update_fields=update_fields)
    return balance_due


def _valid_sales_invoice_payment_methods():
    return {value for value, _ in SalesInvoicePayment.METHOD_CHOICES}


def _normalize_sales_invoice_payment_method(raw_method, default="cash"):
    method = (raw_method or "").strip().lower()
    valid_methods = _valid_sales_invoice_payment_methods()
    return method if method in valid_methods else default


def _manual_sales_invoice_payment_methods():
    return [
        (value, label)
        for value, label in SalesInvoicePayment.METHOD_CHOICES
        if value != "account_credit"
    ]


def _sales_invoice_payment_method_requires_reference(method):
    return method not in {"cash", "account_credit"}


def _sales_invoice_credit_note_reason_choices():
    return list(SalesInvoiceCreditNote.REASON_CHOICES)


def _sales_invoice_payment_redirect(invoice_id, payment_method=None):
    base_url = reverse("sales_invoice_payment", kwargs={"invoice_id": invoice_id})
    normalized_method = _normalize_sales_invoice_payment_method(payment_method, default="")
    if not normalized_method:
        return base_url
    return f"{base_url}?{urlencode({'payment_method': normalized_method})}"


def _parse_sales_invoice_payment_datetime(raw_value):
    parsed = parse_datetime((raw_value or "").strip())
    if not parsed:
        return timezone.now()
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _daily_summary_item_totals(invoice_items):
    totals = {}
    for invoice_item in invoice_items:
        item_name = (
            getattr(invoice_item, "item_name", "")
            or getattr(invoice_item.item, "name", "")
            or "Unnamed Item"
        ).strip()
        totals[item_name] = totals.get(item_name, 0) + int(invoice_item.quantity or 0)

    return [
        {"name": item_name, "quantity": quantity}
        for item_name, quantity in sorted(totals.items(), key=lambda row: row[0].lower())
        if quantity > 0
    ]


def _daily_summary_items_grouped_by_category(invoice_items):
    grouped_totals = {}
    for invoice_item in invoice_items:
        item = getattr(invoice_item, "item", None)
        category_name = (getattr(getattr(item, "category", None), "name", "") or "General").strip() or "General"
        item_name = (
            getattr(invoice_item, "item_name", "")
            or getattr(item, "name", "")
            or "Unnamed Item"
        ).strip()
        category_bucket = grouped_totals.setdefault(category_name, {})
        category_bucket[item_name] = category_bucket.get(item_name, 0) + int(invoice_item.quantity or 0)

    return [
        {
            "category": category_name,
            "items": [
                {"name": item_name, "quantity": quantity}
                for item_name, quantity in sorted(items.items(), key=lambda row: row[0].lower())
                if quantity > 0
            ],
        }
        for category_name, items in sorted(grouped_totals.items(), key=lambda row: row[0].lower())
        if any(quantity > 0 for quantity in items.values())
    ]


def _sales_collection_activity_lines(owner_user, *, selected_date=None, location=None):
    invoice_lines = SalesInvoiceItem.objects.select_related(
        "item",
        "item__category",
        "invoice",
        "invoice__customer",
        "invoice__location",
    ).filter(invoice__status="paid")
    sale_lines = (
        SaleItem.objects.select_related("item", "item__category", "sale", "sale__location")
        .exclude(sale__receipt_status__in=["draft", "voided"])
    )

    if owner_user and not owner_user.is_superuser:
        invoice_lines = invoice_lines.filter(invoice__owner=owner_user)
        sale_lines = sale_lines.filter(sale__owner=owner_user, item__owner=owner_user)
    if location:
        invoice_lines = invoice_lines.filter(invoice__location=location)
        sale_lines = sale_lines.filter(sale__location=location)

    delivered_from_hold = invoice_lines.filter(
        invoice__collection_status="collected_after_hold",
    )
    collected_immediately = invoice_lines.filter(
        invoice__collection_status="collected_immediately",
    )
    if selected_date:
        delivered_from_hold = delivered_from_hold.filter(invoice__collected_at__date=selected_date)
        collected_immediately = collected_immediately.filter(invoice__collected_at__date=selected_date)
        sale_lines = sale_lines.filter(sale__timestamp__date=selected_date)

    remaining_in_store = invoice_lines.filter(
        invoice__collection_status="awaiting_collection",
    )
    if selected_date:
        remaining_in_store = remaining_in_store.filter(
            Q(invoice__collection_status_changed_at__date__lte=selected_date)
            | Q(
                invoice__collection_status_changed_at__isnull=True,
                invoice__issued_at__date__lte=selected_date,
            )
        )
    return {
        "delivered_from_hold": list(delivered_from_hold),
        "collected_immediately": list(collected_immediately) + list(sale_lines),
        "remaining_in_store": list(remaining_in_store),
    }


def _parse_operations_date(request, date=None):
    selected_date = timezone.localdate()
    requested_date = request.GET.get("date") or date
    if requested_date:
        try:
            selected_date = datetime.strptime(requested_date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            messages.warning(request, "Invalid operations date supplied. Showing today's records.")
    return selected_date


def _daily_reconciliation_context(owner_user, selected_date):
    method_totals = {
        "cash": Decimal("0.00"),
        "debit": Decimal("0.00"),
        "credit": Decimal("0.00"),
    }
    if _sales_invoice_payments_available():
        payments = SalesInvoicePayment.objects.unreversed().filter(payment_date__date=selected_date)
        if owner_user and not owner_user.is_superuser:
            payments = payments.filter(owner=owner_user)

        for payment in payments:
            method = _normalize_sales_invoice_payment_method(payment.payment_method)
            amount = (payment.amount or Decimal("0.00")).quantize(Decimal("0.01"))
            if method == "cash":
                method_totals["cash"] += amount
            elif method in {"debit_card", "card", "mobile_money", "jamdex"}:
                method_totals["debit"] += amount
            elif method in {"credit_card", "other"}:
                method_totals["credit"] += amount

    cash_count, _ = DailyCashCount.objects.get_or_create(
        owner=owner_user,
        summary_date=selected_date,
    )
    cash_count_display = {}
    for denomination, field_name in CASH_DENOMINATIONS:
        quantity = getattr(cash_count, field_name)
        cash_count_display[field_name] = quantity
        cash_count_display[f"total_{denomination}"] = Decimal(quantity * denomination).quantize(Decimal("0.01"))

    expected_cash = method_totals["cash"].quantize(Decimal("0.01"))
    expected_card_amount = (method_totals["debit"] + method_totals["credit"]).quantize(Decimal("0.01"))
    expected_reconciliation_total = (expected_cash + expected_card_amount).quantize(Decimal("0.01"))
    actual_cash = cash_count.actual_cash
    actual_card_amount = (cash_count.ncb_machine_amount + cash_count.bns_machine_amount).quantize(Decimal("0.01"))
    actual_reconciliation_total = (actual_cash + actual_card_amount).quantize(Decimal("0.01"))
    variance = (actual_reconciliation_total - expected_reconciliation_total).quantize(Decimal("0.01"))

    return {
        "cash_count": cash_count_display,
        "expected_cash": expected_cash,
        "expected_card_amount": expected_card_amount,
        "expected_reconciliation_total": expected_reconciliation_total,
        "actual_cash": actual_cash,
        "actual_card_amount": actual_card_amount,
        "actual_reconciliation_total": actual_reconciliation_total,
        "ncb_machine_amount": cash_count.ncb_machine_amount,
        "bns_machine_amount": cash_count.bns_machine_amount,
        "variance": variance,
    }
# ---------------------------
# Public Pages
# ---------------------------
def index(request):
    """Render the public landing page without reading account state."""
    return render(request, "inventory/index.html")

@login_required
@role_required(["admin", "manager", "cashier"])
def deliveries_collections(request):
    """
    Deliveries & Collections page for QuickStock JA.
    Ensures profile is retrieved or created for authenticated users.
    """
    profile = UserProfile.for_user(request.user)
    owner_user = _inventory_owner_for_user(request.user)
    selected_date = _parse_operations_date(request)
    active_location = getattr(profile, "default_location", None)
    if active_location and active_location.owner_id != getattr(owner_user, "id", None):
        active_location = None
    collection_lines = _sales_collection_activity_lines(
        owner_user,
        selected_date=selected_date,
        location=active_location,
    )
    delivered_lines = collection_lines["delivered_from_hold"]
    collected_lines = collection_lines["collected_immediately"]
    remaining_lines = collection_lines["remaining_in_store"]
    delivered_totals = _daily_summary_item_totals(delivered_lines)
    collected_totals = _daily_summary_item_totals(collected_lines)
    remaining_totals = _daily_summary_item_totals(remaining_lines)

    remaining_order_map = {}
    for line in remaining_lines:
        invoice = line.invoice
        order = remaining_order_map.setdefault(
            invoice.id,
            {
                "invoice": invoice,
                "held_since": invoice.collection_status_changed_at or invoice.issued_at,
                "items": [],
                "total_quantity": 0,
                "can_mark_collected": _can_manage_sales_invoice_at_active_location(request.user, invoice),
            },
        )
        item_name = line.item_name or getattr(line.item, "name", "") or "Unnamed Item"
        quantity = int(line.quantity or 0)
        order["items"].append({"name": item_name, "quantity": quantity})
        order["total_quantity"] += quantity
    remaining_orders = sorted(
        remaining_order_map.values(),
        key=lambda row: (row["held_since"], row["invoice"].id),
        reverse=True,
    )

    context = {
        "profile": profile,
        "is_pro_user": _is_pro_user(owner_user),
        "selected_date": selected_date.strftime("%Y-%m-%d"),
        "report_date": selected_date.strftime("%B %-d, %Y") if os.name != "nt" else selected_date.strftime("%B %#d, %Y"),
        "collection_location_name": active_location.name if active_location else "All locations",
        "delivered_today": delivered_totals,
        "collected_by_client": collected_totals,
        "awaiting_collection": remaining_totals,
        "delivered_total_quantity": sum(row["quantity"] for row in delivered_totals),
        "collected_total_quantity": sum(row["quantity"] for row in collected_totals),
        "remaining_total_quantity": sum(row["quantity"] for row in remaining_totals),
        "remaining_in_store_orders": remaining_orders,
        "delivered_today_grouped": _daily_summary_items_grouped_by_category(delivered_lines),
        "collected_by_client_grouped": _daily_summary_items_grouped_by_category(collected_lines),
        "awaiting_collection_grouped": _daily_summary_items_grouped_by_category(remaining_lines),
        "is_closeout_wizard": request.GET.get("closeout") == "1",
        "next_closeout_url": reverse("operations"),
    }
    return render(request, "inventory/deliveries_collections.html", context)


@login_required
@role_required(["admin", "manager", "cashier"])
def cash_reconciliation(request, date=None):
    """
    Cash reconciliation page for QuickStock JA.
    Ensures profile is retrieved or created for authenticated users.
    """
    profile = UserProfile.for_user(request.user)
    owner_user = _inventory_owner_for_user(request.user)
    selected_date = _parse_operations_date(request, date)
    is_closeout_wizard = request.GET.get("closeout") == "1" or request.POST.get("closeout") == "1"
    next_closeout_url = f"{reverse('deliveries_collections')}?{urlencode({'date': selected_date.strftime('%Y-%m-%d'), 'closeout': '1'})}"
    selected_shift = None
    selected_shift_expected_cash = Decimal("0.00")
    selected_shift_id = request.POST.get("shift_id") or request.GET.get("shift")
    if selected_shift_id:
        shift_qs = CashShift.objects.select_related("cashier", "location").filter(pk=selected_shift_id)
        if not request.user.is_superuser:
            shift_qs = shift_qs.filter(Q(owner=owner_user) | Q(cashier=request.user))
        selected_shift = shift_qs.first()
        if selected_shift and selected_shift.is_closed:
            selected_shift = None
        if selected_shift:
            selected_shift_expected_cash = selected_shift.reconciliation_totals()["expected_cash"]

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "close_shift":
            if not selected_shift:
                messages.error(request, "Open shift not found or already closed.")
                return redirect(
                    f"{reverse('cash_reconciliation')}?{urlencode({'date': selected_date.strftime('%Y-%m-%d')})}"
                )
            try:
                selected_shift.close_shift(
                    request.POST.get("counted_cash"),
                    actor=request.user,
                    notes=(request.POST.get("notes") or "").strip(),
                )
                messages.success(request, "Shift closeout finalized.")
                if is_closeout_wizard:
                    return redirect(next_closeout_url)
                return redirect("dashboard")
            except (ValidationError, TypeError, ValueError, InvalidOperation) as exc:
                messages.error(request, str(exc))
                return redirect(
                    f"{reverse('cash_reconciliation')}?{urlencode({'date': selected_date.strftime('%Y-%m-%d'), 'shift': selected_shift.id})}"
                )

        if action == "update_cash_count":
            cash_count, _ = DailyCashCount.objects.get_or_create(
                owner=owner_user,
                summary_date=selected_date,
            )
            cash_count.opening_cash_amount = max(
                Decimal("0.00"),
                _safe_decimal(request.POST.get("opening_cash_amount", "0.00"), default="0.00"),
            )
            cash_count.cash_expenditure_amount = max(
                Decimal("0.00"),
                _safe_decimal(request.POST.get("cash_expenditure_amount", "0.00"), default="0.00"),
            )
            cash_count.cash_expenditure_party_name = (request.POST.get("cash_expenditure_party_name") or "").strip()[:255]
            cash_count.cash_expenditure_currency = (
                (request.POST.get("cash_expenditure_currency") or "JMD").strip().upper()
            )
            if cash_count.cash_expenditure_currency not in {"JMD", "USD"}:
                cash_count.cash_expenditure_currency = "JMD"
            cash_count.cash_expenditure_receipt_reference = (
                request.POST.get("cash_expenditure_receipt_reference") or ""
            ).strip()[:120]
            cash_count.cash_lodgement_amount = max(
                Decimal("0.00"),
                _safe_decimal(request.POST.get("cash_lodgement_amount", "0.00"), default="0.00"),
            )
            cash_count.cash_expenditure_notes = (request.POST.get("cash_expenditure_notes") or "").strip()[:255]
            cash_count.cash_lodgement_notes = (request.POST.get("cash_lodgement_notes") or "").strip()[:255]

            update_fields = [
                "updated_by",
                "updated_at",
                "opening_cash_amount",
                "cash_expenditure_amount",
                "cash_expenditure_party_name",
                "cash_expenditure_currency",
                "cash_expenditure_receipt_reference",
                "cash_expenditure_notes",
                "cash_lodgement_amount",
                "cash_lodgement_notes",
                "ncb_machine_amount",
                "bns_machine_amount",
            ]
            for _, field_name in CASH_DENOMINATIONS:
                raw_value = request.POST.get(field_name, "0")
                try:
                    quantity = max(0, int(raw_value or 0))
                except (TypeError, ValueError):
                    quantity = 0
                setattr(cash_count, field_name, quantity)
                update_fields.append(field_name)

            cash_count.ncb_machine_amount = max(
                Decimal("0.00"),
                _safe_decimal(request.POST.get("ncb_machine_amount", "0.00"), default="0.00"),
            )
            cash_count.bns_machine_amount = max(
                Decimal("0.00"),
                _safe_decimal(request.POST.get("bns_machine_amount", "0.00"), default="0.00"),
            )
            cash_count.updated_by = request.user
            cash_count.save(update_fields=update_fields)
            messages.success(request, "Cash reconciliation updated.")
            return redirect(
                f"{reverse('cash_reconciliation')}?{urlencode({'date': selected_date.strftime('%Y-%m-%d')})}"
            )

    context = {
        "profile": profile,
        "is_pro_user": _is_pro_user(owner_user),
        "selected_date": selected_date.strftime("%Y-%m-%d"),
        "report_date": selected_date.strftime("%B %-d, %Y") if os.name != "nt" else selected_date.strftime("%B %#d, %Y"),
        "selected_shift": selected_shift,
        "selected_shift_expected_cash": selected_shift_expected_cash,
        "is_closeout_wizard": is_closeout_wizard,
        "next_closeout_url": next_closeout_url,
    }
    context.update(_daily_reconciliation_context(owner_user, selected_date))
    return render(request, "inventory/cash_reconciliation.html", context)

@login_required
@role_required(["admin", "manager", "cashier"])
def daily_summary(request, date=None):
    """
    Daily reporting page for QuickStock JA.
    Ensures profile is retrieved or created for authenticated users.
    """
    profile = None
    if request.user.is_authenticated:
        # Using your helper ensures no 'NoneType' errors in the template
        profile = UserProfile.for_user(request.user)

    selected_date = timezone.localdate()
    requested_date = request.GET.get("date") or date
    if requested_date:
        try:
            selected_date = datetime.strptime(requested_date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            messages.warning(request, "Invalid summary date supplied. Showing today's summary.")

    owner_user = _inventory_owner_for_user(request.user) if request.user.is_authenticated else None
    can_access_daily_summary = bool(
        request.user.is_authenticated
        and (
            request.user.is_superuser
            or (profile and profile.role == "admin")
            or (profile and profile.role in {"manager", "cashier"} and profile.can_edit_daily_summary)
        )
    )
    can_edit_daily_summary = can_access_daily_summary

    if not can_access_daily_summary:
        messages.error(request, "You do not have permission to use Daily Summary.")
        return redirect("dashboard")

    if request.method == "POST":
        if not can_edit_daily_summary:
            messages.error(request, "You do not have permission to edit Daily Summary.")
            return redirect(f"{reverse('daily_summary')}?{urlencode({'date': selected_date.strftime('%Y-%m-%d')})}")

        action = (request.POST.get("action") or "update_comment").strip()
        if action == "update_cash_count":
            cash_count, _ = DailyCashCount.objects.get_or_create(
                owner=owner_user,
                summary_date=selected_date,
            )
            cash_count.opening_cash_amount = max(
                Decimal("0.00"),
                _safe_decimal(request.POST.get("opening_cash_amount", "0.00"), default="0.00"),
            )
            cash_count.cash_expenditure_amount = max(
                Decimal("0.00"),
                _safe_decimal(request.POST.get("cash_expenditure_amount", "0.00"), default="0.00"),
            )
            cash_count.cash_expenditure_party_name = (request.POST.get("cash_expenditure_party_name") or "").strip()[:255]
            cash_count.cash_expenditure_currency = (
                (request.POST.get("cash_expenditure_currency") or "JMD").strip().upper()
            )
            if cash_count.cash_expenditure_currency not in {"JMD", "USD"}:
                cash_count.cash_expenditure_currency = "JMD"
            cash_count.cash_expenditure_receipt_reference = (
                request.POST.get("cash_expenditure_receipt_reference") or ""
            ).strip()[:120]
            cash_count.cash_lodgement_amount = max(
                Decimal("0.00"),
                _safe_decimal(request.POST.get("cash_lodgement_amount", "0.00"), default="0.00"),
            )
            cash_count.cash_expenditure_notes = (request.POST.get("cash_expenditure_notes") or "").strip()[:255]
            cash_count.cash_lodgement_notes = (request.POST.get("cash_lodgement_notes") or "").strip()[:255]

            update_fields = [
                "updated_by",
                "updated_at",
                "opening_cash_amount",
                "cash_expenditure_amount",
                "cash_expenditure_party_name",
                "cash_expenditure_currency",
                "cash_expenditure_receipt_reference",
                "cash_expenditure_notes",
                "cash_lodgement_amount",
                "cash_lodgement_notes",
                "ncb_machine_amount",
                "bns_machine_amount",
            ]
            for _, field_name in CASH_DENOMINATIONS:
                raw_value = request.POST.get(field_name, "0")
                try:
                    quantity = max(0, int(raw_value or 0))
                except (TypeError, ValueError):
                    quantity = 0
                setattr(cash_count, field_name, quantity)
                update_fields.append(field_name)

            cash_count.ncb_machine_amount = max(Decimal("0.00"), _safe_decimal(request.POST.get("ncb_machine_amount", "0.00"), default="0.00"))
            cash_count.bns_machine_amount = max(Decimal("0.00"), _safe_decimal(request.POST.get("bns_machine_amount", "0.00"), default="0.00"))
            cash_count.updated_by = request.user
            cash_count.save(update_fields=update_fields)
            messages.success(request, "Daily cash count updated.")
            return redirect(f"{reverse('daily_summary')}?{urlencode({'date': selected_date.strftime('%Y-%m-%d')})}")

        row_type = (request.POST.get("row_type") or "").strip()
        row_id = request.POST.get("row_id")
        if action == "update_receipt_no":
            receipt_no = (request.POST.get("receipt_no") or "").strip()
            if row_type != "payment" or not row_id:
                messages.error(request, "Receipt numbers can only be edited for payment rows.")
                return redirect(f"{reverse('daily_summary')}?{urlencode({'date': selected_date.strftime('%Y-%m-%d')})}")

            try:
                with transaction.atomic():
                    payment_qs = SalesInvoicePayment.objects.select_for_update().select_related("invoice")
                    if not request.user.is_superuser:
                        payment_qs = payment_qs.filter(owner=owner_user)
                    payment = payment_qs.get(pk=row_id)
                    if not _can_manage_sales_invoice_at_active_location(request.user, payment.invoice):
                        raise SalesInvoicePayment.DoesNotExist
                    previous_reference = payment.reference
                    payment.reference = receipt_no[:80]
                    payment.save(update_fields=["reference"])
                    _log_action(
                        request.user,
                        "payment",
                        "Sales invoice payment reference corrected",
                        {
                            "payment_id": payment.id,
                            "invoice_id": payment.invoice_id,
                            "previous_reference": previous_reference,
                            "new_reference": payment.reference,
                        },
                        severity="warn",
                        required=True,
                    )
                messages.success(request, "Receipt number updated.")
            except (SalesInvoicePayment.DoesNotExist, ValidationError, ValueError, TypeError):
                messages.error(request, "Could not update that receipt number.")

            return redirect(f"{reverse('daily_summary')}?{urlencode({'date': selected_date.strftime('%Y-%m-%d')})}")

        comment = (request.POST.get("comment") or "").strip()

        comment_model_map = {
            "payment": SalesInvoicePayment,
            "invoice": SalesInvoice,
            "quotation": SalesQuotation,
        }
        model = comment_model_map.get(row_type)

        if model and row_id:
            try:
                record_qs = model.objects.all()
                if row_type == "payment":
                    record_qs = record_qs.select_related("invoice")
                    if not request.user.is_superuser:
                        record_qs = record_qs.filter(owner=owner_user)
                elif not request.user.is_superuser:
                    record_qs = record_qs.filter(owner=owner_user)
                record = record_qs.get(pk=row_id)
                if row_type == "payment" and not _can_manage_sales_invoice_at_active_location(
                    request.user,
                    record.invoice,
                ):
                    raise model.DoesNotExist

                if row_type in {"invoice", "quotation"}:
                    _, existing_meta = _split_document_notes(record.notes)
                    record.notes = _compose_document_notes(comment, existing_meta)
                else:
                    previous_notes = record.notes
                    record.notes = comment
                record.save(update_fields=["notes"])
                if row_type == "payment":
                    _log_action(
                        request.user,
                        "payment",
                        "Sales invoice payment note corrected",
                        {
                            "payment_id": record.id,
                            "invoice_id": record.invoice_id,
                            "previous_notes": previous_notes,
                            "new_notes": record.notes,
                        },
                        severity="warn",
                    )
                messages.success(request, "Daily summary comment updated.")
            except (model.DoesNotExist, ValueError, TypeError):
                messages.error(request, "Could not update that daily summary comment.")
        else:
            messages.error(request, "Could not identify the daily summary row to update.")

        return redirect(f"{reverse('daily_summary')}?{urlencode({'date': selected_date.strftime('%Y-%m-%d')})}")

    invoices = SalesInvoice.objects.select_related("customer", "quotation", "location")
    quotations = SalesQuotation.objects.select_related("customer")

    if owner_user and not owner_user.is_superuser:
        invoices = invoices.filter(owner=owner_user)
        quotations = quotations.filter(owner=owner_user)

    day_invoices = invoices.filter(issued_at__date=selected_date).order_by("issued_at", "id")
    day_quotations = quotations.filter(created_at__date=selected_date).order_by("created_at", "id")

    method_totals = {
        "amount": Decimal("0.00"),
        "cash": Decimal("0.00"),
        "debit": Decimal("0.00"),
        "credit": Decimal("0.00"),
        "wire": Decimal("0.00"),
        "cheque": Decimal("0.00"),
        "discount": Decimal("0.00"),
        "tcost": Decimal("0.00"),
        "no_payment_credit": Decimal("0.00"),
    }
    daily_activity_rows = []

    if _sales_invoice_payments_available():
        collected_invoice_ids = set()
        payments = (
            SalesInvoicePayment.objects.unreversed().select_related("invoice", "invoice__customer", "invoice__quotation")
            .filter(payment_date__date=selected_date)
            .order_by("payment_date", "id")
        )
        if owner_user and not owner_user.is_superuser:
            payments = payments.filter(owner=owner_user)

        for payment in payments:
            collected_invoice_ids.add(payment.invoice_id)
            method = _normalize_sales_invoice_payment_method(payment.payment_method)
            amount = (payment.amount or Decimal("0.00")).quantize(Decimal("0.01"))
            cash_amount = amount if method == "cash" else Decimal("0.00")
            debit_amount = amount if method in {"debit_card", "card", "mobile_money", "jamdex"} else Decimal("0.00")
            credit_amount = amount if method in {"credit_card", "other"} else Decimal("0.00")
            wire_amount = amount if method == "bank_transfer" else Decimal("0.00")
            cheque_amount = amount if method == "cheque" else Decimal("0.00")

            method_totals["cash"] += cash_amount
            method_totals["debit"] += debit_amount
            method_totals["credit"] += credit_amount
            method_totals["wire"] += wire_amount
            method_totals["cheque"] += cheque_amount
            method_totals["amount"] += amount

            invoice = payment.invoice
            comment_payload = _daily_summary_comment_payload(payment.notes)
            daily_activity_rows.append({
                "row_type": "payment",
                "row_id": payment.id,
                "date": timezone.localtime(payment.payment_date).date(),
                "customer_name": invoice.customer.name if invoice.customer else "Walk-in / Unassigned",
                "cash": cash_amount,
                "debit": debit_amount,
                "credit": credit_amount,
                "wire": wire_amount,
                "cheque": cheque_amount,
                "amount": amount,
                "discount": Decimal("0.00"),
                "tcost": Decimal("0.00"),
                "no_payment_credit": Decimal("0.00"),
                "invoice_no": invoice.invoice_no or "-",
                "receipt_no": payment.reference or "-",
                "comment": comment_payload["display"],
                "comment_value": comment_payload["value"],
                "comment_locked": comment_payload["locked"],
                "comment_is_generated": comment_payload["is_generated"],
            })
    else:
        collected_invoice_ids = set()

    fully_paid_invoice_ids = {
        invoice.invoice_no
        for invoice in day_invoices
        if invoice.invoice_no and _sales_invoice_payment_totals(invoice)[1] <= Decimal("0.00")
    }

    for invoice in day_invoices:
        if invoice.invoice_no in fully_paid_invoice_ids:
            continue
        _, invoice_balance_due = _sales_invoice_payment_totals(invoice)
        invoice_discount = _document_summary_context(
            invoice.notes,
            invoice.subtotal,
            invoice.tax_amount,
            invoice.total_amount,
            Decimal("0.00"),
        )["discount_amount"].quantize(Decimal("0.01"))
        invoice_tcost = (invoice.subtotal or Decimal("0.00")).quantize(Decimal("0.01"))
        method_totals["no_payment_credit"] += invoice_balance_due
        method_totals["discount"] += invoice_discount
        method_totals["tcost"] += invoice_tcost
        comment_payload = _daily_summary_comment_payload(invoice.notes)
        daily_activity_rows.append({
            "row_type": "invoice",
            "row_id": invoice.id,
            "date": timezone.localtime(invoice.issued_at).date(),
            "customer_name": invoice.customer.name if invoice.customer else "Walk-in / Unassigned",
            "cash": Decimal("0.00"),
            "debit": Decimal("0.00"),
            "credit": Decimal("0.00"),
            "wire": Decimal("0.00"),
            "cheque": Decimal("0.00"),
            "amount": Decimal("0.00"),
            "discount": invoice_discount,
            "tcost": invoice_tcost,
            "no_payment_credit": invoice_balance_due,
            "invoice_no": invoice.invoice_no or "-",
            "receipt_no": "-",
            "comment": comment_payload["display"],
            "comment_value": comment_payload["value"],
            "comment_locked": comment_payload["locked"],
            "comment_is_generated": comment_payload["is_generated"],
        })

    invoiced_quote_ids = {
        invoice.quotation_id
        for invoice in day_invoices
        if invoice.quotation_id
    }
    for quotation in day_quotations:
        if quotation.id in invoiced_quote_ids:
            continue
        quotation_discount = _document_summary_context(
            quotation.notes,
            quotation.subtotal,
            quotation.tax_amount,
            quotation.total_amount,
            Decimal("0.00"),
        )["discount_amount"].quantize(Decimal("0.01"))
        quotation_tcost = (quotation.subtotal or Decimal("0.00")).quantize(Decimal("0.01"))
        method_totals["discount"] += quotation_discount
        method_totals["tcost"] += quotation_tcost
        comment_payload = _daily_summary_comment_payload(quotation.notes)
        daily_activity_rows.append({
            "row_type": "quotation",
            "row_id": quotation.id,
            "date": timezone.localtime(quotation.created_at).date(),
            "customer_name": quotation.customer.name if quotation.customer else "Walk-in / Unassigned",
            "cash": Decimal("0.00"),
            "debit": Decimal("0.00"),
            "credit": Decimal("0.00"),
            "wire": Decimal("0.00"),
            "cheque": Decimal("0.00"),
            "amount": Decimal("0.00"),
            "discount": quotation_discount,
            "tcost": quotation_tcost,
            "no_payment_credit": Decimal("0.00"),
            "invoice_no": "-",
            "receipt_no": "-",
            "comment": comment_payload["display"],
            "comment_value": comment_payload["value"],
            "comment_locked": comment_payload["locked"],
            "comment_is_generated": comment_payload["is_generated"],
        })

    daily_activity_rows.sort(key=lambda row: (row["date"], row["customer_name"], row["invoice_no"]))

    invoice_total = sum(
        ((invoice.total_amount or Decimal("0.00")) for invoice in day_invoices),
        Decimal("0.00"),
    )
    quotation_total = sum(
        ((quotation.total_amount or Decimal("0.00")) for quotation in day_quotations),
        Decimal("0.00"),
    )
    collection_location = getattr(profile, "default_location", None)
    if collection_location and collection_location.owner_id != getattr(owner_user, "id", None):
        collection_location = None
    collection_lines = _sales_collection_activity_lines(
        owner_user,
        selected_date=selected_date,
        location=collection_location,
    )
    delivered_today = _daily_summary_item_totals(collection_lines["delivered_from_hold"])
    collected_by_client = _daily_summary_item_totals(collection_lines["collected_immediately"])
    awaiting_collection = _daily_summary_item_totals(collection_lines["remaining_in_store"])

    awaiting_invoices = invoices.filter(
        status="paid",
        collection_status="awaiting_collection",
    ).filter(
        Q(collection_status_changed_at__date__lte=selected_date)
        | Q(collection_status_changed_at__isnull=True, issued_at__date__lte=selected_date)
    ).prefetch_related("items__item")
    if collection_location:
        awaiting_invoices = awaiting_invoices.filter(location=collection_location)
    aged_cutoff = selected_date - timedelta(days=7)
    aged_awaiting_invoice_ids = []
    for invoice in awaiting_invoices:
        held_since = invoice.collection_status_changed_at or invoice.issued_at
        if timezone.localtime(held_since).date() <= aged_cutoff:
            aged_awaiting_invoice_ids.append(invoice.id)

    aged_awaiting_collection = _daily_summary_item_totals(
        SalesInvoiceItem.objects.select_related("item").filter(invoice_id__in=aged_awaiting_invoice_ids)
    )
    sales = Sale.objects.filter(timestamp__date=selected_date)
    if owner_user and not owner_user.is_superuser:
        sales = sales.filter(owner=owner_user)
    discounts = (sales.aggregate(total=Sum("discount"))["total"] or Decimal("0.00")).quantize(Decimal("0.01"))
    has_discounts = discounts > Decimal("0.00")
    payment_total = sum(
        (
            method_totals["cash"],
            method_totals["debit"],
            method_totals["credit"],
            method_totals["wire"],
            method_totals["cheque"],
        ),
        Decimal("0.00"),
    )
    outstanding_total = sum((_sales_invoice_payment_totals(invoice)[1] for invoice in day_invoices), Decimal("0.00"))
    cash_count, _ = DailyCashCount.objects.get_or_create(
        owner=owner_user,
        summary_date=selected_date,
    )
    cash_count_display = {}
    for denomination, field_name in CASH_DENOMINATIONS:
        quantity = getattr(cash_count, field_name)
        cash_count_display[field_name] = quantity
        cash_count_display[f"total_{denomination}"] = Decimal(quantity * denomination).quantize(Decimal("0.01"))

    opening_cash_amount = (cash_count.opening_cash_amount or Decimal("0.00")).quantize(Decimal("0.01"))
    cash_expenditure_amount = (cash_count.cash_expenditure_amount or Decimal("0.00")).quantize(Decimal("0.01"))
    cash_lodgement_amount = (cash_count.cash_lodgement_amount or Decimal("0.00")).quantize(Decimal("0.01"))
    expected_cash = (
        opening_cash_amount
        + method_totals["cash"]
        - cash_expenditure_amount
        - cash_lodgement_amount
    ).quantize(Decimal("0.01"))
    expected_card_amount = (method_totals["debit"] + method_totals["credit"]).quantize(Decimal("0.01"))
    expected_reconciliation_total = (expected_cash + expected_card_amount).quantize(Decimal("0.01"))
    actual_cash = cash_count.actual_cash
    actual_card_amount = (cash_count.ncb_machine_amount + cash_count.bns_machine_amount).quantize(Decimal("0.01"))
    actual_reconciliation_total = (actual_cash + actual_card_amount).quantize(Decimal("0.01"))
    variance = (actual_reconciliation_total - expected_reconciliation_total).quantize(Decimal("0.01"))
    pending_transfers = StockTransfer.objects.filter(status="PENDING")
    if owner_user and not owner_user.is_superuser:
        pending_transfers = pending_transfers.filter(item__owner=owner_user)

    daily_alerts = []
    if variance:
        daily_alerts.append(f"Reconciliation variance detected (${variance:,.2f}) in cash/card totals.")
    aged_awaiting_count = sum((row["quantity"] for row in aged_awaiting_collection), 0)
    if aged_awaiting_count:
        aged_names = ", ".join(row["name"] for row in aged_awaiting_collection[:3])
        daily_alerts.append(f"{aged_awaiting_count} item(s) ({aged_names}) awaiting collection > 7 days.")
    pending_transfer_count = pending_transfers.count()
    if pending_transfer_count:
        daily_alerts.append(f"{pending_transfer_count} stock transfer(s) pending confirmation.")

    delivered_total_quantity = sum((row["quantity"] for row in delivered_today), 0)
    collected_total_quantity = sum((row["quantity"] for row in collected_by_client), 0)
    awaiting_total_quantity = sum((row["quantity"] for row in awaiting_collection), 0)
    open_invoice_count = sum(
        1 for invoice in day_invoices if _sales_invoice_payment_totals(invoice)[1] > Decimal("0.00")
    )
    profile_default_location = getattr(profile, "default_location", None) if profile else None
    branch_name = getattr(profile_default_location, "name", "") or "All active locations"

    context = {
        "profile": profile,
        "is_pro_user": _is_pro_user(owner_user),
        "can_access_daily_summary": can_access_daily_summary,
        "can_edit_daily_summary": can_edit_daily_summary,
        "selected_date": selected_date.strftime("%Y-%m-%d"),
        "report_date": selected_date.strftime("%B %-d, %Y") if os.name != "nt" else selected_date.strftime("%B %#d, %Y"),
        "daily_activity_rows": daily_activity_rows,
        "daily_quotations_count": day_quotations.count(),
        "daily_quotation_total": quotation_total,
        "daily_invoice_total": invoice_total,
        "payment_total": payment_total,
        "amount_total": method_totals["amount"],
        "cash_sales": method_totals["cash"],
        "debit_sales": method_totals["debit"],
        "credit_sales": method_totals["credit"],
        "wire_sales": method_totals["wire"],
        "cheque_sales": method_totals["cheque"],
        "discount_total": method_totals["discount"],
        "tcost_total": method_totals["tcost"],
        "no_payment_credit_sales": method_totals["no_payment_credit"],
        "outstanding": outstanding_total,
        "discounts": discounts,
        "has_discounts": has_discounts,
        "cash_count": cash_count_display,
        "opening_cash_amount": opening_cash_amount,
        "cash_expenditure_amount": cash_expenditure_amount,
        "cash_expenditure_party_name": cash_count.cash_expenditure_party_name,
        "cash_expenditure_currency": cash_count.cash_expenditure_currency,
        "cash_expenditure_receipt_reference": cash_count.cash_expenditure_receipt_reference,
        "cash_expenditure_notes": cash_count.cash_expenditure_notes,
        "cash_lodgement_amount": cash_lodgement_amount,
        "cash_lodgement_notes": cash_count.cash_lodgement_notes,
        "expected_cash": expected_cash,
        "expected_card_amount": expected_card_amount,
        "expected_reconciliation_total": expected_reconciliation_total,
        "actual_cash": actual_cash,
        "actual_card_amount": actual_card_amount,
        "actual_reconciliation_total": actual_reconciliation_total,
        "ncb_machine_amount": cash_count.ncb_machine_amount,
        "bns_machine_amount": cash_count.bns_machine_amount,
        "variance": variance,
        "daily_alerts": daily_alerts,
        "delivered_today": delivered_today,
        "collected_by_client": collected_by_client,
        "awaiting_collection": awaiting_collection,
        "gross_sales": invoice_total,
        "total_gross": invoice_total,
        "invoices_count": day_invoices.count(),
        "payments_count": len(collected_invoice_ids),
        "open_invoices_count": open_invoice_count,
        "branch_name": branch_name,
        "delivered_total_quantity": delivered_total_quantity,
        "collected_total_quantity": collected_total_quantity,
        "awaiting_total_quantity": awaiting_total_quantity,
        "aged_awaiting_count": aged_awaiting_count,
        "pending_transfer_count": pending_transfer_count,
        "daily_activity_value_total": (invoice_total + quotation_total).quantize(Decimal("0.01")),
        "is_closeout_wizard": request.GET.get("closeout") == "1",
        "next_closeout_url": f"{reverse('cash_reconciliation')}?{urlencode({'date': selected_date.strftime('%Y-%m-%d'), 'closeout': '1'})}",
    }
    return render(request, "inventory/daily_summary.html", context)

@login_required
@role_required(["admin", "manager", "cashier"])
def operations(request):
    """
    Daily reporting page for QuickStock JA.
    Ensures profile is retrieved or created for authenticated users.
    """
    profile = UserProfile.for_user(request.user)
    owner_user = _inventory_owner_for_user(request.user)
    selected_date = _parse_operations_date(request)
    selected_date_string = selected_date.strftime("%Y-%m-%d")
    closeout_query = {"date": selected_date_string, "closeout": "1"}

    summary_line_count = 0
    if _sales_invoice_payments_available():
        payments = SalesInvoicePayment.objects.unreversed().filter(payment_date__date=selected_date)
        if owner_user and not owner_user.is_superuser:
            payments = payments.filter(owner=owner_user)
        summary_line_count += payments.count()

    invoices = SalesInvoice.objects.filter(issued_at__date=selected_date)
    quotations = SalesQuotation.objects.filter(created_at__date=selected_date)
    if owner_user and not owner_user.is_superuser:
        invoices = invoices.filter(owner=owner_user)
        quotations = quotations.filter(owner=owner_user)
    summary_line_count += invoices.count() + quotations.count()

    reconciliation_context = _daily_reconciliation_context(owner_user, selected_date)
    variance = reconciliation_context["variance"]
    variance_abs = abs(variance).quantize(Decimal("0.01"))
    drawer_balanced = variance == Decimal("0.00")

    collection_location = getattr(profile, "default_location", None)
    if collection_location and collection_location.owner_id != getattr(owner_user, "id", None):
        collection_location = None
    collection_lines = _sales_collection_activity_lines(
        owner_user,
        selected_date=selected_date,
        location=collection_location,
    )
    awaiting_collection = _daily_summary_item_totals(collection_lines["remaining_in_store"])
    awaiting_total_quantity = sum((row["quantity"] for row in awaiting_collection), 0)

    open_shift_qs = CashShift.objects.select_related("cashier", "location").filter(is_closed=False)
    if owner_user and not owner_user.is_superuser:
        open_shift_qs = open_shift_qs.filter(owner=owner_user)
    open_shift_count = open_shift_qs.count()
    active_shift = open_shift_qs.order_by("-opened_at").first()
    cash_reconciliation_params = dict(closeout_query)
    if active_shift:
        cash_reconciliation_params["shift"] = active_shift.id

    summary_status = (
        "Pending Review"
        if summary_line_count
        else "No Activity Yet"
    )
    summary_status_tone = "warning" if summary_line_count else "neutral"
    reconciliation_status = (
        "Drawer Balanced"
        if drawer_balanced
        else f"${variance_abs:,.2f} Variance"
    )
    reconciliation_status_tone = "success" if drawer_balanced else "danger"
    collection_status = (
        "All Collections Cleared"
        if awaiting_total_quantity == 0
        else f"{awaiting_total_quantity:,} Awaiting Pickup"
    )
    collection_status_tone = "success" if awaiting_total_quantity == 0 else "warning"
    shift_status = (
        f"{open_shift_count} Open Shift{'' if open_shift_count == 1 else 's'}"
        if open_shift_count
        else "No Open Shifts"
    )

    return render(
        request,
        "inventory/operations.html",
        {
            "profile": profile,
            "is_pro_user": _is_pro_user(owner_user),
            "selected_date": selected_date_string,
            "summary_line_count": summary_line_count,
            "summary_status": summary_status,
            "summary_status_tone": summary_status_tone,
            "reconciliation_status": reconciliation_status,
            "reconciliation_status_tone": reconciliation_status_tone,
            "collection_status": collection_status,
            "collection_status_tone": collection_status_tone,
            "open_shift_count": open_shift_count,
            "shift_status": shift_status,
            "start_closeout_url": f"{reverse('daily_summary')}?{urlencode(closeout_query)}",
            "balance_drawer_url": f"{reverse('cash_reconciliation')}?{urlencode(cash_reconciliation_params)}",
            "deliveries_closeout_url": f"{reverse('deliveries_collections')}?{urlencode(closeout_query)}",
        },
    )



def about_view(request):
    """About page with optional personalization and production-safe defaults."""
    profile = None
    if request.user.is_authenticated:
        profile = UserProfile.for_user(request.user)

    user = request.user if request.user.is_authenticated else None

    app_name = getattr(settings, "APP_NAME", "QuickStock JA")
    app_version = getattr(settings, "APP_VERSION", "V2.6.0")
    app_build_label = getattr(settings, "APP_BUILD_LABEL", "STABLE_BUILD")
    app_region = getattr(settings, "APP_REGION", "Caribbean")
    app_tagline = getattr(
        settings,
        "APP_TAGLINE",
        "High-integrity inventory management engineered for the region's retail and distribution networks.",
    )

    contact_name = getattr(settings, "ABOUT_CONTACT_NAME", "QuickStock JA Support")
    contact_role = getattr(settings, "ABOUT_CONTACT_ROLE", "Operations & Reliability")
    contact_email = getattr(settings, "ABOUT_CONTACT_EMAIL", getattr(settings, "DEFAULT_FROM_EMAIL", ""))
    contact_phone = getattr(settings, "ABOUT_CONTACT_PHONE", "")

    display_name = None
    if user:
        display_name = user.get_full_name().strip() or user.username

    default_location = getattr(profile, "default_location", None) if profile else None
    tax_rate = _get_tax_rate_for_location(default_location) if user else _get_tax_rate_for_country("JM")
    tax_label = _get_tax_label_for_location(default_location) if user else TAX_LABELS_BY_COUNTRY["JM"]

    plan_label = None
    plan_status = None
    if profile:
        plan_label = profile.plan_badge_label
        if profile.is_pro_active():
            plan_status = "Active (PRO)"
        elif profile.is_trial_active():
            plan_status = "Active (Trial)"
        elif profile.plan == "TRIAL":
            plan_status = "Trial Expired"
        elif profile.plan == "PRO":
            plan_status = "Subscription Expired"
        else:
            plan_status = "Inactive"

    # Resolve renewal date and display tier for UI consistency using hierarchy
    sub_holder = profile.get_effective_plan_owner() if profile else None
    renewal_date = None

    if sub_holder:
        # Check pro_expires first; fall back gracefully to plan_end if it's a Trial account
        if sub_holder.pro_expires:
            renewal_date = sub_holder.pro_expires
        elif sub_holder.plan_end:
            renewal_date = sub_holder.plan_end.date()

    if request.user.is_superuser:
        display_tier = "PRO (SYSTEM ADMIN)"
    elif sub_holder:
        display_tier = sub_holder.plan_badge_label
    else:
        display_tier = "TRIAL"

    stats = None
    if user:
        owner = _inventory_owner_for_user(user)
        cache_key = f"about:stats:{owner.id}"
        stats = cache.get(cache_key)
        if stats is None:
            try:
                items_qs = _item_queryset_for_user(user)
                totals = items_qs.aggregate(
                    item_count=Count("id"),
                    total_units=Sum("quantity"),
                    inventory_value=Sum(
                        ExpressionWrapper(
                            F("quantity") * F("cost_price"),
                            output_field=DecimalField(max_digits=12, decimal_places=2),
                        )
                    ),
                    retail_value=Sum(
                        ExpressionWrapper(
                            F("quantity") * F("price"),
                            output_field=DecimalField(max_digits=12, decimal_places=2),
                        )
                    ),
                )
                stats = {
                    "item_count": _safe_int(totals.get("item_count"), 0),
                    "total_units": _safe_int(totals.get("total_units"), 0),
                    "inventory_value": _safe_decimal(totals.get("inventory_value") or 0, default="0"),
                    "retail_value": _safe_decimal(totals.get("retail_value") or 0, default="0"),
                    "category_count": items_qs.values("category").distinct().count(),
                    "brand_count": items_qs.values("brand").distinct().count(),
                    "location_count": _location_queryset_for_user(user).count(),
                }
                cache.set(cache_key, stats, 300)
            except Exception:
                stats = None

    context = {
        "profile": profile,
        "display_name": display_name,
        "app_name": app_name,
        "app_version": app_version,
        "app_build_label": app_build_label,
        "app_region": app_region,
        "app_tagline": app_tagline,
        "contact_name": contact_name,
        "contact_role": contact_role,
        "contact_email": contact_email,
        "contact_phone": contact_phone,
        "default_location": default_location,
        "tax_rate_percent": int((tax_rate or Decimal("0")) * 100),
        "tax_label": tax_label,
        "plan_label": plan_label,
        "plan_status": plan_status,
        "stats": stats,
        "renewal_date": renewal_date,
        "display_tier": display_tier,
        "current_year": timezone.now().year,
    }
    return render(request, "inventory/about.html", context)


def privacy_view(request):
    """Privacy policy page."""
    return render(request, "inventory/privacy.html")


def terms_view(request):
    """Terms of service page."""
    return render(request, "inventory/terms.html")


def status_view(request):
    """Public status page backed by the runtime health report."""
    report = get_runtime_health()
    context = {
        "status": report["status"],
        "checks": report["checks"],
        "status_label": "All systems operational" if report["status"] == "ok" else "System issues detected",
    }
    return render(request, "inventory/status.html", context)

def support_view(request):
    """
    Hybrid support hook:
    - Authenticated users: Auto-attach profile info and their specific logs.
    - Anonymous users: Provide a clean contact form for login/signup help.
    """
    is_authenticated = request.user.is_authenticated
    profile = None
    recent_logs = []

    # 1. Context Gathering (Layered Security)
    if is_authenticated:
        profile = getattr(request.user, "profile", None) or UserProfile.for_user(request.user)
        recent_logs = AuditLog.objects.filter(user=request.user).order_by("-created_at")[:5]

    context = {
        "profile": profile,
        "recent_logs": recent_logs,
        "is_authenticated": is_authenticated,
        "wipay_env": getattr(settings, "WIPAY_ENVIRONMENT", "sandbox"),
        "submitted_email": "",
        "submitted_message": "",
    }

    if request.method == "POST":
        user_message = request.POST.get("message", "").strip()
        sender_email = request.user.email if is_authenticated else request.POST.get("email", "").strip()
        username = request.user.username if is_authenticated else "Anonymous/Guest"
        context.update(
            {
                "submitted_email": sender_email,
                "submitted_message": user_message,
            }
        )

        if not user_message:
            messages.error(request, "Please enter a message before submitting.")
            return render(request, "inventory/support.html", context)

        if not sender_email:
            messages.error(request, "An email address is required to submit a ticket.")
            return render(request, "inventory/support.html", context)

        try:
            sender_email = _normalize_email_address(sender_email)
        except ValidationError:
            messages.error(request, "Please enter a valid email address.")
            return render(request, "inventory/support.html", context)

        allowed, retry_after = _consume_support_ticket_quota(request)
        if not allowed:
            messages.error(
                request,
                "Too many support requests were submitted. Please wait a few minutes and try again.",
            )
            response = render(request, "inventory/support.html", context, status=429)
            response["Retry-After"] = str(retry_after)
            return response

        # 2. Build the metadata body for tech support diagnostics
        env = context["wipay_env"]
        subject = f"[QuickStock Support Ticket] Entry from {username}"
        
        body_lines = [
            f"User: {username}",
            f"Reply-To Email: {sender_email}",
            f"Environment: {env.upper()}",
            f"Timestamp: {timezone.now()}",
            "--------------------------------------------------",
            "MESSAGE:",
            user_message,
            "--------------------------------------------------",
        ]

        if is_authenticated and profile:
            body_lines.extend([
                "DIAGNOSTIC METADATA:",
                f"Account Role: {profile.role}",
                f"Current Plan: {profile.plan}",
                f"Assigned Default Location: {profile.default_location}",
                "--------------------------------------------------",
                "RECENT RELEVANT AUDIT LOGS (MAX 5):"
            ])
            if recent_logs:
                for log in recent_logs:
                    body_lines.append(f"[{log.created_at}] ({log.action}) {log.message} [{log.severity.upper()}]")
            else:
                body_lines.append("No recent audit logs available for this user.")
            body_lines.append("--------------------------------------------------")

        full_body = "\n".join(body_lines)
        support_recipient = getattr(settings, "SUPPORT_EMAIL_RECIPIENT", getattr(settings, "DEFAULT_FROM_EMAIL", ""))

        try:
            email = EmailMessage(
                subject=subject,
                body=full_body,
                to=[support_recipient],
                reply_to=[sender_email]
            )
            email.send(fail_silently=False)
            
            _log_action(
                user=request.user if is_authenticated else None,
                action="SUPPORT_TICKET_SUBMITTED",
                message=f"Support request successfully dispatched from {sender_email}.",
                severity="info"
            )
            
            messages.success(request, "Your support request has been sent! Our technical operations team will review it shortly.")
            return redirect("index")

        except Exception as exc:
            _log_action(
                user=request.user if is_authenticated else None,
                action="SUPPORT_TICKET_FAILED",
                message=f"Could not send support ticket email: {str(exc)}",
                severity="error"
            )
            messages.error(request, "System error preventing email dispatch. Please attempt submission again later.")

    # 5. Render with Layered Context
    return render(
        request,
        "inventory/support.html",
        context,
    )
    
def pricing_view(request):
    """
    Pricing page for QuickStock JA.
    Passes user profile if authenticated.
    """
    profile = getattr(request.user, "profile", None) if request.user.is_authenticated else None
    return render(request, "inventory/pricing.html", {"profile": profile})

@login_required
@role_required(["admin", "manager"])
def import_items_view(request):
    """
    CSV import for items.
    Columns supported: name, sku, price, quantity, cost_price, brand, category.
    """
    if request.method == "POST" and request.FILES.get("csv_file"):
        file = request.FILES["csv_file"]
        if file.size > 2 * 1024 * 1024:  # 2MB guard
            messages.error(request, "File too large (max 2MB).")
            return redirect("import_items")

        decoded = file.read().decode("utf-8", errors="ignore").splitlines()
        reader = csv.DictReader(decoded)

        created, updated, errors = 0, 0, []
        owner = _inventory_owner_for_user(request.user)

        for idx, row in enumerate(reader, start=2):  # header is row 1
            try:
                name = (row.get("name") or "").strip()
                if not name:
                    errors.append(f"Line {idx}: 'name' is missing.")
                    continue

                sku = (row.get("sku") or "").strip() or None
                price = _safe_decimal(row.get("price") or "0")
                quantity = _safe_int(row.get("quantity") or 0)
                cost_price = _safe_decimal(row.get("cost_price") or "0")
                brand_name = (row.get("brand") or "Generic").strip()
                category_name = (row.get("category") or "General").strip()

                brand, _ = Brand.objects.get_or_create(owner=owner, name=brand_name)
                category, _ = Category.objects.get_or_create(owner=owner, name=category_name)

                with transaction.atomic():
                    item, created_flag = Item.objects.update_or_create(
                        owner=owner,
                        sku=sku,
                        defaults={
                            "name": name,
                            "price": price,
                            "cost_price": cost_price,
                            "brand": brand,
                            "category": category,
                        },
                    )
                    
                    main_store = _get_main_store(owner)
                    StockRecord.objects.update_or_create(
                        item=item,
                        location=main_store,
                        defaults={"quantity": quantity}
                    )
            
                created += int(created_flag)
                updated += int(not created_flag)
            except Exception as e:
                errors.append(f"Line {idx} processing error: {str(e)}")

        if created or updated:
            messages.success(request, f"Import complete. Created {created}, updated {updated}.")
        if errors:
            # Log the full error set but show a summary to the user
            _log_action(request.user, "inventory_import", f"Bulk import finished with {len(errors)} errors.", {"errors": errors}, severity="warn")
            messages.error(request, f"Import completed with {len(errors)} issues. Check system logs for details.")
        return redirect("import_items")

    return render(request, "inventory/import_items.html")


@login_required
@role_required(["admin", "manager"])
def import_customers_view(request):
    """
    CSV import for customers.
    Columns: name, email, phone, notes
    """
    if request.method == "POST" and request.FILES.get("csv_file"):
        file = request.FILES["csv_file"]
        if file.size > 2 * 1024 * 1024:  # 2MB guard
            messages.error(request, "File too large (max 2MB).")
            return redirect("import_customers")

        decoded = file.read().decode("utf-8", errors="ignore").splitlines()
        reader = csv.DictReader(decoded)

        created, updated, errors = 0, 0, []
        owner = _inventory_owner_for_user(request.user)

        for idx, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            email = (row.get("email") or "").strip().lower() or None
            phone = (row.get("phone") or "").strip() or None
            physical_address = (row.get("physical_address") or row.get("address") or "").strip()
            business_address = (row.get("business_address") or "").strip()
            notes = (row.get("notes") or "").strip()

            if not name and not email and not phone:
                errors.append(f"Row {idx}: name or contact required.")
                continue

            # Match priority: email then phone then name
            qs = Customer.objects.filter(owner=owner)
            if email:
                qs = qs.filter(email=email)
            elif phone:
                qs = qs.filter(phone=phone)
            else:
                qs = qs.filter(name=name)

            if qs.exists():
                customer = qs.first()
                customer.name = name or customer.name
                customer.email = email or customer.email
                customer.phone = phone or customer.phone
                if physical_address:
                    customer.physical_address = physical_address
                if business_address:
                    customer.business_address = business_address
                if notes:
                    customer.notes = notes
                customer.save()
                updated += 1
            else:
                Customer.objects.create(
                    owner=owner,
                    name=name or (email or phone),
                    email=email,
                    phone=phone,
                    physical_address=physical_address,
                    business_address=business_address,
                    notes=notes,
                )
                created += 1

        if created or updated:
            messages.success(request, f"Customer import complete. Created {created}, updated {updated}.")
        if errors:
            messages.error(request, "Some rows failed: " + " | ".join(errors[:5]))
        return redirect("import_customers")

    return render(request, "inventory/import_customers.html")

from django.shortcuts import redirect
from .forms import CustomerForm

@login_required
@role_required(["admin", "manager", "cashier"])
def add_customer(request):
    owner = _inventory_owner_for_user(request.user)
    if not owner:
        messages.error(request, "Unable to resolve customer owner.")
        return redirect("customer_list")

    if request.method == "POST":
        form = CustomerForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                customer = form.save(commit=False)
                customer.owner = owner
                customer.save()
                _log_action(
                    request.user,
                    "customer",
                    "Customer created",
                    {"customer_id": customer.id, "customer_name": customer.name},
                    required=True,
                )
            return redirect('customer_list')
    else:
        form = CustomerForm()
    
    return render(request, 'inventory/add_customer.html', {'form': form})

# inventory/views.py
from django.shortcuts import render, get_object_or_404, redirect
from .models import Customer, CustomerNote # Replace with your actual model name

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from .models import Customer
from .forms import CustomerForm  # Ensure you have a CustomerForm defined

@login_required
@role_required(["admin", "manager", "cashier"])
def edit_customer(request, pk):
    customer = get_object_or_404(_customer_queryset_for_user(request.user), pk=pk)
    note_entries = customer.note_entries.select_related("created_by").all()

    if request.method == 'POST':
        if request.POST.get("post_note") == "1":
            new_note = (request.POST.get("new_note") or "").strip()
            if not new_note:
                messages.error(request, "Add a note before posting to the timeline.")
                return redirect("edit_customer", pk=customer.pk)
            with transaction.atomic():
                CustomerNote.objects.create(
                    customer=customer,
                    owner=customer.owner,
                    created_by=request.user,
                    body=new_note,
                )
                _log_action(
                    request.user,
                    "customer",
                    "Customer note added",
                    {"customer_id": customer.id, "customer_name": customer.name},
                    required=True,
                )
            messages.success(request, "Account note posted to the customer timeline.")
            return redirect('customer_detail', pk=customer.pk)

        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            with transaction.atomic():
                customer = form.save()
                new_note = form.cleaned_data.get("new_note")
                if new_note:
                    CustomerNote.objects.create(
                        customer=customer,
                        owner=customer.owner,
                        created_by=request.user,
                        body=new_note,
                    )
                _log_action(
                    request.user,
                    "customer",
                    "Customer updated",
                    {"customer_id": customer.id, "customer_name": customer.name, "note_added": bool(new_note)},
                    required=True,
                )
            messages.success(request, f"Customer '{customer.name}' updated successfully!")
            return redirect('customer_detail', pk=customer.pk)
    else:
        form = CustomerForm(instance=customer)

    context = {
        'form': form,
        'customer': customer,
        'title': f'Edit {customer.name}',
        'note_entries': note_entries,
    }
    return render(request, 'inventory/edit_customer.html', context)

@login_required
@role_required(["admin", "manager"])
def delete_customer(request, pk):
    customer = get_object_or_404(_customer_queryset_for_user(request.user), pk=pk)
    if request.method == 'POST':
        try:
            customer_id = customer.id
            customer_name = customer.name
            with transaction.atomic():
                customer.delete()
                _log_action(
                    request.user,
                    "customer",
                    "Customer deleted",
                    {"customer_id": customer_id, "customer_name": customer_name},
                    severity="warn",
                    required=True,
                )
        except ProtectedError:
            _archive_record(customer, request.user, "Customer retained because financial history is protected.")
            _log_action(
                request.user,
                "customer",
                "Customer archived instead of deleted",
                {"customer_id": customer.id, "reason": "protected_financial_history"},
                required=True,
            )
            messages.warning(request, "This customer has financial history and was archived for ledger reconciliation.")
        return redirect('customer_list')
    return render(request, 'inventory/customer_confirm_delete.html', {'customer': customer})

def inventory_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)
        
    # Block API access for Desktop App if subscription is not active
    profile = getattr(request.user, 'profile', None) or UserProfile.for_user(request.user)
    owner_profile = profile.get_effective_plan_owner()
    
    has_access = False
    if owner_profile.is_pro_active():
        has_access = True
    elif owner_profile.plan == 'TRIAL' and owner_profile.is_trial_active():
        has_access = True

    if not has_access:
        return JsonResponse({"error": "QuickStock PRO subscription required."}, status=403)

    if request.method != "GET":
        if profile.role == "cashier":
            return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
        return JsonResponse({"ok": False, "error": "Method not allowed"}, status=405)

    try:
        effective_owner = _inventory_owner_for_user(request.user)
        location_id = request.GET.get("location_id")

        # 1. Location Resolution
        location = None
        if location_id:
            location = Location.objects.filter(id=location_id, owner=effective_owner).first()
            if not location:
                return JsonResponse({"ok": False, "error": "Location not found"}, status=404)

        # 2. Unified Data Retrieval
        # We query the Item model directly to ensure all products appear in the desktop app,
        # resolving quantities from StockRecord if a location is selected.
        items_qs = _item_queryset_for_user(request.user).select_related('category', 'brand')
        
        data = []
        is_main_store = location and (location.name or "").strip().lower() == "main store"

        for it in items_qs:
            try:
                # Resolve quantity based on location context
                qty = 0
                if location:
                    record = StockRecord.objects.filter(item=it, location=location).first()
                    if record:
                        qty = record.quantity
                    elif is_main_store:
                        # Fallback for Main Store legacy data synchronization
                        qty = it.total_quantity
                else:
                    # Fallback to general Item quantity if no location specified
                    qty = it.total_quantity

                data.append({
                    "sku": it.sku, "barcode": it.barcode, "name": it.name,
                    "category": it.category.name if it.category else "General",
                    "quantity": qty,
                    "price": str(it.price), "cost_price": str(it.cost_price),
                    "brand": it.brand.name if it.brand else "Generic"
                })
            except Exception:
                logger.exception(f"Item serialization failed for SKU {it.sku}")
                continue

        return JsonResponse({"ok": True, "items": data})

    except Exception as e:
        logger.exception("Inventory API crash")
        return JsonResponse(
            {"ok": False, "error": "Inventory data is temporarily unavailable."},
            status=500,
        )


@login_required
def api_locations(request):
    profile = UserProfile.for_user(request.user)
    owner = _inventory_owner_for_user(request.user)

    if request.method == "POST":
        if profile.role == "cashier":
            return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)
        try:
            payload = json.loads(request.body.decode("utf-8")) if request.body else request.POST
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

        name = str(payload.get("name") or "").strip()
        if not name:
            return JsonResponse({"ok": False, "error": "Location name is required."}, status=400)
        location, _created = Location.objects.get_or_create(owner=owner, name=name)
        return JsonResponse({"ok": True, "location": {"id": location.id, "name": location.name}})

    if request.method != "GET":
        return JsonResponse({"ok": False, "error": "Method not allowed"}, status=405)

    locations = _location_queryset_for_user(request.user).order_by("name")
    return JsonResponse({
        "ok": True,
        "locations": [
            {
                "id": location.id,
                "name": location.name,
                "address": location.address,
                "is_warehouse": location.is_warehouse,
            }
            for location in locations
        ],
    })


@login_required
@require_POST
def api_sales(request):
    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else request.POST
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    owner = _inventory_owner_for_user(request.user)
    location_id = payload.get("location_id")
    location = Location.objects.filter(id=location_id, owner=owner).first()
    if not location:
        return JsonResponse({"ok": False, "error": "Location not found"}, status=404)

    client_reference = str(
        payload.get("offline_client_ref") or payload.get("sync_token") or ""
    ).strip()
    if client_reference:
        existing_sale = Sale.objects.filter(owner=owner, sync_token=client_reference).first()
        if existing_sale:
            return JsonResponse(
                {
                    "ok": True,
                    "success": True,
                    "sale_id": existing_sale.id,
                    "receipt_no": existing_sale.receipt_no,
                    "client_reference": client_reference,
                    "contract_version": payload.get("contract_version", 1),
                    "idempotent_replay": True,
                }
            )

    cart_items = payload.get("items") or []
    if not isinstance(cart_items, list):
        return JsonResponse({"ok": False, "error": "Cart items must be a list."}, status=400)
    if not cart_items:
        return JsonResponse({"ok": False, "error": "Cart is empty."}, status=400)
    max_cart_lines = max(1, int(getattr(settings, "POS_CART_MAX_LINES", 200)))
    if len(cart_items) > max_cart_lines:
        return JsonResponse(
            {"ok": False, "error": f"A sale may contain at most {max_cart_lines} lines."},
            status=413,
        )
    if any(not isinstance(row, dict) for row in cart_items):
        return JsonResponse({"ok": False, "error": "Each cart line must be an object."}, status=400)

    requested_skus = [str(item.get("sku") or "").strip() for item in cart_items]
    if any(not sku for sku in requested_skus):
        return JsonResponse({"ok": False, "error": "Every cart line requires a SKU."}, status=400)
    if len(set(requested_skus)) != len(requested_skus):
        return JsonResponse({"ok": False, "error": "Duplicate SKUs are not allowed in one sale."}, status=400)
    items = {
        item.sku: item
        for item in _item_queryset_for_user(request.user)
        .select_related("category", "brand")
        .filter(sku__in=requested_skus)
    }

    line_entries = []
    for row in cart_items:
        sku = str(row.get("sku") or "").strip()
        quantity = _safe_int(row.get("quantity"), 0)
        if quantity <= 0:
            return JsonResponse(
                {"ok": False, "error": f"Quantity for {sku} must be greater than zero."},
                status=400,
            )

        item = items.get(sku)
        if not item:
            return JsonResponse({"ok": False, "error": f"Item {sku} not found."}, status=404)
        line_entries.append((item, quantity))

    if not line_entries:
        return JsonResponse({"ok": False, "error": "Cart is empty."}, status=400)

    try:
        discount = _safe_decimal(payload.get("discount", "0"), default="0")
        tender = str(payload.get("tender") or "cash").strip().lower()
        if tender not in dict(Sale.TENDER_CHOICES):
            return JsonResponse({"ok": False, "error": "Invalid payment method."}, status=400)
        gross_total = sum((item.price * quantity for item, quantity in line_entries), Decimal("0.00"))
        if discount < 0 or discount > gross_total:
            return JsonResponse(
                {"ok": False, "error": "Discount must be between zero and the gross sale total."},
                status=400,
            )

        with transaction.atomic():
            sale_shift = None
            requested_shift_id = payload.get("shift_id")
            if not requested_shift_id and isinstance(payload.get("register"), dict):
                requested_shift_id = payload["register"].get("id")
            if requested_shift_id:
                sale_shift = (
                    CashShift.objects.select_for_update()
                    .filter(
                        pk=requested_shift_id,
                        cashier=request.user,
                        location=location,
                        is_closed=False,
                    )
                    .first()
                )
                if sale_shift is None:
                    return JsonResponse(
                        {"ok": False, "error": "The referenced register shift is unavailable or outside this location."},
                        status=403,
                    )
            else:
                # Legacy API callers without a register payload are retained as
                # explicit non-register sales for backward compatibility.
                sale_shift = (
                    CashShift.objects.select_for_update()
                    .filter(cashier=request.user, location=location, is_closed=False)
                    .first()
                )
            item_ids = [item.id for item, _quantity in line_entries]
            stock_map = {
                stock.item_id: stock
                for stock in StockRecord.objects.select_for_update().filter(
                    item_id__in=item_ids,
                    location=location,
                )
            }

            for item, quantity in line_entries:
                stock = stock_map.get(item.id)
                if not stock or int(stock.quantity or 0) < quantity:
                    return JsonResponse({"ok": False, "error": f"Insufficient stock for {item.name}."}, status=400)

            sale = Sale.objects.create(
                owner=owner,
                cashier=request.user,
                shift=sale_shift,
                location=location,
                discount=discount,
                tender=tender,
                sync_source="web",
                sync_token=client_reference or None,
            )

            for item, quantity in line_entries:
                SaleItem.objects.create(
                    sale=sale,
                    item=item,
                    quantity=quantity,
                    unit_price=item.price,
                    unit_cost=item.cost_price or Decimal("0.00"),
                )

            sale.recalculate_totals()
            if sale_shift:
                sale_shift.calculate_expected_balance(force=True)
                sale_shift.save(
                    update_fields=["total_sales", "expected_cash", "last_calculated_at"],
                    _allow_integrity_update=True,
                )

        return JsonResponse(
            {
                "ok": True,
                "success": True,
                "sale_id": sale.id,
                "client_reference": client_reference,
                "contract_version": payload.get("contract_version", 1),
            }
        )
    except ValidationError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)


@login_required
@require_POST
def api_transfer_stock(request):
    profile = UserProfile.for_user(request.user)
    if not request.user.is_superuser and profile.role not in {"admin", "manager"}:
        return JsonResponse({"ok": False, "error": "Forbidden"}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else request.POST
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Invalid JSON payload."}, status=400)

    owner = _inventory_owner_for_user(request.user)
    sku = str(payload.get("sku") or "").strip()
    quantity = _safe_int(payload.get("quantity"), 0)
    if quantity <= 0:
        return JsonResponse({"ok": False, "error": "Quantity must be at least 1."}, status=400)

    item = _item_queryset_for_user(request.user).filter(sku=sku).first()
    source = Location.objects.filter(id=payload.get("from_location_id"), owner=owner).first()
    destination = Location.objects.filter(id=payload.get("to_location_id"), owner=owner).first()
    if not item:
        return JsonResponse({"ok": False, "error": "Item not found."}, status=404)
    if not source or not destination:
        return JsonResponse({"ok": False, "error": "Location not found."}, status=404)
    if source.id == destination.id:
        return JsonResponse({"ok": False, "error": "Source and destination cannot be the same."}, status=400)

    with transaction.atomic():
        source_stock = (
            StockRecord.objects.select_for_update()
            .filter(item=item, location=source)
            .first()
        )
        if not source_stock or int(source_stock.quantity or 0) < quantity:
            return JsonResponse({"ok": False, "error": "Insufficient stock."}, status=400)

        destination_stock, _created = StockRecord.objects.select_for_update().get_or_create(
            item=item,
            location=destination,
            defaults={"quantity": 0},
        )
        source_stock.quantity = int(source_stock.quantity or 0) - quantity
        destination_stock.quantity = int(destination_stock.quantity or 0) + quantity
        source_stock.save(update_fields=["quantity"])
        destination_stock.save(update_fields=["quantity"])
        StockTransfer.objects.create(
            item=item,
            from_location=source,
            to_location=destination,
            quantity=quantity,
            user=request.user,
            status="COMPLETED",
        )

    return JsonResponse({"ok": True, "success": True})
# ---------------------------
# Authentication
def _social_callback_url(request, provider: str) -> str:
    return request.build_absolute_uri(reverse("social_login_callback", args=[provider]))


def _extract_social_email(provider: str, userinfo: dict) -> tuple[str, bool]:
    if provider == "google":
        email = userinfo.get("email") or ""
        return email, bool(userinfo.get("email_verified"))

    email = userinfo.get("email") or userinfo.get("preferred_username") or userinfo.get("upn") or ""
    return email, True


def _get_social_login_user(email: str):
    normalized_email = _normalize_email_address(email)
    matches = list(User.objects.filter(email__iexact=normalized_email).order_by("id")[:2])
    if not matches:
        raise ValidationError("No QuickStock account is linked to that email address yet.")
    if len(matches) > 1:
        raise ValidationError("Multiple QuickStock accounts use that email address. Please sign in with your username and password.")
    return matches[0]


def social_login_start(request, provider):
    config = _social_provider_settings(provider)
    if not config.get("enabled"):
        messages.error(request, f"{config.get('label') or provider.title()} sign-in is not configured yet.")
        return redirect("login")

    state = secrets.token_urlsafe(24)
    request.session[f"social_auth_state:{config['provider']}"] = state
    params = {
        "client_id": config["client_id"],
        "redirect_uri": _social_callback_url(request, config["provider"]),
        "response_type": "code",
        "scope": " ".join(config.get("scope") or []),
        "state": state,
        "prompt": "select_account",
    }
    return redirect(f"{config['authorize_url']}?{urlencode(params)}")


def social_login_callback(request, provider):
    config = _social_provider_settings(provider)
    if not config.get("enabled"):
        messages.error(request, f"{config.get('label') or provider.title()} sign-in is not configured yet.")
        return redirect("login")

    expected_state = request.session.pop(f"social_auth_state:{config['provider']}", None)
    returned_state = request.GET.get("state", "")
    code = request.GET.get("code", "")
    error = request.GET.get("error", "")

    if error:
        messages.error(request, f"{config.get('label') or provider.title()} sign-in was cancelled or denied.")
        return redirect("login")

    if not code or not expected_state or returned_state != expected_state:
        messages.error(request, "Could not verify the social sign-in request. Please try again.")
        return redirect("login")

    token_payload = {
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": _social_callback_url(request, config["provider"]),
    }

    try:
        token_response = requests.post(
            config["token_url"],
            data=token_payload,
            timeout=15,
        )
        token_response.raise_for_status()
        token_data = token_response.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise ValueError("Missing access token")

        userinfo_response = requests.get(
            config["userinfo_url"],
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        userinfo_response.raise_for_status()
        userinfo = userinfo_response.json()
    except (requests.RequestException, ValueError, json.JSONDecodeError):
        messages.error(request, f"{config.get('label') or provider.title()} sign-in could not be completed right now.")
        return redirect("login")

    email, email_verified = _extract_social_email(config["provider"], userinfo)
    if not email or not email_verified:
        messages.error(request, "Your social account did not provide a verified email address we can use.")
        return redirect("login")

    try:
        user = _get_social_login_user(email)
    except ValidationError as exc:
        messages.error(request, exc.message)
        return redirect("login")

    if not user.is_active:
        return _handle_inactive_login_attempt(request, user)

    return _begin_login_otp_challenge(request, user)


def login_view(request):
    """
    Revised Login: Handles Trial Activation vs. Payment Requirements.
    """
    if request.user.is_authenticated:
        return redirect("login_redirect")

    if request.method == "POST":
        ip_addr = _client_ip_for_request(request)
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        auth_username = _username_for_login_identifier(username)
        
        # 1. Throttling Check
        cache_key = f"login_fail:{ip_addr}:{username}".lower()
        fail_count = cache.get(cache_key, 0)
        if fail_count >= 5:
            messages.error(request, "Too many failed attempts. Try again in 10 minutes.")
            return _render_login(request)

        # 2. Pre-Authentication Inactive Check
        # This is where your "Loop" lived. We now check TRIAL status.
        inactive_user = User.objects.filter(username=auth_username).first()
        if inactive_user and not inactive_user.is_active:
            return _handle_inactive_login_attempt(request, inactive_user)

        # 3. Authenticate User
        user = authenticate(request, username=auth_username, password=password)

        if user is None:
            cache.set(cache_key, fail_count + 1, timeout=300)
            messages.error(request, "Invalid username or password.")
            return _render_login(request)

        # 4. Success Path - Device/OTP Verification
        cache.delete(cache_key)
        if _superuser_otp_bypass_allowed(user):
            return _complete_superuser_otp_bypass(request, user)
        return _begin_login_otp_challenge(request, user)

    return _render_login(request)

from django.contrib.auth import logout
from django.shortcuts import redirect
from .models import CashShift  # Ensure this is imported

def logout_view(request):
    if request.user.is_authenticated:
        # Logging out never silently closes a drawer. The operator must submit
        # a counted reconciliation through the explicit close workflow.
        open_shift_count = CashShift.objects.filter(
            cashier=request.user,
            is_closed=False,
        ).count()
        _log_action(
            request.user,
            "logout",
            "User logged out with open shift retained for reconciliation."
            if open_shift_count
            else "User logged out.",
            {"open_shift_count": open_shift_count},
        )
        logout(request)

    return redirect("index")


@login_required
@require_POST
def session_heartbeat(request):
    return JsonResponse(
        {
            "ok": True,
            "seconds_remaining": int(getattr(settings, "QUICKSTOCK_IDLE_TIMEOUT_SECONDS", 900)),
        }
    )


@login_required
@require_POST
def expire_session(request):
    reason = (request.POST.get("reason") or "client_logout").strip().lower()
    reason_messages = {
        "idle_timeout": "Automatic logout triggered after inactivity timeout.",
        "fingerprint_mismatch": "Automatic logout triggered because the session looked unsafe.",
        "client_logout": "Automatic logout triggered by the session guard.",
    }
    _log_action(
        request.user,
        "logout",
        reason_messages.get(reason, reason_messages["client_logout"]),
        severity="warn",
        metadata={"reason": reason, "path": request.path},
    )
    logout(request)
    return JsonResponse({"ok": True, "reason": reason, "redirect_url": reverse("login")})

def login_otp_view(request):
    """
    Verifies email OTP after password step.
    Finalizes the login process and checks for new device fingerprints.
    """
    pending_user_id = request.session.get("otp_user_id")
    if not pending_user_id:
        return redirect("login")

    user = User.objects.filter(id=pending_user_id).first()
    if not user:
        return redirect("login")

    pending_fp = request.session.get("otp_pending_fp")
    if pending_fp != _device_fingerprint(request):
        _clear_login_otp_challenge(request, user)
        _log_action(
            user,
            "login_otp_device_mismatch",
            "OTP challenge was submitted from a different device or network.",
            severity="warn",
        )
        messages.error(request, "Your sign-in verification session changed. Please sign in again.")
        return redirect("login")

    if request.method == "POST":
        code_submitted = request.POST.get("otp_code", "").strip()
        cache_key = f"login_otp:{user.id}"
        expected_code = cache.get(cache_key)
        failure_key = _login_otp_failure_key(request, user)
        max_attempts = max(1, int(getattr(settings, "LOGIN_OTP_MAX_ATTEMPTS", 5)))
        rate_window = max(1, int(getattr(settings, "LOGIN_OTP_RATE_WINDOW", 900)))
        failure_count = int(cache.get(failure_key, 0) or 0)

        if failure_count >= max_attempts:
            _clear_login_otp_challenge(request, user)
            response = render(
                request,
                "inventory/login_otp.html",
                {"username": user.username},
                status=429,
            )
            response["Retry-After"] = str(rate_window)
            return response

        if not expected_code:
            messages.error(request, "Code expired. Please try logging in again.")
            _clear_login_otp_challenge(request, user)
            return redirect("login")

        if secrets.compare_digest(code_submitted, str(expected_code)):
            # 1. Clear the OTP from cache and session
            _clear_login_otp_challenge(request, user)
            cache.delete(failure_key)

            # 2. Check Device Fingerprint for Security Alert
            current_fp = _device_fingerprint(request)
            last_fp = cache.get(f"last_device_fp:{user.id}")
            
            if last_fp and last_fp != current_fp:
                _send_new_device_alert(
                    user, 
                    request.META.get("REMOTE_ADDR", "unknown"), 
                    request.META.get("HTTP_USER_AGENT", "unknown")
                )
            
            # Save current fingerprint for next time
            cache.set(f"last_device_fp:{user.id}", current_fp, timeout=60*60*24*30) # 30 days

            # 3. Finalize Login
            login(request, user)
            _prime_authenticated_session(request)
            login_metadata = {
                "ip_address": _client_ip_for_request(request),
                "user_agent": (request.META.get("HTTP_USER_AGENT", "") or "")[:255],
                "device_label": _summarize_user_agent(request.META.get("HTTP_USER_AGENT", "")),
                "location": getattr(getattr(user, "profile", None), "default_location_id", None),
            }
            _log_action(user, "login_success", "User completed OTP verification", metadata=login_metadata)

            _prune_auth_billing_messages(request)
            messages.success(request, f"Welcome back, {user.username}!")
            return redirect("login_redirect")
        else:
            if cache.add(failure_key, 1, timeout=rate_window):
                failure_count = 1
            else:
                try:
                    failure_count = cache.incr(failure_key)
                except (ValueError, NotImplementedError):
                    failure_count = int(cache.get(failure_key, 0) or 0) + 1
                    cache.set(failure_key, failure_count, timeout=rate_window)
            _log_action(user, "login_otp_fail", "Incorrect OTP entered", severity="warn")
            if failure_count >= max_attempts:
                _clear_login_otp_challenge(request, user)
                messages.error(request, "Too many incorrect codes. Please wait and sign in again.")
                response = render(
                    request,
                    "inventory/login_otp.html",
                    {"username": user.username},
                    status=429,
                )
                response["Retry-After"] = str(rate_window)
                return response
            messages.error(request, "Invalid verification code.")

    return render(request, "inventory/login_otp.html", {"username": user.username})

def signup_view(request):
    """
    Hybrid Signup:
    1. Public: Creates a new Admin (Trial/Pending).
    2. Admin-Logged-In: Creates a Staff Member (Linked to Admin/Active).
    """
    # Check if the person creating the account is an existing Admin
    is_admin_creating_staff = request.user.is_authenticated and hasattr(request.user, 'profile') and request.user.profile.role == 'admin'

    if request.user.is_authenticated and not is_admin_creating_staff:
        return redirect("dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        password1 = request.POST.get("password", "")
        password2 = request.POST.get("confirm_password", "")
        accepted_terms = request.POST.get("accept_terms") == "on"
        
        # New: Get role from form if Admin is creating, else default to 'admin'
        target_role = request.POST.get("role", "admin") if is_admin_creating_staff else "admin"

        # 1. Basic validation
        if not all([username, email, password1, password2]):
            messages.error(request, "All fields are required.")
            return render(request, "inventory/signup.html")

        try:
            email = _normalize_email_address(email)
        except ValidationError:
            messages.error(request, "Please enter a valid email address.")
            return render(request, "inventory/signup.html")

        if not accepted_terms and not is_admin_creating_staff:
            messages.error(request, "Terms must be accepted.")
            return render(request, "inventory/signup.html")

        if password1 != password2:
            messages.error(request, "Passwords do not match.")
            return render(request, "inventory/signup.html")

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username is already taken.")
            return render(request, "inventory/signup.html")

        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, "An account already uses that email address.")
            return render(request, "inventory/signup.html")

        require_email_verification = bool(
            getattr(settings, "QUICKSTOCK_REQUIRE_EMAIL_VERIFICATION", False)
        )

        # 2. Create User
        # Staff are active immediately. Public signups only wait for email when configured.
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password1,
            is_active=True if is_admin_creating_staff or not require_email_verification else False,
        )

        # 3. Create UserProfile
        profile, _ = UserProfile.objects.get_or_create(user=user)
        
        if is_admin_creating_staff:
            # --- ADMIN CREATING STAFF LOGIC ---
            admin_prof = request.user.profile
            profile.role = target_role if target_role in ['manager', 'cashier'] else 'cashier'
            profile.parent_admin = admin_prof
            profile.plan = admin_prof.plan  # Inherits Admin's PRO/TRIAL status
            profile.status = "active"
            profile.default_location = admin_prof.default_location
            profile.save()
            
            _log_action(request.user, "staff_creation", f"Admin created {profile.role}: {username}")
            messages.success(request, f"Staff account {username} created successfully.")
            return redirect("settings") # Redirect back to your team list
        
        else:
            # --- PUBLIC SIGNUP LOGIC (ADMIN) ---
            user.is_staff = True
            user.save(update_fields=["is_staff"])
            
            profile.role = "admin"
            profile.plan = "TRIAL"
            profile.plan_start = timezone.now()
            profile.plan_end = timezone.now() + timedelta(days=14)
            profile.default_location = _get_main_store(user)
            _get_unassigned_location(user)
            
            profile.terms_accepted = True
            profile.terms_accepted_at = timezone.now()
            profile.status = "pending" if require_email_verification else "active"
            profile.save()

            if require_email_verification:
                email_sent = _send_activation_email(request, user)
                if email_sent:
                    messages.success(request, "Account created! Verify your email to continue.")
                else:
                    messages.warning(
                        request,
                        "Account created, but the activation email could not be sent. Contact support to activate it.",
                    )
            else:
                messages.success(request, "Account created! You can now log in.")
            return redirect("login")

    return render(request, "inventory/signup.html")
# ---------------------------
# Account Activation
# ---------------------------from django.utils import timezone

def activate_account(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        user = None

    if user and token_generator.check_token(user, token):
        profile = UserProfile.for_user(user)

        # 1. Flip the switch to active
        user.is_active = True
        user.save(update_fields=["is_active"])

        # Account verified, trial now active
        profile.status = "active"
        profile.save()

        # 3. Log them in immediately
        from django.contrib.auth import login
        login(request, user)
        _prime_authenticated_session(request)

        _prune_auth_billing_messages(request)
        messages.success(request, f"Welcome to QuickStock JA! Trial active until {profile.plan_end.date()}.")
        return redirect("index")

    messages.error(request, "Activation link is invalid or expired.")
    return redirect("login")

# ---------------------------
# Role-Based Redirects
# ---------------------------
@login_required
def login_redirect(request):
    """
    Redirect user after login based on their role.
    """
    if request.user.is_superuser:
        return redirect("super_admin_dashboard")

    profile = getattr(request.user, "profile", None)
    role = getattr(profile, "role", "cashier")

    if profile:
        today = timezone.now().date()

        # Pending accounts must pay before access
        if profile.status == "pending":
            _prune_auth_billing_messages(request)
            messages.error(request, "Complete payment to activate your account.")
            return redirect("upgrade")

        # Trial expiry
        if profile.plan == "TRIAL" and profile.plan_end:
            trial_expiry = profile.plan_end.date()
            days_left = (trial_expiry - today).days
            if days_left <= 7 and days_left >= 0:
                _send_expiry_reminder(request.user, trial_expiry, "trial")
                _prune_auth_billing_messages(request)
                messages.warning(request, f"Your trial expires in {days_left} day(s). Please upgrade to continue.")
            if days_left < -7:
                profile.status = "suspended"
                profile.save(update_fields=["status"])
                _prune_auth_billing_messages(request)
                messages.error(request, "Your trial expired. Account suspended until you subscribe.")
                return redirect("upgrade")
            if days_left < 0:
                _prune_auth_billing_messages(request)
                messages.error(request, "Your trial has expired. Please subscribe to continue.")
                return redirect("upgrade")

        # Paid subscription expiry
        if profile.plan == "PRO" and profile.pro_expires:
            pro_expiry = profile.pro_expires
            days_left = (pro_expiry - today).days
            if days_left <= 7 and days_left >= 0:
                _send_expiry_reminder(request.user, pro_expiry, "subscription")
                _prune_auth_billing_messages(request)
                messages.warning(request, f"Your subscription expires in {days_left} day(s). Please renew to avoid suspension.")
            if days_left < -7:
                profile.status = "suspended"
                profile.save(update_fields=["status"])
                _prune_auth_billing_messages(request)
                messages.error(request, "Subscription overdue by more than 7 days. Account suspended until renewed.")
                return redirect("upgrade")
            if days_left < 0:
                _prune_auth_billing_messages(request)
                messages.error(request, "Subscription expired. Please renew to regain access.")
                return redirect("upgrade")

        # Suspended (from any reason) cannot proceed
        if profile.status == "suspended":
            _prune_auth_billing_messages(request)
            messages.error(request, "Account is suspended. Please renew your subscription.")
            return redirect("upgrade")

    if role in ["admin", "manager"]:
        return redirect("dashboard")

    default_location = getattr(profile, "default_location", None) if profile else None
    if role == "cashier" and not default_location:
        return redirect("cash_register")

    active_shift = None
    if default_location:
        active_shift = CashShift.objects.filter(
            cashier=request.user,
            location=default_location,
            is_closed=False,
        ).first()

    if active_shift:
        return redirect("cash_register")

    return redirect("open_shift")


@login_required
@role_required(["admin", "manager"], allow_superuser=True)
def admin_dashboard(request):
    """Legacy route: send admin users to the shared dashboard view."""
    if request.user.is_superuser:
        return redirect("super_admin_dashboard")
    return redirect("dashboard")


@login_required
def manager_dashboard(request):
    """Legacy route: send manager users to the shared dashboard view."""
    return redirect("dashboard")


@login_required
@role_required(["cashier"])
def cashier_dashboard(request):
    """Redirect cashier directly to POS interface."""
    return redirect("cash_register")

@login_required
def super_admin_dashboard(request):
    """
    SaaS oversight dashboard for the platform owner.

    Uses separate grouped queries to avoid a massive multi-table join.
    """
    if not request.user.is_superuser:
        messages.error(
            request,
            "Only the platform superuser can access this dashboard.",
        )
        return redirect("dashboard")

    today = timezone.localdate()
    expiring_soon_cutoff = today + timedelta(days=7)

    base_companies = _company_admin_queryset()

    company_summary = base_companies.aggregate(
        total_companies=Count("id"),
        active_companies=Count(
            "id",
            filter=Q(status="active"),
        ),
        pending_companies=Count(
            "id",
            filter=Q(status="pending"),
        ),
        suspended_companies=Count(
            "id",
            filter=Q(status="suspended"),
        ),
        trial_companies=Count(
            "id",
            filter=Q(plan="TRIAL"),
        ),
        pro_companies=Count(
            "id",
            filter=Q(plan="PRO"),
        ),
        free_companies=Count(
            "id",
            filter=Q(plan="FREE"),
        ),
        expiring_soon_companies=Count(
            "id",
            filter=Q(
                status="active",
                plan="PRO",
                pro_expires__isnull=False,
                pro_expires__gte=today,
                pro_expires__lte=expiring_soon_cutoff,
            ),
        ),
    )

    company_rows = list(
        base_companies.select_related("user")
    )

    profile_ids = [
        company.id
        for company in company_rows
    ]

    owner_ids = [
        company.user_id
        for company in company_rows
    ]

    def count_map(queryset, group_field):
        return {
            row[group_field]: row["total"]
            for row in (
                queryset
                .values(group_field)
                .annotate(total=Count("id"))
            )
        }

    staff_counts = count_map(
        UserProfile.objects.filter(
            parent_admin_id__in=profile_ids,
        ),
        "parent_admin_id",
    )

    location_counts = count_map(
        Location.objects.filter(
            owner_id__in=owner_ids,
        ),
        "owner_id",
    )

    item_counts = count_map(
        Item.objects.filter(
            owner_id__in=owner_ids,
        ),
        "owner_id",
    )

    customer_counts = count_map(
        Customer.objects.filter(
            owner_id__in=owner_ids,
        ),
        "owner_id",
    )

    supplier_counts = count_map(
        Supplier.objects.filter(
            owner_id__in=owner_ids,
        ),
        "owner_id",
    )

    sale_counts = count_map(
        Sale.objects.filter(
            owner_id__in=owner_ids,
        ),
        "owner_id",
    )

    invoice_counts = count_map(
        SalesInvoice.objects.filter(
            owner_id__in=owner_ids,
        ),
        "owner_id",
    )

    quotation_counts = count_map(
        SalesQuotation.objects.filter(
            owner_id__in=owner_ids,
        ),
        "owner_id",
    )

    last_activity_map = {
        row["user_id"]: row["last_activity"]
        for row in (
            AuditLog.objects
            .filter(user_id__in=owner_ids)
            .values("user_id")
            .annotate(
                last_activity=Max("created_at")
            )
        )
    }

    for company in company_rows:
        profile_id = company.id
        owner_id = company.user_id

        company.staff_count = staff_counts.get(
            profile_id,
            0,
        )
        company.location_count = location_counts.get(
            owner_id,
            0,
        )
        company.item_count = item_counts.get(
            owner_id,
            0,
        )
        company.customer_count = customer_counts.get(
            owner_id,
            0,
        )
        company.supplier_count = supplier_counts.get(
            owner_id,
            0,
        )
        company.sale_count = sale_counts.get(
            owner_id,
            0,
        )
        company.invoice_count = invoice_counts.get(
            owner_id,
            0,
        )
        company.quotation_count = quotation_counts.get(
            owner_id,
            0,
        )
        company.last_activity = last_activity_map.get(
            owner_id
        )

    company_rows.sort(
        key=lambda company: (
            company.last_activity is not None,
            company.last_activity.timestamp()
            if company.last_activity
            else 0,
        ),
        reverse=True,
    )

    total_staff = sum(
        company.staff_count
        for company in company_rows
    )
    total_locations = sum(
        company.location_count
        for company in company_rows
    )
    total_items = sum(
        company.item_count
        for company in company_rows
    )
    total_customers = sum(
        company.customer_count
        for company in company_rows
    )
    total_suppliers = sum(
        company.supplier_count
        for company in company_rows
    )
    total_sales = sum(
        company.sale_count
        for company in company_rows
    )
    total_invoices = sum(
        company.invoice_count
        for company in company_rows
    )
    total_quotations = sum(
        company.quotation_count
        for company in company_rows
    )

    attention_companies = [
        company
        for company in company_rows
        if (
            company.status in {"pending", "suspended"}
            or (
                company.status == "active"
                and company.plan == "PRO"
                and company.pro_expires
                and today
                <= company.pro_expires
                <= expiring_soon_cutoff
            )
        )
    ][:6]

    recent_company_rows = company_rows[:5]

    attention_count = (
        company_summary["pending_companies"]
        + company_summary["suspended_companies"]
        + company_summary["expiring_soon_companies"]
    )

    paginator = Paginator(company_rows, 20)
    company_page = paginator.get_page(
        request.GET.get("page")
    )

    context = {
        **company_summary,
        "total_staff": total_staff,
        "total_locations": total_locations,
        "total_items": total_items,
        "total_customers": total_customers,
        "total_suppliers": total_suppliers,
        "total_sales": total_sales,
        "total_invoices": total_invoices,
        "total_quotations": total_quotations,
        "attention_count": attention_count,
        "attention_companies": attention_companies,
        "recent_company_rows": recent_company_rows,
        "company_page": company_page,
    }

    return render(
        request,
        "inventory/super_admin_dashboard.html",
        context,
    )

# ---------------------------
# Dashboard & Profile
# ---------------------------
@login_required

@role_required(["admin", "manager", "cashier"], allow_superuser=True)
def dashboard_view(request):
    """
    Main dashboard view.
    - Filters sales/stock by location if selected
    - Calculates totals and low stock counts
    - Includes recent audit logs for admin
    """
    if request.user.is_superuser:
        return redirect("super_admin_dashboard")

    profile = getattr(request.user, "profile", None)
    is_admin = bool(profile and profile.role == "admin")

    # Cashier should go directly to POS
    if profile and profile.role == "cashier":
        return redirect("cash_register")

    # Fetch all locations
    locations = _location_queryset_for_user(request.user).order_by("name")
    location_id = request.GET.get("location", "all")
    low_stock_threshold = _get_low_stock_threshold()
    starter_item_limit = _get_starter_item_limit()
    total_item_count = _item_queryset_for_user(request.user).count()
    starter_items_remaining = max(0, starter_item_limit - total_item_count)
    owner_user = _inventory_owner_for_user(request.user)
    is_pro_user = _is_pro_user(owner_user)

    # Base querysets
    sales_qs = Sale.objects.all()
    if owner_user and not owner_user.is_superuser:
        sales_qs = sales_qs.filter(items__item__owner=owner_user).distinct()
    stock_qs = _stock_queryset_for_user(request.user)
    item_qs = _item_queryset_for_user(request.user)
    invoice_payments_qs = SalesInvoicePayment.objects.unreversed().select_related("invoice") if _sales_invoice_payments_available() else SalesInvoicePayment.objects.none()
    invoice_qs = SalesInvoice.objects.all()
    quotation_qs = SalesQuotation.objects.all()
    if owner_user and not owner_user.is_superuser and _sales_invoice_payments_available():
        invoice_payments_qs = invoice_payments_qs.filter(owner=owner_user)
    if owner_user and not owner_user.is_superuser:
        invoice_qs = invoice_qs.filter(owner=owner_user)
        quotation_qs = quotation_qs.filter(owner=owner_user)

    # Filter by specific location if selected
    active_location = None
    if location_id != "all":
        active_location = _location_queryset_for_user(request.user).filter(pk=location_id).first()
        if active_location:
            if profile and profile.role == "manager" and profile.default_location_id != active_location.id:
                profile.default_location = active_location
                profile.save(update_fields=["default_location"])
            sales_qs = sales_qs.filter(location=active_location)
            if _sales_invoice_payments_available():
                invoice_payments_qs = invoice_payments_qs.filter(location=active_location)
            invoice_qs = invoice_qs.filter(location=active_location)
            stock_qs = stock_qs.filter(location=active_location, item__is_deleted=False, item__status="active")
            low_stock_count = stock_qs.filter(quantity__lte=low_stock_threshold).count()
            total_inventory = stock_qs.aggregate(total=Sum("quantity"))["total"] or 0
            dashboard_inventory_capacity = _clean_inventory_capacity(active_location.inventory_capacity)
        else:
            location_id = "all"

    # Default totals for "All Locations"
    if location_id == "all":
        dashboard_inventory_capacity = (
            locations.aggregate(total=Sum("inventory_capacity"))["total"]
            or INVENTORY_CAPACITY
        )
        global_low_stock_items_qs = (
            item_qs.filter(is_deleted=False, status="active")
            .filter(
                Q(stock_at_locations__quantity__lte=low_stock_threshold)
                | Q(total_quantity__lte=low_stock_threshold)
            )
            .annotate(lowest_quantity=Coalesce(Min("stock_at_locations__quantity"), Value(0)))
            .distinct()
        )
        # Treat any low branch balance as a global stock-integrity warning.
        low_stock_count = global_low_stock_items_qs.count()
        total_inventory = stock_qs.filter(item__is_deleted=False, item__status="active").aggregate(total=Sum("quantity"))["total"] or 0

    pos_total_sales = sales_qs.aggregate(total=Sum("total_price"))["total"] or Decimal("0.00")
    invoice_total = (
        invoice_qs.exclude(status="void").aggregate(total=Sum("total_amount"))["total"]
        or Decimal("0.00")
    )
    quotation_total = Decimal("0.00")
    if location_id == "all":
        quotation_total = (
            quotation_qs.filter(status="draft").aggregate(total=Sum("total_amount"))["total"]
            or Decimal("0.00")
        )
    total_sales = pos_total_sales + invoice_total + quotation_total
    dashboard_inventory_capacity = _clean_inventory_capacity(dashboard_inventory_capacity)
    raw_inventory_percent = int((total_inventory / dashboard_inventory_capacity) * 100) if dashboard_inventory_capacity else 0
    inventory_percent = min(100, raw_inventory_percent)
    inventory_remaining = max(0, dashboard_inventory_capacity - int(total_inventory or 0))
    inventory_over_capacity = max(0, int(total_inventory or 0) - dashboard_inventory_capacity)
    total_inventory_display = f"{int(total_inventory or 0):,}"
    inventory_capacity_display = f"{dashboard_inventory_capacity:,}"
    if inventory_over_capacity > 0:
        inventory_capacity_status = f"{inventory_over_capacity:,} units over capacity"
    elif inventory_percent >= 100:
        inventory_capacity_status = "Capacity Reached"
    else:
        inventory_capacity_status = f"{inventory_remaining:,} units remaining"
    inventory_capacity_note = (
        "Usage is capped at 100% for display. Increase branch capacity if this reflects real stock."
        if inventory_over_capacity > 0
        else ""
    )

    today = timezone.localdate()
    today_start = timezone.make_aware(datetime.combine(today, datetime.min.time()))
    yesterday_start = today_start - timedelta(days=1)

    today_pos_revenue = (
        sales_qs.filter(timestamp__gte=today_start)
        .aggregate(total=Sum("total_price"))["total"]
        or Decimal("0.00")
    )
    yesterday_pos_revenue = (
        sales_qs.filter(timestamp__gte=yesterday_start, timestamp__lt=today_start)
        .aggregate(total=Sum("total_price"))["total"]
        or Decimal("0.00")
    )
    today_invoice_revenue = Decimal("0.00")
    yesterday_invoice_revenue = Decimal("0.00")
    if _sales_invoice_payments_available():
        today_invoice_revenue = (
            invoice_payments_qs.filter(payment_date__gte=today_start)
            .aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        yesterday_invoice_revenue = (
            invoice_payments_qs.filter(payment_date__gte=yesterday_start, payment_date__lt=today_start)
            .aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )

    today_revenue = today_pos_revenue + today_invoice_revenue
    yesterday_revenue = yesterday_pos_revenue + yesterday_invoice_revenue
    revenue_change_amount = today_revenue - yesterday_revenue
    if yesterday_revenue > 0:
        revenue_change_percent = ((revenue_change_amount / yesterday_revenue) * Decimal("100")).quantize(Decimal("0.1"))
        revenue_change_label = f"{revenue_change_percent:+}% vs yesterday"
    elif today_revenue > 0:
        revenue_change_percent = None
        revenue_change_label = "New revenue today"
    else:
        revenue_change_percent = None
        revenue_change_label = "No revenue today"
    if revenue_change_amount > 0:
        revenue_change_status = "up"
    elif revenue_change_amount < 0:
        revenue_change_status = "down"
    else:
        revenue_change_status = "flat"

    low_stock_action_url = f"{reverse('inventory')}?sort=quantity&dir=asc"

    # Low stock list
    if active_location:
        low_stock_items = (
            stock_qs.filter(item__is_deleted=False, item__status="active", quantity__lte=low_stock_threshold)
            .select_related("item")
            .order_by("quantity", "item__name")[:5]
        )
    else:
        # Normalize the structure so the template can access 'it.item' regardless of source.
        # This prevents the VariableDoesNotExist error when viewing All Locations.
        # We ensure archived items are excluded and use the lowest location balance
        # so global alerts reflect branch-specific shortages too.
        _items = global_low_stock_items_qs.order_by("lowest_quantity", "name")[:5]
        low_stock_items = [{"item": it, "quantity": getattr(it, "lowest_quantity", 0)} for it in _items]

    # Top sellers (last 30 days)
    start_30 = timezone.now() - timedelta(days=30)
    saleitems_qs = SaleItem.objects.filter(sale__timestamp__gte=start_30)
    if active_location:
        saleitems_qs = saleitems_qs.filter(sale__location=active_location)
    elif not request.user.is_superuser:
        saleitems_qs = saleitems_qs.filter(item__owner=_inventory_owner_for_user(request.user))

    top_sellers = (
        saleitems_qs.values("item__name")
        .annotate(total_qty=Sum("quantity"), total_revenue=Sum("total_price"))
        .order_by("-total_qty")[:5]
    )

    # Daily revenue (last 7 days)
    start_7 = timezone.now() - timedelta(days=7)
    daily_revenue_qs = sales_qs.filter(timestamp__gte=start_7)
    pos_daily_revenue = (
        daily_revenue_qs.annotate(day=TruncDay("timestamp"))
        .values("day")
        .annotate(total=Sum("total_price"))
        .order_by("day")
    )

    daily_revenue_map = {
        row["day"].date() if hasattr(row["day"], "date") else row["day"]: row["total"] or Decimal("0.00")
        for row in pos_daily_revenue
    }
    if _sales_invoice_payments_available():
        invoice_daily_revenue = (
            invoice_payments_qs.filter(payment_date__gte=start_7)
            .annotate(day=TruncDay("payment_date"))
            .values("day")
            .annotate(total=Sum("amount"))
            .order_by("day")
        )
        for row in invoice_daily_revenue:
            day_key = row["day"].date() if hasattr(row["day"], "date") else row["day"]
            daily_revenue_map[day_key] = daily_revenue_map.get(day_key, Decimal("0.00")) + (row["total"] or Decimal("0.00"))

    daily_revenue = [
        {"day": day, "total": total, "total_display": _format_money(total)}
        for day, total in sorted(daily_revenue_map.items(), key=lambda item: item[0])
    ]

    # Fetch recent logs for admin only
    recent_logs = []
    if profile and profile.role == "admin":
        recent_logs = _audit_log_queryset_for_user(request.user).order_by("-created_at")[:5]

    open_shift_qs = CashShift.objects.filter(is_closed=False)
    if owner_user and not owner_user.is_superuser:
        open_shift_qs = open_shift_qs.filter(owner=owner_user)
    if active_location:
        open_shift_qs = open_shift_qs.filter(location=active_location)
    open_shifts = list(open_shift_qs.select_related("cashier", "location").order_by("-opened_at")[:3])
    open_shift_count = open_shift_qs.count()
    open_shift_alert_params = {"date": today.strftime("%Y-%m-%d")}
    if open_shift_count == 1 and open_shifts:
        open_shift_alert_params["shift"] = open_shifts[0].id
    open_shift_alert_url = f"{reverse('cash_reconciliation')}?{urlencode(open_shift_alert_params)}"

    recent_login_activity = (
        AuditLog.objects.filter(user=request.user, action="login_success")
        .only("created_at", "message", "metadata")
        .order_by("-created_at")
        .first()
    )
    if recent_login_activity:
        login_meta = recent_login_activity.metadata or {}
        recent_login_activity.display_ip = login_meta.get("ip_address") or "Unknown IP"
        recent_login_activity.display_device = login_meta.get("device_label") or _summarize_user_agent(login_meta.get("user_agent", ""))
    elif request.user.last_login:
        recent_login_activity = SimpleNamespace(
            created_at=request.user.last_login,
            display_ip="Unavailable",
            display_device="Historic login record",
            message="Captured from Django's last login timestamp.",
        )

    # Resolve renewal date and display tier for UI consistency using hierarchy
    sub_holder = profile.get_effective_plan_owner() if profile else None
    renewal_date = None

    if sub_holder:
        # Check pro_expires first; fall back gracefully to plan_end if it's a Trial account
        if sub_holder.pro_expires:
            renewal_date = sub_holder.pro_expires
        elif sub_holder.plan_end:
            renewal_date = sub_holder.plan_end.date()

    if request.user.is_superuser:
        display_tier = "PRO (SYSTEM ADMIN)"
    elif sub_holder:
        display_tier = sub_holder.plan_badge_label
    else:
        display_tier = "TRIAL"

    accounting_integration = None
    accounting_sync_counts = {}
    xero_configured = bool(
        getattr(settings, "XERO_CLIENT_ID", "")
        and getattr(settings, "XERO_CLIENT_SECRET", "")
        and getattr(settings, "XERO_REDIRECT_URI", "")
    )
    if is_admin:
        accounting_integration = get_or_create_accounting_integration(
            owner_user,
            provider=AccountingIntegration.PROVIDER_XERO,
        )
        accounting_sync_counts = {
            row["status"]: row["total"]
            for row in AccountingSyncRecord.objects.filter(
                owner=owner_user,
                integration=accounting_integration,
            ).values("status").annotate(total=Count("id"))
        }

    context = {
        "total_sales": total_sales,
        "total_sales_display": _format_money(total_sales),
        "low_stock_count": low_stock_count,
        "low_stock_threshold": low_stock_threshold,
        "total_inventory": total_inventory,
        "total_inventory_display": total_inventory_display,
        "inventory_capacity": dashboard_inventory_capacity,
        "inventory_capacity_display": inventory_capacity_display,
        "inventory_percent": inventory_percent,
        "raw_inventory_percent": raw_inventory_percent,
        "inventory_remaining": inventory_remaining,
        "inventory_over_capacity": inventory_over_capacity,
        "inventory_capacity_status": inventory_capacity_status,
        "inventory_capacity_note": inventory_capacity_note,
        "profile": profile,
        "recent_logs": recent_logs,
        "open_shifts": open_shifts,
        "open_shift_count": open_shift_count,
        "open_shift_alert_url": open_shift_alert_url,
        "recent_login_activity": recent_login_activity,
        "today_revenue": today_revenue,
        "today_revenue_display": _format_money(today_revenue),
        "yesterday_revenue": yesterday_revenue,
        "yesterday_revenue_display": _format_money(yesterday_revenue),
        "revenue_change_amount": revenue_change_amount,
        "revenue_change_label": revenue_change_label,
        "revenue_change_status": revenue_change_status,
        "low_stock_action_url": low_stock_action_url,
        "locations": locations,
        "current_location_id": location_id,
        "starter_item_limit": starter_item_limit,
        "starter_items_used": total_item_count,
        "starter_items_remaining": starter_items_remaining,
        "is_pro_user": is_pro_user,
        "low_stock_items": low_stock_items,
        "top_sellers": top_sellers,
        "daily_revenue": daily_revenue,
        "renewal_date": renewal_date,
        "display_tier": display_tier,
        "accounting_integration": accounting_integration,
        "accounting_sync_counts": accounting_sync_counts,
        "xero_configured": xero_configured,
    }
    return render(request, "inventory/dashboard.html", context)


@login_required
def profile_view(request):
    """
    View and update user's profile settings.
    Currently supports setting default location.
    """
    profile = getattr(request.user, "profile", None)
    locations = _location_queryset_for_user(request.user).order_by("name")

    if request.method == "POST" and profile:
        location_id = request.POST.get("default_location") or ""
        if location_id:
            try:
                profile.default_location = _get_location_for_user(request.user, location_id)
            except Exception:
                profile.default_location = None
        else:
            profile.default_location = None

        profile.save(update_fields=["default_location"])
        messages.success(request, "Default location updated.")
        return redirect("profile")

    # Resolve renewal date and display tier for UI consistency using hierarchy
    sub_holder = profile.get_effective_plan_owner() if profile else None
    renewal_date = None

    if sub_holder:
        # Check pro_expires first; fall back gracefully to plan_end if it's a Trial account
        if sub_holder.pro_expires:
            renewal_date = sub_holder.pro_expires
        elif sub_holder.plan_end:
            renewal_date = sub_holder.plan_end.date()

    if request.user.is_superuser:
        display_tier = "PRO (SYSTEM ADMIN)"
    elif sub_holder:
        display_tier = sub_holder.plan_badge_label
    else:
        display_tier = "TRIAL"

    # Query recent login activity for Profile security section
    recent_login_activity = (
        AuditLog.objects.filter(user=request.user, action="login_success")
        .only("created_at", "message", "metadata")
        .order_by("-created_at")
        .first()
    )
    if recent_login_activity:
        login_meta = recent_login_activity.metadata or {}
        recent_login_activity.display_ip = login_meta.get("ip_address") or "Unknown IP"
        recent_login_activity.display_device = login_meta.get("device_label") or _summarize_user_agent(login_meta.get("user_agent", ""))
    elif request.user.last_login:
        recent_login_activity = SimpleNamespace(
            created_at=request.user.last_login,
            display_ip="Unavailable",
            display_device="Historic login record",
            message="Captured from Django's last login timestamp.",
        )

    return render(request, "inventory/profile.html", {
        "profile": profile, 
        "locations": locations,
        "renewal_date": renewal_date,
        "display_tier": display_tier,
        "recent_login_activity": recent_login_activity,
    })


@login_required
def change_password_view(request):
    """
    Change password for the logged-in user.
    """
    profile = UserProfile.for_user(request.user)
    owner_user = _inventory_owner_for_user(request.user)
    source = (request.GET.get("from") or request.POST.get("from") or "").strip().lower()
    if source not in {"settings", "profile"}:
        referer = request.META.get("HTTP_REFERER", "")
        source = "profile" if "/profile/" in referer else "settings"
    return_url_name = "profile" if source == "profile" else "settings"
    return_label = "Return to Profile" if source == "profile" else "Return to Settings"
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Password updated successfully.")
            return redirect(return_url_name)
    else:
        form = PasswordChangeForm(request.user)

    return render(
        request,
        "inventory/change_password.html",
        {
            "form": form,
            "profile": profile,
            "is_pro_user": _is_pro_user(owner_user),
            "return_source": source,
            "return_url_name": return_url_name,
            "return_label": return_label,
        },
    )


@login_required
def delete_account(request):
    """
    Allows the user to delete their account.
    """
    if request.method == "POST":
        user = request.user
        profile = getattr(user, "profile", None)

        with transaction.atomic():
            _discard_user_draft_records(user)
            history = _owner_financial_history_summary(user)
            if any(history.values()):
                reason = "Account purge blocked because financial history must be retained."
                archived_at = timezone.now()
                for model in (Supplier, Customer, Location):
                    model.objects.filter(owner=user).update(
                        is_archived=True,
                        archived_at=archived_at,
                        archived_by=user,
                        archive_reason=reason,
                    )
                if profile:
                    _archive_user_profile(profile, user, reason)
                _log_action(
                    user,
                    "account",
                    "Account purge blocked; financial history retained and account archived",
                    {"history": history, "archived": True},
                    severity="warn",
                    required=True,
                )
                payload = {
                    "ok": False,
                    "code": "financial_history_protected",
                    "message": reason,
                    "archived": True,
                    "history": history,
                }
                if "application/json" in (request.headers.get("Accept") or ""):
                    return JsonResponse(payload, status=409)
                messages.error(request, "Account deletion is unavailable while financial history exists. The account and records were archived.")
                return redirect("settings")

        try:
            with transaction.atomic():
                # Any account that owns locations must purge related sales first
                # (Sale.location is PROTECT and would block user deletion through Location.owner cascade).
                owns_locations = Location.objects.filter(owner=user).exists()
                is_org_owner = bool(profile and profile.role == "admin")

                # Admin deleting owner account: purge the full organization dataset.
                if is_org_owner:
                    owner_user = user
                    org_profile = getattr(owner_user, "profile", None)
                    staff_profiles = UserProfile.objects.filter(parent_admin=org_profile).select_related("user")
                    staff_user_ids = list(staff_profiles.values_list("user_id", flat=True))
                    org_user_ids = [owner_user.id] + staff_user_ids
                    org_location_ids = list(Location.objects.filter(owner=owner_user).values_list("id", flat=True))

                    # Clear newer transactional records that protect Items and Customers
                    if _sales_invoice_payments_available():
                        sales_payments = SalesInvoicePayment.objects.filter(
                            Q(invoice__owner=owner_user) | Q(received_by_id__in=org_user_ids)
                        )
                        if sales_payments.exists():
                            sales_payments.delete()
                    SalesInvoice.objects.filter(owner=owner_user).delete()
                    SalesQuotation.objects.filter(owner=owner_user).delete()

                    # Remove analytics-driving transactional history first.
                    Sale.objects.filter(
                        Q(owner=owner_user)
                        | Q(location_id__in=org_location_ids)
                        | Q(cashier_id__in=org_user_ids)
                    ).delete()
                    CashShift.objects.filter(
                        Q(location_id__in=org_location_ids)
                        | Q(cashier_id__in=org_user_ids)
                    ).delete()
                    PurchaseOrder.objects.filter(
                        Q(item__owner=owner_user)
                        | Q(supplier__owner=owner_user)
                    ).delete()
                    SupplierInvoice.objects.filter(
                        Q(supplier__owner=owner_user)
                        | Q(location_id__in=org_location_ids)
                    ).delete()
                    StockTransfer.objects.filter(
                        Q(from_location_id__in=org_location_ids)
                        | Q(to_location_id__in=org_location_ids)
                        | Q(item__owner=owner_user)
                    ).delete()
                    StockRecord.objects.filter(
                        Q(location_id__in=org_location_ids)
                        | Q(item__owner=owner_user)
                    ).delete()
                    Item.objects.filter(owner=owner_user).delete()
                    Supplier.objects.filter(owner=owner_user).delete()
                    Customer.objects.filter(owner=owner_user).delete()
                    Payment.objects.filter(user_id__in=org_user_ids).delete()
                    AuditLog.objects.filter(user_id__in=org_user_ids).delete()
                    UserProfile.objects.filter(user_id__in=org_user_ids).delete()
                    Location.objects.filter(owner=owner_user).delete()
                    User.objects.filter(id__in=staff_user_ids).delete()
                elif owns_locations:
                    # Non-admin data owner safety: still clear dependent records.
                    owned_location_ids = list(Location.objects.filter(owner=user).values_list("id", flat=True))
                    Sale.objects.filter(Q(owner=user) | Q(location_id__in=owned_location_ids) | Q(cashier=user)).delete()
                    if _sales_invoice_payments_available():
                        sales_payments = SalesInvoicePayment.objects.filter(
                            Q(invoice__owner=user) | Q(received_by=user)
                        )
                        if sales_payments.exists():
                            sales_payments.delete()
                    SalesInvoice.objects.filter(owner=user).delete()
                    SalesQuotation.objects.filter(owner=user).delete()
                    CashShift.objects.filter(Q(location_id__in=owned_location_ids) | Q(cashier=user)).delete()
                    SupplierInvoice.objects.filter(location_id__in=owned_location_ids).delete()
                    StockTransfer.objects.filter(
                        Q(from_location_id__in=owned_location_ids) | Q(to_location_id__in=owned_location_ids)
                    ).delete()
                    StockRecord.objects.filter(location_id__in=owned_location_ids).delete()
                    Location.objects.filter(id__in=owned_location_ids).delete()
                    AuditLog.objects.filter(user=user).delete()
                    UserProfile.objects.filter(user=user).delete()
                    Payment.objects.filter(user=user).delete()

                # Finally remove the requesting user account.
                user.delete()
        except ProtectedError:
            messages.error(
                request,
                "Account deletion blocked by protected linked records. We need one more cleanup pass."
            )
            return redirect("settings")
        except Exception:
            messages.error(
                request,
                "Unable to delete account right now because related data is still linked. Please try again."
            )
            return redirect("settings")

        logout(request)
        messages.success(request, "Account and organization data deleted successfully.")
        return redirect("index")

    return render(request, "inventory/confirm_delete_account.html")
# ---------------------------
# Inventory Views
# ---------------------------

def _build_inventory_movement_context(effective_owner, active_location=None):
    if not effective_owner:
        return {
            "local_received_rows": [],
            "imported_received_rows": [],
            "transfer_in_rows": [],
            "transfer_out_rows": [],
            "transfer_location_name": "All locations",
            "delivered_from_uncollected_rows": [],
            "remaining_in_store_rows": [],
            "collected_rows": [],
            "pending_transfer_count": 0,
            "remaining_in_store_quantity": 0,
            "movement_timeline_rows": [],
        }

    recent_local_purchase_orders = (
        PurchaseOrder.objects.select_related("item", "supplier", "location")
        .filter(Q(location__owner=effective_owner) | Q(location__isnull=True))
        .filter(
            item__owner=effective_owner,
            supplier__owner=effective_owner,
            supplier__supplier_type=Supplier.TYPE_LOCAL,
        )
        .order_by("-order_date", "-id")[:8]
    )
    recent_international_purchase_orders = (
        PurchaseOrder.objects.select_related("item", "supplier", "location")
        .filter(Q(location__owner=effective_owner) | Q(location__isnull=True))
        .filter(
            item__owner=effective_owner,
            supplier__owner=effective_owner,
            supplier__supplier_type=Supplier.TYPE_INTERNATIONAL,
        )
        .order_by("-order_date", "-id")[:8]
    )
    completed_transfers = (
        StockTransfer.objects.select_related("item", "from_location", "to_location")
        .filter(
            item__owner=effective_owner,
            from_location__owner=effective_owner,
            to_location__owner=effective_owner,
            status="COMPLETED",
        )
        .order_by("-timestamp", "-id")
    )
    if active_location:
        recent_transfer_ins = completed_transfers.filter(to_location=active_location)[:8]
        recent_transfer_outs = completed_transfers.filter(from_location=active_location)[:8]
    else:
        recent_transfer_ins = completed_transfers[:8]
        recent_transfer_outs = completed_transfers[:8]
    fulfillment_invoices = (
        SalesInvoice.objects.select_related("customer", "location")
        .prefetch_related("items__item")
        .filter(
            owner=effective_owner,
            status="paid",
            collection_status__in=[
                "awaiting_collection",
                "collected_immediately",
                "collected_after_hold",
            ],
        )
    )
    if active_location:
        fulfillment_invoices = fulfillment_invoices.filter(location=active_location)

    local_received_rows = [
        {
            "item_name": po.item.name,
            "quantity": po.quantity_received,
            "location_name": po.location.name if po.location else "Unassigned",
            "reference": f"PO-{po.id}",
            "status": po.supplier.name if po.supplier else "Received",
            "_timestamp": po.order_date,
            "_sort_id": po.id,
            "_timeline_type": "Receiving",
        }
        for po in recent_local_purchase_orders
    ]
    imported_received_rows = [
        {
            "item_name": po.item.name,
            "quantity": po.quantity_received,
            "location_name": po.location.name if po.location else "Unassigned",
            "reference": f"PO-{po.id}",
            "status": po.supplier.name if po.supplier else "Received",
            "_timestamp": po.order_date,
            "_sort_id": po.id,
            "_timeline_type": "Import",
        }
        for po in recent_international_purchase_orders
    ]
    transfer_in_rows = [
        {
            "item_name": transfer.item.name,
            "quantity": transfer.quantity,
            "location_name": transfer.to_location.name,
            "reference": f"From {transfer.from_location.name}",
            "status": "Transferred",
            "_timestamp": transfer.timestamp,
            "_sort_id": transfer.id,
            "_timeline_type": "Transfer in",
        }
        for transfer in recent_transfer_ins
    ]

    transfer_out_events = [
        {
            "item_name": transfer.item.name,
            "quantity": transfer.quantity,
            "location_name": transfer.from_location.name,
            "reference": f"To {transfer.to_location.name}",
            "status": "Transferred",
            "_timestamp": transfer.timestamp,
            "_sort_id": transfer.id,
            "_timeline_type": "Transfer out",
        }
        for transfer in recent_transfer_outs
    ]

    recent_sale_lines = (
        SaleItem.objects.select_related("item", "sale", "sale__location")
        .filter(
            item__owner=effective_owner,
            sale__owner=effective_owner,
            sale__location__owner=effective_owner,
        )
        .exclude(sale__receipt_status__in=["draft", "voided"])
    )
    if active_location:
        recent_sale_lines = recent_sale_lines.filter(sale__location=active_location)
    recent_sale_lines = recent_sale_lines.order_by("-sale__timestamp", "-id")[:8]
    immediate_pos_collection_events = []
    for line in recent_sale_lines:
        sale_location = getattr(line.sale, "location", None)
        receipt_reference = (
            f"Receipt #{line.sale.receipt_no}"
            if line.sale.receipt_no
            else f"Receipt #{line.sale_id}"
        )
        transfer_out_events.append(
            {
                "item_name": line.item.name,
                "quantity": line.quantity,
                "location_name": sale_location.name if sale_location else "Unassigned",
                "reference": receipt_reference,
                "status": "Customer purchase",
                "_timestamp": line.sale.timestamp,
                "_sort_id": line.id,
                "_timeline_type": "Customer sale",
            }
        )
        immediate_pos_collection_events.append(
            {
                "item_name": line.item.name,
                "quantity": line.quantity,
                "location_name": sale_location.name if sale_location else "Unassigned",
                "reference": receipt_reference,
                "status": "Picked up at purchase",
                "_timestamp": line.sale.timestamp,
                "_sort_id": line.id,
                "_timeline_type": "Collected",
            }
        )

    recent_pos_invoice_lines = (
        SalesInvoiceItem.objects.select_related("item", "invoice", "invoice__location")
        .filter(
            item__owner=effective_owner,
            invoice__owner=effective_owner,
            invoice__location__owner=effective_owner,
            invoice__notes__startswith=DOCUMENT_META_PREFIX,
            invoice__notes__contains='"source":"cash_register"',
        )
        .exclude(invoice__status="void")
    )
    if active_location:
        recent_pos_invoice_lines = recent_pos_invoice_lines.filter(invoice__location=active_location)
    recent_pos_invoice_lines = recent_pos_invoice_lines.order_by("-invoice__issued_at", "-id")[:16]
    for line in recent_pos_invoice_lines:
        _public_notes, metadata = _split_document_notes(line.invoice.notes)
        if metadata.get("source") != "cash_register":
            continue
        invoice_location = getattr(line.invoice, "location", None)
        transfer_out_events.append(
            {
                "item_name": line.item_name or line.item.name,
                "quantity": line.quantity,
                "location_name": invoice_location.name if invoice_location else "Unassigned",
                "reference": line.invoice.invoice_no or f"Invoice #{line.invoice_id}",
                "status": "Customer purchase",
                "_timestamp": line.invoice.issued_at,
                "_sort_id": line.id,
                "_timeline_type": "Customer sale",
            }
        )

    transfer_out_events.sort(
        key=lambda row: (row["_timestamp"], row["_sort_id"]),
        reverse=True,
    )
    transfer_out_rows = []
    for event in transfer_out_events[:8]:
        transfer_out_rows.append(
            {key: value for key, value in event.items() if not key.startswith("_")}
        )

    def invoice_fulfillment_rows(collection_status, status_label):
        rows = []
        invoices = fulfillment_invoices.filter(collection_status=collection_status).order_by(
            "-collection_status_changed_at",
            "-id",
        )[:8]
        for invoice in invoices:
            invoice_reference = invoice.invoice_no or f"INV-{invoice.id}"
            if invoice.customer:
                invoice_reference = f"{invoice.customer.name} · {invoice_reference}"
            for line in invoice.items.all():
                rows.append(
                    {
                        "item_name": line.item_name or (line.item.name if line.item else "Item"),
                        "quantity": line.quantity,
                        "location_name": invoice.location.name if invoice.location else "Unassigned",
                        "reference": invoice_reference,
                        "status": status_label,
                        "_timestamp": (
                            invoice.collected_at
                            or invoice.collection_status_changed_at
                            or invoice.issued_at
                        ),
                        "_sort_id": line.id,
                        "_timeline_type": status_label,
                    }
                )
                if len(rows) >= 8:
                    return rows
        return rows

    delivered_from_uncollected_events = invoice_fulfillment_rows(
        "collected_after_hold",
        "Picked up after hold",
    )
    remaining_in_store_events = invoice_fulfillment_rows(
        "awaiting_collection",
        "Awaiting customer pickup",
    )
    immediate_invoice_collection_events = invoice_fulfillment_rows(
        "collected_immediately",
        "Picked up at purchase",
    )

    delivered_from_uncollected_rows = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in delivered_from_uncollected_events
    ]
    remaining_in_store_rows = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in remaining_in_store_events
    ]

    collected_events = immediate_invoice_collection_events + immediate_pos_collection_events
    collected_events.sort(
        key=lambda row: (row["_timestamp"], row["_sort_id"]),
        reverse=True,
    )
    collected_rows = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in collected_events[:8]
    ]
    pending_transfer_count = StockTransfer.objects.filter(
        item__owner=effective_owner,
        from_location__owner=effective_owner,
        to_location__owner=effective_owner,
        status="PENDING",
    ).count()
    remaining_in_store_items = SalesInvoiceItem.objects.filter(
        item__owner=effective_owner,
        invoice__owner=effective_owner,
        invoice__location__owner=effective_owner,
        invoice__status="paid",
        invoice__collection_status="awaiting_collection",
    )
    if active_location:
        remaining_in_store_items = remaining_in_store_items.filter(invoice__location=active_location)
    remaining_in_store_quantity = remaining_in_store_items.aggregate(total=Coalesce(Sum("quantity"), 0))["total"] or 0
    movement_events = (
        list(local_received_rows)
        + list(imported_received_rows)
        + list(transfer_in_rows)
        + list(transfer_out_events[:8])
        + list(delivered_from_uncollected_events)
        + list(remaining_in_store_events)
        + list(collected_events[:8])
    )
    undated_timeline_floor = datetime.min.replace(tzinfo=timezone.get_current_timezone())
    movement_events.sort(
        key=lambda row: (
            row.get("_timestamp") is not None,
            row.get("_timestamp") or undated_timeline_floor,
            row.get("_sort_id") or 0,
        ),
        reverse=True,
    )
    movement_timeline_rows = [
        {
            "timestamp": row.get("_timestamp"),
            "type": row.get("_timeline_type") or row.get("status") or "Movement",
            "label": row.get("item_name") or "Item",
            "reference": row.get("reference") or "Reference pending",
            "quantity": row.get("quantity") or 0,
            "location_name": row.get("location_name") or "Unassigned",
            "status": row.get("status") or "",
        }
        for row in movement_events[:10]
    ]

    return {
        "local_received_rows": [
            {key: value for key, value in row.items() if not key.startswith("_")}
            for row in local_received_rows
        ],
        "imported_received_rows": [
            {key: value for key, value in row.items() if not key.startswith("_")}
            for row in imported_received_rows
        ],
        "transfer_in_rows": [
            {key: value for key, value in row.items() if not key.startswith("_")}
            for row in transfer_in_rows
        ],
        "transfer_out_rows": transfer_out_rows,
        "transfer_location_name": active_location.name if active_location else "All locations",
        "delivered_from_uncollected_rows": delivered_from_uncollected_rows,
        "remaining_in_store_rows": remaining_in_store_rows,
        "collected_rows": collected_rows,
        "pending_transfer_count": pending_transfer_count,
        "remaining_in_store_quantity": remaining_in_store_quantity,
        "movement_timeline_rows": movement_timeline_rows,
    }

@login_required
@role_required(["admin", "manager"])
def inventory_view(request):
    """
    List all inventory items with search, sorting, and pagination.
    Supports filtering by name, SKU, category, or brand.
    """
    effective_owner = _inventory_owner_for_user(request.user)
    if effective_owner:
        _seed_inventory_from_shared_json(effective_owner)

    locations = _location_queryset_for_user(request.user).order_by("name")
    items = _item_queryset_for_user(request.user).select_related("category", "brand") \
                        .prefetch_related("stock_at_locations__location") \
                        .all()
    location_param = request.GET.get("location", "").strip()
    active_location = None
    if location_param.isdigit():
        active_location = locations.filter(pk=location_param).first()
        if active_location:
            items = items.filter(stock_at_locations__location=active_location).distinct()

    # --- Search ---
    query = request.GET.get("q", "").strip()
    if query:
        items = items.filter(
            Q(name__icontains=query) |
            Q(sku__icontains=query) |
            Q(category__name__icontains=query) |
            Q(brand__name__icontains=query)
        )

    # --- Sorting ---
    sort = request.GET.get("sort", "name")
    direction = request.GET.get("dir", "asc")
    sort_map = {
        "name": "name",
        "quantity": "total_quantity",
        "price": "price",
        "category": "category__name",
        "brand": "brand__name",
    }
    sort_field = sort_map.get(sort, "name")
    if direction == "desc":
        sort_field = f"-{sort_field}"
    items = items.order_by(sort_field)

    # --- Pagination ---
    page_size = request.GET.get("page_size", "25")
    try:
        page_size = int(page_size)
    except ValueError:
        page_size = 25
    if page_size not in {10, 25, 50, 100}:
        page_size = 25

    paginator = Paginator(items, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))

    starter_item_limit = _get_starter_item_limit()
    total_item_count = _item_queryset_for_user(request.user).count()
    starter_items_remaining = max(0, starter_item_limit - total_item_count)
    is_pro_user = _is_pro_user(_inventory_owner_for_user(request.user))

    # Build location aggregated totals per item
    for item in page_obj:
        totals = {}
        for record in item.stock_at_locations.all():
            if active_location and record.location_id != active_location.id:
                continue
            location_name = record.location.name if record.location else "Unknown"
            totals[location_name] = totals.get(location_name, 0) + record.quantity
        row_quantity = (
            totals.get(active_location.name, 0)
            if active_location
            else item.total_quantity or 0
        )
        item.location_totals = totals
        item.location_totals_display = {
            location_name: f"{int(qty or 0):,}"
            for location_name, qty in totals.items()
        }
        item.row_quantity = int(row_quantity or 0)
        item.quantity_display = f"{item.row_quantity:,}"
        item.price_display = _format_money(item.price)

    context = {
        "items": page_obj,
        "page_obj": page_obj,
        "sort": sort,
        "dir": direction,
        "page_size": page_size,
        "q": query,
        "starter_item_limit": starter_item_limit,
        "starter_items_used": total_item_count,
        "starter_items_remaining": starter_items_remaining,
        "is_pro_user": is_pro_user,
        "active_location": active_location,
        "current_location_id": active_location.id if active_location else "",
        "locations": locations,
    }

    return render(request, "inventory/inventory.html", context)


@login_required
@role_required(["admin", "manager"])
def inventory_overview_view(request):
    effective_owner = _inventory_owner_for_user(request.user)
    if effective_owner:
        _seed_inventory_from_shared_json(effective_owner)

    def normalize_category_name(value):
        cleaned = re.sub(r"\s+", " ", (value or "General").strip()) or "General"
        lower = cleaned.lower()
        if len(lower) > 3 and lower.endswith("s"):
            lower = lower[:-1]
        return lower

    def display_category_name(value):
        normalized = normalize_category_name(value)
        return normalized.replace("-", " ").title()

    def compact_location_label(value):
        cleaned = re.sub(r"\s+", " ", (value or "Unassigned").strip()) or "Unassigned"
        if "," in cleaned:
            cleaned = cleaned.split(",", 1)[0]
        return cleaned.title()

    overview_items = (
        _item_queryset_for_user(request.user)
        .select_related("category", "brand")
        .prefetch_related("stock_at_locations__location")
        .order_by("category__name", "name")
    )
    total_items = overview_items.count()
    total_locations = Location.objects.filter(owner=effective_owner).count() if effective_owner else 0
    grouped_inventory_map = {}
    total_inventory_units = 0
    empty_catalogue_item_count = 0
    profile = (
        UserProfile.objects.select_related("default_location")
        .filter(user=request.user)
        .first()
    )
    active_location = getattr(profile, "default_location", None)
    if active_location and active_location.owner_id != getattr(effective_owner, "id", None):
        active_location = None

    for item in overview_items:
        raw_category_name = (item.category.name if item.category else "General").strip() or "General"
        category_key = normalize_category_name(raw_category_name)
        current_category = grouped_inventory_map.get(category_key)
        if current_category is None:
            current_category = {
                "category": display_category_name(raw_category_name),
                "category_aliases": set(),
                "items": [],
                "item_count": 0,
                "total_quantity": 0,
            }
            grouped_inventory_map[category_key] = current_category
        current_category["category_aliases"].add(raw_category_name)

        location_parts = []
        total_quantity = 0
        for record in item.stock_at_locations.all():
            location_name = record.location.name if record.location else "Unassigned"
            quantity = int(record.quantity or 0)
            total_quantity += quantity
            location_parts.append(
                {
                    "name": location_name,
                    "label": compact_location_label(location_name),
                    "quantity": quantity,
                    "quantity_display": f"{quantity:,}",
                }
            )

        current_category["items"].append(
            {
                "name": item.name,
                "brand": item.brand.name if item.brand else "",
                "sku": item.sku or "PENDING",
                "quantity": total_quantity,
                "quantity_display": f"{total_quantity:,}",
                "location_badges": location_parts,
            }
        )
        if total_quantity <= 0:
            empty_catalogue_item_count += 1
        current_category["item_count"] += 1
        current_category["total_quantity"] += total_quantity
        total_inventory_units += total_quantity

    grouped_inventory = sorted(grouped_inventory_map.values(), key=lambda group: group["category"])
    for group in grouped_inventory:
        aliases = sorted(group["category_aliases"], key=str.lower)
        plural_alias = next((alias for alias in aliases if alias.strip().lower().endswith("s")), None)
        if plural_alias:
            group["category"] = re.sub(r"\s+", " ", plural_alias.strip()).title()
        group["category_aliases"] = aliases
        group["has_merged_aliases"] = len({alias.lower() for alias in aliases}) > 1
        group["total_quantity_display"] = f"{int(group['total_quantity'] or 0):,}"

    movement_context = _build_inventory_movement_context(effective_owner, active_location=active_location)
    remaining_in_store_quantity = int(movement_context.get("remaining_in_store_quantity") or 0)
    attention_items = []
    if remaining_in_store_quantity > 0:
        attention_items.append(
            {
                "label": "Awaiting pickup",
                "value": remaining_in_store_quantity,
                "detail": "paid customer unit(s) remaining in store",
                "tone": "warning",
            }
        )
    if empty_catalogue_item_count > 0:
        attention_items.append(
            {
                "label": "Depleted items",
                "value": empty_catalogue_item_count,
                "detail": "catalogue item(s) with no units on hand",
                "tone": "danger",
            }
        )
    if movement_context.get("pending_transfer_count", 0) > 0:
        attention_items.append(
            {
                "label": "Pending transfers",
                "value": movement_context["pending_transfer_count"],
                "detail": "transfer request(s) awaiting confirmation",
                "tone": "info",
            }
        )

    context = {
        "total_items": total_items,
        "total_locations": total_locations,
        "total_categories": len(grouped_inventory),
        "total_inventory_units": total_inventory_units,
        "empty_catalogue_item_count": empty_catalogue_item_count,
        "grouped_inventory": grouped_inventory,
        "attention_items": attention_items,
        **movement_context,
    }
    return render(request, "inventory/inventory_overview.html", context)


@login_required
@role_required(["admin", "manager"])
def transfer_stock_view(request):
    owner = _inventory_owner_for_user(request.user)
    if not owner:
        messages.error(request, "Unable to resolve inventory owner.")
        return redirect("dashboard")

    # Keep default locations normalized so duplicate legacy rows don't surface in UI.
    _dedupe_default_locations(owner)

    items = _item_queryset_for_user(request.user).order_by("name")
    locations = Location.objects.filter(owner=owner).order_by("name")
    preferred_location_param = request.GET.get("location", "").strip()
    preferred_source_location = (
        locations.filter(pk=preferred_location_param).first()
        if preferred_location_param.isdigit()
        else None
    )

    # --- GET: PREPARE DATA FOR DROPDOWNS ---
    location_rows = [{"id": loc.id, "name": loc.name} for loc in locations]
    stock_records = StockRecord.objects.filter(item__owner=owner, item__is_deleted=False).select_related('location', 'item')
    stock_map = {
        str(item.id): {
            "id": item.id,
            "name": item.name,
            "locations": {
                str(loc["id"]): {
                    "id": loc["id"],
                    "name": loc["name"],
                    "qty": 0,
                }
                for loc in location_rows
            },
        }
        for item in items
    }
    for rec in stock_records:
        if not rec.location_id:
            continue
        item_id = str(rec.item.id)
        if item_id not in stock_map:
            stock_map[item_id] = {
                "id": rec.item.id,
                "name": rec.item.name,
                "locations": {},
            }

        loc_id = str(rec.location_id)
        if loc_id not in stock_map[item_id]["locations"]:
            stock_map[item_id]["locations"][loc_id] = {
                "id": rec.location_id,
                "name": rec.location.name,
                "qty": 0,
            }
        stock_map[item_id]["locations"][loc_id]["qty"] += rec.quantity

    formatted_stock = {
        item_id: {
            "id": data["id"],
            "name": data["name"],
            "locations": list(data["locations"].values()),
        }
        for item_id, data in stock_map.items()
    }
    transfer_confirmation = request.session.pop("transfer_stock_confirmation", None)

    # --- POST: EXECUTE TRANSFER ---
    if request.method == 'POST':
        item_id = request.POST.get('item_id')
        from_location_value = request.POST.get('from_location')
        to_location_id = request.POST.get('to_location')
        try:
            qty_to_move = int(request.POST.get("quantity", 0))
        except (TypeError, ValueError):
            qty_to_move = 0

        try:
            with transaction.atomic():
                target_item = Item.objects.get(id=item_id, owner=owner, is_deleted=False)

                if qty_to_move <= 0:
                    raise ValueError("Quantity must be at least 1.")

                if not from_location_value:
                    raise ValueError("Select a source location.")

                if not to_location_id:
                    raise ValueError("Select a destination location.")

                dest_loc = Location.objects.get(id=to_location_id, owner=owner)
                source_filter = {"owner": owner}
                if str(from_location_value).isdigit():
                    source_filter["id"] = from_location_value
                else:
                    source_filter["name"] = from_location_value
                source_loc = Location.objects.get(**source_filter)
                if source_loc.id == dest_loc.id:
                    raise ValueError("Source and destination cannot be the same.")

                # Source is always a real, tenant-owned location row (including 'Unassigned').
                sources = StockRecord.objects.select_for_update().filter(
                    item=target_item,
                    location=source_loc,
                )

                total_avail = sum(s.quantity for s in sources)
                if total_avail < qty_to_move:
                    raise ValueError(f"Insufficient stock in {source_loc.name}")

                # Deduct from source(s)
                rem = qty_to_move
                for s in sources:
                    if rem <= 0: break
                    take = min(s.quantity, rem)
                    s.quantity -= take
                    s.save()
                    rem -= take

                # Add to Destination
                dest, _ = StockRecord.objects.select_for_update().get_or_create(
                    item=target_item,
                    location=dest_loc,
                    defaults={"quantity": 0},
                )
                
                dest.quantity += qty_to_move
                dest.save(update_fields=["quantity"])

                transfer = StockTransfer.objects.create(
                    item=target_item,
                    from_location=source_loc,
                    to_location=dest_loc,
                    quantity=qty_to_move,
                    status="COMPLETED",
                    user=request.user,
                )

                source_balance = StockRecord.objects.filter(
                    item=target_item,
                    location=source_loc,
                ).aggregate(total=Coalesce(Sum("quantity"), 0))["total"]

                request.session["transfer_stock_confirmation"] = {
                    "reference": f"TRF-{transfer.id:05d}",
                    "item_name": target_item.name,
                    "quantity": qty_to_move,
                    "from_location": source_loc.name,
                    "to_location": dest_loc.name,
                    "source_balance": int(source_balance or 0),
                    "destination_balance": int(dest.quantity or 0),
                }
                messages.success(request, f"Relocated {qty_to_move} units successfully.")
                return redirect('transfer_stock')

        except Exception as e:
            messages.error(request, f"Transfer Failed: {str(e)}")

    # --- RENDER: FIXES THE VALUERROR ---
    return render(request, 'inventory/transfer_stock.html', {
        'items': items,
        'locations': locations,
        'stock_json': formatted_stock,
        'transfer_confirmation': transfer_confirmation,
        'preferred_source_location_id': preferred_source_location.id if preferred_source_location else "",
    })
   


@login_required
@role_required(["admin", "manager", "cashier"])
def customer_list(request):
    query = request.GET.get('q', '')
    customers = _customer_queryset_for_user(request.user).order_by("name", "id")
    
    if query:
        customers = customers.filter(
            models.Q(name__icontains=query) | 
            models.Q(phone__icontains=query) |
            models.Q(email__icontains=query) |
            models.Q(physical_address__icontains=query) |
            models.Q(business_address__icontains=query)
        )

    try:
        page_size = int(request.GET.get("page_size", 25))
    except (TypeError, ValueError):
        page_size = 25
    if page_size not in {10, 25, 50, 100}:
        page_size = 25

    paginator = Paginator(customers, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    page_query_params = request.GET.copy()
    page_query_params.pop("page", None)

    context = {
        'customers': page_obj,
        'page_obj': page_obj,
        'page_size': page_size,
        'page_query_params': page_query_params.urlencode(),
        'query': query,
    }
    return render(request, 'inventory/customer_list.html', context)


@login_required
@role_required(["admin", "manager", "cashier"])
def customer_detail(request, pk):
    owner_user = _inventory_owner_for_user(request.user)
    customer_qs = _customer_queryset_for_user(request.user).select_related()
    customer = get_object_or_404(customer_qs, pk=pk)
    note_entries = customer.note_entries.select_related("created_by").all()

    quotations = (
        SalesQuotation.objects.select_related("created_by")
        .filter(customer=customer)
        .order_by("-created_at")
    )
    invoices = (
        SalesInvoice.objects.select_related("created_by", "quotation", "location")
        .filter(customer=customer)
        .order_by("-created_at")
    )

    if not owner_user.is_superuser:
        quotations = quotations.filter(owner=owner_user)
        invoices = invoices.filter(owner=owner_user)

    customer_total_quotations = quotations.count()
    invoice_filter = (request.GET.get("invoice_filter") or request.GET.get("invoice_status") or "all").strip().lower()
    if invoice_filter not in {"all", "open", "paid"}:
        invoice_filter = "all"

    invoice_rows = list(invoices)
    open_invoices = [
        invoice
        for invoice in invoice_rows
        if invoice.status == "issued" and invoice.balance_due > Decimal("0.00")
    ]
    paid_invoices = [invoice for invoice in invoice_rows if invoice.status == "paid"]
    if invoice_filter == "open":
        visible_invoices = open_invoices
    elif invoice_filter == "paid":
        visible_invoices = paid_invoices
    else:
        visible_invoices = invoice_rows

    customer_total_invoices = len(invoice_rows)
    customer_total_quote_amount = quotations.aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
    customer_total_invoice_amount = sum(
        (invoice.effective_total_amount for invoice in invoice_rows),
        Decimal("0.00"),
    )
    customer_outstanding_balance = sum(
        (invoice.balance_due for invoice in open_invoices),
        Decimal("0.00"),
    )
    invoice_filter_counts = {
        "all": customer_total_invoices,
        "open": len(open_invoices),
        "paid": len(paid_invoices),
    }

    context = {
        "customer": customer,
        "quotations": quotations,
        "invoices": visible_invoices,
        "customer_total_quotations": customer_total_quotations,
        "customer_total_invoices": customer_total_invoices,
        "customer_total_quote_amount": customer_total_quote_amount,
        "customer_total_invoice_amount": customer_total_invoice_amount,
        "customer_outstanding_balance": customer_outstanding_balance,
        "customer_open_invoice_count": len(open_invoices),
        "invoice_filter": invoice_filter,
        "invoice_filter_counts": invoice_filter_counts,
        "note_entries": note_entries,
    }
    return render(request, "inventory/customer_detail.html", context)


def _search_accessible_user_ids(user):
    """
    Resolve user IDs whose customer records are visible to the current user.
    Superuser can access all (empty set signals unrestricted).
    """
    if not user or not user.is_authenticated:
        return set()
    if user.is_superuser:
        return set()

    profile = UserProfile.for_user(user)
    user_ids = {user.id}

    if profile.role == "admin":
        staff_ids = UserProfile.objects.filter(parent_admin=profile).values_list("user_id", flat=True)
        user_ids.update(staff_ids)
        return user_ids

    if profile.parent_admin:
        admin_profile = profile.parent_admin
        user_ids.add(admin_profile.user_id)
        sibling_ids = UserProfile.objects.filter(parent_admin=admin_profile).values_list("user_id", flat=True)
        user_ids.update(sibling_ids)

    return user_ids


GLOBAL_SEARCH_SCOPE_OPTIONS = [
    ("all", "All Business Objects"),
    ("products", "Products"),
    ("customers", "Customers"),
    ("sales", "Sales"),
    ("invoices", "Invoices"),
    ("suppliers", "Suppliers"),
    ("purchase_orders", "Purchase Orders"),
    ("movements", "Inventory Movements"),
    ("locations", "Locations"),
    ("transfers", "Transfers"),
    ("receipts", "Stock Receipts"),
    ("payments", "Payments"),
    ("administration", "Administration"),
]


def _global_search_scope_is(scope, *allowed):
    return scope == "all" or scope in allowed


def _global_search_item(title, *, subtitle="", meta="", detail="", status="", url="", action_label="Open"):
    return {
        "title": title,
        "subtitle": subtitle,
        "meta": meta,
        "detail": detail,
        "status": status,
        "url": url,
        "action_label": action_label,
    }


def _global_search_section(key, title, items, *, description="", module=""):
    return {
        "key": key,
        "title": title,
        "count": len(items),
        "description": description,
        "module": module,
        "items": items,
    }


def _global_search_shortcuts(q, is_admin):
    q_lower = (q or "").strip().lower()
    report_shortcuts = [
        {
            "title": "Advanced Reports",
            "keywords": "reports valuation movement sales trend analytics inventory valuation movement history",
            "url": reverse("advanced_reports"),
            "subtitle": "Performance and valuation reporting",
        },
        {
            "title": "Daily Summary",
            "keywords": "daily summary reconciliation closeout report operations",
            "url": reverse("daily_summary"),
            "subtitle": "Daily operations closeout",
        },
        {
            "title": "Deliveries & Collections",
            "keywords": "deliveries collections awaiting delivered report movement",
            "url": reverse("deliveries_collections"),
            "subtitle": "Field movement tracking",
        },
    ]
    admin_shortcuts = [
        {
            "title": "Settings",
            "keywords": "settings configuration preferences setup administration",
            "url": reverse("settings"),
            "subtitle": "Workspace settings and controls",
        },
        {
            "title": "Audit Logs",
            "keywords": "audit logs activity history administration security",
            "url": reverse("audit_logs"),
            "subtitle": "Operational audit trail",
        },
        {
            "title": "Staff & Roles",
            "keywords": "users staff roles permissions administration",
            "url": reverse("settings"),
            "subtitle": "Role and access controls",
        },
    ]

    def _matches(shortcut):
        haystack = f"{shortcut['title']} {shortcut['keywords']} {shortcut['subtitle']}".lower()
        return q_lower and q_lower in haystack

    report_items = [
        _global_search_item(
            shortcut["title"],
            subtitle=shortcut["subtitle"],
            meta="Module shortcut",
            detail="Open report",
            status="Ready",
            url=shortcut["url"],
        )
        for shortcut in report_shortcuts
        if _matches(shortcut)
    ]
    admin_items = [
        _global_search_item(
            shortcut["title"],
            subtitle=shortcut["subtitle"],
            meta="Administration",
            detail="Open workspace control",
            status="Ready",
            url=shortcut["url"],
        )
        for shortcut in admin_shortcuts
        if is_admin and _matches(shortcut)
    ]
    return report_items, admin_items


def _run_global_search(request, q, scope):
    owner_user = _inventory_owner_for_user(request.user)
    profile = getattr(request.user, "profile", None)
    can_view_finance = bool(
        request.user.is_superuser
        or (profile and profile.role in {"admin", "manager"})
    )
    can_view_admin_records = bool(
        request.user.is_superuser
        or (profile and profile.role == "admin")
    )

    sections = []
    section_counts = []
    total_count = 0

    if q:
        q_no_hash = q.lstrip("#").strip()
        accessible_user_ids = _search_accessible_user_ids(request.user)
        product_matches = []
        customer_matches = []
        sales_matches = []
        invoice_matches = []
        supplier_matches = []
        purchase_order_matches = []
        movement_matches = []
        location_matches = []
        transfer_matches = []
        receipt_matches = []
        payment_matches = []
        user_matches = []
        audit_matches = []

        if _global_search_scope_is(scope, "customers"):
            customer_filters = (
                Q(name__icontains=q)
                | Q(email__icontains=q)
                | Q(phone__icontains=q)
                | Q(physical_address__icontains=q)
                | Q(business_address__icontains=q)
            )

            customer_qs = Customer.objects.all()
            if not request.user.is_superuser:
                customer_qs = customer_qs.filter(owner_id__in=accessible_user_ids)

            customer_records = list(
                customer_qs.filter(customer_filters)
                .order_by("name")[:30]
            )
            customer_matches = [
                _global_search_item(
                    customer.name,
                    subtitle=customer.email or customer.phone or customer.physical_address or customer.business_address or "Customer record",
                    meta=f"Created {customer.created_at:%b %d, %Y}",
                    detail=f"Credit balance ${customer.credit_balance:,.2f}",
                    status="Customer",
                    url=reverse("customer_detail", args=[customer.id]),
                )
                for customer in customer_records
            ]

        if _global_search_scope_is(scope, "products", "movements", "receipts", "transfers") and owner_user:
            inventory_filters = (
                Q(name__icontains=q)
                | Q(sku__icontains=q)
                | Q(barcode__icontains=q)
                | Q(category__name__icontains=q)
                | Q(brand__name__icontains=q)
            )
            inventory_records = list(
                _item_queryset_for_user(request.user)
                .select_related("category", "brand", "supplier")
                .prefetch_related("stock_at_locations__location")
                .filter(inventory_filters)
                .order_by("name")[:30]
            )
            for item in inventory_records:
                location_labels = []
                for record in item.stock_at_locations.all():
                    location_labels.append(f"{record.location.name}: {record.quantity}")
                product_matches.append(
                    _global_search_item(
                        item.name,
                        subtitle=f"{item.category.name if item.category else 'Uncategorised'} | {item.brand.name if item.brand else 'No brand'}",
                        meta=item.sku or item.barcode or "No SKU",
                        detail=" | ".join(location_labels[:3]) if location_labels else "No location stock yet",
                        status=f"Qty {item.total_global_quantity}",
                        url=reverse("inventory_update", args=[item.id]),
                    )
                )

        if can_view_finance and _global_search_scope_is(scope, "sales") and owner_user:
            sales_filters = (
                Q(customer_name__icontains=q)
                | Q(customer_email__icontains=q)
                | Q(payment_reference__icontains=q)
                | Q(notes__icontains=q)
                | Q(receipt_status__icontains=q)
            )
            if q.isdigit():
                sales_filters |= Q(receipt_no=int(q)) | Q(id=int(q))
            sales_filters |= Q(items__item__name__icontains=q) | Q(items__item__sku__icontains=q)

            sale_records = list(
                Sale.objects.filter(owner=owner_user)
                .select_related("location", "cashier")
                .filter(sales_filters)
                .distinct()
                .order_by("-timestamp")[:30]
            )
            sales_matches = [
                _global_search_item(
                    f"Receipt {sale.receipt_no or sale.id}",
                    subtitle=sale.customer_name or "Walk-in / Unassigned",
                    meta=f"{timezone.localtime(sale.timestamp):%b %d, %Y %H:%M} | {sale.location.name if sale.location else 'No location'}",
                    detail=f"Tender {sale.get_tender_display()} | Total ${sale.total_price:,.2f}",
                    status=sale.get_receipt_status_display(),
                    url=reverse("view_receipt", args=[sale.id]),
                    action_label="Receipt",
                )
                for sale in sale_records
            ]

        if can_view_finance and _global_search_scope_is(scope, "invoices"):
            invoice_filters = (
                Q(invoice_no__icontains=q)
                | Q(customer__name__icontains=q)
                | Q(customer__email__icontains=q)
                | Q(customer__phone__icontains=q)
                | Q(status__icontains=q)
                | Q(location__name__icontains=q)
                | Q(quotation__quote_no__icontains=q)
            )
            if q_no_hash and q_no_hash != q:
                invoice_filters |= Q(invoice_no__icontains=f"#{q_no_hash}")
            if q.isdigit():
                invoice_filters |= Q(id=int(q))

            invoice_qs = SalesInvoice.objects.select_related("customer", "location", "quotation")
            if not request.user.is_superuser and owner_user:
                invoice_qs = invoice_qs.filter(owner=owner_user)

            invoice_records = list(
                invoice_qs.filter(invoice_filters)
                .order_by("-issued_at", "-id")[:30]
            )
            invoice_matches = [
                _global_search_item(
                    inv.invoice_no or f"SINV-{inv.id}",
                    subtitle=inv.customer.name if inv.customer else "Walk-in / Unassigned",
                    meta=f"{timezone.localtime(inv.issued_at):%b %d, %Y %H:%M} | {inv.location.name if inv.location else 'Unassigned'}",
                    detail=f"Total ${inv.effective_total_amount:,.2f}",
                    status=inv.get_status_display(),
                    url=reverse("sales_invoice_detail", args=[inv.id]),
                    action_label="Invoice",
                )
                for inv in invoice_records
            ]

        if _global_search_scope_is(scope, "suppliers") and owner_user:
            supplier_filters = (
                Q(name__icontains=q)
                | Q(contact_name__icontains=q)
                | Q(email__icontains=q)
                | Q(phone__icontains=q)
                | Q(address__icontains=q)
            )
            supplier_records = list(
                Supplier.objects.filter(owner=owner_user)
                .filter(supplier_filters)
                .order_by("name")[:30]
            )
            supplier_matches = [
                _global_search_item(
                    supplier.name,
                    subtitle=supplier.contact_name or "Supplier contact",
                    meta=supplier.email or supplier.phone or "No direct contact details",
                    detail=supplier.address[:120] if supplier.address else "Supplier record",
                    status="Supplier",
                    url=reverse("supplier_ledger", args=[supplier.id]),
                )
                for supplier in supplier_records
            ]

        if can_view_finance and _global_search_scope_is(scope, "purchase_orders", "receipts", "movements") and owner_user:
            purchase_order_filters = (
                Q(supplier__name__icontains=q)
                | Q(item__name__icontains=q)
                | Q(item__sku__icontains=q)
                | Q(location__name__icontains=q)
            )
            if q.isdigit():
                purchase_order_filters |= Q(id=int(q))

            purchase_order_records = list(
                PurchaseOrder.objects.select_related("supplier", "item", "location")
                .filter(supplier__owner=owner_user)
                .filter(purchase_order_filters)
                .order_by("-order_date")[:30]
            )
            purchase_order_matches = [
                _global_search_item(
                    f"PO-{po.id}",
                    subtitle=po.item.name,
                    meta=f"{po.supplier.name} | {po.location.name if po.location else 'Unassigned'}",
                    detail=f"{po.quantity_received} received | ${po.total_cost:,.2f}",
                    status="Purchase Order",
                    url=reverse("supplier_ledger", args=[po.supplier.id]),
                    action_label="Ledger",
                )
                for po in purchase_order_records
            ]
            receipt_matches = [
                _global_search_item(
                    po.item.name,
                    subtitle=po.location.name if po.location else "Unassigned",
                    meta=f"Receipt PO-{po.id}",
                    detail=f"{po.quantity_received} units at ${po.unit_cost:,.2f}",
                    status="Received",
                    url=reverse("supplier_ledger", args=[po.supplier.id]),
                    action_label="Receipt",
                )
                for po in purchase_order_records
            ]
            movement_matches.extend(
                [
                    _global_search_item(
                        po.item.name,
                        subtitle="Stock receipt",
                        meta=f"PO-{po.id} | {po.supplier.name}",
                        detail=f"{po.quantity_received} into {po.location.name if po.location else 'Unassigned'}",
                        status="Received",
                        url=reverse("supplier_ledger", args=[po.supplier.id]),
                        action_label="Receipt",
                    )
                    for po in purchase_order_records[:12]
                ]
            )

        if _global_search_scope_is(scope, "transfers", "movements") and owner_user:
            transfer_filters = (
                Q(item__name__icontains=q)
                | Q(item__sku__icontains=q)
                | Q(from_location__name__icontains=q)
                | Q(to_location__name__icontains=q)
                | Q(status__icontains=q)
            )
            if q.isdigit():
                transfer_filters |= Q(id=int(q))
            transfer_records = list(
                StockTransfer.objects.select_related("item", "from_location", "to_location")
                .filter(item__owner=owner_user)
                .filter(transfer_filters)
                .order_by("-timestamp")[:30]
            )
            transfer_matches = [
                _global_search_item(
                    transfer.item.name,
                    subtitle=f"{transfer.from_location.name} → {transfer.to_location.name}",
                    meta=f"Transfer #{transfer.id} | {timezone.localtime(transfer.timestamp):%b %d, %Y}",
                    detail=f"{transfer.quantity} units moved",
                    status=transfer.get_status_display(),
                    url=reverse("transfer_stock"),
                    action_label="Transfer",
                )
                for transfer in transfer_records
            ]
            movement_matches.extend(
                [
                    _global_search_item(
                        transfer.item.name,
                        subtitle="Stock transfer",
                        meta=f"{transfer.from_location.name} → {transfer.to_location.name}",
                        detail=f"{transfer.quantity} units",
                        status=transfer.get_status_display(),
                        url=reverse("transfer_stock"),
                        action_label="Transfer",
                    )
                    for transfer in transfer_records[:12]
                ]
            )

        if _global_search_scope_is(scope, "locations") and owner_user:
            location_filters = Q(name__icontains=q) | Q(address__icontains=q)
            location_records = list(
                _location_queryset_for_user(request.user).filter(location_filters).order_by("name")[:30]
            )
            location_matches = []
            for location in location_records:
                stock_total = (
                    StockRecord.objects.filter(location=location)
                    .aggregate(total=Sum("quantity"))
                    .get("total")
                    or 0
                )
                location_matches.append(
                    _global_search_item(
                        location.name,
                        subtitle=location.get_country_code_display(),
                        meta=location.address or "Location record",
                        detail=f"{stock_total} units currently assigned",
                        status="Warehouse" if location.is_warehouse else "Store",
                        url=reverse("locations"),
                        action_label="Locations",
                    )
                )

        if can_view_finance and _global_search_scope_is(scope, "payments", "movements") and owner_user and _sales_invoice_payments_available():
            payment_filters = (
                Q(reference__icontains=q)
                | Q(invoice__invoice_no__icontains=q)
                | Q(invoice__customer__name__icontains=q)
                | Q(invoice__customer__email__icontains=q)
                | Q(payment_method__icontains=q)
            )
            if q.isdigit():
                payment_filters |= Q(id=int(q))
            payment_records = list(
                SalesInvoicePayment.objects.select_related("invoice", "invoice__customer", "received_by")
                .filter(owner=owner_user)
                .filter(payment_filters)
                .order_by("-payment_date")[:30]
            )
            payment_matches = [
                _global_search_item(
                    payment.reference or f"PAY-{payment.id}",
                    subtitle=payment.invoice.customer.name if payment.invoice.customer else "Walk-in / Unassigned",
                    meta=f"{payment.invoice.invoice_no or payment.invoice.id} | {timezone.localtime(payment.payment_date):%b %d, %Y %H:%M}",
                    detail=f"${payment.amount:,.2f} via {payment.get_payment_method_display()}",
                    status="Payment",
                    url=reverse("sales_invoice_detail", args=[payment.invoice.id]),
                    action_label="Invoice",
                )
                for payment in payment_records
            ]
            movement_matches.extend(
                [
                    _global_search_item(
                        payment.invoice.customer.name if payment.invoice.customer else (payment.invoice.invoice_no or f"Invoice {payment.invoice.id}"),
                        subtitle="Payment collected",
                        meta=payment.reference or f"PAY-{payment.id}",
                        detail=f"${payment.amount:,.2f} via {payment.get_payment_method_display()}",
                        status="Collected",
                        url=reverse("sales_invoice_detail", args=[payment.invoice.id]),
                        action_label="Payment",
                    )
                    for payment in payment_records[:12]
                ]
            )

        if can_view_admin_records and _global_search_scope_is(scope, "administration"):
            user_filters = (
                Q(username__icontains=q)
                | Q(email__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(profile__role__icontains=q)
            )
            if request.user.is_superuser:
                user_qs = User.objects.select_related("profile")
            else:
                user_qs = User.objects.select_related("profile").filter(id__in=accessible_user_ids)
            user_records = list(user_qs.filter(user_filters).order_by("username")[:30])
            user_matches = [
                _global_search_item(
                    user.username,
                    subtitle=user.get_full_name() or user.email or "User account",
                    meta=f"Role: {getattr(user.profile, 'role', 'user').title()}",
                    detail="Administration user",
                    status="Active" if user.is_active else "Inactive",
                    url=reverse("settings"),
                    action_label="Settings",
                )
                for user in user_records
            ]

            audit_filters = Q(message__icontains=q) | Q(action__icontains=q) | Q(severity__icontains=q)
            audit_qs = AuditLog.objects.select_related("user")
            if not request.user.is_superuser:
                audit_qs = audit_qs.filter(user_id__in=accessible_user_ids)
            audit_records = list(audit_qs.filter(audit_filters).order_by("-created_at")[:30])
            audit_matches = [
                _global_search_item(
                    log.message,
                    subtitle=log.get_action_display(),
                    meta=f"{timezone.localtime(log.created_at):%b %d, %Y %H:%M}",
                    detail=log.user.username if log.user else "System",
                    status=log.get_severity_display(),
                    url=reverse("audit_logs"),
                    action_label="Audit",
                )
                for log in audit_records
            ]

        report_shortcuts, admin_shortcuts = _global_search_shortcuts(q, can_view_admin_records)

        ordered_sections = [
            _global_search_section("products", "Products", product_matches, description="Catalogue items, stock position, and SKU identity.", module="Inventory"),
            _global_search_section("customers", "Customers", customer_matches, description="People and companies buying from you.", module="Customers"),
            _global_search_section("sales", "Sales", sales_matches, description="Completed POS receipts and retail transactions.", module="Sales"),
            _global_search_section("invoices", "Invoices", invoice_matches, description="Sales invoices and outstanding billing records.", module="Finance"),
            _global_search_section("suppliers", "Suppliers", supplier_matches, description="Vendor records and purchasing counterparts.", module="Suppliers"),
            _global_search_section("purchase_orders", "Purchase Orders", purchase_order_matches, description="Inbound ordering and supplier receipt records.", module="Procurement"),
            _global_search_section("movements", "Inventory Movements", movement_matches[:24], description="Receipts, transfers, and collected movement events.", module="Inventory"),
            _global_search_section("locations", "Locations", location_matches, description="Branches, stores, and warehouse footprints.", module="Inventory"),
            _global_search_section("transfers", "Transfers", transfer_matches, description="Stock moving between locations.", module="Inventory"),
            _global_search_section("receipts", "Stock Receipts", receipt_matches, description="Physical goods received into stock.", module="Inventory"),
            _global_search_section("payments", "Payments", payment_matches, description="Money received against invoices.", module="Finance"),
            _global_search_section("users", "Users", user_matches, description="Operators, managers, and administrators.", module="Administration"),
            _global_search_section("audit", "Audit Logs", audit_matches, description="Operational trace and security history.", module="Administration"),
            _global_search_section("reports", "Reports", report_shortcuts, description="Analysis and operational reporting shortcuts.", module="Reports"),
            _global_search_section("settings", "Settings", admin_shortcuts, description="Configuration and workspace controls.", module="Administration"),
        ]
        sections = [section for section in ordered_sections if section["count"] > 0]
        section_counts = [
            {"key": section["key"], "title": section["title"], "count": section["count"], "module": section["module"]}
            for section in ordered_sections
        ]
        total_count = sum(section["count"] for section in sections)

    return {
        "q": q,
        "scope": scope,
        "scope_options": GLOBAL_SEARCH_SCOPE_OPTIONS,
        "can_view_finance": can_view_finance,
        "can_view_admin_records": can_view_admin_records,
        "sections": sections,
        "section_counts": section_counts,
        "total_count": total_count,
    }


@login_required
def global_search(request):
    q = (request.GET.get("q") or "").strip()
    scope = (request.GET.get("scope") or "all").strip().lower()
    if scope not in {value for value, _ in GLOBAL_SEARCH_SCOPE_OPTIONS}:
        scope = "all"
    context = _run_global_search(request, q, scope)
    return render(request, "inventory/global_search.html", context)


@login_required
def global_search_panel_api(request):
    q = (request.GET.get("q") or "").strip()
    scope = (request.GET.get("scope") or "all").strip().lower()
    if scope not in {value for value, _ in GLOBAL_SEARCH_SCOPE_OPTIONS}:
        scope = "all"

    context = _run_global_search(request, q, scope)

    return JsonResponse(
        {
            "q": context["q"],
            "scope": context["scope"],
            "can_view_finance": context["can_view_finance"],
            "can_view_admin_records": context["can_view_admin_records"],
            "section_counts": context["section_counts"],
            "sections": context["sections"],
            "total_count": context["total_count"],
        }
    )


@login_required
@role_required(["admin", "manager"])
def inventory_add_view(request):
    """
    Add a new inventory item and optionally assign stock to a location.
    """
    # Fetch dropdown data
    owner_user = _inventory_owner_for_user(request.user)
    locations = _location_queryset_for_user(request.user).order_by('name')
    suppliers = Supplier.objects.filter(owner=owner_user).order_by('name')
    categories = Category.objects.filter(owner=owner_user).order_by("name")
    starter_item_limit = _get_starter_item_limit()
    is_pro_user = _is_pro_user(owner_user)
    total_item_count = _item_queryset_for_user(request.user).count()
    if not is_pro_user and total_item_count >= starter_item_limit:
        messages.error(
            request,
            f"Starter plan limit reached ({starter_item_limit} items). Upgrade to PRO for unlimited items.",
        )
        return redirect("inventory")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        category_name = request.POST.get("category", "").strip()
        brand_name = request.POST.get("brand", "").strip()
        quantity_raw = request.POST.get("quantity", "0")
        price = request.POST.get("selling_price") or request.POST.get("price")
        cost_price = request.POST.get("cost_price")
        currency = request.POST.get("currency", "JMD")
        location_id = request.POST.get("location")

        # Basic validation
        if not name or not category_name or not brand_name or not price:
            messages.error(request, "Name, category, brand, and selling price are required.")
            return render(request, "inventory/add_item.html", {
                "locations": locations,
                "suppliers": suppliers,
                "categories": categories,
            })

        try:
            quantity = int(quantity_raw)
        except (TypeError, ValueError):
            quantity = 0
        if quantity < 0 or quantity > MAX_DB_QUANTITY:
            messages.error(
                request,
                f"Opening quantity must be between 0 and {MAX_DB_QUANTITY:,}.",
            )
            return render(request, "inventory/add_item.html", {
                "locations": locations,
                "suppliers": suppliers,
                "categories": categories,
                "starter_item_limit": starter_item_limit,
                "starter_items_used": total_item_count,
                "starter_items_remaining": max(0, starter_item_limit - total_item_count),
                "is_pro_user": is_pro_user,
            })
        cost_price = _safe_decimal(cost_price or 0)
        price = _safe_decimal(price)

        category, _ = Category.objects.get_or_create(owner=owner_user, name=category_name)
        brand, _ = Brand.objects.get_or_create(owner=owner_user, name=brand_name)
        supplier = None
        if request.POST.get("supplier"):
            supplier = Supplier.objects.filter(pk=request.POST.get("supplier"), owner=owner_user).first()
        barcode = request.POST.get("barcode", "").strip() or None
        sku = request.POST.get("sku", "").strip() or None

        # --- Create Item ---
        item = Item.objects.create(
            owner=owner_user,
            name=name,
            sku=sku,
            barcode=barcode,
            category=category,
            brand=brand,
            supplier=supplier,
            cost_price=cost_price,
            price=price,
        )

        # --- Create StockRecord if location is provided ---
        if location_id:
            location = _get_location_for_user(request.user, location_id)
            StockRecord.objects.create(
                item=item,
                location=location,
                quantity=quantity
            )
        else:
            StockRecord.objects.create(
                item=item,
                location=_get_main_store(owner_user),
                quantity=quantity,
            )

        messages.success(request, f"Asset '{name}' registered to network.")
        return redirect('inventory')

    return render(request, "inventory/add_item.html", {
        "locations": locations,
        "suppliers": suppliers,
        "categories": categories,
        "starter_item_limit": starter_item_limit,
        "starter_items_used": total_item_count,
        "starter_items_remaining": max(0, starter_item_limit - total_item_count),
        "is_pro_user": is_pro_user,
    })
# ---------------------------
# Locations & Inventory Update
# ---------------------------

@login_required
@pro_required
@role_required(["admin"])
def locations_page(request):
    """
    Display all company locations with simple statistics:
    - Total number of locations
    - Total stock value across all locations
    """
    owner_user = _inventory_owner_for_user(request.user)
    profile = UserProfile.for_user(request.user)
    _get_main_store(owner_user)
    _get_unassigned_location(owner_user)
    _dedupe_default_locations(owner_user)
    locations = _location_queryset_for_user(request.user).order_by('name')
    is_pro_user = _is_pro_user(owner_user)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update_capacity":
            location = _location_queryset_for_user(request.user).filter(pk=request.POST.get("location_id")).first()
            if not location:
                messages.error(request, "Branch not found.")
                return redirect("locations")
            capacity = _clean_inventory_capacity(request.POST.get("inventory_capacity"))
            location.inventory_capacity = capacity
            location.save(update_fields=["inventory_capacity"])
            _log_action(
                request.user,
                "location",
                "Branch inventory capacity updated",
                {"location_id": location.id, "capacity": capacity},
            )
            messages.success(request, f"{location.name} capacity updated to {capacity:,} units.")
            return redirect("locations")

    # --- Stats ---
    total_locations = locations.count()
    total_capacity = locations.aggregate(total=Sum("inventory_capacity"))["total"] or 0
    total_stock_value = _stock_queryset_for_user(request.user).aggregate(
        total=Sum(F('quantity') * F('item__price'), output_field=DecimalField())
    )['total'] or 0

    context = {
        "profile": profile,
        "locations": locations,
        "total_locations": total_locations,
        "total_capacity": total_capacity,
        "total_capacity_display": f"{int(total_capacity):,}",
        "total_stock_value": total_stock_value,
        "total_stock_value_display": _format_money(total_stock_value),
        "is_pro_user": is_pro_user,
    }

    return render(request, "inventory/locations.html", context)


@login_required
@role_required(["admin", "manager"])
def inventory_update_view(request, item_id):
    """
    Update an existing inventory item and optionally adjust
    its stock at a specific location.
    """
    owner_user = _inventory_owner_for_user(request.user)
    item_qs = Item.objects.all()
    if not owner_user.is_superuser:
        item_qs = item_qs.filter(owner=owner_user)
    item = get_object_or_404(item_qs, pk=item_id)

    # --- Fetch supporting data for dropdowns ---
    suppliers = Supplier.objects.filter(owner=owner_user)
    categories = Category.objects.filter(owner=owner_user).order_by("name")
    locations = _location_queryset_for_user(request.user).order_by("name")

    if request.method == "POST":
        # --- Capture form data ---
        name = request.POST.get("name", "").strip()
        category_name = request.POST.get("category", "").strip()
        brand_name = request.POST.get("brand", "").strip()
        location_id = request.POST.get("location")

        quantity = _safe_int(request.POST.get("quantity", item.total_quantity))
        price_raw = request.POST.get("selling_price") or request.POST.get("price", item.price)
        price = _safe_decimal(price_raw)
        cost_price = _safe_decimal(request.POST.get("cost_price", item.cost_price))
        new_sku = request.POST.get("sku", "").strip()
        supplier_id = request.POST.get("supplier")
        barcode = request.POST.get("barcode", "").strip() or None

        # --- Basic validation ---
        if not name or not category_name or not brand_name:
            messages.error(request, "Name, category, and brand are required.")
            return render(request, "inventory/inventory_update.html", {
                "item": item,
                "suppliers": suppliers,
                "categories": categories,
                "locations": locations,
            })

        # --- Get or create related objects ---
        category, _ = Category.objects.get_or_create(owner=owner_user, name=category_name)
        brand, _ = Brand.objects.get_or_create(owner=owner_user, name=brand_name)
        supplier = None
        if supplier_id:
            supplier = Supplier.objects.filter(pk=supplier_id, owner=owner_user).first()

        # --- Update main Item fields ---
        item.name = name
        item.category = category
        item.brand = brand
        item.supplier = supplier
        item.barcode = barcode
        item.cost_price = cost_price
        item.price = price
        if new_sku:
            item.sku = new_sku

        # --- Save within atomic transaction ---
        with transaction.atomic():
            item.save()

            # --- Update or create location-specific stock ---
            if location_id:
                target_location = _get_location_for_user(request.user, location_id)
                # Move stock to the selected location: remove other location records
                StockRecord.objects.filter(item=item).exclude(location=target_location).delete()
                stock_record, created = StockRecord.objects.get_or_create(
                    item=item,
                    location=target_location
                )
                stock_record.quantity = quantity
                stock_record.save()

        # --- Audit & feedback ---
        _log_action(
            request.user,
            "inventory",
            f"Item updated: {item.sku}",
            {"item_id": item.id}
        )
        messages.success(request, f"Item '{item.name}' updated successfully.")
        return redirect("inventory")

    # --- GET request context ---
    current_stock = (
        StockRecord.objects.filter(item=item)
        .select_related("location")
        .order_by("location__name")
        .first()
    )
    context = {
        "item": item,
        "suppliers": suppliers,
        "categories": categories,
        "locations": locations,
        "current_location_id": current_stock.location_id if current_stock else None,
        "barcode": item.barcode or "",
    }

    return render(request, "inventory/inventory_update.html", context)
# ---------------------------
# Inventory Delete & Receive Stock
# ---------------------------

@login_required
@role_required(["admin", "manager"])
def inventory_delete_view(request, item_id):
    """
    Deletes an inventory item along with its associated stock records.
    """
    owner_user = _inventory_owner_for_user(request.user)
    item_qs = Item.objects.all()
    if not owner_user.is_superuser:
        item_qs = item_qs.filter(owner=owner_user)
    item = get_object_or_404(item_qs, pk=item_id)

    if request.method == "POST":
        item_name = item.name
        try:
            item.delete()
            _log_action(
                request.user,
                "inventory",
                f"Item deleted: {item_name}",
                {"item_id": item_id},
            )
            messages.success(request, f"Item '{item_name}' deleted successfully.")
        except ProtectedError:
            with transaction.atomic():
                item.status = "archived"
                item.is_deleted = True
                item.save(update_fields=["status", "is_deleted", "last_modified"])
                StockRecord.objects.filter(item=item).delete()

            _log_action(
                request.user,
                "inventory",
                f"Item archived instead of deleted because linked sales records exist: {item_name}",
                {"item_id": item_id},
            )
            messages.warning(
                request,
                f"Item '{item_name}' is linked to invoice history, so it was archived instead of permanently deleted."
            )
        return redirect("inventory")

    # Optionally, you could render a confirmation page here
    return redirect("inventory")

@login_required
@role_required(["admin", "manager"])
def receive_stock(request):
    """
    Receive stock into a specific location.

    - Creates a PurchaseOrder record.
    - Updates the StockRecord for the selected location.
    """

    owner_user = _inventory_owner_for_user(request.user)
    last_purchase_cost = (
        PurchaseOrder.objects.filter(item=OuterRef("pk"))
        .order_by("-order_date", "-pk")
        .values("unit_cost")[:1]
    )
    stock_total = (
        StockRecord.objects.filter(item=OuterRef("pk"))
        .values("item")
        .annotate(total=Sum("quantity"))
        .values("total")[:1]
    )
    average_purchase_cost = (
        PurchaseOrder.objects.filter(item=OuterRef("pk"))
        .values("item")
        .annotate(average=Avg("unit_cost"))
        .values("average")[:1]
    )
    money_value = Value(Decimal("0.00"), output_field=DecimalField(max_digits=12, decimal_places=2))
    items = _item_queryset_for_user(request.user).annotate(
        receive_current_stock=Coalesce(Subquery(stock_total, output_field=IntegerField()), Value(0)),
        receive_last_purchase_cost=Coalesce(Subquery(last_purchase_cost), money_value),
        receive_average_purchase_cost=Coalesce(Subquery(average_purchase_cost), money_value),
    )
    suppliers = Supplier.objects.filter(owner=owner_user)
    locations = _location_queryset_for_user(request.user).order_by("name")
    fast_moving_count = items.filter(total_quantity__lte=5).count()
    receive_confirmation = request.session.pop("receive_stock_confirmation", None)

    def receive_context():
        return {
            "items": items,
            "suppliers": suppliers,
            "locations": locations,
            "fast_moving_count": fast_moving_count,
            "receive_confirmation": receive_confirmation,
        }

    if request.method == "POST":

        # -----------------------------
        # Capture form data
        # -----------------------------
        item_id = request.POST.get("item")
        supplier_id = request.POST.get("supplier")
        location_id = request.POST.get("location")

        quantity = _safe_int(request.POST.get("quantity", "0"))
        unit_cost = _safe_decimal(request.POST.get("unit_cost", "0"))

        # -----------------------------
        # Validate inputs
        # -----------------------------
        if (
            not item_id
            or not supplier_id
            or not location_id
            or quantity <= 0
            or unit_cost < 0
        ):
            messages.error(request, "All fields are required and must be valid.")

            return render(request, "inventory/receive_stock.html", receive_context())

        # -----------------------------
        # Retrieve database objects
        # -----------------------------
        item = get_object_or_404(
            _item_queryset_for_user(request.user),
            pk=item_id,
        )

        supplier = get_object_or_404(
            Supplier.objects.filter(owner=owner_user),
            pk=supplier_id,
        )

        location = _get_location_for_user(request.user, location_id)

        # -----------------------------
        # Save transaction
        # -----------------------------
        with transaction.atomic():

            PurchaseOrder.objects.create(
                item=item,
                supplier=supplier,
                location=location,
                quantity_received=quantity,
                unit_cost=unit_cost,
            )

            stock_record, created = StockRecord.objects.get_or_create(
                item=item,
                location=location,
                defaults={"quantity": 0},
            )

            stock_record.quantity += quantity
            stock_record.save()
            updated_stock = stock_record.quantity

        # -----------------------------
        # Audit Log
        # -----------------------------
        _log_action(
            request.user,
            "inventory",
            f"Received {quantity} × {item.name}",
            {
                "item_id": item.id,
                "supplier_id": supplier.id,
                "location_id": location.id,
                "quantity": quantity,
                "unit_cost": float(unit_cost),
            },
        )

        messages.success(
            request,
            f"Stock for '{item.name}' received successfully.",
        )
        batch_value = (unit_cost * quantity).quantize(Decimal("0.01"))
        request.session["receive_stock_confirmation"] = {
            "quantity": quantity,
            "item_name": item.name,
            "current_stock": updated_stock,
            "batch_value": str(batch_value),
            "location_name": location.name,
        }

        return redirect("receive_stock")

    return render(
        request,
        "inventory/receive_stock.html",
        {
            **receive_context(),
        },
    )

# ---------------------------
# Sales / POS
# ---------------------------
def quickstock_manifest(request):
    """Serve the POS web app manifest used by the cash register page."""
    manifest = {
        "name": "QuickStock JA",
        "short_name": "QuickStock",
        "start_url": reverse("sales"),
        "scope": "/",
        "display": "standalone",
        "background_color": "#0b72d9",
        "theme_color": "#0b72d9",
        "icons": [
            {
                "src": static("images/QuickStock_Logo.jpg"),
                "sizes": "192x192",
                "type": "image/jpeg",
            }
        ],
    }
    return JsonResponse(manifest, content_type="application/manifest+json")


def quickstock_service_worker(request):
    """Serve a minimal service worker for the cash register PWA shell."""
    script = """
self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});
""".strip()
    return HttpResponse(script, content_type="application/javascript")


@login_required
@role_required(["cashier", "manager", "admin"])
def cash_register(request):
    """
    Handles the Cash Register / POS page:
    1. Location update (POST form from dropdown)
    2. Checkout (AJAX/JSON POST) - Now links to active CashShift
    3. Page rendering (GET)
    """
    profile = UserProfile.for_user(request.user)
    profile = (
        UserProfile.objects.select_related(
            "default_location",
            "parent_admin",
            "parent_admin__default_location",
        )
        .filter(pk=profile.pk)
        .first()
        or profile
    )
    owner_user = _inventory_owner_for_user(request.user)

    default_location = _ensure_default_location_for_profile(
        profile,
        auto_assign=(profile.role != "cashier"),
    )

    active_shift = None
    if default_location:
        active_shift = CashShift.objects.filter(
                cashier=request.user, 
                location=default_location,
                is_closed=False
            ).first()

    if request.method == "GET" and default_location and not active_shift:
            messages.warning(request, "Register is not open. Please open a shift before processing sales.")
            return redirect(f"{reverse('open_shift')}?{urlencode({'next': reverse('cash_register')})}")
    
    _get_unassigned_location(owner_user)
    _dedupe_default_locations(owner_user)

    # --- Branding Inheritance ---
    brand_owner = profile
    if profile.role in ["cashier", "manager"] and profile.parent_admin_id:
        brand_owner = (
            UserProfile.objects.filter(pk=profile.parent_admin_id).first()
            or profile
        )

    logo_url = ""
    if brand_owner.receipt_logo:
        logo_url = brand_owner.receipt_logo.url
    else:
        logo_url = brand_owner.receipt_logo_url

    brand_name = brand_owner.receipt_brand_name or "QuickStock JA Business"

    # ---------------------------
    # 1. Update Default Location
    # ---------------------------
    if request.method == "POST" and request.POST.get("action") == "update_location":
        if not request.user.is_superuser and profile.role != "admin":
            messages.error(request, "Only administrators can change the active register location.")
            return redirect("cash_register")

        location_id = request.POST.get("new_location")
        selected_location = _location_queryset_for_user(request.user).filter(pk=location_id).first()
        if not selected_location:
            messages.error(request, "The selected register location is not available in this workspace.")
            return redirect("cash_register")

        profile.default_location = selected_location
        profile.save(update_fields=["default_location"])
        messages.success(request, "Default location updated.")
        return redirect("cash_register")

    # ---------------------------
    # 2. Checkout / Sale Processing
    # ---------------------------
    if request.method == "POST":
        # CRITICAL: Prevent sales if no shift is open
        if profile.role == "cashier" and not default_location:
            return JsonResponse({
                "success": False,
                "error": "No location has been assigned to your cashier profile yet. Please contact your admin."
            }, status=403)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JsonResponse({"success": False, "error": "Invalid JSON payload."}, status=400)

        cart = payload.get("cart", [])
        if not isinstance(cart, list):
            return JsonResponse({"success": False, "error": "Cart must be a list."}, status=400)
        max_cart_lines = max(1, int(getattr(settings, "POS_CART_MAX_LINES", 200)))
        max_line_quantity = max(1, int(getattr(settings, "POS_CART_MAX_LINE_QUANTITY", 10000)))
        max_money_value = Decimal(str(getattr(settings, "POS_MAX_MONEY_VALUE", "999999999.99")))
        if len(cart) > max_cart_lines:
            return JsonResponse(
                {"success": False, "error": f"A sale may contain at most {max_cart_lines} lines."},
                status=413,
            )
        if any(not isinstance(entry, dict) for entry in cart):
            return JsonResponse(
                {"success": False, "error": "Each cart line must be an object."},
                status=400,
            )
        location_id = payload.get("location_id")
        try:
            discount_value = Decimal(str(payload.get("discount", 0)))
            amount_paid = Decimal(str(payload.get("amount_paid", 0) or 0)).quantize(Decimal("0.01"))
        except (ValueError, TypeError, InvalidOperation):
            return JsonResponse(
                {"success": False, "error": "Payment or discount amount is invalid."},
                status=400,
            )
        if (
            not discount_value.is_finite()
            or not amount_paid.is_finite()
            or abs(discount_value) > max_money_value
            or abs(amount_paid) > max_money_value
        ):
            return JsonResponse(
                {"success": False, "error": "Payment or discount amount is outside the allowed range."},
                status=400,
            )
        try:
            discount_value = discount_value.quantize(Decimal("0.01"))
        except InvalidOperation:
            return JsonResponse(
                {"success": False, "error": "Discount amount is invalid."},
                status=400,
            )
        discount_type = payload.get("discount_type", "flat")
        client_reference = str(payload.get("offline_client_ref") or "").strip()
        if client_reference and (
            len(client_reference) > 64
            or not re.fullmatch(r"[A-Za-z0-9._:-]+", client_reference)
        ):
            return JsonResponse({"success": False, "error": "Sale reference is invalid."}, status=400)
        payment_channel = str(payload.get("payment_channel") or payload.get("tender") or "pos").strip().lower()
        customer_id = str(payload.get("customer_id") or "").strip()
        channel_to_tender = {
            "pos": "cash",
            "cash": "cash",
            "tap2pay": "card",
            "scan2pay": "card",
            "wipay2me": "card",
            "card": "card",
            "jamdex": "jamdex",
            "invoice": "invoice",
        }
        tender_type = channel_to_tender.get(payment_channel)

        # --- Validate payload ---
        if not location_id and profile.role != "cashier":
            return JsonResponse({"success": False, "error": "Location is required."}, status=400)
        if discount_type not in {"flat", "percent"}:
            return JsonResponse({"success": False, "error": "Invalid discount type."}, status=400)
        if tender_type is None:
            return JsonResponse({"success": False, "error": "Invalid payment channel."}, status=400)
        if discount_value < 0 or amount_paid < 0:
            return JsonResponse(
                {"success": False, "error": "Payment and discount amounts cannot be negative."},
                status=400,
            )
        invoice_customer = None
        if customer_id:
            try:
                customer_pk = int(customer_id)
            except (TypeError, ValueError):
                return JsonResponse({"success": False, "error": "Selected customer is invalid."}, status=400)
            invoice_customer = _customer_queryset_for_user(request.user).filter(pk=customer_pk).first()
            if invoice_customer is None:
                return JsonResponse({"success": False, "error": "Selected customer is not available."}, status=404)
            if tender_type != "invoice":
                invoice_customer = None
        if not cart:
            return JsonResponse({"success": False, "error": "Cart is empty."}, status=400)
        cart_item_ids = [entry.get("id") for entry in cart]
        if any(item_id in (None, "") for item_id in cart_item_ids):
            return JsonResponse(
                {"success": False, "error": "Every cart line requires an item ID."},
                status=400,
            )
        if len({str(item_id) for item_id in cart_item_ids}) != len(cart_item_ids):
            return JsonResponse(
                {"success": False, "error": "Duplicate items are not allowed in one sale."},
                status=400,
            )

        if profile.role == "cashier":
            sale_location = default_location
        else:
            try:
                sale_location = _get_location_for_user(request.user, location_id)
            except Http404:
                return JsonResponse(
                    {"success": False, "error": "The selected location is no longer available for checkout. Refresh the register and try again."},
                    status=404,
                )

        with transaction.atomic():
            sale_shift = (
                CashShift.objects.select_for_update()
                .filter(cashier=request.user, location=sale_location, is_closed=False)
                .first()
            )
            if not sale_shift:
                return JsonResponse(
                    {
                        "success": False,
                        "error": f"Register is not open for {sale_location.name}. Please open a shift before processing sales.",
                    },
                    status=403,
                )

            if client_reference:
                existing_sale = Sale.objects.filter(
                    owner=owner_user,
                    sync_token=client_reference,
                ).first()
                existing_invoice = SalesInvoice.objects.filter(
                    owner=owner_user,
                    client_reference=client_reference,
                ).first()
                if tender_type == "invoice" and existing_invoice and not existing_sale:
                    if existing_invoice.created_by_id != request.user.id:
                        return JsonResponse(
                            {"success": False, "error": "Sale reference is already in use."},
                            status=409,
                        )
                    return JsonResponse(
                        {
                            "success": True,
                            "type": "invoice",
                            "invoice_id": existing_invoice.id,
                            "invoice_no": existing_invoice.invoice_no,
                            "detail_url": reverse("sales_invoice_detail", args=[existing_invoice.id]),
                            "idempotent_replay": True,
                        }
                    )
                if tender_type != "invoice" and existing_sale and not existing_invoice:
                    if existing_sale.cashier_id != request.user.id:
                        return JsonResponse(
                            {"success": False, "error": "Sale reference is already in use."},
                            status=409,
                        )
                    return JsonResponse(
                        {
                            "success": True,
                            "type": "sale",
                            "sale_id": existing_sale.id,
                            "receipt_no": existing_sale.receipt_no,
                            "payment_channel": payment_channel,
                            "amount_paid": str(existing_sale.amount_paid),
                            "change_due": str(existing_sale.change_due),
                            "total_price": str(existing_sale.total_price),
                            "idempotent_replay": True,
                        }
                    )
                if existing_sale or existing_invoice:
                    return JsonResponse(
                        {"success": False, "error": "Sale reference is already in use."},
                        status=409,
                    )

            # --- Fetch items and lock stock records ---
            item_ids = [entry.get("id") for entry in cart]
            items = _item_queryset_for_user(request.user).select_for_update().filter(id__in=item_ids)
            item_map = {item.id: item for item in items}

            stock_records = {
                sr.item_id: sr
                for sr in StockRecord.objects.select_for_update()
                .filter(item_id__in=item_ids, location=sale_location)
            }

            sale_items_payload = []

            # --- Validate stock and calculate totals ---
            for entry in cart:
                item_id = entry.get("id")
                try:
                    qty_decimal = Decimal(str(entry.get("quantity", 0)))
                except (ValueError, TypeError, InvalidOperation):
                    return JsonResponse(
                        {"success": False, "error": f"Quantity for item {item_id} must be a whole number."},
                        status=400,
                    )
                if (
                    not qty_decimal.is_finite()
                    or qty_decimal != qty_decimal.to_integral_value()
                    or qty_decimal <= 0
                ):
                    return JsonResponse(
                        {"success": False, "error": f"Quantity for item {item_id} must be a positive whole number."},
                        status=400,
                    )
                if qty_decimal > max_line_quantity:
                    return JsonResponse(
                        {"success": False, "error": f"Quantity for item {item_id} cannot exceed {max_line_quantity}."},
                        status=400,
                    )
                qty = int(qty_decimal)

                item = item_map.get(item_id)
                if not item:
                    return JsonResponse({"success": False, "error": f"Item {item_id} not found."}, status=404)

                if profile.role == "cashier" and item_id not in stock_records:
                    if (sale_location.name or "").strip().lower() == "main store":
                        stock_record, _ = StockRecord.objects.select_for_update().get_or_create(
                            item=item,
                            location=sale_location,
                            defaults={"quantity": item.total_quantity or 0},
                        )
                        stock_records[item_id] = stock_record
                    else:
                        return JsonResponse(
                            {"success": False, "error": f"{item.name} is not stocked at {sale_location.name}."},
                            status=400,
                        )

                stock_record = stock_records.get(item_id)
                if not stock_record or stock_record.quantity < qty:
                    return JsonResponse(
                        {"success": False, "error": f"Insufficient stock for {item.name} at {sale_location.name}."},
                        status=400
                    )

                unit_price = _safe_decimal(item.price, default="0.00")
                if unit_price <= 0:
                    return JsonResponse(
                        {"success": False, "error": f"{item.name} does not have a valid selling price yet."},
                        status=400,
                    )

                sale_items_payload.append((item, stock_record, qty, unit_price))

            # --- Apply discount ---
            subtotal = sum((unit_price * qty for _, _, qty, unit_price in sale_items_payload), Decimal("0.00"))
            if discount_type == "percent":
                if discount_value > 100:
                    return JsonResponse(
                        {"success": False, "error": "Percentage discount cannot exceed 100%."},
                        status=400,
                    )
                discount_amount = (subtotal * discount_value) / Decimal("100")
            else:
                discount_amount = discount_value

            if discount_amount > subtotal:
                return JsonResponse(
                    {"success": False, "error": "Discount cannot exceed the gross sale total."},
                    status=400,
                )
            estimated_total = max(subtotal - discount_amount, Decimal("0.00")).quantize(Decimal("0.01"))
            if tender_type == "cash":
                if amount_paid <= Decimal("0.00"):
                    return JsonResponse(
                        {"success": False, "error": "Enter the amount the customer paid before finalizing this cash sale."},
                        status=400,
                    )
                if amount_paid < estimated_total:
                    short_amount = (estimated_total - amount_paid).quantize(Decimal("0.01"))
                    return JsonResponse(
                        {
                            "success": False,
                            "error": f"Customer payment is short by ${short_amount}. Collect the full amount or switch payment method.",
                        },
                        status=400,
                    )
            if tender_type == "invoice":
                tax_rate = _get_tax_rate_for_location(sale_location)
                tax_rate_percent = (tax_rate * Decimal("100")).quantize(Decimal("0.01"))
                taxable_subtotal = sum(
                    (unit_price * qty for item, _stock_record, qty, unit_price in sale_items_payload if item.is_taxable),
                    Decimal("0.00"),
                ).quantize(Decimal("0.01"))
                subtotal = subtotal.quantize(Decimal("0.01"))
                if discount_type == "percent":
                    normalized_discount_value = discount_value.quantize(Decimal("0.01"))
                else:
                    normalized_discount_value = discount_amount.quantize(Decimal("0.01"))
                taxable_ratio = (taxable_subtotal / subtotal) if subtotal > Decimal("0.00") else Decimal("0.00")
                taxable_discount = (discount_amount * taxable_ratio).quantize(Decimal("0.01"))
                adjusted_taxable = max(Decimal("0.00"), taxable_subtotal - taxable_discount)
                tax_amount = (adjusted_taxable - (adjusted_taxable / (Decimal("1.00") + tax_rate))).quantize(Decimal("0.01")) if tax_rate > Decimal("0.00") else Decimal("0.00")
                total_amount = estimated_total
                invoice = SalesInvoice.objects.create(
                    owner=owner_user,
                    created_by=request.user,
                    customer=invoice_customer,
                    location=sale_location,
                    status="issued",
                    notes=_compose_document_notes(
                        "Created from POS payment channel.",
                        {
                            "source": "cash_register",
                            "payment_channel": payment_channel,
                            "discount_type": discount_type,
                            "discount_value": str(normalized_discount_value),
                            "discount_amount": str(discount_amount.quantize(Decimal("0.01"))),
                            "tax_rate_percent": str(tax_rate_percent),
                        },
                    ),
                    subtotal=subtotal,
                    tax_amount=tax_amount,
                    total_amount=total_amount,
                    client_reference=client_reference,
                )

                for item, stock_record, qty, unit_price in sale_items_payload:
                    SalesInvoiceItem.objects.create(
                        invoice=invoice,
                        item=item,
                        item_name=item.name,
                        quantity=qty,
                        unit_price=unit_price,
                        line_total=(unit_price * qty).quantize(Decimal("0.01")),
                    )
                    stock_record.quantity = max(0, int(stock_record.quantity or 0) - int(qty))
                    stock_record.save(update_fields=["quantity"])

                invoice.subtotal = subtotal
                invoice.tax_amount = tax_amount
                invoice.total_amount = total_amount
                invoice.save(update_fields=["subtotal", "tax_amount", "total_amount"])

                _log_action(
                    request.user,
                    "invoice",
                    "Invoice created from POS payment channel",
                    {
                        "invoice_id": invoice.id,
                        "invoice_no": invoice.invoice_no,
                        "payment_channel": payment_channel,
                        "total": str(invoice.total_amount),
                    },
                )
                return JsonResponse(
                    {
                        "success": True,
                        "type": "invoice",
                        "invoice_id": invoice.id,
                        "invoice_no": invoice.invoice_no,
                        "customer_name": invoice.customer.name if invoice.customer else "",
                        "detail_url": reverse("sales_invoice_detail", args=[invoice.id]),
                    }
                )

            try:
                sale = finalize_sale(
                    owner=owner_user,
                    cashier=request.user,
                    shift=sale_shift,
                    location=sale_location,
                    discount=discount_amount,
                    tender=tender_type,
                    line_items=sale_items_payload,
                    sync_token=client_reference or None,
                )
            except SaleWorkflowError as exc:
                return JsonResponse({"success": False, "error": exc.message}, status=exc.status)

            if tender_type == "cash":
                if amount_paid < sale.total_price:
                    short_amount = (sale.total_price - amount_paid).quantize(Decimal("0.01"))
                    transaction.set_rollback(True)
                    return JsonResponse(
                        {
                            "success": False,
                            "error": f"Customer payment is short by ${short_amount}. Collect the full amount or switch payment method.",
                        },
                        status=400,
                    )
                change_due = (amount_paid - sale.total_price).quantize(Decimal("0.01"))
            else:
                change_due = Decimal("0.00")
                amount_paid = sale.total_price

            sale.amount_paid = amount_paid
            sale.change_due = change_due
            sale.save(update_fields=["amount_paid", "change_due", "last_modified"], skip_validation=True)

        _log_action(
            request.user,
            "sale",
            "Sale completed",
            {"sale_id": sale.id, "total": str(sale.total_price)}
        )
        return JsonResponse({
            "success": True,
            "type": "sale",
            "sale_id": sale.id,
            "receipt_no": sale.receipt_no,
            "payment_channel": payment_channel,
            "amount_paid": str(amount_paid if tender_type == "cash" else sale.total_price),
            "change_due": str(change_due),
            "total_price": str(sale.total_price),
        })

    # ---------------------------
    # 3. GET: Initial Page Load
    # ---------------------------
    pos_initial_limit = max(1, int(getattr(settings, "POS_INITIAL_ITEMS_LIMIT", 50)))
    items_qs = _pos_items_queryset_for_user(request.user, profile)
    items = list(items_qs.prefetch_related("stock_at_locations")[:pos_initial_limit])
    locations = _location_queryset_for_user(request.user).order_by("name")
    customers = _customer_queryset_for_user(request.user).order_by("name")

    tax_rate = _get_tax_rate_for_location(default_location)
    tax_label = _get_tax_label_for_location(default_location)
    theme_pref = getattr(profile, "theme", "system") or "system"
    current_role = getattr(profile, "role", "cashier") or "cashier"
    default_location_id = getattr(profile, "default_location_id", "") or ""
    default_location_name = getattr(default_location, "name", "")
    for item in items:
        stock_quantity = _pos_stock_quantity_for_item(item, default_location)
        stock_badge = _pos_stock_badge(stock_quantity)
        item.pos_stock_quantity = stock_quantity
        item.pos_stock_label = stock_badge["label"]
        item.pos_stock_tone = stock_badge["tone"]
    
    return render(
        request,
        "inventory/cash_register.html",
        {
            "items": items,
            "locations": locations,
            "customers": customers,
            "profile": profile,
            "theme_pref": theme_pref,
            "current_role": current_role,
            "default_location_id": default_location_id,
            "default_location_name": default_location_name,
            "is_admin": current_role == "admin",
            "is_manager_or_admin": current_role in {"manager", "admin"},
            "active_shift": active_shift,
            "tax_rate": tax_rate,
            "tax_rate_percent": (tax_rate * Decimal("100")).quantize(Decimal("0.01")),
            "tax_label": tax_label,
            "location_assignment_required": current_role == "cashier" and not default_location,
            "no_stock": bool(default_location) and not items_qs.exists(),
        },
    )

@login_required
@role_required(["admin", "manager", "cashier"])
def pos_items(request):
    """Return JSON list of POS items for the current user (location-aware)."""
    profile = UserProfile.objects.select_related("default_location").get(user=request.user)
    items = _pos_items_queryset_for_user(request.user, profile)
    query = (request.GET.get("q") or "").strip()
    if query:
        items = items.filter(
            Q(name__icontains=query)
            | Q(sku__istartswith=query)
            | Q(barcode__istartswith=query)
            | Q(barcode__iexact=query)
        )
        limit = max(1, int(getattr(settings, "POS_SEARCH_RESULT_LIMIT", 30)))
    else:
        limit = max(1, int(getattr(settings, "POS_INITIAL_ITEMS_LIMIT", 50)))

    items = list(items.prefetch_related("stock_at_locations")[:limit])

    data = []
    for item in items:
        stock_quantity = _pos_stock_quantity_for_item(item, profile.default_location)
        stock_badge = _pos_stock_badge(stock_quantity)
        data.append({
            "id": item.id,
            "name": item.name,
            "price": float(item.price),
            "sku": item.sku or "",
            "barcode": item.barcode or "",
            "stock_quantity": stock_quantity,
            "stock_label": stock_badge["label"],
            "stock_tone": stock_badge["tone"],
        })
    return JsonResponse({"items": data})

@login_required
@role_required(["admin", "manager", "cashier"])
def pos_item_lookup(request):
    """Lookup a single item by SKU for fast barcode scans (location-aware for cashiers)."""
    code = (request.GET.get("q") or "").strip()
    if not code:
        return JsonResponse({"success": False, "error": "Missing barcode/SKU"}, status=400)

    profile = UserProfile.objects.select_related("default_location").get(user=request.user)
    items = _pos_items_queryset_for_user(request.user, profile).filter(
        Q(barcode__iexact=code) | Q(sku__iexact=code)
    )

    item = items.first()
    if not item:
        return JsonResponse({"success": False, "error": "Item not found."}, status=404)

    stock_quantity = _pos_stock_quantity_for_item(item, profile.default_location)
    stock_badge = _pos_stock_badge(stock_quantity)
    return JsonResponse({
        "success": True,
        "item": {
            "id": item.id,
            "name": item.name,
            "price": float(item.price),
            "sku": item.sku or "",
            "barcode": getattr(item, "barcode", None) or "",
            "stock_quantity": stock_quantity,
            "stock_label": stock_badge["label"],
            "stock_tone": stock_badge["tone"],
        }
    })


@login_required
@role_required(["admin", "manager", "cashier"])
def jamdex_checkout(request):
    """
    Initiate a JAM-DEX payment intent and return provider response.
    Expects JSON: { total or total_price, reference?, sale_id? }
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Invalid method"}, status=405)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    sale_id = payload.get("sale_id")
    amount = None
    
    # If sale_id is provided, prioritize database amount for security
    if sale_id:
        owner = _inventory_owner_for_user(request.user)
        sale = Sale.objects.filter(id=sale_id, owner=owner).first()
        if not sale:
            return JsonResponse({"success": False, "error": "Sale not found or access denied"}, status=404)
        amount = sale.total_price
    else:
        amount = payload.get("total") or payload.get("total_price")

    if amount is None:
        return JsonResponse({"success": False, "error": "Amount is required"}, status=400)

    api_url = getattr(settings, "JAMDEX_API_URL", "")
    merchant_id = getattr(settings, "JAMDEX_MERCHANT_ID", "")
    api_key = getattr(settings, "JAMDEX_API_KEY", "")
    
    if not api_url or not merchant_id:
        return JsonResponse({"success": False, "error": "JAM-DEX is not configured"}, status=503)

    # Sale-bound references are generated and persisted server-side so a client
    # cannot redirect a trusted callback to another tenant's receipt/reference.
    if sale_id:
        reference = sale.payment_reference or f"JD-SALE-{sale.id}-{uuid.uuid4().hex[:12].upper()}"
        if sale.payment_reference != reference:
            sale.payment_reference = reference
            sale.save(update_fields=["payment_reference", "last_modified"], skip_validation=True)
    else:
        reference = payload.get("reference") or f"JD-{uuid.uuid4().hex[:10].upper()}"
    
    callback = request.build_absolute_uri("/jamdex/callback/")
    callback_secret = getattr(settings, "JAMDEX_CALLBACK_SECRET", "").strip()
    if callback_secret:
        callback = f"{callback}?{urlencode({'token': callback_secret})}"

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    body = {
        "merchantId": merchant_id,
        "amount": str(amount),
        "reference": reference,
        "callback_url": callback,
    }

    try:
        resp = requests.post(api_url, json=body, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        _log_action(
            request.user,
            "jamdex_init_fail",
            f"JAM-DEX request failed: {str(e)}",
            {"amount": str(amount), "reference": reference},
            severity="error"
        )
        return JsonResponse(
            {"success": False, "error": "JAM-DEX is temporarily unavailable. Please try again."},
            status=502,
        )

    return JsonResponse({"success": True, "provider_response": data, "reference": reference})


@login_required
@role_required(["admin", "manager", "cashier"])
def card_checkout(request):
    """
    Initiate a card payment checkout URL for POS using WiPay configuration.
    Expects JSON: { total or total_price, reference? }
    Returns a hosted checkout URL the frontend can open.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Invalid method"}, status=405)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    amount = payload.get("total") or payload.get("total_price")
    if amount is None:
        return JsonResponse({"success": False, "error": "Amount is required"}, status=400)

    amount_val = _safe_decimal(amount, default="0.00")
    if amount_val <= 0:
        return JsonResponse({"success": False, "error": "Amount must be greater than 0"}, status=400)

    env, account_number, api_key = _get_wipay_config()
    if not account_number or not api_key:
        return JsonResponse({"success": False, "error": "Card processor is not configured"}, status=503)

    wipay_ready, wipay_issue = _probe_wipay_availability()
    if not wipay_ready:
        _log_action(
            request.user,
            "payment",
            "WiPay unavailable during POS card checkout preflight",
            metadata={"issue": wipay_issue},
            severity="warn",
        )
        return JsonResponse(
            {
                "success": False,
                "error": "WiPay is temporarily unavailable. Please try again in a few minutes.",
                "code": "provider_unavailable",
                "retryable": True,
            },
            status=503,
        )

    payment_channel = (payload.get("payment_channel") or "card").strip().lower()
    channel_prefix = re.sub(r"[^A-Za-z0-9_-]+", "_", payment_channel).strip("_-").upper() or "CARD"
    reference = payload.get("reference") or f"POS-{channel_prefix}-{uuid.uuid4().hex[:10].upper()}"
    success_url = request.build_absolute_uri(reverse("cash_register"))
    amount_str = f"{amount_val:.2f}"

    params = {
        "account_number": account_number,
        "total": amount_str,
        "currency": getattr(settings, "WIPAY_CURRENCY", "JMD"),
        "environment": env,
        "order_id": reference,
        "origin": _wipay_origin("QuickStock_JA_POS"),
        "country_code": getattr(settings, "WIPAY_COUNTRY_CODE", "JM"),
        "fee_structure": getattr(settings, "WIPAY_FEE_STRUCTURE", "merchant_absorb"),
        "method": "credit_card",
        "response_url": success_url,
        "return_url": success_url,
        "name": request.user.get_full_name() or request.user.username,
        "email": request.user.email,
        "phone": _checkout_phone_for_user(request.user),
    }
    base_url = getattr(settings, "WIPAY_ENDPOINT", "https://jm.wipayfinancial.com/plugins/payments/request")
    checkout_url = f"{base_url}?{urlencode(params)}"
    return JsonResponse({"success": True, "checkout_url": checkout_url, "reference": reference})

@csrf_exempt
def jamdex_callback(request):
    """
    Handle JAM-DEX provider callback.
    Expected JSON: { reference: "<id>", status: "paid"/"failed"/"cancelled", txid?: "...", amount?: "...", metadata?: {} }
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Invalid method"}, status=405)

    expected_secret = getattr(settings, "JAMDEX_CALLBACK_SECRET", "").strip()
    provided_secret = (
        request.headers.get("X-JAMDEX-CALLBACK-SECRET", "").strip()
        or request.GET.get("token", "").strip()
    )
    if not expected_secret:
        logger.error("JAM-DEX callback secret is not configured.")
        return JsonResponse({"success": False, "error": "JAM-DEX callback is not configured"}, status=503)
    if not provided_secret or not secrets.compare_digest(provided_secret, expected_secret):
        logger.warning("Rejected JAM-DEX callback with invalid secret.")
        return JsonResponse({"success": False, "error": "Unauthorized callback"}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)

    reference = payload.get("reference") or payload.get("sale_id")
    status = (payload.get("status") or "").lower()
    txid = payload.get("txid") or payload.get("transaction_id") or ""

    if not reference or not status:
        return JsonResponse({"success": False, "error": "Missing reference or status"}, status=400)

    reference_text = str(reference).strip()
    sale = Sale.objects.filter(payment_reference=reference_text).first()

    if not sale and reference_text.startswith("SALE-"):
        try:
            sale_id_part = reference_text.removeprefix("SALE-")
            sale = Sale.objects.filter(id=int(sale_id_part)).first()
        except (ValueError, TypeError):
            pass

    # Numeric references were historically allowed to mean either a primary key
    # or a receipt number. Resolve them only when both interpretations point to
    # one sale; otherwise require a persisted or SALE-prefixed reference.
    if not sale and reference_text.isdigit():
        reference_number = int(reference_text)
        candidates = {
            candidate.pk: candidate
            for candidate in Sale.objects.filter(
                Q(pk=reference_number) | Q(receipt_no=reference_number)
            )[:3]
        }
        if len(candidates) == 1:
            sale = next(iter(candidates.values()))
        elif len(candidates) > 1:
            logger.error("Rejected ambiguous legacy JAM-DEX reference %s", reference_text)
            return JsonResponse(
                {"success": False, "error": "Ambiguous legacy reference"},
                status=409,
            )

    if not sale:
        logger.error(f"JAM-DEX Callback Error: Sale not found for reference {reference}")
        return JsonResponse({"success": False, "error": "Sale not found"}, status=404)

    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    # Verify amount if provided by provider to prevent partial payment fraud
    callback_amount = payload.get("amount")
    if callback_amount:
        try:
            if _safe_decimal(callback_amount) != sale.total_price:
                _log_action(
                    sale.owner, "jamdex_mismatch",
                    f"Amount mismatch in callback: {callback_amount} vs {sale.total_price}",
                    {"sale_id": sale.id, "txid": txid}, severity="error"
                )
                return JsonResponse({"success": False, "error": "Amount mismatch"}, status=400)
        except Exception:
            return JsonResponse({"success": False, "error": "Invalid amount"}, status=400)

    if status in {"paid", "success", "approved"}:
        sale.tender = "jamdex"
        sale.save(update_fields=["tender", "last_modified"], skip_validation=True)
        return JsonResponse({"success": True})

    _log_action(
        sale.owner,
        "jamdex_payment_failed",
        f"JAM-DEX callback reported {status} for sale {sale.id}",
        {"sale_id": sale.id, "txid": txid, "provider_status": status, **metadata},
        severity="warn",
    )
    return JsonResponse({"success": True, "status": "marked_failed"})


@login_required
def role_status(request):
    profile = UserProfile.objects.select_related("default_location").get(user=request.user)
    role = profile.role if profile else "cashier"
    if role in {"admin", "manager"}:
        target = reverse("dashboard")
    else:
        target = reverse("cash_register")
    return JsonResponse({"role": role, "redirect": target})
# ---------------------------
# Sales / Receipts
# ---------------------------
def _receipt_user_role(user):
    if getattr(user, "is_superuser", False):
        return "admin"
    profile = getattr(user, "profile", None)
    return getattr(profile, "role", "cashier") or "cashier"


def _receipt_queryset_for_user(user):
    sales = (
        Sale.objects.select_related(
            "cashier",
            "location",
            "owner",
            "previous_revision",
            "voided_by",
            "last_emailed_by",
        )
        .prefetch_related(
            "items__item",
            "receipt_revisions__edited_by",
            "receipt_email_logs__sent_by",
        )
    )
    if not getattr(user, "is_superuser", False):
        owner = _inventory_owner_for_user(user)
        sales = sales.filter(owner=owner)
    return sales


def _receipt_money(value):
    return _safe_decimal(value, default="0.00").quantize(Decimal("0.01"))


def _receipt_document_number(sale):
    return sale.receipt_no or sale.id


def _receipt_sale_snapshot(sale):
    return {
        "receipt_no": _receipt_document_number(sale),
        "receipt_status": sale.receipt_status,
        "customer_name": sale.customer_name or "",
        "customer_email": sale.customer_email or "",
        "customer_phone": sale.customer_phone or "",
        "notes": sale.notes or "",
        "payment_reference": sale.payment_reference or "",
        "discount": str(_receipt_money(sale.discount)),
        "subtotal": str(_receipt_money(sale.subtotal)),
        "tax_amount": str(_receipt_money(sale.gct_amount)),
        "total_price": str(_receipt_money(sale.total_price)),
        "amount_paid": str(_receipt_money(sale.amount_paid)),
        "change_due": str(_receipt_money(sale.change_due)),
        "revision_number": sale.revision_number,
        "tender": sale.tender,
    }


def _receipt_build_pdf_bytes(lines):
    def _escape_pdf(text):
        return str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    page_height = 842
    y = 800
    content_lines = ["BT", "/F1 10 Tf"]
    for line in lines:
        content_lines.append(f"1 0 0 1 48 {y} Tm ({_escape_pdf(line)}) Tj")
        y -= 16
        if y < 48:
            break
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1", errors="replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 {page_height}] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>".encode("ascii"),
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref_start = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF"
        ).encode("ascii")
    )
    return bytes(pdf)


def _receipt_pdf_lines(context):
    sale = context["sale"]
    width = 62

    def rule(char="-"):
        return char * width

    def amount_row(label, value):
        label = str(label)
        value = str(value)
        return f"{label[:38]:<38}{value:>24}"

    def item_row(description, amount):
        description = str(description)
        amount = str(amount)
        return f"{description[:42]:<42}{amount:>20}"

    lines = [
        context["brand_name"].center(width),
        (context.get("brand_address") or "").center(width),
        " ".join(part for part in [context.get("brand_phone") or "", context.get("brand_email") or ""] if part).strip().center(width),
        "",
        context["grand_total_display"].center(width),
        f"Receipt #{_receipt_document_number(sale)}".center(width),
        f"{timezone.localtime(sale.timestamp).strftime('%d %b %Y %I:%M %p')} | {sale.get_receipt_status_display()}".center(width),
        "",
        rule(),
        amount_row("Description", "Amount"),
        rule(),
    ]
    for line_item in context["receipt_items"]:
        lines.append(item_row(line_item["item_name"], line_item["line_total_display"]))
        if line_item["line_meta"]:
            lines.append(f"  {line_item['line_meta']}")
    lines.extend(
        [
            rule(),
            amount_row("Subtotal", context["subtotal_display"]),
            amount_row(context["tax_label"], context["tax_amount_display"]),
            amount_row("Discount", f"-{context['discount_display']}"),
            rule("="),
            amount_row("Amount Paid", context["amount_paid_display"]),
            amount_row("Change", context["change_due_display"]),
            amount_row("Total", context["grand_total_display"]),
            rule("="),
            "",
            f"Cashier: {getattr(sale.cashier, 'username', 'Deleted User')}",
            f"Location: {getattr(sale.location, 'name', '')}",
            f"Tender: {sale.get_tender_display()}",
        ]
    )
    if sale.customer_name:
        lines.append(f"Customer: {sale.customer_name}")
    if sale.payment_reference:
        lines.append(f"Payment Ref: {sale.payment_reference}")
    if sale.notes:
        lines.extend(["", "Notes:", sale.notes])
    lines.extend(["", f"Thanks, {context['thank_brand']}"])
    return [line for line in lines if line is not None]


def _receipt_build_context(request, sale, *, auto_print=False):
    sale_items = list(sale.items.select_related("item").all())

    line_net_total = sum((si.net_amount for si in sale_items), Decimal("0.00"))
    line_tax_total = sum((si.tax_amount for si in sale_items), Decimal("0.00"))
    line_gross_total = sum((si.total_price for si in sale_items), Decimal("0.00"))
    subtotal = sale.subtotal if sale.subtotal else line_net_total
    tax_rate = _get_tax_rate_for_location(sale.location)
    tax_label = _get_tax_label_for_location(sale.location)
    tax_amount = sale.gct_amount if sale.gct_amount else line_tax_total
    grand_total = sale.total_price if sale.total_price else max(Decimal("0.00"), line_gross_total - sale.discount)
    amount_paid = _receipt_money(sale.amount_paid)
    change_due = _receipt_money(sale.change_due)
    if sale.tender == "cash" and amount_paid <= Decimal("0.00"):
        amount_paid = grand_total
    show_change_section = sale.tender == "cash" or amount_paid > Decimal("0.00")

    location = sale.location
    org_owner = sale.owner or _inventory_owner_for_user(request.user)
    owner_profile = UserProfile.for_user(org_owner)

    logo_url = ""
    if getattr(owner_profile, "receipt_logo", None):
        try:
            logo_url = owner_profile.receipt_logo.url
        except Exception:
            logo_url = ""
    if not logo_url:
        logo_url = getattr(owner_profile, "receipt_logo_url", "") or ""
    if not logo_url:
        logo_url = static("images/QuickStock_Logo.jpg")

    brand_name = owner_profile.receipt_brand_name or getattr(location, "name", "") or "QuickStock JA"
    thank_brand = owner_profile.receipt_brand_name or "QuickStock JA"
    brand_email = owner_profile.receipt_contact_email or ""
    brand_phone = owner_profile.receipt_contact_phone or ""
    brand_address = owner_profile.receipt_address or getattr(location, "address", "") or ""

    receipt_items = []
    for si in sale_items:
        unit_price = _receipt_money(si.unit_price or getattr(si.item, "price", Decimal("0.00")))
        line_total = _receipt_money(si.total_price)
        receipt_items.append(
            {
                "quantity": si.quantity,
                "item_name": si.item.name,
                "description": getattr(si.item, "description", "") or "",
                "unit_price_display": f"${unit_price:.2f}",
                "line_total_display": f"${line_total:.2f}",
                "line_meta": f"@ ${unit_price:.2f} each",
            }
        )

    receipt_revisions = list(sale.receipt_revisions.all())
    email_logs = list(sale.receipt_email_logs.all()[:5])
    role = _receipt_user_role(request.user)
    return {
        "sale": sale,
        "receipt_items": receipt_items,
        "sale_items": sale_items,
        "subtotal": subtotal,
        "discount": sale.discount,
        "tax_amount": tax_amount,
        "grand_total": grand_total,
        "amount_paid": amount_paid,
        "change_due": change_due,
        "show_change_section": show_change_section,
        "tax_rate_percent": (tax_rate * Decimal("100")).quantize(Decimal("0.1")),
        "tax_label": tax_label,
        "brand_name": brand_name,
        "logo_url": logo_url,
        "brand_email": brand_email,
        "brand_phone": brand_phone,
        "brand_address": brand_address,
        "branch_name": getattr(location, "name", "") or "",
        "currency_code": "JMD",
        "thank_brand": thank_brand,
        "receipt_number": _receipt_document_number(sale),
        "subtotal_display": f"${_receipt_money(subtotal):.2f}",
        "discount_display": f"${_receipt_money(sale.discount):.2f}",
        "tax_amount_display": f"${_receipt_money(tax_amount):.2f}",
        "grand_total_display": f"${_receipt_money(grand_total):.2f}",
        "amount_paid_display": f"${_receipt_money(amount_paid):.2f}",
        "change_due_display": f"${_receipt_money(change_due):.2f}",
        "receipt_revisions": receipt_revisions,
        "email_logs": email_logs,
        "can_email_receipt": role in {"cashier", "manager", "admin"},
        "can_edit_receipt": role in {"manager", "admin"},
        "can_void_receipt": role == "admin",
        "auto_print": auto_print,
    }


def _receipt_validate_email(raw_email):
    email = (raw_email or "").strip()
    if not email:
        raise ValidationError("Email address is required.")
    return _normalize_email_address(email)


def _receipt_apply_total_effects(sale, *, amount_paid=None):
    total = _receipt_money(sale.total_price)
    if sale.tender == "cash":
        paid = _receipt_money(amount_paid if amount_paid is not None else sale.amount_paid or total)
        if paid < total:
            raise ValidationError(f"Amount paid cannot be below the recalculated total of ${total:.2f}.")
        sale.amount_paid = paid
        sale.change_due = (paid - total).quantize(Decimal("0.01"))
    else:
        sale.amount_paid = total
        sale.change_due = Decimal("0.00")


@login_required
@role_required(["cashier", "manager", "admin"])
def receipt_print_view(request, sale_id):
    sale = get_object_or_404(_receipt_queryset_for_user(request.user), pk=sale_id)
    Sale.objects.filter(pk=sale.pk).update(printed_count=F("printed_count") + 1)
    _log_action(
        request.user,
        "sale",
        "Receipt printed",
        {"sale_id": sale.id, "receipt_no": _receipt_document_number(sale)},
    )
    sale.refresh_from_db()
    return render(request, "inventory/receipt.html", _receipt_build_context(request, sale, auto_print=True))


@login_required
@role_required(["cashier", "manager", "admin"])
def receipt_download_pdf(request, sale_id):
    sale = get_object_or_404(_receipt_queryset_for_user(request.user), pk=sale_id)
    context = _receipt_build_context(request, sale)
    pdf_bytes = _receipt_build_pdf_bytes(_receipt_pdf_lines(context))
    Sale.objects.filter(pk=sale.pk).update(download_count=F("download_count") + 1)
    _log_action(
        request.user,
        "sale",
        "Receipt downloaded",
        {"sale_id": sale.id, "receipt_no": _receipt_document_number(sale), "format": "pdf"},
    )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="receipt-{_receipt_document_number(sale)}.pdf"'
    return response


@login_required
@role_required(["cashier", "manager", "admin"])
@require_POST
def receipt_email_view(request, sale_id):
    sale = get_object_or_404(_receipt_queryset_for_user(request.user), pk=sale_id)
    if sale.receipt_status == "voided":
        messages.error(request, "Voided receipts cannot be emailed.")
        return redirect("view_receipt", sale_id=sale.id)

    try:
        recipient_email = _receipt_validate_email(request.POST.get("recipient_email") or sale.customer_email)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect("view_receipt", sale_id=sale.id)

    context = _receipt_build_context(request, sale)
    email_context = {
        **context,
        "recipient_email": recipient_email,
        "sale_date": timezone.localtime(sale.timestamp),
    }
    email_log = ReceiptEmailLog.objects.create(
        sale=sale,
        recipient_email=recipient_email,
        sender_email=(request.user.email or getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip(),
        subject=f"QuickStock JA Receipt #{_receipt_document_number(sale)}",
        status="pending",
        sent_by=request.user,
        metadata={"receipt_no": _receipt_document_number(sale)},
    )
    try:
        subject = email_log.subject
        html_body = render_to_string("inventory/receipt_email.html", email_context)
        email = EmailMessage(
            subject=subject,
            body=html_body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            to=[recipient_email],
        )
        email.content_subtype = "html"
        email.attach(
            f"receipt-{_receipt_document_number(sale)}.pdf",
            _receipt_build_pdf_bytes(_receipt_pdf_lines(context)),
            "application/pdf",
        )
        email.send(fail_silently=False)
    except Exception as exc:
        email_log.status = "failed"
        email_log.error_message = str(exc)
        email_log.save(update_fields=["status", "error_message"])
        _log_action(
            request.user,
            "sale",
            "Receipt email failed",
            {"sale_id": sale.id, "recipient_email": recipient_email, "error": str(exc)},
            severity="error",
        )
        messages.error(request, "Receipt email could not be sent right now.")
        return redirect("view_receipt", sale_id=sale.id)

    sale.receipt_status = "emailed" if sale.receipt_status != "voided" else sale.receipt_status
    sale.last_emailed_at = timezone.now()
    sale.last_emailed_to = recipient_email
    sale.last_emailed_by = request.user
    sale.email_count = int(sale.email_count or 0) + 1
    sale.save(
        update_fields=[
            "receipt_status",
            "last_emailed_at",
            "last_emailed_to",
            "last_emailed_by",
            "email_count",
            "last_modified",
        ],
        skip_validation=True,
    )
    email_log.status = "sent"
    email_log.error_message = ""
    email_log.save(update_fields=["status", "error_message"])
    _log_action(
        request.user,
        "sale",
        "Receipt emailed",
        {"sale_id": sale.id, "recipient_email": recipient_email, "receipt_no": _receipt_document_number(sale)},
    )
    messages.success(request, f"Receipt emailed to {recipient_email}.")
    return redirect("view_receipt", sale_id=sale.id)


@login_required
@role_required(["manager", "admin"])
@require_POST
def receipt_edit_view(request, sale_id):
    sale = get_object_or_404(_receipt_queryset_for_user(request.user), pk=sale_id)
    if sale.receipt_status == "voided":
        messages.error(request, "Voided receipts cannot be edited.")
        return redirect("view_receipt", sale_id=sale.id)

    revision_reason = (request.POST.get("edit_reason") or "").strip()
    if not revision_reason:
        messages.error(request, "An edit reason is required.")
        return redirect("view_receipt", sale_id=sale.id)

    before_data = _receipt_sale_snapshot(sale)
    customer_email = (request.POST.get("customer_email") or "").strip()
    if customer_email:
        try:
            customer_email = _receipt_validate_email(customer_email)
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
            return redirect("view_receipt", sale_id=sale.id)

    discount = _receipt_money(request.POST.get("discount") or sale.discount)
    amount_paid_input = request.POST.get("amount_paid")
    amount_paid = _receipt_money(amount_paid_input) if amount_paid_input not in {None, ""} else None

    sale.customer_name = (request.POST.get("customer_name") or "").strip()
    sale.customer_email = customer_email
    sale.customer_phone = (request.POST.get("customer_phone") or "").strip()
    sale.notes = (request.POST.get("notes") or "").strip()
    sale.payment_reference = (request.POST.get("payment_reference") or "").strip()
    sale.discount = discount
    sale.recalculate_totals(save=False)
    try:
        _receipt_apply_total_effects(sale, amount_paid=amount_paid)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect("view_receipt", sale_id=sale.id)

    sale.revision_number = int(sale.revision_number or 1) + 1
    sale.receipt_status = "revised"
    sale.save(
        update_fields=[
            "customer_name",
            "customer_email",
            "customer_phone",
            "notes",
            "payment_reference",
            "discount",
            "subtotal",
            "gct_amount",
            "total_price",
            "amount_paid",
            "change_due",
            "revision_number",
            "receipt_status",
            "last_modified",
        ],
        skip_validation=True,
    )
    after_data = _receipt_sale_snapshot(sale)
    ReceiptRevision.objects.create(
        sale=sale,
        revision_number=sale.revision_number,
        edited_by=request.user,
        reason=revision_reason,
        before_data=before_data,
        after_data=after_data,
    )
    _log_action(
        request.user,
        "sale",
        "Receipt edited",
        {
            "sale_id": sale.id,
            "receipt_no": _receipt_document_number(sale),
            "revision_number": sale.revision_number,
            "edit_reason": revision_reason,
        },
    )
    messages.success(request, "Receipt updated and revision recorded.")
    return redirect("view_receipt", sale_id=sale.id)


@login_required
@role_required(["admin"])
@require_POST
def receipt_void_view(request, sale_id):
    sale = get_object_or_404(_receipt_queryset_for_user(request.user), pk=sale_id)
    if sale.receipt_status == "voided":
        messages.info(request, "This receipt is already voided.")
        return redirect("view_receipt", sale_id=sale.id)

    void_reason = (request.POST.get("void_reason") or "").strip()
    if not void_reason:
        messages.error(request, "A void reason is required.")
        return redirect("view_receipt", sale_id=sale.id)

    sale.receipt_status = "voided"
    sale.void_reason = void_reason
    sale.voided_at = timezone.now()
    sale.voided_by = request.user
    sale.save(
        update_fields=["receipt_status", "void_reason", "voided_at", "voided_by", "last_modified"],
        skip_validation=True,
    )
    _log_action(
        request.user,
        "sale",
        "Receipt voided",
        {"sale_id": sale.id, "receipt_no": _receipt_document_number(sale), "void_reason": void_reason},
        severity="warn",
    )
    messages.success(request, "Receipt voided. The transaction history remains preserved.")
    return redirect("view_receipt", sale_id=sale.id)


@login_required
@role_required(["admin", "manager", "cashier"])
def view_receipt(request, sale_id):
    sale = get_object_or_404(_receipt_queryset_for_user(request.user), pk=sale_id)
    return render(request, "inventory/receipt.html", _receipt_build_context(request, sale))

@login_required
@role_required(["admin", "manager", "cashier"])
def sales_history(request):
    """
    List all sales with filters for date range, location, cashier, and search query.
    Supports pagination and sorting.
    """
    sales = Sale.objects.select_related("cashier", "location").all()
    if not request.user.is_superuser:
        owner = _inventory_owner_for_user(request.user)
        sales = sales.filter(owner=owner)
    locations = _location_queryset_for_user(request.user).order_by("name")

    # --- Filters ---
    query = request.GET.get("q", "").strip()
    start = request.GET.get("start")
    end = request.GET.get("end")
    location_filter = request.GET.get("location", "all")

    # Search by Receipt #, Sale ID, or cashier username
    if query:
        filters = Q(cashier__username__icontains=query)
        digits = "".join(ch for ch in query if ch.isdigit())
        if digits:
            q_num = int(digits)
            filters |= Q(receipt_no=q_num) | Q(id=q_num)
        sales = sales.filter(filters)

    # Filter by location
    if location_filter != "all":
        try:
            loc_id = int(location_filter)
            if request.user.is_superuser or _location_queryset_for_user(request.user).filter(pk=loc_id).exists():
                sales = sales.filter(location_id=loc_id)
            else:
                location_filter = "all"
        except (TypeError, ValueError):
            location_filter = "all"

    # Filter by date range
    if start:
        try:
            start_dt = datetime.fromisoformat(start)
            start_dt = timezone.make_aware(start_dt) if timezone.is_naive(start_dt) else start_dt
            sales = sales.filter(timestamp__gte=start_dt)
        except ValueError:
            pass

    if end:
        try:
            end_dt = datetime.fromisoformat(end)
            end_dt = timezone.make_aware(end_dt) if timezone.is_naive(end_dt) else end_dt
            if start and "start_dt" in locals() and end_dt < start_dt:
                start_dt, end_dt = end_dt, start_dt
            sales = sales.filter(timestamp__lte=end_dt)
        except ValueError:
            pass

    # --- Sorting ---
    sort = request.GET.get("sort", "timestamp")
    direction = request.GET.get("dir", "desc")
    sort_map = {
        "timestamp": "timestamp",
        "total": "total_price",
        "cashier": "cashier__username",
        "receipt_no": "receipt_no",
        "location": "location__name",
    }
    sort_field = sort_map.get(sort, "timestamp")
    if direction == "desc":
        sort_field = f"-{sort_field}"
    sales = sales.order_by(sort_field)

    # --- Pagination ---
    page_size = request.GET.get("page_size", "25")
    try:
        page_size = int(page_size)
    except ValueError:
        page_size = 25
    if page_size not in {10, 25, 50, 100}:
        page_size = 25

    paginator = Paginator(sales, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "inventory/history.html",
        {
            "sales": page_obj,
            "page_obj": page_obj,
            "sort": sort,
            "dir": direction,
            "page_size": page_size,
            "q": query,
            "start": start,
            "end": end,
            "locations": locations,
            "location_filter": location_filter,
        },
    )


@login_required
@role_required(["admin"])
@require_POST
def clear_sales_view(request):
    """
    Clear sales for the current business owner. Platform superusers can clear all.
    """
    sales_qs = Sale.objects.all()
    if not request.user.is_superuser:
        sales_qs = sales_qs.filter(owner=_inventory_owner_for_user(request.user))

    if sales_qs.exists():
        _log_action(
            request.user,
            "sale",
            "Sales purge blocked; financial history must be archived or voided",
            {"reason": "protected_financial_history"},
            severity="warn",
            required=True,
        )
        messages.error(request, "Sales history is financial evidence and cannot be destructively cleared.")
        return redirect("dashboard")

    SaleItem.objects.filter(sale__in=sales_qs).delete()
    deleted_count, _ = sales_qs.delete()
    _log_action(request.user, "sale", "All sales history cleared")
    messages.success(request, f"Sales history cleared successfully ({deleted_count} records removed).")
    return redirect("dashboard")
# ---------------------------
# Reports / Exports
# ---------------------------

@login_required
@pro_required
@role_required(["admin"])
def advanced_reports(request):
    """
    Admin dashboard for advanced analytics:
    - Total revenue
    - Low stock items
    - Potential profit
    - Purchase cost vs retail value
    - Sales trends
    - Cashier performance
    """
    low_stock_threshold = _get_low_stock_threshold()
    owner_user = _inventory_owner_for_user(request.user)

    sales_qs = Sale.objects.all()
    if not request.user.is_superuser:
        sales_qs = sales_qs.filter(owner=owner_user)

    # --- Aggregates ---
    total_revenue = sales_qs.aggregate(total=Sum("total_price"))["total"] or Decimal("0")
    avg_sale = sales_qs.aggregate(avg=Avg("total_price"))["avg"] or Decimal("0")
    item_qs = _item_queryset_for_user(request.user)
    total_item_count = item_qs.count()
    low_stock_items = item_qs.filter(total_quantity__lte=low_stock_threshold)
    low_stock_count = low_stock_items.count()
    healthy_item_count = max(total_item_count - low_stock_count, 0)
    stock_health_rate = int((healthy_item_count / total_item_count) * 100) if total_item_count else 0

    # Potential profit = (retail price - cost price) * total_quantity
    profit_expr = ExpressionWrapper(
        (F("price") - F("cost_price")) * F("total_quantity"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    potential_profit = item_qs.aggregate(total=Sum(profit_expr))["total"] or Decimal("0")

    # Total purchase cost and retail value
    purchase_expr = ExpressionWrapper(
        F("cost_price") * F("total_quantity"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    retail_expr = ExpressionWrapper(
        F("price") * F("total_quantity"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    total_purchase_cost = item_qs.aggregate(total=Sum(purchase_expr))["total"] or Decimal("0")
    total_retail_value = item_qs.aggregate(total=Sum(retail_expr))["total"] or Decimal("0")

    # --- Trends ---
    period = request.GET.get("period", "M").upper()
    today = timezone.localdate()

    def _month_start(date_value):
        return date_value.replace(day=1)

    def _add_months(date_value, months):
        month_index = date_value.month - 1 + months
        year = date_value.year + month_index // 12
        month = month_index % 12 + 1
        day = min(date_value.day, calendar.monthrange(year, month)[1])
        return date_value.replace(year=year, month=month, day=day)

    if period == "D":
        start_date = today - timedelta(days=6)
        trend_rows = (
            sales_qs.filter(timestamp__date__range=(start_date, today))
            .annotate(bucket=TruncDay("timestamp"))
            .values("bucket")
            .annotate(total=Sum("total_price"))
            .order_by("bucket")
        )
        trend_map = {
            row["bucket"].date(): float(row["total"] or 0)
            for row in trend_rows
            if row.get("bucket")
        }
        daily_trends = {
            (start_date + timedelta(days=offset)).strftime("%b %d"): trend_map.get(start_date + timedelta(days=offset), 0.0)
            for offset in range(7)
        }
    elif period == "W":
        current_week_start = today - timedelta(days=today.weekday())
        start_week = current_week_start - timedelta(weeks=7)
        trend_rows = (
            sales_qs.filter(timestamp__date__gte=start_week)
            .annotate(bucket=TruncWeek("timestamp"))
            .values("bucket")
            .annotate(total=Sum("total_price"))
            .order_by("bucket")
        )
        trend_map = {
            row["bucket"].date(): float(row["total"] or 0)
            for row in trend_rows
            if row.get("bucket")
        }
        daily_trends = {}
        for offset in range(8):
            week_start = start_week + timedelta(weeks=offset)
            week_label = week_start.strftime("%b %d")
            daily_trends[week_label] = trend_map.get(week_start, 0.0)
    elif period == "Y":
        start_year = today.year - 4
        trend_rows = (
            sales_qs.filter(timestamp__date__gte=today.replace(year=start_year, month=1, day=1))
            .annotate(bucket=TruncYear("timestamp"))
            .values("bucket")
            .annotate(total=Sum("total_price"))
            .order_by("bucket")
        )
        trend_map = {
            row["bucket"].date(): float(row["total"] or 0)
            for row in trend_rows
            if row.get("bucket")
        }
        daily_trends = {}
        for year_value in range(start_year, today.year + 1):
            year_start = today.replace(year=year_value, month=1, day=1)
            daily_trends[year_start.strftime("%Y")] = trend_map.get(year_start, 0.0)
    else:
        current_month_start = _month_start(today)
        start_month = _add_months(current_month_start, -5)
        trend_rows = (
            sales_qs.filter(timestamp__date__gte=start_month)
            .annotate(bucket=TruncMonth("timestamp"))
            .values("bucket")
            .annotate(total=Sum("total_price"))
            .order_by("bucket")
        )
        trend_map = {
            row["bucket"].date(): float(row["total"] or 0)
            for row in trend_rows
            if row.get("bucket")
        }
        daily_trends = {}
        month_cursor = _month_start(start_month)
        for _ in range(6):
            daily_trends[month_cursor.strftime("%b %Y")] = trend_map.get(month_cursor, 0.0)
            month_cursor = _add_months(month_cursor, 1)

    # --- Cashier performance ---
    cashier_performance = list(
        sales_qs.annotate(
            cashier_name=Coalesce(F("cashier__username"), Value("Unassigned"))
        )
        .values("cashier_name")
        .annotate(total_gen=Sum("total_price"), trans_count=Count("id"))
        .order_by("-total_gen")
    )
    top_cashier = cashier_performance[0] if cashier_performance else None

    # --- Supplier ledger pulse (invoices) ---
    supplier_scope_qs = Supplier.objects.all()
    supplier_invoices_qs = SupplierInvoice.objects.select_related(
        "location",
        "supplier",
    ).prefetch_related(
        "payments__reversal",
        "adjustments",
        "refunds",
    )
    if not request.user.is_superuser:
        supplier_scope_qs = supplier_scope_qs.filter(owner=owner_user)
        supplier_invoices_qs = supplier_invoices_qs.filter(supplier__owner=owner_user)

    supplier_filter = request.GET.get("supplier") or ""
    supplier_scope_label = "All Suppliers"
    if supplier_filter.isdigit():
        supplier_id_int = int(supplier_filter)
        selected_supplier = supplier_scope_qs.filter(pk=supplier_id_int).only("name").first()
        if selected_supplier:
            supplier_invoices_qs = supplier_invoices_qs.filter(supplier_id=supplier_id_int)
            supplier_scope_label = selected_supplier.name
        else:
            supplier_filter = ""

    supplier_invoices = list(
        supplier_invoices_qs.order_by("-date_issued", "-created_at")
    )
    active_supplier_invoices = [
        invoice
        for invoice in supplier_invoices
        if invoice.effective_amount > Decimal("0.00")
    ]
    total_purchases = sum(
        (invoice.effective_amount for invoice in supplier_invoices),
        Decimal("0.00"),
    )
    total_paid = sum(
        (invoice.net_paid_amount for invoice in supplier_invoices),
        Decimal("0.00"),
    )
    current_debt = sum(
        (invoice.balance_due for invoice in supplier_invoices),
        Decimal("0.00"),
    )
    settled_liability = sum(
        (
            min(invoice.net_paid_amount, invoice.effective_amount)
            for invoice in supplier_invoices
        ),
        Decimal("0.00"),
    )
    invoice_count = len(active_supplier_invoices)
    avg_invoice = (total_purchases / invoice_count) if invoice_count else Decimal("0")
    avg_balance = (current_debt / invoice_count) if invoice_count else Decimal("0")
    supplier_payment_ratio = int((settled_liability / total_purchases) * 100) if total_purchases else 0

    supplier_payments_qs = SupplierInvoicePayment.objects.unreversed()
    if not request.user.is_superuser:
        supplier_payments_qs = supplier_payments_qs.filter(owner=owner_user)
    if supplier_filter.isdigit():
        supplier_payments_qs = supplier_payments_qs.filter(supplier_id=int(supplier_filter))
    last_payment_obj = supplier_payments_qs.order_by("-payment_date", "-id").first()
    last_payment_date = last_payment_obj.payment_date if last_payment_obj else None

    latest_invoices = active_supplier_invoices[:5]
    for inv in latest_invoices:
        inv.remaining_balance = inv.balance_due
    payables_chart_invoices = list(reversed(latest_invoices))

    # --- User location (for display) ---
    user_location_name = ""
    try:
        user_location_name = request.user.profile.default_location.name  # type: ignore[attr-defined]
    except Exception:
        user_location_name = ""

    # Supplier list for filter UI
    supplier_list = supplier_scope_qs.order_by("name")

    period_labels = {
        "D": "Daily",
        "W": "Weekly",
        "M": "Monthly",
        "Y": "Yearly",
    }

    context = {
        "total_revenue": total_revenue,
        "low_stock_count": low_stock_count,
        "low_stock_items": low_stock_items,
        "low_stock_threshold": low_stock_threshold,
        "total_item_count": total_item_count,
        "healthy_item_count": healthy_item_count,
        "stock_health_rate": stock_health_rate,
        "potential_profit": potential_profit,
        "total_purchase_cost": total_purchase_cost,
        "total_retail_value": total_retail_value,
        "avg_sale": avg_sale,
        "daily_trends": daily_trends,
        "cashier_performance": cashier_performance,
        "top_cashier": top_cashier,
        "current_period": period,
        "current_period_label": period_labels.get(period, "Monthly"),
        "now": timezone.now(),
        "supplier_total_purchases": total_purchases,
        "supplier_current_debt": current_debt,
        "supplier_payment_ratio": supplier_payment_ratio,
        "supplier_last_payment_date": last_payment_date,
        "supplier_latest_invoices": latest_invoices,
        "user_location_name": user_location_name,
        "supplier_invoice_count": invoice_count,
        "supplier_avg_invoice": avg_invoice,
        "supplier_avg_balance": avg_balance,
        "supplier_filter": supplier_filter,
        "supplier_list": supplier_list,
        "supplier_scope_label": supplier_scope_label,
        "daily_trends_json": json.dumps(daily_trends),
        "cashier_labels_json": json.dumps([row.get("cashier_name") or "Unassigned" for row in cashier_performance]),
        "cashier_values_json": json.dumps([float(row.get("total_gen") or 0) for row in cashier_performance]),
        "payables_labels_json": json.dumps([inv.date_issued.strftime("%b %d") for inv in payables_chart_invoices]),
        "payables_debit_json": json.dumps([float(inv.effective_amount) for inv in payables_chart_invoices]),
        "payables_credit_json": json.dumps([float(inv.net_paid_amount) for inv in payables_chart_invoices]),
        "payables_balance_json": json.dumps([float(inv.balance_due) for inv in payables_chart_invoices]),
    }
    return render(request, "inventory/advanced_reports.html", context)


def _build_xlsx(*, title, subtitle, headers, rows):
    """
    Minimal XLSX builder (no external deps) using inline strings.
    Returns raw bytes suitable for HttpResponse.
    """

    def col_letter(idx):
        letters = ""
        while idx:
            idx, rem = divmod(idx - 1, 26)
            letters = chr(65 + rem) + letters
        return letters

    def cell_xml(r, c, value):
        ref = f"{col_letter(c)}{r}"
        if value is None:
            return f'<c r="{ref}"/>'
        if isinstance(value, (int, float, Decimal)):
            return f'<c r="{ref}"><v>{value}</v></c>'
        text = escape(str(value))
        return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'

    all_rows = []
    if title:
        all_rows.append([title])
    if subtitle:
        all_rows.append([subtitle])
    if title or subtitle:
        all_rows.append([])  # spacer
    all_rows.append(headers)
    all_rows.extend(rows)

    sheet_rows = []
    for r_idx, row in enumerate(all_rows, start=1):
        cells = "".join(cell_xml(r_idx, c_idx, val) for c_idx, val in enumerate(row, start=1))
        sheet_rows.append(f'<row r="{r_idx}">{cells}</row>')
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        + "".join(sheet_rows) +
        "</sheetData>"
        "</worksheet>"
    )

    # Package XML parts
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

    rels_root = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

    workbook_xml = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Inventory" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""

    workbook_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

    styles_xml = """<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
</styleSheet>"""

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels_root)
        z.writestr("xl/workbook.xml", workbook_xml)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        z.writestr("xl/styles.xml", styles_xml)

    return buf.getvalue()

@login_required
@pro_required
@role_required(["admin"])
def export_inventory_csv(request):
    """
    Export inventory as an XLSX workbook (labeled CSV historically).
    Added richer columns + proper Excel format for better readability.
    """
    items = _item_queryset_for_user(request.user).select_related("category", "brand").all()

    # --- Filters ---
    query = request.GET.get("q", "").strip()
    if query:
        items = items.filter(
            Q(name__icontains=query)
            | Q(sku__icontains=query)
            | Q(category__name__icontains=query)
            | Q(brand__name__icontains=query)
        )

    # --- Sorting ---
    sort = request.GET.get("sort", "name")
    direction = request.GET.get("dir", "asc")
    sort_map = {
        "name": "name",
        "quantity": "quantity",
        "price": "price",
        "category": "category__name",
        "brand": "brand__name",
    }
    sort_field = sort_map.get(sort, "name")
    if direction == "desc":
        sort_field = f"-{sort_field}"
    items = items.order_by(sort_field)

    # --- Build rows ---
    headers = [
        "Item Name",
        "SKU",
        "Category",
        "Brand",
        "Stock On Hand",
        "Unit Cost (JMD)",
        "Unit Price (JMD)",
        "Stock Value at Cost (JMD)",
        "Stock Value at Retail (JMD)",
    ]
    data_rows = []
    for item in items:
        qty = item.total_quantity or 0
        cost = item.cost_price or Decimal("0")
        price = item.price or Decimal("0")
        data_rows.append(
            [
                item.name,
                item.sku or "",
                item.category.name if item.category else "",
                item.brand.name if item.brand else "",
                int(qty),
                float(cost),
                float(price),
                float(cost * qty),
                float(price * qty),
            ]
        )

    local_now = timezone.localtime(timezone.now())
    generated_str = local_now.strftime("%Y-%m-%d %H:%M:%S %Z")

    content = _build_xlsx(
        title="Inventory",
        subtitle=f"Generated {generated_str}",
        headers=headers,
        rows=data_rows,
    )

    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="inventory.xlsx"'
    return response
# ---------------------------
# CSV Exports: Sales & Suppliers
# ---------------------------

@login_required
@pro_required
@role_required(["admin"])
def export_sales_csv(request):
    """
    Export filtered sales as CSV
    Supports: query by ID or cashier, date range, location filter, sorting
    """
    sales = Sale.objects.select_related("cashier", "location").all()
    if not request.user.is_superuser:
        sales = sales.filter(owner=_inventory_owner_for_user(request.user))

    # --- Filters ---
    query = request.GET.get("q", "").strip()
    start = request.GET.get("start")
    end = request.GET.get("end")
    location_filter = request.GET.get("location", "all")

    if query:
        if query.isdigit():
            sales = sales.filter(id=int(query))
        else:
            sales = sales.filter(cashier__username__icontains=query)

    # Date filters
    if start:
        try:
            start_dt = datetime.fromisoformat(start)
            start_dt = timezone.make_aware(start_dt) if timezone.is_naive(start_dt) else start_dt
            sales = sales.filter(timestamp__gte=start_dt)
        except ValueError:
            pass

    if end:
        try:
            end_dt = datetime.fromisoformat(end)
            end_dt = timezone.make_aware(end_dt) if timezone.is_naive(end_dt) else end_dt
            if start and "start_dt" in locals() and end_dt < start_dt:
                start_dt, end_dt = end_dt, start_dt
            sales = sales.filter(timestamp__lte=end_dt)
        except ValueError:
            pass

    # Location filter
    if location_filter != "all":
        try:
            sales = sales.filter(location_id=int(location_filter))
        except (TypeError, ValueError):
            pass

    # Sorting
    sort = request.GET.get("sort", "timestamp")
    direction = request.GET.get("dir", "desc")
    sort_map = {
        "timestamp": "timestamp",
        "total": "total_price",
        "cashier": "cashier__username",
        "id": "id",
    }
    sort_field = sort_map.get(sort, "timestamp")
    if direction == "desc":
        sort_field = f"-{sort_field}"
    sales = sales.order_by(sort_field)

    # --- Currency context ---
    try:
        currency_code = getattr(request.user.profile, "currency_code", "").upper() or "JMD"
    except Exception:
        currency_code = "JMD"

    # --- CSV Response ---
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename=\"sales.csv\"'
    writer = csv.writer(response)
    now_str = timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M:%S %Z")
    # First row matches column count to avoid viewer misalignment
    writer.writerow(["Generated At", now_str, "", "", "", ""])
    writer.writerow([
        "Sale Number",
        "Cashier",
        "Location",
        f"Sale Total ({currency_code})",
        f"Discount Applied ({currency_code})",
        "Recorded At",
    ])

    for sale in sales:
        ts = timezone.localtime(sale.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([
            sale.id,
            _csv_safe_cell(sale.cashier.username if sale.cashier else "Unassigned"),
            _csv_safe_cell(sale.location.name if sale.location else ""),
            f"{(sale.total_price or 0):.2f}",
            f"{(sale.discount or 0):.2f}",
            ts,
        ])
    return response


@login_required
@pro_required
@role_required(["admin", "manager"])
def export_suppliers_csv(request):
    """
    Export suppliers as CSV
    Supports: filtering by name/contact/email/phone, sorting by name or order count
    """
    owner = _inventory_owner_for_user(request.user)
    suppliers = Supplier.objects.prefetch_related("orders").filter(owner=owner)

    # --- Filtering ---
    query = request.GET.get("q", "").strip()
    type_filter = request.GET.get("type", "").strip().lower()
    if type_filter in {Supplier.TYPE_LOCAL, Supplier.TYPE_INTERNATIONAL}:
        suppliers = suppliers.filter(supplier_type=type_filter)
    if query:
        suppliers = suppliers.filter(
            Q(name__icontains=query)
            | Q(contact_name__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
            | Q(address__icontains=query)
            | Q(country_code__icontains=query)
        )

    # --- Sorting ---
    sort = request.GET.get("sort", "name")
    direction = request.GET.get("dir", "asc")
    if sort == "orders":
        suppliers = suppliers.annotate(order_count=Count("orders"))
        sort_field = "order_count"
    else:
        sort_field = "name"

    if direction == "desc":
        sort_field = f"-{sort_field}"
    suppliers = suppliers.order_by(sort_field)

    # --- CSV Response ---
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="suppliers.csv"'
    writer = csv.writer(response)
    writer.writerow(["Generated At", timezone.now().isoformat()])
    writer.writerow([
        "Supplier Name",
        "Primary Contact",
        "Email Address",
        "Phone Number",
        "Supplier Type",
        "Country / Market",
        "Business Address",
        "Purchase Orders",
    ])

    for supplier in suppliers:
        writer.writerow([
            _csv_safe_cell(supplier.name),
            _csv_safe_cell(supplier.contact_name or ""),
            _csv_safe_cell(supplier.email or ""),
            _csv_safe_cell(supplier.phone or ""),
            _csv_safe_cell(supplier.get_supplier_type_display()),
            _csv_safe_cell(supplier.get_country_code_display()),
            _csv_safe_cell(supplier.address or ""),
            supplier.orders.count(),
        ])
    return response

@login_required
@pro_required
@role_required(["admin"])
def audit_logs_view(request):
    _cleanup_audit_logs(request.user)
    logs = _audit_log_queryset_for_user(request.user)
    profile = UserProfile.for_user(request.user)
    owner_user = _inventory_owner_for_user(request.user)
    source = (request.GET.get("from") or "").strip().lower()
    if source not in {"dashboard", "settings"}:
        referer = request.META.get("HTTP_REFERER", "")
        if "/settings/" in referer:
            source = "settings"
        else:
            source = "dashboard"

    # --- Filters ---
    query = request.GET.get("q", "").strip()
    action = request.GET.get("action", "").strip()
    severity = request.GET.get("severity", "").strip()

    if query:
        logs = logs.filter(
            Q(message__icontains=query)
            | Q(user__username__icontains=query)
            | Q(metadata__icontains=query)
        )
    if action:
        logs = logs.filter(action=action)
    if severity:
        logs = logs.filter(severity=severity)

    # Pagination
    paginator = Paginator(logs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "inventory/audit_logs.html",
        {
            "logs": page_obj,
            "page_obj": page_obj,
            "profile": profile,
            "is_pro_user": _is_pro_user(owner_user),
            "q": query,
            "action": action,
            "severity": severity,
            "return_source": source,
            "return_url_name": "settings" if source == "settings" else "dashboard",
            "return_label": "Back to Settings" if source == "settings" else "Back to Dashboard",
        },
    )
@login_required
@pro_required
@role_required(["admin"])
def export_audit_csv(request):
    logs = _audit_log_queryset_for_user(request.user)

    query = request.GET.get("q", "").strip()
    action = request.GET.get("action", "").strip()
    severity = request.GET.get("severity", "").strip()

    if query:
        logs = logs.filter(
            Q(message__icontains=query)
            | Q(user__username__icontains=query)
            | Q(metadata__icontains=query)
        )
    if action:
        logs = logs.filter(action=action)
    if severity:
        logs = logs.filter(severity=severity)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="audit_logs.csv"'
    writer = csv.writer(response)
    writer.writerow(["Generated At", timezone.now().isoformat()])
    writer.writerow(["Timestamp", "User", "Action", "Severity", "Message", "Metadata"])

    for log in logs:
        writer.writerow([
            log.created_at.isoformat(),
            _csv_safe_cell(log.user.username if log.user else "system"),
            _csv_safe_cell(log.action),
            _csv_safe_cell(log.severity),
            _csv_safe_cell(log.message),
            _csv_safe_cell(json.dumps(log.metadata, ensure_ascii=True)),
        ])
    return response
@login_required
@pro_required
@role_required(["admin"])
def export_data(request, report_type):
    """
    Dispatch export requests to inventory, sales, or suppliers CSV views
    """
    export_map = {
        "inventory": export_inventory_csv,
        "sales": export_sales_csv,
        "suppliers": export_suppliers_csv,
    }
    export_func = export_map.get(report_type)
    if export_func:
        return export_func(request)
    return HttpResponse("Unknown report type.", status=400)


@login_required
@role_required(["admin"])
def export_audit_logs(request):
    """
    Export audit logs as CSV or JSON for compliance.
    Admin only. Non-superusers see only their org's logs.
    """
    export_format = request.GET.get("format", "csv").lower()
    logs = _audit_log_queryset_for_user(request.user).order_by("-created_at")[:5000]

    if export_format == "json":
        data = [
            {
                "timestamp": log.created_at.isoformat(),
                "user": log.user.username if log.user else "system",
                "action": log.action,
                "severity": log.severity,
                "message": log.message,
                "metadata": log.metadata,
            }
            for log in logs
        ]
        return JsonResponse(data, safe=False)

    # default CSV
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="audit_logs_{timezone.now().date()}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Timestamp", "User", "Action", "Severity", "Message", "Metadata"])
    for log in logs:
        writer.writerow([
            log.created_at.isoformat(),
            _csv_safe_cell(log.user.username if log.user else "system"),
            _csv_safe_cell(log.action),
            _csv_safe_cell(log.severity),
            _csv_safe_cell(log.message),
            _csv_safe_cell(json.dumps(log.metadata, ensure_ascii=True)),
        ])
    return response


# ---------------------------
# Upgrade / Payments
# ---------------------------

@login_required
def upgrade_plan(request):
    profile = getattr(request.user, "profile", None)
    env, account_number, api_key = _get_wipay_config()
    wipay_ready = bool(account_number and api_key)
    yearly_price = _get_pro_price("yearly")
    monthly_price = _get_pro_price("monthly")
    return render(
        request,
        "inventory/upgrade.html",
        {
            "profile": profile,
            "now": timezone.now(),
            "wipay_ready": wipay_ready,
            "wipay_env": env,
            "pro_price_yearly": yearly_price,
            "pro_price_monthly": monthly_price,
        },
    )

@login_required
def upgrade_cancel(request):
    return render(request, "inventory/upgrade_cancel.html")


@login_required
def create_checkout_session(request):
    return JsonResponse({"error": "Stripe is not configured."}, status=400)


def _normalize_wipay_order_id(order_id):
    order_id = str(order_id or "").strip()
    if not order_id:
        return ""
    match = re.search(r"QS-\d+-[A-Za-z0-9]+", order_id)
    return match.group(0) if match else order_id


def _is_wipay_sandbox_reference(order_id):
    return str(order_id or "").strip().upper().startswith("SB-")


def _is_wipay_paid_status(status):
    normalized = re.sub(r"[^a-z]+", " ", str(status or "").lower()).strip()
    if normalized in {"success", "successful", "paid", "approved", "complete", "completed"}:
        return True
    status_words = set(normalized.split())
    return bool(status_words & {"success", "successful", "paid", "approved", "complete", "completed"})


@login_required
def create_wipay_checkout_session(request):
    if request.method == "POST":
        payload = {}
        if request.content_type and "application/json" in request.content_type:
            try:
                payload = json.loads(request.body.decode("utf-8") or "{}")
            except Exception:
                payload = {}
        billing_cycle = _normalize_billing_cycle(
            payload.get("billing_cycle") or request.POST.get("billing_cycle") or "yearly"
        )
        env, account_number, api_key = _get_wipay_config()
        if not account_number or not api_key:
            return JsonResponse(
                {
                    "success": False,
                    "error": (
                        "WiPay is not configured. Set account number and API key "
                        f"for {env} environment."
                    ),
                },
                status=400,
            )

        wipay_ready, wipay_issue = _probe_wipay_availability()
        if not wipay_ready:
            _log_action(
                request.user,
                "payment",
                "WiPay unavailable during subscription checkout preflight",
                metadata={"issue": wipay_issue},
                severity="warn",
            )
            return JsonResponse(
                {
                    "success": False,
                    "error": "WiPay is temporarily unavailable. Please try again in a few minutes.",
                    "code": "provider_unavailable",
                    "retryable": True,
                },
                status=503,
            )

        order_id = f"QS-{request.user.id}-{uuid.uuid4().hex[:6]}"
        success_url = request.build_absolute_uri(reverse("wipay_response"))
        amount = _get_pro_price(billing_cycle)
        amount_str = f"{amount:.2f}"

        # Record the pending payment
        Payment.objects.create(
            user=request.user,
            provider="wipay",
            order_id=order_id,
            amount=amount,
            currency=getattr(settings, "WIPAY_CURRENCY", "JMD"),
            status="pending",
            response_payload={"billing_cycle": billing_cycle},
        )
        
        params = {
            "account_number": account_number,
            "total": amount_str,
            "currency": getattr(settings, "WIPAY_CURRENCY", "JMD"),
            "environment": env,
            "order_id": order_id,
            "origin": _wipay_origin("QuickStock_JA"),
            "country_code": getattr(settings, "WIPAY_COUNTRY_CODE", "JM"),
            "fee_structure": getattr(settings, "WIPAY_FEE_STRUCTURE", "merchant_absorb"),
            "method": getattr(settings, "WIPAY_METHOD", "credit_card"),
            "response_url": success_url,    # required by WiPay
            "return_url": success_url,      # backward compatible
            "name": request.user.get_full_name() or request.user.username,
            "email": request.user.email,
            "phone": _checkout_phone_for_user(request.user),
        }

        base_url = getattr(settings, "WIPAY_ENDPOINT", "https://jm.wipayfinancial.com/plugins/payments/request")
        query_string = urlencode(params)
        
        return JsonResponse({"success": True, "url": f"{base_url}?{query_string}", "billing_cycle": billing_cycle})
    return JsonResponse({"success": False, "error": "Invalid request method."}, status=405)
    
@login_required
def upgrade_success(request):
    order_id = _normalize_wipay_order_id(request.GET.get('order_id'))
    if not order_id:
        messages.error(request, "We could not verify your payment reference.")
        return redirect('upgrade')

    payment = Payment.objects.filter(order_id=order_id, user=request.user).first()
    if not payment:
        messages.error(request, "Payment record not found for this account.")
        return redirect('upgrade')

    callback_status = (request.GET.get("status") or request.POST.get("status") or "").strip()
    has_callback_verification_data = any(
        request.GET.get(field) or request.POST.get(field)
        for field in ("amount", "total", "hash", "transaction_id")
    )
    if payment.status != "paid" and callback_status and has_callback_verification_data:
        return wipay_response(request)

    if payment.status != "paid":
        if payment.status == "failed":
            messages.error(request, "Payment failed. Please try again.")
        else:
            messages.info(request, "Payment is still being verified. Refresh in a moment if it just completed.")
        return redirect('upgrade')

    existing_payload = payment.response_payload if isinstance(payment.response_payload, dict) else {}
    billing_cycle = _normalize_billing_cycle(
        existing_payload.get("billing_cycle") or request.GET.get("billing_cycle") or "yearly"
    )

    profile = request.user.profile
    profile.plan = 'PRO'
    profile.pro_expires = timezone.localdate() + timedelta(days=_billing_cycle_days(billing_cycle))
    profile.status = "active"
    profile.plan_end = None
    profile.save(update_fields=["plan", "pro_expires", "plan_end", "status"])

    request.user.is_active = True
    request.user.save(update_fields=["is_active"])

    _log_action(request.user, "payment", f"Verified Pro upgrade confirmed: {order_id}")
    _prune_auth_billing_messages(request)
    messages.success(request, "Welcome to QuickStock JA Pro! Your features are now unlocked.")
    return redirect('dashboard')
from django.db import transaction

@csrf_exempt
def wipay_response(request):
    """
    WiPay callback handler for payment confirmations.
    Handles both GET and POST, verifies amounts and hashes, updates Payment and user profile.
    """
    # 1️⃣ Extract data from request
    data = request.GET.dict() if request.method == "GET" else request.POST.dict()
    raw_order_id = data.get("order_id")
    order_id = _normalize_wipay_order_id(raw_order_id)
    status = (data.get("status") or "").lower()
    transaction_id = data.get("transaction_id")
    response_hash = data.get("hash")
    amount_str = data.get("amount") or data.get("total")

    if not order_id:
        messages.error(request, "Payment response missing order ID.")
        return redirect("upgrade_cancel")

    payment = Payment.objects.filter(order_id=order_id).first()
    if not payment:
        messages.error(request, "Payment record not found.")
        return redirect("upgrade_cancel")

    success_redirect = f"{reverse('upgrade_success')}?{urlencode({'order_id': order_id})}"

    # 2️⃣ Idempotency: already paid
    if payment.status == "paid":
        return redirect(success_redirect)

    # 3️⃣ Begin atomic transaction for safety
    try:
        with transaction.atomic():
            # Serialize callbacks for the same order so concurrent provider retries
            # cannot process one pending payment more than once.
            # Payment.user is nullable, so select_related("user") produces a
            # LEFT OUTER JOIN. PostgreSQL cannot apply FOR UPDATE to the
            # nullable side of that join. Lock only the payment row and let the
            # user relation load separately when it is accessed below.
            payment = Payment.objects.select_for_update().get(pk=payment.pk)
            if payment.status == "paid":
                return redirect(success_redirect)
            if not payment.user_id:
                logger.error("WiPay callback references payment %s without an account owner.", order_id)
                messages.error(request, "Payment account could not be verified.")
                return redirect("upgrade_cancel")

            existing_payload = payment.response_payload if isinstance(payment.response_payload, dict) else {}
            # The selected plan is trusted from our checkout record, not from the
            # provider callback. Provider fields are still retained for auditability.
            billing_cycle = _normalize_billing_cycle(existing_payload.get("billing_cycle") or "yearly")
            payment.transaction_id = transaction_id
            payment.response_payload = {
                **existing_payload,
                **data,
                "raw_order_id": raw_order_id,
                "order_id": order_id,
                "billing_cycle": billing_cycle,
            }

            # 3a️⃣ Verify amount
            if amount_str:
                try:
                    amount_val = _safe_decimal(amount_str)
                    if amount_val != payment.amount:
                        payment.status = "failed"
                        payment.save(update_fields=["transaction_id", "response_payload", "status"])
                        _log_action(
                            payment.user,
                            "payment",
                            "Payment amount mismatch",
                            {"order_id": order_id, "expected": str(payment.amount), "actual": str(amount_val)},
                            severity="error",
                        )
                        messages.error(request, "Payment verification failed (amount mismatch).")
                        return redirect("upgrade_cancel")
                except Exception as e:
                    _log_action(
                        payment.user,
                        "payment",
                        f"Amount verification error: {str(e)}",
                        {"order_id": order_id},
                        severity="error",
                    )
                    messages.error(request, "Payment verification failed (invalid amount).")
                    return redirect("upgrade_cancel")

            # 3b️⃣ Verify hash (only in live)
            env, _, api_key = _get_wipay_config()
            require_hash = env == "live" and not _is_wipay_sandbox_reference(raw_order_id)
            if require_hash:
                if not response_hash or not transaction_id:
                    payment.status = "failed"
                    payment.response_payload["failure_reason"] = "missing_transaction_verification_data"
                    payment.save(update_fields=["transaction_id", "response_payload", "status"])
                    messages.error(request, "Payment verification failed (missing transaction verification data).")
                    return redirect("upgrade_cancel")
                
                expected_str = f"{transaction_id}{payment.amount:.2f}{api_key}"
                expected_hash = hashlib.md5(expected_str.encode()).hexdigest()
                if not secrets.compare_digest(expected_hash, response_hash):
                    payment.status = "failed"
                    payment.response_payload["failure_reason"] = "hash_mismatch"
                    payment.save(update_fields=["transaction_id", "response_payload", "status"])
                    messages.error(request, "Payment verification failed (hash mismatch).")
                    return redirect("upgrade_cancel")

            # 3c️⃣ Success: update payment & profile. WiPay has used a few
            # equivalent labels across hosted checkout flows.
            if _is_wipay_paid_status(status):
                payment.status = "paid"
                payment.save(update_fields=["transaction_id", "response_payload", "status"])

                profile = UserProfile.for_user(payment.user)
                profile.plan = "PRO"
                profile.pro_expires = timezone.localdate() + timedelta(days=_billing_cycle_days(billing_cycle))
                profile.status = "active"
                profile.plan_end = None
                profile.save(update_fields=["plan", "pro_expires", "plan_end", "status"])

                # Activate account once payment is confirmed
                payment.user.is_active = True
                payment.user.save(update_fields=["is_active"])

                _log_action(
                    payment.user,
                    "payment",
                    "Payment successful",
                    {"order_id": order_id, "transaction_id": transaction_id},
                )
                return redirect(success_redirect)

            # 3d️⃣ Any other status = failed
            payment.status = "failed"
            payment.response_payload["failure_reason"] = "unpaid_provider_status"
            payment.save(update_fields=["transaction_id", "response_payload", "status"])
            _log_action(
                payment.user,
                "payment",
                "Payment failed",
                {
                    "order_id": order_id,
                    "transaction_id": transaction_id,
                    "provider_status": status,
                    "raw_order_id": raw_order_id,
                },
                severity="error",
            )
            messages.error(request, "Payment failed. Please try again.")
            return redirect("upgrade_cancel")

    except Exception as e:
        # 4️⃣ Catch-all: ensures database consistency
        exception_type = type(e).__name__
        exception_message = str(e)
        try:
            latest_payload = payment.response_payload if isinstance(payment.response_payload, dict) else {}
            Payment.objects.filter(pk=payment.pk).update(
                response_payload={
                    **latest_payload,
                    "failure_reason": "processing_exception",
                    "exception_type": exception_type,
                    "exception_message": exception_message,
                    "raw_order_id": raw_order_id,
                    "order_id": order_id,
                    "provider_status": status,
                }
            )
        except Exception:
            logger.exception("Could not persist WiPay processing exception details for %s", order_id)
        _log_action(
            payment.user,
            "payment",
            f"Payment processing exception: {exception_type}: {exception_message}",
            {"order_id": order_id, "raw_order_id": raw_order_id, "provider_status": status},
            severity="critical",
        )
        messages.error(
            request,
            f"Payment processing error ({exception_type}): {exception_message or 'No details returned'}. Reference: {order_id}",
        )
        return redirect("upgrade_cancel")

@login_required
def settings_view(request):
    # 1. Resolve Hierarchy (Staff see the Admin's branding)
    profile = UserProfile.for_user(request.user)
    is_admin = bool(request.user.is_superuser or (profile and profile.role == "admin"))
    brand_owner = profile if is_admin else profile.parent_admin
    owner_user = brand_owner.user if brand_owner else request.user

    if request.method == "POST":
        try:
            with transaction.atomic():
                # --- Handle system logistics (Admin Only) ---
                if request.POST.get("action") == "update_tax_rate_single":
                    if not is_admin:
                        messages.error(request, "Only Administrators can update country and tax settings.")
                        return redirect("settings")

                    country_code = (request.POST.get("tax_rate_country") or "JM").upper().strip()
                    if country_code not in TAX_RATES_BY_COUNTRY:
                        messages.error(request, "Invalid country selected.")
                        return redirect("settings")

                    tax_rate_value = _safe_decimal(request.POST.get("tax_rate_value"), default="0")
                    if tax_rate_value < Decimal("0") or tax_rate_value > Decimal("100"):
                        messages.error(request, "Tax rate must be between 0 and 100.")
                        return redirect("settings")

                    tax_rate_decimal = (tax_rate_value / Decimal("100")).quantize(Decimal("0.0001"))
                    cache.set(f"tax_rate:{country_code}", str(tax_rate_decimal), None)

                    updated_locations = _location_queryset_for_user(request.user).exclude(name__iexact="Unassigned").update(
                        country_code=country_code
                    )

                    messages.success(
                        request,
                        f"Country and tax applied: {country_code} at {tax_rate_value.quantize(Decimal('0.01'))}% across {updated_locations} location(s).",
                    )
                    return redirect("settings")

                if request.POST.get("action") == "update_system_logistics":
                    if not is_admin:
                        messages.error(request, "Only Administrators can update system logistics.")
                        return redirect("settings")

                    retention_raw = request.POST.get("audit_retention_days")
                    if retention_raw:
                        try:
                            retention_days = max(1, int(retention_raw))
                            cache.set("audit_retention_days", retention_days, None)
                        except ValueError:
                            messages.error(request, "Invalid audit retention value.")
                            return redirect("settings")

                    funding_id = request.POST.get("funding_branch")
                    if funding_id:
                        branch = _location_queryset_for_user(request.user).filter(id=funding_id).first()
                        if branch and branch.name.lower() != "unassigned":
                            cache.set(f"primary_funding_branch:{owner_user.id}", branch.id, None)
                        else:
                            messages.error(request, "Invalid funding branch selected.")
                            return redirect("settings")

                    messages.success(request, "System logistics updated.")
                    return redirect("settings")

                if request.POST.get("action") == "update_accounting_integration":
                    if not is_admin:
                        messages.error(request, "Only Administrators can update accounting integrations.")
                        return redirect("settings")

                    provider = request.POST.get("provider") or AccountingIntegration.PROVIDER_XERO
                    if provider not in dict(AccountingIntegration.PROVIDER_CHOICES):
                        messages.error(request, "Unsupported accounting provider.")
                        return redirect("settings")

                    integration = get_or_create_accounting_integration(owner_user, provider=provider)
                    requested_status = request.POST.get("status") or AccountingIntegration.STATUS_DISCONNECTED
                    if requested_status not in dict(AccountingIntegration.STATUS_CHOICES):
                        requested_status = AccountingIntegration.STATUS_DISCONNECTED

                    integration.status = requested_status
                    integration.sync_sales = request.POST.get("sync_sales") == "1"
                    integration.sync_sales_invoices = request.POST.get("sync_sales_invoices") == "1"
                    integration.sync_purchase_invoices = request.POST.get("sync_purchase_invoices") == "1"
                    integration.sync_inventory_items = request.POST.get("sync_inventory_items") == "1"
                    integration.save(
                        update_fields=[
                            "status",
                            "sync_sales",
                            "sync_sales_invoices",
                            "sync_purchase_invoices",
                            "sync_inventory_items",
                            "updated_at",
                        ]
                    )

                    queued_count = 0
                    if request.POST.get("queue_existing") == "1" and integration.is_ready_for_sync:
                        queued_count = len(queue_existing_accounting_data(owner_user, provider=provider))

                    suffix = f" {queued_count} existing record(s) queued." if queued_count else ""
                    messages.success(request, f"{integration.get_provider_display()} integration settings saved.{suffix}")
                    return redirect("settings")

                # --- 2. Personal preferences (Available to all) ---
                updates = []
                theme = request.POST.get("theme")
                if theme in dict(UserProfile.THEME_CHOICES):
                    profile.theme = theme
                    updates.append("theme")

                pos_cfg = request.POST.get("pos")
                if pos_cfg is not None:
                    profile.pos_config = pos_cfg.strip()
                    updates.append("pos_config")

                if updates:
                    profile.save(update_fields=updates)

                # --- 3. Branding settings (Admin only) ---
                if is_admin and brand_owner:
                    brand_updates = []
                    fields = {
                        "receipt_brand_name": request.POST.get("brand_name", "").strip(),
                        "receipt_contact_email": request.POST.get("brand_email", "").strip(),
                        "receipt_contact_phone": request.POST.get("brand_phone", "").strip(),
                    }

                    for field, value in fields.items():
                        if getattr(brand_owner, field) != value:
                            setattr(brand_owner, field, value)
                            brand_updates.append(field)

                    logo_file = request.FILES.get("brand_logo_file")
                    logo_url = request.POST.get("brand_logo_url", "").strip()
                    remove_logo = request.POST.get("remove_brand_logo") == "1"

                    if remove_logo:
                        if brand_owner.receipt_logo:
                            brand_owner.receipt_logo.delete(save=False)
                        brand_owner.receipt_logo = None
                        brand_owner.receipt_logo_url = ""
                        brand_updates.extend(["receipt_logo", "receipt_logo_url"])
                    elif logo_file:
                        try:
                            brand_owner.receipt_logo = _validate_receipt_logo_upload(logo_file)
                        except ValidationError as exc:
                            messages.error(request, exc.messages[0])
                            return redirect("settings")
                        brand_owner.receipt_logo_url = ""
                        brand_updates.extend(["receipt_logo", "receipt_logo_url"])
                    elif logo_url != brand_owner.receipt_logo_url:
                        brand_owner.receipt_logo_url = logo_url
                        brand_owner.receipt_logo = None
                        brand_updates.extend(["receipt_logo_url", "receipt_logo"])

                    if brand_updates:
                        brand_owner.save(update_fields=brand_updates)
                        _log_action(request.user, "SYSTEM_UPDATE", f"Updated branding for {brand_owner.receipt_brand_name}")

                messages.success(request, "System preferences saved.")

        except Exception as e:
            _log_action(request.user, "SYSTEM_ERROR", f"Settings update failed: {str(e)}", severity="error")
            messages.error(request, "A system error occurred during synchronization.")
        
        return redirect('settings')

  # --- 4. Logic for Live Cash Register Tracking (ENHANCED) ---
    active_shifts = []
    staff_profiles = UserProfile.objects.none()
    if is_admin:
        # 1. Define a visibility window (e.g., shifts from the last 12 hours)
        # This ensures you see "Finished" shifts even after they end.
        visibility_window = timezone.now() - timedelta(hours=12)

        staff_profiles = (
            UserProfile.objects
            .filter(parent_admin=profile, is_archived=False, user__is_active=True)
            .exclude(status="suspended")
            .select_related("user", "default_location")
        )
        tracked_user_ids = list(staff_profiles.values_list("user_id", flat=True))
        tracked_user_ids.append(request.user.id)

        # 2. Show open shifts regardless of start time, plus recently closed shifts.
        active_shifts = CashShift.objects.filter(
            cashier_id__in=tracked_user_ids,
        ).filter(
            Q(is_closed=False)
            | Q(end_time__gte=visibility_window)
            | Q(is_closed=True, end_time__isnull=True, opened_at__gte=visibility_window)
        ).select_related('cashier', 'location').order_by('-opened_at')

        # 3. Idle Staff Logic: show as idle only if not already listed above.
        active_cashier_ids = {shift.cashier_id for shift in active_shifts if not shift.is_closed}
        visible_cashier_ids = {shift.cashier_id for shift in active_shifts}
        
        unassigned_staff = staff_profiles.filter(role="cashier", default_location__isnull=True)
        assigned_idle_staff = staff_profiles.filter(
            role="cashier",
            default_location__isnull=False,
        ).exclude(user_id__in=active_cashier_ids).exclude(user_id__in=visible_cashier_ids)

    # --- 5. Prepare Display Data ---
    display_logo = ""
    if brand_owner and brand_owner.receipt_logo:
        display_logo = brand_owner.receipt_logo.url
    elif brand_owner and brand_owner.receipt_logo_url:
        display_logo = brand_owner.receipt_logo_url

    primary_funding_id = cache.get(f"primary_funding_branch:{owner_user.id}")
    if not primary_funding_id:
        main_store = _get_main_store(owner_user)
        primary_funding_id = main_store.id if main_store else None

    all_locations = _location_queryset_for_user(request.user)
    for loc in all_locations:
        setattr(loc, "is_primary", loc.id == primary_funding_id)

    # Resolve renewal date and display tier for UI consistency using hierarchy
    sub_holder = profile.get_effective_plan_owner() if profile else None
    renewal_date = None

    if sub_holder:
        # Check pro_expires first; fall back gracefully to plan_end if it's a Trial account
        if sub_holder.pro_expires:
            renewal_date = sub_holder.pro_expires
        elif sub_holder.plan_end:
            renewal_date = sub_holder.plan_end.date()

    if request.user.is_superuser:
        display_tier = "PRO (SYSTEM ADMIN)"
    elif sub_holder:
        display_tier = sub_holder.plan_badge_label
    else:
        display_tier = "TRIAL"

    context = {
        "profile": profile,
        "is_admin": is_admin,
        "active_shifts": active_shifts, 
        "live_shift_count": len(active_shifts),
        "unassigned_staff": unassigned_staff,
        "assigned_idle_staff": assigned_idle_staff,
        "live_register_attention_count": len(active_shifts) + len(unassigned_staff) + len(assigned_idle_staff),
        "brand_defaults": {
            "name": brand_owner.receipt_brand_name if brand_owner else "",
            "logo": display_logo,
            "logo_url": brand_owner.receipt_logo_url if brand_owner else "",
            "email": brand_owner.receipt_contact_email if brand_owner else "",
            "phone": brand_owner.receipt_contact_phone if brand_owner else "",
        },
        "all_locations": all_locations,
        "all_staff": staff_profiles if is_admin else [],
        "staff_management_enabled": profile.is_pro_active(),
        "tax_rate_percent_by_country": {code: float(_get_tax_rate_for_country(code)) * 100 for code in TAX_RATES_BY_COUNTRY},
        "audit_retention_days": cache.get("audit_retention_days", 90),
        "is_pro_user": _is_pro_user(owner_user),
        "renewal_date": renewal_date,
        "display_tier": display_tier,
    }
    return render(request, "inventory/settings.html", context)

def _send_discrepancy_alert(admin_user, staff_user, variance):
    """
    Sends an immediate email alert to the Business Owner/Admin 
    when a cashier or manager clocks out with a significant cash variance.
    """
    if not admin_user or not admin_user.email:
        logger.warning(f"Could not send discrepancy alert for {staff_user.username}: No admin email.")
        return

    # 1. Format the variance for readability (e.g., -$450.00)
    formatted_variance = f"{variance:,.2f}"
    
    # 2. Determine the severity 'emoji' for quick scanning in a busy inbox
    # Use a higher threshold if you want to ignore small 'cents' differences
    status_icon = "⚠️" if abs(variance) > 100 else "ℹ️"
    
    subject = f"{status_icon} QuickStock JA: Cash Discrepancy Alert - {staff_user.username}"
    
    # 3. Build a high-integrity body with context
    body = (
        f"Attention {admin_user.username},\n\n"
        f"A shift closure (Clock Out) was just recorded with a cash discrepancy.\n\n"
        f"STAFF MEMBER: {staff_user.get_full_name()} (@{staff_user.username})\n"
        f"VARIANCE: ${formatted_variance}\n"
        f"TIME: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')} (EST)\n"
        f"LOCATION: {getattr(staff_user.profile.default_location, 'name', 'Main Store')}\n\n"
        "Please log in to the QuickStock Admin Dashboard to review the full Audit Log "
        "and reconciliation report.\n\n"
        "— QuickStock JA Reliability Engine"
    )

    try:
        email = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[admin_user.email],
            reply_to=[staff_user.email] if staff_user.email else None,
        )
        email.send(fail_silently=True)
    except Exception as e:
        logger.error(f"Failed to dispatch discrepancy email: {str(e)}")



@login_required
def logout_with_clock_out(request):
    if request.method == "POST":
        # Reconciliation-driven clock-out using CashShift.
        active_shift = CashShift.get_active_shift(request.user)

        if active_shift:
            actual_cash = (request.POST.get("actual_cash") or "").strip()
            if not actual_cash:
                messages.error(request, "Enter counted cash before closing the shift.")
                return render(request, "inventory/reconcile_and_logout.html")
            try:
                active_shift.close_shift(
                    actual_cash,
                    actor=request.user,
                    notes="Shift closed during logout reconciliation.",
                )
            except (ValidationError, TypeError, ValueError) as exc:
                messages.error(request, str(exc))
                return render(request, "inventory/reconcile_and_logout.html")

        logout(request)
        return redirect('login')
    
    # If they hit this via GET (e.g. clicking a link), redirect to the 
    # reconciliation page so they are FORCED to enter cash totals.
    return render(request, 'inventory/reconcile_and_logout.html')

from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from decimal import Decimal
from .models import CashShift, UserProfile
from .decorators import role_required # Assuming this is your custom decorator

@login_required
@role_required(["cashier", "manager", "admin"])
def open_shift(request):
    next_url = _safe_next_url(request, "cash_register")

    # 1. Get user profile and ensure they have a default location assigned
    try:
        profile = UserProfile.objects.select_related("default_location").get(user=request.user)
        default_location = _ensure_default_location_for_profile(
            profile,
            auto_assign=(profile.role != "cashier"),
        )
        if not default_location:
            messages.error(request, "No location has been assigned to your cashier profile yet. Please contact your admin.")
            return redirect('cash_register')
    except UserProfile.DoesNotExist:
        messages.error(request, "User profile not found.")
        return redirect('dashboard')

    active_shift = CashShift.get_active_shift(request.user)
    if active_shift:
        messages.info(request, "You already have an active shift session. Reconcile it before opening another shift.")
        return redirect(next_url)

    if request.method == "POST":
        opening_cash = _safe_decimal(request.POST.get("opening_cash", "0.00"))
        if opening_cash < Decimal("0.00"):
            messages.error(request, "Opening cash cannot be negative.")
            return render(request, "inventory/open_shift.html", {
                "profile": profile,
                "location_name": default_location.name,
                "next_url": next_url,
            })
        try:
            with transaction.atomic():
                CashShift.objects.create(
                    cashier=request.user,
                    location=default_location,
                    opening_cash=opening_cash,
                    expected_cash=opening_cash,
                    is_closed=False,
                )
        except IntegrityError:
            messages.info(request, "A shift was opened already. Reconcile it before opening another shift.")
            return redirect(next_url)
        
        # Log and Notify
        _log_action(request.user, "shift_open", f"Register opened at {default_location.name}")
        messages.success(request, f"Shift started at {default_location.name} with ${opening_cash} float.")
        
        return redirect(next_url)

    return render(request, "inventory/open_shift.html", {
        "profile": profile,
        "location_name": default_location.name,
        "next_url": next_url,
    })

@login_required
@role_required(["cashier", "manager", "admin"])
def open_register(request):
    """
    View for a cashier to 'Clock In' to a register. 
    This creates the CashShift record that the Admin tracks.
    """
    profile = UserProfile.for_user(request.user)
    
    # Check if they already have an open shift
    existing_shift = CashShift.objects.filter(
        cashier=request.user, 
        is_closed=False
    ).first()
    
    if existing_shift:
        return redirect('cash_register')

    if getattr(profile.default_location, "is_archived", False):
        messages.error(request, "Archived locations cannot open a register.")
        return redirect("cash_register")

    if request.method == "POST":
        opening_cash = _safe_decimal(request.POST.get("opening_cash", 0))
        if opening_cash < Decimal("0.00"):
            messages.error(request, "Opening cash cannot be negative.")
            return render(request, "inventory/open_register.html", {"profile": profile})
        try:
            CashShift.objects.create(
                cashier=request.user,
                location=profile.default_location,
                opening_cash=opening_cash,
                expected_cash=opening_cash,
                is_closed=False,
            )
        except IntegrityError:
            messages.info(request, "A shift is already open for this operator and location.")
            return redirect("cash_register")
        
        _log_action(request.user, "register_open", f"Opened register at {profile.default_location}")
        messages.success(request, f"Register opened with ${opening_cash}")
        return redirect('cash_register')

    return render(request, "inventory/open_register.html", {"profile": profile})


@login_required
@role_required("admin")
@require_POST
def audit_cleanup_now(request):
    """
    Trigger manual cleanup of audit logs based on retention policy.
    """
    if request.user.is_superuser:
        _cleanup_audit_logs()
    else:
        _cleanup_audit_logs(request.user)
    _log_action(request.user, "system", "Manual audit log cleanup performed")
    messages.success(request, "Audit logs cleaned up successfully based on retention policy.")
    return redirect("settings")


@login_required
@role_required("admin")
@require_POST
def clear_all_logs(request):
    """
    Purge audit logs for the current business owner, bypassing retention policy.
    """
    logs_qs = _audit_log_queryset_for_user(request.user)

    purgeable_logs = logs_qs.exclude(action__in=FINANCIAL_AUDIT_ACTIONS)
    total_logs = purgeable_logs.count()
    purgeable_logs.delete()

    # Log the purge (this will be the only remaining log)
    _log_action(request.user, "system", f"Manual nonfinancial log purge: {total_logs} logs deleted.")

    messages.success(request, f"Successfully purged all {total_logs} system logs.")
    redirect_to = request.POST.get("redirect", "settings")
    return redirect("audit_logs" if redirect_to == "audit_logs" else "settings")


def _send_staff_invite_email(user, temp_password):
    """
    Sends an email with temporary credentials to a new staff member.
    """
    if not user.email:
        return

    subject = "Your QuickStock JA Staff Account"
    body = (
        "Your staff account has been created.\n\n"
        f"Username: {user.username}\n"
        f"Temporary password: {temp_password}\n\n"
        "Please log in and change your password immediately."
    )
    EmailMessage(subject, body, to=[user.email]).send(fail_silently=True)


def _send_activation_email(request, user):
    """
    Send account activation email to the user.
    """
    if not user.email:
        return False
    delivery_status = email_delivery_status()
    if not delivery_status["ok"]:
        logger.error(
            "Cannot send activation email for user %s: %s",
            user.pk,
            delivery_status["detail"],
        )
        return False

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = token_generator.make_token(user)
    context = {
        "user": user,
        "uid": uid,
        "token": token,
        "domain": request.get_host(),
        "protocol": "https" if request.is_secure() else "http",
    }
    subject = "Activate your QuickStock JA account"
    body = render_to_string("inventory/email_verification.html", context)
    connection = get_delivery_connection()
    email = EmailMessage(subject, body, to=[user.email], connection=connection)
    email.content_subtype = "html"
    try:
        email.send(fail_silently=False)
        return True
    except Exception:
        logger.exception("Could not send activation email to user %s", user.pk)
        return False


@login_required
@pro_required
@role_required("admin")
def manage_staff(request):
    """
    Unified staff management: handles creation, role updates, 
    location assignments, and deletions.
    """
    if request.method != "POST":
        return redirect("settings")

    action = request.POST.get("action")
    user_id = request.POST.get("user_id")
    admin_profile = request.user.profile
    can_manage_daily_summary_access = bool(
        request.user.is_superuser or (admin_profile and admin_profile.role == "admin")
    )

    # 1. Action Validation
    if action in ["update_location", "update_role", "update_daily_summary_access", "delete", "archive"] and not user_id:
        messages.error(request, "Critical Error: No User ID received.")
        return redirect("settings")

    # 2. Target Profile Retrieval
    # Ensures the admin only modifies their own staff or themselves
    profile = None
    if user_id:
        profile = UserProfile.objects.filter(
            Q(user_id=user_id, parent_admin=admin_profile) | 
            Q(user_id=user_id, id=admin_profile.id)
        ).first()
        
        if not profile and action != "create":
            messages.error(request, "User not found or access denied.")
            return redirect("settings")

    with transaction.atomic():
        # --- ACTION: UPDATE LOCATION (Unassign Support) ---
        if action == "update_location":
            loc_id = request.POST.get("new_location")
            previous_location_id = profile.default_location_id
            
            # If loc_id is empty string, 'none', or '0', we unassign the user
            if not loc_id or loc_id.lower() in ["none", "0"]:
                if CashShift.objects.filter(cashier=profile.user, is_closed=False).exists():
                    messages.error(request, "Reconcile and close the active shift before changing its location assignment.")
                else:
                    profile.default_location = None
                    profile.save(update_fields=["default_location"])
                    messages.success(request, f"Unassigned {profile.user.username} from all locations.")
            else:
                # Only query if we have a potential ID to avoid ValueError
                branch = _location_queryset_for_user(request.user).filter(id=loc_id).first()
                if branch:
                    active_other_location = CashShift.objects.filter(
                        cashier=profile.user,
                        is_closed=False,
                    ).exclude(location=branch).exists()
                    if active_other_location:
                        messages.error(request, "Reconcile and close the active shift before changing its location assignment.")
                    else:
                        profile.default_location = branch
                        profile.save(update_fields=["default_location"])
                        messages.success(request, f"Assigned {profile.user.username} to {branch.name}.")
                else:
                    messages.error(request, "Invalid location selected.")

        # --- ACTION: UPDATE ROLE ---
        elif action == "update_role":
            new_role = request.POST.get("new_role")
            valid_roles = [choice[0] for choice in UserProfile.ROLE_CHOICES]
            
            if new_role in valid_roles:
                if profile.user == request.user and new_role != "admin":
                    messages.error(request, "Safety Lock: You cannot demote yourself.")
                else:
                    old_role = profile.role
                    role_weights = {"admin": 3, "manager": 2, "cashier": 1}
                    is_demotion = role_weights.get(old_role, 0) > role_weights.get(new_role, 0)

                    profile.role = new_role
                    if new_role not in {"manager", "cashier"}:
                        profile.can_edit_daily_summary = False
                    profile.save()

                    if is_demotion:
                        # Clear sessions to force re-login with new permissions
                        for s in Session.objects.all():
                            if s.get_decoded().get("_auth_user_id") == str(profile.user.id):
                                s.delete()
                        messages.success(request, f"{profile.user.username} demoted and logged out for security.")
                    else:
                        messages.success(request, f"Role updated for {profile.user.username}.")

        elif action == "update_daily_summary_access":
            if not can_manage_daily_summary_access:
                messages.error(request, "Only an admin or superuser can change Daily Summary access.")
                return redirect("settings")
            if profile.role not in {"manager", "cashier"}:
                profile.can_edit_daily_summary = False
                profile.save(update_fields=["can_edit_daily_summary"])
                messages.error(request, "Daily Summary access can only be granted to managers or cashiers.")
            else:
                profile.can_edit_daily_summary = request.POST.get("can_edit_daily_summary") == "1"
                profile.save(update_fields=["can_edit_daily_summary"])
                state = "granted" if profile.can_edit_daily_summary else "revoked"
                messages.success(request, f"Daily Summary edit access {state} for {profile.user.username}.")

       # --- ACTION: CREATE STAFF (REPAIRED WITH EMAIL LOGIC) ---
        elif action == "create":
            email = (request.POST.get("email") or "").strip()
            username = (request.POST.get("username") or "").strip()
            role = (request.POST.get("role") or "cashier").strip()
            loc_id = request.POST.get("default_location")

            if not email or not username:
                messages.error(request, "Username and Email are required.")
                return redirect("settings")

            try:
                email = _normalize_email_address(email)
            except ValidationError:
                messages.error(request, "Please enter a valid email address.")
                return redirect("settings")

            if role not in {"manager", "cashier"}:
                messages.error(request, "Staff accounts must use the Manager or Cashier role.")
                return redirect("settings")

            # Existing accounts must never be silently moved between companies.
            if User.objects.filter(username__iexact=username).exists():
                messages.error(request, "That username is already registered. Choose a different username.")
                return redirect("settings")
            if User.objects.filter(email__iexact=email).exists():
                messages.error(request, "That email address is already linked to a QuickStock account.")
                return redirect("settings")

            temp_password = "".join(
                secrets.choice(string.ascii_letters + string.digits) for _ in range(16)
            )
            user = User(username=username, email=email, is_active=True)
            user.set_password(temp_password)
            user.save()

            # The user post-save signal creates the profile; configure only this
            # newly created record for the current company.
            new_profile = UserProfile.for_user(user)
            new_profile.parent_admin = admin_profile
            new_profile.role = role
            new_profile.status = "active"
            new_profile.can_edit_daily_summary = False
            
            # 3. Handle Location Assignment
            if loc_id and loc_id not in ["", "none"]:
                new_profile.default_location = _location_queryset_for_user(request.user).filter(id=loc_id).first()
            new_profile.save()

            try:
                subject = "Your QuickStock JA Staff Account"
                body = (
                    f"Hi {username},\n\n"
                    f"Your staff account has been created for QuickStock.\n\n"
                    f"Username: {username}\n"
                    f"Temporary Password: {temp_password}\n\n"
                    "Please login and update your password in the settings immediately."
                )
                EmailMessage(subject, body, to=[email]).send(fail_silently=False)
                messages.success(request, f"Staff '{username}' created. Credentials sent to {email}.")
            except Exception as e:
                logger.exception("Staff invitation email failed for user %s", username)
                messages.warning(request, f"Staff created, but email delivery failed. Temporary password: {temp_password}")

        # --- ACTION: DELETE STAFF ---
        elif action == "delete":
            target_user = User.objects.filter(id=user_id).first()
            
            if not target_user or target_user == request.user:
                messages.error(request, "Cannot delete yourself or user not found.")
            else:
                tprof = UserProfile.for_user(target_user)
                # Verify they are part of this admin's organization
                if tprof.parent_admin == admin_profile or request.user.is_superuser:
                    if action == "archive" or _user_has_financial_history(target_user):
                        _archive_user_profile(
                            tprof,
                            request.user,
                            "Staff account retained because financial history must remain attributable.",
                        )
                        for s in Session.objects.all():
                            if s.get_decoded().get("_auth_user_id") == str(target_user.id):
                                s.delete()
                        _log_action(
                            request.user,
                            "user",
                            "Staff account archived instead of deleted",
                            {"user_id": target_user.id, "reason": "financial_history"},
                            required=True,
                        )
                        messages.warning(
                            request,
                            f"Staff '{target_user.username}' was archived so financial audit history remains attributable.",
                        )
                        return redirect("settings")

                    # Session cleanup
                    for s in Session.objects.all():
                        if s.get_decoded().get("_auth_user_id") == str(target_user.id):
                            s.delete()

                    # Keep business data under the organization owner before user deletion.
                    org_owner = _inventory_owner_for_user(request.user) or request.user
                    Item.objects.filter(owner=target_user).update(owner=org_owner)
                    Supplier.objects.filter(owner=target_user).update(owner=org_owner)
                    Customer.objects.filter(owner=target_user).update(owner=org_owner)
                    Sale.objects.filter(owner=target_user).update(owner=org_owner)
                    SalesInvoice.objects.filter(owner=target_user).update(owner=org_owner)
                    SalesQuotation.objects.filter(owner=target_user).update(owner=org_owner)

                    # Re-home locations safely (owner+name must stay unique).
                    for old_loc in Location.objects.filter(owner=target_user):
                        replacement = (
                            Location.objects
                            .filter(owner=org_owner, name=old_loc.name)
                            .exclude(id=old_loc.id)
                            .first()
                        )

                        if replacement:
                            # Move FK references to the replacement location.
                            Sale.objects.filter(location=old_loc).update(location=replacement)
                            CashShift.objects.filter(location=old_loc).update(location=replacement)
                            SupplierInvoice.objects.filter(location=old_loc).update(location=replacement)
                            UserProfile.objects.filter(default_location=old_loc).update(default_location=replacement)
                            StockTransfer.objects.filter(from_location=old_loc).update(from_location=replacement)
                            StockTransfer.objects.filter(to_location=old_loc).update(to_location=replacement)

                            # Merge stock quantities for matching item/location pairs.
                            for stock in StockRecord.objects.select_related("item").filter(location=old_loc):
                                target_stock, created = StockRecord.objects.get_or_create(
                                    item=stock.item,
                                    location=replacement,
                                    defaults={"quantity": 0},
                                )
                                target_stock.quantity = (target_stock.quantity or 0) + (stock.quantity or 0)
                                target_stock.save(update_fields=["quantity"])
                                stock.delete()

                            old_loc.delete()
                        else:
                            old_loc.owner = org_owner
                            old_loc.save(update_fields=["owner"])

                    # If this user managed other staff, re-link them to the current admin.
                    UserProfile.objects.filter(parent_admin=tprof).update(parent_admin=admin_profile)

                    # Explicitly purge the profile record to prevent data bleeding into future signups
                    tprof.delete()

                    uname = target_user.username
                    try:
                        target_user.delete()
                        messages.success(request, f"Staff '{uname}' removed successfully.")
                    except (ProtectedError, IntegrityError):
                        messages.error(
                            request,
                            "Unable to delete this staff account because protected records are still linked. "
                            "Please reassign related records, then try again."
                        )
                else:
                    messages.error(request, "Unauthorized delete attempt.")

    return redirect("settings")

        
# Supplier Management / Ledger
# ---------------------------
@login_required
@role_required(["admin", "manager"])
def supplier_list(request):
    owner = _inventory_owner_for_user(request.user)
    base_suppliers = Supplier.objects.filter(owner=owner)
    supplier_counts = {
        "all": base_suppliers.count(),
        "local": base_suppliers.filter(supplier_type=Supplier.TYPE_LOCAL).count(),
        "international": base_suppliers.filter(supplier_type=Supplier.TYPE_INTERNATIONAL).count(),
    }
    suppliers = base_suppliers.annotate(order_count=Count("orders")).prefetch_related("invoices")
    query = request.GET.get("q", "").strip()
    type_filter = request.GET.get("type", "").strip().lower()
    if type_filter not in {Supplier.TYPE_LOCAL, Supplier.TYPE_INTERNATIONAL}:
        type_filter = ""

    if type_filter:
        suppliers = suppliers.filter(supplier_type=type_filter)

    if query:
        suppliers = suppliers.filter(
            Q(name__icontains=query)
            | Q(contact_name__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
            | Q(address__icontains=query)
            | Q(country_code__icontains=query)
        )

    # Sorting
    sort = request.GET.get("sort", "name")
    direction = request.GET.get("dir", "asc")
    if sort == "orders":
        sort_field = "order_count"
    else:
        sort_field = "name"

    if direction == "desc":
        sort_field = f"-{sort_field}"

    suppliers = suppliers.order_by(sort_field)

    # Pagination
    try:
        page_size = int(request.GET.get("page_size", 25))
        if page_size not in {10, 25, 50, 100}:
            page_size = 25
    except ValueError:
        page_size = 25

    paginator = Paginator(suppliers, page_size)
    page_obj = paginator.get_page(request.GET.get("page"))
    page_query_params = request.GET.copy()
    page_query_params.pop("page", None)

    return render(
        request,
        "inventory/supplier_list.html",
        {
            "suppliers": page_obj,
            "page_obj": page_obj,
            "sort": sort,
            "dir": direction,
            "page_size": page_size,
            "q": query,
            "type_filter": type_filter,
            "supplier_counts": supplier_counts,
            "page_query": page_query_params.urlencode(),
        },
    )


@login_required
@role_required(["admin", "manager"])
def supplier_add(request):
    if request.method == "POST":
        form = SupplierForm(request.POST)
        if form.is_valid():
            supplier = form.save(commit=False)
            supplier.owner = _inventory_owner_for_user(request.user)
            supplier.save()
            _log_action(
                request.user,
                "supplier",
                "Supplier created",
                {"supplier_id": supplier.id, "name": supplier.name},
            )
            messages.success(request, "Supplier added successfully.")
            return redirect("supplier_list")
    else:
        form = SupplierForm()

    return render(request, "inventory/add_supplier.html", {"form": form, "mode": "add"})


@login_required
@role_required(["admin", "manager"])
def supplier_edit(request, pk):
    supplier_qs = Supplier.objects.filter(owner=_inventory_owner_for_user(request.user))
    supplier = get_object_or_404(supplier_qs, pk=pk)

    if request.method == "POST":
        form = SupplierForm(request.POST, instance=supplier)
        if form.is_valid():
            supplier = form.save()
            _log_action(
                request.user,
                "supplier",
                "Supplier updated",
                {"supplier_id": supplier.id, "name": supplier.name},
            )
            messages.success(request, f"Supplier '{supplier.name}' updated successfully.")
            return redirect("supplier_list")
    else:
        form = SupplierForm(instance=supplier)

    return render(
        request,
        "inventory/add_supplier.html",
        {"form": form, "supplier": supplier, "mode": "edit"},
    )
from django.db import IntegrityError # Import this!

@login_required
@pro_required
@role_required(["admin"])
def add_location(request):
    if request.method == "POST":
        name = request.POST.get("name")
        address = request.POST.get("address")
        country_code = (request.POST.get("country_code") or "JM").upper()
        inventory_capacity = _clean_inventory_capacity(request.POST.get("inventory_capacity"))
        
        if name:
            try:
                # We use .strip() to avoid "PRIORY " being different from "PRIORY"
                new_node = Location.objects.create(
                    owner=_inventory_owner_for_user(request.user),
                    name=name.upper().strip(), 
                    address=address,
                    country_code=country_code,
                    inventory_capacity=inventory_capacity,
                )
                _log_action(
                    request.user,
                    "node_add",
                    f"System expansion: {name}",
                    {"location_id": new_node.id, "capacity": inventory_capacity},
                )
                messages.success(request, f"Node {new_node.name} is now online.")
                return redirect("locations") 
                
            except IntegrityError:
                # Catch the UNIQUE constraint failure and send a friendly message
                messages.error(request, f"Initialization Error: A node named '{name.upper()}' already exists in the network.")
            
    return render(request, "inventory/add_location.html")


@login_required
@pro_required
@role_required(["admin"])
@require_POST
def delete_location(request, location_id):
    main_store = _get_main_store(request.user)
    unassigned = _get_unassigned_location(request.user)
    location = _get_location_for_user(request.user, location_id)
    if location.id in {main_store.id, unassigned.id}:
        messages.error(request, "Default locations cannot be deleted.")
        return redirect("locations")
    has_financial_history = any(
        queryset.exists()
        for queryset in (
            Sale.objects.filter(location=location),
            CashShift.objects.filter(location=location),
            CashMovement.objects.filter(location=location),
            CashReconciliation.objects.filter(location=location),
            SalesInvoice.objects.filter(location=location),
            SalesInvoicePayment.objects.filter(location=location),
            SupplierInvoice.objects.filter(location=location),
            SupplierInvoicePayment.objects.filter(location=location),
            SupplierInvoiceAdjustment.objects.filter(location=location),
            SupplierInvoiceRefund.objects.filter(location=location),
            PurchaseOrder.objects.filter(location=location),
        )
    )
    if has_financial_history:
        _archive_record(location, request.user, "Location retained because financial history references its snapshot.")
        _log_action(
            request.user,
            "location",
            "Location archived instead of deleted",
            {"location_id": location.id, "reason": "protected_financial_history"},
            required=True,
        )
        messages.warning(request, f"Location '{location.name}' was archived because financial history references it.")
        return redirect("locations")
    try:
        # Re-point sales to the main store to avoid PROTECT constraint
        Sale.objects.filter(location=location).update(location=main_store)
        # Re-point supplier invoices to the main store (SET_NULL otherwise)
        SupplierInvoice.objects.filter(location=location).update(location=main_store)
        UserProfile.objects.filter(default_location=location).update(default_location=main_store)
        location_name = location.name
        location.delete()
        _log_action(request.user, "location", f"Location deleted: {location_name}")
        messages.success(request, f"Location '{location_name}' deleted.")
    except Exception:
        _archive_record(location, request.user, "Location retained because linked history prevented deletion.")
        _log_action(
            request.user,
            "location",
            "Location archived after deletion was blocked",
            {"location_id": location.id, "reason": "protected_link"},
            required=True,
        )
        messages.warning(request, "Location could not be deleted and was archived instead.")
    return redirect("locations")

@login_required
@role_required("admin")
@require_POST
def supplier_delete(request, pk):
    supplier_qs = Supplier.objects.filter(owner=_inventory_owner_for_user(request.user))
    supplier = get_object_or_404(supplier_qs, pk=pk)
    supplier_name = supplier.name
    try:
        supplier.delete()
    except ProtectedError:
        _archive_record(supplier, request.user, "Supplier retained because financial history is protected.")
        _log_action(
            request.user,
            "supplier",
            "Supplier archived instead of deleted",
            {"supplier_id": supplier.id, "reason": "protected_financial_history"},
            required=True,
        )
        messages.warning(
            request,
            "This supplier has protected invoice, payment, or receipt history and was archived for AP reconciliation.",
        )
        return redirect("supplier_list")
    _log_action(
        request.user,
        "supplier",
        "Supplier deleted",
        {"supplier_id": pk, "name": supplier_name},
    )
    messages.success(request, f"Supplier '{supplier_name}' removed.")
    return redirect("supplier_list")

@login_required
@role_required(["admin", "manager"])
def supplier_ledger(request, supplier_id):
    supplier_qs = Supplier.objects.filter(owner=_inventory_owner_for_user(request.user))
    supplier = get_object_or_404(supplier_qs, id=supplier_id)
    invoices = list(
        supplier.invoices.select_related("location")
        .prefetch_related("payments__reversal", "adjustments", "refunds")
        .order_by("date_issued", "created_at")
    )

    running_balance = Decimal("0.00")
    total_invoiced = Decimal("0.00")
    total_paid = Decimal("0.00")
    total_adjustments = Decimal("0.00")
    aging_buckets = {
        "Current": {"count": 0, "total": Decimal("0.00")},
        "1-30 Days": {"count": 0, "total": Decimal("0.00")},
        "31-60 Days": {"count": 0, "total": Decimal("0.00")},
        "60+ Days": {"count": 0, "total": Decimal("0.00")},
    }
    for inv in invoices:
        inv.display_invoice_no = f"#{str(inv.invoice_no or '').lstrip('#')}"
        inv.payment_entries = sorted(
            inv.payments.all(),
            key=lambda payment: (payment.payment_date, payment.id),
            reverse=True,
        )
        inv.recorded_paid_amount = inv.total_paid_amount
        inv.recorded_refund_amount = inv.total_refund_amount
        inv.recorded_net_paid_amount = inv.net_paid_amount
        inv.recorded_adjustment_amount = inv.total_credit_amount
        inv.recorded_credit_total = (
            inv.recorded_net_paid_amount + inv.recorded_adjustment_amount
        ).quantize(Decimal("0.01"))
        inv.remaining_balance = inv.balance_due
        total_invoiced += inv.amount or Decimal("0.00")
        total_paid += inv.recorded_net_paid_amount
        total_adjustments += inv.recorded_adjustment_amount
        running_balance += inv.net_balance
        inv.cumulative_balance = running_balance
        inv.aging_label = inv.aging_bucket
        if inv.aging_label in aging_buckets:
            aging_buckets[inv.aging_label]["count"] += 1
            aging_buckets[inv.aging_label]["total"] += inv.remaining_balance

    balance_due = max(running_balance, Decimal("0.00"))
    supplier_credit = max(-running_balance, Decimal("0.00"))
    last_payment = (
        SupplierInvoicePayment.objects.unreversed()
        .filter(owner=supplier.owner, supplier=supplier)
        .order_by("-payment_date", "-id")
        .first()
    )

    if request.GET.get("statement") == "vendor" and request.GET.get("format") == "pdf":
        lines = _supplier_statement_pdf_lines(
            supplier=supplier,
            invoices=invoices,
            total_invoiced=total_invoiced,
            total_paid=total_paid,
            total_adjustments=total_adjustments,
            balance_due=balance_due,
            supplier_credit=supplier_credit,
        )
        response = HttpResponse(_receipt_build_pdf_bytes(lines), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="vendor-statement-{supplier.id}.pdf"'
        return response

    return render(request, 'inventory/supplier_ledger.html', {
        'supplier': supplier,
        'invoices': invoices,
        'total_invoiced': total_invoiced,
        'total_paid': total_paid,
        'total_adjustments': total_adjustments,
        'balance_due': balance_due,
        'supplier_credit': supplier_credit,
        'aging_buckets': aging_buckets,
        'last_payment_date': last_payment.payment_date if last_payment else "No Payments",
        'can_reverse_payments': (
            request.user.is_superuser
            or getattr(getattr(request.user, "profile", None), "role", None) == "admin"
        ),
    })


def _supplier_statement_pdf_lines(
    *,
    supplier,
    invoices,
    total_invoiced,
    total_paid,
    total_adjustments,
    balance_due,
    supplier_credit,
):
    generated_at = timezone.localtime(timezone.now()).strftime("%b %d, %Y %I:%M %p")
    lines = [
        "QuickStock JA",
        f"Vendor Statement: {supplier.name}",
        f"Generated: {generated_at}",
        "",
        f"Total Invoiced: ${Decimal(total_invoiced or 0).quantize(Decimal('0.01'))}",
        f"Net Payments: ${Decimal(total_paid or 0).quantize(Decimal('0.01'))}",
        f"Credits / Voids: ${Decimal(total_adjustments or 0).quantize(Decimal('0.01'))}",
        f"Outstanding Balance: ${Decimal(balance_due or 0).quantize(Decimal('0.01'))}",
        f"Supplier Credit: ${Decimal(supplier_credit or 0).quantize(Decimal('0.01'))}",
        "",
        "Invoice | Issue | Due | Terms | Status | Debit | Credits | Balance",
    ]
    for inv in invoices:
        lines.append(
            " | ".join(
                [
                    str(inv.display_invoice_no or inv.invoice_no or inv.id),
                    inv.date_issued.strftime("%Y-%m-%d") if inv.date_issued else "-",
                    inv.due_date.strftime("%Y-%m-%d") if inv.due_date else "-",
                    inv.get_payment_terms_display() if hasattr(inv, "get_payment_terms_display") else "-",
                    str(inv.status),
                    f"${Decimal(inv.amount or 0).quantize(Decimal('0.01'))}",
                    f"${Decimal(inv.recorded_credit_total or 0).quantize(Decimal('0.01'))}",
                    f"${Decimal(inv.remaining_balance or 0).quantize(Decimal('0.01'))}",
                ]
            )
        )
    return lines


def _refresh_supplier_invoice_financial_state(invoice):
    paid_total = (
        SupplierInvoicePayment.objects.unreversed()
        .filter(invoice=invoice)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    ).quantize(Decimal("0.01"))
    credit_total = (
        SupplierInvoiceAdjustment.objects.filter(invoice=invoice)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    ).quantize(Decimal("0.01"))
    refund_total = (
        SupplierInvoiceRefund.objects.filter(invoice=invoice)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    ).quantize(Decimal("0.01"))
    is_void = SupplierInvoiceAdjustment.objects.filter(
        invoice=invoice,
        adjustment_type=SupplierInvoiceAdjustment.TYPE_VOID_CREDIT,
    ).exists()
    balance = (
        Decimal(invoice.amount or "0.00") - credit_total - paid_total + refund_total
    ).quantize(Decimal("0.01"))
    status = "Void" if is_void else ("Paid" if balance <= Decimal("0.00") else "Pending")

    SupplierInvoice.all_objects.filter(pk=invoice.pk).update(
        paid_amount=paid_total,
        status=status,
    )
    invoice.paid_amount = paid_total
    invoice.status = status
    return balance

# Ensure Location is imported

@login_required
@role_required(["admin", "manager"])
def add_invoice(request, supplier_id):
    supplier_qs = Supplier.objects.filter(owner=_inventory_owner_for_user(request.user))
    supplier = get_object_or_404(supplier_qs, id=supplier_id)
    # 1. FETCH LOCATIONS FOR THE DROPDOWN
    locations = _location_queryset_for_user(request.user).order_by('name')

    if request.method == "POST":
        invoice_no = request.POST.get("invoice_no")
        amount = _safe_decimal(request.POST.get("amount", "0") or "0")
        raw_terms = request.POST.get("payment_terms") or request.POST.get("status") or SupplierInvoice.TERMS_DUE_ON_RECEIPT
        status = "Paid" if raw_terms in {"Paid", "paid_now"} else "Pending"
        payment_terms = raw_terms if raw_terms in SupplierInvoice.PAYMENT_TERM_DAYS else SupplierInvoice.TERMS_DUE_ON_RECEIPT

        # Accept both "location_id" and legacy "location" form fields.
        raw_location = request.POST.get("location_id") or request.POST.get("location") or ""
        location_id = None
        if raw_location.strip():
            try:
                location_id = int(raw_location)
            except (TypeError, ValueError):
                messages.error(request, "Invalid location selected.")
                return render(
                    request,
                    "inventory/add_invoice.html",
                    {
                        "supplier": supplier,
                        "locations": locations,
                        "payment_term_choices": SupplierInvoice.PAYMENT_TERM_CHOICES,
                        "today": timezone.now().date(),
                    },
                )
   
        date_issued = request.POST.get("date_issued")

        # VALIDATION
        if amount <= 0:
            messages.error(request, "Amount must be greater than zero.")
            return render(
                request,
                "inventory/add_invoice.html",
                {
                    "supplier": supplier, 
                    "locations": locations, # Pass back on error
                    "payment_term_choices": SupplierInvoice.PAYMENT_TERM_CHOICES,
                    "today": timezone.now().date()
                },
            )

        # DATE PARSING
        if date_issued:
            try:
                date_issued = datetime.fromisoformat(date_issued).date()
            except ValueError:
                date_issued = timezone.now().date()
        else:
            date_issued = timezone.now().date()

        # 2. CREATE INVOICE WITH LOCATION MAPPING (optional)
        location = None
        if location_id:
            try:
                location = _get_location_for_user(request.user, location_id)
            except Exception:
                messages.error(request, "Selected location is not available.")
                return render(
                    request,
                    "inventory/add_invoice.html",
                    {
                        "supplier": supplier,
                        "locations": locations,
                        "payment_term_choices": SupplierInvoice.PAYMENT_TERM_CHOICES,
                        "today": timezone.now().date(),
                    },
                )
        # Invoice number: keep user entry if unique; else generate global sequence
        invoice_no_clean = (invoice_no or "").strip()
        invoice_no_clean = invoice_no_clean.lstrip("#").strip()
        if invoice_no_clean and not invoice_no_clean.upper().startswith("INV-"):
            invoice_no_clean = f"INV-{invoice_no_clean}"

        def _next_global_seq():
            """
            Find the next global invoice sequence based on existing invoice_no patterns.
            Resets to 1 only when there are no invoices (e.g., after all invoices deleted).
            """
            max_seq = 0
            for inv in SupplierInvoice.objects.values_list("invoice_no", flat=True):
                if not inv:
                    continue
                inv_str = str(inv).lstrip("#")
                parts = inv_str.split("-")
                if parts and parts[-1].isdigit():
                    try:
                        max_seq = max(max_seq, int(parts[-1]))
                    except ValueError:
                        continue
            return max_seq + 1

        candidate = f"#{invoice_no_clean}" if invoice_no_clean else ""
        if (not invoice_no_clean) or SupplierInvoice.objects.filter(invoice_no=candidate).exists():
            seq = _next_global_seq()
            invoice_no_clean = f"#INV-{seq:06d}"
        else:
            invoice_no_clean = candidate

        with transaction.atomic():
            invoice = SupplierInvoice.objects.create(
                supplier=supplier,
                location=location,
                invoice_no=invoice_no_clean,
                amount=amount,
                paid_amount=Decimal("0.00"),
                status="Pending",
                date_issued=date_issued,
                payment_terms=payment_terms,
            )
            initial_payment = None
            if status == "Paid":
                initial_payment = SupplierInvoicePayment.objects.create(
                    invoice=invoice,
                    paid_by=request.user,
                    amount=amount,
                    notes="Initial payment recorded with supplier invoice.",
                )
                _refresh_supplier_invoice_financial_state(invoice)
                _log_action(
                    request.user,
                    "supplier_payment",
                    "Supplier invoice payment recorded",
                    {
                        "invoice_id": invoice.id,
                        "payment_id": initial_payment.id,
                        "supplier_id": supplier.id,
                        "owner_id": initial_payment.owner_id,
                        "location_id": initial_payment.location_id,
                        "amount": str(initial_payment.amount),
                    },
                    required=True,
                )

            _log_action(
                request.user,
                "invoice",
                "Supplier invoice created",
                {
                    "invoice_id": invoice.id,
                    "supplier_id": supplier.id,
                    "location_id": getattr(location, "id", None),
                    "amount": str(amount),
                    "status": invoice.status,
                    "payment_terms": invoice.payment_terms,
                    "due_date": invoice.due_date.isoformat() if invoice.due_date else None,
                    "initial_payment_id": getattr(initial_payment, "id", None),
                },
                required=True,
            )
        messages.success(request, f"Invoice {invoice_no or ''} committed to ledger.")
        return redirect("supplier_ledger", supplier_id=supplier.id)

    # 3. CONTEXT FOR INITIAL PAGE LOAD
    return render(
        request,
        "inventory/add_invoice.html",
        {
            "supplier": supplier, 
            "locations": locations, # The vital piece
            "payment_term_choices": SupplierInvoice.PAYMENT_TERM_CHOICES,
            "today": timezone.now().date()
        },
    )

@login_required
@role_required(["admin", "manager"])
def invoice_detail(request, invoice_id):
    invoice_qs = (
        SupplierInvoice.objects.select_related("supplier", "location")
        .prefetch_related("payments__reversal", "adjustments")
        .filter(supplier__owner=_inventory_owner_for_user(request.user))
    )
    invoice = get_object_or_404(invoice_qs, id=invoice_id)
    owner_profile = UserProfile.for_user(invoice.supplier.owner)
    return render(
        request,
        "inventory/invoice_detail.html",
        {
            "invoice": invoice,
            "profile": owner_profile,
            "currency_code": "JMD",
        },
    )


@login_required
@role_required(["admin", "manager"])
@require_POST
def mark_invoice_paid(request, invoice_id):
    invoice_qs = SupplierInvoice.objects.select_related("supplier").filter(
        supplier__owner=_inventory_owner_for_user(request.user)
    )
    invoice = get_object_or_404(invoice_qs, id=invoice_id)
    payment_raw = str(
        request.POST.get("payment_amount") or request.POST.get("amount") or ""
    ).strip()
    try:
        payment_amount = _safe_decimal(payment_raw or invoice.balance_due)
    except Exception:
        messages.error(request, "Enter a valid payment amount.")
        return redirect("supplier_ledger", supplier_id=invoice.supplier.id)

    payment_amount = payment_amount.quantize(Decimal("0.01"))
    if payment_amount <= 0:
        messages.error(request, "Payment amount must be greater than $0.00.")
        return redirect("supplier_ledger", supplier_id=invoice.supplier.id)

    with transaction.atomic():
        locked_invoice = get_object_or_404(
            invoice_qs.select_for_update(),
            pk=invoice.pk,
        )
        _refresh_supplier_invoice_financial_state(locked_invoice)
        if locked_invoice.status == "Void":
            messages.error(request, "A voided supplier invoice cannot accept payments.")
            return redirect("supplier_ledger", supplier_id=locked_invoice.supplier.id)

        remaining = locked_invoice.balance_due
        if remaining <= Decimal("0.00"):
            messages.error(request, "This invoice is already fully paid.")
            return redirect("supplier_ledger", supplier_id=locked_invoice.supplier.id)
        if payment_amount > remaining:
            messages.error(
                request,
                f"Overpayment detected: remaining balance is ${remaining:.2f}.",
            )
            return redirect("supplier_ledger", supplier_id=locked_invoice.supplier.id)

        payment = SupplierInvoicePayment.objects.create(
            invoice=locked_invoice,
            paid_by=request.user,
            amount=payment_amount,
            reference=(request.POST.get("reference") or "").strip(),
            notes=(request.POST.get("notes") or "").strip(),
        )
        remaining_after = _refresh_supplier_invoice_financial_state(locked_invoice)
        _log_action(
            request.user,
            "supplier_payment",
            "Supplier invoice payment recorded",
            {
                "invoice_id": locked_invoice.id,
                "payment_id": payment.id,
                "supplier_id": locked_invoice.supplier.id,
                "owner_id": payment.owner_id,
                "location_id": payment.location_id,
                "amount": str(payment.amount),
                "reference": payment.reference,
            },
            required=True,
        )

    if locked_invoice.status == "Paid":
        messages.success(request, "Payment applied. Invoice marked as paid.")
    else:
        messages.success(request, f"Partial payment recorded. Remaining balance: ${remaining_after:.2f}.")
    return redirect("supplier_ledger", supplier_id=invoice.supplier.id)


@login_required
@role_required(["admin", "manager"])
@require_POST
def undo_invoice_payment(request, invoice_id):
    invoice_qs = SupplierInvoice.objects.select_related("supplier").filter(
        supplier__owner=_inventory_owner_for_user(request.user)
    )
    invoice = get_object_or_404(invoice_qs, id=invoice_id)
    messages.error(
        request,
        "Destructive undo is disabled. Reverse the specific posted payment and enter a reason.",
    )
    return redirect("supplier_ledger", supplier_id=invoice.supplier.id)


@login_required
@role_required(["admin"])
@require_POST
def reverse_supplier_invoice_payment(request, invoice_id, payment_id):
    owner_user = _inventory_owner_for_user(request.user)
    invoice_qs = SupplierInvoice.objects.select_related("supplier").filter(
        supplier__owner=owner_user
    )
    invoice = get_object_or_404(invoice_qs, pk=invoice_id)
    reason = (request.POST.get("reversal_reason") or "").strip()
    if not reason:
        messages.error(request, "Enter a reason before reversing this supplier payment.")
        return redirect("supplier_ledger", supplier_id=invoice.supplier.id)

    try:
        with transaction.atomic():
            locked_invoice = get_object_or_404(
                invoice_qs.select_for_update(),
                pk=invoice.pk,
            )
            payment_qs = SupplierInvoicePayment.objects.select_for_update().select_related(
                "invoice",
                "supplier",
                "location",
            )
            if not request.user.is_superuser:
                payment_qs = payment_qs.filter(owner=owner_user)
            payment = get_object_or_404(
                payment_qs,
                pk=payment_id,
                invoice=locked_invoice,
                supplier=locked_invoice.supplier,
            )
            if SupplierInvoicePaymentReversal.objects.filter(payment=payment).exists():
                messages.info(request, "This supplier payment has already been reversed.")
                return redirect("supplier_ledger", supplier_id=locked_invoice.supplier.id)

            reversal = SupplierInvoicePaymentReversal.objects.create(
                payment=payment,
                reversed_by=request.user,
                reason=reason,
            )
            remaining_after = _refresh_supplier_invoice_financial_state(locked_invoice)
            _log_action(
                request.user,
                "supplier_payment",
                "Supplier invoice payment reversed",
                {
                    "invoice_id": locked_invoice.id,
                    "payment_id": payment.id,
                    "reversal_id": reversal.id,
                    "supplier_id": payment.supplier_id,
                    "owner_id": payment.owner_id,
                    "location_id": payment.location_id,
                    "amount": str(payment.amount),
                    "reason": reason,
                    "remaining_balance": str(max(remaining_after, Decimal("0.00"))),
                },
                severity="warn",
                required=True,
            )
    except IntegrityError:
        messages.info(request, "This supplier payment has already been reversed.")
        return redirect("supplier_ledger", supplier_id=invoice.supplier.id)

    messages.success(request, f"Supplier payment of ${payment.amount:.2f} reversed.")
    return redirect("supplier_ledger", supplier_id=invoice.supplier.id)


@login_required
@role_required(["admin", "manager"])
@require_POST
def void_invoice(request, invoice_id):
    invoice_qs = SupplierInvoice.objects.select_related("supplier").filter(
        supplier__owner=_inventory_owner_for_user(request.user)
    )
    reason = request.POST.get("void_reason", "").strip()
    invoice = get_object_or_404(invoice_qs, id=invoice_id)
    if not reason:
        messages.error(request, "Enter a reason before voiding this supplier invoice.")
        return redirect("supplier_ledger", supplier_id=invoice.supplier.id)

    try:
        with transaction.atomic():
            locked_invoice = get_object_or_404(
                invoice_qs.select_for_update(),
                pk=invoice.pk,
            )
            if SupplierInvoiceAdjustment.objects.filter(
                invoice=locked_invoice,
                adjustment_type=SupplierInvoiceAdjustment.TYPE_VOID_CREDIT,
            ).exists():
                messages.info(request, "This supplier invoice has already been voided.")
                return redirect("supplier_ledger", supplier_id=locked_invoice.supplier.id)

            adjustment = SupplierInvoiceAdjustment.objects.create(
                invoice=locked_invoice,
                adjustment_type=SupplierInvoiceAdjustment.TYPE_VOID_CREDIT,
                amount=locked_invoice.amount,
                reason=reason,
                created_by=request.user,
            )
            SupplierInvoice.all_objects.filter(pk=locked_invoice.pk).update(
                void_reason=reason,
                voided_at=timezone.now(),
                voided_by=request.user,
            )
            _refresh_supplier_invoice_financial_state(locked_invoice)
            _log_action(
                request.user,
                "supplier_invoice",
                "Supplier invoice voided with compensating adjustment",
                {
                    "invoice_id": locked_invoice.id,
                    "supplier_id": locked_invoice.supplier.id,
                    "adjustment_id": adjustment.id,
                    "adjustment_amount": str(adjustment.amount),
                    "unreversed_payment_total": str(locked_invoice.paid_amount),
                    "reason": reason,
                },
                severity="warn",
                required=True,
            )
    except IntegrityError:
        messages.info(request, "This supplier invoice has already been voided.")
        return redirect("supplier_ledger", supplier_id=invoice.supplier.id)

    messages.info(request, "Invoice voided with a durable compensating adjustment; payment history was preserved.")
    return redirect("supplier_ledger", supplier_id=invoice.supplier.id)


@login_required
@role_required(["admin"])
@require_POST
def supplier_invoice_credit_note(request, invoice_id):
    invoice_qs = SupplierInvoice.objects.select_related("supplier").filter(
        supplier__owner=_inventory_owner_for_user(request.user)
    )
    invoice = get_object_or_404(invoice_qs, pk=invoice_id)
    reason = (request.POST.get("credit_reason") or "").strip()
    try:
        amount = _safe_decimal(request.POST.get("credit_amount", "0"))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0.00")
    if not reason or amount <= Decimal("0.00"):
        messages.error(request, "A positive credit amount and reason are required.")
        return redirect("supplier_ledger", supplier_id=invoice.supplier.id)

    try:
        with transaction.atomic():
            locked_invoice = get_object_or_404(invoice_qs.select_for_update(), pk=invoice.pk)
            if locked_invoice.status == "Void":
                messages.error(request, "A voided supplier invoice cannot receive another credit note.")
                return redirect("supplier_ledger", supplier_id=locked_invoice.supplier.id)
            remaining_credit = max(
                Decimal(locked_invoice.amount or "0.00") - locked_invoice.total_credit_amount,
                Decimal("0.00"),
            )
            if amount > remaining_credit:
                messages.error(request, f"Credit cannot exceed the remaining invoice value of ${remaining_credit:.2f}.")
                return redirect("supplier_ledger", supplier_id=locked_invoice.supplier.id)
            adjustment = SupplierInvoiceAdjustment.objects.create(
                invoice=locked_invoice,
                adjustment_type=SupplierInvoiceAdjustment.TYPE_SUPPLIER_CREDIT,
                amount=amount,
                reason=reason,
                created_by=request.user,
            )
            remaining_after = _refresh_supplier_invoice_financial_state(locked_invoice)
            _log_action(
                request.user,
                "supplier_invoice",
                "Supplier credit note recorded",
                {
                    "invoice_id": locked_invoice.id,
                    "supplier_id": locked_invoice.supplier_id,
                    "adjustment_id": adjustment.id,
                    "amount": str(adjustment.amount),
                    "remaining_balance": str(max(remaining_after, Decimal("0.00"))),
                    "reason": reason,
                },
                required=True,
            )
    except IntegrityError:
        messages.info(request, "The supplier credit note was already recorded.")
        return redirect("supplier_ledger", supplier_id=invoice.supplier.id)

    messages.success(request, "Supplier credit note recorded as an immutable ledger movement.")
    return redirect("supplier_ledger", supplier_id=invoice.supplier.id)


@login_required
@role_required(["admin"])
@require_POST
def supplier_invoice_refund(request, invoice_id):
    invoice_qs = SupplierInvoice.objects.select_related("supplier").filter(
        supplier__owner=_inventory_owner_for_user(request.user)
    )
    invoice = get_object_or_404(invoice_qs, pk=invoice_id)
    reason = (request.POST.get("refund_reason") or "").strip()
    reference = (request.POST.get("refund_reference") or "").strip()
    try:
        amount = _safe_decimal(request.POST.get("refund_amount", "0"))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0.00")
    if not reason or not reference or amount <= Decimal("0.00"):
        messages.error(request, "A positive refund amount, reference, and reason are required.")
        return redirect("supplier_ledger", supplier_id=invoice.supplier.id)

    try:
        with transaction.atomic():
            locked_invoice = get_object_or_404(invoice_qs.select_for_update(), pk=invoice.pk)
            if SupplierInvoiceRefund.objects.filter(invoice=locked_invoice, reference=reference).exists():
                messages.info(request, "This supplier refund reference has already been recorded.")
                return redirect("supplier_ledger", supplier_id=locked_invoice.supplier.id)
            refundable = locked_invoice.total_paid_amount - locked_invoice.total_refund_amount
            if amount > refundable:
                messages.error(request, f"Refund cannot exceed the refundable paid balance of ${refundable:.2f}.")
                return redirect("supplier_ledger", supplier_id=locked_invoice.supplier.id)
            refund = SupplierInvoiceRefund.objects.create(
                invoice=locked_invoice,
                received_by=request.user,
                amount=amount,
                reference=reference,
                reason=reason,
            )
            remaining_after = _refresh_supplier_invoice_financial_state(locked_invoice)
            _log_action(
                request.user,
                "supplier_invoice",
                "Supplier refund recorded",
                {
                    "invoice_id": locked_invoice.id,
                    "supplier_id": locked_invoice.supplier_id,
                    "refund_id": refund.id,
                    "amount": str(refund.amount),
                    "reference": reference,
                    "remaining_balance": str(max(remaining_after, Decimal("0.00"))),
                    "reason": reason,
                },
                severity="warn",
                required=True,
            )
    except IntegrityError:
        messages.info(request, "This supplier refund reference has already been recorded.")
        return redirect("supplier_ledger", supplier_id=invoice.supplier.id)

    messages.success(request, "Supplier refund recorded as an immutable ledger movement.")
    return redirect("supplier_ledger", supplier_id=invoice.supplier.id)


# ---------------------------
# Premium / Downloads
# ---------------------------

@login_required
def premium_feature(request):
    return render(request, "inventory/premium.html")


@login_required
@pro_required
def download_software(request):
    download_url = (getattr(settings, "DOWNLOAD_SOFTWARE_URL", "") or "").strip()
    if download_url:
        return redirect(download_url)
    raise Http404("Software download is not available.")



from .models import CashShift


def _safe_next_url(request, fallback_name):
    fallback_url = reverse(fallback_name)
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback_url

@login_required
@role_required(["cashier", "manager", "admin"])
def close_shift_view(request, shift_id):
    owner_user = _inventory_owner_for_user(request.user)
    shift_qs = CashShift.objects.filter(pk=shift_id)
    if not request.user.is_superuser:
        shift_qs = shift_qs.filter(Q(owner=owner_user) | Q(cashier=request.user))
    shift = get_object_or_404(shift_qs)

    if request.method == "POST":
        try:
            actual_count = request.POST.get("actual_cash")
            if actual_count in (None, ""):
                raise ValidationError("Enter counted cash before closing the shift.")
            raw_denominations = (request.POST.get("denominations") or "").strip()
            denominations = json.loads(raw_denominations) if raw_denominations else None
            if denominations is not None and not isinstance(denominations, dict):
                raise ValidationError("Cash denominations must be supplied as an object.")
            shift = shift.close_shift(
                actual_count,
                actor=request.user,
                notes=(request.POST.get("notes") or "").strip(),
                denominations=denominations,
            )

            # 3. Success Message with Shortage/Over Alert
            diff = shift.actual_cash - shift.expected_cash
            if diff < 0:
                messages.warning(request, f"Shift Closed. Warning: Shortage of ${abs(diff):,.2f} JMD.")
            elif diff > 0:
                messages.info(request, f"Shift Closed. Drawer is Over by ${diff:,.2f} JMD.")
            else:
                messages.success(request, "Shift Closed. Drawer balanced perfectly!")

            return redirect('settings')
            
        except (ValidationError, TypeError, ValueError, InvalidOperation) as exc:
            messages.error(request, str(exc))
            
    return redirect('cash_register')


@login_required
@role_required(["cashier", "manager", "admin"])
@require_POST
def cash_shift_reconcile(request, shift_id):
    """Record one immutable counted-cash snapshot for an open shift."""
    owner_user = _inventory_owner_for_user(request.user)
    shift_qs = CashShift.objects.filter(pk=shift_id)
    if not request.user.is_superuser:
        shift_qs = shift_qs.filter(Q(owner=owner_user) | Q(cashier=request.user))
    shift = get_object_or_404(shift_qs)
    raw_denominations = (request.POST.get("denominations") or "").strip()
    try:
        denominations = json.loads(raw_denominations) if raw_denominations else None
        if denominations is not None and not isinstance(denominations, dict):
            raise ValidationError("Cash denominations must be supplied as an object.")
        with transaction.atomic():
            existing = CashReconciliation.objects.filter(shift=shift).first()
            reconciliation = shift.reconcile(
                request.POST.get("counted_cash"),
                actor=request.user,
                denominations=denominations,
                explanation=(request.POST.get("explanation") or "").strip(),
            )
            if existing is None:
                _log_action(
                    request.user,
                    "register",
                    "Cash shift reconciliation recorded",
                    {
                        "shift_id": shift.id,
                        "reconciliation_id": reconciliation.id,
                        "expected_cash": str(reconciliation.expected_cash),
                        "counted_cash": str(reconciliation.counted_cash),
                        "variance": str(reconciliation.variance),
                    },
                    severity="warn" if reconciliation.variance else "info",
                    required=True,
                )
        return JsonResponse(
            {
                "ok": True,
                "reconciliation_id": reconciliation.id,
                "expected_cash": str(reconciliation.expected_cash),
                "counted_cash": str(reconciliation.counted_cash),
                "variance": str(reconciliation.variance),
                "idempotent_replay": existing is not None,
            }
        )
    except (ValidationError, TypeError, ValueError, InvalidOperation) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)


@login_required
@role_required(["cashier", "manager", "admin"])
@require_POST
def cash_shift_movement(request, shift_id):
    """Append an immutable cash-in, cash-out, or refund movement."""
    owner_user = _inventory_owner_for_user(request.user)
    shift_qs = CashShift.objects.filter(pk=shift_id)
    if not request.user.is_superuser:
        shift_qs = shift_qs.filter(Q(owner=owner_user) | Q(cashier=request.user))
    shift = get_object_or_404(shift_qs)
    try:
        amount = Decimal(str(request.POST.get("amount"))).quantize(Decimal("0.01"))
        direction = (request.POST.get("direction") or "").strip().lower()
        movement_type = (request.POST.get("movement_type") or "cash_out").strip().lower()
        if direction not in {CashMovement.DIRECTION_IN, CashMovement.DIRECTION_OUT}:
            raise ValidationError("Cash movement direction must be in or out.")
        if movement_type not in {choice[0] for choice in CashMovement.TYPE_CHOICES}:
            raise ValidationError("Cash movement type is invalid.")
        if movement_type == CashMovement.TYPE_REFUND:
            direction = CashMovement.DIRECTION_OUT
        reference = (request.POST.get("reference") or "").strip()[:120]
        reason = (request.POST.get("reason") or "").strip()
        with transaction.atomic():
            locked_shift = CashShift.objects.select_for_update().get(pk=shift.pk)
            locked_shift._authorized_actor(request.user)
            existing = None
            if reference:
                existing = CashMovement.objects.filter(
                    owner=locked_shift.owner,
                    shift=locked_shift,
                    reference=reference,
                ).first()
            if existing:
                return JsonResponse(
                    {"ok": True, "movement_id": existing.id, "idempotent_replay": True}
                )
            movement = CashMovement.objects.create(
                shift=locked_shift,
                owner=locked_shift.owner,
                location=locked_shift.location,
                recorded_by=request.user,
                direction=direction,
                movement_type=movement_type,
                amount=amount,
                reason=reason,
                reference=reference,
            )
            locked_shift.calculate_expected_balance(force=True)
            locked_shift.save(
                update_fields=["total_sales", "expected_cash", "last_calculated_at"],
                _allow_integrity_update=True,
            )
            _log_action(
                request.user,
                "register",
                "Cash movement recorded",
                {
                    "shift_id": locked_shift.id,
                    "movement_id": movement.id,
                    "direction": movement.direction,
                    "movement_type": movement.movement_type,
                    "amount": str(movement.amount),
                    "reference": movement.reference,
                },
                required=True,
            )
        return JsonResponse({"ok": True, "movement_id": movement.id})
    except (ValidationError, TypeError, ValueError, InvalidOperation, IntegrityError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=409 if isinstance(exc, IntegrityError) else 400)


def _resolve_sales_invoice_location(user, raw_location_id=None):
    owner_user = _inventory_owner_for_user(user)
    if not owner_user:
        return None

    if raw_location_id:
        try:
            return _get_location_for_user(user, int(raw_location_id))
        except Exception:
            return None

    profile = UserProfile.for_user(user)
    default_location = getattr(profile, "default_location", None)
    if default_location:
        return default_location

    return _get_main_store(owner_user)


def _create_sales_invoice_from_quotation(user, owner_user, quote_id, selected_location):
    """
    Convert a draft sales quotation to an issued sales invoice.
    Returns: (invoice, created_new, quotation)
    """
    quotation_qs = SalesQuotation.objects.select_related("customer")
    if not owner_user.is_superuser:
        quotation_qs = quotation_qs.filter(owner=owner_user)
    quotation = get_object_or_404(quotation_qs, pk=quote_id)
    _expire_stale_sales_quotations(owner_user)

    with transaction.atomic():
        locked_quote = (
            SalesQuotation.objects.select_for_update()
            .select_related("customer")
            .get(pk=quotation.pk)
        )

        if (
            locked_quote.status == "draft"
            and locked_quote.valid_until
            and locked_quote.valid_until < timezone.localdate()
        ):
            locked_quote.status = "expired"
            locked_quote.save(update_fields=["status"])

        if locked_quote.status not in {"draft", "converted"}:
            raise ValueError("Only draft or converted quotations can be invoiced.")

        quote_items = list(
            SalesQuotationItem.objects.select_related("item")
            .filter(quotation=locked_quote)
            .order_by("id")
        )
        if not quote_items:
            raise ValueError("Quotation has no items to convert.")

        required_qty_by_item = {}
        for line in quote_items:
            required_qty_by_item[line.item_id] = required_qty_by_item.get(line.item_id, 0) + int(line.quantity or 0)

        item_ids = list(required_qty_by_item.keys())
        items_qs = _item_queryset_for_user(owner_user).select_for_update().filter(id__in=item_ids)
        item_map = {item.id: item for item in items_qs}

        stock_map = {
            stock.item_id: stock
            for stock in StockRecord.objects.select_for_update().filter(
                item_id__in=item_ids,
                location=selected_location,
            )
        }

        for item_id, required_qty in required_qty_by_item.items():
            item = item_map.get(item_id)
            if not item:
                item_name = next((ln.item_name for ln in quote_items if ln.item_id == item_id), "Selected item")
                raise ValueError(f"{item_name} is no longer in inventory.")

            if int(item.total_quantity or 0) < required_qty:
                raise ValueError(
                    f"Insufficient stock for {item.name}. Available: {item.total_quantity}, required: {required_qty}."
                )

            stock = stock_map.get(item_id)
            if stock and int(stock.quantity or 0) < required_qty:
                raise ValueError(f"{item.name} has only {stock.quantity} unit(s) in {selected_location.name}.")

        invoice = SalesInvoice.objects.create(
            owner=owner_user,
            created_by=user,
            quotation=locked_quote,
            customer=locked_quote.customer,
            location=selected_location,
            status="issued",
            due_date=locked_quote.valid_until,
            notes=locked_quote.notes,
            subtotal=locked_quote.subtotal,
            tax_amount=locked_quote.tax_amount,
            total_amount=locked_quote.total_amount,
        )

        for line in quote_items:
            SalesInvoiceItem.objects.create(
                invoice=invoice,
                item=line.item,
                item_name=line.item_name or line.item.name,
                quantity=line.quantity,
                unit_price=line.unit_price,
                line_total=line.line_total,
            )

        for item_id, required_qty in required_qty_by_item.items():
            # Item quantity is managed via StockRecord relations; no denormalized field on Item.

            stock = stock_map.get(item_id)
            if stock:
                stock.quantity = max(0, int(stock.quantity or 0) - required_qty)
                stock.save(update_fields=["quantity"])

        update_fields = []
        if locked_quote.status != "converted":
            locked_quote.status = "converted"
            update_fields.append("status")
        if locked_quote.converted_at is None:
            locked_quote.converted_at = timezone.now()
            update_fields.append("converted_at")
        if update_fields:
            locked_quote.save(update_fields=update_fields)

    return invoice, True, locked_quote


def _sales_document_email_context(document, document_kind, recipient_email=""):
    """Build the shared customer-facing context for invoice and quotation email."""
    if document_kind not in {"invoice", "quotation"}:
        raise ValueError("Unsupported sales document type.")

    is_invoice = document_kind == "invoice"
    owner_profile = UserProfile.for_user(document.owner)
    location = document.location if is_invoice else owner_profile.default_location
    summary = _document_summary_context(
        document.notes,
        document.subtotal,
        document.tax_amount,
        document.total_amount,
        _get_tax_rate_for_location(location),
    )
    document_items = list(document.items.select_related("item").all())
    line_items = []
    for line_item in document_items:
        unit_price = _receipt_money(line_item.unit_price)
        line_total = _receipt_money(line_item.line_total)
        line_items.append(
            {
                "quantity": line_item.quantity,
                "item_name": line_item.item_name or getattr(line_item.item, "name", "Item"),
                "unit_price_display": f"${unit_price:.2f}",
                "line_total_display": f"${line_total:.2f}",
            }
        )

    document_date_value = document.issued_at if is_invoice else document.created_at
    if timezone.is_aware(document_date_value):
        document_date_value = timezone.localtime(document_date_value)
    expiry_date = document.due_date if is_invoice else document.valid_until
    expiry_label = "Due Date" if is_invoice else "Valid Until"

    customer = document.customer
    brand_name = re.sub(
        r"[\r\n]+",
        " ",
        (owner_profile.receipt_brand_name or "QuickStock JA").strip(),
    ).strip() or "QuickStock JA"
    subtotal = _receipt_money(document.subtotal)
    discount_amount = _receipt_money(summary["discount_amount"])
    tax_amount = _receipt_money(document.tax_amount)
    total_amount = _receipt_money(document.total_amount)
    paid_total = Decimal("0.00")
    balance_due = Decimal("0.00")
    effective_total = total_amount
    if is_invoice:
        paid_total, balance_due = _sales_invoice_payment_totals(document)
        effective_total = _receipt_money(_sales_invoice_effective_total(document))

    document_label = "Invoice" if is_invoice else "Quotation"
    document_number = document.invoice_no if is_invoice else document.quote_no
    return {
        "brand_name": brand_name,
        "brand_address": (owner_profile.receipt_address or getattr(location, "address", "") or "").strip(),
        "brand_phone": (owner_profile.receipt_contact_phone or "").strip(),
        "brand_email": (owner_profile.receipt_contact_email or "").strip(),
        "recipient_email": recipient_email,
        "customer_name": getattr(customer, "name", "") or "Valued customer",
        "customer_email": getattr(customer, "email", "") or "",
        "document_kind": document_kind,
        "document_label": document_label,
        "document_number": document_number,
        "status_display": document.get_status_display(),
        "document_date": document_date_value.strftime("%B %d, %Y at %I:%M %p"),
        "expiry_date": expiry_date,
        "expiry_label": expiry_label,
        "expiry_date_display": expiry_date.strftime("%B %d, %Y") if expiry_date else "",
        "valid_until": document.valid_until if not is_invoice else None,
        "due_date": document.due_date if is_invoice else None,
        "location_name": getattr(location, "name", "") or "",
        "line_items": line_items,
        "subtotal_display": f"${subtotal:.2f}",
        "discount_display": f"${discount_amount:.2f}",
        "show_discount": discount_amount > Decimal("0.00"),
        "tax_label": _get_tax_label_for_location(location),
        "tax_amount_display": f"${tax_amount:.2f}",
        "total_display": f"${total_amount:.2f}",
        "effective_total_display": f"${effective_total:.2f}",
        "show_effective_total": is_invoice and effective_total != total_amount,
        "paid_total_display": f"${_receipt_money(paid_total):.2f}",
        "balance_due_display": f"${_receipt_money(balance_due):.2f}",
        "show_payment_summary": is_invoice,
        "notes_display": summary["notes_display"],
    }


def _sales_document_email_pdf_lines(context):
    lines = [
        context["brand_name"],
        context.get("brand_address") or "",
        " ".join(
            part
            for part in [context.get("brand_phone") or "", context.get("brand_email") or ""]
            if part
        ).strip(),
        "",
        f"{context['document_label']} {context['document_number']}",
        f"Status: {context['status_display']}",
        f"Date: {context['document_date']}",
        f"Customer: {context['customer_name']}",
    ]
    if context.get("expiry_date"):
        lines.append(f"{context['expiry_label']}: {context['expiry_date'].strftime('%B %d, %Y')}")
    if context.get("location_name"):
        lines.append(f"Location: {context['location_name']}")

    lines.append("")
    for line_item in context["line_items"]:
        lines.append(
            f"{line_item['quantity']} x {line_item['item_name']}  {line_item['line_total_display']}"
        )
        lines.append(f"    @ {line_item['unit_price_display']} each")

    lines.extend(["", f"Subtotal: {context['subtotal_display']}"])
    if context.get("show_discount"):
        lines.append(f"Discount: {context['discount_display']}")
    lines.extend(
        [
            f"{context['tax_label']}: {context['tax_amount_display']}",
            f"Total: {context['total_display']}",
        ]
    )
    if context.get("show_effective_total"):
        lines.append(f"Net Total After Credit Notes: {context['effective_total_display']}")
    if context.get("show_payment_summary"):
        lines.extend(
            [
                f"Paid: {context['paid_total_display']}",
                f"Balance Due: {context['balance_due_display']}",
            ]
        )
    if context.get("notes_display"):
        lines.extend(["", "Notes:", context["notes_display"]])
    return [line for line in lines if line is not None]


def _send_sales_document_email(document, document_kind, recipient_email):
    context = _sales_document_email_context(document, document_kind, recipient_email)
    subject = f"{context['brand_name']} {context['document_label']} {context['document_number']}"
    email = EmailMessage(
        subject=subject,
        body=render_to_string("inventory/sales_document_email.html", context),
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[recipient_email],
    )
    email.content_subtype = "html"
    email.attach(
        f"{document_kind}-{context['document_number']}.pdf",
        _receipt_build_pdf_bytes(_sales_document_email_pdf_lines(context)),
        "application/pdf",
    )
    if email.send(fail_silently=False) < 1:
        raise RuntimeError("The email backend did not accept the document email.")
    return context


def _expire_stale_sales_quotations(owner_user=None):
    """Move unconverted draft quotes past their validity date into expired state."""
    today = timezone.localdate()
    stale_quotes = SalesQuotation.objects.filter(
        status="draft",
        valid_until__isnull=False,
        valid_until__lt=today,
    )
    if owner_user is not None and not owner_user.is_superuser:
        stale_quotes = stale_quotes.filter(owner=owner_user)
    return stale_quotes.update(status="expired")


def _sales_quotation_line_initial_data(quotation):
    if not quotation:
        return []
    return [
        {
            "item_id": line.item_id,
            "quantity": int(line.quantity or 1),
            "unit_price": str((line.unit_price or Decimal("0.00")).quantize(Decimal("0.01"))),
        }
        for line in quotation.items.select_related("item").order_by("id")
    ]


def _sales_quotation_form_items(user, quotation=None):
    items = _item_queryset_for_user(user)
    if quotation:
        quotation_item_ids = list(quotation.items.values_list("item_id", flat=True))
        if quotation_item_ids:
            return items.filter(Q(status="active") | Q(id__in=quotation_item_ids)).order_by("name")
    return items.filter(status="active").order_by("name")


def _sales_quotation_payload_from_request(request, owner_user, customers, items, default_tax_rate):
    customer_id = (request.POST.get("customer_id") or "").strip()
    customer_name = (request.POST.get("customer_name") or "").strip()
    valid_until_raw = (request.POST.get("valid_until") or "").strip()
    notes = (request.POST.get("notes") or "").strip()

    customer = None
    if customer_id:
        customer = get_object_or_404(customers, pk=customer_id)
    elif customer_name:
        customer = Customer.objects.filter(
            owner=owner_user,
            name__iexact=customer_name,
        ).first()
        if not customer:
            customer = Customer.objects.create(
                owner=owner_user,
                name=customer_name,
            )

    valid_until = None
    if valid_until_raw:
        try:
            valid_until = datetime.fromisoformat(valid_until_raw).date()
        except ValueError as exc:
            raise ValueError("Invalid quote expiry date.") from exc

    item_ids = request.POST.getlist("item_id[]") or request.POST.getlist("item_id")
    quantities = request.POST.getlist("quantity[]") or request.POST.getlist("quantity")
    unit_prices = request.POST.getlist("unit_price[]") or request.POST.getlist("unit_price")

    if not item_ids:
        raise ValueError("Add at least one item to the quotation.")

    item_id_set = set()
    for raw_id in item_ids:
        try:
            item_id_set.add(int(raw_id))
        except (TypeError, ValueError):
            continue

    item_map = {obj.id: obj for obj in items.filter(id__in=item_id_set)}
    line_entries = []
    subtotal = Decimal("0.00")
    taxable_subtotal = Decimal("0.00")

    for idx, raw_item_id in enumerate(item_ids):
        try:
            item_id = int(raw_item_id)
        except (TypeError, ValueError):
            continue

        item = item_map.get(item_id)
        if not item:
            raise ValueError("One of the selected items is no longer available.")

        qty = _safe_int(quantities[idx] if idx < len(quantities) else 0, 0)
        unit_price = _safe_decimal(unit_prices[idx] if idx < len(unit_prices) else item.price, default=item.price)

        if qty <= 0:
            continue
        if unit_price <= 0:
            raise ValueError(f"Invalid unit price for {item.name}.")

        line_total = (Decimal(qty) * unit_price).quantize(Decimal("0.01"))
        subtotal += line_total
        if item.is_taxable:
            taxable_subtotal += line_total

        line_entries.append(
            {
                "item": item,
                "quantity": qty,
                "unit_price": unit_price.quantize(Decimal("0.01")),
                "line_total": line_total,
            }
        )

    if not line_entries:
        raise ValueError("Add at least one valid line item to save the quotation.")

    adjustment_meta, tax_rate = _document_adjustments_from_request(request, default_tax_rate, customer)
    discount_amount, tax_amount, total_amount = _calculate_document_totals(
        subtotal,
        taxable_subtotal,
        tax_rate,
        adjustment_meta["discount_type"],
        _safe_decimal(adjustment_meta["discount_value"], default="0"),
    )
    adjustment_meta["discount_amount"] = str(discount_amount.quantize(Decimal("0.01")))

    return {
        "customer": customer,
        "valid_until": valid_until,
        "notes": _compose_document_notes(notes, adjustment_meta),
        "subtotal": subtotal.quantize(Decimal("0.01")),
        "tax_amount": tax_amount,
        "total_amount": total_amount,
        "line_entries": line_entries,
        "created_customer_name": customer.name if customer and customer_name else "",
    }


def _sales_quotation_copy_customer_from_request(request, owner_user, customers):
    customer_id = (request.POST.get("copy_customer_id") or "").strip()
    customer_name = (request.POST.get("copy_customer_name") or "").strip()
    if customer_id:
        return get_object_or_404(customers, pk=customer_id)
    if customer_name:
        customer = Customer.objects.filter(owner=owner_user, name__iexact=customer_name).first()
        if customer:
            return customer
        return Customer.objects.create(owner=owner_user, name=customer_name)
    return None


@login_required
@role_required(["admin", "manager", "cashier"])
def sales_quotation_list(request):
    owner_user = _inventory_owner_for_user(request.user)
    _expire_stale_sales_quotations(owner_user)
    quotations = SalesQuotation.objects.select_related("customer", "created_by")
    if not owner_user.is_superuser:
        quotations = quotations.filter(owner=owner_user)

    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip().lower()

    if q:
        quotations = quotations.filter(
            Q(quote_no__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(customer__phone__icontains=q)
            | Q(customer__email__icontains=q)
        )
    if status in {"draft", "converted", "cancelled", "expired"}:
        quotations = quotations.filter(status=status)

    visible_quotations = list(quotations.order_by("-created_at"))
    today = timezone.localdate()
    expiring_cutoff = today + timedelta(days=7)
    quotation_summary = {
        "visible_count": len(visible_quotations),
        "draft_count": sum(1 for quote in visible_quotations if quote.status == "draft"),
        "converted_count": sum(1 for quote in visible_quotations if quote.status == "converted"),
        "cancelled_count": sum(1 for quote in visible_quotations if quote.status == "cancelled"),
        "expired_count": sum(1 for quote in visible_quotations if quote.status == "expired"),
        "expiring_soon_count": sum(
            1
            for quote in visible_quotations
            if quote.status == "draft" and quote.valid_until and today <= quote.valid_until <= expiring_cutoff
        ),
        "quoted_total": sum(((quote.total_amount or Decimal("0.00")) for quote in visible_quotations), Decimal("0.00")),
    }

    profile = UserProfile.for_user(request.user)
    context = {
        "profile": profile,
        "quotations": visible_quotations,
        "q": q,
        "status": status,
        "quotation_summary": quotation_summary,
        "can_email_sales_documents": _can_email_sales_documents(request.user),
    }
    return render(request, "inventory/sales_quotation_list.html", context)


@login_required
@role_required(["admin", "manager", "cashier"])
def sales_quotation_create(request):
    owner_user = _inventory_owner_for_user(request.user)
    profile = UserProfile.for_user(request.user)
    customers = _customer_queryset_for_user(request.user).order_by("name")
    items = _sales_quotation_form_items(request.user)
    default_tax_rate = _get_tax_rate_for_location(getattr(profile, "default_location", None))
    selected_customer_id = _safe_int(request.GET.get("customer_id"), 0)
    if selected_customer_id and not customers.filter(pk=selected_customer_id).exists():
        selected_customer_id = 0

    if request.method == "POST":
        try:
            payload = _sales_quotation_payload_from_request(
                request,
                owner_user,
                customers,
                items,
                default_tax_rate,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("sales_quotation_create")

        if payload["created_customer_name"]:
            messages.success(request, f"Customer '{payload['created_customer_name']}' created for this quotation.")

        with transaction.atomic():
            quotation = SalesQuotation.objects.create(
                owner=owner_user,
                created_by=request.user,
                customer=payload["customer"],
                valid_until=payload["valid_until"],
                notes=payload["notes"],
                subtotal=payload["subtotal"],
                tax_amount=payload["tax_amount"],
                total_amount=payload["total_amount"],
            )

            for line in payload["line_entries"]:
                SalesQuotationItem.objects.create(
                    quotation=quotation,
                    item=line["item"],
                    item_name=line["item"].name,
                    quantity=line["quantity"],
                    unit_price=line["unit_price"],
                    line_total=line["line_total"],
                )

        _log_action(
            request.user,
            "sale",
            "Sales quotation created",
            {"quotation_id": quotation.id, "quote_no": quotation.quote_no, "total": str(payload["total_amount"])},
        )
        messages.success(request, f"Quotation {quotation.quote_no} created successfully.")
        return redirect("sales_quotation_detail", quote_id=quotation.id)

    context = {
        "profile": profile,
        "customers": customers,
        "items": items,
        "default_tax_rate_percent": int(default_tax_rate * 100),
        "selected_customer_id": selected_customer_id,
        "form_mode": "create",
        "form_action_url": reverse("sales_quotation_create"),
        "page_title": "New Sales Quotation",
        "page_subtitle": "Build a clear customer offer from live inventory, pricing and fulfillment context.",
        "submit_label": "Save Quotation",
        "initial_quote_lines": [],
        "initial_notes": "",
        "initial_valid_until": "",
        "initial_tax_mode": "default",
        "initial_discount_type": "flat",
        "initial_discount_value": "0.00",
        "initial_tax_rate_percent": int(default_tax_rate * 100),
    }
    return render(request, "inventory/sales_quotation_form.html", context)


@login_required
@role_required(["admin", "manager", "cashier"])
def sales_quotation_edit(request, quote_id):
    owner_user = _inventory_owner_for_user(request.user)
    profile = UserProfile.for_user(request.user)
    quotations = SalesQuotation.objects.select_related("customer", "created_by")
    if not owner_user.is_superuser:
        quotations = quotations.filter(owner=owner_user)
    quotation = get_object_or_404(quotations, pk=quote_id)

    customers = _customer_queryset_for_user(request.user).order_by("name")
    items = _sales_quotation_form_items(request.user, quotation)
    default_tax_rate = _get_tax_rate_for_location(getattr(profile, "default_location", None))

    if request.method == "POST":
        try:
            payload = _sales_quotation_payload_from_request(
                request,
                owner_user,
                customers,
                items,
                default_tax_rate,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("sales_quotation_edit", quote_id=quotation.id)

        if payload["created_customer_name"]:
            messages.success(request, f"Customer '{payload['created_customer_name']}' created for this quotation.")

        with transaction.atomic():
            quotation.customer = payload["customer"]
            quotation.valid_until = payload["valid_until"]
            quotation.notes = payload["notes"]
            quotation.subtotal = payload["subtotal"]
            quotation.tax_amount = payload["tax_amount"]
            quotation.total_amount = payload["total_amount"]
            if quotation.status == "expired" and (
                quotation.valid_until is None or quotation.valid_until >= timezone.localdate()
            ):
                quotation.status = "draft"
                quotation.converted_at = None
            quotation.save()
            quotation.items.all().delete()
            for line in payload["line_entries"]:
                SalesQuotationItem.objects.create(
                    quotation=quotation,
                    item=line["item"],
                    item_name=line["item"].name,
                    quantity=line["quantity"],
                    unit_price=line["unit_price"],
                    line_total=line["line_total"],
                )

        _log_action(
            request.user,
            "sale",
            "Sales quotation edited",
            {"quotation_id": quotation.id, "quote_no": quotation.quote_no, "total": str(payload["total_amount"])},
        )
        messages.success(request, f"Quotation {quotation.quote_no} updated successfully.")
        return redirect("sales_quotation_detail", quote_id=quotation.id)

    detail_context = _document_summary_context(
        quotation.notes,
        quotation.subtotal,
        quotation.tax_amount,
        quotation.total_amount,
        default_tax_rate,
    )
    selected_customer_id = quotation.customer_id or 0
    context = {
        "profile": profile,
        "customers": customers,
        "items": items,
        "quotation": quotation,
        "default_tax_rate_percent": int(default_tax_rate * 100),
        "selected_customer_id": selected_customer_id,
        "form_mode": "edit",
        "form_action_url": reverse("sales_quotation_edit", args=[quotation.id]),
        "page_title": f"Edit {quotation.quote_no}",
        "page_subtitle": "Update quote details without changing any invoices already issued from it.",
        "submit_label": "Update Quotation",
        "initial_quote_lines": _sales_quotation_line_initial_data(quotation),
        "initial_notes": detail_context["notes_display"],
        "initial_valid_until": quotation.valid_until.isoformat() if quotation.valid_until else "",
        "initial_tax_mode": detail_context["tax_mode"],
        "initial_discount_type": detail_context["discount_type"],
        "initial_discount_value": str(detail_context["discount_value"].quantize(Decimal("0.01"))),
        "initial_tax_rate_percent": str(detail_context["tax_rate_percent"]),
    }
    return render(request, "inventory/sales_quotation_form.html", context)


@login_required
@role_required(["admin", "manager", "cashier"])
@require_POST
def sales_quotation_copy(request, quote_id):
    owner_user = _inventory_owner_for_user(request.user)
    quotations = SalesQuotation.objects.select_related("customer")
    if not owner_user.is_superuser:
        quotations = quotations.filter(owner=owner_user)
    quotation = get_object_or_404(quotations, pk=quote_id)
    customers = _customer_queryset_for_user(request.user).order_by("name")
    target_customer = _sales_quotation_copy_customer_from_request(request, owner_user, customers)
    quote_items = list(quotation.items.select_related("item").order_by("id"))
    if not quote_items:
        messages.error(request, "This quotation has no items to copy.")
        return redirect("sales_quotation_detail", quote_id=quotation.id)

    with transaction.atomic():
        copied_quote = SalesQuotation.objects.create(
            owner=owner_user,
            created_by=request.user,
            customer=target_customer,
            valid_until=quotation.valid_until,
            notes=quotation.notes,
            subtotal=quotation.subtotal,
            tax_amount=quotation.tax_amount,
            total_amount=quotation.total_amount,
        )
        for line in quote_items:
            SalesQuotationItem.objects.create(
                quotation=copied_quote,
                item=line.item,
                item_name=line.item_name or line.item.name,
                quantity=line.quantity,
                unit_price=line.unit_price,
                line_total=line.line_total,
            )

    _log_action(
        request.user,
        "sale",
        "Sales quotation copied",
        {
            "source_quotation_id": quotation.id,
            "source_quote_no": quotation.quote_no,
            "quotation_id": copied_quote.id,
            "quote_no": copied_quote.quote_no,
            "customer_id": getattr(target_customer, "id", None),
        },
    )
    customer_label = target_customer.name if target_customer else "Walk-in / Unassigned"
    messages.success(request, f"{quotation.quote_no} copied to {copied_quote.quote_no} for {customer_label}.")
    return redirect("sales_quotation_detail", quote_id=copied_quote.id)


@login_required
@role_required(["admin", "manager", "cashier"])
def sales_quotation_detail(request, quote_id):
    owner_user = _inventory_owner_for_user(request.user)
    _expire_stale_sales_quotations(owner_user)
    quotations = SalesQuotation.objects.select_related("customer", "created_by")
    if not owner_user.is_superuser:
        quotations = quotations.filter(owner=owner_user)

    quotation = get_object_or_404(quotations, pk=quote_id)
    quote_items = quotation.items.select_related("item").all()
    locations = _location_queryset_for_user(request.user).order_by("name")
    customers = _customer_queryset_for_user(request.user).order_by("name")
    detail_context = _document_summary_context(
        quotation.notes,
        quotation.subtotal,
        quotation.tax_amount,
        quotation.total_amount,
        _get_tax_rate_for_location(getattr(request.user.profile, "default_location", None)),
    )

    profile = UserProfile.for_user(request.user)
    context = {
        "profile": profile,
        "quotation": quotation,
        "quote_items": quote_items,
        "locations": locations,
        "customers": customers,
        "payment_methods": SalesInvoicePayment.METHOD_CHOICES,
        "converted_invoice_count": quotation.converted_invoice_count,
        "latest_converted_invoice": quotation.converted_invoice,
        "can_email_sales_document": (
            _can_email_sales_documents(request.user) and quotation.status != "cancelled"
        ),
        "email_panel_open": request.GET.get("email") == "1",
        **detail_context,
    }
    return render(request, "inventory/sales_quotation_detail.html", context)


@login_required
@role_required(["admin", "manager"])
@require_POST
def sales_quotation_email(request, quote_id):
    owner_user = _inventory_owner_for_user(request.user)
    quotations = SalesQuotation.objects.select_related("owner", "customer")
    if not owner_user.is_superuser:
        quotations = quotations.filter(owner=owner_user)

    quotation = get_object_or_404(quotations, pk=quote_id)
    if quotation.status == "cancelled":
        messages.error(request, "Cancelled quotations cannot be emailed.")
        return redirect("sales_quotation_detail", quote_id=quotation.id)

    try:
        recipient_email = _receipt_validate_email(
            request.POST.get("recipient_email") or getattr(quotation.customer, "email", "")
        )
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect("sales_quotation_detail", quote_id=quotation.id)

    audit_metadata = {
        "document_type": "quotation",
        "quotation_id": quotation.id,
        "quote_no": quotation.quote_no,
        "recipient_email": recipient_email,
        "status": quotation.status,
    }
    try:
        _send_sales_document_email(quotation, "quotation", recipient_email)
    except Exception as exc:
        _log_action(
            request.user,
            "invoice",
            "Sales quotation email failed",
            {**audit_metadata, "error": str(exc)},
            severity="error",
        )
        messages.error(request, "The quotation email could not be sent right now. Please try again.")
        return redirect("sales_quotation_detail", quote_id=quotation.id)

    _log_action(
        request.user,
        "invoice",
        "Sales quotation emailed",
        audit_metadata,
    )
    messages.success(request, f"Quotation {quotation.quote_no} emailed to {recipient_email}.")
    return redirect("sales_quotation_detail", quote_id=quotation.id)


@login_required
@role_required(["admin", "manager", "cashier"])
@require_POST
def sales_quotation_convert_to_invoice(request, quote_id):
    owner_user = _inventory_owner_for_user(request.user)
    selected_location = _resolve_sales_invoice_location(request.user, request.POST.get("location_id"))
    if not selected_location:
        messages.error(request, "Choose a valid source location before conversion.")
        return redirect("sales_quotation_detail", quote_id=quote_id)

    next_step = (request.POST.get("next_step") or "").strip().lower()
    payment_method = _normalize_sales_invoice_payment_method(request.POST.get("payment_method"), default="cash")

    try:
        invoice, created_new, quotation = _create_sales_invoice_from_quotation(
            request.user,
            owner_user,
            quote_id,
            selected_location,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("sales_quotation_detail", quote_id=quote_id)

    _log_action(
        request.user,
        "invoice",
        "Sales quotation invoiced",
        {
            "quotation_id": getattr(quotation, "id", quote_id),
            "quote_no": getattr(quotation, "quote_no", None),
            "invoice_id": invoice.id,
            "invoice_no": invoice.invoice_no,
            "location_id": getattr(selected_location, "id", None),
            "created_new": created_new,
        },
    )

    messages.success(request, f"{quotation.quote_no} copied to invoice {invoice.invoice_no}.")

    if next_step == "pay":
        messages.info(request, "Invoice ready. Continue to the payment screen.")
        return redirect(_sales_invoice_payment_redirect(invoice.id, payment_method))

    return redirect("sales_invoice_detail", invoice_id=invoice.id)


@login_required
@role_required(["admin", "manager", "cashier"])
def sales_invoice_create(request):
    owner_user = _inventory_owner_for_user(request.user)
    _expire_stale_sales_quotations(owner_user)
    profile = UserProfile.for_user(request.user)

    customers = _customer_queryset_for_user(request.user).order_by("name")
    items = _item_queryset_for_user(request.user).filter(status="active").order_by("name")
    locations = _location_queryset_for_user(request.user).order_by("name")
    selected_customer_id = _safe_int(request.GET.get("customer_id"), 0)
    if selected_customer_id and not customers.filter(pk=selected_customer_id).exists():
        selected_customer_id = 0

    draft_quotations = SalesQuotation.objects.select_related("customer").filter(status__in=["draft", "converted"])
    if not owner_user.is_superuser:
        draft_quotations = draft_quotations.filter(owner=owner_user)
    draft_quotations = draft_quotations.order_by("-created_at")

    source = (request.GET.get("source") or request.POST.get("source_mode") or "scratch").strip().lower()
    if source not in {"scratch", "quotation"}:
        source = "scratch"

    if request.method == "POST":
        source_mode = (request.POST.get("source_mode") or "scratch").strip().lower()
        next_step = (request.POST.get("next_step") or "").strip().lower()
        payment_method = _normalize_sales_invoice_payment_method(request.POST.get("payment_method"), default="cash")

        if source_mode == "quotation":
            quotation_id = _safe_int(request.POST.get("quotation_id"), 0)
            if quotation_id <= 0:
                messages.error(request, "Select a valid quotation to continue.")
                return redirect(f"{reverse('sales_invoice_create')}?source=quotation")

            selected_location = _resolve_sales_invoice_location(request.user, request.POST.get("location_id"))
            if not selected_location:
                messages.error(request, "Choose a valid location for this invoice.")
                return redirect(f"{reverse('sales_invoice_create')}?source=quotation")

            try:
                invoice, created_new, quotation = _create_sales_invoice_from_quotation(
                    request.user,
                    owner_user,
                    quotation_id,
                    selected_location,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return redirect(f"{reverse('sales_invoice_create')}?source=quotation")

            _log_action(
                request.user,
                "invoice",
                "Sales invoice created from quotation",
                {
                    "quotation_id": getattr(quotation, "id", quotation_id),
                    "quote_no": getattr(quotation, "quote_no", None),
                    "invoice_id": invoice.id,
                    "invoice_no": invoice.invoice_no,
                    "created_new": created_new,
                },
            )

            messages.success(request, f"Invoice {invoice.invoice_no} created from {quotation.quote_no}.")

            if next_step == "pay":
                messages.info(request, "Invoice ready. Continue to the payment screen.")
                return redirect(_sales_invoice_payment_redirect(invoice.id, payment_method))
            return redirect("sales_invoice_detail", invoice_id=invoice.id)

        # Scratch invoice path
        customer_id = (request.POST.get("customer_id") or "").strip()
        customer_name = (request.POST.get("customer_name") or "").strip()
        notes = (request.POST.get("notes") or "").strip()
        due_date_raw = (request.POST.get("due_date") or "").strip()
        if due_date_raw:
            try:
                due_date = datetime.fromisoformat(due_date_raw).date()
            except ValueError:
                messages.error(request, "Invalid invoice due date.")
                return redirect(f"{reverse('sales_invoice_create')}?source=scratch")
        else:
            due_date = timezone.localdate() + timedelta(days=7)
        selected_location = _resolve_sales_invoice_location(request.user, request.POST.get("location_id"))
        if not selected_location:
            messages.error(request, "Choose a valid location for this invoice.")
            return redirect(f"{reverse('sales_invoice_create')}?source=scratch")

        customer = None
        if customer_id:
            customer = get_object_or_404(customers, pk=customer_id)
        elif customer_name:
            customer = Customer.objects.filter(
                owner=owner_user,
                name__iexact=customer_name,
            ).first()
            if not customer:
                customer = Customer.objects.create(
                    owner=owner_user,
                    name=customer_name,
                )
                messages.success(request, f"Customer '{customer.name}' created for this invoice.")

        item_ids = request.POST.getlist("item_id[]") or request.POST.getlist("item_id")
        quantities = request.POST.getlist("quantity[]") or request.POST.getlist("quantity")
        unit_prices = request.POST.getlist("unit_price[]") or request.POST.getlist("unit_price")

        if not item_ids:
            messages.error(request, "Add at least one item to create an invoice.")
            return redirect(f"{reverse('sales_invoice_create')}?source=scratch")

        item_id_set = set()
        for raw_id in item_ids:
            try:
                item_id_set.add(int(raw_id))
            except (TypeError, ValueError):
                continue

        items_qs = _item_queryset_for_user(owner_user).filter(id__in=item_id_set)
        item_map = {obj.id: obj for obj in items_qs}

        line_entries = []
        subtotal = Decimal("0.00")
        taxable_subtotal = Decimal("0.00")
        required_qty_by_item = {}

        for idx, raw_item_id in enumerate(item_ids):
            try:
                item_id = int(raw_item_id)
            except (TypeError, ValueError):
                continue

            item = item_map.get(item_id)
            if not item:
                messages.error(request, "One of the selected items is unavailable.")
                return redirect(f"{reverse('sales_invoice_create')}?source=scratch")

            qty = _safe_int(quantities[idx] if idx < len(quantities) else 0, 0)
            unit_price = _safe_decimal(unit_prices[idx] if idx < len(unit_prices) else item.price, default=item.price)

            if qty <= 0:
                continue
            if unit_price <= 0:
                messages.error(request, f"Invalid unit price for {item.name}.")
                return redirect(f"{reverse('sales_invoice_create')}?source=scratch")

            line_total = (Decimal(qty) * unit_price).quantize(Decimal("0.01"))
            subtotal += line_total
            if item.is_taxable:
                taxable_subtotal += line_total

            required_qty_by_item[item_id] = required_qty_by_item.get(item_id, 0) + qty
            line_entries.append(
                {
                    "item": item,
                    "quantity": qty,
                    "unit_price": unit_price.quantize(Decimal("0.01")),
                    "line_total": line_total,
                }
            )

        if not line_entries:
            messages.error(request, "Add at least one valid item line.")
            return redirect(f"{reverse('sales_invoice_create')}?source=scratch")

        stock_map = {
            stock.item_id: stock
            for stock in StockRecord.objects.filter(
                item_id__in=list(required_qty_by_item.keys()),
                location=selected_location,
            )
        }

        for item_id, required_qty in required_qty_by_item.items():
            item = item_map[item_id]
            if int(item.total_quantity or 0) < required_qty:
                messages.error(
                    request,
                 f"Insufficient stock for {item.name}. Available: {item.total_quantity}, required: {required_qty}.",
                )
                return redirect(f"{reverse('sales_invoice_create')}?source=scratch")

            stock = stock_map.get(item_id)
            if stock and int(stock.quantity or 0) < required_qty:
                messages.error(request, f"{item.name} has only {stock.quantity} unit(s) in {selected_location.name}.")
                return redirect(f"{reverse('sales_invoice_create')}?source=scratch")

        default_tax_rate = _get_tax_rate_for_location(selected_location)
        adjustment_meta, tax_rate = _document_adjustments_from_request(request, default_tax_rate, customer)
        discount_amount, tax_amount, total_amount = _calculate_document_totals(
            subtotal,
            taxable_subtotal,
            tax_rate,
            adjustment_meta["discount_type"],
            _safe_decimal(adjustment_meta["discount_value"], default="0"),
        )
        adjustment_meta["discount_amount"] = str(discount_amount.quantize(Decimal("0.01")))
        stored_notes = _compose_document_notes(notes, adjustment_meta)

        with transaction.atomic():
            invoice = SalesInvoice.objects.create(
                owner=owner_user,
                created_by=request.user,
                quotation=None,
                customer=customer,
                location=selected_location,
                status="issued",
                due_date=due_date,
                notes=stored_notes,
                subtotal=subtotal.quantize(Decimal("0.01")),
                tax_amount=tax_amount,
                total_amount=total_amount,
            )

            for line in line_entries:
                SalesInvoiceItem.objects.create(
                    invoice=invoice,
                    item=line["item"],
                    item_name=line["item"].name,
                    quantity=line["quantity"],
                    unit_price=line["unit_price"],
                    line_total=line["line_total"],
                )

            for item_id, required_qty in required_qty_by_item.items():
                # Inventory reduction is handled exclusively via StockRecord relations.
                stock = stock_map.get(item_id)
                if stock:
                    stock.quantity = max(0, int(stock.quantity or 0) - required_qty)
                    stock.save(update_fields=["quantity"])

        _log_action(
            request.user,
            "invoice",
            "Sales invoice created from scratch",
            {
                "invoice_id": invoice.id,
                "invoice_no": invoice.invoice_no,
                "location_id": getattr(selected_location, "id", None),
                "total": str(total_amount),
            },
        )
        messages.success(request, f"Invoice {invoice.invoice_no} created successfully.")
        if next_step == "pay":
            messages.info(request, "Invoice ready. Continue to the payment screen.")
            return redirect(_sales_invoice_payment_redirect(invoice.id, payment_method))
        return redirect("sales_invoice_detail", invoice_id=invoice.id)

    context = {
        "profile": profile,
        "customers": customers,
        "items": items,
        "locations": locations,
        "draft_quotations": draft_quotations,
        "default_location": _resolve_sales_invoice_location(request.user),
        "default_tax_rate_percent": int(_get_tax_rate_for_location(_resolve_sales_invoice_location(request.user)) * 100),
        "payment_methods": SalesInvoicePayment.METHOD_CHOICES,
        "source": source,
        "selected_customer_id": selected_customer_id,
        "default_due_date": timezone.localdate() + timedelta(days=7),
    }
    return render(request, "inventory/sales_invoice_form.html", context)


@login_required
@role_required(["admin", "manager", "cashier"])
def sales_invoice_list(request):
    owner_user = _inventory_owner_for_user(request.user)
    invoices = SalesInvoice.objects.select_related("customer", "created_by", "quotation", "location")
    if not owner_user.is_superuser:
        invoices = invoices.filter(owner=owner_user)

    q = (request.GET.get("q") or "").strip()
    q_clean = q.lstrip("#").strip()
    status = (request.GET.get("status") or "").strip().lower()

    if q:
        query_filter = (
            Q(invoice_no__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(customer__phone__icontains=q)
            | Q(customer__email__icontains=q)
            | Q(quotation__quote_no__icontains=q)
        )
        if q_clean and q_clean != q:
            query_filter |= Q(invoice_no__icontains=q_clean) | Q(quotation__quote_no__icontains=q_clean)
        if q_clean.isdigit():
            query_filter |= Q(id=int(q_clean))
        invoices = invoices.filter(query_filter)
    if status in {"issued", "paid", "void"}:
        invoices = invoices.filter(status=status)

    visible_invoices = list(invoices.order_by("-created_at"))
    invoice_summary = {
        "visible_count": len(visible_invoices),
        "issued_count": sum(1 for invoice in visible_invoices if invoice.status == "issued"),
        "paid_count": sum(1 for invoice in visible_invoices if invoice.status == "paid"),
        "void_count": sum(1 for invoice in visible_invoices if invoice.status == "void"),
        "quotation_backed_count": sum(1 for invoice in visible_invoices if invoice.quotation_id),
        "effective_total": sum((invoice.effective_total_amount for invoice in visible_invoices), Decimal("0.00")),
        "outstanding_total": sum((invoice.balance_due for invoice in visible_invoices), Decimal("0.00")),
        "paid_total": sum((invoice.total_paid_amount for invoice in visible_invoices), Decimal("0.00")),
    }
    invoice_summary["direct_count"] = invoice_summary["visible_count"] - invoice_summary["quotation_backed_count"]

    profile = UserProfile.for_user(request.user)
    context = {
        "profile": profile,
        "invoices": visible_invoices,
        "q": q,
        "status": status,
        "payments_enabled": _sales_invoice_payments_available(),
        "invoice_summary": invoice_summary,
        "can_manage_all_invoice_locations": request.user.is_superuser or profile.role == "admin",
        "invoice_action_location_id": profile.default_location_id,
        "can_email_sales_documents": _can_email_sales_documents(request.user),
    }
    return render(request, "inventory/sales_invoice_list.html", context)


@login_required
@role_required(["admin", "manager", "cashier"])
@require_POST
def sales_invoice_mark_paid(request, invoice_id):
    owner_user = _inventory_owner_for_user(request.user)
    invoice_qs = SalesInvoice.objects.select_related("quotation", "customer")
    if not owner_user.is_superuser:
        invoice_qs = invoice_qs.filter(owner=owner_user)

    invoice = get_object_or_404(invoice_qs, pk=invoice_id)
    if not _can_manage_sales_invoice_at_active_location(request.user, invoice):
        raise Http404("Invoice is outside your active location.")
    if invoice.status == "void":
        messages.error(request, "Voided invoices cannot be marked as paid.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)

    paid_total, balance_due = _sales_invoice_payment_totals(invoice)
    payments_enabled = _sales_invoice_payments_available()

    if invoice.status == "paid" or balance_due <= Decimal("0.00"):
        messages.info(request, f"{invoice.invoice_no} is already marked as paid.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)

    raw_pickup_status = (request.POST.get("pickup_status") or "").strip().lower()
    if raw_pickup_status not in VALID_PICKUP_STATUSES:
        messages.error(request, "Choose whether the customer collected the items or left them in store.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)
    pickup_status = _normalize_sales_invoice_pickup_status(raw_pickup_status)

    with transaction.atomic():
        invoice = (
            SalesInvoice.objects.select_for_update()
            .select_related("quotation", "customer", "location")
            .get(pk=invoice.pk)
        )
        paid_total, balance_due = _sales_invoice_payment_totals(invoice)
        if invoice.status == "paid" or balance_due <= Decimal("0.00"):
            messages.info(request, f"{invoice.invoice_no} is already marked as paid.")
            return redirect("sales_invoice_detail", invoice_id=invoice.id)
        settlement_payment = None
        if payments_enabled:
            settlement_payment = SalesInvoicePayment.objects.create(
                invoice=invoice,
                received_by=request.user,
                amount=balance_due,
                payment_method="other",
                notes="Quick-pay balance settlement via Mark Paid button.",
            )

        invoice.status = "paid"
        invoice.save(update_fields=["status"])
        collection_status = _apply_sales_invoice_pickup_choice(
            invoice,
            pickup_status,
            actor=request.user,
        )

        _log_action(
            request.user,
            "invoice",
            "Sales invoice marked paid",
            {
                "invoice_id": invoice.id,
                "payment_id": getattr(settlement_payment, "id", None),
                "amount": str(balance_due),
                "collection_status": collection_status,
            },
            required=True,
        )

    if collection_status == "awaiting_collection":
        messages.success(
            request,
            f"Invoice {invoice.invoice_no} marked as paid. Its items are remaining in store until pickup.",
        )
    else:
        messages.success(request, f"Invoice {invoice.invoice_no} marked as paid and collected.")
    return redirect("sales_invoice_detail", invoice_id=invoice.id)


@login_required
@role_required(["admin", "manager", "cashier"])
@require_POST
def sales_invoice_mark_collected(request, invoice_id):
    owner_user = _inventory_owner_for_user(request.user)
    invoice_qs = SalesInvoice.objects.select_related("customer", "location")
    if not owner_user.is_superuser:
        invoice_qs = invoice_qs.filter(owner=owner_user)

    invoice = get_object_or_404(invoice_qs, pk=invoice_id)
    if not _can_manage_sales_invoice_at_active_location(request.user, invoice):
        raise Http404("Invoice is outside your active location.")
    if invoice.status != "paid":
        messages.error(request, "Only fully paid invoices can be recorded as picked up.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)
    if invoice.collection_status == "collected_after_hold":
        messages.info(request, f"{invoice.invoice_no} was already recorded as picked up.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)
    if invoice.collection_status != "awaiting_collection":
        messages.error(request, "This invoice is not currently listed as remaining in store.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)

    with transaction.atomic():
        locked_invoice = SalesInvoice.objects.select_for_update().get(pk=invoice.pk)
        if locked_invoice.status != "paid" or locked_invoice.collection_status != "awaiting_collection":
            messages.error(request, "The invoice pickup status changed before this action completed.")
            return redirect("sales_invoice_detail", invoice_id=invoice.id)
        _set_sales_invoice_collection_status(
            locked_invoice,
            "collected_after_hold",
            actor=request.user,
        )

    _log_action(
        request.user,
        "invoice",
        "Held sales invoice collected",
        {
            "invoice_id": invoice.id,
            "invoice_no": invoice.invoice_no,
            "location_id": invoice.location_id,
        },
    )
    messages.success(
        request,
        f"Pickup recorded for {invoice.invoice_no}. Its items moved to Delivered From Uncollected.",
    )
    return redirect("sales_invoice_detail", invoice_id=invoice.id)


@login_required
@role_required(["admin", "manager", "cashier"])
@require_POST
def sales_invoice_classify_collection(request, invoice_id):
    owner_user = _inventory_owner_for_user(request.user)
    invoice_qs = SalesInvoice.objects.select_related("customer", "location")
    if not owner_user.is_superuser:
        invoice_qs = invoice_qs.filter(owner=owner_user)

    invoice = get_object_or_404(invoice_qs, pk=invoice_id)
    if not _can_manage_sales_invoice_at_active_location(request.user, invoice):
        raise Http404("Invoice is outside your active location.")
    if invoice.status != "paid":
        messages.error(request, "Settle the invoice before recording its pickup outcome.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)

    collection_status = (request.POST.get("collection_status") or "").strip().lower()
    valid_targets = {
        "awaiting_collection",
        "collected_immediately",
        "collected_after_hold",
    }
    if collection_status not in valid_targets:
        messages.error(request, "Choose a valid pickup outcome.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)

    with transaction.atomic():
        locked_invoice = SalesInvoice.objects.select_for_update().get(pk=invoice.pk)
        if locked_invoice.status != "paid":
            messages.error(request, "The invoice payment status changed before this action completed.")
            return redirect("sales_invoice_detail", invoice_id=invoice.id)
        if locked_invoice.collection_status not in {"untracked", "pending_payment"}:
            messages.error(request, "This invoice already has a pickup outcome.")
            return redirect("sales_invoice_detail", invoice_id=invoice.id)
        _set_sales_invoice_collection_status(
            locked_invoice,
            collection_status,
            actor=request.user,
        )

    _log_action(
        request.user,
        "invoice",
        "Sales invoice pickup outcome classified",
        {
            "invoice_id": invoice.id,
            "invoice_no": invoice.invoice_no,
            "collection_status": collection_status,
            "location_id": invoice.location_id,
        },
    )
    messages.success(request, f"Pickup outcome saved for {invoice.invoice_no}.")
    return redirect("sales_invoice_detail", invoice_id=invoice.id)

@login_required
@role_required(["admin", "manager", "cashier"])
def sales_invoice_detail(request, invoice_id):
    """
    Display the details of a specific sales invoice, including line items
    and payment status.
    """
    owner_user = _inventory_owner_for_user(request.user)
    invoices = SalesInvoice.objects.select_related("customer", "created_by", "quotation", "location")
    if not owner_user.is_superuser:
        invoices = invoices.filter(owner=owner_user)

    invoice = get_object_or_404(invoices, pk=invoice_id)
    invoice_items = invoice.items.select_related("item").all()
    payments = (
        invoice.payments.select_related("received_by", "reversal", "reversal__reversed_by").order_by("-payment_date")
        if _sales_invoice_payments_available()
        else []
    )
    credit_notes = invoice.credit_notes.select_related("created_by").all() if _sales_invoice_credit_notes_available() else []
    
    paid_total, balance_due = _sales_invoice_payment_totals(invoice)
    overpayment_amount = _sales_invoice_overpayment_amount(invoice, paid_total)
    credit_note_total = _sales_invoice_credit_note_total(invoice)
    effective_total_amount = _sales_invoice_effective_total(invoice)
    detail_context = _document_summary_context(
        invoice.notes,
        invoice.subtotal,
        invoice.tax_amount,
        invoice.total_amount,
        _get_tax_rate_for_location(invoice.location),
    )

    profile = UserProfile.for_user(request.user)
    context = {
        "profile": profile,
        "invoice": invoice,
        "invoice_items": invoice_items,
        "payments": payments,
        "payments_enabled": _sales_invoice_payments_available(),
        "paid_total": paid_total,
        "balance_due": balance_due,
        "overpayment_amount": overpayment_amount,
        "customer_credit_balance": _customer_credit_balance(invoice.customer),
        "balance_after_customer_credit": max(
            balance_due - _customer_credit_balance(invoice.customer),
            Decimal("0.00"),
        ).quantize(Decimal("0.01")),
        "credit_notes": credit_notes,
        "credit_note_total": credit_note_total,
        "effective_total_amount": effective_total_amount,
        "can_issue_credit_notes": _can_issue_sales_invoice_credit_notes(request.user),
        "credit_note_reason_choices": _sales_invoice_credit_note_reason_choices(),
        "can_manage_invoice_at_location": _can_manage_sales_invoice_at_active_location(request.user, invoice),
        "can_email_sales_document": (
            _can_email_sales_documents(request.user) and invoice.status != "void"
        ),
        "email_panel_open": request.GET.get("email") == "1",
        **detail_context,
    }
    return render(request, "inventory/sales_invoice_detail.html", context)


@login_required
@role_required(["admin", "manager"])
@require_POST
def sales_invoice_email(request, invoice_id):
    owner_user = _inventory_owner_for_user(request.user)
    invoices = SalesInvoice.objects.select_related("owner", "customer", "location")
    if not owner_user.is_superuser:
        invoices = invoices.filter(owner=owner_user)

    invoice = get_object_or_404(invoices, pk=invoice_id)
    if invoice.status == "void":
        messages.error(request, "Voided invoices cannot be emailed.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)

    try:
        recipient_email = _receipt_validate_email(
            request.POST.get("recipient_email") or getattr(invoice.customer, "email", "")
        )
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect("sales_invoice_detail", invoice_id=invoice.id)

    audit_metadata = {
        "document_type": "invoice",
        "invoice_id": invoice.id,
        "invoice_no": invoice.invoice_no,
        "recipient_email": recipient_email,
        "status": invoice.status,
    }
    try:
        _send_sales_document_email(invoice, "invoice", recipient_email)
    except Exception as exc:
        _log_action(
            request.user,
            "invoice",
            "Sales invoice email failed",
            {**audit_metadata, "error": str(exc)},
            severity="error",
        )
        messages.error(request, "The invoice email could not be sent right now. Please try again.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)

    _log_action(
        request.user,
        "invoice",
        "Sales invoice emailed",
        audit_metadata,
    )
    messages.success(request, f"Invoice {invoice.invoice_no} emailed to {recipient_email}.")
    return redirect("sales_invoice_detail", invoice_id=invoice.id)


@login_required
@role_required(["admin", "manager"])
@require_POST
def sales_invoice_create_credit_note(request, invoice_id):
    if not _sales_invoice_credit_notes_available():
        messages.error(request, "Credit note records are not available until the latest invoice migrations are applied.")
        return redirect("sales_invoice_detail", invoice_id=invoice_id)

    owner_user = _inventory_owner_for_user(request.user)
    invoice_qs = SalesInvoice.objects.select_related("customer")
    if not owner_user.is_superuser:
        invoice_qs = invoice_qs.filter(owner=owner_user)

    invoice = get_object_or_404(invoice_qs, pk=invoice_id)
    if invoice.status == "void":
        messages.error(request, "Voided invoices cannot receive credit notes.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)

    amount = _safe_decimal(request.POST.get("amount", "0"), default="0")
    reason = (request.POST.get("reason") or "pricing_adjustment").strip().lower()
    notes = (request.POST.get("notes") or "").strip()
    valid_reasons = {value for value, _label in SalesInvoiceCreditNote.REASON_CHOICES}

    if amount <= Decimal("0.00"):
        messages.error(request, "Enter a credit note amount greater than zero.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)
    if reason not in valid_reasons:
        messages.error(request, "Select a valid credit note reason.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)

    with transaction.atomic():
        locked_invoice = SalesInvoice.objects.select_related("customer").select_for_update().get(pk=invoice.id)
        locked_customer = None
        if locked_invoice.customer_id:
            locked_customer = Customer.objects.select_for_update().get(pk=locked_invoice.customer_id)

        current_effective_total = _sales_invoice_effective_total(locked_invoice)
        if amount > current_effective_total:
            messages.error(
                request,
                f"Credit notes on this invoice cannot exceed the remaining net invoice total of ${current_effective_total}.",
            )
            return redirect("sales_invoice_detail", invoice_id=locked_invoice.id)

        paid_total, _balance_due = _sales_invoice_payment_totals(locked_invoice)
        current_overpayment = _sales_invoice_overpayment_amount(locked_invoice, paid_total)
        next_effective_total = max(current_effective_total - amount, Decimal("0.00")).quantize(Decimal("0.01"))
        next_overpayment = max(paid_total - next_effective_total, Decimal("0.00")).quantize(Decimal("0.01"))
        credited_customer_amount = max(next_overpayment - current_overpayment, Decimal("0.00")).quantize(Decimal("0.01"))

        if credited_customer_amount > Decimal("0.00") and not locked_customer:
            messages.error(
                request,
                "This credit note would create an overpayment, so the invoice must be assigned to a customer before it can be issued.",
            )
            return redirect("sales_invoice_detail", invoice_id=locked_invoice.id)

        credit_note = SalesInvoiceCreditNote.objects.create(
            invoice=locked_invoice,
            created_by=request.user,
            amount=amount,
            reason=reason,
            notes=notes,
            credited_customer_amount=credited_customer_amount,
        )

        if credited_customer_amount > Decimal("0.00"):
            locked_customer.add_credit(
                credited_customer_amount,
                movement_type="invoice_credit_note",
                created_by=request.user,
                credit_note=credit_note,
                reason=f"Credit note {credit_note.id} created customer account credit.",
            )

        _refresh_sales_invoice_status(locked_invoice)
        _log_action(
            request.user,
            "invoice",
            "Sales invoice credit note created",
            {
                "invoice_id": invoice.id,
                "credit_note_id": credit_note.id,
                "amount": str(amount),
                "reason": reason,
                "credited_customer_amount": str(credited_customer_amount),
            },
            severity="warn",
            required=True,
        )

    if credited_customer_amount > Decimal("0.00") and locked_invoice.customer:
        messages.success(
            request,
            f"Credit note of ${amount} added to {locked_invoice.invoice_no}. "
            f"${credited_customer_amount} was added to {locked_invoice.customer.name}'s account credit.",
        )
    else:
        messages.success(request, f"Credit note of ${amount} added to {locked_invoice.invoice_no}.")
    return redirect("sales_invoice_detail", invoice_id=invoice.id)


@login_required
@role_required(["admin", "manager", "cashier"])
def sales_invoice_payment(request, invoice_id):
    """
    View to record a manual payment against a sales invoice.
    """
    owner_user = _inventory_owner_for_user(request.user)
    invoices = SalesInvoice.objects.select_related("customer")
    if not owner_user.is_superuser:
        invoices = invoices.filter(owner=owner_user)

    invoice = get_object_or_404(invoices, pk=invoice_id)
    if not _can_manage_sales_invoice_at_active_location(request.user, invoice):
        raise Http404("Invoice is outside your active location.")
    if invoice.status == "void":
        messages.error(request, "Cannot record payment for a voided invoice.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)

    paid_total, balance_due = _sales_invoice_payment_totals(invoice)
    overpayment_amount = _sales_invoice_overpayment_amount(invoice, paid_total)
    available_customer_credit = _credit_applicable_to_invoice(invoice)
    payments = (
        invoice.payments.select_related("received_by", "reversal", "reversal__reversed_by").order_by("-payment_date")
        if _sales_invoice_payments_available()
        else []
    )
    
    now_local = timezone.localtime(timezone.now())
    default_payment_datetime = now_local.strftime("%Y-%m-%dT%H:%M")
    default_payment_method = _normalize_sales_invoice_payment_method(request.GET.get("payment_method"), default="cash")

    if request.method == "POST":
        amount = _safe_decimal(request.POST.get("amount", "0"), default="0")
        credit_to_apply = _safe_decimal(request.POST.get("credit_to_apply", "0"), default="0")
        method = _normalize_sales_invoice_payment_method(request.POST.get("payment_method"), default="cash")
        if method == "account_credit" and amount > Decimal("0.00"):
            credit_to_apply = (credit_to_apply + amount).quantize(Decimal("0.01"))
            amount = Decimal("0.00")
        reference = (request.POST.get("reference") or "").strip()
        notes = (request.POST.get("notes") or "").strip()
        payment_date = _parse_sales_invoice_payment_datetime(request.POST.get("payment_date"))
        raw_pickup_status = (request.POST.get("pickup_status") or "").strip().lower()
        if raw_pickup_status and raw_pickup_status not in VALID_PICKUP_STATUSES:
            messages.error(request, "Choose whether the customer collected the items or left them in store.")
            return redirect("sales_invoice_payment", invoice_id=invoice.id)
        pickup_status = _normalize_sales_invoice_pickup_status(raw_pickup_status)

        if amount <= 0 and credit_to_apply <= 0:
            messages.error(request, "Enter a payment amount or apply customer credit before saving.")
            return redirect("sales_invoice_payment", invoice_id=invoice.id)

        if amount > 0 and _sales_invoice_payment_method_requires_reference(method) and not reference:
            messages.error(request, "Enter a reference or transaction ID for non-cash payments.")
            return redirect("sales_invoice_payment", invoice_id=invoice.id)

        if credit_to_apply > Decimal("0.00") and not invoice.customer:
            messages.error(
                request,
                "Customer credit can only be applied when this invoice is assigned to a customer.",
            )
            return redirect("sales_invoice_payment", invoice_id=invoice.id)

        final_collection_status = None
        created_payment_ids = []
        with transaction.atomic():
            invoice = (
                SalesInvoice.objects.select_for_update()
                .select_related("customer", "location")
                .get(pk=invoice.pk)
            )
            if invoice.status == "void":
                messages.error(request, "Cannot record payment for a voided invoice.")
                return redirect("sales_invoice_detail", invoice_id=invoice.id)

            paid_total, balance_due = _sales_invoice_payment_totals(invoice)
            if invoice.status == "paid" or balance_due <= Decimal("0.00"):
                messages.info(request, f"{invoice.invoice_no} is already fully paid.")
                return redirect("sales_invoice_detail", invoice_id=invoice.id)

            payment_shift = None
            if invoice.location_id:
                payment_shift = (
                    CashShift.objects.select_for_update()
                    .filter(
                        cashier=request.user,
                        location_id=invoice.location_id,
                        is_closed=False,
                    )
                    .first()
                )
                if payment_shift and payment_date < payment_shift.opened_at:
                    payment_shift = None

            locked_customer = None
            if invoice.customer_id:
                locked_customer = Customer.objects.select_for_update().get(pk=invoice.customer_id)
                available_customer_credit = min(
                    _customer_credit_balance(locked_customer),
                    balance_due,
                ).quantize(Decimal("0.01"))
            else:
                available_customer_credit = Decimal("0.00")

            if credit_to_apply < Decimal("0.00"):
                messages.error(request, "Credit to apply cannot be negative.")
                return redirect("sales_invoice_payment", invoice_id=invoice.id)
            if credit_to_apply > available_customer_credit:
                messages.error(
                    request,
                    f"Only ${available_customer_credit} of customer credit can be applied to this invoice right now.",
                )
                return redirect("sales_invoice_payment", invoice_id=invoice.id)

            remaining_due_after_credit = max(balance_due - credit_to_apply, Decimal("0.00")).quantize(Decimal("0.01"))
            if credit_to_apply > Decimal("0.00") and amount == balance_due:
                amount = Decimal("0.00")
            elif credit_to_apply > Decimal("0.00") and amount > remaining_due_after_credit:
                amount = remaining_due_after_credit
            incoming_overpayment = max(amount - remaining_due_after_credit, Decimal("0.00")).quantize(Decimal("0.01"))

            if incoming_overpayment > Decimal("0.00") and not locked_customer:
                messages.error(
                    request,
                    "Overpayment requires a customer on the invoice so the extra amount can be stored as account credit.",
                )
                return redirect("sales_invoice_payment", invoice_id=invoice.id)

            if credit_to_apply > Decimal("0.00"):
                credit_payment = SalesInvoicePayment.objects.create(
                    invoice=invoice,
                    shift=payment_shift,
                    received_by=request.user,
                    amount=credit_to_apply,
                    applied_customer_credit=credit_to_apply,
                    payment_method="account_credit",
                    reference=reference[:80],
                    notes=(f"Applied from customer account credit. {notes}".strip()),
                    payment_date=payment_date,
                )
                created_payment_ids.append(credit_payment.id)
                locked_customer.use_credit(
                    credit_to_apply,
                    movement_type="invoice_credit_application",
                    created_by=request.user,
                    payment=credit_payment,
                    reason=f"Applied customer credit to invoice {invoice.invoice_no or invoice.id}.",
                )

            cash_payment = None
            if amount > Decimal("0.00"):
                cash_payment = SalesInvoicePayment.objects.create(
                    invoice=invoice,
                    shift=payment_shift,
                    received_by=request.user,
                    amount=amount,
                    credited_customer_overpayment=incoming_overpayment,
                    payment_method=method,
                    reference=reference,
                    notes=notes,
                    payment_date=payment_date,
                )
                created_payment_ids.append(cash_payment.id)
            
            # Check if fully paid and update status
            _, updated_balance = _sales_invoice_payment_totals(invoice)
            if updated_balance <= 0:
                invoice.status = "paid"
                invoice.save(update_fields=["status"])
                final_collection_status = _apply_sales_invoice_pickup_choice(
                    invoice,
                    pickup_status,
                    actor=request.user,
                    occurred_at=payment_date,
                )
            if incoming_overpayment > Decimal("0.00"):
                locked_customer.add_credit(
                    incoming_overpayment,
                    movement_type="invoice_overpayment",
                    created_by=request.user,
                    payment=cash_payment,
                    reason=f"Overpayment credited from invoice {invoice.invoice_no or invoice.id}.",
                )

            _log_action(
                request.user,
                "payment",
                "Sales invoice payment recorded",
                {
                    "invoice_id": invoice.id,
                    "payment_ids": created_payment_ids,
                    "amount": str(amount),
                    "credit_applied": str(credit_to_apply),
                    "overpayment_credit": str(incoming_overpayment),
                    "collection_status": final_collection_status,
                },
                required=True,
            )
        if incoming_overpayment > Decimal("0.00") and credit_to_apply > Decimal("0.00"):
            messages.success(
                request,
                f"Payment saved for {invoice.invoice_no}. "
                f"Applied ${credit_to_apply} from account credit and stored ${incoming_overpayment} back as new credit.",
            )
        elif incoming_overpayment > Decimal("0.00"):
            messages.success(
                request,
                f"Payment of ${amount} recorded for {invoice.invoice_no}. "
                f"${incoming_overpayment} was added to {invoice.customer.name}'s account credit.",
            )
        elif credit_to_apply > Decimal("0.00") and amount > Decimal("0.00"):
            messages.success(
                request,
                f"Payment of ${amount} recorded and ${credit_to_apply} of customer credit applied to {invoice.invoice_no}.",
            )
        elif credit_to_apply > Decimal("0.00"):
            messages.success(
                request,
                f"${credit_to_apply} of customer credit applied to {invoice.invoice_no}.",
            )
        else:
            messages.success(request, f"Payment of ${amount} recorded for {invoice.invoice_no}.")
        if final_collection_status == "awaiting_collection":
            messages.info(
                request,
                "The paid items are now listed under Remaining In Store until the customer picks them up.",
            )
        elif final_collection_status == "collected_immediately":
            messages.info(request, "The paid items were recorded as collected at the time of payment.")
        return redirect("sales_invoice_detail", invoice_id=invoice.id)

    context = {
        "profile": UserProfile.for_user(request.user),
        "invoice": invoice,
        "paid_total": paid_total,
        "balance_due": balance_due,
        "payment_methods": _manual_sales_invoice_payment_methods(),
        "payments": payments,
        "default_payment_amount": Decimal("0.00"),
        "default_payment_method": default_payment_method,
        "default_payment_datetime": default_payment_datetime,
        "overpayment_amount": overpayment_amount,
        "customer_credit_balance": _customer_credit_balance(invoice.customer),
        "available_customer_credit": available_customer_credit,
        "can_revert_payments": _can_revert_sales_invoice_payments(request.user),
    }
    return render(request, "inventory/sales_invoice_payment_form.html", context)


@login_required
@role_required(["admin"])
@require_POST
def sales_invoice_payment_revert(request, invoice_id, payment_id):
    owner_user = _inventory_owner_for_user(request.user)
    invoices = SalesInvoice.objects.select_related("customer")
    if not owner_user.is_superuser:
        invoices = invoices.filter(owner=owner_user)

    invoice = get_object_or_404(invoices, pk=invoice_id)
    if not _can_manage_sales_invoice_at_active_location(request.user, invoice):
        raise Http404("Invoice is outside your active location.")

    reversal_reason = (request.POST.get("reversal_reason") or "").strip()
    if not reversal_reason:
        messages.error(request, "Enter a reason before reversing this payment.")
        return redirect("sales_invoice_payment", invoice_id=invoice.id)

    try:
        with transaction.atomic():
            locked_invoice = get_object_or_404(
                invoices.select_for_update().select_related("customer", "location"),
                pk=invoice.id,
            )
            payment = get_object_or_404(
                SalesInvoicePayment.objects.select_for_update().select_related(
                    "invoice",
                    "customer",
                    "location",
                ),
                pk=payment_id,
                invoice=locked_invoice,
            )

            if SalesInvoicePaymentReversal.objects.filter(payment=payment).exists():
                messages.info(request, "This payment has already been reversed.")
                return redirect("sales_invoice_payment", invoice_id=locked_invoice.id)

            locked_customer = None
            if payment.customer_id:
                locked_customer = Customer.objects.select_for_update().get(pk=payment.customer_id)

            overpayment_credit = (payment.credited_customer_overpayment or Decimal("0.00")).quantize(Decimal("0.01"))
            applied_credit = (payment.applied_customer_credit or Decimal("0.00")).quantize(Decimal("0.01"))

            if (overpayment_credit > Decimal("0.00") or applied_credit > Decimal("0.00")) and not locked_customer:
                messages.error(
                    request,
                    "This payment cannot be reversed because its original customer account is unavailable.",
                )
                return redirect("sales_invoice_payment", invoice_id=locked_invoice.id)
            if overpayment_credit > Decimal("0.00") and _customer_credit_balance(locked_customer) < overpayment_credit:
                messages.error(
                    request,
                    f"Cannot reverse this payment because ${overpayment_credit} of its credited overpayment has already been used.",
                )
                return redirect("sales_invoice_payment", invoice_id=locked_invoice.id)

            reversal = SalesInvoicePaymentReversal.objects.create(
                payment=payment,
                reversed_by=request.user,
                reason=reversal_reason,
            )

            if overpayment_credit > Decimal("0.00"):
                locked_customer.use_credit(
                    overpayment_credit,
                    movement_type="payment_reversal",
                    created_by=request.user,
                    payment_reversal=reversal,
                    reason=f"Removed overpayment credit from reversed payment {payment.id}.",
                )

            if applied_credit > Decimal("0.00"):
                locked_customer.add_credit(
                    applied_credit,
                    movement_type="payment_reversal",
                    created_by=request.user,
                    payment_reversal=reversal,
                    reason=f"Restored account credit from reversed payment {payment.id}.",
                )

            payment_amount = (payment.amount or Decimal("0.00")).quantize(Decimal("0.01"))
            payment_method = payment.get_payment_method_display()
            _refresh_sales_invoice_status(locked_invoice)
            _log_action(
                request.user,
                "payment",
                "Sales invoice payment reversed",
                {
                    "invoice_id": locked_invoice.id,
                    "payment_id": payment.id,
                    "reversal_id": reversal.id,
                    "reason": reversal_reason,
                    "payment_amount": str(payment_amount),
                    "payment_method": payment_method,
                    "applied_customer_credit": str(applied_credit),
                    "credited_customer_overpayment": str(overpayment_credit),
                    "owner_id": payment.owner_id,
                    "location_id": payment.location_id,
                },
                severity="warn",
                required=True,
            )
    except IntegrityError:
        messages.info(request, "This payment has already been reversed.")
        return redirect("sales_invoice_payment", invoice_id=invoice.id)

    messages.success(request, f"Reverted {payment_method} payment of ${payment_amount} on {invoice.invoice_no}.")
    return redirect("sales_invoice_payment", invoice_id=invoice.id)

@login_required
@require_POST
def super_admin_update_subscription(request, profile_id):
    messages.info(
        request,
        "Subscription update has not been implemented yet."
    )
    return redirect("super_admin_dashboard")
