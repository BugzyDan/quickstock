"""
Pricing and tax calculation utilities for Caribbean regions.
"""

from typing import Dict, Optional, Tuple

# Caribbean tax rates and labels
CARIBBEAN_TAX_MAP = {
    "Jamaica": {"label": "GCT", "rate": 0.15},
    "JM": {"label": "GCT", "rate": 0.15},
    "Barbados": {"label": "VAT", "rate": 0.175},
    "BB": {"label": "VAT", "rate": 0.175},
    "Trinidad": {"label": "VAT", "rate": 0.125},
    "Trinidad & Tobago": {"label": "VAT", "rate": 0.125},
    "TT": {"label": "VAT", "rate": 0.125},
    "Guyana": {"label": "VAT", "rate": 0.14},
    "Saint Lucia": {"label": "VAT", "rate": 0.125},
    "International": {"label": "TAX", "rate": 0.00}
}


def get_tax_info(country: str) -> Dict[str, any]:
    """
    Get tax information for a specific country.
    
    Args:
        country: Country name or code
        
    Returns:
        Dictionary with 'label' and 'rate' keys
    """
    return CARIBBEAN_TAX_MAP.get(country, CARIBBEAN_TAX_MAP["International"])


def calculate_tax(
    subtotal: float,
    country: str = "Jamaica",
    tax_inclusive: bool = False
) -> Tuple[float, float, float]:
    """
    Calculate tax amount and total.
    
    Args:
        subtotal: Pre-tax amount
        country: Country for tax rate lookup
        tax_inclusive: If True, subtotal already includes tax
        
    Returns:
        Tuple of (subtotal, tax_amount, total)
    """
    tax_info = get_tax_info(country)
    tax_rate = tax_info["rate"]
    
    if tax_inclusive:
        # Extract tax from inclusive amount
        tax_amount = subtotal - (subtotal / (1 + tax_rate))
        total = subtotal
    else:
        tax_amount = subtotal * tax_rate
        total = subtotal + tax_amount
    
    return round(subtotal, 2), round(tax_amount, 2), round(total, 2)


def get_tax_label(country: str = "Jamaica") -> str:
    """Get the tax label for a country (e.g., 'GCT', 'VAT')."""
    return get_tax_info(country)["label"]


def get_tax_rate(country: str = "Jamaica") -> float:
    """Get the tax rate for a country as a decimal (e.g., 0.15)."""
    return get_tax_info(country)["rate"]


def format_price(amount: float, currency: str = "JMD") -> str:
    """
    Format a monetary amount with currency symbol.
    
    Args:
        amount: The amount to format
        currency: Currency code (default: JMD)
        
    Returns:
        Formatted string like "$1,234.56"
    """
    symbols = {
        "JMD": "$",
        "USD": "$",
        "BBD": "$",
        "TTD": "TT$",
        "GYD": "G$",
        "XCD": "EC$",
    }
    symbol = symbols.get(currency, "$")
    return f"{symbol}{amount:,.2f}"


def calculate_discount(
    amount: float,
    discount_percent: float,
    apply_tax_after: bool = True
) -> float:
    """
    Calculate discounted amount.
    
    Args:
        amount: Original amount
        discount_percent: Discount as percentage (e.g., 10 for 10%)
        apply_tax_after: If True, tax is calculated after discount
        
    Returns:
        Discounted amount
    """
    discount_amount = amount * (discount_percent / 100)
    return round(amount - discount_amount, 2)