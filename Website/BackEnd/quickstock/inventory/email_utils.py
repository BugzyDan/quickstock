from email.utils import parseaddr

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string


SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
CONSOLE_BACKEND = "django.core.mail.backends.console.EmailBackend"
LOCMEM_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
RESEND_BACKEND = "anymail.backends.resend.EmailBackend"

PLACEHOLDER_SENDER_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "your-verified-domain",
    "your-verified-domain.example",
}
PUBLIC_MAILBOX_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "hotmail.com",
    "icloud.com",
    "live.com",
    "outlook.com",
    "yahoo.com",
}


def _sender_status(default_from, *, provider):
    sender = parseaddr(default_from)[1].strip().lower()
    domain = sender.rsplit("@", 1)[1] if "@" in sender else ""

    if not sender or "@" not in sender or not domain:
        return False, "A valid DEFAULT_FROM_EMAIL sender address is required."
    if domain == "localhost" or domain.endswith(".localhost"):
        return False, "DEFAULT_FROM_EMAIL cannot use a localhost sender in production."
    if domain in PLACEHOLDER_SENDER_DOMAINS or domain.endswith(".example"):
        return False, "DEFAULT_FROM_EMAIL must use a real sender on your verified email domain."
    if provider == "resend" and domain in PUBLIC_MAILBOX_DOMAINS:
        return False, "Resend requires DEFAULT_FROM_EMAIL to use a domain verified in Resend, not a public mailbox domain."
    return True, "Sender address is configured."


def email_delivery_status():
    """Return a non-secret summary of whether this process can deliver email."""
    backend = str(getattr(settings, "EMAIL_BACKEND", "") or "")
    provider = str(getattr(settings, "EMAIL_PROVIDER", "") or "").lower()
    default_from = str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
    sender_ready, sender_detail = _sender_status(default_from, provider=provider)

    if provider == "resend" and backend != RESEND_BACKEND:
        return {
            "ok": False,
            "provider": provider,
            "backend": backend,
            "sender_set": sender_ready,
            "detail": (
                "Resend is selected but its HTTPS email backend is not active."
                if sender_ready
                else sender_detail
            ),
        }

    if backend == RESEND_BACKEND:
        key_ready = bool(getattr(settings, "RESEND_API_KEY", ""))
        ok = key_ready and sender_ready
        detail = (
            "Resend HTTPS email is configured."
            if ok
            else sender_detail
            if not sender_ready
            else "Resend requires RESEND_API_KEY."
        )
        return {
            "ok": ok,
            "provider": "resend",
            "backend": backend,
            "api_key_set": key_ready,
            "sender_set": sender_ready,
            "detail": detail,
        }

    if backend == SMTP_BACKEND:
        host_ready = bool(getattr(settings, "EMAIL_HOST", ""))
        user_ready = bool(getattr(settings, "EMAIL_HOST_USER", ""))
        password_ready = bool(getattr(settings, "EMAIL_HOST_PASSWORD", ""))
        ok = host_ready and user_ready and password_ready and sender_ready
        return {
            "ok": ok,
            "provider": provider or "smtp",
            "backend": backend,
            "host_set": host_ready,
            "user_set": user_ready,
            "password_set": password_ready,
            "sender_set": sender_ready,
            "detail": "SMTP email is configured." if ok else "SMTP email settings are incomplete.",
        }

    if backend in {CONSOLE_BACKEND, LOCMEM_BACKEND}:
        development_only = bool(
            getattr(settings, "DEBUG", False)
            or (
                getattr(settings, "RUNNING_TESTS", False)
                and provider not in {"resend"}
            )
        )
        return {
            "ok": development_only,
            "provider": provider or "development",
            "backend": backend,
            "sender_set": sender_ready,
            "detail": (
                "Development email backend is active."
                if development_only
                else "Production email delivery is not configured."
            ),
        }

    return {
        "ok": False,
        "provider": provider or "unknown",
        "backend": backend,
        "sender_set": sender_ready,
        "detail": "The configured email backend is not recognized.",
    }


def get_delivery_connection():
    """Create an email connection with SMTP-only timeout options."""
    if getattr(settings, "EMAIL_BACKEND", "") == SMTP_BACKEND:
        return get_connection(timeout=getattr(settings, "EMAIL_TIMEOUT", 5))
    return get_connection()


def send_verification_email(user_email, username, code):
    subject = "Verify your QuickStock Account"
    from_email = settings.DEFAULT_FROM_EMAIL
    
    # You can create a template at templates/emails/verify.html
    html_content = render_to_string('emails/verify.html', {
        'username': username,
        'code': code
    })
    
    connection = get_delivery_connection()
    msg = EmailMultiAlternatives(subject, "", from_email, [user_email], connection=connection)
    msg.attach_alternative(html_content, "text/html")
    msg.send()
