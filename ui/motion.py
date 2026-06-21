"""Reusable motion + glow helpers built on QGraphicsDropShadowEffect.

A widget may only hold one QGraphicsEffect, so these helpers create/own a single
drop-shadow per widget and mutate it (color / blur) for elevation and accent
glows. All animations are parented to the effect so they stay alive while running
and are cleaned up afterwards.
"""

from PySide6.QtCore import (
    QEasingCurve, QParallelAnimationGroup, QPoint, QPointF, QPropertyAnimation,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QStackedWidget


def attach_shadow(widget, color="#000000", blur=22, dx=0, dy=6, alpha=120):
    """Create and attach a drop shadow / glow effect to a widget."""
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(dx, dy)
    c = QColor(color)
    c.setAlpha(alpha)
    eff.setColor(c)
    widget.setGraphicsEffect(eff)
    return eff


def set_glow(eff, color, blur=None, alpha=160, dx=0, dy=0):
    """Set the shadow color/offset immediately. ``blur`` is optional so callers
    can change color while animating the blur separately."""
    if eff is None:
        return
    c = QColor(color)
    c.setAlpha(alpha)
    eff.setColor(c)
    if blur is not None:
        eff.setBlurRadius(blur)
    eff.setOffset(dx, dy)


def animate_blur(eff, end, duration=170):
    if eff is None:
        return None
    anim = QPropertyAnimation(eff, b"blurRadius", eff)
    anim.setDuration(duration)
    anim.setStartValue(eff.blurRadius())
    anim.setEndValue(float(end))
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def animate_offset(eff, end_dy, duration=170):
    """Smoothly animate a drop shadow's vertical offset (for hover lift)."""
    if eff is None:
        return None
    anim = QPropertyAnimation(eff, b"offset", eff)
    anim.setDuration(duration)
    anim.setStartValue(eff.offset())
    anim.setEndValue(QPointF(0.0, float(end_dy)))
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)
    anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


class SlidingStackedWidget(QStackedWidget):
    """A QStackedWidget that slides between pages instead of switching instantly.

    Direction is inferred from index order (higher index slides in from the
    right, lower from the left), so going deeper / coming back feels natural.
    No opacity effects are used, so it composes safely with the cards' drop
    shadows.
    """

    def __init__(self, parent=None, duration: int = 260):
        super().__init__(parent)
        self._duration = duration
        self._easing = QEasingCurve.Type.OutCubic
        self._active = False

    def slide_to_widget(self, widget) -> None:
        self.slide_to(self.indexOf(widget))

    def slide_to(self, index: int) -> None:
        if index < 0 or index >= self.count():
            return
        if self._active or index == self.currentIndex() or self.currentWidget() is None:
            self.setCurrentIndex(index)
            return

        forward = index > self.currentIndex()
        cur = self.currentWidget()
        nxt = self.widget(index)
        w, h = self.width(), self.height()
        start_x = w if forward else -w

        nxt.setGeometry(0, 0, w, h)
        nxt.move(start_x, 0)
        nxt.show()
        nxt.raise_()
        self._active = True

        a_out = QPropertyAnimation(cur, b"pos", self)
        a_out.setDuration(self._duration)
        a_out.setEasingCurve(self._easing)
        a_out.setStartValue(QPoint(0, 0))
        a_out.setEndValue(QPoint(-start_x, 0))

        a_in = QPropertyAnimation(nxt, b"pos", self)
        a_in.setDuration(self._duration)
        a_in.setEasingCurve(self._easing)
        a_in.setStartValue(QPoint(start_x, 0))
        a_in.setEndValue(QPoint(0, 0))

        group = QParallelAnimationGroup(self)
        group.addAnimation(a_out)
        group.addAnimation(a_in)

        def _done():
            self.setCurrentIndex(index)
            cur.move(0, 0)  # restore for when it's shown again
            self._active = False

        group.finished.connect(_done)
        group.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)
