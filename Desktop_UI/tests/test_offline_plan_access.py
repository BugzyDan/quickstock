from datetime import date, timedelta

import Inventory_gui as inventory_gui


def test_active_pro_expiry_overrides_stale_trial_end():
    gui = inventory_gui.InventoryGUI.__new__(inventory_gui.InventoryGUI)
    has_access, message = gui._evaluate_offline_plan_access(
        {
            "plan": "PRO",
            "status": "active",
            "pro_expires": (date.today() + timedelta(days=30)).isoformat(),
            "plan_end": "2020-01-01T00:00:00+00:00",
        }
    )

    assert has_access is True
    assert message == ""


def test_expired_pro_is_still_denied_offline():
    """The stale-trial fix must not loosen access for genuinely expired plans."""
    gui = inventory_gui.InventoryGUI.__new__(inventory_gui.InventoryGUI)
    has_access, _ = gui._evaluate_offline_plan_access(
        {
            "plan": "PRO",
            "status": "active",
            "pro_expires": (date.today() - timedelta(days=1)).isoformat(),
            "plan_end": (date.today() - timedelta(days=365)).isoformat() + "T00:00:00+00:00",
        }
    )

    assert has_access is False


def test_plan_without_any_expiry_is_denied_offline():
    """A cache with no expiry cannot prove paid access."""
    gui = inventory_gui.InventoryGUI.__new__(inventory_gui.InventoryGUI)
    has_access, _ = gui._evaluate_offline_plan_access({"plan": "PRO", "status": "active"})

    assert has_access is False
