"""Windows 11 window effects via DWM (no extra dependencies).

All calls are best-effort: every function is wrapped so that an unsupported
Windows build, a missing attribute, or any COM hiccup degrades silently to a
plain window. Uses the same ``ctypes.windll`` approach as the DPI/OLE init in
``main.py``.
"""

import ctypes
import logging
from ctypes import wintypes

logger = logging.getLogger(__name__)

# Master switch — flip to False to disable all native effects app-wide.
ENABLED = True

# DwmSetWindowAttribute attribute ids
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19  # builds 18985..19041
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_SYSTEMBACKDROP_TYPE = 38

# Corner preferences
_DWMWCP_ROUND = 2

# Backdrop types
BACKDROP_NONE = 1
BACKDROP_MICA = 2
BACKDROP_ACRYLIC = 3
BACKDROP_TABBED = 4


def _dwm_set_int(hwnd: int, attr: int, value: int) -> bool:
    try:
        val = ctypes.c_int(value)
        res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), ctypes.c_uint(attr), ctypes.byref(val), ctypes.sizeof(val)
        )
        return res == 0
    except Exception:
        return False


def set_dark_titlebar(hwnd: int, dark: bool) -> None:
    """Match the native title bar to the app theme."""
    if not _dwm_set_int(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0):
        _dwm_set_int(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, 1 if dark else 0)


def set_backdrop(hwnd: int, kind: int) -> None:
    _dwm_set_int(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, kind)


def round_corners(hwnd: int) -> None:
    _dwm_set_int(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)


def apply_window_chrome(widget, dark: bool, backdrop: int = BACKDROP_NONE) -> None:
    """Apply theme-matched title bar (+ optional backdrop, rounded corners) to a
    top-level Qt widget. Safe no-op on failure or when disabled."""
    if not ENABLED:
        return
    try:
        hwnd = int(widget.winId())
    except Exception:
        return
    if not hwnd:
        return
    try:
        set_dark_titlebar(hwnd, dark)
        round_corners(hwnd)
        if backdrop != BACKDROP_NONE:
            set_backdrop(hwnd, backdrop)
    except Exception:
        logger.debug("apply_window_chrome failed", exc_info=True)
