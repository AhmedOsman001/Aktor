"""Design tokens + themable Qt stylesheets for FlowRecord.

A single source of truth for the app's visual language (Linear / Notion / Arc
inspired UI). Supports light + dark modes and a user-selectable accent color,
all switchable at runtime.

How it works
------------
* Palettes (`_DARK`, `_LIGHT`) define neutral surface / text / border tokens.
* Semantic colors (danger / success / warning) are shared across modes.
* The accent *ramp* (hover/pressed/soft/focus/selection) is derived from a single
  base accent color, so the picker only has to store one value.
* `tokens()` merges all of the above into a flat name -> value dict.
* QSS is rendered on demand from raw `@TOKEN@` templates via `_render()`.

Painter code (the overlay, the log panel) should read colors *per paint* through
`color(name)` and repaint on `manager.changed`, so theme switches are live. The
legacy module-level constants below remain available for back-compat and reflect
the default (dark) palette.
"""

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor


# ---------------------------------------------------------------------------
# Palettes (neutral surfaces / borders / text)
# ---------------------------------------------------------------------------

_DARK = {
    "BG_APP": "#0a0a10",
    "BG_SURFACE": "#16161f",
    "BG_ELEVATED": "#20202c",
    "BG_INSET": "#0d0d14",
    "BG_OVERLAY": "#17171f",
    "OVERLAY_BG": "rgba(20, 20, 28, 232)",
    "OVERLAY_TOP": "#24242f",
    # Window backdrop gradient (cool, slightly blue-tinted depth)
    "BG_GRAD_TOP": "#1a1a26",
    "BG_GRAD_BOTTOM": "#0a0a10",
    # Card gradient
    "CARD_TOP": "#1c1c27",
    "CARD_BOTTOM": "#15151d",
    "CARD_HOVER_TOP": "#24243150",
    "BORDER": "rgba(255,255,255,0.08)",
    "BORDER_STRONG": "rgba(255,255,255,0.15)",
    "HAIRLINE": "rgba(255,255,255,0.05)",
    "GLASS_HI": "rgba(255,255,255,0.06)",
    "GLASS_HI_STRONG": "rgba(255,255,255,0.14)",
    "TEXT_PRIMARY": "#f3f3f8",
    "TEXT_SECONDARY": "#a2a2b0",
    "TEXT_MUTED": "#6a6a78",
    "TEXT_DISABLED": "#494954",
    "TEXT_ON_ACCENT": "#ffffff",
}

_LIGHT = {
    "BG_APP": "#eef0f5",
    "BG_SURFACE": "#ffffff",
    "BG_ELEVATED": "#f3f4f8",
    "BG_INSET": "#eaecf1",
    "BG_OVERLAY": "#fbfbfd",
    "OVERLAY_BG": "rgba(252, 252, 255, 235)",
    "OVERLAY_TOP": "#ffffff",
    "BG_GRAD_TOP": "#ffffff",
    "BG_GRAD_BOTTOM": "#e9ebf2",
    "CARD_TOP": "#ffffff",
    "CARD_BOTTOM": "#f5f6fa",
    "CARD_HOVER_TOP": "#f0f1f8",
    "BORDER": "rgba(0,0,0,0.10)",
    "BORDER_STRONG": "rgba(0,0,0,0.18)",
    "HAIRLINE": "rgba(0,0,0,0.07)",
    "GLASS_HI": "rgba(0,0,0,0.04)",
    "GLASS_HI_STRONG": "rgba(0,0,0,0.10)",
    "TEXT_PRIMARY": "#16161d",
    "TEXT_SECONDARY": "#50505b",
    "TEXT_MUTED": "#80808c",
    "TEXT_DISABLED": "#b2b2ba",
    "TEXT_ON_ACCENT": "#ffffff",
}

# Semantic colors — shared across modes (read fine on both surfaces).
_SEMANTIC = {
    "DANGER": "#e5484d",
    "DANGER_HOVER": "#f3555a",
    "DANGER_PRESSED": "#cd3a3f",
    "DANGER_SOFT": "rgba(229,72,77,0.14)",
    "SUCCESS": "#30a46c",
    "SUCCESS_HOVER": "#37b578",
    "SUCCESS_PRESSED": "#2a9261",
    "SUCCESS_SOFT": "rgba(48,164,108,0.14)",
    "WARNING": "#f5a524",
}

