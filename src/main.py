# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Northstar PDF: an offline PDF markup, measurement, and review app for Windows.

The workflow follows Bluebeam Revu: live markups you can select and restyle,
a Properties panel, a Markups List, a Tool Chest of saved tools, scaled
measurements, and drawing compare/overlay. Everything runs locally.
"""
from __future__ import annotations

import getpass
import os
import sys
import tempfile
from pathlib import Path

# Install the offline guard before loading any UI or PDF libraries.
from offline_guard import install_offline_guard
install_offline_guard()

import pymupdf as fitz
from PySide6.QtCore import QPointF, QSettings, QSize, QStandardPaths, Qt, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QCloseEvent, QColor, QGuiApplication,
                           QKeySequence, QPalette)
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QDockWidget,
                               QFileDialog, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
                               QMessageBox, QPlainTextEdit, QSpinBox, QStyleFactory, QTabWidget,
                               QToolBar, QVBoxLayout, QWidget)

import compare
import icons
import links
import markups
import printing
import report
from recovery import RecoveryStore
import pdf_operations as ops
from canvas import PdfCanvas
from dialogs import BatchDialog, CalibrateDialog, FormDialog, ScaleDialog
from markups import KIND_LABELS, MEASURE_KINDS, Style, default_style
from panels import (BookmarksPanel, LayersPanel, MarkupsList, PropertiesPanel, SearchPanel,
                    ThumbnailsPanel, ToolChestPanel, search_document)
from pdf_document import PdfSession
from tool_chest import MY_TOOLS, ToolChest, ToolPreset

APP_NAME = "Northstar PDF"
APP_VERSION = "0.2.0"
LAYOUT_VERSION = 3
SOURCE_URL = "https://github.com/Wrothpuddle/PDF-one"

MARKUP_TOOLS = ["text", "callout", "note", "highlight", "underline", "strikeout",
                "pen", "highlighter", "line", "arrow", "polyline", "polygon", "cloud",
                "rectangle", "ellipse", "stamp", "image"]
MEASURE_TOOLS = ["length", "polylength", "area", "volume", "perimeter", "count", "angle"]
NAV_TOOLS = {"select": "Select", "pan": "Pan", "text_select": "Select Text",
             "zoom_area": "Zoom Area", "crop": "Crop", "snapshot": "Snapshot",
             "calibrate": "Calibrate", "redact": "Redact", "link": "Link"}
SHORTCUTS = {"select": "V", "pan": "H", "text_select": "Alt+T", "text": "T", "callout": "Q",
             "note": "N", "highlight": "Alt+H", "pen": "P", "line": "L", "arrow": "A",
             "polyline": "Alt+N", "polygon": "Shift+P", "cloud": "C", "rectangle": "R",
             "ellipse": "E", "length": "Shift+Alt+L", "area": "Shift+Alt+A",
             "count": "Shift+Alt+C"}
# Tools that stay active after use (others return to Select unless locked).
STAY_ACTIVE = {"count", "pen", "highlighter", "zoom_area"}


def tool_label(kind: str) -> str:
    return NAV_TOOLS.get(kind) or KIND_LABELS.get(kind, kind.title())


class DocumentView(QWidget):
    """One document tab: its session and canvas."""

    def __init__(self, session: PdfSession, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.canvas = PdfCanvas(session, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self.search_hits: list[tuple[int, fitz.Rect, str]] = []

    @property
    def title(self) -> str:
        return self.session.path.name + (" *" if self.session.dirty else "")


class MainWindow(QMainWindow):
    def __init__(self, files: list[str] | None = None, recovery: bool = True) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1440, 900)
        # Organization/application names are set on the QApplication, so the
        # self-test can redirect settings to a temporary folder.
        self.settings = QSettings()
        data_dir = Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation) or tempfile.gettempdir())
        self.chest = ToolChest(data_dir / "tool_chest.json")
        self.user_name = str(self.settings.value("userName", "") or getpass.getuser())
        self.tool = "select"
        self.tool_locked = False
        self.tool_styles: dict[str, Style] = {}
        self.clipboard: tuple[PdfSession, list[tuple[int, int]]] | None = None
        self._temp_files: list[Path] = []
        self._recovered: set[Path] = set()
        self.recovery = RecoveryStore(data_dir / "recovery") if recovery else None
        self._autosaved: dict[int, int] = {}  # id(session) -> revision written
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(60_000)
        self._autosave_timer.timeout.connect(self._autosave)
        if self.recovery is not None:
            self._autosave_timer.start()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self._refresh_panels)
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(900)
        self._thumb_timer.timeout.connect(self._refresh_thumbnails)

        self.tabs = QTabWidget(self)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.setCentralWidget(self.tabs)
        self.start_page = self._make_start_page()
        self.tabs.addTab(self.start_page, "Start")

        self._make_docks()
        self._make_actions()
        self._make_menus()
        self._make_toolbars()
        self._make_status_bar()
        self._apply_icons()
        self._set_tool_labels(self.labels_action.isChecked())
        self.tabs.currentChanged.connect(self._tab_changed)
        geometry = self.settings.value("windowGeometry")
        state = self.settings.value("windowState")
        if geometry:
            self.restoreGeometry(geometry)
        # Panel layouts saved by older versions do not match the current docks.
        if state and self.settings.value("layoutVersion", 0, type=int) == LAYOUT_VERSION:
            self.restoreState(state)
        QTimer.singleShot(0, self.thumb_dock.raise_)
        QTimer.singleShot(0, self.props_dock.raise_)
        if self.settings.value("darkTheme", False, type=bool):
            self.dark_action.setChecked(True)
        self.set_tool("select")
        for path in files or []:
            self.open_file(path)
        self._update_actions()
        if self.recovery is not None:
            QTimer.singleShot(400, self._offer_recovery)

    # -- construction ---------------------------------------------------------
    def _make_start_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        heading = QLabel(APP_NAME, page)
        heading.setStyleSheet("font-size:28px; font-weight:600; padding:24px 24px 6px 24px;")
        hint = QLabel(
            "Open a PDF (Ctrl+O) or drop one here to mark up, measure, and compare drawings.\n"
            "Files never leave this computer.", page)
        hint.setStyleSheet("font-size:14px; padding:0 24px 18px 24px;")
        layout.addWidget(heading)
        layout.addWidget(hint)
        layout.addStretch(1)
        self.setAcceptDrops(True)
        return page

    def _dock(self, title: str, name: str, widget: QWidget, area) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    def _make_docks(self) -> None:
        left, right = Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea
        self.thumbnails = ThumbnailsPanel()
        self.bookmarks = BookmarksPanel()
        self.layers = LayersPanel()
        self.properties = PropertiesPanel()
        self.tool_chest = ToolChestPanel(self.chest)
        self.search = SearchPanel()
        self.markups_list = MarkupsList()
        self.thumb_dock = self._dock("Thumbnails", "ThumbnailsDock", self.thumbnails, left)
        self.bookmark_dock = self._dock("Bookmarks", "BookmarksDock", self.bookmarks, left)
        self.layer_dock = self._dock("Layers", "LayersDock", self.layers, left)
        self.tabifyDockWidget(self.thumb_dock, self.bookmark_dock)
        self.tabifyDockWidget(self.bookmark_dock, self.layer_dock)
        self.thumb_dock.raise_()
        self.props_dock = self._dock("Properties", "PropertiesDock", self.properties, right)
        self.chest_dock = self._dock("Tool Chest", "ToolChestDock", self.tool_chest, right)
        self.search_dock = self._dock("Search", "SearchDock", self.search, right)
        self.tabifyDockWidget(self.props_dock, self.chest_dock)
        self.tabifyDockWidget(self.chest_dock, self.search_dock)
        self.props_dock.raise_()
        self.markups_dock = self._dock("Markups List", "MarkupsListDock", self.markups_list,
                                       Qt.DockWidgetArea.BottomDockWidgetArea)
        self.resizeDocks([self.markups_dock], [210], Qt.Orientation.Vertical)
        self.resizeDocks([self.thumb_dock, self.props_dock], [210, 290], Qt.Orientation.Horizontal)

        self.thumbnails.pageActivated.connect(lambda page: self._canvas_call("go_to_page", page))
        self.thumbnails.reorderRequested.connect(self._reorder_from_thumbnails)
        self.thumbnails.contextRequested.connect(self._thumbnail_menu)
        self.bookmarks.pageActivated.connect(lambda page: self._canvas_call("go_to_page", page))
        self.bookmarks.tocEdited.connect(self._set_toc)
        self.layers.toggled.connect(self._toggle_layer)
        self.properties.styleEdited.connect(self._style_edited)
        self.properties.commentEdited.connect(self._comment_edited)
        self.properties.statusEdited.connect(
            lambda status: self._set_status(self._selection(), status))
        self.properties.saveToolRequested.connect(self._save_tool)
        self.properties.replyRequested.connect(self._reply)
        self.tool_chest.toolChosen.connect(self._tool_preset_chosen)
        self.tool_chest.new_set.clicked.connect(self._new_tool_set)
        self.tool_chest.import_button.clicked.connect(self._import_tools)
        self.tool_chest.export_button.clicked.connect(self._export_tools)
        self.search.searchRequested.connect(self._search)
        self.search.resultActivated.connect(self._search_result)
        self.markups_list.activated.connect(self._go_to_markup)
        self.markups_list.selectionChanged.connect(self._list_selection)
        self.markups_list.statusRequested.connect(self._set_status)
        self.markups_list.deleteRequested.connect(self._delete_markups)
        self.markups_list.exportRequested.connect(self._export_markups)

    def _action(self, text: str, slot=None, shortcut=None, checkable: bool = False,
                tip: str = "") -> QAction:
        action = QAction(text, self)
        if slot is not None:
            action.triggered.connect(slot)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.setCheckable(checkable)
        if tip:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        return action

    def _make_actions(self) -> None:
        a = self._action
        self.open_action = a("&Open…", self.open_dialog, QKeySequence.StandardKey.Open)
        self.save_action = a("&Save", self.save, QKeySequence.StandardKey.Save)
        self.save_as_action = a("Save &As…", self.save_as, QKeySequence.StandardKey.SaveAs)
        self.close_action = a("&Close", lambda: self._close_tab(self.tabs.currentIndex()),
                              QKeySequence.StandardKey.Close)
        self.print_action = a("&Print…", self.print_document, QKeySequence.StandardKey.Print)
        self.print_preview_action = a("Print Pre&view…", self.print_preview)
        self.print_markups_action = a("Print &Markups", None, checkable=True)
        self.print_markups_action.setChecked(self.settings.value("printMarkups", True, type=bool))
        self.print_markups_action.toggled.connect(
            lambda on: self.settings.setValue("printMarkups", on))
        self.print_fit_action = a("&Fit Pages to Paper", None, checkable=True)
        self.print_fit_action.setChecked(self.settings.value("printFit", True, type=bool))
        self.print_fit_action.toggled.connect(lambda on: self.settings.setValue("printFit", on))
        self.undo_action = a("&Undo", self.undo, QKeySequence.StandardKey.Undo)
        self.redo_action = a("&Redo", self.redo, QKeySequence.StandardKey.Redo)
        self.copy_action = a("&Copy", self.copy, QKeySequence.StandardKey.Copy)
        self.paste_action = a("&Paste", self.paste, QKeySequence.StandardKey.Paste)
        self.delete_action = a("&Delete", lambda: self._delete_markups(self._selection()),
                               QKeySequence.StandardKey.Delete)
        self.duplicate_action = a("D&uplicate", self.duplicate, "Ctrl+D")
        self.select_all_action = a("Select &All Markups on Page", self.select_all_on_page,
                                   QKeySequence.StandardKey.SelectAll)
        self.edit_text_action = a("Edit Te&xt…", self._edit_selected_text, "F2")
        self.fit_width_action = a("Fit &Width", lambda: self._canvas_call("fit", "width"), "Ctrl+2")
        self.fit_page_action = a("Fit &Page", lambda: self._canvas_call("fit", "page"), "Ctrl+0")
        self.actual_size_action = a("&Actual Size", lambda: self._set_zoom(1.0), "Ctrl+1")
        self.zoom_in_action = a("Zoom &In", lambda: self._canvas_call("zoom_by", 1.25),
                                QKeySequence.StandardKey.ZoomIn)
        self.zoom_out_action = a("Zoom &Out", lambda: self._canvas_call("zoom_by", 0.8),
                                 QKeySequence.StandardKey.ZoomOut)
        self.single_page_action = a("&Single Page", self._toggle_single_page, checkable=True)
        self.prev_page_action = a("Previous Page", lambda: self._step_page(-1), "PgUp")
        self.next_page_action = a("Next Page", lambda: self._step_page(1), "PgDown")
        self.full_screen_action = a("&Full Screen", self._toggle_full_screen, "F11")
        self.dark_action = a("&Dark Theme", None, checkable=True)
        self.dark_action.toggled.connect(self._set_dark_theme)
        self.lock_action = a("Tool &Lock", None, "Ctrl+L", checkable=True,
                             tip="Keep the current markup tool active after each use")
        self.lock_action.toggled.connect(lambda on: setattr(self, "tool_locked", on))
        self.ortho_action = a("&Ortho (snap lines to 45°)", None, "F8", checkable=True)
        self.ortho_action.toggled.connect(self._set_ortho)
        self.snap_action = a("S&nap to Content", None, "F9", checkable=True,
                             tip="Snap points to line endpoints, midpoints, and intersections")
        self.snap_action.setChecked(self.settings.value("snapToContent", True, type=bool))
        self.snap_action.toggled.connect(self._set_snap)
        self.find_action = a("&Find…", self._focus_search, QKeySequence.StandardKey.Find)
        self.find_next_action = a("Find &Next", lambda: self._step_search(1), "F3")
        self.find_prev_action = a("Find Pre&vious", lambda: self._step_search(-1), "Shift+F3")

        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_actions: dict[str, QAction] = {}
        for kind in list(NAV_TOOLS) + MARKUP_TOOLS + MEASURE_TOOLS:
            if kind in self.tool_actions:
                continue
            action = a(tool_label(kind), lambda checked=False, k=kind: self.set_tool(k),
                       SHORTCUTS.get(kind), checkable=True)
            self.tool_group.addAction(action)
            self.tool_actions[kind] = action
        self.document_actions = [
            self.save_action, self.save_as_action, self.close_action, self.select_all_action,
            self.print_action, self.print_preview_action,
            self.fit_width_action, self.fit_page_action, self.actual_size_action,
            self.zoom_in_action, self.zoom_out_action, self.single_page_action,
            self.prev_page_action, self.next_page_action, self.find_action,
            self.find_next_action, self.find_prev_action, *self.tool_actions.values()]

    def _make_menus(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")
        file_menu.addAction(self.open_action)
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self._refresh_recent_menu()
        file_menu.addAction(self.save_action)
        file_menu.addAction(self.save_as_action)
        file_menu.addAction(self.close_action)
        file_menu.addSeparator()
        file_menu.addAction(self.print_action)
        file_menu.addAction(self.print_preview_action)
        print_options = file_menu.addMenu("Print &Options")
        print_options.addAction(self.print_markups_action)
        print_options.addAction(self.print_fit_action)
        file_menu.addSeparator()
        self._doc_menu_action(file_menu, "Password-Protect a Copy…", self._protect_copy)
        self._doc_menu_action(file_menu, "Export Text…", self._export_text)
        self._doc_menu_action(file_menu, "Export Markups to CSV…", self._export_markups)
        file_menu.addSeparator()
        file_menu.addAction(self._action("E&xit", self.close, QKeySequence.StandardKey.Quit))

        edit = bar.addMenu("&Edit")
        for action in (self.undo_action, self.redo_action, None, self.copy_action,
                       self.paste_action, self.duplicate_action, self.delete_action,
                       self.select_all_action, None, self.edit_text_action, None,
                       self.find_action, self.find_next_action, self.find_prev_action):
            edit.addSeparator() if action is None else edit.addAction(action)
        edit.addSeparator()
        edit.addAction(self._action("Pre&ferences…", self._preferences))

        view = bar.addMenu("&View")
        for action in (self.fit_width_action, self.fit_page_action, self.actual_size_action,
                       self.zoom_in_action, self.zoom_out_action, None, self.single_page_action,
                       self.prev_page_action, self.next_page_action, None,
                       self.full_screen_action, self.dark_action):
            view.addSeparator() if action is None else view.addAction(action)
        view.addSeparator()
        panels = view.addMenu("&Panels")
        for dock in (self.thumb_dock, self.bookmark_dock, self.layer_dock, self.props_dock,
                     self.chest_dock, self.search_dock, self.markups_dock):
            panels.addAction(dock.toggleViewAction())
        self.toolbars_menu = view.addMenu("&Toolbars")
        self.labels_action = self._action("Show Tool &Labels", self._set_tool_labels,
                                          checkable=True)
        self.labels_action.setChecked(self.settings.value("toolLabels", False, type=bool))
        view.addAction(self.labels_action)
        self._toolbars: list[QToolBar] = []

        document = bar.addMenu("&Document")
        for text, slot, key in (
                ("Rotate Page Clockwise", lambda: self._rotate_pages(90), "Ctrl+Shift+Plus"),
                ("Rotate Page Counterclockwise", lambda: self._rotate_pages(270),
                 "Ctrl+Shift+Minus"),
                (None, None, None),
                ("Insert Blank Page", self._insert_blank, None),
                ("Insert Pages from PDF…", self._insert_pages, "Ctrl+Shift+I"),
                ("Replace Current Page…", self._replace_page, None),
                ("Delete Pages…", self._delete_pages, "Ctrl+Shift+D"),
                ("Extract Pages…", self._extract_pages, None),
                ("Reorder Pages…", self._reorder_dialog, None),
                (None, None, None),
                ("Reset Crop", self._reset_crop, None),
                ("Split into One-Page PDFs…", self._split, None),
                ("Merge PDFs…", self._merge, None)):
            if text is None:
                document.addSeparator()
            else:
                self._doc_menu_action(document, text, slot, key)
        document.addSeparator()
        document.addAction(self.tool_actions["crop"])
        links_menu = document.addMenu("&Links")
        links_menu.addAction(self.tool_actions["link"])
        self._doc_menu_action(links_menu, "Auto-Link Sheet Numbers…", self._auto_link)
        self._doc_menu_action(links_menu, "Remove Links on This Page", self._remove_page_links)

        markup = bar.addMenu("&Markup")
        for kind in ("select", "pan", "text_select", "zoom_area"):
            markup.addAction(self.tool_actions[kind])
        markup.addSeparator()
        for kind in MARKUP_TOOLS:
            markup.addAction(self.tool_actions[kind])
        markup.addSeparator()
        markup.addAction(self.lock_action)
        markup.addAction(self.ortho_action)
        markup.addSeparator()
        self._doc_menu_action(markup, "Markup Summary Report (PDF)…", self._summary_report)
        self._doc_menu_action(markup, "Flatten All Markups…", self._flatten)

        measure = bar.addMenu("Mea&sure")
        measure.addAction(self.tool_actions["calibrate"])
        self._doc_menu_action(measure, "Set Scale…", self._set_scale)
        measure.addAction(self.snap_action)
        measure.addSeparator()
        for kind in MEASURE_TOOLS:
            measure.addAction(self.tool_actions[kind])

        tools = bar.addMenu("&Tools")
        tools.addAction(self.tool_actions["snapshot"])
        self._doc_menu_action(tools, "Compare Documents…", self._compare_documents)
        self._doc_menu_action(tools, "Overlay Pages…", self._overlay_pages)
        tools.addAction(self._action("&Batch Process…", self._batch))
        tools.addSeparator()
        self._doc_menu_action(tools, "Fill Form Fields…", self._fill_form)
        self._doc_menu_action(tools, "Edit Existing Text…", self._edit_existing_text)
        tools.addSeparator()
        tools.addAction(self.tool_actions["redact"])
        self._doc_menu_action(tools, "Apply Redactions…", self._apply_redactions)

        help_menu = bar.addMenu("&Help")
        help_menu.addAction(self._action("&Keyboard Shortcuts", self._show_shortcuts))
        help_menu.addAction(self._action("&License", self._show_license))
        help_menu.addAction(self._action("&About", self._about))

    def _doc_menu_action(self, menu: QMenu, text: str, slot, shortcut=None) -> QAction:
        action = self._action(text, slot, shortcut)
        menu.addAction(action)
        self.document_actions.append(action)
        return action

    def _toolbar(self, title: str, name: str) -> QToolBar:
        toolbar = QToolBar(title, self)
        toolbar.setObjectName(name)
        toolbar.setIconSize(QSize(22, 22))
        self._toolbars.append(toolbar)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        self.toolbars_menu.addAction(toolbar.toggleViewAction())
        return toolbar

    def _action_icons(self) -> dict[QAction, str]:
        named = {self.open_action: "open", self.save_action: "save", self.print_action: "print",
                 self.undo_action: "undo", self.redo_action: "redo",
                 self.zoom_in_action: "zoom_in", self.zoom_out_action: "zoom_out",
                 self.fit_width_action: "fit_width", self.fit_page_action: "fit_page",
                 self.prev_page_action: "prev_page", self.next_page_action: "next_page",
                 self.lock_action: "lock", self.snap_action: "snap"}
        named.update({action: kind for kind, action in self.tool_actions.items()})
        return named

    def _apply_icons(self) -> None:
        """(Re)draw icons in the current theme's text colour."""
        foreground = self.palette().color(QPalette.ColorRole.WindowText)
        for action, name in self._action_icons().items():
            action.setIcon(icons.make_icon(name, foreground))
            shortcut = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            label = action.text().replace("&", "")
            action.setToolTip(f"{label} ({shortcut})" if shortcut else label)

    def _set_tool_labels(self, show: bool) -> None:
        self.settings.setValue("toolLabels", show)
        style = (Qt.ToolButtonStyle.ToolButtonTextBesideIcon if show
                 else Qt.ToolButtonStyle.ToolButtonIconOnly)
        for toolbar in self._toolbars:
            toolbar.setToolButtonStyle(style)

    def _make_toolbars(self) -> None:
        main = self._toolbar("File", "FileToolbar")
        for action in (self.open_action, self.save_action, self.print_action, None,
                       self.undo_action, self.redo_action):
            main.addSeparator() if action is None else main.addAction(action)
        nav = self._toolbar("Navigation", "NavigationToolbar")
        nav.addAction(self.prev_page_action)
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 1)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.valueChanged.connect(lambda v: self._canvas_call("go_to_page", v - 1))
        nav.addWidget(self.page_spin)
        self.page_total = QLabel(" / 0 ")
        nav.addWidget(self.page_total)
        nav.addAction(self.next_page_action)
        nav.addSeparator()
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(True)
        self.zoom_box.setMinimumWidth(80)
        for value in ("25%", "50%", "75%", "100%", "150%", "200%", "400%", "800%"):
            self.zoom_box.addItem(value)
        self.zoom_box.lineEdit().returnPressed.connect(self._zoom_box_entered)
        self.zoom_box.activated.connect(self._zoom_box_entered)
        nav.addWidget(self.zoom_box)
        for action in (self.zoom_out_action, self.zoom_in_action, self.fit_width_action,
                       self.fit_page_action):
            nav.addAction(action)
        self.addToolBarBreak()
        tools = self._toolbar("Markup", "MarkupToolbar")
        tools.setStyleSheet("QToolButton:checked { background:#cfe6f8; border:1px solid #2782bd; }")
        for kind in ("select", "pan", "text_select"):
            tools.addAction(self.tool_actions[kind])
        tools.addSeparator()
        for kind in MARKUP_TOOLS:
            tools.addAction(self.tool_actions[kind])
        tools.addSeparator()
        tools.addAction(self.lock_action)
        measure = self._toolbar("Measure", "MeasureToolbar")
        measure.setStyleSheet(tools.styleSheet())
        measure.addAction(self.tool_actions["calibrate"])
        self.scale_button = QAction("Scale: 1:1", self)
        self.scale_button.triggered.connect(self._set_scale)
        measure.addAction(self.scale_button)
        measure.addAction(self.snap_action)
        measure.addSeparator()
        for kind in MEASURE_TOOLS:
            measure.addAction(self.tool_actions[kind])
        measure.addSeparator()
        for kind in ("snapshot", "crop", "link", "redact"):
            measure.addAction(self.tool_actions[kind])

    def _make_status_bar(self) -> None:
        status = self.statusBar()
        self.tool_label = QLabel()
        self.tool_label.setStyleSheet("QLabel { background:#e8f1f9; color:#123b5d; padding:2px 8px;"
                                      " border:1px solid #9db6c9; border-radius:3px;"
                                      " font-weight:600; }")
        self.page_label = QLabel()
        self.zoom_label = QLabel()
        self.scale_label = QLabel()
        for widget in (self.scale_label, self.page_label, self.zoom_label, self.tool_label):
            status.addPermanentWidget(widget)
        status.showMessage("Ready · offline · wheel to zoom · middle-drag or Space+drag to pan")

    # -- documents --------------------------------------------------------------
    def view(self) -> DocumentView | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, DocumentView) else None

    def views(self) -> list[DocumentView]:
        return [w for w in (self.tabs.widget(i) for i in range(self.tabs.count()))
                if isinstance(w, DocumentView)]

    def _canvas_call(self, name: str, *args):
        view = self.view()
        if view is not None:
            return getattr(view.canvas, name)(*args)
        return None

    def open_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Open PDF", self._last_dir(),
                                                "PDF files (*.pdf)")
        for path in paths:
            self.open_file(path)

    def _last_dir(self) -> str:
        return str(self.settings.value("lastDirectory", "") or "")

    def open_file(self, path: str, title: str | None = None) -> DocumentView | None:
        path = str(Path(path).resolve())
        for view in self.views():
            if str(view.session.path) == path:
                self.tabs.setCurrentWidget(view)
                return view
        if not Path(path).is_file():
            QMessageBox.warning(self, "File not found", f"The file does not exist:\n{path}")
            return None
        password = None
        while True:
            try:
                session = PdfSession(path, password)
                break
            except PermissionError:
                password, accepted = QInputDialog.getText(
                    self, "Password required", f"Enter the password for {Path(path).name}:",
                    QLineEdit.EchoMode.Password)
                if not accepted:
                    return None
            except Exception as exc:
                QMessageBox.warning(self, "Could not open PDF", f"{exc}\n\n{path}")
                return None
        view = DocumentView(session, self.tabs)
        canvas = view.canvas
        canvas.createRequested.connect(lambda *args, v=view: self._create_markup(v, *args))
        canvas.moveRequested.connect(lambda *args, v=view: self._move_markups(v, *args))
        canvas.resizeRequested.connect(lambda *args, v=view: self._resize_markup(v, *args))
        canvas.pointsRequested.connect(lambda *args, v=view: self._reshape_markup(v, *args))
        canvas.editTextRequested.connect(lambda *args, v=view: self._edit_text(v, *args))
        canvas.deleteRequested.connect(lambda: self._delete_markups(self._selection()))
        canvas.selectionChanged.connect(lambda v=view: self._canvas_selection(v))
        canvas.areaRequested.connect(lambda *args, v=view: self._area_requested(v, *args))
        canvas.lineRequested.connect(lambda *args, v=view: self._line_requested(v, *args))
        canvas.pageChanged.connect(lambda page, v=view: self._page_changed(v, page))
        canvas.zoomChanged.connect(lambda zoom, v=view: self._zoom_changed(v, zoom))
        canvas.toolFinished.connect(lambda: self.set_tool("select"))
        canvas.contextRequested.connect(lambda *args, v=view: self._canvas_menu(v, *args))
        canvas.linkActivated.connect(self._follow_link)
        canvas.linkHovered.connect(lambda text: self.statusBar().showMessage(text, 3000)
                                   if text else None)
        canvas.textSelected.connect(lambda text: self.statusBar().showMessage(
            f"{len(text.split())} words selected · Ctrl+C to copy", 3000))
        session.add_listener(lambda v=view: self._document_changed(v))
        index = self.tabs.addTab(view, title or session.path.name)
        self.tabs.setTabToolTip(index, path)
        self.tabs.setCurrentIndex(index)
        if self.start_page is not None and self.tabs.indexOf(self.start_page) >= 0:
            self.tabs.removeTab(self.tabs.indexOf(self.start_page))
        canvas.set_tool(self.tool if self.tool not in ("crop",) else "select")
        canvas.ortho = self.ortho_action.isChecked()
        canvas.set_snap(self.snap_action.isChecked())
        if title is None:
            self._remember_recent(path)
        self.settings.setValue("lastDirectory", str(Path(path).parent))
        self.statusBar().showMessage(f"Opened {session.path.name}", 3000)
        return view

    def _remember_recent(self, path: str) -> None:
        recent = [p for p in self.settings.value("recentFiles", [], type=list) if p != path]
        self.settings.setValue("recentFiles", [path] + recent[:11])
        self._refresh_recent_menu()

    def _refresh_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent = self.settings.value("recentFiles", [], type=list)
        for path in recent:
            action = self.recent_menu.addAction(Path(path).name)
            action.setToolTip(path)
            action.triggered.connect(lambda checked=False, p=path: self.open_file(p))
        if not recent:
            self.recent_menu.addAction("No recent files").setEnabled(False)

    def save(self) -> bool:
        view = self.view()
        if view is None:
            return False
        if view.session.path in self._temp_files:
            return self.save_as()
        return self._save_view(view)

    def _save_view(self, view: DocumentView, path: str | None = None) -> bool:
        self.properties.flush_comment()
        try:
            view.session.save(path)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save PDF", str(exc))
            return False
        self._recovered.discard(view.session.path)
        if self.recovery is not None:
            self.recovery.forget(view.session)
        self._update_tab_title(view)
        self.statusBar().showMessage(f"Saved {view.session.path}", 4000)
        return True

    def save_as(self) -> bool:
        view = self.view()
        if view is None:
            return False
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF As", str(view.session.path),
                                              "PDF files (*.pdf)")
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        if self._save_view(view, path):
            self.tabs.setTabToolTip(self.tabs.indexOf(view), path)
            self._remember_recent(str(Path(path).resolve()))
            return True
        return False

    # -- printing ------------------------------------------------------------------
    def _print_options(self) -> printing.PrintOptions:
        return printing.PrintOptions(markups=self.print_markups_action.isChecked(),
                                     fit=self.print_fit_action.isChecked())

    def _printer(self):
        from PySide6.QtPrintSupport import QPrinter
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(self.view().session.path.stem)
        return printer

    def print_document(self) -> None:
        view = self.view()
        if view is None:
            return
        from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog
        printer = self._printer()
        dialog = QPrintDialog(printer, self)
        dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintCurrentPage, True)
        dialog.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, True)
        dialog.setMinMax(1, view.session.page_count)
        if not dialog.exec():
            return
        pages = printing.page_list(printer, view.session.page_count, view.canvas.current_page)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            count = printing.print_pages(view.session.doc, printer, pages, self._print_options())
        except Exception as exc:
            QMessageBox.warning(self, "Print", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.statusBar().showMessage(f"Sent {count} page(s) to the printer", 4000)

    def print_preview(self) -> None:
        view = self.view()
        if view is None:
            return
        from PySide6.QtPrintSupport import QPrintPreviewDialog
        printer = self._printer()
        dialog = QPrintPreviewDialog(printer, self)
        dialog.setWindowTitle(f"Print Preview — {view.session.path.name}")
        dialog.resize(1000, 800)
        dialog.paintRequested.connect(lambda target: printing.print_pages(
            view.session.doc, target, printing.page_list(
                target, view.session.page_count, view.canvas.current_page),
            self._print_options()))
        dialog.exec()

    def _confirm_close(self, view: DocumentView) -> bool:
        if view.session.path in self._recovered and not view.session.dirty:
            self.tabs.setCurrentWidget(view)
            answer = QMessageBox.question(
                self, "Recovered document", "Save this recovered document before closing?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
            if answer == QMessageBox.StandardButton.Cancel:
                return False
            return answer == QMessageBox.StandardButton.Discard or self.save_as()
        if not view.session.dirty:
            return True
        self.tabs.setCurrentWidget(view)
        answer = QMessageBox.question(
            self, "Unsaved changes", f"Save changes to {view.session.path.name} before closing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return True

    def _close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if isinstance(widget, DocumentView):
            if not self._confirm_close(widget):
                return
            if self.clipboard and self.clipboard[0] is widget.session:
                self.clipboard = None
            if self.recovery is not None:
                self.recovery.forget(widget.session)
            # Remove the tab and stop rendering before closing the document, so
            # no queued paint or tab-change refresh touches a closed file.
            self.tabs.removeTab(index)
            widget.canvas.shutdown()
            widget.session.close()
        elif index >= 0:
            self.tabs.removeTab(index)
        if widget is not None and widget is not self.start_page:
            widget.deleteLater()
        if self.tabs.count() == 0:
            self.start_page = self._make_start_page()
            self.tabs.addTab(self.start_page, "Start")

    def closeEvent(self, event: QCloseEvent) -> None:
        for view in self.views():
            if not self._confirm_close(view):
                event.ignore()
                return
        for view in self.views():
            if self.recovery is not None:
                self.recovery.forget(view.session)
            view.canvas.shutdown()
            view.session.close()
        for path in self._temp_files:
            path.unlink(missing_ok=True)
        self.settings.setValue("windowGeometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.setValue("layoutVersion", LAYOUT_VERSION)
        event.accept()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            if url.toLocalFile().lower().endswith(".pdf"):
                self.open_file(url.toLocalFile())

    # -- state refresh -------------------------------------------------------------
    def _tab_changed(self, _index: int) -> None:
        view = self.view()
        if view is not None:
            view.canvas.set_tool(self.tool)
            view.canvas.ortho = self.ortho_action.isChecked()
        self._refresh_panels()
        self._refresh_thumbnails()
        self._update_actions()

    def _document_changed(self, view: DocumentView) -> None:
        self._update_tab_title(view)
        if view is self.view():
            self._refresh_timer.start()
            self._thumb_timer.start()
            self._update_actions()

    def _update_tab_title(self, view: DocumentView) -> None:
        index = self.tabs.indexOf(view)
        if index >= 0:
            self.tabs.setTabText(index, view.title)

    def _refresh_panels(self) -> None:
        view = self.view()
        if view is None:
            self.markups_list.set_rows([])
            self.bookmarks.set_toc([])
            self.layers.set_layers([])
            self.search.set_results([])
            self._show_properties()
            self._update_status()
            return
        doc = view.session.doc
        self.markups_list.set_rows(markups.list_markups(doc))
        self.markups_list.select_keys(view.canvas.selection)
        self.bookmarks.set_toc(doc.get_toc(simple=True))
        try:
            self.layers.set_layers(doc.layer_ui_configs())
        except Exception:
            self.layers.set_layers([])
        self.search.set_results([(page, context) for page, _r, context in view.search_hits])
        self._show_properties()
        self._update_status()

    def _refresh_thumbnails(self) -> None:
        view = self.view()
        if view is None:
            self.thumbnails.clear()
            return
        doc = view.session.doc
        labels = []
        for index in range(doc.page_count):
            label = doc[index].get_label() or str(index + 1)
            labels.append(label if label == str(index + 1) else f"{index + 1} ({label})")
        self.thumbnails.populate(doc.page_count, labels,
                                 lambda index, c=view.canvas: c.page_thumbnail(index, 140))
        self.thumbnails.set_current(view.canvas.current_page)

    def _update_actions(self) -> None:
        view = self.view()
        has_doc = view is not None
        for action in self.document_actions:
            action.setEnabled(has_doc)
        session = view.session if view else None
        self.undo_action.setEnabled(bool(session and session.can_undo))
        self.redo_action.setEnabled(bool(session and session.can_redo))
        self.undo_action.setText(f"&Undo {session.undo_label}" if session and session.can_undo
                                 else "&Undo")
        self.redo_action.setText(f"&Redo {session.redo_label}" if session and session.can_redo
                                 else "&Redo")
        selection = bool(view and view.canvas.selection)
        for action in (self.copy_action, self.duplicate_action, self.delete_action,
                       self.edit_text_action):
            action.setEnabled(selection or (action is self.copy_action and has_doc))
        self.paste_action.setEnabled(has_doc and self.clipboard is not None)
        if view is not None:
            count = view.session.page_count
            self.page_spin.blockSignals(True)
            self.page_spin.setRange(1, count)
            self.page_spin.setValue(view.canvas.current_page + 1)
            self.page_spin.blockSignals(False)
            self.page_total.setText(f" / {count} ")
            self.single_page_action.setChecked(view.canvas.single_page)
        else:
            self.page_total.setText(" / 0 ")

    def _update_status(self) -> None:
        view = self.view()
        self.tool_label.setText(f"TOOL  {tool_label(self.tool)}"
                                + ("  🔒" if self.tool_locked else ""))
        if view is None:
            self.page_label.setText("")
            self.zoom_label.setText("")
            self.scale_label.setText("")
            self.scale_button.setText("Scale: —")
            return
        canvas = view.canvas
        doc = view.session.doc
        page = min(canvas.current_page, doc.page_count - 1)
        self.page_label.setText(f"Page {page + 1} of {doc.page_count}")
        self.zoom_label.setText(f"{canvas.zoom * 100:.0f}%")
        scale = markups.page_scale(doc[page])
        self.scale_label.setText(f"Scale: {scale.label}")
        self.scale_button.setText(f"Scale: {scale.label}")

    def _page_changed(self, view: DocumentView, page: int) -> None:
        if view is not self.view():
            return
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(page + 1)
        self.page_spin.blockSignals(False)
        self.thumbnails.set_current(page)
        self.bookmarks.current_page = page
        self._update_status()

    def _zoom_changed(self, view: DocumentView, zoom: float) -> None:
        if view is self.view():
            self.zoom_box.setEditText(f"{zoom * 100:.0f}%")
            self._update_status()

    def _zoom_box_entered(self, *_args) -> None:
        text = self.zoom_box.currentText().strip().rstrip("%")
        try:
            self._set_zoom(float(text) / 100)
        except ValueError:
            pass

    def _set_zoom(self, zoom: float) -> None:
        view = self.view()
        if view:
            view.canvas.fit_mode = ""
            view.canvas.set_zoom(zoom)

    def _step_page(self, step: int) -> None:
        view = self.view()
        if view:
            view.canvas.go_to_page(view.canvas.current_page + step)

    def _toggle_single_page(self, single: bool) -> None:
        view = self.view()
        if view:
            view.canvas.set_single_page(single)

    def _toggle_full_screen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _set_snap(self, on: bool) -> None:
        self.settings.setValue("snapToContent", on)
        for view in self.views():
            view.canvas.set_snap(on)

    def _set_ortho(self, on: bool) -> None:
        for view in self.views():
            view.canvas.ortho = on

    def _set_dark_theme(self, enabled: bool) -> None:
        self.settings.setValue("darkTheme", enabled)
        app = QApplication.instance()
        app.setStyle(QStyleFactory.create("Fusion"))
        if not enabled:
            app.setPalette(app.style().standardPalette())
            self._apply_icons()
            return
        palette = QPalette()
        for role, colour in ((QPalette.ColorRole.Window, "#2b2d31"),
                             (QPalette.ColorRole.WindowText, "#e7e8ea"),
                             (QPalette.ColorRole.Base, "#1f2124"),
                             (QPalette.ColorRole.AlternateBase, "#2b2d31"),
                             (QPalette.ColorRole.Text, "#e7e8ea"),
                             (QPalette.ColorRole.Button, "#33363b"),
                             (QPalette.ColorRole.ButtonText, "#e7e8ea"),
                             (QPalette.ColorRole.Highlight, "#1683d8"),
                             (QPalette.ColorRole.HighlightedText, "#ffffff"),
                             (QPalette.ColorRole.ToolTipBase, "#33363b"),
                             (QPalette.ColorRole.ToolTipText, "#e7e8ea")):
            palette.setColor(role, QColor(colour))
        app.setPalette(palette)
        self._apply_icons()

    # -- tools ------------------------------------------------------------------------
    def set_tool(self, kind: str, style: Style | None = None) -> None:
        if style is not None:
            self.tool_styles[kind] = style
        self.tool = kind
        action = self.tool_actions.get(kind)
        if action is not None:
            action.setChecked(True)
        view = self.view()
        if view is not None:
            view.canvas.set_tool(kind)
            view.canvas.setFocus()
        self._show_properties()
        self._update_status()
        hints = {
            "select": "Click a markup to select · drag to move · drag handles to resize",
            "pan": "Drag to pan", "text_select": "Drag over text to select it · Ctrl+C copies",
            "calibrate": "Drag along a known dimension to calibrate the scale",
            "crop": "Drag the area to keep", "snapshot": "Drag an area to copy as an image",
            "zoom_area": "Drag an area to zoom to",
        }
        if kind in markups.CLICK_POINTS:
            message = "Click points · double-click, right-click, or Enter to finish · Backspace undoes a point"
        elif kind in markups.DRAG_LINE:
            message = "Drag to draw · hold Shift to snap to 45°"
        elif kind in markups.SINGLE_CLICK:
            message = "Click to place"
        else:
            message = hints.get(kind, "Drag on the page")
        self.statusBar().showMessage(f"{tool_label(kind)}: {message} · Esc returns to Select")

    def style_for(self, kind: str) -> Style:
        if kind not in self.tool_styles:
            self.tool_styles[kind] = default_style(kind)
        return self.tool_styles[kind]

    def _tool_preset_chosen(self, preset: ToolPreset) -> None:
        self.set_tool(preset.kind, preset.make_style())
        self.statusBar().showMessage(f"Tool Chest: {preset.name}", 3000)

    def _save_tool(self) -> None:
        view = self.view()
        selection = self._selection()
        if selection and view:
            page, xref = selection[0]
            pdf_page = view.session.doc[page]
            kind = markups.markup_kind(pdf_page, pdf_page.load_annot(xref))
            style = markups.read_style(pdf_page, xref)
        elif self.tool in KIND_LABELS:
            kind, style = self.tool, self.style_for(self.tool)
        else:
            return
        user_sets = [name for name in self.chest.user if name != "Recent Tools"]
        set_name, accepted = QInputDialog.getItem(self, "Add to Tool Chest", "Tool set:",
                                                  user_sets, max(0, user_sets.index(MY_TOOLS))
                                                  if MY_TOOLS in user_sets else 0, True)
        if not accepted or not set_name.strip():
            return
        name, accepted = QInputDialog.getText(self, "Add to Tool Chest", "Tool name:",
                                              text=style.subject or KIND_LABELS.get(kind, kind))
        if not accepted:
            return
        if set_name not in self.chest.user:
            self.chest.add_set(set_name)
        self.chest.add(set_name, ToolPreset(name.strip() or KIND_LABELS[kind], kind,
                                            style.to_dict()))
        self.tool_chest.refresh()
        self.chest_dock.raise_()

    def _new_tool_set(self) -> None:
        name, accepted = QInputDialog.getText(self, "New tool set", "Set name:")
        if accepted:
            try:
                self.chest.add_set(name)
            except ValueError as exc:
                QMessageBox.warning(self, "Tool Chest", str(exc))
            self.tool_chest.refresh()

    def _import_tools(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import tool set", "", "Tool sets (*.json)")
        if path:
            try:
                names = self.chest.import_sets(path)
            except Exception as exc:
                QMessageBox.warning(self, "Could not import tools", str(exc))
                return
            self.tool_chest.refresh()
            self.statusBar().showMessage(f"Imported {', '.join(names)}", 4000)

    def _export_tools(self) -> None:
        name = self.tool_chest.selected_set()
        if not name:
            QMessageBox.information(self, "Export tool set", "Select a tool set first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export tool set", f"{name}.json",
                                              "Tool sets (*.json)")
        if path:
            self.chest.export_set(name, path)

    # -- markup editing ---------------------------------------------------------------
    def _selection(self) -> list[tuple[int, int]]:
        view = self.view()
        return list(view.canvas.selection) if view else []

    def _apply(self, view: DocumentView, label: str, operation):
        try:
            return view.session.apply(label, operation)
        except Exception as exc:
            QMessageBox.warning(self, label, str(exc))
            return None

    def _create_markup(self, view: DocumentView, kind: str, page: int, points: list,
                       text: str) -> None:
        if kind not in KIND_LABELS:
            return
        style = Style.from_dict(self.style_for(kind).to_dict())
        if kind == "image" and not (style.image and Path(style.image).is_file()):
            picture, _ = QFileDialog.getOpenFileName(
                self, "Choose a picture for the image stamp", self._last_dir(),
                "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff)")
            if not picture:
                return
            style.image = picture
            self.style_for("image").image = picture  # reuse it for the next placement
        if kind == "note" and not text:
            text, accepted = QInputDialog.getMultiLineText(self, "Note", "Note text:")
            if not accepted:
                return
        xref = self._apply(view, f"Add {KIND_LABELS[kind]}", lambda doc: markups.create_markup(
            doc[page], kind, points, style, text=text, author=self.user_name))
        if xref is None:
            return
        self.chest.note_used(kind, style)
        self.tool_chest.refresh()
        if kind in STAY_ACTIVE or self.tool_locked:
            return
        self.set_tool("select")
        view.canvas.set_selection([(page, xref)])

    def _move_markups(self, view: DocumentView, page: int, xrefs: list, dx: float,
                      dy: float) -> None:
        def move(doc):
            for xref in xrefs:
                markups.move_markup(doc[page], xref, dx, dy)
        self._apply(view, "Move", move)

    def _resize_markup(self, view: DocumentView, page: int, xref: int, rect) -> None:
        self._apply(view, "Resize", lambda doc: markups.resize_markup(doc[page], xref, rect))

    def _reshape_markup(self, view: DocumentView, page: int, xref: int, points: list) -> None:
        self._apply(view, "Edit Points", lambda doc: markups.set_markup_points(doc[page], xref,
                                                                                 points))

    def _edit_text(self, view: DocumentView, page: int, xref: int) -> None:
        pdf_page = view.session.doc[page]
        annot = pdf_page.load_annot(xref)
        current = annot.info.get("content", "")
        kind = markups.markup_kind(pdf_page, annot)
        title = "Edit text" if kind in ("text", "callout", "note") else "Edit comment"
        text, accepted = QInputDialog.getMultiLineText(self, title, title + ":", current)
        if accepted and text != current:
            if kind in ("text", "callout") and not text.strip():
                QMessageBox.warning(self, title, "Text boxes cannot be empty.")
                return
            self._apply(view, "Edit Text", lambda doc: markups.set_text(doc[page], xref, text))

    def _reply(self) -> None:
        view = self.view()
        selection = self._selection()
        if view is None or len(selection) != 1:
            return
        page, xref = selection[0]
        text, accepted = QInputDialog.getMultiLineText(self, "Reply", "Your reply:")
        if accepted and text.strip():
            self._apply(view, "Reply", lambda doc: markups.add_reply(doc[page], xref, text,
                                                                     self.user_name))

    def _edit_selected_text(self) -> None:
        view = self.view()
        selection = self._selection()
        if view and selection:
            self._edit_text(view, *selection[0])

    def _delete_markups(self, keys: list) -> None:
        view = self.view()
        if not view or not keys:
            return

        def delete(doc):
            for page, xref in keys:
                markups.delete_markup(doc[page], xref)
        view.canvas.set_selection([])
        self._apply(view, "Delete", delete)

    def _set_status(self, keys: list, status: str) -> None:
        view = self.view()
        if not view or not keys:
            return

        def apply(doc):
            for page, xref in keys:
                markups.set_status(doc[page], xref, status)
        self._apply(view, "Set Status", apply)

    def _style_edited(self, style: Style) -> None:
        view = self.view()
        selection = self._selection()
        if view and selection:
            def apply(doc):
                for page, xref in selection:
                    pdf_page = doc[page]
                    current = markups.read_style(pdf_page, xref)
                    kind = markups.markup_kind(pdf_page, pdf_page.load_annot(xref))
                    applied = Style.from_dict(style.to_dict())
                    if len(selection) > 1:
                        # Multi-select changes colours/lines but keeps each one's own text.
                        applied.font_size, applied.subject = current.font_size, current.subject
                    if kind in ("text", "callout") and not markups.is_rich_text(pdf_page, xref):
                        applied.stroke = applied.text_color  # plain text boxes: one colour
                    markups.apply_style(pdf_page, xref, applied)
            self._apply(view, "Change Properties", apply)
        elif self.tool in KIND_LABELS:
            self.tool_styles[self.tool] = style

    def _comment_edited(self, text: str) -> None:
        view = self.view()
        selection = self._selection()
        if not view or len(selection) != 1:
            return
        page, xref = selection[0]
        pdf_page = view.session.doc[page]
        annot = pdf_page.load_annot(xref)
        if annot is None or annot.info.get("content", "") == text:
            return
        kind = markups.markup_kind(pdf_page, annot)
        if kind in ("text", "callout") and not text.strip():
            return
        self._apply(view, "Edit Comment", lambda doc: markups.set_text(doc[page], xref, text))

    def _canvas_selection(self, view: DocumentView) -> None:
        if view is not self.view():
            return
        self.properties.flush_comment()
        self.markups_list.select_keys(view.canvas.selection)
        self._show_properties()
        self._update_actions()

    def _list_selection(self, keys: list) -> None:
        view = self.view()
        if view is None:
            return
        view.canvas.set_selection(keys)
        if keys:
            page, xref = keys[0]
            if page != view.canvas.current_page:
                self._go_to_markup(page, xref)

    def _go_to_markup(self, page: int, xref: int) -> None:
        view = self.view()
        if view is None:
            return
        try:
            bounds = markups.markup_bounds(view.session.doc[page], xref)
        except LookupError:
            return
        view.canvas.go_to_page(page, bounds.tl + (bounds.width / 2, bounds.height / 2))
        view.canvas.set_selection([(page, xref)])

    def _show_properties(self) -> None:
        view = self.view()
        selection = self._selection()
        if view and selection:
            page, xref = selection[0]
            pdf_page = view.session.doc[page]
            try:
                annot = pdf_page.load_annot(xref)
                kind = markups.markup_kind(pdf_page, annot)
                style = markups.read_style(pdf_page, xref)
            except Exception:
                self.properties.show_nothing()
                return
            result = markups.measurement(pdf_page, xref) if kind in MEASURE_KINDS else None
            heading = KIND_LABELS.get(kind, kind) if len(selection) == 1 else \
                f"{len(selection)} markups selected"
            status = markups._get_string(view.session.doc, xref, markups.STATUS_KEY)
            self.properties.show_style(kind, style, heading,
                                       annot.info.get("content", "") if len(selection) == 1
                                       else None, status, result[1] if result else "")
            self.properties.set_replies(markups.replies(pdf_page, xref)
                                        if len(selection) == 1 else None)
        elif self.tool in KIND_LABELS:
            self.properties.show_style(self.tool, self.style_for(self.tool),
                                       f"{KIND_LABELS[self.tool]} tool defaults")
        else:
            self.properties.show_nothing()

    def copy(self) -> None:
        view = self.view()
        if view is None:
            return
        if view.canvas.tool == "text_select" and view.canvas.selected_text:
            QGuiApplication.clipboard().setText(view.canvas.selected_text)
            return
        if view.canvas.selection:
            self.clipboard = (view.session, list(view.canvas.selection))
            self.statusBar().showMessage(f"Copied {len(view.canvas.selection)} markup(s)", 2500)
        self._update_actions()

    def paste(self) -> None:
        view = self.view()
        if view is None or self.clipboard is None:
            return
        source, keys = self.clipboard
        target_page = view.canvas.current_page
        same_doc = source is view.session

        def paste(doc):
            created = []
            for page, xref in keys:
                src_page = source.doc[page] if not same_doc else doc[page]
                created.append((target_page, markups.duplicate_markup(
                    src_page, xref, 12, 12, target=doc[target_page])))
            return created
        created = self._apply(view, "Paste", paste)
        if created:
            view.canvas.set_selection(created)

    def duplicate(self) -> None:
        view = self.view()
        keys = self._selection()
        if view is None or not keys:
            return

        def duplicate(doc):
            return [(page, markups.duplicate_markup(doc[page], xref)) for page, xref in keys]
        created = self._apply(view, "Duplicate", duplicate)
        if created:
            view.canvas.set_selection(created)

    def select_all_on_page(self) -> None:
        view = self.view()
        if view is None:
            return
        self.set_tool("select")
        page = view.canvas.current_page
        pdf_page = view.session.doc[page]
        keys = [(page, annot.xref) for annot in pdf_page.annots() or []
                if annot.type[0] not in (fitz.PDF_ANNOT_POPUP, fitz.PDF_ANNOT_LINK,
                                         fitz.PDF_ANNOT_WIDGET) and not annot.irt_xref]
        view.canvas.set_selection(keys)

    def undo(self) -> None:
        view = self.view()
        if view and view.session.undo():
            self.statusBar().showMessage("Undone", 1500)

    def redo(self) -> None:
        view = self.view()
        if view and view.session.redo():
            self.statusBar().showMessage("Redone", 1500)

    def _canvas_menu(self, view: DocumentView, global_pos, page: int, point) -> None:
        menu = QMenu(self)
        if view.canvas.selection:
            menu.addAction(self.edit_text_action)
            menu.addAction("Properties", lambda: (self.props_dock.show(), self.props_dock.raise_()))
            menu.addSeparator()
            menu.addAction(self.copy_action)
            menu.addAction(self.duplicate_action)
            menu.addAction(self.delete_action)
            status = menu.addMenu("Set Status")
            for name in markups.STATUSES:
                status.addAction(name or "(clear)",
                                 lambda s=name: self._set_status(self._selection(), s))
            menu.addAction("Add to Tool Chest…", self._save_tool)
        else:
            menu.addAction(self.paste_action)
            menu.addAction(self.select_all_action)
            menu.addSeparator()
            menu.addAction("Rotate Page Clockwise", lambda: self._rotate_pages(90))
            menu.addAction("Bookmark This Page…", self.bookmarks._add)
            menu.addAction("Set Scale…", self._set_scale)
        menu.exec(global_pos)

    # -- tool results: crop, snapshot, calibrate -------------------------------------
    def _area_requested(self, view: DocumentView, purpose: str, page: int, rect) -> None:
        if purpose == "crop":
            pdf_page = view.session.doc[page]
            offset = pdf_page.cropbox.tl
            mediabox_rect = fitz.Rect(rect) + (offset.x, offset.y, offset.x, offset.y)
            self._apply(view, "Crop", lambda doc: ops.crop_page(doc, page, mediabox_rect))
            self.set_tool("select")
        elif purpose == "link":
            count = view.session.page_count
            target, accepted = QInputDialog.getInt(self, "Link", f"Go to page (1–{count}):",
                                                   min(count, view.canvas.current_page + 2), 1,
                                                   count)
            if accepted:
                self._apply(view, "Add Link", lambda doc: links.add_page_link(
                    doc[page], rect, target - 1))
            self.set_tool("select")
        elif purpose == "snapshot":
            pixmap = view.canvas.render_area(page, rect)
            QGuiApplication.clipboard().setPixmap(pixmap)
            self.statusBar().showMessage(
                f"Snapshot copied to the clipboard ({pixmap.width()}×{pixmap.height()} px)", 4000)
            self.set_tool("select")

    def _line_requested(self, view: DocumentView, purpose: str, page: int, points: list) -> None:
        if purpose != "calibrate":
            return
        length = abs(fitz.Point(points[1]) - fitz.Point(points[0]))
        dialog = CalibrateDialog(length, view.session.page_count, self)
        if dialog.exec():
            scale = dialog.scale()
            pages = range(view.session.page_count) if dialog.all_pages.isChecked() else [page]
            self._apply(view, "Calibrate", lambda doc: [markups.set_page_scale(doc[i], scale)
                                                       for i in pages])
            self._update_status()
        self.set_tool("select")

    def _set_scale(self) -> None:
        view = self.view()
        if view is None:
            return
        page = view.canvas.current_page
        dialog = ScaleDialog(markups.page_scale(view.session.doc[page]), view.session.page_count,
                             self)
        if dialog.exec():
            try:
                scale = dialog.scale()
            except ValueError as exc:
                QMessageBox.warning(self, "Set scale", str(exc))
                return
            pages = range(view.session.page_count) if dialog.apply_to_all() else [page]
            self._apply(view, "Set Scale", lambda doc: [markups.set_page_scale(doc[i], scale)
                                                       for i in pages])
            self._update_status()

    # -- search ------------------------------------------------------------------------------
    def _focus_search(self) -> None:
        self.search_dock.show()
        self.search_dock.raise_()
        view = self.view()
        if view and view.canvas.selected_text:
            self.search.query.setText(view.canvas.selected_text.split("\n")[0][:80])
        self.search.query.setFocus()
        self.search.query.selectAll()

    def _search(self, text: str, match_case: bool, whole: bool) -> None:
        view = self.view()
        if view is None:
            return
        view.search_hits = search_document(view.session.doc, text, match_case, whole)
        self.search.set_results([(page, context) for page, _r, context in view.search_hits])
        view.canvas.show_search_hits([(page, rect) for page, rect, _c in view.search_hits],
                                     0 if view.search_hits else -1)
        if view.search_hits:
            self.search.results.blockSignals(True)
            self.search.results.setCurrentRow(0)
            self.search.results.blockSignals(False)
        else:
            self.statusBar().showMessage("No matches found", 3000)

    def _search_result(self, row: int) -> None:
        view = self.view()
        if view and 0 <= row < len(view.search_hits):
            view.canvas.show_search_hits([(p, r) for p, r, _c in view.search_hits], row)

    def _step_search(self, step: int) -> None:
        view = self.view()
        if not view or not view.search_hits:
            self._focus_search()
            return
        row = (self.search.results.currentRow() + step) % len(view.search_hits)
        self.search.results.setCurrentRow(row)

    # -- pages -------------------------------------------------------------------------------
    def _target_pages(self) -> list[int]:
        view = self.view()
        selected = self.thumbnails.selected_pages() if self.thumb_dock.isVisible() else []
        if view and len(selected) > 1:
            return selected
        return [view.canvas.current_page] if view else []

    def _rotate_pages(self, degrees: int) -> None:
        view = self.view()
        pages = self._target_pages()
        if view and pages:
            self._apply(view, "Rotate", lambda doc: [ops.rotate_page(doc, i, degrees)
                                                    for i in pages])

    def _delete_pages(self) -> None:
        view = self.view()
        pages = self._target_pages()
        if not view or not pages:
            return
        listed = ", ".join(str(p + 1) for p in pages)
        if QMessageBox.question(self, "Delete pages", f"Delete page(s) {listed}?") != \
                QMessageBox.StandardButton.Yes:
            return
        self._apply(view, "Delete Pages", lambda doc: ops.delete_pages(doc, pages))

    def _insert_blank(self) -> None:
        view = self.view()
        if view:
            after = view.canvas.current_page + 1
            try:
                view.session.apply_copy("Insert Blank Page",
                                        lambda doc: ops.insert_blank_page(doc, after))
            except Exception as exc:
                QMessageBox.warning(self, "Insert blank page", str(exc))
                return
            view.canvas.go_to_page(after)

    def _insert_pages(self) -> None:
        view = self.view()
        if view is None:
            return
        source, _ = QFileDialog.getOpenFileName(self, "Insert pages from PDF", self._last_dir(),
                                                "PDF files (*.pdf)")
        if not source:
            return
        count = view.session.page_count
        before, accepted = QInputDialog.getInt(
            self, "Insert pages", f"Insert before page (1–{count + 1}):",
            view.canvas.current_page + 2, 1, count + 1)
        if not accepted:
            return
        try:
            view.session.apply_copy("Insert Pages",
                                    lambda doc: ops.insert_pages(doc, source, before - 1))
        except PermissionError:
            password, ok = QInputDialog.getText(self, "Password required",
                                                f"Password for {Path(source).name}:",
                                                QLineEdit.EchoMode.Password)
            if ok:
                try:
                    view.session.apply_copy("Insert Pages", lambda doc: ops.insert_pages(
                        doc, source, before - 1, password))
                except Exception as exc:
                    QMessageBox.warning(self, "Insert pages", str(exc))
        except Exception as exc:
            QMessageBox.warning(self, "Insert pages", str(exc))

    def _replace_page(self) -> None:
        view = self.view()
        if view is None:
            return
        source, _ = QFileDialog.getOpenFileName(self, "Replacement page", self._last_dir(),
                                                "PDF files (*.pdf)")
        if not source:
            return
        page_number, accepted = QInputDialog.getInt(self, "Replace page",
                                                    "Use page number from that PDF:", 1, 1, 99999)
        if not accepted:
            return
        current = view.canvas.current_page
        try:
            view.session.apply_copy("Replace Page", lambda doc: ops.replace_page(
                doc, current, source, page_number - 1))
        except Exception as exc:
            QMessageBox.warning(self, "Replace page", str(exc))

    def _extract_pages(self) -> None:
        view = self.view()
        pages = self._target_pages()
        if not view or not pages:
            return
        text, accepted = QInputDialog.getText(
            self, "Extract pages", "Pages to extract (e.g. 1-3, 5):",
            text=", ".join(str(p + 1) for p in pages))
        if not accepted:
            return
        try:
            indices = parse_page_ranges(text, view.session.page_count)
        except ValueError as exc:
            QMessageBox.warning(self, "Extract pages", str(exc))
            return
        stem = view.session.path.stem
        output, _ = QFileDialog.getSaveFileName(
            self, "Save extracted pages", str(view.session.path.with_name(f"{stem}_extract.pdf")),
            "PDF files (*.pdf)")
        if not output:
            return
        try:
            ops.extract_pages(view.session.doc, indices, output,
                              view.session.password, view.session.path)
        except Exception as exc:
            QMessageBox.warning(self, "Extract pages", str(exc))
            return
        self.open_file(output)

    def _reorder_dialog(self) -> None:
        view = self.view()
        if view is None:
            return
        count = view.session.page_count
        text, accepted = QInputDialog.getText(
            self, "Reorder pages",
            f"New order of all {count} pages (ranges allowed, e.g. 3, 1-2, 4-{count}):\n"
            "Tip: you can also drag thumbnails.")
        if not accepted:
            return
        try:
            order = parse_page_ranges(text, count)
            self._apply(view, "Reorder Pages", lambda doc: ops.reorder_pages(doc, order))
        except ValueError as exc:
            QMessageBox.warning(self, "Reorder pages", str(exc))

    def _reorder_from_thumbnails(self, order: list[int]) -> None:
        view = self.view()
        if view:
            self._apply(view, "Reorder Pages", lambda doc: ops.reorder_pages(doc, order))
            self._refresh_thumbnails()

    def _thumbnail_menu(self, global_pos, pages: list[int]) -> None:
        if not pages:
            return
        view = self.view()
        menu = QMenu(self)
        menu.addAction("Rotate Clockwise", lambda: self._rotate_pages(90))
        menu.addAction("Rotate Counterclockwise", lambda: self._rotate_pages(270))
        menu.addAction("Extract…", self._extract_pages)
        menu.addAction("Insert Blank Page After", self._insert_blank)
        menu.addAction("Reset Crop", self._reset_crop)
        menu.addSeparator()
        menu.addAction("Delete…", self._delete_pages)
        if view and len(pages) == 1:
            view.canvas.go_to_page(pages[0])
        menu.exec(global_pos)

    def _reset_crop(self) -> None:
        view = self.view()
        pages = self._target_pages()
        if view and pages:
            self._apply(view, "Reset Crop", lambda doc: [ops.reset_crop(doc, i) for i in pages])

    def _split(self) -> None:
        view = self.view()
        if view is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Folder for the one-page PDFs",
                                                  str(view.session.path.parent))
        if folder:
            try:
                written = ops.split_pdf(view.session.doc, folder, view.session.path.stem,
                                        view.session.password)
            except Exception as exc:
                QMessageBox.warning(self, "Split PDF", str(exc))
                return
            self.statusBar().showMessage(f"Wrote {len(written)} PDFs to {folder}", 5000)

    def _merge(self) -> None:
        sources, _ = QFileDialog.getOpenFileNames(self, "Choose PDFs to merge (in order)",
                                                  self._last_dir(), "PDF files (*.pdf)")
        if len(sources) < 2:
            if sources:
                QMessageBox.information(self, "Merge PDFs", "Choose at least two PDFs.")
            return
        output, _ = QFileDialog.getSaveFileName(self, "Save merged PDF", "", "PDF files (*.pdf)")
        if not output:
            return
        try:
            ops.merge_pdfs(sources, output)
        except Exception as exc:
            QMessageBox.warning(self, "Merge PDFs", str(exc))
            return
        self.open_file(output)

    def _set_toc(self, toc: list) -> None:
        view = self.view()
        if view:
            # set_toc manages journal operations itself, so it cannot run inside
            # one; apply it to a document copy instead (still undoable).
            try:
                view.session.apply_copy("Edit Bookmarks", lambda doc: doc.set_toc(toc))
            except Exception as exc:
                QMessageBox.warning(self, "Edit bookmarks", str(exc))

    def _toggle_layer(self, number: int, visible: bool) -> None:
        view = self.view()
        if view is None:
            return
        # Layer visibility is a view setting, so it is not an undo step.
        view.session.doc.set_layer_ui_config(number, 0 if visible else 2)
        for item in view.canvas.pages:
            item.stale = True
        view.canvas.schedule_render()

    # -- tools menu ----------------------------------------------------------------------
    def _fill_form(self) -> None:
        view = self.view()
        if view is None:
            return
        fields = ops.read_form_fields(view.session.doc)
        if not fields:
            QMessageBox.information(self, "Fill form", "This PDF has no fillable form fields.")
            return
        dialog = FormDialog(fields, self)
        if dialog.exec():
            values = dialog.values()
            if values:
                self._apply(view, "Fill Form", lambda doc: ops.fill_form_fields(doc, values))

    def _edit_existing_text(self) -> None:
        view = self.view()
        if view is None:
            return
        page = view.canvas.current_page
        old, accepted = QInputDialog.getText(self, "Edit existing text",
                                             "Exact text to find on the current page:",
                                             text=view.canvas.selected_text.split("\n")[0])
        if not accepted or not old:
            return
        new, accepted = QInputDialog.getText(self, "Edit existing text",
                                             "Replacement text (empty removes it):")
        if accepted:
            count = self._apply(view, "Edit Text", lambda doc: ops.replace_existing_text(
                doc, page, old, new))
            if count:
                self.statusBar().showMessage(f"Replaced {count} occurrence(s)", 4000)

    def _export_text(self) -> None:
        view = self.view()
        if view is None:
            return
        output, _ = QFileDialog.getSaveFileName(self, "Export text",
                                                str(view.session.path.with_suffix(".txt")),
                                                "Text files (*.txt)")
        if output:
            ops.export_text(view.session.doc, output)
            self.statusBar().showMessage(f"Exported text to {output}", 4000)

    def _export_markups(self) -> None:
        view = self.view()
        if view is None:
            return
        output, _ = QFileDialog.getSaveFileName(
            self, "Export Markups List", str(view.session.path.with_name(
                view.session.path.stem + "_markups.csv")), "CSV files (*.csv)")
        if output:
            markups.export_markups_csv(markups.list_markups(view.session.doc), output)
            self.statusBar().showMessage(f"Exported markups to {output}", 4000)

    def _protect_copy(self) -> None:
        view = self.view()
        if view is None:
            return
        password, accepted = QInputDialog.getText(self, "Password-protect a copy",
                                                  "Password for the copy:",
                                                  QLineEdit.EchoMode.Password)
        if not accepted:
            return
        confirm, accepted = QInputDialog.getText(self, "Password-protect a copy",
                                                 "Enter the password again:",
                                                 QLineEdit.EchoMode.Password)
        if not accepted:
            return
        if password != confirm:
            QMessageBox.warning(self, "Password-protect a copy", "The passwords do not match.")
            return
        output, _ = QFileDialog.getSaveFileName(
            self, "Save protected copy", str(view.session.path.with_name(
                view.session.path.stem + "_protected.pdf")), "PDF files (*.pdf)")
        if output:
            try:
                ops.protect_pdf(view.session.pdf_bytes(encrypt=False), output, password,
                                view.session.path)
            except Exception as exc:
                QMessageBox.warning(self, "Password-protect a copy", str(exc))
                return
            self.statusBar().showMessage(f"Saved AES-256 protected copy: {output}", 5000)

    def _flatten(self) -> None:
        view = self.view()
        if view is None:
            return
        if QMessageBox.question(self, "Flatten markups",
                                "Burn every markup into the page so it can no longer be "
                                "edited? You can undo this until you close the file.") != \
                QMessageBox.StandardButton.Yes:
            return
        view.canvas.set_selection([])
        self._apply(view, "Flatten Markups", ops.flatten_markups)

    def _apply_redactions(self) -> None:
        view = self.view()
        if view is None:
            return
        pending = sum(1 for row in markups.list_markups(view.session.doc) if row["kind"] == "redact")
        if not pending:
            QMessageBox.information(self, "Apply redactions",
                                    "Mark areas with the Redact tool first.")
            return
        if QMessageBox.warning(
                self, "Apply redactions",
                f"Permanently remove all text, images, and graphics under {pending} redaction "
                "mark(s)? After saving, the removed content cannot be recovered.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel) != \
                QMessageBox.StandardButton.Yes:
            return
        view.canvas.set_selection([])
        pages = self._apply(view, "Apply Redactions", ops.apply_redactions)
        if pages:
            self.statusBar().showMessage(f"Redacted content on {pages} page(s)", 5000)

    def _choose_other_pdf(self, title: str) -> fitz.Document | None:
        path, _ = QFileDialog.getOpenFileName(self, title, self._last_dir(), "PDF files (*.pdf)")
        if not path:
            return None
        password = None
        while True:
            try:
                return ops.open_pdf(path, password)
            except PermissionError:
                password, ok = QInputDialog.getText(self, "Password required",
                                                    f"Password for {Path(path).name}:",
                                                    QLineEdit.EchoMode.Password)
                if not ok:
                    return None
            except Exception as exc:
                QMessageBox.warning(self, title, str(exc))
                return None

    def _compare_documents(self) -> None:
        view = self.view()
        if view is None:
            return
        old = self._choose_other_pdf("Choose the OLDER revision to compare against")
        if old is None:
            return
        try:
            pages = min(old.page_count, view.session.page_count)
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                found = {i: compare.difference_regions(old[i], view.session.doc[i])
                         for i in range(pages)}
            finally:
                QApplication.restoreOverrideCursor()
            total = sum(len(regions) for regions in found.values())
            if not total:
                QMessageBox.information(self, "Compare documents",
                                        f"No visible differences found on {pages} page(s).")
                return
            style = default_style("cloud")
            style.subject = "Compare: changed"
            style.stroke = "#e02020"

            def add(doc):
                for index, regions in found.items():
                    for rect in regions:
                        markups.create_markup(doc[index], "cloud",
                                              [rect.tl, rect.tr, rect.br, rect.bl], style,
                                              author=self.user_name)
            self._apply(view, "Compare Documents", add)
            self.markups_dock.show()
            self.statusBar().showMessage(
                f"Added {total} change cloud(s) on {sum(1 for r in found.values() if r)} page(s)",
                6000)
        finally:
            old.close()

    def _overlay_pages(self) -> None:
        view = self.view()
        if view is None:
            return
        old = self._choose_other_pdf("Choose the OLDER revision to overlay")
        if old is None:
            return
        try:
            pages = min(old.page_count, view.session.page_count)
            data = compare.overlay_pdf([(old[i], view.session.doc[i]) for i in range(pages)])
        finally:
            old.close()
        handle, name = tempfile.mkstemp(prefix=f"overlay-{view.session.path.stem}-",
                                        suffix=".pdf")
        os.close(handle)
        path = Path(name)
        path.write_bytes(data)
        self._temp_files.append(path.resolve())
        self.open_file(str(path), f"Overlay: {view.session.path.name}")
        self.statusBar().showMessage("Overlay: red = only in older, cyan = only in newer, "
                                     "black = unchanged", 8000)

    # -- links, report, recovery --------------------------------------------------------
    def _follow_link(self, kind: str, target) -> None:
        view = self.view()
        if view is None:
            return
        if kind == "page":
            view.canvas.go_to_page(int(target))
            return
        if kind in ("uri", "file") and target:
            if QMessageBox.question(self, "Open link", f"Open this link outside Northstar PDF?"
                                    f"\n\n{target}") == QMessageBox.StandardButton.Yes:
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                url = QUrl(str(target)) if kind == "uri" else QUrl.fromLocalFile(
                    str((view.session.path.parent / str(target)).resolve()))
                QDesktopServices.openUrl(url)

    def _batch(self) -> None:
        import batch
        from PySide6.QtWidgets import QProgressDialog
        dialog = BatchDialog(self._last_dir(), self)
        view = self.view()
        if view is not None and view.session.path not in self._temp_files:
            dialog.add_files([str(view.session.path)])
        if not dialog.exec():
            return
        paths, options = dialog.paths(), dialog.options()
        options.author = self.user_name
        progress = QProgressDialog("Processing…", "Stop", 0, len(paths), self)
        progress.setWindowTitle("Batch process")
        progress.setMinimumDuration(0)

        def step(index: int, name: str) -> bool:
            progress.setValue(index)
            progress.setLabelText(f"Processing {name} ({index + 1} of {len(paths)})")
            QApplication.processEvents()
            return not progress.wasCanceled()

        results = batch.run_batch(paths, dialog.folder.text().strip(), options, step)
        progress.setValue(len(paths))
        failed = [result for result in results if not result.ok]
        lines = [f"{'✔' if r.ok else '✘'} {r.source.name}: {r.message}" for r in results]
        summary = (f"Processed {len(results) - len(failed)} of {len(paths)} file(s) into\n"
                   f"{dialog.folder.text().strip()}\n\n" + "\n".join(lines[:30]))
        (QMessageBox.warning if failed else QMessageBox.information)(
            self, "Batch process", summary)

    def _auto_link(self) -> None:
        view = self.view()
        if view is None:
            return
        ids = links.sheet_ids(view.session.doc)
        if not ids:
            QMessageBox.information(
                self, "Auto-link sheet numbers",
                "No sheet numbers were found. Name bookmarks or page labels with sheet "
                "numbers such as \"A-101 Floor Plan\" first.")
            return
        listed = ", ".join(sorted(ids)[:12]) + ("…" if len(ids) > 12 else "")
        if QMessageBox.question(self, "Auto-link sheet numbers",
                                f"Link every mention of these {len(ids)} sheets to their "
                                f"pages?\n\n{listed}") != QMessageBox.StandardButton.Yes:
            return
        added = self._apply(view, "Auto-Link Sheets",
                            lambda doc: links.auto_link_sheets(doc, ids))
        self.statusBar().showMessage(f"Added {added or 0} link(s)", 5000)

    def _remove_page_links(self) -> None:
        view = self.view()
        if view is None:
            return
        page = view.canvas.current_page
        removed = self._apply(view, "Remove Links", lambda doc: links.remove_links(doc[page]))
        self.statusBar().showMessage(f"Removed {removed or 0} link(s) from page {page + 1}", 4000)

    def _summary_report(self) -> None:
        view = self.view()
        if view is None:
            return
        rows = markups.list_markups(view.session.doc)
        if not rows:
            QMessageBox.information(self, "Markup summary", "This document has no markups.")
            return
        output, _ = QFileDialog.getSaveFileName(
            self, "Save markup summary", str(view.session.path.with_name(
                view.session.path.stem + "_markup_summary.pdf")), "PDF files (*.pdf)")
        if not output:
            return
        try:
            report.write_summary(view.session.doc, output, view.session.path.name, rows)
        except Exception as exc:
            QMessageBox.warning(self, "Markup summary", str(exc))
            return
        self.open_file(output)

    def _autosave(self) -> None:
        if self.recovery is None:
            return
        for view in self.views():
            session = view.session
            if not session.dirty:
                self.recovery.forget(session)
                self._autosaved.pop(id(session), None)
            elif self._autosaved.get(id(session)) != session.revision:
                try:
                    self.recovery.save(session)
                    self._autosaved[id(session)] = session.revision
                except Exception:
                    pass  # autosave must never interrupt work
        self.recovery.heartbeat()

    def _offer_recovery(self) -> None:
        pending = self.recovery.pending() if self.recovery else []
        if not pending:
            return
        names = "\n".join(f"• {Path(item['original']).name or 'Untitled'}" for item in pending[:10])
        answer = QMessageBox.question(
            self, "Recover unsaved work",
            f"Northstar PDF closed unexpectedly. Recover {len(pending)} unsaved document(s)?"
            f"\n\n{names}\n\nRecovered copies open as new tabs; use Save As to keep them.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.Discard:
            for item in pending:
                self.recovery.discard(item["id"])
            return
        if answer != QMessageBox.StandardButton.Yes:
            return
        for item in pending:
            original = Path(item["original"])
            handle, name = tempfile.mkstemp(prefix=f"recovered-{original.stem}-", suffix=".pdf")
            os.close(handle)
            path = self.recovery.claim(item["id"], name).resolve()
            self._temp_files.append(path)
            self._recovered.add(path)
            self.open_file(str(path), f"Recovered: {original.name}")

    # -- misc ------------------------------------------------------------------------------
    def _preferences(self) -> None:
        name, accepted = QInputDialog.getText(self, "Preferences",
                                              "Your name (shown as markup author):",
                                              text=self.user_name)
        if accepted and name.strip():
            self.user_name = name.strip()
            self.settings.setValue("userName", self.user_name)

    def _show_shortcuts(self) -> None:
        lines = [f"{tool_label(kind)}: {key}" for kind, key in SHORTCUTS.items()]
        lines += ["", "Esc: back to Select / cancel", "Space+drag or middle-drag: pan",
                  "Wheel: zoom · Shift+wheel: scroll sideways · Ctrl+wheel: scroll",
                  "Shift while drawing: snap to 45°", "Enter / double-click: finish polygon",
                  "Arrow keys: nudge selected markups (Shift = 10 pt)",
                  "Delete: delete selected markups", "Ctrl+D: duplicate", "F2: edit text"]
        QMessageBox.information(self, "Keyboard shortcuts", "\n".join(lines))

    def _about(self) -> None:
        QMessageBox.about(self, f"About {APP_NAME}", (
            f"<b>{APP_NAME} {APP_VERSION}</b><br>Offline PDF markup, measurement, and review."
            "<br><br>Copyright © 2026 Northstar PDF contributors.<br>"
            "This program is free software: you can redistribute it and/or modify it under the"
            " terms of the GNU Affero General Public License, version 3 or (at your option) any"
            " later version. It comes with ABSOLUTELY NO WARRANTY; see Help → License."
            f'<br><br>Source code: <a href="{SOURCE_URL}">{SOURCE_URL}</a>'
            "<br><br>Built with PySide6 (Qt, LGPLv3) and PyMuPDF (AGPL-3.0-or-later)."))

    def _show_license(self) -> None:
        text = read_bundled_text("LICENSE") or (
            "The license file was not found. Northstar PDF is licensed under the GNU Affero "
            f"General Public License v3.0 or later. See {SOURCE_URL}.")
        notices = read_bundled_text("THIRD_PARTY_NOTICES.md")
        dialog = QDialog(self)
        dialog.setWindowTitle("License")
        dialog.resize(720, 620)
        viewer = QPlainTextEdit(dialog)
        viewer.setReadOnly(True)
        viewer.setPlainText(text + ("\n\n" + "=" * 72 + "\n\n" + notices if notices else ""))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        layout = QVBoxLayout(dialog)
        layout.addWidget(viewer)
        layout.addWidget(buttons)
        dialog.exec()


def read_bundled_text(name: str) -> str:
    """Read a file shipped next to the program (or inside the one-file build)."""
    bases = [Path(getattr(sys, "_MEIPASS", "")), Path(sys.executable).parent,
             Path(__file__).resolve().parent.parent]
    for base in bases:
        path = base / name
        if str(base) and path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    return ""


def parse_page_ranges(text: str, count: int) -> list[int]:
    """'1-3, 5' -> [0, 1, 2, 4] (zero-based, order kept, 1 <= n <= count)."""
    pages = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, _sep, end = part.partition("-")
            first, last = int(start), int(end)
            step = 1 if last >= first else -1
            pages.extend(range(first, last + step, step))
        else:
            pages.append(int(part))
    if not pages:
        raise ValueError("Enter at least one page number.")
    if any(page < 1 or page > count for page in pages):
        raise ValueError(f"Page numbers must be between 1 and {count}.")
    return [page - 1 for page in pages]


# -- self-test ----------------------------------------------------------------------

def _report_self_test(checks: list[tuple[str, bool]]) -> int:
    """Print self-test results and copy them to NORTHSTAR_SELF_TEST_REPORT if set.

    The packaged build is a windowed executable without a console, so CI reads
    the report file instead of stdout.
    """
    lines = [f"{name}: {'PASS' if ok else 'FAIL'}" for name, ok in checks]
    report = os.environ.get("NORTHSTAR_SELF_TEST_REPORT")
    if report:
        Path(report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    for line in lines:
        print(line)
    return 0 if all(ok for _name, ok in checks) else 1


def _non_blocking_dialogs(messages: list[str]) -> None:
    """Record message boxes instead of showing them, so CI can never hang on one."""
    def recorder(kind: str):
        def show(_parent, title, text, *_args, **_kwargs):
            messages.append(f"{kind} - {title}: {text}")
            return QMessageBox.StandardButton.Cancel
        return staticmethod(show)

    for kind in ("warning", "critical", "information", "question"):
        setattr(QMessageBox, kind, recorder(kind))


def _start_watchdog(seconds: float) -> None:
    """Fail (and still write the report) if the self-test stops responding."""
    import threading

    def expire() -> None:
        _report_self_test([(f"Self-test finished within {seconds:.0f} s", False)])
        os._exit(2)

    timer = threading.Timer(seconds, expire)
    timer.daemon = True
    timer.start()


def self_test() -> int:
    """Drive the real window with simulated mouse input; used by the build workflow."""
    from PySide6.QtTest import QTest

    _start_watchdog(180)
    dialogs: list[str] = []
    _non_blocking_dialogs(dialogs)
    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("Northstar")
    app.setApplicationName("NorthstarPDF-SelfTest")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      tempfile.mkdtemp(prefix="northstar-settings-"))
    workdir = Path(tempfile.mkdtemp(prefix="northstar-self-test-"))
    source = workdir / "plan.pdf"
    doc = fitz.open()
    for number in range(3):
        page = doc.new_page(width=792, height=612)
        page.insert_text((72, 90), f"Sheet A-10{number} Floor plan", fontsize=16)
        page.draw_rect(fitz.Rect(100, 150, 500, 450), color=(0, 0, 0), width=2)
    doc.save(source)
    doc.close()
    checks: list[tuple[str, bool]] = []
    window = MainWindow([str(source)], recovery=False)
    try:
        window.resize(1300, 900)
        window.show()
        QTest.qWait(150)
        view = window.view()
        canvas = view.canvas
        session = view.session
        checks.append(("Open document in canvas", view is not None and len(canvas.pages) == 3))

        def page_to_view(page: int, point) -> QPointF:
            item = canvas.page_item(page)
            return QPointF(canvas.mapFromScene(item.pos() + canvas.to_visual(page, point)))

        def drag(start, end, page: int = 0) -> None:
            a = page_to_view(page, start).toPoint()
            b = page_to_view(page, end).toPoint()
            QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, a)
            for step in range(1, 6):
                point = a + (b - a) * step / 5
                QTest.mouseMove(canvas.viewport(), point)
            QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, b)
            QTest.qWait(30)

        canvas.fit("page")
        QTest.qWait(60)
        window.set_tool("rectangle")
        drag((150, 200), (300, 300))
        rows = markups.list_markups(session.doc)
        rect_ok = len(rows) == 1 and rows[0]["kind"] == "rectangle"
        bounds = markups.markup_bounds(session.doc[0], rows[0]["xref"]) if rows else fitz.Rect()
        checks.append(("Draw rectangle with the mouse",
                       rect_ok and abs(bounds.x0 - 150) < 3 and abs(bounds.y1 - 300) < 3))
        checks.append(("Tool returns to Select and selects the markup",
                       window.tool == "select" and len(canvas.selection) == 1))

        drag((220, 250), (260, 290))
        moved = markups.markup_bounds(session.doc[0], rows[0]["xref"]) if rows else fitz.Rect()
        checks.append(("Drag a selected markup to move it",
                       abs(moved.x0 - 190) < 3 and abs(moved.y0 - 240) < 3))
        window.undo()
        restored = markups.markup_bounds(session.doc[0], rows[0]["xref"]) if rows else fitz.Rect()
        checks.append(("Undo the move", abs(restored.x0 - 150) < 3))

        style = Style.from_dict(markups.read_style(session.doc[0], rows[0]["xref"]).to_dict())
        style.stroke = "#00aa00"
        window._style_edited(style)
        checks.append(("Change colour from the Properties panel",
                       markups.read_style(session.doc[0], rows[0]["xref"]).stroke == "#00aa00"))

        window.set_tool("cloud")
        for point in ((520, 160), (700, 160), (700, 300)):
            p = page_to_view(0, point).toPoint()
            QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p)
        QTest.mouseDClick(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                          page_to_view(0, (520, 300)).toPoint())
        QTest.qWait(30)
        kinds = [row["kind"] for row in markups.list_markups(session.doc)]
        checks.append(("Click points to draw a revision cloud", "cloud" in kinds))

        canvas.set_selection([])
        window.set_tool("highlight")
        drag((68, 70), (330, 98))
        highlight = [r for r in markups.list_markups(session.doc) if r["kind"] == "highlight"]
        checks.append(("Highlight snaps to text", bool(highlight)))

        window._apply(view, "Scale", lambda d: markups.set_page_scale(
            d[0], markups.common_scale('1/4" = 1\'-0"')))
        window.set_tool("length")
        drag((100, 500), (280, 500))
        lengths = [r for r in markups.list_markups(session.doc) if r["kind"] == "length"]
        # 180 pt at 1/4" = 1'-0" is 10 ft (18 pt per foot); allow two screen
        # pixels for whole-pixel mouse positions.
        tolerance = 2 / canvas.zoom / 18
        checks.append(("Scaled length measurement", bool(lengths)
                       and abs(lengths[0]["value"] - 10) < tolerance
                       and lengths[0]["measurement"] == markups.format_length(
                           lengths[0]["value"], "ft-in")))
        # The page has a rectangle at (100, 150)-(500, 450): a click 3 pt away from
        # its corner should land exactly on it.
        window.snap_action.setChecked(True)
        window.set_tool("length")
        drag((103, 152), (300, 448))
        snapped = [r for r in markups.list_markups(session.doc) if r["kind"] == "length"][-1]
        start, end = markups.markup_points(session.doc[0], snapped["xref"])
        checks.append(("Snap to drawing corners", abs(start - fitz.Point(100, 150)) < 0.01
                       and abs(end.y - 450) < 0.01))
        window._refresh_panels()
        checks.append(("Markups List shows every markup",
                       window.markups_list.table.rowCount() == len(markups.list_markups(session.doc))))

        window.set_tool("select")
        canvas.go_to_page(0)
        window._apply(view, "Link", lambda d: links.add_page_link(d[0], fitz.Rect(600, 500, 700, 560), 2))
        QTest.qWait(30)
        target = page_to_view(0, (650, 530)).toPoint()
        QTest.mouseMove(canvas.viewport(), target)
        QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, target)
        QTest.qWait(30)
        checks.append(("Click a link to jump to its page", canvas.current_page == 2))
        canvas.go_to_page(0)
        QTest.qWait(30)

        window.search.query.setText("Floor plan")
        window._search("Floor plan", False, False)
        checks.append(("Search all pages", len(view.search_hits) == 3))

        window._apply(view, "Rotate", lambda d: ops.rotate_page(d, 1))
        QTest.qWait(60)
        window.set_tool("ellipse")
        canvas.go_to_page(1)
        QTest.qWait(60)
        drag((150, 200), (250, 260), page=1)
        ellipses = [r for r in markups.list_markups(session.doc) if r["kind"] == "ellipse"]
        e_bounds = markups.markup_bounds(session.doc[1], ellipses[0]["xref"]) if ellipses \
            else fitz.Rect()
        checks.append(("Draw on a rotated page in the right place",
                       bool(ellipses) and abs(e_bounds.x0 - 150) < 3 and abs(e_bounds.y0 - 200) < 3))

        saved = window._save_view(view)
        with fitz.open(source) as reopened:
            persisted = sum(1 for page in reopened for _a in page.annots())
        checks.append(("Save keeps live markups", saved and persisted == len(
            markups.list_markups(session.doc))))
        canvas.zoom_by(2.0)
        QTest.qWait(80)
        canvas._render_visible()
        checks.append(("Sharp render at high zoom", any(item.detail is not None or
                                                        item.base_scale >= canvas.zoom * 0.99
                                                        for item in canvas.pages if item.base)))
        checks.append(("License text is bundled",
                       "GNU AFFERO GENERAL PUBLIC LICENSE" in read_bundled_text("LICENSE")))
        checks.append(("No unexpected message boxes", not dialogs))
        for message in dialogs:
            print("Message box:", message)
    except Exception as exc:  # report rather than crash so CI shows which step broke
        import traceback
        traceback.print_exc()
        checks.append((f"Self-test raised {type(exc).__name__}: {exc}", False))
    finally:
        for open_view in window.views():
            open_view.canvas.shutdown()
        window.tabs.clear()
        for open_view in list(window.findChildren(DocumentView)):
            open_view.session.close()
        window.close()
        app.processEvents()
    return _report_self_test(checks)


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setOrganizationName("Northstar")
    app.setApplicationName("NorthstarPDF")
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    files = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    window = MainWindow(files)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
