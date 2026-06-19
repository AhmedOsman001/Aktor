import json
import logging
import traceback
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import Qt, QSize, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from flowrecord.config import APP_NAME, DEFAULT_RECORD_HOTKEY
from flowrecord.models import ActionStep, Trigger, Workflow
from flowrecord.storage.workflow_store import (
    delete_workflow,
    get_all_workflows,
    get_workflow,
    save_steps,
    save_workflow,
    update_trigger,
    update_workflow_name,
)
from flowrecord.ui import theme, icons, motion, win_effects
from flowrecord.ui.log_panel import LogPanel

logger = logging.getLogger(__name__)


def _connect_theme(window, on_change=None) -> None:
    """Apply the dialog stylesheet now and keep it live across theme changes.

    ``on_change`` (optional) is invoked on every *subsequent* theme change so the
    window can re-tint its vector icons. The subscription is dropped when the
    window closes so the long-lived theme manager doesn't pin the dialog.
    """
    def _chrome():
        win_effects.apply_window_chrome(window, theme.manager.is_dark())

    window.setStyleSheet(theme.manager.qss_dialog())
    _chrome()
    QTimer.singleShot(0, _chrome)  # re-apply once the native handle is shown

    def _reapply():
        window.setStyleSheet(theme.manager.qss_dialog())
        _chrome()
        if on_change is not None:
            on_change()

    def _disconnect(*_):
        try:
            theme.manager.changed.disconnect(_reapply)
        except (TypeError, RuntimeError):
            pass

    theme.manager.changed.connect(_reapply)
    window.finished.connect(_disconnect)


def _format_last_run(dt: Optional[datetime]) -> str:
    if not dt:
        return "Never run"
    now = datetime.now()
    if dt.date() == now.date():
        return "Last run: today"
    delta_days = (now.date() - dt.date()).days
    if delta_days == 1:
        return "Last run: yesterday"
    if delta_days < 7:
        return f"Last run: {delta_days}d ago"
    return f"Last run: {dt.strftime('%b %d')}"


class _ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        else:
            super().mousePressEvent(event)


