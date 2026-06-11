import logging
from typing import Optional

import psutil
from pywinauto import Desktop
from pywinauto.uia_element_info import UIAElementInfo

logger = logging.getLogger(__name__)


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


def get_element_at(x: int, y: int) -> dict:
    result: dict = {
        "x": x,
        "y": y,
        "element_name": None,
        "element_type": None,
        "app_name": None,
        "window_title": None,
        "window_rect": None,
        "x_relative": None,
        "y_relative": None,
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
        result["element_name"] = element_info.name or None
        result["element_type"] = element_info.control_type or None
    except Exception:
        pass

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

    return result


def find_element(
    app_name: Optional[str],
    element_name: Optional[str],
    element_type: Optional[str],
    window_title: Optional[str] = None,
) -> Optional[dict]:
    try:
        desktop = _get_desktop()

        if app_name:
            windows = []
            try:
                windows = desktop.windows()
            except Exception:
                pass

            target_windows = []
            for w in windows:
                try:
                    pid = _resolve_pid(w)
                    if pid:
                        proc = psutil.Process(pid)
                        if proc.name().lower() == app_name.lower():
                            target_windows.append(w)
                except Exception:
                    continue

            if window_title:
                title_matches = [
                    w for w in target_windows
                    if _resolve_name(w) and window_title.lower() in (_resolve_name(w) or "").lower()
                ]
                if title_matches:
                    target_windows = title_matches

            for win in target_windows:
                match = _search_descendants(win, element_name, element_type)
                if match:
                    return _element_result(match, win)

        if window_title and not app_name:
            windows = []
            try:
                windows = desktop.windows()
            except Exception:
                pass
            for w in windows:
                wname = _resolve_name(w)
                if wname and window_title.lower() in wname.lower():
                    match = _search_descendants(w, element_name, element_type)
                    if match:
                        return _element_result(match, w)

        return None

    except Exception:
        logger.exception("find_element failed")
        return None


def _search_descendants(root, element_name: Optional[str], element_type: Optional[str]):
    try:
        descendants = root.descendants()
    except Exception:
        return None

    candidates = []
    for d in descendants:
        d_name = _resolve_name(d) or ""
        d_type = _resolve_control_type(d) or ""

        name_match = not element_name or element_name.lower() in d_name.lower()
        type_match = not element_type or element_type.lower() == d_type.lower()

        if name_match and type_match:
            candidates.append(d)

    return candidates[0] if candidates else None


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
        "window_title": _resolve_name(window),
        "window_rect": win_rect,
    }
