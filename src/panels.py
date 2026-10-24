"""Dockable panels: Properties, Markups List, Tool Chest, Thumbnails, Bookmarks,
Layers, and Search."""
from __future__ import annotations

import pymupdf as fitz
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox,
                               QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit,
                               QPushButton, QSlider, QTableWidget, QTableWidgetItem,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

import markups
from markups import KIND_LABELS, LINE_ENDS, STAMPS, STATUSES, Style
from tool_chest import RECENT, ToolChest, ToolPreset


class ColorButton(QToolButton):
    """Swatch button that opens a colour picker; optionally allows 'none'."""

    changed = Signal(str)

    def __init__(self, allow_none: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.allow_none = allow_none
        self._value = ""
        self.setMinimumWidth(64)
        self.clicked.connect(self._pick)
        if allow_none:
            menu = QMenu(self)
            menu.addAction("Choose colour…", self._pick)
            menu.addAction("No fill", lambda: self._set_and_emit(""))
            self.setMenu(menu)
            self.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value or ""
        if self._value:
            pixmap = QPixmap(28, 14)
            pixmap.fill(QColor(self._value))
            self.setIcon(QIcon(pixmap))
            self.setText("")
        else:
            self.setIcon(QIcon())
            self.setText("None")
        self.setToolTip(self._value or "None")

    def _set_and_emit(self, value: str) -> None:
        self.set_value(value)
        self.changed.emit(value)

    def _pick(self) -> None:
        colour = QColorDialog.getColor(QColor(self._value or "#ff0000"), self, "Choose colour")
        if colour.isValid():
            self._set_and_emit(colour.name())


class PropertiesPanel(QWidget):
    """Edits the selected markup, or the active tool's defaults when nothing is selected."""

    styleEdited = Signal(object)      # Style
    commentEdited = Signal(str)
    statusEdited = Signal(str)
    saveToolRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        self.kind = ""
        self.heading = QLabel("No selection")
        self.heading.setStyleSheet("font-weight:600; font-size:14px; padding:2px 0 6px 0;")
        self.subject = QLineEdit()
        self.stroke = ColorButton()
        self.fill = ColorButton(allow_none=True)
        self.width = QDoubleSpinBox()
        self.width.setRange(0, 72)
        self.width.setSingleStep(0.5)
        self.width.setSuffix(" pt")
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(5, 100)
        self.opacity_label = QLabel("100%")
        self.dash = QComboBox()
        self.dash.addItems(["solid", "dashed", "dotted"])
        self.line_start = QComboBox()
        self.line_end = QComboBox()
        for combo in (self.line_start, self.line_end):
            combo.addItems(list(LINE_ENDS))
        self.font = QComboBox()
        for code, name in markups.FONTS.items():
            self.font.addItem(name, code)
        self.font_size = QDoubleSpinBox()
        self.font_size.setRange(4, 144)
        self.font_size.setSuffix(" pt")
        self.text_color = ColorButton()
        self.align = QComboBox()
        self.align.addItems(["left", "center", "right"])
        self.stamp = QComboBox()
        self.stamp.addItems(STAMPS)
        self.comment = QPlainTextEdit()
        self.comment.setPlaceholderText("Comment")
        self.comment.setMaximumHeight(90)
        self.status = QComboBox()
        self.status.addItems(STATUSES)
        self.measurement = QLabel("")
        self.measurement.setStyleSheet("font-weight:600; color:#0b3d91;")
        self.save_tool = QPushButton("Add to Tool Chest…")
        self.save_tool.clicked.connect(self.saveToolRequested)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.opacity, 1)
        opacity_row.addWidget(self.opacity_label)
        form = QFormLayout()
        self.rows = {}
        for label, widget in (("Subject", self.subject), ("Line colour", self.stroke),
                              ("Fill", self.fill), ("Line width", self.width),
                              ("Opacity", opacity_row), ("Line style", self.dash),
                              ("Start", self.line_start), ("End", self.line_end),
                              ("Font", self.font), ("Font size", self.font_size),
                              ("Text colour", self.text_color), ("Align", self.align),
                              ("Stamp", self.stamp), ("Status", self.status),
                              ("Measurement", self.measurement)):
            form.addRow(label, widget)
            self.rows[label] = form.rowCount() - 1
        self._form = form
        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addLayout(form)
        layout.addWidget(QLabel("Comment"))
        layout.addWidget(self.comment)
        layout.addWidget(self.save_tool)
        layout.addStretch(1)

        self._comment_timer = QTimer(self)
        self._comment_timer.setSingleShot(True)
        self._comment_timer.setInterval(600)
        self._comment_timer.timeout.connect(lambda: self.commentEdited.emit(
            self.comment.toPlainText()))
        self.subject.editingFinished.connect(self._emit_style)
        self.stroke.changed.connect(self._emit_style)
        self.fill.changed.connect(self._emit_style)
        self.text_color.changed.connect(self._emit_style)
        self.width.editingFinished.connect(self._emit_style)
        self.font_size.editingFinished.connect(self._emit_style)
        self.opacity.valueChanged.connect(lambda v: self.opacity_label.setText(f"{v}%"))
        self.opacity.sliderReleased.connect(self._emit_style)
        for combo in (self.dash, self.line_start, self.line_end, self.font, self.align, self.stamp):
            combo.currentIndexChanged.connect(self._emit_style)
        self.comment.textChanged.connect(self._comment_changed)
        self.status.currentTextChanged.connect(self._status_changed)
        self.show_nothing()

    def _row_visible(self, label: str, visible: bool) -> None:
        self._form.setRowVisible(self.rows[label], visible)

    def show_nothing(self) -> None:
        self._loading = True
        self.kind = ""
        self.heading.setText("Select a markup or tool")
        for label in self.rows:
            self._row_visible(label, False)
        self.comment.setEnabled(False)
        self.save_tool.setEnabled(False)
        self._loading = False

    def show_style(self, kind: str, style: Style, heading: str, comment: str | None = None,
                   status: str | None = None, measurement: str = "") -> None:
        """Load a style; comment/status are None when editing tool defaults."""
        self._loading = True
        self.kind = kind
        self.heading.setText(heading)
        text_kind = kind in ("text", "callout")
        lines = kind in ("line", "arrow", "polyline", "length", "polylength", "callout")
        shapes = kind not in ("note", "stamp", "highlight", "underline", "strikeout", "squiggly")
        visible = {
            "Subject": True, "Line colour": kind != "stamp" and not text_kind,
            "Fill": kind in ("text", "callout", "rectangle", "ellipse", "polygon", "cloud",
                             "area", "count", "redact"),
            "Line width": shapes and kind != "count",
            "Opacity": True, "Line style": shapes and kind not in ("count", "pen", "highlighter"),
            "Start": lines and kind != "callout", "End": lines,
            "Font": text_kind, "Font size": text_kind, "Text colour": text_kind,
            "Align": text_kind, "Stamp": kind == "stamp", "Status": status is not None,
            "Measurement": bool(measurement),
        }
        for label, show in visible.items():
            self._row_visible(label, show)
        self.subject.setText(style.subject)
        self.stroke.set_value(style.stroke)
        self.fill.set_value(style.fill)
        self.width.setValue(style.width)
        self.opacity.setValue(round(style.opacity * 100))
        self.opacity_label.setText(f"{round(style.opacity * 100)}%")
        self.dash.setCurrentText(style.dash)
        self.line_start.setCurrentText(style.line_start)
        self.line_end.setCurrentText(style.line_end)
        self.font.setCurrentIndex(max(0, self.font.findData(style.font)))
        self.font_size.setValue(style.font_size)
        self.text_color.set_value(style.text_color)
        self.align.setCurrentText(style.align if style.align in ("left", "center", "right") else "left")
        self.stamp.setCurrentText(style.stamp)
        self.comment.setEnabled(comment is not None)
        self.comment.setPlainText(comment or "")
        self.status.setCurrentText(status or "")
        self.measurement.setText(measurement)
        self.save_tool.setEnabled(True)
        self._loading = False

    def current_style(self) -> Style:
        return Style(stroke=self.stroke.value(), fill=self.fill.value(),
                     width=self.width.value(), opacity=self.opacity.value() / 100,
                     dash=self.dash.currentText(), font_size=self.font_size.value(),
                     text_color=self.text_color.value() or "#000000",
                     font=self.font.currentData() or "Helv", align=self.align.currentText(),
                     line_start=self.line_start.currentText(),
                     line_end=self.line_end.currentText(), stamp=self.stamp.currentText(),
                     subject=self.subject.text().strip())

    def _emit_style(self, *_args) -> None:
        if not self._loading and self.kind:
            style = self.current_style()
            if self.kind in ("text", "callout"):
                style.stroke = style.text_color
            self.styleEdited.emit(style)

    def _comment_changed(self) -> None:
        if not self._loading and self.comment.isEnabled():
            self._comment_timer.start()

    def flush_comment(self) -> None:
        if self._comment_timer.isActive():
            self._comment_timer.stop()
            self.commentEdited.emit(self.comment.toPlainText())

    def _status_changed(self, status: str) -> None:
        if not self._loading:
            self.statusEdited.emit(status)


