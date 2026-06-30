import ctypes
import logging
import re
import time
from ctypes import wintypes
from typing import Optional

import psutil
from pywinauto import Desktop
from pywinauto.uia_element_info import UIAElementInfo

logger = logging.getLogger(__name__)

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_NAME_W = 0.34
_TYPE_W = 0.06
_PROX_W = 0.15
_AUTOID_W = 0.30
_CLASS_W = 0.15
_PROX_MAX_PX = 600.0
_WEAK_NAME_THRESHOLD = 0.4

# Element types worth keeping even when nameless, when searching a window's
# tree for the element under a click point (e.g. Spotify, where from_point
# returns a D3D surface and the real controls live deeper in the tree).
_INTERACTIVE_TYPES = frozenset({
    "Button", "Edit", "TabItem", "ListItem", "MenuItem", "Hyperlink",
    "CheckBox", "RadioButton", "ComboBox", "Tab", "DataItem", "TreeItem",
    "ScrollBar", "Slider", "Spinner", "Document",
})


# ---------------------------------------------------------------------------
# Chromium / CEF accessibility wake
# ---------------------------------------------------------------------------
# Web-based apps (Spotify, Slack, Discord, VS Code, Electron, CEF) keep their
# UIAutomation tree dormant until an Assistive Technology signals interest. The
# reliable signal is the same one screen readers send: a WM_GETOBJECT message
# (with OBJID_CLIENT) directed at the window and its child renderer window.
# oleacc.AccessibleObjectFromPoint alone resolves at the OS level and often does
# NOT engage the renderer, so we combine both and re-query afterwards because
# the tree builds asynchronously.

_oleacc = ctypes.windll.oleacc
_user32 = ctypes.windll.user32

_WM_GETOBJECT = 0x003D
_OBJID_CLIENT = 0xFFFFFFFC  # OBJID_CLIENT
_SMTO_ABORTIFHUNG = 0x0002

_GA_ROOT = 2

_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_ulong),
]
_user32.SendMessageTimeoutW.restype = ctypes.c_long
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD

_EnumChildProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

try:
    from comtypes.automation import VARIANT as _VARIANT

    _oleacc.AccessibleObjectFromPoint.argtypes = [
        wintypes.POINT,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(_VARIANT),
    ]
    _oleacc.AccessibleObjectFromPoint.restype = ctypes.c_long
    _OLEACC_POINT_AVAILABLE = True
except Exception:
    _OLEACC_POINT_AVAILABLE = False
    logger.debug("comtypes unavailable — oleacc poke disabled", exc_info=True)

# Debounce per pid (seconds) so we re-attempt later if the first wake was too
# early, instead of permanently giving up like a plain set would.
_wake_last_attempt: dict = {}
_WAKE_DEBOUNCE_S = 0.8


def reset_a11y_cache() -> None:
    """Clear the per-process wake debounce map (e.g. on recording restart)."""
    _wake_last_attempt.clear()


def _poke_hwnd(hwnd) -> None:
    if not hwnd:
        return
    try:
        result = ctypes.c_ulong()
        _user32.SendMessageTimeoutW(
            hwnd, _WM_GETOBJECT, 0, _OBJID_CLIENT,
            _SMTO_ABORTIFHUNG, 200, ctypes.byref(result),
        )
    except Exception:
        pass


def _wake_chromium_a11y(pid, x: int, y: int) -> bool:
    """Send WM_GETOBJECT to the window under (x,y) and its children to force the
    host process to build its accessibility tree. Returns True if a poke was
    performed this call (debounced per pid)."""
    if pid is None:
        return False
    now = time.monotonic()
    if now - _wake_last_attempt.get(pid, 0.0) < _WAKE_DEBOUNCE_S:
        return False
    _wake_last_attempt[pid] = now

    hwnd = None
    try:
        hwnd = _user32.WindowFromPoint(wintypes.POINT(x, y))
    except Exception:
        hwnd = None
    if not hwnd:
        return False

    _poke_hwnd(hwnd)
    proc = _EnumChildProc(lambda h, _lp: (_poke_hwnd(h), True)[1])
    try:
        _user32.EnumChildWindows(hwnd, proc, 0)
    except Exception:
        pass

    # Secondary nudge via legacy MSAA — harmless and helps some hosts.
    if _OLEACC_POINT_AVAILABLE:
        try:
            pt = wintypes.POINT(x, y)
            acc = ctypes.c_void_p()
            child = _VARIANT()
            _oleacc.AccessibleObjectFromPoint(pt, ctypes.byref(acc), ctypes.byref(child))
        except Exception:
            pass

    logger.debug("Chromium a11y wake poked for pid=%s hwnd=%s at (%d,%d)", pid, hwnd, x, y)
    return True


