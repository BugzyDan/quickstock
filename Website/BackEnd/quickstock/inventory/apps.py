# inventory/apps.py
from django.apps import AppConfig

class InventoryConfig(AppConfig):
    """
    Configuration for the Inventory app.
    Registers signal handlers to manage inventory drift and financial totals.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'inventory'
    verbose_name = 'Inventory & POS Management'

    def ready(self):
        """
        Register signal handlers during app initialization.
        Imports here ensure that the signal receivers are bound to the 
        model events before the application begins processing requests.
        """
        try:
            # We use an absolute import to ensure we register the signals 
            # associated with SaleItem, StockRecord, and Sale events.
            import inventory.signals
        except ImportError:
            # If signals.py is missing or failing, we log a critical startup error
            import logging
            logger = logging.getLogger(__name__)
            logger.critical("Failed to import inventory signals. Inventory logic will NOT track drift.")


from django.apps import AppConfig

class InventoryConfig(AppConfig):
    name = 'inventory'

    def ready(self):
        import inventory.signals