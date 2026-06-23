import logging
import math
import time
from enum import Enum
from typing import Optional

from PySide6.QtCore import (
    Qt,
    QTimer,
    QThread,
    QPoint,
    QRect,
    QSize,
    Signal,
    QObject,
    QPropertyAnimation,
    QVariantAnimation,
    QEasingCurve,
    QParallelAnimationGroup,
)
from PySide6.QtGui import QColor, QPainter, QCursor, QPen, QLinearGradient, QImage
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QFrame,
    QProgressBar,
    QGraphicsDropShadowEffect,
)

from flowrecord.ui import theme, icons, motion

logger = logging.getLogger(__name__)

# Vertical dock metrics — a slim bar tucked against the left screen edge.
BAR_WIDTH = 56            # visible frame width
BAR_HEIGHT = 190          # visible frame height (content is centered within)
_SHADOW_PAD = 22          # room around the frame so the drop shadow isn't clipped

OVERLAY_WIDTH = BAR_WIDTH + _SHADOW_PAD * 2
OVERLAY_HEIGHT = BAR_HEIGHT + _SHADOW_PAD * 2

_EDGE_GAP = 8             # gap from the screen edge when revealed (out)
_PEEK = 6                 # thin sliver left visible when hidden at rest
_REVEAL_TRIGGER = 10      # how close to the edge the cursor must be to reveal
_REVEAL_HOLD_MS = 1500    # stay revealed this long after show / a state change

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