_FONT_BASE = '"Segoe UI", "Segoe UI Variable", sans-serif'

DEFAULT_MODE = "dark"
DEFAULT_ACCENT = "#5e6ad2"

# Recording red (for the live pulse dot) — independent of theme/accent.
RECORD_RED = QColor(232, 57, 70)


def qc(hex_or_rgba: str) -> QColor:
    """Parse a #hex or rgba() string into a QColor."""
    s = hex_or_rgba.strip()
    if s.startswith("rgba"):
        inner = s[s.index("(") + 1:s.rindex(")")]
        r, g, b, a = [p.strip() for p in inner.split(",")]
        return QColor(int(r), int(g), int(b), int(float(a) * 255))
    return QColor(s)


def _rgba(c: QColor, alpha: float) -> str:
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha})"


def _accent_ramp(base: str) -> dict:
    """Derive the accent-related tokens from a single base accent color."""
    c = QColor(base)
    if not c.isValid():
        c = QColor(DEFAULT_ACCENT)
    return {
        "ACCENT": c.name(),
        "ACCENT_HOVER": c.lighter(118).name(),
        "ACCENT_PRESSED": c.darker(112).name(),
        "ACCENT_2": c.lighter(140).name(),  # brighter companion for gradients
        "ACCENT_SOFT": _rgba(c, 0.16),
        "ACCENT_GLOW": _rgba(c, 0.32),
        "BORDER_FOCUS": _rgba(c, 0.60),
        "SELECTION": _rgba(c, 0.22),
        "GLOW": _rgba(c, 0.55),
    }


def _build_tokens(mode: str, accent: str) -> dict:
    palette = _LIGHT if mode == "light" else _DARK
    tokens = {"FONT_BASE": _FONT_BASE}
    tokens.update(palette)
    tokens.update(_SEMANTIC)
    tokens.update(_accent_ramp(accent))
    return tokens


# ---------------------------------------------------------------------------
# Stylesheet templating
#
# QSS contains literal { } braces, so we can't use str.format(). We use simple
# @TOKEN@ markers and replace them against the active token set.
# ---------------------------------------------------------------------------

def _render(template: str, tokens: dict) -> str:
    out = template
    for key, val in tokens.items():
        out = out.replace("@" + key + "@", val)
    return out


_SCROLLBAR_TPL = """
QScrollBar:vertical {
    background: transparent; width: 12px; margin: 2px;
}
QScrollBar::handle:vertical {
    background: @GLASS_HI_STRONG@;
    border-radius: 4px; min-height: 28px; margin: 2px;
}
QScrollBar::handle:vertical:hover { background: @BORDER_STRONG@; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal {
    background: transparent; height: 12px; margin: 2px;
}
QScrollBar::handle:horizontal {
    background: @GLASS_HI_STRONG@;
    border-radius: 4px; min-width: 28px; margin: 2px;
}
QScrollBar::handle:horizontal:hover { background: @BORDER_STRONG@; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""

_CHECKBOX_TPL = """
QCheckBox { color: @TEXT_SECONDARY@; font-size: 12px; spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    background: @BG_ELEVATED@;
    border: 1px solid @BORDER_STRONG@;
    border-radius: 5px;
}
QCheckBox::indicator:hover { border: 1px solid @ACCENT@; }
QCheckBox::indicator:checked {
    background: @ACCENT@;
    border: 1px solid @ACCENT@;
    image: none;
}
"""


# ---- Global application stylesheet (applied to QApplication) ----
# IMPORTANT: must NOT impose a background on bare QWidget, otherwise the
# translucent overlay window breaks. Only native chrome is styled here.
_APP_TPL = """
* { font-family: @FONT_BASE@; }

QToolTip {
    background-color: @BG_ELEVATED@;
    color: @TEXT_PRIMARY@;
    border: 1px solid @BORDER@;
    border-radius: 6px;
    padding: 5px 8px;
    font-size: 11px;
}

