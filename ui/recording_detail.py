"""Recording detail / step view — integrated as a page inside the main window
(not a popup). Shows a step timeline plus a playback settings panel, matching the
Aktor design. Keeps FlowRecord's engine/model (ActionStep) underneath.
"""

from typing import Optional

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from flowrecord.models import Workflow
from flowrecord.storage import workflow_store as store
from flowrecord.ui import icons, theme
from flowrecord.ui.components import (
    HotkeyPill, PlayButton, SegmentedControl, Stepper, ToggleSwitch,
    _style, ask_text, pretty_hotkey,
)

# step.type -> (label, icon name, category)
_STEP_META = {
    "click": ("Left click", "click", "click"),
    "double_click": ("Double click", "click", "click"),
    "right_click": ("Right click", "click", "click"),
    "middle_click": ("Middle click", "click", "click"),
    "keypress": ("Key press", "keypress", "key"),
    "type_text": ("Type text", "type_text", "key"),
    "scroll": ("Scroll", "scroll", "scroll"),
    "delay": ("Wait", "delay", "wait"),
    "launch_app": ("Launch app", "launch_app", "app"),
}
_CAT_COLOR = {
    "click": "#2563EB",
    "key": "#6366F1",
    "scroll": "#0EA5E9",
    "wait": "#F59E0B",
    "app": "#22C55E",
}


def _soft(hex_color: str) -> str:
    c = QColor(hex_color)
    a = 0.22 if theme.manager.is_dark() else 0.14
    return f"rgba({c.red()},{c.green()},{c.blue()},{a})"


