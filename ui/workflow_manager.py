import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QDoubleSpinBox,
 QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from flowrecord.config import APP_NAME
from flowrecord.models import ActionStep, Trigger, Workflow
from flowrecord.storage.workflow_store import (
    delete_workflow,
    get_all_workflows,
    get_workflow,
    save_workflow,
    update_last_run,
    update_step_delay,
    update_step_enabled,
    update_trigger,
    update_workflow_name,
)

logger = logging.getLogger(__name__)

DARK_STYLE = """
    QDialog, QWidget { background-color: #1e1e26; color: #cccccc; }
    QListWidget {
        background-color: #2a2a36; color: #cccccc;
        border: 1px solid #444; border-radius: 6px;
        font-size: 13px; padding: 4px;
    }
    QListWidget::item { padding: 8px; border-radius: 4px; }
    QListWidget::item:selected { background-color: #3a5fcd; }
    QListWidget::item:disabled { color: #666; }
    QPushButton {
        background-color: #3a3a4a; color: #cccccc;
        border: none; border-radius: 6px;
        padding: 8px 16px; font-size: 12px; font-weight: bold;
    }
    QPushButton:hover { background-color: #4a4a5a; }
    QPushButton:disabled { background-color: #2a2a36; color: #555; }
    QPushButton#btnPlay { background-color: rgba(40, 140, 60, 200); }
    QPushButton#btnPlay:hover { background-color: rgba(60, 170, 80, 220); }
    QPushButton#btnDelete { background-color: rgba(160, 50, 50, 200); }
    QPushButton#btnDelete:hover { background-color: rgba(190, 70, 70, 220); }
    QPushButton#btnExport { background-color: rgba(50, 100, 170, 200); }
    QPushButton#btnExport:hover { background-color: rgba(70, 120, 190, 220); }
    QLabel { color: #aaa; font-size: 12px; }
    QLineEdit {
        background-color: #2a2a36; color: #cccccc;
        border: 1px solid #444; border-radius: 4px;
        padding: 6px; font-size: 13px;
    }
    QLineEdit:focus { border: 1px solid #6688cc; }
    QDoubleSpinBox, QSpinBox {
        background-color: #2a2a36; color: #cccccc;
        border: 1px solid #444; border-radius: 4px;
        padding: 4px;
    }
    QCheckBox { color: #cccccc; font-size: 12px; spacing: 6px; }
    QCheckBox::indicator { width: 16px; height: 16px; }
    QCheckBox::indicator:unchecked { background-color: #3a3a4a; border: 1px solid #555; border-radius: 3px; }
    QCheckBox::indicator:checked { background-color: #3a8fcd; border: 1px solid #3a8fcd; border-radius: 3px; }
    QSplitter::handle { background-color: #333; }
    QTextEdit {
        background-color: #2a2a36; color: #999;
        border: 1px solid #333; border-radius: 4px;
        font-size: 11px; padding: 4px;
    }
"""