QMenu {
    background-color: @BG_SURFACE@;
    border: 1px solid @BORDER@;
    border-radius: 8px;
    padding: 5px;
    color: @TEXT_PRIMARY@;
    font-size: 12px;
}
QMenu::item {
    padding: 7px 26px 7px 14px;
    border-radius: 6px;
    margin: 1px 2px;
}
QMenu::item:selected { background-color: @ACCENT@; color: @TEXT_ON_ACCENT@; }
QMenu::separator { height: 1px; background: @BORDER@; margin: 5px 8px; }
QMenu::right-arrow, QMenu::tearoff { image: none; width: 0; }

QInputDialog, QMessageBox {
    background-color: @BG_SURFACE@;
}
QInputDialog QLabel, QMessageBox QLabel {
    color: @TEXT_PRIMARY@; font-size: 13px;
}
QInputDialog QLineEdit, QMessageBox QLineEdit {
    background-color: @BG_INSET@; color: @TEXT_PRIMARY@;
    border: 1px solid @BORDER@; border-radius: 7px;
    padding: 8px 10px; font-size: 13px;
    selection-background-color: @ACCENT@;
}
QInputDialog QLineEdit:focus, QMessageBox QLineEdit:focus { border: 1px solid @BORDER_FOCUS@; }
QInputDialog QPushButton, QMessageBox QPushButton {
    background-color: @BG_ELEVATED@; color: @TEXT_PRIMARY@;
    border: 1px solid @BORDER@;
    border-radius: 7px;
    padding: 8px 18px; font-size: 12px; font-weight: 600;
    min-width: 64px;
}
QInputDialog QPushButton:hover, QMessageBox QPushButton:hover {
    background-color: @GLASS_HI_STRONG@; border: 1px solid @BORDER_STRONG@;
}
QInputDialog QPushButton:default, QMessageBox QPushButton:default {
    background-color: @ACCENT@; color: @TEXT_ON_ACCENT@; border: 1px solid @ACCENT@;
}
QInputDialog QPushButton:default:hover, QMessageBox QPushButton:default:hover {
    background-color: @ACCENT_HOVER@;
}
"""


# ---- Heavy stylesheet for FlowRecord windows (manager + step editor) ----
_DIALOG_TPL = """
QDialog {
    background-color: qlineargradient(x1:0, y1:0, x2:0.5, y2:1,
        stop:0 @BG_GRAD_TOP@, stop:1 @BG_GRAD_BOTTOM@);
    color: @TEXT_PRIMARY@;
}
QWidget { color: @TEXT_PRIMARY@; }

/* ---- Buttons ---- */
QPushButton {
    background-color: @GLASS_HI@;
    color: @TEXT_PRIMARY@;
    border: 1px solid @BORDER@;
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: @GLASS_HI_STRONG@;
    border: 1px solid @BORDER_STRONG@;
}
QPushButton:pressed { background-color: @BG_ELEVATED@; }
QPushButton:disabled {
    background-color: @BG_INSET@; color: @TEXT_DISABLED@; border: 1px solid transparent;
}

/* primary: accent */
QPushButton#btnNew, QPushButton#btnTest {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 @ACCENT_2@, stop:1 @ACCENT@);
    color: @TEXT_ON_ACCENT@; border: 1px solid @ACCENT@;
}
QPushButton#btnNew:hover, QPushButton#btnTest:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 @ACCENT_2@, stop:1 @ACCENT_HOVER@);
    border: 1px solid @ACCENT_HOVER@;
}
QPushButton#btnNew:pressed, QPushButton#btnTest:pressed { background-color: @ACCENT_PRESSED@; }

