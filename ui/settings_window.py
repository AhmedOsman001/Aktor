"""Settings screen for FlowRecord — General / Recording / Hotkeys groups shown as
inset cards, matching the Aktor design. Reached via the sidebar (no back button).

Emits ``theme_mode_changed(mode)`` (system|light|dark) and
``pref_changed(key, value)``; the app persists + applies them.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from flowrecord.config import DEFAULT_RECORD_HOTKEY
from flowrecord.ui import icons, theme
from flowrecord.ui.components import (
    HotkeyPill, InsetList, SegmentedControl, ToggleSwitch, _style, ask_text,
    pretty_hotkey,
)

_THEME_MODES = ["system", "light", "dark"]

_HK_DEFAULTS = {
    "record_hotkey": DEFAULT_RECORD_HOTKEY,
    "stop_hotkey": "esc",
    "showhide_hotkey": "ctrl+shift+a",
}


class SettingsWindow(QWidget):
    back_requested = Signal()
    theme_mode_changed = Signal(str)       # system | light | dark
    accent_changed = Signal(str)           # kept for API compat (unused)
    pref_changed = Signal(str, object)
    about_requested = Signal()             # kept for API compat (unused)

    def __init__(self, prefs: dict | None = None, parent=None):
        super().__init__(parent)
        self._prefs = prefs or {}
        self._chevrons: list[QLabel] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent;")
        scroll.viewport().setStyleSheet("background: transparent;")
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 18, 28, 24)
        root.setSpacing(12)

        title = QLabel("Settings")
        title.setObjectName("settingsTitle")
        root.addWidget(title)

        # ---- GENERAL ----
        root.addWidget(self._section("GENERAL"))
        general = InsetList()
        general.add_row(
            "Launch at startup", self._toggle("launch_at_startup", False),
            subtitle="Start FlowRecord when you log in",
        )
        general.add_row(
            "Minimize to system tray", self._toggle("minimize_to_tray", True),
            subtitle="Keep FlowRecord running in the background",
        )
        pref = self._prefs.get("theme", theme.manager.mode)
        idx = _THEME_MODES.index(pref) if pref in _THEME_MODES else 2
        self._theme_seg = SegmentedControl(["System", "Light", "Dark"], index=idx)
        self._theme_seg.changed.connect(
            lambda i: self.theme_mode_changed.emit(_THEME_MODES[i])
        )
        general.add_row("Theme", self._theme_seg)

        side = self._prefs.get("overlay_side", "left")
        self._side_seg = SegmentedControl(["Left", "Right"], index=(1 if side == "right" else 0))
        self._side_seg.changed.connect(
            lambda i: self.pref_changed.emit("overlay_side", "right" if i == 1 else "left")
        )
        general.add_row(
            "Overlay position", self._side_seg,
            subtitle="Dock the control bar to the left or right edge",
        )
        root.addWidget(general)

        # ---- RECORDING ----
        root.addWidget(self._section("RECORDING"))
        recording = InsetList()
        recording.add_row(
            "Capture mouse movement", self._toggle("capture_moves", False),
            subtitle="Record cursor path between actions",
        )
        recording.add_row(
            "Capture delays", self._toggle("capture_delays", True),
            subtitle="Preserve timing between actions",
        )
        recording.add_row(
            "Minimize on record", self._toggle("minimize_on_record", True),
            subtitle="Hide the window while capturing",
        )
        root.addWidget(recording)

        # ---- HOTKEYS ----
        root.addWidget(self._section("HOTKEYS"))
        hotkeys = InsetList()
        self._hk_row(hotkeys, "Start / stop recording", "record_hotkey")
        self._hk_row(hotkeys, "Stop playback", "stop_hotkey")
        self._hk_row(hotkeys, "Show / hide FlowRecord", "showhide_hotkey")
        root.addWidget(hotkeys)

        root.addStretch(1)

        theme.manager.changed.connect(self._apply)
        self._apply()

    # ---- builders ----
    def _section(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("sectionLabel")
        return lbl

    def _toggle(self, key: str, default: bool) -> ToggleSwitch:
        t = ToggleSwitch(self._prefs.get(key, default))
        t.toggled.connect(lambda v, k=key: self.pref_changed.emit(k, v))
        return t

    def _hk_row(self, inset: InsetList, title: str, key: str) -> None:
        value = self._prefs.get(key, _HK_DEFAULTS.get(key, ""))
        pill = HotkeyPill(value)
        chevron = QLabel()
        chevron.setObjectName("settingsChevron")
        self._chevrons.append(chevron)
        trailing = QWidget()
        tl = QHBoxLayout(trailing)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(10)
        tl.addWidget(pill)
        tl.addWidget(chevron)
        inset.add_row(title, trailing, on_click=lambda k=key, t=title, p=pill: self._edit_hk(k, t, p))

    def _edit_hk(self, key: str, title: str, pill: HotkeyPill) -> None:
        current = self._prefs.get(key, _HK_DEFAULTS.get(key, ""))
        text, ok = ask_text(
            self, title, "Set hotkey (e.g. ctrl+shift+r) — blank to clear:",
            current or "",
        )
        if not ok:
            return
        value = text.strip().lower()
        self._prefs[key] = value
        pill.set_hotkey(value)
        self.pref_changed.emit(key, value)

    # ---- styling ----
    def _apply(self) -> None:
        self.setStyleSheet(_style(
            "#settingsTitle { font-size: 28px; font-weight: 800; color: @HEADING@; }"
            "#sectionLabel { color: @MUTED@; font-size: 12px; font-weight: 700;"
            " letter-spacing: 1px; padding: 10px 2px 2px; }"
        ))
        for chev in self._chevrons:
            chev.setPixmap(icons.pixmap("chevron_right", theme.manager.color("MUTED").name(), 16))
