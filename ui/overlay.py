import logging
from enum import Enum
from typing import Optional

from PyQt6.QtCore import (
    Qt,
    QTimer,
    QThread,
    QPoint,
    QRect,
    QSize,
    pyqtSignal,
    QObject,
    QPropertyAnimation,
    QVariantAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
)
from PyQt6.QtGui import QColor, QPainter, QCursor, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFrame,
    QProgressBar,
    QGraphicsDropShadowEffect,
)

from flowrecord.ui import theme, icons, motion

logger = logging.getLogger(__name__)

# Pill metrics (the visible rounded surface)
PILL_WIDTH = 320
PILL_HEIGHT = 52
_SHADOW_PAD = 22  # space around the pill so the drop shadow is not clipped

OVERLAY_WIDTH = PILL_WIDTH + _SHADOW_PAD * 2
OVERLAY_HEIGHT = PILL_HEIGHT + _SHADOW_PAD * 2

BORDER_FLASH_MS = 280
BORDER_FLASH_COUNT = 3

_TARGET_OPACITY = 0.98


class OverlayState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PLAYING = "playing"
    SMART_WAITING = "smart_waiting"


def _with_alpha(color: QColor, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(alpha)
    return c


class _RecordDot(QWidget):
    """A breathing record indicator. Smoothly pulses via a value animation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(20, 20)
        self._level = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setKeyValueAt(0.5, 1.0)
        self._anim.setEndValue(0.0)
        self._anim.setDuration(1150)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._set_level)

    def start_pulse(self):
        self._anim.start()
        self.update()

    def stop_pulse(self):
        self._anim.stop()
        self._level = 0.0
        self.update()

    def _set_level(self, val):
        try:
            self._level = float(val)
        except (TypeError, ValueError):
            return
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        cx = self.width() / 2
        cy = self.height() / 2
        level = self._level

        # Outer halo
        halo_r = 6.0 + level * 4.5
        p.setBrush(_with_alpha(theme.RECORD_RED, int(35 + level * 90)))
        p.drawEllipse(QPoint(int(cx), int(cy)), int(halo_r), int(halo_r))

        # Core dot
        core_r = 4.6 + level * 1.6
        p.setBrush(_with_alpha(theme.RECORD_RED, int(150 + level * 105)))
        p.drawEllipse(QPoint(int(cx), int(cy)), int(core_r), int(core_r))
        p.end()


class _BorderFlash(QWidget):
    """Full-screen red edge flash used when recording starts/stops."""

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background: transparent;")
        self._pen = QPen(QColor(245, 70, 75, 200), 3)
        self._visible = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._count = 0

    def flash(self):
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        self._count = 0
        self._visible = True
        self.show()
        self.update()
        self._timer.start(BORDER_FLASH_MS)

    def _tick(self):
        self._count += 1
        if self._count >= BORDER_FLASH_COUNT * 2:
            self._timer.stop()
            self.hide()
            return
        self._visible = not self._visible
        self.update()

    def paintEvent(self, event):
        if not self._visible:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(self._pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(2, 2, self.width() - 4, self.height() - 4)
        p.end()


class _OverlayBar(QWidget):
    record_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    pause_clicked = pyqtSignal()
    workflows_clicked = pyqtSignal()

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(OVERLAY_WIDTH, OVERLAY_HEIGHT)
        self.setWindowOpacity(_TARGET_OPACITY)
        self._drag_pos: Optional[QPoint] = None

        self._main_layout = QHBoxLayout(self)
        self._main_layout.setContentsMargins(
            _SHADOW_PAD, _SHADOW_PAD, _SHADOW_PAD, _SHADOW_PAD
        )
        self._main_layout.setSpacing(0)

        self._frame = QFrame()
        self._frame.setObjectName("pillFrame")

        # Elevation / glow (color shifts per state — see _apply_state_glow)
        self._shadow = QGraphicsDropShadowEffect(self._frame)
        self._shadow.setBlurRadius(28)
        self._shadow.setColor(QColor(0, 0, 0, 180))
        self._shadow.setOffset(0, 9)
        self._frame.setGraphicsEffect(self._shadow)
        self._state = OverlayState.IDLE

        self._inner_layout = QHBoxLayout(self._frame)
        self._inner_layout.setContentsMargins(12, 7, 12, 7)
        self._inner_layout.setSpacing(8)

        self._btn_record = QPushButton("Record")
        self._btn_record.setObjectName("btnRecord")
        self._btn_record.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self._btn_stop = QPushButton("Stop")
        self._btn_stop.setObjectName("btnStop")
        self._btn_stop.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self._btn_pause = QPushButton("Pause")
        self._btn_pause.setObjectName("btnPause")
        self._btn_pause.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self._btn_workflows = QPushButton("Workflows")
        self._btn_workflows.setObjectName("btnWorkflows")
        self._btn_workflows.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self._dot = _RecordDot()
        self._sep = QFrame()
        self._sep.setFixedHeight(18)
        self._sep.setFixedWidth(1)

        self._step_label = QLabel("")
        self._progress_label = QLabel("")

        # Smart Wait state: a status label + a thin progress bar.
        self._smart_wait_label = QLabel("")

        self._smart_wait_bar = QProgressBar()
        self._smart_wait_bar.setTextVisible(False)
        self._smart_wait_bar.setFixedHeight(6)
        self._smart_wait_bar.setFixedWidth(90)
        self._smart_wait_bar.setRange(0, 100)
        self._smart_wait_bar.setValue(0)

        self._btn_record.clicked.connect(self.record_clicked.emit)
        self._btn_stop.clicked.connect(self.stop_clicked.emit)
        self._btn_pause.clicked.connect(self.pause_clicked.emit)
        self._btn_workflows.clicked.connect(self.workflows_clicked.emit)

        self._main_layout.addWidget(self._frame)

        self._set_idle_layout()

        screen = QApplication.primaryScreen().geometry()
        self.move(
            (screen.width() - OVERLAY_WIDTH) // 2,
            screen.bottom() - OVERLAY_HEIGHT - 36,
        )

        self._apply_theme()
        theme.manager.changed.connect(self._apply_theme)

    # ---- theming ----
    def _apply_theme(self):
        toks = theme.manager.tokens()
        self._frame.setStyleSheet(theme.manager.qss_overlay())

        # Vector icons (retinted for the active theme).
        white = "#ffffff"
        prim = toks["TEXT_PRIMARY"]
        for btn in (self._btn_record, self._btn_stop, self._btn_pause, self._btn_workflows):
            btn.setIconSize(QSize(15, 15))
        self._btn_record.setIcon(icons.icon("record", white, 14))
        self._btn_stop.setIcon(icons.icon("stop", white, 14))
        self._btn_pause.setIcon(icons.icon("pause", prim, 14))
        self._btn_workflows.setIcon(icons.icon("workflows", prim, 16))
        self._sep.setStyleSheet(
            f"background-color: {toks['GLASS_HI_STRONG']}; border: none;"
        )
        label_css = (
            f"color: {toks['TEXT_SECONDARY']}; font-size: 12px; font-weight: 600;"
        )
        self._step_label.setStyleSheet(label_css)
        self._progress_label.setStyleSheet(label_css)
        self._smart_wait_label.setStyleSheet(label_css)
        self._smart_wait_bar.setStyleSheet(
            "QProgressBar { background-color: " + toks["GLASS_HI_STRONG"]
            + "; border: none; border-radius: 3px; }"
            " QProgressBar::chunk { background-color: " + toks["ACCENT"]
            + "; border-radius: 3px; }"
        )
        self._apply_state_glow(self._state)

    # ---- layout state ----
    def _clear_layout(self):
        while self._inner_layout.count():
            item = self._inner_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(self._frame)

    def _set_idle_layout(self):
        self._clear_layout()
        self._btn_record.show()
        self._btn_workflows.show()
        self._btn_stop.hide()
        self._btn_pause.hide()
        self._dot.hide()
        self._sep.hide()
        self._step_label.hide()
        self._progress_label.hide()
        self._smart_wait_label.hide()
        self._smart_wait_bar.hide()
        self._inner_layout.addWidget(self._btn_record)
        self._inner_layout.addWidget(self._btn_workflows)
        self._inner_layout.addStretch(1)

    def _set_recording_layout(self):
        self._clear_layout()
        self._btn_record.hide()
        self._btn_workflows.hide()
        self._btn_stop.show()
        self._btn_pause.show()
        self._dot.show()
        self._sep.show()
        self._step_label.show()
        self._progress_label.hide()
        self._smart_wait_label.hide()
        self._smart_wait_bar.hide()
        self._inner_layout.addWidget(self._btn_stop)
        self._inner_layout.addWidget(self._btn_pause)
        self._inner_layout.addWidget(self._sep)
        self._inner_layout.addWidget(self._dot)
        self._inner_layout.addWidget(self._step_label)
        self._inner_layout.addStretch(1)

    def _set_playing_layout(self):
        self._clear_layout()
        self._btn_record.hide()
        self._btn_workflows.hide()
        self._btn_stop.show()
        self._btn_pause.hide()
        self._dot.hide()
        self._sep.hide()
        self._step_label.hide()
        self._progress_label.show()
        self._smart_wait_label.hide()
        self._smart_wait_bar.hide()
        self._inner_layout.addWidget(self._btn_stop)
        self._inner_layout.addWidget(self._sep)
        self._inner_layout.addWidget(self._progress_label)
        self._inner_layout.addStretch(1)

    def _set_smart_waiting_layout(self):
        self._clear_layout()
        self._btn_record.hide()
        self._btn_workflows.hide()
        self._btn_stop.show()
        self._btn_pause.hide()
        self._dot.hide()
        self._sep.show()
        self._step_label.hide()
        self._progress_label.hide()
        self._smart_wait_label.show()
        self._smart_wait_bar.show()
        self._inner_layout.addWidget(self._btn_stop)
        self._inner_layout.addWidget(self._sep)
        self._inner_layout.addWidget(self._smart_wait_label, 1)
        self._inner_layout.addWidget(self._smart_wait_bar)

    def set_state(self, state: OverlayState):
        self._state = state
        if state == OverlayState.IDLE:
            self._set_idle_layout()
            self._dot.stop_pulse()
        elif state == OverlayState.RECORDING:
            self._set_recording_layout()
            self._dot.start_pulse()
        elif state == OverlayState.PLAYING:
            self._set_playing_layout()
            self._dot.stop_pulse()
        elif state == OverlayState.SMART_WAITING:
            self._set_smart_waiting_layout()
            self._dot.stop_pulse()
        self._apply_state_glow(state)

    def _apply_state_glow(self, state: OverlayState):
        if state == OverlayState.RECORDING:
            motion.set_glow(self._shadow, theme.RECORD_RED.name(), blur=46, alpha=185)
        elif state in (OverlayState.PLAYING, OverlayState.SMART_WAITING):
            motion.set_glow(self._shadow, theme.manager.color("ACCENT").name(), blur=44, alpha=165)
        else:
            motion.set_glow(self._shadow, "#000000", blur=28, alpha=180, dy=9)

    def set_step_count(self, n: int):
        self._step_label.setText(f"{n} steps")

    def set_playback_progress(self, current: int, total: int, description: str):
        desc = description[:30] + "…" if len(description) > 30 else description
        self._progress_label.setText(f"Step {current} / {total}  ·  {desc}")

    @staticmethod
    def _short_name(element_name: str) -> str:
        name = element_name or "element"
        return name[:14] + "…" if len(name) > 15 else name

    def show_smart_wait(self, element_name: str, elapsed: int, timeout: int):
        self._smart_wait_label.setText(
            f"⏳ Waiting for '{self._short_name(element_name)}'…  {elapsed}s / {timeout}s"
        )
        self._smart_wait_bar.setRange(0, max(1, timeout))
        self._smart_wait_bar.setValue(min(elapsed, timeout))

    def show_smart_wait_found(self, element_name: str, elapsed: int):
        self._smart_wait_label.setText(
            f"✅ Found '{self._short_name(element_name)}' after {elapsed}s"
        )
        self._smart_wait_bar.setValue(self._smart_wait_bar.maximum())

    def show_smart_wait_timeout(self, element_name: str, timeout: int):
        self._smart_wait_label.setText(
            f"⚠️ '{self._short_name(element_name)}' not found after {timeout}s"
        )

    # ---- entrance animation ----
    def _animate_in(self):
        self.setWindowOpacity(0.0)
        final_pos = self.pos()
        start_pos = QPoint(final_pos.x(), final_pos.y() + 10)

        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(190)
        fade.setStartValue(0.0)
        fade.setEndValue(_TARGET_OPACITY)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(220)
        slide.setStartValue(start_pos)
        slide.setEndValue(final_pos)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(fade)
        group.addAnimation(slide)
        group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)

    def showEvent(self, event):
        super().showEvent(event)
        self._animate_in()

    # ---- dragging ----
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


class OverlayController(QObject):
    record_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    workflows_requested = pyqtSignal()

    # Internal: marshal a callable onto the GUI thread. The player invokes the
    # overlay from its worker thread; touching Qt widgets there crashes/hangs Qt,
    # so all widget mutations are funneled through this queued signal.
    _invoke = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._bar = _OverlayBar()
        self._flash = _BorderFlash()
        self._state = OverlayState.IDLE

        self._invoke.connect(self._run_on_gui)

        self._bar.record_clicked.connect(self._on_record)
        self._bar.stop_clicked.connect(self._on_stop)
        self._bar.pause_clicked.connect(self._on_pause)
        self._bar.workflows_clicked.connect(self._on_workflows)

    def _run_on_gui(self, fn):
        try:
            fn()
        except Exception:
            logger.debug("overlay GUI callable failed", exc_info=True)

    def _dispatch(self, fn):
        """Run ``fn`` on the GUI thread — directly if already there, otherwise
        queued via the _invoke signal."""
        if QThread.currentThread() is self.thread():
            fn()
        else:
            self._invoke.emit(fn)

    def show(self):
        self._dispatch(self._bar.show)

    def hide(self):
        def _do():
            self._bar.hide()
            self._flash.hide()
        self._dispatch(_do)

    def set_state(self, state: OverlayState):
        self._dispatch(lambda: self._do_set_state(state))

    def _do_set_state(self, state: OverlayState):
        logger.debug("overlay state -> %s", state.value)
        prev = self._state
        self._state = state
        self._bar.set_state(state)
        if state == OverlayState.RECORDING:
            self._flash.flash()
        elif prev == OverlayState.RECORDING and state == OverlayState.IDLE:
            self._flash.flash()

    def set_step_count(self, n: int):
        self._dispatch(lambda: self._bar.set_step_count(n))

    def set_playback_progress(self, current: int, total: int, description: str):
        def _do():
            logger.debug("overlay progress=%d/%d %s", current, total, description)
            # A normal step update means smart waiting is over — return to playing.
            if self._state == OverlayState.SMART_WAITING:
                self._do_set_state(OverlayState.PLAYING)
            self._bar.set_playback_progress(current, total, description)
        self._dispatch(_do)

    def show_smart_wait(self, element_name: str, elapsed: int, timeout: int):
        def _do():
            if self._state != OverlayState.SMART_WAITING:
                self._do_set_state(OverlayState.SMART_WAITING)
            self._bar.show_smart_wait(element_name, elapsed, timeout)
        self._dispatch(_do)

    def show_smart_wait_found(self, element_name: str, elapsed: int):
        def _do():
            if self._state != OverlayState.SMART_WAITING:
                self._do_set_state(OverlayState.SMART_WAITING)
            self._bar.show_smart_wait_found(element_name, elapsed)
        self._dispatch(_do)

    def show_smart_wait_timeout(self, element_name: str, timeout: int):
        def _do():
            if self._state != OverlayState.SMART_WAITING:
                self._do_set_state(OverlayState.SMART_WAITING)
            self._bar.show_smart_wait_timeout(element_name, timeout)
        self._dispatch(_do)

    def _on_record(self):
        self.record_requested.emit()

    def _on_stop(self):
        self.stop_requested.emit()

    def _on_pause(self):
        self.pause_requested.emit()

    def _on_workflows(self):
        self.workflows_requested.emit()
