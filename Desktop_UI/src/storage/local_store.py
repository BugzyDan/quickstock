"""
Local data storage module for QuickStock JA.
Handles JSON file-based persistence for inventory, receipts, and settings.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .cache import get_secure_cache

# Canonical JSON file shared by the desktop and web apps.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
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


def _normalize_inventory_item(item: Dict, number: int) -> Dict:
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


def _inventory_to_product(item: Dict, settings: Optional[Dict] = None) -> Dict:
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


def _extract_data(data: Dict) -> Tuple[List[Dict], List[Dict], Dict]:
    if not isinstance(data, dict):
        return [], [], {}

    raw_inventory = data.get("inventory")
    if raw_inventory is None:
        raw_inventory = data.get("products", [])
    raw_receipts = data.get("receipts", [])
    settings = data.get("settings", {})

    if isinstance(data.get("inventory"), list):
        inventory = [item for item in raw_inventory if isinstance(item, dict)]
    else:
        inventory = [
            _normalize_inventory_item(item, index)
            for index, item in enumerate(raw_inventory or [], start=1)
            if isinstance(item, dict)
        ]
    return inventory, raw_receipts if isinstance(raw_receipts, list) else [], settings if isinstance(settings, dict) else {}


def set_data_file(path: str):
    """
    Allow callers (GUI) to scope inventory/receipts to a specific file.
    
    Args:
        path: Path to the data file
    """
    global DATA_FILE
    DATA_FILE = path


def get_data_file() -> str:
    """
    Get the current data file path.
    
    Returns:
        Current data file path
    """
    return DATA_FILE


def load_data(filepath: Optional[str] = None) -> Tuple[List, List, Dict]:
    """
    Loads inventory, receipts, and settings from the JSON file.
    
    Args:
        filepath: Optional specific file path (uses global DATA_FILE if None)
        
    Returns:
        Tuple of (inventory, receipts, settings)
    """
    file_path = filepath or DATA_FILE
    
    if os.path.exists(file_path):
        try:
            cache = get_secure_cache(file_path)
            data = cache.load()
            inventory, receipts, settings = _extract_data(data)
            if inventory or receipts or settings:
                return inventory, receipts, settings
        except (json.JSONDecodeError, IOError, KeyError) as e:
            print(f"Warning: Error loading data from {file_path}: {e}")
    
    return [], [], {}


def save_data(
    inventory: List[Dict],
    receipts: List[Dict],
    settings: Optional[Dict] = None,
    filepath: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Saves inventory, receipts, and settings data to a JSON file.
    
    Args:
        inventory: List of inventory items
        receipts: List of receipts
        settings: System settings dictionary
        filepath: Optional specific file path
        
    Returns:
        Tuple of (success, message)
    """
    file_path = filepath or DATA_FILE
    settings = settings or {}
    
    try:
        existing_settings = {}
        existing_customers = []
        if os.path.exists(file_path):
            try:
                cache = get_secure_cache(file_path)
                existing_data = cache.load()
                if isinstance(existing_data, dict):
                    existing_settings = existing_data.get("settings", {}) if isinstance(existing_data.get("settings", {}), dict) else {}
                    existing_customers = existing_data.get("customers", [])
            except Exception:
                existing_settings = {}
                existing_customers = []
        merged_settings = {**existing_settings, **settings}
        data = {
            "inventory": inventory,
            "products": [_inventory_to_product(item, merged_settings) for item in inventory],
            "receipts": receipts,
            "settings": merged_settings,
        }
        if isinstance(existing_customers, list):
            data["customers"] = existing_customers
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        cache = get_secure_cache(file_path)
        cache.save(data)
        
        return True, f"Data saved to {file_path}"
        
    except IOError as e:
        return False, f"Error saving data: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def backup_data(filepath: Optional[str] = None) -> Tuple[bool, str]:
    """
    Backup the current data file to a timestamped file.
    
    Args:
        filepath: Optional specific file path
        
    Returns:
        Tuple of (success, message)
    """
    file_path = filepath or DATA_FILE
    
    try:
        if not os.path.exists(file_path):
            return False, "No data file to backup"
        
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_dir = os.path.dirname(os.path.abspath(file_path)) or os.getcwd()
        backup_file = os.path.join(backup_dir, f"inventory_data_backup_{timestamp}.json")
        
        # Copy to backup
        with open(file_path, "r", encoding="utf-8") as src:
            with open(backup_file, "w", encoding="utf-8") as dst:
                dst.write(src.read())
        
        return True, f"Backup saved to {backup_file}"
        
    except IOError as e:
        return False, f"Error backing up file: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def get_data_directory() -> str:
    """
    Get the directory where data files are stored.
    
    Returns:
        Data directory path
    """
    import platform
    
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.path.expanduser("~/.local/share")
    
    path = os.path.join(base, "QuickStockJA")
    os.makedirs(path, exist_ok=True)
    return path


def get_data_file_path(filename: str = DATA_FILE) -> str:
    """
    Get full path to a data file.
    
    Args:
        filename: Name of the file
        
    Returns:
        Full file path
    """
    return os.path.join(get_data_directory(), filename)
