"""Reusable motion + glow helpers built on QGraphicsDropShadowEffect.

A widget may only hold one QGraphicsEffect, so these helpers create/own a single
drop-shadow per widget and mutate it (color / blur) for elevation and accent
glows. All animations are parented to the effect so they stay alive while running
and are cleaned up afterwards.
"""

from PyQt6.QtCore import QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsDropShadowEffect


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