def prime_window(hwnd) -> None:
    """Proactively wake a window's accessibility tree (poke it + its children).

    Call this when an app gains focus so a Chromium/CEF host has time to build
    its tree *before* the user clicks — otherwise the click races the build."""
    if not hwnd:
        return
    _poke_hwnd(hwnd)
    try:
        proc = _EnumChildProc(lambda h, _lp: (_poke_hwnd(h), True)[1])
        _user32.EnumChildWindows(hwnd, proc, 0)
    except Exception:
        pass


def _get_desktop(backend: str = "uia") -> Desktop:
    return Desktop(backend=backend)


def _resolve_name(obj) -> Optional[str]:
    if isinstance(obj, UIAElementInfo):
        return obj.name or None
    if hasattr(obj, "window_text"):
        try:
            return obj.window_text() or None
        except Exception:
            pass
    ei = getattr(obj, "element_info", None)
    if ei is not None:
        return getattr(ei, "name", None)
    return getattr(obj, "name", None)


def _resolve_control_type(obj) -> Optional[str]:
    if isinstance(obj, UIAElementInfo):
        return obj.control_type or None
    ei = getattr(obj, "element_info", None)
    if ei is not None:
        return getattr(ei, "control_type", None)
    return getattr(obj, "control_type", None)


def _resolve_automation_id(obj) -> Optional[str]:
    if isinstance(obj, UIAElementInfo):
        try:
            return obj.automation_id or None
        except Exception:
            return None
    ei = getattr(obj, "element_info", None)
    if ei is not None:
        try:
            return getattr(ei, "automation_id", None) or None
        except Exception:
            return None
    try:
        return getattr(obj, "automation_id", None) or None
    except Exception:
        return None


def _resolve_class_name(obj) -> Optional[str]:
    if isinstance(obj, UIAElementInfo):
        try:
            return obj.class_name or None
        except Exception:
            return None
    ei = getattr(obj, "element_info", None)
    if ei is not None:
        try:
            return getattr(ei, "class_name", None) or None
        except Exception:
            return None
    try:
        return getattr(obj, "class_name", None) or None
    except Exception:
        return None


def _resolve_pid(obj) -> Optional[int]:
    if isinstance(obj, UIAElementInfo):
        return obj.process_id
    ei = getattr(obj, "element_info", None)
    if ei is not None:
        return getattr(ei, "process_id", None)
    return getattr(obj, "process_id", None)


def _resolve_rect(obj):
    if isinstance(obj, UIAElementInfo):
        return obj.rectangle
    r = getattr(obj, "rectangle", None)
    if callable(r):
        return r()
    return r


def _deepest_at_point(root, x: int, y: int):
    """Walk down from ``root`` to the deepest child whose rectangle contains
    (x, y). Used when ``from_point`` returns a nameless container (common with
    Chromium/CEF hosts whose tree has built but whose hit-testing still resolves
    to the top web-view pane). Returns the original element if no deeper child
    contains the point."""
    best = root
    current = root
    for _ in range(64):
        try:
            children = current.children
            if callable(children):
                children = children()
        except Exception:
            break
        if not children:
            break
        deeper = None
        for child in children:
            try:
                rect = child.rectangle
            except Exception:
                continue
            if rect and rect.left <= x <= rect.right and rect.top <= y <= rect.bottom:
                deeper = child
                break
        if deeper is None:
            break
        best = deeper
        current = deeper
    return best


