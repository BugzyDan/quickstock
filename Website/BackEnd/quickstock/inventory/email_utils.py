from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string


SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
CONSOLE_BACKEND = "django.core.mail.backends.console.EmailBackend"
LOCMEM_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
RESEND_BACKEND = "anymail.backends.resend.EmailBackend"


def email_delivery_status():
    """Return a non-secret summary of whether this process can deliver email."""
    backend = str(getattr(settings, "EMAIL_BACKEND", "") or "")
    provider = str(getattr(settings, "EMAIL_PROVIDER", "") or "").lower()
    default_from = str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
    sender_ready = bool(default_from and "@localhost" not in default_from.lower())

    if provider == "resend" and backend != RESEND_BACKEND:
        return {
            "ok": False,
            "provider": provider,
            "backend": backend,
            "sender_set": sender_ready,
            "detail": "Resend is selected but its HTTPS email backend is not active.",
        }

    if backend == RESEND_BACKEND:
        key_ready = bool(getattr(settings, "RESEND_API_KEY", ""))
        ok = key_ready and sender_ready
        detail = (
            "Resend HTTPS email is configured."
            if ok
            else "Resend requires RESEND_API_KEY and a verified sender address."
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
