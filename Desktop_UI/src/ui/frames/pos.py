"""
Point of Sale (POS) frame for QuickStock JA.
"""

import customtkinter as ctk
import tkinter as tk
from typing import Callable, Dict, List, Optional


class POSFrame(ctk.CTkFrame):
    """Point of Sale frame component for processing sales."""
    
    def __init__(self, master, on_sale_complete: Optional[Callable] = None, **kwargs):
        super().__init__(master, **kwargs)
        self.on_sale_complete = on_sale_complete
        self._setup_ui()
    
    def _setup_ui(self):
        """Setup the POS UI."""
        # Title
        self.title_label = ctk.CTkLabel(
            self,
            text="Cash Register",
            font=("Arial", 24, "bold"),
        )
        self.title_label.pack(pady=10)
        
        # Main content area (split into cart and items)
        self.content_frame = ctk.CTkFrame(self)
        self.content_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Left side - Cart
        self.cart_frame = ctk.CTkFrame(self.content_frame, width=300)
        self.cart_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))
        
        ctk.CTkLabel(self.cart_frame, text="Cart", font=("Arial", 16, "bold")).pack(pady=10)
        
        # Cart items list
        self.cart_list_frame = ctk.CTkScrollableFrame(self.cart_frame)
        self.cart_list_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # Cart totals
        self.totals_frame = ctk.CTkFrame(self.cart_frame)
        self.totals_frame.pack(fill="x", padx=10, pady=10)
        
        self.subtotal_label = ctk.CTkLabel(self.totals_frame, text="Subtotal: $0.00")
        self.subtotal_label.pack(pady=5)
        
        self.tax_label = ctk.CTkLabel(self.totals_frame, text="Tax (GCT): $0.00")
        self.tax_label.pack(pady=5)
        
        self.total_label = ctk.CTkLabel(
            self.totals_frame, 
            text="Total: $0.00", 
            font=("Arial", 14, "bold"),
        )
        self.total_label.pack(pady=5)
        
        # Checkout button
        self.checkout_button = ctk.CTkButton(
            self.cart_frame,
            text="Checkout",
            command=self._process_checkout,
            height=50,
            fg_color="#2ECC71",
        )
        self.checkout_button.pack(fill="x", padx=10, pady=10)
        
        # Clear cart button
        self.clear_button = ctk.CTkButton(
            self.cart_frame,
            text="Clear Cart",
            command=self._clear_cart,
            fg_color="#E74C3C",
        )
        self.clear_button.pack(fill="x", padx=10, pady=5)
        
        # Right side - Item entry
        self.entry_frame = ctk.CTkFrame(self.content_frame, width=250)
        self.entry_frame.pack(side="right", fill="both", expand=True)
        
        ctk.CTkLabel(self.entry_frame, text="Add Item", font=("Arial", 16, "bold")).pack(pady=10)
        
        # SKU entry
        ctk.CTkLabel(self.entry_frame, text="SKU / Barcode").pack(pady=(20, 5))
        self.sku_entry = ctk.CTkEntry(self.entry_frame)
        self.sku_entry.pack(pady=5)
        
        # Quantity
        ctk.CTkLabel(self.entry_frame, text="Quantity").pack(pady=(20, 5))
        self.quantity_entry = ctk.CTkEntry(self.entry_frame, width=100)
        self.quantity_entry.insert(0, "1")
        self.quantity_entry.pack(pady=5)
        
        # Add to cart button
        self.add_button = ctk.CTkButton(
            self.entry_frame,
            text="Add to Cart",
            command=self._add_to_cart,
        )
        self.add_button.pack(pady=20)
        
        # Item info display
        self.item_info_frame = ctk.CTkFrame(self.entry_frame)
        self.item_info_frame.pack(fill="x", padx=10, pady=20)
        
        self.item_name_label = ctk.CTkLabel(self.item_info_frame, text="")
        self.item_name_label.pack(pady=5)
        
        self.item_price_label = ctk.CTkLabel(self.item_info_frame, text="")
        self.item_price_label.pack(pady=5)
        
        self.item_stock_label = ctk.CTkLabel(self.item_info_frame, text="")
        self.item_stock_label.pack(pady=5)
    
    def _add_to_cart(self):
        """Handle add to cart button."""
        # Placeholder - would be implemented with actual cart logic
        pass
    
    def _process_checkout(self):
        """Handle checkout button."""
        # Placeholder - would be implemented with actual checkout logic
        pass
    
    def _clear_cart(self):
        """Clear the cart."""
        # Placeholder - would clear cart items
        pass
    
    def add_cart_item(self, item_data: Dict):
        """Add an item to the cart display."""
        # Placeholder
        pass
    
    def update_totals(self, subtotal: float, tax: float, total: float):
        """Update the totals display."""
        self.subtotal_label.configure(text=f"Subtotal: ${subtotal:.2f}")
        self.tax_label.configure(text=f"Tax (GCT): ${tax:.2f}")
        self.total_label.configure(text=f"Total: ${total:.2f}")