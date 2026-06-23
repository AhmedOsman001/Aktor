"""Reusable, theme-aware widgets for FlowRecord's iOS-style UI.

Ported from the Aktor design and adapted to FlowRecord's ``theme.manager`` API
and ``Workflow``/``ActionStep`` model. Most widgets style themselves from QSS
templates using ``@TOKEN@`` markers (substituted by :func:`_style`) and re-apply
on ``theme.manager.changed``. ToggleSwitch, RecordButton, and Logo are
custom-painted.
"""

from datetime import datetime
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve, QPointF, QRect, QRectF, QSize, Qt, QTimer, Property,
    Signal, QPropertyAnimation,
)
from PySide6.QtGui import (
    QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton, QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from flowrecord.ui import icons, motion, theme

# Clean dialog chrome: title + close only (no grayed-out min/max from the
# system menu that clash with the app's custom look).
_DIALOG_FLAGS = (
    Qt.WindowType.Dialog
    | Qt.WindowType.CustomizeWindowHint
    | Qt.WindowType.WindowTitleHint
    | Qt.WindowType.WindowCloseButtonHint
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _style(template: str) -> str:
    """Replace @TOKEN@ markers in a QSS template with active theme values."""
    out = template
    for key, val in theme.manager.tokens().items():
        out = out.replace("@" + key + "@", val)
    return out


def ask_text(parent, title: str, label: str, text: str = "") -> tuple[str, bool]:
    """A themed text-input prompt with clean chrome (title + close only)."""
    dlg = QInputDialog(parent)
    dlg.setWindowFlags(_DIALOG_FLAGS)
    dlg.setWindowTitle(title)
    dlg.setLabelText(label)
    dlg.setInputMode(QInputDialog.InputMode.TextInput)
    dlg.setTextValue(text)
    accepted = dlg.exec() == QDialog.DialogCode.Accepted
    return dlg.textValue(), accepted


def confirm(parent, title: str, text: str, yes: str = "Yes", no: str = "No") -> bool:
    """A themed yes/no confirmation with clean chrome."""
    box = QMessageBox(parent)
    box.setWindowFlags(_DIALOG_FLAGS)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.NoIcon)
    yes_btn = box.addButton(yes, QMessageBox.ButtonRole.AcceptRole)
    box.addButton(no, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(yes_btn)
    box.exec()
    return box.clickedButton() is yes_btn


def info(parent, title: str, text: str) -> None:
    """A themed information dialog with clean chrome."""
    box = QMessageBox(parent)
    box.setWindowFlags(_DIALOG_FLAGS)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.NoIcon)
    box.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def relative_time(dt: Optional[datetime]) -> str:
    if not dt:
        return "never run"
    secs = (datetime.now() - dt).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    days = int(secs // 86400)
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days}d ago"
    return dt.strftime("%b %d")


def pretty_hotkey(hotkey: Optional[str]) -> list[str]:
    if not hotkey:
        return []
    parts = []
    for raw in hotkey.replace(" ", "").split("+"):
        if not raw:
            continue
        parts.append(raw.upper() if len(raw) == 1 else raw.capitalize())
    return parts


def _drop_shadow(widget, color: QColor, blur: int, dy: int) -> QGraphicsDropShadowEffect:
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(0, dy)
    eff.setColor(color)
    widget.setGraphicsEffect(eff)
    return eff


# ===========================================================================
# Logo — blue rounded square with white broadcast / record-waves glyph
# ===========================================================================
class Logo(QWidget):
    def __init__(self, size: int = 30, parent=None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)
        theme.manager.changed.connect(self.update)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = self._size
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, s, s), s * 0.30, s * 0.30)
        # Leaf-green gradient squircle (lighter top-left -> accent bottom-right).
        acc = theme.manager.color("ACCENT")
        grad = QLinearGradient(0, 0, s, s)
        grad.setColorAt(0.0, acc.lighter(122))
        grad.setColorAt(1.0, acc)
        p.fillPath(path, QBrush(grad))

        # A white leaf with green veins — the nature signature. Side veins only
        # at larger sizes so the small title-bar mark stays crisp.
        icons.draw_leaf(
            p, s / 2, s / 2, length=s * 0.62,
            fill=QColor("#ffffff"), vein=acc.darker(108),
            detail=s >= 40, vein_w=max(1.2, s * 0.05),
        )
        p.end()