class MarkupsList(QWidget):
    """Table of every markup in the document with filters, status, and CSV export."""

    COLUMNS = ["Page", "Type", "Subject", "Comment", "Author", "Date", "Status", "Colour",
               "Measurement"]
    activated = Signal(int, int)           # page, xref
    selectionChanged = Signal(list)        # [(page, xref)]
    statusRequested = Signal(list, str)    # [(page, xref)], status
    deleteRequested = Signal(list)
    exportRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[dict] = []
        self._updating = False
        self.filter_text = QLineEdit()
        self.filter_text.setPlaceholderText("Filter markups…")
        self.filter_type = QComboBox()
        self.filter_status = QComboBox()
        self.filter_status.addItem("Any status", None)
        for status in STATUSES:
            self.filter_status.addItem(status or "(no status)", status)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.totals = QLabel("")
        self.totals.setWordWrap(True)
        status_button = QToolButton()
        status_button.setText("Set status")
        status_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        status_menu = QMenu(status_button)
        for status in STATUSES:
            status_menu.addAction(status or "(clear)",
                                  lambda s=status: self.statusRequested.emit(self.selected(), s))
        status_button.setMenu(status_menu)
        delete_button = QPushButton("Delete")
        delete_button.clicked.connect(lambda: self.deleteRequested.emit(self.selected()))
        export_button = QPushButton("Export CSV…")
        export_button.clicked.connect(self.exportRequested)
        top = QHBoxLayout()
        top.addWidget(self.filter_text, 2)
        top.addWidget(self.filter_type, 1)
        top.addWidget(self.filter_status, 1)
        top.addWidget(status_button)
        top.addWidget(delete_button)
        top.addWidget(export_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.totals)
        self.filter_text.textChanged.connect(self._apply_filter)
        self.filter_type.currentIndexChanged.connect(self._apply_filter)
        self.filter_status.currentIndexChanged.connect(self._apply_filter)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(self._double_clicked)

    def set_rows(self, rows: list[dict]) -> None:
        self._updating = True
        self.rows = rows
        selected_type = self.filter_type.currentData()
        self.filter_type.blockSignals(True)
        self.filter_type.clear()
        self.filter_type.addItem("All types", None)
        for label in sorted({row["type"] for row in rows}):
            self.filter_type.addItem(label, label)
        index = self.filter_type.findData(selected_type)
        self.filter_type.setCurrentIndex(max(0, index))
        self.filter_type.blockSignals(False)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for number, row in enumerate(rows):
            values = [row["page"] + 1, row["type"], row["subject"], row["comment"], row["author"],
                      row["date"], row["status"], row["color"], row["measurement"]]
            for column, value in enumerate(values):
                item = QTableWidgetItem()
                item.setData(Qt.ItemDataRole.DisplayRole, value)
                item.setData(Qt.ItemDataRole.UserRole, (row["page"], row["xref"]))
                if column == 7 and row["color"]:
                    item.setBackground(QColor(row["color"]))
                self.table.setItem(number, column, item)
        self.table.setSortingEnabled(True)
        self._apply_filter()
        totals = markups.measurement_totals(rows)
        parts = []
        for subject, (value, count) in sorted(totals.items()):
            sample = next(r for r in rows if (r["subject"] or r["type"]) == subject and
                          r["value"] is not None)
            if sample["kind"] == "count":
                parts.append(f"{subject}: {count}")
            elif sample["kind"] == "angle":
                continue
            else:
                unit = sample["measurement"].split(" ", 1)[1] if " " in sample["measurement"] \
                    and not sample["measurement"].endswith('"') else ""
                shown = f"{value:.2f} {unit}".strip() if unit else f"{value:.2f}"
                parts.append(f"{subject}: {shown} ({count})")
        self.totals.setText("Totals — " + " · ".join(parts) if parts else "")
        self._updating = False

    def _apply_filter(self) -> None:
        text = self.filter_text.text().strip().lower()
        kind = self.filter_type.currentData()
        status = self.filter_status.currentData()
        for row in range(self.table.rowCount()):
            values = [self.table.item(row, c).text().lower() for c in range(len(self.COLUMNS))]
            hidden = bool(text) and not any(text in value for value in values)
            hidden |= kind is not None and self.table.item(row, 1).text() != kind
            hidden |= status is not None and self.table.item(row, 6).text() != status
            self.table.setRowHidden(row, hidden)

    def selected(self) -> list[tuple[int, int]]:
        keys = []
        for index in self.table.selectionModel().selectedRows():
            keys.append(self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole))
        return keys

    def select_keys(self, keys: list[tuple[int, int]]) -> None:
        self._updating = True
        self.table.clearSelection()
        wanted = set(keys)
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) in wanted:
                self.table.selectRow(row)
        self._updating = False

    def _selection_changed(self) -> None:
        if not self._updating:
            self.selectionChanged.emit(self.selected())

    def _double_clicked(self, row: int, _column: int) -> None:
        page, xref = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self.activated.emit(page, xref)

    def _context_menu(self, position) -> None:
        keys = self.selected()
        if not keys:
            return
        menu = QMenu(self)
        menu.addAction("Go to markup", lambda: self.activated.emit(*keys[0]))
        status_menu = menu.addMenu("Set status")
        for status in STATUSES:
            status_menu.addAction(status or "(clear)",
                                  lambda s=status: self.statusRequested.emit(keys, s))
        menu.addAction("Delete", lambda: self.deleteRequested.emit(keys))
        menu.exec(self.table.viewport().mapToGlobal(position))