/* positive: success */
QPushButton#btnRun, QPushButton#btnSave {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 @SUCCESS_HOVER@, stop:1 @SUCCESS@);
    color: #ffffff; border: 1px solid @SUCCESS@;
}
QPushButton#btnRun:hover, QPushButton#btnSave:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 @SUCCESS_HOVER@, stop:1 @SUCCESS_HOVER@);
    border: 1px solid @SUCCESS_HOVER@;
}
QPushButton#btnRun:pressed, QPushButton#btnSave:pressed { background-color: @SUCCESS_PRESSED@; }
QPushButton#btnRun:disabled, QPushButton#btnSave:disabled {
    background-color: @BG_INSET@; color: @TEXT_DISABLED@; border: 1px solid transparent;
}

/* destructive: danger outline */
QPushButton#btnDelete {
    background-color: transparent; color: @DANGER@;
    border: 1px solid rgba(229,72,77,0.30);
}
QPushButton#btnDelete:hover {
    background-color: @DANGER_SOFT@; color: #ffffff; border: 1px solid @DANGER@;
}
QPushButton#btnDelete:pressed { background-color: @DANGER_PRESSED@; }

/* ghost / icon buttons */
QPushButton#btnEdit, QPushButton#btnSettings,
QPushButton#btnBack, QPushButton#btnStepDelete, QPushButton#btnExport,
QPushButton#btnClear, QPushButton#btnCollapse {
    background-color: transparent;
    color: @TEXT_SECONDARY@;
    border: 1px solid @BORDER@;
}
QPushButton#btnEdit:hover, QPushButton#btnSettings:hover,
QPushButton#btnBack:hover, QPushButton#btnStepDelete:hover, QPushButton#btnExport:hover,
QPushButton#btnClear:hover, QPushButton#btnCollapse:hover {
    background-color: @GLASS_HI@;
    color: @TEXT_PRIMARY@;
    border: 1px solid @BORDER_STRONG@;
}
QPushButton#btnStepDelete:hover {
    background-color: @DANGER_SOFT@; color: #ffffff; border: 1px solid @DANGER@;
}

/* Smart Wait toggle — highlights green when active */
QPushButton#btnSmartWait {
    background-color: transparent;
    color: @TEXT_SECONDARY@;
    border: 1px solid @BORDER@;
    padding: 5px 8px;
    font-size: 11px;
    font-weight: 600;
}
QPushButton#btnSmartWait:hover {
    background-color: @GLASS_HI@; color: @TEXT_PRIMARY@; border: 1px solid @BORDER_STRONG@;
}
QPushButton#btnSmartWait:checked {
    background-color: @SUCCESS_SOFT@; color: @SUCCESS@; border: 1px solid @SUCCESS@;
}
QPushButton#btnSmartWait:disabled {
    background-color: transparent; color: @TEXT_DISABLED@; border: 1px solid transparent;
}

/* ---- Activity log console ---- */
QFrame#panelFrame {
    background-color: @BG_SURFACE@;
    border: 1px solid @BORDER@;
    border-radius: 10px;
}
QLabel#panelTitle { color: @TEXT_PRIMARY@; font-size: 12px; font-weight: 700; }
QLabel#panelHint { color: @TEXT_MUTED@; font-size: 11px; }
QTextEdit#logView {
    background-color: @BG_INSET@;
    color: @TEXT_SECONDARY@;
    border: 1px solid @BORDER@;
    border-radius: 8px;
    padding: 8px 10px;
    font-family: "Cascadia Mono", "Consolas", "Menlo", monospace;
    font-size: 12px;
    selection-background-color: @ACCENT@;
}
QComboBox {
    background-color: @BG_ELEVATED@;
    color: @TEXT_PRIMARY@;
    border: 1px solid @BORDER@;
    border-radius: 7px;
    padding: 5px 10px;
    font-size: 12px;
    min-width: 90px;
}
QComboBox:hover { border: 1px solid @BORDER_STRONG@; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid @TEXT_SECONDARY@;
    margin-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: @BG_SURFACE@;
    color: @TEXT_PRIMARY@;
    border: 1px solid @BORDER@;
    border-radius: 6px;
    padding: 4px;
    outline: 0;
    selection-background-color: @ACCENT@;
    selection-color: #ffffff;
}