def _best_element_in_window(window_element, x: int, y: int):
    """Search a window's full descendant tree for the element covering (x, y).

    Used when ``from_point`` returns an opaque surface (e.g. Spotify's
    ``Intermediate D3D Window``) that sits on top of the real UIAutomation
    tree. Among descendants whose rectangle contains the point, prefer named
    ones, then the smallest area (most specific)."""
    if window_element is None:
        return None
    descendants = getattr(window_element, "descendants", None)
    if callable(descendants):
        descendants = descendants()
    if not descendants:
        return None

    best = None
    best_key = None
    for e in descendants:
        try:
            rect = e.rectangle
            if not (rect.left <= x <= rect.right and rect.top <= y <= rect.bottom):
                continue
            nm = (e.name or "").strip()
            ct = (e.control_type or "").strip()
            if not nm and ct not in _INTERACTIVE_TYPES:
                continue
            area = (rect.right - rect.left) * (rect.bottom - rect.top)
            key = (1 if nm else 0, -area)
            if best_key is None or key > best_key:
                best = e
                best_key = key
        except Exception:
            continue
    return best


def _children(node):
    try:
        ch = node.children
        if callable(ch):
            ch = ch()
        return ch or []
    except Exception:
        return []


def _contains(rect, x: int, y: int) -> bool:
    try:
        return rect is not None and rect.left <= x <= rect.right and rect.top <= y <= rect.bottom
    except Exception:
        return False


def _search_at_point(root, x: int, y: int, max_depth: int = 45):
    """Hit-test by descending only the rect-containing branches of ``root`` and
    returning the smallest named/interactive element covering (x, y).

    This is the reliable path for Chromium/CEF hosts: ``from_point`` resolves to
    the GPU surface (``Intermediate D3D Window``), but the real controls live in
    a sibling web subtree under the window. Following rect-containment from the
    window root reaches them, and pruning non-containing branches keeps it fast
    (vs. enumerating the entire descendant list)."""
    best = None
    best_area = None
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > max_depth:
            continue
        for ch in _children(node):
            try:
                rect = ch.rectangle
            except Exception:
                continue
            if not _contains(rect, x, y):
                continue
            try:
                nm = (ch.name or "").strip()
                ct = ch.control_type or ""
            except Exception:
                nm, ct = "", ""
            if nm or ct in _INTERACTIVE_TYPES:
                area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
                if best_area is None or area < best_area:
                    best, best_area = ch, area
            stack.append((ch, depth + 1))
    return best


def _top_window_element_at(x: int, y: int):
    """Return the UIA element for the top-level OS window under (x, y).

    More reliable than walking the UIA parent chain for Chromium/CEF hosts:
    that chain stops at a nameless intermediate pane (the GPU/web-view branch),
    whose subtree does NOT contain the page content. The real window — the
    common ancestor of both the GPU surface and the web tree — is the GA_ROOT
    of the HWND under the point."""
    try:
        hwnd = _user32.WindowFromPoint(wintypes.POINT(x, y))
        if not hwnd:
            return None
        root = _user32.GetAncestor(hwnd, _GA_ROOT) or hwnd
        return UIAElementInfo(int(root))
    except Exception:
        return None


def _find_window_element(element_info):
    window_element = None
    try:
        current = element_info
        for _ in range(50):
            ct = current.control_type
            if ct in ("Window", "Pane"):
                parent = current.parent
                if parent is None:
                    window_element = current
                    break
                parent_ct = parent.control_type
                if parent_ct == "Pane" and parent.name == "":
                    window_element = current
                    break
            parent = current.parent
            if parent is None:
                break
            current = parent
    except Exception:
        pass
    return window_element


def _looks_webview(*elements) -> bool:
    """Heuristic: does any element belong to a Chromium/CEF/Electron host?"""
    for el in elements:
        if el is None:
            continue
        try:
            cls = el.class_name or ""
        except Exception:
            cls = ""
        if any(h in cls for h in ("Chrome", "CEF", "Intermediate D3D", "Widget")):
            return True
    return False


# Control types that represent something the user actually clicks (vs. content).
_CLICKABLE_TYPES = frozenset({
    "Button", "MenuItem", "Hyperlink", "TabItem", "CheckBox", "RadioButton",
    "SplitButton", "ComboBox", "Link", "ListItem", "TreeItem",
})


