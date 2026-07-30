# inventory/apps.py
import logging
import os

from django.apps import AppConfig
from django.db.backends.signals import connection_created


logger = logging.getLogger(__name__)


def configure_sqlite_connection(sender, connection, **kwargs):
    """Give local SQLite a little more room under browser/dev-server concurrency."""
    if connection.vendor != "sqlite":
        return

    try:
        timeout_ms = int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "30000"))
    except (TypeError, ValueError):
        timeout_ms = 30000
    with connection.cursor() as cursor:
        cursor.execute(f"PRAGMA busy_timeout = {timeout_ms}")
        try:
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
        except Exception:
            logger.debug("SQLite WAL pragmas were not applied for this connection.", exc_info=True)


class InventoryConfig(AppConfig):
    """
    Configuration for the Inventory app.
    Registers signal handlers used by inventory, finance, and local SQLite tuning.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "inventory"
    verbose_name = "Inventory & POS Management"

    def ready(self):
        try:
            import inventory.signals  # noqa: F401
        except ImportError:
            logger.critical("Failed to import inventory signals. Inventory logic will NOT track drift.")

        connection_created.connect(
            configure_sqlite_connection,
            dispatch_uid="inventory.configure_sqlite_connection",
        )
