import json
from pathlib import Path
from types import SimpleNamespace

from . import storage


def test_backend_storage_uses_project_root_inventory_file():
    expected = Path(__file__).resolve().parents[4] / "inventory_data.json"
    assert Path(storage.get_data_file_path()).resolve() == expected.resolve()


def test_backend_load_data_accepts_products_schema(tmp_path):
    file_path = tmp_path / "inventory_data.json"
    file_path.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "name": "Sample Item",
                        "sku": "SAM-001",
                        "category": "General",
                        "quantity_on_hand": 5,
                        "cost_price": "10.00",
                        "selling_price": "15.00",
                    }
                ]
            }
        )
    )

    original_file = storage.DATA_FILE
    storage.DATA_FILE = str(file_path)
    try:
        inventory, receipts = storage.load_data()
    finally:
        storage.DATA_FILE = original_file

    assert receipts == []
    assert inventory[0]["Name"] == "Sample Item"
    assert inventory[0]["Amount"] == 5


def test_owner_storage_uses_tenant_specific_file_name():
    owner = SimpleNamespace(pk=42, username="Admin One")
    owner_path = Path(storage.get_owner_data_file_path(owner))

    assert owner_path.parent.name == "tenant_inventory_data"
    assert owner_path.name == "inventory_data.owner-42-admin-one.json"


def test_load_seed_products_for_owner_rejects_unmatched_shared_file(tmp_path):
    file_path = tmp_path / "inventory_data.json"
    file_path.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "name": "Foreign Item",
                        "sku": "FOREIGN-1",
                        "quantity_on_hand": 3,
                    }
                ],
                "settings": {
                    "owner_username": "someone-else",
                },
            }
        )
    )

    original_file = storage.DATA_FILE
    storage.DATA_FILE = str(file_path)
    try:
        products = storage.load_seed_products_for_owner(
            SimpleNamespace(pk=7, username="target-admin", email="target@example.com")
        )
    finally:
        storage.DATA_FILE = original_file

    assert products == []


def test_load_seed_products_for_owner_prefers_owner_scoped_file(tmp_path):
    shared_file = tmp_path / "inventory_data.json"
    shared_file.write_text(json.dumps({"products": []}))

    owner = SimpleNamespace(pk=7, username="target-admin", email="target@example.com")
    owner_file = tmp_path / "tenant_inventory_data" / "inventory_data.owner-7-target-admin.json"
    owner_file.parent.mkdir(parents=True, exist_ok=True)
    owner_file.write_text(
        json.dumps(
            {
                "products": [
                    {
                        "name": "Scoped Item",
                        "sku": "SCOPED-1",
                        "quantity_on_hand": 9,
                    }
                ]
            }
        )
    )

    original_file = storage.DATA_FILE
    storage.DATA_FILE = str(shared_file)
    try:
        products = storage.load_seed_products_for_owner(owner)
    finally:
        storage.DATA_FILE = original_file

    assert len(products) == 1
    assert products[0]["SKU"] == "SCOPED-1"
