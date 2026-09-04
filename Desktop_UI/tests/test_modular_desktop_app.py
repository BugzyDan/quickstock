from datetime import datetime, timezone
from decimal import Decimal

from src.services.sync_service import SyncStatus
from src.ui.app import InventoryGUI


class _InventoryService:
    def __init__(self, items, summary=None):
        self._items = items
        self._summary = summary or {}

    def get_all_items(self):
        return list(self._items)

    def get_inventory_summary(self):
        return dict(self._summary)


class _SyncService:
    def get_sync_summary(self):
        return {
            "pending_actions": 3,
            "failed_actions": 1,
            "last_sync": datetime(2026, 8, 17, 10, 30, tzinfo=timezone.utc),
            "api_configured": True,
            "status": SyncStatus.PARTIAL.value,
        }


class _Frame:
    def winfo_exists(self):
        return True


class _StatusLabel:
    def __init__(self):
        self.kwargs = {}

    def configure(self, **kwargs):
        self.kwargs.update(kwargs)


class _Styles:
    def get_color(self, name):
        return name


def test_modular_desktop_inventory_export_rows_normalize_item_shapes():
    gui = InventoryGUI.__new__(InventoryGUI)
    gui.active_location_name = "Main Store"
    gui.inventory_service = _InventoryService([
        {
            "sku": "abc-1",
            "name": "Blue Soap",
            "category": "Household",
            "quantity": 4,
            "cost_price": "75",
            "selling_price": "120.5",
        }
    ])

    rows = gui._inventory_export_rows()

    assert rows == [
        {
            "SKU": "abc-1",
            "Name": "Blue Soap",
            "Category": "Household",
            "Brand": "",
            "Quantity": 4,
            "Cost": "75.00",
            "Price": "120.50",
            "Location": "Main Store",
        }
    ]


def test_modular_desktop_sync_summary_message_is_human_readable():
    gui = InventoryGUI.__new__(InventoryGUI)
    gui.sync_service = _SyncService()

    message = gui._sync_summary_message()

    assert "Sync status: Partial" in message
    assert "Pending changes: 3" in message
    assert "Failed changes: 1" in message
    assert "Server connection: Connected" in message
    assert "Last completed sync: 2026-08-17 10:30" in message


def test_modular_desktop_quantity_number_falls_back_to_zero():
    gui = InventoryGUI.__new__(InventoryGUI)

    assert gui._quantity_number("4.5") == Decimal("4.5")
    assert gui._quantity_number("not-a-number") == Decimal("0")


def test_modular_desktop_role_update_refreshes_dashboard():
    gui = InventoryGUI.__new__(InventoryGUI)
    gui.current_username = "kevon"
    gui.current_role = "admin"
    gui.main_frame = _Frame()
    gui.status_label = _StatusLabel()
    calls = []
    gui._setup_main_menu = lambda: calls.append("refresh")

    gui._update_menu_for_role()

    assert gui.status_label.kwargs["text"] == "Logged in as kevon (admin)"
    assert calls == ["refresh"]


def test_modular_desktop_logout_clears_session_and_refreshes_dashboard():
    gui = InventoryGUI.__new__(InventoryGUI)
    gui.current_username = "cashier"
    gui.current_role = "cashier"
    gui.is_admin = True
    gui.api_token = "token"
    gui._is_offline = True
    gui.active_register_id = 123
    gui.active_register_location_id = 7
    gui.active_register_opened_at = "2026-08-17T10:00:00Z"
    gui.active_register_is_open = True
    gui.main_frame = _Frame()
    gui.status_label = _StatusLabel()
    gui.styles = _Styles()
    calls = []
    gui._setup_main_menu = lambda: calls.append("refresh")
    gui._show_login = lambda: calls.append("login")

    gui._logout()

    assert gui.current_username is None
    assert gui.current_role is None
    assert gui.api_token is None
    assert gui._is_offline is False
    assert gui.active_register_id is None
    assert gui.active_register_is_open is False
    assert calls == ["refresh", "login"]


def test_modular_desktop_inventory_window_summary_uses_current_rows():
    gui = InventoryGUI.__new__(InventoryGUI)
    gui.inventory_service = _InventoryService([], {"total_items": 2, "retail_value": "360.75"})

    summary = gui._inventory_window_summary([
        {"Quantity": 8},
        {"Quantity": 0},
    ])

    assert summary == {
        "total_items": 2,
        "total_value": "360.75",
        "low_stock_count": 1,
    }


def test_modular_desktop_sales_summary_normalizes_receipts():
    gui = InventoryGUI.__new__(InventoryGUI)

    summary = gui._sales_summary_metrics([
        {
            "Timestamp": datetime.now(timezone.utc).isoformat(),
            "Total Bill": "120.50",
            "Items Purchased": [{"Quantity": 2}, {"Quantity": "3"}],
        },
        {
            "Timestamp": "2026-08-17T10:30:00+00:00",
            "total": "20",
            "items": [{"quantity": 1}],
        },
    ])

    assert summary["receipt_count"] == 2
    assert summary["items_sold"] == 6
    assert summary["total_sales"] == "140.50"
    assert summary["today_receipts"] == 1
    assert summary["today_sales"] == "120.50"


def test_modular_desktop_receipt_history_rows_sort_newest_first():
    gui = InventoryGUI.__new__(InventoryGUI)

    rows = gui._receipt_history_rows([
        {"Invoice No": 1, "Timestamp": "2026-08-17T10:30:00+00:00", "Total Bill": "20"},
        {"Invoice No": 2, "Timestamp": "2026-08-18T09:00:00+00:00", "Total Bill": "30"},
    ])

    assert [row["Invoice"] for row in rows] == [2, 1]
    assert rows[0]["Total"] == "30.00"
