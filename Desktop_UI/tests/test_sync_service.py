from src.services.sync_service import SyncService


def test_queue_action_persists_and_reloads(tmp_path):
    sync = SyncService(local_data_path=str(tmp_path))

    sync.queue_action("UPSERT_ITEM", {"sku": "ABC-1", "quantity": 2}, item_id="ABC-1")

    reloaded = SyncService(local_data_path=str(tmp_path))
    pending = reloaded.get_pending_actions()

    assert len(pending) == 1
    assert pending[0].action == "UPSERT_ITEM"
    assert pending[0].data["sku"] == "ABC-1"


def test_queue_action_replaces_existing_item_action(tmp_path):
    sync = SyncService(local_data_path=str(tmp_path))

    sync.queue_action("UPSERT_ITEM", {"sku": "ABC-1", "quantity": 2}, item_id="ABC-1")
    sync.queue_action("UPSERT_ITEM", {"sku": "ABC-1", "quantity": 5}, item_id="ABC-1")

    pending = sync.get_pending_actions()

    assert len(pending) == 1
    assert pending[0].data["quantity"] == 5


def test_make_api_request_rejects_insecure_production_url(tmp_path, monkeypatch):
    monkeypatch.setenv("QUICKSTOCK_ENV", "production")
    sync = SyncService(local_data_path=str(tmp_path))
    sync.set_api_config("http://api.example.com", "token-123")

    success, message, response = sync._make_api_request("/api/inventory/")

    assert success is False
    assert "HTTPS" in message
    assert response is None
