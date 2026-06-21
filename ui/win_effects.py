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


# Class-style drop shadow — the subtle shadow Windows draws for menus/popups.
# This is a safe, single SetClassLong call (no message handling), so it gives a
# minimal native shadow to frameless windows without the risky WS_THICKFRAME /
# WM_NCCALCSIZE approach.
_GCL_STYLE = -26
_CS_DROPSHADOW = 0x00020000


def enable_drop_shadow(hwnd: int) -> None:
    try:
        u = ctypes.windll.user32
        getf = getattr(u, "GetClassLongPtrW", None) or u.GetClassLongW
        setf = getattr(u, "SetClassLongPtrW", None) or u.SetClassLongW
        cur = getf(hwnd, _GCL_STYLE)
        if not (cur & _CS_DROPSHADOW):
            setf(hwnd, _GCL_STYLE, cur | _CS_DROPSHADOW)
    except Exception:
        logger.debug("enable_drop_shadow failed", exc_info=True)


def apply_window_chrome(widget, dark: bool, backdrop: int = BACKDROP_NONE) -> None:
    """Apply theme-matched title bar, rounded corners, and a minimal native drop
    shadow to a top-level Qt widget. Safe no-op on failure or when disabled."""
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
        enable_drop_shadow(hwnd)
        if backdrop != BACKDROP_NONE:
            set_backdrop(hwnd, backdrop)
    except Exception:
        logger.debug("apply_window_chrome failed", exc_info=True)