def _rect_distance(rect, x: int, y: int) -> float:
    """0 if (x, y) is inside rect, else the straight-line distance to its edge."""
    try:
        dx = max(rect.left - x, 0, x - rect.right)
        dy = max(rect.top - y, 0, y - rect.bottom)
        return (dx * dx + dy * dy) ** 0.5
    except Exception:
        return 1e9


def _nearest_clickable(root, x: int, y: int, radius: int = 28):
    """Nearest clickable control whose rect is within ``radius`` px of (x, y).

    Recovers the real target when from_point falls through to the content behind
    a control — e.g. a modal 'OK' button that Chromium's a11y hit-test skips in
    favour of the table cell underneath. Uses a flat descendants() scan rather
    than a rect-pruned walk: intermediate web containers can have off rects that
    would prune the branch before the button is reached."""
    try:
        descendants = root.descendants()
    except Exception:
        return None

    best = None
    best_key = None
    for d in descendants:
        try:
            ct = d.control_type or ""
            if ct not in _CLICKABLE_TYPES:
                continue
            nm = (d.name or "").strip()
            if not nm:
                continue
            rect = d.rectangle
        except Exception:
            continue
        dist = _rect_distance(rect, x, y)
        if dist > radius:
            continue
        area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        key = (dist, area)  # nearest first, then most specific (smallest)
        if best_key is None or key < best_key:
            best, best_key = d, key
    return best


def _same_element(a, b) -> bool:
    try:
        ra, rb = a.rectangle, b.rectangle
        return (
            ra.left == rb.left and ra.top == rb.top
            and ra.right == rb.right and ra.bottom == rb.bottom
            and (_resolve_name(a) or "") == (_resolve_name(b) or "")
        )
    except Exception:
        return False


def _find_anchor(element_info, x: int, y: int, radius: int = 320) -> Optional[dict]:
    """A nearby element with a stable handle to anchor self-healing to.

    Scans the target's siblings (and its grandparent's children) — a small, fast
    set that usually holds the most stable nearby reference (a field's label, a
    section header, an adjacent button). Prefers an AutomationId, else a short
    Name. Returns ``{name, automation_id, control_type, rect:[l,t,r,b], dx, dy}``
    where (dx, dy) is the click offset from the anchor's *center*, so playback can
    re-derive the point (find_element returns element centers).
    """
    nodes = []
    try:
        parent = element_info.parent
    except Exception:
        parent = None
    if parent is not None:
        nodes.extend(_children(parent))
        try:
            gp = parent.parent
            if gp is not None:
                nodes.extend(_children(gp))
        except Exception:
            pass

    best = None
    best_score = None
    for c in nodes:
        try:
            if _same_element(c, element_info):
                continue
            aid = (_resolve_automation_id(c) or "").strip()
            nm = (_resolve_name(c) or "").strip()
            # A usable anchor needs a stable handle: an AutomationId, or a short
            # human Name (labels/buttons) — not large content blobs.
            if not aid and not (nm and len(nm) <= 40):
                continue
            rect = c.rectangle
            cx = (rect.left + rect.right) / 2
            cy = (rect.top + rect.bottom) / 2
            dist = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
            if dist > radius:
                continue
            # Bias toward AutomationId anchors (most stable).
            score = dist * (0.5 if aid else 1.0)
            if best_score is None or score < best_score:
                best_score = score
                best = {
                    "name": nm or None,
                    "automation_id": aid or None,
                    "control_type": (_resolve_control_type(c) or None),
                    "rect": [rect.left, rect.top, rect.right, rect.bottom],
                    "dx": int(x - cx),
                    "dy": int(y - cy),
                }
        except Exception:
            continue
    return best


