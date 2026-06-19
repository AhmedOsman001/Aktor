import logging
import subprocess
import threading
import time
from typing import Callable, Optional

import psutil
import pyautogui
from pywinauto import Desktop

from flowrecord.config import MIN_ACTION_INTERVAL_MS, SYSTEM_PROCESSES
from flowrecord.core.element_detector import find_element
from flowrecord.models import ActionStep, Workflow

logger = logging.getLogger(__name__)

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.02

# How often Smart Wait re-checks for the target element. Fixed by design — 1s
# is the right granularity for UI element detection and keeps things simple.
SMART_WAIT_INTERVAL_S = 1.0

# One mouse-wheel notch = WHEEL_DELTA raw units. pynput records scroll in notches
# but pyautogui.scroll() takes raw units, so we scale notches up on playback.
WHEEL_DELTA = 120


class PlaybackError(Exception):
    """Raised to halt playback (e.g. a Smart Wait step timed out with on_timeout='stop')."""


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

        # Smart Wait callbacks — set by the host (see main.py). Invoked from the
        # playback thread, same pattern as on_step_complete.
        self.on_smart_wait_progress: Optional[Callable[[str, int, int], None]] = None
        self.on_smart_wait_found: Optional[Callable[[str, int], None]] = None
        self.on_smart_wait_timeout: Optional[Callable[[str, int], None]] = None

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

            logger.debug(
                "Step %d/%d type=%s desc=%r enabled=%s delay=%.2fs",
                i + 1, len(steps), step.type, step.description, step.enabled, step.delay_after,
            )
            try:
                self._execute_step(step, speed)
            except PlaybackError as e:
                logger.error("Playback halted at step %d: %s", i + 1, e)
                break
            except Exception:
                logger.exception("Error executing step %d (%s): %s", i, step.type, step.description)

            min_interval = MIN_ACTION_INTERVAL_MS / 1000.0
            time.sleep(min_interval)

        self._playing = False
        logger.info("Playback finished")

    def _execute_step(self, step: ActionStep, speed: float) -> None:
        # Smart Wait gate: poll for the target element before acting. When
        # enabled, the fixed delay_after is ignored entirely for this step.
        if step.smart_wait_enabled:
            if not self._smart_wait(step):
                if step.smart_wait_on_timeout == "skip":
                    logger.warning(
                        "Skipping step — '%s' not found after %.0fs",
                        step.element_name, step.smart_wait_timeout,
                    )
                    return
                raise PlaybackError(
                    f"Smart wait timed out: '{step.element_name}' "
                    f"not found after {step.smart_wait_timeout:.0f}s"
                )

        handler = {
            "click": self._do_click,
            "double_click": self._do_double_click,
            "right_click": self._do_right_click,
            "middle_click": self._do_middle_click,
            "keypress": self._do_keypress,
            "type_text": self._do_type_text,
            "scroll": self._do_scroll,
            "launch_app": self._do_launch_app,
            "delay": lambda s: None,
        }.get(step.type)

        if handler:
            logger.debug("Executing %s via %s", step.type, getattr(handler, "__name__", handler))
            handler(step)
        else:
            logger.warning("Unknown step type: %s", step.type)

        if not step.smart_wait_enabled and step.delay_after > 0:
            sleep_time = max(step.delay_after / speed, MIN_ACTION_INTERVAL_MS / 1000.0)
            logger.debug("Sleeping %.3fs (delay_after/speed=%.2f/%.1f)", sleep_time, step.delay_after, speed)
            time.sleep(sleep_time)

    def _smart_wait(self, step: ActionStep) -> bool:
        """Poll every second for the step's target element until it exists or the
        timeout elapses. Returns True if found, False on timeout/stop."""
        if not step.element_name:
            # Nothing to wait for — don't block playback on a misconfigured step.
            logger.warning("Smart wait enabled but step has no element_name; proceeding")
            return True

        timeout = step.smart_wait_timeout
        interval = SMART_WAIT_INTERVAL_S
        elapsed = 0.0
        logger.info("Smart wait: looking for '%s' (timeout %.0fs)", step.element_name, timeout)

        while elapsed < timeout:
            if self._stop_flag.is_set():
                return False

            self._emit_smart_wait_progress(step.element_name, elapsed, timeout)

            result = find_element(
                app_name=step.app_name,
                element_name=step.element_name,
                element_type=step.element_type,
                window_title=step.window_title,
                x=step.x,
                y=step.y,
                automation_id=step.automation_id,
                class_name=step.class_name,
                parent_path=step.parent_path,
            )
            if result:
                logger.info("Smart wait: found '%s' after %.0fs", step.element_name, elapsed)
                self._emit_smart_wait_found(step.element_name, elapsed)
                return True

            # Interruptible sleep so stop() breaks out immediately.
            if self._stop_flag.wait(interval):
                return False
            elapsed += interval

        logger.warning("Smart wait: '%s' not found after %.0fs", step.element_name, timeout)
        self._emit_smart_wait_timeout(step.element_name, timeout)
        return False

    def _emit_smart_wait_progress(self, element_name: str, elapsed: float, timeout: float) -> None:
        if self.on_smart_wait_progress:
            try:
                self.on_smart_wait_progress(element_name, int(elapsed), int(timeout))
            except Exception:
                pass

    def _emit_smart_wait_found(self, element_name: str, elapsed: float) -> None:
        if self.on_smart_wait_found:
            try:
                self.on_smart_wait_found(element_name, int(elapsed))
            except Exception:
                pass

    def _emit_smart_wait_timeout(self, element_name: str, timeout: float) -> None:
        if self.on_smart_wait_timeout:
            try:
                self.on_smart_wait_timeout(element_name, int(timeout))
            except Exception:
                pass

    def _do_click(self, step: ActionStep) -> None:
        click_x, click_y = self._resolve_click_coords(step)
        logger.debug("Click at (%d, %d)", click_x, click_y)
        pyautogui.click(click_x, click_y)

    def _do_double_click(self, step: ActionStep) -> None:
        click_x, click_y = self._resolve_click_coords(step)
        logger.debug("Double-click at (%d, %d)", click_x, click_y)
        pyautogui.doubleClick(click_x, click_y)

    def _do_right_click(self, step: ActionStep) -> None:
        click_x, click_y = self._resolve_click_coords(step)
        logger.debug("Right-click at (%d, %d)", click_x, click_y)
        pyautogui.rightClick(click_x, click_y)

    def _do_middle_click(self, step: ActionStep) -> None:
        click_x, click_y = self._resolve_click_coords(step)
        logger.debug("Middle-click at (%d, %d)", click_x, click_y)
        pyautogui.middleClick(click_x, click_y)

    def _resolve_click_coords(self, step: ActionStep) -> tuple[int, int]:
        if step.element_name:
            logger.debug(
                "Searching element name=%r type=%r app=%r window=%r near (%s,%s)",
                step.element_name, step.element_type, step.app_name, step.window_title, step.x, step.y,
            )
            found = find_element(
                app_name=step.app_name,
                element_name=step.element_name,
                element_type=step.element_type,
                window_title=step.window_title,
                x=step.x,
                y=step.y,
                automation_id=step.automation_id,
                class_name=step.class_name,
                parent_path=step.parent_path,
            )
            if found and found["x"] is not None and found["y"] is not None:
                logger.debug(
                    "Element found: clicking center (%d,%d) el=%r type=%r win=%r",
                    found["x"], found["y"], found.get("element_name"),
                    found.get("element_type"), found.get("window_title"),
                )
                return found["x"], found["y"]
            else:
                logger.warning("Element '%s' not found, falling back to coordinates", step.element_name)

        if step.x_relative is not None and step.y_relative is not None:
            coords = self._relative_to_absolute(
                step.app_name, step.window_title, step.x_relative, step.y_relative
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
        self,
        app_name: Optional[str],
        window_title: Optional[str],
        x_rel: float,
        y_rel: float,
    ) -> Optional[tuple[int, int]]:
        try:
            desktop = Desktop(backend="uia")
            windows = desktop.windows()

            # Prefer matching by window title (most specific).
            if window_title:
                wt = window_title.lower()
                for w in windows:
                    try:
                        wname = w.window_text()
                    except Exception:
                        wname = None
                    if wname and wt in wname.lower():
                        rect = w.rectangle()
                        if rect:
                            x = int(rect.left + x_rel * (rect.right - rect.left))
                            y = int(rect.top + y_rel * (rect.bottom - rect.top))
                            return x, y

            # Fall back to matching by process — important for titleless
            # windows (e.g. Spotify's custom-chrome main window).
            if app_name:
                target = app_name.lower()
                for w in windows:
                    try:
                        pid = None
                        ei = getattr(w, "element_info", None)
                        if ei is not None:
                            pid = getattr(ei, "process_id", None)
                        if pid is None:
                            pid = getattr(w, "process_id", None)
                        if not pid:
                            continue
                        if psutil.Process(pid).name().lower() != target:
                            continue
                        rect = w.rectangle()
                        if rect:
                            x = int(rect.left + x_rel * (rect.right - rect.left))
                            y = int(rect.top + y_rel * (rect.bottom - rect.top))
                            return x, y
                    except Exception:
                        continue
        except Exception:
            logger.debug("Failed to find window for relative coords: title=%s app=%s", window_title, app_name)
        return None

    def _do_keypress(self, step: ActionStep) -> None:
        keys_str = step.keys or ""
        if not keys_str:
            logger.debug("keypress step has empty keys, skipping")
            return

        parts = [p.strip() for p in keys_str.split("+")]
        if len(parts) > 1:
            logger.debug("hotkey %s", keys_str)
            pyautogui.hotkey(*parts)
        else:
            logger.debug("press %s", parts[0])
            pyautogui.press(parts[0])

    def _do_type_text(self, step: ActionStep) -> None:
        if not step.text:
            logger.debug("type_text step has empty text, skipping")
            return
        logger.debug("typing %d chars: %r", len(step.text), step.text[:60])
        pyautogui.write(step.text, interval=0.02)

    def _do_scroll(self, step: ActionStep) -> None:
        # Scale notches -> raw wheel units.
        amount_y = int(step.scroll_dy * WHEEL_DELTA)
        amount_x = int(step.scroll_dx * WHEEL_DELTA)

        # IMPORTANT: a wheel event goes to the window under the *current* cursor
        # (Win32 mouse_event ignores x/y for MOUSEEVENTF_WHEEL), so move the
        # cursor onto the target first — passing x/y to pyautogui.scroll alone
        # does nothing.
        if step.x is not None and step.y is not None:
            try:
                pyautogui.moveTo(step.x, step.y)
            except Exception:
                logger.debug("scroll moveTo(%s,%s) failed", step.x, step.y)

        logger.debug(
            "scroll notches dx=%d dy=%d -> delta (%d,%d) at (%s,%s)",
            step.scroll_dx, step.scroll_dy, amount_x, amount_y, step.x, step.y,
        )
        if amount_y != 0:
            pyautogui.scroll(amount_y)
        if amount_x != 0:
            pyautogui.hscroll(amount_x)

    def _do_launch_app(self, step: ActionStep) -> None:
        app_name = step.app_name
        if not app_name:
            logger.warning("launch_app step has no app_name")
            return

        if app_name.lower() in SYSTEM_PROCESSES:
            logger.debug("Skipping system process in launch step: %s", app_name)
            return

        if self._focus_existing_window(app_name):
            logger.info("Focused existing window: %s", app_name)
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

    def _focus_existing_window(self, app_name: str) -> bool:
        target = app_name.lower()
        matches = 0
        try:
            desktop = Desktop(backend="uia")
            windows = desktop.windows()
            logger.debug("Scanning %d top-level windows for %s", len(windows), app_name)
            for w in windows:
                try:
                    pid = None
                    ei = getattr(w, "element_info", None)
                    if ei is not None:
                        pid = getattr(ei, "process_id", None)
                    if pid is None:
                        pid = getattr(w, "process_id", None)
                    if not pid:
                        continue
                    proc = psutil.Process(pid)
                    if proc.name().lower() != target:
                        continue
                    matches += 1
                    try:
                        if not w.is_visible():
                            logger.debug("  match pid=%d but not visible, skipping", pid)
                            continue
                    except Exception:
                        pass
                    logger.debug("  focusing window pid=%d", pid)
                except Exception:
                    continue
                try:
                    w.set_focus()
                    return True
                except Exception:
                    continue
        except Exception:
            logger.debug("Failed scanning windows for %s", app_name, exc_info=True)
        logger.debug("No focusable window found for %s (%d pid matches)", app_name, matches)
        return False

    def _wait_for_window(self, app_name: str, timeout: float = 10.0) -> None:
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
