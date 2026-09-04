# Copyright 2026 QuickStock JA JA - All Rights Reserved
"""
QuickStock JA - Inventory Management GUI

This module now re-exports from the new modular architecture for backward compatibility.
For new development, import directly from the src package.

Backward Compatibility Note:
    This file maintains compatibility with existing code that imports from Inventory_gui.
    New code should import from src.ui.app, src.services, src.core, etc.

Example migration:
    Old: from Inventory_gui import InventoryGUI
    New: from src.ui.app import InventoryGUI
"""

import csv
import json
import os
import platform
import re
import shutil
import sys
import threading
import time
import random # Already imported at top
import uuid
import webbrowser
import tkinter as tk
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk, simpledialog

# Re-export from new architecture for backward compatibility
try:
    from src.ui.app import InventoryGUI
    from src.services.inventory_service import InventoryService
    from src.services.cart_service import CartService
    from src.services.sync_service import SyncService
    from src.core.inventory import InventorySystem
    from src.core.pricing import CARIBBEAN_TAX_MAP, calculate_tax, get_tax_info
    from src.core.sales import SalesManager, build_receipt
    from src.core.validation import validate_sku, validate_quantity, validate_price
    from src.storage import load_data, save_data, SecureCache, get_secure_cache
    from src.infrastructure.logger import setup_logger, log_security_event, log_database_event
    from src.infrastructure.network import (
        DEFAULT_API_BASE_URL,
        build_api_url,
        requires_https_in_production,
        validate_api_base_url,
    )
    NEW_ARCHITECTURE_AVAILABLE = True
except ImportError:
    NEW_ARCHITECTURE_AVAILABLE = False


import requests
try:
    import keyring
except ImportError:
    keyring = None

# Initialize logger for this module early to avoid NameError in startup checks
try:
    from src.infrastructure.logger import setup_logger
    logger = setup_logger("quickstock.gui")
except ImportError:
    import logging
    logger = logging.getLogger("quickstock.gui")

if keyring and platform.system() == "Linux":
    try:
        import secretstorage  # noqa
        import logging
        logging.getLogger("dbus.proxies").setLevel(logging.CRITICAL)
        keyring.get_password("QuickStockJA", "__probe__")
    except Exception:
        keyring = None
        logger.info("Keyring backend unavailable. Using file-based token storage.")
        
from dotenv import load_dotenv

from customtkinter import CTkImage, CTkLabel
from passlib.hash import django_pbkdf2_sha256 as handler

# Import storage module for data persistence
try:
    from src.storage import load_data, save_data as storage_save_data, set_data_file, get_data_file
    storage = type('storage', (), {
        'save_data': storage_save_data, 
        'load_data': load_data, 
        'set_data_file': set_data_file,
        'get_data_file': get_data_file
    })
except ImportError:
    try:
        from Desktop_UI.src.storage import load_data, save_data as storage_save_data, set_data_file, get_data_file
        storage = type('storage', (), {
            'save_data': storage_save_data, 
            'load_data': load_data, 
            'set_data_file': set_data_file,
            'get_data_file': get_data_file
        })
    except ImportError:
        # Create a minimal storage stub with proper method signatures
        class _StorageStub:
            _data_file = "inventory_data.json"
            
            def save_data(self, inventory, receipts, settings=None, filepath=None):
                return True, "OK"
            
            def load_data(self, filepath=None):
                return [], [], {}
            
            def set_data_file(self, path):
                self._data_file = path
            
            def get_data_file(self):
                return self._data_file
        
        storage = _StorageStub()

# Check OS to prevent crash on Linux
if platform.system() == "Windows":
    import winsound
else:
    winsound = None

import customtkinter as ctk
from PIL import Image

# Import from src package (new architecture)
try:
    from src.core.inventory import InventorySystem
    from src.storage import load_data, save_data
    from src.storage.cache import SecureCache
    from src.infrastructure.logger import setup_logger, log_security_event, log_database_event, log_sync_event, log_api_event, log_user_event
    from src.infrastructure.network import (
        DEFAULT_API_BASE_URL,
        build_api_url,
        requires_https_in_production,
        validate_api_base_url,
    )
except ImportError:
    # Fallback imports for backward compatibility
    # These are only used when running this file directly without the src package
    try:
        from Desktop_UI.src.core.inventory import InventorySystem
        from Desktop_UI.src.storage import load_data, save_data
        from Desktop_UI.src.storage.cache import SecureCache
        from Desktop_UI.src.infrastructure.logger import setup_logger, log_security_event, log_database_event, log_sync_event, log_api_event, log_user_event
    except ImportError:
        # Last resort: create minimal stubs
        class InventorySystem:
            pass
        def load_data():
            return [], [], {}
        def save_data(*args):
            pass
        class SecureCache:
            def __init__(self, *args): pass
            def load(self): return {}
            def save(self, *args): pass
        def setup_logger(*args):
            import logging
            return logging.getLogger(__name__)
        def log_security_event(*args, **kwargs): pass
        def log_database_event(*args, **kwargs): pass
        def log_sync_event(*args, **kwargs): pass
        def log_api_event(*args, **kwargs): pass
        def log_user_event(*args, **kwargs): pass
        DEFAULT_API_BASE_URL = "http://localhost:8000"
        def build_api_url(base_url, path):
            base = (base_url or DEFAULT_API_BASE_URL).strip().rstrip("/")
            return f"{base}{path if path.startswith('/') else '/' + path}"
        def requires_https_in_production(base_url, env=None):
            current_env = (env or os.getenv("QUICKSTOCK_ENV", "")).strip().lower()
            return current_env == "production" and not str(base_url).startswith("https://") and "localhost" not in str(base_url) and "127.0.0.1" not in str(base_url)
        def validate_api_base_url(base_url, env=None):
            return (True, "") if base_url else (False, "API Base URL is required.")

# Initialize logger for this module
logger = setup_logger("quickstock.gui")

def resource_path(relative_path):
    """Get absolute path to resource, works for dev and PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except AttributeError:
        # Use the directory where this script lives
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


# --------------------------
# Sync / Cache file constants
# --------------------------
def get_data_dir():
    """Returns a writeable directory for user data across OS platforms to avoid permission issues."""
    if platform.system() == "Windows":
        path = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "QuickStockJA")
    else:
        path = os.path.expanduser("~/.local/share/QuickStockJA")
    os.makedirs(path, exist_ok=True)
    return path

DATA_DIR = get_data_dir()
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SHARED_DATA_FILE = os.path.join(PROJECT_ROOT, "inventory_data.json")
PENDING_QUEUE_FILE = os.path.join(DATA_DIR, "pending_sync.json")
LOCAL_CACHE_FILE = os.path.join(DATA_DIR, "inventory_cache.json")
USER_CACHE_FILE = os.path.join(DATA_DIR, "user_cache.json")
USER_CACHE_TMP_FILE = os.path.join(DATA_DIR, "user_cache.tmp")
BACKEND_DATA_FILE = os.path.join(PROJECT_ROOT, "Website", "BackEnd", "quickstock", "inventory_data.json")
LOCATION_CACHE_FILE = os.path.join(DATA_DIR, "location_cache.json")
SUPPLIER_CACHE_FILE = os.path.join(DATA_DIR, "supplier_cache.json")
SQLITE_DB_PATH = os.path.join(PROJECT_ROOT, "Website", "BackEnd", "quickstock", "db.sqlite3")
BUSINESS_PROFILE_FILE = os.path.join(DATA_DIR, "business_profile.json")
CUSTOMER_REGISTRY_FILE = os.path.join(DATA_DIR, "customer_registry.json")
SUPERUSER_REGISTRY_CACHE_FILE = os.path.join(DATA_DIR, "superuser_registry_cache.json")
SUPERUSER_SYNC_STATUS_CACHE_FILE = os.path.join(DATA_DIR, "superuser_sync_status.json")
# Default API URL - uses localhost for development
# Production deployments should set API_BASE_URL in .env to their HTTPS endpoint
API_BASE_URL_DEFAULT = DEFAULT_API_BASE_URL
APP_VERSION = "1.0.0"
MAX_DB_QUANTITY = 2147483647
QUEUE_SCHEMA_VERSION = 1
QUEUE_MAX_ATTEMPTS = 8
QUEUE_IN_FLIGHT_TIMEOUT_SECONDS = 60
QUEUE_BACKOFF_BASE_SECONDS = 5
QUEUE_BACKOFF_MAX_SECONDS = 300
QUEUE_ACTIVE_STATES = {"pending", "in_flight", "retry", "paused_auth"}
QUEUE_TERMINAL_STATES = {"dead_letter"}
CHECKOUT_CONTRACT_VERSION = 1
CHECKOUT_MONEY_QUANTUM = Decimal("0.01")
CHECKOUT_RATE_QUANTUM = Decimal("0.0001")
CHECKOUT_PAYMENT_METHODS = {"cash", "jamdex", "card", "bank_transfer", "mobile_money", "other"}


CARIBBEAN_TAX_MAP = {
    "Jamaica": {"label": "GCT", "rate": 0.15},
    "Barbados": {"label": "VAT", "rate": 0.175},
    "Trinidad": {"label": "VAT", "rate": 0.125},
    "Guyana": {"label": "VAT", "rate": 0.14},
    "Saint Lucia": {"label": "VAT", "rate": 0.125},
    "International": {"label": "TAX", "rate": 0.00}
}


class InventoryGUI:
    def __init__(self, root):
        load_dotenv(resource_path(".env"), override=False)
        load_dotenv(override=False)
        self.system = InventorySystem()
        self.root = root
        self.current_role = None
        self.current_username = None
        self.current_user_id = None
        self.current_tenant_id = None
        self.current_operator_status = None
        self.default_location_id = None
        self.active_register_id = None
        self.active_register_location_id = None
        self.active_register_opened_at = None
        self.active_register_is_open = False
        self.is_admin = False
        self.cash_register_window = None
        self.cash_register_controller = None
        self.all_buttons = []
        self.business_name = "Your Business"
        self.business_trn = ""
        self.api_base_url = os.getenv("API_BASE_URL", API_BASE_URL_DEFAULT)
        self.api_token = None
        self._session_invalid = False
        self.last_server_error_time = 0
        self.server_cooldown = 30  # Back off for 30 seconds after a 500
        self.api_status = "UNKNOWN" # New: More granular API status
        self.locations = []
        self.suppliers = []
        self.categories = [] # New: Store categories
        self.customers = []
        self.active_location_id = None
        self.db_pool = None
        self.active_location_name = "All Locations"
        self.style = ttk.Style(self.root)
        self.last_sync_time = None # Track last successful sync for delta updates
        self.data_lock = threading.Lock() # Thread safety for inventory updates
        self._session_lock = threading.Lock()
        self._queue_lock = threading.RLock()
        self._queue_replay_lock = threading.Lock()
        self._active_queue_action_ids = set()
        self._queue_corruption_info = None
        self._is_offline = False
        self.sync_service = None  # Populated after login if SyncService is available
        self._sync_in_progress = False

        self._load_business_profile()

        # --- STATUS BAR ---
        self.status_bar = tk.Frame(self.root, bg="#1B2631", bd=1, relief="sunken")
        self.status_bar.pack(side="bottom", fill="x")

        self.status_label = tk.Label(
            self.status_bar,
            text="Waiting for Login...",
            font=("Arial", 10, "bold"),
            bg="#1B2631",
            fg="#58D68D",
            pady=5
        )
        self.status_label.pack(side="left", padx=10)
        # 🌐 New: Auto-detect region and tax on startup
        self.apply_regional_tax()

        # --- WINDOW BASICS ---
        self.root.title("QuickStock JA - Inventory Management")
        self.root.geometry("1080x700")
        self.root.minsize(900, 620)

        # --- HOME DASHBOARD ---
        self.home_frame = tk.Frame(self.root, bg="#101827")
        self.home_frame.pack(fill="both", expand=True)
        self.home_metric_labels = {}
        self.home_status_chips = {}
        self.home_identity_labels = {}
        self._build_home_dashboard()
        self._apply_theme_preference()
        self.root.after(100, lambda: self.login_window(self.root))
       
        
        # --- BACKGROUND SERVICES ---
        self._start_background_sync_worker()
        
 
    def _is_api_available(self):
        """Check if API is available for operations based on current status."""
        return self.api_status == "ONLINE"

    def refresh_status(self, connected=True, syncing=False):
        """Updates UI with visual cues for connectivity (Amber for Offline) and sync activity."""
        # Determine _is_offline based on the more granular api_status
        self._is_offline = self.api_status in ["SERVER_DOWN", "AUTH_FAILED", "TOKEN_EXPIRED", "SSL_ERROR", "INSECURE_CONNECTION", "PLAN_EXPIRED"]
        
        sync_tag = " 🔄 [Syncing...]" if syncing else ""
        
        if self._is_offline:
            self.status_bar.configure(bg="#7D6608") # Soft Amber
            status_text = "OFFLINE MODE - Local Cache Active"
            if self.api_status == "SERVER_DOWN":
                status_text = "OFFLINE MODE - Server Unreachable"
            elif self.api_status == "AUTH_FAILED":
                status_text = "OFFLINE MODE - Authentication Failed"
            elif self.api_status == "TOKEN_EXPIRED":
                status_text = "OFFLINE MODE - Session Expired"
            elif self.api_status == "PLAN_EXPIRED":
                status_text = "OFFLINE MODE - Subscription Expired"
            
            self.status_label.configure(bg="#7D6608", fg="#F4D03F", text=f"{status_text}{sync_tag}")
        else:
            pref = (self.system.settings.get("theme") or "system").strip().lower()
            bg = "#1B2631" if pref == "dark" else "#D5D8DC"
            fg = "#58D68D" if pref == "dark" else "#1B2631"
            self.status_bar.configure(bg=bg)
            self.status_label.configure(bg=bg, fg=fg)
            if syncing:
                current_text = self.status_label.cget("text")
                if "Syncing" not in current_text:
                    self.status_label.configure(text=current_text + sync_tag)

        controller = getattr(self, "cash_register_controller", None)
        if controller and hasattr(controller, "refresh_connectivity_banner"):
            try:
                controller.refresh_connectivity_banner(syncing=syncing)
            except Exception:
                pass
        self._update_home_dashboard_widgets()


    def _load_business_profile(self):
        profile = self._default_business_profile()
        profile_path = self._business_profile_path()
        load_paths = [profile_path]
        if profile_path != BUSINESS_PROFILE_FILE:
            load_paths.append(BUSINESS_PROFILE_FILE)
        for path in load_paths:
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    profile.update({k: v for k, v in loaded.items() if k in profile})
                break
            except Exception:
                continue
        self.business_name = str(profile.get("business_name") or "Your Business")
        self.business_trn = str(profile.get("trn") or "").strip()
        self.api_base_url = os.getenv("API_BASE_URL", str(profile.get("api_base_url") or API_BASE_URL_DEFAULT).strip())
     
        self.system.settings.update({
            "brand_name": self.business_name,
            "brand_email": str(profile.get("brand_email") or "").strip(),
            "brand_phone": str(profile.get("brand_phone") or "").strip(),
            "brand_address": str(profile.get("brand_address") or "").strip(),
            "brand_logo": str(profile.get("brand_logo") or "").strip(),
            "brand_logo_url": str(profile.get("brand_logo_url") or "").strip(),
            "country": str(profile.get("country") or "Jamaica"),
            "tax_rate": float(profile.get("tax_rate", self.system.settings.get("tax_rate", 0.15)) or 0),
            "tax_label": str(profile.get("tax_label") or self.system.settings.get("tax_label", "GCT")),
            "tax_inclusive": bool(profile.get("tax_inclusive", False)),
            "theme": str(profile.get("theme") or "system"),
            "pos_config": str(profile.get("pos_config") or "").strip(),
            "audit_retention_days": int(profile.get("audit_retention_days") or 90),
            "primary_funding_branch_id": self._coerce_int(profile.get("primary_funding_branch_id")),
            "primary_funding_branch_name": str(profile.get("primary_funding_branch_name") or "").strip(),
        })
        self.system.set_business_profile(self.business_name, self.business_trn)
        self._save_business_profile()

    def _default_business_profile(self):
        return {
            "business_name": "Your Business",
            "trn": "",
            "api_base_url": API_BASE_URL_DEFAULT,
            "api_token": "",
            "theme": "system",
            "pos_config": "",
            "brand_email": "",
            "brand_phone": "",
            "brand_address": "",
            "brand_logo": "",
            "brand_logo_url": "",
            "country": self.system.settings.get("country", "Jamaica"),
            "tax_label": self.system.settings.get("tax_label", "GCT"),
            "tax_inclusive": False,
            "tax_rate": self.system.settings.get("tax_rate", 0.15),
        }

    def _business_profile_path(self):
        if self.current_user_id:
            return os.path.join(DATA_DIR, f"business_profile_{self.current_user_id}.json")
        return BUSINESS_PROFILE_FILE

    def _save_business_profile(self):
        profile = {
            "business_name": self.business_name,
            "trn": self.business_trn,
            "api_base_url": self.api_base_url,
            "api_token": "", # Sensitive token moved to OS keyring
            "theme": self.system.settings.get("theme", "system"),
            "pos_config": self.system.settings.get("pos_config", ""),
            "brand_email": self.system.settings.get("brand_email", ""),
            "brand_phone": self.system.settings.get("brand_phone", ""),
            "brand_address": self.system.settings.get("brand_address", ""),
            "brand_logo": self.system.settings.get("brand_logo", ""),
            "brand_logo_url": self.system.settings.get("brand_logo_url", ""),
            "country": self.system.settings.get("country", "Jamaica"),
            "tax_label": self.system.settings.get("tax_label", "GCT"),
            "tax_inclusive": self.system.settings.get("tax_inclusive", False),
            "tax_rate": self.system.settings.get("tax_rate", 0.15),
            "audit_retention_days": self.system.settings.get("audit_retention_days", 90),
            "primary_funding_branch_id": self.system.settings.get("primary_funding_branch_id"),
            "primary_funding_branch_name": self.system.settings.get("primary_funding_branch_name", ""),
        }
        try:
            with open(self._business_profile_path(), "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=2)
        except Exception:
            pass

    def _persist_offline_settings(self):
        self.system.settings["brand_name"] = self.business_name
        self._save_business_profile()
        try:
            storage.save_data(self.system.inventory, self.system.receipts, self.system.settings)
        except Exception:
            pass

    def _home_button_palette(self, label):
        palettes = {
            "Operations": ("#3D7BFF", "#2E63D3", "#FFFFFF", "#AFCBFF"),
            "Reports": ("#1EB980", "#17A06E", "#FFFFFF", "#8FE6C0"),
            "Settings": ("#1F2D46", "#2A3C5A", "#F8FAFC", "#6F89B2"),
            "Switch User": ("#26364F", "#324765", "#F8FAFC", "#6F89B2"),
            "Online": ("#0F766E", "#0D9488", "#FFFFFF", "#5EEAD4"),
            "Users": ("#2E63D3", "#2457BC", "#FFFFFF", "#AFCBFF"),
            "Exit": ("#D64432", "#B93829", "#FFFFFF", "#F8A092"),
        }
        return palettes.get(label)

    def _format_money(self, value):
        try:
            amount = Decimal(str(value)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            amount = Decimal("0.00")
        return f"${amount:,.2f}"

    def _api_status_summary(self):
        status = str(self.api_status or "UNKNOWN").strip().upper()
        if status == "ONLINE":
            return "API Online", "Live sync ready", "good"
        if status in {"SERVER_DOWN", "SSL_ERROR", "SERVER_ERROR"}:
            return "Offline", "Using local cache", "warn"
        if status in {"AUTH_FAILED", "TOKEN_EXPIRED", "AUTH_FORBIDDEN"}:
            return "Login Attention", "Session needs refresh", "warn"
        if status == "PLAN_EXPIRED":
            return "Plan Attention", "Subscription required", "danger"
        if self.api_token:
            return "API Checking", "Session available", "neutral"
        return "Local Mode", "Sign in to sync", "neutral"

    def _register_status_summary(self):
        if self.active_register_is_open:
            location = self.active_location_name or "Selected location"
            return "Register Open", location, "good"
        return "Register Closed", "Open before checkout", "neutral"

    def _home_inventory_metrics(self):
        items = self._normalize_items(self.system.inventory or [])
        units = 0
        low_count = 0
        retail_value = Decimal("0.00")
        for item in items:
            qty = self._coerce_int(item.get("Amount") or item.get("amount") or 0) or 0
            units += qty
            if qty <= 5:
                low_count += 1
            try:
                retail_value += Decimal(str(item.get("Price") or item.get("price") or 0)) * Decimal(qty)
            except (InvalidOperation, TypeError, ValueError):
                continue
        return {
            "items": len(items),
            "units": units,
            "low": low_count,
            "retail_value": retail_value,
        }

    def _today_sales_total(self):
        today_total = Decimal("0.00")
        today_str = datetime.now().strftime("%Y-%m-%d")
        for receipt in self.system.receipts or []:
            if str(receipt.get("archived")) == "True":
                continue
            date_value = str(receipt.get("Date") or receipt.get("Timestamp") or "")
            if not date_value.startswith(today_str):
                continue
            try:
                today_total += Decimal(str(receipt.get("Total Bill") or 0))
            except (InvalidOperation, TypeError, ValueError):
                continue
        return today_total

    def _chip_colors(self, palette, tone):
        color_map = {
            "good": ("#E6F7EF", "#0E8E5D"),
            "warn": ("#FFF7ED", "#B45309"),
            "danger": ("#FEE2E2", "#B91C1C"),
            "neutral": (palette["badge_bg"], palette["badge_fg"]),
        }
        if (self.system.settings.get("theme") or "system").strip().lower() == "dark":
            color_map.update({
                "good": ("#143D36", "#8DEFCB"),
                "warn": ("#3D2E15", "#FACC6B"),
                "danger": ("#4A1D1D", "#FCA5A5"),
            })
        return color_map.get(tone, color_map["neutral"])

    def _dashboard_chip(self, parent, key, title, detail, tone="neutral"):
        palette = self._desktop_window_palette()
        bg, fg = self._chip_colors(palette, tone)
        chip = tk.Frame(parent, bg=bg, padx=12, pady=7)
        label = tk.Label(
            chip,
            text=title,
            bg=bg,
            fg=fg,
            font=("Segoe UI", 9, "bold"),
        )
        label.pack(anchor="w")
        detail_label = tk.Label(
            chip,
            text=detail,
            bg=bg,
            fg=fg,
            font=("Segoe UI", 8),
        )
        detail_label.pack(anchor="w")
        self.home_status_chips[key] = (chip, label, detail_label)
        return chip

    def _dashboard_metric_card(self, parent, key, label, value, detail, palette):
        card = self._build_desktop_card(parent, palette)
        tk.Label(
            card,
            text=label.upper(),
            bg=palette["card"],
            fg=palette["muted"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")
        value_label = tk.Label(
            card,
            text=value,
            bg=palette["card"],
            fg=palette["title"],
            font=("Segoe UI", 20, "bold"),
        )
        value_label.pack(anchor="w", pady=(6, 0))
        detail_label = tk.Label(
            card,
            text=detail,
            bg=palette["card"],
            fg=palette["muted"],
            font=("Segoe UI", 9),
            justify="left",
            wraplength=210,
        )
        detail_label.pack(anchor="w", pady=(4, 0))
        self.home_metric_labels[key] = (value_label, detail_label)
        return card

    def _build_home_dashboard(self):
        palette = self._desktop_window_palette()
        self.all_buttons = []
        self.home_metric_labels = {}
        self.home_status_chips = {}
        self.home_identity_labels = {}

        for widget in self.home_frame.winfo_children():
            widget.destroy()

        self.home_frame.configure(bg=palette["bg"], padx=22, pady=18)
        self.home_frame.columnconfigure(0, weight=1)
        self.home_frame.rowconfigure(2, weight=1)

        header = tk.Frame(self.home_frame, bg=palette["header_bg"], padx=20, pady=18)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.columnconfigure(1, weight=1)
        self.hero_frame = header

        self.load_logo(
            header,
            size=(96, 96),
            pack_options=None,
            grid_options={"row": 0, "column": 0, "sticky": "nw", "padx": (0, 18)}
        )

        self.hero_copy = tk.Frame(header, bg=palette["header_bg"])
        self.hero_copy.grid(row=0, column=1, sticky="nsew")
        self.brand_title = tk.Label(
            self.hero_copy,
            text=self.business_name or "QuickStock JA",
            font=("Segoe UI", 22, "bold"),
            bg=palette["header_bg"],
            fg=palette["header_title"],
        )
        self.brand_title.pack(anchor="w")
        self.brand_subtitle = tk.Label(
            self.hero_copy,
            text="Daily inventory, register, reports, and offline sync control center.",
            font=("Segoe UI", 10),
            bg=palette["header_bg"],
            fg=palette["header_muted"],
        )
        self.brand_subtitle.pack(anchor="w", pady=(4, 0))

        identity_row = tk.Frame(self.hero_copy, bg=palette["header_bg"])
        identity_row.pack(fill="x", pady=(14, 0))
        for key, label in (
            ("operator", "Operator: Not signed in"),
            ("role", "Role: -"),
            ("location", "Location: All Locations"),
        ):
            badge = tk.Label(
                identity_row,
                text=label,
                font=("Segoe UI", 9, "bold"),
                bg=palette["button_subtle"],
                fg=palette["button_subtle_text"],
                padx=10,
                pady=5,
            )
            badge.pack(side="left", padx=(0, 8))
            self.home_identity_labels[key] = badge
        self.session_pill = self.home_identity_labels["operator"]

        status_panel = tk.Frame(header, bg=palette["header_bg"])
        status_panel.grid(row=0, column=2, sticky="ne")
        api_title, api_detail, api_tone = self._api_status_summary()
        register_title, register_detail, register_tone = self._register_status_summary()
        self._dashboard_chip(status_panel, "api", api_title, api_detail, api_tone).pack(anchor="e", pady=(0, 8))
        self._dashboard_chip(status_panel, "register", register_title, register_detail, register_tone).pack(anchor="e")

        metrics = tk.Frame(self.home_frame, bg=palette["bg"])
        metrics.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        for index in range(4):
            metrics.columnconfigure(index, weight=1)
        metric_defs = (
            ("sales", "Today Sales", "$0.00", "Local receipt total"),
            ("inventory", "Inventory", "0 items", "0 units on hand"),
            ("queue", "Sync Queue", "0", "Actions waiting"),
            ("stock", "Stock Alerts", "0 low", "Items at five or fewer"),
        )
        for index, metric in enumerate(metric_defs):
            self._dashboard_metric_card(metrics, *metric, palette).grid(
                row=0,
                column=index,
                sticky="nsew",
                padx=(0 if index == 0 else 8, 0),
            )

        self.button_frame = tk.Frame(
            self.home_frame,
            bg=palette["card"],
            highlightthickness=1,
            highlightbackground=palette["card_border"],
            padx=16,
            pady=16,
        )
        self.button_frame.grid(row=2, column=0, sticky="nsew")
        for column in range(2):
            self.button_frame.columnconfigure(column, weight=1)
        for row in range(4):
            self.button_frame.rowconfigure(row, weight=1)

        self.create_button(self.button_frame, "Operations", self.open_operations_menu, row=0, col=0)
        self.create_button(self.button_frame, "Reports", self.open_reports_menu, row=0, col=1)
        self.create_button(self.button_frame, "Settings", self.open_settings_menu, row=1, col=0)
        self.create_button(self.button_frame, "Switch User", self.logout, row=1, col=1)
        self.create_button(self.button_frame, "Online", self.open_online_workspace, row=2, col=0)
        self.create_button(self.button_frame, "Users", self.open_users_workspace, row=2, col=1)
        self.create_button(self.button_frame, "Exit", self.root.quit, row=3, col=0)
        self._update_home_dashboard_widgets()

    def _update_home_dashboard_widgets(self):
        if not getattr(self, "home_frame", None):
            return

        palette = self._desktop_window_palette()
        if getattr(self, "brand_title", None):
            self.brand_title.configure(text=self.business_name or "QuickStock JA")

        identity = {
            "operator": f"Operator: {self.current_username or 'Not signed in'}",
            "role": f"Role: {str(self.current_role or '-').upper()}",
            "location": f"Location: {self.active_location_name or 'All Locations'}",
        }
        for key, text in identity.items():
            label = self.home_identity_labels.get(key)
            if label:
                label.configure(text=text)

        api_title, api_detail, api_tone = self._api_status_summary()
        register_title, register_detail, register_tone = self._register_status_summary()
        for key, title, detail, tone in (
            ("api", api_title, api_detail, api_tone),
            ("register", register_title, register_detail, register_tone),
        ):
            chip_tuple = self.home_status_chips.get(key)
            if not chip_tuple:
                continue
            chip, label, detail_label = chip_tuple
            bg, fg = self._chip_colors(palette, tone)
            chip.configure(bg=bg)
            label.configure(text=title, bg=bg, fg=fg)
            detail_label.configure(text=detail, bg=bg, fg=fg)

        metrics = self._home_inventory_metrics()
        try:
            queue_summary = self._summarize_pending_queue()
        except Exception:
            queue_summary = {"total": 0, "blocked": True}
        metric_values = {
            "sales": (self._format_money(self._today_sales_total()), "Local receipt total"),
            "inventory": (f"{metrics['items']:,} items", f"{metrics['units']:,} units on hand"),
            "queue": (str(queue_summary["total"]), "Actions waiting" if not queue_summary["blocked"] else "Queue needs review"),
            "stock": (f"{metrics['low']:,} low", f"Retail value {self._format_money(metrics['retail_value'])}"),
        }
        for key, (value, detail) in metric_values.items():
            labels = self.home_metric_labels.get(key)
            if labels:
                value_label, detail_label = labels
                value_label.configure(text=value)
                detail_label.configure(text=detail)

    def _apply_theme_preference(self):
        pref = (self.system.settings.get("theme") or "system").strip().lower()
        theme_mode = "Dark" if pref == "dark" else "Light"
        ctk.set_appearance_mode(theme_mode)
        palette = self._desktop_window_palette()

        if pref == "dark":
            bg_color = "#101827"
            header_bg = "#121B2F"
            panel_color = "#172233"
            panel_border = "#2B3C55"
            status_bg = "#1B2631"
            status_fg = "#58D68D"
            title_fg = "#ECF4FF"
            muted_fg = "#9FB3C8"
            button_fg = "#233247"
            button_hover = "#31465F"
            button_text = "#ECF4FF"
            button_border = "#415877"
            pill_bg = "#0F766E"
            pill_fg = "#DFFCF6"
        else:
            bg_color = palette["bg"]
            header_bg = palette["header_bg"]
            panel_color = palette["card"]
            panel_border = palette["card_border"]
            status_bg = palette["status_bg"]
            status_fg = palette["status_fg"]
            title_fg = palette["header_title"]
            muted_fg = palette["header_muted"]
            button_fg = palette["button_subtle"]
            button_hover = palette["button_subtle_active"]
            button_text = palette["button_subtle_text"]
            button_border = palette["card_border"]
            pill_bg = palette["badge_active_bg"]
            pill_fg = palette["badge_active_fg"]

        try:
            self.root.configure(bg=bg_color)
        except Exception:
            pass
        if hasattr(self, "home_frame"):
            self.home_frame.configure(bg=bg_color)
        if hasattr(self, "hero_frame"):
            self.hero_frame.configure(bg=header_bg)
        if hasattr(self, "hero_copy"):
            self.hero_copy.configure(bg=header_bg)
        if hasattr(self, "button_frame"):
            self.button_frame.configure(bg=panel_color, highlightbackground=panel_border)
        if hasattr(self, "status_bar"):
            self.status_bar.configure(bg=status_bg)
        if hasattr(self, "status_label"):
            self.status_label.configure(bg=status_bg, fg=status_fg)
        if hasattr(self, "logo_label"):
            self.logo_label.configure(fg_color=header_bg)
        if hasattr(self, "brand_title"):
            self.brand_title.configure(bg=header_bg, fg=title_fg)
        if hasattr(self, "brand_subtitle"):
            self.brand_subtitle.configure(bg=header_bg, fg=muted_fg)
        if hasattr(self, "session_pill"):
            self.session_pill.configure(bg=pill_bg, fg=pill_fg)
        for btn in getattr(self, "all_buttons", []):
            try:
                button_palette = self._home_button_palette(btn.cget("text"))
                if button_palette:
                    btn.configure(
                        fg_color=button_palette[0],
                        hover_color=button_palette[1],
                        text_color=button_palette[2],
                        border_width=1,
                        border_color=button_palette[3],
                    )
                    continue
                btn.configure(
                    fg_color=button_fg,
                    hover_color=button_hover,
                    text_color=button_text,
                    border_width=1,
                    border_color=button_border,
                )
            except Exception:
                pass
        if hasattr(self, "home_frame") and self.home_frame.winfo_exists():
            self._build_home_dashboard()
            if self.current_role:
                self.apply_permissions()

    def _save_cache(self, path, data):
        try:
            self._write_json_atomic(path, data)
        except Exception as e:
            logger.error(f"Failed to save cache to {path}: {e}")

    def _write_json_atomic(self, path, data, indent=2):
        directory = os.path.dirname(path) or "."
        temp_path = os.path.join(directory, f".{os.path.basename(path)}.tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent)
        os.replace(temp_path, path)

    def _load_cache(self, path):
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _load_json_object(self, path):
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _cache_scope_suffix(self):
        if self.current_user_id:
            return str(self.current_user_id)
        if self.current_username:
            normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(self.current_username).strip())
            if normalized:
                return normalized
        return "default"

    def _superuser_registry_cache_path(self):
        suffix = self._cache_scope_suffix()
        return os.path.join(DATA_DIR, f"superuser_registry_{suffix}.json")

    def _superuser_sync_status_cache_path(self):
        suffix = self._cache_scope_suffix()
        return os.path.join(DATA_DIR, f"superuser_sync_status_{suffix}.json")

    def _load_superuser_registry_snapshot(self):
        path = self._superuser_registry_cache_path()
        snapshot = self._load_json_object(path)
        if snapshot:
            return snapshot
        return self._load_json_object(SUPERUSER_REGISTRY_CACHE_FILE)

    def _save_superuser_registry_snapshot(self, scope, users):
        normalized_users = [dict(user) for user in users if isinstance(user, dict)]
        snapshot = {
            "scope": scope or "platform",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(normalized_users),
            "users": normalized_users,
        }
        path = self._superuser_registry_cache_path()
        self._write_json_atomic(path, snapshot)
        self._write_json_atomic(SUPERUSER_REGISTRY_CACHE_FILE, snapshot)
        return snapshot

    def _registry_snapshot_summary(self, snapshot):
        users = snapshot.get("users") or []
        companies = {
            str(user.get("company") or user.get("username") or "").strip()
            for user in users
            if str(user.get("company") or user.get("username") or "").strip()
        }
        active_users = 0
        flagged_users = 0
        for user in users:
            status = str(user.get("status") or ("active" if user.get("is_active") else "inactive")).strip().lower()
            if status == "active":
                active_users += 1
            else:
                flagged_users += 1
        return {
            "users": len(users),
            "companies": len(companies),
            "active_users": active_users,
            "flagged_users": flagged_users,
            "updated_at": snapshot.get("updated_at"),
            "scope": snapshot.get("scope") or "platform",
        }

    def _load_superuser_sync_snapshot(self):
        path = self._superuser_sync_status_cache_path()
        snapshot = self._load_json_object(path)
        if snapshot:
            return snapshot
        return self._load_json_object(SUPERUSER_SYNC_STATUS_CACHE_FILE)

    def _save_superuser_sync_snapshot(self, status, recent):
        snapshot = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "status": dict(status or {}),
            "recent_syncs": [dict(item) for item in (recent or []) if isinstance(item, dict)],
            "local_queue_count": len(self._read_pending_queue()),
        }
        path = self._superuser_sync_status_cache_path()
        self._write_json_atomic(path, snapshot)
        self._write_json_atomic(SUPERUSER_SYNC_STATUS_CACHE_FILE, snapshot)
        return snapshot

    def _selectable_locations(self, include_unassigned_when_only_option=True):
        locations = [loc for loc in (self.locations or []) if isinstance(loc, dict) and loc.get("name")]
        real_locations = [
            loc for loc in locations
            if str(loc.get("name") or "").strip().lower() != "unassigned"
        ]
        if real_locations:
            return real_locations
        if include_unassigned_when_only_option:
            return locations
        return []

    def _summarize_pending_queue(self, queue=None):
        queue = list(queue if queue is not None else self._read_pending_queue())
        summary = {
            "total": len(queue),
            "actions": {},
            "states": {},
            "latest_queued_at": None,
            "blocked": bool(getattr(self, "_queue_corruption_info", None)),
        }
        latest_dt = None
        for item in queue:
            action = str(item.get("action") or "UNKNOWN").strip().upper()
            summary["actions"][action] = summary["actions"].get(action, 0) + 1
            state = str((item.get("_sync") or {}).get("state") or "pending").strip().lower()
            summary["states"][state] = summary["states"].get(state, 0) + 1
            queued_at = self._parse_cached_datetime(item.get("queued_at"))
            if queued_at and (latest_dt is None or queued_at > latest_dt):
                latest_dt = queued_at
        if latest_dt:
            summary["latest_queued_at"] = latest_dt.isoformat()
        return summary

    def _format_queue_target(self, item):
        target = item.get("sku") or item.get("name") or item.get("username")
        if target:
            return str(target)
        items = item.get("items")
        if isinstance(items, list) and items:
            first = items[0] if isinstance(items[0], dict) else {}
            sku = first.get("sku") or first.get("SKU") or first.get("name")
            if len(items) == 1 and sku:
                return str(sku)
            return f"{len(items)} line item(s)"
        return "Queued task"

    def _format_queue_detail(self, item):
        parts = []
        for label, value in (
            ("Quantity", item.get("quantity")),
            ("Location", item.get("location_id") or item.get("from_location_id")),
            ("To", item.get("to_location_id")),
            ("Tender", item.get("tender")),
            ("Total", item.get("total_price")),
        ):
            if value not in (None, "", []):
                parts.append(f"{label}: {value}")
        if not parts and item.get("items"):
            parts.append(f"Items: {len(item.get('items') or [])}")
        return " | ".join(parts) or "Waiting for reconnect"

    def _render_registry_rows(self, tree, users):
        tree.delete(*tree.get_children())
        for user in users:
            tree.insert("", "end", values=(
                user.get("company") or user.get("username") or "N/A",
                user.get("username") or "N/A",
                user.get("email") or "No email set",
                str(user.get("role") or "cashier").upper(),
                str(user.get("status") or ("active" if user.get("is_active") else "inactive")).upper(),
                str(user.get("plan") or "").upper(),
                user.get("default_location") or "All locations",
            ))

    def _build_superuser_sync_status_message(self, intro, snapshot=None):
        queue_summary = self._summarize_pending_queue()
        lines = [intro, ""]
        lines.extend([
            f"Local queued actions: {queue_summary['total']}",
            f"Distinct queued action types: {len(queue_summary['actions'])}",
            f"Latest queued action: {queue_summary['latest_queued_at'] or 'No queued timestamp yet'}",
            f"Local inventory records: {len(self.system.inventory or [])}",
            f"Local receipts: {len(self.system.receipts or [])}",
        ])
        if queue_summary["actions"]:
            lines.append("")
            lines.append("Queued actions by type:")
            for action, count in sorted(queue_summary["actions"].items()):
                lines.append(f"- {action}: {count}")
        if snapshot:
            status = snapshot.get("status") or {}
            recent = snapshot.get("recent_syncs") or []
            lines.append("")
            lines.append(f"Last cached API sync snapshot: {snapshot.get('updated_at') or 'Unknown'}")
            lines.append(f"Cached pending syncs: {status.get('pending_syncs', 0)}")
            lines.append(f"Cached items needing sync: {status.get('items_needing_sync', 0)}")
            lines.append(f"Cached unsynced sales: {status.get('unsynced_sales', 0)}")
            lines.append("Cached recent sync activity:")
            if recent:
                for item in recent[:5]:
                    lines.append(
                        f"- {item.get('sync_type') or 'sync'} / {item.get('direction') or 'n/a'}: "
                        f"{item.get('status') or 'unknown'}"
                    )
            else:
                lines.append("- No cached sync records.")
        return "\n".join(lines)

    def _desktop_window_palette(self):
        pref = (self.system.settings.get("theme") or "system").strip().lower()
        if pref == "dark":
            return {
                "bg": "#101827",
                "header_bg": "#121B2F",
                "header_title": "#F8FAFC",
                "header_muted": "#94A3B8",
                "card": "#172233",
                "card_border": "#2B3C55",
                "title": "#ECF4FF",
                "text": "#D7E3F1",
                "muted": "#9FB3C8",
                "input_bg": "#0F172A",
                "input_fg": "#ECF4FF",
                "input_border": "#2B3C55",
                "subtle": "#1F2937",
                "subtle_text": "#ECF4FF",
                "subtle_active": "#273449",
                "button_subtle": "#1F2D46",
                "button_subtle_active": "#2A3C5A",
                "button_subtle_text": "#F8FAFC",
                "primary": "#0F766E",
                "primary_active": "#115E59",
                "primary_text": "#FFFFFF",
                "accent": "#3D7BFF",
                "accent_active": "#2E63D3",
                "danger": "#B91C1C",
                "danger_active": "#991B1B",
                "banner_bg": "#172233",
                "banner_border": "#3D7BFF",
                "banner_title": "#8FB3FF",
                "banner_text": "#D7E3F1",
                "badge_bg": "#214C45",
                "badge_fg": "#DFFCF6",
                "badge_active_bg": "#3D7BFF",
                "badge_active_fg": "#FFFFFF",
                "status_bg": "#1B2631",
                "status_fg": "#58D68D",
                "table_bg": "#172233",
                "table_alt": "#1B2631",
                "table_heading": "#1F2D46",
                "selection": "#3D7BFF",
                "menu_bg": "#121B2F",
                "menu_panel": "#18243A",
                "menu_button": "#1F2D46",
                "menu_button_active": "#2A3C5A",
            }
        return {
            "bg": "#EEF3F8",
            "header_bg": "#121B2F",
            "header_title": "#F8FAFC",
            "header_muted": "#94A3B8",
            "card": "#FFFFFF",
            "card_border": "#D7E0EB",
            "title": "#13233D",
            "text": "#22344D",
            "muted": "#60758F",
            "input_bg": "#FFFFFF",
            "input_fg": "#132238",
            "input_border": "#D7E0EB",
            "subtle": "#E5EDF6",
            "subtle_text": "#132238",
            "subtle_active": "#D5E0EC",
            "button_subtle": "#E5EDF6",
            "button_subtle_active": "#D5E0EC",
            "button_subtle_text": "#132238",
            "primary": "#1EB980",
            "primary_active": "#17A06E",
            "primary_text": "#FFFFFF",
            "accent": "#3D7BFF",
            "accent_active": "#2E63D3",
            "danger": "#D64432",
            "danger_active": "#B93829",
            "banner_bg": "#F8FBFF",
            "banner_border": "#3D7BFF",
            "banner_title": "#1E4CA1",
            "banner_text": "#5F6F82",
            "badge_bg": "#E9F2FF",
            "badge_fg": "#2E63D3",
            "badge_active_bg": "#3D7BFF",
            "badge_active_fg": "#FFFFFF",
            "status_bg": "#E5EDF6",
            "status_fg": "#132238",
            "table_bg": "#FFFFFF",
            "table_alt": "#F8FBFF",
            "table_heading": "#E5EDF6",
            "selection": "#3D7BFF",
            "menu_bg": "#121B2F",
            "menu_panel": "#18243A",
            "menu_button": "#1F2D46",
            "menu_button_active": "#2A3C5A",
        }

    def _style_desktop_ttk(self, palette=None):
        palette = palette or self._desktop_window_palette()
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Treeview",
            background=palette["table_bg"],
            fieldbackground=palette["table_bg"],
            foreground=palette["text"],
            rowheight=30,
            bordercolor=palette["card_border"],
            lightcolor=palette["card_border"],
            darkcolor=palette["card_border"],
            relief="flat",
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background=palette["table_heading"],
            foreground=palette["title"],
            bordercolor=palette["card_border"],
            relief="flat",
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Treeview",
            background=[("selected", palette["selection"])],
            foreground=[("selected", "#FFFFFF")],
        )
        style.configure(
            "TCombobox",
            fieldbackground=palette["input_bg"],
            background=palette["input_bg"],
            foreground=palette["input_fg"],
            bordercolor=palette["input_border"],
            arrowcolor=palette["title"],
            relief="flat",
            padding=7,
        )
        style.configure(
            "TNotebook",
            background=palette["bg"],
            borderwidth=0,
        )
        style.configure(
            "TNotebook.Tab",
            background=palette["subtle"],
            foreground=palette["text"],
            padding=(14, 8),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", palette["accent"])],
            foreground=[("selected", "#FFFFFF")],
        )

    def _style_desktop_window(self, win, palette=None):
        palette = palette or self._desktop_window_palette()
        win.configure(bg=palette["bg"])
        self._style_desktop_ttk(palette)
        return palette

    def _desktop_label(self, parent, text, palette, surface="bg", role="text", font=None, **kwargs):
        return tk.Label(
            parent,
            text=text,
            font=font or ("Segoe UI", 10),
            bg=palette.get(surface, palette["bg"]),
            fg=palette.get(role, palette["text"]),
            **kwargs,
        )

    def _desktop_entry(self, parent, palette, textvariable=None, width=None, show=None, **kwargs):
        entry = tk.Entry(
            parent,
            textvariable=textvariable,
            width=width,
            show=show,
            font=("Segoe UI", 11),
            bg=palette["input_bg"],
            fg=palette["input_fg"],
            insertbackground=palette["input_fg"],
            relief="flat",
            bd=0,
            **kwargs,
        )
        return entry

    def _desktop_button(self, parent, text, command, palette, kind="subtle", **kwargs):
        color_map = {
            "primary": ("primary", "primary_active", "primary_text"),
            "accent": ("accent", "accent_active", "primary_text"),
            "danger": ("danger", "danger_active", "primary_text"),
            "subtle": ("subtle", "subtle_active", "subtle_text"),
        }
        bg_key, active_key, fg_key = color_map.get(kind, color_map["subtle"])
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=palette[bg_key],
            fg=palette[fg_key],
            activebackground=palette[active_key],
            activeforeground=palette[fg_key],
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=14,
            pady=9,
            **kwargs,
        )

    def _desktop_form_shell(self, win, title, geometry):
        palette = self._style_desktop_window(win)
        win.title(title)
        win.geometry(geometry)
        win.transient(self.root)
        content = self._build_desktop_card(win, palette)
        content.pack(fill="both", expand=True, padx=18, pady=18)
        return palette, content

    def _build_desktop_surface(self, win, title, subtitle, geometry="900x620"):
        palette = self._desktop_window_palette()
        self._style_desktop_ttk(palette)
        win.title(title)
        win.geometry(geometry)
        win.configure(bg=palette["bg"])
        shell = tk.Frame(win, bg=palette["bg"])
        shell.pack(fill="both", expand=True, padx=22, pady=18)

        header = tk.Frame(shell, bg=palette["bg"])
        header.pack(fill="x")
        hero = tk.Frame(header, bg=palette["bg"])
        hero.pack(side="left", fill="x", expand=True)
        tk.Label(
            hero,
            text=title,
            font=("Segoe UI", 20, "bold"),
            bg=palette["bg"],
            fg=palette["title"],
        ).pack(anchor="w")
        tk.Label(
            hero,
            text=subtitle,
            font=("Segoe UI", 10),
            bg=palette["bg"],
            fg=palette["muted"],
            justify="left",
        ).pack(anchor="w", pady=(4, 0))
        tk.Button(
            header,
            text="Back to Main GUI",
            command=win.destroy,
            bg=palette["subtle"],
            fg=palette["subtle_text"],
            activebackground=palette["subtle_active"],
            relief="flat",
            padx=14,
            pady=8,
        ).pack(side="right")

        body = tk.Frame(shell, bg=palette["bg"])
        body.pack(fill="both", expand=True, pady=(18, 0))
        return palette, shell, body

    def _build_desktop_card(self, parent, palette):
        return tk.Frame(
            parent,
            bg=palette["card"],
            highlightthickness=1,
            highlightbackground=palette["card_border"],
            padx=16,
            pady=16,
        )

    def _build_desktop_banner(self, parent, palette, title, detail, badge_text=None):
        banner = tk.Frame(
            parent,
            bg=palette["banner_bg"],
            highlightthickness=1,
            highlightbackground=palette["banner_border"],
            padx=16,
            pady=14,
        )
        tk.Label(
            banner,
            text=title,
            font=("Segoe UI", 13, "bold"),
            bg=palette["banner_bg"],
            fg=palette["banner_title"],
        ).pack(anchor="w")
        tk.Label(
            banner,
            text=detail,
            font=("Segoe UI", 10),
            bg=palette["banner_bg"],
            fg=palette["banner_text"],
            justify="left",
        ).pack(anchor="w", pady=(5, 0))
        if badge_text:
            tk.Label(
                banner,
                text=badge_text,
                font=("Segoe UI", 9, "bold"),
                bg=palette["badge_bg"],
                fg=palette["badge_fg"],
                padx=10,
                pady=4,
            ).pack(anchor="w", pady=(10, 0))
        return banner

    def _is_superuser_role(self, role=None):
        role_name = (role if role is not None else self.current_role) or ""
        return str(role_name).strip().lower() in {"superuser", "admin"}

    def _is_platform_superuser(self):
        return str(self.current_role or "").strip().lower() == "superuser"

    def _customer_registry_path(self):
        return CUSTOMER_REGISTRY_FILE

    def _load_customer_registry(self):
        path = self._customer_registry_path()
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                return [entry for entry in data if isinstance(entry, dict)]
        except Exception:
            return []
        return []

    def _save_customer_registry(self, customers):
        try:
            normalized = [entry for entry in customers if isinstance(entry, dict)]
            self._write_json_atomic(self._customer_registry_path(), normalized)
            self.customers = normalized
        except Exception as exc:
            logger.error(f"Failed to save customer registry: {exc}")

    def _normalize_customer_name(self, value):
        return " ".join(str(value or "").split()).strip()

    def _customer_display_name(self, record):
        return self._normalize_customer_name(record.get("name") or record.get("Customer Name") or record.get("customer_name"))

    def _receipt_customer_name(self, receipt):
        return self._normalize_customer_name(
            receipt.get("Customer Name")
            or receipt.get("customer_name")
            or receipt.get("Client")
            or receipt.get("Customer")
            or ""
        )

    def _receipt_document_type(self, receipt):
        return str(receipt.get("Document Type") or receipt.get("document_type") or "Invoice").strip() or "Invoice"

    def _customer_receipts(self, customer_name):
        target = self._normalize_customer_name(customer_name).lower()
        if not target:
            return []
        results = []
        for receipt in self.system.receipts or []:
            if str(receipt.get("archived")) == "True":
                continue
            names = {
                self._receipt_customer_name(receipt).lower(),
                self._normalize_customer_name(receipt.get("Customer")).lower(),
            }
            if target in {name for name in names if name}:
                results.append(receipt)
        results.sort(key=lambda item: self._receipt_datetime(item) or datetime.min, reverse=True)
        return results

    def _rebuild_customer_registry(self):
        existing = {self._customer_display_name(record).lower(): record for record in self._load_customer_registry() if self._customer_display_name(record)}
        latest_by_name = {}

        for receipt in self.system.receipts or []:
            if str(receipt.get("archived")) == "True":
                continue
            name = self._receipt_customer_name(receipt)
            if not name:
                continue
            key = name.lower()
            record = latest_by_name.get(key, existing.get(key, {})).copy()
            receipt_time = self._receipt_datetime(receipt)
            if not record:
                record = {
                    "name": name,
                    "phone": "",
                    "email": "",
                    "notes": "",
                    "joined": self._receipt_date_value(receipt),
                    "last_activity": self._receipt_date_value(receipt),
                    "invoice_count": 0,
                    "quotation_count": 0,
                    "invoice_numbers": [],
                    "quotation_numbers": [],
                    "credit_balance": 0.0,
                }
            record["name"] = name
            record["credit_balance"] = round(self._receipt_number_value(record.get("credit_balance", 0)), 2)
            record["last_activity"] = self._receipt_date_value(receipt)
            record["joined"] = record.get("joined") or self._receipt_date_value(receipt)
            document_type = self._receipt_document_type(receipt).lower()
            invoice_no = self._receipt_invoice_value(receipt)
            if document_type == "quotation":
                record["quotation_count"] = int(record.get("quotation_count") or 0) + 1
                if invoice_no is not None:
                    record.setdefault("quotation_numbers", [])
                    if invoice_no not in record["quotation_numbers"]:
                        record["quotation_numbers"].append(invoice_no)
            else:
                record["invoice_count"] = int(record.get("invoice_count") or 0) + 1
                if invoice_no is not None:
                    record.setdefault("invoice_numbers", [])
                    if invoice_no not in record["invoice_numbers"]:
                        record["invoice_numbers"].append(invoice_no)
            if receipt_time:
                record["last_activity_sort"] = receipt_time.isoformat()
            latest_by_name[key] = record

        merged = list(existing.values())
        for key, record in latest_by_name.items():
            found = next((idx for idx, entry in enumerate(merged) if self._customer_display_name(entry).lower() == key), None)
            if found is None:
                merged.append(record)
            else:
                current = merged[found]
                current.update(record)

        merged.sort(
            key=lambda entry: self._receipt_datetime(
                {"Timestamp": entry.get("last_activity_sort") or entry.get("last_activity") or entry.get("joined")}
            ) or datetime.min,
            reverse=True,
        )
        self._save_customer_registry(merged)
        return merged

    def _get_customer_record(self, customer_name):
        normalized = self._normalize_customer_name(customer_name)
        if not normalized:
            return None
        for record in self.customers or self._load_customer_registry():
            if self._customer_display_name(record).lower() == normalized.lower():
                return record
        return None

    def _upsert_customer_record(self, customer_name, phone="", email="", notes=""):
        normalized = self._normalize_customer_name(customer_name)
        if not normalized:
            return None
        customers = self._load_customer_registry()
        record = next((entry for entry in customers if self._customer_display_name(entry).lower() == normalized.lower()), None)
        if record is None:
            record = {
                "name": normalized,
                "phone": "",
                "email": "",
                "notes": "",
                "joined": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "last_activity": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "invoice_count": 0,
                "quotation_count": 0,
                "invoice_numbers": [],
                "quotation_numbers": [],
                "credit_balance": 0.0,
            }
            customers.append(record)
        else:
            record["name"] = normalized
            record["credit_balance"] = self._receipt_number_value(record.get("credit_balance", 0))
        if phone is not None:
            record["phone"] = str(phone).strip()
        if email is not None:
            record["email"] = str(email).strip()
        if notes is not None:
            record["notes"] = str(notes).strip()
        record["last_activity"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_customer_registry(customers)
        return record

    def _customer_credit_value(self, customer_name):
        record = self._get_customer_record(customer_name)
        if not record:
            return 0.0
        return round(self._receipt_number_value(record.get("credit_balance", 0)), 2)

    def _set_customer_credit(self, customer_name, new_value):
        normalized = self._normalize_customer_name(customer_name)
        if not normalized:
            return None
        customers = self._load_customer_registry()
        updated = None
        for entry in customers:
            if self._customer_display_name(entry).lower() == normalized.lower():
                entry["credit_balance"] = round(max(0.0, self._receipt_number_value(new_value)), 2)
                updated = entry
                break
        if updated is None:
            updated = self._upsert_customer_record(normalized)
            if not updated:
                return None
            updated["credit_balance"] = round(max(0.0, self._receipt_number_value(new_value)), 2)
            customers = [
                entry for entry in self._load_customer_registry()
                if self._customer_display_name(entry).lower() != normalized.lower()
            ] + [updated]
        self._save_customer_registry(customers)
        return updated

    def _adjust_customer_credit(self, customer_name, delta):
        normalized = self._normalize_customer_name(customer_name)
        if not normalized:
            return None
        current = self._customer_credit_value(normalized)
        new_value = round(max(0.0, current + self._receipt_number_value(delta)), 2)
        return self._set_customer_credit(normalized, new_value)

    def _payment_entry_amount(self, entry):
        return round(self._receipt_number_value((entry or {}).get("amount", 0)), 2)

    def _receipt_payment_history(self, receipt):
        history = receipt.get("Payment History")
        if isinstance(history, list):
            return [entry for entry in history if isinstance(entry, dict)]
        return []

    def _receipt_total_bill_value(self, receipt):
        return round(self._receipt_number_value(receipt.get("Total Bill", 0)), 2)

    def _create_receipt_payment_entry(
        self,
        amount,
        payment_method="cash",
        reference="",
        notes="",
        amount_tendered=None,
        change_given=0.0,
        applied_customer_credit=0.0,
        credited_customer_overpayment=0.0,
    ):
        amount_value = round(max(0.0, self._receipt_number_value(amount)), 2)
        tendered_value = amount_value if amount_tendered is None else round(max(0.0, self._receipt_number_value(amount_tendered)), 2)
        return {
            "payment_id": uuid.uuid4().hex,
            "amount": amount_value,
            "payment_method": str(payment_method or "cash").strip() or "cash",
            "reference": str(reference or "").strip(),
            "notes": str(notes or "").strip(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "amount_tendered": tendered_value,
            "change_given": round(max(0.0, self._receipt_number_value(change_given)), 2),
            "applied_customer_credit": round(max(0.0, self._receipt_number_value(applied_customer_credit)), 2),
            "credited_customer_overpayment": round(max(0.0, self._receipt_number_value(credited_customer_overpayment)), 2),
        }

    def _sync_receipt_payment_state(self, receipt):
        history = self._receipt_payment_history(receipt)
        if not history and self._receipt_document_type(receipt).lower() != "quotation":
            total_bill = self._receipt_total_bill_value(receipt)
            default_amount_paid = self._receipt_number_value(receipt.get("Amount Paid", total_bill))
            default_change = self._receipt_number_value(receipt.get("Change Given", 0))
            history = [
                self._create_receipt_payment_entry(
                    amount=default_amount_paid if default_amount_paid > 0 else total_bill,
                    payment_method=receipt.get("Payment Method", "cash"),
                    amount_tendered=default_amount_paid if default_amount_paid > 0 else total_bill,
                    change_given=default_change,
                )
            ]
        receipt["Payment History"] = history
        total_paid = round(sum(self._payment_entry_amount(entry) for entry in history), 2)
        total_bill = self._receipt_total_bill_value(receipt)
        balance_due = round(max(0.0, total_bill - total_paid), 2)
        overpayment = round(max(0.0, total_paid - total_bill), 2)
        receipt["Total Paid"] = total_paid
        receipt["Balance Due"] = balance_due
        receipt["Overpayment"] = overpayment
        receipt["Payment Status"] = "Paid" if total_bill > 0 and balance_due <= 0 else "Issued"
        if history:
            latest = history[-1]
            receipt["Amount Paid"] = round(self._receipt_number_value(latest.get("amount_tendered", latest.get("amount", 0))), 2)
            receipt["Change Given"] = round(self._receipt_number_value(latest.get("change_given", 0)), 2)
            receipt["Payment Method"] = latest.get("payment_method", receipt.get("Payment Method", "cash"))
        else:
            receipt["Amount Paid"] = 0.0
            receipt["Change Given"] = 0.0
        return receipt

    def _save_receipts_and_customers(self):
        storage.save_data(
            self.system.inventory,
            self.system.receipts,
            self.system.settings
        )
        self.customers = self._rebuild_customer_registry()

    def _find_receipt_index(self, receipt):
        for idx, current in enumerate(self.system.receipts or []):
            if current is receipt:
                return idx
            if self._receipt_invoice_value(current) == self._receipt_invoice_value(receipt):
                return idx
        return None

    def _apply_payment_to_receipt(
        self,
        receipt,
        amount,
        payment_method="cash",
        credit_to_apply=0.0,
        reference="",
        notes="",
    ):
        receipt = self._sync_receipt_payment_state(receipt)
        customer_name = self._receipt_customer_name(receipt)
        available_credit = self._customer_credit_value(customer_name) if customer_name else 0.0
        balance_due = self._receipt_number_value(receipt.get("Balance Due", 0))
        amount_value = round(max(0.0, self._receipt_number_value(amount)), 2)
        credit_value = round(max(0.0, self._receipt_number_value(credit_to_apply)), 2)

        if amount_value <= 0 and credit_value <= 0:
            return False, "Enter a payment amount or apply customer credit."
        if credit_value > 0 and not customer_name:
            return False, "Assign a customer to this invoice before applying account credit."
        if credit_value > min(available_credit, balance_due):
            return False, f"Only ${min(available_credit, balance_due):.2f} of credit can be applied."

        remaining_after_credit = round(max(0.0, balance_due - credit_value), 2)
        overpayment = round(max(0.0, amount_value - remaining_after_credit), 2)
        if overpayment > 0 and not customer_name:
            return False, "Enter a customer name before storing any overage as credit."

        if credit_value > 0:
            self._adjust_customer_credit(customer_name, -credit_value)
            receipt.setdefault("Payment History", []).append(
                self._create_receipt_payment_entry(
                    amount=credit_value,
                    payment_method="account_credit",
                    reference=reference,
                    notes=notes or "Applied from customer account credit.",
                    applied_customer_credit=credit_value,
                )
            )

        if amount_value > 0:
            change_given = overpayment if str(payment_method).strip().lower() == "cash" else 0.0
            receipt.setdefault("Payment History", []).append(
                self._create_receipt_payment_entry(
                    amount=amount_value,
                    payment_method=payment_method,
                    reference=reference,
                    notes=notes,
                    amount_tendered=amount_value,
                    change_given=change_given,
                    credited_customer_overpayment=overpayment,
                )
            )

        if overpayment > 0 and customer_name:
            self._adjust_customer_credit(customer_name, overpayment)

        self._sync_receipt_payment_state(receipt)
        self._save_receipts_and_customers()
        return True, overpayment

    def _revert_receipt_payment(self, receipt, payment_id):
        receipt = self._sync_receipt_payment_state(receipt)
        history = self._receipt_payment_history(receipt)
        payment = next((entry for entry in history if str(entry.get("payment_id")) == str(payment_id)), None)
        if not payment:
            return False, "Selected payment could not be found."

        customer_name = self._receipt_customer_name(receipt)
        credited_overpayment = self._receipt_number_value(payment.get("credited_customer_overpayment", 0))
        applied_credit = self._receipt_number_value(payment.get("applied_customer_credit", 0))
        if credited_overpayment > 0:
            if not customer_name:
                return False, "This payment cannot be reverted because the customer record is missing."
            if self._customer_credit_value(customer_name) < credited_overpayment:
                return False, f"Cannot revert yet. ${credited_overpayment:.2f} of stored credit has already been used."
            self._adjust_customer_credit(customer_name, -credited_overpayment)
        if applied_credit > 0:
            if not customer_name:
                return False, "This credit payment cannot be reverted because the customer record is missing."
            self._adjust_customer_credit(customer_name, applied_credit)

        receipt["Payment History"] = [entry for entry in history if str(entry.get("payment_id")) != str(payment_id)]
        self._sync_receipt_payment_state(receipt)
        self._save_receipts_and_customers()
        return True, payment

    def _open_receipt_payment_window(self, receipt, parent=None, on_update=None):
        receipt = self._sync_receipt_payment_state(receipt)
        customer_name = self._receipt_customer_name(receipt)
        balance_due = self._receipt_number_value(receipt.get("Balance Due", 0))
        available_credit = self._customer_credit_value(customer_name) if customer_name else 0.0

        win = tk.Toplevel(parent or self.root)
        palette, content = self._desktop_form_shell(win, f"Collect Payment - Invoice #{self._receipt_invoice_value(receipt)}", "560x590")
        win.transient(parent or self.root)

        self._desktop_label(content, f"Invoice #{self._receipt_invoice_value(receipt)}", palette, surface="card", role="title", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        self._desktop_label(content, f"Customer: {customer_name or 'Walk-In Customer'}", palette, surface="card", role="text").pack(anchor="w", pady=(6, 0))
        self._desktop_label(content, f"Total: ${self._receipt_total_bill_value(receipt):.2f}", palette, surface="card", role="text").pack(anchor="w")
        self._desktop_label(content, f"Already Paid: ${self._receipt_number_value(receipt.get('Total Paid', 0)):.2f}", palette, surface="card", role="text").pack(anchor="w")
        self._desktop_label(content, f"Balance Due: ${balance_due:.2f}", palette, surface="card", role="title", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 10))

        amount_var = tk.StringVar(value=f"{balance_due:.2f}")
        credit_var = tk.StringVar(value="0.00")
        method_var = tk.StringVar(value="cash")
        reference_var = tk.StringVar()
        overage_var = tk.StringVar(value="Amount Over: $0.00")
        remaining_var = tk.StringVar(value=f"Remaining After Credit: ${balance_due:.2f}")
        credit_var_display = tk.StringVar(value=f"Available Customer Credit: ${available_credit:.2f}")

        for label_text, variable in [
            ("Amount Paid", amount_var),
            ("Apply Account Credit", credit_var),
            ("Reference", reference_var),
        ]:
            self._desktop_label(content, label_text, palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
            self._desktop_entry(content, palette, textvariable=variable).pack(fill="x", pady=(4, 8), ipady=8)

        self._desktop_label(content, "Payment Method", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Combobox(content, textvariable=method_var, values=["cash", "card", "bank_transfer", "mobile_money", "other"], state="readonly").pack(fill="x", pady=(0, 8), ipady=6)

        self._desktop_label(content, "Notes", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        notes_box = tk.Text(content, height=5, wrap="word", bg=palette["input_bg"], fg=palette["input_fg"], insertbackground=palette["input_fg"], relief="flat", bd=0, font=("Segoe UI", 10))
        notes_box.pack(fill="both", expand=True, pady=(0, 10))

        info_card = tk.Frame(content, bg=palette["banner_bg"], padx=12, pady=12, highlightthickness=1, highlightbackground=palette["banner_border"])
        info_card.pack(fill="x", pady=(0, 12))
        tk.Label(info_card, textvariable=credit_var_display, bg=palette["banner_bg"], fg=palette["banner_title"], font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Label(info_card, textvariable=remaining_var, bg=palette["banner_bg"], fg=palette["banner_title"]).pack(anchor="w", pady=(4, 0))
        tk.Label(info_card, textvariable=overage_var, bg=palette["banner_bg"], fg=palette["banner_title"], font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(4, 0))

        def refresh_preview(*_args):
            try:
                amount_value = max(0.0, float(amount_var.get() or 0))
            except Exception:
                amount_value = 0.0
            try:
                credit_value = max(0.0, float(credit_var.get() or 0))
            except Exception:
                credit_value = 0.0
            credit_value = min(credit_value, available_credit, balance_due)
            remaining_after_credit = max(0.0, balance_due - credit_value)
            amount_over = max(0.0, amount_value - remaining_after_credit)
            credit_var_display.set(f"Available Customer Credit: ${available_credit:.2f}")
            remaining_var.set(f"Remaining After Credit: ${remaining_after_credit:.2f}")
            overage_var.set(f"Amount Over: ${amount_over:.2f}")
            if customer_name:
                info_card.configure(bg=palette["banner_bg"] if amount_over <= 0 else palette["badge_bg"])
            else:
                info_card.configure(bg="#FDECEC" if amount_over > 0 or credit_value > 0 else palette["banner_bg"])

        amount_var.trace_add("write", refresh_preview)
        credit_var.trace_add("write", refresh_preview)
        refresh_preview()

        def save_payment():
            success, result = self._apply_payment_to_receipt(
                receipt,
                amount=amount_var.get(),
                payment_method=method_var.get(),
                credit_to_apply=credit_var.get(),
                reference=reference_var.get(),
                notes=notes_box.get("1.0", "end").strip(),
            )
            if not success:
                messagebox.showwarning("Payment Not Saved", result, parent=win)
                return
            if on_update:
                on_update()
            if isinstance(result, float) and result > 0 and customer_name:
                messagebox.showinfo("Credit Stored", f"Customer is over by ${result:.2f}. It has been added to {customer_name}'s credit.", parent=win)
            win.destroy()

        action_row = tk.Frame(content, bg=palette["card"])
        action_row.pack(fill="x")
        self._desktop_button(action_row, "Save Payment", save_payment, palette, kind="primary").pack(side="left")
        self._desktop_button(action_row, "Cancel", win.destroy, palette, kind="subtle").pack(side="right")

    def _show_receipt_detail_window(self, receipt, parent=None, on_update=None):
        receipt = self._sync_receipt_payment_state(receipt)
        detail = tk.Toplevel(parent or self.root)
        invoice_display = self._receipt_invoice_value(receipt, fallback="Invoice")
        palette, wrap = self._desktop_form_shell(detail, f"Invoice #{invoice_display}", "620x800")
        detail.transient(parent or self.root)

        self._desktop_label(wrap, f"Invoice #{invoice_display}", palette, surface="card", role="title", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        self._desktop_label(wrap, f"Date: {self._receipt_date_value(receipt)}", palette, surface="card", role="text").pack(anchor="w", pady=(6, 0))
        self._desktop_label(wrap, f"Customer: {self._receipt_customer_name(receipt) or 'Walk-In Customer'}", palette, surface="card", role="text").pack(anchor="w", pady=(4, 0))
        self._desktop_label(wrap, f"Status: {receipt.get('Payment Status', 'Issued')}", palette, surface="card", role="text").pack(anchor="w", pady=(4, 0))
        self._desktop_label(wrap, f"Payment Method: {receipt.get('Payment Method', 'cash')}", palette, surface="card", role="text").pack(anchor="w", pady=(4, 0))
        self._desktop_label(wrap, f"Total Paid: ${self._receipt_number_value(receipt.get('Total Paid', 0)):.2f}", palette, surface="card", role="text").pack(anchor="w", pady=(4, 0))
        self._desktop_label(wrap, f"Balance Due: ${self._receipt_number_value(receipt.get('Balance Due', 0)):.2f}", palette, surface="card", role="title", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(4, 10))
        if self._receipt_number_value(receipt.get("Change Given", 0)) > 0:
            self._desktop_label(wrap, f"Change Given: ${self._receipt_number_value(receipt.get('Change Given', 0)):.2f}", palette, surface="card", role="primary").pack(anchor="w", pady=(0, 4))
        if self._receipt_number_value(receipt.get("Overpayment", 0)) > 0:
            self._desktop_label(wrap, f"Overpayment Logged: ${self._receipt_number_value(receipt.get('Overpayment', 0)):.2f}", palette, surface="card", role="accent").pack(anchor="w")

        items_frame = tk.Frame(wrap, bg=palette["card"], highlightthickness=1, highlightbackground=palette["card_border"], padx=10, pady=10)
        items_frame.pack(fill="x", pady=(0, 12))
        self._desktop_label(items_frame, "Items", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        for item in self._receipt_items(receipt):
            calc_total = self._receipt_number_value(item.get("Total", item.get("Price", 0) * item.get("Quantity", 0)))
            item_name = item.get("Name") or item.get("SKU") or "Item"
            item_qty = self._coerce_int(item.get("Quantity")) or 0
            self._desktop_label(items_frame, f"{item_name} x{item_qty} = ${calc_total:.2f}", palette, surface="card", role="text", anchor="w").pack(fill="x")

        totals_frame = tk.Frame(wrap, bg=palette["card"], highlightthickness=1, highlightbackground=palette["card_border"], padx=10, pady=10)
        totals_frame.pack(fill="x", pady=(0, 12))
        self._desktop_label(totals_frame, "Totals", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tax_rate = self._receipt_number_value(receipt.get("TaxRate", 0))
        tax_label = receipt.get("TaxLabel") or self.system.settings.get("tax_label", "GCT")
        self._desktop_label(totals_frame, f"Subtotal: ${self._receipt_number_value(receipt.get('Subtotal', 0)):.2f}", palette, surface="card", role="text").pack(anchor="w")
        self._desktop_label(totals_frame, f"{tax_label} ({tax_rate * 100:.0f}%): ${self._receipt_number_value(receipt.get('GCT', 0)):.2f}", palette, surface="card", role="text").pack(anchor="w", pady=(4, 0))
        self._desktop_label(totals_frame, f"TOTAL: ${self._receipt_number_value(receipt.get('Total Bill', 0)):.2f}", palette, surface="card", role="title", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(8, 0))
        self._desktop_label(totals_frame, f"Amount Paid: ${self._receipt_number_value(receipt.get('Amount Paid', 0)):.2f}", palette, surface="card", role="text").pack(anchor="w", pady=(8, 0))

        payments_frame = tk.Frame(wrap, bg=palette["card"], highlightthickness=1, highlightbackground=palette["card_border"], padx=10, pady=10)
        payments_frame.pack(fill="both", expand=True, pady=(0, 12))
        self._desktop_label(payments_frame, "Payment History", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))
        payment_columns = ("When", "Method", "Amount", "Applied Credit", "Stored Overage")
        payment_tree = ttk.Treeview(payments_frame, columns=payment_columns, show="headings", height=8)
        payment_tree.pack(fill="both", expand=True)
        for column in payment_columns:
            payment_tree.heading(column, text=column)
            payment_tree.column(column, anchor="center")
        payment_tree.column("When", width=170)

        payment_lookup = {}

        def render_payments():
            receipt_local = self._sync_receipt_payment_state(receipt)
            payment_tree.delete(*payment_tree.get_children())
            payment_lookup.clear()
            for entry in self._receipt_payment_history(receipt_local):
                row_id = str(entry.get("payment_id"))
                payment_lookup[row_id] = entry
                payment_tree.insert(
                    "",
                    "end",
                    iid=row_id,
                    values=(
                        self._receipt_date_value({"Timestamp": entry.get("timestamp")}),
                        str(entry.get("payment_method", "cash")).replace("_", " ").title(),
                        f"${self._payment_entry_amount(entry):.2f}",
                        f"${self._receipt_number_value(entry.get('applied_customer_credit', 0)):.2f}",
                        f"${self._receipt_number_value(entry.get('credited_customer_overpayment', 0)):.2f}",
                    ),
                )

        render_payments()

        action_row = tk.Frame(wrap, bg=palette["card"])
        action_row.pack(fill="x")
        if self._receipt_document_type(receipt).lower() != "quotation":
            self._desktop_button(
                action_row,
                "Collect Payment",
                lambda: self._open_receipt_payment_window(receipt, parent=detail, on_update=lambda: (render_payments(), on_update() if on_update else None)),
                palette,
                kind="primary",
            ).pack(side="left")
        if self._is_superuser_role():
            def revert_selected():
                selected = payment_tree.focus()
                if not selected:
                    messagebox.showwarning("Select Payment", "Choose a payment entry to revert.", parent=detail)
                    return
                ok, result = self._revert_receipt_payment(receipt, selected)
                if not ok:
                    messagebox.showwarning("Cannot Revert", result, parent=detail)
                    return
                render_payments()
                if on_update:
                    on_update()
                messagebox.showinfo("Payment Reverted", "Selected payment was reverted successfully.", parent=detail)

            self._desktop_button(action_row, "Revert Selected Payment", revert_selected, palette, kind="danger").pack(side="left", padx=8)
        self._desktop_button(action_row, "Close", detail.destroy, palette, kind="subtle").pack(side="right")

    def _coerce_int(self, value):
        try:
            return int(value)
        except Exception:
            return None

    def _receipt_invoice_value(self, receipt, fallback=None):
        value = (
            receipt.get("Invoice Code")
            or receipt.get("Invoice No")
            or receipt.get("Invoice #")
            or receipt.get("receipt_no")
            or receipt.get("receipt_id")
            or fallback
        )
        return value

    def _receipt_datetime(self, receipt):
        raw = receipt.get("Timestamp") or receipt.get("Date") or ""
        if not raw:
            return None
        try:
            normalized = str(raw).replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is not None:
                return dt.astimezone()
            return dt
        except Exception:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(raw), fmt)
            except Exception:
                continue
        return None

    def _receipt_date_value(self, receipt):
        dt = self._receipt_datetime(receipt)
        if dt:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        return str(receipt.get("Date") or receipt.get("Timestamp") or "N/A")

    def _receipt_number_value(self, value):
        try:
            return float(value)
        except Exception:
            try:
                return float(str(value))
            except Exception:
                return 0.0

    def _receipt_items(self, receipt):
        items = receipt.get("Items Purchased")
        if isinstance(items, list):
            return items
        return []

    def _sale_to_receipt(self, sale):
        timestamp = sale.get("timestamp") or sale.get("Timestamp") or datetime.now(timezone.utc).isoformat()
        subtotal = self._receipt_number_value(sale.get("subtotal") or sale.get("Subtotal", 0))
        gct_amount = self._receipt_number_value(sale.get("gct_amount") or sale.get("GCT", 0))
        total_price = self._receipt_number_value(sale.get("total_price") or sale.get("Total Bill", 0))
        customer_name = self._normalize_customer_name(
            sale.get("customer_name")
            or sale.get("Customer Name")
            or ""
        )
        document_type = str(sale.get("document_type") or sale.get("Document Type") or "Invoice").strip() or "Invoice"
        
        tax_rate = 0.0
        if subtotal:
            try:
                tax_rate = max(0.0, gct_amount / subtotal)
            except Exception:
                tax_rate = 0.0
        elif self.system.settings.get("tax_rate") is not None:
            tax_rate = self._receipt_number_value(self.system.settings.get("tax_rate", 0))

        items_purchased = []
        # Support both 'items' list and old 'Items Purchased' list
        raw_items = sale.get("items") or sale.get("Items Purchased", [])
        
        for item in raw_items:
            # Fallback checking handles old UI format vs new backend schema
            sku = item.get("item_sku") or item.get("SKU") or ""
            name = item.get("item_name") or item.get("Name") or sku or "Item"
            quantity = self._coerce_int(item.get("quantity")) or self._coerce_int(item.get("Quantity")) or 0
            unit_price = self._receipt_number_value(item.get("unit_price") or item.get("Unit Price", 0))
            line_total = self._receipt_number_value(item.get("total_price") or item.get("Total", quantity * unit_price))
            
            items_purchased.append({
                "SKU": sku,
                "Name": name,
                "Quantity": quantity,
                "Unit Price": unit_price,
                "Total": round(line_total, 2),
            })

        return {
            "Invoice No": sale.get("receipt_no") or sale.get("Invoice No") or sale.get("id"),
            "Date": self._receipt_date_value({"Timestamp": timestamp}),
            "Timestamp": timestamp,
            "TaxLabel": self.system.settings.get("tax_label", "GCT"),
            "TaxRate": tax_rate,
            "Subtotal": round(subtotal, 2),
            "GCT": round(gct_amount, 2),
            "Total Bill": round(total_price, 2),
            "Items Purchased": items_purchased,
            "Customer Name": customer_name,
            "Document Type": document_type,
            "Source": "API",
            "Amount Paid": self._receipt_number_value(sale.get("amount_paid", total_price)),
            "Change Given": self._receipt_number_value(sale.get("change_due", 0)),
            "Payment Method": sale.get("tender") or sale.get("payment_method") or "cash",
        }

    def _merge_remote_sales_into_receipts(self, sales):
        if not sales:
            return 0

        existing = list(self.system.receipts or [])
        by_invoice = {}
        for idx, receipt in enumerate(existing):
            invoice_key = str(self._receipt_invoice_value(receipt, fallback=idx + 1))
            by_invoice[invoice_key] = idx

        merged_count = 0
        for sale in sales:
            receipt = self._sale_to_receipt(sale)
            invoice_key = str(self._receipt_invoice_value(receipt))
            if not invoice_key:
                continue
            if invoice_key in by_invoice:
                existing[by_invoice[invoice_key]] = receipt
            else:
                by_invoice[invoice_key] = len(existing)
                existing.append(receipt)
            merged_count += 1

        self.system.receipts = existing
        self.customers = self._rebuild_customer_registry()
        storage.save_data(
            self.system.inventory,
            self.system.receipts,
            self.system.settings
        )
        return merged_count

    def sync_receipt_history(self, callback=None):
        if getattr(self, "_receipt_sync_in_progress", False):
            if callback:
                self.root.after(300, callback)
            return
        self._receipt_sync_in_progress = True
        threading.Thread(target=self._sync_receipt_history_worker, args=(callback,), daemon=True).start()

    def _sync_receipt_history_worker(self, callback=None):
        try:
            if not self.api_token or not self.api_base_url:
                return
            params = {
                "client_id": "desktop-" + str(self.current_user_id) if self.current_user_id else "desktop-client"
            }
            resp = self._api_request("GET", "/api/sales/", params=params)
            if resp and resp.get("ok"):
                sales = resp.get("sales", [])
                if sales:
                    with self.data_lock:
                        self._merge_remote_sales_into_receipts(sales)
        finally:
            self._receipt_sync_in_progress = False
            if callback:
                self.root.after(0, callback)

    def _sanitize_db_quantity(self, value, clamp=True):
        try:
            quantity = int(value)
        except Exception:
            quantity = 0
        if quantity < 0:
            quantity = 0
        if quantity > MAX_DB_QUANTITY:
            print(f"⚠️ DATA INTEGRITY WARNING: Quantity {quantity} exceeds DB limit. Clamping to {MAX_DB_QUANTITY}.")
            if clamp:
                return MAX_DB_QUANTITY
            raise ValueError(f"Quantity must be between 0 and {MAX_DB_QUANTITY:,}.")
        return quantity

    def _category_sku_prefix(self, category):
        """
        Build a 3-char SKU prefix from category text.
        Example: 'Beverages' -> 'BEV', 'IT' -> 'ITX'
        """
        cleaned = re.sub(r"[^A-Za-z0-9]", "", str(category or "").upper())
        if not cleaned:
            return "GEN"
        return (cleaned[:3]).ljust(3, "X")

    def _country_name_from_code(self, code):
        if not code:
            return None
        country_map = {
            "JM": "Jamaica",
            "TT": "Trinidad & Tobago",
            "BB": "Barbados",
        }
        normalized = str(code).strip()
        if len(normalized) == 2:
            return country_map.get(normalized.upper())
        return normalized

    def _apply_location_tax(self, location_id=None):
        location_id = location_id if location_id is not None else self.active_location_id
        if not location_id:
            return
        match = next(
            (l for l in self.locations if self._coerce_int(l.get("id")) == self._coerce_int(location_id)),
            None,
        )
        if not match:
            return
        raw_country = match.get("country_code") or match.get("country") or ""
        country_name = self._country_name_from_code(raw_country) or self.system.settings.get("country", "Jamaica")
        self.system.settings["country"] = country_name
        self.system.auto_detect_tax()

    def _inventory_cache_path(self):
        if self.current_user_id:
            return os.path.join(DATA_DIR, f"inventory_cache_{self.current_user_id}.json")
        return LOCAL_CACHE_FILE

    def _pending_queue_path(self):
        current_user_id = getattr(self, "current_user_id", None)
        if current_user_id:
            return os.path.join(DATA_DIR, f"pending_sync_{current_user_id}.json")
        return PENDING_QUEUE_FILE

    def _get_queue_lock(self):
        lock = getattr(self, "_queue_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._queue_lock = lock
        return lock

    def _queue_timestamp(self):
        return datetime.now(timezone.utc).isoformat()

    def _queue_client_id(self):
        current_user_id = getattr(self, "current_user_id", None)
        if current_user_id:
            return f"desktop-{current_user_id}"
        return "desktop-unknown"

    def _parse_queue_timestamp(self, value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            return None

    def _upgrade_pending_item(self, item):
        """Upgrade one legacy queue entry without changing its business payload."""
        if not isinstance(item, dict):
            item = {"action": "UNKNOWN", "payload": item}
        upgraded = dict(item)
        action = str(upgraded.get("action") or "UNKNOWN").strip().upper()
        upgraded["action"] = action
        upgraded.setdefault("queued_at", self._queue_timestamp())

        metadata = upgraded.get("_sync")
        if not isinstance(metadata, dict):
            metadata = {}
        else:
            metadata = dict(metadata)

        state = str(metadata.get("state") or "pending").strip().lower()
        if state not in QUEUE_ACTIVE_STATES | QUEUE_TERMINAL_STATES:
            state = "pending"
        try:
            attempts = max(0, int(metadata.get("attempts", 0)))
        except (TypeError, ValueError):
            attempts = 0
        try:
            payload_version = max(1, int(metadata.get("payload_version") or 1))
        except (TypeError, ValueError):
            payload_version = 1
        current_user_id = getattr(self, "current_user_id", None)
        active_location_id = getattr(self, "active_location_id", None)
        default_location_id = getattr(self, "default_location_id", None)

        metadata.update(
            {
                "schema_version": QUEUE_SCHEMA_VERSION,
                "payload_version": payload_version,
                "action_id": str(metadata.get("action_id") or f"QUEUE-{uuid.uuid4()}"),
                "client_id": str(metadata.get("client_id") or self._queue_client_id()),
                "operator_user_id": metadata.get("operator_user_id") or current_user_id,
                "location_id": metadata.get("location_id")
                or upgraded.get("location_id")
                or active_location_id
                or default_location_id,
                "state": state,
                "attempts": attempts,
                "created_at": str(metadata.get("created_at") or upgraded["queued_at"]),
                "last_attempt_at": metadata.get("last_attempt_at"),
                "next_attempt_at": metadata.get("next_attempt_at"),
                "last_error": metadata.get("last_error"),
            }
        )

        if action == "RECORD_SALE" and not upgraded.get("offline_client_ref"):
            upgraded["offline_client_ref"] = f"DESKTOP-{uuid.uuid4()}"

        active_action_ids = getattr(self, "_active_queue_action_ids", set())
        if metadata["state"] == "in_flight":
            last_attempt = self._parse_queue_timestamp(metadata.get("last_attempt_at"))
            age_seconds = None
            if last_attempt:
                age_seconds = (datetime.now(timezone.utc) - last_attempt).total_seconds()
            action_is_active = metadata["action_id"] in active_action_ids
            if not action_is_active or age_seconds is None or age_seconds >= QUEUE_IN_FLIGHT_TIMEOUT_SECONDS:
                metadata["state"] = "retry"
                metadata["next_attempt_at"] = None
                metadata["last_error"] = "Replay was interrupted before acknowledgement."

        upgraded["_sync"] = metadata
        return upgraded

    def _backup_legacy_queue(self, path):
        backup_path = f"{path}.v0.bak"
        if os.path.exists(path) and not os.path.exists(backup_path):
            shutil.copy2(path, backup_path)
        return backup_path

    def _preserve_corrupt_queue(self, path, exc):
        source = Path(path)
        existing_backups = list(source.parent.glob(f"{source.name}.corrupt-*"))
        backup_path = str(existing_backups[0]) if existing_backups else None
        if backup_path is None:
            try:
                version = source.stat().st_mtime_ns
            except OSError:
                version = int(time.time() * 1_000_000_000)
            backup_path = f"{path}.corrupt-{version}"
            try:
                shutil.copy2(path, backup_path)
            except OSError as backup_exc:
                logger.error(f"Could not preserve corrupt queue {path}: {backup_exc}")
                backup_path = None
        self._queue_corruption_info = {
            "path": path,
            "backup_path": backup_path,
            "error": str(exc),
        }
        logger.error(
            "Pending queue is corrupt and has been blocked from overwrite: %s",
            path,
        )

    def _read_pending_queue(self):
        with self._get_queue_lock():
            queue_path = self._pending_queue_path()
            candidate_paths = [queue_path]
            if queue_path != PENDING_QUEUE_FILE:
                candidate_paths.append(PENDING_QUEUE_FILE)

            for path in candidate_paths:
                if not os.path.exists(path):
                    continue
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        queue = json.load(f)
                    if not isinstance(queue, list):
                        raise ValueError("Pending queue root must be a JSON list.")
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    self._preserve_corrupt_queue(path, exc)
                    return []

                upgraded_queue = [self._upgrade_pending_item(item) for item in queue]
                changed = upgraded_queue != queue
                if changed:
                    self._backup_legacy_queue(path)

                self._queue_corruption_info = None
                if path != queue_path or changed:
                    self._write_pending_queue(upgraded_queue)
                    if path != queue_path:
                        try:
                            os.remove(path)
                        except OSError:
                            pass
                return upgraded_queue

            self._queue_corruption_info = None
            return []
    
    def _token_file_path(self):
        safe_name = (self.current_username or "default").replace("/", "_")
        return os.path.join(DATA_DIR, f".token_{safe_name}")

    def _write_token_file(self, token):
        import base64
        try:
            encoded = base64.b64encode(token.encode()).decode()
            token_path = self._token_file_path()
            self._write_json_atomic(token_path, {"t": encoded})
            try:
                os.chmod(token_path, 0o600)
            except OSError:
                logger.warning("Could not restrict token fallback file permissions.")
        except Exception as e:
            logger.error(f"Token file write error: {e}")

    def _read_token_file(self):
        import base64
        path = self._token_file_path()
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return base64.b64decode(data.get("t", "")).decode()
        except Exception:
            return None

    def _delete_token_file(self):
        path = self._token_file_path()
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    def save_api_token(self, token):
        self.api_token = token if token else None
        # Keep SyncService in sync if it exists
        if hasattr(self, 'sync_service') and self.sync_service:
            self.sync_service.api_token = self.api_token
        if not token or not self.current_username:
            self.delete_api_token()
            return
        if keyring:
            try:
                keyring.set_password("QuickStockJA", self.current_username, token)
                return
            except Exception as e:
                logger.warning(f"Keyring unavailable ({e}). Using file fallback.")
        self._write_token_file(token)

    def load_api_token(self):
        if not self.current_username:
            return self.api_token
        if keyring:
            try:
                token = keyring.get_password("QuickStockJA", self.current_username)
                if token:
                    self.api_token = token
                    return self.api_token
            except Exception as e:
                logger.warning(f"Keyring read failed ({e}). Trying file fallback.")
        token = self._read_token_file()
        self.api_token = token
        return self.api_token

    def _get_db_connection(self):
        """DEPRECATED: Direct database access has been removed for security.
        All database operations now go through the REST API.
        This method is kept for backward compatibility but will raise an error.
        """
        logger.error("Direct database access is no longer supported. Use API methods instead.")
        raise RuntimeError(
            "Direct database access has been removed for security. "
            "Please configure API_BASE_URL and authenticate to use the system."
        )

    def delete_api_token(self):
        """Remove token from keyring and file fallback."""
        if keyring and self.current_username:
            try:
                keyring.delete_password("QuickStockJA", self.current_username)
            except Exception:
                pass
        self._delete_token_file()
        self.api_token = None
        # Clear SyncService token too
        if hasattr(self, 'sync_service') and self.sync_service:
            self.sync_service.api_token = None

    def _api_request(self, method, path, payload=None, params=None):
        """Hardened API request using requests library and secure token handling."""
        if not self.api_base_url:
            return None

        # System-wide circuit breaker: block all network activity during cooldown after a backend crash
        if time.time() - self.last_server_error_time < self.server_cooldown:
            return None

        url = build_api_url(self.api_base_url, path)

        if requires_https_in_production(self.api_base_url):
            logger.error(f"Blocked insecure connection attempt to {url}")
            self.api_status = "INSECURE_CONNECTION"
            return None

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "QuickStockJA-Desktop/1.0",
            "X-Client-ID": f"desktop-{self.current_user_id}" if self.current_user_id else "desktop-unknown"
            
        }
        
        # DRF TokenAuthentication expects: Authorization: Token <key>
        # A Bearer header logs in successfully but fails every authenticated API call.
        token = self.api_token
        if token and path != "/api/login/":
            headers["Authorization"] = f"Token {token}"

        try:
            response = requests.request(
                method=method,
                url=url,
                json=payload,
                params=params,
                headers=headers,
                timeout=10, 
                verify=True
            )
            
            # Handle authentication errors specifically
            if response.status_code == 401:
                # This is a critical path for authentication state management
                with self._session_lock:
                    if path == "/api/login/":
                        self.api_status = "AUTH_FAILED"
                        return {"ok": False, "message": "Invalid username or password."}
                    else: # 401 from an authenticated endpoint
                        self.api_status = "TOKEN_EXPIRED"
                        self.delete_api_token()
                        logger.warning(f"Session expired or invalid token during {path}. Transitioning to offline mode.")
                return None
                
            # Handle 500 errors - Update status and trigger cooldown
            if response.status_code >= 500:
                self.api_status = "SERVER_ERROR"
                self.last_server_error_time = time.time()
                logger.error(f"Backend Crash ({response.status_code}) at {path}. Entering {self.server_cooldown}s cooldown.")
                return None

            if response.status_code == 413:
                logger.error("Payload too large for server. Reducing sync batch size.")
                messagebox.showwarning("Sync Warning", "The data batch is too large. Try syncing in smaller groups.")
                return None

            response.raise_for_status()
            self.api_status = "ONLINE" # If successful, we are online
            return response.json()

        except requests.exceptions.SSLError:
            logger.error("SSL Verification Failed! Potential security risk.")
            self.api_status = "SSL_ERROR"
            return None
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Network unreachable: {e}. Transitioning to offline mode.")
            self.api_status = "SERVER_DOWN"
            # Do not set _is_offline here, refresh_status will handle it based on api_status
            # self._is_offline = True 
            return None
        except requests.exceptions.RequestException as e:
            if "404" in str(e) or "Not Found" in str(e):
                pass  # Missing endpoints are expected — not an error
            elif not any(err in str(e) for err in ["Connection", "Timeout", "unreachable"]):
                logger.error(f"API Request failed: {e}")
            elif not getattr(self, '_is_offline', False):
                logger.warning(f"API Request failed (non-critical): {e}")
                    

    def sync_reference_data(self, callback=None):
        """Sync locations, suppliers, and categories from MySQL in a background thread."""
        threading.Thread(target=self._sync_reference_data_worker, args=(callback,), daemon=True).start()

    def _sync_reference_data_worker(self, callback=None):
        self.root.after(0, lambda: self.refresh_status(connected=not getattr(self, '_is_offline', False), syncing=True))

        if not self.api_token:
            self.locations = self._load_cache(LOCATION_CACHE_FILE)
            self.suppliers = self._load_cache(SUPPLIER_CACHE_FILE)
            self.categories = self._load_cache(os.path.join(DATA_DIR, f"category_cache_{self.current_user_id}.json"))
            self.root.after(0, lambda: self.refresh_status(connected=False, syncing=False))
            if callback: self.root.after(0, callback)
            return

        try:
            # Optimized: One combined call to fetch all metadata (locations, suppliers, categories)
            ref_resp = self._api_request("GET", "/api/sync/reference/")
            if ref_resp and ref_resp.get("ok"):
                self.locations = ref_resp.get("locations", [])
                self._save_cache(LOCATION_CACHE_FILE, self.locations)

                self.suppliers = ref_resp.get("suppliers", [])
                self._save_cache(SUPPLIER_CACHE_FILE, self.suppliers)

                self.categories = ref_resp.get("categories", [])
                self._save_cache(os.path.join(DATA_DIR, f"category_cache_{self.current_user_id}.json"), self.categories)

                self.customers = ref_resp.get("customers", [])
                if self.customers:
                    self._save_customer_registry(self.customers)

                self._apply_active_register_snapshot(ref_resp.get("active_register"))

            self.root.after(0, lambda: self.refresh_status(connected=True, syncing=False))
        except Exception as e:
            logger.error(f"Reference data sync error: {e}")
            self.root.after(0, lambda: self.refresh_status(connected=False, syncing=False))
        finally:
            if callback: self.root.after(0, callback)

    def _set_default_active_location(self):
        selectable_locations = self._selectable_locations()
        match = None
        if self.default_location_id:
            default_id = self._coerce_int(self.default_location_id)
            match = next(
                (l for l in selectable_locations if self._coerce_int(l.get("id")) == default_id),
                None,
            )
        if match:
            self.active_location_id = self._coerce_int(match.get("id"))
            self.active_location_name = match.get("name", "All Locations")
            self._apply_location_tax(self.active_location_id)
            return
        if selectable_locations:
            self.active_location_id = self._coerce_int(selectable_locations[0].get("id"))
            self.active_location_name = selectable_locations[0].get("name", "All Locations")
            self._apply_location_tax(self.active_location_id)
        else:
            self.active_location_id = None
            self.active_location_name = "All Locations"

    def set_active_location(self, location_id):
        self.active_location_id = location_id
        match = next((l for l in self.locations if self._coerce_int(l.get("id")) == self._coerce_int(location_id)), None)
        self.active_location_name = match.get("name", "All Locations") if match else "All Locations"
        self._apply_location_tax(location_id)
        self.sync_inventory()
        if hasattr(self, "update_dashboard_stats"):
            self.update_dashboard_stats()

    def ensure_business_profile_for_sale(self):
        """Validates business details and Jamaican TRN (9 digits) before allowing sales."""
        if self.business_trn and re.match(r"^\d{9}$", self.business_trn):
            return True
            
        trn = simpledialog.askstring(
            "Business TRN Required",
            "Please enter your 9-digit Jamaican TRN:",
            parent=self.root,
        )
        if not trn:
            return False
            
        cleaned_trn = trn.strip().replace("-", "")
        if not re.match(r"^\d{9}$", cleaned_trn):
            messagebox.showerror("Invalid TRN", "Jamaican TRN must be exactly 9 digits.")
            return False
            
        self.business_trn = cleaned_trn
        self.system.set_business_profile(self.business_name, self.business_trn)
        self._save_business_profile()
        return True

    def business_info_window(self):
        win = tk.Toplevel(self.root)
        palette, content = self._desktop_form_shell(win, "Business Info", "440x560")

        self._desktop_label(content, "Business Info", palette, surface="card", role="title", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        self._desktop_label(content, "Keep local receipt identity aligned with the online workspace.", palette, surface="card", role="muted", wraplength=360, justify="left").pack(anchor="w", pady=(4, 16))

        self._desktop_label(content, "Business Name", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        name_entry = self._desktop_entry(content, palette)
        name_entry.pack(fill="x", pady=(5, 12), ipady=8)
        name_entry.insert(0, self.business_name or "")

        self._desktop_label(content, "Business TRN", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        trn_entry = self._desktop_entry(content, palette)
        trn_entry.pack(fill="x", pady=(5, 12), ipady=8)
        trn_entry.insert(0, self.business_trn or "")

        self._desktop_label(content, "API Base URL", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        api_entry = self._desktop_entry(content, palette)
        api_entry.pack(fill="x", pady=(5, 12), ipady=8)
        api_entry.insert(0, self.api_base_url or API_BASE_URL_DEFAULT)

        self._desktop_label(content, "API Token (Bearer)", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        token_entry = self._desktop_entry(content, palette, show="*")
        token_entry.pack(fill="x", pady=(5, 12), ipady=8)
        token_entry.insert(0, self.api_token or "")

        self._desktop_label(content, "Receipt Email", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        email_entry = self._desktop_entry(content, palette)
        email_entry.pack(fill="x", pady=(5, 12), ipady=8)
        email_entry.insert(0, self.system.settings.get("brand_email", ""))

        self._desktop_label(content, "Receipt Phone", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        phone_entry = self._desktop_entry(content, palette)
        phone_entry.pack(fill="x", pady=(5, 14), ipady=8)
        phone_entry.insert(0, self.system.settings.get("brand_phone", ""))

        def save_profile():
            api_base_url = api_entry.get().strip() or API_BASE_URL_DEFAULT
            is_valid_url, error_message = validate_api_base_url(api_base_url)
            if not is_valid_url:
                messagebox.showerror("Error", error_message)
                return

            self.business_name = name_entry.get().strip() or "Your Business"
            self.business_trn = trn_entry.get().strip()
            self.api_base_url = api_base_url.rstrip("/")
            self.api_token = token_entry.get().strip() or None
            self.system.settings["brand_email"] = email_entry.get().strip()
            self.system.settings["brand_phone"] = phone_entry.get().strip()
            self.system.set_business_profile(self.business_name, self.business_trn)
            self._persist_offline_settings()
            messagebox.showinfo("Saved", "Business profile updated.")
            win.destroy()

        self._desktop_button(content, "Save", save_profile, palette, kind="primary").pack(fill="x", pady=(4, 0))

    def sync_settings_from_online_profile(self):
        """Sync user settings from the online profile via API."""
        if not self.current_user_id:
            return

        if not self.api_token:
            return

        resp = self._api_request("GET", "/api/profile/")
        if resp and resp.get("ok"):
            user_info = resp.get("user") or {}
            # In a production environment, the profile endpoint would return
            # the full brand/theme settings.
            self.current_role = user_info.get("role", self.current_role)
            self.default_location_id = self._coerce_int(user_info.get("default_location_id"))
            self._apply_theme_preference()

    def _action_permissions(self):
        return {
            "superuser": [
                "Cash Register", "Add Item", "View Inventory", "Check Stock",
                "Receive Stock", "Transfer Stock",
                "Global Search", "Sales Summary", "Receipt History", "Customer Directory",
                "Export Sales (Excel)", "Export Inventory (CSV)", "Export Sales (CSV)",
                "Business Info", "Toggle Theme", "Select Location", "Suppliers",
                "User Management", "Users",
                "Tax Settings",
                "Sync Pending", "Sync Status",
                "Super Admin Console", "Super Admin Dashboard", "Django Admin",
                "Tenant Profiles", "Online User Registry",
                "Online Workspace", "Online Dashboard", "Locations / Network",
                "Audit Logs", "Sales Invoices", "Sales Quotations", "Import Items",
                "Support Center", "Pricing / Upgrade",
            ],
            "admin": [
                "Cash Register", "Add Item", "View Inventory", "Check Stock",
                "Receive Stock", "Transfer Stock",
                "Global Search", "Sales Summary", "Receipt History", "Customer Directory",
                "Export Sales (Excel)", "Export Inventory (CSV)", "Export Sales (CSV)",
                "Business Info", "Toggle Theme", "Select Location", "Suppliers",
                "User Management", "Users",
                "Tax Settings",
                "Sync Pending", "Sync Status",
                "Online Workspace", "Online Dashboard", "Locations / Network",
                "Audit Logs", "Sales Invoices", "Sales Quotations", "Import Items",
                "Support Center", "Pricing / Upgrade",
            ],
            "manager": [
                "Cash Register", "Add Item", "View Inventory", "Check Stock",
                "Receive Stock", "Transfer Stock",
                "Global Search", "Receipt History", "Customer Directory",
                "Business Info", "Toggle Theme", "Select Location", "Suppliers",
                "Tax Settings", "Sync Status",
                "Online Workspace", "Online Dashboard", "Locations / Network",
                "Audit Logs", "Sales Invoices", "Sales Quotations",
                "Support Center",
            ],
            "cashier": [
                "Cash Register", "View Inventory", "Global Search", "Check Stock",
                "Select Location", "Toggle Theme", "Sync Status",
                "Online Workspace", "Online Dashboard", "Support Center",
            ],
        }

    def _open_menu_window(self, title, actions):
        role = self.current_role.lower() if self.current_role else "cashier"
        allowed = set(self._action_permissions().get(role, self._action_permissions().get("admin", [])))
        palette = self._desktop_window_palette()

        win = ctk.CTkToplevel(self.root)
        win.title(title)
        win.geometry("440x560")
        win.configure(fg_color=palette["menu_bg"])
        win.transient(self.root)

        # wait until window is visible
        def _safe_grab():
            try:
                win.update_idletasks()
                win.lift()
                win.focus_force()
                win.grab_set()
            except Exception:
                pass
        win.after(150, _safe_grab)

        container = ctk.CTkFrame(win, fg_color=palette["menu_bg"])
        container.pack(fill="both", expand=True, padx=20, pady=20)

        visible_count = sum(1 for label, _command in actions if label in allowed)

        ctk.CTkLabel(
            container,
            text=title,
            font=("Segoe UI", 22, "bold"),
            text_color=palette["header_title"],
            anchor="w",
        ).pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            container,
            text=f"{visible_count} available action(s) for {role.title()} access",
            font=("Segoe UI", 12),
            text_color=palette["header_muted"],
            anchor="w",
        ).pack(fill="x", pady=(0, 16))

        for label, command in actions:
            is_allowed = label in allowed
            btn = ctk.CTkButton(
                container,
                text=label if is_allowed else f"{label}  |  Unavailable",
                command=(lambda c=command, w=win: [w.destroy(), c()]) if is_allowed else None,
                height=44,
                corner_radius=8,
                fg_color=palette["menu_button"] if is_allowed else "#233247",
                hover_color=palette["menu_button_active"] if is_allowed else "#233247",
                text_color=palette["header_title"] if is_allowed else "#6F89B2",
                border_width=1,
                border_color="#2F455E" if is_allowed else "#26364F",
                font=("Segoe UI", 12, "bold"),
                state="normal" if is_allowed else "disabled",
            )
            btn.pack(fill="x", pady=6)

    def open_operations_menu(self):
        actions = [
            ("Cash Register", self.open_cash_register),
            ("Add Item", self.add_item_window),
            ("Receive Stock", self.receive_stock_window),
            ("Transfer Stock", self.transfer_stock_window),
            ("Select Location", self.location_selector_window)
        ]
        self._open_menu_window("Operations", actions)

    def open_reports_menu(self):
        actions = [
            ("Global Search", self.global_search_window),
            ("View Inventory", self.view_inventory),
            ("Sales Summary", self.sales_summary_window),
            ("Receipt History", self.receipt_history_window),
            ("Customer Directory", self.customer_directory_window),
            ("Sync Status", self.online_sync_status_window),
            ("Export Inventory (CSV)", self.export_inventory_csv),
        ]
        self._open_menu_window("Reports", actions)

    def open_settings_menu(self):
        actions = [
            ("System Preferences", self.system_preferences_window),
            ("Business Info", self.business_info_window),
            ("Select Location", self.location_selector_window),
            ("Suppliers", self.suppliers_window),
            ("Tax Settings", self.tax_settings_window),
            ("Toggle Theme", self.toggle_theme),
            ("Super Admin Console", self.superuser_console_window),
            ("User Management", self.open_users_workspace),
            ("Sync Pending", self.sync_offline_queue),
            ("Online Workspace", self.open_online_workspace),
        ]
        self._open_menu_window("Settings", actions)

    def _web_base_url(self):
        base = (self.api_base_url or API_BASE_URL_DEFAULT).strip().rstrip("/")
        if base.endswith("/api"):
            base = base[:-4]
        return base or API_BASE_URL_DEFAULT

    def _open_online_page(self, path):
        webbrowser.open_new_tab(f"{self._web_base_url()}/{path.lstrip('/')}")

    def open_users_workspace(self):
        if self._is_platform_superuser():
            self.superuser_console_window()
            return
        self.manage_users_window()

    def open_online_workspace(self):
        actions = [
            ("Super Admin Dashboard", lambda: self._open_online_page("/super-admin-dashboard/")),
            ("Django Admin", lambda: self._open_online_page("/admin/")),
            ("Online User Registry", lambda: self._open_online_page("/admin/auth/user/")),
            ("Tenant Profiles", lambda: self._open_online_page("/admin/inventory/userprofile/")),
            ("Online Dashboard", lambda: self._open_online_page("/dashboard/")),
            ("Locations / Network", lambda: self._open_online_page("/locations/")),
            ("Audit Logs", lambda: self._open_online_page("/audit-logs/")),
            ("Sales Invoices", lambda: self._open_online_page("/sales/invoices/")),
            ("Sales Quotations", lambda: self._open_online_page("/sales/quotations/")),
            ("Import Items", lambda: self._open_online_page("/import/items/")),
            ("Support Center", lambda: self._open_online_page("/support/")),
            ("Pricing / Upgrade", lambda: self._open_online_page("/pricing/")),
            ("Sync Status", self.online_sync_status_window),
        ]
        self._open_menu_window("Online Workspace", actions)

    def online_sync_status_window(self):
        win = tk.Toplevel(self.root)
        palette, shell, body = self._build_desktop_surface(
            win,
            "Online Sync Status",
            "Live sync health from the QuickStock web API.",
            geometry="640x520",
        )

        card = self._build_desktop_card(body, palette)
        card.pack(fill="both", expand=True)
        status_text = tk.Text(
            card,
            height=16,
            wrap="word",
            bg=palette["input_bg"],
            fg=palette["input_fg"],
            insertbackground=palette["input_fg"],
            relief="flat",
            bd=0,
            font=("Segoe UI", 10),
        )
        status_text.pack(fill="both", expand=True)

        def render(message):
            status_text.configure(state="normal")
            status_text.delete("1.0", tk.END)
            status_text.insert("end", message)
            status_text.configure(state="disabled")

        def refresh():
            if not self.api_token:
                if self._is_platform_superuser():
                    snapshot = self._load_superuser_sync_snapshot()
                    render(
                        self._build_superuser_sync_status_message(
                            "Offline superuser mode is showing local sync health because there is no active web session.",
                            snapshot=snapshot if snapshot else None,
                        )
                    )
                else:
                    render("Login online first to view sync status.")
                return

            render("Loading sync status...")
            def worker():
                resp = self._api_request("GET", "/api/sync/status/")
                if not resp:
                    if self._is_platform_superuser():
                        snapshot = self._load_superuser_sync_snapshot()
                        message = self._build_superuser_sync_status_message(
                            "Could not reach the online sync status API. Showing cached platform sync data and local queue health instead.",
                            snapshot=snapshot if snapshot else None,
                        )
                    else:
                        message = "Could not reach the online sync status API. Check your connection and login session."
                else:
                    status = resp.get("status") or {}
                    recent = resp.get("recent_syncs") or []
                    if self._is_platform_superuser():
                        self._save_superuser_sync_snapshot(status, recent)
                    lines = [
                        f"Last inventory sync: {status.get('last_inventory_sync') or 'Not synced yet'}",
                        f"Last sales sync: {status.get('last_sales_sync') or 'Not synced yet'}",
                        f"Pending syncs: {status.get('pending_syncs', 0)}",
                        f"Items needing sync: {status.get('items_needing_sync', 0)}",
                        f"Unsynced sales: {status.get('unsynced_sales', 0)}",
                        "",
                        "Recent sync activity:",
                    ]
                    if recent:
                        for item in recent[:10]:
                            lines.append(
                                f"- {item.get('sync_type') or 'sync'} / {item.get('direction') or 'n/a'}: "
                                f"{item.get('status') or 'unknown'} "
                                f"(pulled {item.get('items_pulled') or 0}, pushed {item.get('items_pushed') or 0})"
                            )
                    else:
                        lines.append("- No recent sync records.")
                    message = "\n".join(lines)
                self.root.after(0, lambda: render(message))

            threading.Thread(target=worker, daemon=True).start()

        buttons = tk.Frame(body, bg=palette["bg"])
        buttons.pack(fill="x", pady=(12, 0))
        self._desktop_button(buttons, "Refresh", refresh, palette, kind="primary").pack(side="left", padx=(0, 8))
        self._desktop_button(buttons, "Open Web Dashboard", lambda: self._open_online_page("/dashboard/"), palette, kind="accent").pack(side="left")

        refresh()

    def system_preferences_window(self):
        win = tk.Toplevel(self.root)
        win.title("System Preferences")
        win.geometry("520x760")
        win.transient(self.root)
        palette = self._style_desktop_window(win)

        canvas = tk.Canvas(win, highlightthickness=0, bg=palette["bg"])
        scrollbar = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas, padx=16, pady=16, bg=palette["bg"])
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def add_label(text, pady=(10, 0)):
            self._desktop_label(content, text, palette, surface="bg", role="title", font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", pady=pady)

        def add_entry(variable, width=52):
            entry = self._desktop_entry(content, palette, textvariable=variable, width=width)
            entry.pack(pady=4, fill="x", ipady=8)
            return entry

        self._desktop_label(content, "Offline Settings Sync", palette, surface="bg", role="title", font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(0, 8))
        self._desktop_label(
            content,
            "These fields mirror the website settings and are saved locally for offline use.",
            palette,
            surface="bg",
            role="muted",
            justify="left",
            wraplength=460,
        ).pack(anchor="w", pady=(0, 12))

        theme_var = tk.StringVar(value=self.system.settings.get("theme", "system"))
        pos_var = tk.StringVar(value=self.system.settings.get("pos_config", ""))
        brand_name_var = tk.StringVar(value=self.business_name or self.system.settings.get("brand_name", ""))
        brand_logo_url_var = tk.StringVar(value=self.system.settings.get("brand_logo_url", ""))
        brand_logo_file_var = tk.StringVar(value=self.system.settings.get("brand_logo", ""))
        brand_email_var = tk.StringVar(value=self.system.settings.get("brand_email", ""))
        brand_phone_var = tk.StringVar(value=self.system.settings.get("brand_phone", ""))
        brand_address_var = tk.StringVar(value=self.system.settings.get("brand_address", ""))
        country_var = tk.StringVar(value=self.system.settings.get("country", "Jamaica"))
        tax_rate_var = tk.StringVar(value=f"{float(self.system.settings.get('tax_rate', 0.15)) * 100:.2f}")
        tax_inclusive_var = tk.BooleanVar(value=self.system.settings.get("tax_inclusive", False))
        audit_retention_var = tk.StringVar(value=str(self.system.settings.get("audit_retention_days", 90)))
        role_display = (self.current_role or "cashier").upper()

        location_names = [loc.get("name", "") for loc in self.locations if loc.get("name")]
        current_funding_name = self.system.settings.get("primary_funding_branch_name") or self.active_location_name
        funding_var = tk.StringVar(value=current_funding_name if current_funding_name in location_names else (location_names[0] if location_names else ""))

        add_label("My Access Level", pady=(0, 0))
        access_entry = self._desktop_entry(content, palette, width=52)
        access_entry.insert(0, role_display)
        access_entry.configure(state="readonly")
        access_entry.pack(pady=4, fill="x", ipady=8)

        add_label("System Theme")
        theme_combo = ttk.Combobox(content, textvariable=theme_var, values=["system", "light", "dark"], state="readonly")
        theme_combo.pack(pady=4, fill="x")

        add_label("POS Terminal Config")
        add_entry(pos_var)

        add_label("Receipt / Invoice Brand Name")
        add_entry(brand_name_var)

        add_label("Brand Logo URL")
        add_entry(brand_logo_url_var)

        add_label("Upload Logo / Local Logo Path")
        logo_row = tk.Frame(content, bg=palette["bg"])
        logo_row.pack(fill="x", pady=4)
        self._desktop_entry(logo_row, palette, textvariable=brand_logo_file_var, width=40).pack(side="left", fill="x", expand=True, ipady=8)

        def browse_logo():
            chosen = filedialog.askopenfilename(
                title="Select Logo",
                filetypes=[("Image Files", "*.png *.jpg *.jpeg *.gif"), ("All Files", "*.*")],
            )
            if chosen:
                brand_logo_file_var.set(chosen)

        self._desktop_button(logo_row, "Browse", browse_logo, palette, kind="subtle").pack(side="left", padx=(8, 0))

        add_label("Receipt Contact Email")
        add_entry(brand_email_var)

        add_label("Receipt Contact Phone")
        add_entry(brand_phone_var)

        add_label("Receipt Address")
        add_entry(brand_address_var)

        add_label("Country / Region")
        country_combo = ttk.Combobox(
            content,
            textvariable=country_var,
            values=["Jamaica", "Trinidad & Tobago", "Barbados", "Guyana", "Saint Lucia", "International"],
            state="readonly",
        )
        country_combo.pack(pady=4, fill="x")

        add_label("Tax Rate (%)")
        add_entry(tax_rate_var)

        tk.Checkbutton(
            content,
            text="Prices are Tax Inclusive",
            variable=tax_inclusive_var,
            font=("Segoe UI", 10),
            bg=palette["bg"],
            fg=palette["text"],
            activebackground=palette["bg"],
            activeforeground=palette["text"],
            selectcolor=palette["input_bg"],
        ).pack(anchor="w", pady=10)

        add_label("Audit Retention (Days)")
        add_entry(audit_retention_var)

        add_label("Primary Funding Branch")
        if location_names:
            funding_combo = ttk.Combobox(content, textvariable=funding_var, values=location_names, state="readonly")
            funding_combo.pack(pady=4, fill="x")
        else:
            funding_entry = self._desktop_entry(content, palette, textvariable=funding_var, width=52)
            funding_entry.pack(pady=4, fill="x", ipady=8)

        def refresh_tax_from_country(*_args):
            selected = country_var.get().strip() or "Jamaica"
            mapping = CARIBBEAN_TAX_MAP.get(selected)
            if mapping:
                tax_rate_var.set(f"{mapping['rate'] * 100:.2f}")

        country_combo.bind("<<ComboboxSelected>>", refresh_tax_from_country)

        def save_preferences():
            self.business_name = brand_name_var.get().strip() or "Your Business"
            self.system.set_business_profile(self.business_name, self.business_trn)
            self.system.settings["theme"] = theme_var.get().strip() or "system"
            self.system.settings["pos_config"] = pos_var.get().strip()
            self.system.settings["brand_name"] = self.business_name
            self.system.settings["brand_logo_url"] = brand_logo_url_var.get().strip()
            self.system.settings["brand_logo"] = brand_logo_file_var.get().strip() or self.system.settings["brand_logo_url"]
            self.system.settings["brand_email"] = brand_email_var.get().strip()
            self.system.settings["brand_phone"] = brand_phone_var.get().strip()
            self.system.settings["brand_address"] = brand_address_var.get().strip()
            self.system.settings["country"] = country_var.get().strip() or "Jamaica"
            self.system.settings["tax_inclusive"] = tax_inclusive_var.get()
            try:
                self.system.settings["tax_rate"] = max(0.0, float(tax_rate_var.get().strip()) / 100.0)
            except Exception:
                self.system.auto_detect_tax()
            else:
                mapped = CARIBBEAN_TAX_MAP.get(self.system.settings["country"])
                self.system.settings["tax_label"] = mapped["label"] if mapped else self.system.settings.get("tax_label", "TAX")
            try:
                self.system.settings["audit_retention_days"] = max(1, int(audit_retention_var.get().strip()))
            except Exception:
                self.system.settings["audit_retention_days"] = 90

            selected_funding_name = funding_var.get().strip()
            selected_funding = next((loc for loc in self.locations if loc.get("name") == selected_funding_name), None)
            self.system.settings["primary_funding_branch_id"] = selected_funding.get("id") if selected_funding else None
            self.system.settings["primary_funding_branch_name"] = selected_funding_name

            self._apply_theme_preference()
            self.apply_regional_tax()
            self._persist_offline_settings()
            self._update_home_dashboard_widgets()
            messagebox.showinfo("Saved", "Offline settings synced successfully.")
            win.destroy()

        footer = tk.Frame(content, bg=palette["bg"])
        footer.pack(fill="x", pady=(18, 0))
        self._desktop_button(footer, "Save Preferences", save_preferences, palette, kind="primary").pack(side="left", fill="x", expand=True)
        self._desktop_button(footer, "Close", win.destroy, palette, kind="subtle").pack(side="left", fill="x", expand=True, padx=(8, 0))

    def superuser_console_window(self):
        """Platform-owner console for online admin links and local sync health."""
        if not self._is_platform_superuser():
            self.manage_users_window()
            return

        win = tk.Toplevel(self.root)
        palette, shell, body = self._build_desktop_surface(
            win,
            "Platform Superuser Console",
            "A desktop launchpad for tenant oversight, Django admin tools, and offline sync health.",
            geometry="1040x680",
        )
        win.transient(self.root)

        queued_actions = len(self._read_pending_queue())
        local_items = len(self.system.inventory or [])
        registry_snapshot = self._load_superuser_registry_snapshot()
        registry_summary = self._registry_snapshot_summary(registry_snapshot) if registry_snapshot else {
            "users": 0,
            "companies": 0,
            "updated_at": None,
        }
        api_state = str(self.api_status or "UNKNOWN").replace("_", " ").title()
        online_state = "online" if self.api_token and not getattr(self, "_is_offline", False) else "offline"

        banner = self._build_desktop_banner(
            body,
            palette,
            "Platform controls stay available during offline desktop sessions",
            (
                f"Signed in as {self.current_username or 'platform owner'} using {self._web_base_url()}."
                if online_state == "online"
                else "Working from local cache. Platform registry snapshots and queued sync tools remain available."
            ),
            badge_text=f"API: {api_state}",
        )
        banner.pack(fill="x", pady=(0, 14))

        summary = tk.Frame(body, bg=palette["bg"])
        summary.pack(fill="x", pady=(0, 14))
        for index in range(4):
            summary.columnconfigure(index, weight=1)

        metrics = [
            ("Queued Sync", queued_actions, "Offline actions waiting to replay"),
            ("Local Items", local_items, "Inventory records in desktop cache"),
            ("Cached Users", registry_summary["users"], "Last platform registry snapshot stored on this desktop"),
            ("Cached Companies", registry_summary["companies"], registry_summary["updated_at"] or "No registry snapshot saved yet"),
        ]
        for index, (label, value, detail) in enumerate(metrics):
            card = self._build_desktop_card(summary, palette)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            tk.Label(card, text=label.upper(), font=("Segoe UI", 9, "bold"), bg=palette["card"], fg=palette["muted"]).pack(anchor="w")
            tk.Label(card, text=str(value), font=("Segoe UI", 24, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w", pady=(8, 0))
            tk.Label(card, text=detail, font=("Segoe UI", 9), bg=palette["card"], fg=palette["muted"], wraplength=190, justify="left").pack(anchor="w", pady=(4, 0))

        command_grid = tk.Frame(body, bg=palette["bg"])
        command_grid.pack(fill="both", expand=True)
        command_grid.columnconfigure(0, weight=1)
        command_grid.columnconfigure(1, weight=1)

        commands = [
            ("Super Admin Dashboard", "Tenant registry, subscription health, and attention queue.", lambda: self._open_online_page("/super-admin-dashboard/")),
            ("Django Admin", "Full platform database administration for trusted operators.", lambda: self._open_online_page("/admin/")),
            ("Online User Registry", "Manage auth users, staff access, and active accounts.", lambda: self._open_online_page("/admin/auth/user/")),
            ("Tenant Profiles", "Review plans, statuses, renewals, and parent account links.", lambda: self._open_online_page("/admin/inventory/userprofile/")),
            ("Desktop User Registry", "Open the platform user list with offline snapshot fallback.", self.manage_users_window),
            ("Offline Queue Inspector", "Review every queued desktop action before reconnect replay.", self.superuser_queue_inspector_window),
            ("Sync Status", "Inspect pull/push health or local cached sync snapshots.", self.online_sync_status_window),
        ]

        for index, (label, detail, command) in enumerate(commands):
            card = self._build_desktop_card(command_grid, palette)
            card.grid(row=index // 2, column=index % 2, sticky="nsew", padx=(0 if index % 2 == 0 else 10, 0), pady=(0, 10))
            tk.Label(card, text=label, font=("Segoe UI", 13, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
            tk.Label(card, text=detail, font=("Segoe UI", 9), bg=palette["card"], fg=palette["muted"], wraplength=370, justify="left").pack(anchor="w", pady=(5, 12))
            tk.Button(
                card,
                text="Open",
                command=command,
                bg=palette["primary"],
                fg=palette["primary_text"],
                activebackground=palette["primary_active"],
                relief="flat",
                padx=14,
                pady=7,
            ).pack(anchor="w")

    def superuser_queue_inspector_window(self):
        if not self._is_platform_superuser():
            self.online_sync_status_window()
            return

        win = tk.Toplevel(self.root)
        palette, shell, body = self._build_desktop_surface(
            win,
            "Offline Queue Inspector",
            "Review pending desktop actions before the platform reconnects.",
            geometry="980x620",
        )
        win.transient(self.root)

        queue = self._read_pending_queue()
        summary = self._summarize_pending_queue(queue)

        banner = self._build_desktop_banner(
            body,
            palette,
            "Queued work is visible even while the server is offline",
            "Superusers can audit what will be replayed, grouped by action type and target.",
            badge_text=f"Queued actions: {summary['total']}",
        )
        banner.pack(fill="x", pady=(0, 14))

        stats = tk.Frame(body, bg=palette["bg"])
        stats.pack(fill="x", pady=(0, 14))
        stats.columnconfigure(0, weight=1)
        stats.columnconfigure(1, weight=1)
        stats.columnconfigure(2, weight=1)

        stat_cards = [
            ("Total Actions", summary["total"], "Everything waiting for replay"),
            ("Action Types", len(summary["actions"]), "Distinct operation groups"),
            ("Latest Queue Time", summary["latest_queued_at"] or "No timestamp", "Newest queued action"),
        ]
        for index, (label, value, detail) in enumerate(stat_cards):
            card = self._build_desktop_card(stats, palette)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            tk.Label(card, text=label.upper(), font=("Segoe UI", 9, "bold"), bg=palette["card"], fg=palette["muted"]).pack(anchor="w")
            tk.Label(card, text=str(value), font=("Segoe UI", 16, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w", pady=(8, 0))
            tk.Label(card, text=detail, font=("Segoe UI", 9), bg=palette["card"], fg=palette["muted"], wraplength=240, justify="left").pack(anchor="w", pady=(4, 0))

        split = tk.Frame(body, bg=palette["bg"])
        split.pack(fill="both", expand=True)
        split.columnconfigure(0, weight=1)
        split.columnconfigure(1, weight=2)
        split.rowconfigure(0, weight=1)

        action_card = self._build_desktop_card(split, palette)
        action_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        tk.Label(action_card, text="Action Breakdown", font=("Segoe UI", 13, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")

        action_tree = ttk.Treeview(action_card, columns=("Action", "Count"), show="headings", height=10)
        action_tree.pack(fill="both", expand=True, pady=(10, 0))
        for col, width in (("Action", 170), ("Count", 90)):
            action_tree.heading(col, text=col.upper())
            action_tree.column(col, anchor="w", width=width, stretch=True)
        for action, count in sorted(summary["actions"].items()):
            action_tree.insert("", "end", values=(action, count))
        if not summary["actions"]:
            action_tree.insert("", "end", values=("NONE", 0))

        item_card = self._build_desktop_card(split, palette)
        item_card.grid(row=0, column=1, sticky="nsew")
        tk.Label(item_card, text="Queued Items", font=("Segoe UI", 13, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")

        queue_tree = ttk.Treeview(
            item_card,
            columns=("Action", "Target", "Queued At", "Details"),
            show="headings",
            height=12,
        )
        queue_tree.pack(fill="both", expand=True, pady=(10, 0))
        for col, width in (
            ("Action", 150),
            ("Target", 180),
            ("Queued At", 190),
            ("Details", 320),
        ):
            queue_tree.heading(col, text=col.upper())
            queue_tree.column(col, anchor="w", width=width, stretch=True)

        for item in queue:
            queue_tree.insert("", "end", values=(
                str(item.get("action") or "UNKNOWN").upper(),
                self._format_queue_target(item),
                item.get("queued_at") or "Legacy queue item",
                self._format_queue_detail(item),
            ))
        if not queue:
            queue_tree.insert("", "end", values=("NONE", "No pending work", "-", "Reconnect actions have all been cleared."))

    def manage_users_window(self):
        """API-backed user registry for company admins and platform superusers."""
        win = tk.Toplevel(self.root)
        palette, shell, body = self._build_desktop_surface(
            win,
            "Business User Registry",
            "View staff access from the online API. Platform superusers also keep a cached tenant snapshot for offline review.",
            geometry="1040x620",
        )
        win.transient(self.root)

        scope_label = tk.Label(
            body,
            text="Waiting to load users...",
            font=("Segoe UI", 10, "bold"),
            bg=palette["bg"],
            fg=palette["muted"],
        )
        scope_label.pack(anchor="w", pady=(0, 10))

        table_card = self._build_desktop_card(body, palette)
        table_card.pack(fill="both", expand=True)

        columns = ("Company", "Username", "Email", "Role", "Status", "Plan", "Location")
        tree = ttk.Treeview(table_card, columns=columns, show="headings", height=14)
        tree.pack(fill="both", expand=True)

        column_widths = {
            "Company": 150,
            "Username": 140,
            "Email": 210,
            "Role": 95,
            "Status": 95,
            "Plan": 95,
            "Location": 150,
        }
        for col in columns:
            tree.heading(col, text=col.upper())
            tree.column(col, anchor="w", width=column_widths.get(col, 120), stretch=True)

        footer = tk.Frame(body, bg=palette["bg"])
        footer.pack(fill="x", pady=(12, 0))

        def render_message(message):
            scope_label.configure(text=message)

        def render_cached_snapshot(snapshot, source_label):
            users = snapshot.get("users") or []
            summary = self._registry_snapshot_summary(snapshot)
            self._render_registry_rows(tree, users)
            render_message(
                f"{source_label}: {summary['users']} user record(s) across {summary['companies']} company profile(s). "
                f"Snapshot time: {summary['updated_at'] or 'unknown'}."
            )

        def fetch_users():
            if not self.api_token:
                if self._is_platform_superuser():
                    snapshot = self._load_superuser_registry_snapshot()
                    if snapshot:
                        render_cached_snapshot(snapshot, "Offline platform snapshot loaded")
                        return
                tree.delete(*tree.get_children())
                render_message("Login online first to load user registry data.")
                return

            tree.delete(*tree.get_children())
            render_message("Loading users from QuickStock online...")
            resp = self._api_request("GET", "/api/users/")
            if resp and resp.get("ok"):
                users = resp.get("users", [])
                scope = "Platform" if resp.get("scope") == "platform" else "Company"
                render_message(f"{scope} registry loaded: {resp.get('count', len(users))} user record(s).")
                self._render_registry_rows(tree, users)
                if self._is_platform_superuser() and scope.lower() == "platform":
                    self._save_superuser_registry_snapshot(scope.lower(), users)
                if not users:
                    render_message(f"{scope} registry loaded. No users found.")
            else:
                if self._is_platform_superuser():
                    snapshot = self._load_superuser_registry_snapshot()
                    if snapshot:
                        render_cached_snapshot(snapshot, "Live API unavailable, cached platform snapshot shown")
                        return
                render_message("Could not retrieve user registry data from the online server.")

        tk.Button(
            footer,
            text="Refresh User List",
            command=fetch_users,
            bg=palette["primary"],
            fg=palette["primary_text"],
            activebackground=palette["primary_active"],
            relief="flat",
            padx=14,
            pady=8,
        ).pack(side="left")

        if self._is_platform_superuser():
            tk.Button(
                footer,
                text="Open Django Users",
                command=lambda: self._open_online_page("/admin/auth/user/"),
                bg=palette["subtle"],
                fg=palette["subtle_text"],
                activebackground=palette["subtle_active"],
                relief="flat",
                padx=14,
                pady=8,
            ).pack(side="left", padx=(8, 0))

            tk.Button(
                footer,
                text="Queue Inspector",
                command=self.superuser_queue_inspector_window,
                bg=palette["subtle"],
                fg=palette["subtle_text"],
                activebackground=palette["subtle_active"],
                relief="flat",
                padx=14,
                pady=8,
            ).pack(side="left", padx=(8, 0))

        tk.Button(
            footer,
            text="Close",
            command=win.destroy,
            bg=palette["subtle"],
            fg=palette["subtle_text"],
            activebackground=palette["subtle_active"],
            relief="flat",
            padx=14,
            pady=8,
        ).pack(side="right")

        fetch_users()

    def tax_settings_window(self):
        win = tk.Toplevel(self.root)
        palette, content = self._desktop_form_shell(win, "Tax Settings", "420x390")

        country_map = {
            "Jamaica": "Jamaica",
            "Trinidad & Tobago": "Trinidad & Tobago",
            "Barbados": "Barbados",
            "Guyana": "Guyana",
            "Saint Lucia": "Saint Lucia",
            "International": "International",
        }

        current_country = self.system.settings.get("country", "Jamaica")
        self._desktop_label(content, "Tax Settings", palette, surface="card", role="title", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        self._desktop_label(content, "Match the desktop tax profile to your online workspace.", palette, surface="card", role="muted", wraplength=340, justify="left").pack(anchor="w", pady=(4, 16))
        self._desktop_label(content, "Country/Region", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        country_var = tk.StringVar(value=current_country)
        country_combo = ttk.Combobox(content, textvariable=country_var, width=30, state="readonly")
        country_combo["values"] = list(country_map.keys())
        if current_country not in country_map:
            country_var.set("International")
        country_combo.pack(fill="x", pady=(5, 12))

        self._desktop_label(content, "Tax Rate (%)", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        rate_var = tk.StringVar()
        rate_entry = self._desktop_entry(content, palette, textvariable=rate_var, width=10)
        rate_entry.pack(fill="x", pady=(5, 16), ipady=8)

        def refresh_rate():
            selected = country_var.get()
            self.system.settings["country"] = selected
            self.system.auto_detect_tax()
            rate_pct = self.system.settings.get("tax_rate", 0.0) * 100
            rate_var.set(f"{rate_pct:.2f}")

        def save_tax_settings():
            selected = country_var.get()
            self.system.settings["country"] = selected
            self.system.auto_detect_tax()
            try:
                custom_rate = float(rate_var.get().strip()) / 100.0
                if custom_rate >= 0:
                    self.system.settings["tax_rate"] = custom_rate
            except Exception:
                pass
            self.apply_regional_tax()
            self._persist_offline_settings()
            messagebox.showinfo("Saved", "Tax settings updated.")
            win.destroy()

        country_combo.bind("<<ComboboxSelected>>", lambda e: refresh_rate())
        refresh_rate()

        self._desktop_button(content, "Save", save_tax_settings, palette, kind="primary").pack(fill="x", pady=(8, 0))

    def location_selector_window(self):
        win = tk.Toplevel(self.root)
        palette, content = self._desktop_form_shell(win, "Select Location", "420x300")

        selectable_locations = self._selectable_locations()
        if not selectable_locations:
            self._desktop_label(content, "No locations found. Sync online first.", palette, surface="card", role="muted", wraplength=340, justify="left").pack(pady=20)
            self._desktop_button(content, "Close", win.destroy, palette, kind="subtle").pack(fill="x", pady=10)
            return

        self._desktop_label(content, "Active Location", palette, surface="card", role="title", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        self._desktop_label(content, "Choose the branch this desktop should use for stock-aware workflows.", palette, surface="card", role="muted", wraplength=340, justify="left").pack(anchor="w", pady=(4, 16))
        location_map = {loc.get("name"): loc.get("id") for loc in selectable_locations}
        location_var = tk.StringVar(value=self.active_location_name)
        location_combo = ttk.Combobox(content, textvariable=location_var, width=30, state="readonly")
        location_combo["values"] = list(location_map.keys())
        if self.active_location_name not in location_map and location_combo["values"]:
            location_var.set(location_combo["values"][0])
        location_combo.pack(fill="x", pady=(5, 16))

        def apply_location():
            location_id = location_map.get(location_var.get())
            if location_id:
                self.set_active_location(location_id)
                self.system.settings["primary_funding_branch_id"] = location_id
                self.system.settings["primary_funding_branch_name"] = location_var.get()
                self._persist_offline_settings()
                messagebox.showinfo("Location Updated", f"Active location: {location_var.get()}")
            win.destroy()

        self._desktop_button(content, "Apply", apply_location, palette, kind="primary").pack(fill="x", pady=(4, 0))

    def suppliers_window(self):
        win = tk.Toplevel(self.root)
        palette, shell, body = self._build_desktop_surface(
            win,
            "Suppliers",
            "Review supplier contacts and queue new supplier records when offline.",
            geometry="820x520",
        )

        columns = ("Name", "Contact", "Phone", "Email", "Address")
        table_card = self._build_desktop_card(body, palette)
        table_card.pack(fill="both", expand=True)
        tree = ttk.Treeview(table_card, columns=columns, show="headings")
        tree.pack(fill="both", expand=True)

        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, anchor="center", width=120)

        def refresh_suppliers():
            tree.delete(*tree.get_children())
            self.sync_reference_data()
            for s in self.suppliers:
                tree.insert("", "end", values=(
                    s.get("name", ""),
                    s.get("contact_name", ""),
                    s.get("phone", ""),
                    s.get("email", ""),
                    s.get("address", ""),
                ))

        def add_supplier():
            add_win = tk.Toplevel(win)
            add_palette, add_content = self._desktop_form_shell(add_win, "Add Supplier", "420x430")

            fields = {}
            for label in ("Name", "Contact Name", "Phone", "Email", "Address"):
                self._desktop_label(add_content, label, add_palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
                entry = self._desktop_entry(add_content, add_palette, width=35)
                entry.pack(fill="x", pady=(4, 9), ipady=8)
                fields[label] = entry

            def save_supplier():
                name = fields["Name"].get().strip()
                if not name:
                    messagebox.showerror("Error", "Supplier name is required.")
                    return
                
                # Try Online API first
                if self.api_token:
                    resp = self._api_request(
                        "POST",
                        "/api/suppliers/",
                        {
                            "name": name,
                            "contact_name": fields["Contact Name"].get().strip(),
                            "phone": fields["Phone"].get().strip(),
                            "email": fields["Email"].get().strip(),
                            "address": fields["Address"].get().strip(),
                        },
                    )
                    if resp and resp.get("ok"):
                        add_win.destroy()
                        refresh_suppliers()
                        return
                
                # Removed direct MySQL fallback to enforce API-only architecture
                # This prevents client machines from needing DB credentials
                payload = {
                    "name": name,
                    "contact_name": fields["Contact Name"].get().strip(),
                    "phone": fields["Phone"].get().strip(),
                    "email": fields["Email"].get().strip(),
                    "address": fields["Address"].get().strip(),
                    "user_id": self.current_user_id,
                }

                # Check for connectivity before queuing
                if not self.api_token:
                    self._queue_action("ADD_SUPPLIER", payload)
                    self.suppliers.append(payload)
                    self._save_cache(SUPPLIER_CACHE_FILE, self.suppliers)
                    add_win.destroy()
                    refresh_suppliers()
                    messagebox.showwarning("Offline", "Supplier queued for local sync.")
                    return

                # Fallback to Offline Queue
                if not self.current_user_id:
                    messagebox.showerror("Error", "No active user. Please login online first.")
                    return
                
                payload = {
                    "name": name,
                    "contact_name": fields["Contact Name"].get().strip(),
                    "phone": fields["Phone"].get().strip(),
                    "email": fields["Email"].get().strip(),
                    "address": fields["Address"].get().strip(),
                    "user_id": self.current_user_id,
                }
                self._queue_action("ADD_SUPPLIER", payload)
                self.suppliers.append(payload)
                self._save_cache(SUPPLIER_CACHE_FILE, self.suppliers)
                add_win.destroy()
                refresh_suppliers()
                messagebox.showwarning("Offline", "Supplier queued for local sync.")

            self._desktop_button(add_content, "Save Supplier", save_supplier, add_palette, kind="primary").pack(fill="x", pady=(8, 0))

        btn_frame = tk.Frame(body, bg=palette["bg"])
        btn_frame.pack(fill="x", pady=(12, 0))
        self._desktop_button(btn_frame, "Refresh", refresh_suppliers, palette, kind="subtle").pack(side="left", padx=(0, 8))
        self._desktop_button(btn_frame, "Add Supplier", add_supplier, palette, kind="primary").pack(side="left")

        refresh_suppliers()

    def receive_stock_window(self):
        win = tk.Toplevel(self.root)
        palette, shell, body = self._build_desktop_surface(
            win,
            "Receive Stock",
            "Capture incoming stock with supplier and location details, even when the server is temporarily down.",
            geometry="760x620",
        )
        banner = self._build_desktop_banner(
            body,
            palette,
            "Receiving can be queued offline",
            "If the API cannot be reached, the stock receipt is stored locally and pushed once the desktop reconnects.",
            badge_text=f"Queued actions: {len(self._read_pending_queue())}",
        )
        banner.pack(fill="x", pady=(0, 14))

        card = self._build_desktop_card(body, palette)
        card.pack(fill="both", expand=True)

        tk.Label(card, text="Item (SKU | Name)", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        items = [f'{i.get("SKU")} | {i.get("Name")}' for i in self.get_clean_inventory()]
        item_var = tk.StringVar()
        item_combo = ttk.Combobox(card, textvariable=item_var, values=items, width=40)
        item_combo.pack(fill="x", pady=(6, 12))

        tk.Label(card, text="Supplier", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        supplier_map = {s.get("name"): s.get("id") for s in self.suppliers or []}
        supplier_var = tk.StringVar()
        supplier_combo = ttk.Combobox(card, textvariable=supplier_var, values=list(supplier_map.keys()), width=40)
        supplier_combo.pack(fill="x", pady=(6, 12))

        tk.Label(card, text="Location", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        location_map = {l.get("name"): l.get("id") for l in self._selectable_locations()}
        location_var = tk.StringVar(value=self.active_location_name)
        location_combo = ttk.Combobox(card, textvariable=location_var, values=list(location_map.keys()), width=40, state="readonly")
        if self.active_location_name not in location_map and location_combo["values"]:
            location_var.set(location_combo["values"][0])
        location_combo.pack(fill="x", pady=(6, 12))

        tk.Label(card, text="Quantity Received", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        qty_entry = tk.Entry(card, font=("Segoe UI", 11), bg=palette["input_bg"], fg=palette["input_fg"], insertbackground=palette["input_fg"], relief="flat", bd=0)
        qty_entry.pack(fill="x", pady=(6, 12), ipady=9)

        tk.Label(card, text="Unit Cost", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        cost_entry = tk.Entry(card, font=("Segoe UI", 11), bg=palette["input_bg"], fg=palette["input_fg"], insertbackground=palette["input_fg"], relief="flat", bd=0)
        cost_entry.pack(fill="x", pady=(6, 18), ipady=9)

        def save_receive():
            raw_item = item_var.get().strip()
            if not raw_item:
                messagebox.showerror("Error", "Select an item.")
                return
            if not self.current_user_id:
                messagebox.showerror("Error", "No active user. Please login online first.")
                return
            sku = raw_item.split("|")[0].strip()
            supplier_id = supplier_map.get(supplier_var.get())
            location_id = location_map.get(location_var.get())

            try:
                qty = int(qty_entry.get().strip())
                unit_cost = float(cost_entry.get().strip())
            except ValueError:
                messagebox.showerror("Error", "Quantity and Unit Cost must be numeric.")
                return

            if qty <= 0 or unit_cost < 0:
                messagebox.showerror("Error", "Invalid quantity or cost.")
                return
            if not supplier_id:
                messagebox.showerror("Error", "Select a supplier.")
                return

            if self.api_token:
                resp = self._api_request(
                    "POST",
                    "/api/receive-stock/",
                    {
                        "sku": sku,
                        "supplier_id": supplier_id,
                        "location_id": location_id,
                        "quantity": qty,
                        "unit_cost": unit_cost,
                    },
                )
                if resp and resp.get("ok"):
                    messagebox.showinfo("Success", "Stock received and synced.")
                    self.sync_inventory()
                    win.destroy()
                    return

            payload = {
                "sku": sku, "supplier_id": supplier_id, "location_id": location_id,
                "quantity": qty, "unit_cost": unit_cost, "user_id": self.current_user_id,
            }
            self._queue_action("RECEIVE_STOCK", payload)
            messagebox.showwarning("Offline", "Server unreachable. Receive stock queued locally.")
            win.destroy()

        action_row = tk.Frame(card, bg=palette["card"])
        action_row.pack(fill="x")
        tk.Button(action_row, text="Receive Stock", command=save_receive, bg=palette["primary"], fg=palette["primary_text"], activebackground=palette["primary_active"], relief="flat", padx=14, pady=12).pack(side="left", fill="x", expand=True, padx=(0, 8))
        tk.Button(action_row, text="Cancel", command=win.destroy, bg=palette["subtle"], fg=palette["subtle_text"], activebackground=palette["subtle_active"], relief="flat", padx=14, pady=12).pack(side="left", fill="x", expand=True, padx=(8, 0))

    def transfer_stock_window(self):
        win = tk.Toplevel(self.root)
        palette, shell, body = self._build_desktop_surface(
            win,
            "Transfer Stock",
            "Move stock between locations with clearer source and destination context.",
            geometry="760x620",
        )
        banner = self._build_desktop_banner(
            body,
            palette,
            "Transfers continue offline",
            "Queued transfers preserve the move request locally when the web API is unreachable.",
            badge_text=f"Queued actions: {len(self._read_pending_queue())}",
        )
        banner.pack(fill="x", pady=(0, 14))

        card = self._build_desktop_card(body, palette)
        card.pack(fill="both", expand=True)

        tk.Label(card, text="Item (SKU | Name)", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        items = [f'{i.get("SKU")} | {i.get("Name")}' for i in self.get_clean_inventory()]
        item_var = tk.StringVar()
        item_combo = ttk.Combobox(card, textvariable=item_var, values=items, width=40)
        item_combo.pack(fill="x", pady=(6, 12))

        tk.Label(card, text="From Location", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        location_map = {l.get("name"): l.get("id") for l in self._selectable_locations()}
        from_var = tk.StringVar(value=self.active_location_name)
        from_combo = ttk.Combobox(card, textvariable=from_var, values=list(location_map.keys()), width=40, state="readonly")
        if self.active_location_name not in location_map and from_combo["values"]:
            from_var.set(from_combo["values"][0])
        from_combo.pack(fill="x", pady=(6, 12))

        tk.Label(card, text="To Location", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        to_var = tk.StringVar()
        to_combo = ttk.Combobox(card, textvariable=to_var, values=list(location_map.keys()), width=40, state="readonly")
        if to_combo["values"]:
            to_var.set(to_combo["values"][0])
        to_combo.pack(fill="x", pady=(6, 12))

        tk.Label(card, text="Quantity", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        qty_entry = tk.Entry(card, font=("Segoe UI", 11), bg=palette["input_bg"], fg=palette["input_fg"], insertbackground=palette["input_fg"], relief="flat", bd=0)
        qty_entry.pack(fill="x", pady=(6, 18), ipady=9)

        def save_transfer():
            raw_item = item_var.get().strip()
            if not raw_item:
                messagebox.showerror("Error", "Select an item.")
                return
            sku = raw_item.split("|")[0].strip()
            from_loc = location_map.get(from_var.get())
            to_loc = location_map.get(to_var.get())
            if not from_loc or not to_loc or from_loc == to_loc:
                messagebox.showerror("Error", "Select two different locations.")
                return
            try:
                qty = int(qty_entry.get().strip())
            except ValueError:
                messagebox.showerror("Error", "Quantity must be a number.")
                return
            if qty <= 0:
                messagebox.showerror("Error", "Quantity must be greater than 0.")
                return

            if self.api_token:
                resp = self._api_request(
                    "POST",
                    "/api/transfer-stock/",
                    {
                        "sku": sku,
                        "from_location_id": from_loc,
                        "to_location_id": to_loc,
                        "quantity": qty,
                    },
                )
                if resp and resp.get("ok"):
                    messagebox.showinfo("Success", "Stock transferred and synced.")
                    self.sync_inventory()
                    win.destroy()
                    return

            payload = {
                "sku": sku,
                "from_location_id": from_loc,
                "to_location_id": to_loc,
                "quantity": qty,
                "user_id": self.current_user_id,
            }
            self._queue_action("TRANSFER_STOCK", payload)
            messagebox.showwarning("Offline", "Stock transfer queued for sync.")
            win.destroy()

        action_row = tk.Frame(card, bg=palette["card"])
        action_row.pack(fill="x")
        tk.Button(action_row, text="Transfer Stock", command=save_transfer, bg=palette["primary"], fg=palette["primary_text"], activebackground=palette["primary_active"], relief="flat", padx=14, pady=12).pack(side="left", fill="x", expand=True, padx=(0, 8))
        tk.Button(action_row, text="Cancel", command=win.destroy, bg=palette["subtle"], fg=palette["subtle_text"], activebackground=palette["subtle_active"], relief="flat", padx=14, pady=12).pack(side="left", fill="x", expand=True, padx=(8, 0))

    def _normalize_items(self, items):
        """Normalize mixed-key item dicts into a unified schema for offline + core usage."""
        normalized = []
        for idx, item in enumerate(items or [], start=1):
            product_id = item.get("id") or item.get("product_id")
            sku = item.get("sku") or item.get("SKU") or ""
            name = item.get("name") or item.get("Name") or "Unknown"
            category = item.get("category_name") or item.get("category") or item.get("Category") or "General"
            barcode = item.get("barcode") or item.get("Barcode") or ""
            cost = item.get("cost_price") or item.get("cost") or item.get("Cost") or 0
            price = item.get("price") or item.get("Price") or 0
            amount = item.get("quantity") or item.get("qty") or item.get("Quantity") or item.get("Amount") or item.get("amount") or 0
            number = item.get("Number") or idx
            status = str(item.get("status") or item.get("Status") or "active").strip().lower()
            raw_deleted = item.get("is_deleted", item.get("Is Deleted", False))
            is_deleted = raw_deleted if isinstance(raw_deleted, bool) else str(raw_deleted).strip().lower() in {"1", "true", "yes"}
            raw_taxable = item.get("is_taxable", item.get("Is Taxable", True))
            is_taxable = raw_taxable if isinstance(raw_taxable, bool) else str(raw_taxable).strip().lower() not in {"0", "false", "no"}

            try:
                number = int(number)
            except Exception:
                number = idx
            try:
                cost = float(cost)
            except Exception:
                cost = 0.0
            try:
                price = float(price)
            except Exception:
                price = 0.0
            try:
                amount = int(amount)
            except Exception:
                amount = 0

            normalized.append({
                "id": self._coerce_int(product_id),
                "product_id": self._coerce_int(product_id),
                "Number": number,
                "SKU": str(sku).upper(),
                "Category": str(category),
                "Name": str(name),
                "Cost": cost,
                "Price": price,
                "Amount": amount,
                "sku": str(sku).upper(),
                "barcode": str(barcode),
                "category": str(category),
                "name": str(name),
                "cost": cost,
                "price": price,
                "amount": amount,
                "status": status,
                "is_deleted": is_deleted,
                "is_taxable": is_taxable,
                "last_modified": item.get("last_modified"),
                "sync_token": item.get("sync_token"),
            })
        return normalized

    def _touch_inventory_item(self, item):
        """Mark an inventory row as locally changed before saving or syncing."""
        if not isinstance(item, dict):
            return
        item["last_modified"] = datetime.now(timezone.utc).isoformat()
        item["sync_token"] = uuid.uuid4().hex
        item["sync_source"] = "desktop"

    def create_button(self, parent, text, command, row, col):
        pref = (self.system.settings.get("theme") or "system").strip().lower()
        palette = self._desktop_window_palette()
        button_palette = self._home_button_palette(text)
        if button_palette:
            fg_color, hover_color, text_color, border_color = button_palette
        elif pref == "dark":
            fg_color = "#233247"
            hover_color = "#31465F"
            text_color = "#ECF4FF"
            border_color = "#415877"
        else:
            fg_color = "#E7EEF6"
            hover_color = "#D8E2EE"
            text_color = "#132238"
            border_color = "#C3D0DF"

        descriptions = {
            "Operations": "Sell, receive, transfer, and control the active branch.",
            "Reports": "Search records, inspect inventory, and review sales history.",
            "Settings": "Business identity, tax, suppliers, locations, and sync tools.",
            "Switch User": "Close this session and return to operator login.",
            "Online": "Open connected QuickStock web workspaces and admin pages.",
            "Users": "Review staff access and cached user registry state.",
            "Exit": "Close the QuickStock desktop application.",
        }
        card = tk.Frame(
            parent,
            bg=palette["card"],
            highlightthickness=1,
            highlightbackground=palette["card_border"],
            padx=12,
            pady=12,
        )
        card.grid(row=row, column=col, sticky="nsew", padx=8, pady=8)
        card.columnconfigure(0, weight=1)
        group_label = {
            "Operations": "OPERATIONS",
            "Reports": "REPORTING",
            "Settings": "ADMIN",
            "Switch User": "SESSION",
            "Online": "ONLINE",
            "Users": "ACCESS",
            "Exit": "SESSION",
        }.get(text, "COMMAND")
        tk.Label(
            card,
            text=group_label,
            bg=palette["card"],
            fg=palette["muted"],
            font=("Segoe UI", 8, "bold"),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            card,
            text=descriptions.get(text, "Open this workspace."),
            bg=palette["card"],
            fg=palette["text"],
            font=("Segoe UI", 9),
            justify="left",
            wraplength=360,
        ).grid(row=1, column=0, sticky="ew", pady=(4, 10))
        button = ctk.CTkButton(
            card,
            text=text,
            command=command,
            font=("Segoe UI", 12, "bold"),
            corner_radius=8,
            height=42,
            fg_color=fg_color,
            hover_color=hover_color,
            text_color=text_color,
            border_width=1,
            border_color=border_color
        )
        button.grid(row=2, column=0, sticky="ew")
        button._home_card = card
        button._home_grid = (row, col)
        self.all_buttons.append(button)
        return button
    
    def apply_regional_tax(self):
        """Triggers detection and updates the UI status."""
        try:
            # Call the detection method we added to InventorySystem
            self.system.auto_detect_tax() 
            
            # Update the status label to show the detected region
            region = self.system.settings.get("country", "Jamaica")
            tax_name = self.system.settings.get("tax_label", "GCT")
            rate = self.system.settings.get("tax_rate", 0.15) * 100
            
            self.status_label.config(
                text=f"System Ready | Region: {region} ({tax_name}: {rate}%)"
            )
        except Exception as e:
            print(f"Tax detection error: {e}")
    

    def add_to_sync_queue(self, task, action=None):
        """Queue an offline task so it can be replayed when the API is reachable."""
        with self._get_queue_lock():
            queue = self._read_pending_queue()
            if isinstance(task, dict):
                queued_task = dict(task)
                if action:
                    queued_task["action"] = action
            else:
                queued_task = {"action": action or "UNKNOWN", "payload": task}
            queued_task.setdefault("queued_at", self._queue_timestamp())
            queued_task = self._upgrade_pending_item(queued_task)
            queue.append(queued_task)
            self._write_pending_queue(queue)
            return queued_task

    def _queue_action(self, action, payload):
        if str(action or "").upper() == "RECORD_SALE":
            valid, error = self._validate_checkout_payload(payload, replay=False)
            if not valid:
                raise ValueError(f"{error['code']}: {error['message']}")
        return self.add_to_sync_queue(payload, action=action)

    def _write_queue_file_atomic(self, path, items):
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        temp_path = os.path.join(
            directory,
            f".{os.path.basename(path)}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp",
        )
        try:
            with open(temp_path, "w", encoding="utf-8") as queue_file:
                json.dump(items, queue_file, indent=2)
                queue_file.flush()
                os.fsync(queue_file.fileno())
            os.replace(temp_path, path)
            try:
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                logger.warning("Could not fsync pending queue directory %s", directory)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _write_pending_queue(self, items):
        with self._get_queue_lock():
            queue_path = self._pending_queue_path()
            corruption = getattr(self, "_queue_corruption_info", None)
            if corruption:
                raise RuntimeError(
                    "Pending sync queue is corrupt. Restore or inspect the preserved queue before retrying."
                )

            if items:
                self._write_queue_file_atomic(queue_path, items)
                if queue_path != PENDING_QUEUE_FILE and os.path.exists(PENDING_QUEUE_FILE):
                    try:
                        os.remove(PENDING_QUEUE_FILE)
                    except OSError:
                        pass
            else:
                for path in {queue_path, PENDING_QUEUE_FILE}:
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except OSError:
                            pass

    def _update_pending_action(self, action_id, *, state=None, attempts=None, error=None, next_attempt_at=None):
        with self._get_queue_lock():
            queue = self._read_pending_queue()
            updated = None
            for index, item in enumerate(queue):
                metadata = item.get("_sync") or {}
                if metadata.get("action_id") != action_id:
                    continue
                updated = dict(item)
                updated_metadata = dict(metadata)
                if state is not None:
                    updated_metadata["state"] = state
                if attempts is not None:
                    updated_metadata["attempts"] = max(0, int(attempts))
                updated_metadata["last_error"] = error
                updated_metadata["next_attempt_at"] = next_attempt_at
                if state == "in_flight":
                    updated_metadata["last_attempt_at"] = self._queue_timestamp()
                updated["_sync"] = updated_metadata
                queue[index] = updated
                break
            if updated is not None:
                self._write_pending_queue(queue)
            return updated

    def _remove_pending_action(self, action_id):
        with self._get_queue_lock():
            queue = self._read_pending_queue()
            remaining = [
                item for item in queue
                if (item.get("_sync") or {}).get("action_id") != action_id
            ]
            removed = len(remaining) != len(queue)
            if removed:
                self._write_pending_queue(remaining)
            return removed

    def retry_pending_action(self, action_id):
        """Move a retained failed action back to pending for an explicit retry."""
        return self._update_pending_action(
            action_id,
            state="pending",
            attempts=0,
            error=None,
            next_attempt_at=None,
        ) is not None

    def _sync_queue_request(self, method, path, payload=None):
        """Return a classified transport result without losing HTTP failure detail."""
        if not self.api_base_url:
            return {"kind": "retryable", "status": None, "body": {}, "error": "API URL is not configured."}
        if not self.api_token:
            return {"kind": "auth", "status": None, "body": {}, "error": "Authentication is required."}
        if time.time() - self.last_server_error_time < self.server_cooldown:
            return {"kind": "retryable", "status": None, "body": {}, "error": "Server cooldown is active."}

        url = build_api_url(self.api_base_url, path)
        if requires_https_in_production(self.api_base_url):
            self.api_status = "INSECURE_CONNECTION"
            return {"kind": "permanent", "status": None, "body": {}, "error": "Insecure API connection blocked."}

        headers = {
            "Authorization": f"Token {self.api_token}",
            "Content-Type": "application/json",
            "User-Agent": "QuickStockJA-Desktop/1.0",
            "X-Client-ID": self._queue_client_id(),
        }
        try:
            response = requests.request(
                method=method,
                url=url,
                json=payload,
                headers=headers,
                timeout=10,
                verify=True,
            )
        except requests.exceptions.SSLError as exc:
            self.api_status = "SSL_ERROR"
            return {"kind": "permanent", "status": None, "body": {}, "error": f"SSL verification failed: {exc}"}
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            self.api_status = "SERVER_DOWN"
            return {"kind": "retryable", "status": None, "body": {}, "error": str(exc) or "Network interruption."}
        except requests.exceptions.RequestException as exc:
            return {"kind": "retryable", "status": None, "body": {}, "error": str(exc) or "Request failed."}

        try:
            body = response.json()
            if not isinstance(body, dict):
                body = {}
        except ValueError:
            body = {}

        status = response.status_code
        error = str(body.get("error") or body.get("message") or f"HTTP {status}")
        if 200 <= status < 300:
            self.api_status = "ONLINE"
            return {"kind": "success", "status": status, "body": body, "error": None}
        if status in (401, 403):
            self.api_status = "TOKEN_EXPIRED" if status == 401 else "AUTH_FORBIDDEN"
            if status == 401:
                self.api_token = None
            return {"kind": "auth", "status": status, "body": body, "error": error}
        if status in (408, 425, 429) or status >= 500:
            if status >= 500:
                self.api_status = "SERVER_ERROR"
                self.last_server_error_time = time.time()
            return {"kind": "retryable", "status": status, "body": body, "error": error}
        return {"kind": "permanent", "status": status, "body": body, "error": error}

    def _sale_queue_payload(self, item):
        return {
            key: value
            for key, value in item.items()
            if key not in {"action", "queued_at", "_sync"}
        }

    def _sale_acknowledgement_matches(self, payload, response):
        if not isinstance(response, dict):
            return False
        expected_reference = str(payload.get("offline_client_ref") or "")
        return (
            bool(expected_reference)
            and response.get("ok") is True
            and response.get("sale_id") is not None
            and response.get("contract_version") == payload.get("contract_version")
            and str(response.get("client_reference") or "")
            == expected_reference
        )

    def _execute_queued_action(self, item):
        action = item.get("action")
        if action == "RECORD_SALE":
            payload = self._sale_queue_payload(item)
            valid, validation_error = self._validate_checkout_payload(payload, replay=True)
            if not valid:
                return {
                    "kind": "permanent",
                    "status": None,
                    "body": {},
                    "error": f"{validation_error['code']}: {validation_error['message']}",
                }
            result = self._sync_queue_request("POST", "/api/sales/", payload)
            if result["kind"] != "success":
                return result
            body = result.get("body") or {}
            acknowledgement_matches = self._sale_acknowledgement_matches(payload, body)
            if not acknowledgement_matches:
                return {
                    "kind": "permanent",
                    "status": result.get("status"),
                    "body": body,
                    "error": "Server acknowledgement did not match the queued sale reference.",
                }
            return result

        if action == "RECORD_SALE_ITEM":
            return {
                "kind": "permanent",
                "status": None,
                "body": {},
                "error": "Legacy sale-line action has no verifiable parent-sale acknowledgement.",
            }

        category_id = 1
        if action == "UPSERT_ITEM" and item.get("category"):
            category_id = self._get_or_create_category_id(item["category"])
            if category_id is None:
                return {"kind": "retryable", "status": None, "body": {}, "error": "Category could not be synchronized."}

        response = None
        if action == "UPSERT_ITEM":
            response = self._api_request(
                "POST",
                "/api/inventory/push/",
                {
                    "sku": item.get("sku"),
                    "name": item.get("name"),
                    "cost_price": item.get("cost_price"),
                    "price": item.get("price"),
                    "quantity": self._sanitize_db_quantity(item.get("quantity"), clamp=True),
                    "category_id": category_id,
                    "location_id": item.get("location_id"),
                },
            )
        elif action == "ADD_SUPPLIER":
            response = self._api_request(
                "POST",
                "/api/suppliers/",
                {
                    "name": item.get("name"),
                    "contact_name": item.get("contact_name"),
                    "phone": item.get("phone"),
                    "email": item.get("email"),
                    "address": item.get("address"),
                },
            )
        elif action == "RECEIVE_STOCK":
            response = self._api_request(
                "POST",
                "/api/receive-stock/",
                {
                    "sku": item.get("sku"),
                    "supplier_id": item.get("supplier_id"),
                    "location_id": item.get("location_id"),
                    "quantity": item.get("quantity"),
                    "unit_cost": item.get("unit_cost"),
                },
            )
        elif action == "TRANSFER_STOCK":
            response = self._api_request(
                "POST",
                "/api/transfer-stock/",
                {
                    "sku": item.get("sku"),
                    "from_location_id": item.get("from_location_id"),
                    "to_location_id": item.get("to_location_id"),
                    "quantity": item.get("quantity"),
                },
            )
        elif action == "DELETE":
            response = self._api_request("DELETE", f"/api/inventory/{item.get('sku')}/")
        elif action == "DEDUCT_STOCK":
            payload = self._build_item_sync_payload_from_local_state(
                item.get("sku"),
                item.get("location_id"),
            )
            if payload:
                response = self._api_request("POST", "/api/inventory/", payload)
        else:
            return {"kind": "permanent", "status": None, "body": {}, "error": f"Unsupported queue action: {action}"}

        if response and response.get("ok"):
            return {"kind": "success", "status": 200, "body": response, "error": None}
        return {"kind": "retryable", "status": None, "body": response or {}, "error": "Queued action was not acknowledged."}

    def _queue_retry_time(self, attempts):
        delay = min(
            QUEUE_BACKOFF_MAX_SECONDS,
            QUEUE_BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)),
        )
        return datetime.fromtimestamp(time.time() + delay, tz=timezone.utc).isoformat()

    def _queue_item_is_due(self, item, force=False):
        if force:
            return True
        next_attempt = self._parse_queue_timestamp((item.get("_sync") or {}).get("next_attempt_at"))
        return next_attempt is None or next_attempt <= datetime.now(timezone.utc)

    def sync_offline_queue(self, silent=False, force=False):
        """Replay durable actions and remove only actions with verified acknowledgements."""
        replay_lock = getattr(self, "_queue_replay_lock", None)
        if replay_lock is None:
            replay_lock = threading.Lock()
            self._queue_replay_lock = replay_lock
        if not replay_lock.acquire(blocking=False):
            return {"status": "already_running", "synced": 0, "remaining": len(self._read_pending_queue())}

        synced_count = 0
        attempted_count = 0
        try:
            pending_items = self._read_pending_queue()
            corruption = getattr(self, "_queue_corruption_info", None)
            if corruption:
                if not silent:
                    messagebox.showerror(
                        "Sync Queue Blocked",
                        "The pending queue is corrupt and was preserved for recovery. No queued data was overwritten.",
                    )
                return {"status": "corrupt", "synced": 0, "remaining": 0, "corruption": corruption}
            if not pending_items:
                if not silent:
                    messagebox.showinfo("Sync", "No pending items to sync.")
                return {"status": "empty", "synced": 0, "remaining": 0}
            if not self.api_token:
                if not silent:
                    messagebox.showwarning(
                        "Sync Offline",
                        "Direct database access is disabled. Please login online to sync pending items via API.",
                    )
                return {"status": "auth_required", "synced": 0, "remaining": len(pending_items)}

            active_action_ids = getattr(self, "_active_queue_action_ids", None)
            if active_action_ids is None:
                active_action_ids = set()
                self._active_queue_action_ids = active_action_ids

            for snapshot_item in pending_items:
                metadata = snapshot_item.get("_sync") or {}
                action_id = metadata.get("action_id")
                if not action_id or metadata.get("state") in QUEUE_TERMINAL_STATES:
                    continue
                if not self._queue_item_is_due(snapshot_item, force=force):
                    continue
                if not self.api_token:
                    break

                prior_attempts = int(metadata.get("attempts") or 0)
                active_action_ids.add(action_id)
                current_item = self._update_pending_action(
                    action_id,
                    state="in_flight",
                    attempts=prior_attempts + 1,
                    error=None,
                    next_attempt_at=None,
                )
                if current_item is None:
                    active_action_ids.discard(action_id)
                    continue

                attempted_count += 1
                try:
                    result = self._execute_queued_action(current_item)
                    kind = result.get("kind")
                    error = result.get("error")
                    attempts = int((current_item.get("_sync") or {}).get("attempts") or prior_attempts + 1)
                    if kind == "success":
                        if self._remove_pending_action(action_id):
                            synced_count += 1
                    elif kind == "auth":
                        self._update_pending_action(
                            action_id,
                            state="paused_auth",
                            attempts=prior_attempts,
                            error=error,
                            next_attempt_at=None,
                        )
                    elif kind == "permanent" or attempts >= QUEUE_MAX_ATTEMPTS:
                        self._update_pending_action(
                            action_id,
                            state="dead_letter",
                            attempts=attempts,
                            error=error,
                            next_attempt_at=None,
                        )
                    else:
                        self._update_pending_action(
                            action_id,
                            state="retry",
                            attempts=attempts,
                            error=error,
                            next_attempt_at=self._queue_retry_time(attempts),
                        )
                except Exception as exc:
                    logger.exception("Unexpected queued action replay failure")
                    attempts = prior_attempts + 1
                    self._update_pending_action(
                        action_id,
                        state="dead_letter" if attempts >= QUEUE_MAX_ATTEMPTS else "retry",
                        attempts=attempts,
                        error=f"Unexpected replay failure: {exc}",
                        next_attempt_at=None if attempts >= QUEUE_MAX_ATTEMPTS else self._queue_retry_time(attempts),
                    )
                finally:
                    active_action_ids.discard(action_id)

            remaining = self._read_pending_queue()
            if not silent:
                if remaining:
                    messagebox.showwarning(
                        "Sync",
                        f"Synced {synced_count} queued action(s). {len(remaining)} item(s) remain queued.",
                    )
                else:
                    messagebox.showinfo("Sync", f"Pending items synced via API ({synced_count} action(s)).")
            if attempted_count:
                self.sync_inventory()
                self.sync_reference_data()
            return {
                "status": "complete" if not remaining else "pending",
                "synced": synced_count,
                "attempted": attempted_count,
                "remaining": len(remaining),
            }
        finally:
            replay_lock.release()

    def sync_inventory(self, callback=None):
        if getattr(self, '_sync_in_progress', False):
            if callback:
                self.root.after(500, callback)
            return
        self._sync_in_progress = True
        threading.Thread(target=self._sync_inventory_worker, args=(callback,), daemon=True).start()

    def _sync_inventory_worker(self, callback=None):
        try:
            if self.api_token and self.api_base_url:
                self.root.after(0, lambda: self.refresh_status(connected=True, syncing=True))
                self.sync_offline_queue(silent=True)

                params = {"client_id": f"desktop-{self.current_user_id}" if self.current_user_id else "desktop-client"}
                
                if self.active_location_id:
                    params["location_id"] = self.active_location_id

                resp = self._api_request("GET", "/api/inventory/", params=params)

                success = bool(resp and resp.get("ok"))
                if success:
                    items = resp.get("items", [])
                    with self.data_lock:
                        self.system.inventory = self._normalize_items(items)
                    self.save_inventory_cache(items, from_api=True) # Indicate data came from API
                    try:
                        storage.save_data(self.system.inventory, self.system.receipts, self.system.settings)
                        self._session_invalid = False
                    except Exception as e:
                        logger.error(f"Failed to persist synced inventory: {e}")
                else:
                    self.load_from_local_cache()

                self.root.after(0, lambda: self.refresh_status(connected=success, syncing=False))
                return

            self.load_from_local_cache()

        finally:
            self._sync_in_progress = False
            if callback:
                self.root.after(0, callback)


    def _start_background_sync_worker(self):
        """Handles event-driven sync by processing the pending queue in the background."""
        def run_sync():
            while True:
                time.sleep(30)  # Wait first — don't probe before login

                if not self.current_user_id:
                    continue  # Skip until someone is logged in

                # Connectivity Heartbeat using API availability
                is_online = False
                if self.api_base_url:
                    try:
                        resp = self._api_request("GET", "/api/health/")
                        is_online = bool(resp and resp.get("status") in {"ok", "degraded"})
                    except Exception:
                        is_online = False

                self.root.after(0, lambda online=is_online: self.refresh_status(connected=online))

                if is_online:
                    if self.api_token and not getattr(self, '_is_offline', False):
                        # Only sync if we have a valid session
                        self.sync_offline_queue(silent=True)
                        self.sync_inventory()

        worker = threading.Thread(target=run_sync, daemon=True)
        worker.start()
    
    def load_from_local_cache(self):
        """Load inventory from local cache if DB unavailable."""
        cache_path = self._inventory_cache_path()
        # Priority: User-specific cache -> Global cache -> Shared data file -> Legacy local copy
        fallback_paths = [
            cache_path,
            LOCAL_CACHE_FILE,
            SHARED_DATA_FILE,
            os.path.join(DATA_DIR, "inventory_data.json"),
        ]
        
        if cache_path != LOCAL_CACHE_FILE:
            fallback_paths.append(LOCAL_CACHE_FILE)

        # Use a set to avoid checking the same path twice
        for path in dict.fromkeys(fallback_paths):
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # Handle both raw list format and storage dictionary format
                items = data.get("inventory", data) if isinstance(data, dict) else data
                
                self.system.inventory = self._normalize_items(items)
                
                if path != cache_path and self.current_user_id:
                    try:
                        self._write_json_atomic(cache_path, self.system.inventory)
                    except Exception:
                        pass
                if len(self.system.inventory) > 0:
                    print(f"Loaded {len(self.system.inventory)} items from local cache.")
                return
            except json.JSONDecodeError as e:
                print(f"Corrupt cache detected at {path}: {e}")
                try:
                    os.remove(path)
                except Exception:
                    pass
            except Exception as e:
                print(f"Failed to load local cache from {path}: {e}")

        print("No local cache found.")
        self.system.inventory = []

    def _get_owner_id_for_user(self):
        """
        Determine the owner_id for the current user.
        For cashiers/managers, this returns their parent admin's user ID.
        For admins, this returns their own user ID.
        
        NOTE: This logic is simplified for the desktop client. The backend API
        should ideally provide the correct owner context for operations.
        """
        return self.current_user_id # Simplified: Assume current user is the owner for desktop operations

    def _get_or_create_category_id(self, category_name):
        if not category_name:
            return 1

        for cat in self.categories:
            if cat.get("name", "").lower() == category_name.lower():
                return cat.get("id")

        if self.api_token:
            resp = self._api_request("POST", "/api/categories/", {"name": category_name})
            if resp and resp.get("ok"):
                self.sync_reference_data()
                return resp.get("category_id")
            return 1  # Endpoint missing or failed — use default silently

        return 1  # Offline — use default

  

    def _load_offline_credentials(self, username):
        """Load the cached offline login snapshot for a user."""
        try:
            cache_obj = SecureCache(USER_CACHE_FILE)
            data = cache_obj.load()
        except Exception as e:
            logger.error(f"Failed to load offline credentials: {e}")
            return None

        if not isinstance(data, dict):
            return None
        if str(data.get("username") or "").strip() != str(username).strip():
            return None
        if not data.get("hash"):
            return None
        if "password" in data:
            data.pop("password", None)
            try:
                SecureCache(USER_CACHE_FILE).save(data)
            except Exception as exc:
                logger.warning(f"Failed to purge legacy offline password cache: {exc}")
        return data

    def _parse_cached_datetime(self, raw_value):
        if not raw_value:
            return None
        try:
            parsed = datetime.fromisoformat(str(raw_value))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _parse_cached_date(self, raw_value):
        if not raw_value:
            return None
        try:
            return datetime.fromisoformat(str(raw_value)).date()
        except (TypeError, ValueError):
            return None

    def _apply_active_register_snapshot(self, snapshot):
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        self.active_register_id = self._coerce_int(snapshot.get("id"))
        self.active_register_location_id = self._coerce_int(snapshot.get("location_id"))
        self.active_register_opened_at = snapshot.get("opened_at")
        self.active_register_is_open = bool(snapshot.get("is_open") and self.active_register_id)

    def _evaluate_offline_plan_access(self, cached_user):
        if not isinstance(cached_user, dict):
            return False, "Offline access requires a recent online login."

        status = str(cached_user.get("plan_status") or cached_user.get("status") or "active").strip().lower()
        if status and status != "active":
            return False, "Account is suspended. Renew your subscription online to continue."

        plan = str(cached_user.get("plan") or "").strip().upper()
        now_utc = datetime.now(timezone.utc)

        if plan == "TRIAL":
            plan_end = self._parse_cached_datetime(cached_user.get("plan_end"))
            if not plan_end or now_utc > plan_end:
                return False, "Your trial has expired. Reconnect and subscribe to continue."
            return True, ""

        if plan == "PRO":
            pro_expires = self._parse_cached_date(cached_user.get("pro_expires"))
            plan_end = self._parse_cached_datetime(cached_user.get("plan_end"))
            if pro_expires and now_utc.date() > pro_expires:
                return False, "Your subscription expired. Reconnect and renew to continue."
            if plan_end and now_utc > plan_end:
                return False, "Your subscription expired. Reconnect and renew to continue."
            if not pro_expires and not plan_end:
                return False, "Offline access needs a recent plan check. Please sign in online first."
            return True, ""

        return False, "QuickStock PRO subscription required. Reconnect and renew to continue."

    def save_offline_credentials(self, username, password_hash, role, api_token=None, password=None, plan_snapshot=None):
        """Save an offline login snapshot without storing recoverable secrets."""
        cache_data = {
            "username": username,
            "hash": password_hash,
            "role": role,
            "is_admin": str(role).lower() in {"admin", "superuser"},
            "user_id": self.current_user_id,
            "tenant_id": self.current_tenant_id,
            "operator_status": self.current_operator_status,
            "default_location_id": self.default_location_id,
            "active_register": {
                "id": self.active_register_id,
                "location_id": self.active_register_location_id,
                "opened_at": self.active_register_opened_at,
                "is_open": self.active_register_is_open,
            },
            "sync_date": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        if isinstance(plan_snapshot, dict):
            cache_data.update({
                "plan": str(plan_snapshot.get("plan") or "").upper() or None,
                "plan_status": str(plan_snapshot.get("status") or "").strip().lower() or None,
                "plan_end": plan_snapshot.get("plan_end"),
                "pro_expires": plan_snapshot.get("pro_expires"),
            })
        try:
            cache_obj = SecureCache(USER_CACHE_FILE)
            cache_obj.save(cache_data)
        except Exception as e:
            logger.error(f"Failed to securely save credentials: {e}")

    def login_window(self, root):
        self.login_win = tk.Toplevel(root)
        self.login_win.title("QuickStock JA Login")
        self.login_win.geometry("380x380")
        self.login_win.resizable(False, False)
        self.login_win.grab_set()
        palette, content = self._desktop_form_shell(self.login_win, "QuickStock JA Login", "380x380")
        
        # Ensures app closes if login is force-closed``
        self.login_win.protocol("WM_DELETE_WINDOW", root.destroy)

        self._desktop_label(content, "QuickStock JA", palette, surface="card", role="title", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        self._desktop_label(content, "Sign in to sync with the online workspace or continue from the offline cache.", palette, surface="card", role="muted", wraplength=300, justify="left").pack(anchor="w", pady=(4, 18))
        
        self._desktop_label(content, "Username", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.username_entry = self._desktop_entry(content, palette, width=25)
        self.username_entry.pack(fill="x", pady=(5, 12), ipady=9)
        self.username_entry.focus_set()

        self._desktop_label(content, "Password", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.password_entry = self._desktop_entry(content, palette, show="*", width=25)
        self.password_entry.pack(fill="x", pady=(5, 12), ipady=9)

        # Allow pressing "Enter" to login
        self.password_entry.bind('<Return>', lambda event: self.attempt_login())

        self._desktop_button(content, "Login", self.attempt_login, palette, kind="primary", cursor="hand2").pack(fill="x", pady=(12, 0))

    def attempt_login(self):
        username = self.username_entry.get().strip()
        password = self.password_entry.get().strip()

        if not username or not password: # Check for empty fields
            messagebox.showwarning("Input Error", "Please enter both username and password.")
            return
        
        # Reset ONLY local session state (do NOT wipe token logic here)
        self._session_invalid = False

        # 1. Try API login first
        api_resp = self._api_request(
            "POST",
            "/api/login/",
            {
                "username": username,
                "password": password
            }
        )

        # Handle API login response
        if api_resp and api_resp.get("ok") is False:
            self.api_status = "AUTH_FAILED"
            messagebox.showerror(
                "Login Failed",
                api_resp.get("message", "Invalid credentials."),
                parent=self.login_win
            )
            return
        
        # Network failure (api_resp is None)
        if not api_resp:
            self.api_status = "SERVER_DOWN"
            offline_user = self._load_offline_credentials(username)
            if offline_user:
                try:
                    stored_hash = offline_user.get("hash")
                    if stored_hash and handler.verify(password, stored_hash):
                        has_access, access_message = self._evaluate_offline_plan_access(offline_user)
                        if not has_access:
                            self.api_status = "PLAN_EXPIRED"
                            messagebox.showerror(
                                "Offline Access Restricted",
                                access_message,
                                parent=self.login_win
                            )
                            return
                        self.api_token = None
                        self.current_username = username
                        self.current_role = offline_user.get("role") or "manager"
                        self.current_user_id = self._coerce_int(offline_user.get("user_id"))
                        self.current_tenant_id = self._coerce_int(offline_user.get("tenant_id"))
                        self.current_operator_status = offline_user.get("operator_status") or "active"
                        self.default_location_id = self._coerce_int(offline_user.get("default_location_id"))
                        self._apply_active_register_snapshot(offline_user.get("active_register"))
                        self.api_status = "OFFLINE_CACHE"

                        try:
                            self.save_offline_credentials(
                                username,
                                stored_hash,
                                self.current_role,
                                plan_snapshot=offline_user,
                            )
                        except Exception:
                            pass

                        self.status_label.config(
                            text=f"User: {username} | Role: {self.current_role.upper()} | OFFLINE"
                        )
                        self.finalize_login()
                        return
                except Exception as e:
                    logger.warning(f"Offline login verification failed: {e}")

            messagebox.showerror(
                "Network Error",
                f"Cannot reach server at {self.api_base_url}.\n\n"
                "Possible reasons:\n"
                "1. No internet connection\n"
                "2. Server is offline\n"
                "3. Incorrect API URL in settings\n\n"
                "Attempting to use offline mode if local data is available.",
                parent=self.login_win
            )
            return

        # SUCCESS PATH
        if api_resp.get("ok"):
            user = api_resp.get("user", {})

            self.api_token = api_resp.get("token")
            self.current_username = username
            self.current_role = user.get("role") or "cashier"
            self.current_user_id = user.get("id")
            self.current_tenant_id = self._coerce_int(user.get("tenant_id"))
            self.current_operator_status = user.get("operator_status") or "active"
            self.default_location_id = self._coerce_int(user.get("default_location_id"))
            self._apply_active_register_snapshot(user.get("active_register"))
            self.api_status = "ONLINE"

            # persist token safely
            self.save_api_token(self.api_token)

            # store offline credentials
            try:
                pwd_hash = handler.hash(password)
                self.save_offline_credentials(username, pwd_hash, self.current_role, plan_snapshot=user)
            except Exception:
                pass

            self.status_label.config(
                text=f"User: {username} | Role: {self.current_role.upper()} | ONLINE"
            )

            self.finalize_login()
            return

        # Fallback error (should rarely hit if API response structure is consistent)
        # fallback error (should rarely hit)
        messagebox.showerror(
            "Login Failed",
            "Unexpected login error.",
            parent=self.login_win
        )


    def finalize_login(self):
        if hasattr(self, 'login_win') and self.login_win.winfo_exists():
            self.login_win.destroy()

        # Use the shared inventory file so the desktop and web apps stay aligned.
        storage.set_data_file(SHARED_DATA_FILE)

        # Load local data
        logger.debug(f"Attempting to load local data from {storage.get_data_file()}")
        inventory, receipts, settings = storage.load_data()
        self.system.inventory = inventory or []
        self.system.receipts = receipts or []

        if settings:
            self.system.settings.update(settings)

        logger.info(f"Loaded {len(self.system.inventory)} inventory items, {len(self.system.receipts)} receipts, and settings from local storage.")
        self._load_business_profile()
        self.apply_permissions()

        # IMPORTANT: define safe sync behavior
        def on_login_complete():
            self._set_default_active_location()

            # only sync if token exists
            if self.api_token:
                self.sync_inventory(
                    callback=lambda: self.root.after(0, self.update_dashboard_stats)
                )
            else:
                self.update_dashboard_stats()

        # API sync ONLY if token exists
        if self.api_token:
            self.sync_reference_data(callback=on_login_complete)
        else:
            on_login_complete()

        # Save local state (safe)
        try:
            storage.save_data(
                self.system.inventory,
                self.system.receipts,
                self.system.settings
            )
        except Exception:
            pass
        self.refresh_status(connected=self.api_status == "ONLINE")

        self._start_role_refresh()

    def apply_permissions(self):
        """Hides or shows buttons based on role and ensures layout consistency."""
        
        # 1. Define the Coordinate Map for the 3-Group Logic
        # This ensures buttons never 'stack' or move when switching users.
        button_coords = {
            "Operations": (0, 0), "Reports": (0, 1),
            "Settings": (1, 0), "Switch User": (1, 1),
            "Online": (2, 0), "Users": (2, 1),
            "Exit": (3, 0)
        }

        # 2. Updated Permission Lists (Including your new 2026 tools)
        role_permissions = {
            "superuser": list(button_coords.keys()),
            "admin": list(button_coords.keys()),
            "manager": list(button_coords.keys()),
            "cashier": list(button_coords.keys()),
        }

        # Get allowed list based on current_role (fallback to cashier)
        role = self.current_role.lower() if self.current_role else "cashier"
        allowed = role_permissions.get(role, role_permissions["cashier"])

        # 3. Apply the Grid
        for btn in self.all_buttons:
            btn_text = btn.cget("text")
            card = getattr(btn, "_home_card", btn)
            
            if btn_text in allowed and btn_text in button_coords:
                r, c = button_coords[btn_text]
                # Re-grid with explicit coordinates to prevent hidden cards from drifting.
                card.grid(row=r, column=c, sticky="nsew", padx=8, pady=8)
            else:
                card.grid_remove()
        self._update_home_dashboard_widgets()

    def _start_role_refresh(self):
        if getattr(self, "_role_refresh_started", False):
            return
        self._role_refresh_started = True
        self.root.after(3000, self._refresh_role_from_server)

    def _refresh_role_from_server(self):
        try:
            new_role = None
            new_default_location_id = None
            if self.api_token:
                resp = self._api_request("GET", "/api/profile/")
                if resp and resp.get("ok"):
                    user_info = resp.get("user") or {}
                    new_role = user_info.get("role")
                    new_default_location_id = self._coerce_int(user_info.get("default_location_id"))
                    self.current_tenant_id = self._coerce_int(user_info.get("tenant_id"))
                    self.current_operator_status = user_info.get("operator_status") or self.current_operator_status
                    self._apply_active_register_snapshot(user_info.get("active_register"))
            # Offline case: Stick to the currently cached role
            # and skip the DB query entirely to avoid the error loop.

            if new_role and new_role != self.current_role:
                self.current_role = new_role
                if self.current_username:
                    status_mode = "ONLINE" if self.api_token else "OFFLINE"
                    self.status_label.config(
                        text=f"User: {self.current_username} | Role: {self.current_role.upper()} | {status_mode}"
                    )
                self.apply_permissions()

        except Exception:
            pass
        finally:
            # Keep polling
            self.root.after(8000, self._refresh_role_from_server)

    def logout(self):
        if messagebox.askyesno("Logout", "Are you sure you want to switch users?"):
            self.delete_api_token()
            self._clear_local_caches()
            storage.set_data_file(SHARED_DATA_FILE)
            for widget in self.button_frame.winfo_children():
                widget.grid()
            self.current_role = None
            self.current_username = None
            self.current_user_id = None
            self.current_tenant_id = None
            self.current_operator_status = None
            self.default_location_id = None
            self._apply_active_register_snapshot(None)
            self.active_location_id = None
            self.active_location_name = "All Locations"
            self.api_status = "UNKNOWN"
            self.status_label.config(text="Waiting for Login...", fg="#58D68D")
            self._update_home_dashboard_widgets()
            self.login_window(self.root)

    def _clear_local_caches(self):
        for path in [LOCAL_CACHE_FILE, USER_CACHE_FILE, LOCATION_CACHE_FILE, SUPPLIER_CACHE_FILE]:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
        try:
            for fname in os.listdir(DATA_DIR):
                if fname.startswith("inventory_cache_") and fname.endswith(".json"):
                    os.remove(os.path.join(DATA_DIR, fname))
        except Exception:
            pass



    def load_logo(self, root, size=(300, 300), pack_options=None, grid_options=None):
        from pathlib import Path
        logo_file = Path(resource_path("QuickStock_Logo.jpg"))

        if not logo_file.exists():
            print(f"Logo not found at {logo_file}")
            return

        # Use CTkImage
        ctk_image = CTkImage(Image.open(logo_file), size=size)

        self.logo_label = CTkLabel(root, image=ctk_image, text="")
        self.logo_label.image = ctk_image  # keep a reference!
        if grid_options is not None:
            self.logo_label.grid(**grid_options)
        else:
            self.logo_label.pack(**(pack_options or {"pady": 10}))
        return self.logo_label


    def update_dashboard_stats(self):
        """
        Refreshes daily sales, calculates tax-adjusted profit,
        and updates the USD Reinvestment Goal (2026 Strategy).
        """
        # 1. Clear existing timer to prevent memory leaks
        if hasattr(self, "_refresh_id"):
            self.root.after_cancel(self._refresh_id)

        today_total = 0.0
        today_str = datetime.now().strftime("%Y-%m-%d")
        tax_rate = self.system.settings.get("tax_rate", 0.15)
        tax_label = self.system.settings.get("tax_label", "GCT")
        location_text = self.active_location_name or "All Locations"

        try:
            # 2. Reload receipts and settings from disk — but NOT inventory.
            # Inventory is owned by the sync worker; overwriting it here would
            # discard freshly synced data with whatever is on disk.
            loaded = storage.load_data()
            if isinstance(loaded, tuple) and len(loaded) == 2:
                _, receipts = loaded
                settings = self.system.settings
            else:
                _, receipts, settings = loaded

            # Only update receipts and settings — leave inventory untouched
            self.system.receipts = receipts or []
            if settings:
                self.system.settings.update(settings)

            # 3. Sum daily sales (excluding archived/cleared data)
            for receipt in self.system.receipts:
                if str(receipt.get("archived")) == "True":
                    continue
                r_date = str(receipt.get("Date", ""))
                if r_date.startswith(today_str):
                    try:
                        today_total += float(receipt.get("Total Bill", 0))
                    except (ValueError, TypeError):
                        continue

            # 4. Strategy Calculations (2026-2027 Funding Plan)
            net_revenue = today_total / (1 + tax_rate)
            usd_allocation = net_revenue * 0.25

            # 5. Dynamic UI Update based on Role
            role = getattr(self, "current_role", "cashier").lower()

            if role in {"admin", "superuser"}:
                status_text = (
                    f"{role.title()} | Today's {tax_label} Sales: ${today_total:,.2f} | "
                    f"Region: {self.system.settings.get('country')} | Location: {location_text}"
                )
                status_color = "#58D68D"
            elif role == "manager":
                status_text = (
                    f"Manager | Sales Active: ${today_total:,.2f} | "
                    f"Region: {self.system.settings.get('country')} | Location: {location_text}"
                )
                status_color = "#5DADE2"
            else:
                status_text = f"QuickStock JA Active | {today_str} | Location: {location_text}"
                status_color = "#F39C12"

            self.status_label.config(text=status_text, fg=status_color)
            self._update_home_dashboard_widgets()

        except Exception as e:
            print(f"Error updating dashboard: {e}")
            self.status_label.config(text="Dashboard Update Error", fg="#E74C3C")
            self._update_home_dashboard_widgets()

        # 6. Auto-refresh every 30 seconds
        self._refresh_id = self.root.after(30000, self.update_dashboard_stats)

    @staticmethod
    def log_transaction(message):
        with open("activity_log.txt", "a") as f:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {message}\n")

    def setup_dark_mode(self):
        """Set up dark mode theme for buttons and background"""
        self.style.configure("TButton",
                             background="#2E3B4E",
                             foreground="white",
                             padding=6,
                             relief="flat",
                             font=("Helvetica", 12))

    def setup_light_mode(self):
        """Set up light mode theme for buttons and background"""
        self.style.configure("TButton",
                             background="#F0F0F0",
                             foreground="black",
                             padding=6,
                             relief="flat",
                             font=("Helvetica", 12))


    def toggle_theme(self):
        """Toggle between light and dark and persist the preference offline."""
        current_pref = (self.system.settings.get("theme") or "system").strip().lower()
        next_pref = "dark" if current_pref != "dark" else "light"
        self.system.settings["theme"] = next_pref
        self._apply_theme_preference()
        self._persist_offline_settings()
        print(f"Theme switched to {next_pref.title()}")

    def delete_item(self):
        """
        Delete an item following the Local-First rule:
        1. Update GUI and Cache immediately.
        2. Attempt MySQL delete in background.
        3. Queue for sync if offline.
        """
        selected_name = self.get_selected_item_name()
        if not selected_name:
            messagebox.showwarning("Selection", "Please select an item to delete.")
            return

        # Locate the item in active memory (handles both casing conventions)
        item = next((i for i in self.system.inventory 
                    if i.get("name") == selected_name or i.get("Name") == selected_name), None)
        
        if not item:
            messagebox.showerror("Error", "Item not found in local inventory.")
            return

        # Normalize the SKU for the database query
        sku = item.get("sku") or item.get("SKU")
        
        if not messagebox.askyesno("Confirm", f"Are you sure you want to delete '{selected_name}'?"):
            return

        # --- STEP 1: IMMEDIATE LOCAL REMOVAL ---
        # We update the UI first so the app feels 'live' and responsive
        try:
            # Filter memory
            self.system.inventory = [i for i in self.system.inventory if (i.get("sku") or i.get("SKU")) != sku]
            
            # Persist change immediately
            storage.save_data(
                self.system.inventory,
                self.system.receipts,
                self.system.settings
            )
            if self.api_token:
                self.sync_inventory()
            
            # Refresh the Treeview/Table
            if hasattr(self, 'display_inventory'):
                self.display_inventory()
            elif hasattr(self, 'refresh_table'):
                self.refresh_table()
                
            print(f"Local-First Success: SKU {sku} removed from GUI and Cache.")
        except Exception as e:
            messagebox.showerror("Local Error", f"Failed to update local view: {e}")
            return

        # --- STEP 2: DATABASE SYNCHRONIZATION ---
        queued_for_sync = False
        if self.api_token:
            resp = self._api_request("DELETE", f"/api/inventory/{sku}/")
            if not resp or not resp.get("ok"):
                self.add_to_sync_queue({"sku": sku}, action="DELETE")
                queued_for_sync = True
        else:
            self.add_to_sync_queue({"sku": sku}, action="DELETE")
            queued_for_sync = True
            logger.info("Queued inventory delete for later sync because no API session is active.")

        if queued_for_sync:
            messagebox.showinfo("Queued", f"Item '{selected_name}' was removed locally and will sync when the desktop reconnects.")
        else:
            messagebox.showinfo("Success", f"Item '{selected_name}' removed.")

    def add_item_window(self):
        win = tk.Toplevel(self.root)
        palette, shell, body = self._build_desktop_surface(
            win,
            "Add Item",
            "Scan an existing SKU or build a new inventory item with the same offline-friendly workflow.",
            geometry="760x760",
        )

        banner = self._build_desktop_banner(
            body,
            palette,
            "Inventory updates stay local-first",
            "If the web API is unavailable, this form still saves locally and queues the item for sync.",
            badge_text=f"Queued actions: {len(self._read_pending_queue())}",
        )
        banner.pack(fill="x", pady=(0, 14))

        content = tk.Frame(body, bg=palette["bg"])
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.columnconfigure(1, weight=1)

        search_card = self._build_desktop_card(content, palette)
        search_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 12))
        form_card = self._build_desktop_card(content, palette)
        form_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0), pady=(0, 12))
        actions_card = self._build_desktop_card(content, palette)
        actions_card.grid(row=1, column=0, columnspan=2, sticky="ew")

        tk.Label(search_card, text="Find Existing Item", font=("Segoe UI", 13, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        tk.Label(search_card, text="Choose a saved item if the SKU is unknown, or scan directly below.", font=("Segoe UI", 10), bg=palette["card"], fg=palette["muted"], justify="left").pack(anchor="w", pady=(4, 12))

        search_var = tk.StringVar()
        search_combo = ttk.Combobox(search_card, textvariable=search_var, width=35)
        search_combo.pack(fill="x", pady=(0, 14))

        item_names = sorted(list(set(
            i.get("name") or i.get("Name") for i in self.system.inventory if i.get("name") or i.get("Name")
        )))
        search_combo["values"] = item_names

        tk.Label(search_card, text="SKU / Barcode", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        self.add_sku_entry = tk.Entry(
            search_card,
            font=("Segoe UI", 12),
            bg=palette["input_bg"],
            fg=palette["input_fg"],
            insertbackground=palette["input_fg"],
            relief="flat",
            bd=0,
        )
        self.add_sku_entry.pack(fill="x", pady=(6, 12), ipady=10)
        self.add_sku_entry.focus_set()

        location_map = {loc.get("name"): loc.get("id") for loc in self._selectable_locations()}
        tk.Label(search_card, text="Location", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        location_var = tk.StringVar()
        location_combo = ttk.Combobox(search_card, textvariable=location_var, width=35, state="readonly")
        location_combo["values"] = list(location_map.keys())
        if self.active_location_name in location_map:
            location_var.set(self.active_location_name)
        elif location_combo["values"]:
            location_var.set(location_combo["values"][0])
        location_combo.pack(fill="x", pady=(6, 0))

        fields = {}
        tk.Label(form_card, text="Item Details", font=("Segoe UI", 13, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        tk.Label(form_card, text="Complete the fields below. Existing items will auto-fill when the SKU matches.", font=("Segoe UI", 10), bg=palette["card"], fg=palette["muted"], justify="left").pack(anchor="w", pady=(4, 12))
        for label in ("Category", "Name", "Cost", "Price", "Amount"):
            tk.Label(form_card, text=label, font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
            entry = tk.Entry(
                form_card,
                font=("Segoe UI", 11),
                bg=palette["input_bg"],
                fg=palette["input_fg"],
                insertbackground=palette["input_fg"],
                relief="flat",
                bd=0,
            )
            entry.pack(fill="x", pady=(6, 10), ipady=9)
            fields[label] = entry

        def check_existing_sku(event=None):
            scanned_sku = self.add_sku_entry.get().strip()
            if not scanned_sku:
                return

            item = next((i for i in self.system.inventory 
                        if str(i.get("sku") or i.get("SKU")) == scanned_sku), None)

            if item:
                if winsound:
                    winsound.Beep(1000, 200)

                data_map = {
                    "Category": item.get("category") or item.get("Category") or "General",
                    "Name":     item.get("name") or item.get("Name") or "Unknown",
                    "Cost":     item.get("cost") or item.get("Cost") or 0.0,
                    "Price":    item.get("price") or item.get("Price") or 0.0,
                    "Amount":   item.get("amount") if item.get("amount") is not None else item.get("Amount") or 0
                }

                for label, value in data_map.items():
                    fields[label].delete(0, tk.END)
                    if label in ["Cost", "Price"]:
                        fields[label].insert(0, f"{float(value):.2f}")
                    else:
                        fields[label].insert(0, value)

                fields["Amount"].focus_set()
                fields["Amount"].selection_range(0, tk.END)

                tax_label = self.system.settings.get("tax_label", "Tax")
                self.status_label.config(
                    text=f"Editing: {data_map['Name']} | Prices exclude {tax_label}", 
                    fg="#5DADE2"
                )
            else:
                if winsound:
                    winsound.Beep(500, 200)

                self.status_label.config(text="New SKU detected. Please enter details.", fg="#F39C12")
                fields["Category"].focus_set()

        def on_name_selected(event):
            selected_name = search_combo.get()
            item = next((i for i in self.system.inventory if (i.get("name") == selected_name or i.get("Name") == selected_name)), None)

            if item:
                self.add_sku_entry.delete(0, tk.END)
                sku_val = item.get("sku") or item.get("SKU") or ""
                self.add_sku_entry.insert(0, str(sku_val))
                check_existing_sku()

        search_combo.bind("<<ComboboxSelected>>", on_name_selected)
        self.add_sku_entry.bind("<Return>", check_existing_sku)

        category_names = [cat.get("name") for cat in self.categories]
        fields["Category"].destroy()
        fields["Category"] = ttk.Combobox(form_card, values=category_names, width=35)
        fields["Category"].pack(fill="x", pady=(6, 10), ipady=4, before=fields["Name"])

        def save_item():
            try:
                if not self.current_user_id:
                    messagebox.showerror("Error", "No active user. Please login online first.")
                    return
                sku = self.add_sku_entry.get().strip().upper()
                category = fields["Category"].get().strip()
                name = fields["Name"].get().strip()
                cost = float(fields["Cost"].get() or 0)
                price = float(fields["Price"].get() or 0)
                amount = self._sanitize_db_quantity(fields["Amount"].get() or 0, clamp=False)

                if not name:
                    messagebox.showerror("Error", "Name is required!")
                    return

                category_id = self._get_or_create_category_id(category)
                if category_id is None and self.api_token:
                    messagebox.showerror("Error", f"Failed to get or create category '{category}'. Item cannot be saved online.")
                    return

                if not sku:
                    prefix = self._category_sku_prefix(category)
                    timestamp = datetime.now().strftime("%y%m%d")
                    suffix = "".join(random.choices("123456789ABCDEF", k=3))
                    sku = f"{prefix}-{timestamp}-{suffix}"
                    messagebox.showinfo("Auto-SKU", f"Generated New SKU: {sku}")

                location_id = location_map.get(location_var.get()) if location_map else None
                sync_payload = {
                    "sku": sku,
                    "name": name,
                    "cost_price": cost,
                    "price": price,
                    "quantity": amount,
                    "category": category,
                    "user_id": self.current_user_id,
                    "location_id": location_id,
                    "category_id": category_id,
                }
                api_synced = False

                if self.api_token:
                    resp = self._api_request("POST", "/api/inventory/push/", sync_payload)
                    if resp and resp.get("ok"):
                        api_synced = True
                        print(f"Synced SKU {sku} to API.")
                    else:
                        logger.error(f"API Sync Failed for SKU {sku}: {resp.get('message', 'Unknown error')}")
                        self._queue_action("UPSERT_ITEM", sync_payload)
                else:
                    self._queue_action("UPSERT_ITEM", sync_payload)

                if category_id:
                    sync_payload["category_id"] = category_id

                existing_item = next(
                    (i for i in self.system.inventory if str(i.get("sku") or i.get("SKU")) == sku),
                    None,
                )

                item_data_update = {
                    "category": category,
                    "Category": category,
                    "name": name,
                    "Name": name,
                    "cost_price": cost,
                    "Cost": cost,
                    "price": price,
                    "Price": price,
                    "quantity": amount,
                    "Amount": amount,
                    "sku": sku
                }

                if existing_item:
                    existing_item.update(item_data_update)
                else:
                    self.system.inventory.append(item_data_update)

                target_item = next(
                    (i for i in self.system.inventory if str(i.get("sku") or i.get("SKU")) == sku),
                    None,
                )
                if target_item:
                    self._touch_inventory_item(target_item)
                    sync_payload["last_modified"] = target_item.get("last_modified")
                    sync_payload["sync_token"] = target_item.get("sync_token")

                self.system.inventory = self._normalize_items(self.system.inventory)
                storage.save_data(
                    self.system.inventory,
                    self.system.receipts,
                    self.system.settings
                )
                self.save_inventory_cache(self.system.inventory)

                if api_synced:
                    self.sync_inventory()
                if hasattr(self, 'display_inventory'):
                    self.display_inventory()

                if str(self.current_role).lower() in {"admin", "superuser"}:
                    win.destroy()
                else:
                    self.add_sku_entry.delete(0, tk.END)
                    for key in fields: fields[key].delete(0, tk.END)
                    search_var.set("")
                    self.add_sku_entry.focus_force()

                if api_synced:
                    messagebox.showinfo("Success", f"Item '{name}' saved and synced.")
                else:
                    messagebox.showwarning("Offline Save", f"Item '{name}' saved locally and queued for sync.")

            except ValueError:
                messagebox.showerror(
                    "Error",
                    f"Numeric error: Cost and Price must be numbers, and Amount must be between 0 and {MAX_DB_QUANTITY:,}.",
                )

        tk.Button(
            actions_card,
            text="Save to Inventory",
            command=save_item,
            bg=palette["primary"],
            fg=palette["primary_text"],
            activebackground=palette["primary_active"],
            font=("Segoe UI", 11, "bold"),
            relief="flat",
            padx=14,
            pady=12,
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        def clear_form():
            self.add_sku_entry.delete(0, tk.END)
            for key in fields:
                fields[key].delete(0, tk.END)
            search_var.set("")
            self.add_sku_entry.focus_set()

        tk.Button(
            actions_card,
            text="Clear Form",
            command=clear_form,
            bg=palette["subtle"],
            fg=palette["subtle_text"],
            activebackground=palette["subtle_active"],
            relief="flat",
            padx=14,
            pady=12,
        ).pack(side="left", fill="x", expand=True, padx=(8, 0))

    def save_inventory_cache(self, items, from_api=False):
        """Standardize DB items into a local cache for offline + core usage."""
        try:
            # Only overwrite with empty if it explicitly came from API and was empty
            # Or if it's not from API (meaning it's a local update)
            if not from_api and not items and os.path.exists(self._inventory_cache_path()):
                logger.info("Skipping cache overwrite with empty list as it's not from API.")
                return

            normalized = self._normalize_items(items)
            self._write_json_atomic(self._inventory_cache_path(), normalized, indent=4)
            
        except Exception as e:
            print(f"Mapping Error in save_inventory_cache: {e}")

    def _build_item_sync_payload_from_local_state(self, sku: str, location_id: int | None = None) -> Optional[Dict[str, Any]]:
        """Build an inventory upsert payload from the current local item state."""
        normalized_sku = str(sku or "").strip().upper()
        if not normalized_sku:
            return None

        local_item = next(
            (i for i in self.system.inventory if str(i.get("sku") or i.get("SKU") or "").strip().upper() == normalized_sku),
            None,
        )
        if not local_item:
            return None

        category_name = local_item.get("category") or local_item.get("Category") or "General"
        category_id = self._get_or_create_category_id(category_name) or 1

        quantity = self._sanitize_db_quantity(local_item.get("amount") or local_item.get("Amount") or 0, clamp=True)
        return {
            "sku": normalized_sku,
            "name": local_item.get("name") or local_item.get("Name") or "Unknown",
            "barcode": local_item.get("barcode") or local_item.get("Barcode") or "",
            "cost_price": local_item.get("cost") or local_item.get("Cost") or 0,
            "price": local_item.get("price") or local_item.get("Price") or 0,
            "quantity": quantity,
            "category_id": category_id,
            "location_id": location_id if location_id is not None else self.active_location_id,
        }

    def _build_sale_payload(
        self,
        total_price: float,
        gct_amount: float,
        discount_amount: float = 0.0,
        subtotal: Optional[float] = None,
        items_sold: Optional[list[dict]] = None,
        tender: str = "cash",
        location_id: Optional[int] = None,
        customer_name: str = "",
        document_type: str = "Invoice",
        amount_tendered: Optional[float] = None,
        credit_applied: float = 0.0,
        change_due: float = 0.0,
        credited_overpayment: float = 0.0,
        occurred_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build checkout contract v1 for immediate submission or durable replay."""
        location_id = location_id if location_id is not None else (
            self.active_location_id if self.active_location_id is not None else self.default_location_id
        )
        tax_rate = Decimal(str(self.system.settings.get("tax_rate", 0.15))).quantize(CHECKOUT_RATE_QUANTUM)
        normalized_customer_name = self._normalize_customer_name(customer_name)
        customer = self._get_customer_record(normalized_customer_name) or {}
        line_items = []
        for row in items_sold or []:
            sku = str(row.get("sku") or row.get("SKU") or "").strip().upper()
            local_item = next(
                (
                    item for item in self.system.inventory
                    if str(item.get("SKU") or item.get("sku") or "").strip().upper() == sku
                ),
                {},
            )
            is_taxable = bool(row.get("is_taxable", local_item.get("is_taxable", True)))
            line_items.append(
                {
                    "product_id": row.get("product_id") or row.get("id") or local_item.get("id") or local_item.get("product_id"),
                    "sku": sku,
                    "quantity": row.get("quantity", row.get("Quantity")),
                    "unit_price": self._checkout_decimal_string(
                        row.get("unit_price", row.get("Price", local_item.get("Price", local_item.get("price", 0))))
                    ),
                    "unit_cost": self._checkout_decimal_string(
                        row.get("unit_cost", row.get("Cost", local_item.get("Cost", local_item.get("cost", 0))))
                    ),
                    "is_taxable": is_taxable,
                    "tax_rate": self._checkout_rate_string(tax_rate if is_taxable else Decimal("0.0000")),
                    "status": str(row.get("status") or local_item.get("status") or "active").strip().lower(),
                    "is_deleted": bool(row.get("is_deleted", local_item.get("is_deleted", False))),
                }
            )

        subtotal_value = total_price - gct_amount + discount_amount if subtotal is None else subtotal
        amount_tendered = total_price if amount_tendered is None else amount_tendered
        return {
            "contract_version": CHECKOUT_CONTRACT_VERSION,
            "offline_client_ref": f"DESKTOP-{uuid.uuid4()}",
            "occurred_at": occurred_at or datetime.now(timezone.utc).isoformat(),
            "operator": {
                "id": self.current_user_id,
                "role": str(self.current_role or "").strip().lower(),
            },
            "tenant": {"id": self.current_tenant_id},
            "location": {"id": location_id},
            "register": {
                "id": self.active_register_id,
                "location_id": self.active_register_location_id,
                "opened_at": self.active_register_opened_at,
            },
            "customer": {
                "id": customer.get("id"),
                "name": normalized_customer_name,
            },
            "currency": str(self.system.settings.get("currency") or "JMD").strip().upper(),
            "payment": {
                "method": str(tender or "").strip().lower(),
                "amount_tendered": self._checkout_decimal_string(amount_tendered),
                "account_credit": self._checkout_decimal_string(credit_applied),
                "change_due": self._checkout_decimal_string(change_due),
                "credited_overpayment": self._checkout_decimal_string(credited_overpayment),
            },
            "totals": {
                "subtotal": self._checkout_decimal_string(subtotal_value),
                "discount": self._checkout_decimal_string(discount_amount),
                "tax": self._checkout_decimal_string(gct_amount),
                "total": self._checkout_decimal_string(total_price),
            },
            "tax": {
                "label": str(self.system.settings.get("tax_label") or "GCT").strip().upper(),
                "rate": self._checkout_rate_string(tax_rate),
                "inclusive": True,
            },
            "line_items": line_items,
            "metadata": {
                "source": "desktop",
                "queue_schema_version": QUEUE_SCHEMA_VERSION,
                "document_type": str(document_type or "Invoice").strip() or "Invoice",
            },
        }

    def _checkout_decimal_string(self, value):
        try:
            return str(Decimal(str(value)).quantize(CHECKOUT_MONEY_QUANTUM))
        except (InvalidOperation, TypeError, ValueError):
            return str(value or "")

    def _checkout_rate_string(self, value):
        try:
            return str(Decimal(str(value)).quantize(CHECKOUT_RATE_QUANTUM))
        except (InvalidOperation, TypeError, ValueError):
            return str(value or "")

    def _checkout_validation_error(self, code, field, message):
        return False, {"code": code, "field": field, "message": message}

    def _checkout_decimal_value(self, value, field, places=2):
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError(f"{field} must be a valid decimal.")
        if not parsed.is_finite() or max(0, -parsed.as_tuple().exponent) > places:
            raise ValueError(f"{field} has invalid precision.")
        return parsed.quantize(CHECKOUT_MONEY_QUANTUM if places == 2 else CHECKOUT_RATE_QUANTUM)

    def _validate_checkout_payload(self, payload, *, replay=False):
        if not isinstance(payload, dict) or payload.get("contract_version") != CHECKOUT_CONTRACT_VERSION:
            return self._checkout_validation_error(
                "UNSUPPORTED_CONTRACT_VERSION",
                "contract_version",
                f"Checkout contract version {CHECKOUT_CONTRACT_VERSION} is required.",
            )
        reference = str(payload.get("offline_client_ref") or "").strip()
        if not reference or len(reference) > 64 or not re.fullmatch(r"[A-Za-z0-9._:-]+", reference):
            return self._checkout_validation_error("INVALID_CLIENT_REFERENCE", "offline_client_ref", "A valid sale reference is required.")

        operator = payload.get("operator")
        tenant = payload.get("tenant")
        location = payload.get("location")
        register = payload.get("register")
        payment = payload.get("payment")
        totals = payload.get("totals")
        tax = payload.get("tax")
        customer = payload.get("customer") or {}
        metadata = payload.get("metadata")
        for field, value in (
            ("operator", operator),
            ("tenant", tenant),
            ("location", location),
            ("register", register),
            ("payment", payment),
            ("totals", totals),
            ("tax", tax),
            ("customer", customer),
            ("metadata", metadata),
        ):
            if not isinstance(value, dict):
                return self._checkout_validation_error("INVALID_PAYLOAD", field, f"{field} must be an object.")

        role = str(operator.get("role") or "").lower()
        if not self.current_user_id or operator.get("id") != self.current_user_id:
            return self._checkout_validation_error("OPERATOR_MISMATCH", "operator.id", "Checkout operator does not match the active user.")
        if role not in {"superuser", "admin", "manager", "cashier"} or role != str(self.current_role or "").lower():
            return self._checkout_validation_error("OPERATOR_UNAUTHORIZED", "operator.role", "Operator is not permitted to sell.")
        if str(self.current_operator_status or "active").lower() != "active":
            return self._checkout_validation_error("OPERATOR_INACTIVE", "operator", "Operator account is not active.")
        if not self.current_tenant_id or tenant.get("id") != self.current_tenant_id:
            return self._checkout_validation_error("TENANT_MISMATCH", "tenant.id", "Checkout tenant does not match the active workspace.")

        location_id = self._coerce_int(location.get("id"))
        known_location_ids = {self._coerce_int(entry.get("id")) for entry in (self.locations or [])}
        if not location_id or location_id not in known_location_ids:
            return self._checkout_validation_error("INVALID_LOCATION", "location.id", "Select a synchronized checkout location.")
        if self._coerce_int(register.get("location_id")) != location_id:
            return self._checkout_validation_error("REGISTER_LOCATION_MISMATCH", "register.location_id", "Register does not match the checkout location.")
        if not self._coerce_int(register.get("id")) or not register.get("opened_at"):
            return self._checkout_validation_error("REGISTER_UNAVAILABLE", "register", "An assigned open register is required.")
        if not replay and (
            not self.active_register_is_open
            or self._coerce_int(register.get("id")) != self._coerce_int(self.active_register_id)
        ):
            return self._checkout_validation_error("REGISTER_CLOSED", "register", "Open the assigned register before checkout.")

        if str(payload.get("currency") or "").upper() != "JMD":
            return self._checkout_validation_error("INVALID_CURRENCY", "currency", "Desktop checkout currency must be JMD.")
        if metadata.get("source") != "desktop" or metadata.get("queue_schema_version") != QUEUE_SCHEMA_VERSION:
            return self._checkout_validation_error("INVALID_QUEUE_SCHEMA", "metadata", "Checkout queue metadata is invalid.")
        if tax.get("inclusive") is not True:
            return self._checkout_validation_error("INVALID_TAX", "tax.inclusive", "Shelf prices must be tax-inclusive.")

        line_items = payload.get("line_items")
        if not isinstance(line_items, list) or not line_items:
            return self._checkout_validation_error("EMPTY_CART", "line_items", "Cart is empty.")
        seen_ids = set()
        seen_skus = set()
        gross_total = Decimal("0.00")
        net_subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")
        try:
            contract_tax_rate = self._checkout_decimal_value(tax.get("rate"), "tax.rate", places=4)
            expected_tax_rate = Decimal(str(self.system.settings.get("tax_rate", 0.15))).quantize(CHECKOUT_RATE_QUANTUM)
            if contract_tax_rate != expected_tax_rate or not Decimal("0.0000") <= contract_tax_rate <= Decimal("1.0000"):
                return self._checkout_validation_error("INVALID_TAX", "tax.rate", "Tax rate does not match the active location.")
            for index, line in enumerate(line_items):
                if not isinstance(line, dict):
                    return self._checkout_validation_error("INVALID_CART", f"line_items.{index}", "Cart line must be an object.")
                product_id = self._coerce_int(line.get("product_id"))
                sku = str(line.get("sku") or "").strip().upper()
                if not product_id:
                    return self._checkout_validation_error("MISSING_PRODUCT_ID", f"line_items.{index}.product_id", "Every cart line requires a product ID.")
                if not sku:
                    return self._checkout_validation_error("MISSING_SKU", f"line_items.{index}.sku", "Every cart line requires a SKU.")
                if product_id in seen_ids or sku in seen_skus:
                    return self._checkout_validation_error("DUPLICATE_ITEM", f"line_items.{index}", "Duplicate items are not allowed.")
                seen_ids.add(product_id)
                seen_skus.add(sku)
                quantity = line.get("quantity")
                if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
                    return self._checkout_validation_error("INVALID_QUANTITY", f"line_items.{index}.quantity", "Quantity must be greater than zero.")
                unit_price = self._checkout_decimal_value(line.get("unit_price"), f"line_items.{index}.unit_price")
                unit_cost = self._checkout_decimal_value(line.get("unit_cost"), f"line_items.{index}.unit_cost")
                line_tax_rate = self._checkout_decimal_value(line.get("tax_rate"), f"line_items.{index}.tax_rate", places=4)
                if unit_price <= 0 or unit_cost < 0:
                    return self._checkout_validation_error("INVALID_PRICE", f"line_items.{index}", "Price must be positive and cost cannot be negative.")
                if line.get("is_deleted") is not False or str(line.get("status") or "").lower() != "active":
                    return self._checkout_validation_error("ITEM_UNAVAILABLE", f"line_items.{index}", "Archived or deleted items cannot be sold.")
                local_item = next(
                    (
                        item for item in self.system.inventory
                        if self._coerce_int(item.get("id") or item.get("product_id")) == product_id
                        and str(item.get("SKU") or item.get("sku") or "").upper() == sku
                    ),
                    None,
                )
                if not local_item:
                    return self._checkout_validation_error("ITEM_NOT_FOUND", f"line_items.{index}", "Product is not present in the synchronized inventory cache.")
                if local_item.get("is_deleted") or str(local_item.get("status") or "active").lower() != "active":
                    return self._checkout_validation_error("ITEM_UNAVAILABLE", f"line_items.{index}", "Product is archived or deleted.")
                if unit_price != Decimal(str(local_item.get("Price", 0))).quantize(CHECKOUT_MONEY_QUANTUM):
                    return self._checkout_validation_error("STALE_PRICE", f"line_items.{index}.unit_price", "Product price changed. Synchronize and retry.")
                if unit_cost != Decimal(str(local_item.get("Cost", 0))).quantize(CHECKOUT_MONEY_QUANTUM):
                    return self._checkout_validation_error("STALE_COST", f"line_items.{index}.unit_cost", "Product cost changed. Synchronize and retry.")
                taxable = bool(local_item.get("is_taxable", True))
                expected_line_rate = contract_tax_rate if taxable else Decimal("0.0000")
                if line.get("is_taxable") is not taxable or line_tax_rate != expected_line_rate:
                    return self._checkout_validation_error("INVALID_TAX", f"line_items.{index}.tax_rate", "Product tax treatment is invalid.")
                if not replay and quantity > int(local_item.get("Amount") or 0):
                    return self._checkout_validation_error("INSUFFICIENT_STOCK", f"line_items.{index}.quantity", "Insufficient cached stock.")

                line_gross = (unit_price * quantity).quantize(CHECKOUT_MONEY_QUANTUM)
                if taxable:
                    line_net = (line_gross / (Decimal("1.0000") + contract_tax_rate)).quantize(CHECKOUT_MONEY_QUANTUM)
                    line_tax = (line_gross - line_net).quantize(CHECKOUT_MONEY_QUANTUM)
                else:
                    line_net = line_gross
                    line_tax = Decimal("0.00")
                gross_total += line_gross
                net_subtotal += line_net
                tax_total += line_tax

            supplied_subtotal = self._checkout_decimal_value(totals.get("subtotal"), "totals.subtotal")
            supplied_discount = self._checkout_decimal_value(totals.get("discount"), "totals.discount")
            supplied_tax = self._checkout_decimal_value(totals.get("tax"), "totals.tax")
            supplied_total = self._checkout_decimal_value(totals.get("total"), "totals.total")
            canonical_total = (gross_total - supplied_discount).quantize(CHECKOUT_MONEY_QUANTUM)
            if supplied_discount < 0 or supplied_discount > gross_total:
                return self._checkout_validation_error("INVALID_DISCOUNT", "totals.discount", "Discount is outside the allowed range.")
            if supplied_subtotal != net_subtotal.quantize(CHECKOUT_MONEY_QUANTUM):
                return self._checkout_validation_error("INVALID_TOTAL", "totals.subtotal", "Subtotal does not match the cart.")
            if supplied_tax != tax_total.quantize(CHECKOUT_MONEY_QUANTUM):
                return self._checkout_validation_error("INVALID_TAX", "totals.tax", "Tax does not match the cart.")
            if supplied_total != canonical_total or (supplied_subtotal + supplied_tax - supplied_discount).quantize(CHECKOUT_MONEY_QUANTUM) != supplied_total:
                return self._checkout_validation_error("INVALID_TOTAL", "totals.total", "Total does not match subtotal minus discount plus tax.")

            payment_method = str(payment.get("method") or "").strip().lower()
            if payment_method not in CHECKOUT_PAYMENT_METHODS:
                return self._checkout_validation_error("MISSING_PAYMENT", "payment.method", "Select a supported payment method.")
            amount_tendered = self._checkout_decimal_value(payment.get("amount_tendered"), "payment.amount_tendered")
            account_credit = self._checkout_decimal_value(payment.get("account_credit"), "payment.account_credit")
            change_due = self._checkout_decimal_value(payment.get("change_due"), "payment.change_due")
            overpayment_credit = self._checkout_decimal_value(payment.get("credited_overpayment"), "payment.credited_overpayment")
            if min(amount_tendered, account_credit, change_due, overpayment_credit) < 0:
                return self._checkout_validation_error("INVALID_PAYMENT", "payment", "Payment values cannot be negative.")
            funding = (amount_tendered + account_credit).quantize(CHECKOUT_MONEY_QUANTUM)
            if funding < supplied_total:
                return self._checkout_validation_error("UNDERPAYMENT", "payment.amount_tendered", "Payment does not cover the checkout total.")
            overage = (funding - supplied_total).quantize(CHECKOUT_MONEY_QUANTUM)
            expected_change = overage if payment_method == "cash" else Decimal("0.00")
            expected_credit = overage if payment_method != "cash" else Decimal("0.00")
            if change_due != expected_change or overpayment_credit != expected_credit:
                return self._checkout_validation_error("INVALID_CHANGE", "payment", "Change or overpayment does not match the payment.")
            if account_credit > 0 or overpayment_credit > 0:
                customer_id = self._coerce_int(customer.get("id"))
                customer_record = self._get_customer_record(customer.get("name"))
                if not customer_id or not customer_record or self._coerce_int(customer_record.get("id")) != customer_id:
                    return self._checkout_validation_error("CUSTOMER_REQUIRED", "customer.id", "A synchronized tenant customer is required for account credit.")
                if self._coerce_int(customer_record.get("owner_id")) not in (None, self.current_tenant_id):
                    return self._checkout_validation_error("CUSTOMER_TENANT_MISMATCH", "customer.id", "Customer belongs to another workspace.")
                if not replay and account_credit > Decimal(str(customer_record.get("credit_balance", 0))).quantize(CHECKOUT_MONEY_QUANTUM):
                    return self._checkout_validation_error("INSUFFICIENT_CUSTOMER_CREDIT", "payment.account_credit", "Customer account credit is insufficient.")
        except ValueError as exc:
            return self._checkout_validation_error("INVALID_DECIMAL", "checkout", str(exc))
        except (InvalidOperation, TypeError):
            return self._checkout_validation_error("INVALID_PAYLOAD", "checkout", "Checkout contains invalid numeric data.")

        return True, None

    def deduct_stock_from_server(self, sku: str, quantity_sold: int, location_id: int | None = None) -> bool:
        """
        Legacy method retained for compatibility.
        Stock adjustments should be replayed through queued sales or inventory upserts.
        """
        logger.info("Direct stock-deduct API calls are deprecated; queueing inventory reconciliation instead.")
        self._queue_action("DEDUCT_STOCK", {"sku": sku, "quantity": quantity_sold, "location_id": location_id if location_id is not None else self.active_location_id})
        return False

    def record_sale_to_web(self, total_price: float, gct_amount: float, discount_amount: float = 0.0, items_sold: list[dict] | None = None) -> int | None:
        """
        Records a sale in the web database via API.
        Returns the sale_id on success, None otherwise.
        """
        payload = self._build_sale_payload(
            total_price=total_price,
            gct_amount=gct_amount,
            discount_amount=discount_amount,
            items_sold=items_sold,
        )
        valid_checkout, validation_error = self._validate_checkout_payload(payload)
        if not valid_checkout:
            logger.error(
                "Desktop checkout validation failed before submission: %s (%s)",
                validation_error["message"],
                validation_error["field"],
            )
            return None

        if not self.api_token:
            logger.warning("API token missing. Queuing sale for later sync.")
            self._queue_action("RECORD_SALE", payload)
            return None

        delivery = self._sync_queue_request("POST", "/api/sales/", payload)
        resp = delivery.get("body") or {}
        if self._sale_acknowledgement_matches(payload, resp):
            sale_id = resp.get("sale_id")
            logger.info(f"Web Sync Success: ${total_price} recorded on Website for user {self.current_username}. Sale ID: {sale_id}")
            return sale_id
        if delivery.get("kind") == "permanent" or (
            delivery.get("kind") == "auth" and resp.get("code")
        ):
            logger.error(
                "Desktop checkout rejected by server: %s",
                resp.get("message") or delivery.get("error") or "Validation failed.",
            )
            return None
        else:
            logger.error(f"Web Revenue Sync Error: {(resp or {}).get('message', 'Missing or invalid acknowledgement')}")
            self._queue_action("RECORD_SALE", payload)
            return None

    def record_sale_item_to_web(self, sale_id: int, sku: str, quantity: int, total_price: float):
        """
        Legacy no-op retained for compatibility.
        """
        logger.info("Sale items are now synchronized as part of the parent sale payload.")

    def view_inventory(self):
        win = tk.Toplevel(self.root)
        palette, shell, body = self._build_desktop_surface(
            win,
            "Inventory List",
            "Review the desktop cache and live stock records in one workspace.",
            geometry="1080x680",
        )

        summary_row = tk.Frame(body, bg=palette["bg"])
        summary_row.pack(fill="x", pady=(0, 12))
        for index in range(4):
            summary_row.columnconfigure(index, weight=1)

        inventory_summary_labels = {}

        def add_summary_card(index, key, title, value="0", detail=""):
            card = self._build_desktop_card(summary_row, palette)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            tk.Label(card, text=title.upper(), font=("Segoe UI", 8, "bold"), bg=palette["card"], fg=palette["muted"]).pack(anchor="w")
            value_label = tk.Label(card, text=value, font=("Segoe UI", 18, "bold"), bg=palette["card"], fg=palette["title"])
            value_label.pack(anchor="w", pady=(5, 0))
            detail_label = tk.Label(card, text=detail, font=("Segoe UI", 9), bg=palette["card"], fg=palette["muted"])
            detail_label.pack(anchor="w", pady=(3, 0))
            inventory_summary_labels[key] = (value_label, detail_label)

        add_summary_card(0, "items", "Items")
        add_summary_card(1, "units", "Units")
        add_summary_card(2, "low", "Low Stock")
        add_summary_card(3, "value", "Retail Value")

        toolbar = self._build_desktop_card(body, palette)
        toolbar.pack(fill="x", pady=(0, 12))
        tk.Label(toolbar, text="Search inventory", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["title"]).pack(side="left")
        search_var = tk.StringVar()
        search_entry = self._desktop_entry(toolbar, palette, textvariable=search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=10, ipady=8)
        source_var = tk.StringVar(value="Waiting to load")
        source_label = tk.Label(
            toolbar,
            text=source_var.get(),
            font=("Segoe UI", 9, "bold"),
            bg=palette["badge_bg"],
            fg=palette["badge_fg"],
            padx=10,
            pady=5,
        )
        source_label.pack(side="left")

        container = self._build_desktop_card(body, palette)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        columns = ("SKU", "Barcode", "Name", "Category", "Cost", "Price", "Amount")
        v_scroll = ttk.Scrollbar(container, orient="vertical")
        h_scroll = ttk.Scrollbar(container, orient="horizontal")

        tree = ttk.Treeview(
            container, 
            columns=columns, 
            show="headings", 
            yscrollcommand=v_scroll.set,
            xscrollcommand=h_scroll.set
        )
        v_scroll.config(command=tree.yview)
        h_scroll.config(command=tree.xview)

        tree.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        for col in columns:
            tree.heading(col, text=col.upper())
            tree.column(col, width=120, anchor="center")
        tree.column("Barcode", width=130)
        tree.column("Name", width=180, anchor="w")
        tree.tag_configure("low", background="#FFF7ED", foreground="#7C2D12")
        tree.tag_configure("empty", background="#FEE2E2", foreground="#7F1D1D")
        tree.tag_configure("muted", foreground=palette["muted"])

        current_items = []

        def update_summary(items, source_text):
            total_units = 0
            low_count = 0
            retail_value = Decimal("0.00")
            for item in items:
                qty = self._coerce_int(item.get("Amount") or item.get("amount") or 0) or 0
                total_units += qty
                if qty <= 5:
                    low_count += 1
                try:
                    retail_value += Decimal(str(item.get("Price") or item.get("price") or 0)) * Decimal(qty)
                except (InvalidOperation, TypeError, ValueError):
                    continue
            values = {
                "items": (f"{len(items):,}", source_text),
                "units": (f"{total_units:,}", "Stock on hand"),
                "low": (f"{low_count:,}", "Five units or fewer"),
                "value": (self._format_money(retail_value), "Shelf-value estimate"),
            }
            for key, (value, detail) in values.items():
                value_label, detail_label = inventory_summary_labels[key]
                value_label.configure(text=value)
                detail_label.configure(text=detail)

        def render_inventory_rows():
            term = search_var.get().strip().lower()
            tree.delete(*tree.get_children())
            shown = 0
            for item in current_items:
                values = (
                    item.get("sku") or item.get("SKU") or "N/A",
                    item.get("barcode") or item.get("Barcode") or "",
                    item.get("name") or item.get("Name") or "Unknown",
                    item.get("category_name") or item.get("category") or item.get("Category") or "General",
                    f"{self._receipt_number_value(item.get('cost') or item.get('Cost') or 0):.2f}",
                    f"{self._receipt_number_value(item.get('price') or item.get('Price') or 0):.2f}",
                    item.get("Amount") or item.get("amount") or 0,
                )
                haystack = " ".join(str(value or "") for value in values).lower()
                if term and term not in haystack:
                    continue
                qty = self._coerce_int(values[-1]) or 0
                tag = "empty" if qty <= 0 else ("low" if qty <= 5 else "")
                tree.insert("", "end", values=values, tags=(tag,) if tag else ())
                shown += 1
            if shown == 0:
                message = "No inventory rows match this search." if term else "No inventory records found."
                tree.insert("", "end", values=("-", "-", message, "-", "-", "-", "-"), tags=("muted",))
            source_label.configure(text=f"{source_var.get()} | Showing {shown:,}")

        def refresh_inventory():
            nonlocal current_items
            items = []
            source_text = "LIVE"

            try:
                params = {"location_id": self.active_location_id} if self.active_location_id else {}
                resp = self._api_request("GET", "/api/inventory/", params=params)
                if resp and resp.get("ok"):
                    items = resp.get("items", [])
                    self.save_inventory_cache(items, from_api=True)
                    source_text = "LIVE (via API)"
                else:
                    # If API fails, try loading from local cache
                    self.load_from_local_cache()
                    items = self.system.inventory # Use the loaded items
                    source_text = "OFFLINE CACHE"

                # Ensure items are normalized for display
                items = self._normalize_items(items)
                self.system.inventory = items # Update in-memory system inventory

            except Exception as e:
                print(f"SQL Error: {e}")
                source_text = "OFFLINE CACHE"
                # Refresh from memory if available, otherwise hit the file
                if self.system.inventory and len(self.system.inventory) > 0:
                    items = self._normalize_items(self.system.inventory)
                else:
                    cache_path = self._inventory_cache_path()
                    if os.path.exists(cache_path):
                        with open(cache_path, "r", encoding="utf-8") as f:
                            items = json.load(f)
                        items = self._normalize_items(items)
                    else:
                        messagebox.showerror("Error", "No connection and no saved data.")
                        return

            current_items = items
            source_var.set(source_text)
            update_summary(current_items, source_text)
            render_inventory_rows()
            win.title(f"Inventory List - Mode: {source_text}")

        def delete_selected():
            selected = tree.selection()
            if not selected: return
            
            item_values = tree.item(selected[0], "values")
            sku = item_values[0]  # Using SKU as the ID
            if not sku or sku == "-":
                return

            if not messagebox.askyesno("Confirm", f"Delete SKU {sku} from database?"):
                return

            if self.api_token:
                resp = self._api_request("DELETE", f"/api/inventory/{sku}/")
                if resp and resp.get("ok"):
                    messagebox.showinfo("Success", f"Item {sku} deleted from server.")
                else:
                    messagebox.showerror("Error", f"Failed to delete item {sku} from server. {(resp or {}).get('message', 'Unknown error')}")
                    self._queue_action("DELETE", {"sku": sku}) # Queue for later sync
                    messagebox.showwarning("Offline", "Delete action queued for sync.")
            else:
                self._queue_action("DELETE", {"sku": sku}) # Queue for later sync
                messagebox.showwarning("Offline", "Delete action queued for sync.")
            
            # Always refresh local view after attempting delete or queuing
            refresh_inventory()

        btn_frame = tk.Frame(body, bg=palette["bg"])
        btn_frame.pack(fill="x", pady=(12, 0))

        self._desktop_button(btn_frame, "Refresh", refresh_inventory, palette, kind="accent").pack(side="left", padx=(0, 8))
        self._desktop_button(btn_frame, "Delete Selected", delete_selected, palette, kind="danger").pack(side="left")
        self._desktop_button(btn_frame, "Export CSV", self.export_inventory_csv, palette, kind="subtle").pack(side="left", padx=(8, 0))
        self._desktop_button(btn_frame, "Close", win.destroy, palette, kind="subtle").pack(side="right")

        search_var.trace_add("write", lambda *_args: render_inventory_rows())
        refresh_inventory()


    def check_low_stock(self):
        inventory = self.get_clean_inventory()
        if not inventory:
            messagebox.showinfo("Stock Status", "No items in inventory.")
            return

        low_items = []
        for item in inventory:
            name = item["name"]
            qty = item["amount"]
            try:
                if int(qty) <= 5:
                    low_items.append(f"{name} (Qty: {qty})")
            except (ValueError, TypeError):
                continue

        if low_items:
            messagebox.showwarning(
                "Low Stock Alert",
                "The following items are low:\n\n" + "\n".join(low_items)
            )
        else:
            messagebox.showinfo("Stock Status", "All items are sufficiently stocked.")
            
        # 2. Buddy Stock Concentration Check (Diversification Strategy)
        categories = set(i.get('category') for i in inventory if i.get('category'))
        for cat in categories:
            self.check_concentration(cat)

    def check_concentration(self, category_name):
        """Ensures one brand doesn't dominate a category's value (Buddy Stock rule)."""
        inventory = self.get_clean_inventory()
        category_items = [i for i in inventory if i['category'].lower() == category_name.lower()]
        if not category_items: return
        
        total_value = sum(i['price'] * i['amount'] for i in category_items)
        if total_value == 0: return
        
        brand_values = {}
        for i in category_items:
            brand = i.get('brand_name') or i['SKU'].split('-')[0]
            brand_values[brand] = brand_values.get(brand, 0) + (i['price'] * i['amount'])
            
        for brand, val in brand_values.items():
            concentration = val / total_value
            if concentration > 0.6: # 60% threshold alert
                messagebox.showwarning("Concentration Alert", 
                    f"Brand '{brand}' occupies {concentration:.0%} of {category_name}.\n"
                    "Consider diversifying stock per 'Buddy Stock' guidelines.")

    def global_search_window(self):
        """Search inventory, customers, and receipts from one desktop window."""
        win = tk.Toplevel(self.root)
        palette, shell, body = self._build_desktop_surface(
            win,
            "Global Search",
            "Search inventory, customers, invoices, and quotations in one place.",
            geometry="1040x660",
        )

        search_frame = self._build_desktop_card(body, palette)
        search_frame.pack(fill="x", pady=(0, 12))
        self._desktop_label(search_frame, "Search", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(side="left")
        search_var = tk.StringVar()
        search_entry = self._desktop_entry(search_frame, palette, textvariable=search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=8, ipady=8)
        search_entry.focus_set()

        type_var = tk.StringVar(value="All")
        type_filter = ttk.Combobox(
            search_frame,
            textvariable=type_var,
            values=("All", "Inventory", "Customer", "Receipt"),
            width=14,
            state="readonly",
        )
        type_filter.pack(side="left", padx=(0, 4))

        table_card = self._build_desktop_card(body, palette)
        table_card.pack(fill="both", expand=True)
        columns = ("Type", "Name / ID", "Details", "Meta")
        tree = ttk.Treeview(table_card, columns=columns, show="headings")
        tree.pack(fill="both", expand=True)
        for column in columns:
            tree.heading(column, text=column)
            tree.column(column, anchor="center", width=160)
        tree.column("Name / ID", anchor="w", width=220)
        tree.column("Details", anchor="w", width=380)
        tree.column("Meta", anchor="w", width=190)

        result_lookup = {}
        customer_cache = self._rebuild_customer_registry()

        def item_text(item):
            return " ".join(
                str(value or "")
                for value in (
                    item.get("sku") or item.get("SKU"),
                    item.get("name") or item.get("Name"),
                    item.get("category") or item.get("Category"),
                    item.get("brand_name"),
                )
            ).lower()

        def customer_text(customer):
            return " ".join(
                str(value or "")
                for value in (
                    self._customer_display_name(customer),
                    customer.get("phone"),
                    customer.get("email"),
                    customer.get("notes"),
                )
            ).lower()

        def receipt_text(receipt):
            item_blob = " ".join(
                str(line.get("Name") or line.get("SKU") or "")
                for line in self._receipt_items(receipt)
            )
            return " ".join(
                str(value or "")
                for value in (
                    self._receipt_invoice_value(receipt),
                    self._receipt_customer_name(receipt),
                    self._receipt_date_value(receipt),
                    self._receipt_document_type(receipt),
                    item_blob,
                )
            ).lower()

        def render_rows(*_args):
            term = search_var.get().strip().lower()
            selected_type = type_var.get()
            tree.delete(*tree.get_children())
            result_lookup.clear()

            row_count = 0
            if selected_type in {"All", "Inventory"}:
                for item in self.get_clean_inventory():
                    if term and term not in item_text(item):
                        continue
                    row_id = f"inventory-{row_count}"
                    result_lookup[row_id] = ("Inventory", item)
                    tree.insert(
                        "",
                        "end",
                        iid=row_id,
                        values=(
                            "Inventory",
                            item.get("sku") or item.get("SKU") or "N/A",
                            item.get("name") or item.get("Name") or "Unknown item",
                            f"{item.get('category') or item.get('Category') or 'General'} | Qty {item.get('amount') or item.get('Amount') or 0} | ${self._receipt_number_value(item.get('price') or item.get('Price', 0)):.2f}",
                        ),
                    )
                    row_count += 1

            if selected_type in {"All", "Customer"}:
                for customer in customer_cache or []:
                    name = self._customer_display_name(customer)
                    if not name or (term and term not in customer_text(customer)):
                        continue
                    row_id = f"customer-{row_count}"
                    result_lookup[row_id] = ("Customer", customer)
                    tree.insert(
                        "",
                        "end",
                        iid=row_id,
                        values=(
                            "Customer",
                            name,
                            f"{customer.get('phone', '')} {customer.get('email', '')}".strip() or "No contact saved",
                            f"Invoices {customer.get('invoice_count', 0)} | Quotes {customer.get('quotation_count', 0)} | {customer.get('last_activity') or 'No activity'}",
                        ),
                    )
                    row_count += 1

            if selected_type in {"All", "Receipt"}:
                indexed_receipts = list(enumerate(self.system.receipts or []))
                indexed_receipts.sort(
                    key=lambda item: self._receipt_datetime(item[1]) or datetime.min,
                    reverse=True,
                )
                for idx, receipt in indexed_receipts:
                    if str(receipt.get("archived")) == "True":
                        continue
                    if term and term not in receipt_text(receipt):
                        continue
                    invoice_display = self._receipt_invoice_value(receipt, fallback=idx + 1)
                    row_id = f"receipt-{row_count}"
                    result_lookup[row_id] = ("Receipt", receipt)
                    tree.insert(
                        "",
                        "end",
                        iid=row_id,
                        values=(
                            self._receipt_document_type(receipt),
                            f"#{invoice_display}",
                            self._receipt_customer_name(receipt) or "Walk-In Customer",
                            f"{self._receipt_date_value(receipt)} | ${self._receipt_number_value(receipt.get('Total Bill', 0)):.2f}",
                        ),
                    )
                    row_count += 1

        def open_selected():
            selected = tree.focus()
            if not selected:
                messagebox.showwarning("Select Result", "Select a search result first.")
                return
            result = result_lookup.get(selected)
            if not result:
                return
            kind, data = result
            if kind == "Inventory":
                self._show_inventory_item_detail(win, data)
            elif kind == "Customer":
                self.customer_detail_window(self._customer_display_name(data))
            elif kind == "Receipt":
                self._show_receipt_detail(win, data)

        def refresh_results():
            nonlocal customer_cache
            customer_cache = self._rebuild_customer_registry()
            render_rows()

        buttons = tk.Frame(body, bg=palette["bg"])
        buttons.pack(fill="x", pady=(12, 0))
        self._desktop_button(buttons, "Open Selected", open_selected, palette, kind="primary").pack(side="left", padx=(0, 8))
        self._desktop_button(buttons, "Refresh", refresh_results, palette, kind="subtle").pack(side="left")
        self._desktop_button(buttons, "Close", win.destroy, palette, kind="subtle").pack(side="right")

        search_var.trace_add("write", render_rows)
        type_var.trace_add("write", render_rows)
        tree.bind("<Double-1>", lambda _event: open_selected())
        render_rows()

    def _show_inventory_item_detail(self, parent, item):
        detail = tk.Toplevel(parent)
        sku = item.get("sku") or item.get("SKU") or "N/A"
        palette, content = self._desktop_form_shell(detail, f"Inventory Item: {sku}", "460x420")
        detail.transient(parent)

        self._desktop_label(content, f"Inventory Item: {sku}", palette, surface="card", role="title", font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(0, 12))
        rows = [
            ("SKU", sku),
            ("Barcode", item.get("barcode") or item.get("Barcode") or ""),
            ("Name", item.get("name") or item.get("Name") or "Unknown"),
            ("Category", item.get("category") or item.get("Category") or "General"),
            ("Cost", f"${self._receipt_number_value(item.get('cost') or item.get('Cost', 0)):.2f}"),
            ("Price", f"${self._receipt_number_value(item.get('price') or item.get('Price', 0)):.2f}"),
            ("Amount", item.get("amount") or item.get("Amount") or 0),
            ("Location", item.get("location_name") or self.active_location_name or "All Locations"),
        ]
        for label, value in rows:
            self._desktop_label(content, f"{label}: {value}", palette, surface="card", role="text", anchor="w").pack(fill="x", pady=3)
        action_row = tk.Frame(content, bg=palette["card"])
        action_row.pack(fill="x", pady=(16, 0))
        self._desktop_button(action_row, "Open Inventory List", lambda: (detail.destroy(), self.view_inventory()), palette, kind="accent").pack(side="left")
        self._desktop_button(action_row, "Close", detail.destroy, palette, kind="subtle").pack(side="right")

    def _show_receipt_detail(self, parent, receipt):
        detail = tk.Toplevel(parent)
        invoice_display = self._receipt_invoice_value(receipt)
        palette, content = self._desktop_form_shell(detail, f"{self._receipt_document_type(receipt)} #{invoice_display}", "470x520")
        detail.transient(parent)

        self._desktop_label(content, f"{self._receipt_document_type(receipt)} #{invoice_display}", palette, surface="card", role="title", font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(0, 8))
        self._desktop_label(content, f"Date: {self._receipt_date_value(receipt)}", palette, surface="card", role="text").pack(anchor="w")
        self._desktop_label(content, f"Customer: {self._receipt_customer_name(receipt) or 'Walk-In Customer'}", palette, surface="card", role="text").pack(anchor="w")
        if receipt.get("Business TRN"):
            self._desktop_label(content, f"TRN: {receipt.get('Business TRN')}", palette, surface="card", role="text").pack(anchor="w")

        item_frame = tk.Frame(content, bg=palette["card"])
        item_frame.pack(fill="both", expand=True, padx=20, pady=10)
        for item in self._receipt_items(receipt):
            item_name = item.get("Name") or item.get("SKU") or "Item"
            item_qty = self._coerce_int(item.get("Quantity")) or 0
            calc_total = self._receipt_number_value(item.get("Total", item.get("Price", 0) * item.get("Quantity", 0)))
            self._desktop_label(item_frame, f"{item_name} x{item_qty} = ${calc_total:.2f}", palette, surface="card", role="text", anchor="w").pack(fill="x")

        tax_rate = self._receipt_number_value(receipt.get("TaxRate", 0))
        tax_label = receipt.get("TaxLabel") or self.system.settings.get("tax_label", "GCT")
        self._desktop_label(content, f"Subtotal: ${self._receipt_number_value(receipt.get('Subtotal', 0)):.2f}", palette, surface="card", role="text").pack(anchor="w")
        self._desktop_label(content, f"{tax_label} ({tax_rate * 100:.0f}%): ${self._receipt_number_value(receipt.get('GCT', 0)):.2f}", palette, surface="card", role="text").pack(anchor="w")
        self._desktop_label(content, f"TOTAL: ${self._receipt_number_value(receipt.get('Total Bill', 0)):.2f}", palette, surface="card", role="title", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=10)
        self._desktop_button(content, "Close", detail.destroy, palette, kind="subtle").pack(fill="x", pady=10)

    def get_clean_inventory(self):
        """Return a normalized view of the current inventory for consistent key access."""
        if not self.system.inventory:
            return []
        return self._normalize_items(self.system.inventory)

    def customer_directory_window(self):
        self.customers = self._rebuild_customer_registry()

        win = tk.Toplevel(self.root)
        palette, shell, body = self._build_desktop_surface(
            win,
            "Customer Directory",
            "Browse customers, invoices, and quotations.",
            geometry="1040x640",
        )

        search_bar = self._build_desktop_card(body, palette)
        search_bar.pack(fill="x", pady=(0, 12))
        self._desktop_label(search_bar, "Search", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(side="left")
        search_var = tk.StringVar()
        search_entry = self._desktop_entry(search_bar, palette, textvariable=search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=8, ipady=8)
        search_entry.focus_set()

        buttons = tk.Frame(body, bg=palette["bg"])
        buttons.pack(fill="x", pady=(0, 12))

        columns = ("Name", "Phone", "Email", "Invoices", "Quotations", "Last Activity")
        table_card = self._build_desktop_card(body, palette)
        table_card.pack(fill="both", expand=True)
        tree = ttk.Treeview(table_card, columns=columns, show="headings")
        tree.pack(fill="both", expand=True)
        for column in columns:
            tree.heading(column, text=column)
            tree.column(column, anchor="center", width=150)
        tree.column("Name", anchor="w", width=220)
        tree.column("Email", width=220)

        customer_lookup = {}

        def render_rows(*_args):
            search_term = search_var.get().strip().lower()
            tree.delete(*tree.get_children())
            customer_lookup.clear()

            for idx, customer in enumerate(sorted(self.customers or [], key=lambda item: self._customer_display_name(item).lower())):
                name = self._customer_display_name(customer)
                if not name:
                    continue
                blob = " ".join([
                    name.lower(),
                    str(customer.get("phone", "")).lower(),
                    str(customer.get("email", "")).lower(),
                    str(customer.get("notes", "")).lower(),
                ])
                if search_term and search_term not in blob:
                    continue
                row_id = f"customer-{idx}"
                customer_lookup[row_id] = customer
                tree.insert(
                    "",
                    "end",
                    iid=row_id,
                    values=(
                        name,
                        customer.get("phone", ""),
                        customer.get("email", ""),
                        customer.get("invoice_count", 0),
                        customer.get("quotation_count", 0),
                        customer.get("last_activity") or customer.get("joined", ""),
                    ),
                )

        def selected_customer():
            selected = tree.focus()
            if not selected:
                messagebox.showwarning("Select Customer", "Please select a customer first.")
                return None
            customer = customer_lookup.get(selected)
            if not customer:
                return None
            return customer

        def open_selected_customer():
            customer = selected_customer()
            if not customer:
                return
            self.customer_detail_window(self._customer_display_name(customer))

        def edit_selected_customer():
            customer = selected_customer()
            if not customer:
                return
            self.edit_customer_window(self._customer_display_name(customer))

        self._desktop_button(buttons, "Open", open_selected_customer, palette, kind="primary").pack(side="left", padx=(0, 8))
        self._desktop_button(buttons, "Edit", edit_selected_customer, palette, kind="accent").pack(side="left", padx=(0, 8))
        self._desktop_button(buttons, "Refresh", lambda: (setattr(self, "customers", self._rebuild_customer_registry()), render_rows()), palette, kind="subtle").pack(side="left", padx=(0, 8))
        self._desktop_button(buttons, "New Customer", lambda: self.edit_customer_window(""), palette, kind="accent").pack(side="left")
        self._desktop_button(buttons, "Close", win.destroy, palette, kind="subtle").pack(side="right")

        tree.bind("<Double-1>", lambda _event: open_selected_customer())
        search_var.trace_add("write", render_rows)
        render_rows()

    def customer_detail_window(self, customer_name):
        self.customers = self._rebuild_customer_registry()
        record = self._get_customer_record(customer_name) or {"name": self._normalize_customer_name(customer_name)}
        name = self._customer_display_name(record) or self._normalize_customer_name(customer_name) or "Customer"
        receipts = self._customer_receipts(name)

        win = tk.Toplevel(self.root)
        palette, shell, body = self._build_desktop_surface(
            win,
            f"Customer: {name}",
            "Review customer contact details, invoices, quotations, and credit.",
            geometry="1040x700",
        )

        top = self._build_desktop_card(body, palette)
        top.pack(fill="x", pady=(0, 12))
        self._desktop_label(top, name, palette, surface="card", role="title", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        self._desktop_label(top, f"Phone: {record.get('phone', '') or '-'}", palette, surface="card", role="text").pack(anchor="w")
        self._desktop_label(top, f"Email: {record.get('email', '') or '-'}", palette, surface="card", role="text").pack(anchor="w")
        self._desktop_label(top, f"Joined: {record.get('joined', '') or '-'}", palette, surface="card", role="text").pack(anchor="w")
        self._desktop_label(top, f"Last Activity: {record.get('last_activity', '') or '-'}", palette, surface="card", role="text").pack(anchor="w")
        self._desktop_label(top, f"Account Credit: ${self._customer_credit_value(name):.2f}", palette, surface="card", role="title", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(2, 0))
        notes_text = record.get("notes", "") or "No notes added."
        self._desktop_label(top, f"Notes: {notes_text}", palette, surface="card", role="muted", wraplength=920, justify="left").pack(anchor="w", pady=(4, 0))

        actions = tk.Frame(body, bg=palette["bg"])
        actions.pack(fill="x", pady=(0, 12))
        self._desktop_button(actions, "Edit Customer", lambda: self.edit_customer_window(name), palette, kind="accent").pack(side="left", padx=(0, 8))
        self._desktop_button(actions, "Refresh", lambda: win.destroy() or self.customer_detail_window(name), palette, kind="subtle").pack(side="left")
        self._desktop_button(actions, "Close", win.destroy, palette, kind="subtle").pack(side="right")

        notebook = ttk.Notebook(body)
        notebook.pack(fill="both", expand=True)

        invoice_frame = tk.Frame(notebook, bg=palette["card"], padx=12, pady=12)
        quotation_frame = tk.Frame(notebook, bg=palette["card"], padx=12, pady=12)
        notebook.add(invoice_frame, text="Invoices")
        notebook.add(quotation_frame, text="Quotations")

        invoice_columns = ("Invoice #", "Date", "Status", "Balance Due", "Total", "Items")
        invoice_tree = ttk.Treeview(invoice_frame, columns=invoice_columns, show="headings")
        invoice_tree.pack(fill="both", expand=True)
        for column in invoice_columns:
            invoice_tree.heading(column, text=column)
            invoice_tree.column(column, anchor="center")
        invoice_tree.column("Items", anchor="w", width=450)

        quotation_tree = ttk.Treeview(quotation_frame, columns=invoice_columns, show="headings")
        quotation_tree.pack(fill="both", expand=True)
        for column in invoice_columns:
            quotation_tree.heading(column, text=column)
            quotation_tree.column(column, anchor="center")
        quotation_tree.column("Items", anchor="w", width=450)

        invoice_lookup = {}
        quotation_lookup = {}

        def add_row(tree, lookup, receipt):
            receipt = self._sync_receipt_payment_state(receipt)
            invoice_display = self._receipt_invoice_value(receipt)
            items_text = ", ".join(
                f"{item.get('Name') or item.get('SKU') or 'Item'} x{self._coerce_int(item.get('Quantity')) or 0}"
                for item in self._receipt_items(receipt)
            )
            row_id = f"{tree.winfo_name()}-{invoice_display}"
            lookup[row_id] = receipt
            tree.insert(
                "",
                "end",
                iid=row_id,
                values=(
                    invoice_display,
                    self._receipt_date_value(receipt),
                    receipt.get("Payment Status", "Issued"),
                    f"${self._receipt_number_value(receipt.get('Balance Due', 0)):.2f}",
                    f"${self._receipt_number_value(receipt.get('Total Bill', 0)):.2f}",
                    items_text,
                ),
            )

        for receipt in receipts:
            document_type = self._receipt_document_type(receipt).lower()
            if document_type == "quotation":
                add_row(quotation_tree, quotation_lookup, receipt)
            else:
                add_row(invoice_tree, invoice_lookup, receipt)

        if not receipts:
            self._desktop_label(invoice_frame, "No sales found for this customer.", palette, surface="card", role="muted", pady=20).pack()
            self._desktop_label(quotation_frame, "No quotations found for this customer.", palette, surface="card", role="muted", pady=20).pack()

        def open_receipt(tree, lookup):
            selected = tree.focus()
            if not selected:
                return
            receipt = lookup.get(selected)
            if not receipt:
                return
            self._show_receipt_detail_window(receipt, parent=win, on_update=lambda: (win.destroy(), self.customer_detail_window(name)))

        invoice_tree.bind("<Double-1>", lambda _event: open_receipt(invoice_tree, invoice_lookup))
        quotation_tree.bind("<Double-1>", lambda _event: open_receipt(quotation_tree, quotation_lookup))

    def edit_customer_window(self, customer_name=""):
        original_name = self._normalize_customer_name(customer_name)
        record = self._get_customer_record(original_name) or {
            "name": original_name,
            "phone": "",
            "email": "",
            "notes": "",
        }

        win = tk.Toplevel(self.root)
        palette, content = self._desktop_form_shell(win, "Edit Customer" if original_name else "New Customer", "560x460")

        self._desktop_label(content, "Customer Profile", palette, surface="card", role="title", font=("Segoe UI", 16, "bold")).pack(anchor="w", pady=(0, 12))

        fields = {}
        for label, key, default in [
            ("Customer Name", "name", record.get("name", "")),
            ("Phone", "phone", record.get("phone", "")),
            ("Email", "email", record.get("email", "")),
        ]:
            self._desktop_label(content, label, palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
            entry = self._desktop_entry(content, palette, width=45)
            entry.insert(0, default or "")
            entry.pack(fill="x", pady=(4, 10), ipady=8)
            fields[key] = entry

        self._desktop_label(content, "Notes", palette, surface="card", role="title", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        notes = tk.Text(content, height=7, wrap="word", bg=palette["input_bg"], fg=palette["input_fg"], insertbackground=palette["input_fg"], relief="flat", bd=0, font=("Segoe UI", 10))
        notes.insert("1.0", record.get("notes", "") or "")
        notes.pack(fill="both", expand=True, pady=(0, 10))

        def save_customer():
            name_value = self._normalize_customer_name(fields["name"].get())
            if not name_value:
                messagebox.showwarning("Missing Name", "Customer name is required.")
                return

            customers = self._load_customer_registry()
            if original_name and original_name.lower() != name_value.lower():
                customers = [entry for entry in customers if self._customer_display_name(entry).lower() != original_name.lower()]
            else:
                customers = [entry for entry in customers if self._customer_display_name(entry).lower() != name_value.lower()]

            updated = self._upsert_customer_record(
                name_value,
                phone=fields["phone"].get().strip(),
                email=fields["email"].get().strip(),
                notes=notes.get("1.0", "end").strip(),
            )
            if updated:
                # Merge back any manual removals before the upsert.
                self._save_customer_registry(customers + [updated])
                if original_name and original_name.lower() != name_value.lower():
                    for receipt in self.system.receipts or []:
                        if self._receipt_customer_name(receipt).lower() == original_name.lower():
                            receipt["Customer Name"] = name_value
                    storage.save_data(
                        self.system.inventory,
                        self.system.receipts,
                        self.system.settings
                    )
                self.customers = self._rebuild_customer_registry()
                messagebox.showinfo("Saved", "Customer saved successfully.")
                win.destroy()
                return

            messagebox.showerror("Error", "Failed to save customer.")

        action_row = tk.Frame(content, bg=palette["card"])
        action_row.pack(fill="x")
        self._desktop_button(action_row, "Save", save_customer, palette, kind="primary").pack(side="left")
        self._desktop_button(action_row, "Cancel", win.destroy, palette, kind="subtle").pack(side="right")

    def receipt_history_window(self):
        win = tk.Toplevel(self.root)
        palette, shell, body = self._build_desktop_surface(
            win,
            "Receipt History",
            "Search prior invoices by date, customer, item, or invoice number from one place.",
            geometry="1100x720",
        )
        win.transient(self.root)
        receipt_lookup = {}

        banner = self._build_desktop_banner(
            body,
            palette,
            "History stays available offline",
            "Receipt search reads from the local desktop archive first, then refreshes history in the background.",
            badge_text=f"Saved receipts: {len(self.system.receipts or [])}",
        )
        banner.pack(fill="x", pady=(0, 14))

        search_card = self._build_desktop_card(body, palette)
        search_card.pack(fill="x", pady=(0, 12))
        tk.Label(search_card, text="Search (Date, Invoice, Customer, or Item)", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["title"]).pack(side="left")
        search_var = tk.StringVar()
        search_entry = tk.Entry(search_card, textvariable=search_var, font=("Segoe UI", 11), bg=palette["input_bg"], fg=palette["input_fg"], insertbackground=palette["input_fg"], relief="flat", bd=0)
        search_entry.pack(side="left", fill="x", expand=True, padx=12, ipady=9)
        search_entry.focus_set()

        table_card = self._build_desktop_card(body, palette)
        table_card.pack(fill="both", expand=True)
        columns = ("Invoice #", "Customer", "Date", "Status", "Balance Due", "Total")
        tree = ttk.Treeview(table_card, columns=columns, show="headings")
        tree.pack(fill="both", expand=True, pady=(0, 12))

        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, anchor="center")
        tree.column("Customer", anchor="w", width=220)
        tree.column("Date", width=180)

        def update_list(*args):
            search_term = search_var.get().lower()
            tree.delete(*tree.get_children())
            receipt_lookup.clear()

            indexed_receipts = list(enumerate(self.system.receipts or []))
            indexed_receipts.sort(
                key=lambda item: self._receipt_datetime(item[1]) or datetime.min,
                reverse=True,
            )

            for idx, receipt in indexed_receipts:
                if str(receipt.get("archived")) == "True":
                    continue
                receipt = self._sync_receipt_payment_state(receipt)
                invoice_display = self._receipt_invoice_value(receipt, fallback=idx + 1)
                receipt_id = str(invoice_display).lower()
                date_display = self._receipt_date_value(receipt)
                date_str = date_display.lower()
                item_blob = " ".join(
                    str(line.get("Name") or line.get("SKU") or "").lower()
                    for line in self._receipt_items(receipt)
                )
                customer_blob = self._receipt_customer_name(receipt).lower()

                if search_term in receipt_id or search_term in date_str or search_term in item_blob or search_term in customer_blob:
                    row_id = f"receipt-{idx}"
                    receipt_lookup[row_id] = idx
                    tree.insert(
                        "",
                        "end",
                        iid=row_id,
                        values=(
                            invoice_display,
                            self._receipt_customer_name(receipt) or "Walk-In Customer",
                            date_display,
                            receipt.get("Payment Status", "Issued"),
                            f"${self._receipt_number_value(receipt.get('Balance Due', 0)):.2f}",
                            f"${self._receipt_number_value(receipt.get('Total Bill', 0)):.2f}",
                        ),
                    )

        # Bind the search variable to the update function
        search_var.trace_add("write", update_list)
        
        # Initial load of the data
        update_list()


        # --- DETAIL VIEWER ---
        def show_receipt_details(event):
            selected = tree.focus()
            if not selected:
                return

            try:
                receipt = self.system.receipts[receipt_lookup[selected]]
            except (KeyError, IndexError, ValueError):
                return

            self._show_receipt_detail_window(receipt, parent=win, on_update=update_list)

        tree.bind("<Double-1>", show_receipt_details)
        
        def delete_selected_receipt():
            selected = tree.focus()
            if not selected:
                messagebox.showwarning("Warning", "No receipt selected.")
                return

            if not messagebox.askyesno("Confirm Delete", "Are you sure you want to delete this receipt?"):
                return

            try:
                idx = receipt_lookup[selected]
                receipt = self.system.receipts[idx]
                if self._receipt_payment_history(receipt):
                    messagebox.showwarning("Delete Blocked", "Revert the invoice payments first before deleting this receipt.")
                    return
                del self.system.receipts[idx]
                self._save_receipts_and_customers()
                update_list()
                messagebox.showinfo("Deleted", "Receipt deleted successfully.")
            except (KeyError, IndexError, ValueError):
                messagebox.showerror("Error", "Failed to delete receipt.")
        action_row = tk.Frame(table_card, bg=palette["card"])
        action_row.pack(fill="x")
        if self._is_superuser_role():
            tk.Button(action_row, text="Delete Selected Receipt", command=delete_selected_receipt, bg=palette["danger"], fg="#FFFFFF", activebackground=palette["danger_active"], relief="flat", padx=14, pady=10).pack(side="left")
        tk.Button(action_row, text="Refresh History", command=update_list, bg=palette["primary"], fg=palette["primary_text"], activebackground=palette["primary_active"], relief="flat", padx=14, pady=10).pack(side="left", padx=8)
        self.sync_receipt_history(callback=update_list)


    def sales_summary_window(self):
        win = tk.Toplevel()
        palette, shell, body = self._build_desktop_surface(
            win,
            "Sales Summary",
            "Today’s local sales totals and tax collection.",
            geometry="460x500",
        )

        content = self._build_desktop_card(body, palette)
        content.pack(fill="both", expand=True)

        def render():
            # 1. Reset UI
            for widget in content.winfo_children():
                widget.destroy()

            today_str = datetime.now().strftime("%Y-%m-%d")

            # 2. Filter out receipts that are already archived
            todays_receipts = [
                r for r in self.system.receipts
                if r.get("Date", "").startswith(today_str)
                and str(r.get("archived")) != "True"
            ]

            # 3. Calculate Totals
            total_receipts = len(todays_receipts)
            total_items = 0
            subtotal = 0.0
            total_gct = 0.0
            total_sales = 0.0

            for receipt in todays_receipts:
                subtotal += float(receipt.get("Subtotal", 0))
                total_gct += float(receipt.get("GCT", 0))
                total_sales += float(receipt.get("Total Bill", 0))
                for item in receipt.get("Items Purchased", []):
                    total_items += int(item.get("Quantity", 0))

            # 4. Display UI
            self._desktop_label(
                content,
                f"SALES SUMMARY ({today_str})",
                palette,
                surface="card",
                role="title",
                font=("Segoe UI", 13, "bold"),
            ).pack(anchor="w", pady=(0, 12))

            self._desktop_label(content, f"Total Receipts: {total_receipts}", palette, surface="card", role="text").pack(anchor="w")
            self._desktop_label(content, f"Total Items Sold: {total_items}", palette, surface="card", role="text").pack(anchor="w")
            self._desktop_label(content, f"Subtotal: ${subtotal:,.2f}", palette, surface="card", role="text").pack(anchor="w")
            tax_label = self.system.settings.get("tax_label", "GCT")
            self._desktop_label(content, f"Total {tax_label}: ${total_gct:,.2f}", palette, surface="card", role="text").pack(anchor="w")

            self._desktop_label(
                content,
                f"TOTAL SALES: ${total_sales:,.2f}",
                palette,
                surface="card",
                role="primary",
                font=("Segoe UI", 14, "bold"),
            ).pack(anchor="w", pady=15)

            # 5. Control Buttons
            self._desktop_button(content, "Refresh", render, palette, kind="accent").pack(fill="x", pady=5)
            self._desktop_button(content, "Clear Today's Sales", clear_sales, palette, kind="danger").pack(fill="x", pady=5)

        def clear_sales():
            today_str = datetime.now().strftime("%Y-%m-%d")

            # Check if there are any active (non-archived) sales left to clear
            active_sales = [
                r for r in self.system.receipts
                if r.get("Date", "").startswith(today_str)
                and str(r.get("archived")) != "True"
            ]

            if not active_sales:
                messagebox.showinfo("Info", "No active sales today to clear.")
                return

            if messagebox.askyesno(
                "Confirm Clear",
                "Reset today's summary? \n\nNote: All receipts remain safely in your history."
            ):
                # Apply the archived flag to all of today's receipts
                for r in self.system.receipts:
                    if r.get("Date", "").startswith(today_str):
                        r["archived"] = True

                # Save data and refresh both this window and the dashboard
                storage.save_data(
                    self.system.inventory,
                    self.system.receipts,
                    self.system.settings
                )
                render()
                if hasattr(self, "update_dashboard_stats"):
                    self.update_dashboard_stats()

        # Initial render call
        render()


    def low_stock_window(self):
        win = tk.Toplevel()
        palette, shell, body = self._build_desktop_surface(
            win,
            "Low Stock Dashboard",
            "Items that need replenishment attention.",
            geometry="620x440",
        )

        columns = ("SKU", "Name", "Category", "Stock")

        table_card = self._build_desktop_card(body, palette)
        table_card.pack(fill="both", expand=True)
        tree = ttk.Treeview(table_card, columns=columns, show="headings")
        tree.pack(fill="both", expand=True)

        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, anchor="center")

        # Style for low stock
        tree.tag_configure("low", background="#ffcccc")
        tree.tag_configure("critical", background="#ff9999")

        def load_low_stock():
            tree.delete(*tree.get_children())

            for item in self.system.inventory:
                stock = item.get("Amount", 0)

                if stock <= 5:
                    tag = "critical" if stock <= 2 else "low"

                    tree.insert(
                        "",
                        "end",
                        values=(
                            item.get("SKU"),
                            item.get("Name"),
                            item.get("Category"),
                            stock
                        ),
                        tags=(tag,)
                    )

            if not tree.get_children():
                messagebox.showinfo(
                    "Stock Status",
                    "No low-stock items found."
                )

        load_low_stock()

        tk.Button(
            body,
            text="Refresh",
            command=load_low_stock,
            bg=palette["accent"],
            fg=palette["primary_text"],
            activebackground=palette["accent_active"],
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=14,
            pady=9,
        ).pack(fill="x", pady=(12, 0))

    def export_inventory_csv(self):
        if not self.system.inventory:
            messagebox.showinfo("Info", "No inventory data to export.")
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            title="Save Inventory CSV"
        )

        if not file_path:
            return

        try:
            with open(file_path, mode="w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow([
                    "Item Number", "SKU", "Category", "Item Name",
                    "Unit Cost", "Unit Price", "Stock On Hand"
                ])

                for item in self.system.inventory:
                    writer.writerow([
                        item.get("Number"),
                        item.get("SKU"),
                        item.get("Category"),
                        item.get("Name"),
                        item.get("Cost"),
                        item.get("Price", ""),
                        item.get("Amount")
                    ])

            messagebox.showinfo("Success", "Inventory exported successfully.")

        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _write_sales_csv(self, file_path):
        with open(file_path, mode="w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow([
                "Invoice Number", "Business TRN", "Sale Date", "Item Name", "Quantity Sold",
                "Line Total", "Sale Subtotal", "GCT Rate", "GCT Amount", "Grand Total"
            ])

            for i, receipt in enumerate(self.system.receipts, start=1):
                invoice_display = receipt.get("Invoice Code") or receipt.get("Invoice No") or i
                business_trn = receipt.get("Business TRN") or self.business_trn or ""
                tax_rate = receipt.get("TaxRate", 0)
                for item in receipt["Items Purchased"]:
                    writer.writerow([
                        invoice_display,
                        business_trn,
                        receipt.get("Date", "N/A"),
                        item["Name"],
                        item["Quantity"],
                        f"{item['Total']:.2f}",
                        f"{receipt['Subtotal']:.2f}",
                        f"{tax_rate * 100:.2f}%",
                        f"{receipt['GCT']:.2f}",
                        f"{receipt['Total Bill']:.2f}"
                    ])

    def export_sales_csv(self):
        if not self.system.receipts:
            messagebox.showinfo("Info", "No sales data to export.")
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
            title="Save Sales CSV"
        )

        if not file_path:
            return

        try:
            self._write_sales_csv(file_path)
            messagebox.showinfo("Success", "Sales exported successfully.")

        except Exception as e:
            messagebox.showerror("Error", str(e))

    def export_sales_excel(self):
        if not self.system.receipts:
            messagebox.showinfo("Info", "No sales data to export.")
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx"), ("CSV files", "*.csv")],
            title="Save Sales Export"
        )

        if not file_path:
            return

        if file_path.lower().endswith(".csv"):
            try:
                self._write_sales_csv(file_path)
                messagebox.showinfo("Success", "Sales exported successfully.")
            except Exception as e:
                messagebox.showerror("Error", str(e))
            return

        try:
            from openpyxl import Workbook
        except Exception:
            messagebox.showerror(
                "Missing Dependency",
                "openpyxl is required for Excel exports. Please install it or choose CSV."
            )
            return

        try:
            wb = Workbook()
            ws = wb.active
            ws.title = "Sales"
            ws.append([
                "Invoice Number", "Business TRN", "Sale Date", "Item Name", "Quantity Sold",
                "Line Total", "Sale Subtotal", "GCT Rate", "GCT Amount", "Grand Total"
            ])

            for i, receipt in enumerate(self.system.receipts, start=1):
                invoice_display = receipt.get("Invoice Code") or receipt.get("Invoice No") or i
                business_trn = receipt.get("Business TRN") or self.business_trn or ""
                tax_rate = receipt.get("TaxRate", 0)
                for item in receipt["Items Purchased"]:
                    ws.append([
                        invoice_display,
                        business_trn,
                        receipt.get("Date", "N/A"),
                        item["Name"],
                        item["Quantity"],
                        float(f"{item['Total']:.2f}"),
                        float(f"{receipt['Subtotal']:.2f}"),
                        f"{tax_rate * 100:.2f}%",
                        float(f"{receipt['GCT']:.2f}"),
                        float(f"{receipt['Total Bill']:.2f}"),
                    ])

            wb.save(file_path)
            messagebox.showinfo("Success", "Sales exported successfully.")

        except Exception as e:
            messagebox.showerror("Error", str(e))


    def open_shift_from_desktop(self):
        """Open a backend CashShift from the desktop application."""

        if not self.api_token or self.api_status != "ONLINE":
            messagebox.showerror(
                "Online Connection Required",
                "Connect to QuickStock before opening a register shift.",
                parent=self.root,
            )
            return False

        location_id = self._coerce_int(self.active_location_id)

        if not location_id:
            messagebox.showerror(
                "Location Required",
                "Select a location before opening the register.",
                parent=self.root,
            )
            return False

        if self.active_register_is_open:
            return True

        raw_amount = simpledialog.askstring(
            "Open Register Shift",
            (
                f"Location: {self.active_location_name}\n\n"
                "Enter the opening cash float:"
            ),
            initialvalue="0.00",
            parent=self.root,
        )

        if raw_amount is None:
            return False

        try:
            opening_cash = Decimal(
                str(raw_amount).strip().replace(",", "")
            ).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            messagebox.showerror(
                "Invalid Amount",
                "Enter a valid opening cash amount.",
                parent=self.root,
            )
            return False

        if opening_cash < Decimal("0.00"):
            messagebox.showerror(
                "Invalid Amount",
                "Opening cash cannot be negative.",
                parent=self.root,
            )
            return False

        response = self._api_request(
            "POST",
            "/api/register/open/",
            payload={
                "location_id": location_id,
                "opening_cash": str(opening_cash),
            },
        )

        if not response:
            messagebox.showerror(
                "Register Error",
                "QuickStock could not contact the server.",
                parent=self.root,
            )
            return False

        active_register = response.get("active_register")

        if isinstance(active_register, dict):
            self._apply_active_register_snapshot(active_register)

        if not response.get("ok"):
            messagebox.showerror(
                "Register Error",
                response.get(
                    "message",
                    "The register shift could not be opened.",
                ),
                parent=self.root,
            )
            return False

        messagebox.showinfo(
            "Register Open",
            response.get(
                "message",
                f"Register opened at {self.active_location_name}.",
            ),
            parent=self.root,
        )

        self._update_home_dashboard_widgets()
        return True


    def open_cash_register(self):
        """Open the POS and automatically request a register shift."""

        if (
            self.cash_register_window
            and self.cash_register_window.winfo_exists()
        ):
            self.cash_register_window.lift()
            self.cash_register_window.focus_force()
            return

        if not self.active_register_is_open:
            if not self.open_shift_from_desktop():
                return

        if not self.ensure_business_profile_for_sale():
            return

        try:
            self.sync_inventory()

            if not self.system.inventory:
                self.load_from_local_cache()

        except Exception as exc:
            logger.warning(
                "Could not refresh inventory before opening POS: %s",
                exc,
            )

        self.cash_register_window = tk.Toplevel(self.root)
        self.cash_register_window.title("Sell Items")

        Cash_register_GUI(
            self.cash_register_window,
            self.system,
            self,
        )

        self.cash_register_window.protocol(
            "WM_DELETE_WINDOW",
            self._handle_cash_register_exit,
        )


    def _handle_cash_register_exit(self):
        """Prompt to close the active shift when exiting the POS."""

        window = self.cash_register_window
        controller = self.cash_register_controller

        if not window or not window.winfo_exists():
            self.cash_register_window = None
            self.cash_register_controller = None
            return

        if self.active_register_is_open:
            choice = messagebox.askyesnocancel(
                "Exit Cash Register",
                (
                    "The register shift is still open.\n\n"
                    "Would you like to close the shift now?\n\n"
                    "Yes — count cash, close the shift, and exit\n"
                    "No — exit while leaving the shift open\n"
                    "Cancel — return to the cash register"
                ),
                parent=window,
            )

            # Cancel: remain in the POS.
            if choice is None:
                return

            # Yes: complete the closing-cash workflow first.
            if choice and not self.close_shift_from_desktop():
                return

        # Clean up bindings owned by Cash_register_GUI.
        if controller and hasattr(controller, "_unbind_register_mousewheel"):
            try:
                controller._unbind_register_mousewheel()
            except Exception as exc:
                logger.warning(
                    "Could not remove register mousewheel bindings: %s",
                    exc,
                )

        window.destroy()
        self.cash_register_window = None
        self.cash_register_controller = None


    def close_shift_from_desktop(self):
        """Close the active CashShift through the QuickStock backend API."""

        if not self.api_token or self.api_status != "ONLINE":
            messagebox.showerror(
                "Online Connection Required",
                "Connect to QuickStock before closing the register shift.",
                parent=self.root,
            )
            return False

        register_id = self._coerce_int(self.active_register_id)

        if not register_id or not self.active_register_is_open:
            messagebox.showinfo(
                "No Open Register",
                "There is no active register shift to close.",
                parent=self.root,
            )
            return False

        parent_window = (
            self.cash_register_window
            if (
                self.cash_register_window
                and self.cash_register_window.winfo_exists()
            )
            else self.root
        )

        raw_amount = simpledialog.askstring(
            "Close Register Shift",
            (
                f"Location: {self.active_location_name}\n\n"
                "Enter the counted closing cash:"
            ),
            initialvalue="0.00",
            parent=parent_window,
        )

        if raw_amount is None:
            return False

        try:
            closing_cash = Decimal(
                str(raw_amount).strip().replace(",", "")
            ).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            messagebox.showerror(
                "Invalid Amount",
                "Enter a valid closing cash amount.",
                parent=parent_window,
            )
            return False

        if closing_cash < Decimal("0.00"):
            messagebox.showerror(
                "Invalid Amount",
                "Closing cash cannot be negative.",
                parent=parent_window,
            )
            return False

        notes = simpledialog.askstring(
            "Close Register Shift",
            "Enter closing notes, or leave blank:",
            initialvalue="",
            parent=parent_window,
        )

        if notes is None:
            return False

        confirmed = messagebox.askyesno(
            "Confirm Register Close",
            (
                f"Close register at {self.active_location_name}?\n\n"
                f"Counted cash: ${closing_cash:,.2f}"
            ),
            parent=parent_window,
        )

        if not confirmed:
            return False

        response = self._api_request(
            "POST",
            "/api/register/close/",
            payload={
                "register_id": register_id,
                "closing_cash": str(closing_cash),
                "notes": str(notes or "").strip(),
            },
        )

        if not response:
            messagebox.showerror(
                "Register Error",
                "QuickStock could not complete the close-register request.",
                parent=parent_window,
            )
            return False

        active_register = response.get("active_register")

        if not response.get("ok"):
            if isinstance(active_register, dict):
                self._apply_active_register_snapshot(active_register)

            messagebox.showerror(
                "Register Error",
                response.get(
                    "message",
                    "The register shift could not be closed.",
                ),
                parent=parent_window,
            )
            return False

        self._apply_active_register_snapshot(None)

        try:
            cached_user = self._load_offline_credentials(
                self.current_username
            )

            if isinstance(cached_user, dict):
                cached_user["active_register"] = None
                SecureCache(USER_CACHE_FILE).save(cached_user)

        except Exception as exc:
            logger.warning(
                "Could not clear the cached register snapshot: %s",
                exc,
            )

        messagebox.showinfo(
            "Register Closed",
            response.get(
                "message",
                "The register shift was closed successfully.",
            ),
            parent=parent_window,
        )
        self._update_home_dashboard_widgets()

        controller = getattr(
            self,
            "cash_register_controller",
            None,
        )

        if controller:
            try:
                if hasattr(controller, "refresh_register_status"):
                    controller.refresh_register_status()

                if hasattr(controller, "refresh_connectivity_banner"):
                    controller.refresh_connectivity_banner()

            except Exception as exc:
                logger.warning(
                    "Could not refresh the cash-register interface: %s",
                    exc,
                )

        return True

class Cash_register_GUI:
    def __init__(self, root, system,gui_parent):   
        self.system = system
        self.gui_parent = gui_parent
        if self.gui_parent:
            self.gui_parent.cash_register_controller = self
        self.cart = []
        self.root = root
        self.root.protocol("WM_DELETE_WINDOW", self.close_register)
        
        self.item_var = tk.StringVar()
        self.barcode_entry = None
        self.qty_entry = None
        self.cart_display = None
        self.total_label = None
        self.search_field= None
        self.discount_entry = None
        self.combo = None
        self.customer_entry = None
        self.fast_mode_var = tk.BooleanVar(value=True)
        self.close_after_sale_var = tk.BooleanVar(value=False)
        self.status_card = None
        self.status_title_label = None
        self.status_detail_label = None
        self.queue_badge_label = None
        self.mode_badge_label = None
        self.scanner_hint_label = None
        self.register_canvas = None
        self.register_scrollbar = None
        self.register_workspace = None
        self.register_window_id = None
        self.business_trn = str(
            getattr(self.gui_parent, "business_trn", "")
            or self.system.settings.get("business_trn", "")
            or self.system.settings.get("trn", "")
        ).strip()

        if self.gui_parent:
            self.gui_parent.cash_register_controller = self
        self.create_cash_register_ui()

    def add_to_cart_logic(self, item, quantity):
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            return False
        if quantity <= 0:
            return False
        if item.get("is_deleted") or str(item.get("status") or "active").lower() != "active":
            return False
        existing_in_cart = next((i for i in self.cart if i["SKU"] == item["SKU"]), None)
        requested_total = quantity + (existing_in_cart["Quantity"] if existing_in_cart else 0)
        if requested_total > int(item.get("Amount") or 0):
            return False
        if existing_in_cart:
            existing_in_cart["Quantity"] += quantity
        else:
            self.cart.append({
                "Product ID": item.get("id") or item.get("product_id"),
                "SKU": item["SKU"],
                "Name": item["Name"],
                "Cost": item.get("Cost", item.get("cost", 0)),
                "Price": item["Price"],
                "Quantity": quantity,
                "Taxable": bool(item.get("is_taxable", True)),
                "Status": str(item.get("status") or "active").lower(),
                "Is Deleted": bool(item.get("is_deleted", False)),
            })
        self.update_cart_display()
        return True

    def handle_barcode_scan(self, event):
            """Processes input from the barcode scanner or manual SKU entry"""
            if not self.barcode_entry.winfo_exists():
                return

            raw_input = self.barcode_entry.get().strip().upper()
            if not raw_input:
                return
            # Remove hidden scanner/control characters
            raw_input = "".join(ch for ch in raw_input if ch.isalnum() or ch in "-_")
            if not raw_input:
                self.barcode_entry.delete(0, tk.END)
                return

            # Lazy-refresh inventory if empty (avoids needing to open Inventory screen first)
            if not self.system.inventory and self.gui_parent:
                try:
                    self.gui_parent.sync_inventory()
                    if not self.system.inventory:
                        self.gui_parent.load_from_local_cache()
                    self.refresh_inventory_sources()
                except Exception:
                    logger.warning("Failed to load local cache for cash register.")

            # Search the inventory
            item = None
            matches = []
            for i in self.system.inventory:
                stored_sku = str(i.get("SKU", ""))
                stored_barcode = str(i.get("barcode") or i.get("Barcode") or "")
                if stored_sku == raw_input or stored_barcode == raw_input:
                    item = i
                    break
                if raw_input in stored_sku or (stored_barcode and raw_input in stored_barcode):
                    matches.append(i)
            
            if not item:
                if len(matches) == 1:
                    item = matches[0]
                elif len(matches) > 1:
                    # Partial input matches multiple items; wait for more input
                    self.barcode_entry.selection_range(0, tk.END)
                    self.barcode_entry.icursor(tk.END)
                    return
                else:
                    # Too short or no match: wait for more input to avoid false errors
                    if len(raw_input) < 3:
                        self.barcode_entry.selection_range(0, tk.END)
                        self.barcode_entry.icursor(tk.END)
                        return

            # --- THE LOGIC CHECK ---
            if item:
                if item["Amount"] > 0 and not item.get("is_deleted") and str(item.get("status") or "active").lower() == "active":
                    # ✅ SAFE BEEP
                    if winsound: winsound.Beep(2500, 100) 
                    
                    if self.add_to_cart_logic(item, 1):
                        self.barcode_entry.delete(0, tk.END)
                        self.set_scanner_hint(f"Scanned {item['Name']} successfully.")
                    else:
                        messagebox.showwarning("Stock Limit", f"No more units of {item['Name']} are available for this cart.")
                else:
                    # ✅ SAFE BEEP
                    if winsound: winsound.Beep(500, 400) 
                    
                    messagebox.showwarning("Out of Stock", f"{item['Name']} is empty.")
                    self.barcode_entry.delete(0, tk.END)
                    self.set_scanner_hint(f"{item['Name']} is out of stock.", tone="danger")
                    self.root.after(50, self.barcode_entry.focus_set)
            else:
                # ✅ SAFE BEEP - This was the crash site!
                if winsound: winsound.Beep(300, 500)
                
                messagebox.showerror("Not Found", f"Barcode/SKU '{raw_input}' not found.")
                self.barcode_entry.delete(0, tk.END)
                self.set_scanner_hint(f"No inventory match for {raw_input}.", tone="danger")
                self.root.after(50, self.barcode_entry.focus_set)
                
    def filter_items(self, event):
        """Filters the dropdown list and auto-selects the top match"""
        term = event.widget.get().lower()
        
        # 1. Generate the filtered list
        filtered = [f'{i["SKU"]} | {i["Name"]} | Stock: {i["Amount"]}' for i in self.system.inventory
                    if not i.get("is_deleted")
                    and str(i.get("status") or "active").lower() == "active"
                    and (term in i["Name"].lower() or term in str(i["SKU"]).lower())]
        
        self.combo['values'] = filtered
        
        # 2. If we found matches, auto-populate the combobox with the first one
        if filtered:
            self.item_var.set(filtered[0])
        else:
            self.item_var.set("")

    def manual_add(self):
        try:
            selected = self.item_var.get()
            if not selected:
                return

            sku = selected.split(" | ")[0]
            qty_text = self.qty_entry.get()
            qty = int(qty_text) if qty_text else 1
            if qty <= 0:
                messagebox.showerror("Error", "Quantity must be greater than zero.")
                return

            item = next((i for i in self.system.inventory if str(i["SKU"]) == str(sku)), None)

            if item:
                if item.get("is_deleted") or str(item.get("status") or "active").lower() != "active":
                    messagebox.showerror("Error", "This item is archived or unavailable for sale.")
                elif item["Amount"] >= qty and self.add_to_cart_logic(item, qty):

                    # --- 🟢 THE CLEANUP (This is where those lines go) ---
                    self.item_var.set("")  # Clears the dropdown text
                    if self.search_field:
                        self.search_field.delete(0, tk.END)  # Clears the search box

                    # 🟢 THE RESET: Automatically jump back to the barcode scanner
                    if self.barcode_entry:
                        self.barcode_entry.focus_set()
                    self.set_scanner_hint(f"Added {item['Name']} x{qty} to cart.")

                else:
                    messagebox.showerror("Error", "Insufficient stock.")
            else:
                messagebox.showerror("Error", "Item not found.")
        except ValueError:
            messagebox.showerror("Error", "Please enter a valid number for quantity.")

    def create_cash_register_ui(self):
        self.root.title("Sell Items")
        try:
            self.root.state("zoomed")  # Windows full screen
        except Exception:
            self.root.attributes("-fullscreen", True)  # Fallback
        # Exit/toggle full screen controls
        self.root.bind("<F11>", lambda e: self.toggle_fullscreen())
        self.root.bind("<Escape>", lambda e: self.toggle_fullscreen(False))
        self.root.bind("<Control-q>", lambda e: self.close_register())
        self.root.bind("<F12>", lambda e: self.process_sale())
        self.root.bind("<Control-Return>", lambda e: self.process_sale())
        # ESC handled by fullscreen toggle above

        palette = self._register_palette()
        self.root.configure(bg=palette["window_bg"])
        self._apply_register_ttk_theme(palette)

        shell = tk.Frame(self.root, bg=palette["window_bg"])
        shell.pack(fill="both", expand=True)

        nav_bar = tk.Frame(shell, bg=palette["header_bg"], height=108)
        nav_bar.pack(fill="x")
        nav_bar.pack_propagate(False)

        hero = tk.Frame(nav_bar, bg=palette["header_bg"])
        hero.pack(side="left", fill="both", expand=True, padx=26, pady=10)
        tk.Label(
            hero,
            text=getattr(self.gui_parent, "business_name", None) or "QuickStock Cash Register",
            font=("Segoe UI", 20, "bold"),
            bg=palette["header_bg"],
            fg=palette["header_title"],
        ).pack(anchor="w", pady=(12, 0))
        tk.Label(
            hero,
            text="Fast checkout with register, location, and sync status always visible.",
            font=("Segoe UI", 10),
            bg=palette["header_bg"],
            fg=palette["header_muted"],
        ).pack(anchor="w", pady=(2, 0))

        header_actions = tk.Frame(nav_bar, bg=palette["header_bg"])
        header_actions.pack(side="right", padx=20, pady=10, fill="y")
        tk.Label(
            header_actions,
            text="STATION",
            font=("Segoe UI", 8, "bold"),
            bg=palette["header_bg"],
            fg=palette["header_muted"],
        ).pack(anchor="e", pady=(14, 0))
        self.station_value_label = tk.Label(
            header_actions,
            text=str(getattr(self.gui_parent, "current_username", "") or "cashier").upper(),
            font=("Segoe UI", 12, "bold"),
            bg=palette["header_bg"],
            fg=palette["header_accent"],
        )
        self.station_value_label.pack(anchor="e", pady=(0, 8))
        tk.Button(
            header_actions,
            text="Back to Main GUI",
            command=self.close_register,
            bg=palette["header_button_bg"],
            fg=palette["header_button_fg"],
            activebackground=palette["header_button_active"],
            activeforeground=palette["header_button_fg"],
            relief="flat",
            padx=14,
            pady=9,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="e")

        body_shell = tk.Frame(shell, bg=palette["window_bg"])
        body_shell.pack(fill="both", expand=True)

        self.register_canvas = tk.Canvas(
            body_shell,
            bg=palette["window_bg"],
            highlightthickness=0,
            bd=0,
        )
        self.register_scrollbar = ttk.Scrollbar(
            body_shell,
            orient="vertical",
            command=self.register_canvas.yview,
        )
        self.register_canvas.configure(yscrollcommand=self.register_scrollbar.set)
        self.register_canvas.pack(side="left", fill="both", expand=True)
        self.register_scrollbar.pack(side="right", fill="y")

        workspace = tk.Frame(self.register_canvas, bg=palette["window_bg"])
        self.register_workspace = workspace
        self.register_window_id = self.register_canvas.create_window((0, 0), window=workspace, anchor="nw")
        workspace.bind("<Configure>", self._update_register_scrollregion)
        self.register_canvas.bind("<Configure>", self._resize_register_canvas_window)
        self.register_canvas.bind("<Enter>", self._bind_register_mousewheel)
        self.register_canvas.bind("<Leave>", self._unbind_register_mousewheel)

        self.status_card = tk.Frame(
            workspace,
            bg=palette["status_bg"],
            highlightthickness=1,
            highlightbackground=palette["status_border"],
        )
        self.status_card.pack(fill="x", padx=26, pady=(18, 16))

        status_inner = tk.Frame(self.status_card, bg=palette["status_bg"])
        status_inner.pack(fill="x", padx=18, pady=14)

        self.status_title_label = tk.Label(
            status_inner,
            text="Offline ready",
            font=("Segoe UI", 12, "bold"),
            bg=palette["status_bg"],
            fg=palette["status_title"],
        )
        self.status_title_label.pack(anchor="w")

        self.status_detail_label = tk.Label(
            status_inner,
            text="Sales can continue from local cache while sync waits for reconnect.",
            font=("Segoe UI", 10),
            bg=palette["status_bg"],
            fg=palette["status_detail"],
            justify="left",
            wraplength=1080,
        )
        self.status_detail_label.pack(anchor="w", pady=(5, 10))

        badge_row = tk.Frame(status_inner, bg=palette["status_bg"])
        badge_row.pack(fill="x")
        self.mode_badge_label = tk.Label(
            badge_row,
            text="Mode",
            font=("Segoe UI", 9, "bold"),
            bg=palette["badge_bg"],
            fg=palette["badge_fg"],
            padx=10,
            pady=4,
        )
        self.mode_badge_label.pack(side="left")
        self.queue_badge_label = tk.Label(
            badge_row,
            text="Queued actions",
            font=("Segoe UI", 9, "bold"),
            bg=palette["queue_bg"],
            fg=palette["queue_fg"],
            padx=10,
            pady=4,
        )
        self.queue_badge_label.pack(side="left", padx=(8, 0))

        content = tk.Frame(workspace, bg=palette["window_bg"])
        content.pack(fill="both", expand=True, padx=26, pady=(0, 18))
        content.columnconfigure(0, weight=14)
        content.columnconfigure(1, weight=9)
        content.rowconfigure(0, weight=1)

        left_column = tk.Frame(content, bg=palette["window_bg"])
        left_column.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right_column = tk.Frame(content, bg=palette["window_bg"])
        right_column.grid(row=0, column=1, sticky="nsew", padx=(12, 0))

        scanner_card = self._build_register_card(left_column, palette)
        scanner_card.pack(fill="x", pady=(0, 12))
        tk.Label(
            scanner_card,
            text="Scan Barcode or Type Product Name",
            font=("Segoe UI", 16, "bold"),
            bg=palette["card"],
            fg=palette["title"],
            justify="left",
        ).pack(anchor="w")
        self.barcode_entry = tk.Entry(
            scanner_card,
            font=("Segoe UI", 18, "bold"),
            bg=palette["entry_bg"],
            fg=palette["input_fg"],
            insertbackground=palette["input_fg"],
            relief="flat",
            bd=0,
            highlightthickness=2,
            highlightbackground=palette["entry_border"],
            highlightcolor=palette["entry_focus"],
        )
        self.barcode_entry.pack(fill="x", ipady=18, pady=(14, 0))
        self.barcode_entry.bind("<Return>", self.handle_barcode_scan)
        self.barcode_entry.focus_set()

        self.scanner_hint_label = tk.Label(
            scanner_card,
            text="Scanner focused and ready.",
            font=("Segoe UI", 10, "bold"),
            bg=palette["card"],
            fg=palette["accent"],
        )
        self.scanner_hint_label.pack(anchor="w", pady=(10, 0))

        manual_card = self._build_register_card(left_column, palette)
        manual_card.pack(fill="x", pady=(0, 12))
        tk.Label(manual_card, text="Manual Selection", font=("Segoe UI", 13, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        tk.Label(
            manual_card,
            text="Search by name or SKU, then add the right quantity without leaving the register.",
            font=("Segoe UI", 10),
            bg=palette["card"],
            fg=palette["muted"],
        ).pack(anchor="w", pady=(4, 10))
        tk.Label(manual_card, text="Search Name / SKU", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["label"]).pack(anchor="w")
        self.search_field = tk.Entry(
            manual_card,
            font=("Segoe UI", 11),
            bg=palette["entry_bg"],
            fg=palette["input_fg"],
            insertbackground=palette["input_fg"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=palette["card_border"],
            highlightcolor=palette["entry_focus"],
        )
        self.search_field.pack(fill="x", pady=(6, 10), ipady=9)
        self.search_field.bind("<KeyRelease>", self.filter_items)
        self.search_field.bind("<Return>", lambda e: self.manual_add())

        if self.gui_parent:
            try:
                self.gui_parent.load_from_local_cache()
            except Exception:
                pass
        self.combo = ttk.Combobox(manual_card, values=[], textvariable=self.item_var, width=40, style="Register.TCombobox")
        self.combo.pack(fill="x", pady=(0, 10))
        self.refresh_inventory_sources()

        tk.Label(manual_card, text="Quantity", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["label"]).pack(anchor="w")
        self.qty_entry = tk.Entry(
            manual_card,
            font=("Segoe UI", 11),
            bg=palette["entry_bg"],
            fg=palette["input_fg"],
            insertbackground=palette["input_fg"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=palette["card_border"],
            highlightcolor=palette["entry_focus"],
        )
        self.qty_entry.insert(0, "1")
        self.qty_entry.pack(fill="x", pady=(6, 10), ipady=9)

        tk.Button(
            manual_card,
            text="Add Manual Item",
            command=self.manual_add,
            bg=palette["button_primary"],
            fg=palette["button_primary_text"],
            activebackground=palette["button_primary_active"],
            activeforeground=palette["button_primary_text"],
            relief="flat",
            padx=14,
            pady=10,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")

        self.terminal_card = tk.Frame(
            right_column,
            bg=palette["card"],
            highlightthickness=1,
            highlightbackground=palette["card_border"],
            padx=18,
            pady=16,
        )
        self.terminal_card.pack(fill="x", pady=(0, 12))
        tk.Label(
            self.terminal_card,
            text="ACTIVE TERMINAL",
            font=("Segoe UI", 9, "bold"),
            bg=palette["card"],
            fg=palette["label"],
        ).pack(anchor="w")
        terminal_row = tk.Frame(self.terminal_card, bg=palette["card"])
        terminal_row.pack(fill="x", pady=(6, 0))
        self.terminal_indicator = tk.Label(
            terminal_row,
            text="●",
            font=("Segoe UI", 11, "bold"),
            bg=palette["card"],
            fg=palette["success"],
        )
        self.terminal_indicator.pack(side="left")
        self.terminal_location_label = tk.Label(
            terminal_row,
            text=self._current_terminal_label(),
            font=("Segoe UI", 13, "bold"),
            bg=palette["card"],
            fg=palette["header_accent"],
        )
        self.terminal_location_label.pack(side="left", padx=(10, 0))

        cart_card = self._build_register_card(right_column, palette)
        cart_card.pack(fill="both", expand=True, pady=(0, 12))
        cart_head = tk.Frame(cart_card, bg=palette["card"])
        cart_head.pack(fill="x", pady=(0, 10))
        tk.Label(cart_head, text="TERMINAL CART", font=("Segoe UI", 11, "bold"), bg=palette["card"], fg=palette["label"]).pack(side="left")
        self.cart_count_label = tk.Label(
            cart_head,
            text="0",
            font=("Segoe UI", 11, "bold"),
            bg=palette["badge_active_bg"],
            fg="#FFFFFF",
            padx=12,
            pady=4,
        )
        self.cart_count_label.pack(side="right")
        self.cart_display = tk.Listbox(
            cart_card,
            height=14,
            width=55,
            bg=palette["list_bg"],
            fg=palette["list_fg"],
            selectbackground=palette["badge_active_bg"],
            selectforeground="#FFFFFF",
            relief="flat",
            bd=0,
            activestyle="none",
            highlightthickness=1,
            highlightbackground=palette["card_border"],
            font=("Segoe UI", 11),
        )
        self.cart_display.pack(fill="both", expand=True)

        totals_row = tk.Frame(cart_card, bg=palette["card"])
        totals_row.pack(fill="x", pady=(12, 0))
        self.total_label = tk.Label(
            totals_row,
            text="TOTAL: $0.00",
            font=("Segoe UI", 16, "bold"),
            bg=palette["card"],
            fg=palette["title"],
        )
        self.total_label.pack(side="left")
        tk.Button(
            totals_row,
            text="Remove Selected",
            command=self.remove_from_cart,
            bg=palette["danger"],
            fg="#FFFFFF",
            activebackground=palette["danger_active"],
            activeforeground="#FFFFFF",
            relief="flat",
            padx=14,
            pady=8,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="right")

        checkout_card = self._build_register_card(right_column, palette)
        checkout_card.pack(fill="x")
        tk.Label(checkout_card, text="Checkout Details", font=("Segoe UI", 13, "bold"), bg=palette["card"], fg=palette["title"]).pack(anchor="w")
        tk.Label(
            checkout_card,
            text="Confirm payment details before finalizing the sale.",
            font=("Segoe UI", 10),
            bg=palette["card"],
            fg=palette["muted"],
        ).pack(anchor="w", pady=(4, 10))
        tk.Label(checkout_card, text="Apply Discount", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["label"]).pack(anchor="w")
        self.discount_entry = tk.Entry(
            checkout_card,
            font=("Segoe UI", 11, "bold"),
            bg=palette["entry_bg"],
            fg=palette["input_fg"],
            insertbackground=palette["input_fg"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=palette["card_border"],
            highlightcolor=palette["entry_focus"],
            justify="center",
        )
        self.discount_entry.insert(0, "0")
        self.discount_entry.pack(fill="x", pady=(6, 12), ipady=10)

        self.payment_summary_card = tk.Frame(checkout_card, bg=palette["payment_bg"], padx=16, pady=16)
        self.payment_summary_card.pack(fill="x", pady=(0, 14))
        payment_summary_top = tk.Frame(self.payment_summary_card, bg=palette["payment_bg"])
        payment_summary_top.pack(fill="x")
        tk.Label(
            payment_summary_top,
            text="Payment received",
            font=("Segoe UI", 11, "bold"),
            bg=palette["payment_bg"],
            fg="#FFFFFF",
        ).pack(side="left")
        self.payment_total_label = tk.Label(
            payment_summary_top,
            text="$0.00",
            font=("Segoe UI", 11, "bold"),
            bg=palette["payment_bg"],
            fg=palette["payment_total_fg"],
        )
        self.payment_total_label.pack(side="right")
        self.payment_method_label = tk.Label(
            self.payment_summary_card,
            text="Point of Sale",
            font=("Segoe UI", 10),
            bg=palette["payment_bg"],
            fg=palette["payment_subtle_fg"],
        )
        self.payment_method_label.pack(anchor="w", pady=(6, 0))

        tk.Label(checkout_card, text="Customer Name (optional)", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["label"]).pack(anchor="w")
        self.customer_entry = tk.Entry(
            checkout_card,
            font=("Segoe UI", 11),
            bg=palette["entry_bg"],
            fg=palette["input_fg"],
            insertbackground=palette["input_fg"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=palette["card_border"],
            highlightcolor=palette["entry_focus"],
        )
        self.customer_entry.pack(fill="x", pady=(6, 10), ipady=9)

        tk.Label(checkout_card, text="Payment Method", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["label"]).pack(anchor="w")
        self.payment_method_var = tk.StringVar(value="cash")
        payment_method_menu = ttk.Combobox(
            checkout_card,
            textvariable=self.payment_method_var,
            values=["cash", "card", "bank_transfer", "mobile_money", "other"],
            state="readonly",
            style="Register.TCombobox",
        )
        payment_method_menu.pack(fill="x", pady=(6, 12), ipady=6)

        billing_meta = tk.Frame(checkout_card, bg=palette["card"])
        billing_meta.pack(fill="x", pady=(2, 10))
        self.tax_meta_label = tk.Label(
            billing_meta,
            text=f"ACTIVE TAX  {self.system.settings.get('tax_label', 'GCT')} {self.system.settings.get('tax_rate', 0.15) * 100:.2f}%",
            font=("Segoe UI", 10, "bold"),
            bg=palette["card"],
            fg=palette["label"],
        )
        self.tax_meta_label.pack(side="left")
        self.total_value_label = tk.Label(
            billing_meta,
            text="$0.00",
            font=("Segoe UI", 28, "bold"),
            bg=palette["card"],
            fg=palette["title"],
        )
        self.total_value_label.pack(side="right")

        tk.Label(checkout_card, text="Amount Paid", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["label"]).pack(anchor="w")
        self.amount_paid_var = tk.StringVar(value="0.00")
        self.amount_paid_entry = tk.Entry(
            checkout_card,
            textvariable=self.amount_paid_var,
            font=("Segoe UI", 11),
            bg=palette["entry_bg"],
            fg=palette["input_fg"],
            insertbackground=palette["input_fg"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=palette["card_border"],
            highlightcolor=palette["entry_focus"],
        )
        self.amount_paid_entry.pack(fill="x", pady=(6, 10), ipady=9)
        tk.Label(checkout_card, text="Apply Account Credit", font=("Segoe UI", 10, "bold"), bg=palette["card"], fg=palette["label"]).pack(anchor="w")
        self.credit_to_apply_var = tk.StringVar(value="0.00")
        self.credit_to_apply_entry = tk.Entry(
            checkout_card,
            textvariable=self.credit_to_apply_var,
            font=("Segoe UI", 11),
            bg=palette["entry_bg"],
            fg=palette["input_fg"],
            insertbackground=palette["input_fg"],
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=palette["card_border"],
            highlightcolor=palette["entry_focus"],
        )
        self.credit_to_apply_entry.pack(fill="x", pady=(6, 10), ipady=9)
        self.change_due_label = tk.Label(
            checkout_card,
            text="Change Due: $0.00",
            font=("Segoe UI", 11, "bold"),
            bg=palette["card"],
            fg=palette["accent"],
        )
        self.change_due_label.pack(anchor="w")
        self.overage_label = tk.Label(
            checkout_card,
            text="",
            font=("Segoe UI", 10, "bold"),
            bg=palette["notice_bg"],
            fg=palette["notice_fg"],
            wraplength=380,
            justify="left",
            padx=10,
            pady=8,
        )
        self.overage_label.pack(fill="x", pady=(8, 12))
        self.overage_label.pack_forget()

        options_frame = tk.Frame(checkout_card, bg=palette["card"])
        options_frame.pack(pady=(0, 12), fill="x")
        tk.Checkbutton(
            options_frame,
            text="Fast Mode (skip confirm)",
            variable=self.fast_mode_var,
            bg=palette["card"],
            fg=palette["title"],
            selectcolor=palette["card"],
            activebackground=palette["card"],
            activeforeground=palette["title"],
            font=("Segoe UI", 10),
        ).pack(anchor="w")
        tk.Checkbutton(
            options_frame,
            text="Close after sale",
            variable=self.close_after_sale_var,
            bg=palette["card"],
            fg=palette["title"],
            selectcolor=palette["card"],
            activebackground=palette["card"],
            activeforeground=palette["title"],
            font=("Segoe UI", 10),
        ).pack(anchor="w")

        action_row = tk.Frame(checkout_card, bg=palette["card"])
        action_row.pack(fill="x")
        tk.Button(
            action_row,
            text="FINALIZE POS",
            command=self.process_sale,
            font=("Segoe UI", 12, "bold"),
            bg=palette["button_primary"],
            fg=palette["button_primary_text"],
            activebackground=palette["button_primary_active"],
            activeforeground=palette["button_primary_text"],
            relief="flat",
            padx=16,
            pady=12,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Button(
            action_row,
            text="CLEAR CART",
            command=self.reset_sale,
            bg=palette["button_subtle"],
            fg=palette["button_subtle_text"],
            activebackground=palette["button_subtle_active"],
            activeforeground=palette["button_subtle_text"],
            relief="flat",
            padx=16,
            pady=12,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        self.discount_entry.bind("<KeyRelease>", lambda _event: self.refresh_checkout_preview())
        self.customer_entry.bind("<KeyRelease>", lambda _event: self.refresh_checkout_preview())
        self.amount_paid_entry.bind("<KeyRelease>", lambda _event: self.refresh_checkout_preview())
        self.credit_to_apply_entry.bind("<KeyRelease>", lambda _event: self.refresh_checkout_preview())
        payment_method_menu.bind("<<ComboboxSelected>>", lambda _event: self.refresh_checkout_preview())

        self.refresh_connectivity_banner()
        self.refresh_checkout_preview()

    def _update_register_scrollregion(self, _event=None):
        if self.register_canvas and self.register_canvas.winfo_exists():
            self.register_canvas.configure(scrollregion=self.register_canvas.bbox("all"))

    def _resize_register_canvas_window(self, event):
        if not self.register_canvas or not self.register_workspace or self.register_window_id is None:
            return
        width = max(event.width - (self.register_scrollbar.winfo_width() if self.register_scrollbar else 0), 1)
        self.register_canvas.itemconfigure(self.register_window_id, width=width)

    def _bind_register_mousewheel(self, _event=None):
        self.root.bind_all("<MouseWheel>", self._on_register_mousewheel)
        self.root.bind_all("<Button-4>", self._on_register_mousewheel)
        self.root.bind_all("<Button-5>", self._on_register_mousewheel)

    def _unbind_register_mousewheel(self, _event=None):
        self.root.unbind_all("<MouseWheel>")
        self.root.unbind_all("<Button-4>")
        self.root.unbind_all("<Button-5>")

    def _on_register_mousewheel(self, event):
        if not self.register_canvas or not self.register_canvas.winfo_exists():
            return
        if getattr(event, "num", None) == 4:
            self.register_canvas.yview_scroll(-1, "units")
            return
        if getattr(event, "num", None) == 5:
            self.register_canvas.yview_scroll(1, "units")
            return
        delta = getattr(event, "delta", 0)
        if delta:
            direction = -1 if delta > 0 else 1
            self.register_canvas.yview_scroll(direction, "units")

    def _register_palette(self):
        return {
            "window_bg": "#EEF3F8",
            "header_bg": "#121B2F",
            "header_title": "#F8FAFC",
            "header_muted": "#94A3B8",
            "header_accent": "#5EEAD4",
            "header_button_bg": "#1F2D46",
            "header_button_active": "#2A3C5A",
            "header_button_fg": "#F8FAFC",
            "card": "#FFFFFF",
            "card_border": "#D7E0EB",
            "title": "#13233D",
            "label": "#60758F",
            "muted": "#5F6F82",
            "accent": "#0F766E",
            "entry_bg": "#FFFFFF",
            "entry_border": "#3D7BFF",
            "entry_focus": "#3D7BFF",
            "input_fg": "#132238",
            "list_bg": "#FFFFFF",
            "list_fg": "#132238",
            "button_primary": "#1EB980",
            "button_primary_active": "#17A06E",
            "button_primary_text": "#FFFFFF",
            "button_subtle": "#E5EDF6",
            "button_subtle_active": "#D5E0EC",
            "button_subtle_text": "#132238",
            "danger": "#D64432",
            "danger_active": "#B93829",
            "success": "#19B76B",
            "status_bg": "#F8FBFF",
            "status_border": "#3D7BFF",
            "status_title": "#1E4CA1",
            "status_detail": "#5F6F82",
            "status_online_bg": "#F8FBFF",
            "status_online_border": "#3D7BFF",
            "status_online_title": "#1E4CA1",
            "status_online_detail": "#5F6F82",
            "status_offline_bg": "#F0FDF4",
            "status_offline_border": "#1EB980",
            "status_offline_title": "#0E8E5D",
            "status_offline_detail": "#436A57",
            "status_sync_bg": "#FFF7ED",
            "status_sync_border": "#F59E0B",
            "status_sync_title": "#B45309",
            "status_sync_detail": "#9A6B31",
            "badge_bg": "#E9F2FF",
            "badge_fg": "#2E63D3",
            "badge_active_bg": "#3D7BFF",
            "badge_active_fg": "#FFFFFF",
            "queue_bg": "#E6F7EF",
            "queue_fg": "#0E8E5D",
            "payment_bg": "#0F766E",
            "payment_total_fg": "#DFFCF6",
            "payment_subtle_fg": "#CFF8EA",
            "notice_bg": "#EFF6FF",
            "notice_fg": "#1D4ED8",
        }

    def _build_register_card(self, parent, palette):
        return tk.Frame(
            parent,
            bg=palette["card"],
            highlightthickness=1,
            highlightbackground=palette["card_border"],
            padx=18,
            pady=18,
        )

    def _apply_register_ttk_theme(self, palette):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Register.TCombobox",
            fieldbackground=palette["entry_bg"],
            background=palette["entry_bg"],
            foreground=palette["input_fg"],
            bordercolor=palette["card_border"],
            lightcolor=palette["entry_bg"],
            darkcolor=palette["entry_bg"],
            arrowcolor=palette["title"],
            relief="flat",
            padding=8,
        )
        style.map(
            "Register.TCombobox",
            fieldbackground=[("readonly", palette["entry_bg"])],
            selectbackground=[("readonly", palette["entry_bg"])],
            selectforeground=[("readonly", palette["input_fg"])],
        )

    def _current_terminal_label(self):
        if self.gui_parent:
            target_id = getattr(self.gui_parent, "active_location_id", None) or getattr(self.gui_parent, "default_location_id", None)
            for location in getattr(self.gui_parent, "locations", []) or []:
                if self._coerce_int(location.get("id")) == self._coerce_int(target_id):
                    return str(location.get("name") or "MAIN STORE").upper()
        return str(
            self.system.settings.get("default_location_name")
            or self.system.settings.get("branch_name")
            or "MAIN STORE"
        ).upper()

    def _receipt_number_value(self, value):
        helper = getattr(self.gui_parent, "_receipt_number_value", None)
        if callable(helper):
            return helper(value)
        try:
            return float(value)
        except Exception:
            try:
                return float(str(value))
            except Exception:
                return 0.0

    def _normalize_customer_name(self, value):
        helper = getattr(self.gui_parent, "_normalize_customer_name", None)
        if callable(helper):
            return helper(value)
        return " ".join(str(value or "").split()).strip()

    def _customer_credit_value(self, customer_name):
        helper = getattr(self.gui_parent, "_customer_credit_value", None)
        if callable(helper):
            return helper(customer_name)
        return 0.0

    def _adjust_customer_credit(self, customer_name, delta):
        helper = getattr(self.gui_parent, "_adjust_customer_credit", None)
        if callable(helper):
            return helper(customer_name, delta)
        return None

    def _coerce_int(self, value):
        helper = getattr(self.gui_parent, "_coerce_int", None)
        if callable(helper):
            return helper(value)
        try:
            return int(value)
        except Exception:
            return None

    def _create_receipt_payment_entry(
        self,
        amount,
        payment_method="cash",
        reference="",
        notes="",
        amount_tendered=None,
        change_given=0.0,
        applied_customer_credit=0.0,
        credited_customer_overpayment=0.0,
    ):
        helper = getattr(self.gui_parent, "_create_receipt_payment_entry", None)
        if callable(helper):
            return helper(
                amount,
                payment_method=payment_method,
                reference=reference,
                notes=notes,
                amount_tendered=amount_tendered,
                change_given=change_given,
                applied_customer_credit=applied_customer_credit,
                credited_customer_overpayment=credited_customer_overpayment,
            )
        amount_value = round(max(0.0, self._receipt_number_value(amount)), 2)
        tendered_value = amount_value if amount_tendered is None else round(max(0.0, self._receipt_number_value(amount_tendered)), 2)
        return {
            "payment_id": uuid.uuid4().hex,
            "amount": amount_value,
            "payment_method": str(payment_method or "cash").strip() or "cash",
            "reference": str(reference or "").strip(),
            "notes": str(notes or "").strip(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "amount_tendered": tendered_value,
            "change_given": round(max(0.0, self._receipt_number_value(change_given)), 2),
            "applied_customer_credit": round(max(0.0, self._receipt_number_value(applied_customer_credit)), 2),
            "credited_customer_overpayment": round(max(0.0, self._receipt_number_value(credited_customer_overpayment)), 2),
        }

    def _sync_receipt_payment_state(self, receipt):
        helper = getattr(self.gui_parent, "_sync_receipt_payment_state", None)
        if callable(helper):
            return helper(receipt)
        history = receipt.get("Payment History")
        if not isinstance(history, list):
            history = []
        receipt["Payment History"] = [entry for entry in history if isinstance(entry, dict)]
        total_bill = round(self._receipt_number_value(receipt.get("Total Bill", 0)), 2)
        total_paid = round(sum(self._receipt_number_value(entry.get("amount", 0)) for entry in receipt["Payment History"]), 2)
        balance_due = round(max(0.0, total_bill - total_paid), 2)
        receipt["Total Paid"] = total_paid
        receipt["Balance Due"] = balance_due
        receipt["Payment Status"] = "Paid" if total_bill > 0 and balance_due <= 0 else "Issued"
        if receipt["Payment History"]:
            latest = receipt["Payment History"][-1]
            receipt["Amount Paid"] = round(self._receipt_number_value(latest.get("amount_tendered", latest.get("amount", 0))), 2)
            receipt["Change Given"] = round(self._receipt_number_value(latest.get("change_given", 0)), 2)
            receipt["Payment Method"] = latest.get("payment_method", receipt.get("Payment Method", "cash"))
        else:
            receipt["Amount Paid"] = 0.0
            receipt["Change Given"] = 0.0
        return receipt

    def _save_receipts_and_customers(self):
        helper = getattr(self.gui_parent, "_save_receipts_and_customers", None)
        if callable(helper):
            return helper()
        storage.save_data(
            self.system.inventory,
            self.system.receipts,
            self.system.settings,
        )

    def _pending_queue_count(self):
        if not self.gui_parent:
            return 0
        try:
            return len(self.gui_parent._read_pending_queue())
        except Exception:
            return 0

    def set_scanner_hint(self, text, tone="accent"):
        if not self.scanner_hint_label or not self.scanner_hint_label.winfo_exists():
            return

        palette = self._register_palette()
        tones = {
            "accent": palette["accent"],
            "muted": palette["muted"],
            "warn": palette["status_title"],
            "danger": palette["danger"],
        }
        self.scanner_hint_label.configure(text=text, fg=tones.get(tone, palette["accent"]))

    def refresh_register_status(self):
        if self.station_value_label and self.station_value_label.winfo_exists():
            self.station_value_label.configure(
                text=str(getattr(self.gui_parent, "current_username", "") or "cashier").upper()
            )
        if self.terminal_location_label and self.terminal_location_label.winfo_exists():
            self.terminal_location_label.configure(text=self._current_terminal_label())
        self.refresh_connectivity_banner()

    def refresh_connectivity_banner(self, syncing=False):
        if not self.status_card or not self.status_card.winfo_exists() or not self.gui_parent:
            return

        palette = self._register_palette()
        queue_count = self._pending_queue_count()
        api_status = getattr(self.gui_parent, "api_status", "UNKNOWN")
        is_offline = getattr(self.gui_parent, "_is_offline", False) or not getattr(self.gui_parent, "api_token", None)

        if syncing:
            title = "Sync in progress"
            detail = "Queued work is being checked against the web API. Cashiers can keep selling while sync completes."
            bg = palette["status_sync_bg"]
            border = palette["status_sync_border"]
            title_fg = palette["status_sync_title"]
            detail_fg = palette["status_sync_detail"]
        elif is_offline:
            title = "Offline checkout active"
            detail = "This register is working from local cache and will queue sales for sync when the connection returns."
            if api_status == "TOKEN_EXPIRED":
                detail = "Your online session expired, but local checkout can continue while sales wait to sync."
            elif api_status == "AUTH_FAILED":
                detail = "Login needs attention. Local checkout is still available from cached data."
            bg = palette["status_offline_bg"]
            border = palette["status_offline_border"]
            title_fg = palette["status_offline_title"]
            detail_fg = palette["status_offline_detail"]
        else:
            title = "Online and ready"
            detail = "Sales are connected to the web API and queued actions can sync immediately."
            bg = palette["status_online_bg"]
            border = palette["status_online_border"]
            title_fg = palette["status_online_title"]
            detail_fg = palette["status_online_detail"]

        mode_text = "Mode: Offline" if is_offline else "Mode: Online"
        if syncing:
            mode_text = f"{mode_text} | Syncing"

        self.status_card.configure(bg=bg, highlightbackground=border)
        self.status_title_label.configure(text=title, bg=bg, fg=title_fg)
        self.status_detail_label.configure(text=detail, bg=bg, fg=detail_fg)
        self.mode_badge_label.configure(
            text=mode_text,
            bg=palette["badge_bg"] if is_offline else palette["badge_active_bg"],
            fg=palette["badge_fg"] if is_offline else palette["badge_active_fg"],
        )
        self.queue_badge_label.configure(text=f"Queued actions: {queue_count}")
        if self.terminal_location_label and self.terminal_location_label.winfo_exists():
            self.terminal_location_label.configure(text=self._current_terminal_label())

        self.status_title_label.configure(text=title)
        self.status_detail_label.configure(text=detail)

        if is_offline:
            self.set_scanner_hint("Offline mode is active. Local cache and queued sync are keeping checkout moving.", tone="warn")
        else:
            self.set_scanner_hint("Online mode is active. Sales can sync immediately after checkout.", tone="accent")

    def remove_from_cart(self):
        selected = self.cart_display.curselection()
        if selected:
            del self.cart[selected[0]]
            self.update_cart_display()

    def update_cart_display(self):
        try:
            if not self.cart_display or not self.cart_display.winfo_exists():
                return
            self.cart_display.delete(0, tk.END)
            total = 0
            for item in self.cart:
                line_total = item["Price"] * item["Quantity"]
                total += line_total
                self.cart_display.insert(tk.END, f'{item["Name"]} x{item["Quantity"]} - ${line_total:.2f}')
            if self.total_label and self.total_label.winfo_exists():
                self.total_label.config(text=f"TOTAL: ${total:.2f}")
            if getattr(self, "cart_count_label", None) and self.cart_count_label.winfo_exists():
                self.cart_count_label.config(text=str(sum(item["Quantity"] for item in self.cart)))
            self.refresh_connectivity_banner()
            self.refresh_checkout_preview()
        except tk.TclError:
            return

    def _current_checkout_totals(self):
        try:
            tax_rate = Decimal(str(self.system.settings.get("tax_rate", 0.15))).quantize(CHECKOUT_RATE_QUANTUM)
        except (InvalidOperation, TypeError, ValueError):
            tax_rate = Decimal("0.1500")
        tax_label = self.system.settings.get("tax_label", "GCT")
        net_subtotal = Decimal("0.00")
        gross_total = Decimal("0.00")
        tax_total = Decimal("0.00")
        for item in self.cart:
            try:
                line_gross = (
                    Decimal(str(item.get("Price"))) * Decimal(str(item.get("Quantity")))
                ).quantize(CHECKOUT_MONEY_QUANTUM)
            except (InvalidOperation, TypeError, ValueError):
                line_gross = Decimal("0.00")
            if bool(item.get("Taxable", True)):
                line_net = (line_gross / (Decimal("1.0000") + tax_rate)).quantize(CHECKOUT_MONEY_QUANTUM)
                line_tax = (line_gross - line_net).quantize(CHECKOUT_MONEY_QUANTUM)
            else:
                line_net = line_gross
                line_tax = Decimal("0.00")
            gross_total += line_gross
            net_subtotal += line_net
            tax_total += line_tax

        discount_error = None
        try:
            discount_val = self.discount_entry.get().strip()
            discount_percent = Decimal(discount_val or "0").quantize(CHECKOUT_MONEY_QUANTUM)
            if discount_percent < 0 or discount_percent > 100:
                discount_error = "Discount percentage must be between 0 and 100."
        except (InvalidOperation, TypeError, ValueError):
            discount_percent = Decimal("0.00")
            discount_error = "Discount percentage must be a valid number."

        if discount_error:
            discount_amount = Decimal("0.00")
        else:
            discount_amount = (gross_total * discount_percent / Decimal("100")).quantize(CHECKOUT_MONEY_QUANTUM)
        total_bill = (gross_total - discount_amount).quantize(CHECKOUT_MONEY_QUANTUM)
        return {
            "subtotal": float(net_subtotal.quantize(CHECKOUT_MONEY_QUANTUM)),
            "gross_total": float(gross_total.quantize(CHECKOUT_MONEY_QUANTUM)),
            "tax_rate": float(tax_rate),
            "tax_label": tax_label,
            "gct": float(tax_total.quantize(CHECKOUT_MONEY_QUANTUM)),
            "discount_amount": float(discount_amount),
            "discount_percent": float(discount_percent),
            "discount_error": discount_error,
            "total_bill": float(total_bill),
        }

    def refresh_checkout_preview(self):
        totals = self._current_checkout_totals()
        amount_paid = self._receipt_number_value(getattr(self, "amount_paid_var", None).get() if getattr(self, "amount_paid_var", None) else 0)
        total_bill = totals["total_bill"]
        payment_method = (self.payment_method_var.get().strip().lower() if getattr(self, "payment_method_var", None) else "cash") or "cash"
        customer_name = self._normalize_customer_name(self.customer_entry.get() if self.customer_entry else "")
        available_credit = self._customer_credit_value(customer_name) if customer_name else 0.0
        requested_credit = self._receipt_number_value(getattr(self, "credit_to_apply_var", None).get() if getattr(self, "credit_to_apply_var", None) else 0)
        credit_applied = min(max(0.0, requested_credit), available_credit, total_bill)
        remaining_due = max(0.0, total_bill - credit_applied)
        change_due = max(0.0, round(amount_paid - remaining_due, 2))
        overage = change_due if payment_method == "cash" else max(0.0, round(amount_paid - remaining_due, 2))

        if getattr(self, "tax_meta_label", None) and self.tax_meta_label.winfo_exists():
            self.tax_meta_label.config(
                text=f"ACTIVE TAX  {totals['tax_label']} {totals['tax_rate'] * 100:.2f}%"
            )
        if getattr(self, "total_value_label", None) and self.total_value_label.winfo_exists():
            self.total_value_label.config(text=f"${total_bill:.2f}")
        if getattr(self, "payment_total_label", None) and self.payment_total_label.winfo_exists():
            self.payment_total_label.config(text=f"${total_bill:.2f}")
        if getattr(self, "payment_method_label", None) and self.payment_method_label.winfo_exists():
            payment_names = {
                "cash": "Point of Sale",
                "card": "Card Terminal",
                "bank_transfer": "Bank Transfer",
                "mobile_money": "Mobile Money",
                "other": "Other Payment",
            }
            self.payment_method_label.config(text=payment_names.get(payment_method, "Point of Sale"))
        if getattr(self, "change_due_label", None) and self.change_due_label.winfo_exists():
            label = "Change Due" if payment_method == "cash" else "Amount Over"
            self.change_due_label.config(text=f"{label}: ${change_due:.2f}")

        if getattr(self, "overage_label", None) and self.overage_label.winfo_exists():
            if overage > 0:
                if customer_name:
                    message = f"Credit applied: ${credit_applied:.2f}. Customer is over by ${overage:.2f}. This extra amount can be stored on {customer_name}'s account as credit."
                else:
                    message = f"Customer is over by ${overage:.2f}. Enter a customer name to store this as account credit."
                self.overage_label.config(text=message)
                self.overage_label.pack(fill="x", pady=(8, 12))
            else:
                if credit_applied > 0 and customer_name:
                    self.overage_label.config(text=f"Credit applied: ${credit_applied:.2f}. Remaining due after credit: ${remaining_due:.2f}.")
                    self.overage_label.pack(fill="x", pady=(8, 12))
                else:
                    self.overage_label.pack_forget()

    def _next_local_invoice_number(self):
        try:
            return int(self.system.sales_manager._next_invoice_number()) + 1
        except Exception:
            return int(datetime.now(timezone.utc).timestamp() % 1000000)

    def _build_local_receipt_from_cart(
        self,
        customer_name,
        document_type,
        payment_method,
        amount_paid,
        change_given,
        credit_applied,
        credited_overpayment,
        totals,
        payment_notes="",
        payment_reference="",
    ):
        tax_label = totals["tax_label"]
        receipt = {
            "Invoice No": self._next_local_invoice_number(),
            "Business Name": self.system.settings.get("brand_name", "QuickStock JA"),
            "Business TRN": self.business_trn,
            "Customer Name": customer_name,
            "Document Type": document_type,
            "Brand Logo": self.system.settings.get("brand_logo"),
            "Brand Email": self.system.settings.get("brand_email", ""),
            "Brand Phone": self.system.settings.get("brand_phone", ""),
            "Brand Address": self.system.settings.get("brand_address", ""),
            "TaxLabel": tax_label,
            "TaxRate": totals["tax_rate"],
            "Items Purchased": [
                {
                    "SKU": item.get("SKU", ""),
                    "Name": item.get("Name", ""),
                    "Quantity": item.get("Quantity", 0),
                    "Unit Price": self._receipt_number_value(item.get("Price", 0)),
                    "Total": round(self._receipt_number_value(item.get("Price", 0)) * self._coerce_int(item.get("Quantity")) if self._coerce_int(item.get("Quantity")) else 0, 2),
                }
                for item in self.cart
            ],
            "Subtotal": totals["subtotal"],
            "Discount": totals["discount_amount"],
            tax_label: totals["gct"],
            "GCT": totals["gct"],
            "Total Bill": totals["total_bill"],
            "Timestamp": datetime.now(timezone.utc).isoformat(),
            "Payment Method": payment_method,
        }
        payment_history = []
        if credit_applied > 0:
            payment_history.append(
                self._create_receipt_payment_entry(
                    amount=credit_applied,
                    payment_method="account_credit",
                    notes="Applied from desktop customer credit.",
                    applied_customer_credit=credit_applied,
                )
            )
        if amount_paid > 0:
            payment_history.append(
                self._create_receipt_payment_entry(
                    amount=amount_paid,
                    payment_method=payment_method,
                    reference=payment_reference,
                    notes=payment_notes,
                    amount_tendered=amount_paid,
                    change_given=change_given,
                    credited_customer_overpayment=credited_overpayment,
                )
            )
        receipt["Payment History"] = payment_history
        return self._sync_receipt_payment_state(receipt)

    def process_sale(self):
        if getattr(self, "_sale_lock", False):
            return

        if not self.cart:
            messagebox.showerror("Error", "Cart is empty!")
            return

        self._sale_lock = True

        try:
            totals = self._current_checkout_totals()
            subtotal = totals["subtotal"]
            tax_rate = totals["tax_rate"]
            tax_label = totals["tax_label"]
            gct = totals["gct"]
            total_bill = totals["total_bill"]
            discount_amount = totals["discount_amount"]
            if totals.get("discount_error"):
                messagebox.showerror("Invalid Discount", totals["discount_error"])
                return
            customer_name = self._normalize_customer_name(
                self.customer_entry.get() if self.customer_entry else ""
            )
            document_type = "Invoice"
            payment_method = (self.payment_method_var.get().strip().lower() if getattr(self, "payment_method_var", None) else "cash") or "cash"
            amount_paid = round(max(0.0, self._receipt_number_value(self.amount_paid_var.get() if getattr(self, "amount_paid_var", None) else 0)), 2)
            available_credit = self._customer_credit_value(customer_name) if customer_name else 0.0
            requested_credit = round(max(0.0, self._receipt_number_value(self.credit_to_apply_var.get() if getattr(self, "credit_to_apply_var", None) else 0)), 2)
            credit_applied = min(available_credit, total_bill, requested_credit)
            remaining_due_after_credit = round(max(0.0, total_bill - credit_applied), 2)
            payment_overage = round(max(0.0, amount_paid - remaining_due_after_credit), 2)
            change_given = payment_overage if payment_method == "cash" else 0.0
            credited_overpayment = payment_overage if payment_method != "cash" else 0.0

            if credited_overpayment > 0 and not customer_name:
                messagebox.showwarning(
                    "Customer Required",
                    f"This payment is over by ${credited_overpayment:.2f}. Enter a customer name so the overage can be stored as credit.",
                )
                return

            items_payload = [
                {
                    "product_id": item.get("Product ID"),
                    "sku": item.get("SKU"),
                    "quantity": item.get("Quantity"),
                    "unit_price": item.get("Price"),
                    "unit_cost": item.get("Cost"),
                    "is_taxable": item.get("Taxable", True),
                    "status": item.get("Status", "active"),
                    "is_deleted": item.get("Is Deleted", False),
                }
                for item in self.cart
            ]
            sale_payload = self.gui_parent._build_sale_payload(
                total_price=total_bill,
                gct_amount=gct,
                discount_amount=discount_amount,
                subtotal=subtotal,
                items_sold=items_payload,
                tender=payment_method,
                location_id=self.gui_parent.active_location_id or self.gui_parent.default_location_id,
                customer_name=customer_name,
                document_type=document_type,
                amount_tendered=amount_paid,
                credit_applied=credit_applied,
                change_due=change_given,
                credited_overpayment=credited_overpayment,
            )
            valid_checkout, validation_error = self.gui_parent._validate_checkout_payload(sale_payload)
            if not valid_checkout:
                messagebox.showerror(
                    "Checkout Blocked",
                    f"{validation_error['message']}\n\nField: {validation_error['field']}",
                )
                return

            if not self.fast_mode_var.get():
                if not messagebox.askyesno(
                    "Confirm Sale",
                    f"Subtotal: ${subtotal:.2f}\n"
                    f"{tax_label} ({tax_rate * 100:.0f}%): ${gct:.2f}\n"
                    f"Total: ${total_bill:.2f}\n"
                    f"Account Credit Applied: ${credit_applied:.2f}\n"
                    f"Amount Paid: ${amount_paid:.2f}\n"
                    f"Change / Overage: ${payment_overage:.2f}\n\nProceed with Transaction?"
                ):
                    return

            sale_id = None

            if self.gui_parent and self.gui_parent.api_token:
                delivery = self.gui_parent._sync_queue_request(
                    "POST",
                    "/api/sales/",
                    sale_payload,
                )
                resp = delivery.get("body") or {}
                if self.gui_parent._sale_acknowledgement_matches(sale_payload, resp):
                    sale_id = resp.get("sale_id")
                    logger.info(f"Sale recorded via API. Sale ID: {sale_id}")
                elif delivery.get("kind") == "permanent" or (
                    delivery.get("kind") == "auth" and resp.get("code")
                ):
                    messagebox.showerror(
                        "Checkout Rejected",
                        str(resp.get("message") or delivery.get("error") or "The server rejected this checkout."),
                    )
                    return
                else:
                    logger.error(
                        f"Failed to record sale via API: "
                        f"{resp.get('message', delivery.get('error') or 'Missing or invalid acknowledgement')}"
                    )
                    # Ambiguous delivery keeps the same idempotency reference.
                    self.gui_parent._queue_action("RECORD_SALE", sale_payload)
            else: # Offline or no API token
                self.gui_parent._queue_action("RECORD_SALE", sale_payload)

            for cart_item in self.cart:
                item = self.system.get_item_by_sku(cart_item["SKU"].strip().upper())
                item["Amount"] -= cart_item["Quantity"]

            if customer_name and credit_applied > 0:
                self._adjust_customer_credit(customer_name, -credit_applied)
            if customer_name and credited_overpayment > 0:
                self._adjust_customer_credit(customer_name, credited_overpayment)

            receipt = self._build_local_receipt_from_cart(
                customer_name=customer_name,
                document_type=document_type,
                payment_method=payment_method,
                amount_paid=amount_paid,
                change_given=change_given,
                credit_applied=credit_applied,
                credited_overpayment=credited_overpayment,
                totals=totals,
            )
            self.system.receipts.append(receipt)
            self._save_receipts_and_customers()

            if receipt:

                if self.gui_parent:
                    self.gui_parent.update_dashboard_stats()

                if self.close_after_sale_var.get():
                    self.close_register()
                else:
                    self.reset_sale()
                if credited_overpayment > 0 and customer_name:
                    self.set_scanner_hint(
                        f"{customer_name} is over by ${credited_overpayment:.2f}. Saved to account credit.",
                        tone="warn",
                    )
                self.refresh_connectivity_banner()
            else:
                messagebox.showwarning("Incomplete Sale", "No items were successfully processed.")

        except Exception as e:
            messagebox.showerror("Critical Error", f"An unexpected error occurred: {e}")
        
        finally:
            self._sale_lock = False

    def reset_sale(self):
        self.cart.clear()
        self.update_cart_display()
        try:
            if self.discount_entry and self.discount_entry.winfo_exists():
                self.discount_entry.delete(0, tk.END)
                self.discount_entry.insert(0, "0")
            if self.customer_entry and self.customer_entry.winfo_exists():
                self.customer_entry.delete(0, tk.END)
            if getattr(self, "amount_paid_entry", None) and self.amount_paid_entry.winfo_exists():
                self.amount_paid_entry.delete(0, tk.END)
                self.amount_paid_entry.insert(0, "0.00")
            if getattr(self, "credit_to_apply_entry", None) and self.credit_to_apply_entry.winfo_exists():
                self.credit_to_apply_entry.delete(0, tk.END)
                self.credit_to_apply_entry.insert(0, "0.00")
            if getattr(self, "payment_method_var", None):
                self.payment_method_var.set("cash")
            if self.barcode_entry and self.barcode_entry.winfo_exists():
                self.barcode_entry.delete(0, tk.END)
                self.barcode_entry.focus_set()
            self.set_scanner_hint("Cart cleared. Scanner focused and ready.", tone="muted")
            self.refresh_connectivity_banner()
            self.refresh_checkout_preview()
        except tk.TclError:
            return

    def toggle_fullscreen(self, enable=None):
        try:
            if enable is None:
                # Toggle
                is_full = bool(self.root.attributes("-fullscreen"))
                self.root.attributes("-fullscreen", not is_full)
            else:
                self.root.attributes("-fullscreen", bool(enable))
        except Exception:
            # Fallback for Windows zoomed state
            try:
                if enable is None:
                    self.root.state("normal" if self.root.state() == "zoomed" else "zoomed")
                else:
                    self.root.state("zoomed" if enable else "normal")
            except Exception:
                pass

    def refresh_inventory_sources(self):
        items = [f'{i["SKU"]} | {i["Name"]} | Stock: {i["Amount"]}' for i in self.system.inventory]
        if self.combo and self.combo.winfo_exists():
            self.combo["values"] = items

    def close_register(self):
        """Route POS exit through the parent shift-close workflow."""

        if self.gui_parent:
            self.gui_parent._handle_cash_register_exit()
            return

        # Fallback if no parent controller exists.
        self._unbind_register_mousewheel()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    InventoryGUI(root)
    root.mainloop()