def get_element_at(x: int, y: int) -> dict:
    result: dict = {
        "x": x,
        "y": y,
        "element_name": None,
        "element_type": None,
        "automation_id": None,
        "class_name": None,
        "parent_path": None,
        "app_name": None,
        "window_title": None,
        "window_rect": None,
        "x_relative": None,
        "y_relative": None,
        "element_rect": None,
        "anchor": None,
    }

    element_info = None
    try:
        element_info = UIAElementInfo.from_point(x, y)
    except Exception:
        logger.warning("UIAutomation failed at (%d, %d)", x, y)
        return result

    if element_info is None:
        return result

    try:
        leaf_name = (element_info.name or "").strip()
        leaf_type = element_info.control_type or ""
    except Exception:
        leaf_name, leaf_type = "", ""

    # Resolve the true top-level window via the HWND (GA_ROOT). For Chromium
    # hosts the UIA parent walk stops at a nameless intermediate pane, so prefer
    # the HWND root — it is the common ancestor of both the GPU surface and the
    # web content tree, and gives the correct title/rect for relative coords.
    window_element = _top_window_element_at(x, y) or _find_window_element(element_info)
    is_webview = _looks_webview(element_info, window_element)

    # Chromium/CEF: from_point lands on the GPU surface (Intermediate D3D
    # Window) with no name — or on a generic web container (Document/Group) —
    # while the real controls live deeper. In those cases we descend the window
    # tree to the actual control under (x, y). When from_point already returns a
    # specific named control we TRUST it: native hit-testing respects z-order
    # (so it picks the dialog button over the cell behind it), whereas the tree
    # descent only approximates it and can miss/over-pick.
    _GENERIC = ("Document", "Pane", "Custom", "Group", "View")
    if (not leaf_name) or (leaf_type in _GENERIC):
        try:
            pid = element_info.process_id
        except Exception:
            pid = None
        woke = _wake_chromium_a11y(pid, x, y) if not leaf_name else False

        hit = _search_at_point(window_element or element_info, x, y)

        # The tree builds asynchronously after a wake; for webview hosts retry
        # briefly so the first interaction still resolves a control.
        if hit is None and woke and is_webview:
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline and hit is None:
                time.sleep(0.15)
                hit = _search_at_point(window_element or element_info, x, y)

        # Deep fallback: exhaustively search the window's descendants.
        if hit is None and window_element is not None and is_webview:
            hit = _best_element_in_window(window_element, x, y)

        if hit is not None:
            element_info = hit
            if window_element is None:
                window_element = _find_window_element(hit)

    # Webview only: if we landed on a content element (not something the user
    # clicks) but an actual control sits right next to the click, prefer it.
    # Recovers controls whose accessible rect is smaller than their clickable
    # area (e.g. a modal 'Ok' button resolving to the table cell behind it).
    if is_webview and window_element is not None:
        try:
            chosen_ct = element_info.control_type or ""
        except Exception:
            chosen_ct = ""
        if chosen_ct not in _CLICKABLE_TYPES:
            near = _nearest_clickable(window_element, x, y)
            if near is not None:
                logger.debug("Recovered nearby clickable for (%d,%d) over %r", x, y, chosen_ct)
                element_info = near

    try:
        result["element_name"] = element_info.name or None
        result["element_type"] = element_info.control_type or None
        result["automation_id"] = element_info.automation_id or None
        result["class_name"] = element_info.class_name or None
    except Exception:
        pass

    # Element bounding rect — a strong, recoverable signal on its own and a
    # last-resort absolute target for self-healing.
    try:
        er = element_info.rectangle
        if er is not None:
            result["element_rect"] = (er.left, er.top, er.right, er.bottom)
    except Exception:
        pass

    # A nearby element with a stable handle (AutomationId / short Name) + the
    # click's offset from it — lets playback re-derive the point if the target
    # itself shifts but a stable neighbour is still findable.
    try:
        result["anchor"] = _find_anchor(element_info, x, y)
    except Exception:
        logger.debug("anchor capture failed at (%d,%d)", x, y, exc_info=True)

    if window_element is not None:
        try:
            result["window_title"] = window_element.name or None
        except Exception:
            pass

        try:
            rect = window_element.rectangle
            if rect is not None:
                result["window_rect"] = (rect.left, rect.top, rect.right, rect.bottom)
                win_w = rect.right - rect.left
                win_h = rect.bottom - rect.top
                if win_w > 0 and win_h > 0:
                    result["x_relative"] = round((x - rect.left) / win_w, 4)
                    result["y_relative"] = round((y - rect.top) / win_h, 4)
        except Exception:
            pass

        try:
            pid = window_element.process_id
            if pid:
                proc = psutil.Process(pid)
                result["app_name"] = proc.name()
        except Exception:
            pass

    result["parent_path"] = _build_parent_path(element_info)

    logger.debug(
        "get_element_at(%d,%d) -> el=%r type=%r autoid=%r class=%r app=%r win=%r rel=(%s,%s)",
        x, y, result["element_name"], result["element_type"],
        result["automation_id"], result["class_name"],
        result["app_name"], result["window_title"],
        result["x_relative"], result["y_relative"],
    )
    return result


