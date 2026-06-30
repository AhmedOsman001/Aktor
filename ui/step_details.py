"""Step inspector — shows everything captured for a single recorded step:
its action, the matched UI element, the element tree path, geometry, self-heal
signals, variable binding, and timing.
"""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from flowrecord.ui import theme
from flowrecord.ui.components import _style
from flowrecord.ui.title_bar import FramelessDialog


class StepDetailsDialog(FramelessDialog):
    def __init__(self, step, index=None, parent=None):
        title = "Step details" if index is None else f"Step {index} details"
        super().__init__(title, show_logo=False, parent=parent)
        self.setMinimumWidth(480)
        self.resize(520, 600)
        self._step = step

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("background: transparent;")
        scroll.viewport().setStyleSheet("background: transparent;")

        self._body = QWidget()
        self._v = QVBoxLayout(self._body)
        self._v.setContentsMargins(24, 16, 24, 22)
        self._v.setSpacing(6)
        self._build(step)
        self._v.addStretch(1)
        scroll.setWidget(self._body)
        self.content_layout().addWidget(scroll)

        theme.manager.changed.connect(self._apply)
        self._apply()

    # ---- builders ----
    def _section(self, text: str) -> None:
        lbl = QLabel(text)
        lbl.setObjectName("sdSection")
        self._v.addSpacing(10)
        self._v.addWidget(lbl)

    def _kv(self, key: str, value) -> None:
        if value is None or value == "":
            return
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        k = QLabel(key)
        k.setObjectName("sdKey")
        k.setFixedWidth(118)
        k.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        val = QLabel(str(value))
        val.setObjectName("sdVal")
        val.setWordWrap(True)
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        h.addWidget(k)
        h.addWidget(val, 1)
        self._v.addWidget(row)

    def _tree(self, parent_path: str) -> None:
        parts = [p.strip() for p in parent_path.split(">") if p.strip()]
        if not parts:
            return
        lines = []
        for i, part in enumerate(parts):
            prefix = ("    " * i + "└ ") if i else ""
            lines.append(prefix + part)
        tree = QLabel("\n".join(lines))
        tree.setObjectName("sdTree")
        tree.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._v.addWidget(tree)

    def _build(self, s) -> None:
        # ---- Action ----
        self._section("ACTION")
        self._kv("Type", s.type)
        self._kv("Description", s.description)
        if s.variable:
            self._kv("Variable", "{{ %s }}" % s.variable)

        # ---- Element ----
        if any([s.element_name, s.element_type, s.automation_id, s.class_name,
                s.app_name, s.window_title]):
            self._section("ELEMENT")
            self._kv("Name", s.element_name)
            self._kv("Control type", s.element_type)
            self._kv("AutomationId", s.automation_id)
            self._kv("Class", s.class_name)
            self._kv("App", s.app_name)
            self._kv("Window", s.window_title)

        # ---- Tree path ----
        if s.parent_path:
            self._section("TREE PATH")
            self._tree(s.parent_path)

        # ---- Geometry ----
        if any(v is not None for v in (s.x, s.x2, s.element_rect, s.x_relative)):
            self._section("GEOMETRY")
            if s.x is not None:
                self._kv("Point", f"({s.x}, {s.y})")
            if s.x2 is not None:
                self._kv("Drag end", f"({s.x2}, {s.y2})")
            if s.x_relative is not None:
                self._kv("Relative", f"({s.x_relative}, {s.y_relative}) of window")
            if s.element_rect:
                self._kv("Element rect", s.element_rect)

        # ---- Self-heal anchor ----
        if s.anchor:
            self._section("ANCHOR (self-heal)")
            try:
                a = json.loads(s.anchor)
                self._kv("Name", a.get("name"))
                self._kv("AutomationId", a.get("automation_id"))
                self._kv("Control type", a.get("control_type"))
                if a.get("dx") is not None:
                    self._kv("Offset", f"dx {a.get('dx')}, dy {a.get('dy')}")
                if a.get("rect"):
                    self._kv("Rect", ", ".join(str(v) for v in a["rect"]))
            except Exception:
                self._kv("Raw", s.anchor)

        # ---- Input ----
        if s.keys or s.text or s.scroll_dx or s.scroll_dy:
            self._section("INPUT")
            self._kv("Keys", s.keys)
            self._kv("Text", repr(s.text) if s.text else None)
            if s.scroll_dx or s.scroll_dy:
                self._kv("Scroll", f"dx {s.scroll_dx}, dy {s.scroll_dy}")

        # ---- Timing / behaviour ----
        self._section("TIMING")
        self._kv("Enabled", "yes" if s.enabled else "no")
        if s.smart_wait_enabled:
            self._kv("Smart Wait", f"on · {s.smart_wait_timeout:.0f}s · {s.smart_wait_on_timeout}")
        else:
            self._kv("Delay after", f"{s.delay_after:.2f}s")

    def _apply(self) -> None:
        self.setStyleSheet(theme.manager.qss_dialog() + _style(
            "#sdSection { color: @ACCENT_ON_SOFT@; font-size: 11px; font-weight: 700;"
            " letter-spacing: 1px; padding-top: 2px; }"
            "#sdKey { color: @MUTED@; font-size: 12px; }"
            "#sdVal { color: @HEADING@; font-size: 12px; }"
            "#sdTree { color: @BODY@; font-size: 12px; font-family: 'Cascadia Mono',"
            " 'Consolas', monospace; background: @CONTROL@; border-radius: 8px; padding: 10px 12px; }"
        ))
