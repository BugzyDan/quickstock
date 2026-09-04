"""
Main application window for QuickStock JA.
"""

import csv
import tkinter as tk
from tkinter import messagebox, ttk, simpledialog, filedialog
import customtkinter as ctk
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import requests
from typing import Optional
import threading

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Handle relative imports when run directly (for development/testing)
try:
    from .styles import AppStyles, get_styles
    from ..services.inventory_service import InventoryService
    from ..services.cart_service import CartService
    from ..services.sync_service import SyncService
    from ..core.inventory import InventorySystem
    from ..core.pricing import CARIBBEAN_TAX_MAP
    from ..storage import load_data, save_data
    from ..infrastructure.logger import setup_logger
    from ..infrastructure.network import DEFAULT_API_BASE_URL, build_api_url
except ImportError:
    # Fallback to absolute imports when run directly
    from src.ui.styles import AppStyles, get_styles
    from src.services.inventory_service import InventoryService
    from src.services.cart_service import CartService
    from src.services.sync_service import SyncService
    from src.core.inventory import InventorySystem
    from src.core.pricing import CARIBBEAN_TAX_MAP
    from src.storage import load_data, save_data
    from src.infrastructure.logger import setup_logger
    from src.infrastructure.network import DEFAULT_API_BASE_URL, build_api_url

logger = setup_logger("quickstock.ui.app")


