import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path


logger = logging.getLogger("inventory")

# Canonical JSON file shared by the desktop and web apps.
PROJECT_ROOT = Path(__file__).resolve().parents[4]
DATA_FILE = str(PROJECT_ROOT / "inventory_data.json")


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_inventory_item(item, number):
    sku = item.get("sku") or item.get("SKU") or ""
    name = item.get("name") or item.get("Name") or "Unknown"
    category = item.get("category_name") or item.get("category") or item.get("Category") or "General"
    brand = item.get("brand") or item.get("Brand") or "Generic"
    location = item.get("location") or item.get("Location") or ""
    barcode = item.get("barcode") or item.get("Barcode") or ""
    cost = _safe_float(item.get("cost_price") or item.get("cost") or item.get("Cost") or 0)
    price = _safe_float(item.get("selling_price") or item.get("price") or item.get("Price") or 0)
    amount = _safe_int(
        item.get("quantity_on_hand")
        or item.get("quantity")
        or item.get("qty")
        or item.get("Amount")
        or item.get("amount")
        or 0
    )

    normalized = {
        "Number": _safe_int(item.get("Number") or item.get("number") or number, number),
        "SKU": str(sku).upper(),
        "Category": str(category),
        "Name": str(name),
        "Cost": cost,
        "Price": price,
        "Amount": amount,
        "sku": str(sku).upper(),
        "category": str(category),
        "name": str(name),
        "cost": cost,
        "price": price,
        "amount": amount,
        "quantity": amount,
    }
    if barcode:
        normalized["barcode"] = str(barcode)
    if brand:
        normalized["brand"] = str(brand)
    if location:
        normalized["location"] = str(location)
    if "tax_label" in item:
        normalized["tax_label"] = item.get("tax_label")
    if "tax_rate" in item:
        normalized["tax_rate"] = item.get("tax_rate")
    if "tax_inclusive" in item:
        normalized["tax_inclusive"] = item.get("tax_inclusive")
    if "active" in item:
        normalized["active"] = item.get("active")
    if "last_modified" in item:
        normalized["last_modified"] = item.get("last_modified")
    if "sync_token" in item:
        normalized["sync_token"] = item.get("sync_token")
    return normalized


def _inventory_to_product(item, settings=None):
    settings = settings or {}
    cost = _safe_float(item.get("Cost") or item.get("cost") or item.get("cost_price") or 0)
    price = _safe_float(item.get("Price") or item.get("price") or item.get("selling_price") or 0)
    amount = _safe_int(item.get("Amount") or item.get("amount") or item.get("quantity") or 0)

    return {
        "name": str(item.get("Name") or item.get("name") or "Unknown"),
        "sku": str(item.get("SKU") or item.get("sku") or "").upper(),
        "barcode": str(item.get("barcode") or item.get("Barcode") or ""),
        "category": str(item.get("Category") or item.get("category") or "General"),
        "brand": str(item.get("brand") or item.get("Brand") or "Generic"),
        "quantity_on_hand": amount,
        "location": str(item.get("location") or item.get("Location") or settings.get("primary_funding_branch_name") or "Main Store"),
        "cost_price": f"{cost:.2f}",
        "selling_price": f"{price:.2f}",
        "tax_label": item.get("tax_label") or settings.get("tax_label", "GCT"),
        "tax_rate": item.get("tax_rate") if item.get("tax_rate") is not None else settings.get("tax_rate", 0.15),
        "tax_inclusive": bool(item.get("tax_inclusive", settings.get("tax_inclusive", False))),
        "active": item.get("active", True),
        "last_modified": item.get("last_modified"),
        "sync_token": item.get("sync_token"),
    }


def _extract_data(data):
    if not isinstance(data, dict):
        return [], [], {}

    raw_inventory = data.get("inventory")
    if raw_inventory is None:
        raw_inventory = data.get("products", [])
    raw_receipts = data.get("receipts", [])
    settings = data.get("settings", {})

    inventory = [
        _normalize_inventory_item(item, index)
        for index, item in enumerate(raw_inventory or [], start=1)
        if isinstance(item, dict)
    ]
    return inventory, raw_receipts if isinstance(raw_receipts, list) else [], settings if isinstance(settings, dict) else {}


def get_data_file_path() -> str:
    """Return the shared inventory JSON path."""
    return DATA_FILE


