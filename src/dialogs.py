# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Dialogs: measurement scale, calibration, and form filling."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

import markups
from markups import COMMON_SCALES, UNITS, Scale


class ScaleDialog(QDialog):
    """Pick a standard drawing scale or enter a custom paper = real ratio."""

    def __init__(self, current: Scale, page_count: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set measurement scale")
        self.preset = QComboBox()
        self.preset.addItem("Custom…", None)
        for name in COMMON_SCALES:
            self.preset.addItem(name, name)
        self.paper = QDoubleSpinBox()
        self.paper.setDecimals(4)
        self.paper.setRange(0.0001, 100000)
        self.paper.setValue(1)
        self.paper_unit = QComboBox()
        self.paper_unit.addItems(["in", "mm", "cm", "pt"])
        self.real = QDoubleSpinBox()
        self.real.setDecimals(4)
        self.real.setRange(0.0001, 1000000)
        self.real.setValue(1)
        self.real_unit = QComboBox()
        self.real_unit.addItems(UNITS)
        self.precision = QSpinBox()
        self.precision.setRange(0, 6)
        self.precision.setValue(current.precision)
        self.this_page = QRadioButton("This page")
        self.all_pages = QRadioButton(f"All {page_count} pages")
        self.this_page.setChecked(True)
        paper_row = QHBoxLayout()
        paper_row.addWidget(self.paper)
        paper_row.addWidget(self.paper_unit)
        real_row = QHBoxLayout()
        real_row.addWidget(self.real)
        real_row.addWidget(self.real_unit)
        scope = QHBoxLayout()
        scope.addWidget(self.this_page)
        scope.addWidget(self.all_pages)
        form = QFormLayout()
        form.addRow("Current", QLabel(current.label))
        form.addRow("Standard scale", self.preset)
        form.addRow("Paper length", paper_row)
        form.addRow("Equals real length", real_row)
        form.addRow("Decimal places", self.precision)
        form.addRow("Apply to", scope)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.preset.currentIndexChanged.connect(self._preset_changed)

    def _preset_changed(self) -> None:
        name = self.preset.currentData()
        custom = name is None
        for widget in (self.paper, self.paper_unit, self.real, self.real_unit):
            widget.setEnabled(custom)
        if name:
            paper, paper_unit, real, real_unit = COMMON_SCALES[name]
            self.paper.setValue(paper)
            self.paper_unit.setCurrentText(paper_unit)
            self.real.setValue(real)
            self.real_unit.setCurrentText("m" if real_unit == "mm" else real_unit)

    def scale(self) -> Scale:
        name = self.preset.currentData()
        if name:
            scale = markups.common_scale(name)
        else:
            scale = markups.scale_from_ratio(self.paper.value(), self.paper_unit.currentText(),
                                             self.real.value(), self.real_unit.currentText())
        scale.precision = self.precision.value()
        return scale

    def apply_to_all(self) -> bool:
        return self.all_pages.isChecked()


class CalibrateDialog(QDialog):
    """Enter the real length of the line just drawn."""

    def __init__(self, measured_points: float, page_count: int, parent=None) -> None:
        super().__init__(parent)
        self.measured = measured_points
        self.setWindowTitle("Calibrate scale")
        self.length = QLineEdit()
        self.length.setPlaceholderText("e.g. 12.5 or 12'-6\"")
        self.unit = QComboBox()
        self.unit.addItems(UNITS)
        self.unit.setCurrentText("ft-in")
        self.all_pages = QCheckBox(f"Apply to all {page_count} pages")
        self.error = QLabel("")
        self.error.setStyleSheet("color:#b00020;")
        row = QHBoxLayout()
        row.addWidget(self.length, 1)
        row.addWidget(self.unit)
        form = QFormLayout()
        form.addRow("Line drawn", QLabel(f"{measured_points:.2f} pt on paper"))
        form.addRow("Known length", row)
        form.addRow("", self.all_pages)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        try:
            self.value()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.accept()

    def value(self) -> float:
        return parse_length(self.length.text(), self.unit.currentText())

    def scale(self) -> Scale:
        unit = self.unit.currentText()
        return markups.scale_from_calibration(self.measured, self.value(), unit)


def parse_length(text: str, unit: str) -> float:
    """Parse '12.5', '12\\'-6"', '12 6', or '6"' into a number of `unit`."""
    import re
    text = text.strip()
    if not text:
        raise ValueError("Enter the known length.")
    if unit in ("ft", "ft-in") and ("'" in text or '"' in text):
        match = re.fullmatch(r"(?:(\d+(?:\.\d+)?)\s*')?\s*-?\s*(?:(\d+(?:\.\d+)?)"
                             r"(?:\s+(\d+)/(\d+))?\s*\"?)?", text)
        if not match or not any(match.groups()):
            raise ValueError("Use a length like 12'-6\" or 12.5.")
        feet = float(match.group(1) or 0)
        inches = float(match.group(2) or 0)
        if match.group(3):
            inches += float(match.group(3)) / float(match.group(4))
        return feet + inches / 12
    try:
        value = float(text)
    except ValueError:
        raise ValueError("Enter a number, for example 12.5.") from None
    if value <= 0:
        raise ValueError("The length must be positive.")
    return value


class FormDialog(QDialog):
    """Table of the PDF's form fields with editable values."""

    def __init__(self, fields: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Fill form fields")
        self.resize(620, 460)
        self.fields = fields
        self.table = QTableWidget(len(fields), 4)
        self.table.setHorizontalHeaderLabels(["Field", "Type", "Page", "Value"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.editors = []
        for row, field in enumerate(fields):
            for column, value in enumerate((field["name"], field["type"], str(field["page"] + 1))):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
            if field["type"] in ("CheckBox", "RadioButton"):
                editor = QCheckBox()
                editor.setChecked(field["value"] not in (None, "", "Off", False))
            elif field["choices"]:
                editor = QComboBox()
                editor.setEditable(field["type"] == "ComboBox")
                editor.addItems([str(choice if not isinstance(choice, (list, tuple))
                                     else choice[-1]) for choice in field["choices"]])
                editor.setCurrentText(str(field["value"] or ""))
            else:
                editor = QLineEdit(str(field["value"] or ""))
            self.table.setCellWidget(row, 3, editor)
            self.editors.append(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(buttons)

    def values(self) -> dict[str, object]:
        result = {}
        for field, editor in zip(self.fields, self.editors):
            if isinstance(editor, QCheckBox):
                value = editor.isChecked()
            elif isinstance(editor, QComboBox):
                value = editor.currentText()
            else:
                value = editor.text()
            if value != field["value"]:
                result[field["name"]] = value
        return result


class BatchDialog(QDialog):
    """Choose files, actions, and an output folder for batch processing."""

    def __init__(self, start_folder: str = "", parent=None) -> None:
        from PySide6.QtWidgets import QFileDialog, QListWidget, QPushButton
        super().__init__(parent)
        self.setWindowTitle("Batch process PDFs")
        self.resize(620, 560)
        self._file_dialog = QFileDialog
        self._start = start_folder
        self.files = QListWidget()
        add = QPushButton("Add PDFs…")
        remove = QPushButton("Remove")
        add.clicked.connect(self._add)
        remove.clicked.connect(lambda: [self.files.takeItem(self.files.row(item))
                                        for item in self.files.selectedItems()])
        self.files.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        file_buttons = QHBoxLayout()
        file_buttons.addWidget(add)
        file_buttons.addWidget(remove)
        file_buttons.addStretch(1)

        self.scale = QComboBox()
        self.scale.addItem("(leave unchanged)", "")
        for name in COMMON_SCALES:
            self.scale.addItem(name, name)
        self.auto_link = QCheckBox("Auto-link sheet numbers")
        self.stamp = QComboBox()
        self.stamp.addItem("(no stamp)", "")
        for name in markups.STAMPS:
            self.stamp.addItem(name, name)
        self.flatten = QCheckBox("Flatten markups")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("leave empty for no password")
        self.source_password = QLineEdit()
        self.source_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.source_password.setPlaceholderText("for encrypted inputs")
        self.summary = QCheckBox("Markup summary report (PDF)")
        self.csv = QCheckBox("Markups list (CSV)")
        self.folder = QLineEdit()
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)

        form = QFormLayout()
        form.addRow("Set scale on every page", self.scale)
        form.addRow("", self.auto_link)
        form.addRow("Stamp first page", self.stamp)
        form.addRow("", self.flatten)
        form.addRow("Protect outputs with", self.password)
        form.addRow("Password of inputs", self.source_password)
        form.addRow("Also write", self.summary)
        form.addRow("", self.csv)
        form.addRow("Output folder", folder_row)
        self.error = QLabel("")
        self.error.setStyleSheet("color:#b00020;")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Run")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Files"))
        layout.addWidget(self.files, 1)
        layout.addLayout(file_buttons)
        layout.addLayout(form)
        layout.addWidget(QLabel("Originals are never changed; results are written to the "
                                "output folder."))
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def add_files(self, paths: list[str]) -> None:
        existing = {self.files.item(i).text() for i in range(self.files.count())}
        for path in paths:
            if path not in existing:
                self.files.addItem(path)
        if paths and not self.folder.text():
            from pathlib import Path
            self.folder.setText(str(Path(paths[0]).parent / "Batch output"))

    def _add(self) -> None:
        paths, _ = self._file_dialog.getOpenFileNames(self, "Add PDFs", self._start,
                                                      "PDF files (*.pdf)")
        self.add_files(paths)

    def _browse(self) -> None:
        folder = self._file_dialog.getExistingDirectory(self, "Output folder",
                                                        self.folder.text() or self._start)
        if folder:
            self.folder.setText(folder)

    def _accept(self) -> None:
        if not self.paths():
            self.error.setText("Add at least one PDF.")
            return
        if not self.folder.text().strip():
            self.error.setText("Choose an output folder.")
            return
        options = self.options()
        if not any((options.auto_link, options.stamp, options.scale, options.flatten,
                    options.password, options.summary, options.csv)):
            self.error.setText("Choose at least one action.")
            return
        self.accept()

    def paths(self) -> list[str]:
        return [self.files.item(i).text() for i in range(self.files.count())]

    def options(self):
        from batch import BatchOptions
        return BatchOptions(auto_link=self.auto_link.isChecked(),
                            stamp=self.stamp.currentData() or "",
                            scale=self.scale.currentData() or "",
                            flatten=self.flatten.isChecked(), password=self.password.text(),
                            summary=self.summary.isChecked(), csv=self.csv.isChecked(),
                            source_password=self.source_password.text())


class ViewportsDialog(QDialog):
    """Rename, rescale, or delete the viewports on one page."""

    def __init__(self, items: list[dict], parent=None) -> None:
        from PySide6.QtWidgets import QInputDialog, QListWidget, QPushButton
        super().__init__(parent)
        self.setWindowTitle("Viewports on this page")
        self.resize(460, 320)
        self.items = [dict(item) for item in items]
        self._input = QInputDialog
        self.list = QListWidget()
        rename = QPushButton("Rename…")
        rescale = QPushButton("Change Scale…")
        delete = QPushButton("Delete")
        rename.clicked.connect(self._rename)
        rescale.clicked.connect(self._rescale)
        delete.clicked.connect(self._delete)
        row = QHBoxLayout()
        for button in (rename, rescale, delete):
            row.addWidget(button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.list, 1)
        layout.addLayout(row)
        layout.addWidget(buttons)
        self._refresh()

    def _refresh(self) -> None:
        current = self.list.currentRow()
        self.list.clear()
        for item in self.items:
            self.list.addItem(f"{item['name']}  —  {item['scale'].label}")
        if self.items:
            self.list.setCurrentRow(max(0, min(current, len(self.items) - 1)))

    def _rename(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        name, accepted = self._input.getText(self, "Rename viewport", "Name:",
                                             text=self.items[row]["name"])
        if accepted and name.strip():
            self.items[row]["name"] = name.strip()
            self._refresh()

    def _rescale(self) -> None:
        row = self.list.currentRow()
        if row < 0:
            return
        dialog = ScaleDialog(self.items[row]["scale"], 1, self)
        dialog.this_page.hide()
        dialog.all_pages.hide()
        if dialog.exec():
            self.items[row]["scale"] = dialog.scale()
            self._refresh()

    def _delete(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            del self.items[row]
            self._refresh()
