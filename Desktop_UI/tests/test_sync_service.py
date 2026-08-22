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


def test_sales_push_uses_render_api_route(tmp_path, monkeypatch):
    sync = SyncService(local_data_path=str(tmp_path))
    calls = []

    def fake_request(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return True, "ok", {"message": "saved", "results": {"pushed": 1}}

    monkeypatch.setattr(sync, "_make_api_request", fake_request)
    success, _message, results = sync.sync_push_sales(
        [{"receipt_no": "SALE-1", "items": []}],
    )

    assert success is True
    assert results["pushed"] == 1
    assert calls[0][0] == "/api/sales/push/"


def test_sales_pull_uses_render_api_route(tmp_path, monkeypatch):
    sync = SyncService(local_data_path=str(tmp_path))
    calls = []

    def fake_request(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return True, "ok", {"sales": [], "count": 0}

    monkeypatch.setattr(sync, "_make_api_request", fake_request)
    success, message, sales = sync.sync_pull_sales()

    assert success is True
    assert message == "Pulled 0 sales from server"
    assert sales == []
    assert calls[0][0] == "/api/sales/"
