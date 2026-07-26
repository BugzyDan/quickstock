#!/usr/bin/env python3
"""
QuickStock JA - Main Entry Point
Inventory Management and Cash Registration System

Copyright 2026 QuickStock JA - All Rights Reserved
"""

import sys
import os

# Add the Desktop_UI directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
import customtkinter as ctk

# Import the main GUI class - use the full-featured implementation from Inventory_gui.py
# The src.ui.app version is incomplete and only shows "coming soon" messages
from Inventory_gui import InventoryGUI
from src.infrastructure.logger import setup_logger
from src.storage import save_data as save_local_data

# Setup logging
logger = setup_logger("quickstock.main")


def main():
    """Main entry point for the application."""
    try:
        # Initialize customtkinter
        ctk.set_appearance_mode("system")
        ctk.set_default_color_theme("blue")
        
        # Create main window
        root = ctk.CTk()
        
        # Set window icon if available
        try:
            icon_path = os.path.join(os.path.dirname(__file__), "QuickStock_Logo.ico")
            if os.path.exists(icon_path):
                root.iconbitmap(icon_path)
        except Exception:
            pass
        
        # Initialize the GUI
        app = InventoryGUI(root)
        
        # Handle window close
        def on_closing():
            try:
                # Save data before closing
                if hasattr(app, 'inventory_service'):
                    app.inventory_service.save_data()
                elif hasattr(app, 'system'):
                    save_local_data(
                        getattr(app.system, 'inventory', []),
                        getattr(app.system, 'receipts', []),
                        getattr(app.system, 'settings', {}),
                    )
                    if hasattr(app, 'save_inventory_cache'):
                        app.save_inventory_cache(getattr(app.system, 'inventory', []))
            except Exception as e:
                logger.error(f"Error saving data on close: {e}")
            finally:
                root.destroy()
                root.quit()
        
        root.protocol("WM_DELETE_WINDOW", on_closing)
        
        # Center window on screen
        root.update_idletasks()
        width = root.winfo_width()
        height = root.winfo_height()
        x = (root.winfo_screenwidth() // 2) - (width // 2)
        y = (root.winfo_screenheight() // 2) - (height // 2)
        root.geometry(f'{width}x{height}+{x}+{y}')
        
        # Start the application
        logger.info("Starting QuickStock JA application")
        root.mainloop()
        
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
