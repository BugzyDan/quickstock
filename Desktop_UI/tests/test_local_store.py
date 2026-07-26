from pathlib import Path

import json

from src.storage.local_store import DATA_FILE, load_data, save_data


def test_save_and_load_data_round_trip(tmp_path):
    file_path = tmp_path / "inventory_data.json"
    inventory = [{"SKU": "ABC-1", "Name": "Widget", "Amount": 3}]
    receipts = [{"Invoice No": 1, "Total Bill": 100.0}]
    settings = {"country": "Jamaica", "brand_name": "QuickStock"}

    ok, _message = save_data(inventory, receipts, settings, str(file_path))

    assert ok is True
    loaded_inventory, loaded_receipts, loaded_settings = load_data(str(file_path))
    assert loaded_inventory == inventory
    assert loaded_receipts == receipts
    assert loaded_settings["country"] == "Jamaica"
    assert loaded_settings["brand_name"] == "QuickStock"


def test_default_data_file_points_to_project_root():
    expected = Path(__file__).resolve().parents[2] / "inventory_data.json"
    assert Path(DATA_FILE).resolve() == expected.resolve()


def test_load_data_accepts_products_schema(tmp_path):
    file_path = tmp_path / "inventory_data.json"
    file_path.write_text(
        json.dumps(
            {
                "settings": {"brand_name": "QuickStock Store"},
                "products": [
                    {
                        "name": "Sample Item",
                        "sku": "SAM-001",
                        "category": "General",
                        "quantity_on_hand": 5,
                        "cost_price": "10.00",
                        "selling_price": "15.00",
                    }
                ],
            }
        )
    )

    inventory, receipts, settings = load_data(str(file_path))

    assert receipts == []
    assert settings["brand_name"] == "QuickStock Store"
    assert inventory[0]["Name"] == "Sample Item"
    assert inventory[0]["Amount"] == 5


def test_save_data_preserves_existing_customers(tmp_path):
    file_path = tmp_path / "inventory_data.json"
    file_path.write_text(
        json.dumps(
            {
                "customers": [
                    {
                        "name": "KeviDan",
                        "phone": "8765551234",
                        "email": "kevidan@example.com",
                    }
                ]
            }
        )
    )

    save_data(
        [{"SKU": "ABC-1", "Name": "Widget", "Amount": 3}],
        [{"Invoice No": 1, "Total Bill": 100.0}],
        {"country": "Jamaica"},
        str(file_path),
    )

    saved = json.loads(file_path.read_text())
    assert saved["customers"][0]["name"] == "KeviDan"