class ToolChestPanel(QWidget):
    """Tool sets: click a tool to make it active with its saved properties."""

    toolChosen = Signal(object)  # ToolPreset

    def __init__(self, chest: ToolChest, parent=None) -> None:
        super().__init__(parent)
        self.chest = chest
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.setIconSize(QSize(18, 18))
        buttons = QHBoxLayout()
        self.new_set = QPushButton("New set…")
        self.import_button = QPushButton("Import…")
        self.export_button = QPushButton("Export…")
        for button in (self.new_set, self.import_button, self.export_button):
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        expanded = {self.tree.topLevelItem(i).text(0): self.tree.topLevelItem(i).isExpanded()
                    for i in range(self.tree.topLevelItemCount())}
        self.tree.clear()
        for name, tools in self.chest.sets().items():
            parent = QTreeWidgetItem([f"{name}  ({len(tools)})"])
            parent.setData(0, Qt.ItemDataRole.UserRole, ("set", name))
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            self.tree.addTopLevelItem(parent)
            for tool in tools:
                child = QTreeWidgetItem([tool.name])
                child.setData(0, Qt.ItemDataRole.UserRole, ("tool", tool.id))
                child.setToolTip(0, KIND_LABELS.get(tool.kind, tool.kind))
                style = tool.make_style()
                swatch = QPixmap(16, 16)
                swatch.fill(QColor(style.fill or style.stroke or "#888888"))
                child.setIcon(0, QIcon(swatch))
                parent.addChild(child)
            parent.setExpanded(expanded.get(parent.text(0), name in (RECENT, "Markup",
                                                                      "Construction Review")))

    def selected_set(self) -> str | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        while item.parent() is not None:
            item = item.parent()
        return item.data(0, Qt.ItemDataRole.UserRole)[1]

    def _clicked(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data[0] == "tool":
            tool = self.chest.find(data[1])
            if tool:
                self.toolChosen.emit(tool)

    def _context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            return
        kind, key = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        if kind == "tool":
            set_name = item.parent().data(0, Qt.ItemDataRole.UserRole)[1]
            if not self.chest.is_builtin(set_name):
                menu.addAction("Remove tool", lambda: (self.chest.remove(key), self.refresh()))
            copy_menu = menu.addMenu("Copy to set")
            for name in self.chest.user:
                tool = self.chest.find(key)
                copy_menu.addAction(name, lambda n=name, t=tool: (
                    self.chest.add(n, ToolPreset(t.name, t.kind, dict(t.style))), self.refresh()))
        elif not self.chest.is_builtin(key):
            menu.addAction("Remove set", lambda: self._remove_set(key))
        if not menu.isEmpty():
            menu.exec(self.tree.viewport().mapToGlobal(position))

    def _remove_set(self, name: str) -> None:
        try:
            self.chest.remove_set(name)
        except ValueError:
            return
        self.refresh()


class ThumbnailsPanel(QListWidget):
    """Page thumbnails; drag to reorder, right-click for page actions."""

    pageActivated = Signal(int)
    reorderRequested = Signal(list)
    contextRequested = Signal(object, list)  # global pos, page indices

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setIconSize(QSize(120, 150))
        self.setSpacing(6)
        self.setMovement(QListWidget.Movement.Snap)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.contextRequested.emit(self.viewport().mapToGlobal(pos),
                                                   self.selected_pages()))
        self.itemClicked.connect(lambda item: self.pageActivated.emit(
            item.data(Qt.ItemDataRole.UserRole)))
        self._render = None
        self._pending: list[int] = []
        self._timer = QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._render_next)

    def populate(self, count: int, labels: list[str], render) -> None:
        """render(index) -> QPixmap. Thumbnails render a few at a time."""
        self._render = render
        self.clear()
        placeholder = QPixmap(96, 120)
        placeholder.fill(QColor("#e8e8e8"))
        for index in range(count):
            item = QListWidgetItem(QIcon(placeholder), labels[index])
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.addItem(item)
        self._pending = list(range(count))
        self._timer.start()

    def _render_next(self) -> None:
        for _ in range(3):
            if not self._pending or self._render is None:
                self._timer.stop()
                return
            index = self._pending.pop(0)
            if index < self.count():
                try:
                    self.item(index).setIcon(QIcon(self._render(index)))
                except Exception:
                    pass

    def set_current(self, index: int) -> None:
        if 0 <= index < self.count():
            self.blockSignals(True)
            self.setCurrentRow(index)
            self.blockSignals(False)

    def selected_pages(self) -> list[int]:
        return sorted(item.data(Qt.ItemDataRole.UserRole) for item in self.selectedItems())

    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        order = [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]
        if order != sorted(order):
            self.reorderRequested.emit(order)