class StepItemWidget(QWidget):
    enabled_changed = pyqtSignal(int, bool)
    delay_changed = pyqtSignal(int, float)

    def __init__(self, step: ActionStep, index: int, parent=None):
        super().__init__(parent)
        self._step_id = step.id
        self._index = index

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(8)

        self._checkbox = QCheckBox()
        self._checkbox.setChecked(step.enabled)
        self._checkbox.stateChanged.connect(self._on_enabled)

        idx_label = QLabel(f"#{index + 1}")
        idx_label.setFixedWidth(30)
        idx_label.setStyleSheet("color: #888; font-weight: bold;")

        type_colors = {
            "click": "#5599ff", "keypress": "#ff9955", "type_text": "#55cc88",
            "scroll": "#cc88ff", "launch_app": "#ffcc55", "delay": "#888888",
        }
        type_label = QLabel(step.type)
        type_label.setFixedWidth(80)
        type_label.setStyleSheet(
            f"color: {type_colors.get(step.type, '#ccc')}; font-weight: bold; font-size: 11px;"
        )

        desc = step.description or f"{step.type} step"
        desc_label = QLabel(desc[:60] + ("..." if len(desc) > 60 else ""))
        desc_label.setStyleSheet("color: #bbb;")
        desc_label.setToolTip(desc)

        delay_spin = QDoubleSpinBox()
        delay_spin.setRange(0.0, 30.0)
        delay_spin.setSingleStep(0.1)
        delay_spin.setDecimals(1)
        delay_spin.setSuffix("s")
        delay_spin.setValue(step.delay_after)
        delay_spin.setFixedWidth(70)
        delay_spin.valueChanged.connect(self._on_delay)

        layout.addWidget(self._checkbox)
        layout.addWidget(idx_label)
        layout.addWidget(type_label)
        layout.addWidget(desc_label, 1)
        layout.addWidget(delay_spin)

    def _on_enabled(self, state):
        self.enabled_changed.emit(self._step_id, state == Qt.CheckState.Checked.value)

    def _on_delay(self, val):
        self.delay_changed.emit(self._step_id, val)


