import ctypes
import json
import logging
import os
import sys
import time
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ctypes.windll.ole32.OleInitialize(None)

try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    pass

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

warnings.filterwarnings("ignore", message=".*Revert to STA COM threading mode.*")

os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

from flowrecord.logger_setup import setup_logging

_log_file = setup_logging()

from PySide6.QtWidgets import (
    QApplication,
    QSystemTrayIcon,
    QMenu,
)
from PySide6.QtGui import QAction, QIcon, QFont
from PySide6.QtCore import Qt, QTimer

from flowrecord.config import APP_NAME, DATA_DIR, DEFAULT_RECORD_HOTKEY
from flowrecord.listeners.hotkey_listener import register, unregister, unregister_all
from flowrecord.models import Trigger, Workflow
from flowrecord.ui import icons, theme, theme_prefs
from flowrecord.ui.components import ask_text
from flowrecord.ui.overlay import OverlayController, OverlayState
from flowrecord.core.player import Player
from flowrecord.core.recorder import Recorder
from flowrecord.ui.workflow_manager import WorkflowManagerWindow
from flowrecord.storage.workflow_store import (
    get_all_workflows,
    get_workflow,
    init_db,
    save_workflow,
    update_last_run,
)

logger = logging.getLogger(__name__)
logger.debug("Log file: %s", _log_file)


def _make_icon() -> QIcon:
    return icons.app_icon(32)


_APP_PREFS_PATH = DATA_DIR / "app_prefs.json"
_DEFAULT_APP_PREFS = {
    "launch_at_startup": False,
    "minimize_to_tray": True,
    "capture_moves": False,
    "capture_delays": True,
    "minimize_on_record": True,
    "overlay_side": "left",
    "record_hotkey": DEFAULT_RECORD_HOTKEY,
    "stop_hotkey": "esc",
    "showhide_hotkey": "ctrl+shift+a",
}


def _resolve_theme(pref: str) -> str:
    """Map a theme preference (system/light/dark) to an effective light/dark."""
    if pref == "system":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return "light" if val == 1 else "dark"
        except Exception:
            return "dark"
    return pref if pref in ("light", "dark") else "dark"


def _load_app_prefs() -> dict:
    prefs = dict(_DEFAULT_APP_PREFS)
    try:
        with open(_APP_PREFS_PATH, "r", encoding="utf-8") as f:
            prefs.update(json.load(f))
    except Exception:
        pass
    return prefs


