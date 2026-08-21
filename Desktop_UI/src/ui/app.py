"""
Main application window for QuickStock JA.
"""

import tkinter as tk
from tkinter import messagebox, ttk
import customtkinter as ctk
import os
import sys
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
    from ..infrastructure.network import DEFAULT_API_BASE_URL
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
    from src.infrastructure.network import DEFAULT_API_BASE_URL

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
        self.root.geometry("1000x650")
        self.root.minsize(800, 600)
        
        # Apply theme
        theme = self.inventory_service.get_settings().get("theme", "system")
        self.styles.set_theme(theme)
        
        # Configure main frame
        self.main_frame = tk.Frame(self.root, bg=self.styles.get_color("bg_secondary"))
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Configure grid
        self.main_frame.columnconfigure(0, weight=1)
        self.main_frame.columnconfigure(1, weight=1)
        for i in range(4):
            self.main_frame.rowconfigure(i, weight=1)
    
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
        
        # Configure grid rows to accommodate logo at top
        # Row 0 for logo, Rows 1-4 for buttons
        self.main_frame.rowconfigure(0, weight=0)  # Logo row
        for i in range(1, 5):
            self.main_frame.rowconfigure(i, weight=1)
        
        # Load and display logo at the top (row 0)
        self._load_logo()
        
        # Create menu buttons (rows 1-3)
        self._create_menu_button("Operations", self._open_operations_menu, 1, 0)
        self._create_menu_button("Reports", self._open_reports_menu, 1, 1)
        self._create_menu_button("Settings", self._open_settings_menu, 2, 0)
        self._create_menu_button("Switch User", self._logout, 2, 1)
        self._create_menu_button("Exit", self._exit_app, 3, 0)
    
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
    
    def _create_menu_button(self, text: str, command, row: int, col: int):
        """Create a styled menu button."""
        btn = ctk.CTkButton(
            self.main_frame,
            text=text,
            command=command,
            height=60,
            corner_radius=12,
            fg_color=self.styles.get_color("bg_tertiary"),
            hover_color=self.styles.get_color("hover"),
            font=self.styles.get_font("heading_small"),
        )
        btn.grid(row=row, column=col, padx=20, pady=20, sticky="nsew")
    
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
    
    # Placeholder methods for menu actions
    def _open_cash_register(self):
        messagebox.showinfo("Cash Register", "Cash Register feature coming soon!")
    
    def _open_add_item(self):
        messagebox.showinfo("Add Item", "Add Item feature coming soon!")
    
    def _open_receive_stock(self):
        messagebox.showinfo("Receive Stock", "Receive Stock feature coming soon!")
    
    def _open_transfer_stock(self):
        messagebox.showinfo("Transfer Stock", "Transfer Stock feature coming soon!")
    
    def _open_location_selector(self):
        messagebox.showinfo("Location", "Location Selector feature coming soon!")
    
    def _open_view_inventory(self):
        messagebox.showinfo("Inventory", "View Inventory feature coming soon!")
    
    def _open_sales_summary(self):
        messagebox.showinfo("Sales", "Sales Summary feature coming soon!")
    
    def _open_receipt_history(self):
        messagebox.showinfo("Receipts", "Receipt History feature coming soon!")
    
    def _export_inventory_csv(self):
        messagebox.showinfo("Export", "Export feature coming soon!")
    
    def _open_system_preferences(self):
        messagebox.showinfo("Preferences", "System Preferences feature coming soon!")
    
    def _open_business_info(self):
        messagebox.showinfo("Business Info", "Business Info feature coming soon!")
    
    def _open_tax_settings(self):
        messagebox.showinfo("Tax", "Tax Settings feature coming soon!")
    
    def _toggle_theme(self):
        current = self.styles.theme_mode
        new_theme = "light" if current == "dark" else "dark"
        self.styles.set_theme(new_theme)
        self.inventory_service.update_settings({"theme": new_theme})
        self._setup_main_menu()
        messagebox.showinfo("Theme", f"Theme changed to {new_theme}")
    
    def _sync_pending(self):
        messagebox.showinfo("Sync", "Sync feature coming soon!")
    
    def _logout(self):
        """Logout and show login screen."""
        self.current_username = None
        self.current_role = None
        self.is_admin = False
        self.status_label.configure(text="Waiting for Login...", fg=self.styles.get_color("fg_primary"))
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
                    
                    self.root.after(0, lambda: self.update_status("Data Synchronized", "success"))
                except Exception as e:
                    logger.error(f"Background sync failed: {e}")
                    self.root.after(0, lambda: self.update_status("Sync Error - Offline Mode", "warning"))
                
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
        color = colors.get(status_type, colors["online"])
        self.status_label.configure(text=message, fg=color)
