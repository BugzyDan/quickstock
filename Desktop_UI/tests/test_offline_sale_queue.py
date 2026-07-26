import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import Inventory_gui as inventory_gui


@contextmanager
def _queue_gui(tmp_path):
    original_pending_file = inventory_gui.PENDING_QUEUE_FILE
    queue_path = tmp_path / "pending_sync.json"
    inventory_gui.PENDING_QUEUE_FILE = str(queue_path)
    gui = inventory_gui.InventoryGUI.__new__(inventory_gui.InventoryGUI)
    gui.current_user_id = 7
    gui.current_username = "cashier"
    gui.current_role = "cashier"
    gui.current_tenant_id = 3
    gui.current_operator_status = "active"
    gui.default_location_id = 11
    gui.active_location_id = 11
    gui.active_register_id = 19
    gui.active_register_location_id = 11
    gui.active_register_opened_at = "2026-07-22T08:00:00+00:00"
    gui.active_register_is_open = True
    gui.locations = [{"id": 11, "name": "Main Store"}]
    gui.customers = []
    gui.system = SimpleNamespace(
        settings={"tax_rate": 0.15, "tax_label": "GCT", "currency": "JMD"},
        inventory=[
            {
                "id": 101,
                "product_id": 101,
                "SKU": "QUEUE-SKU",
                "Name": "Queue Item",
                "Price": 100.0,
                "Cost": 50.0,
                "Amount": 10,
                "is_taxable": True,
                "status": "active",
                "is_deleted": False,
            }
        ],
    )
    gui.api_token = "test-token"
    gui.api_base_url = "https://quickstock.example"
    gui.last_server_error_time = 0
    gui.server_cooldown = 30
    gui.api_status = "ONLINE"
    gui._queue_lock = threading.RLock()
    gui._queue_replay_lock = threading.Lock()
    gui._active_queue_action_ids = set()
    gui._queue_corruption_info = None
    gui.sync_inventory = lambda *args, **kwargs: None
    gui.sync_reference_data = lambda *args, **kwargs: None
    gui._pending_queue_path = lambda: str(queue_path)
    try:
        yield gui, queue_path
    finally:
        inventory_gui.PENDING_QUEUE_FILE = original_pending_file


def _legacy_sale_payload():
    return {
        "location_id": 11,
        "tender": "cash",
        "total_price": "200.00",
        "items": [{"sku": "QUEUE-SKU", "quantity": 2}],
    }


def _sale_payload():
    return {
        "contract_version": inventory_gui.CHECKOUT_CONTRACT_VERSION,
        "offline_client_ref": "DESKTOP-QUEUE-001",
        "occurred_at": "2026-07-22T12:00:00+00:00",
        "operator": {"id": 7, "role": "cashier"},
        "tenant": {"id": 3},
        "location": {"id": 11},
        "register": {
            "id": 19,
            "location_id": 11,
            "opened_at": "2026-07-22T08:00:00+00:00",
        },
        "customer": {"id": None, "name": ""},
        "currency": "JMD",
        "payment": {
            "method": "cash",
            "amount_tendered": "100.00",
            "account_credit": "0.00",
            "change_due": "0.00",
            "credited_overpayment": "0.00",
        },
        "totals": {
            "subtotal": "86.96",
            "discount": "0.00",
            "tax": "13.04",
            "total": "100.00",
        },
        "tax": {"label": "GCT", "rate": "0.1500", "inclusive": True},
        "line_items": [
            {
                "product_id": 101,
                "sku": "QUEUE-SKU",
                "quantity": 1,
                "unit_price": "100.00",
                "unit_cost": "50.00",
                "is_taxable": True,
                "tax_rate": "0.1500",
                "status": "active",
                "is_deleted": False,
            }
        ],
        "metadata": {"source": "desktop", "queue_schema_version": 1},
    }


def _success(reference, *, replay=False):
    return {
        "kind": "success",
        "status": 200,
        "body": {
            "ok": True,
            "sale_id": 91,
            "receipt_no": 12,
            "client_reference": reference,
            "contract_version": inventory_gui.CHECKOUT_CONTRACT_VERSION,
            "idempotent_replay": replay,
        },
        "error": None,
    }


def test_legacy_sale_queue_is_upgraded_once_with_stable_reference(tmp_path):
    with _queue_gui(tmp_path) as (gui, queue_path):
        queue_path.write_text(
            json.dumps([{"action": "RECORD_SALE", **_legacy_sale_payload()}]),
            encoding="utf-8",
        )

        first_read = gui._read_pending_queue()
        second_read = gui._read_pending_queue()

        assert len(first_read) == 1
        assert first_read[0]["offline_client_ref"].startswith("DESKTOP-")
        assert first_read[0]["offline_client_ref"] == second_read[0]["offline_client_ref"]
        assert first_read[0]["_sync"]["action_id"] == second_read[0]["_sync"]["action_id"]
        assert first_read[0]["_sync"]["schema_version"] == inventory_gui.QUEUE_SCHEMA_VERSION
        assert queue_path.with_name(f"{queue_path.name}.v0.bak").exists()