def _pretty_app(app: Optional[str]) -> str:
    if not app:
        return ""
    name = app.rsplit("\\", 1)[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name[:1].upper() + name[1:] if name else ""


def _subtitle(step) -> str:
    t = step.type
    if t in ("click", "double_click", "right_click", "middle_click"):
        app = _pretty_app(step.app_name)
        el = step.element_name
        if app and el:
            return f"{app} · {el}"
        if el:
            return el
        if step.x is not None:
            return f"({step.x}, {step.y})"
        return step.description or ""
    if t == "type_text":
        return f'"{step.text or ""}"'
    if t == "keypress":
        return (step.keys or step.description or "Key").replace("+", " + ")
    if t == "scroll":
        if step.description:
            return step.description
        return "Scroll up" if step.scroll_dy > 0 else "Scroll down"
    if t == "delay":
        return f"{step.delay_after:.1f} seconds"
    if t == "launch_app":
        return _pretty_app(step.app_name) or step.description or ""
    return step.description or ""


class _ClickFrame(QFrame):
    """A QFrame that emits ``clicked`` — used to make the hotkey field tappable."""

    clicked = pyqtSignal()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class _TimelineRow(QFrame):
    """A timeline step row with inline per-step delay + Smart Wait editing."""

    changed = pyqtSignal()

    def __init__(self, step, parent=None):
        super().__init__(parent)
        self._step = step
        # Smart Wait only makes sense for steps that target a UI element.
        self._sw_supported = bool(step.element_name)
        self.setObjectName("tlRow")
        label, icon_name, cat = _STEP_META.get(
            step.type, (step.type.replace("_", " ").title(), "click", "click")
        )
        color = _CAT_COLOR.get(cat, "#2563EB")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 7, 8, 7)
        lay.setSpacing(12)

        badge = QLabel()
        badge.setObjectName("tlBadge")
        badge.setFixedSize(38, 38)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setPixmap(icons.pixmap(icon_name, color, 17))
        badge.setStyleSheet(f"#tlBadge {{ background: {_soft(color)}; border-radius: 10px; }}")
        lay.addWidget(badge)

        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(label)
        title.setObjectName("tlTitle")
        sub = QLabel(_subtitle(step))
        sub.setObjectName("tlSub")
        sub.setToolTip(_subtitle(step))
        text.addWidget(title)
        text.addWidget(sub)
        lay.addLayout(text, 1)

        # ---- per-step delay ----
        self._delay = QDoubleSpinBox()
        self._delay.setRange(0.0, 30.0)
        self._delay.setSingleStep(0.1)
        self._delay.setDecimals(1)
        self._delay.setSuffix(" s")
        self._delay.setValue(step.delay_after)
        self._delay.setFixedWidth(74)
        self._delay.setToolTip("Delay before this step")
        self._delay.setCorrectionMode(QDoubleSpinBox.CorrectionMode.CorrectToNearestValue)
        self._delay.valueChanged.connect(self._on_delay)
        lay.addWidget(self._delay)

        # ---- Smart Wait toggle ----
        self._sw = QPushButton()
        self._sw.setObjectName("btnSmartWait")
        self._sw.setCheckable(True)
        self._sw.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sw.setFixedWidth(70)
        self._sw.setIconSize(QSize(14, 14))
        self._sw.clicked.connect(self._on_toggle_sw)
        lay.addWidget(self._sw)

        # ---- Smart Wait timeout + on-timeout (shown only when ON) ----
        self._sw_controls = QWidget()
        sc = QHBoxLayout(self._sw_controls)
        sc.setContentsMargins(0, 0, 0, 0)
        sc.setSpacing(6)
        self._sw_timeout = QDoubleSpinBox()
        self._sw_timeout.setRange(1.0, 300.0)
        self._sw_timeout.setDecimals(0)
        self._sw_timeout.setSingleStep(1.0)
        self._sw_timeout.setSuffix(" s")
        self._sw_timeout.setValue(step.smart_wait_timeout)
        self._sw_timeout.setFixedWidth(62)
        self._sw_timeout.setToolTip("Seconds to wait for the element (1–300)")
        self._sw_timeout.valueChanged.connect(self._on_timeout)
        self._sw_action = QComboBox()
        self._sw_action.addItem("Stop", "stop")
        self._sw_action.addItem("Skip", "skip")
        idx = self._sw_action.findData(step.smart_wait_on_timeout)
        self._sw_action.setCurrentIndex(idx if idx >= 0 else 0)
        self._sw_action.currentIndexChanged.connect(self._on_action)
        sc.addWidget(self._sw_timeout)
        sc.addWidget(self._sw_action)
        lay.addWidget(self._sw_controls)

        self.setStyleSheet(_style(
            "#tlRow { background: transparent; border-radius: 10px; }"
            "#tlRow:hover { background: @CONTROL@; }"
            "#tlTitle { color: @HEADING@; font-size: 14px; font-weight: 600;"
            " background: transparent; }"
            "#tlSub { color: @MUTED@; font-size: 13px; background: transparent; }"
        ))
        self._sync_sw()

    # ---- edits ----
    def _on_delay(self, val: float) -> None:
        self._step.delay_after = val
        self.changed.emit()

    def _on_toggle_sw(self) -> None:
        if not self._sw_supported:
            return
        self._step.smart_wait_enabled = self._sw.isChecked()
        self._sync_sw()
        self.changed.emit()

    def _on_timeout(self, val: float) -> None:
        self._step.smart_wait_timeout = float(val)
        self.changed.emit()

    def _on_action(self, _i: int) -> None:
        self._step.smart_wait_on_timeout = self._sw_action.currentData()
        self.changed.emit()

    def _sync_sw(self) -> None:
        if not self._sw_supported:
            self._sw.setEnabled(False)
            self._sw.setChecked(False)
            self._sw.setIcon(icons.icon("smart_wait", theme.manager.color("TEXT_DISABLED").name(), 14))
            self._sw.setText(" —")
            self._sw.setToolTip("Smart Wait needs a step that targets a UI element")
            self._sw_controls.setVisible(False)
            self._set_delay_sw(False)
            return
        on = self._step.smart_wait_enabled
        self._sw.setEnabled(True)
        self._sw.setChecked(on)
        col = "SUCCESS" if on else "TEXT_SECONDARY"
        self._sw.setIcon(icons.icon("smart_wait", theme.manager.color(col).name(), 14))
        self._sw.setText(" ON" if on else " OFF")
        self._sw.setToolTip(
            "Wait for the target element before running this step (delay ignored)"
        )
        self._sw_controls.setVisible(on)
        self._set_delay_sw(on)

    def _set_delay_sw(self, on: bool) -> None:
        """Gray the delay field and show '—' when Smart Wait is on, without
        disturbing the stored delay_after value."""
        self._delay.blockSignals(True)
        if on:
            self._delay.setSpecialValueText("—")
            self._delay.setValue(self._delay.minimum())
            self._delay.setEnabled(False)
        else:
            self._delay.setSpecialValueText("")
            self._delay.setValue(self._step.delay_after)
            self._delay.setEnabled(True)
        self._delay.blockSignals(False)


