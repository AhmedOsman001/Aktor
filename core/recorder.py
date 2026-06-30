import ctypes
import json
import logging
import os
import queue
import threading
import time
from ctypes import wintypes
from typing import Callable, Optional

import psutil
from pynput import keyboard, mouse

from flowrecord.config import (
    APP_NAME, APP_POLL_INTERVAL_MS, DELAY_THRESHOLD,
    MAX_DELAY_SECONDS, SYSTEM_PROCESSES,
)
from flowrecord.core.element_detector import get_element_at, prime_window, reset_a11y_cache
from flowrecord.models import ActionStep

logger = logging.getLogger(__name__)

_OUR_PID = os.getpid()

# Mouse-movement sampling (only when "Capture mouse movement" is on): at most one
# move step per interval, and only after the cursor has travelled a few pixels.
_MOVE_INTERVAL_S = 0.045
_MOVE_MIN_DIST = 6

# A press→release that travels more than this (px) is a drag, not a click.
_DRAG_THRESHOLD = 12

_user32 = ctypes.windll.user32
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.WindowFromPoint.restype = wintypes.HWND


def _window_pid_at(x: int, y: int) -> Optional[int]:
    """PID owning the window under (x, y). Fast (no UIA) — used to filter clicks
    on FlowRecord's own windows without an expensive element lookup."""
    try:
        hwnd = _user32.WindowFromPoint(wintypes.POINT(x, y))
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value or None
    except Exception:
        return None