/* ---- Labels ---- */
QLabel { color: @TEXT_SECONDARY@; font-size: 12px; }
QLabel#titleLabel { color: @TEXT_PRIMARY@; font-size: 20px; font-weight: 800; letter-spacing: 0.2px; }
QLabel#nameLabel { color: @TEXT_PRIMARY@; font-size: 14px; font-weight: 600; }
QLabel#infoLabel { color: @TEXT_MUTED@; font-size: 11px; }
QLabel#emptyLabel { color: @TEXT_MUTED@; font-size: 13px; }
QLabel#emptyTitle { color: @TEXT_PRIMARY@; font-size: 16px; font-weight: 700; }
QLabel#editorTitle { color: @TEXT_PRIMARY@; font-size: 15px; font-weight: 700; }
QLabel#tipLabel { color: @TEXT_MUTED@; font-size: 11px; padding: 2px 2px; }
QLabel#stepIndex { color: @TEXT_MUTED@; font-weight: 700; font-size: 12px; }
QLabel#stepIcon { font-size: 15px; }
QLabel#stepDesc { color: @TEXT_PRIMARY@; font-size: 12px; }
QLabel#dragHandle { color: @TEXT_DISABLED@; font-size: 16px; }
QLabel#dragHandle:hover { color: @TEXT_SECONDARY@; }

/* ---- Inputs ---- */
QLineEdit {
    background-color: @BG_INSET@; color: @TEXT_PRIMARY@;
    border: 1px solid @BORDER@; border-radius: 7px;
    padding: 7px 10px; font-size: 13px;
    selection-background-color: @ACCENT@;
}
QLineEdit:focus { border: 1px solid @BORDER_FOCUS@; background-color: @BG_INSET@; }
QLineEdit#searchBox {
    border-radius: 9px;
    padding: 10px 12px 10px 34px;
    font-size: 13px;
    background-color: @BG_SURFACE@;
}

QDoubleSpinBox {
    background-color: @BG_INSET@; color: @TEXT_PRIMARY@;
    border: 1px solid @BORDER@; border-radius: 7px;
    padding: 5px 6px; font-size: 12px;
}
QDoubleSpinBox:focus { border: 1px solid @BORDER_FOCUS@; }
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background: transparent; border: none; width: 16px;
}
QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow { image: none; width: 0; height: 0; }

@CHECKBOX@

/* ---- Workflow rows (cards) ---- */
QFrame#wfRow {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 @CARD_TOP@, stop:1 @CARD_BOTTOM@);
    border: 1px solid @BORDER@;
    border-radius: 13px;
}
QFrame#wfRow:hover {
    border: 1px solid @BORDER_FOCUS@;
}

QFrame#stepRow {
    background-color: transparent;
    border: none;
    border-bottom: 1px solid @HAIRLINE@;
}
QFrame#stepRow:hover { background-color: @GLASS_HI@; }

/* ---- Lists ---- */
QListWidget {
    background-color: @BG_INSET@; color: @TEXT_PRIMARY@;
    border: 1px solid @BORDER@; border-radius: 9px;
    font-size: 13px; padding: 3px;
    outline: 0;
}
QListWidget::item { padding: 6px; border-radius: 6px; }
QListWidget::item:selected { background-color: @SELECTION@; color: @TEXT_PRIMARY@; }
QListWidget::item:disabled { color: @TEXT_DISABLED@; }

QListWidget#stepList {
    background-color: @BG_INSET@;
    border: 1px solid @BORDER@;
    border-radius: 10px;
    padding: 2px;
}
QListWidget#stepList::item {
    padding: 0; border-bottom: 1px solid @HAIRLINE@;
}
QListWidget#stepList::item:selected { background-color: @SELECTION@; }

QScrollArea { background-color: transparent; border: none; }
QWidget#listContainer { background-color: transparent; }

QSplitter::handle:vertical { background: transparent; height: 8px; }
QSplitter::handle:vertical:hover { background: @GLASS_HI@; }

