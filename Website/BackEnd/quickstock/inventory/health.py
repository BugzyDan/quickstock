import os
from pathlib import Path

from django.conf import settings
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


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
        checks["database"] = {"ok": False, "detail": f"Database check failed: {exc}"}
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
        checks["migrations"] = {"ok": False, "detail": f"Migration check failed: {exc}"}

    media_root = getattr(settings, "MEDIA_ROOT", "")
    checks["media_storage"] = {
        "ok": bool(media_root and _path_is_writable(media_root)),
        "detail": "Media storage is writable." if media_root and _path_is_writable(media_root) else "Media storage path is not writable.",
    }

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
