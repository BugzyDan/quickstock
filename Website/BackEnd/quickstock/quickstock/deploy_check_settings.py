import os

os.environ.setdefault("DJANGO_SECRET_KEY", "deploy-check-secret-key-for-quickstock-production-validation-1234567890")
os.environ.setdefault("DJANGO_DEBUG", "false")
os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "deploy-check.quickstockja.com")
os.environ.setdefault("DJANGO_CSRF_TRUSTED_ORIGINS", "https://deploy-check.quickstockja.com")
os.environ.setdefault("DJANGO_SESSION_COOKIE_SECURE", "true")
os.environ.setdefault("DJANGO_CSRF_COOKIE_SECURE", "true")
os.environ.setdefault("DJANGO_SECURE_SSL_REDIRECT", "true")
os.environ.setdefault("DJANGO_SECURE_HSTS_SECONDS", "31536000")
os.environ.setdefault("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", "true")
os.environ.setdefault("DJANGO_SECURE_HSTS_PRELOAD", "true")
os.environ.setdefault("DJANGO_EMAIL_HOST", "smtp.example.com")
os.environ.setdefault("DJANGO_EMAIL_HOST_USER", "deploy-check@example.com")
os.environ.setdefault("DJANGO_EMAIL_HOST_PASSWORD", "replace-me")
os.environ.setdefault("DJANGO_DB_ENGINE", "django.db.backends.sqlite3")
os.environ.setdefault("DJANGO_DB_NAME", "deploy_check.sqlite3")
os.environ.setdefault("WIPAY_ENVIRONMENT", "live")
os.environ.setdefault("WIPAY_ACCOUNT_NUMBER_LIVE", "replace-me")
os.environ.setdefault("WIPAY_API_KEY_LIVE", "replace-me")

from .settings import *  # noqa: F403,F401


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(BASE_DIR / "deploy_check.sqlite3"),  # noqa: F405
    }
}
