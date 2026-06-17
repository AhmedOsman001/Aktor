"""Live activity-log panel + a logging handler that bridges Python's
``logging`` module into Qt signals.

The handler captures everything emitted under the ``flowrecord`` logger and
buffers it, so the panel can show history even when it hasn't been opened yet.
"""

import collections
import html
import logging

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QTextCharFormat, QTextCursor, QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from flowrecord.ui import theme, icons

_MAX_BUFFER = 2000

_LEVEL_COLORS = {
    "DEBUG": theme.TEXT_MUTED,
    "INFO": "#c6c6d0",
    "WARNING": theme.WARNING,
    "ERROR": theme.DANGER,
    "CRITICAL": theme.DANGER,
}

_LEVEL_VALUE = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_COMBO_TO_LEVEL = {
    "Debug": logging.DEBUG,
    "Info": logging.INFO,
    "Warning": logging.WARNING,
    "Error": logging.ERROR,
}


class _LogBridge(QObject):
    """Signal bridge so worker threads can safely push records to the UI."""

    record_posted = pyqtSignal(str, str)  # (level_name, formatted_line)


class QtLogHandler(logging.Handler):
    """A logging handler that buffers records and emits them as Qt signals."""

    _instance: "QtLogHandler | None" = None

    def __init__(self):
        super().__init__()
        self.setLevel(logging.DEBUG)
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"
            )
        )
        self._bridge = _LogBridge()
        self._buffer: collections.deque[tuple[str, str]] = collections.deque(
            maxlen=_MAX_BUFFER
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
            self._buffer.append((record.levelname, text))
            self._bridge.record_posted.emit(record.levelname, text)
        except Exception:
            self.handleError(record)

    @classmethod
    def instance(cls) -> "QtLogHandler":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


def install(level: int = logging.DEBUG) -> QtLogHandler:
    """Attach the handler to the ``flowrecord`` logger (idempotent)."""
    h = QtLogHandler.instance()
    h.setLevel(level)
    lg = logging.getLogger("flowrecord")
    if h not in lg.handlers:
        lg.addHandler(h)
    return h


class LogPanel(QWidget):
    """Bottom-docked console showing the live log stream."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self._min_level = logging.INFO

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._frame = QFrame()
        self._frame.setObjectName("panelFrame")
        f = QVBoxLayout(self._frame)
        f.setContentsMargins(10, 8, 10, 10)
        f.setSpacing(7)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("\u25CF  Activity")
        title.setObjectName("panelTitle")
        hint = QLabel("live log stream")
        hint.setObjectName("panelHint")

        self._levels = QComboBox()
        self._levels.addItems(["Info", "Debug", "Warning", "Error"])
        self._levels.setCurrentText("Info")
        self._levels.setCursor(Qt.CursorShape.PointingHandCursor)
        self._levels.currentTextChanged.connect(self._on_level)

        self._btn_clear = QPushButton("Clear")
        self._btn_clear.setObjectName("btnClear")
        self._btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_clear.clicked.connect(self._on_clear)

        self._btn_collapse = QPushButton()
        self._btn_collapse.setObjectName("btnCollapse")
        self._btn_collapse.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_collapse.setToolTip("Collapse / expand")
        self._btn_collapse.setFixedWidth(32)
        self._btn_collapse.clicked.connect(self.toggle_collapse)
        self._update_collapse_icon()

        header.addWidget(title)
        header.addWidget(hint)
        header.addStretch(1)
        header.addWidget(self._levels)
        header.addWidget(self._btn_clear)
        header.addWidget(self._btn_collapse)

        self._view = QTextEdit()
        self._view.setObjectName("logView")
        self._view.setReadOnly(True)
        self._view.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self._view.setPlaceholderText("Nothing logged yet \u2014 start recording or run a workflow.")
        doc = self._view.document()
        doc.setDocumentMargin(0)

        f.addLayout(header)
        f.addWidget(self._view, 1)

        outer.addWidget(self._frame)

        # Subscribe + replay buffered history.
        handler = QtLogHandler.instance()
        handler._bridge.record_posted.connect(self._on_record)
        self._render_all()

    # ---- public API ----
    def toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed
        self._view.setVisible(not self._collapsed)
        self._update_collapse_icon()

    def retint(self) -> None:
        """Re-tint icons for the active theme (called by the host on theme change)."""
        self._update_collapse_icon()

    def _update_collapse_icon(self) -> None:
        name = "chevron_right" if self._collapsed else "chevron_down"
        color = theme.manager.color("TEXT_SECONDARY").name()
        self._btn_collapse.setIcon(icons.icon(name, color, 14))

    # ---- slots ----
    def _on_level(self, text: str) -> None:
        self._min_level = _COMBO_TO_LEVEL.get(text, logging.INFO)
        self._render_all()

    def _on_clear(self) -> None:
        self._view.clear()

    def _on_record(self, level: str, text: str) -> None:
        if _LEVEL_VALUE.get(level, 20) < self._min_level:
            return
        self._insert(level, text, stick=True)

    # ---- helpers ----
    def _render_all(self) -> None:
        self._view.clear()
        handler = QtLogHandler.instance()
        sb = self._view.verticalScrollBar()
        for level, text in handler._buffer:
            if _LEVEL_VALUE.get(level, 20) >= self._min_level:
                self._insert(level, text, stick=False)
        sb.setValue(sb.maximum())

    def _insert(self, level: str, text: str, stick: bool) -> None:
        sb = self._view.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 8

        cursor = self._view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        color = QColor(_LEVEL_COLORS.get(level, "#c6c6d0"))
        bold = level in ("ERROR", "CRITICAL")
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        fmt.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        cursor.setCharFormat(fmt)
        cursor.insertText(text + "\n")

        if stick and (at_bottom or self._view.document().characterCount() <= 1):
            sb.setValue(sb.maximum())
