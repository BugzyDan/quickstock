"""
Application styles and theme configuration for QuickStock JA.
"""

import customtkinter as ctk
import tkinter as tk
from typing import Dict, Tuple


class AppStyles:
    """Centralized style configuration for the application."""
    
    # Color schemes
    COLORS = {
        "dark": {
            "bg_primary": "#1B2631",
            "bg_secondary": "#2E3B4E",
            "bg_tertiary": "#34495E",
            "fg_primary": "#58D68D",
            "fg_secondary": "#F4D03F",
            "fg_accent": "#3498DB",
            "fg_danger": "#E74C3C",
            "fg_warning": "#F39C12",
            "fg_success": "#2ECC71",
            "text_primary": "#ECF0F1",
            "text_secondary": "#BDC3C7",
            "text_muted": "#7F8C8D",
            "border": "#5D6D7E",
            "hover": "#5D6D7E",
        },
        "light": {
            "bg_primary": "#D5D8DC",
            "bg_secondary": "#F0F0F0",
            "bg_tertiary": "#FFFFFF",
            "fg_primary": "#1B2631",
            "fg_secondary": "#2ECC71",
            "fg_accent": "#3498DB",
            "fg_danger": "#E74C3C",
            "fg_warning": "#F39C12",
            "fg_success": "#27AE60",
            "text_primary": "#2C3E50",
            "text_secondary": "#5D6D7E",
            "text_muted": "#7F8C8D",
            "border": "#BDC3C7",
            "hover": "#AEB6BF",
        }
    }
    
    # Fonts
    FONTS = {
        "heading_large": ("Arial", 24, "bold"),
        "heading_medium": ("Arial", 18, "bold"),
        "heading_small": ("Arial", 14, "bold"),
        "body": ("Arial", 12),
        "body_small": ("Arial", 10),
        "button": ("Arial", 12, "bold"),
        "status": ("Arial", 10, "bold"),
    }
    
    # Spacing
    PADDING = {
        "xs": 4,
        "sm": 8,
        "md": 16,
        "lg": 24,
        "xl": 32,
    }
    
    # Button styles
    BUTTON_CORNER_RADIUS = 8
    BUTTON_HEIGHT = 40
    BUTTON_SMALL_HEIGHT = 30
    
    def __init__(self, theme: str = "system"):
        """
        Initialize application styles.
        
        Args:
            theme: Theme mode ("system", "light", or "dark")
        """
        self.theme_mode = theme
        self._apply_theme()
    
    def _apply_theme(self):
        """Apply the current theme to customtkinter."""
        ctk.set_appearance_mode(self.theme_mode)
    
    def set_theme(self, theme: str):
        """
        Set the application theme.
        
        Args:
            theme: Theme mode ("system", "light", or "dark")
        """
        self.theme_mode = theme
        self._apply_theme()
    
    def get_colors(self) -> Dict[str, str]:
        """Get colors for current theme."""
        if self.theme_mode == "dark":
            return self.COLORS["dark"]
        elif self.theme_mode == "light":
            return self.COLORS["light"]
        else:
            # System theme - use dark as default
            return self.COLORS["dark"]
    
    def get_color(self, name: str) -> str:
        """
        Get a specific color by name.
        
        Args:
            name: Color name
            
        Returns:
            Color hex code
        """
        colors = self.get_colors()
        return colors.get(name, colors.get("text_primary", "#000000"))
    
    def get_font(self, name: str) -> Tuple[str, int, str]:
        """
        Get a font by name.
        
        Args:
            name: Font name
            
        Returns:
            Font tuple (family, size, weight)
        """
        return self.FONTS.get(name, self.FONTS["body"])
    
    def get_padding(self, size: str = "md") -> int:
        """
        Get padding value by size name.
        
        Args:
            size: Padding size name
            
        Returns:
            Padding value in pixels
        """
        return self.PADDING.get(size, self.PADDING["md"])
    
    def configure_button(self, button, style: str = "primary"):
        """
        Configure a button with predefined style.
        
        Args:
            button: Button widget to configure
            style: Button style ("primary", "secondary", "danger", "success")
        """
        colors = self.get_colors()
        
        style_config = {
            "primary": {
                "fg_color": colors["fg_accent"],
                "hover_color": self._darken_color(colors["fg_accent"]),
            },
            "secondary": {
                "fg_color": colors["bg_tertiary"],
                "hover_color": colors["hover"],
            },
            "danger": {
                "fg_color": colors["fg_danger"],
                "hover_color": self._darken_color(colors["fg_danger"]),
            },
            "success": {
                "fg_color": colors["fg_success"],
                "hover_color": self._darken_color(colors["fg_success"]),
            },
        }
        
        config = style_config.get(style, style_config["primary"])
        
        if hasattr(button, "configure"):
            button.configure(
                fg_color=config["fg_color"],
                hover_color=config["hover_color"],
                corner_radius=self.BUTTON_CORNER_RADIUS,
                height=self.BUTTON_HEIGHT,
            )
    
    def _darken_color(self, hex_color: str, factor: float = 0.8) -> str:
        """
        Darken a hex color by a factor.
        
        Args:
            hex_color: Original hex color
            factor: Darkening factor (0-1)
            
        Returns:
            Darkened hex color
        """
        # Remove # if present
        hex_color = hex_color.lstrip("#")
        
        # Convert to RGB
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        
        # Darken
        r = int(r * factor)
        g = int(g * factor)
        b = int(b * factor)
        
        # Convert back to hex
        return f"#{r:02x}{g:02x}{b:02x}"
    
    def get_status_colors(self) -> Dict[str, str]:
        """
        Get colors for status indicators.
        
        Returns:
            Dictionary of status colors
        """
        return {
            "online": self.get_color("fg_success"),
            "offline": self.get_color("fg_danger"),
            "warning": self.get_color("fg_warning"),
            "syncing": self.get_color("fg_accent"),
        }


# Global style instance
default_styles = AppStyles()


def get_styles() -> AppStyles:
    """Get the global styles instance."""
    return default_styles