@SCROLLBAR@
"""


# ---- Overlay pill stylesheet ----
_OVERLAY_TPL = """
#pillFrame {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 @OVERLAY_TOP@, stop:1 @OVERLAY_BG@);
    border: 1px solid @BORDER_STRONG@;
    border-radius: 16px;
}
QPushButton {
    border-radius: 10px;
    padding: 8px 16px;
    font-size: 12px;
    font-weight: 600;
    border: 1px solid transparent;
    color: #ffffff;
}
QPushButton#btnRecord, QPushButton#btnStop {
    background-color: @DANGER@; border: 1px solid @DANGER@;
}
QPushButton#btnRecord:hover, QPushButton#btnStop:hover {
    background-color: @DANGER_HOVER@; border: 1px solid @DANGER_HOVER@;
}
QPushButton#btnRecord:pressed, QPushButton#btnStop:pressed {
    background-color: @DANGER_PRESSED@;
}
QPushButton#btnWorkflows, QPushButton#btnPause {
    background-color: @GLASS_HI@;
    color: @TEXT_PRIMARY@;
    border: 1px solid @BORDER@;
}
QPushButton#btnWorkflows:hover, QPushButton#btnPause:hover {
    background-color: @GLASS_HI_STRONG@;
    border: 1px solid @BORDER_STRONG@;
}
QPushButton#btnWorkflows:pressed, QPushButton#btnPause:pressed {
    background-color: @GLASS_HI@;
}
"""


# ---------------------------------------------------------------------------
# Theme manager
# ---------------------------------------------------------------------------

class _ThemeManager(QObject):
    """Holds the active mode + accent and renders QSS on demand.

    Emits ``changed`` whenever the theme changes so widgets can re-apply local
    stylesheets and repaint.
    """

    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._mode = DEFAULT_MODE
        self._accent = DEFAULT_ACCENT

    # ---- state ----
    @property
    def mode(self) -> str:
        return self._mode

    @property
    def accent(self) -> str:
        return self._accent

    def is_dark(self) -> bool:
        return self._mode != "light"

    def tokens(self) -> dict:
        return _build_tokens(self._mode, self._accent)

    def color(self, name: str) -> QColor:
        if name == "RECORD_RED":
            return QColor(RECORD_RED)
        val = self.tokens().get(name)
        return qc(val) if val else QColor("#000000")

    # ---- rendering ----
    def qss_app(self) -> str:
        return _render(_APP_TPL, self.tokens())

    def qss_dialog(self) -> str:
        body = _DIALOG_TPL.replace("@CHECKBOX@", _CHECKBOX_TPL).replace(
            "@SCROLLBAR@", _SCROLLBAR_TPL
        )
        return _render(body, self.tokens())

    def qss_overlay(self) -> str:
        return _render(_OVERLAY_TPL, self.tokens())

    # ---- mutation ----
    def set_mode(self, mode: str, *, emit: bool = True) -> None:
        mode = "light" if mode == "light" else "dark"
        if mode == self._mode:
            return
        self._mode = mode
        _refresh_module_constants()
        if emit:
            self.changed.emit()

    def set_accent(self, accent: str, *, emit: bool = True) -> None:
        if not accent or accent == self._accent:
            return
        self._accent = accent
        _refresh_module_constants()
        if emit:
            self.changed.emit()

    def set_theme(self, mode: str, accent: str) -> None:
        self._mode = "light" if mode == "light" else "dark"
        self._accent = accent or DEFAULT_ACCENT
        _refresh_module_constants()
        self.changed.emit()

    def apply(self, app) -> None:
        """Apply the global app stylesheet and notify subscribers."""
        app.setStyleSheet(self.qss_app())
        self.changed.emit()


manager = _ThemeManager()


# ---------------------------------------------------------------------------
# Back-compat module constants
#
# Existing code reads a handful of palette values and the three QSS strings as
# module attributes. We keep them populated from the *current* theme and refresh
# them on every theme change. (Widgets that need live updates should instead read
# via manager.color()/manager.qss_*() and subscribe to manager.changed.)
# ---------------------------------------------------------------------------

def _refresh_module_constants() -> None:
    g = globals()
    g.update(manager.tokens())
    g["APP_QSS"] = manager.qss_app()
    g["DIALOG_QSS"] = manager.qss_dialog()
    g["OVERLAY_QSS"] = manager.qss_overlay()


_refresh_module_constants()
