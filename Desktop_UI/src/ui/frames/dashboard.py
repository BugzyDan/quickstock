"""
Dashboard frame for QuickStock JA.
"""

import customtkinter as ctk
import tkinter as tk
from typing import Dict, Optional


class DashboardFrame(ctk.CTkFrame):
    """Dashboard frame component for displaying summary statistics."""
    
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._setup_ui()
    
    def _setup_ui(self):
        """Setup the dashboard UI."""
        # Title
        self.title_label = ctk.CTkLabel(
            self,
            text="Dashboard",
            font=("Arial", 24, "bold"),
        )
        self.title_label.pack(pady=20)
        
        # Stats frame
        self.stats_frame = ctk.CTkFrame(self)
        self.stats_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Summary labels (will be updated dynamically)
        self.total_items_label = ctk.CTkLabel(self.stats_frame, text="Total Items: 0")
        self.total_items_label.pack(pady=10)
        
        self.total_value_label = ctk.CTkLabel(self.stats_frame, text="Total Value: $0.00")
        self.total_value_label.pack(pady=10)
        
        self.low_stock_label = ctk.CTkLabel(self.stats_frame, text="Low Stock Items: 0")
        self.low_stock_label.pack(pady=10)
        
        self.out_of_stock_label = ctk.CTkLabel(self.stats_frame, text="Out of Stock: 0")
        self.out_of_stock_label.pack(pady=10)
    
    def update_stats(self, stats: Dict):
        """Update dashboard statistics."""
        self.total_items_label.configure(text=f"Total Items: {stats.get('total_items', 0)}")
        self.total_value_label.configure(text=f"Total Value: ${stats.get('retail_value', 0):.2f}")
        self.low_stock_label.configure(text=f"Low Stock Items: {stats.get('low_stock', 0)}")
        self.out_of_stock_label.configure(text=f"Out of Stock: {stats.get('out_of_stock', 0)}")