def _foreground_app_info() -> Optional[tuple[int, str]]:
    """Return (pid, window_title) of the current foreground window, or None."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    length = _user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return pid.value, (buf.value or "")


def _cursor_pos() -> Optional[tuple[int, int]]:
    """Physical screen position of the cursor, or None on failure."""
    try:
        pt = wintypes.POINT()
        if _user32.GetCursorPos(ctypes.byref(pt)):
            return pt.x, pt.y
    except Exception:
        pass
    return None

_MODIFIER_KEYS = {
    keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r,
    keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r,
    keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r,
    keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r,
}

_NORMAL_CHAR_KEYS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    " `-=[]\\;',./"
)

# Double-click thresholds from the OS (time window + max travel between clicks).
_DBL_CLICK_S = (_user32.GetDoubleClickTime() or 500) / 1000.0
_DBL_DX = _user32.GetSystemMetrics(36) or 4  # SM_CXDOUBLECLK
_DBL_DY = _user32.GetSystemMetrics(37) or 4  # SM_CYDOUBLECLK


class Recorder:
    def __init__(self, on_step_added: Optional[Callable[[int], None]] = None):
        self._steps: list[ActionStep] = []
        self._recording = False
        self._paused = False
        self._last_action_time: float = 0.0
        self._start_time: float = 0.0
        self._mouse_listener: Optional[mouse.Listener] = None
        self._keyboard_listener: Optional[keyboard.Listener] = None
        self._app_poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._held_modifiers: set = set()
        self._text_buffer: str = ""
        self._text_buffer_start: float = 0.0
        self._on_step_added = on_step_added
        self._last_fg_exe: Optional[str] = None
        self._lock = threading.Lock()
        # Mouse-movement capture (cursor path between actions) — opt-in.
        self.capture_moves = False
        self._last_move_time = 0.0
        self._last_move_pos: Optional[tuple] = None
        # Drag detection: remember the press so the release can decide click-vs-drag.
        self._press_info: Optional[tuple] = None  # (x, y, button, step)
        self._button_down = False
        # Double-click detection state.
        self._last_click: Optional[tuple] = None  # (time, x, y, button)
        self._last_click_step: Optional[ActionStep] = None
        # Off-thread element resolution so the input hook stays responsive.
        self._resolve_queue: "queue.Queue" = queue.Queue()
        self._resolver_thread: Optional[threading.Thread] = None

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def step_count(self) -> int:
        return len(self._steps)

    def start(self) -> None:
        if self._recording:
            return
        self._steps.clear()
        self._recording = True
        self._paused = False
        self._held_modifiers.clear()
        self._text_buffer = ""
        self._last_click = None
        self._last_click_step = None
        self._start_time = time.monotonic()
        self._last_action_time = self._start_time
        self._stop_event.clear()
        reset_a11y_cache()

        self._resolve_queue = queue.Queue()
        self._resolver_thread = threading.Thread(target=self._resolve_worker, daemon=True)
        self._resolver_thread.start()

        self._last_fg_exe = self._current_foreground_exe()
        # Wake the focused app's accessibility tree up front (Chromium/CEF apps
        # build it lazily) so the first clicks resolve to named elements.
        try:
            prime_window(_user32.GetForegroundWindow())
        except Exception:
            pass

        mouse_kwargs = {"on_click": self._on_click, "on_scroll": self._on_scroll}
        if self.capture_moves:
            self._last_move_time = 0.0
            self._last_move_pos = None
            mouse_kwargs["on_move"] = self._on_move
        self._mouse_listener = mouse.Listener(**mouse_kwargs)
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._mouse_listener.start()
        self._keyboard_listener.start()

        self._app_poll_thread = threading.Thread(
            target=self._poll_foreground_apps, daemon=True
        )
        self._app_poll_thread.start()

        logger.info("Recording started")
        logger.debug("Foreground at start: %s", self._last_fg_exe)

    def stop(self) -> list[ActionStep]:
        if not self._recording:
            return []

        self._recording = False
        self._stop_event.set()

        self._flush_text_buffer()

        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._keyboard_listener:
            self._keyboard_listener.stop()
        if self._app_poll_thread:
            self._app_poll_thread.join(timeout=2.0)

        # Let the resolver finish element lookups for the final clicks.
        if self._resolver_thread:
            self._resolve_queue.put(None)
            self._resolver_thread.join(timeout=8.0)

        self._mouse_listener = None
        self._keyboard_listener = None
        self._app_poll_thread = None
        self._resolver_thread = None

        steps = list(self._steps)
        logger.info("Recording stopped — %d steps captured", len(steps))
        return steps

    def pause(self) -> None:
        if self._recording and not self._paused:
            self._paused = True
            self._flush_text_buffer()
            logger.info("Recording paused")

    def resume(self) -> None:
        if self._recording and self._paused:
            self._paused = False
            self._last_action_time = time.monotonic()
            logger.info("Recording resumed")

    def _current_foreground_exe(self) -> Optional[str]:
        info = _foreground_app_info()
        if not info:
            return None
        pid, _title = info
        try:
            return psutil.Process(pid).name().lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    def _poll_foreground_apps(self) -> None:
        interval = APP_POLL_INTERVAL_MS / 1000.0
        while not self._stop_event.is_set():
            self._stop_event.wait(interval)
            if self._stop_event.is_set() or self._paused:
                continue

            info = _foreground_app_info()
            if not info:
                continue
            pid, title = info

            try:
                exe = psutil.Process(pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

            exe_l = exe.lower()
            if exe_l == self._last_fg_exe:
                continue
            self._last_fg_exe = exe_l
            logger.debug("Foreground changed -> pid=%d exe=%s title=%r", pid, exe, title)

            if exe_l in SYSTEM_PROCESSES:
                logger.debug("Skipping system process: %s", exe)
                continue
            if exe_l in ("python.exe", "pythonw.exe", "py.exe"):
                if APP_NAME.lower() in title.lower() or "flowrecord" in title.lower():
                    logger.debug("Ignoring FlowRecord foreground: %s", exe)
                    continue

            # Proactively wake the newly focused app's accessibility tree so a
            # Chromium/CEF host has it built before the user clicks.
            try:
                prime_window(_user32.GetForegroundWindow())
            except Exception:
                pass

            self._flush_text_buffer()
            self._add_delay()

            step = ActionStep(
                type="launch_app",
                app_name=exe,
                window_title=title or None,
                description=f"Activated {exe}",
            )
            with self._lock:
                self._steps.append(step)
            self._notify_step_added()

    def _add_delay(self) -> None:
        now = time.monotonic()
        delta = now - self._last_action_time
        if delta > DELAY_THRESHOLD and self._steps:
            capped = min(delta, MAX_DELAY_SECONDS)
            self._steps[-1].delay_after = round(capped, 3)
            logger.debug("delay_after=%.3fs appended to step %d", capped, len(self._steps) - 1)
        self._last_action_time = now

    def _flush_text_buffer(self) -> None:
        if not self._text_buffer:
            return
        with self._lock:
            text = self._text_buffer
            self._text_buffer = ""
            # If the previous step is also typed text (a continuous typing chain),
            # append to it so it's one action with no split / no in-between delay.
            prev = self._steps[-1] if self._steps else None
            if prev is not None and prev.type == "type_text" and prev.variable is None:
                prev.text = (prev.text or "") + text
                prev.description = f"Type '{prev.text}'"
                merged = True
            else:
                self._steps.append(ActionStep(
                    type="type_text", text=text, description=f"Type '{text}'"))
                merged = False
        logger.debug("Typed text %s: %r (%d chars)",
                     "merged" if merged else "flushed", text, len(text))
        self._notify_step_added()

    def _notify_step_added(self) -> None:
        if self._on_step_added:
            try:
                self._on_step_added(len(self._steps))
            except Exception:
                pass

    def _on_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        # Runs on the OS input hook thread — must return fast. The press records
        # a click step; the release decides whether it was actually a drag.
        if not self._recording or self._paused:
            return
        if pressed:
            self._handle_press(x, y, button)
        else:
            self._handle_release(x, y, button)

    def _handle_press(self, x: int, y: int, button: mouse.Button) -> None:
        # Fast self-filter: ignore clicks on FlowRecord's own windows (no UIA).
        if _window_pid_at(x, y) == _OUR_PID:
            logger.debug("Ignoring click on FlowRecord UI at (%d,%d)", x, y)
            return

        self._button_down = True
        now = time.monotonic()
        # Double-click: a second press of the same button, near the same spot,
        # within the OS double-click window -> promote the prior click step.
        if self._is_double_click(now, x, y, button):
            self._promote_to_double_click(x, y)
            self._last_click = None  # don't chain a 3rd press into the pair
            self._press_info = None
            return

        if button == mouse.Button.right:
            click_type, verb = "right_click", "Right-click"
        elif button == mouse.Button.middle:
            click_type, verb = "middle_click", "Middle-click"
        else:
            click_type, verb = "click", "Click"

        self._flush_text_buffer()
        self._add_delay()

        step = ActionStep(
            type=click_type,
            x=x,
            y=y,
            description=f"{verb} at ({x}, {y})",
        )
        with self._lock:
            self._steps.append(step)
        self._last_click = (now, x, y, button)
        self._last_click_step = step
        # Remember the press so a far-away release can turn this into a drag.
        self._press_info = (x, y, button, step)
        self._notify_step_added()

        # Resolve the UI element off the hook thread and fill the step in.
        self._resolve_queue.put((step, x, y, verb))

    def _handle_release(self, x: int, y: int, button: mouse.Button) -> None:
        self._button_down = False
        pi = self._press_info
        self._press_info = None
        if pi is None:
            return
        px, py, pbtn, step = pi
        # Only left-button press→release that travelled far enough becomes a drag.
        if button != pbtn or button != mouse.Button.left or step is None:
            return
        if abs(x - px) < _DRAG_THRESHOLD and abs(y - py) < _DRAG_THRESHOLD:
            return  # barely moved -> it was a normal click

        with self._lock:
            if step.type in ("click", "double_click"):
                step.type = "drag"
                step.x2 = x
                step.y2 = y
                target = f" on '{step.element_name}'" if step.element_name else ""
                step.description = f"Drag from ({px}, {py}) to ({x}, {y}){target}"
        self._last_click = None  # a drag shouldn't chain into a double-click
        logger.debug("DRAG (%d,%d) -> (%d,%d)", px, py, x, y)
        self._notify_step_added()

    def _resolve_worker(self) -> None:
        """Drain queued clicks and fill in their UI element details (the slow,
        UIA-heavy part) without blocking the input hook."""
        while True:
            try:
                item = self._resolve_queue.get(timeout=0.2)
            except queue.Empty:
                if self._stop_event.is_set():
                    return
                continue
            if item is None:
                return
            step, x, y, verb = item
            try:
                elem = get_element_at(x, y)
                step.app_name = elem.get("app_name")
                step.window_title = elem.get("window_title")
                step.element_name = elem.get("element_name")
                step.element_type = elem.get("element_type")
                step.automation_id = elem.get("automation_id")
                step.class_name = elem.get("class_name")
                step.parent_path = elem.get("parent_path")
                step.x_relative = elem.get("x_relative")
                step.y_relative = elem.get("y_relative")

                # Rich self-heal signals: element bounding rect + a nearby stable
                # anchor with the click's offset from it.
                er = elem.get("element_rect")
                if er:
                    step.element_rect = ",".join(str(int(v)) for v in er)
                anchor = elem.get("anchor")
                if anchor:
                    try:
                        step.anchor = json.dumps(anchor, separators=(",", ":"))
                    except Exception:
                        pass

                name = elem.get("element_name")
                if name:
                    # Don't clobber a drag's description if the release already
                    # promoted it; otherwise add the element name to the verb.
                    if step.type == "drag":
                        step.description = (
                            f"Drag '{name}' to ({step.x2}, {step.y2})"
                            if step.x2 is not None else f"Drag '{name}'"
                        )
                    else:
                        step.description = f"{verb} at ({x}, {y}) on '{name}'"
                logger.debug(
                    "CLICK (%d,%d) element=%r type=%r app=%r window=%r rel=(%s,%s)",
                    x, y, elem.get("element_name"), elem.get("element_type"),
                    elem.get("app_name"), elem.get("window_title"),
                    elem.get("x_relative"), elem.get("y_relative"),
                )
            except Exception:
                logger.exception("Element resolution failed for click at (%d,%d)", x, y)

    def _is_double_click(self, now: float, x: int, y: int, button) -> bool:
        # Only the left button forms a double_click; right/middle stay separate.
        if button != mouse.Button.left:
            return False
        if not self._last_click or self._last_click_step is None:
            return False
        lt, lx, ly, lb = self._last_click
        return (
            lb == mouse.Button.left
            and (now - lt) <= _DBL_CLICK_S
            and abs(x - lx) <= _DBL_DX
            and abs(y - ly) <= _DBL_DY
            and self._last_click_step.type in ("click", "double_click")
        )

    def _promote_to_double_click(self, x: int, y: int) -> None:
        step = self._last_click_step
        if step is None:
            return
        step.type = "double_click"
        target = f" on '{step.element_name}'" if step.element_name else ""
        step.description = f"Double-click at ({step.x}, {step.y}){target}"
        logger.debug("Promoted click to double_click at (%d,%d)%s", step.x, step.y, target)
        self._notify_step_added()

    def _on_move(self, x: int, y: int) -> None:
        # Records the cursor path between actions. Throttled by time + distance so
        # it samples the path instead of flooding with thousands of pixels. Runs
        # on the input hook thread, so it stays cheap (no element resolution).
        if not self._recording or self._paused or not self.capture_moves:
            return
        if self._button_down:  # moves during a held button belong to a drag
            return
        if self._text_buffer:  # don't fragment an in-progress type burst
            return
        now = time.monotonic()
        if now - self._last_move_time < _MOVE_INTERVAL_S:
            return
        if self._last_move_pos is not None:
            lx, ly = self._last_move_pos
            if abs(x - lx) < _MOVE_MIN_DIST and abs(y - ly) < _MOVE_MIN_DIST:
                return
        self._last_move_time = now
        self._last_move_pos = (x, y)
        step = ActionStep(type="move", x=x, y=y, description=f"Move to ({x}, {y})")
        with self._lock:
            self._steps.append(step)
        self._notify_step_added()

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self._recording or self._paused:
            return

        self._flush_text_buffer()
        self._add_delay()

        # pynput's wheel-event coordinates can be stale; read the live cursor so
        # playback scrolls over the right area.
        pos = _cursor_pos()
        sx, sy = pos if pos else (x, y)

        direction = "down" if dy < 0 else "up" if dy > 0 else "left" if dx < 0 else "right"
        logger.debug("SCROLL %s dx=%d dy=%d at (%d,%d)", direction, dx, dy, sx, sy)
        step = ActionStep(
            type="scroll",
            x=sx,
            y=sy,
            scroll_dx=dx,
            scroll_dy=dy,
            description=f"Scroll {direction} ({abs(dy)} clicks)",
        )

        with self._lock:
            self._steps.append(step)
        self._notify_step_added()

    def _on_key_press(self, key) -> None:
        if not self._recording or self._paused:
            return

        if key in _MODIFIER_KEYS:
            self._held_modifiers.add(key)
            return

        now_active_modifiers = set(self._held_modifiers)

        if now_active_modifiers:
            self._flush_text_buffer()
            self._add_delay()

            key_str = self._key_to_str(key)
            mod_names = self._modifiers_str(now_active_modifiers)
            combo = f"{mod_names}+{key_str}" if mod_names else key_str

            logger.debug("KEY combo=%s (mods=%s)", combo, mod_names)
            step = ActionStep(
                type="keypress",
                keys=combo,
                description=f"Press {combo}",
            )
            with self._lock:
                self._steps.append(step)
            self._notify_step_added()
        else:
            char = self._key_to_char(key)
            if char and char in _NORMAL_CHAR_KEYS:
                if not self._text_buffer:
                    self._add_delay()
                self._text_buffer += char
                logger.debug("buffering char %r -> buffer=%r", char, self._text_buffer)
            else:
                self._flush_text_buffer()
                self._add_delay()

                key_str = self._key_to_str(key)
                step = ActionStep(
                    type="keypress",
                    keys=key_str,
                    description=f"Press {key_str}",
                )
                with self._lock:
                    self._steps.append(step)
                self._notify_step_added()

    def _on_key_release(self, key) -> None:
        self._held_modifiers.discard(key)

    @staticmethod
    def _key_to_str(key) -> str:
        if isinstance(key, keyboard.Key):
            return key.name
        if isinstance(key, keyboard.KeyCode):
            if key.char:
                return key.char
            if key.vk:
                return f"vk_{key.vk}"
        return str(key)

    @staticmethod
    def _key_to_char(key) -> Optional[str]:
        if isinstance(key, keyboard.KeyCode) and key.char:
            return key.char
        # Space arrives as a special Key, not a char — fold it into typed text so
        # "hello world" stays one type_text instead of splitting on the space.
        if key == keyboard.Key.space:
            return " "
        return None

    @staticmethod
    def _modifiers_str(modifiers: set) -> str:
        names = []
        for m in modifiers:
            name = m.name if isinstance(m, keyboard.Key) else str(m)
            if "ctrl" in name:
                names.append("ctrl")
            elif "alt" in name:
                names.append("alt")
            elif "shift" in name:
                names.append("shift")
            elif "cmd" in name:
                names.append("win")
        return "+".join(sorted(set(names)))