class WorkflowManager(QDialog):
    play_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(f"{APP_NAME} - Workflow Manager")
        self.setMinimumSize(700, 500)
        self.setStyleSheet(DARK_STYLE)

        self._current_wf: Optional[Workflow] = None

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(splitter)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 6, 0)

        left_layout.addWidget(QLabel("Workflows"))

        self._wf_list = QListWidget()
        self._wf_list.currentRowChanged.connect(self._on_wf_selected)
        left_layout.addWidget(self._wf_list)

        btn_row = QHBoxLayout()
        self._btn_play = QPushButton("Play")
        self._btn_play.setObjectName("btnPlay")
        self._btn_play.setEnabled(False)
        self._btn_play.clicked.connect(self._on_play)

        self._btn_delete = QPushButton("Delete")
        self._btn_delete.setObjectName("btnDelete")
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._on_delete)

        self._btn_export = QPushButton("Export")
        self._btn_export.setObjectName("btnExport")
        self._btn_export.setEnabled(False)
        self._btn_export.clicked.connect(self._on_export)

        self._btn_import = QPushButton("Import")
        self._btn_import.setObjectName("btnImport")
        self._btn_import.clicked.connect(self._on_import)

        self._btn_refresh = QPushButton("Refresh")
        self._btn_refresh.clicked.connect(self._load_workflows)

        btn_row.addWidget(self._btn_play)
        btn_row.addWidget(self._btn_delete)
        btn_row.addWidget(self._btn_export)
        btn_row.addWidget(self._btn_import)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_refresh)
        left_layout.addLayout(btn_row)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 0, 0, 0)

        right_layout.addWidget(QLabel("Edit Workflow"))

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self._edit_name = QLineEdit()
        self._edit_name.setPlaceholderText("Workflow name")
        self._edit_name.editingFinished.connect(self._on_name_changed)
        name_row.addWidget(self._edit_name)
        right_layout.addLayout(name_row)

        hotkey_row = QHBoxLayout()
        hotkey_row.addWidget(QLabel("Hotkey:"))
        self._edit_hotkey = QLineEdit()
        self._edit_hotkey.setPlaceholderText("e.g. ctrl+alt+1")
        self._edit_hotkey.editingFinished.connect(self._on_hotkey_changed)
        hotkey_row.addWidget(self._edit_hotkey)
        right_layout.addLayout(hotkey_row)

        info_row = QHBoxLayout()
        self._lbl_info = QLabel("")
        self._lbl_info.setStyleSheet("color: #777; font-size: 11px;")
        info_row.addWidget(self._lbl_info)
        info_row.addStretch()
        right_layout.addLayout(info_row)

        right_layout.addWidget(QLabel("Steps"))
        self._steps_list = QListWidget()
        self._steps_list.setStyleSheet(
            "QListWidget { background-color: #242430; border: 1px solid #333; border-radius: 6px; padding: 2px; }"
            "QListWidget::item { padding: 2px; border-bottom: 1px solid #2a2a36; }"
            "QListWidget::item:selected { background-color: transparent; }"
        )
        right_layout.addWidget(self._steps_list)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([250, 450])

        self._workflows: list[Workflow] = []
        self._load_workflows()

    def _load_workflows(self):
        self._wf_list.clear()
        self._workflows = get_all_workflows()
        for wf in self._workflows:
            full = get_workflow(wf.id)
            step_count = len(full.steps) if full else 0
            trigger_str = wf.trigger.hotkey or "none"
            last_run = wf.last_run.strftime("%H:%M %m/%d") if wf.last_run else "never"
            text = f"{wf.name}\n  {step_count} steps | {trigger_str} | {last_run}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, wf.id)
            self._wf_list.addItem(item)
        self._current_wf = None
        self._clear_edit()

    def _clear_edit(self):
        self._edit_name.clear()
        self._edit_hotkey.clear()
        self._lbl_info.clear()
        self._steps_list.clear()

    def _on_wf_selected(self, row):
        has_sel = row >= 0
        self._btn_play.setEnabled(has_sel)
        self._btn_delete.setEnabled(has_sel)
        self._btn_export.setEnabled(has_sel)

        if not has_sel:
            self._current_wf = None
            self._clear_edit()
            return

        wf_id = self._workflows[row].id
        self._current_wf = get_workflow(wf_id)
        if not self._current_wf:
            return

        self._edit_name.setText(self._current_wf.name)
        self._edit_hotkey.setText(self._current_wf.trigger.hotkey or "")

        created = self._current_wf.created_at.strftime("%Y-%m-%d %H:%M") if self._current_wf.created_at else "?"
        self._lbl_info.setText(f"Created: {created}  |  {len(self._current_wf.steps)} steps")

        self._steps_list.clear()
        for i, step in enumerate(self._current_wf.steps):
            item = QListWidgetItem()
            widget = StepItemWidget(step, i)
            widget.enabled_changed.connect(self._on_step_enabled)
            widget.delay_changed.connect(self._on_step_delay)
            item.setSizeHint(widget.sizeHint())
            self._steps_list.addItem(item)
            self._steps_list.setItemWidget(item, widget)

    def _on_name_changed(self):
        if not self._current_wf:
            return
        name = self._edit_name.text().strip()
        if name and name != self._current_wf.name:
            update_workflow_name(self._current_wf.id, name)
            self._current_wf.name = name
            self._load_workflows()

    def _on_hotkey_changed(self):
        if not self._current_wf:
            return
        hotkey = self._edit_hotkey.text().strip() or None
        update_trigger(self._current_wf.id, hotkey=hotkey)
        self._current_wf.trigger.hotkey = hotkey

    def _on_step_enabled(self, step_id: int, enabled: bool):
        update_step_enabled(step_id, enabled)

    def _on_step_delay(self, step_id: int, delay: float):
        update_step_delay(step_id, delay)

    def _on_play(self):
        row = self._wf_list.currentRow()
        if row < 0:
            return
        wf = self._workflows[row]
        if wf.id:
            self.play_requested.emit(wf.id)
            self.accept()

    def _on_delete(self):
        row = self._wf_list.currentRow()
        if row < 0:
            return
        wf = self._workflows[row]
        reply = QMessageBox.question(
            self, "Delete",
            f"Delete '{wf.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            delete_workflow(wf.id)
            self._load_workflows()

    def _on_export(self):
        if not self._current_wf:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Workflow", f"{self._current_wf.name}.flowrecord",
            "FlowRecord Files (*.flowrecord);;All Files (*)",
        )
        if not path:
            return
        data = _workflow_to_dict(self._current_wf)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info("Exported workflow to %s", path)

    def _on_import(self):
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
        except Exception:
            QMessageBox.warning(self, "Import Error", f"Failed to import:\n{traceback.format_exc()}")


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


import traceback
