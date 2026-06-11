import logging
import threading
import time
from typing import Callable, Optional

import psutil
from pynput import keyboard, mouse

from flowrecord.config import APP_NAME, APP_POLL_INTERVAL_MS, DELAY_THRESHOLD, MAX_DELAY_SECONDS
from flowrecord.core.element_detector import get_element_at
from flowrecord.models import ActionStep

logger = logging.getLogger(__name__)

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
        self._known_pids: set[int] = set()
        self._lock = threading.Lock()

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
        self._start_time = time.monotonic()
        self._last_action_time = self._start_time
        self._stop_event.clear()

        self._snapshot_running_pids()

        self._mouse_listener = mouse.Listener(
            on_click=self._on_click,
            on_scroll=self._on_scroll,
        )
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._mouse_listener.start()
        self._keyboard_listener.start()

        self._app_poll_thread = threading.Thread(
            target=self._poll_new_apps, daemon=True
        )
        self._app_poll_thread.start()

        logger.info("Recording started")

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

        self._mouse_listener = None
        self._keyboard_listener = None
        self._app_poll_thread = None

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

    def _snapshot_running_pids(self) -> None:
        self._known_pids.clear()
        try:
            for proc in psutil.process_iter(["pid"]):
                self._known_pids.add(proc.pid)
        except Exception:
            pass

    def _add_delay(self) -> None:
        now = time.monotonic()
        delta = now - self._last_action_time
        if delta > DELAY_THRESHOLD and self._steps:
            capped = min(delta, MAX_DELAY_SECONDS)
            self._steps[-1].delay_after = round(capped, 3)
        self._last_action_time = now

    def _flush_text_buffer(self) -> None:
        if not self._text_buffer:
            return
        with self._lock:
            text = self._text_buffer
            self._text_buffer = ""

        step = ActionStep(
            type="type_text",
            text=text,
            description=f"Type '{text}'",
        )
        with self._lock:
            self._steps.append(step)
        self._notify_step_added()

    def _notify_step_added(self) -> None:
        if self._on_step_added:
            try:
                self._on_step_added(len(self._steps))
            except Exception:
                pass

    def _on_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        if not self._recording or self._paused or not pressed:
            return

        elem = get_element_at(x, y)

        app = elem.get("app_name", "")
        if app and app.lower() in ("python.exe", "pythonw.exe", "py.exe"):
            win = elem.get("window_title", "") or ""
            if APP_NAME.lower() in win.lower() or "flowrecord" in win.lower():
                return

        self._flush_text_buffer()
        self._add_delay()

        elem = get_element_at(x, y)

        step = ActionStep(
            type="click",
            x=x,
            y=y,
            app_name=elem.get("app_name"),
            window_title=elem.get("window_title"),
            element_name=elem.get("element_name"),
            element_type=elem.get("element_type"),
            x_relative=elem.get("x_relative"),
            y_relative=elem.get("y_relative"),
            description=f"Click at ({x}, {y})"
            + (f" on '{elem.get('element_name')}'" if elem.get("element_name") else ""),
        )

        with self._lock:
            self._steps.append(step)
        self._notify_step_added()

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self._recording or self._paused:
            return

        self._flush_text_buffer()
        self._add_delay()

        direction = "down" if dy < 0 else "up" if dy > 0 else "left" if dx < 0 else "right"
        step = ActionStep(
            type="scroll",
            x=x,
            y=y,
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

    def _poll_new_apps(self) -> None:
        interval = APP_POLL_INTERVAL_MS / 1000.0
        while not self._stop_event.is_set():
            self._stop_event.wait(interval)
            if self._stop_event.is_set():
                break
            if self._paused:
                continue
            try:
                current_pids: set[int] = set()
                new_processes: list[psutil.Process] = []
                for proc in psutil.process_iter(["pid", "name"]):
                    current_pids.add(proc.pid)
                    if proc.pid not in self._known_pids:
                        try:
                            new_processes.append(psutil.Process(proc.pid))
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass

                self._known_pids = current_pids

                for proc in new_processes:
                    try:
                        name = proc.name()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

                    self._add_delay()
                    step = ActionStep(
                        type="launch_app",
                        app_name=name,
                        description=f"App launched: {name}",
                    )
                    with self._lock:
                        self._steps.append(step)
                    self._notify_step_added()
            except Exception:
                logger.warning("Error polling for new apps", exc_info=True)

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