class RecordingDetailPage(QWidget):
    back_requested = pyqtSignal()
    play_requested = pyqtSignal(int, float, int, bool)  # id, speed, repeat, loop
    duplicate_requested = pyqtSignal(int)
    delete_requested = pyqtSignal(int)
    hotkey_changed = pyqtSignal(int, str)
    steps_changed = pyqtSignal()  # per-step delay / Smart Wait edited + saved

    def __init__(self, parent=None):
        super().__init__(parent)
        self._wf: Optional[Workflow] = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._save_steps)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 20)
        root.setSpacing(16)

        # ---- header ----
        header = QHBoxLayout()
        header.setSpacing(12)
        self._back = QPushButton("‹")
        self._back.setObjectName("backChevron")
        self._back.setFixedSize(34, 34)
        self._back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back.clicked.connect(self.back_requested.emit)

        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        self._title = QLabel("Recording")
        self._title.setObjectName("detailTitle")
        meta_row = QHBoxLayout()
        meta_row.setSpacing(8)
        self._hotkey_pill = HotkeyPill(None)
        self._meta = QLabel("")
        self._meta.setObjectName("detailMeta")
        meta_row.addWidget(self._hotkey_pill)
        meta_row.addWidget(self._meta)
        meta_row.addStretch(1)
        title_box.addWidget(self._title)
        title_box.addLayout(meta_row)

        self._play = PlayButton("solid", "Play")
        self._play.clicked.connect(self._emit_play)

        header.addWidget(self._back)
        header.addLayout(title_box, 1)
        header.addWidget(self._play)
        root.addLayout(header)

        # ---- body: timeline + playback panel ----
        body = QHBoxLayout()
        body.setSpacing(22)

        self._tl_scroll = QScrollArea()
        self._tl_scroll.setWidgetResizable(True)
        self._tl_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._tl_scroll.setStyleSheet("background: transparent;")
        self._tl_scroll.viewport().setStyleSheet("background: transparent;")
        body.addWidget(self._tl_scroll, 1)

        body.addWidget(self._build_panel())
        root.addLayout(body, 1)

        theme.manager.changed.connect(self._on_theme)
        self._apply()

    # ---- playback panel ----
    def _build_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(280)
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 4, 0, 0)
        v.setSpacing(10)

        v.addWidget(self._label("PLAYBACK", "sectionLabel"))
        v.addWidget(self._label("Speed", "fieldLabel"))
        self._speed = SegmentedControl(["0.5×", "1×", "2×"], index=1)
        v.addWidget(self._speed)

        v.addWidget(self._label("Repeat", "fieldLabel"))
        self._repeat = Stepper(1, 1, 50, "×")
        rep_row = QHBoxLayout()
        rep_row.setContentsMargins(0, 0, 0, 0)
        rep_row.addWidget(self._repeat)
        rep_row.addStretch(1)
        v.addLayout(rep_row)

        loop_row = QHBoxLayout()
        loop_box = QVBoxLayout()
        loop_box.setSpacing(1)
        loop_box.addWidget(self._label("Loop", "fieldLabel"))
        loop_box.addWidget(self._label("Repeat forever", "fieldSub"))
        self._loop = ToggleSwitch(False)
        loop_row.addLayout(loop_box)
        loop_row.addStretch(1)
        loop_row.addWidget(self._loop)
        v.addLayout(loop_row)

        v.addSpacing(8)
        v.addWidget(self._label("HOTKEY", "sectionLabel"))
        self._hk_field = _ClickFrame()
        self._hk_field.setObjectName("hkField")
        self._hk_field.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hk_field.clicked.connect(self._edit_hotkey)
        hk = QHBoxLayout(self._hk_field)
        hk.setContentsMargins(12, 9, 8, 9)
        self._hk_text = QLabel("Not set")
        self._hk_text.setObjectName("hkText")
        self._hk_edit = QPushButton("✎")
        self._hk_edit.setObjectName("hkEdit")
        self._hk_edit.setFixedSize(26, 26)
        self._hk_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hk_edit.clicked.connect(self._edit_hotkey)
        hk.addWidget(self._hk_text)
        hk.addStretch(1)
        hk.addWidget(self._hk_edit)
        v.addWidget(self._hk_field)

        v.addSpacing(10)
        self._dup = self._action_row("Duplicate", "edit", False)
        self._dup.clicked.connect(lambda: self._wf and self.duplicate_requested.emit(self._wf.id))
        self._del = self._action_row("Delete recording", "trash", True)
        self._del.clicked.connect(lambda: self._wf and self.delete_requested.emit(self._wf.id))
        v.addWidget(self._dup)
        v.addWidget(self._del)
        v.addStretch(1)
        return panel

    def _label(self, text: str, name: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName(name)
        return lbl

    def _action_row(self, text: str, icon_name: str, danger: bool) -> QPushButton:
        b = QPushButton("   " + text)
        b.setObjectName("dangerRow" if danger else "actionRow")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setProperty("iconName", icon_name)
        return b

    # ---- API ----
    def set_workflow(self, wf: Workflow) -> None:
        self._wf = wf
        self._title.setText(wf.name)
        self._meta.setText(f"{len(wf.steps)} steps · {wf.duration}")
        self._hotkey_pill.set_hotkey(wf.trigger.hotkey)
        self._set_hk_text(wf.trigger.hotkey)
        self._rebuild_timeline()

    def _rebuild_timeline(self) -> None:
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        box = QVBoxLayout(container)
        box.setContentsMargins(2, 2, 8, 8)
        box.setSpacing(2)
        if self._wf and self._wf.steps:
            for step in self._wf.steps:
                row = _TimelineRow(step)
                row.changed.connect(self._schedule_save)
                box.addWidget(row)
        else:
            empty = QLabel("No steps recorded.")
            empty.setStyleSheet(_style("QLabel { color: @MUTED@; padding: 30px; }"))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(empty)
        box.addStretch(1)
        self._tl_scroll.setWidget(container)

    # ---- per-step edits (delay / Smart Wait) ----
    def _schedule_save(self) -> None:
        # Refresh the duration in the meta line immediately; persist debounced.
        if self._wf:
            self._meta.setText(f"{len(self._wf.steps)} steps · {self._wf.duration}")
        self._save_timer.start()

    def _save_steps(self) -> None:
        if not self._wf or self._wf.id is None:
            return
        store.save_steps(self._wf.id, self._wf.steps)
        self.steps_changed.emit()

    # ---- intents ----
    def _speed_value(self) -> float:
        return {0: 0.5, 1: 1.0, 2: 2.0}.get(self._speed.current(), 1.0)

    def _emit_play(self) -> None:
        if self._wf:
            self.play_requested.emit(
                self._wf.id, self._speed_value(), self._repeat.value(),
                self._loop.isChecked(),
            )

    def _set_hk_text(self, hk: Optional[str]) -> None:
        self._hk_text.setText("+".join(pretty_hotkey(hk)) if hk else "Not set")

    def _edit_hotkey(self) -> None:
        if not self._wf:
            return
        text, ok = ask_text(
            self, "Assign hotkey",
            "Trigger hotkey (e.g. ctrl+alt+1) — leave blank to clear:",
            self._wf.trigger.hotkey or "",
        )
        if not ok:
            return
        hk = text.strip().lower() or None
        self._wf.trigger.hotkey = hk
        self._set_hk_text(hk)
        self._hotkey_pill.set_hotkey(hk)
        self.hotkey_changed.emit(self._wf.id, hk or "")

    def _on_theme(self) -> None:
        self._apply()
        if self._wf:
            self._rebuild_timeline()

    def _apply(self) -> None:
        sec = theme.manager.color("TEXT_SECONDARY").name()
        self.setStyleSheet(_style(
            "#backChevron { background: @CONTROL@; color: @HEADING@; border: 0;"
            " border-radius: 10px; font-size: 20px; font-weight: 600; }"
            "#backChevron:hover { background: @ELEVATED@; }"
            "#detailTitle { color: @HEADING@; font-size: 22px; font-weight: 700; }"
            "#detailMeta { color: @MUTED@; font-size: 13px; }"
            "#sectionLabel { color: @MUTED@; font-size: 11px; font-weight: 700;"
            " letter-spacing: 1px; padding-top: 4px; }"
            "#fieldLabel { color: @HEADING@; font-size: 14px; font-weight: 600; }"
            "#fieldSub { color: @MUTED@; font-size: 12px; }"
            "#hkField { background: @CONTROL@; border: 1px solid @HAIRLINE@;"
            " border-radius: 11px; }"
            "#hkField:hover { border: 1px solid @ACCENT@; }"
            "#hkText { color: @HEADING@; font-size: 13px; font-weight: 600;"
            " background: transparent; }"
            "#hkEdit { background: transparent; color: @ACCENT_ON_SOFT@; border: 0;"
            " border-radius: 7px; font-size: 14px; }"
            "#hkEdit:hover { background: @SURFACE@; }"
            "#actionRow { text-align: left; background: transparent; color: @HEADING@;"
            " border: 0; border-radius: 9px; padding: 9px 10px; font-size: 14px;"
            " font-weight: 500; }"
            "#actionRow:hover { background: @CONTROL@; }"
            "#dangerRow { text-align: left; background: transparent; color: @DANGER_TEXT@;"
            " border: 0; border-radius: 9px; padding: 9px 10px; font-size: 14px;"
            " font-weight: 600; }"
            "#dangerRow:hover { background: @DANGER_SOFT@; }"
        ))
        # action-row leading icons
        self._dup.setIcon(icons.icon("edit", sec, 16))
        self._del.setIcon(icons.icon("trash", theme.manager.color("DANGER").name(), 16))