class _SearchBox(QLineEdit):
    """A search input with a leading magnifier glyph."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("searchBox")
        self.setPlaceholderText("Search workflows\u2026")
        self.setClearButtonEnabled(True)
        self.setTextMargins(30, 0, 0, 0)
        self._icon = QLabel(self)
        self._icon.setStyleSheet("background: transparent; border: none;")
        self._icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.retint()

    def retint(self):
        self._icon.setPixmap(
            icons.pixmap("search", theme.manager.color("TEXT_MUTED").name(), 15)
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._icon.move(11, (self.height() - self._icon.sizeHint().height()) // 2)
        self._icon.raise_()


_STEP_ICON_NAMES = {
    "launch_app": "launch_app",
    "click": "click",
    "double_click": "click",
    "right_click": "click",
    "middle_click": "click",
    "keypress": "keypress",
    "type_text": "type_text",
    "scroll": "scroll",
    "delay": "delay",
}


def _step_icon_name(step_type: str) -> str:
    return _STEP_ICON_NAMES.get(step_type, "click")


class StepRowWidget(QFrame):
    delete_clicked = pyqtSignal(object)
    changed = pyqtSignal()

    def __init__(self, step: ActionStep, index: int, parent=None):
        super().__init__(parent)
        self._step = step
        # Smart Wait only makes sense for steps that target a UI element.
        self._sw_supported = bool(step.element_name)
        self.setObjectName("stepRow")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        handle = QLabel()
        handle.setObjectName("dragHandle")
        handle.setToolTip("Drag to reorder")
        handle.setCursor(Qt.CursorShape.SizeVerCursor)
        handle.setFixedWidth(18)
        handle.setPixmap(
            icons.pixmap("grip", theme.manager.color("TEXT_DISABLED").name(), 16)
        )

        self._index_label = QLabel(f"{index + 1:02d}")
        self._index_label.setObjectName("stepIndex")
        self._index_label.setFixedWidth(28)

        icon = QLabel()
        icon.setObjectName("stepIcon")
        icon.setFixedWidth(22)
        icon.setPixmap(
            icons.pixmap(_step_icon_name(step.type), theme.manager.color("ACCENT").name(), 16)
        )

        desc_text = step.description or f"{step.type} step"
        desc = QLabel(desc_text)
        desc.setObjectName("stepDesc")
        desc.setToolTip(desc_text)

        self._delay = QDoubleSpinBox()
        self._delay.setRange(0.0, 30.0)
        self._delay.setSingleStep(0.1)
        self._delay.setDecimals(1)
        self._delay.setSuffix(" s")
        self._delay.setValue(step.delay_after)
        self._delay.setFixedWidth(78)
        self._delay.setCorrectionMode(QDoubleSpinBox.CorrectionMode.CorrectToNearestValue)
        self._delay.valueChanged.connect(self._on_delay)

        # ---- Smart Wait column ----
        self._sw_toggle = QPushButton()
        self._sw_toggle.setObjectName("btnSmartWait")
        self._sw_toggle.setCheckable(True)
        self._sw_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sw_toggle.setFixedWidth(74)
        self._sw_toggle.setIconSize(QSize(14, 14))
        self._sw_toggle.clicked.connect(self._on_toggle_smart_wait)

        # Timeout + on-timeout controls, shown only when Smart Wait is ON.
        self._sw_controls = QWidget()
        sw_layout = QHBoxLayout(self._sw_controls)
        sw_layout.setContentsMargins(0, 0, 0, 0)
        sw_layout.setSpacing(6)

        self._sw_timeout = QDoubleSpinBox()
        self._sw_timeout.setRange(1.0, 300.0)
        self._sw_timeout.setDecimals(0)
        self._sw_timeout.setSingleStep(1.0)
        self._sw_timeout.setSuffix(" s")
        self._sw_timeout.setValue(step.smart_wait_timeout)
        self._sw_timeout.setFixedWidth(64)
        self._sw_timeout.setToolTip("Seconds to wait for the element (1–300)")
        self._sw_timeout.valueChanged.connect(self._on_timeout_changed)

        self._sw_action = QComboBox()
        self._sw_action.addItem("Stop on timeout", "stop")
        self._sw_action.addItem("Skip on timeout", "skip")
        action_idx = self._sw_action.findData(step.smart_wait_on_timeout)
        self._sw_action.setCurrentIndex(action_idx if action_idx >= 0 else 0)
        self._sw_action.currentIndexChanged.connect(self._on_action_changed)

        sw_layout.addWidget(self._sw_timeout)
        sw_layout.addWidget(self._sw_action)

        btn_del = QPushButton()
        btn_del.setObjectName("btnStepDelete")
        btn_del.setIcon(icons.icon("trash", theme.manager.color("TEXT_SECONDARY").name(), 15))
        btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del.setToolTip("Delete step")
        btn_del.setFixedWidth(36)
        btn_del.clicked.connect(lambda: self.delete_clicked.emit(self._step))

        layout.addWidget(handle)
        layout.addWidget(self._index_label)
        layout.addWidget(icon)
        layout.addWidget(desc, 1)
        layout.addWidget(self._delay)
        layout.addWidget(self._sw_toggle)
        layout.addWidget(self._sw_controls)
        layout.addWidget(btn_del)

        self._sync_smart_wait_ui()

    def set_index(self, index: int) -> None:
        self._index_label.setText(f"{index + 1:02d}")

    def _on_delay(self, val: float) -> None:
        self._step.delay_after = val
        self.changed.emit()

    def _on_toggle_smart_wait(self) -> None:
        if not self._sw_supported:
            return
        self._step.smart_wait_enabled = self._sw_toggle.isChecked()
        self._sync_smart_wait_ui()
        self.changed.emit()

    def _on_timeout_changed(self, val: float) -> None:
        # The spin box already clamps to 1–300, so the value is always valid.
        self._step.smart_wait_timeout = float(val)
        self.changed.emit()

    def _on_action_changed(self, _index: int) -> None:
        self._step.smart_wait_on_timeout = self._sw_action.currentData()
        self.changed.emit()

    def _sync_smart_wait_ui(self) -> None:
        """Reflect the step's Smart Wait state in the row widgets."""
        if not self._sw_supported:
            self._sw_toggle.setEnabled(False)
            self._sw_toggle.setChecked(False)
            self._sw_toggle.setIcon(
                icons.icon("smart_wait", theme.manager.color("TEXT_DISABLED").name(), 14)
            )
            self._sw_toggle.setText(" —")
            self._sw_toggle.setToolTip("Smart wait requires a click or UI element step")
            self._sw_controls.setVisible(False)
            self._set_delay_smart_wait(False)
            return

        on = self._step.smart_wait_enabled
        self._sw_toggle.setEnabled(True)
        self._sw_toggle.setChecked(on)
        sw_color = "SUCCESS" if on else "TEXT_SECONDARY"
        self._sw_toggle.setIcon(
            icons.icon("smart_wait", theme.manager.color(sw_color).name(), 14)
        )
        self._sw_toggle.setText(" ON" if on else " OFF")
        self._sw_toggle.setToolTip(
            "Wait for the target element before running this step (delay ignored)"
        )
        self._sw_controls.setVisible(on)
        self._set_delay_smart_wait(on)

    def _set_delay_smart_wait(self, on: bool) -> None:
        """Gray out the delay field and show '—' when Smart Wait is on, without
        disturbing the stored delay_after value."""
        self._delay.blockSignals(True)
        if on:
            self._delay.setSpecialValueText("—")
            self._delay.setValue(self._delay.minimum())
            self._delay.setEnabled(False)
        else:
            self._delay.setSpecialValueText("")
            self._delay.setValue(self._step.delay_after)
            self._delay.setEnabled(True)
        self._delay.blockSignals(False)


