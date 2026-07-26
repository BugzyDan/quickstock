# QuickStock JA - UI Frames Package
"""
UI frame components for different sections of the application.
"""

from .login import LoginFrame
from .dashboard import DashboardFrame
from .inventory import InventoryFrame
from .pos import POSFrame

__all__ = [
    'LoginFrame',
    'DashboardFrame',
    'InventoryFrame',
    'POSFrame',
]