def test_legacy_sale_queue_is_preserved_as_dead_letter_when_contract_cannot_be_proven(tmp_path):
    with _queue_gui(tmp_path) as (gui, queue_path):
        queue_path.write_text(
            json.dumps([{"action": "RECORD_SALE", **_legacy_sale_payload()}]),
            encoding="utf-8",
        )

        result = gui.sync_offline_queue(silent=True, force=True)
        retained = gui._read_pending_queue()

        assert result["synced"] == 0
        assert result["remaining"] == 1
        assert retained[0]["_sync"]["state"] == "dead_letter"
        assert retained[0]["offline_client_ref"].startswith("DESKTOP-")
        assert "contract version" in retained[0]["_sync"]["last_error"].lower()


def test_timeout_after_commit_retries_same_sale_and_accepts_idempotent_ack(tmp_path):
    with _queue_gui(tmp_path) as (gui, _queue_path):
        queued = gui._queue_action("RECORD_SALE", _sale_payload())
        reference = queued["offline_client_ref"]
        observed_references = []

        def request(_method, _path, payload):
            observed_references.append(payload["offline_client_ref"])
            if len(observed_references) == 1:
                return {"kind": "retryable", "status": None, "body": {}, "error": "Read timed out after commit."}
            return _success(reference, replay=True)

        gui._sync_queue_request = request
        first_result = gui.sync_offline_queue(silent=True, force=True)
        retained = gui._read_pending_queue()
        second_result = gui.sync_offline_queue(silent=True, force=True)

        assert first_result["remaining"] == 1
        assert retained[0]["_sync"]["state"] == "retry"
        assert retained[0]["offline_client_ref"] == reference
        assert second_result["remaining"] == 0
        assert observed_references == [reference, reference]


def test_restart_recovers_an_in_flight_sale_without_changing_reference(tmp_path):
    with _queue_gui(tmp_path) as (gui, queue_path):
        queued = gui._queue_action("RECORD_SALE", _sale_payload())
        reference = queued["offline_client_ref"]
        persisted = gui._read_pending_queue()
        persisted[0]["_sync"]["state"] = "in_flight"
        persisted[0]["_sync"]["last_attempt_at"] = gui._queue_timestamp()
        gui._write_queue_file_atomic(str(queue_path), persisted)

        restarted = inventory_gui.InventoryGUI.__new__(inventory_gui.InventoryGUI)
        restarted.current_user_id = None
        restarted.default_location_id = 11
        restarted.active_location_id = 11
        restarted._queue_lock = threading.RLock()
        restarted._active_queue_action_ids = set()
        restarted._queue_corruption_info = None
        recovered = restarted._read_pending_queue()

        assert recovered[0]["_sync"]["state"] == "retry"
        assert recovered[0]["_sync"]["next_attempt_at"] is None
        assert recovered[0]["offline_client_ref"] == reference


def test_power_failure_before_atomic_replace_leaves_last_queue_recoverable(tmp_path):
    with _queue_gui(tmp_path) as (gui, queue_path):
        queued = gui._queue_action("RECORD_SALE", _sale_payload())
        interrupted_temp = queue_path.with_name(f".{queue_path.name}.interrupted.tmp")
        interrupted_temp.write_text('[{"action": "RECORD_SALE"', encoding="utf-8")

        recovered = gui._read_pending_queue()

        assert len(recovered) == 1
        assert recovered[0]["offline_client_ref"] == queued["offline_client_ref"]
        assert interrupted_temp.exists()


def test_network_interruption_retains_sale_with_retry_metadata(tmp_path):
    with _queue_gui(tmp_path) as (gui, _queue_path):
        queued = gui._queue_action("RECORD_SALE", _sale_payload())
        gui._sync_queue_request = lambda *_args, **_kwargs: {
            "kind": "retryable",
            "status": None,
            "body": {},
            "error": "Network connection interrupted.",
        }

        result = gui.sync_offline_queue(silent=True, force=True)
        retained = gui._read_pending_queue()[0]

        assert result["synced"] == 0
        assert retained["offline_client_ref"] == queued["offline_client_ref"]
        assert retained["_sync"]["state"] == "retry"
        assert retained["_sync"]["attempts"] == 1
        assert retained["_sync"]["next_attempt_at"] is not None


def test_concurrent_enqueue_is_not_lost_when_sale_is_acknowledged(tmp_path):
    with _queue_gui(tmp_path) as (gui, _queue_path):
        sale = gui._queue_action("RECORD_SALE", _sale_payload())

        def request(_method, _path, _payload):
            enqueue_thread = threading.Thread(
                target=gui._queue_action,
                args=(
                    "UPSERT_ITEM",
                    {"sku": "NEW-SKU", "name": "Concurrent Item", "quantity": 1},
                ),
            )
            enqueue_thread.start()
            enqueue_thread.join(timeout=2)
            assert not enqueue_thread.is_alive()
            return _success(sale["offline_client_ref"])

        gui._sync_queue_request = request
        result = gui.sync_offline_queue(silent=True, force=True)
        remaining = gui._read_pending_queue()

        assert result["synced"] == 1
        assert len(remaining) == 1
        assert remaining[0]["action"] == "UPSERT_ITEM"
        assert remaining[0]["sku"] == "NEW-SKU"