class _ReorderableStepList(QListWidget):
    order_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("stepList")
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def dropEvent(self, event):
        super().dropEvent(event)
        self.order_changed.emit()


class WorkflowRowWidget(QFrame):
    run_clicked = pyqtSignal(int)
    edit_clicked = pyqtSignal(int)
    delete_clicked = pyqtSignal(int)
    rename_requested = pyqtSignal(int, str)

    def __init__(self, workflow: Workflow, step_count: int, parent=None):
        super().__init__(parent)
        self._wf_id = workflow.id
        self._name = workflow.name
        self.setObjectName("wfRow")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(8)

        dot = QLabel("\u25CF")
        dot.setStyleSheet(f"color: {theme.ACCENT}; font-size: 9px;")
        dot.setFixedWidth(10)

        self._name_label = _ClickableLabel(self._name)
        self._name_label.setObjectName("nameLabel")
        self._name_label.setCursor(Qt.CursorShape.IBeamCursor)
        self._name_label.setToolTip("Click to rename")
        self._name_label.clicked.connect(self._start_rename)

        self._name_edit = QLineEdit(self._name)
        self._name_edit.hide()
        self._name_edit.returnPressed.connect(self._name_edit.clearFocus)
        self._name_edit.editingFinished.connect(self._commit_rename)

        self._steps_label = QLabel(
            f"{step_count} step{'s' if step_count != 1 else ''}"
        )
        self._steps_label.setObjectName("infoLabel")

        self._last_run_label = QLabel(_format_last_run(workflow.last_run))
        self._last_run_label.setObjectName("infoLabel")

        top.addWidget(dot)
        top.addWidget(self._name_label, 1)
        top.addWidget(self._name_edit, 1)
        top.addWidget(self._steps_label)
        top.addWidget(self._last_run_label)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        hk = workflow.trigger.hotkey or "not set"
        self._hotkey_label = QLabel(f"Hotkey: {hk}")
        self._hotkey_label.setObjectName("infoLabel")

        self._btn_run = QPushButton("  Run")
        self._btn_run.setObjectName("btnRun")
        self._btn_run.setIcon(icons.icon("play_fill", "#ffffff", 13))
        self._btn_run.setIconSize(QSize(13, 13))
        self._btn_run.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_run.setToolTip("Run this workflow now")
        self._btn_run.clicked.connect(lambda: self.run_clicked.emit(self._wf_id))

        self._btn_edit = QPushButton()
        self._btn_edit.setObjectName("btnEdit")
        self._btn_edit.setIcon(icons.icon("edit", theme.manager.color("TEXT_SECONDARY").name(), 15))
        self._btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_edit.setToolTip("Edit steps")
        self._btn_edit.setFixedWidth(40)
        self._btn_edit.clicked.connect(lambda: self.edit_clicked.emit(self._wf_id))

        self._btn_delete = QPushButton()
        self._btn_delete.setObjectName("btnDelete")
        self._btn_delete.setIcon(icons.icon("trash", theme.manager.color("DANGER").name(), 15))
        self._btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_delete.setToolTip("Delete workflow")
        self._btn_delete.setFixedWidth(40)
        self._btn_delete.clicked.connect(lambda: self.delete_clicked.emit(self._wf_id))

        bottom.addWidget(self._hotkey_label)
        bottom.addStretch(1)
        bottom.addWidget(self._btn_run)
        bottom.addWidget(self._btn_edit)
        bottom.addWidget(self._btn_delete)

        root.addLayout(top)
        root.addLayout(bottom)

        # Soft elevation; brightens to an accent glow on hover.
        self._shadow = motion.attach_shadow(self, "#000000", blur=16, dy=5, alpha=90)

    def enterEvent(self, event):
        motion.set_glow(self._shadow, theme.manager.color("ACCENT").name(), alpha=150, dy=4)
        motion.animate_blur(self._shadow, 30)
        super().enterEvent(event)

    def leaveEvent(self, event):
        motion.set_glow(self._shadow, "#000000", alpha=90, dy=5)
        motion.animate_blur(self._shadow, 16)
        super().leaveEvent(event)

    def _start_rename(self):
        self._name_edit.setText(self._name)
        self._name_edit.show()
        self._name_edit.setFocus()
        self._name_edit.selectAll()
        self._name_label.hide()

    def _commit_rename(self):
        if not self._name_edit.isVisible():
            return
        new_name = self._name_edit.text().strip()
        self._name_edit.hide()
        self._name_label.show()
        if new_name and new_name != self._name:
            self._name = new_name
            self._name_label.setText(new_name)
            self.rename_requested.emit(self._wf_id, new_name)


