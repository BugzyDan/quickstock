import os
import sys
import types
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DESKTOP_ROOT = PROJECT_ROOT / "Desktop_UI"

for path in (PROJECT_ROOT, DESKTOP_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

os.environ.setdefault("QUICKSTOCK_ENV", "development")

try:
    import customtkinter  # noqa: F401
except ImportError:
    class _CTkWidget:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def configure(self, **kwargs):
            self.kwargs.update(kwargs)

        def pack(self, *args, **kwargs):
            self.pack_args = args
            self.pack_kwargs = kwargs

        def grid(self, *args, **kwargs):
            self.grid_args = args
            self.grid_kwargs = kwargs

        def place(self, *args, **kwargs):
            self.place_args = args
            self.place_kwargs = kwargs

    customtkinter_stub = types.ModuleType("customtkinter")
    customtkinter_stub.CTkImage = _CTkWidget
    customtkinter_stub.CTkLabel = _CTkWidget
    customtkinter_stub.CTkFrame = _CTkWidget
    customtkinter_stub.CTkButton = _CTkWidget
    customtkinter_stub.CTkEntry = _CTkWidget
    customtkinter_stub.CTkOptionMenu = _CTkWidget
    customtkinter_stub.CTkScrollableFrame = _CTkWidget
    customtkinter_stub.CTkTextbox = _CTkWidget
    customtkinter_stub.CTkToplevel = _CTkWidget
    customtkinter_stub.CTk = _CTkWidget
    customtkinter_stub.set_appearance_mode = lambda *args, **kwargs: None
    customtkinter_stub.set_default_color_theme = lambda *args, **kwargs: None
    sys.modules["customtkinter"] = customtkinter_stub

try:
    import passlib.hash  # noqa: F401
except ImportError:
    passlib_stub = types.ModuleType("passlib")
    passlib_hash_stub = types.ModuleType("passlib.hash")

    class _DjangoPbkdf2Sha256:
        @staticmethod
        def hash(value):
            return f"test-hash:{value}"

        @staticmethod
        def verify(value, hashed):
            return hashed == f"test-hash:{value}"

    passlib_hash_stub.django_pbkdf2_sha256 = _DjangoPbkdf2Sha256
    passlib_stub.hash = passlib_hash_stub
    sys.modules["passlib"] = passlib_stub
    sys.modules["passlib.hash"] = passlib_hash_stub
