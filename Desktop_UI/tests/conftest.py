import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DESKTOP_ROOT = PROJECT_ROOT / "Desktop_UI"

for path in (PROJECT_ROOT, DESKTOP_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

os.environ.setdefault("QUICKSTOCK_ENV", "development")