def get_owner_data_file_path(owner) -> str:
    """Return the owner-scoped inventory JSON path."""
    base_path = Path(DATA_FILE)
    owner_id = getattr(owner, "pk", None) or "unknown"
    username = str(getattr(owner, "username", "") or "owner").strip().lower()
    safe_username = re.sub(r"[^a-z0-9._-]+", "-", username).strip("-") or "owner"
    tenant_dir = base_path.parent / "tenant_inventory_data"
    return str(tenant_dir / f"inventory_data.owner-{owner_id}-{safe_username}.json")


def _owner_identity_tokens(owner):
    tokens = set()
    if not owner:
        return tokens

    for value in (
        getattr(owner, "pk", None),
        getattr(owner, "id", None),
        getattr(owner, "username", None),
        getattr(owner, "email", None),
    ):
        if value in {None, ""}:
            continue
        tokens.add(str(value).strip().lower())

    return tokens


def _payload_matches_owner(payload, owner) -> bool:
    if not isinstance(payload, dict):
        return False

    owner_tokens = _owner_identity_tokens(owner)
    if not owner_tokens:
        return False

    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    candidate_blocks = [
        payload.get("owner"),
        payload.get("tenant"),
        settings.get("owner"),
        settings.get("tenant"),
    ]

    candidate_values = []
    for block in candidate_blocks:
        if isinstance(block, dict):
            candidate_values.extend(block.values())

    candidate_values.extend(
        [
            payload.get("owner_id"),
            payload.get("owner_username"),
            payload.get("owner_email"),
            payload.get("tenant_id"),
            payload.get("tenant_username"),
            payload.get("tenant_email"),
            settings.get("owner_id"),
            settings.get("owner_username"),
            settings.get("owner_email"),
            settings.get("tenant_id"),
            settings.get("tenant_username"),
            settings.get("tenant_email"),
        ]
    )

    normalized_values = {
        str(value).strip().lower()
        for value in candidate_values
        if value not in {None, ""}
    }
    return bool(owner_tokens & normalized_values)


def load_seed_products_for_owner(owner):
    """
    Load seed inventory only from an owner-scoped file, or from the shared file
    when that shared payload explicitly declares the same owner.
    """
    if not owner:
        return []

    owner_path = Path(get_owner_data_file_path(owner))
    shared_path = Path(DATA_FILE)

    for path, require_owner_match in ((owner_path, False), (shared_path, True)):
        if not path.exists():
            continue

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if require_owner_match and not _payload_matches_owner(payload, owner):
            continue

        inventory, _receipts, _settings = _extract_data(payload)
        if inventory:
            return inventory

    return []


def load_data():
    """
    Load inventory and receipt data from the JSON file.

    Returns:
        tuple: (inventory_list, receipts_list)
        - inventory_list: list of items in the inventory
        - receipts_list: list of past receipts
    """
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as file:
                data = json.load(file)
                inventory, receipts, _settings = _extract_data(data)
                return inventory, receipts
        except (json.JSONDecodeError, ValueError) as e:
            logger.exception("Could not decode inventory JSON from %s", DATA_FILE)
        except OSError as e:
            logger.exception("Could not open inventory data file %s", DATA_FILE)

    # Return empty lists if file doesn't exist or error occurs
    return [], []


def save_data(inventory, receipts, settings=None):
    """
    Save inventory and receipt data to the JSON file.

    Args:
        inventory (list): The current inventory
        receipts (list): List of receipts
    """
    try:
        existing_settings = {}
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r") as file:
                    existing_data = json.load(file)
                if isinstance(existing_data, dict):
                    existing_settings = existing_data.get("settings", {}) if isinstance(existing_data.get("settings", {}), dict) else {}
            except Exception:
                existing_settings = {}

        merged_settings = {**existing_settings, **(settings or {})}

        with open(DATA_FILE, "w") as file:
            json.dump(
                {
                    "inventory": inventory,
                    "products": [_inventory_to_product(item, merged_settings) for item in inventory],
                    "receipts": receipts,
                    "settings": merged_settings,
                },
                file,
                indent=4
            )
    except OSError as e:
        logger.exception("Could not save inventory data file %s", DATA_FILE)
    except Exception as e:
        logger.exception("Unexpected error while saving inventory data")


def backup_data():
    """
    Backup the current data file to a timestamped file.

    Example backup filename:
        inventory_data_backup_20260208_143210.json
    """
    try:
        if os.path.exists(DATA_FILE):
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            backup_file = os.path.join(
                os.path.dirname(DATA_FILE),
                f"inventory_data_backup_{timestamp}.json",
            )
            os.rename(DATA_FILE, backup_file)
            logger.info("Inventory backup saved to %s", backup_file)
    except Exception as e:
        logger.exception("Could not back up inventory data file %s", DATA_FILE)
