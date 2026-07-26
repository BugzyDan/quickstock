"""
Core inventory management system.
"""

import uuid
from typing import Any, Dict, List, Optional, Tuple

from .pricing import CARIBBEAN_TAX_MAP, get_tax_info
from .sales import SalesManager
from .validation import validate_item_data, validate_price, validate_quantity


class InventorySystem:
    """
    Main inventory management system.
    
    This class handles all inventory operations including adding items,
    managing stock levels, and tracking sales.
    """
    
    def __init__(self, inventory: Optional[List[Dict]] = None, receipts: Optional[List[Dict]] = None, settings: Optional[Dict] = None):
        """
        Initialize the InventorySystem.
        
        Args:
            inventory: Existing inventory list
            receipts: Existing receipts list
            settings: System settings dictionary
        """
        self.inventory = inventory if inventory is not None else []
        self.receipts = receipts if receipts is not None else []
        
        # Default settings
        self.settings = {
            "country": "Jamaica",
            "tax_label": "GCT",
            "tax_rate": 0.15,
            "brand_name": "Your Business",
            "brand_logo": "",
            "brand_email": "",
            "brand_phone": "",
            "brand_address": "",
            "tax_inclusive": False,
            "theme": "system",
        }
        
        if settings:
            self.settings.update(settings)
        
        self.business_name = self.settings.get("brand_name") or "Your Business"
        self.business_trn = ""
        
        # Initialize sales manager
        self.sales_manager = SalesManager(self.receipts)
    
    def auto_detect_tax(self):
        """Update tax settings based on selected country."""
        country = self.settings.get("country", "Jamaica")
        tax_info = get_tax_info(country)
        self.settings["tax_label"] = tax_info["label"]
        self.settings["tax_rate"] = tax_info["rate"]
    
    def set_business_profile(self, name: Optional[str] = None, trn: Optional[str] = None):
        """
        Set business profile information.
        
        Args:
            name: Business name
            trn: Tax Registration Number
        """
        if name is not None:
            self.business_name = name
            self.settings["brand_name"] = name
        if trn is not None:
            self.business_trn = trn
    
    def _next_item_id(self) -> int:
        """Get the next item ID."""
        if not self.inventory:
            return 1
        return max(item.get("Number", 0) for item in self.inventory) + 1
    
    def generate_sku(self, category: str) -> str:
        """
        Generate a unique SKU for an item.
        
        Args:
            category: Item category
            
        Returns:
            Unique SKU string
        """
        prefix = category[:3].upper() if category else "GEN"
        while True:
            sku = f"{prefix}-{uuid.uuid4().hex[:6].upper()}"
            if not any(item.get("SKU") == sku for item in self.inventory):
                return sku
    
    def add_item(
        self,
        category: str,
        name: str,
        cost: float = 0,
        price: float = 0,
        amount: int = 0,
        sku: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Add a new item to inventory.
        
        Args:
            category: Item category
            name: Item name
            cost: Item cost price
            price: Item selling price
            amount: Initial stock quantity
            sku: Optional SKU (will generate if not provided)
            
        Returns:
            The created item dictionary
            
        Raises:
            ValueError: If validation fails
        """
        # Validate inputs
        category = str(category).strip().title() if category else ""
        
        if not name or not str(name).strip():
            raise ValueError("Item name is required")
        
        # Validate numeric values
        is_valid, msg, cost_val = validate_price(cost, allow_zero=True)
        if not is_valid:
            raise ValueError(f"Invalid cost: {msg}")
        
        is_valid, msg, price_val = validate_price(price, allow_zero=True)
        if not is_valid:
            raise ValueError(f"Invalid price: {msg}")
        
        is_valid, msg, amount_val = validate_quantity(amount)
        if not is_valid:
            raise ValueError(f"Invalid amount: {msg}")
        
        # Generate or validate SKU
        if not sku:
            sku = self.generate_sku(category)
        else:
            # Check if SKU already exists
            if any(item.get("SKU") == str(sku) for item in self.inventory):
                raise ValueError(f"SKU '{sku}' already exists")
        
        # Create item
        item = {
            "Number": self._next_item_id(),
            "SKU": str(sku),
            "Category": category,
            "Name": str(name).strip(),
            "Cost": cost_val,
            "Price": price_val,
            "Amount": amount_val,
        }
        
        self.inventory.append(item)
        return item
    
    def get_item_by_sku(self, sku: str) -> Optional[Dict[str, Any]]:
        """
        Find an item by SKU.
        
        Args:
            sku: Item SKU to search for
            
        Returns:
            Item dictionary or None if not found
        """
        search_sku = str(sku).strip()
        return next((item for item in self.inventory if str(item.get("SKU")) == search_sku), None)
    
    def get_item_by_number(self, number: int) -> Optional[Dict[str, Any]]:
        """
        Find an item by its number.
        
        Args:
            number: Item number to search for
            
        Returns:
            Item dictionary or None if not found
        """
        for item in self.inventory:
            if item.get("Number") == number:
                return item
        return None
    
    def update_item(self, item_number: int, updates: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Update an existing item.
        
        Args:
            item_number: Number of item to update
            updates: Dictionary of fields to update
            
        Returns:
            Tuple of (success, message)
        """
        item = self.get_item_by_number(item_number)
        if not item:
            return False, "Item not found"
        
        # Validate updates
        if updates:
            # Create a copy with updates for validation
            test_item = {**item, **updates}
            is_valid, msg = validate_item_data(test_item)
            if not is_valid:
                return False, msg
        
        # Apply updates
        for key, value in updates.items():
            if key in item:
                item[key] = value
        
        return True, "Item updated successfully"
    
    def delete_item(self, item_number: int) -> Tuple[bool, str]:
        """
        Delete an item from inventory.
        
        Args:
            item_number: Number of item to delete
            
        Returns:
            Tuple of (success, message)
        """
        item = self.get_item_by_number(item_number)
        if not item:
            return False, "Item not found"
        
        self.inventory = [i for i in self.inventory if i.get("Number") != item_number]
        return True, "Item deleted successfully"
    
    def sell_item(
        self,
        sku: str,
        quantity: int,
        customer_name: str = "",
        document_type: str = "Invoice",
    ) -> Dict[str, Any]:
        """
        Sell an item (process a sale).
        
        Args:
            sku: Item SKU
            quantity: Quantity to sell
            
        Returns:
            Receipt for the sale
            
        Raises:
            ValueError: If sale cannot be completed
        """
        item = self.get_item_by_sku(sku)
        if not item:
            raise ValueError(f"Item with SKU {sku} not found")
        
        return self.sales_manager.process_sale(
            item=item,
            quantity=quantity,
            inventory=self.inventory,
            settings=self.settings,
            business_trn=self.business_trn,
            customer_name=customer_name,
            document_type=document_type,
        )
    
    def receive_stock(self, sku: str, quantity: int, cost: Optional[float] = None) -> Tuple[bool, str]:
        """
        Receive stock for an item.
        
        Args:
            sku: Item SKU
            quantity: Quantity received
            cost: Optional new cost price
            
        Returns:
            Tuple of (success, message)
        """
        item = self.get_item_by_sku(sku)
        if not item:
            return False, "Item not found"
        
        is_valid, msg, qty = validate_quantity(quantity)
        if not is_valid:
            return False, msg
        
        item["Amount"] += qty
        
        if cost is not None:
            is_valid, msg, cost_val = validate_price(cost, allow_zero=True)
            if is_valid:
                item["Cost"] = cost_val
        
        return True, f"Received {qty} units of {item['Name']}"
    
    def transfer_stock(self, from_sku: str, to_sku: str, quantity: int) -> Tuple[bool, str]:
        """
        Transfer stock between items (rarely used, but supported).
        
        Args:
            from_sku: Source item SKU
            to_sku: Destination item SKU
            quantity: Quantity to transfer
            
        Returns:
            Tuple of (success, message)
        """
        from_item = self.get_item_by_sku(from_sku)
        to_item = self.get_item_by_sku(to_sku)
        
        if not from_item:
            return False, "Source item not found"
        if not to_item:
            return False, "Destination item not found"
        
        if from_item["Amount"] < quantity:
            return False, f"Insufficient stock (have {from_item['Amount']}, need {quantity})"
        
        from_item["Amount"] -= quantity
        to_item["Amount"] += quantity
        
        return True, f"Transferred {quantity} units"
    
    def search_items(self, search_term: str, search_by: str = "all") -> List[Dict[str, Any]]:
        """
        Search inventory items.
        
        Args:
            search_term: Term to search for
            search_by: Field to search in ('sku', 'name', 'category', 'all')
            
        Returns:
            List of matching items
        """
        if not search_term:
            return self.inventory.copy()
        
        term = search_term.lower().strip()
        results = []
        
        for item in self.inventory:
            match = False
            
            if search_by in ("sku", "all"):
                if term in item.get("SKU", "").lower():
                    match = True
            
            if search_by in ("name", "all"):
                if term in item.get("Name", "").lower():
                    match = True
            
            if search_by in ("category", "all"):
                if term in item.get("Category", "").lower():
                    match = True
            
            if match:
                results.append(item)
        
        return results
    
    def get_low_stock_items(self, threshold: int = 5) -> List[Dict[str, Any]]:
        """
        Get items with stock below threshold.
        
        Args:
            threshold: Minimum stock level
            
        Returns:
            List of low stock items
        """
        return [item for item in self.inventory if item.get("Amount", 0) < threshold]
    
    def get_out_of_stock_items(self) -> List[Dict[str, Any]]:
        """
        Get items that are out of stock.
        
        Returns:
            List of out of stock items
        """
        return [item for item in self.inventory if item.get("Amount", 0) == 0]
    
    def get_total_valuation(self) -> Tuple[float, float]:
        """
        Calculate total inventory value.
        
        Returns:
            Tuple of (total_cost_value, total_retail_value)
        """
        total_cost = sum(
            float(item.get("Cost", 0)) * int(item.get("Amount", 0))
            for item in self.inventory
        )
        total_retail = sum(
            float(item.get("Price", 0)) * int(item.get("Amount", 0))
            for item in self.inventory
        )
        return round(total_cost, 2), round(total_retail, 2)
    
    def get_inventory_summary(self) -> Dict[str, Any]:
        """
        Get summary statistics for inventory.
        
        Returns:
            Dictionary with summary data
        """
        total_items = len(self.inventory)
        total_quantity = sum(item.get("Amount", 0) for item in self.inventory)
        categories = set(item.get("Category", "") for item in self.inventory)
        out_of_stock = len(self.get_out_of_stock_items())
        low_stock = len(self.get_low_stock_items())
        cost_value, retail_value = self.get_total_valuation()
        
        return {
            "total_items": total_items,
            "total_quantity": total_quantity,
            "categories": len(categories),
            "out_of_stock": out_of_stock,
            "low_stock": low_stock,
            "cost_value": cost_value,
            "retail_value": retail_value,
            "potential_profit": round(retail_value - cost_value, 2),
        }
    
    def clear_inventory(self):
        """Clear all inventory and receipts (use with caution)."""
        self.inventory.clear()
        self.receipts.clear()
