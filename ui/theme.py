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

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor


# ---------------------------------------------------------------------------
# Palettes (neutral surfaces / borders / text)
# ---------------------------------------------------------------------------

# Component-library palette ("Aktor" iOS-like calm: blue accent, clean surfaces).
# Token *names* are kept stable so existing painter code and QSS keep resolving;
# only the values change to the new design language.

# Nature palette — "Deep Forest" (dark) & "Morning Meadow" (light): mossy
# greens, bark text, parchment canvas, leaf-green accent. Token *names* stay
# stable so existing painter code and QSS keep resolving; only values change.

_DARK = {
    "BG_APP": "#0f150e",          # forest-floor canvas
    "BG_SURFACE": "#18211a",      # cards / surfaces (deep moss)
    "BG_ELEVATED": "#27322a",     # elevated control fill
    "BG_INSET": "#222c23",        # filled inputs / segmented track
    "BG_OVERLAY": "#18211a",
    "OVERLAY_BG": "rgba(24, 33, 26, 240)",
    "OVERLAY_TOP": "#27322a",
    # Subtle canopy depth — light filtering down through the trees.
    "BG_GRAD_TOP": "#121a11",
    "BG_GRAD_BOTTOM": "#0d120c",
    "CARD_TOP": "#18211a",
    "CARD_BOTTOM": "#18211a",
    "CARD_HOVER_TOP": "#1e281f",
    "CONTROL_FILL": "#27322a",    # search, inputs, segmented track, secondary btn
    "BORDER": "#2c3a2e",          # hairline / divider
    "BORDER_STRONG": "rgba(255,255,255,0.20)",
    "HAIRLINE": "#2c3a2e",
    "GLASS_HI": "rgba(255,255,255,0.06)",
    "GLASS_HI_STRONG": "rgba(255,255,255,0.13)",
    "TEXT_PRIMARY": "#eef4e8",    # moonlit leaf-white
    "TEXT_SECONDARY": "#c6d4bd",  # body
    "TEXT_MUTED": "#8a9a80",      # sage meta
    "TEXT_DISABLED": "#5d6a55",
    "TEXT_ON_ACCENT": "#ffffff",
    # Status / badge soft fills + text (per-mode).
    "SUCCESS_SOFT": "rgba(74,222,128,0.20)",
    "SUCCESS_TEXT": "#86efac",
    "DANGER_SOFT": "rgba(239,90,68,0.22)",
    "DANGER_TEXT": "#ff8a73",
    "IDLE_SOFT": "rgba(255,255,255,0.07)",
    "IDLE_TEXT": "#9aa890",
    # Extras (dark)
    "SEG_SEL": "#3a4a38",
    "CHIP_FILL": "#27322a",
    "CHIP_BORDER": "#374a36",
    "SIDEBAR": "rgba(20,28,21,0.95)",
    "DISABLED_FILL": "#1d261c",
    "IDLE_DOT": "#86a07e",
}

_LIGHT = {
    "BG_APP": "#f3f7ee",          # soft sage-parchment canvas
    "BG_SURFACE": "#ffffff",      # clean cards / surfaces
    "BG_ELEVATED": "#eaf1e2",
    "BG_INSET": "#eaf1e2",        # filled inputs / segmented track
    "BG_OVERLAY": "#ffffff",
    "OVERLAY_BG": "rgba(255, 255, 255, 240)",
    "OVERLAY_TOP": "#ffffff",
    # Morning light over a meadow — a whisper of green at the base.
    "BG_GRAD_TOP": "#f5f9f0",
    "BG_GRAD_BOTTOM": "#e9f0df",
    "CARD_TOP": "#ffffff",
    "CARD_BOTTOM": "#ffffff",
    "CARD_HOVER_TOP": "#ffffff",
    "CONTROL_FILL": "#eaf1e2",
    "BORDER": "#dde7d0",          # soft leaf hairline / divider
    "BORDER_STRONG": "rgba(0,0,0,0.14)",
    "HAIRLINE": "#dde7d0",
    "GLASS_HI": "rgba(0,0,0,0.04)",
    "GLASS_HI_STRONG": "rgba(0,0,0,0.08)",
    "TEXT_PRIMARY": "#1c2a1a",    # deep forest / bark heading
    "TEXT_SECONDARY": "#3c4a36",  # bark body
    "TEXT_MUTED": "#73826a",      # sage meta
    "TEXT_DISABLED": "#aeb9a4",
    "TEXT_ON_ACCENT": "#ffffff",
    "SUCCESS_SOFT": "#d8f3df",
    "SUCCESS_TEXT": "#15803d",
    "DANGER_SOFT": "#fde8e2",
    "DANGER_TEXT": "#c2533c",
    "IDLE_SOFT": "#eaf1e2",
    "IDLE_TEXT": "#6b7a62",
    # Extras (light)
    "SEG_SEL": "#ffffff",
    "CHIP_FILL": "#ffffff",
    "CHIP_BORDER": "#dde7d0",
    "SIDEBAR": "rgba(240,245,234,0.95)",
    "DISABLED_FILL": "#eef2e8",
    "IDLE_DOT": "#9caf90",
}

