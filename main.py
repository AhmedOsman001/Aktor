import ctypes
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

from PyQt6.QtWidgets import (
    QApplication,
    QInputDialog,
    QSystemTrayIcon,
    QMenu,
)
from PyQt6.QtGui import QAction, QIcon, QFont
from PyQt6.QtCore import Qt, QTimer

from flowrecord.config import APP_NAME, DEFAULT_RECORD_HOTKEY
from flowrecord.listeners.hotkey_listener import register, unregister, unregister_all
from flowrecord.models import Trigger, Workflow
from flowrecord.ui import icons, theme, theme_prefs
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


class FlowRecordApp:
    def __init__(self):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setQuitOnLastWindowClosed(False)

        self._app.setFont(QFont("Segoe UI", 10))

        # Load + apply the saved theme (mode + accent) before building any UI.
        prefs = theme_prefs.load()
        theme.manager.set_theme(prefs["mode"], prefs["accent"])
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
        self._wf_dialog: WorkflowManagerWindow | None = None

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

        menu = QMenu()

        act_show = menu.addAction("Show Overlay")
        act_show.triggered.connect(self._show_overlay)

        act_record = menu.addAction("Toggle Recording")
        act_record.triggered.connect(self._toggle_recording)

        act_workflows = menu.addAction("Workflows...")
        act_workflows.triggered.connect(self._show_workflows)

        act_theme = menu.addAction("Toggle Light / Dark")
        act_theme.triggered.connect(self._toggle_theme)

        menu.addSeparator()

        act_quit = menu.addAction("Quit FlowRecord")
        act_quit.triggered.connect(self._quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.setToolTip(f"{APP_NAME}\nCtrl+Shift+R to record")
        self._tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_overlay()

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

    def _register_global_hotkeys(self):
        register(DEFAULT_RECORD_HOTKEY, self._toggle_recording)

        for wf in get_all_workflows():
            if wf.trigger.hotkey:
                full_wf = get_workflow(wf.id)
                if full_wf:
                    register(wf.trigger.hotkey, lambda w=full_wf: self._play_workflow(w))

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
        self._close_wf_dialog()
        logger.info("Starting recording...")
        self._overlay.show()
        self._overlay.set_state(OverlayState.RECORDING)
        self._overlay.set_step_count(0)
        self._recorder.start()

    def _stop(self):
        if self._recorder.recording:
            self._stop_recording()
        elif self._player.playing:
            self._player.stop()
            self._overlay.set_state(OverlayState.IDLE)

    def _stop_recording(self):
        steps = self._recorder.stop()
        logger.info("Recording stopped — %d steps", len(steps))
        self._overlay.set_state(OverlayState.IDLE)

        if not steps:
            logger.info("No steps recorded, discarding")
            return

        self._close_wf_dialog()

        name, ok = QInputDialog.getText(
            None, "Save Workflow", "Workflow name:", text="My Workflow"
        )
        if not ok or not name.strip():
            logger.info("Workflow save cancelled")
            return

        hotkey_str, ok2 = QInputDialog.getText(
            None, "Set Hotkey (optional)",
            "Trigger hotkey (e.g. ctrl+alt+1) or leave blank:",
            text=""
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
        self._wf_dialog = WorkflowManagerWindow()
        self._wf_dialog.play_requested.connect(self._play_by_id)
        self._wf_dialog.new_requested.connect(self._on_new_from_manager)
        self._wf_dialog.test_steps_requested.connect(self._play_workflow)
        self._wf_dialog.finished.connect(self._on_wf_dialog_closed)
        self._wf_dialog.show()

    def _on_new_from_manager(self):
        self._close_wf_dialog()
        self._start_recording()

    def _on_wf_dialog_closed(self):
        self._wf_dialog = None

    def _close_wf_dialog(self):
        if self._wf_dialog is not None:
            self._wf_dialog.close()
            self._wf_dialog = None

    def _play_by_id(self, wf_id: int):
        wf = get_workflow(wf_id)
        if wf:
            self._play_workflow(wf)

    def _play_workflow(self, wf: Workflow):
        if self._recorder.recording:
            return
        logger.info("Playing workflow '%s' (%d steps)", wf.name, len(wf.steps))
        self._overlay.show()
        self._overlay.set_state(OverlayState.PLAYING)
        self._player.play(wf)

        if wf.id:
            update_last_run(wf.id)

        def check_done():
            if not self._player.playing:
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
