"""
Inventory service that wraps core inventory logic with persistence and API-based sync.
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..core.inventory import InventorySystem
from ..core.pricing import get_tax_info
from ..storage.local_store import load_data as load_local_data, save_data as save_local_data
from .sync_service import SyncService, SyncConflictResolution


class InventoryService:
    """
    Service layer for inventory operations.
    
    This class wraps the core InventorySystem and adds:
    - Data persistence
    - Sync capabilities with bidirectional sync
    - API integration
    - Conflict resolution
    """
    
    def __init__(self, storage_path: Optional[str] = None):
        """
        Initialize the inventory service.
        
        Args:
            storage_path: Path to storage directory
        """
        self.system = InventorySystem()
        self.storage_path = storage_path or self._default_storage_path()
        self.last_sync_time: Optional[datetime] = None
        self.pending_changes: List[Dict] = []
        
        # Initialize sync service
        self.sync_service = SyncService(self.storage_path)
        self.sync_service.set_conflict_resolution(SyncConflictResolution.LAST_WRITE_WINS)
        
        # Try to load existing data
        self._load_data()
    
    def _default_storage_path(self) -> str:
        """Get default storage path based on OS."""
        import platform
        if platform.system() == "Windows":
            base = os.environ.get("APPDATA", os.path.expanduser("~"))
        else:
            base = os.path.expanduser("~/.local/share")
        
        path = os.path.join(base, "QuickStockJA")
        os.makedirs(path, exist_ok=True)
        return path
    
    def _load_data(self):
        """Load inventory data from storage."""
        try:
            inventory_file = os.path.join(self.storage_path, "inventory_data.json")
            inventory, receipts, settings = load_local_data(inventory_file)
            self.system.inventory = inventory or []
            self.system.receipts = receipts or []
            if settings:
                self.system.settings.update(settings)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Could not load inventory data: {e}")
    
    def save_data(self):
        """Save inventory data to storage."""
        try:
            inventory_file = os.path.join(self.storage_path, "inventory_data.json")
            save_local_data(
                self.system.inventory,
                self.system.receipts,
                self.system.settings,
                inventory_file,
            )
        except IOError as e:
            print(f"Error saving inventory data: {e}")
    
    # Inventory CRUD Operations
    
    def add_item(
        self,
        category: str,
        name: str,
        cost: float = 0,
        price: float = 0,
        amount: int = 0,
        sku: Optional[str] = None,
    ) -> Tuple[bool, str, Optional[Dict]]:
        """
        Add a new item to inventory.
        
        Returns:
            Tuple of (success, message, item)
        """
        try:
            item = self.system.add_item(
                category=category,
                name=name,
                cost=cost,
                price=price,
                amount=amount,
                sku=sku,
            )
            self.save_data()
            self._queue_change("add_item", {"item": item})
            return True, "Item added successfully", item
        except ValueError as e:
            return False, str(e), None
    
    def update_item(
        self,
        item_number: int,
        updates: Dict[str, Any],
    ) -> Tuple[bool, str]:
        """
        Update an existing item.
        
        Returns:
            Tuple of (success, message)
        """
        success, message = self.system.update_item(item_number, updates)
        if success:
            self.save_data()
            self._queue_change("update_item", {"item_number": item_number, "updates": updates})
        return success, message
    
    def delete_item(self, item_number: int) -> Tuple[bool, str]:
        """
        Delete an item from inventory.
        
        Returns:
            Tuple of (success, message)
        """
        success, message = self.system.delete_item(item_number)
        if success:
            self.save_data()
            self._queue_change("delete_item", {"item_number": item_number})
        return success, message
    
    def get_item(self, item_number: int) -> Optional[Dict]:
        """Get an item by number."""
        return self.system.get_item_by_number(item_number)
    
    def get_item_by_sku(self, sku: str) -> Optional[Dict]:
        """Get an item by SKU."""
        return self.system.get_item_by_sku(sku)
    
    def search_items(self, search_term: str, search_by: str = "all") -> List[Dict]:
        """Search inventory items."""
        return self.system.search_items(search_term, search_by)
    
    def get_all_items(self) -> List[Dict]:
        """Get all inventory items."""
        return self.system.inventory.copy()
    
    def get_inventory_summary(self) -> Dict:
        """Get inventory summary statistics."""
        return self.system.get_inventory_summary()
    
    # Stock Operations
    
    def receive_stock(
        self,
        sku: str,
        quantity: int,
        cost: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Receive stock for an item.
        
        Returns:
            Tuple of (success, message)
        """
        success, message = self.system.receive_stock(sku, quantity, cost)
        if success:
            self.save_data()
            self._queue_change("receive_stock", {"sku": sku, "quantity": quantity})
        return success, message
    
    def sell_item(self, sku: str, quantity: int) -> Tuple[bool, str, Optional[Dict]]:
        """
        Sell an item.
        
        Returns:
            Tuple of (success, message, receipt)
        """
        try:
            receipt = self.system.sell_item(sku, quantity)
            self.save_data()
            self._queue_change("sell_item", {"sku": sku, "quantity": quantity})
            return True, "Sale completed", receipt
        except ValueError as e:
            return False, str(e), None
    
    def transfer_stock(
        self,
        from_sku: str,
        to_sku: str,
        quantity: int,
    ) -> Tuple[bool, str]:
        """
        Transfer stock between items.
        
        Returns:
            Tuple of (success, message)
        """
        success, message = self.system.transfer_stock(from_sku, to_sku, quantity)
        if success:
            self.save_data()
            self._queue_change("transfer_stock", {
                "from_sku": from_sku,
                "to_sku": to_sku,
                "quantity": quantity,
            })
        return success, message
    
    # Settings Management
    
    def update_settings(self, settings: Dict[str, Any]):
        """Update system settings."""
        self.system.settings.update(settings)
        self.save_data()
    
    def get_settings(self) -> Dict:
        """Get current settings."""
        return self.system.settings.copy()
    
    def set_tax_from_country(self, country: str):
        """Set tax rate based on country."""
        self.system.settings["country"] = country
        self.system.auto_detect_tax()
        self.save_data()
    
    def set_business_profile(self, name: Optional[str] = None, trn: Optional[str] = None):
        """Set business profile."""
        self.system.set_business_profile(name, trn)
        self.save_data()
    
    # Change Queue for Offline Sync
    
    def _queue_change(self, action: str, data: Dict):
        """Queue a change for later sync."""
        # Use the robust persistent sync service
        item_id = None
        if "sku" in data:
            item_id = data["sku"]
        elif "item" in data and isinstance(data["item"], dict):
            item_id = data["item"].get("sku") or data["item"].get("SKU")
            
        self.sync_service.queue_action(
            action=action,
            data=data,
            priority=1 if action in ["sell_item", "receive_stock"] else 0,
            item_id=item_id
        )
    
    def get_pending_changes(self) -> List[Dict]:
        """Get list of pending changes for sync."""
        return self.pending_changes.copy()
    
    def clear_pending_changes(self):
        """Clear pending changes after successful sync."""
        self.pending_changes.clear()
        self.last_sync_time = datetime.now(timezone.utc)
    
    # Export/Import
    
    def export_inventory_csv(self, filepath: str) -> Tuple[bool, str]:
        """
        Export inventory to CSV file.
        
        Returns:
            Tuple of (success, message)
        """
        try:
            import csv
            
            if not self.system.inventory:
                return False, "No inventory to export"
            
            fieldnames = [
                "Item Number",
                "SKU",
                "Category",
                "Item Name",
                "Unit Cost",
                "Unit Price",
                "Stock On Hand",
            ]
            
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for item in self.system.inventory:
                    writer.writerow({
                        "Item Number": item.get("Number", ""),
                        "SKU": item.get("SKU", ""),
                        "Category": item.get("Category", ""),
                        "Item Name": item.get("Name", ""),
                        "Unit Cost": item.get("Cost", ""),
                        "Unit Price": item.get("Price", ""),
                        "Stock On Hand": item.get("Amount", ""),
                    })
            
            return True, f"Exported {len(self.system.inventory)} items to {filepath}"
        except IOError as e:
            return False, f"Export failed: {e}"
    
    def import_inventory_csv(self, filepath: str) -> Tuple[bool, str, int]:
        """
        Import inventory from CSV file.
        
        Returns:
            Tuple of (success, message, items_imported)
        """
        try:
            import csv
            
            imported = 0
            with open(filepath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        category = row.get("Category") or row.get("Item Category") or "Imported"
                        name = row.get("Item Name") or row.get("Name") or "Unknown"
                        cost = row.get("Unit Cost") or row.get("Cost") or 0
                        price = row.get("Unit Price") or row.get("Price") or 0
                        amount = row.get("Stock On Hand") or row.get("Amount") or 0
                        sku = row.get("SKU")
                        self.system.add_item(
                            category=category,
                            name=name,
                            cost=float(cost),
                            price=float(price),
                            amount=int(amount),
                            sku=sku,
                        )
                        imported += 1
                    except (ValueError, KeyError) as e:
                        print(f"Skipping invalid row: {e}")
                        continue
            
            self.save_data()
            return True, f"Imported {imported} items", imported
        except IOError as e:
            return False, f"Import failed: {e}", 0
