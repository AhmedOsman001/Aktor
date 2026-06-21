"""Persist the user's theme choice (mode + accent) to disk.

Stored as a tiny JSON file under the app data dir so the look survives restarts.
Reads are defensive: any problem falls back to the built-in defaults.
"""

import json
import logging

from flowrecord.config import DATA_DIR

logger = logging.getLogger(__name__)

_PREFS_PATH = DATA_DIR / "theme.json"

DEFAULT_MODE = "dark"
DEFAULT_ACCENT = "#2563eb"


def load() -> dict:
    """Return {'mode': 'dark'|'light', 'accent': '#rrggbb'} with safe defaults."""
    try:
        with open(_PREFS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        mode = data.get("mode")
        accent = data.get("accent")
        return {
            "mode": mode if mode in ("dark", "light") else DEFAULT_MODE,
            "accent": accent if isinstance(accent, str) and accent else DEFAULT_ACCENT,
        }
    except FileNotFoundError:
        return {"mode": DEFAULT_MODE, "accent": DEFAULT_ACCENT}
    except Exception:
        logger.debug("Failed to read theme prefs; using defaults", exc_info=True)
        return {"mode": DEFAULT_MODE, "accent": DEFAULT_ACCENT}


def save(mode: str, accent: str) -> None:
    try:
        with open(_PREFS_PATH, "w", encoding="utf-8") as f:
            json.dump({"mode": mode, "accent": accent}, f, indent=2)
    except Exception:
        logger.debug("Failed to save theme prefs", exc_info=True)
