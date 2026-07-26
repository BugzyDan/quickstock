"""
Network helpers for desktop/API communication.
"""

import os
from urllib.parse import urlparse


DEFAULT_API_BASE_URL = "http://localhost:8000"
LOCAL_API_HOSTS = {"localhost", "127.0.0.1", "::1"}


def normalize_api_base_url(base_url: str, default: str = DEFAULT_API_BASE_URL) -> str:
    """Normalize a configured API base URL for consistent request building."""
    normalized = (base_url or default).strip()
    for suffix in ("/inventory", "/inventory/"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    return normalized.rstrip("/")


def is_local_api_url(base_url: str) -> bool:
    """Return True when the API endpoint targets a local development host."""
    normalized = normalize_api_base_url(base_url)
    if not normalized:
        return False

    parsed = urlparse(normalized if "://" in normalized else f"http://{normalized}")
    return (parsed.hostname or "").lower() in LOCAL_API_HOSTS


def requires_https_in_production(base_url: str, env: str | None = None) -> bool:
    """
    Return True when a production API URL should be rejected for not using HTTPS.

    Local development hosts remain allowed over HTTP so local setups continue to work.
    """
    current_env = (env or os.getenv("QUICKSTOCK_ENV", "")).strip().lower()
    normalized = normalize_api_base_url(base_url)
    if current_env != "production" or not normalized or is_local_api_url(normalized):
        return False

    parsed = urlparse(normalized if "://" in normalized else f"http://{normalized}")
    return parsed.scheme != "https"


def validate_api_base_url(base_url: str, env: str | None = None) -> tuple[bool, str]:
    """Validate an API base URL before saving or using it."""
    normalized = normalize_api_base_url(base_url)
    if not normalized:
        return False, "API Base URL is required."

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False, "API Base URL must include http:// or https://."

    if requires_https_in_production(normalized, env):
        return False, "Production API URLs must use HTTPS unless they point to localhost."

    return True, ""


def build_api_url(base_url: str, path: str) -> str:
    """Build a full API URL from a normalized base URL and request path."""
    normalized = normalize_api_base_url(base_url)
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{normalized}{path}"