def _build_parent_path(element_info) -> Optional[str]:
    types = []
    current = element_info
    first = True
    for _ in range(50):
        try:
            ct = current.control_type
        except Exception:
            ct = None
        if ct == "Window":
            break
        if ct and not first:
            types.append(ct)
        first = False
        try:
            parent = current.parent
        except Exception:
            break
        if parent is None:
            break
        current = parent
    if not types:
        return None
    return " > ".join(reversed(types))


def find_element(
    app_name: Optional[str],
    element_name: Optional[str],
    element_type: Optional[str],
    window_title: Optional[str] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
    automation_id: Optional[str] = None,
    class_name: Optional[str] = None,
    parent_path: Optional[str] = None,
) -> Optional[dict]:
    if not (element_name or automation_id or class_name):
        return None
    try:
        desktop = _get_desktop()

        def _is_strong(c) -> bool:
            return (
                c.get("autoid_score", 0.0) >= 0.95
                or c.get("class_score", 0.0) >= 0.95
                or c.get("name_score", 0.0) >= _WEAK_NAME_THRESHOLD
            )

        def _scan():
            try:
                windows = desktop.windows()
            except Exception:
                windows = []
            scoped = _scope_windows(windows, app_name, window_title)
            cands = _collect_candidates(
                scoped, element_name, element_type, x, y, automation_id, class_name
            )
            if not cands and app_name and window_title:
                title_scoped = [w for w in windows
                                if _title_contains(w, window_title) and _is_visible_window(w)]
                cands = _collect_candidates(
                    title_scoped, element_name, element_type, x, y, automation_id, class_name
                )
            return cands

        # Element matching is what makes replay resolution/position-independent —
        # it clicks the control wherever it now lives. Chromium/Electron build
        # their UIA tree lazily, so wake the target app and retry briefly while
        # the best match is still weak (mirrors get_element_at on the record side).
        pids = _pids_for_app(app_name) if app_name else []
        for pid in pids:
            _wake_chromium_a11y(pid, x or 0, y or 0)

        best = None
        deadline = time.monotonic() + (1.6 if pids else 0.0)
        while True:
            candidates = _scan()
            if candidates:
                cand = max(candidates, key=lambda c: c["score"])
                if best is None or cand["score"] > best["score"]:
                    best = cand
                if _is_strong(cand):
                    best = cand
                    break
            if not pids or time.monotonic() >= deadline:
                break
            for pid in pids:
                _wake_last_attempt.pop(pid, None)  # bypass the wake debounce on retry
                _wake_chromium_a11y(pid, x or 0, y or 0)
            time.sleep(0.25)

        if best is None:
            logger.debug(
                "find_element: no candidates el=%r type=%r autoid=%r class=%r app=%r win=%r",
                element_name, element_type, automation_id, class_name, app_name, window_title,
            )
            return None

        if not _is_strong(best):
            # Only a weak/ambiguous match after retrying — returning it would
            # click the wrong element, so fall back to the recorded coordinates
            # (relative/anchor/raw) in the player.
            logger.warning(
                "find_element: weak match el=%r -> %r (name=%.2f autoid=%.2f class=%.2f total=%.3f) "
                "near (%s,%s) — falling back to coordinates",
                element_name, best["name"], best["name_score"],
                best.get("autoid_score", 0.0), best.get("class_score", 0.0),
                best["score"], x, y,
            )
            return None

        logger.debug(
            "find_element: matched el=%r -> %r (name=%.2f type=%.2f autoid=%.2f class=%.2f prox=%.2f total=%.3f)",
            element_name, best["name"], best["name_score"], best["type_score"],
            best.get("autoid_score", 0.0), best.get("class_score", 0.0),
            best["prox_score"], best["score"],
        )
        return _element_result(best["element"], best["window"])

    except Exception:
        logger.exception("find_element failed")
        return None


