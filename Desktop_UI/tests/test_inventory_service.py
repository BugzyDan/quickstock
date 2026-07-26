from src.services.inventory_service import InventoryService


def test_inventory_service_persists_items_to_storage(tmp_path):
    service = InventoryService(storage_path=str(tmp_path))

    ok, _message, item = service.add_item(
        category="Beverages",
        name="Soda",
        cost=50,
        price=100,
        amount=6,
        sku="SODA-1",
    )

    assert ok is True
    assert item is not None

    reloaded = InventoryService(storage_path=str(tmp_path))
    stored = reloaded.get_item_by_sku("SODA-1")

    assert stored is not None
    assert stored["Name"] == "Soda"
    assert stored["Amount"] == 6
