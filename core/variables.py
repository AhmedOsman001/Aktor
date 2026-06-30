"""Workflow variables — `{{name}}` placeholders resolved at playback.

A ``type_text`` step's text can contain ``{{variable}}`` tokens. At run time the
player substitutes values from a *context*: prompted values, a CSV/Excel row, or
built-ins (date/time/clipboard/row). This is the foundation for the CSV/Excel
batch feature (run a workflow once per spreadsheet row).

The variables a workflow uses are *discovered* from its step text — there's no
separate definition to keep in sync. Built-ins resolve automatically and are
never prompted for or mapped to a column.
"""

import ctypes
import logging
import re
from ctypes import wintypes
from datetime import datetime
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# {{ name }} — name starts with a letter/underscore; spaces allowed inside.
_TOKEN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_ ]*?)\s*\}\}")

# Resolved automatically — excluded from prompts / column mapping.
BUILTIN_NAMES = frozenset({"date", "time", "datetime", "clipboard", "row", "count"})


def extract_variables(steps: Iterable) -> list[str]:
    """Return the ordered, de-duplicated user variables referenced by the steps
    (built-ins excluded).

    Primary source is an explicit binding on the step (``step.variable``); inline
    ``{{tokens}}`` in step text are also collected for power users / built-ins.
    """
    found: list[str] = []

    def _add(name: Optional[str]) -> None:
        if name and name not in BUILTIN_NAMES and name not in found:
            found.append(name)

    for step in steps:
        _add(getattr(step, "variable", None))
        text = getattr(step, "text", None)
        if text:
            for m in _TOKEN.finditer(text):
                _add(m.group(1).strip())
    return found


def variable_samples(steps: Iterable) -> dict:
    """Map each bound variable to the recorded sample value of its step, so the
    run/batch dialogs can show 'name — e.g. "Ada Lovelace"'."""
    samples: dict = {}
    for step in steps:
        v = getattr(step, "variable", None)
        if v and v not in samples:
            samples[v] = getattr(step, "text", "") or ""
    return samples


def has_variables(steps: Iterable) -> bool:
    return bool(extract_variables(steps))


def resolve(template: Optional[str], context: dict) -> str:
    """Substitute ``{{var}}`` tokens in ``template`` from ``context``.

    Unknown tokens resolve to an empty string (and are logged), so a missing
    value never leaves a literal ``{{x}}`` in the typed output.
    """
    if not template:
        return template or ""

    def _sub(m: "re.Match") -> str:
        name = m.group(1).strip()
        if name in context and context[name] is not None:
            return str(context[name])
        logger.debug("variable %r has no value — substituting empty", name)
        return ""

    return _TOKEN.sub(_sub, template)


def build_context(
    values: Optional[dict] = None,
    *,
    row_index: Optional[int] = None,
    total: Optional[int] = None,
) -> dict:
    """Merge user-supplied values with built-ins into a resolution context.

    User values win over same-named built-ins (e.g. a CSV column called ``date``).
    ``row``/``count`` are set for batch runs.
    """
    now = datetime.now()
    ctx: dict = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "datetime": now.strftime("%Y-%m-%d %H:%M"),
        "clipboard": _read_clipboard(),
    }
    if values:
        ctx.update({k: ("" if v is None else v) for k, v in values.items()})
    if row_index is not None:
        ctx["row"] = row_index
    if total is not None:
        ctx["count"] = total
    return ctx


# ---------------------------------------------------------------------------
# Clipboard (Win32, thread-safe — the player resolves on its worker thread)
# ---------------------------------------------------------------------------
_CF_UNICODETEXT = 13
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# Declare full signatures — without these, ctypes assumes 32-bit int returns and
# truncates 64-bit handles/pointers, which crashes when dereferenced.
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.CloseClipboard.restype = wintypes.BOOL
_kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
_kernel32.GlobalUnlock.restype = wintypes.BOOL


def _read_clipboard() -> str:
    try:
        if not _user32.OpenClipboard(None):
            return ""
        try:
            handle = _user32.GetClipboardData(_CF_UNICODETEXT)
            if not handle:
                return ""
            ptr = _kernel32.GlobalLock(handle)
            if not ptr:
                return ""
            try:
                return ctypes.c_wchar_p(ptr).value or ""
            finally:
                _kernel32.GlobalUnlock(handle)
        finally:
            _user32.CloseClipboard()
    except Exception:
        logger.debug("clipboard read failed", exc_info=True)
        return ""
