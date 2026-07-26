"""
Cart and Point of Sale service.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..core.pricing import calculate_tax, get_tax_label, get_tax_rate
from ..core.sales import build_receipt


class CartItem:
    """Represents an item in the shopping cart."""
    
    def __init__(self, item: Dict[str, Any], quantity: int = 1):
        """
        Initialize a cart item.
        
        Args:
            item: Inventory item dictionary
            quantity: Quantity to add to cart
        """
        self.sku = item.get("SKU", "")
        self.name = item.get("Name", "")
        self.price = float(item.get("Price", 0))
        self.stock = int(item.get("Amount", 0))
        self.quantity = min(quantity, self.stock)  # Can't add more than stock
        self.discount_percent = 0.0
    
    @property
    def subtotal(self) -> float:
        """Get line subtotal before tax."""
        return round(self.price * self.quantity, 2)
    
    @property
    def discount_amount(self) -> float:
        """Get discount amount for this line."""
        return round(self.subtotal * (self.discount_percent / 100), 2)
    
    @property
    def line_total(self) -> float:
        """Get line total after discount."""
        return round(self.subtotal - self.discount_amount, 2)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "SKU": self.sku,
            "Name": self.name,
            "Quantity": self.quantity,
            "Unit Price": self.price,
            "Discount %": self.discount_percent,
            "Subtotal": self.subtotal,
            "Discount": self.discount_amount,
            "Total": self.line_total,
        }


class CartService:
    """
    Service for managing shopping cart and POS operations.
    """
    
    def __init__(self, settings: Optional[Dict] = None):
        """
        Initialize cart service.
        
        Args:
            settings: System settings for tax calculation
        """
        self.items: List[CartItem] = []
        self.settings = settings or {
            "tax_rate": 0.15,
            "tax_label": "GCT",
            "country": "Jamaica",
        }
        self.discount_percent = 0.0  # Cart-wide discount
        self.customer_name = ""
    
    def add_item(self, item: Dict[str, Any], quantity: int = 1) -> Tuple[bool, str]:
        """
        Add an item to the cart.
        
        Args:
            item: Inventory item dictionary
            quantity: Quantity to add
            
        Returns:
            Tuple of (success, message)
        """
        if not item:
            return False, "Item not found"
        
        if quantity <= 0:
            return False, "Quantity must be positive"
        
        # Check if item already in cart
        sku = item.get("SKU", "")
        existing = self.get_item_by_sku(sku)
        
        if existing:
            # Update quantity
            new_qty = existing.quantity + quantity
            available_stock = item.get("Amount", 0)
            if new_qty > available_stock:
                return False, f"Not enough stock (available: {available_stock})"
            existing.quantity = new_qty
        else:
            # Add new item
            cart_item = CartItem(item, quantity)
            self.items.append(cart_item)
        
        return True, f"Added {quantity} x {item.get('Name', '')} to cart"
    
    def remove_item(self, sku: str) -> Tuple[bool, str]:
        """
        Remove an item from the cart.
        
        Args:
            sku: Item SKU to remove
            
        Returns:
            Tuple of (success, message)
        """
        for i, item in enumerate(self.items):
            if item.sku == sku:
                self.items.pop(i)
                return True, f"Removed {item.name} from cart"
        
        return False, "Item not in cart"
    
    def update_quantity(self, sku: str, quantity: int, available_stock: int) -> Tuple[bool, str]:
        """
        Update quantity of an item in cart.
        
        Args:
            sku: Item SKU
            quantity: New quantity
            available_stock: Current stock available
            
        Returns:
            Tuple of (success, message)
        """
        item = self.get_item_by_sku(sku)
        if not item:
            return False, "Item not in cart"
        
        if quantity <= 0:
            return self.remove_item(sku)
        
        if quantity > available_stock:
            return False, f"Not enough stock (available: {available_stock})"
        
        item.quantity = quantity
        return True, f"Updated quantity to {quantity}"
    
    def get_item_by_sku(self, sku: str) -> Optional[CartItem]:
        """Get cart item by SKU."""
        for item in self.items:
            if item.sku == sku:
                return item
        return None
    
    def clear(self):
        """Clear the entire cart."""
        self.items.clear()
        self.discount_percent = 0.0
        self.customer_name = ""
    
    def is_empty(self) -> bool:
        """Check if cart is empty."""
        return len(self.items) == 0
    
    def item_count(self) -> int:
        """Get total number of items in cart."""
        return sum(item.quantity for item in self.items)
    
    def line_count(self) -> int:
        """Get number of line items in cart."""
        return len(self.items)
    
    @property
    def subtotal(self) -> float:
        """Get cart subtotal before tax and discounts."""
        return round(sum(item.subtotal for item in self.items), 2)
    
    @property
    def total_discount(self) -> float:
        """Get total discount amount."""
        line_discounts = sum(item.discount_amount for item in self.items)
        cart_discount = self.subtotal * (self.discount_percent / 100)
        return round(line_discounts + cart_discount, 2)
    
    @property
    def taxable_amount(self) -> float:
        """Get amount subject to tax."""
        return round(self.subtotal - self.total_discount, 2)
    
    @property
    def tax_amount(self) -> float:
        """Get calculated tax amount."""
        _, tax, _ = calculate_tax(self.taxable_amount, self.settings.get("country", "Jamaica"))
        return tax
    
    @property
    def total(self) -> float:
        """Get cart total including tax."""
        return round(self.taxable_amount + self.tax_amount, 2)
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Get cart summary for display.
        
        Returns:
            Dictionary with cart totals
        """
        return {
            "item_count": self.item_count(),
            "line_count": self.line_count(),
            "subtotal": self.subtotal,
            "discount": self.total_discount,
            "taxable_amount": self.taxable_amount,
            "tax_label": self.settings.get("tax_label", "GCT"),
            "tax_amount": self.tax_amount,
            "total": self.total,
        }
    
    def get_receipt_data(
        self,
        invoice_no: int,
        business_name: str = "QuickStock JA",
        business_trn: str = "",
        customer_name: str = "",
        document_type: str = "Invoice",
        brand_logo: Optional[str] = None,
        brand_email: str = "",
        brand_phone: str = "",
        brand_address: str = "",
    ) -> Dict[str, Any]:
        """
        Get receipt data for the cart.
        
        Args:
            invoice_no: Invoice number
            business_name: Business name for receipt
            business_trn: Business TRN
            customer_name: Customer associated with the receipt
            document_type: Receipt type such as Invoice or Quotation
            brand_logo: Logo filename/URL
            brand_email: Contact email
            brand_phone: Contact phone
            brand_address: Business address
            
        Returns:
            Receipt dictionary
        """
        tax_label = self.settings.get("tax_label", "GCT")
        
        receipt = {
            "Invoice No": invoice_no,
            "Business Name": business_name,
            "Business TRN": business_trn,
            "Customer Name": customer_name.strip(),
            "Document Type": document_type.strip() or "Invoice",
            "Brand Logo": brand_logo,
            "Brand Email": brand_email,
            "Brand Phone": brand_phone,
            "Brand Address": brand_address,
            "TaxLabel": tax_label,
            "Items Purchased": [item.to_dict() for item in self.items],
            "Subtotal": self.subtotal,
            "Discount": self.total_discount,
            tax_label: self.tax_amount,
            "GCT": self.tax_amount,  # Backwards compatibility
            "Total Bill": self.total,
            "Timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        return receipt
    
    def validate_stock(self, inventory_lookup) -> List[str]:
        """
        Validate that all items in cart have sufficient stock.
        
        Args:
            inventory_lookup: Function that takes SKU and returns current stock
            
        Returns:
            List of error messages for items with insufficient stock
        """
        errors = []
        for item in self.items:
            current_stock = inventory_lookup(item.sku)
            if current_stock is None:
                errors.append(f"{item.name}: Item no longer available")
            elif item.quantity > current_stock:
                errors.append(f"{item.name}: Only {current_stock} available (cart has {item.quantity})")
        return errors
    
    def apply_cart_discount(self, percent: float):
        """
        Apply a cart-wide discount.
        
        Args:
            percent: Discount percentage (0-100)
        """
        self.discount_percent = max(0, min(100, percent))
    
    def set_customer(self, name: str):
        """Set customer name for the sale."""
        self.customer_name = name
    
    def to_list(self) -> List[Dict]:
        """Convert cart to list of dictionaries."""
        return [item.to_dict() for item in self.items]
    
    @classmethod
    def from_list(cls, items_list: List[Dict], settings: Optional[Dict] = None) -> 'CartService':
        """
        Create a CartService from a list of item dictionaries.
        
        Args:
            items_list: List of item dictionaries with SKU, Name, Price, Quantity
            settings: System settings
            
        Returns:
            CartService instance
        """
        cart = cls(settings)
        for item_data in items_list:
            cart_item = CartItem(
                item={
                    "SKU": item_data.get("SKU", ""),
                    "Name": item_data.get("Name", ""),
                    "Price": item_data.get("Unit Price", 0),
                    "Amount": item_data.get("Quantity", 1),  # Default to what was saved
                },
                quantity=item_data.get("Quantity", 1),
            )
            cart_item.discount_percent = item_data.get("Discount %", 0)
            cart.items.append(cart_item)
        return cart