# ===========================================================================
# Buttons
# ===========================================================================
_PILL_BASE = """
QPushButton {
    border: 1px solid transparent; border-radius: 11px;
    padding: 10px 20px; font-size: 14px; font-weight: 600;
}
QPushButton:disabled { background: @DISABLED_FILL@; color: @DISABLED_TEXT@; }
"""
_PILL_VARIANTS = {
    "primary": """
QPushButton { background: @ACCENT@; color: @ON_ACCENT@; }
QPushButton:hover { background: @ACCENT_HOVER@; }
QPushButton:pressed { background: @ACCENT_PRESSED@; }
""",
    "secondary": """
QPushButton { background: @CONTROL@; color: @HEADING@; }
QPushButton:hover { background: @ELEVATED@; }
""",
    "destructive": """
QPushButton { background: @DANGER@; color: #ffffff; }
QPushButton:hover { background: @DANGER_HOVER@; }
""",
    "ghost": """
QPushButton { background: transparent; color: @MUTED@; }
QPushButton:hover { background: @CONTROL@; color: @HEADING@; }
""",
}


class PillButton(QPushButton):
    def __init__(self, text: str = "", variant: str = "primary", parent=None):
        super().__init__(text, parent)
        self._variant = variant
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        theme.manager.changed.connect(self._apply)
        self._apply()

    def set_variant(self, variant: str) -> None:
        self._variant = variant
        self._apply()

    def _apply(self) -> None:
        tpl = _PILL_BASE + _PILL_VARIANTS.get(self._variant, _PILL_VARIANTS["primary"])
        self.setStyleSheet(_style(tpl))


_PLAY_SOLID = """
QPushButton {
    background: @ACCENT@; color: #ffffff; border: 0; border-radius: 11px;
    padding: 11px 18px; font-size: 14px; font-weight: 600;
}
QPushButton:hover { background: @ACCENT_HOVER@; }
QPushButton:disabled { background: @DISABLED_FILL@; color: @DISABLED_TEXT@; }
"""
_PLAY_SOFT = """
QPushButton {
    background: @ACCENT_SOFT_2@; color: @ACCENT_ON_SOFT@; border: 0;
    border-radius: 11px; padding: 10px 18px; font-size: 14px; font-weight: 600;
}
QPushButton:hover { background: @ACCENT_SOFT@; }
"""


class PlayButton(QPushButton):
    def __init__(self, variant: str = "solid", text: str = "Play",
                 full_width: bool = False, parent=None):
        super().__init__(f"▶  {text}", parent)
        self._variant = variant
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if full_width:
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        theme.manager.changed.connect(self._apply)
        self._apply()

    def set_variant(self, variant: str) -> None:
        self._variant = variant
        self._apply()

    def _apply(self) -> None:
        self.setStyleSheet(_style(_PLAY_SOLID if self._variant == "solid" else _PLAY_SOFT))


