"""
Inventory management frame for QuickStock JA.
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional


class InventoryFrame(ctk.CTkFrame):
    """Inventory management frame component."""
    
    def __init__(self, master, on_item_selected: Optional[Callable] = None, **kwargs):
        super().__init__(master, **kwargs)
        self.on_item_selected = on_item_selected
        self._setup_ui()
    
    def _setup_ui(self):
        """Setup the inventory UI."""
        # Title
        self.title_label = ctk.CTkLabel(
            self,
            text="Inventory",
            font=("Arial", 24, "bold"),
        )
        self.title_label.pack(pady=10)
        
        # Search frame
        self.search_frame = ctk.CTkFrame(self)
        self.search_frame.pack(fill="x", padx=20, pady=10)
        
        self.search_entry = ctk.CTkEntry(self.search_frame, placeholder_text="Search items...")
        self.search_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        
        self.search_button = ctk.CTkButton(self.search_frame, text="Search", width=100)
        self.search_button.pack(side="left")
        
        # Inventory treeview
        self.tree_frame = ctk.CTkFrame(self)
        self.tree_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        columns = ("Number", "SKU", "Name", "Category", "Price", "Stock")
        self.tree = ttk.Treeview(self.tree_frame, columns=columns, show="headings")
        
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=100)
        
        self.tree.column("Name", width=200)
        self.tree.column("Category", width=150)
        
        # Scrollbar
        scrollbar = ttk.Scrollbar(self.tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Action buttons
        self.actions_frame = ctk.CTkFrame(self)
        self.actions_frame.pack(fill="x", padx=20, pady=10)
        
        self.add_button = ctk.CTkButton(self.actions_frame, text="Add Item", width=120)
        self.add_button.pack(side="left", padx=5)
        
        self.edit_button = ctk.CTkButton(self.actions_frame, text="Edit", width=100)
        self.edit_button.pack(side="left", padx=5)
        
        self.delete_button = ctk.CTkButton(
            self.actions_frame, 
            text="Delete", 
            width=100,
            fg_color="#E74C3C",
        )
        self.delete_button.pack(side="left", padx=5)
        
        self.refresh_button = ctk.CTkButton(self.actions_frame, text="Refresh", width=100)
        self.refresh_button.pack(side="right", padx=5)
    
    def load_items(self, items: List[Dict]):
        """Load items into the treeview."""
        # Clear existing items
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        # Add items
        for item in items:
            self.tree.insert("", "end", values=(
                item.get("Number", ""),
                item.get("SKU", ""),
                item.get("Name", ""),
                item.get("Category", ""),
                f"${item.get('Price', 0):.2f}",
                item.get("Amount", 0),
            ))
    
    def get_selected_item(self) -> Optional[str]:
        """Get the currently selected item's SKU."""
        selection = self.tree.selection()
        if selection:
            item = self.tree.item(selection[0])
            return item["values"][1]  # SKU
        return None