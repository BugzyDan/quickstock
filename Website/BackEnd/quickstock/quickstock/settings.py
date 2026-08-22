import os
import sys
import warnings
from pathlib import Path
from urllib.parse import urlparse, unquote
from dotenv import load_dotenv

# 1. Define BASE_DIR first
BASE_DIR = Path(__file__).resolve().parent.parent.parent # Adjust .parent count as needed

# 2. Load local environment without hardcoding a developer machine path.
dotenv_candidates = [
    Path(os.getenv("DJANGO_ENV_FILE", "")) if os.getenv("DJANGO_ENV_FILE") else None,
    BASE_DIR / ".env",
    BASE_DIR.parent / ".env",
    BASE_DIR.parent.parent / ".env",
]
for dotenv_path in (path for path in dotenv_candidates if path):
    if dotenv_path.exists():
        load_dotenv(dotenv_path)
        break

# Now your other imports and logic follow...

# -----------------------------------------------------
# WiPay Settings
# -----------------------------------------------------
WIPAY_ACCOUNT_NUMBER_SANDBOX = os.getenv("WIPAY_ACCOUNT_NUMBER_SANDBOX")
WIPAY_API_KEY_SANDBOX = os.getenv("WIPAY_API_KEY_SANDBOX")

# -----------------------------------------------------
# Add Apps Directory To Python Path
# -----------------------------------------------------
if (BASE_DIR / "apps").exists():
    sys.path.insert(0, str(BASE_DIR / "apps"))

# -----------------------------------------------------
# Environment Helper
# -----------------------------------------------------
def get_env_or_raise(name: str) -> str:
    value = os.getenv(name)

    if not value:
        if not DEBUG:
            raise RuntimeError(
                f"CRITICAL: Environment variable {name} is missing in production!"
            )
        return ""

    return value.strip()

# -----------------------------------------------------
# Media Files
# -----------------------------------------------------
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-dev-key")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = _env_bool("DJANGO_DEBUG", True)

# Dev/LAN friendly defaults.
raw_hosts = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,192.168.50.223")
ALLOWED_HOSTS = [h.strip() for h in raw_hosts.split(",") if h.strip()]

# Quality-of-life for local/mobile testing: allow any host while in DEBUG.
# Set DJANGO_ALLOW_ALL_HOSTS_IN_DEBUG=False to disable this behavior.
if DEBUG and _env_bool("DJANGO_ALLOW_ALL_HOSTS_IN_DEBUG", True):
    ALLOWED_HOSTS = ["*"]
else:
    SECURE_BROWSER_XSS_FILTER = True

# quickstock/settings.py
AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',  # username/password
    
]

SOCIAL_AUTH_PROVIDERS = {
    "google": {
        "label": "Google",
        "client_id": _env_str("GOOGLE_OAUTH_CLIENT_ID"),
        "client_secret": _env_str("GOOGLE_OAUTH_CLIENT_SECRET"),
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": ["openid", "email", "profile"],
    },
    "microsoft": {
        "label": "Microsoft",
        "client_id": _env_str("MICROSOFT_OAUTH_CLIENT_ID"),
        "client_secret": _env_str("MICROSOFT_OAUTH_CLIENT_SECRET"),
        "tenant_id": _env_str("MICROSOFT_OAUTH_TENANT_ID", "common"),
        "scope": ["openid", "email", "profile", "User.Read"],
    },
}

XERO_CLIENT_ID = _env_str("XERO_CLIENT_ID")
XERO_CLIENT_SECRET = _env_str("XERO_CLIENT_SECRET")
XERO_REDIRECT_URI = _env_str("XERO_REDIRECT_URI")
XERO_WEBHOOK_KEY = _env_str("XERO_WEBHOOK_KEY")
ACCOUNTING_SYNC_BATCH_SIZE = _env_int("ACCOUNTING_SYNC_BATCH_SIZE", 50)

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework.authtoken',

    'inventory.apps.InventoryConfig',  # keep this
    'api.apps.ApiConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'quickstock.middleware.SecuritySessionMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'quickstock.middleware.SubscriptionEnforcementMiddleware',
    'quickstock.middleware.ThemeMiddleware',
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'quickstock.auth.SubscriptionTokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle'
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.getenv('DJANGO_API_THROTTLE_ANON', '60/minute'),
        'user': os.getenv('DJANGO_API_THROTTLE_USER', '600/minute'),
    },
}