class RecordButton(QAbstractButton):
    """Circular ~48px red-gradient record button with a white dot + red glow."""

    def __init__(self, diameter: int = 48, parent=None):
        super().__init__(parent)
        self._d = diameter
        self.setFixedSize(diameter, diameter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        _drop_shadow(self, QColor(244, 63, 94, 150), blur=26, dy=6)

    def sizeHint(self) -> QSize:
        return QSize(self._d, self._d)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = self._d
        grad = QLinearGradient(0, 0, d, d)
        grad.setColorAt(0, QColor("#FF5A5A"))
        grad.setColorAt(1, QColor("#F43F5E"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawEllipse(2, 2, d - 4, d - 4)
        p.setBrush(QColor("#ffffff"))
        r = d * 0.16
        p.drawEllipse(QPointF(d / 2, d / 2), r, r)
        p.end()


class RecordPill(QPushButton):
    """Floating red record capsule — a big white dot + bold 'Record', soft shadow.

    The corner radius is kept at half the button's height so it's always a true
    capsule regardless of the font / icon size.
    """

    def __init__(self, text: str = "Record", parent=None):
        super().__init__("  " + text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(16, 16))   # the white dot, as a vector circle
        self._radius = 22
        _drop_shadow(self, QColor(0, 0, 0, 55), blur=22, dy=6)
        theme.manager.changed.connect(self._apply)
        self._apply()

    def _apply(self) -> None:
        rec = theme.manager.color("RECORD_BOTTOM").name()
        self.setStyleSheet(
            f"QPushButton {{ background: {rec}; color: #ffffff; border: 0;"
            f" border-radius: {self._radius}px; padding: 13px 26px;"
            f" font-size: 14px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: #e11d48; }}"
            f"QPushButton:pressed {{ background: #be123c; }}"
        )
        self.setIcon(icons.icon("record", "#ffffff", 16))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        r = max(8, self.height() // 2)
        if r != self._radius:
            self._radius = r
            self._apply()


# ===========================================================================
# ToggleSwitch — custom-painted, animated
# ===========================================================================
class ToggleSwitch(QAbstractButton):
    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(50, 30)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"knob_pos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        theme.manager.changed.connect(self.update)

    def sizeHint(self) -> QSize:
        return QSize(50, 30)

    def get_knob_pos(self) -> float:
        return self._pos

    def set_knob_pos(self, v: float) -> None:
        self._pos = v
        self.update()

    knob_pos = Property(float, get_knob_pos, set_knob_pos)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        off = theme.manager.color("HAIRLINE")
        on = theme.manager.color("SUCCESS")
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(0, 0, w, h, h / 2, h / 2)
        knob_d = h - 6
        x = 3 + self._pos * (w - knob_d - 6)
        # soft knob shadow
        p.setBrush(QColor(0, 0, 0, 45))
        p.drawEllipse(QRectF(x, 4.5, knob_d, knob_d))
        # white knob
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(x, 3, knob_d, knob_d))
        p.end()


# ===========================================================================
# SegmentedControl
# ===========================================================================
_SEGMENTED = """
#segmented { background: @CONTROL@; border-radius: 9px; }
#segmented QPushButton {
    background: transparent; color: @MUTED@; border: 0; border-radius: 7px;
    padding: 6px 16px; font-size: 13px; font-weight: 500;
}
#segmented QPushButton:hover { color: @BODY@; }
#segmented QPushButton:checked {
    background: @SEG_SEL@; color: @HEADING@; font-weight: 600;
}
"""


class SegmentedControl(QWidget):
    changed = Signal(int)

    def __init__(self, options: list[str], index: int = 0, parent=None):
        super().__init__(parent)
        self.setObjectName("segmented")
        self._options = options
        self._index = index
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._btns: list[QPushButton] = []
        for i, opt in enumerate(options):
            b = QPushButton(opt)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, idx=i: self.set_index(idx))
            self._btns.append(b)
            lay.addWidget(b)
        theme.manager.changed.connect(self._apply)
        self._apply()
        self.set_index(index, emit=False)

    def set_index(self, index: int, emit: bool = True) -> None:
        self._index = index
        for i, b in enumerate(self._btns):
            b.setChecked(i == index)
        if emit:
            self.changed.emit(index)

    def current(self) -> int:
        return self._index

    def current_text(self) -> str:
        return self._options[self._index]

    def _apply(self) -> None:
        self.setStyleSheet(_style(_SEGMENTED))


# ===========================================================================
# Stepper
# ===========================================================================
_STEPPER = """
#stepper { background: @CONTROL@; border: 1px solid @HAIRLINE@; border-radius: 10px; }
#stepper QPushButton {
    background: @SURFACE@; color: @HEADING@; border: 1px solid @HAIRLINE@;
    border-radius: 8px; font-size: 17px; font-weight: 700;
}
#stepper QPushButton:hover { background: @ELEVATED@; }
#stepper QPushButton:pressed { background: @ACCENT_SOFT@; color: @ACCENT_ON_SOFT@; }
#stepper QLabel { color: @HEADING@; font-size: 14px; font-weight: 700; }
"""


class Stepper(QWidget):
    valueChanged = Signal(int)

    def __init__(self, value: int = 1, minimum: int = 1, maximum: int = 99,
                 suffix: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("stepper")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._value = value
        self._min = minimum
        self._max = maximum
        self._suffix = suffix
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        self._minus = QPushButton()
        self._minus.setFixedSize(30, 30)
        self._minus.setIconSize(QSize(14, 14))
        self._minus.setCursor(Qt.CursorShape.PointingHandCursor)
        self._minus.clicked.connect(lambda: self._step(-1))
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setMinimumWidth(46)
        self._plus = QPushButton()
        self._plus.setFixedSize(30, 30)
        self._plus.setIconSize(QSize(14, 14))
        self._plus.setCursor(Qt.CursorShape.PointingHandCursor)
        self._plus.clicked.connect(lambda: self._step(1))
        lay.addWidget(self._minus)
        lay.addWidget(self._label)
        lay.addWidget(self._plus)
        theme.manager.changed.connect(self._apply)
        self._apply()
        self._refresh()

    def value(self) -> int:
        return self._value

    def _step(self, delta: int) -> None:
        new = max(self._min, min(self._max, self._value + delta))
        if new != self._value:
            self._value = new
            self._refresh()
            self.valueChanged.emit(new)

    def _refresh(self) -> None:
        self._label.setText(f"{self._value}{self._suffix}")

    def _apply(self) -> None:
        self.setStyleSheet(_style(_STEPPER))
        c = theme.manager.color("HEADING").name()
        self._minus.setIcon(icons.icon("minus", c, 14))
        self._plus.setIcon(icons.icon("plus", c, 14))


# ===========================================================================
# Inputs
# ===========================================================================
class SearchInput(QLineEdit):
    def __init__(self, placeholder: str = "Search…", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setTextMargins(30, 0, 8, 0)
        self.setClearButtonEnabled(True)
        self.setMinimumHeight(40)
        theme.manager.changed.connect(self.update)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(theme.manager.color("MUTED"))
        pen.setWidthF(1.7)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        cy = self.height() / 2
        p.drawEllipse(QRectF(11, cy - 7, 10, 10))
        p.drawLine(QPointF(20, cy + 3), QPointF(24, cy + 7))
        p.end()


class TextInput(QLineEdit):
    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setMinimumHeight(40)


# ===========================================================================
# Hotkey display
# ===========================================================================
_KBD = """
QLabel {
    background: @CHIP_FILL@; border: 1px solid @CHIP_BORDER@; border-radius: 6px;
    color: @HEADING@; font-size: 12px; font-weight: 600; padding: 2px 7px;
}
"""
_PLUS = "QLabel { color: @MUTED@; font-size: 12px; }"
_NO_HOTKEY = "QLabel { color: @MUTED@; font-size: 13px; }"


class HotkeyChips(QWidget):
    def __init__(self, hotkey: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(6)
        self._lay.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._hotkey = hotkey
        theme.manager.changed.connect(self._rebuild)
        self._rebuild()

    def set_hotkey(self, hotkey: Optional[str]) -> None:
        self._hotkey = hotkey
        self._rebuild()

    def _clear(self) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _rebuild(self) -> None:
        self._clear()
        keys = pretty_hotkey(self._hotkey)
        if not keys:
            lbl = QLabel("No hotkey assigned")
            lbl.setStyleSheet(_style(_NO_HOTKEY))
            self._lay.addWidget(lbl)
            return
        for i, k in enumerate(keys):
            if i:
                plus = QLabel("+")
                plus.setStyleSheet(_style(_PLUS))
                self._lay.addWidget(plus)
            chip = QLabel(k)
            chip.setStyleSheet(_style(_KBD))
            self._lay.addWidget(chip)


_HOTPILL = """
QLabel {
    background: @ACCENT_SOFT@; color: @ACCENT_ON_SOFT@;
    border-radius: 11px; padding: 4px 11px; font-size: 12px; font-weight: 600;
}
"""


class HotkeyPill(QLabel):
    def __init__(self, hotkey: Optional[str] = None, parent=None):
        super().__init__(parent)
        theme.manager.changed.connect(self._apply)
        self.set_hotkey(hotkey)

    def set_hotkey(self, hotkey: Optional[str]) -> None:
        self.setText("+".join(pretty_hotkey(hotkey)) if hotkey else "")
        self.setVisible(bool(hotkey))
        self._apply()

    def _apply(self) -> None:
        self.setStyleSheet(_style(_HOTPILL))


# ===========================================================================
# StatusBadge
# ===========================================================================
_BADGE_MAP = {
    "ready": ("SUCCESS", "SUCCESS_TEXT", "SUCCESS_SOFT", "Ready"),
    "idle": ("IDLE_DOT", "IDLE_TEXT", "IDLE_SOFT", "Idle"),
    "recording": ("REC_TEXT", "REC_TEXT", "REC_SOFT", "Recording"),
}


class StatusBadge(QFrame):
    def __init__(self, status: str = "ready", parent=None):
        super().__init__(parent)
        self._status = status
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 11, 4)
        lay.setSpacing(7)
        self._dot = QFrame()
        self._dot.setFixedSize(7, 7)
        self._label = QLabel()
        lay.addWidget(self._dot)
        lay.addWidget(self._label)
        theme.manager.changed.connect(self._apply)
        self.set_status(status)

    def set_status(self, status: str) -> None:
        self._status = status if status in _BADGE_MAP else "idle"
        self._apply()

    def _apply(self) -> None:
        dot_tok, text_tok, soft_tok, label = _BADGE_MAP[self._status]
        self._label.setText(label)
        self.setStyleSheet(_style(
            "StatusBadge { background: @%s@; border-radius: 11px; }" % soft_tok
        ))
        self._dot.setStyleSheet(_style(
            "QFrame { background: @%s@; border-radius: 3px; }" % dot_tok
        ))
        self._label.setStyleSheet(_style(
            "QLabel { background: transparent; color: @%s@; font-size: 12px;"
            " font-weight: 600; }" % text_tok
        ))


# ===========================================================================
# InsetList — rounded card with hairline-divided rows
# ===========================================================================
class _InsetRow(QWidget):
    """An InsetList row that can emit ``clicked`` (for tappable rows)."""

    clicked = Signal()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class InsetList(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("insetList")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._rows = 0
        # Soft shadow so the card lifts off the canvas (per the reference).
        self._shadow = _drop_shadow(self, QColor(0, 0, 0, 28), blur=16, dy=3)
        theme.manager.changed.connect(self._apply)
        self._apply()

    def add_row(self, label: str, trailing: Optional[QWidget] = None,
                subtitle: Optional[str] = None, on_click=None) -> QWidget:
        if self._rows:
            line = QFrame()
            line.setFixedHeight(1)
            line.setObjectName("insetDivider")
            self._lay.addWidget(line)

        row = _InsetRow()
        row.setObjectName("insetRow")
        if on_click is not None:
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row.clicked.connect(on_click)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(16, 13, 16, 13)
        rl.setSpacing(12)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        title = QLabel(label)
        title.setObjectName("insetTitle")
        text_box.addWidget(title)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("insetSub")
            text_box.addWidget(sub)
        rl.addLayout(text_box)
        rl.addStretch(1)
        if trailing is not None:
            rl.addWidget(trailing)
        self._lay.addWidget(row)
        self._rows += 1
        self._apply()
        return row

    def _apply(self) -> None:
        self._shadow.setColor(QColor(0, 0, 0, 110 if theme.manager.is_dark() else 28))
        self.setStyleSheet(_style(
            "#insetList { background: @SURFACE@; border: 1px solid @HAIRLINE@;"
            " border-radius: 16px; }"
            "#insetDivider { background: @HAIRLINE@; }"
            "#insetTitle { color: @HEADING@; font-size: 15px; font-weight: 500;"
            " background: transparent; }"
            "#insetSub { color: @MUTED@; font-size: 13px; background: transparent; }"
        ))


# ===========================================================================
# RecordingCard — grid + list variants
# ===========================================================================
class SmoothScrollArea(QScrollArea):
    """A QScrollArea with eased, animated wheel scrolling. Wheel notches
    accumulate into one in-flight animation so fast scrolling stays fluid
    instead of jumping line-by-line."""

    _STEP = 1.0        # px of scroll per unit of wheel angle-delta
    _DURATION = 190    # ms per eased glide — short so it stays responsive

    def __init__(self, parent=None):
        super().__init__(parent)
        self._anim = QPropertyAnimation(self.verticalScrollBar(), b"value", self)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(self._DURATION)
        self._target: Optional[int] = None

    def wheelEvent(self, e):
        sb = self.verticalScrollBar()
        if sb is None or sb.maximum() == sb.minimum():
            return super().wheelEvent(e)
        dy = e.angleDelta().y()
        if dy == 0:
            return super().wheelEvent(e)
        running = self._anim.state() == QPropertyAnimation.State.Running
        base = self._target if (running and self._target is not None) else sb.value()
        target = max(sb.minimum(), min(sb.maximum(), int(base - dy * self._STEP)))
        self._target = target
        self._anim.stop()
        self._anim.setStartValue(sb.value())
        self._anim.setEndValue(target)
        self._anim.start()
        e.accept()


class RecordingCard(QFrame):
    play_clicked = Signal(int)
    menu_clicked = Signal(int)
    open_clicked = Signal(int)

    def __init__(self, workflow, variant: str = "grid", parent=None):
        super().__init__(parent)
        self.setObjectName("recCard")
        self._wf = workflow
        self._variant = variant
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._shadow = _drop_shadow(self, QColor(0, 0, 0, 34), blur=24, dy=6)
        self._set_shadow(self._shadow_rest())
        # Hover "focus" — a smooth scale-up via geometry animation.
        self._rest_geom: QRect | None = None
        self._hovered = False
        self._geo_anim = QPropertyAnimation(self, b"geometry", self)
        self._geo_anim.setDuration(200)
        self._geo_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        if variant == "grid":
            self._build_grid()
        else:
            self._build_list()
        theme.manager.changed.connect(self._apply)
        self._apply()

    def _meta_text(self) -> str:
        wf = self._wf
        unit = "step" if wf.step_count == 1 else "steps"
        return f"{wf.step_count} {unit} · {wf.duration} · {relative_time(wf.last_run)}"

    def _build_grid(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(StatusBadge(self._wf.status))
        top.addStretch(1)
        if self._wf.trigger.hotkey:
            top.addWidget(HotkeyPill(self._wf.trigger.hotkey))
        root.addLayout(top)
        title = QLabel(self._wf.name)
        title.setObjectName("cardTitle")
        root.addWidget(title)
        meta = QLabel(self._meta_text())
        meta.setObjectName("cardMeta")
        root.addWidget(meta)
        root.addStretch(1)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        play = PlayButton("solid", full_width=True)
        play.clicked.connect(lambda: self.play_clicked.emit(self._wf.id))
        more = QPushButton("⋯")
        more.setObjectName("moreBtn")
        more.setFixedSize(40, 40)
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.clicked.connect(lambda: self.menu_clicked.emit(self._wf.id))
        bottom.addWidget(play, 1)
        bottom.addWidget(more)
        root.addLayout(bottom)

    def _build_list(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(9)
        dot = QFrame()
        dot.setObjectName("nameDot")
        dot.setFixedSize(8, 8)
        name = QLabel(self._wf.name)
        name.setObjectName("cardTitle")
        more = QPushButton("⋯")
        more.setObjectName("moreBtn")
        more.setFixedSize(36, 36)
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.clicked.connect(lambda: self.menu_clicked.emit(self._wf.id))
        top.addWidget(dot)
        top.addWidget(name)
        top.addStretch(1)
        top.addWidget(more)
        root.addLayout(top)
        meta = QLabel(self._meta_text())
        meta.setObjectName("cardMeta")
        root.addWidget(meta)
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        bottom.addWidget(HotkeyChips(self._wf.trigger.hotkey))
        bottom.addStretch(1)
        play = PlayButton("soft")
        play.clicked.connect(lambda: self.play_clicked.emit(self._wf.id))
        bottom.addWidget(play)
        root.addLayout(bottom)

    def mousePressEvent(self, e):
        # Defer the open so the handler (which may open a modal editor and
        # rebuild — i.e. delete — this card) never runs while we're still inside
        # this widget's event handler.
        if e.button() == Qt.MouseButton.LeftButton:
            wid = self._wf.id
            QTimer.singleShot(0, lambda: self.open_clicked.emit(wid))
            e.accept()
            return
        super().mousePressEvent(e)

    # Clean, soft black shadow that lifts a touch on hover (no colored glow).
    def _shadow_rest(self):
        return (QColor(0, 0, 0, 140 if theme.manager.is_dark() else 34), 24, 6)

    def _shadow_hover(self):
        return (QColor(0, 0, 0, 175 if theme.manager.is_dark() else 52), 36, 12)

    def _set_shadow(self, spec) -> None:
        color, blur, dy = spec
        self._shadow.setColor(color)
        self._shadow.setBlurRadius(blur)
        self._shadow.setOffset(0, dy)

    def _grown(self, rect: QRect) -> QRect:
        gw = int(rect.width() * 0.06)
        gh = int(rect.height() * 0.06)
        return QRect(rect.x() - gw // 2, rect.y() - gh // 2,
                     rect.width() + gw, rect.height() + gh)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # Capture the resting geometry only when stable (not hovered / animating)
        # so the layout's own resizes don't get mistaken for the hover scale.
        if not self._hovered and self._geo_anim.state() != QPropertyAnimation.State.Running:
            self._rest_geom = self.geometry()

    def _animate_geo(self, target: QRect) -> None:
        self._geo_anim.stop()
        self._geo_anim.setStartValue(self.geometry())
        self._geo_anim.setEndValue(target)
        self._geo_anim.start()

    def enterEvent(self, e):
        self._hovered = True
        if self._rest_geom is None:
            self._rest_geom = self.geometry()
        # Only grid cards scale up — full-width list rows would overflow the
        # frame, so they get the shadow lift alone.
        if self._variant == "grid":
            self.raise_()
            self._animate_geo(self._grown(self._rest_geom))
        color, blur, dy = self._shadow_hover()
        self._shadow.setColor(color)
        motion.animate_blur(self._shadow, blur, 200)
        motion.animate_offset(self._shadow, dy, 200)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        if self._variant == "grid" and self._rest_geom is not None:
            self._animate_geo(self._rest_geom)
        color, blur, dy = self._shadow_rest()
        self._shadow.setColor(color)
        motion.animate_blur(self._shadow, blur, 240)
        motion.animate_offset(self._shadow, dy, 240)
        super().leaveEvent(e)

    def _apply(self) -> None:
        self._set_shadow(self._shadow_rest())
        radius = 16 if self._variant == "grid" else 14
        self.setStyleSheet(_style(
            "#recCard { background: @SURFACE@; border: 1px solid @HAIRLINE@;"
            " border-radius: %dpx; }"
            "#cardTitle { color: @HEADING@; font-size: 16px; font-weight: 600;"
            " background: transparent; }"
            "#cardMeta { color: @MUTED@; font-size: 13px; background: transparent; }"
            "#moreBtn { background: transparent; color: @MUTED@; border: 0;"
            " border-radius: 9px; font-size: 18px; }"
            "#moreBtn:hover { background: @CONTROL@; color: @HEADING@; }"
            "#nameDot { background: @SUCCESS@; border-radius: 4px; }"
            % radius
        ))
