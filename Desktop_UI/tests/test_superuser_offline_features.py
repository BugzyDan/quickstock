from datetime import datetime, timezone

import Inventory_gui as inventory_gui


def test_superuser_registry_snapshot_round_trip(tmp_path):
    gui = inventory_gui.InventoryGUI.__new__(inventory_gui.InventoryGUI)
    gui.current_user_id = 77
    gui.current_username = "platform-owner"

    scoped_cache = tmp_path / "superuser_registry_77.json"
    fallback_cache = tmp_path / "superuser_registry_cache.json"

    original_fallback = inventory_gui.SUPERUSER_REGISTRY_CACHE_FILE
    inventory_gui.SUPERUSER_REGISTRY_CACHE_FILE = str(fallback_cache)
    try:
        gui._superuser_registry_cache_path = lambda: str(scoped_cache)
        snapshot = gui._save_superuser_registry_snapshot(
            "platform",
            [
                {"company": "Alpha Stores", "username": "owner1", "role": "admin", "status": "active", "plan": "PRO"},
                {"company": "Beta Mart", "username": "owner2", "role": "manager", "status": "inactive", "plan": "TRIAL"},
            ],
        )
        loaded = gui._load_superuser_registry_snapshot()
    finally:
        inventory_gui.SUPERUSER_REGISTRY_CACHE_FILE = original_fallback

    assert snapshot["scope"] == "platform"
    assert snapshot["count"] == 2
    assert loaded["count"] == 2
    assert loaded["users"][0]["company"] == "Alpha Stores"

    summary = gui._registry_snapshot_summary(loaded)
    assert summary["companies"] == 2
    assert summary["active_users"] == 1
    assert summary["flagged_users"] == 1


def test_pending_queue_summary_tracks_counts_and_latest_timestamp():
    gui = inventory_gui.InventoryGUI.__new__(inventory_gui.InventoryGUI)

    queue = [
        {"action": "UPSERT_ITEM", "sku": "ABC1", "queued_at": "2026-07-12T09:30:00+00:00"},
        {"action": "UPSERT_ITEM", "sku": "ABC2", "queued_at": "2026-07-12T10:30:00+00:00"},
        {"action": "RECORD_SALE", "items": [{"sku": "ABC2"}], "queued_at": "2026-07-12T11:00:00+00:00"},
    ]

    summary = gui._summarize_pending_queue(queue)

    assert summary["total"] == 3
    assert summary["actions"]["UPSERT_ITEM"] == 2
    assert summary["actions"]["RECORD_SALE"] == 1
    assert summary["latest_queued_at"] == datetime(2026, 7, 12, 11, 0, tzinfo=timezone.utc).isoformat()
