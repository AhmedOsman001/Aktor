"""Dialogs for running a workflow with variable values.

* ``RunValuesDialog`` — prompt the user for each ``{{variable}}`` once.
* ``BatchDialog`` — load a CSV/Excel file, map columns to variables, choose an
  error policy, and run the workflow once per row.
"""

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from flowrecord.core import tabular
from flowrecord.ui import theme
from flowrecord.ui.components import PillButton, SegmentedControl, _style, info
from flowrecord.ui.title_bar import FramelessDialog

_SKIP = "—  (skip)  —"


class RunValuesDialog(FramelessDialog):
    """Collect a value for each workflow variable, then run once."""

    def __init__(self, variables: list[str], samples: dict | None = None, parent=None):
        super().__init__("Run with values", show_logo=False, parent=parent)
        self.setMinimumWidth(420)
        samples = samples or {}
        self._fields: dict[str, QLineEdit] = {}
        self.values: dict[str, str] = {}

        lay = self.content_layout()
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(24, 18, 24, 20)
        v.setSpacing(14)

        intro = QLabel("Enter a value for each variable in this workflow:")
        intro.setObjectName("dlgIntro")
        intro.setWordWrap(True)
        v.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        for name in variables:
            edit = QLineEdit()
            sample = samples.get(name, "")
            if sample:
                edit.setText(sample)            # prefill with the recorded sample
                edit.setPlaceholderText(f"e.g. {sample}")
            else:
                edit.setPlaceholderText(f"value for {name}")
            self._fields[name] = edit
            form.addRow(QLabel(name), edit)
        v.addLayout(form)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = PillButton("Cancel", "secondary")
        cancel.clicked.connect(self.reject)
        run = PillButton("Run", "primary")
        run.clicked.connect(self._on_run)
        btns.addWidget(cancel)
        btns.addWidget(run)
        v.addLayout(btns)

        lay.addWidget(wrap)
        theme.manager.changed.connect(self._apply)
        self._apply()
        if variables:
            self._fields[variables[0]].setFocus()

    def _apply(self):
        self.setStyleSheet(theme.manager.qss_dialog() + _style(
            "#dlgIntro { color: @MUTED@; font-size: 13px; }"
        ))

    def _on_run(self):
        self.values = {n: f.text() for n, f in self._fields.items()}
        self.accept()


class BatchDialog(FramelessDialog):
    """Load a spreadsheet, map its columns to the workflow's variables, and run
    the workflow once per row."""

    def __init__(self, variables: list[str], samples: dict | None = None, parent=None):
        super().__init__("Run batch — CSV / Excel", show_logo=False, parent=parent)
        self.setMinimumWidth(560)
        self._variables = variables
        self._samples = samples or {}
        self._combos: dict[str, QComboBox] = {}

        # Result fields read by the caller after exec().
        self.rows: list[dict] = []
        self.headers: list[str] = []
        self.mapping: dict[str, Optional[str]] = {}
        self.policy = "skip"   # "skip" | "stop"
        self.file_path: Optional[str] = None

        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(24, 18, 24, 20)
        v.setSpacing(14)

        # ---- file picker ----
        file_row = QHBoxLayout()
        self._file_lbl = QLabel("No file selected")
        self._file_lbl.setObjectName("batchFile")
        choose = PillButton("Choose file…", "secondary")
        choose.clicked.connect(self._choose_file)
        file_row.addWidget(self._file_lbl, 1)
        file_row.addWidget(choose)
        v.addLayout(file_row)

        hint = QLabel("Pick a .csv or .xlsx file. The first row is the column header.")
        hint.setObjectName("batchHint")
        hint.setWordWrap(True)
        v.addWidget(hint)

        # ---- column → variable mapping (scrollable) ----
        self._map_title = QLabel("MAP COLUMNS TO VARIABLES")
        self._map_title.setObjectName("batchSection")
        v.addWidget(self._map_title)

        self._map_host = QWidget()
        self._map_form = QFormLayout(self._map_host)
        self._map_form.setSpacing(10)
        self._map_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        for name in variables:
            combo = QComboBox()
            combo.addItem(_SKIP)
            self._combos[name] = combo
            sample = self._samples.get(name, "")
            label = name if not sample else f'{name}   ·   e.g. "{sample[:24]}"'
            self._map_form.addRow(QLabel(label), combo)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self._map_host)
        scroll.setMaximumHeight(220)
        v.addWidget(scroll)

        # ---- error policy ----
        pol_row = QHBoxLayout()
        pol_lbl = QLabel("On row error")
        self._policy_seg = SegmentedControl(["Skip row", "Stop"], index=0)
        self._policy_seg.changed.connect(
            lambda i: setattr(self, "policy", "stop" if i == 1 else "skip")
        )
        pol_row.addWidget(pol_lbl)
        pol_row.addStretch(1)
        pol_row.addWidget(self._policy_seg)
        v.addLayout(pol_row)

        # ---- actions ----
        btns = QHBoxLayout()
        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName("batchCount")
        btns.addWidget(self._count_lbl)
        btns.addStretch(1)
        cancel = PillButton("Cancel", "secondary")
        cancel.clicked.connect(self.reject)
        self._run_btn = PillButton("Run batch", "primary")
        self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self._on_run)
        btns.addWidget(cancel)
        btns.addWidget(self._run_btn)
        v.addLayout(btns)

        self.content_layout().addWidget(root)
        theme.manager.changed.connect(self._apply)
        self._apply()

    def _apply(self):
        self.setStyleSheet(theme.manager.qss_dialog() + _style(
            "#batchFile { color: @HEADING@; font-size: 13px; font-weight: 600; }"
            "#batchHint { color: @MUTED@; font-size: 12px; }"
            "#batchSection { color: @MUTED@; font-size: 11px; font-weight: 700;"
            " letter-spacing: 1px; padding-top: 4px; }"
            "#batchCount { color: @BODY@; font-size: 12px; }"
        ))

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose data file", "",
            "Spreadsheets (*.csv *.xlsx *.xlsm);;CSV (*.csv);;Excel (*.xlsx *.xlsm);;All files (*.*)",
        )
        if not path:
            return
        try:
            headers, rows = tabular.read_table(path)
        except Exception as e:
            info(self, "Could not read file", f"Failed to read:\n{e}")
            return
        if not headers or not rows:
            info(self, "Empty file", "That file has no header row and data rows.")
            return

        self.file_path = path
        self.headers = headers
        self.rows = rows
        import os
        self._file_lbl.setText(os.path.basename(path))
        self._count_lbl.setText(f"{len(rows)} row(s) · {len(headers)} column(s)")
        self._populate_combos(headers)
        self._run_btn.setEnabled(True)

    def _populate_combos(self, headers: list[str]):
        lower = {h.lower().strip(): h for h in headers}
        for name, combo in self._combos.items():
            combo.clear()
            combo.addItem(_SKIP)
            for h in headers:
                combo.addItem(h)
            # Auto-map a column whose name matches the variable (case-insensitive).
            match = lower.get(name.lower().strip())
            if match is not None:
                combo.setCurrentText(match)

    def _on_run(self):
        if not self.rows:
            return
        self.mapping = {
            name: (None if combo.currentText() == _SKIP else combo.currentText())
            for name, combo in self._combos.items()
        }
        self.accept()
