import logging
import os
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse

from .email_utils import email_delivery_status


logger = logging.getLogger("inventory")


def _failure_detail(label, exc):
    logger.exception("Runtime health %s check failed", label)
    if settings.DEBUG:
        return f"{label.title()} check failed: {exc}"
    return f"{label.title()} check failed. Check the server logs."


def _path_is_writable(path_value):
    path = Path(path_value)
    if path.exists():
        if path.is_dir():
            return os.access(path, os.W_OK)
        return os.access(path, os.W_OK)

    candidate = path.parent
    return candidate.exists() and candidate.is_dir() and os.access(candidate, os.W_OK)


def get_runtime_health():
    checks = {}

    try:
        connection = connections["default"]
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = {"ok": True, "detail": "Database connection is healthy."}
    except Exception as exc:
        checks["database"] = {"ok": False, "detail": _failure_detail("database", exc)}
        connection = None

    try:
        if connection is None:
            raise RuntimeError("Database unavailable")
        executor = MigrationExecutor(connection)
        targets = executor.loader.graph.leaf_nodes()
        plan = executor.migration_plan(targets)
        if plan:
            pending = [f"{migration.app_label}.{migration.name}" for migration, _ in plan]
            checks["migrations"] = {
                "ok": False,
                "detail": "Unapplied migrations detected.",
                "pending": pending,
            }
        else:
            checks["migrations"] = {"ok": True, "detail": "All migrations are applied."}
    except Exception as exc:
        checks["migrations"] = {"ok": False, "detail": _failure_detail("migration", exc)}

    media_provider = getattr(settings, "MEDIA_STORAGE_PROVIDER", "filesystem")
    media_root = getattr(settings, "MEDIA_ROOT", "")
    if media_provider == "s3":
        media_configured = bool(
            getattr(settings, "AWS_STORAGE_BUCKET_NAME", "")
            and getattr(settings, "AWS_ACCESS_KEY_ID", "")
            and getattr(settings, "AWS_SECRET_ACCESS_KEY", "")
        )
        media_persistent = media_configured
        media_detail = (
            "Persistent object storage is configured."
            if media_configured
            else "S3 media storage settings are incomplete."
        )
        media_ok = media_configured
    else:
        media_writable = bool(media_root and _path_is_writable(media_root))
        media_persistent = not bool(os.getenv("RENDER"))
        media_ok = media_writable and (
            media_persistent
            or not getattr(settings, "QUICKSTOCK_REQUIRE_PERSISTENT_MEDIA", False)
        )
        media_detail = (
            "Media storage is writable."
            if media_persistent and media_writable
            else "Media storage is writable but ephemeral on Render."
            if media_writable
            else "Media storage path is not writable."
        )
    checks["media_storage"] = {
        "ok": media_ok,
        "persistent": media_persistent,
        "provider": media_provider,
        "detail": media_detail,
    }

    cache_probe_key = "quickstock:health:cache"
    try:
        cache.set(cache_probe_key, "ok", timeout=30)
        cache_ok = cache.get(cache_probe_key) == "ok"
        cache.delete(cache_probe_key)
        checks["cache"] = {
            "ok": cache_ok,
            "detail": "Shared cache is readable and writable." if cache_ok else "Cache probe did not round-trip.",
        }
    except Exception as exc:
        checks["cache"] = {"ok": False, "detail": _failure_detail("cache", exc)}

    checks["email"] = email_delivery_status()

    file_handlers = getattr(settings, "LOGGING", {}).get("handlers", {})
    file_handler = file_handlers.get("file", {})
    log_path = file_handler.get("filename")
    if log_path:
        log_writable = _path_is_writable(log_path)
        checks["logging"] = {
            "ok": log_writable,
            "detail": (
                "Application log path is writable."
                if log_writable
                else "Application log path is not writable in the current environment."
            ),
        }
    else:
        checks["logging"] = {"ok": True, "detail": "No file logger configured."}

    payments_configured = bool(getattr(settings, "WIPAY_ACCOUNT_NUMBER", "") and getattr(settings, "WIPAY_API_KEY", ""))
    jamdex_enabled = bool(getattr(settings, "JAMDEX_API_URL", "") and getattr(settings, "JAMDEX_MERCHANT_ID", ""))
    checks["payments"] = {
        "ok": True,
        "detail": "At least one payment path is configured." if (payments_configured or jamdex_enabled) else "No live payment provider is configured.",
        "providers": {
            "wipay_configured": payments_configured,
            "jamdex_configured": jamdex_enabled,
        },
    }

    overall_ok = all(data.get("ok") for key, data in checks.items() if key != "payments")
    return {
        "status": "ok" if overall_ok else "degraded",
        "checks": checks,
    }


def readiness_view(request):
    """Lightweight Render probe for database, shared cache, and login email."""
    checks = {}
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = {"ok": True}
    except Exception as exc:
        checks["database"] = {"ok": False, "detail": _failure_detail("database", exc)}

    probe_key = "quickstock:readiness:cache"
    try:
        cache.set(probe_key, "ok", timeout=15)
        cache_ok = cache.get(probe_key) == "ok"
        cache.delete(probe_key)
        checks["cache"] = {"ok": cache_ok}
    except Exception as exc:
        checks["cache"] = {"ok": False, "detail": _failure_detail("cache", exc)}

    checks["email"] = email_delivery_status()
    ready = all(check.get("ok") for check in checks.values())
    return JsonResponse(
        {"status": "ok" if ready else "degraded", "checks": checks},
        status=200 if ready else 503,
    )