# Solid semantic colors — shared across modes. (Soft fills + status text live in
# the per-mode palettes above.)
_SEMANTIC = {
    "DANGER": "#ef4444",
    "DANGER_HOVER": "#dc2626",
    "DANGER_PRESSED": "#b91c1c",
    "SUCCESS": "#22c55e",
    "SUCCESS_HOVER": "#16a34a",
    "SUCCESS_PRESSED": "#15803d",
    "WARNING": "#f59e0b",
    # Record identity — gradient pill (floating / overlay record button).
    "RECORD_GRAD_TOP": "#ff5a5a",
    "RECORD_GRAD_BOTTOM": "#f43f5e",
}

_FONT_BASE = '"Inter", -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", sans-serif'

DEFAULT_MODE = "dark"
DEFAULT_ACCENT = "#3a9d5a"  # fresh leaf green

# Recording red (for the live pulse dot) — matches the record gradient identity.
RECORD_RED = QColor(244, 63, 94)


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


def _accent_ramp(base: str, mode: str = "dark") -> dict:
    """Derive the accent-related tokens from a single base accent color.

    For the leaf-green accent (#3A9D5A) the hover/pressed states *darken* into
    deeper foliage, and the soft fill is light on white / translucent on dark.
    """
    c = QColor(base)
    if not c.isValid():
        c = QColor(DEFAULT_ACCENT)
    is_light = mode == "light"
    soft_alpha = 0.12 if is_light else 0.24
    return {
        "ACCENT": c.name(),
        "ACCENT_HOVER": c.darker(112).name(),
        "ACCENT_PRESSED": c.darker(126).name(),
        "ACCENT_2": c.lighter(112).name(),  # subtle lighter companion
        "ACCENT_SOFT": _rgba(c, soft_alpha),
        "ACCENT_ON_SOFT": c.name() if is_light else c.lighter(155).name(),
        "ACCENT_GLOW": _rgba(c, 0.34),
        "BORDER_FOCUS": _rgba(c, 0.60),
        "SELECTION": _rgba(c, 0.20),
        "GLOW": _rgba(c, 0.55),
    }