def _window_belongs(hwnd, app_name: Optional[str]) -> bool:
    """True if the given top-level window belongs to ``app_name``'s process."""
    if not hwnd or not app_name:
        return False
    try:
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False
        return _exe_matches(psutil.Process(pid.value).name(), app_name)
    except Exception:
        return False


def focus_window(app_name: Optional[str], window_title: Optional[str]) -> bool:
    """Bring the target app's window to the foreground before we act on it, so a
    click/drag can never land on whatever window happens to be on top.

    Identifies the window by the same captured info we match elements with
    (process name + window title). Returns True only if it actually switched
    focus (so the caller can wait for the window to come forward); returns False
    if the right window is already foreground or none was found.
    """
    if not (app_name or window_title):
        return False
    try:
        fg = _user32.GetForegroundWindow()
    except Exception:
        fg = None
    if fg and app_name and _window_belongs(fg, app_name):
        return False  # already on the intended app — nothing to do

    try:
        windows = _get_desktop().windows()
    except Exception:
        return False
    scoped = _scope_windows(windows, app_name, window_title)
    for w in scoped:
        try:
            if not _is_visible_window(w):
                continue
            w.set_focus()  # pywinauto restores + foregrounds (handles the focus dance)
            logger.debug("focus_window: focused %r (app=%s)", _resolve_name(w), app_name)
            return True
        except Exception:
            continue
    logger.debug("focus_window: no window for app=%s title=%r", app_name, window_title)
    return False


def _pids_for_app(app_name: str) -> list:
    target = _strip_exe(app_name)
    pids = []
    try:
        for proc in psutil.process_iter(["name", "pid"]):
            name = proc.info.get("name")
            if name and _strip_exe(name) == target:
                pids.append(proc.info["pid"])
    except Exception:
        pass
    return pids


def _strip_exe(name: str) -> str:
    n = (name or "").lower().strip()
    return n[:-4] if n.endswith(".exe") else n


def _exe_matches(live_name: str, target: str) -> bool:
    return _strip_exe(live_name) == _strip_exe(target)


def _title_contains(w, title: str) -> bool:
    wname = _resolve_name(w)
    return bool(wname) and title.lower() in wname.lower()


def _is_visible_window(w) -> bool:
    try:
        return bool(w.is_visible())
    except Exception:
        return True


def _scope_windows(windows, app_name: Optional[str], window_title: Optional[str]) -> list:
    if app_name:
        target = []
        for w in windows:
            try:
                pid = _resolve_pid(w)
                if not pid:
                    continue
                proc = psutil.Process(pid)
                if _exe_matches(proc.name(), app_name):
                    target.append(w)
            except Exception:
                continue
        logger.debug(
            "find_element: app=%s -> %d/%d windows (title filter=%r)",
            app_name, len(target), len(windows), window_title,
        )
        if window_title:
            tm = [w for w in target if _title_contains(w, window_title)]
            if tm:
                target = tm
        return target
    if window_title:
        return [w for w in windows if _title_contains(w, window_title)]
    return list(windows)


