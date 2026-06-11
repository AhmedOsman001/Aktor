import logging
import sys
from enum import Enum
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer, QPoint, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QPainter, QPen, QFont, QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QFrame,
)

logger = logging.getLogger(__name__)

OVERLAY_WIDTH = 340
OVERLAY_HEIGHT = 60
BORDER_FLASH_MS = 300
BORDER_FLASH_COUNT = 3


class OverlayState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PLAYING = "playing"


class _RecordDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._on = True
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._toggle)
        self._color = QColor(220, 40, 40)

    def start_pulse(self):
        self._on = True
        self._timer.start(500)
        self.update()

    def stop_pulse(self):
        self._timer.stop()
        self._on = False
        self.update()

    def _toggle(self):
        self._on = not self._on
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self._color if self._on else self._color.darker(200)
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(1, 1, 12, 12)
        p.end()


class _BorderFlash(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background: transparent;")
        self._pen = QPen(QColor(255, 40, 40, 180), 4)
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
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(OVERLAY_WIDTH, OVERLAY_HEIGHT)
        self.setWindowOpacity(0.92)

        self._drag_pos: Optional[QPoint] = None

        self._main_layout = QHBoxLayout(self)
        self._main_layout.setContentsMargins(8, 4, 8, 4)
        self._main_layout.setSpacing(6)

        self._frame = QFrame()
        self._frame.setObjectName("pillFrame")
        self._frame.setStyleSheet("""
            #pillFrame {
                background-color: rgba(30, 30, 38, 230);
                border-radius: 16px;
                border: 1px solid rgba(80, 80, 100, 150);
            }
        """)
        self._inner_layout = QHBoxLayout(self._frame)
        self._inner_layout.setContentsMargins(12, 6, 12, 6)
        self._inner_layout.setSpacing(8)

        self._btn_record = QPushButton("● Record")
        self._btn_record.setObjectName("btnRecord")
        self._btn_record.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self._btn_stop = QPushButton("■ Stop")
        self._btn_stop.setObjectName("btnStop")
        self._btn_stop.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self._btn_pause = QPushButton("‖ Pause")
        self._btn_pause.setObjectName("btnPause")
        self._btn_pause.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self._btn_workflows = QPushButton("▶ Workflows")
        self._btn_workflows.setObjectName("btnWorkflows")
        self._btn_workflows.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        self._dot = _RecordDot()
        self._step_label = QLabel("")
        self._step_label.setStyleSheet("color: #cccccc; font-size: 12px;")

        self._progress_label = QLabel("")
        self._progress_label.setStyleSheet("color: #cccccc; font-size: 12px;")

        self._apply_button_styles()

        self._btn_record.clicked.connect(self.record_clicked.emit)
        self._btn_stop.clicked.connect(self.stop_clicked.emit)
        self._btn_pause.clicked.connect(self.pause_clicked.emit)
        self._btn_workflows.clicked.connect(self.workflows_clicked.emit)

        self._set_idle_layout()

        screen = QApplication.primaryScreen().geometry()
        self.move(
            (screen.width() - OVERLAY_WIDTH) // 2,
            screen.bottom() - OVERLAY_HEIGHT - 40,
        )

    def _apply_button_styles(self):
        common = """
            QPushButton {{
                background-color: {bg};
                color: {fg};
                border: none;
                border-radius: 10px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {hover};
            }}
        """
        self._btn_record.setStyleSheet(common.format(
            bg="rgba(220, 60, 60, 200)", fg="#ffffff", hover="rgba(240, 80, 80, 220)"
        ))
        self._btn_stop.setStyleSheet(common.format(
            bg="rgba(180, 60, 60, 200)", fg="#ffffff", hover="rgba(200, 80, 80, 220)"
        ))
        self._btn_pause.setStyleSheet(common.format(
            bg="rgba(60, 120, 200, 200)", fg="#ffffff", hover="rgba(80, 140, 220, 220)"
        ))
        self._btn_workflows.setStyleSheet(common.format(
            bg="rgba(60, 160, 80, 200)", fg="#ffffff", hover="rgba(80, 180, 100, 220)"
        ))

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
        self._step_label.hide()
        self._progress_label.hide()
        self._inner_layout.addWidget(self._btn_record)
        self._inner_layout.addWidget(self._btn_workflows)

    def _set_recording_layout(self):
        self._clear_layout()
        self._btn_record.hide()
        self._btn_workflows.hide()
        self._btn_stop.show()
        self._btn_pause.show()
        self._dot.show()
        self._step_label.show()
        self._progress_label.hide()
        self._inner_layout.addWidget(self._btn_stop)
        self._inner_layout.addWidget(self._btn_pause)
        self._inner_layout.addWidget(self._dot)
        self._inner_layout.addWidget(self._step_label)

    def _set_playing_layout(self):
        self._clear_layout()
        self._btn_record.hide()
        self._btn_workflows.hide()
        self._btn_stop.show()
        self._btn_pause.hide()
        self._dot.hide()
        self._step_label.hide()
        self._progress_label.show()
        self._inner_layout.addWidget(self._btn_stop)
        self._inner_layout.addWidget(self._progress_label)

    def set_state(self, state: OverlayState):
        if state == OverlayState.IDLE:
            self._set_idle_layout()
            self._dot.stop_pulse()
        elif state == OverlayState.RECORDING:
            self._set_recording_layout()
            self._dot.start_pulse()
        elif state == OverlayState.PLAYING:
            self._set_playing_layout()
            self._dot.stop_pulse()

    def set_step_count(self, n: int):
        self._step_label.setText(f"● {n} steps")

    def set_playback_progress(self, current: int, total: int, description: str):
        desc = description[:30] + "…" if len(description) > 30 else description
        self._progress_label.setText(f"▶ Step {current} / {total} — {desc}")

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

    def __init__(self):
        super().__init__()
        self._bar = _OverlayBar()
        self._flash = _BorderFlash()
        self._state = OverlayState.IDLE

        self._bar.record_clicked.connect(self._on_record)
        self._bar.stop_clicked.connect(self._on_stop)
        self._bar.pause_clicked.connect(self._on_pause)
        self._bar.workflows_clicked.connect(self._on_workflows)

    def show(self):
        self._bar.show()

    def hide(self):
        self._bar.hide()
        self._flash.hide()

    def set_state(self, state: OverlayState):
        self._state = state
        self._bar.set_state(state)
        if state == OverlayState.RECORDING:
            self._flash.flash()
        elif state == OverlayState.IDLE and self._state != OverlayState.IDLE:
            self._flash.flash()

    def set_step_count(self, n: int):
        self._bar.set_step_count(n)

    def set_playback_progress(self, current: int, total: int, description: str):
        self._bar.set_playback_progress(current, total, description)

    def _on_record(self):
        self.record_requested.emit()

    def _on_stop(self):
        self.stop_requested.emit()

    def _on_pause(self):
        self.pause_requested.emit()

    def _on_workflows(self):
        self.workflows_requested.emit()