def _build_tokens(mode: str, accent: str) -> dict:
    palette = _LIGHT if mode == "light" else _DARK
    tokens = {"FONT_BASE": _FONT_BASE}
    tokens.update(palette)
    tokens.update(_SEMANTIC)
    tokens.update(_accent_ramp(accent, mode))
    # Component-library aliases — let widgets ported from the Aktor design use
    # short token names (@SURFACE@, @HEADING@, …) that map onto FlowRecord's set.
    tokens.update({
        "SURFACE": tokens["BG_SURFACE"],
        "CANVAS": tokens["BG_APP"],
        "CONTROL": tokens["CONTROL_FILL"],
        "ELEVATED": tokens["BG_ELEVATED"],
        "HEADING": tokens["TEXT_PRIMARY"],
        "BODY": tokens["TEXT_SECONDARY"],
        "MUTED": tokens["TEXT_MUTED"],
        "ON_ACCENT": tokens["TEXT_ON_ACCENT"],
        "RECORD_TOP": tokens["RECORD_GRAD_TOP"],
        "RECORD_BOTTOM": tokens["RECORD_GRAD_BOTTOM"],
        "ACCENT_SOFT_2": tokens["ACCENT_SOFT"],
        "DISABLED_TEXT": tokens["TEXT_DISABLED"],
    })
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
    border-radius: 14px;
    padding: 6px;
    color: @TEXT_PRIMARY@;
    font-size: 13px;
}
QMenu::item {
    padding: 9px 28px 9px 14px;
    border-radius: 9px;
    margin: 1px 2px;
}
QMenu::item:selected { background-color: @CONTROL_FILL@; color: @TEXT_PRIMARY@; }
QMenu::separator { height: 1px; background: @BORDER@; margin: 6px 8px; }
QMenu::right-arrow, QMenu::tearoff { image: none; width: 0; }

QInputDialog, QMessageBox {
    background-color: @BG_SURFACE@;
}
QInputDialog QLabel, QMessageBox QLabel {
    color: @TEXT_PRIMARY@; font-size: 13px;
}
QInputDialog QLineEdit, QMessageBox QLineEdit {
    background-color: @CONTROL_FILL@; color: @TEXT_PRIMARY@;
    border: 1px solid transparent; border-radius: 11px;
    padding: 9px 12px; font-size: 13px;
    selection-background-color: @ACCENT@;
}
QInputDialog QLineEdit:focus, QMessageBox QLineEdit:focus { border: 1px solid @BORDER_FOCUS@; }
QInputDialog QPushButton, QMessageBox QPushButton {
    background-color: @CONTROL_FILL@; color: @TEXT_PRIMARY@;
    border: 1px solid transparent;
    border-radius: 11px;
    padding: 9px 18px; font-size: 12px; font-weight: 600;
    min-width: 64px;
}
QInputDialog QPushButton:hover, QMessageBox QPushButton:hover {
    background-color: @GLASS_HI_STRONG@; border: 1px solid transparent;
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
/* Secondary (default): control-fill, no visible border, 12px radius. */
QPushButton {
    background-color: @CONTROL_FILL@;
    color: @TEXT_PRIMARY@;
    border: 1px solid transparent;
    border-radius: 12px;
    padding: 9px 18px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton:hover { background-color: @GLASS_HI_STRONG@; }
QPushButton:pressed { background-color: @CONTROL_FILL@; }
QPushButton:disabled {
    background-color: @BG_INSET@; color: @TEXT_DISABLED@; border: 1px solid transparent;
}

/* primary CTAs — solid accent blue (New / Test / Run / Save / Play) */
QPushButton#btnNew, QPushButton#btnTest,
QPushButton#btnRun, QPushButton#btnSave {
    background-color: @ACCENT@; color: @TEXT_ON_ACCENT@; border: 1px solid @ACCENT@;
}
QPushButton#btnNew:hover, QPushButton#btnTest:hover,
QPushButton#btnRun:hover, QPushButton#btnSave:hover {
    background-color: @ACCENT_HOVER@; border: 1px solid @ACCENT_HOVER@;
}
QPushButton#btnNew:pressed, QPushButton#btnTest:pressed,
QPushButton#btnRun:pressed, QPushButton#btnSave:pressed { background-color: @ACCENT_PRESSED@; }
QPushButton#btnRun:disabled, QPushButton#btnSave:disabled {
    background-color: @BG_INSET@; color: @TEXT_DISABLED@; border: 1px solid transparent;
}

/* destructive: soft danger that fills solid on hover */
QPushButton#btnDelete {
    background-color: @DANGER_SOFT@; color: @DANGER_TEXT@;
    border: 1px solid transparent;
}
QPushButton#btnDelete:hover {
    background-color: @DANGER@; color: #ffffff; border: 1px solid @DANGER@;
}
QPushButton#btnDelete:pressed { background-color: @DANGER_PRESSED@; }