class _PlaybackGlow(QWidget):
    """A soft, breathing multi-hue glow around the screen edges shown while a
    workflow replays — ambient feedback like Siri / Google Assistant.

    The window is full-screen, translucent, always-on-top and **click-through**
    (``WindowTransparentForInput`` + ``WA_TransparentForMouseEvents``) so it can
    never intercept the mouse/keyboard the player is driving. Hues flow around
    the perimeter and the whole thing breathes; it fades in/out smoothly.
    """

    DEPTH = 150        # how far the glow reaches inward from each edge (px)
    _FPS_MS = 8        # ~120 fps

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background: transparent;")
        self._phase = 0.0
        self._intensity = 0.0      # eased 0..1 envelope
        self._target = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(self._FPS_MS)
        self._timer.timeout.connect(self._tick)

    def start(self):
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        self._target = 1.0
        self.show()
        self.raise_()
        if not self._timer.isActive():
            self._timer.start()

    def stop(self):
        # Fade out, then hide in _tick once the envelope reaches ~0.
        self._target = 0.0

    def _tick(self):
        self._phase += 0.006                                  # scaled for 120 fps
        self._intensity += (self._target - self._intensity) * 0.035

        if self._target == 0.0 and self._intensity < 0.02:
            self._intensity = 0.0
            self._timer.stop()
            self.hide()
            return
        self.update()

    def _flow_gradient(self, x0, y0, x1, y1,
                       perim_start: float, edge_len: float, perim: float) -> QLinearGradient:
        """Colour running ALONG an edge: one coherent leaf-green whose brightness
        follows a smooth sine wave travelling continuously around the whole
        perimeter — light flowing, not the hue changing randomly."""
        acc = theme.manager.color("ACCENT")
        hh, s, vv, _ = acc.getHsv()
        g = QLinearGradient(x0, y0, x1, y1)
        steps = 20                 # many stops -> a smooth wave, no banding
        cycles = 3.0               # bright crests around the perimeter
        speed = 0.22               # how fast the wave travels
        for k in range(steps + 1):
            f = k / steps
            pf = (perim_start + f * edge_len) / perim
            wave = 0.5 + 0.5 * math.sin(2 * math.pi * (pf * cycles - self._phase * speed))
            hue = (hh - 10 * wave) % 360                      # stays green; only a hair lighter at crests
            sat = max(150, min(255, s + 20))
            val = min(255, vv + int(75 * wave))
            alpha = int((0.45 + 0.55 * wave) * 255)           # never fully dark; crests pop
            g.setColorAt(f, QColor.fromHsv(int(hue), sat, val, alpha))
        return g

    def _mask(self, x0, y0, x1, y1) -> QLinearGradient:
        """A white→transparent alpha mask running from the outer edge inward,
        with a soft (non-linear) falloff so the glow melts away smoothly."""
        g = QLinearGradient(x0, y0, x1, y1)
        g.setColorAt(0.0, QColor(255, 255, 255, 255))
        g.setColorAt(0.40, QColor(255, 255, 255, 160))
        g.setColorAt(0.72, QColor(255, 255, 255, 45))
        g.setColorAt(1.0, QColor(255, 255, 255, 0))
        return g

    def _band_image(self, iw: int, ih: int, flow, mask) -> QImage:
        """One smoothly-faded edge band: the flowing colour multiplied by the
        perpendicular alpha mask. A single continuous gradient — no strips, no
        seams."""
        img = QImage(iw, ih, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(0)
        ip = QPainter(img)
        ip.setPen(Qt.PenStyle.NoPen)
        ip.fillRect(0, 0, iw, ih, flow)
        ip.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        ip.fillRect(0, 0, iw, ih, mask)
        ip.end()
        return img

    def paintEvent(self, event):
        if self._intensity <= 0.01:
            return
        p = QPainter(self)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            p.end()
            return
        breathe = 0.86 + 0.14 * math.sin(self._phase * 1.6)
        d = int(min(self.DEPTH, w // 2, h // 2))
        per = 2.0 * (w + h)

        # Each edge: a coherent green band, faded inward by a smooth mask. The
        # along-edge gradients run in the perimeter direction (top→right→bottom→
        # left) so the brightness wave circulates seamlessly around the screen.
        top = self._band_image(w, d, self._flow_gradient(0, 0, w, 0, 0.0, w, per),
                               self._mask(0, 0, 0, d))
        right = self._band_image(d, h, self._flow_gradient(0, 0, 0, h, w, h, per),
                                 self._mask(d, 0, 0, 0))
        bottom = self._band_image(w, d, self._flow_gradient(w, 0, 0, 0, w + h, w, per),
                                  self._mask(0, d, 0, 0))
        left = self._band_image(d, h, self._flow_gradient(0, h, 0, 0, 2 * w + h, h, per),
                                self._mask(0, 0, d, 0))

        p.setOpacity(min(1.0, self._intensity * breathe * 0.82))
        p.drawImage(0, 0, top)
        p.drawImage(w - d, 0, right)
        p.drawImage(0, h - d, bottom)
        p.drawImage(0, 0, left)
        p.setOpacity(1.0)
        p.end()


class _OverlayBar(QWidget):
    """A slim vertical control dock pinned to the left screen edge.

    Buttons are icon-only and stacked vertically. The bar tucks itself to the
    edge when idle (leaving a small sliver) and slides out when the cursor
    approaches or whenever it has something to show (recording / playing /
    smart-waiting). All position changes are animated.
    """

    record_clicked = Signal()
    stop_clicked = Signal()
    pause_clicked = Signal()
    workflows_clicked = Signal()

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(OVERLAY_WIDTH)   # height is sized to the content per state
        self.setWindowOpacity(_TARGET_OPACITY)

        self._main_layout = QHBoxLayout(self)
        self._main_layout.setContentsMargins(
            _SHADOW_PAD, _SHADOW_PAD, _SHADOW_PAD, _SHADOW_PAD
        )
        self._main_layout.setSpacing(0)

        self._frame = QFrame()
        self._frame.setObjectName("pillFrame")

        # Elevation / glow (color shifts per state — see _apply_state_glow). The
        # bar sits on the left edge, so the shadow is cast to the right.
        self._shadow = QGraphicsDropShadowEffect(self._frame)
        self._shadow.setBlurRadius(28)
        self._shadow.setColor(QColor(0, 0, 0, 180))
        self._shadow.setOffset(5, 0)
        self._frame.setGraphicsEffect(self._shadow)
        self._state = OverlayState.IDLE
        self._paused_state = False

        self._inner_layout = QVBoxLayout(self._frame)
        self._inner_layout.setContentsMargins(8, 12, 8, 12)
        self._inner_layout.setSpacing(10)

        self._btn_record = self._make_button("btnRecord", "Record")
        self._btn_stop = self._make_button("btnStop", "Stop")
        self._btn_pause = self._make_button("btnPause", "Pause")
        self._btn_workflows = self._make_button("btnWorkflows", "Workflows")

        self._dot = _RecordDot()
        self._sep = QFrame()
        self._sep.setFixedHeight(1)
        self._sep.setFixedWidth(30)

        self._step_label = self._make_label()
        self._progress_label = self._make_label()
        self._smart_wait_label = self._make_label()

        self._smart_wait_bar = QProgressBar()
        self._smart_wait_bar.setTextVisible(False)
        self._smart_wait_bar.setFixedHeight(6)
        self._smart_wait_bar.setFixedWidth(40)
        self._smart_wait_bar.setRange(0, 100)
        self._smart_wait_bar.setValue(0)

        self._btn_record.clicked.connect(self.record_clicked.emit)
        self._btn_stop.clicked.connect(self.stop_clicked.emit)
        self._btn_pause.clicked.connect(self.pause_clicked.emit)
        self._btn_workflows.clicked.connect(self.workflows_clicked.emit)

        self._main_layout.addWidget(self._frame)

        # Dock state (set before the first layout so geometry helpers resolve).
        self._side = "left"       # "left" | "right" screen edge
        self._docked_out = False  # False = hidden at edge, True = revealed
        self._dock_anim: Optional[QPropertyAnimation] = None
        self._reveal_until = 0.0

        self._set_idle_layout()
        self._fit()   # size to content + position at the docked edge

        # Poll the cursor (kept for future auto-hide; currently always docked out).
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(140)
        self._hover_timer.timeout.connect(self._update_dock)

        self._apply_theme()
        theme.manager.changed.connect(self._apply_theme)

    # ---- builders ----
    def _make_button(self, obj_name: str, tip: str) -> QPushButton:
        b = QPushButton()
        b.setObjectName(obj_name)
        b.setToolTip(tip)
        b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        b.setFixedSize(40, 40)
        b.setIconSize(QSize(18, 18))
        return b

    def _make_label(self) -> QLabel:
        lab = QLabel("")
        lab.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lab.setFixedWidth(BAR_WIDTH - 16)
        return lab

    # ---- geometry / docking ----
    def _screen(self) -> QRect:
        return QApplication.primaryScreen().geometry()

    def _is_right(self) -> bool:
        return self._side == "right"

    def _screen_right(self) -> int:
        sc = self._screen()
        return sc.left() + sc.width()

    def _center_y(self) -> int:
        sc = self._screen()
        return sc.top() + (sc.height() - self.height()) // 2

    def _fit(self) -> None:
        """Size the window to the current content and place it at the position
        matching the current dock state (rest vs out). Called after every layout
        rebuild."""
        self._inner_layout.activate()
        self.setFixedHeight(self._frame.sizeHint().height() + 2 * _SHADOW_PAD)
        target = self._out_x() if self._docked_out else self._rest_x()
        self.move(target, self._center_y())

    def _rest_x(self) -> int:
        """Hidden at the edge — only a thin _PEEK sliver pokes onto the screen."""
        if self._is_right():
            return self._screen_right() - _PEEK - _SHADOW_PAD
        return self._screen().left() + _PEEK - BAR_WIDTH - _SHADOW_PAD

    def _out_x(self) -> int:
        """Fully revealed, a small gap from the edge."""
        if self._is_right():
            return self._screen_right() - _EDGE_GAP - BAR_WIDTH - _SHADOW_PAD
        return self._screen().left() + _EDGE_GAP - _SHADOW_PAD

    def _offscreen_x(self) -> int:
        """Fully off the edge — the entrance slide-in start point."""
        if self._is_right():
            return self._screen_right() + _SHADOW_PAD
        return self._screen().left() - BAR_WIDTH - _SHADOW_PAD

    def _out_frame_rect(self) -> QRect:
        """The frame rect at the revealed position — used for the hover zone."""
        h = self.height() - 2 * _SHADOW_PAD
        return QRect(self._out_x() + _SHADOW_PAD, self._center_y() + _SHADOW_PAD,
                     BAR_WIDTH, h)

    def _hovering(self) -> bool:
        r = self._out_frame_rect()
        sc = self._screen()
        top = r.top() - 14
        height = r.height() + 28
        if self._docked_out:
            # Already revealed: stay out while the cursor is over the bar (+ a
            # small margin) so it doesn't flicker shut.
            if self._is_right():
                x0 = r.left() - 10
                zone = QRect(x0, top, self._screen_right() - x0, height)
            else:
                zone = QRect(sc.left(), top, (r.right() + 10) - sc.left(), height)
        else:
            # Hidden: a thin but full-height strip along the edge, so the bar
            # reveals whenever the cursor reaches that side of the screen.
            if self._is_right():
                zone = QRect(self._screen_right() - _REVEAL_TRIGGER, sc.top(),
                             _REVEAL_TRIGGER, sc.height())
            else:
                zone = QRect(sc.left(), sc.top(), _REVEAL_TRIGGER, sc.height())
        return zone.contains(QCursor.pos())

    def set_side(self, side: str) -> None:
        """Dock the bar to the 'left' or 'right' screen edge."""
        side = "right" if side == "right" else "left"
        self._side = side
        self.move((self._out_x() if self._docked_out else self._rest_x()),
                  self._center_y())
        self._apply_state_glow(self._state)

    def _hold_revealed(self):
        self._reveal_until = time.monotonic() + _REVEAL_HOLD_MS / 1000.0

    def _want_out(self) -> bool:
        # Reveal while active (recording/playing/waiting), briefly after a change,
        # or when hovered; otherwise hide at the edge.
        if self._state != OverlayState.IDLE:
            return True
        if time.monotonic() < self._reveal_until:
            return True
        return self._hovering()

    def _update_dock(self):
        want = self._want_out()
        if want == self._docked_out:
            return
        self._docked_out = want
        self._slide_x(self._out_x() if want else self._rest_x())

    def _slide_x(self, target_x: int):
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(240)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.setStartValue(self.pos())
        anim.setEndValue(QPoint(target_x, self._center_y()))
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._dock_anim = anim

    # ---- theming ----
    def _apply_theme(self):
        toks = theme.manager.tokens()
        self._frame.setStyleSheet(theme.manager.qss_overlay())

        # Vector icons (retinted for the active theme).
        white = "#ffffff"
        prim = toks["TEXT_PRIMARY"]
        self._btn_record.setIcon(icons.icon("record", white, 16))
        self._btn_stop.setIcon(icons.icon("stop", white, 16))
        self._btn_workflows.setIcon(icons.icon("workflows", prim, 18))
        self.set_paused(self._paused_state)
        self._sep.setStyleSheet(
            f"background-color: {toks['GLASS_HI_STRONG']}; border: none;"
        )
        label_css = (
            f"color: {toks['TEXT_SECONDARY']}; font-size: 11px; "
            f"font-weight: 700; background: transparent;"
        )
        for lab in (self._step_label, self._progress_label, self._smart_wait_label):
            lab.setStyleSheet(label_css)
        self._smart_wait_bar.setStyleSheet(
            "QProgressBar { background-color: " + toks["GLASS_HI_STRONG"]
            + "; border: none; border-radius: 3px; }"
            " QProgressBar::chunk { background-color: " + toks["ACCENT"]
            + "; border-radius: 3px; }"
        )
        self._apply_state_glow(self._state)

    # ---- layout state (vertical, centered with top/bottom stretches) ----
    def _clear_layout(self):
        # Remove every item (widgets + stretches) from the layout. Widgets stay
        # children of the frame and are hidden by _hide_all; nothing is left
        # managed, so the next build can't overlap the previous one.
        while self._inner_layout.count():
            self._inner_layout.takeAt(0)

    def _hide_all(self):
        for w in (self._btn_record, self._btn_stop, self._btn_pause,
                  self._btn_workflows, self._dot, self._sep,
                  self._step_label, self._progress_label,
                  self._smart_wait_label, self._smart_wait_bar):
            w.hide()

    def _add(self, widget):
        self._inner_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignHCenter)
        widget.show()

    def _set_idle_layout(self):
        self._clear_layout()
        self._hide_all()
        self._add(self._btn_record)
        self._add(self._btn_workflows)

    def _set_recording_layout(self):
        self._clear_layout()
        self._hide_all()
        self.set_paused(False)  # each recording starts un-paused
        self._add(self._dot)
        self._add(self._step_label)
        self._add(self._sep)
        self._add(self._btn_pause)
        self._add(self._btn_stop)

    def _set_playing_layout(self):
        self._clear_layout()
        self._hide_all()
        self._add(self._progress_label)
        self._add(self._sep)
        self._add(self._btn_stop)

    def _set_smart_waiting_layout(self):
        self._clear_layout()
        self._hide_all()
        self._add(self._smart_wait_label)
        self._add(self._smart_wait_bar)
        self._add(self._sep)
        self._add(self._btn_stop)

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
        # Resize to the new content and resolve positions synchronously so the
        # freshly shown widgets never flash at stale/overlapping spots.
        self._fit()
        self._apply_state_glow(state)
        # A state change is worth seeing — reveal now, then auto-hide when idle.
        self._hold_revealed()
        self._update_dock()

    def _apply_state_glow(self, state: OverlayState):
        dx = -5 if self._is_right() else 5  # cast the shadow into the screen
        if state == OverlayState.RECORDING:
            motion.set_glow(self._shadow, theme.RECORD_RED.name(), blur=46, alpha=185, dx=dx)
        elif state in (OverlayState.PLAYING, OverlayState.SMART_WAITING):
            motion.set_glow(self._shadow, theme.manager.color("ACCENT").name(),
                            blur=44, alpha=165, dx=dx)
        else:
            motion.set_glow(self._shadow, "#000000", blur=28, alpha=170, dx=dx)

    def set_paused(self, paused: bool):
        self._paused_state = paused
        prim = theme.manager.color("TEXT_PRIMARY").name()
        if paused:
            self._btn_pause.setToolTip("Resume")
            self._btn_pause.setIcon(icons.icon("play_fill", prim, 16))
        else:
            self._btn_pause.setToolTip("Pause")
            self._btn_pause.setIcon(icons.icon("pause", prim, 16))

    def set_step_count(self, n: int):
        self._step_label.setText(str(n))
        self._step_label.setToolTip(f"{n} steps recorded")

    def set_playback_progress(self, current: int, total: int, description: str):
        self._progress_label.setText(f"{current}/{total}")
        self._progress_label.setToolTip(description or "")

    def show_smart_wait(self, element_name: str, elapsed: int, timeout: int):
        self._smart_wait_label.setText(f"{elapsed}s")
        self._smart_wait_label.setToolTip(
            f"Waiting for '{element_name or 'element'}'  ·  {elapsed}s / {timeout}s"
        )
        self._smart_wait_bar.setRange(0, max(1, timeout))
        self._smart_wait_bar.setValue(min(elapsed, timeout))

    def show_smart_wait_found(self, element_name: str, elapsed: int):
        self._smart_wait_label.setText("✓")
        self._smart_wait_label.setToolTip(
            f"Found '{element_name or 'element'}' after {elapsed}s"
        )
        self._smart_wait_bar.setValue(self._smart_wait_bar.maximum())

    def show_smart_wait_timeout(self, element_name: str, timeout: int):
        self._smart_wait_label.setText("!")
        self._smart_wait_label.setToolTip(
            f"'{element_name or 'element'}' not found after {timeout}s"
        )

    # ---- entrance animation (slide in from the edge) ----
    def _animate_in(self):
        # Reveal on launch (slide in fully) so the user sees where it lives; the
        # hover poll tucks it to the edge once the reveal hold expires.
        self.setWindowOpacity(0.0)
        self._docked_out = True
        self._hold_revealed()
        self.move(self._offscreen_x(), self._center_y())

        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(220)
        fade.setStartValue(0.0)
        fade.setEndValue(_TARGET_OPACITY)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)

        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(300)
        slide.setStartValue(QPoint(self._offscreen_x(), self._center_y()))
        slide.setEndValue(QPoint(self._out_x(), self._center_y()))
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(fade)
        group.addAnimation(slide)
        group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)

    def showEvent(self, event):
        super().showEvent(event)
        self._animate_in()
        self._hover_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._hover_timer.stop()


class OverlayController(QObject):
    record_requested = Signal()
    stop_requested = Signal()
    pause_requested = Signal()
    workflows_requested = Signal()

    # Internal: marshal a callable onto the GUI thread. The player invokes the
    # overlay from its worker thread; touching Qt widgets there crashes/hangs Qt,
    # so all widget mutations are funneled through this queued signal.
    _invoke = Signal(object)

    def __init__(self):
        super().__init__()
        self._bar = _OverlayBar()
        self._flash = _BorderFlash()
        self._glow = _PlaybackGlow()
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
            self._glow.stop()
        self._dispatch(_do)

    def set_side(self, side: str):
        self._dispatch(lambda: self._bar.set_side(side))

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
        # Ambient playback glow — on while replaying / smart-waiting, off otherwise.
        if state in (OverlayState.PLAYING, OverlayState.SMART_WAITING):
            self._glow.start()
        else:
            self._glow.stop()

    def set_step_count(self, n: int):
        self._dispatch(lambda: self._bar.set_step_count(n))

    def set_paused(self, paused: bool):
        self._dispatch(lambda: self._bar.set_paused(paused))

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