def test_mismatched_acknowledgement_is_retained_as_dead_letter(tmp_path):
    with _queue_gui(tmp_path) as (gui, _queue_path):
        queued = gui._queue_action("RECORD_SALE", _sale_payload())
        gui._sync_queue_request = lambda *_args, **_kwargs: _success("DESKTOP-WRONG-REFERENCE")

        result = gui.sync_offline_queue(silent=True, force=True)
        retained = gui._read_pending_queue()[0]

        assert result["synced"] == 0
        assert retained["offline_client_ref"] == queued["offline_client_ref"]
        assert retained["_sync"]["state"] == "dead_letter"
        assert "acknowledgement" in retained["_sync"]["last_error"].lower()


def test_immediate_sale_acknowledgement_requires_matching_reference_and_sale_id(tmp_path):
    with _queue_gui(tmp_path) as (gui, _queue_path):
        payload = {**_sale_payload(), "offline_client_ref": "DESKTOP-ACK-001"}

        assert gui._sale_acknowledgement_matches(
            payload,
            {
                "ok": True,
                "sale_id": 9,
                "client_reference": "DESKTOP-ACK-001",
                "contract_version": inventory_gui.CHECKOUT_CONTRACT_VERSION,
            },
        )
        assert not gui._sale_acknowledgement_matches(
            payload,
            {
                "ok": True,
                "sale_id": 9,
                "client_reference": "DESKTOP-ACK-OTHER",
                "contract_version": inventory_gui.CHECKOUT_CONTRACT_VERSION,
            },
        )
        assert not gui._sale_acknowledgement_matches(
            payload,
            {
                "ok": True,
                "client_reference": "DESKTOP-ACK-001",
                "contract_version": inventory_gui.CHECKOUT_CONTRACT_VERSION,
            },
        )


def test_auth_failure_pauses_sale_without_consuming_retry_budget(tmp_path):
    with _queue_gui(tmp_path) as (gui, _queue_path):
        gui._queue_action("RECORD_SALE", _sale_payload())
        gui._sync_queue_request = lambda *_args, **_kwargs: {
            "kind": "auth",
            "status": 401,
            "body": {},
            "error": "Authentication expired.",
        }

        gui.sync_offline_queue(silent=True, force=True)
        retained = gui._read_pending_queue()[0]

        assert retained["_sync"]["state"] == "paused_auth"
        assert retained["_sync"]["attempts"] == 0


def test_queue_transport_classifies_connection_interruption_as_retryable(tmp_path):
    with _queue_gui(tmp_path) as (gui, _queue_path):
        original_request = inventory_gui.requests.request

        def interrupted_request(**_kwargs):
            raise inventory_gui.requests.exceptions.ConnectionError("connection dropped")

        inventory_gui.requests.request = interrupted_request
        try:
            result = gui._sync_queue_request("POST", "/api/sales/", _sale_payload())
        finally:
            inventory_gui.requests.request = original_request

        assert result["kind"] == "retryable"
        assert result["status"] is None
        assert "connection dropped" in result["error"]


def test_corrupt_queue_is_preserved_and_blocked_from_overwrite(tmp_path):
    with _queue_gui(tmp_path) as (gui, queue_path):
        queue_path.write_text('[{"action": "RECORD_SALE"', encoding="utf-8")

        loaded = gui._read_pending_queue()
        backups = list(tmp_path.glob("pending_sync.json.corrupt-*"))
        blocked = False
        try:
            gui._queue_action("RECORD_SALE", _sale_payload())
        except RuntimeError:
            blocked = True

        assert loaded == []
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == queue_path.read_text(encoding="utf-8")
        assert blocked
        assert queue_path.read_text(encoding="utf-8") == '[{"action": "RECORD_SALE"'


def test_dead_letter_can_be_explicitly_requeued(tmp_path):
    with _queue_gui(tmp_path) as (gui, _queue_path):
        queued = gui._queue_action("RECORD_SALE", _sale_payload())
        action_id = queued["_sync"]["action_id"]
        gui._update_pending_action(
            action_id,
            state="dead_letter",
            attempts=inventory_gui.QUEUE_MAX_ATTEMPTS,
            error="Protocol mismatch.",
            next_attempt_at=None,
        )

        assert gui.retry_pending_action(action_id)
        retried = gui._read_pending_queue()[0]
        assert retried["_sync"]["state"] == "pending"
        assert retried["_sync"]["attempts"] == 0
        assert retried["_sync"]["last_error"] is None