class StepEditorWindow(QDialog):
    test_requested = pyqtSignal(object)

    def __init__(self, workflow: Workflow, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self._workflow = workflow
        self._steps: list[ActionStep] = list(workflow.steps)
        self._dirty = False
        self.setWindowTitle(f"{APP_NAME} \u2014 Edit: {workflow.name}")
        self.setMinimumSize(640, 560)
        self.resize(820, 720)
        _connect_theme(self, self._on_theme_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._btn_back = QPushButton("  Back")
        self._btn_back.setObjectName("btnBack")
        self._btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_back.clicked.connect(self._on_back)

        self._title = QLabel(workflow.name)
        self._title.setObjectName("editorTitle")

        self._btn_test = QPushButton("  Test")
        self._btn_test.setObjectName("btnTest")
        self._btn_test.setIconSize(QSize(13, 13))
        self._btn_test.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_test.setToolTip("Run the current step list (unsaved)")
        self._btn_test.clicked.connect(self._on_test)

        self._btn_save = QPushButton("  Save")
        self._btn_save.setObjectName("btnSave")
        self._btn_save.setIconSize(QSize(14, 14))
        self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._on_save)

        header.addWidget(self._btn_back)
        header.addWidget(self._title, 1)
        header.addWidget(self._btn_test)
        header.addWidget(self._btn_save)

        self._list = _ReorderableStepList()
        self._list.order_changed.connect(self._on_order_changed)

        self._empty = QLabel("No steps in this workflow.")
        self._empty.setObjectName("emptyLabel")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setStyleSheet("padding: 30px;")

        tip = QLabel(
            "💡  Tip: Adjust delays if playback feels too fast, or enable "
            "Smart Wait (⏳) to wait for an element instead of a fixed delay."
        )
        tip.setObjectName("tipLabel")

        layout.addLayout(header)
        layout.addWidget(self._list, 1)
        layout.addWidget(self._empty)
        layout.addWidget(tip)

        self._apply_icons()
        self._rebuild_rows()

    def _apply_icons(self) -> None:
        sec = theme.manager.color("TEXT_SECONDARY").name()
        self._btn_back.setIcon(icons.icon("back", sec, 15))
        self._btn_test.setIcon(icons.icon("play_fill", "#ffffff", 13))
        self._btn_save.setIcon(icons.icon("save", "#ffffff", 14))

    def _on_theme_changed(self) -> None:
        self._apply_icons()
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        self._list.clear()
        for i, step in enumerate(self._steps):
            item = QListWidgetItem()
            widget = StepRowWidget(step, i)
            widget.delete_clicked.connect(self._on_delete_step)
            widget.changed.connect(self._mark_dirty)
            item.setData(Qt.ItemDataRole.UserRole, step)
            item.setSizeHint(widget.sizeHint())
            self._list.addItem(item)
            self._list.setItemWidget(item, widget)
        self._empty.setVisible(not self._steps)

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._btn_save.setEnabled(True)
        if not self._title.text().endswith("*"):
            self._title.setText(self._workflow.name + "  *")

    def _on_delete_step(self, step: ActionStep) -> None:
        try:
            idx = self._steps.index(step)
        except ValueError:
            return
        inherited_delay = step.delay_after
        self._steps.pop(idx)
        if 0 <= idx - 1 < len(self._steps):
            self._steps[idx - 1].delay_after = inherited_delay
        self._mark_dirty()
        self._rebuild_rows()

    def _on_order_changed(self) -> None:
        ordered: list[ActionStep] = []
        for i in range(self._list.count()):
            step = self._list.item(i).data(Qt.ItemDataRole.UserRole)
            if step is not None:
                ordered.append(step)
        if len(ordered) == len(self._steps) and ordered != self._steps:
            self._steps = ordered
            self._mark_dirty()
            QTimer.singleShot(0, self._rebuild_rows)

    def _on_test(self) -> None:
        wf = Workflow(id=None, name=f"{self._workflow.name} (test)", steps=list(self._steps))
        logger.debug("Test run requested: %d steps", len(self._steps))
        self.test_requested.emit(wf)

    def _on_save(self) -> None:
        if self._workflow.id is None:
            logger.warning("Cannot save steps: workflow has no id")
            return
        save_steps(self._workflow.id, self._steps)
        self._dirty = False
        self._btn_save.setEnabled(False)
        self._title.setText(self._workflow.name)

    def _confirm_leave(self) -> bool:
        if not self._dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved changes")
        box.setText(f"Save changes to “{self._workflow.name}” before leaving?")
        btn_save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        btn_discard = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_save:
            self._on_save()
            return True
        if clicked is btn_discard:
            return True
        return False

    def _on_back(self) -> None:
        if self._confirm_leave():
            self.accept()

    def closeEvent(self, event):
        if not self._confirm_leave():
            event.ignore()
        else:
            super().closeEvent(event)


class _EmptyState(QWidget):
    """Illustrated empty / onboarding state shown when there are no workflows."""

    record_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(12)
        v.setContentsMargins(24, 40, 24, 40)

        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._title = QLabel("No workflows yet")
        self._title.setObjectName("emptyTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._subtitle = QLabel("")
        self._subtitle.setObjectName("emptyLabel")
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle.setWordWrap(True)

        self._btn = QPushButton("  Record a workflow")
        self._btn.setObjectName("btnNew")
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setIconSize(QSize(14, 14))
        self._btn.clicked.connect(self.record_clicked.emit)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self._btn)
        btn_row.addStretch(1)

        v.addStretch(1)
        v.addWidget(self._icon)
        v.addWidget(self._title)
        v.addWidget(self._subtitle)
        v.addLayout(btn_row)
        v.addStretch(1)

        self.retint()
        self.set_default(DEFAULT_RECORD_HOTKEY)

    def retint(self) -> None:
        self._icon.setPixmap(
            icons.pixmap("workflows", theme.manager.color("TEXT_DISABLED").name(), 46)
        )
        self._btn.setIcon(icons.icon("record", "#ffffff", 14))

    def set_default(self, hotkey: str) -> None:
        self._title.setText("No workflows yet")
        self._subtitle.setText(
            f"Record your first automation — press {hotkey} or click below."
        )
        self._btn.setVisible(True)

    def set_no_match(self, query: str) -> None:
        self._title.setText("No matches")
        self._subtitle.setText(f"No workflows match “{query}”.")
        self._btn.setVisible(False)


class WorkflowManagerWindow(QDialog):
    play_requested = pyqtSignal(int)
    new_requested = pyqtSignal()
    test_steps_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle(f"{APP_NAME} \u2014 Workflows")
        self.setMinimumSize(820, 580)
        self.resize(1040, 740)
        _connect_theme(self, self._on_theme_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("FlowRecord")
        title.setObjectName("titleLabel")
        self._count_label = QLabel("")
        self._count_label.setObjectName("infoLabel")

        self._btn_new = QPushButton("  New")
        self._btn_new.setObjectName("btnNew")
        self._btn_new.setIconSize(QSize(14, 14))
        self._btn_new.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_new.setToolTip("Record a new workflow")
        self._btn_new.clicked.connect(self.new_requested.emit)

        self._btn_settings = QPushButton()
        self._btn_settings.setObjectName("btnSettings")
        self._btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_settings.setToolTip("More actions")
        self._btn_settings.setFixedWidth(40)
        self._btn_settings.clicked.connect(self._show_settings_menu)

        header.addWidget(title)
        header.addWidget(self._count_label)
        header.addStretch(1)
        header.addWidget(self._btn_new)
        header.addWidget(self._btn_settings)

        self._search = _SearchBox()
        self._search.textChanged.connect(self._apply_filter)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._list_container = QWidget()
        self._list_container.setObjectName("listContainer")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 4, 0)
        self._list_layout.setSpacing(8)

        self._empty = _EmptyState()
        self._empty.record_clicked.connect(self.new_requested.emit)
        self._list_layout.addWidget(self._empty)
        self._list_layout.addStretch(1)

        self._scroll.setWidget(self._list_container)

        # Activity log panel (bottom)
        self._log_panel = LogPanel()
        self._log_panel.setMinimumHeight(150)

        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.setHandleWidth(8)
        self._splitter.setChildrenCollapsible(True)
        self._splitter.addWidget(self._scroll)
        self._splitter.addWidget(self._log_panel)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 0)
        self._splitter.setSizes([540, 220])

        layout.addLayout(header)
        layout.addWidget(self._search)
        layout.addWidget(self._splitter, 1)

        self._workflows: list[Workflow] = []
        self._rows: list[tuple[Workflow, WorkflowRowWidget]] = []
        self._apply_icons()
        self._load_workflows()

    def _apply_icons(self) -> None:
        self._btn_new.setIcon(icons.icon("plus", "#ffffff", 14))
        self._btn_settings.setIcon(
            icons.icon("settings", theme.manager.color("TEXT_SECONDARY").name(), 16)
        )
        self._search.retint()
        self._log_panel.retint()
        self._empty.retint()

    def _on_theme_changed(self) -> None:
        self._apply_icons()
        self._load_workflows()

    def refresh(self) -> None:
        self._load_workflows()
        self._apply_filter(self._search.text())

    def _load_workflows(self) -> None:
        for _, row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

        self._workflows = get_all_workflows()

        for wf in self._workflows:
            full = get_workflow(wf.id)
            step_count = len(full.steps) if full else 0
            row = WorkflowRowWidget(wf, step_count)
            row.run_clicked.connect(self._on_run)
            row.edit_clicked.connect(self._on_edit)
            row.delete_clicked.connect(self._on_delete)
            row.rename_requested.connect(self._on_rename)
            insert_index = self._list_layout.count() - 1
            self._list_layout.insertWidget(insert_index, row)
            self._rows.append((wf, row))

        if not self._rows:
            self._empty.set_default(DEFAULT_RECORD_HOTKEY)
        self._empty.setVisible(not self._rows)
        self._update_count()

    def _update_count(self) -> None:
        n = len(self._workflows)
        self._count_label.setText(f"{n} workflow{'s' if n != 1 else ''}")

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for wf, row in self._rows:
            row.setVisible(not needle or needle in wf.name.lower())

        if not self._rows:
            self._empty.set_default(DEFAULT_RECORD_HOTKEY)
            self._empty.setVisible(True)
            return

        any_visible = any(row.isVisible() for _, row in self._rows)
        if any_visible:
            self._empty.setVisible(False)
        else:
            self._empty.set_no_match(text.strip())
            self._empty.setVisible(True)

    def _on_run(self, wf_id: int) -> None:
        # Keep the window open so the Activity log streams live during playback.
        self.play_requested.emit(wf_id)

    def _on_edit(self, wf_id: int) -> None:
        full = get_workflow(wf_id)
        if not full:
            return
        editor = StepEditorWindow(full, self)
        editor.test_requested.connect(self.test_steps_requested)
        editor.exec()
        self._load_workflows()
        self._apply_filter(self._search.text())

    def _on_delete(self, wf_id: int) -> None:
        wf = next((w for w in self._workflows if w.id == wf_id), None)
        name = wf.name if wf else "this workflow"
        reply = QMessageBox.question(
            self, "Delete Workflow",
            f"Delete “{name}”?\nThis action cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            delete_workflow(wf_id)
            self._load_workflows()
            self._apply_filter(self._search.text())

    def _on_rename(self, wf_id: int, new_name: str) -> None:
        update_workflow_name(wf_id, new_name)
        for wf, _ in self._rows:
            if wf.id == wf_id:
                wf.name = new_name
                break

    def _show_settings_menu(self) -> None:
        menu = QMenu(self)
        act_import = menu.addAction("Import workflow\u2026")
        act_refresh = menu.addAction("Refresh list")
        chosen = menu.exec(
            self._btn_settings.mapToGlobal(self._btn_settings.rect().bottomLeft())
        )
        if chosen is act_import:
            self._import_workflow()
        elif chosen is act_refresh:
            self.refresh()

    def _import_workflow(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Workflow", "",
            "FlowRecord Files (*.flowrecord);;All Files (*)",
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            wf = _dict_to_workflow(data)
            wf_id = save_workflow(wf)
            logger.info("Imported workflow '%s' (id=%d)", wf.name, wf_id)
            self._load_workflows()
            self._apply_filter(self._search.text())
        except Exception:
            logger.exception("Import failed")
            QMessageBox.warning(
                self, "Import Error",
                f"Failed to import workflow:\n{traceback.format_exc()}",
            )


def _workflow_to_dict(wf: Workflow) -> dict:
    return {
        "name": wf.name,
        "trigger": {"hotkey": wf.trigger.hotkey, "voice_phrase": wf.trigger.voice_phrase},
        "steps": [
            {
                "type": s.type,
                "app_name": s.app_name,
                "window_title": s.window_title,
                "element_name": s.element_name,
                "element_type": s.element_type,
                "automation_id": s.automation_id,
                "class_name": s.class_name,
                "parent_path": s.parent_path,
                "x": s.x,
                "y": s.y,
                "x_relative": s.x_relative,
                "y_relative": s.y_relative,
                "keys": s.keys,
                "text": s.text,
                "scroll_dx": s.scroll_dx,
                "scroll_dy": s.scroll_dy,
                "delay_after": s.delay_after,
                "description": s.description,
                "enabled": s.enabled,
                "smart_wait_enabled": s.smart_wait_enabled,
                "smart_wait_timeout": s.smart_wait_timeout,
                "smart_wait_on_timeout": s.smart_wait_on_timeout,
            }
            for s in wf.steps
        ],
    }


def _dict_to_workflow(data: dict) -> Workflow:
    steps = []
    for s in data.get("steps", []):
        steps.append(ActionStep(
            type=s.get("type", "click"),
            app_name=s.get("app_name"),
            window_title=s.get("window_title"),
            element_name=s.get("element_name"),
            element_type=s.get("element_type"),
            automation_id=s.get("automation_id"),
            class_name=s.get("class_name"),
            parent_path=s.get("parent_path"),
            x=s.get("x"),
            y=s.get("y"),
            x_relative=s.get("x_relative"),
            y_relative=s.get("y_relative"),
            keys=s.get("keys"),
            text=s.get("text"),
            scroll_dx=s.get("scroll_dx", 0),
            scroll_dy=s.get("scroll_dy", 0),
            delay_after=s.get("delay_after", 0.0),
            description=s.get("description"),
            enabled=s.get("enabled", True),
            smart_wait_enabled=s.get("smart_wait_enabled", False),
            smart_wait_timeout=s.get("smart_wait_timeout", 10.0),
            smart_wait_on_timeout=s.get("smart_wait_on_timeout", "stop"),
        ))
    trigger_data = data.get("trigger", {})
    return Workflow(
        name=data.get("name", "Imported"),
        steps=steps,
        trigger=Trigger(
            hotkey=trigger_data.get("hotkey"),
            voice_phrase=trigger_data.get("voice_phrase"),
        ),
    )
