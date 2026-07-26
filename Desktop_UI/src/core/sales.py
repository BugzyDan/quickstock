"""
Sales and receipt management logic.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .pricing import calculate_tax, get_tax_label, get_tax_rate


def build_receipt(
    item: Dict[str, Any],
    quantity: int,
    invoice_no: Optional[int] = None,
    business_name: str = "QuickStock JA",
    business_trn: str = "",
    customer_name: str = "",
    document_type: str = "Invoice",
    brand_logo: Optional[str] = None,
    brand_email: str = "",
    brand_phone: str = "",
    brand_address: str = "",
    tax_rate: float = 0.15,
    tax_label: str = "GCT",
) -> Dict[str, Any]:
    """
    Build a receipt for a sale transaction.
    
    Args:
        item: The item being sold
        quantity: Quantity being sold
        invoice_no: Invoice number (auto-generated if None)
        business_name: Name of the business
        business_trn: Business TRN
        customer_name: Customer associated with the receipt
        document_type: Receipt type such as Invoice or Quotation
        brand_logo: Logo filename or URL
        brand_email: Contact email
        brand_phone: Contact phone
        brand_address: Business address
        tax_rate: Tax rate as decimal
        tax_label: Tax label (e.g., 'GCT', 'VAT')
        
    Returns:
        Receipt dictionary
    """
    # Calculate amounts
    unit_price = float(item.get("Price", 0))
    subtotal = unit_price * quantity
    tax_amount = subtotal * tax_rate
    total = subtotal + tax_amount
    
    receipt = {
        "Invoice No": invoice_no or _generate_invoice_number(),
        "Business Name": business_name,
        "Business TRN": business_trn,
        "Customer Name": customer_name.strip(),
        "Document Type": document_type.strip() or "Invoice",
        "Brand Logo": brand_logo,
        "Brand Email": brand_email,
        "Brand Phone": brand_phone,
        "Brand Address": brand_address,
        "TaxLabel": tax_label,
        "Items Purchased": [
            {
                "SKU": item.get("SKU", ""),
                "Name": item.get("Name", ""),
                "Quantity": quantity,
                "Unit Price": unit_price,
                "Total": round(unit_price * quantity, 2),
            }
        ],
        "Subtotal": round(subtotal, 2),
        tax_label: round(tax_amount, 2),
        "GCT": round(tax_amount, 2),  # Keep for backwards compatibility
        "Total Bill": round(total, 2),
        "Timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    return receipt


def _generate_invoice_number() -> int:
    """Generate a unique invoice number based on timestamp."""
    return int(datetime.now(timezone.utc).timestamp() % 1000000)


class SalesManager:
    """Manages sales operations and receipt history."""
    
    def __init__(self, receipts: Optional[List[Dict]] = None):
        """
        Initialize SalesManager.
        
        Args:
            receipts: Existing receipts list (will be modified in place)
        """
        self.receipts = receipts if receipts is not None else []
    
    def process_sale(
        self,
        item: Dict[str, Any],
        quantity: int,
        inventory: List[Dict[str, Any]],
        settings: Dict[str, Any],
        business_trn: str = "",
        customer_name: str = "",
        document_type: str = "Invoice",
    ) -> Dict[str, Any]:
        """
        Process a complete sale transaction.
        
        Args:
            item: Item being sold
            quantity: Quantity to sell
            inventory: Inventory list to update
            settings: System settings with tax info
            business_trn: Business TRN
            
        Returns:
            Receipt for the sale
            
        Raises:
            ValueError: If sale cannot be completed
        """
        # Validate
        if not item:
            raise ValueError("Item not found")
        
        if quantity <= 0:
            raise ValueError("Invalid quantity")
        
        current_stock = item.get("Amount", 0)
        if quantity > current_stock:
            raise ValueError(f"Not enough stock (have {current_stock}, need {quantity})")
        
        # Get tax info from settings
        tax_rate = settings.get("tax_rate", 0.15)
        tax_label = settings.get("tax_label", "GCT")
        business_name = settings.get("brand_name", "QuickStock JA")
        brand_logo = settings.get("brand_logo")
        brand_email = settings.get("brand_email", "")
        brand_phone = settings.get("brand_phone", "")
        brand_address = settings.get("brand_address", "")
        
        # Generate invoice number
        invoice_no = self._next_invoice_number() + 1
        
        # Build receipt
        receipt = build_receipt(
            item=item,
            quantity=quantity,
            invoice_no=invoice_no,
            business_name=business_name,
            business_trn=business_trn,
            customer_name=customer_name,
            document_type=document_type,
            brand_logo=brand_logo,
            brand_email=brand_email,
            brand_phone=brand_phone,
            brand_address=brand_address,
            tax_rate=tax_rate,
            tax_label=tax_label,
        )
        
        # Update stock
        item["Amount"] -= quantity
        
        # Record sale
        self.receipts.append(receipt)
        
        return receipt
    
    def _next_invoice_number(self) -> int:
        """Get the next invoice number based on existing receipts."""
        max_no = 0
        for receipt in self.receipts:
            raw = receipt.get("Invoice No")
            if raw is None:
                raw = receipt.get("Invoice Code") or receipt.get("Invoice #")
            if raw is None:
                continue
            try:
                num = int(raw)
            except (TypeError, ValueError):
                digits = "".join(ch for ch in str(raw) if ch.isdigit())
                num = int(digits) if digits else 0
            if num > max_no:
                max_no = num
        return max_no
    
    def get_daily_summary(self, date: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Get summary of sales for a specific date.
        
        Args:
            date: Date to summarize (defaults to today)
            
        Returns:
            Dictionary with summary statistics
        """
        if date is None:
            date = datetime.now(timezone.utc)
        
        target_date = date.strftime("%Y-%m-%d")
        
        total_items_sold = 0
        total_sales = 0.0
        receipt_count = 0
        
        for receipt in self.receipts:
            timestamp = receipt.get("Timestamp", "")
            if timestamp.startswith(target_date):
                receipt_count += 1
                for item in receipt.get("Items Purchased", []):
                    total_items_sold += item.get("Quantity", 0)
                    total_sales += item.get("Total", 0)
        
        return {
            "date": target_date,
            "receipt_count": receipt_count,
            "total_items_sold": total_items_sold,
            "total_sales": round(total_sales, 2),
        }
    
    def get_receipt_history(
        self,
        limit: Optional[int] = None,
        reverse: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get receipt history.
        
        Args:
            limit: Maximum number of receipts to return
            reverse: If True, return newest first
            
        Returns:
            List of receipts
        """
        receipts = self.receipts.copy()
        
        if reverse:
            receipts.reverse()
        
        if limit and limit > 0:
            receipts = receipts[:limit]
        
        return receipts
    
    def clear_receipts(self):
        """Clear all receipts (use with caution)."""
        self.receipts.clear()