class BookmarksPanel(QWidget):
    """Document outline with add, rename, and delete."""

    pageActivated = Signal(int)
    tocEdited = Signal(list)  # new table of contents for doc.set_toc

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._clicked)
        self.toc: list = []
        self.current_page = 0
        add = QPushButton("Add")
        add.setToolTip("Bookmark the current page")
        rename = QPushButton("Rename")
        remove = QPushButton("Delete")
        add.clicked.connect(self._add)
        rename.clicked.connect(self._rename)
        remove.clicked.connect(self._remove)
        buttons = QHBoxLayout()
        for button in (add, rename, remove):
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)

    def set_toc(self, toc: list) -> None:
        self.toc = [list(entry[:3]) for entry in toc]
        self.tree.clear()
        parents: dict[int, QTreeWidgetItem] = {}
        for number, (level, title, page) in enumerate(self.toc):
            item = QTreeWidgetItem([title])
            item.setData(0, Qt.ItemDataRole.UserRole, (number, page))
            parent = parents.get(level - 1)
            (parent.addChild(item) if parent is not None else self.tree.addTopLevelItem(item))
            parents[level] = item
        self.tree.expandAll()

    def _clicked(self, item: QTreeWidgetItem) -> None:
        _number, page = item.data(0, Qt.ItemDataRole.UserRole)
        if page >= 1:
            self.pageActivated.emit(page - 1)

    def _selected_number(self) -> int | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole)[0] if item else None

    def _add(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        title, accepted = QInputDialog.getText(self, "Add bookmark", "Bookmark title:",
                                               text=f"Page {self.current_page + 1}")
        if accepted and title.strip():
            self.tocEdited.emit(self.toc + [[1, title.strip(), self.current_page + 1]])

    def _rename(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        number = self._selected_number()
        if number is None:
            return
        title, accepted = QInputDialog.getText(self, "Rename bookmark", "Bookmark title:",
                                               text=self.toc[number][1])
        if accepted and title.strip():
            toc = [list(entry) for entry in self.toc]
            toc[number][1] = title.strip()
            self.tocEdited.emit(toc)

    def _remove(self) -> None:
        number = self._selected_number()
        if number is None:
            return
        level = self.toc[number][0]
        end = number + 1
        while end < len(self.toc) and self.toc[end][0] > level:
            end += 1  # remove children with their parent
        self.tocEdited.emit(self.toc[:number] + self.toc[end:])


class LayersPanel(QWidget):
    """Optional content (layers) with visibility checkboxes."""

    toggled = Signal(int, bool)  # ui config number, visible

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.list = QListWidget()
        self.empty = QLabel("This PDF has no layers.")
        self.empty.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.empty)
        layout.addWidget(self.list, 1)
        self.list.itemChanged.connect(self._changed)

    def set_layers(self, configs: list[dict]) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for config in configs:
            item = QListWidgetItem(("    " * config.get("depth", 0)) + (config.get("text") or "Layer"))
            item.setData(Qt.ItemDataRole.UserRole, config["number"])
            if config.get("type") in ("checkbox", "radiobox"):
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if config.get("on")
                                   else Qt.CheckState.Unchecked)
            if config.get("locked"):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(item)
        self.empty.setVisible(not configs)
        self.list.blockSignals(False)

    def _changed(self, item: QListWidgetItem) -> None:
        self.toggled.emit(item.data(Qt.ItemDataRole.UserRole),
                          item.checkState() == Qt.CheckState.Checked)