def _collect_candidates(
    windows,
    element_name,
    element_type,
    x,
    y,
    automation_id=None,
    class_name=None,
) -> list:
    out = []
    for win in windows:
        try:
            descendants = win.descendants()
        except Exception:
            continue
        for d in descendants:
            live_name = _resolve_name(d) or ""
            name_score = _name_similarity(element_name, live_name) if element_name else 0.0
            autoid_score = (
                _autoid_score(automation_id, _resolve_automation_id(d)) if automation_id else 0.0
            )
            class_score = (
                _class_score(class_name, _resolve_class_name(d)) if class_name else 0.0
            )

            if name_score <= 0 and autoid_score <= 0 and class_score <= 0:
                continue

            live_type = _resolve_control_type(d) or ""
            type_score = _type_similarity(element_type, live_type)
            prox_score = _proximity_score(_resolve_rect(d), x, y)

            weights = {"name": _NAME_W, "type": _TYPE_W, "prox": _PROX_W}
            scores = {"name": name_score, "type": type_score, "prox": prox_score}
            if automation_id:
                weights["autoid"] = _AUTOID_W
                scores["autoid"] = autoid_score
            if class_name:
                weights["class"] = _CLASS_W
                scores["class"] = class_score
            total_w = sum(weights.values())
            score = sum(scores[k] * weights[k] for k in scores) / total_w

            out.append({
                "element": d, "window": win, "name": live_name,
                "name_score": name_score, "type_score": type_score,
                "prox_score": prox_score, "autoid_score": autoid_score,
                "class_score": class_score, "score": score,
            })
    return out


def _tokens(s: str) -> list:
    return [t for t in _TOKEN_SPLIT.split((s or "").lower()) if t]


def _is_subsequence(small: list, big: list) -> bool:
    n, m = len(small), len(big)
    if n == 0 or n > m:
        return False
    for start in range(m - n + 1):
        if big[start:start + n] == small:
            return True
    return False


def _name_similarity(stored: Optional[str], live: Optional[str]) -> float:
    """0 = unrelated; up to 1.0 = exact. Token-aligned to avoid 'ok' matching 'booking'."""
    if not stored:
        return 0.0
    s = stored.lower().strip()
    l = (live or "").lower().strip()
    if not l:
        return 0.0
    if s == l:
        return 1.0

    st = _tokens(s)
    lt = _tokens(l)
    if not st or not lt:
        return 0.0

    if _is_subsequence(st, lt) or _is_subsequence(lt, st):
        ratio = min(len(st), len(lt)) / max(len(st), len(lt))
        return 0.6 + 0.3 * ratio

    overlap = len(set(st) & set(lt))
    if overlap == 0:
        return 0.0
    return 0.4 * (overlap / len(st))


def _type_similarity(stored_type: Optional[str], live_type: Optional[str]) -> float:
    if not stored_type:
        return 0.5
    if not live_type:
        return 0.0
    s = stored_type.lower()
    l = live_type.lower()
    if s == l:
        return 1.0
    if s in l or l in s:
        return 0.7
    return 0.0


def _autoid_score(stored: Optional[str], live: Optional[str]) -> float:
    if not stored:
        return 0.0
    s = stored.strip()
    l = (live or "").strip()
    if not l:
        return 0.0
    if s == l:
        return 1.0
    if s.lower() == l.lower():
        return 0.95
    if s.lower() in l.lower() or l.lower() in s.lower():
        return 0.6
    return 0.0


def _class_score(stored: Optional[str], live: Optional[str]) -> float:
    if not stored:
        return 0.0
    s = stored.strip()
    l = (live or "").strip()
    if not l:
        return 0.0
    if s == l:
        return 1.0
    if s.lower() == l.lower():
        return 0.95
    if s.lower() in l.lower():
        return 0.7
    return 0.0


def _proximity_score(rect, x: Optional[int], y: Optional[int]) -> float:
    if rect is None or x is None or y is None:
        return 0.5
    try:
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
    except Exception:
        return 0.5
    dist = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
    return max(0.0, 1.0 - dist / _PROX_MAX_PX)


def _element_result(element, window) -> dict:
    rect = _resolve_rect(element)
    if rect is not None:
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
    else:
        cx, cy = None, None

    win_rect = None
    try:
        wr = _resolve_rect(window)
        if wr:
            win_rect = (wr.left, wr.top, wr.right, wr.bottom)
    except Exception:
        pass

    return {
        "x": cx,
        "y": cy,
        "element_name": _resolve_name(element),
        "element_type": _resolve_control_type(element),
        "automation_id": _resolve_automation_id(element),
        "class_name": _resolve_class_name(element),
        "window_title": _resolve_name(window),
        "window_rect": win_rect,
    }
