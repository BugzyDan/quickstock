"""
Validation utilities for inventory operations.
"""

import re
from typing import Any, Dict, List, Optional, Tuple


def validate_sku(sku: str) -> Tuple[bool, str]:
    """
    Validate a SKU format.
    
    Args:
        sku: The SKU to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not sku:
        return False, "SKU cannot be empty"
    
    if len(sku) < 3:
        return False, "SKU must be at least 3 characters"
    
    if len(sku) > 50:
        return False, "SKU cannot exceed 50 characters"
    
    # Allow alphanumeric, hyphens, and underscores
    if not re.match(r'^[a-zA-Z0-9\-_]+$', sku):
        return False, "SKU can only contain letters, numbers, hyphens, and underscores"
    
    return True, ""


def validate_quantity(quantity: Any, allow_zero: bool = False) -> Tuple[bool, str, int]:
    """
    Validate and convert a quantity value.
    
    Args:
        quantity: The quantity to validate
        allow_zero: Whether zero is a valid quantity
        
    Returns:
        Tuple of (is_valid, error_message, int_quantity)
    """
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        return False, "Quantity must be a valid number", 0
    
    if qty < 0:
        return False, "Quantity cannot be negative", 0
    
    if qty == 0 and not allow_zero:
        return False, "Quantity must be greater than zero", 0
    
    # Check for database limit (2^31 - 1)
    if qty > 2147483647:
        return False, "Quantity exceeds maximum allowed value", 0
    
    return True, "", qty


def validate_price(price: Any, allow_zero: bool = False) -> Tuple[bool, str, float]:
    """
    Validate and convert a price value.
    
    Args:
        price: The price to validate
        allow_zero: Whether zero is a valid price
        
    Returns:
        Tuple of (is_valid, error_message, float_price)
    """
    try:
        p = float(price)
    except (TypeError, ValueError):
        return False, "Price must be a valid number", 0.0
    
    if p < 0:
        return False, "Price cannot be negative", 0.0
    
    if p == 0 and not allow_zero:
        return False, "Price must be greater than zero", 0.0
    
    return True, "", round(p, 2)


def validate_item_data(data: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Validate complete item data for creation/update.
    
    Args:
        data: Dictionary containing item data
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    errors = []
    
    # Check required fields
    if not data.get("Name"):
        errors.append("Item name is required")
    
    if not data.get("Category"):
        errors.append("Category is required")
    
    # Validate SKU if provided
    if data.get("SKU"):
        is_valid, msg = validate_sku(data["SKU"])
        if not is_valid:
            errors.append(msg)
    
    # Validate numeric fields
    if "Amount" in data:
        is_valid, msg, _ = validate_quantity(data["Amount"])
        if not is_valid:
            errors.append(f"Amount: {msg}")
    
    if "Price" in data:
        is_valid, msg, _ = validate_price(data["Price"], allow_zero=True)
        if not is_valid:
            errors.append(f"Price: {msg}")
    
    if "Cost" in data:
        is_valid, msg, _ = validate_price(data["Cost"], allow_zero=True)
        if not is_valid:
            errors.append(f"Cost: {msg}")
    
    if errors:
        return False, "; ".join(errors)
    
    return True, ""


def sanitize_string(value: str, max_length: int = 200) -> str:
    """
    Sanitize a string value by stripping whitespace and limiting length.
    
    Args:
        value: The string to sanitize
        max_length: Maximum allowed length
        
    Returns:
        Sanitized string
    """
    if not value:
        return ""
    
    cleaned = str(value).strip()
    return cleaned[:max_length]


def search_items(
    inventory: List[Dict],
    search_term: str,
    search_by: str = "all"
) -> List[Dict]:
    """
    Search inventory by various criteria.
    
    Args:
        inventory: List of inventory items
        search_term: Term to search for
        search_by: Field to search in ('sku', 'name', 'category', 'all')
        
    Returns:
        List of matching items
    """
    if not search_term:
        return inventory
    
    term = search_term.lower().strip()
    results = []
    
    for item in inventory:
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


def filter_by_stock_level(
    inventory: List[Dict],
    min_stock: int = 0,
    max_stock: Optional[int] = None,
    include_out_of_stock: bool = False
) -> List[Dict]:
    """
    Filter inventory by stock levels.
    
    Args:
        inventory: List of inventory items
        min_stock: Minimum stock level
        max_stock: Maximum stock level (None for unlimited)
        include_out_of_stock: Whether to include items with 0 stock
        
    Returns:
        List of matching items
    """
    results = []
    
    for item in inventory:
        amount = item.get("Amount", 0)
        
        if amount == 0 and not include_out_of_stock:
            continue
        
        if amount < min_stock:
            continue
        
        if max_stock is not None and amount > max_stock:
            continue
        
        results.append(item)
    
    return results