class SearchPanel(QWidget):
    """Find text across every page, with whole-word and case options."""

    searchRequested = Signal(str, bool, bool)  # text, match case, whole word
    resultActivated = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Find text…")
        self.case = QCheckBox("Match case")
        self.whole = QCheckBox("Whole words")
        go = QPushButton("Search")
        self.results = QListWidget()
        self.summary = QLabel("")
        row = QHBoxLayout()
        row.addWidget(self.query, 1)
        row.addWidget(go)
        options = QHBoxLayout()
        options.addWidget(self.case)
        options.addWidget(self.whole)
        options.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(row)
        layout.addLayout(options)
        layout.addWidget(self.summary)
        layout.addWidget(self.results, 1)
        go.clicked.connect(self._search)
        self.query.returnPressed.connect(self._search)
        self.results.currentRowChanged.connect(
            lambda row: self.resultActivated.emit(row) if row >= 0 else None)

    def _search(self) -> None:
        self.searchRequested.emit(self.query.text(), self.case.isChecked(),
                                  self.whole.isChecked())

    def set_results(self, results: list[tuple[int, str]]) -> None:
        self.results.blockSignals(True)
        self.results.clear()
        for page, context in results:
            self.results.addItem(f"Page {page + 1}:  {context}")
        self.results.blockSignals(False)
        self.summary.setText(f"{len(results)} match{'es' if len(results) != 1 else ''}"
                             if self.query.text() else "")


def search_document(doc: fitz.Document, text: str, match_case: bool = False,
                    whole_words: bool = False) -> list[tuple[int, fitz.Rect, str]]:
    """Find text on every page; returns (page, rect, surrounding words)."""
    text = text.strip()
    if not text:
        return []
    hits = []
    needle = text if match_case else text.lower()
    for page in doc:
        words = markups.page_words(page, sort=True)
        textpage = markups.text_page(page, fitz.TEXTFLAGS_SEARCH)
        for rect in page.search_for(text, textpage=textpage):
            context_words = [w for w in words if fitz.Rect(w[:4]).intersects(rect + (-60, -2, 60, 2))]
            context = " ".join(w[4] for w in context_words)[:90]
            covered = " ".join(w[4] for w in words if fitz.Rect(w[:4]).intersects(rect))
            if match_case and text not in covered:
                continue
            if whole_words:
                tokens = [w[4] if match_case else w[4].lower() for w in words
                          if fitz.Rect(w[:4]).intersects(rect)]
                if " ".join(t.strip(".,;:!?()[]\"'") for t in tokens) != needle:
                    continue
            hits.append((page.number, rect, context))
    return hits