ROOT_URLCONF = 'quickstock.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # Add this line if not already present
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'inventory.context_processors.starter_plan_context',
                'inventory.context_processors.session_security_context',
            ],
        },
    },
]


WSGI_APPLICATION = 'quickstock.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_ENGINE = os.getenv("DJANGO_DB_ENGINE", "").strip()
RUNNING_TESTS = (
    "test" in sys.argv
    or "PYTEST_CURRENT_TEST" in os.environ
    or "pytest" in Path(sys.argv[0]).name
    or "pytest" in sys.modules
)

def _database_from_url(database_url: str) -> dict:
    parsed = urlparse(database_url)
    scheme = parsed.scheme.lower()
    engine_map = {
        "postgres": "django.db.backends.postgresql",
        "postgresql": "django.db.backends.postgresql",
        "psql": "django.db.backends.postgresql",
        "mysql": "django.db.backends.mysql",
        "mysql2": "django.db.backends.mysql",
        "sqlite": "django.db.backends.sqlite3",
        "sqlite3": "django.db.backends.sqlite3",
    }
    engine = engine_map.get(scheme)
    if not engine:
        raise RuntimeError(f"Unsupported DATABASE_URL scheme: {scheme}")

    if engine == "django.db.backends.sqlite3":
        return {
            "ENGINE": engine,
            "NAME": unquote(parsed.path.lstrip("/")) or BASE_DIR / "db.sqlite3",
            "OPTIONS": {"timeout": _env_int("SQLITE_BUSY_TIMEOUT_SECONDS", 30)},
        }

    return {
        "ENGINE": engine,
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "",
        "PORT": str(parsed.port or ""),
    }


if DATABASE_URL:
    DATABASES = {"default": _database_from_url(DATABASE_URL)}
else:
    if DB_ENGINE:
        db_engine = DB_ENGINE
    elif DEBUG or RUNNING_TESTS:
        db_engine = "django.db.backends.sqlite3"
    else:
        db_engine = "django.db.backends.mysql"

    if db_engine == "django.db.backends.sqlite3":
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": os.getenv("DJANGO_DB_NAME", BASE_DIR / "db.sqlite3"),
                "OPTIONS": {
                    "timeout": _env_int("SQLITE_BUSY_TIMEOUT_SECONDS", 30),
                },
            }
        }
    else:
        db_name = get_env_or_raise("DJANGO_DB_NAME")
        db_user = get_env_or_raise("DJANGO_DB_USER")
        db_password = get_env_or_raise("DJANGO_DB_PASSWORD")
        db_host = os.getenv("DJANGO_DB_HOST", "localhost")
        db_port = os.getenv("DJANGO_DB_PORT", "3306") # Define db_port here

        DATABASES = {
            "default": {
                "ENGINE": db_engine,
                "NAME": db_name,
                "USER": db_user,
                "PASSWORD": db_password,
                "HOST": db_host,
                "PORT": db_port,
                "OPTIONS": {
                    "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
                },
            }
        }


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = os.getenv('TIME_ZONE', 'America/Jamaica')

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = '/static/'

# App static assets are discovered via AppDirectoriesFinder.
# Leave this empty unless you add a separate global static folder to avoid duplicate collection.
STATICFILES_DIRS = []

# Add this:
STATIC_ROOT = BASE_DIR / 'staticfiles'  # destination for collectstatic
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
# Ensure the directory exists to avoid startup failures before collectstatic runs.
STATIC_ROOT.mkdir(parents=True, exist_ok=True)

# Media files (uploads: e.g., branded logos)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field


DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = 'login'
QUICKSTOCK_IDLE_TIMEOUT_SECONDS = max(300, _env_int("QUICKSTOCK_IDLE_TIMEOUT_SECONDS", 8 * 60 * 60))
QUICKSTOCK_ENFORCE_SESSION_FINGERPRINT = _env_bool("QUICKSTOCK_ENFORCE_SESSION_FINGERPRINT", False)
QUICKSTOCK_SECURITY_HEARTBEAT_SECONDS = max(
    60,
    min(
        QUICKSTOCK_IDLE_TIMEOUT_SECONDS - 60,
        _env_int(
            "QUICKSTOCK_SECURITY_HEARTBEAT_SECONDS",
            min(600, max(120, QUICKSTOCK_IDLE_TIMEOUT_SECONDS // 12)),
        ),
    ),
)

# Email backend
# If DJANGO_EMAIL_BACKEND is set, always use it (even in DEBUG).
EMAIL_BACKEND = os.getenv("DJANGO_EMAIL_BACKEND", "").strip()
if not EMAIL_BACKEND:
    # Prefer real SMTP if host/user are provided, even in DEBUG
    if os.getenv("DJANGO_EMAIL_HOST") or os.getenv("DJANGO_EMAIL_HOST_USER"):
        EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    elif DEBUG:
        EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
    else:
        EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"

DEFAULT_FROM_EMAIL = os.getenv("DJANGO_DEFAULT_FROM_EMAIL", "webmaster@localhost")
EMAIL_HOST = os.getenv("DJANGO_EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("DJANGO_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = _env_bool("DJANGO_EMAIL_USE_TLS", True)
EMAIL_USE_SSL = _env_bool("DJANGO_EMAIL_USE_SSL", False)

if not DEBUG:
    if not EMAIL_HOST:
        raise RuntimeError("DJANGO_EMAIL_HOST must be set in production!")
    if not EMAIL_HOST_USER:
        raise RuntimeError("DJANGO_EMAIL_HOST_USER must be set in production!")
    if not EMAIL_HOST_PASSWORD:
        raise RuntimeError("DJANGO_EMAIL_HOST_PASSWORD must be set in production!")

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")  # for subscriptions

# JAM-DEX (optional)
JAMDEX_API_URL = os.getenv("JAMDEX_API_URL", "").strip()
JAMDEX_MERCHANT_ID = os.getenv("JAMDEX_MERCHANT_ID", "").strip()
JAMDEX_API_KEY = os.getenv("JAMDEX_API_KEY", "").strip()
JAMDEX_CALLBACK_SECRET = os.getenv("JAMDEX_CALLBACK_SECRET", "").strip()

# =========================
# WiPay (Jamaica) Settings
# =========================

import os

# Environment: 'sandbox' or 'live'
WIPAY_ENVIRONMENT = os.getenv("WIPAY_ENVIRONMENT", "sandbox").strip().lower()
if WIPAY_ENVIRONMENT not in {"sandbox", "live"}:
    WIPAY_ENVIRONMENT = "sandbox"

# -------------------------
# Sandbox credentials
# -------------------------
WIPAY_ACCOUNT_NUMBER_SANDBOX = os.getenv("WIPAY_ACCOUNT_NUMBER_SANDBOX")
WIPAY_API_KEY_SANDBOX = os.getenv("WIPAY_API_KEY_SANDBOX")

# -------------------------
# Live credentials
# -------------------------
WIPAY_ACCOUNT_NUMBER_LIVE = os.getenv("WIPAY_ACCOUNT_NUMBER_LIVE", "")
WIPAY_API_KEY_LIVE = os.getenv("WIPAY_API_KEY_LIVE", "")

# -------------------------
# Select active credentials
# -------------------------
if WIPAY_ENVIRONMENT == "live":
    WIPAY_ACCOUNT_NUMBER = WIPAY_ACCOUNT_NUMBER_LIVE
    WIPAY_API_KEY = WIPAY_API_KEY_LIVE
else:  # sandbox
    WIPAY_ACCOUNT_NUMBER = WIPAY_ACCOUNT_NUMBER_SANDBOX
    WIPAY_API_KEY = WIPAY_API_KEY_SANDBOX

# -------------------------
# Common WiPay settings
# -------------------------
WIPAY_FEE_STRUCTURE = os.getenv("WIPAY_FEE_STRUCTURE", "merchant_absorb")
WIPAY_METHOD = os.getenv("WIPAY_METHOD", "credit_card")
WIPAY_ORIGIN = os.getenv("WIPAY_ORIGIN", "QuickStock JA")
WIPAY_COUNTRY_CODE = os.getenv("WIPAY_COUNTRY_CODE", "JM")
WIPAY_CURRENCY = os.getenv("WIPAY_CURRENCY", "JMD")
WIPAY_ENDPOINT = os.getenv(
    "WIPAY_ENDPOINT",
    "https://jm.wipayfinancial.com/plugins/payments/request",
)
WIPAY_PRO_PRICE = float(os.getenv("WIPAY_PRO_PRICE", "30400.00"))
WIPAY_PRO_PRICE_MONTHLY = float(os.getenv("WIPAY_PRO_PRICE_MONTHLY", "2600.00"))
WIPAY_ALLOW_SANDBOX_IN_PROD = _env_bool("WIPAY_ALLOW_SANDBOX_IN_PROD", False)

# Starter plan limits
STARTER_ITEM_LIMIT = int(os.getenv("STARTER_ITEM_LIMIT", "100"))


# -------------------------
# Ensure sandbox/live credentials are present
# -------------------------
if WIPAY_ENVIRONMENT == "sandbox" and (not WIPAY_ACCOUNT_NUMBER_SANDBOX or not WIPAY_API_KEY_SANDBOX):
    warnings.warn(
        "Sandbox WiPay credentials are missing. Card payments will be unavailable until "
        "WIPAY_ACCOUNT_NUMBER_SANDBOX and WIPAY_API_KEY_SANDBOX are set.",
        RuntimeWarning,
    )

if WIPAY_ENVIRONMENT == "live" and (not WIPAY_ACCOUNT_NUMBER_LIVE or not WIPAY_API_KEY_LIVE):
    warnings.warn(
        "Live WiPay credentials are missing. Card payments will be unavailable until "
        "WIPAY_ACCOUNT_NUMBER_LIVE and WIPAY_API_KEY_LIVE are set.",
        RuntimeWarning,
    )

if not DEBUG and WIPAY_ENVIRONMENT != "live" and not WIPAY_ALLOW_SANDBOX_IN_PROD:
    raise RuntimeError("WIPAY_ENVIRONMENT must be 'live' in production!")

# Audit log retention (days)
AUDIT_LOG_RETENTION_DAYS = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "90"))
API_TOKEN_MAX_AGE = int(os.getenv("API_TOKEN_MAX_AGE", "86400"))
API_LOGIN_RATE_LIMIT_ATTEMPTS = int(os.getenv("API_LOGIN_RATE_LIMIT_ATTEMPTS", "10"))
API_LOGIN_RATE_LIMIT_WINDOW = int(os.getenv("API_LOGIN_RATE_LIMIT_WINDOW", "300"))
SUPPORT_TICKET_RATE_LIMIT = _env_int("SUPPORT_TICKET_RATE_LIMIT", 5)
SUPPORT_TICKET_RATE_WINDOW = _env_int("SUPPORT_TICKET_RATE_WINDOW", 900)
API_SYNC_MAX_INVENTORY_ITEMS = _env_int("API_SYNC_MAX_INVENTORY_ITEMS", 1000)
API_SYNC_MAX_SALES = _env_int("API_SYNC_MAX_SALES", 500)
POS_CART_MAX_LINES = _env_int("POS_CART_MAX_LINES", 200)
LOGIN_OTP_MAX_ATTEMPTS = _env_int("LOGIN_OTP_MAX_ATTEMPTS", 5)
LOGIN_OTP_RATE_WINDOW = _env_int("LOGIN_OTP_RATE_WINDOW", 900)
DOWNLOAD_SOFTWARE_URL = os.getenv("DOWNLOAD_SOFTWARE_URL", "").strip()
WIPAY_CONTACT_PHONE_DEFAULT = os.getenv("WIPAY_CONTACT_PHONE_DEFAULT", "").strip()
RECEIPT_LOGO_MAX_BYTES = _env_int("RECEIPT_LOGO_MAX_BYTES", 2 * 1024 * 1024)
RECEIPT_LOGO_MAX_DIMENSION = _env_int("RECEIPT_LOGO_MAX_DIMENSION", 4096)

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
if not DEBUG and not CSRF_TRUSTED_ORIGINS:
    raise RuntimeError("CSRF_TRUSTED_ORIGINS must be set in production!")

SESSION_COOKIE_SECURE = _env_bool("DJANGO_SESSION_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_SECURE = _env_bool("DJANGO_CSRF_COOKIE_SECURE", not DEBUG)
SESSION_COOKIE_HTTPONLY = True
# Keep CSRF readable by JavaScript because the current POS scripts submit AJAX CSRF headers.
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_SAMESITE = os.getenv('DJANGO_SESSION_COOKIE_SAMESITE', 'Lax')
CSRF_COOKIE_SAMESITE = os.getenv('DJANGO_CSRF_COOKIE_SAMESITE', 'Lax')
SECURE_SSL_REDIRECT = _env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "0" if DEBUG else "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", not DEBUG)
SECURE_HSTS_PRELOAD = _env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_REFERRER_POLICY = os.getenv("DJANGO_SECURE_REFERRER_POLICY", "same-origin")
SECURE_CROSS_ORIGIN_OPENER_POLICY = os.getenv("DJANGO_SECURE_CROSS_ORIGIN_OPENER_POLICY", "same-origin")
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

if not DEBUG and SECRET_KEY == "unsafe-dev-key":
    raise RuntimeError("DJANGO_SECRET_KEY must be set in production.")

# Fail-fast checks for production
if not DEBUG:
    if SECRET_KEY in ("unsafe-dev-key", ""):
        raise RuntimeError("DJANGO_SECRET_KEY must be set in production!")

    if not ALLOWED_HOSTS or ALLOWED_HOSTS == ["localhost"] or "yourdomain.com" in ALLOWED_HOSTS:
        raise RuntimeError("DJANGO_ALLOWED_HOSTS must be set to your real domain(s) in production!")

    # SQLite in production is discouraged but allowed here; ensure durability requirements are understood.
    if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
        warnings.warn(
            "Using SQLite in production; consider MySQL/Postgres for durability and concurrency.",
            RuntimeWarning,
        )

    # Database requirements
    if not DATABASES["default"].get("NAME"):
        raise RuntimeError("DJANGO_DB_NAME must be set in production!")
    if DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3" and not DATABASES["default"].get("HOST"):
        raise RuntimeError("DJANGO_DB_HOST must be set in production!")

    # HTTPS / cookie hardening
    if not SESSION_COOKIE_SECURE:
        raise RuntimeError("SESSION_COOKIE_SECURE must be True in production!")
    if not CSRF_COOKIE_SECURE:
        raise RuntimeError("CSRF_COOKIE_SECURE must be True in production!")
    if not SECURE_SSL_REDIRECT:
        raise RuntimeError("SECURE_SSL_REDIRECT must be True in production!")
    if SECURE_HSTS_SECONDS <= 0:
        raise RuntimeError("SECURE_HSTS_SECONDS must be > 0 in production!")

# Basic logging for production readiness
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": BASE_DIR / "quickstock.log",
            "maxBytes": 1024 * 1024 * 10,  # 10MB
            "backupCount": 5,
            "formatter": "standard",
        },
    },
    "loggers": {
        "django": {"handlers": ["console", "file"], "level": "INFO", "propagate": True},
        "inventory": {"handlers": ["console", "file"], "level": "INFO", "propagate": False},
    },
}

if not DEBUG:
    log_path = BASE_DIR / "quickstock.log"
    if log_path.exists() and not os.access(log_path, os.W_OK):
        raise RuntimeError("quickstock.log is not writable in production!")


SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
SENTRY_ENVIRONMENT = os.getenv("SENTRY_ENVIRONMENT", "development" if DEBUG else "production").strip()
SENTRY_TRACES_SAMPLE_RATE = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0"))
SENTRY_PROFILES_SAMPLE_RATE = float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0"))
SENTRY_SEND_PII = _env_bool("SENTRY_SEND_DEFAULT_PII", False)

if SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration
    except Exception as exc:
        if not DEBUG:
            raise RuntimeError("SENTRY_DSN is set but sentry-sdk is not installed.") from exc
    else:
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=SENTRY_ENVIRONMENT,
            integrations=[DjangoIntegration()],
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            profiles_sample_rate=SENTRY_PROFILES_SAMPLE_RATE,
            send_default_pii=SENTRY_SEND_PII,
        )