def _save_app_prefs(prefs: dict) -> None:
    try:
        with open(_APP_PREFS_PATH, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
    except Exception:
        logger.debug("Failed to save app prefs", exc_info=True)


def _set_startup(enabled: bool) -> None:
    """Best-effort 'launch at startup' via the HKCU Run key."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        )
        if enabled:
            winreg.SetValueEx(
                key, APP_NAME, 0, winreg.REG_SZ,
                f'"{sys.executable}" "{sys.argv[0]}"',
            )
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception:
        logger.debug("Could not update startup registry key")


class FlowRecordApp:
    def __init__(self):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setQuitOnLastWindowClosed(False)

        # Prefer Inter; Qt falls back to the next available family if absent.
        _base_font = QFont("Inter", 10)
        _base_font.setStyleHint(QFont.StyleHint.SansSerif)
        self._app.setFont(_base_font)

        self._app_prefs = _load_app_prefs()

        # Load + apply the saved theme before building any UI. The theme
        # *preference* (system/light/dark) lives in app prefs; the accent and a
        # cached effective mode live in theme prefs.
        saved = theme_prefs.load()
        theme_pref = self._app_prefs.get("theme", saved["mode"])
        self._app_prefs["theme"] = theme_pref
        theme.manager.set_theme(_resolve_theme(theme_pref), saved["accent"])
        theme.manager.apply(self._app)

        self._icon = _make_icon()
        self._app.setWindowIcon(self._icon)

        # Bridge Python logging into the UI's Activity panel.
        from flowrecord.ui import log_panel
        log_panel.install()

        init_db()

        self._recorder = Recorder(on_step_added=self._on_step_added)
        self._player = Player(on_step_complete=self._on_playback_step)
        self._overlay = OverlayController()
        self._overlay.set_side(self._app_prefs.get("overlay_side", "left"))
        self._wf_dialog: WorkflowManagerWindow | None = None
        self._play_remaining = 0
        self._play_loop = False
        # True while we close the manager ourselves (record/new), so the close
        # isn't mistaken for the user quitting via the window's X button.
        self._programmatic_close = False

        # Smart Wait status -> overlay (called from the playback thread).
        self._player.on_smart_wait_progress = (
            lambda name, e, t: self._overlay.show_smart_wait(name, e, t)
        )
        self._player.on_smart_wait_found = (
            lambda name, e: self._overlay.show_smart_wait_found(name, e)
        )
        self._player.on_smart_wait_timeout = (
            lambda name, t: self._overlay.show_smart_wait_timeout(name, t)
        )

        self._overlay.record_requested.connect(self._start_recording)
        self._overlay.stop_requested.connect(self._stop)
        self._overlay.pause_requested.connect(self._toggle_pause)
        self._overlay.workflows_requested.connect(self._show_workflows)

        self._setup_tray()
        self._register_global_hotkeys()
        self._overlay.show()
        self._show_workflows()

        self._last_toggle_time = 0.0

        logger.info("FlowRecord started  |  Record hotkey: %s  |  Tray icon in taskbar", DEFAULT_RECORD_HOTKEY)

    def _setup_tray(self):
        self._tray = QSystemTrayIcon(self._icon, self._app)
        self._menu = QMenu()
        self._menu.aboutToShow.connect(self._rebuild_tray_menu)
        self._rebuild_tray_menu()
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.setToolTip(f"{APP_NAME}\n{self._record_hotkey()} to record")
        self._tray.show()

    def _rebuild_tray_menu(self):
        m = self._menu
        m.clear()

        act_record = m.addAction("●  Record / Stop")
        act_record.triggered.connect(self._toggle_recording)

        m.addSeparator()
        label = m.addAction("RECENT")
        label.setEnabled(False)
        for wf in get_all_workflows()[:5]:
            text = wf.name
            if wf.trigger.hotkey:
                text = f"{wf.name}\t{wf.trigger.hotkey}"
            act = m.addAction("▶  " + text)
            act.triggered.connect(lambda _=False, wid=wf.id: self._play_by_id(wid))

        m.addSeparator()
        act_show = m.addAction("Open FlowRecord")
        act_show.triggered.connect(self._show_workflows)
        act_settings = m.addAction("Settings…")
        act_settings.triggered.connect(self._open_settings)
        act_theme = m.addAction("Toggle Light / Dark")
        act_theme.triggered.connect(self._toggle_theme)

        m.addSeparator()
        act_quit = m.addAction("Quit FlowRecord")
        act_quit.triggered.connect(self._quit)

    def _open_settings(self):
        self._show_workflows()
        if self._wf_dialog is not None:
            self._wf_dialog.show_settings()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_workflows()

    def _show_overlay(self):
        self._overlay.show()

    def _toggle_theme(self):
        new_mode = "light" if theme.manager.is_dark() else "dark"
        theme.manager.set_mode(new_mode, emit=False)
        theme.manager.apply(self._app)
        self._refresh_app_icon()
        theme_prefs.save(theme.manager.mode, theme.manager.accent)
        logger.info("Theme switched to %s mode", new_mode)

    def _refresh_app_icon(self):
        self._icon = _make_icon()
        self._app.setWindowIcon(self._icon)
        self._tray.setIcon(self._icon)

    def _record_hotkey(self) -> str:
        return self._app_prefs.get("record_hotkey", DEFAULT_RECORD_HOTKEY)

    def _register_global_hotkeys(self):
        unregister_all()
        register(self._record_hotkey(), self._toggle_recording)

        stop_hk = self._app_prefs.get("stop_hotkey")
        if stop_hk:
            register(stop_hk, lambda: QTimer.singleShot(0, self._stop))
        showhide = self._app_prefs.get("showhide_hotkey")
        if showhide:
            register(showhide, lambda: QTimer.singleShot(0, self._toggle_window))

        for wf in get_all_workflows():
            if wf.trigger.hotkey:
                full_wf = get_workflow(wf.id)
                if full_wf:
                    register(wf.trigger.hotkey, lambda w=full_wf: self._play_workflow(w))

    def _toggle_window(self):
        if self._wf_dialog is not None and self._wf_dialog.isVisible():
            self._wf_dialog.hide()
        else:
            self._show_workflows()

    def _toggle_recording(self):
        now = time.monotonic()
        if now - self._last_toggle_time < 0.5:
            return
        self._last_toggle_time = now

        QTimer.singleShot(0, self._do_toggle)

    def _do_toggle(self):
        if self._recorder.recording:
            self._stop_recording()
        else:
            self._start_recording()

    def run(self) -> int:
        return self._app.exec()

    def _start_recording(self):
        if self._recorder.recording:
            return
        if self._player.playing:
            self._player.stop()
        if self._app_prefs.get("minimize_on_record", True):
            self._close_wf_dialog()
        logger.info("Starting recording...")
        self._overlay.show()
        self._overlay.set_state(OverlayState.RECORDING)
        self._overlay.set_step_count(0)
        self._recorder.capture_moves = self._app_prefs.get("capture_moves", False)
        self._recorder.start()

    def _stop(self):
        if self._recorder.recording:
            self._stop_recording()
        elif self._player.playing:
            # Cancel any pending repeat/loop so playback doesn't restart.
            self._play_loop = False
            self._play_remaining = 0
            self._player.stop()
            self._overlay.set_state(OverlayState.IDLE)

    def _stop_recording(self):
        steps = self._recorder.stop()
        logger.info("Recording stopped — %d steps", len(steps))
        self._overlay.set_state(OverlayState.IDLE)

        if not steps:
            logger.info("No steps recorded, discarding")
            return

        # "Capture delays" off -> play back as fast as possible.
        if not self._app_prefs.get("capture_delays", True):
            for s in steps:
                s.delay_after = 0.0

        self._close_wf_dialog()

        name, ok = ask_text(
            self._wf_dialog, "Save Workflow", "Workflow name:", "My Workflow"
        )
        if not ok or not name.strip():
            logger.info("Workflow save cancelled")
            return

        hotkey_str, ok2 = ask_text(
            self._wf_dialog, "Set Hotkey (optional)",
            "Trigger hotkey (e.g. ctrl+alt+1) or leave blank:", "",
        )

        hotkey = hotkey_str.strip() if ok2 and hotkey_str.strip() else None

        wf = Workflow(
            name=name.strip(),
            steps=steps,
            trigger=Trigger(hotkey=hotkey),
        )
        wf_id = save_workflow(wf)

        if hotkey:
            register(hotkey, lambda: self._play_workflow(get_workflow(wf_id)))

        logger.info("Workflow '%s' saved (id=%d, %d steps, hotkey=%s)",
                     name.strip(), wf_id, len(steps), hotkey or "none")

    def _toggle_pause(self):
        paused = None
        if self._recorder.recording:
            if self._recorder.paused:
                self._recorder.resume()
                paused = False
            else:
                self._recorder.pause()
                paused = True
        elif self._player.playing:
            if self._player.paused:
                self._player.resume()
                paused = False
            else:
                self._player.pause()
                paused = True
        if paused is not None:
            self._overlay.set_paused(paused)

    def _show_workflows(self):
        if self._wf_dialog is not None:
            self._wf_dialog.raise_()
            self._wf_dialog.activateWindow()
            return
        self._wf_dialog = WorkflowManagerWindow(self._build_prefs())
        self._wf_dialog.play_requested.connect(self._play_by_id)
        self._wf_dialog.new_requested.connect(self._on_new_from_manager)
        self._wf_dialog.test_steps_requested.connect(self._play_workflow)
        self._wf_dialog.hotkeys_changed.connect(self._register_global_hotkeys)
        self._wf_dialog.theme_mode_requested.connect(self._on_theme_mode)
        self._wf_dialog.accent_requested.connect(self._on_accent)
        self._wf_dialog.pref_changed.connect(self._on_pref_changed)
        self._wf_dialog.finished.connect(self._on_wf_dialog_closed)
        self._wf_dialog.show()

    def _build_prefs(self) -> dict:
        g = self._app_prefs.get
        return {
            "accent": theme.manager.accent,
            "theme": g("theme", theme.manager.mode),
            "launch_at_startup": g("launch_at_startup", False),
            "minimize_to_tray": g("minimize_to_tray", True),
            "capture_moves": g("capture_moves", False),
            "capture_delays": g("capture_delays", True),
            "minimize_on_record": g("minimize_on_record", True),
            "overlay_side": g("overlay_side", "left"),
            "record_hotkey": self._record_hotkey(),
            "stop_hotkey": g("stop_hotkey", "esc"),
            "showhide_hotkey": g("showhide_hotkey", "ctrl+shift+a"),
        }

    def _on_theme_mode(self, mode: str):
        # mode is the *preference*: system | light | dark
        self._app_prefs["theme"] = mode
        _save_app_prefs(self._app_prefs)
        effective = _resolve_theme(mode)
        theme.manager.set_mode(effective, emit=False)
        theme.manager.apply(self._app)
        self._refresh_app_icon()
        theme_prefs.save(effective, theme.manager.accent)
        logger.info("Theme -> %s (effective %s)", mode, effective)

    def _on_accent(self, hexc: str):
        theme.manager.set_accent(hexc, emit=False)
        theme.manager.apply(self._app)
        self._refresh_app_icon()
        theme_prefs.save(theme.manager.mode, theme.manager.accent)
        logger.info("Accent -> %s", hexc)

    def _on_pref_changed(self, key: str, value):
        self._app_prefs[key] = value
        _save_app_prefs(self._app_prefs)
        if key in ("record_hotkey", "stop_hotkey", "showhide_hotkey"):
            self._register_global_hotkeys()
            self._tray.setToolTip(f"{APP_NAME}\n{self._record_hotkey()} to record")
        elif key == "launch_at_startup":
            _set_startup(bool(value))
        elif key == "overlay_side":
            self._overlay.set_side(value)
        # capture_moves / capture_delays / minimize_on_record / minimize_to_tray
        # are read from prefs when needed (record start/stop, window close).
        logger.info("Setting changed: %s = %s", key, value)

    def _on_new_from_manager(self):
        self._close_wf_dialog()
        self._start_recording()

    def _on_wf_dialog_closed(self):
        self._wf_dialog = None
        # "Minimize to system tray" off -> the X button quits the app (unless
        # we closed it ourselves, or something is actively running).
        if (not self._programmatic_close
                and not self._app_prefs.get("minimize_to_tray", True)
                and not self._recorder.recording
                and not self._player.playing):
            logger.info("Minimize-to-tray is off — quitting on window close")
            self._quit()

    def _close_wf_dialog(self):
        if self._wf_dialog is not None:
            self._programmatic_close = True
            try:
                self._wf_dialog.close()
            finally:
                self._programmatic_close = False
            self._wf_dialog = None

    def _play_by_id(self, wf_id: int, speed: float = 1.0, repeat: int = 1,
                    loop: bool = False):
        wf = get_workflow(wf_id)
        if wf:
            self._play_workflow(wf, speed, repeat, loop)

    def _play_workflow(self, wf: Workflow, speed: float = 1.0, repeat: int = 1,
                       loop: bool = False):
        if self._recorder.recording:
            return
        logger.info("Playing workflow '%s' (%d steps, speed=%.2gx, repeat=%d, loop=%s)",
                    wf.name, len(wf.steps), speed, repeat, loop)
        self._overlay.show()
        self._overlay.set_state(OverlayState.PLAYING)
        self._player.play(wf, speed)

        if wf.id:
            update_last_run(wf.id)

        # Track remaining passes so repeat / loop replay the workflow.
        self._play_remaining = max(1, repeat)
        self._play_loop = bool(loop)

        def check_done():
            if not self._player.playing:
                self._play_remaining -= 1
                if self._play_loop or self._play_remaining > 0:
                    self._player.play(wf, speed)
                    return
                timer.stop()
                self._overlay.set_state(OverlayState.IDLE)
                logger.info("Playback finished")

        timer = QTimer()
        timer.timeout.connect(check_done)
        timer.start(200)

    def _on_step_added(self, count: int):
        self._overlay.set_step_count(count)

    def _on_playback_step(self, current: int, total: int, description: str):
        self._overlay.set_playback_progress(current, total, description)

    def _quit(self):
        if self._recorder.recording:
            self._recorder.stop()
        if self._player.playing:
            self._player.stop()
        self._close_wf_dialog()
        self._overlay.hide()
        self._tray.hide()
        self._app.quit()


def main():
    app = FlowRecordApp()
    exit_code = app.run()
    unregister_all()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
