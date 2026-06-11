import logging
import subprocess
import threading
import time
from typing import Callable, Optional

import pyautogui
from pywinauto import Desktop

from flowrecord.config import MIN_ACTION_INTERVAL_MS
from flowrecord.core.element_detector import find_element
from flowrecord.models import ActionStep, Workflow

logger = logging.getLogger(__name__)

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.02


class Player:
    def __init__(self, on_step_complete: Optional[Callable[[int, int, str], None]] = None):
        self._playing = False
        self._paused = False
        self._stop_flag = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._current_index = 0
        self._total_steps = 0
        self._on_step_complete = on_step_complete
        self._thread: Optional[threading.Thread] = None

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def total_steps(self) -> int:
        return self._total_steps

    def play(self, workflow: Workflow, speed: float = 1.0) -> None:
        if self._playing:
            logger.warning("Player already playing")
            return

        self._playing = True
        self._paused = False
        self._stop_flag.clear()
        self._pause_event.set()
        self._total_steps = len(workflow.steps)
        self._current_index = 0

        self._thread = threading.Thread(
            target=self._run, args=(workflow.steps, speed), daemon=True
        )
        self._thread.start()

    def play_blocking(self, workflow: Workflow, speed: float = 1.0) -> None:
        if self._playing:
            return
        self._playing = True
        self._paused = False
        self._stop_flag.clear()
        self._pause_event.set()
        self._total_steps = len(workflow.steps)
        self._current_index = 0
        try:
            self._run(workflow.steps, speed)
        finally:
            self._playing = False

    def stop(self) -> None:
        if self._playing:
            self._stop_flag.set()
            self._pause_event.set()
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=5.0)
            self._playing = False
            self._paused = False

    def pause(self) -> None:
        if self._playing and not self._paused:
            self._paused = True
            self._pause_event.clear()

    def resume(self) -> None:
        if self._playing and self._paused:
            self._paused = False
            self._pause_event.set()

    def _run(self, steps: list[ActionStep], speed: float) -> None:
        logger.info("Playback starting — %d steps, speed=%.1fx", len(steps), speed)
        for i, step in enumerate(steps):
            if self._stop_flag.is_set():
                logger.info("Playback stopped at step %d", i)
                break

            self._pause_event.wait()

            if self._stop_flag.is_set():
                break

            self._current_index = i

            if not step.enabled:
                logger.debug("Skipping disabled step %d: %s", i, step.description)
                self._notify(step)
                continue

            self._notify(step)

            try:
                self._execute_step(step, speed)
            except Exception:
                logger.exception("Error executing step %d (%s): %s", i, step.type, step.description)

            min_interval = MIN_ACTION_INTERVAL_MS / 1000.0
            time.sleep(min_interval)

        self._playing = False
        logger.info("Playback finished")

    def _execute_step(self, step: ActionStep, speed: float) -> None:
        handler = {
            "click": self._do_click,
            "keypress": self._do_keypress,
            "type_text": self._do_type_text,
            "scroll": self._do_scroll,
            "launch_app": self._do_launch_app,
            "delay": lambda s: None,
        }.get(step.type)

        if handler:
            handler(step)
        else:
            logger.warning("Unknown step type: %s", step.type)

        if step.delay_after > 0:
            sleep_time = max(step.delay_after / speed, MIN_ACTION_INTERVAL_MS / 1000.0)
            time.sleep(sleep_time)

    def _do_click(self, step: ActionStep) -> None:
        click_x, click_y = self._resolve_click_coords(step)
        logger.debug("Click at (%d, %d)", click_x, click_y)
        pyautogui.click(click_x, click_y)

    def _resolve_click_coords(self, step: ActionStep) -> tuple[int, int]:
        if step.element_name:
            found = find_element(
                app_name=step.app_name,
                element_name=step.element_name,
                element_type=step.element_type,
                window_title=step.window_title,
            )
            if found and found["x"] is not None and found["y"] is not None:
                logger.debug("Element found: clicking at element center (%d, %d)", found["x"], found["y"])
                return found["x"], found["y"]
            else:
                logger.warning("Element '%s' not found, falling back to coordinates", step.element_name)

        if step.window_title and step.x_relative is not None and step.y_relative is not None:
            coords = self._relative_to_absolute(
                step.window_title, step.x_relative, step.y_relative
            )
            if coords:
                logger.debug("Using relative coords within window")
                return coords

        if step.x is not None and step.y is not None:
            logger.debug("Falling back to raw coordinates (%d, %d)", step.x, step.y)
            return step.x, step.y

        logger.warning("No coordinates available for click step")
        return 0, 0

    def _relative_to_absolute(
        self, window_title: str, x_rel: float, y_rel: float
    ) -> Optional[tuple[int, int]]:
        try:
            desktop = Desktop(backend="uia")
            for w in desktop.windows():
                wname = w.window_text()
                if wname and window_title.lower() in wname.lower():
                    rect = w.rectangle()
                    x = int(rect.left + x_rel * (rect.right - rect.left))
                    y = int(rect.top + y_rel * (rect.bottom - rect.top))
                    return x, y
        except Exception:
            logger.debug("Failed to find window for relative coords: %s", window_title)
        return None

    def _do_keypress(self, step: ActionStep) -> None:
        keys_str = step.keys or ""
        if not keys_str:
            return

        parts = [p.strip() for p in keys_str.split("+")]
        if len(parts) > 1:
            pyautogui.hotkey(*parts)
        else:
            pyautogui.press(parts[0])

    def _do_type_text(self, step: ActionStep) -> None:
        if not step.text:
            return
        pyautogui.write(step.text, interval=0.02)

    def _do_scroll(self, step: ActionStep) -> None:
        if step.scroll_dy != 0:
            pyautogui.scroll(step.scroll_dy)
        if step.scroll_dx != 0:
            pyautogui.hscroll(step.scroll_dx)

    def _do_launch_app(self, step: ActionStep) -> None:
        app_name = step.app_name
        if not app_name:
            logger.warning("launch_app step has no app_name")
            return

        logger.info("Launching app: %s", app_name)
        try:
            subprocess.Popen([app_name], shell=True)
        except Exception:
            logger.exception("Failed to launch app: %s", app_name)
            logger.info("Retrying in 3s...")
            time.sleep(3.0)
            try:
                subprocess.Popen([app_name], shell=True)
            except Exception:
                logger.exception("Retry failed for app: %s", app_name)
                return

        self._wait_for_window(app_name, timeout=10)

    def _wait_for_window(self, app_name: str, timeout: float = 10.0) -> None:
        import psutil
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                for proc in psutil.process_iter(["name"]):
                    if proc.info["name"] and proc.info["name"].lower() == app_name.lower():
                        return
            except Exception:
                pass
            time.sleep(0.5)
        logger.warning("Timed out waiting for app window: %s", app_name)

    def _notify(self, step: ActionStep) -> None:
        if self._on_step_complete:
            try:
                self._on_step_complete(self._current_index + 1, self._total_steps, step.description or "")
            except Exception:
                pass
