"""Crisp, theme-tinted vector icons rendered from inline SVG.

Uses ``PySide6.QtSvg`` (bundled with PySide6 — no extra dependency). Icons are simple
geometric line/fill marks on a 24x24 grid. Each icon is tinted at render time to
the requested color (defaults to the active theme's secondary text color), so the
same source works in light and dark modes.

Usage::

    from flowrecord.ui import icons
    btn.setIcon(icons.icon("play"))
    btn.setIcon(icons.icon("trash", color=theme.manager.color("DANGER").name()))
"""

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer

from flowrecord.ui import theme

# Each entry: inner SVG markup on a 24x24 viewBox. ``fill`` icons are painted
# solid; the rest are drawn as rounded strokes.
_STROKE = {
    "play": '<path d="M8 5l11 7-11 7z"/>',  # also drawn filled below
    "workflows": (
        '<line x1="8" y1="6" x2="20" y2="6"/><line x1="8" y1="12" x2="20" y2="12"/>'
        '<line x1="8" y1="18" x2="20" y2="18"/><circle cx="4" cy="6" r="1"/>'
        '<circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>'
    ),
    "edit": (
        '<path d="M12 20h9"/>'
        '<path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>'
    ),
    "trash": (
        '<polyline points="3 6 5 6 21 6"/>'
        '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>'
        '<path d="M10 11v6"/><path d="M14 11v6"/>'
        '<path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>'
    ),
    "settings": (
        '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/>'
        '<line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/>'
        '<line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/>'
        '<line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/>'
        '<line x1="17" y1="16" x2="23" y2="16"/>'
    ),
    "search": '<circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "smart_wait": (
        '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2"/>'
        '<line x1="9" y1="2" x2="15" y2="2"/><line x1="12" y1="2" x2="12" y2="5"/>'
    ),
    "delay": '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/>',
    "chevron_down": '<polyline points="6 9 12 15 18 9"/>',
    "chevron_right": '<polyline points="9 6 15 12 9 18"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "minus": '<line x1="5" y1="12" x2="19" y2="12"/>',
    "save": (
        '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>'
        '<polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>'
    ),
    "back": '<line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/>',
    "import": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>'
    ),
    "close": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "minimize": '<line x1="5" y1="12" x2="19" y2="12"/>',
    "maximize": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "restore": (
        '<rect x="8" y="8" width="11" height="11" rx="2"/>'
        '<path d="M5 16V6a2 2 0 0 1 2-2h10"/>'
    ),
    "sun": (
        '<circle cx="12" cy="12" r="4"/><line x1="12" y1="2" x2="12" y2="4"/>'
        '<line x1="12" y1="20" x2="12" y2="22"/><line x1="4.2" y1="4.2" x2="5.6" y2="5.6"/>'
        '<line x1="18.4" y1="18.4" x2="19.8" y2="19.8"/><line x1="2" y1="12" x2="4" y2="12"/>'
        '<line x1="20" y1="12" x2="22" y2="12"/><line x1="4.2" y1="19.8" x2="5.6" y2="18.4"/>'
        '<line x1="18.4" y1="5.6" x2="19.8" y2="4.2"/>'
    ),
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>',
    # step-type icons
    "launch_app": (
        '<path d="M5 13c-1.5 1.5-2 5-2 5s3.5-.5 5-2"/>'
        '<path d="M14.5 4.5C18 4 20 6 19.5 9.5c-.5 4-5 8-8.5 9l-5-5c1-3.5 5-8 8.5-9z"/>'
        '<circle cx="14" cy="10" r="1.6"/>'
    ),
    "click": '<path d="M5 3l6 18 2.6-7.4L21 11 5 3z"/>',
    "keypress": (
        '<rect x="2" y="6" width="20" height="12" rx="2"/>'
        '<path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>'
    ),
    "type_text": (
        '<polyline points="4 7 4 4 20 4 20 7"/>'
        '<line x1="9" y1="20" x2="15" y2="20"/><line x1="12" y1="4" x2="12" y2="20"/>'
    ),
    "scroll": (
        '<polyline points="8 7 12 3 16 7"/><polyline points="8 17 12 21 16 17"/>'
        '<line x1="12" y1="3" x2="12" y2="21"/>'
    ),
    # Sidebar nav marks
    "library": (
        '<rect x="3" y="3" width="7" height="7" rx="1.5"/>'
        '<rect x="14" y="3" width="7" height="7" rx="1.5"/>'
        '<rect x="3" y="14" width="7" height="7" rx="1.5"/>'
        '<rect x="14" y="14" width="7" height="7" rx="1.5"/>'
    ),
    "activity": (
        '<line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="12" x2="20" y2="12"/>'
        '<line x1="4" y1="17" x2="14" y2="17"/>'
    ),
    "circle": '<circle cx="12" cy="12" r="9"/>',
    # Botanical marks (nature theme)
    "leaf": (
        '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 '
        '0 5.5-4.78 10-10 10Z"/>'
        '<path d="M2 21c0-3 1.85-5.36 5.08-6"/>'
    ),
    "sprout": (
        '<path d="M7 20h10"/><path d="M12 20c0-6-1.5-9-7-9 0 5 2 8 7 9z"/>'
        '<path d="M12 20c0-7 2-10 7-10 0 5-2 9-7 10z"/>'
    ),
}

