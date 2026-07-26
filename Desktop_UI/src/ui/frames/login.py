"""
Login frame for QuickStock JA.
"""

import customtkinter as ctk
import tkinter as tk
from typing import Callable, Optional


class LoginFrame(ctk.CTkFrame):
    """Login frame component."""
    
    def __init__(self, master, on_login: Optional[Callable] = None, **kwargs):
        super().__init__(master, **kwargs)
        self.on_login = on_login
        self._setup_ui()
    
    def _setup_ui(self):
        """Setup the login UI."""
        # Title
        self.title_label = ctk.CTkLabel(
            self,
            text="QuickStock JA",
            font=("Arial", 24, "bold"),
        )
        self.title_label.pack(pady=30)
        
        # Username
        self.username_label = ctk.CTkLabel(self, text="Username")
        self.username_label.pack(pady=(20, 5))
        self.username_entry = ctk.CTkEntry(self, width=250)
        self.username_entry.pack(pady=5)
        
        # Password
        self.password_label = ctk.CTkLabel(self, text="Password")
        self.password_label.pack(pady=(20, 5))
        self.password_entry = ctk.CTkEntry(self, width=250, show="*")
        self.password_entry.pack(pady=5)
        
        # Role
        self.role_label = ctk.CTkLabel(self, text="Role")
        self.role_label.pack(pady=(20, 5))
        self.role_var = ctk.StringVar(value="cashier")
        self.role_combo = ctk.CTkComboBox(
            self,
            values=["superuser", "admin", "manager", "cashier"],
            variable=self.role_var,
            width=250,
        )
        self.role_combo.pack(pady=5)
        
        # Login button
        self.login_button = ctk.CTkButton(
            self,
            text="Login",
            command=self._do_login,
            width=250,
        )
        self.login_button.pack(pady=30)
    
    def _do_login(self):
        """Handle login button click."""
        if self.on_login:
            username = self.username_entry.get().strip()
            password = self.password_entry.get()
            role = self.role_var.get()
            self.on_login(username, password, role)
    
    def clear(self):
        """Clear all fields."""
        self.username_entry.delete(0, 'end')
        self.password_entry.delete(0, 'end')
        self.role_var.set("cashier")