/* ghost / icon buttons — borderless, muted, control-fill on hover */
QPushButton#btnEdit, QPushButton#btnSettings,
QPushButton#btnBack, QPushButton#btnStepDelete, QPushButton#btnExport,
QPushButton#btnClear, QPushButton#btnCollapse {
    background-color: transparent;
    color: @TEXT_SECONDARY@;
    border: 1px solid transparent;
}
QPushButton#btnEdit:hover, QPushButton#btnSettings:hover,
QPushButton#btnBack:hover, QPushButton#btnExport:hover,
QPushButton#btnClear:hover, QPushButton#btnCollapse:hover {
    background-color: @CONTROL_FILL@;
    color: @TEXT_PRIMARY@;
    border: 1px solid transparent;
}
QPushButton#btnStepDelete:hover {
    background-color: @DANGER_SOFT@; color: @DANGER_TEXT@; border: 1px solid transparent;
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
    border-radius: 14px;
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
    background-color: @CONTROL_FILL@;
    color: @TEXT_PRIMARY@;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 6px 10px;
    font-size: 12px;
    min-width: 90px;
}
QComboBox:hover { border: 1px solid @BORDER_FOCUS@; }
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

/* ---- Inputs ---- (filled control-fill, no visible border) */
QLineEdit {
    background-color: @CONTROL_FILL@; color: @TEXT_PRIMARY@;
    border: 1px solid transparent; border-radius: 11px;
    padding: 9px 12px; font-size: 13px;
    selection-background-color: @ACCENT@;
}
QLineEdit:focus { border: 1px solid @BORDER_FOCUS@; background-color: @CONTROL_FILL@; }
QLineEdit#searchBox {
    border-radius: 12px;
    padding: 10px 12px 10px 34px;
    font-size: 13px;
    background-color: @CONTROL_FILL@;
}

QDoubleSpinBox {
    background-color: @CONTROL_FILL@; color: @TEXT_PRIMARY@;
    border: 1px solid transparent; border-radius: 10px;
    padding: 6px 8px; font-size: 12px;
}
QDoubleSpinBox:focus { border: 1px solid @BORDER_FOCUS@; }
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background: transparent; border: none; width: 16px;
}
QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow { image: none; width: 0; height: 0; }

@CHECKBOX@

/* ---- Workflow rows (cards) ---- */
QFrame#wfRow {
    background-color: @BG_SURFACE@;
    border: 1px solid @BORDER@;
    border-radius: 14px;
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


# ---- Overlay dock stylesheet (slim vertical bar on the left edge) ----
_OVERLAY_TPL = """
#pillFrame {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 @OVERLAY_TOP@, stop:1 @OVERLAY_BG@);
    border: 1px solid @BORDER_STRONG@;
    border-radius: 22px;
}
/* Square icon buttons stacked vertically. */
QPushButton {
    border-radius: 13px;
    padding: 0;
    border: 1px solid transparent;
    color: #ffffff;
}
/* Record / Stop — red-gradient (record identity). */
QPushButton#btnRecord, QPushButton#btnStop {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 @RECORD_GRAD_TOP@, stop:1 @RECORD_GRAD_BOTTOM@);
    border: 1px solid @RECORD_GRAD_BOTTOM@;
}
QPushButton#btnRecord:hover, QPushButton#btnStop:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #ff6d6d, stop:1 #f6536e);
    border: 1px solid #f6536e;
}
QPushButton#btnRecord:pressed, QPushButton#btnStop:pressed {
    background-color: @DANGER_PRESSED@; border: 1px solid @DANGER_PRESSED@;
}
QPushButton#btnWorkflows, QPushButton#btnPause {
    background-color: @CONTROL_FILL@;
    color: @TEXT_PRIMARY@;
    border: 1px solid transparent;
}
QPushButton#btnWorkflows:hover, QPushButton#btnPause:hover {
    background-color: @GLASS_HI_STRONG@;
    border: 1px solid @BORDER_STRONG@;
}
QPushButton#btnWorkflows:pressed, QPushButton#btnPause:pressed {
    background-color: @CONTROL_FILL@;
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

    changed = Signal()

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
