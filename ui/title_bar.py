"""Frameless dialog base + custom title bar for FlowRecord.

``FramelessDialog`` is an opaque borderless ``QDialog`` (so existing code that
relies on ``finished`` / ``show`` keeps working). Rounded corners + a minimal
drop shadow come from Windows DWM (``win_effects.apply_window_chrome``); resizing
is via the corner ``QSizeGrip``.

The title bar carries the app logo + title on the left and crisp vector
min / maximize / close buttons on the right, with a hairline separator beneath
it — matching the reference design.
"""

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QSizeGrip, QVBoxLayout,
    QWidget,
)

from flowrecord.ui import icons, theme, win_effects
from flowrecord.ui.components import Logo, _style

_TITLEBAR_QSS = """
#winBtn { background: transparent; border: 0; border-radius: 7px; }
#winBtn:hover { background: @CONTROL@; }
#winClose { background: transparent; border: 0; border-radius: 7px; }
#winClose:hover { background: @DANGER@; }
#winTitle { color: @HEADING@; font-size: 13px; font-weight: 700; background: transparent; }
"""


class _WinButton(QPushButton):
    """A caption button rendered from a crisp vector icon (close turns white on
    its red hover)."""

    def __init__(self, icon_name: str, danger: bool = False, parent=None):
        super().__init__(parent)
        self._name = icon_name
        self._danger = danger
        self._hover = False
        self.setObjectName("winClose" if danger else "winBtn")
        self.setFixedSize(42, 30)
        self.setIconSize(QSize(15, 15))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        theme.manager.changed.connect(self._retint)
        self._retint()

    def set_icon_name(self, name: str) -> None:
        if name != self._name:
            self._name = name
            self._retint()

    def _retint(self) -> None:
        color = "#ffffff" if (self._hover and self._danger) \
            else theme.manager.color("TEXT_SECONDARY").name()
        self.setIcon(icons.icon(self._name, color, 15))

    def enterEvent(self, e):
        self._hover = True
        self._retint()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._retint()
        super().leaveEvent(e)


class TitleBar(QWidget):
    def __init__(self, window: "FramelessDialog", title: str = "FlowRecord",
                 show_logo: bool = True, parent=None):
        super().__init__(parent)
        self._win = window
        self._drag_pos: QPoint | None = None
        self.setFixedHeight(46)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 8, 8)
        lay.setSpacing(9)
        if show_logo:
            lay.addWidget(Logo(22))
        self._title = QLabel(title)
        self._title.setObjectName("winTitle")
        self._title.setVisible(bool(title))
        lay.addWidget(self._title)
        lay.addStretch(1)

        self._btn_min = _WinButton("minimize")
        self._btn_max = _WinButton("maximize")
        self._btn_close = _WinButton("close", danger=True)
        self._btn_min.clicked.connect(self._win.showMinimized)
        self._btn_max.clicked.connect(self._win.toggle_max)
        self._btn_close.clicked.connect(self._win.close)
        lay.addWidget(self._btn_min)
        lay.addWidget(self._btn_max)
        lay.addWidget(self._btn_close)

        theme.manager.changed.connect(self._apply)
        self._apply()

    def set_title(self, title: str) -> None:
        self._title.setText(title)
        self._title.setVisible(bool(title))

    def set_maximized(self, maximized: bool) -> None:
        self._btn_max.set_icon_name("restore" if maximized else "maximize")

    def _apply(self) -> None:
        self.setStyleSheet(_style(_TITLEBAR_QSS))

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            if self._win.isMaximized():
                self._win.toggle_max()
                self._drag_pos = e.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            self._win.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, _e):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, _e):
        self._win.toggle_max()


class FramelessDialog(QDialog):
    def __init__(self, title: str = "FlowRecord", show_logo: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)

        self._card = QFrame()
        self._card.setObjectName("winCard")
        self._outer.addWidget(self._card)

        card_lay = QVBoxLayout(self._card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        card_lay.setSpacing(0)

        self.title_bar = TitleBar(self, title, show_logo)
        card_lay.addWidget(self.title_bar)

        self._divider = QFrame()
        self._divider.setObjectName("titleDivider")
        self._divider.setFixedHeight(1)
        card_lay.addWidget(self._divider)

        self._content = QWidget()
        self._content.setObjectName("winContent")
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(0, 0, 0, 0)
        self._content_lay.setSpacing(0)
        card_lay.addWidget(self._content, 1)

        self._grip = QSizeGrip(self)
        self._grip.resize(16, 16)

        theme.manager.changed.connect(self._apply_chrome)
        self._apply_chrome()

    def content_layout(self) -> QVBoxLayout:
        return self._content_lay

    def set_title(self, title: str) -> None:
        self.title_bar.set_title(title)

    def toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _apply_chrome(self) -> None:
        maximized = self.isMaximized()
        radius = 0 if maximized else 10
        self._card.setStyleSheet(_style(
            "#winCard { background: @CANVAS@; border: 0; border-radius: %dpx; }"
            "#winContent { background: transparent; }"
            "#titleDivider { background: @HAIRLINE@; }" % radius
        ))
        self.title_bar.set_maximized(maximized)
        try:
            win_effects.apply_window_chrome(self, theme.manager.is_dark())
        except Exception:
            pass

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.WindowStateChange:
            self._apply_chrome()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._grip.move(self.width() - 16, self.height() - 16)
        self._grip.raise_()

    def showEvent(self, e):
        super().showEvent(e)
        self._apply_chrome()