class InventoryGUI:
    """
    Main GUI application for QuickStock JA Inventory Management.
    
    This class manages the main application window and coordinates
    between different UI components and services.
    """
    
    def __init__(self, root: tk.Tk):
        """
        Initialize the main GUI with Offline-First data loading.
        """
        self.root = root
        self.styles = get_styles()
        
        # 1. Initialize Control Events first
        self.stop_sync_event = threading.Event()
        
        # 2. Initialize core services
        self.inventory_service = InventoryService()
        self.sync_service = SyncService()
        
        # 3. LOAD LOCAL DATA IMMEDIATELY
        # This populates self.inventory_service.system.inventory from local disk
        # before any network or login attempts occur.
        self._load_saved_data()
        
        # 4. Initialize Cart (Needs settings from loaded data)
        self.cart_service = CartService(self.inventory_service.get_settings())
        
        # 5. Set Application State
        self.current_user = None
        self.current_role = None
        self.current_user_id = None
        self.is_admin = False
        self.api_token = None
        self.api_base_url = os.getenv("API_BASE_URL", DEFAULT_API_BASE_URL)
        self.active_register_id = None
        self.active_register_location_id = None
        self.active_register_opened_at = None
        self.active_register_is_open = False
        
        # These are now populated from the _load_saved_data call above
        settings = self.inventory_service.get_settings()
        self.business_name = settings.get("brand_name", "Your Business")
        self.business_trn = settings.get("business_trn", "")
        
        self.active_location_id = None
        self.active_location_name = "All Locations"
        self._is_offline = False
        
        # 6. Setup UI
        # By setting up the window and menu now, the UI can render 
        # local data in the background while the login window is open.
        self._setup_window()
        self._setup_status_bar()
        self._setup_main_menu()
        
        # 7. Show login and background processes
        # We check the login, but the app "underneath" is already loaded with data.
        self.root.after(100, self._show_login)
        
        # Background sync won't actually hit the network until api_token is set
        self._start_background_sync()
    
    def _load_saved_data(self):
        """Load saved inventory and settings data into the service layers."""
        try:
            # load_data() usually returns (dict, list, dict) from your storage.py
            inventory, receipts, settings = load_data()
            
            # 1. Update the Inventory System
            if inventory:
                self.inventory_service.system.inventory = inventory
                logger.info(f"Loaded {len(inventory)} items from local cache.")
                
            if receipts:
                self.inventory_service.system.receipts = receipts
                
            # 2. Update Settings & Business Identity
            if settings:
                # Merge loaded settings into the live system settings
                self.inventory_service.system.settings.update(settings)
                
                # Extract branding for the UI
                self.business_name = settings.get("brand_name", "Your Business")
                self.business_trn = settings.get("business_trn", "")
                
                # Apply the saved theme immediately
                if "theme" in settings:
                    self.styles.set_theme(settings["theme"])
            
            # 3. Synchronize service state
            # This ensures internal caches in the service layer are refreshed
            self.inventory_service.save_data() 
            
        except Exception as e:
            logger.error(f"Critical error during offline data load: {e}")
            # Fallback defaults if load fails
            self.business_name = "QuickStock Offline"
    
    def _setup_window(self):
        """Configure the main window."""
        self.root.title("QuickStock JA - Inventory Management")
        self.root.geometry("1100x720")
        self.root.minsize(800, 600)
        
        # Apply theme
        theme = self.inventory_service.get_settings().get("theme", "system")
        self.styles.set_theme(theme)
        
        # Configure main frame
        self.main_frame = ctk.CTkFrame(
            self.root,
            fg_color=self.styles.get_color("bg_secondary"),
            corner_radius=0,
        )
        self.main_frame.pack(fill="both", expand=True, padx=18, pady=18)
    
    def _setup_status_bar(self):
        """Create the status bar at the bottom."""
        self.status_bar = tk.Frame(self.root, bg=self.styles.get_color("bg_primary"), bd=1, relief="sunken")
        self.status_bar.pack(side="bottom", fill="x")
        
        self.status_label = tk.Label(
            self.status_bar,
            text="Waiting for Login...",
            font=self.styles.get_font("status"),
            bg=self.styles.get_color("bg_primary"),
            fg=self.styles.get_color("fg_primary"),
            pady=5
        )
        self.status_label.pack(side="left", padx=10)
    
    def _setup_main_menu(self):
        """Create the main menu buttons."""
        # Clear existing widgets
        for widget in self.main_frame.winfo_children():
            widget.destroy()
        self.main_frame.configure(fg_color=self.styles.get_color("bg_secondary"))
        
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_columnconfigure(1, weight=1)
        self.main_frame.grid_columnconfigure(2, weight=1)
        self.main_frame.grid_rowconfigure(2, weight=1)

        self._create_dashboard_header()
        self._create_dashboard_summary()

        role = self.current_role or "cashier"
        allowed = self._get_role_permissions(role)
        is_adminish = role in {"admin", "manager", "superuser"}

        self._create_action_group(
            "Operations",
            "Daily register work",
            [
                ("Open Shift", self._open_shift_from_desktop, "cashier", "success"),
                ("Cash Register", self._open_cash_register, "cashier", "primary"),
                ("Select Location", self._open_location_selector, "admin", "secondary"),
            ],
            allowed,
            role,
            2,
            0,
        )
        self._create_action_group(
            "Inventory",
            "Stock checks and movement",
            [
                ("View Inventory", self._open_view_inventory, "view_inventory", "primary"),
                ("Export CSV", self._export_inventory_csv, "export", "secondary"),
                ("Receive Stock", self._open_receive_stock, "receive_stock", "secondary"),
                ("Transfer Stock", self._open_transfer_stock, "transfer_stock", "secondary"),
                ("Add Item", self._open_add_item, "add_item", "secondary"),
            ],
            allowed,
            role,
            2,
            1,
        )
        self._create_action_group(
            "Reports & Admin" if is_adminish else "Session",
            "Review, sync, and user access",
            [
                ("Sales Summary", self._open_sales_summary, "sales_summary", "secondary"),
                ("Receipt History", self._open_receipt_history, "receipt_history", "secondary"),
                ("Sync Pending", self._sync_pending, "sync", "primary"),
                ("Settings", self._open_settings_menu, "settings", "secondary"),
                ("Switch User", self._logout, "cashier", "secondary"),
                ("Exit", self._exit_app, "cashier", "danger"),
            ],
            allowed,
            role,
            2,
            2,
        )
    
    def _load_logo(self):
        """Load and display the QuickStock JA logo."""
        try:
            from PIL import Image
            import os
            
            # Look for logo in common locations
            logo_paths = [
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "QuickStock_Logo.jpg"),
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "QuickStock_Logo.png"),
                "QuickStock_Logo.jpg",
                "QuickStock_Logo.png",
            ]
            
            logo_path = None
            for path in logo_paths:
                if os.path.exists(path):
                    logo_path = path
                    break
            
            if logo_path:
                # Load image
                img = Image.open(logo_path)
                img = img.resize((200, 200), Image.Resampling.LANCZOS)
                
                # Convert to CTkImage
                ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(200, 200))
                
                # Create label at row 0
                logo_label = ctk.CTkLabel(
                    self.main_frame,
                    image=ctk_img,
                    text="",
                    fg_color="transparent"
                )
                logo_label.grid(row=0, column=0, columnspan=2, pady=(10, 20))
                logo_label.image = ctk_img  # Keep reference to prevent garbage collection
                return  # Success, exit function
                
        except Exception as e:
            logger.warning(f"Could not load logo: {e}")
        
        # Fallback: Create a text label as fallback at row 0
        fallback_label = ctk.CTkLabel(
            self.main_frame,
            text="QuickStock JA",
            font=("Arial", 28, "bold"),
            fg_color="transparent"
        )
        fallback_label.grid(row=0, column=0, columnspan=2, pady=(10, 20))
    
    def _status_tone_color(self, tone: str) -> str:
        colors = {
            "success": self.styles.get_color("fg_success"),
            "warning": self.styles.get_color("fg_warning"),
            "danger": self.styles.get_color("fg_danger"),
            "info": self.styles.get_color("fg_accent"),
            "neutral": self.styles.get_color("bg_tertiary"),
        }
        return colors.get(tone, colors["neutral"])

    def _create_status_chip(self, parent, text: str, tone: str = "neutral"):
        chip = ctk.CTkLabel(
            parent,
            text=f"  {text}  ",
            font=self.styles.get_font("status"),
            fg_color=self._status_tone_color(tone),
            text_color="#FFFFFF" if tone != "neutral" else self.styles.get_color("text_primary"),
            corner_radius=14,
            height=28,
        )
        return chip

    def _connection_status(self):
        if self.api_token:
            return "Online", "success"
        if self._is_offline:
            return "Offline cache", "warning"
        return "Sign-in pending", "neutral"

    def _register_status(self):
        if self.active_register_is_open:
            return "Register open", "success"
        return "Register closed", "warning"

    def _refresh_dashboard(self):
        """Rebuild the dashboard so session, register, and sync state stay current."""
        if hasattr(self, "main_frame") and self.main_frame.winfo_exists():
            self._setup_main_menu()

    def _run_background_task(self, working_message: str, task, on_complete):
        """Run a slow UI-triggered task without blocking the Tk event loop."""
        self.update_status(working_message, "info")

        def worker():
            result = None
            error = None
            try:
                result = task()
            except Exception as exc:
                error = exc
            self.root.after(0, lambda: on_complete(result, error))

        threading.Thread(target=worker, daemon=True).start()

    def _create_dashboard_header(self):
        header = ctk.CTkFrame(
            self.main_frame,
            fg_color=self.styles.get_color("bg_primary"),
            corner_radius=8,
        )
        header.grid(row=0, column=0, columnspan=3, sticky="ew", padx=4, pady=(4, 12))
        header.grid_columnconfigure(0, weight=1)

        business = self.business_name or "QuickStock JA"
        role = self.current_role or "cashier"
        username = getattr(self, "current_username", None)
        user_copy = f"{username} ({role})" if username else "Awaiting sign-in"

        ctk.CTkLabel(
            header,
            text=business,
            font=self.styles.get_font("heading_large"),
            text_color=self.styles.get_color("text_primary"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 2))
        ctk.CTkLabel(
            header,
            text=f"{user_copy} - {self.active_location_name or 'All Locations'}",
            font=self.styles.get_font("body"),
            text_color=self.styles.get_color("text_secondary"),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 14))

        chip_frame = ctk.CTkFrame(header, fg_color="transparent")
        chip_frame.grid(row=0, column=1, rowspan=2, sticky="e", padx=18, pady=12)
        connection_text, connection_tone = self._connection_status()
        self._create_status_chip(chip_frame, connection_text, connection_tone).pack(side="left", padx=(0, 8))
        self._create_status_chip(chip_frame, self._register_status()[0], self._register_status()[1]).pack(side="left")

    def _create_dashboard_summary(self):
        summary_frame = ctk.CTkFrame(
            self.main_frame,
            fg_color="transparent",
        )
        summary_frame.grid(row=1, column=0, columnspan=3, sticky="ew", padx=4, pady=(0, 12))
        for index in range(3):
            summary_frame.grid_columnconfigure(index, weight=1)

        try:
            inventory_summary = self.inventory_service.get_inventory_summary()
            item_count = len(self.inventory_service.get_all_items())
        except Exception:
            inventory_summary = {}
            item_count = 0

        total_items = inventory_summary.get("total_items", item_count)
        total_value = self._format_money(inventory_summary.get("retail_value", inventory_summary.get("total_value", 0)))
        try:
            sync_summary = self.sync_service.get_sync_summary()
        except Exception:
            sync_summary = {}
        pending = sync_summary.get("pending_actions", 0)
        failed = sync_summary.get("failed_actions", 0)

        cards = [
            ("Inventory", f"{total_items} items", f"Estimated stock value ${total_value}", "info"),
            ("Sync Queue", f"{pending} pending", f"{failed} failed actions need review", "warning" if failed else "success"),
            ("Register", self._register_status()[0], "Open a shift before cashier checkout", self._register_status()[1]),
        ]
        for index, (label, value, detail, tone) in enumerate(cards):
            self._create_summary_card(summary_frame, label, value, detail, tone).grid(
                row=0,
                column=index,
                sticky="ew",
                padx=(0 if index == 0 else 8, 0 if index == 2 else 8),
            )

    def _create_summary_card(self, parent, label: str, value: str, detail: str, tone: str):
        card = ctk.CTkFrame(parent, fg_color=self.styles.get_color("bg_tertiary"), corner_radius=8)
        card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            card,
            text=label.upper(),
            font=self.styles.get_font("body_small"),
            text_color=self.styles.get_color("text_secondary"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 2))
        ctk.CTkLabel(
            card,
            text=value,
            font=self.styles.get_font("heading_small"),
            text_color=self._status_tone_color(tone),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 2))
        ctk.CTkLabel(
            card,
            text=detail,
            font=self.styles.get_font("body_small"),
            text_color=self.styles.get_color("text_muted"),
            anchor="w",
        ).grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 12))
        return card

    def _create_action_group(self, title: str, subtitle: str, actions, allowed: set, role: str, row: int, col: int):
        group = ctk.CTkFrame(
            self.main_frame,
            fg_color=self.styles.get_color("bg_primary"),
            corner_radius=8,
        )
        group.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
        group.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            group,
            text=title,
            font=self.styles.get_font("heading_medium"),
            text_color=self.styles.get_color("text_primary"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 2))
        ctk.CTkLabel(
            group,
            text=subtitle,
            font=self.styles.get_font("body_small"),
            text_color=self.styles.get_color("text_secondary"),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))

        button_row = 2
        for text, command, permission, style in actions:
            if permission in allowed or role in {"admin", "superuser"}:
                self._create_action_button(group, text, command, style).grid(
                    row=button_row,
                    column=0,
                    sticky="ew",
                    padx=16,
                    pady=5,
                )
                button_row += 1

    def _create_action_button(self, parent, text: str, command, style: str = "secondary"):
        palette = {
            "primary": self.styles.get_color("fg_accent"),
            "success": self.styles.get_color("fg_success"),
            "danger": self.styles.get_color("fg_danger"),
            "secondary": self.styles.get_color("bg_tertiary"),
        }
        text_color = "#FFFFFF" if style in {"primary", "success", "danger"} else self.styles.get_color("text_primary")
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            height=46,
            corner_radius=8,
            fg_color=palette.get(style, palette["secondary"]),
            hover_color=self.styles.get_color("hover"),
            text_color=text_color,
            font=self.styles.get_font("button"),
            anchor="w",
        )
    
    def _show_login(self):
        """Show the login window."""
        login_window = ctk.CTkToplevel(self.root)
        login_window.title("Login - QuickStock JA")
        login_window.geometry("400x450") # Added a bit of height for the button
        login_window.transient(self.root)
        login_window.grab_set()

        login_window.protocol("WM_DELETE_WINDOW", self._exit_app)
        login_window.attributes('-topmost', True)
        
        # Title
        title = ctk.CTkLabel(
            login_window,
            text="QuickStock JA",
            font=self.styles.get_font("heading_large"),
        )
        title.pack(pady=20)
        
        # Username
        ctk.CTkLabel(login_window, text="Username").pack(pady=(10, 5))
        username_entry = ctk.CTkEntry(login_window, width=250)
        username_entry.pack(pady=5)
        username_entry.focus_set() # Better UX: start typing immediately
        
        # Password
        ctk.CTkLabel(login_window, text="Password").pack(pady=(10, 5))
        password_entry = ctk.CTkEntry(login_window, width=250, show="*")
        password_entry.pack(pady=5)

        # Helper functions for the login process
        def finalize_ui(success, user_info, username):
            if success:
                # Online Path
                self.api_token = user_info.get("token")
                self.current_role = user_info.get("role") or "cashier"
                self._is_offline = False
                self.status_label.configure(
                    text=f"ONLINE - {username} ({self.current_role})", 
                    fg=self.styles.get_color("fg_success")
                )
            else:
                # Offline Path: Check if the local cache actually has items
                if self.inventory_service.system.inventory and len(self.inventory_service.system.inventory) > 0:
                    messagebox.showinfo("Offline Mode", "Server unreachable. Accessing local database.")
                    self._is_offline = True
                    self.api_token = None
                    # Default to manager/cashier for offline if role isn't cached
                    self.current_role = self.current_role or "manager" 
                    
                    self.status_label.configure(
                        text=f"OFFLINE - {username} (Local Access)", 
                        fg="orange"
                    )
                else:
                    # Total Failure: No Internet AND No Local Data
                    messagebox.showerror("Error", "No local inventory found. Please connect to the internet for the initial sync.")
                    login_btn.configure(state="normal", text="Login")
                    return # Stop here, keep login window open

            # Common wrap-up for both successful paths
            self.current_username = username
            login_window.destroy()
            self._update_menu_for_role()
            
            # Start sync (it will internally check for self.api_token)
            self._start_background_sync()

        def run_sync_thread(username, password):
            try:
                # Actual network call
                success, user_info = self.sync_service.login_and_initial_sync(username, password)
                self.root.after(0, lambda: finalize_ui(success, user_info, username))
            except Exception as e:
                logger.error(f"Login thread error: {e}")
                self.root.after(0, lambda: finalize_ui(False, {}, username))

        def do_login(event=None): # Added event=None so 'Enter' key works too
            import threading
            username = username_entry.get().strip()
            password = password_entry.get()
            
            if not username or not password:
                messagebox.showerror("Error", "Required fields missing")
                return

            # UI Feedback
            login_btn.configure(state="disabled", text="Authenticating...")
            self.status_label.configure(text="Connecting to QuickStock Server...", fg="orange")

            # Start thread
            threading.Thread(target=run_sync_thread, args=(username, password), daemon=True).start()

        # Login Button
        login_btn = ctk.CTkButton(
            login_window,
            text="Login",
            command=do_login,
            width=250,
            height=40
        )
        login_btn.pack(pady=30)

        # Bind Enter key for convenience
        login_window.bind('<Return>', do_login)

    def _update_menu_for_role(self):
        """Update menu options based on user role."""
        # For now, just update status - actual permission handling would be more sophisticated
        role = self.current_role or "cashier"
        self.status_label.configure(text=f"Logged in as {self.current_username} ({role})")
        self._refresh_dashboard()
    
    def _open_operations_menu(self):
        """Open the operations menu."""
        role = self.current_role or "cashier"
        allowed = self._get_role_permissions(role)
        
        menu = ctk.CTkToplevel(self.root)
        menu.title("Operations")
        menu.geometry("400x500")
        menu.transient(self.root)
        menu.grab_set()
        
        ctk.CTkLabel(
            menu,
            text="Operations",
            font=self.styles.get_font("heading_medium"),
        ).pack(pady=20)
        
        # Operations buttons based on permissions
        operations = [
            ("Open Register Shift", self._open_shift_from_desktop, "cashier"),
            ("Cash Register", self._open_cash_register, "cashier"),
            ("Add Item", self._open_add_item, "admin"),
            ("Receive Stock", self._open_receive_stock, "admin"),
            ("Transfer Stock", self._open_transfer_stock, "admin"),
            ("Select Location", self._open_location_selector, "admin"),
        ]
        
        for text, command, required_role in operations:
            if required_role in allowed or role in {"admin", "superuser"}:
                btn = ctk.CTkButton(
                    menu,
                    text=text,
                    command=lambda c=command, m=menu: [m.destroy(), c()],
                    height=45,
                )
                btn.pack(fill="x", padx=20, pady=8)
        
        ctk.CTkButton(
            menu,
            text="Close",
            command=menu.destroy,
            fg_color=self.styles.get_color("bg_tertiary"),
        ).pack(pady=20)
    
    def _open_reports_menu(self):
        """Open the reports menu."""
        role = self.current_role or "cashier"
        allowed = self._get_role_permissions(role)
        
        menu = ctk.CTkToplevel(self.root)
        menu.title("Reports")
        menu.geometry("400x400")
        menu.transient(self.root)
        menu.grab_set()
        
        ctk.CTkLabel(
            menu,
            text="Reports",
            font=self.styles.get_font("heading_medium"),
        ).pack(pady=20)
        
        reports = [
            ("View Inventory", self._open_view_inventory, "admin"),
            ("Sales Summary", self._open_sales_summary, "admin"),
            ("Receipt History", self._open_receipt_history, "admin"),
            ("Export Inventory (CSV)", self._export_inventory_csv, "admin"),
        ]
        
        for text, command, required_role in reports:
            if required_role in allowed or role in {"admin", "superuser"}:
                btn = ctk.CTkButton(
                    menu,
                    text=text,
                    command=lambda c=command, m=menu: [m.destroy(), c()],
                    height=45,
                )
                btn.pack(fill="x", padx=20, pady=8)
        
        ctk.CTkButton(
            menu,
            text="Close",
            command=menu.destroy,
            fg_color=self.styles.get_color("bg_tertiary"),
        ).pack(pady=20)
    
    def _open_settings_menu(self):
        """Open the settings menu."""
        role = self.current_role or "cashier"
        allowed = self._get_role_permissions(role)
        
        menu = ctk.CTkToplevel(self.root)
        menu.title("Settings")
        menu.geometry("400x500")
        menu.transient(self.root)
        menu.grab_set()
        
        ctk.CTkLabel(
            menu,
            text="Settings",
            font=self.styles.get_font("heading_medium"),
        ).pack(pady=20)
        
        settings = [
            ("System Preferences", self._open_system_preferences, "admin"),
            ("Business Info", self._open_business_info, "admin"),
            ("Select Location", self._open_location_selector, "admin"),
            ("Tax Settings", self._open_tax_settings, "admin"),
            ("Toggle Theme", self._toggle_theme, "cashier"),
            ("Sync Pending", self._sync_pending, "admin"),
        ]
        
        for text, command, required_role in settings:
            if required_role in allowed or role in {"admin", "superuser"}:
                btn = ctk.CTkButton(
                    menu,
                    text=text,
                    command=lambda c=command, m=menu: [m.destroy(), c()],
                    height=45,
                )
                btn.pack(fill="x", padx=20, pady=8)
        
        ctk.CTkButton(
            menu,
            text="Close",
            command=menu.destroy,
            fg_color=self.styles.get_color("bg_tertiary"),
        ).pack(pady=20)
    
    def _get_role_permissions(self, role: str) -> set:
        """Get permissions for a role."""
        permissions = {
            "superuser": {
                "superuser", "admin", "manager", "cashier",
                "add_item", "receive_stock", "transfer_stock",
                "view_inventory", "sales_summary", "receipt_history",
                "export", "settings", "sync",
            },
            "admin": {
                "admin", "manager", "cashier",
                "add_item", "receive_stock", "transfer_stock",
                "view_inventory", "sales_summary", "receipt_history",
                "export", "settings", "sync",
            },
            "manager": {
                "manager", "cashier",
                "add_item", "receive_stock",
                "view_inventory", "receipt_history",
                "settings",
            },
            "cashier": {
                "cashier",
                "cash_register", "view_inventory",
                "toggle_theme",
            },
        }
        return permissions.get(role, permissions.get("admin", permissions["cashier"]))
    
    def _item_value(self, item, *keys, default=""):
        for key in keys:
            value = item.get(key)
            if value not in (None, ""):
                return value
        return default

    def _format_money(self, value):
        try:
            return f"{Decimal(str(value or '0')).quantize(Decimal('0.01'))}"
        except (InvalidOperation, ValueError):
            return "0.00"

    def _quantity_number(self, value):
        try:
            return Decimal(str(value or "0"))
        except (InvalidOperation, ValueError):
            return Decimal("0")

    def _inventory_export_rows(self):
        rows = []
        for item in self.inventory_service.get_all_items():
            rows.append({
                "SKU": self._item_value(item, "SKU", "sku"),
                "Name": self._item_value(item, "Name", "name"),
                "Category": self._item_value(item, "Category", "category", default="General"),
                "Brand": self._item_value(item, "Brand", "brand", default=""),
                "Quantity": self._item_value(item, "Amount", "quantity", "amount", default=0),
                "Cost": self._format_money(self._item_value(item, "Cost", "cost", "cost_price", default=0)),
                "Price": self._format_money(self._item_value(item, "Price", "price", "selling_price", default=0)),
                "Location": self._item_value(item, "Location", "location", default=self.active_location_name),
            })
        return rows

    def _inventory_window_summary(self, rows):
        try:
            summary = self.inventory_service.get_inventory_summary()
        except Exception:
            summary = {}
        return {
            "total_items": summary.get("total_items", len(rows)),
            "total_value": self._format_money(summary.get("retail_value", summary.get("total_value", 0))),
            "low_stock_count": sum(1 for row in rows if self._quantity_number(row.get("Quantity")) <= 5),
        }

    def _receipt_records(self):
        system = getattr(self.inventory_service, "system", None)
        receipts = getattr(system, "receipts", None)
        if receipts is not None:
            return list(receipts)
        sales_manager = getattr(system, "sales_manager", None)
        receipts = getattr(sales_manager, "receipts", None)
        return list(receipts or [])

    def _receipt_timestamp(self, receipt):
        raw = receipt.get("Timestamp") or receipt.get("timestamp") or receipt.get("date") or ""
        if hasattr(raw, "strftime"):
            parsed = raw
        else:
            if not raw:
                return None
            try:
                parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _receipt_total(self, receipt):
        return self._format_money(
            receipt.get("Total Bill", receipt.get("total", receipt.get("Total", receipt.get("amount", 0))))
        )

    def _sales_summary_metrics(self, receipts=None):
        receipts = list(self._receipt_records() if receipts is None else receipts)
        today = datetime.now(timezone.utc).date()
        totals = {
            "receipt_count": len(receipts),
            "items_sold": 0,
            "total_sales_raw": Decimal("0"),
            "today_receipts": 0,
            "today_sales_raw": Decimal("0"),
            "latest_receipt": "",
        }

        latest_timestamp = None
        for receipt in receipts:
            receipt_total = self._quantity_number(self._receipt_total(receipt))
            totals["total_sales_raw"] += receipt_total
            timestamp = self._receipt_timestamp(receipt)
            if timestamp and timestamp.date() == today:
                totals["today_receipts"] += 1
                totals["today_sales_raw"] += receipt_total
            if timestamp and (latest_timestamp is None or timestamp > latest_timestamp):
                latest_timestamp = timestamp
                totals["latest_receipt"] = timestamp.strftime("%Y-%m-%d %H:%M")

            for item in receipt.get("Items Purchased", receipt.get("items", [])) or []:
                totals["items_sold"] += int(self._quantity_number(item.get("Quantity", item.get("quantity", 0))))

        return {
            "receipt_count": totals["receipt_count"],
            "items_sold": totals["items_sold"],
            "total_sales": self._format_money(totals["total_sales_raw"]),
            "today_receipts": totals["today_receipts"],
            "today_sales": self._format_money(totals["today_sales_raw"]),
            "latest_receipt": totals["latest_receipt"],
        }

    def _receipt_history_rows(self, receipts=None):
        receipts = list(self._receipt_records() if receipts is None else receipts)
        rows = []
        for receipt in receipts:
            timestamp = self._receipt_timestamp(receipt)
            items = receipt.get("Items Purchased", receipt.get("items", [])) or []
            rows.append({
                "Invoice": receipt.get("Invoice No", receipt.get("invoice_no", receipt.get("id", ""))),
                "Date": timestamp.strftime("%Y-%m-%d %H:%M") if timestamp else "",
                "Customer": receipt.get("Customer Name", receipt.get("customer", "")) or "",
                "Items": sum(int(self._quantity_number(item.get("Quantity", item.get("quantity", 0)))) for item in items),
                "Total": self._receipt_total(receipt),
            })
        rows.sort(key=lambda row: row["Date"], reverse=True)
        return rows

    def _sync_summary_message(self):
        summary = self.sync_service.get_sync_summary()
        last_sync = summary.get("last_sync")
        if last_sync and hasattr(last_sync, "strftime"):
            last_sync = last_sync.strftime("%Y-%m-%d %H:%M")
        status = str(summary.get("status", "unknown")).replace("_", " ").title()
        api_state = "Connected" if summary.get("api_configured") else "Not configured"
        return (
            f"Sync status: {status}\n"
            f"Pending changes: {summary.get('pending_actions', 0)}\n"
            f"Failed changes: {summary.get('failed_actions', 0)}\n"
            f"Server connection: {api_state}\n"
            f"Last completed sync: {last_sync or 'Never'}"
        )

    def _open_shift_from_desktop(self):
        if not self.api_token:
            messagebox.showwarning(
                "Open Register Shift",
                "You are using local/offline access. Sign in online before opening a register shift.",
            )
            return

        opening_cash = simpledialog.askstring(
            "Open Register Shift",
            "Enter the cash float in the drawer before sales begin.",
            initialvalue="0.00",
            parent=self.root,
        )
        if opening_cash is None:
            return

        try:
            Decimal(str(opening_cash)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            messagebox.showerror("Open Register Shift", "Enter a valid cash amount.")
            return

        def open_shift_request():
            response = requests.post(
                build_api_url(self.api_base_url, "/api/register/open/"),
                headers={"Authorization": f"Token {self.api_token}"},
                json={"opening_cash": str(opening_cash), "location_id": self.active_location_id},
                timeout=30,
            )
            try:
                payload = response.json() if response.content else {}
            except ValueError as exc:
                raise ValueError("The server returned an invalid response.") from exc
            return response.status_code, payload

        def finish_open_shift(result, error):
            if error:
                message = str(error)
                if isinstance(error, requests.RequestException):
                    message = f"Could not reach the server: {error}"
                messagebox.showerror("Open Register Shift", message)
                self.update_status("Register shift was not opened", "warning")
                return

            status_code, payload = result
            if status_code >= 400 or not payload.get("ok", payload.get("success", False)):
                messagebox.showerror(
                    "Open Register Shift",
                    payload.get("error") or payload.get("message") or "Shift could not be opened.",
                )
                self.update_status("Register shift was not opened", "warning")
                return

            register = payload.get("active_register") or payload.get("register") or payload.get("shift") or payload
            self.active_register_id = register.get("id") or register.get("shift_id")
            self.active_register_location_id = register.get("location_id")
            self.active_register_opened_at = register.get("opened_at")
            self.active_register_is_open = True
            self.update_status("Register shift open", "success")
            self._refresh_dashboard()
            messagebox.showinfo("Open Register Shift", "Register shift is open. Cashiers can now start checkout.")

        self._run_background_task("Opening register shift...", open_shift_request, finish_open_shift)

    def _open_cash_register(self):
        window = ctk.CTkToplevel(self.root)
        window.title("Cash Register")
        window.geometry("460x420")
        window.transient(self.root)
        window.grab_set()
        window.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            window,
            text="Cash Register",
            font=self.styles.get_font("heading_medium"),
            text_color=self.styles.get_color("text_primary"),
        ).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 4))
        ctk.CTkLabel(
            window,
            text="Process a quick single-item sale from local inventory.",
            font=self.styles.get_font("body_small"),
            text_color=self.styles.get_color("text_secondary"),
        ).grid(row=1, column=0, sticky="w", padx=18, pady=(0, 14))

        form = ctk.CTkFrame(window, fg_color=self.styles.get_color("bg_tertiary"), corner_radius=8)
        form.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 14))
        form.grid_columnconfigure(0, weight=1)

        sku_var = tk.StringVar()
        quantity_var = tk.StringVar(value="1")
        customer_var = tk.StringVar()

        fields = [
            ("SKU", sku_var, "Scan or enter SKU"),
            ("Quantity", quantity_var, "1"),
            ("Customer", customer_var, "Optional"),
        ]
        for index, (label, variable, placeholder) in enumerate(fields):
            ctk.CTkLabel(
                form,
                text=label,
                font=self.styles.get_font("body_small"),
                text_color=self.styles.get_color("text_secondary"),
            ).grid(row=index * 2, column=0, sticky="w", padx=14, pady=(12 if index == 0 else 8, 2))
            ctk.CTkEntry(form, textvariable=variable, placeholder_text=placeholder, height=38).grid(
                row=index * 2 + 1,
                column=0,
                sticky="ew",
                padx=14,
                pady=(0, 2),
            )

        preview = ctk.CTkLabel(
            window,
            text="Enter a SKU to preview the item.",
            font=self.styles.get_font("body"),
            text_color=self.styles.get_color("text_secondary"),
            anchor="w",
        )
        preview.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 14))

        def refresh_preview(*_args):
            sku = sku_var.get().strip()
            item = self.inventory_service.get_item_by_sku(sku) if sku else None
            if not item:
                preview.configure(text="Enter a SKU to preview the item.")
                return
            preview.configure(
                text=(
                    f"{item.get('Name', '')} - "
                    f"{item.get('Amount', 0)} in stock - "
                    f"${self._format_money(item.get('Price', 0))}"
                )
            )

        def checkout():
            sku = sku_var.get().strip()
            try:
                quantity = int(Decimal(str(quantity_var.get().strip() or "0")))
            except (InvalidOperation, ValueError):
                messagebox.showerror("Cash Register", "Enter a valid whole-number quantity.", parent=window)
                return
            success, message, receipt = self.inventory_service.sell_item(sku, quantity)
            if not success:
                messagebox.showerror("Cash Register", message, parent=window)
                return
            if receipt is not None and customer_var.get().strip():
                receipt["Customer Name"] = customer_var.get().strip()
                self.inventory_service.save_data()
            self.update_status("Sale completed", "success")
            self._refresh_dashboard()
            total = self._format_money((receipt or {}).get("Total Bill", 0))
            messagebox.showinfo("Cash Register", f"{message}\nReceipt total: ${total}", parent=window)
            window.destroy()

        sku_var.trace_add("write", refresh_preview)
        ctk.CTkButton(
            window,
            text="Complete Sale",
            command=checkout,
            height=42,
            fg_color=self.styles.get_color("fg_success"),
        ).grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 8))
        ctk.CTkButton(
            window,
            text="Cancel",
            command=window.destroy,
            height=38,
            fg_color=self.styles.get_color("bg_tertiary"),
        ).grid(row=5, column=0, sticky="ew", padx=18, pady=(0, 18))
    
    def _open_add_item(self):
        window = ctk.CTkToplevel(self.root)
        window.title("Add Item")
        window.geometry("460x520")
        window.transient(self.root)
        window.grab_set()
        window.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(window, text="Add Item", font=self.styles.get_font("heading_medium")).grid(
            row=0, column=0, sticky="w", padx=18, pady=(18, 12)
        )
        form = ctk.CTkFrame(window, fg_color=self.styles.get_color("bg_tertiary"), corner_radius=8)
        form.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 14))
        form.grid_columnconfigure(0, weight=1)

        variables = {
            "sku": tk.StringVar(),
            "name": tk.StringVar(),
            "category": tk.StringVar(value="General"),
            "cost": tk.StringVar(value="0.00"),
            "price": tk.StringVar(value="0.00"),
            "amount": tk.StringVar(value="0"),
        }
        labels = [
            ("SKU", "sku", "Optional; generated if blank"),
            ("Name", "name", "Item name"),
            ("Category", "category", "General"),
            ("Cost", "cost", "0.00"),
            ("Price", "price", "0.00"),
            ("Opening Stock", "amount", "0"),
        ]
        for index, (label, key, placeholder) in enumerate(labels):
            ctk.CTkLabel(form, text=label, font=self.styles.get_font("body_small")).grid(
                row=index * 2, column=0, sticky="w", padx=14, pady=(10 if index else 14, 2)
            )
            ctk.CTkEntry(form, textvariable=variables[key], placeholder_text=placeholder, height=36).grid(
                row=index * 2 + 1, column=0, sticky="ew", padx=14, pady=(0, 2)
            )

        def save_item():
            try:
                cost = float(Decimal(str(variables["cost"].get().strip() or "0")))
                price = float(Decimal(str(variables["price"].get().strip() or "0")))
                amount = int(Decimal(str(variables["amount"].get().strip() or "0")))
            except (InvalidOperation, ValueError):
                messagebox.showerror("Add Item", "Cost, price, and stock must be valid numbers.", parent=window)
                return
            success, message, item = self.inventory_service.add_item(
                category=variables["category"].get().strip() or "General",
                name=variables["name"].get().strip(),
                cost=cost,
                price=price,
                amount=amount,
                sku=variables["sku"].get().strip() or None,
            )
            if not success:
                messagebox.showerror("Add Item", message, parent=window)
                return
            self.update_status(message, "success")
            self._refresh_dashboard()
            messagebox.showinfo("Add Item", f"{item.get('Name')} saved as {item.get('SKU')}.", parent=window)
            window.destroy()

        ctk.CTkButton(window, text="Save Item", command=save_item, height=42, fg_color=self.styles.get_color("fg_success")).grid(
            row=2, column=0, sticky="ew", padx=18, pady=(0, 8)
        )
        ctk.CTkButton(window, text="Cancel", command=window.destroy, height=38, fg_color=self.styles.get_color("bg_tertiary")).grid(
            row=3, column=0, sticky="ew", padx=18, pady=(0, 18)
        )
    
    def _open_receive_stock(self):
        sku = simpledialog.askstring("Receive Stock", "Enter item SKU:", parent=self.root)
        if not sku:
            return
        quantity = simpledialog.askinteger("Receive Stock", "Quantity received:", parent=self.root, minvalue=1)
        if quantity is None:
            return
        cost = simpledialog.askstring("Receive Stock", "New unit cost (optional):", parent=self.root)
        cost_value = None
        if cost:
            try:
                cost_value = float(Decimal(str(cost)))
            except (InvalidOperation, ValueError):
                messagebox.showerror("Receive Stock", "Enter a valid cost.")
                return
        success, message = self.inventory_service.receive_stock(sku.strip(), quantity, cost_value)
        self.update_status(message, "success" if success else "warning")
        self._refresh_dashboard()
        (messagebox.showinfo if success else messagebox.showerror)("Receive Stock", message)
    
    def _open_transfer_stock(self):
        from_sku = simpledialog.askstring("Transfer Stock", "Source SKU:", parent=self.root)
        if not from_sku:
            return
        to_sku = simpledialog.askstring("Transfer Stock", "Destination SKU:", parent=self.root)
        if not to_sku:
            return
        quantity = simpledialog.askinteger("Transfer Stock", "Quantity to transfer:", parent=self.root, minvalue=1)
        if quantity is None:
            return
        success, message = self.inventory_service.transfer_stock(from_sku.strip(), to_sku.strip(), quantity)
        self.update_status(message, "success" if success else "warning")
        self._refresh_dashboard()
        (messagebox.showinfo if success else messagebox.showerror)("Transfer Stock", message)
    
    def _open_location_selector(self):
        location = simpledialog.askstring(
            "Select Location",
            "Enter the active location name for this terminal:",
            initialvalue=self.active_location_name or "All Locations",
            parent=self.root,
        )
        if location is None:
            return
        self.active_location_name = location.strip() or "All Locations"
        self.update_status(f"Active location: {self.active_location_name}", "success")
        self._refresh_dashboard()
    
    def _open_view_inventory(self):
        rows = self._inventory_export_rows()
        window = ctk.CTkToplevel(self.root)
        window.title("Inventory Viewer")
        window.geometry("1040x640")
        window.transient(self.root)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(3, weight=1)

        header = ctk.CTkFrame(window, fg_color=self.styles.get_color("bg_primary"), corner_radius=8)
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 10))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="Inventory Viewer",
            font=self.styles.get_font("heading_medium"),
            text_color=self.styles.get_color("text_primary"),
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 2))
        ctk.CTkLabel(
            header,
            text=f"{self.active_location_name or 'All Locations'} - search, review, refresh, or export current stock.",
            font=self.styles.get_font("body"),
            text_color=self.styles.get_color("text_secondary"),
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 14))

        summary_row = ctk.CTkFrame(window, fg_color="transparent")
        summary_row.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))
        for index in range(3):
            summary_row.grid_columnconfigure(index, weight=1)

        def draw_summary():
            for widget in summary_row.winfo_children():
                widget.destroy()
            summary = self._inventory_window_summary(rows)
            self._create_summary_card(summary_row, "Items", str(summary["total_items"]), "Products in local inventory", "info").grid(
                row=0, column=0, sticky="ew", padx=(0, 8)
            )
            self._create_summary_card(summary_row, "Stock Value", f"${summary['total_value']}", "Estimated retail value", "success").grid(
                row=0, column=1, sticky="ew", padx=8
            )
            self._create_summary_card(summary_row, "Watch List", str(summary["low_stock_count"]), "Items at 5 units or less", "warning").grid(
                row=0, column=2, sticky="ew", padx=(8, 0)
            )

        controls = ctk.CTkFrame(window, fg_color=self.styles.get_color("bg_tertiary"), corner_radius=8)
        controls.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 10))
        controls.grid_columnconfigure(0, weight=1)

        search_var = tk.StringVar()
        search_entry = ctk.CTkEntry(
            controls,
            textvariable=search_var,
            placeholder_text="Search SKU, name, category, brand, or location",
            height=38,
        )
        search_entry.grid(row=0, column=0, sticky="ew", padx=12, pady=12)

        count_label = ctk.CTkLabel(
            controls,
            text=f"Showing {len(rows)} of {len(rows)}",
            font=self.styles.get_font("body_small"),
            text_color=self.styles.get_color("text_secondary"),
        )
        count_label.grid(row=0, column=1, padx=(0, 8), pady=12)

        table_frame = tk.Frame(window, bg=self.styles.get_color("bg_secondary"))
        table_frame.grid(row=3, column=0, sticky="nsew", padx=14, pady=(0, 14))
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)

        columns = ("SKU", "Name", "Category", "Brand", "Quantity", "Cost", "Price", "Location")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=16)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)

        widths = {
            "SKU": 110,
            "Name": 240,
            "Category": 130,
            "Brand": 120,
            "Quantity": 90,
            "Cost": 90,
            "Price": 90,
            "Location": 150,
        }
        for column in columns:
            tree.heading(column, text=column)
            tree.column(
                column,
                width=widths[column],
                minwidth=70,
                anchor="e" if column in {"Quantity", "Cost", "Price"} else "w",
            )

        tree.tag_configure("low_stock", foreground=self.styles.get_color("fg_warning"))
        tree.tag_configure("out_stock", foreground=self.styles.get_color("fg_danger"))

        empty_label = ctk.CTkLabel(
            table_frame,
            text="No inventory items match this view.",
            font=self.styles.get_font("heading_medium"),
            text_color=self.styles.get_color("text_secondary"),
        )

        def row_matches(row, term):
            if not term:
                return True
            return term in " ".join(str(row.get(column, "")) for column in columns).lower()

        def draw_table(*_args):
            term = search_var.get().strip().lower()
            matches = [row for row in rows if row_matches(row, term)]
            for item_id in tree.get_children():
                tree.delete(item_id)

            for row in matches:
                quantity = self._quantity_number(row.get("Quantity"))
                tags = ()
                if quantity <= 0:
                    tags = ("out_stock",)
                elif quantity <= 5:
                    tags = ("low_stock",)
                tree.insert("", "end", values=[row[column] for column in columns], tags=tags)

            count_label.configure(text=f"Showing {len(matches)} of {len(rows)}")
            if matches:
                empty_label.grid_forget()
                tree.grid(row=0, column=0, sticky="nsew")
                scrollbar.grid(row=0, column=1, sticky="ns")
            else:
                tree.grid_forget()
                scrollbar.grid_forget()
                empty_label.grid(row=0, column=0, sticky="nsew")

        def refresh_rows():
            nonlocal rows
            rows = self._inventory_export_rows()
            draw_summary()
            draw_table()

        ctk.CTkButton(
            controls,
            text="Refresh",
            command=refresh_rows,
            height=38,
            width=100,
            fg_color=self.styles.get_color("bg_primary"),
            hover_color=self.styles.get_color("hover"),
        ).grid(row=0, column=2, padx=(0, 8), pady=12)
        ctk.CTkButton(
            controls,
            text="Export CSV",
            command=self._export_inventory_csv,
            height=38,
            width=120,
            fg_color=self.styles.get_color("fg_accent"),
            hover_color=self.styles.get_color("hover"),
        ).grid(row=0, column=3, padx=(0, 12), pady=12)

        search_var.trace_add("write", draw_table)
        draw_summary()
        draw_table()
    
    def _open_sales_summary(self):
        summary = self._sales_summary_metrics()
        window = ctk.CTkToplevel(self.root)
        window.title("Sales Summary")
        window.geometry("720x420")
        window.transient(self.root)
        window.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(window, text="Sales Summary", font=self.styles.get_font("heading_medium")).grid(
            row=0, column=0, sticky="w", padx=18, pady=(18, 12)
        )
        cards = ctk.CTkFrame(window, fg_color="transparent")
        cards.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 14))
        for index in range(3):
            cards.grid_columnconfigure(index, weight=1)
        self._create_summary_card(cards, "Receipts", str(summary["receipt_count"]), "Total completed sales", "info").grid(
            row=0, column=0, sticky="ew", padx=(0, 8)
        )
        self._create_summary_card(cards, "Items Sold", str(summary["items_sold"]), "Units across receipts", "success").grid(
            row=0, column=1, sticky="ew", padx=8
        )
        self._create_summary_card(cards, "Revenue", f"${summary['total_sales']}", "Receipt total including tax", "success").grid(
            row=0, column=2, sticky="ew", padx=(8, 0)
        )

        details = ctk.CTkFrame(window, fg_color=self.styles.get_color("bg_tertiary"), corner_radius=8)
        details.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 14))
        ctk.CTkLabel(
            details,
            text=f"Today: {summary['today_receipts']} receipts - ${summary['today_sales']}",
            font=self.styles.get_font("body"),
            anchor="w",
        ).pack(fill="x", padx=14, pady=(14, 4))
        ctk.CTkLabel(
            details,
            text=f"Latest receipt: {summary['latest_receipt'] or 'No receipts yet'}",
            font=self.styles.get_font("body_small"),
            text_color=self.styles.get_color("text_secondary"),
            anchor="w",
        ).pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkButton(window, text="Close", command=window.destroy, fg_color=self.styles.get_color("bg_tertiary")).grid(
            row=3, column=0, sticky="ew", padx=18, pady=(0, 18)
        )
    
    def _open_receipt_history(self):
        rows = self._receipt_history_rows()
        window = ctk.CTkToplevel(self.root)
        window.title("Receipt History")
        window.geometry("860x520")
        window.transient(self.root)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(window, text="Receipt History", font=self.styles.get_font("heading_medium")).grid(
            row=0, column=0, sticky="w", padx=18, pady=(18, 12)
        )
        frame = tk.Frame(window, bg=self.styles.get_color("bg_secondary"))
        frame.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 14))
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)
        columns = ("Invoice", "Date", "Customer", "Items", "Total")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        for column in columns:
            tree.heading(column, text=column)
            tree.column(column, width=130, anchor="e" if column in {"Items", "Total"} else "w")
        for row in rows:
            tree.insert("", "end", values=[row[column] for column in columns])
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        if not rows:
            ctk.CTkLabel(frame, text="No receipts yet.", font=self.styles.get_font("body")).grid(row=0, column=0)
        ctk.CTkButton(window, text="Close", command=window.destroy, fg_color=self.styles.get_color("bg_tertiary")).grid(
            row=2, column=0, sticky="ew", padx=18, pady=(0, 18)
        )
    
    def _export_inventory_csv(self):
        rows = self._inventory_export_rows()
        if not rows:
            messagebox.showinfo("Export", "No inventory items are available to export.")
            return

        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export Inventory",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="quickstock_inventory.csv",
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
        except OSError as exc:
            messagebox.showerror("Export", f"Could not export inventory: {exc}")
            return

        messagebox.showinfo("Export", f"Inventory exported to {path}")
    
    def _open_system_preferences(self):
        settings = self.inventory_service.get_settings()
        tax_inclusive = messagebox.askyesno(
            "System Preferences",
            "Should shelf prices be treated as tax-inclusive?",
            parent=self.root,
        )
        self.inventory_service.update_settings({"tax_inclusive": tax_inclusive, "theme": settings.get("theme", self.styles.theme_mode)})
        self.cart_service.settings = self.inventory_service.get_settings()
        self.update_status("System preferences saved", "success")
    
    def _open_business_info(self):
        settings = self.inventory_service.get_settings()
        name = simpledialog.askstring(
            "Business Info",
            "Business / receipt name:",
            initialvalue=settings.get("brand_name", self.business_name),
            parent=self.root,
        )
        if name is None:
            return
        trn = simpledialog.askstring(
            "Business Info",
            "Business TRN:",
            initialvalue=settings.get("business_trn", self.business_trn),
            parent=self.root,
        )
        if trn is None:
            return
        updates = {"brand_name": name.strip() or "Your Business", "business_trn": trn.strip()}
        self.inventory_service.update_settings(updates)
        self.business_name = updates["brand_name"]
        self.business_trn = updates["business_trn"]
        self._refresh_dashboard()
        self.update_status("Business info saved", "success")
    
    def _open_tax_settings(self):
        countries = ", ".join(sorted(CARIBBEAN_TAX_MAP.keys()))
        country = simpledialog.askstring(
            "Tax Settings",
            f"Country / tax profile:\n{countries}",
            initialvalue=self.inventory_service.get_settings().get("country", "Jamaica"),
            parent=self.root,
        )
        if country is None:
            return
        self.inventory_service.set_tax_from_country(country.strip() or "International")
        self.cart_service.settings = self.inventory_service.get_settings()
        tax = self.inventory_service.get_settings()
        self.update_status(f"Tax profile: {tax.get('tax_label')} {float(tax.get('tax_rate', 0)) * 100:.2f}%", "success")
    
    def _toggle_theme(self):
        current = self.styles.theme_mode
        new_theme = "light" if current == "dark" else "dark"
        self.styles.set_theme(new_theme)
        self.inventory_service.update_settings({"theme": new_theme})
        self._setup_main_menu()
        messagebox.showinfo("Theme", f"Theme changed to {new_theme}")
    
    def _sync_pending(self):
        summary = self._sync_summary_message()
        if not self.api_token:
            messagebox.showinfo("Sync", summary)
            return

        if not messagebox.askyesno("Sync", f"{summary}\n\nRun sync now?"):
            return

        def sync_request():
            return self.sync_service.force_sync(
                self.inventory_service.get_all_items(),
                client_id=self.current_username or "desktop-client",
            )

        def finish_sync(result, error):
            if error:
                self.update_status("Sync failed", "warning")
                self._refresh_dashboard()
                messagebox.showerror("Sync", f"Sync could not complete: {error}")
                return

            success, message, _results = result
            self.update_status(message, "success" if success else "warning")
            self._refresh_dashboard()
            messagebox.showinfo("Sync", message)

        self._run_background_task("Syncing pending changes...", sync_request, finish_sync)
    
    def _logout(self):
        """Logout and show login screen."""
        self.current_username = None
        self.current_role = None
        self.is_admin = False
        self.api_token = None
        self._is_offline = False
        self.active_register_id = None
        self.active_register_location_id = None
        self.active_register_opened_at = None
        self.active_register_is_open = False
        self.status_label.configure(text="Waiting for Login...", fg=self.styles.get_color("fg_primary"))
        self._refresh_dashboard()
        self._show_login()
    
    def _exit_app(self):
        """Exit the application."""
        if messagebox.askyesno("Exit", "Are you sure you want to exit?"):
            # Save data before exiting
            self.inventory_service.save_data()
            self.root.quit()
            self.root.destroy()
    
    def _start_background_sync(self):
        """Start background sync worker to keep local data fresh."""
        import threading
        import time

        def sync_loop():
            # Check the stop event instead of just the token
            while not self.stop_sync_event.is_set() and self.api_token:
                try:
                    self.root.after(0, lambda: self.update_status("Syncing...", "info"))
                    
                    # Perform the actual data pull/push
                    self.sync_service.perform_sync(self.api_token)
                    
                    self.root.after(0, lambda: [self.update_status("Data Synchronized", "success"), self._refresh_dashboard()])
                except Exception as e:
                    logger.error(f"Background sync failed: {e}")
                    self.root.after(0, lambda: [self.update_status("Sync Error - Offline Mode", "warning"), self._refresh_dashboard()])
                
                # Wait for 300 seconds OR until the stop event is set
                # This makes logout/exit instantaneous
                stopped = self.stop_sync_event.wait(timeout=300)
                if stopped:
                    break

        sync_thread = threading.Thread(target=sync_loop, daemon=True)
        sync_thread.start()

    
    def update_status(self, message: str, status_type: str = "info"):
        """
        Update the status bar message.
        
        Args:
            message: Status message
            status_type: Type of status (info, success, warning, error)
        """
        colors = self.styles.get_status_colors()
        colors.update({
            "info": self.styles.get_color("fg_accent"),
            "success": self.styles.get_color("fg_success"),
            "error": self.styles.get_color("fg_danger"),
        })
        color = colors.get(status_type, colors["online"])
        self.status_label.configure(text=message, fg=color)