# Solid/filled glyphs (record dot, stop, pause, play triangle, grip dots).
_FILL = {
    "record": '<circle cx="12" cy="12" r="6"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2.5"/>',
    "pause": (
        '<rect x="7" y="6" width="3.5" height="12" rx="1.2"/>'
        '<rect x="13.5" y="6" width="3.5" height="12" rx="1.2"/>'
    ),
    "play_fill": '<path d="M8 5l11 7-11 7z"/>',
    "grip": (
        '<circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/>'
        '<circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/>'
        '<circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/>'
    ),
}

_STROKE_TPL = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{c}" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round">'
    "{d}</svg>"
)
_FILL_TPL = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="{c}" stroke="none">{d}</svg>'
)

_RENDER_SCALE = 2  # render at 2x for crisp HiDPI output
_pixmap_cache: dict[tuple, QPixmap] = {}


def _svg_for(name: str, color: str, width: float) -> str:
    if name in _FILL:
        return _FILL_TPL.format(c=color, d=_FILL[name])
    if name in _STROKE:
        return _STROKE_TPL.format(c=color, w=width, d=_STROKE[name])
    raise KeyError(f"Unknown icon: {name!r}")


def pixmap(name: str, color: str | None = None, size: int = 18, width: float = 2.0) -> QPixmap:
    if color is None:
        color = theme.manager.color("TEXT_SECONDARY").name()
    key = (name, color, size, width)
    cached = _pixmap_cache.get(key)
    if cached is not None:
        return cached

    svg = _svg_for(name, color, width)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))

    px = size * _RENDER_SCALE
    pm = QPixmap(px, px)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(float(_RENDER_SCALE))

    _pixmap_cache[key] = pm
    return pm


def icon(name: str, color: str | None = None, size: int = 18, width: float = 2.0) -> QIcon:
    return QIcon(pixmap(name, color, size, width))


def draw_leaf(
    p: QPainter,
    cx: float,
    cy: float,
    length: float,
    *,
    fill: QColor,
    vein: QColor,
    tilt: float = -38.0,
    detail: bool = True,
    vein_w: float = 2.0,
) -> None:
    """Paint a leaf (pointed-oval body + midrib, optionally pinnate side veins).

    Centered on ``(cx, cy)``, ``length`` tip-to-tip, rotated ``tilt`` degrees so
    it sits on a natural diagonal. Shared by the app/tray mark and the Logo
    widget so the botanical signature is identical everywhere.
    """
    from PySide6.QtGui import QPen

    p.save()
    p.translate(cx, cy)
    p.rotate(tilt)
    half = length / 2.0
    w = length * 0.34  # half-width

    body = QPainterPath()
    body.moveTo(0.0, -half)
    body.quadTo(w, 0.0, 0.0, half)
    body.quadTo(-w, 0.0, 0.0, -half)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fill)
    p.drawPath(body)

    pen = QPen(vein, vein_w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Midrib.
    p.drawLine(QPointF(0.0, -half * 0.80), QPointF(0.0, half * 0.86))
    # Pinnate side veins branching up-and-out from the midrib.
    if detail:
        for fy in (0.42, 0.08, -0.26):
            y = fy * half
            reach = w * (0.46 + 0.18 * (1.0 - abs(fy)))
            p.drawLine(QPointF(0.0, y), QPointF(reach, y - length * 0.15))
            p.drawLine(QPointF(0.0, y), QPointF(-reach, y - length * 0.15))
    p.restore()


def app_icon(size: int = 32) -> QIcon:
    """The app/tray mark: an accent rounded-square badge with a white leaf. Uses
    the active accent so it matches the chosen theme."""
    accent = theme.manager.color("ACCENT")
    accent2 = theme.manager.color("ACCENT_HOVER")

    scale = 2
    px = size * scale
    pm = QPixmap(px, px)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    from PySide6.QtGui import QLinearGradient

    grad = QLinearGradient(0, 0, px, px)
    grad.setColorAt(0.0, accent.lighter(118))
    grad.setColorAt(1.0, accent)
    p.setBrush(grad)
    p.setPen(Qt.PenStyle.NoPen)
    pad = px * 0.07
    radius = px * 0.26
    p.drawRoundedRect(QRectF(pad, pad, px - 2 * pad, px - 2 * pad), radius, radius)

    draw_leaf(
        p, px / 2, px / 2, length=px * 0.62,
        fill=QColor("#ffffff"), vein=accent.darker(108),
        detail=True, vein_w=px * 0.03,
    )
    p.end()

    pm.setDevicePixelRatio(float(scale))
    return QIcon(pm)


def clear_cache() -> None:
    _pixmap_cache.clear()
