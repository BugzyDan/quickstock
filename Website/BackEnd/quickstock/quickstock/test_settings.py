from pathlib import Path

from .settings import *  # noqa: F403,F401


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(BASE_DIR / "test_db.sqlite3"),  # noqa: F405
    }
}
