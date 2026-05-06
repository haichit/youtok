from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parent
STATIC_DIR = str(_PKG_ROOT / "static")
TEMPLATES_DIR = str(_PKG_ROOT / "templates")
