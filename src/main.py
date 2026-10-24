"""Northstar PDF: a local-only Windows PDF viewer built with Qt PDF/PDFium."""
from __future__ import annotations
import os
import sys
from pathlib import Path

# Install the offline guard before loading any UI or PDF libraries.
from offline_guard import install_offline_guard
install_offline_guard()

from pdf_operations import (add_pdf_annotation, add_rich_text_box, extract_pages,
                            extract_text_file, merge_pdfs, read_form_fields,
                            split_pdf, protect_pdf, replace_existing_text)
from pypdf import PdfReader
from edit_session import EditSession

from PySide6.QtCore import Qt, QPoint, QPointF, QRect, QSettings, QTimer, Signal
from PySide6.QtGui import (QAction, QActionGroup, QKeySequence, QCloseEvent, QFont, QKeyEvent,
                           QTextBlockFormat, QTextCharFormat, QTextCursor)
from PySide6.QtPdf import QPdfDocument, QPdfBookmarkModel, QPdfSearchModel
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDockWidget, QFileDialog, QInputDialog, QLabel,
    QLineEdit, QListView, QMainWindow, QMessageBox, QSpinBox, QTabWidget,
    QToolBar, QTreeView, QWidget, QHBoxLayout, QVBoxLayout, QTabBar,
    QToolButton, QScrollArea, QFrame, QRubberBand, QDoubleSpinBox,
    QGridLayout, QTextEdit,
)

APP_NAME = "Northstar PDF"


class PlacementTextEdit(QTextEdit):
    """Text editor with explicit Place-on-Enter and newline-on-Ctrl+Enter behavior."""

    placeRequested = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() == Qt.KeyboardModifier.NoModifier:
                self.placeRequested.emit()
                event.accept()
                return
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.textCursor().insertBlock()
                event.accept()
                return
        super().keyPressEvent(event)


class DragHandle(QLabel):
    """Small grip that moves a live text box without interrupting typing."""

    def __init__(self, editor: "TextPlacementEditor") -> None:
        super().__init__("⠿  Drag to move", editor)
        self._origin = None
        self._initial_pos = None
        self.setFixedHeight(20)
        self.setAccessibleName("Move text box")
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setStyleSheet("background:#eaf2fc;color:#245c9b;border:0;font-size:11px;padding-left:4px;")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.globalPosition().toPoint()
            self._initial_pos = self.parentWidget().pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._origin is not None and self._initial_pos is not None:
            delta = event.globalPosition().toPoint() - self._origin
            editor = self.parentWidget()
            bounds = editor.parentWidget().rect()
            x = max(0, min(bounds.width() - editor.width(),
                           self._initial_pos.x() + delta.x()))
            y = max(0, min(bounds.height() - editor.height(),
                           self._initial_pos.y() + delta.y()))
            editor.move(x, y)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._origin = None
        self._initial_pos = None
        event.accept()


class ResizeCorner(QLabel):
    """Drag handle that lets users resize the live text box in the page view."""

    def __init__(self, editor: "TextPlacementEditor") -> None:
        super().__init__("◢", editor)
        self._origin = None
        self._initial_size = None
        self.setFixedSize(18, 18)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setStyleSheet("background:#2878d0;color:white;border:0;font-size:13px;")

    def mousePressEvent(self, event) -> None:
        self._origin = event.globalPosition().toPoint()
        self._initial_size = self.parentWidget().size()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._origin is not None and self._initial_size is not None:
            delta = event.globalPosition().toPoint() - self._origin
            editor = self.parentWidget()
            editor.resize(max(110, self._initial_size.width() + delta.x()),
                          max(54, self._initial_size.height() + delta.y()))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._origin = None
        self._initial_size = None
        event.accept()


class TextPlacementEditor(QFrame):
    """In-document live text preview with a draggable resize corner."""

    def __init__(self, parent: QWidget, geometry: QRect) -> None:
        super().__init__(parent)
        self.setObjectName("TextPlacementEditor")
        self.setGeometry(geometry)
        self.setMinimumSize(110, 54)
        self.setFrameShape(QFrame.Shape.Box)
        self.setLineWidth(2)
        self.setStyleSheet(
            "QFrame#TextPlacementEditor { background:#ffffff; border:2px solid #2878d0; }"
            "QTextEdit { background:#ffffff; color:#111111; border:0; padding:2px; }"
        )
        self.move_handle = DragHandle(self)
        self.text_edit = PlacementTextEdit(self)
        self.text_edit.setAcceptRichText(False)
        self.text_edit.setPlaceholderText("Type your text here…")
        self.text_edit.setAccessibleName("Text to place in PDF")
        layout = QGridLayout(self)
        layout.setContentsMargins(5, 4, 5, 5)
        layout.setSpacing(0)
        layout.addWidget(self.move_handle, 0, 0)
        layout.addWidget(self.text_edit, 1, 0)
        self.resize_corner = ResizeCorner(self)
        layout.addWidget(self.resize_corner, 1, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        self._add_shortcuts()

    def _add_shortcuts(self) -> None:
        self.cancel_action = QAction("Cancel text placement", self)
        # Escape is handled by the main window so it works from the page,
        # text editor, formatting bar, and tool panel.
        self.commit_action = QAction("Place text", self)


class InteractivePdfView(QPdfView):
    """PDF view with selection, markup gestures, zoom, and middle-button panning."""

    annotationGesture = Signal(object)
    selectionGesture = Signal(object)
    escapePressed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.annotation_tool = "select"
        self._pan_point = None
        self._pan_h = 0
        self._pan_v = 0
        self._pan_page = 0
        self._pan_location = QPointF()
        self._pan_zoom = 1.0
        self._draw_start = None
        self._draw_points: list[tuple[float, float]] = []
        self._draw_page = 0
        self._draw_location = QPointF()
        self._draw_zoom = 1.0
        self._select_start = None
        self._select_page = 0
        self._select_location = QPointF()
        self._select_zoom = 1.0
        self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._selection_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._selection_band.setStyleSheet(
            "QRubberBand { border:2px solid #1683d8; background:transparent; }"
        )

    def set_annotation_tool(self, tool: str | None) -> None:
        self.annotation_tool = tool or "select"
        if self.annotation_tool == "select":
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self._rubber_band.hide()
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def show_selected_object(self, rect: QRect | None) -> None:
        if rect is None:
            self._selection_band.hide()
            return
        self._selection_band.setGeometry(rect)
        self._selection_band.show()
        self._selection_band.raise_()

    def wheelEvent(self, event) -> None:
        """Use the wheel to zoom around the pointer; middle drag remains the pan gesture."""
        delta = event.angleDelta().y()
        nav = self.pageNavigator()
        page = nav.currentPage()
        if not delta or page < 0 or self.document() is None:
            super().wheelEvent(event)
            return
        old_zoom = float(nav.currentZoom())
        if old_zoom <= 0:
            old_zoom = max(float(self.zoomFactor()), 0.1)
        new_zoom = max(0.1, min(8.0, old_zoom * (1.18 ** (delta / 120.0))))
        if abs(new_zoom - old_zoom) < 0.0001:
            event.accept()
            return
        location = nav.currentLocation()
        pointer = event.position()
        new_location = QPointF(
            location.x() + pointer.x() / old_zoom - pointer.x() / new_zoom,
            location.y() + pointer.y() / old_zoom - pointer.y() / new_zoom,
        )
        self.setZoomMode(QPdfView.ZoomMode.Custom)
        self.setZoomFactor(new_zoom)
        nav.jump(page, new_location, new_zoom)
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_point = event.position().toPoint()
            self._pan_h = self.horizontalScrollBar().value()
            self._pan_v = self.verticalScrollBar().value()
            navigator = self.pageNavigator()
            self._pan_page = max(0, navigator.currentPage())
            self._pan_location = QPointF(navigator.currentLocation())
            self._pan_zoom = max(float(navigator.currentZoom()), 0.1)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.annotation_tool == "select":
            point = event.position().toPoint()
            nav = self.pageNavigator()
            self._select_start = (float(point.x()), float(point.y()))
            self._select_page = max(0, nav.currentPage())
            self._select_location = QPointF(nav.currentLocation())
            self._select_zoom = max(float(nav.currentZoom()), 0.1)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.annotation_tool != "select":
            point = event.position().toPoint()
            nav = self.pageNavigator()
            self._draw_start = (float(point.x()), float(point.y()))
            self._draw_points = [self._draw_start]
            self._draw_page = max(0, nav.currentPage())
            self._draw_location = QPointF(nav.currentLocation())
            self._draw_zoom = max(float(nav.currentZoom()), 0.1)
            self._rubber_band.setGeometry(point.x(), point.y(), 1, 1)
            if self.annotation_tool not in ("note", "text"):
                self._rubber_band.show()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        point = event.position().toPoint()
        if self._pan_point is not None:
            delta = point - self._pan_point
            horizontal = self.horizontalScrollBar()
            vertical = self.verticalScrollBar()
            horizontal.setValue(self._pan_h - delta.x())
            vertical.setValue(self._pan_v - delta.y())
            pan_x = horizontal.value() == self._pan_h
            pan_y = vertical.value() == self._pan_v
            if pan_x or pan_y:
                document = self.document()
                page_size = document.pagePointSize(self._pan_page) if document else None
                if page_size is not None:
                    zoom = self._pan_zoom
                    margin_x = max(0.0, (self.viewport().width()
                                         - float(page_size.width()) * zoom) / (2.0 * zoom))
                    margin_y = max(0.0, (self.viewport().height()
                                         - float(page_size.height()) * zoom) / (2.0 * zoom))
                    x = self._pan_location.x() - (delta.x() / zoom if pan_x else 0.0)
                    y = self._pan_location.y() - (delta.y() / zoom if pan_y else 0.0)
                    x = max(-margin_x, min(margin_x, x))
                    y = max(-margin_y, min(margin_y, y))
                    self.setZoomMode(QPdfView.ZoomMode.Custom)
                    self.setZoomFactor(zoom)
                    self.pageNavigator().jump(self._pan_page, QPointF(x, y), zoom)
            event.accept()
            return
        if self._select_start is not None:
            left, top = self._select_start
            self._selection_band.setGeometry(
                int(min(left, point.x())), int(min(top, point.y())),
                max(1, int(abs(point.x() - left))), max(1, int(abs(point.y() - top))),
            )
            self._selection_band.show()
            event.accept()
            return
        if self._draw_start is not None:
            raw = (float(point.x()), float(point.y()))
            if self.annotation_tool == "pen":
                previous = self._draw_points[-1]
                if (raw[0] - previous[0]) ** 2 + (raw[1] - previous[1]) ** 2 >= 9:
                    self._draw_points.append(raw)
            left, top = self._draw_start
            self._rubber_band.setGeometry(
                int(min(left, raw[0])), int(min(top, raw[1])),
                max(1, int(abs(raw[0] - left))), max(1, int(abs(raw[1] - top))),
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_point is not None:
            self._pan_point = None
            self.setCursor(Qt.CursorShape.ArrowCursor if self.annotation_tool == "select"
                           else Qt.CursorShape.CrossCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._select_start is not None:
            end = event.position().toPoint()
            self.selectionGesture.emit({
                "page": self._select_page,
                "start": self._select_start,
                "end": (float(end.x()), float(end.y())),
                "location": QPointF(self._select_location),
                "zoom": self._select_zoom,
            })
            self._select_start = None
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._draw_start is not None:
            end = event.position().toPoint()
            raw_end = (float(end.x()), float(end.y()))
            if self.annotation_tool == "pen":
                self._draw_points.append(raw_end)
            self.annotationGesture.emit({
                "kind": self.annotation_tool,
                "page": self._draw_page,
                "start": self._draw_start,
                "end": raw_end,
                "points": list(self._draw_points),
                "location": QPointF(self._draw_location),
                "zoom": self._draw_zoom,
            })
            self._rubber_band.hide()
            self._draw_start = None
            self._draw_points = []
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.escapePressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class PdfTab(QWidget):
    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = str(Path(path).resolve())
        self.document = QPdfDocument(self)
        self.bookmarks = QPdfBookmarkModel(self)
        self.bookmarks.setDocument(self.document)
        self.search = QPdfSearchModel(self)
        self.search.setDocument(self.document)
        self.view = InteractivePdfView(self)
        self.view.setDocument(self.document)
        self.view.setSearchModel(self.search)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.view.setPageSpacing(14)
        self.error: str | None = None
        self.password: str | None = None
        self.document.passwordRequired.connect(self._request_password)
        result = self.document.load(self.path)
        if result != QPdfDocument.Error.None_:
            self.error = f"Could not open PDF (error: {result})."
        self.edit_session = EditSession(self.path)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

    @property
    def dirty(self) -> bool:
        return self.edit_session.dirty

    def _reload_working_copy(self, page: int) -> None:
        self.document.close()
        result = self.document.load(str(self.edit_session.working_path))
        if result != QPdfDocument.Error.None_:
            raise RuntimeError(f"Could not reload edited PDF (error: {result}).")
        QTimer.singleShot(0, lambda: self.view.pageNavigator().jump(
            min(page, max(0, self.document.pageCount() - 1)), QPointF(0, 0)
        ))

    def rotate_current_page(self) -> None:
        page = self.view.pageNavigator().currentPage()
        self.edit_session.rotate(page, password=self.password)
        self._reload_working_copy(page)

    def delete_current_page(self) -> None:
        page = self.view.pageNavigator().currentPage()
        self.edit_session.delete_page(page, password=self.password)
        self._reload_working_copy(min(page, self.document.pageCount() - 2))

    def reorder_pages(self, order: list[int]) -> None:
        self.edit_session.reorder(order, password=self.password)
        self._reload_working_copy(0)

    def crop_current_page(self, left: float, bottom: float, right: float, top: float) -> None:
        page = self.view.pageNavigator().currentPage()
        self.edit_session.crop(page, left, bottom, right, top, password=self.password)
        self._reload_working_copy(page)

    def insert_pages(self, source: str, before_page: int) -> None:
        page = self.view.pageNavigator().currentPage()
        self.edit_session.insert(source, before_page, password=self.password)
        self._reload_working_copy(page)

    def replace_current_page(self, source: str) -> None:
        page = self.view.pageNavigator().currentPage()
        self.edit_session.replace(source, page, password=self.password)
        self._reload_working_copy(page)

    def fill_fields(self, values: dict[str, str]) -> None:
        page = self.view.pageNavigator().currentPage()
        self.edit_session.fill_fields(values, password=self.password)
        self._reload_working_copy(page)

    def add_sticky_note(self, text: str) -> None:
        page = self.view.pageNavigator().currentPage()
        self.edit_session.add_sticky_note(page, text, password=self.password)
        self._reload_working_copy(page)

    def undo_edit(self) -> None:
        page = self.view.pageNavigator().currentPage()
        self.edit_session.undo()
        self._reload_working_copy(page)

    def redo_edit(self) -> None:
        page = self.view.pageNavigator().currentPage()
        self.edit_session.redo()
        self._reload_working_copy(page)

    def save_edits(self) -> None:
        if not self.dirty:
            return
        page = self.view.pageNavigator().currentPage()
        self.document.close()
        try:
            self.edit_session.save()
        except Exception:
            self.document.load(str(self.edit_session.working_path))
            raise
        self._reload_working_copy(page)

    def discard_edits(self) -> None:
        page = self.view.pageNavigator().currentPage()
        self.edit_session.discard()
        self._reload_working_copy(page)

    def cleanup(self) -> None:
        self.document.close()
        self.edit_session.cleanup()

    def _request_password(self) -> None:
        password, accepted = QInputDialog.getText(
            self, "Password required", f"Enter the password for {Path(self.path).name}:",
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            self.error = "Password entry was cancelled."
            return
        self.password = password
        self.document.setPassword(password)
        QTimer.singleShot(0, lambda: self.document.load(self.path))

class MainWindow(QMainWindow):
    def __init__(self, files: list[str] | None = None) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 860)
        self.settings = QSettings("Northstar", "NorthstarPDF")
        self.tool_actions: dict[str, QAction] = {}
        self._text_editor: TextPlacementEditor | None = None
        self._text_active_tab: PdfTab | None = None
        self._text_context: dict | None = None
        self._selected_text_box: tuple[PdfTab, int, int, tuple] | None = None
        self.tabs = QTabWidget(self)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.setCentralWidget(self.tabs)
        self._make_navigation_dock()
        self._make_search_dock()
        self._make_toolbar()
        self._make_tool_dock()
        self._make_menus()
        self._make_markup_toolbar()
        self.escape_action = QAction("Return to cursor", self)
        self.escape_action.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        self.escape_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.escape_action.triggered.connect(self._escape_to_pointer)
        self.addAction(self.escape_action)
        self._make_workspace_tabs()
        self.active_tool_indicator = QLabel("TOOL  SELECT", self)
        self.active_tool_indicator.setAccessibleName("Current tool")
        self.active_tool_indicator.setStyleSheet(
            "QLabel { background:#e8f1f9; color:#123b5d; border:1px solid #9db6c9;"
            " border-radius:3px; padding:4px 9px; font-weight:700; }"
        )
        self.statusBar().addPermanentWidget(self.active_tool_indicator)
        self._update_tool_indicator("select")
        self.tabs.currentChanged.connect(self._refresh_active_document)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.statusBar().showMessage("Ready · offline · scroll to zoom · middle-drag to pan")
        saved_geometry = self.settings.value("windowGeometry")
        saved_state = self.settings.value("windowState")
        if saved_geometry:
            self.restoreGeometry(saved_geometry)
        if saved_state:
            self.restoreState(saved_state)
        workspace = self.settings.value("workspace", "Read")
        workspace_index = next(
            (i for i in range(self.workspace_tabs.count())
             if self.workspace_tabs.tabText(i) == workspace), 0
        )
        self.workspace_tabs.setCurrentIndex(workspace_index)
        for path in files or []:
            self.open_file(path)
        if self.tabs.count() == 0:
            self._show_start_page()

    def _make_navigation_dock(self) -> None:
        self.navigation = QTabWidget(self)
        self.page_list = QListView(self.navigation)
        self.bookmark_tree = QTreeView(self.navigation)
        self.navigation.addTab(self.page_list, "Pages")
        self.navigation.addTab(self.bookmark_tree, "Bookmarks")
        self.nav_dock = QDockWidget("Navigation", self)
        self.nav_dock.setObjectName("NavigationDock")
        self.nav_dock.setWidget(self.navigation)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.nav_dock)
        self.page_list.clicked.connect(self._jump_to_page_index)
        self.bookmark_tree.clicked.connect(self._jump_to_bookmark)

    def _make_search_dock(self) -> None:
        self.search_results = QListView(self)
        self.search_dock = QDockWidget("Search results", self)
        self.search_dock.setObjectName("SearchDock")
        self.search_dock.setWidget(self.search_results)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.search_dock)
        self.search_results.clicked.connect(self._jump_to_search_result)

    def _make_toolbar(self) -> None:
        toolbar = QToolBar("Document controls", self)
        toolbar.setObjectName("DocumentControlsToolbar")
        toolbar.setMovable(True)
        self.addToolBar(toolbar)
        open_action = QAction("Open", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_dialog)
        toolbar.addAction(open_action)
        toolbar.addSeparator()
        toolbar.addWidget(QLabel("Page "))
        self.page_number = QSpinBox(self)
        self.page_number.setMinimum(1)
        self.page_number.setMaximum(1)
        self.page_number.setFixedWidth(72)
        self.page_number.valueChanged.connect(self._jump_to_page_number)
        toolbar.addWidget(self.page_number)
        self.page_total = QLabel("of 0", self)
        toolbar.addWidget(self.page_total)
        toolbar.addSeparator()
        self.mode = QComboBox(self)
        self.mode.addItem("Continuous", QPdfView.PageMode.MultiPage)
        self.mode.addItem("Single page", QPdfView.PageMode.SinglePage)
        self.mode.currentIndexChanged.connect(self._change_page_mode)
        toolbar.addWidget(self.mode)
        self.zoom = QComboBox(self)
        self.zoom.addItem("Fit width", QPdfView.ZoomMode.FitToWidth)
        self.zoom.addItem("Fit page", QPdfView.ZoomMode.FitInView)
        self.zoom.addItem("100%", QPdfView.ZoomMode.Custom)
        self.zoom.currentIndexChanged.connect(self._change_zoom)
        toolbar.addWidget(self.zoom)
        toolbar.addSeparator()
        self.search_box = QLineEdit(self)
        self.search_box.setPlaceholderText("Find text in this PDF")
        self.search_box.setMaximumWidth(250)
        self.search_box.returnPressed.connect(self._start_search)
        toolbar.addWidget(self.search_box)
        find_action = QAction("Find", self)
        self.find_action = find_action
        find_action.setShortcut(QKeySequence.StandardKey.Find)
        find_action.triggered.connect(self._start_search)
        toolbar.addAction(find_action)
        self.previous_result = QAction("Previous", self)
        self.previous_result.triggered.connect(lambda: self._step_search(-1))
        toolbar.addAction(self.previous_result)
        self.next_result = QAction("Next", self)
        self.next_result.triggered.connect(lambda: self._step_search(1))
        toolbar.addAction(self.next_result)

        # Keep formatting controls visible so text can be styled while it is placed.
        self.text_toolbar = QToolBar("Text formatting", self)
        self.text_toolbar.setObjectName("TextFormattingToolbar")
        self.text_toolbar.setMovable(True)
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.text_toolbar)
        self.text_toolbar.addWidget(QLabel("Size", self.text_toolbar))
        self.text_size = QSpinBox(self.text_toolbar)
        self.text_size.setRange(4, 144)
        self.text_size.setValue(12)
        self.text_size.setSuffix(" pt")
        self.text_size.setToolTip("Text size in points")
        self.text_toolbar.addWidget(self.text_size)
        self.text_bold = QToolButton(self.text_toolbar)
        self.text_bold.setText("B")
        self.text_bold.setToolTip("Bold")
        self.text_bold.setCheckable(True)
        self.text_bold.setStyleSheet("font-weight:700")
        self.text_toolbar.addWidget(self.text_bold)
        self.text_italic = QToolButton(self.text_toolbar)
        self.text_italic.setText("I")
        self.text_italic.setToolTip("Italic")
        self.text_italic.setCheckable(True)
        self.text_italic.setStyleSheet("font-style:italic")
        self.text_toolbar.addWidget(self.text_italic)
        self.text_underline = QToolButton(self.text_toolbar)
        self.text_underline.setText("U")
        self.text_underline.setToolTip("Underline")
        self.text_underline.setCheckable(True)
        self.text_underline.setStyleSheet("text-decoration:underline")
        self.text_toolbar.addWidget(self.text_underline)
        self.text_toolbar.addSeparator()
        self.text_toolbar.addWidget(QLabel("Letter spacing", self.text_toolbar))
        self.text_letter_spacing = QDoubleSpinBox(self.text_toolbar)
        self.text_letter_spacing.setRange(0, 20)
        self.text_letter_spacing.setDecimals(1)
        self.text_letter_spacing.setSingleStep(0.5)
        self.text_letter_spacing.setSuffix(" pt")
        self.text_letter_spacing.setToolTip("Spacing between characters")
        self.text_toolbar.addWidget(self.text_letter_spacing)
        self.text_toolbar.addWidget(QLabel("Line spacing", self.text_toolbar))
        self.text_line_spacing = QDoubleSpinBox(self.text_toolbar)
        self.text_line_spacing.setRange(0.5, 4)
        self.text_line_spacing.setDecimals(1)
        self.text_line_spacing.setSingleStep(0.1)
        self.text_line_spacing.setValue(1.0)
        self.text_line_spacing.setSuffix("×")
        self.text_line_spacing.setToolTip("Line spacing multiplier")
        self.text_toolbar.addWidget(self.text_line_spacing)
        self.text_toolbar.addSeparator()
        self.text_alignment = QComboBox(self.text_toolbar)
        self.text_alignment.addItem("Left", "left")
        self.text_alignment.addItem("Centered", "center")
        self.text_alignment.addItem("Right", "right")
        self.text_alignment.addItem("Justified", "justify")
        self.text_alignment.setToolTip("Paragraph alignment")
        self.text_toolbar.addWidget(self.text_alignment)
        self.text_toolbar.addSeparator()
        self.place_text_action = QAction("Place", self)
        self.place_text_action.triggered.connect(self._commit_text_box)
        self.text_toolbar.addAction(self.place_text_action)
        self.cancel_text_action = QAction("Cancel", self)
        self.cancel_text_action.triggered.connect(self._cancel_text_box)
        self.text_toolbar.addAction(self.cancel_text_action)
        for control in (self.text_size, self.text_bold, self.text_italic,
                        self.text_underline, self.text_letter_spacing,
                        self.text_line_spacing, self.text_alignment):
            if isinstance(control, QSpinBox) or isinstance(control, QDoubleSpinBox):
                control.valueChanged.connect(self._apply_text_style)
            elif isinstance(control, QToolButton):
                control.toggled.connect(self._apply_text_style)
            else:
                control.currentIndexChanged.connect(self._apply_text_style)
        self.text_toolbar.setEnabled(False)

    def _make_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        open_action = file_menu.addAction("&Open…")
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.open_dialog)
        save_action = file_menu.addAction("&Save")
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self._save_active)
        self.recent_menu = file_menu.addMenu("Open &Recent")
        self._refresh_recent_menu()
        file_menu.addSeparator()
        exit_action = file_menu.addAction("E&xit")
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(QApplication.quit)
        edit_menu = self.menuBar().addMenu("&Edit")
        self.undo_action = edit_menu.addAction("&Undo")
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_action.triggered.connect(self._undo_active)
        self.redo_action = edit_menu.addAction("&Redo")
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self.redo_action.triggered.connect(self._redo_active)
        page_menu = self.menuBar().addMenu("&Pages")
        rotate_action = page_menu.addAction("Rotate current page clockwise")
        rotate_action.triggered.connect(self._rotate_current_page)
        delete_action = page_menu.addAction("Delete current page…")
        delete_action.triggered.connect(self._delete_current_page)
        note_action = page_menu.addAction("Add sticky note…")
        note_action.triggered.connect(self._add_sticky_note)
        extract_action = page_menu.addAction("Extract current page…")
        extract_action.triggered.connect(self._extract_current_page)
        reorder_action = page_menu.addAction("Reorder pages…")
        reorder_action.triggered.connect(self._reorder_pages)
        crop_action = page_menu.addAction("Crop current page…")
        crop_action.triggered.connect(self._crop_current_page)
        insert_action = page_menu.addAction("Insert pages from PDF…")
        insert_action.triggered.connect(self._insert_pages)
        replace_action = page_menu.addAction("Replace current page…")
        replace_action.triggered.connect(self._replace_current_page)
        split_action = page_menu.addAction("Split into one-page PDFs…")
        split_action.triggered.connect(self._split_pdf)
        merge_action = page_menu.addAction("Merge PDFs…")
        merge_action.triggered.connect(self._merge_documents)
        tools_menu = self.menuBar().addMenu("&Tools")
        fill_action = tools_menu.addAction("Fill form fields…")
        fill_action.triggered.connect(self._fill_form_fields)
        replace_text_action = tools_menu.addAction("Edit existing text…")
        replace_text_action.triggered.connect(self._edit_existing_text)
        export_text_action = tools_menu.addAction("Export text…")
        export_text_action.triggered.connect(self._export_text)
        comment_menu = self.menuBar().addMenu("&Comment")
        self.annotation_action_group = QActionGroup(self)
        self.annotation_action_group.setExclusive(True)
        annotation_actions = {}
        select_action = comment_menu.addAction("Select tool")
        select_action.setCheckable(True)
        select_action.setChecked(True)
        select_action.triggered.connect(lambda: self._activate_annotation_tool("select"))
        self.annotation_action_group.addAction(select_action)
        annotation_actions["Select"] = select_action
        self.annotation_actions_by_kind = {"select": select_action}
        for label, kind in (
            ("Place text box", "text"), ("Place note", "note"),
            ("Highlight region", "highlight"), ("Underline region", "underline"),
            ("Strikethrough region", "strikeout"), ("Freehand pen", "pen"),
            ("Line", "line"), ("Arrow", "arrow"),
            ("Rectangle", "rectangle"), ("Ellipse", "ellipse"),
        ):
            action = comment_menu.addAction(label)
            action.setCheckable(True)
            self.annotation_action_group.addAction(action)
            self.annotation_actions_by_kind[kind] = action
            action.triggered.connect(
                lambda checked=False, selected=kind: self._activate_annotation_tool(selected)
            )
            annotation_actions[label] = action
        protect_action = tools_menu.addAction("Password-protect a copy…")
        protect_action.triggered.connect(self._protect_copy)
        view_menu = self.menuBar().addMenu("&View")
        fullscreen = view_menu.addAction("Full screen")
        fullscreen.setShortcut(QKeySequence("F11"))
        fullscreen.triggered.connect(self._toggle_full_screen)
        dark = view_menu.addAction("Dark theme")
        dark.setCheckable(True)
        dark.setChecked(bool(self.settings.value("darkTheme", False, type=bool)))
        dark.toggled.connect(self._set_dark_theme)
        view_menu.addAction(self.tool_dock.toggleViewAction())
        view_menu.addAction(self.nav_dock.toggleViewAction())
        view_menu.addAction(self.search_dock.toggleViewAction())
        if dark.isChecked():
            self._set_dark_theme(True)

        # Shared action registry feeds the floating task tools panel. Each button
        # invokes the same QAction as its menu/toolbar counterpart.
        self.annotation_actions = annotation_actions
        self.tool_actions = {
            "Open": open_action, "Save": save_action,
            "Select": select_action,
            "Undo": self.undo_action, "Redo": self.redo_action,
            "Rotate page": rotate_action, "Delete page": delete_action,
            "Sticky note": note_action, "Extract page": extract_action,
            "Reorder pages": reorder_action, "Crop page": crop_action,
            "Insert pages": insert_action, "Replace page": replace_action,
            "Split PDF": split_action, "Merge PDFs": merge_action,
            "Fill form": fill_action, "Edit existing text": replace_text_action,
            "Export text": export_text_action,
            "Protect copy": protect_action,
            **annotation_actions,
            "Find text": self.find_action, "Previous match": self.previous_result,
            "Next match": self.next_result, "Full screen": fullscreen,
        }
        dark_action = dark
        self.tool_actions["Dark theme"] = dark_action

    def _make_workspace_tabs(self) -> None:
        """Task presets inspired by familiar Acrobat workspaces, with original UI."""
        bar = QToolBar("Workspaces", self)
        bar.setObjectName("WorkspaceTabsToolbar")
        bar.setMovable(False)
        bar.setStyleSheet(
            "QToolBar#WorkspaceTabsToolbar { background:#dfe8ef; border-bottom:1px solid #aab9c4; }"
            "QTabBar::tab { padding:8px 18px; color:#263744; }"
            "QTabBar::tab:selected { background:white; color:#1264a3; border-bottom:3px solid #1683d8; }"
        )
        self.workspace_tabs = QTabBar(bar)
        self.workspace_tabs.setObjectName("WorkspaceTabs")
        self.workspace_tabs.setShape(QTabBar.Shape.RoundedNorth)
        self.workspace_tabs.setExpanding(False)
        self.workspace_tabs.setDrawBase(False)
        for name in ("Home", "Document", "Markup", "Tool Chest"):
            self.workspace_tabs.addTab(name)
        self.workspace_tabs.currentChanged.connect(self._set_workspace)
        bar.addWidget(self.workspace_tabs)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        self._set_workspace(0)

    def _make_tool_dock(self) -> None:
        """Build a movable/floating tool palette whose contents follow the workspace."""
        self.tool_dock = QDockWidget("Tools · Read", self)
        self.tool_dock.setObjectName("TaskToolsDock")
        self.tool_dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.tool_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.tool_scroll = QScrollArea(self.tool_dock)
        self.tool_scroll.setWidgetResizable(True)
        self.tool_content = QWidget(self.tool_scroll)
        self.tool_layout = QVBoxLayout(self.tool_content)
        self.tool_layout.setContentsMargins(10, 12, 10, 12)
        self.tool_layout.setSpacing(7)
        self.tool_scroll.setWidget(self.tool_content)
        self.tool_dock.setWidget(self.tool_scroll)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.tool_dock)
        self.tabifyDockWidget(self.nav_dock, self.tool_dock)
        self.nav_dock.raise_()

    def _set_workspace(self, index: int) -> None:
        if not hasattr(self, "workspace_tabs"):
            return
        name = self.workspace_tabs.tabText(index)
        presets = {
            "Home": ("Open", "Save", "Undo", "Redo"),
            "Document": ("Find text", "Previous match", "Next match", "Rotate page",
                         "Reorder pages", "Insert pages", "Replace page", "Extract page",
                         "Delete page", "Crop page", "Split PDF", "Merge PDFs"),
            "Markup": ("Select", "Place text box", "Place note", "Highlight region",
                       "Underline region", "Strikethrough region", "Freehand pen",
                       "Line", "Arrow", "Rectangle", "Ellipse", "Undo", "Redo"),
            "Tool Chest": ("Select", "Place text box", "Place note", "Highlight region",
                           "Underline region", "Strikethrough region", "Freehand pen",
                           "Line", "Arrow", "Rectangle", "Ellipse"),
        }
        self.tool_dock.setWindowTitle(f"Tool Chest · {name}")
        while self.tool_layout.count():
            item = self.tool_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        heading = QLabel(f"{name} tools", self.tool_content)
        heading.setStyleSheet("font-size:16px;font-weight:600;padding:4px 2px 8px 2px;")
        heading.setAccessibleName(f"{name} workspace tools")
        self.tool_layout.addWidget(heading)
        for label in presets.get(name, ()):
            action = self.tool_actions.get(label)
            if action is None:
                continue
            button = QToolButton(self.tool_content)
            button.setText(label)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setMinimumHeight(34)
            button.setSizePolicy(button.sizePolicy().horizontalPolicy(),
                                 button.sizePolicy().verticalPolicy())
            button.setAccessibleName(label)
            button.setToolTip(action.toolTip() or action.text().replace("&", ""))
            button.clicked.connect(action.trigger)
            self.tool_layout.addWidget(button)
        divider = QFrame(self.tool_content)
        divider.setFrameShape(QFrame.Shape.HLine)
        self.tool_layout.addWidget(divider)
        note = QLabel(
            "More Acrobat-class tools are planned. Features not yet implemented are not shown as working controls.",
            self.tool_content,
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#777;padding:4px;")
        self.tool_layout.addWidget(note)
        self.tool_layout.addStretch(1)
        if name in ("Read", "Edit", "Organize", "Comment", "Fill & Sign"):
            self.nav_dock.show()
        if name == "Read":
            self.search_dock.show()
        elif name != "Home":
            self.search_dock.hide()
        self.settings.setValue("workspace", name)
        self.tool_dock.show()
        self.tool_dock.raise_()

    def _show_start_page(self) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        heading = QLabel("Northstar PDF", page)
        heading.setStyleSheet("font-size: 26px; font-weight: 600; padding: 18px;")
        hint = QLabel("Open a PDF to view, search, and navigate it. Files stay on this computer.", page)
        hint.setStyleSheet("font-size: 14px; padding: 0 20px 20px 20px;")
        layout.addWidget(heading)
        layout.addWidget(hint)
        layout.addStretch(1)
        self.tabs.addTab(page, "Home")

    def open_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Open PDF", "", "PDF files (*.pdf)")
        for path in paths:
            self.open_file(path)

    def open_file(self, path: str) -> None:
        if not Path(path).is_file():
            QMessageBox.warning(self, "File not found", f"The file does not exist:\n{path}")
            return
        tab = PdfTab(path, self.tabs)
        if tab.error:
            QMessageBox.warning(self, "Could not open PDF", f"{tab.error}\n\n{path}")
            tab.cleanup()
            tab.deleteLater()
            return
        for i in range(self.tabs.count()):
            existing = self.tabs.widget(i)
            if isinstance(existing, PdfTab) and existing.path == tab.path:
                tab.cleanup()
                tab.deleteLater()
                self.tabs.setCurrentIndex(i)
                return
        tab.view.annotationGesture.connect(
            lambda gesture, target=tab: self._handle_annotation_gesture(target, gesture)
        )
        tab.view.selectionGesture.connect(
            lambda gesture, target=tab: self._handle_selection_gesture(target, gesture)
        )
        tab.view.escapePressed.connect(self._escape_to_pointer)
        index = self.tabs.addTab(tab, Path(path).name)
        self.tabs.setCurrentIndex(index)
        recent = self.settings.value("recentFiles", [], type=list)
        recent = [p for p in recent if p != tab.path]
        recent.insert(0, tab.path)
        self.settings.setValue("recentFiles", recent[:12])
        self._refresh_recent_menu()
        self.statusBar().showMessage(f"Opened locally: {Path(path).name}", 4000)

    def _refresh_recent_menu(self) -> None:
        self.recent_menu.clear()
        recent = self.settings.value("recentFiles", [], type=list)
        for path in recent:
            action = self.recent_menu.addAction(Path(path).name)
            action.setToolTip(path)
            action.triggered.connect(lambda checked=False, p=path: self.open_file(p))
        if not recent:
            empty = self.recent_menu.addAction("No recent files")
            empty.setEnabled(False)

    def _active(self) -> PdfTab | None:
        tab = self.tabs.currentWidget()
        return tab if isinstance(tab, PdfTab) else None

    def _refresh_active_document(self, _index: int = -1) -> None:
        tab = self._active()
        if tab is None:
            self.page_list.setModel(None)
            self.bookmark_tree.setModel(None)
            self.search_results.setModel(None)
            self.page_number.setMaximum(1)
            self.page_number.setValue(1)
            self.page_total.setText("of 0")
            return
        self.page_list.setModel(tab.document.pageModel())
        self.bookmark_tree.setModel(tab.bookmarks)
        self.search_results.setModel(tab.search)
        self.page_number.blockSignals(True)
        self.page_number.setMaximum(max(1, tab.document.pageCount()))
        self.page_number.setValue(tab.view.pageNavigator().currentPage() + 1)
        self.page_number.blockSignals(False)
        self.page_total.setText(f"of {tab.document.pageCount()}")
        try:
            tab.view.pageNavigator().currentPageChanged.connect(self._current_page_changed)
        except RuntimeError:
            pass

    def _current_page_changed(self, page: int) -> None:
        if self._active() is None:
            return
        tab = self._active()
        self.page_number.blockSignals(True)
        self.page_number.setValue(page + 1)
        self.page_number.blockSignals(False)
        self.page_list.setCurrentIndex(tab.document.pageModel().index(page, 0))
        self.statusBar().showMessage(f"Page {tab.document.pageLabel(page)} of {tab.document.pageCount()}")

    def _jump_to_page_index(self, index) -> None:
        tab = self._active()
        if tab:
            tab.view.pageNavigator().jump(index.row(), QPointF(0, 0))

    def _jump_to_page_number(self, value: int) -> None:
        tab = self._active()
        if tab and tab.document.pageCount():
            tab.view.pageNavigator().jump(value - 1, QPointF(0, 0))

    def _jump_to_bookmark(self, index) -> None:
        tab = self._active()
        if not tab:
            return
        page = index.data(int(QPdfBookmarkModel.Role.Page))
        location = index.data(int(QPdfBookmarkModel.Role.Location))
        if page is not None and int(page) >= 0:
            tab.view.pageNavigator().jump(int(page), location if isinstance(location, QPointF) else QPointF())

    def _jump_to_search_result(self, index) -> None:
        tab = self._active()
        if not tab:
            return
        tab.view.setCurrentSearchResultIndex(index.row())
        link = tab.search.resultAtIndex(index.row())
        tab.view.pageNavigator().jump(link)

    def _start_search(self) -> None:
        tab = self._active()
        if not tab:
            return
        term = self.search_box.text().strip()
        tab.search.setSearchString(term)
        self.search_dock.show()
        self.search_dock.raise_()
        if not term:
            self.statusBar().showMessage("Enter text to search")
            return
        QTimer.singleShot(100, lambda: self._step_search(1))

    def _step_search(self, direction: int) -> None:
        tab = self._active()
        if not tab:
            return
        count = tab.search.count()
        if count <= 0:
            self.statusBar().showMessage("No matches found")
            return
        current = tab.view.currentSearchResultIndex()
        nxt = (current + direction) % count if current >= 0 else (0 if direction > 0 else count - 1)
        tab.view.setCurrentSearchResultIndex(nxt)
        tab.view.pageNavigator().jump(tab.search.resultAtIndex(nxt))
        self.statusBar().showMessage(f"Match {nxt + 1} of {count}")

    def _change_page_mode(self, index: int) -> None:
        tab = self._active()
        if tab:
            tab.view.setPageMode(self.mode.itemData(index))

    def _change_zoom(self, index: int) -> None:
        tab = self._active()
        if not tab:
            return
        zoom_mode = self.zoom.itemData(index)
        tab.view.setZoomMode(zoom_mode)
        if zoom_mode == QPdfView.ZoomMode.Custom:
            tab.view.setZoomFactor(1.0)

    def _output_path(self, suffix: str) -> str | None:
        tab = self._active()
        if not tab:
            return None
        suggested = str(Path(tab.path).with_name(Path(tab.path).stem + suffix + ".pdf"))
        path, _ = QFileDialog.getSaveFileName(self, "Save modified PDF as", suggested, "PDF files (*.pdf)")
        return path or None

    def _finish_page_operation(self, output: str) -> None:
        self.open_file(output)
        self.statusBar().showMessage(f"Saved a new PDF: {output}", 5000)

    def _rotate_current_page(self) -> None:
        tab = self._active()
        if not tab:
            return
        try:
            tab.rotate_current_page()
            self._set_tab_modified(tab)
            self._refresh_active_document()
            self.statusBar().showMessage("Rotation is pending. Save with Ctrl+S or close and choose Save.", 6000)
        except Exception as exc:
            QMessageBox.warning(self, "Could not rotate page", str(exc))

    def _delete_current_page(self) -> None:
        tab = self._active()
        if not tab:
            return
        if QMessageBox.question(self, "Delete page", "Delete the current page? This change will be pending until you save.") != QMessageBox.StandardButton.Yes:
            return
        try:
            tab.delete_current_page()
            self._refresh_active_document()
            self._set_tab_modified(tab)
            self.statusBar().showMessage("Page deletion is pending. Save with Ctrl+S or close and choose Save.", 6000)
        except Exception as exc:
            QMessageBox.warning(self, "Could not delete page", str(exc))

    def _add_sticky_note(self) -> None:
        tab = self._active()
        if not tab:
            return
        note, accepted = QInputDialog.getMultiLineText(self, "Add sticky note", "Note text:")
        if not accepted or not note.strip():
            return
        try:
            tab.add_sticky_note(note)
            self._set_tab_modified(tab)
            self.statusBar().showMessage("Sticky note is pending. Save with Ctrl+S or close and choose Save.", 6000)
        except Exception as exc:
            QMessageBox.warning(self, "Could not add sticky note", str(exc))

    def _set_tab_modified(self, tab: PdfTab) -> None:
        index = self.tabs.indexOf(tab)
        self.tabs.setTabText(index, Path(tab.path).name + (" *" if tab.dirty else ""))


    def _extract_current_page(self) -> None:
        tab = self._active()
        if not tab:
            return
        output = self._output_path("_page_extracted")
        if not output:
            return
        try:
            extract_pages(tab.path, output, [tab.view.pageNavigator().currentPage()])
            self._finish_page_operation(output)
        except Exception as exc:
            QMessageBox.warning(self, "Could not extract page", str(exc))

    def _reorder_pages(self) -> None:
        tab = self._active()
        if not tab:
            return
        order, accepted = QInputDialog.getText(
            self, "Reorder pages", f"Enter all {tab.document.pageCount()} page numbers in the new order, separated by commas:"
        )
        if not accepted:
            return
        try:
            values = [int(part.strip()) - 1 for part in order.split(",")]
            tab.reorder_pages(values)
            self._refresh_active_document()
            self._set_tab_modified(tab)
        except Exception as exc:
            QMessageBox.warning(self, "Could not reorder pages", str(exc))

    def _crop_current_page(self) -> None:
        tab = self._active()
        if not tab:
            return
        text, accepted = QInputDialog.getText(
            self, "Crop current page",
            "Enter left,bottom,right,top coordinates in PDF points:"
        )
        if not accepted:
            return
        try:
            values = [float(part.strip()) for part in text.split(",")]
            if len(values) != 4:
                raise ValueError("Enter exactly four coordinates.")
            tab.crop_current_page(*values)
            self._set_tab_modified(tab)
        except Exception as exc:
            QMessageBox.warning(self, "Could not crop page", str(exc))

    def _insert_pages(self) -> None:
        tab = self._active()
        if not tab:
            return
        source, _ = QFileDialog.getOpenFileName(self, "Choose PDF pages to insert", "", "PDF files (*.pdf)")
        if not source:
            return
        before, accepted = QInputDialog.getInt(
            self, "Insert pages", f"Insert before page (1 to {tab.document.pageCount() + 1}):",
            max(1, tab.view.pageNavigator().currentPage() + 1), 1, tab.document.pageCount() + 1
        )
        if not accepted:
            return
        try:
            tab.insert_pages(source, before - 1)
            self._refresh_active_document()
            self._set_tab_modified(tab)
        except Exception as exc:
            QMessageBox.warning(self, "Could not insert pages", str(exc))

    def _replace_current_page(self) -> None:
        tab = self._active()
        if not tab:
            return
        source, _ = QFileDialog.getOpenFileName(self, "Choose replacement PDF page", "", "PDF files (*.pdf)")
        if not source:
            return
        try:
            tab.replace_current_page(source)
            self._refresh_active_document()
            self._set_tab_modified(tab)
        except Exception as exc:
            QMessageBox.warning(self, "Could not replace page", str(exc))

    def _split_pdf(self) -> None:
        tab = self._active()
        if not tab:
            return
        directory = QFileDialog.getExistingDirectory(self, "Choose a folder for the split PDF pages")
        if not directory:
            return
        try:
            paths = split_pdf(tab.edit_session.working_path, directory, tab.password)
            self.statusBar().showMessage(f"Created {len(paths)} local PDF files in {directory}", 6000)
        except Exception as exc:
            QMessageBox.warning(self, "Could not split PDF", str(exc))

    def _fill_form_fields(self) -> None:
        tab = self._active()
        if not tab:
            return
        try:
            fields = read_form_fields(tab.edit_session.working_path, tab.password)
            if not fields:
                QMessageBox.information(self, "No form fields", "This PDF does not contain editable AcroForm fields.")
                return
            prompt = "Enter one field=value pair per line. Fields found:\n" + "\n".join(fields)
            text, accepted = QInputDialog.getMultiLineText(self, "Fill PDF form", prompt)
            if not accepted:
                return
            values = {}
            for line in text.splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()
            if not values:
                return
            tab.fill_fields(values)
            self._set_tab_modified(tab)
        except Exception as exc:
            QMessageBox.warning(self, "Could not fill form", str(exc))

    def _edit_existing_text(self) -> None:
        """Replace exact page text using redaction, staged in the per-tab undo history."""
        tab = self._active()
        if not tab:
            return
        page = max(0, tab.view.pageNavigator().currentPage())
        old_text, accepted = QInputDialog.getText(
            self, "Edit existing text", "Exact text to find on the current page:"
        )
        if not accepted or not old_text:
            return
        new_text, accepted = QInputDialog.getText(
            self, "Edit existing text", "Replacement text (leave empty to remove it):"
        )
        if not accepted:
            return
        try:
            tab.edit_session.apply_operation(
                replace_existing_text, page, old_text, new_text, password=tab.password
            )
            tab._reload_working_copy(page)
            self._set_tab_modified(tab)
            self.statusBar().showMessage(
                "Text replacement staged · Ctrl+Z to undo · Ctrl+S to save", 6000
            )
        except Exception as exc:
            QMessageBox.warning(self, "Could not edit text", str(exc))

    def _export_text(self) -> None:
        tab = self._active()
        if not tab:
            return
        output, _ = QFileDialog.getSaveFileName(
            self, "Export PDF text", str(Path(tab.path).with_suffix(".txt")), "Text files (*.txt)"
        )
        if not output:
            return
        try:
            extract_text_file(tab.edit_session.working_path, output, tab.password)
            self.statusBar().showMessage(f"Exported local text to {output}", 5000)
        except Exception as exc:
            QMessageBox.warning(self, "Could not export text", str(exc))

    def _activate_annotation_tool(self, kind: str) -> None:
        tab = self._active()
        if not tab:
            self.statusBar().showMessage("Open a PDF before selecting an annotation tool.", 4000)
            return
        if self._text_editor is not None:
            self.statusBar().showMessage("Place or cancel the active text box first.", 4000)
            return
        tab.view.set_annotation_tool(kind)
        tab.view.setFocus()
        labels = {
            "text": "Text box", "note": "Sticky note", "highlight": "Highlight",
            "underline": "Underline", "strikeout": "Strikethrough", "pen": "Freehand pen",
            "line": "Line", "arrow": "Arrow", "rectangle": "Rectangle", "ellipse": "Ellipse",
        }
        self.statusBar().showMessage(
            f"{labels.get(kind, kind)} active · click or drag on the page · Esc to exit"
        )

    @staticmethod
    def _viewport_to_pdf(tab: PdfTab, page_index: int, point: tuple[float, float],
                         location: QPointF, zoom: float, box) -> tuple[float, float]:
        page = tab.document.pagePointSize(page_index)
        view_width = float(page.width()) * zoom
        x_margin = max(0.0, (tab.view.viewport().width() - view_width) / 2.0)
        x_from_top_left = location.x() + max(0.0, point[0] - x_margin) / zoom
        y_from_top_left = location.y() + max(0.0, point[1]) / zoom
        x_from_top_left = min(float(page.width()), max(0.0, x_from_top_left))
        y_from_top_left = min(float(page.height()), max(0.0, y_from_top_left))
        return float(box.left) + x_from_top_left, float(box.top) - y_from_top_left

    def _begin_text_box(self, tab: PdfTab, gesture: dict) -> None:
        start_x, start_y = gesture["start"]
        end_x, end_y = gesture["end"]
        left, top = min(start_x, end_x), min(start_y, end_y)
        width, height = abs(end_x - start_x), abs(end_y - start_y)
        if width < 48 or height < 28:
            width, height = 230, 88
        viewport = tab.view.viewport()
        width = min(max(110, int(width)), max(110, viewport.width() - 8))
        height = min(max(54, int(height)), max(54, viewport.height() - 8))
        left = max(0, min(int(left), max(0, viewport.width() - width)))
        top = max(0, min(int(top), max(0, viewport.height() - height)))
        editor = TextPlacementEditor(viewport, QRect(left, top, width, height))
        self._text_editor = editor
        self._text_active_tab = tab
        self._text_context = {
            "page": int(gesture["page"]),
            "location": QPointF(gesture["location"]),
            "zoom": float(gesture["zoom"]),
        }
        editor.cancel_action.triggered.connect(self._cancel_text_box)
        editor.commit_action.triggered.connect(self._commit_text_box)
        editor.text_edit.placeRequested.connect(self._commit_text_box)
        editor.show()
        editor.raise_()
        self.text_toolbar.setEnabled(True)
        self._apply_text_style()
        editor.text_edit.setFocus()
        self.statusBar().showMessage(
            "Type in the outlined box · drag the blue corner to resize · Place or Ctrl+Enter to finish"
        )

    def _text_style(self) -> dict:
        return {
            "size": self.text_size.value(),
            "bold": self.text_bold.isChecked(),
            "italic": self.text_italic.isChecked(),
            "underline": self.text_underline.isChecked(),
            "letter_spacing": self.text_letter_spacing.value(),
            "line_spacing": self.text_line_spacing.value(),
            "alignment": self.text_alignment.currentData(),
        }

    def _apply_text_style(self, *_args) -> None:
        editor = self._text_editor
        if editor is None:
            return
        style = self._text_style()
        font = QFont("Helvetica")
        font.setPointSizeF(float(style["size"]))
        font.setBold(bool(style["bold"]))
        font.setItalic(bool(style["italic"]))
        font.setUnderline(bool(style["underline"]))
        char_format = QTextCharFormat()
        char_format.setFont(font)
        char_format.setFontLetterSpacing(float(style["letter_spacing"]))
        block_format = QTextBlockFormat()
        block_format.setLineHeight(
            float(style["line_spacing"]) * 100.0,
            QTextBlockFormat.LineHeightTypes.ProportionalHeight,
        )
        alignment = {
            "left": Qt.AlignmentFlag.AlignLeft,
            "center": Qt.AlignmentFlag.AlignHCenter,
            "right": Qt.AlignmentFlag.AlignRight,
            "justify": Qt.AlignmentFlag.AlignJustify,
        }.get(style["alignment"], Qt.AlignmentFlag.AlignLeft)
        block_format.setAlignment(alignment)
        cursor = editor.text_edit.textCursor()
        position = cursor.position()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.mergeCharFormat(char_format)
        cursor.mergeBlockFormat(block_format)
        cursor.clearSelection()
        cursor.setPosition(min(position, editor.text_edit.document().characterCount() - 1))
        editor.text_edit.setTextCursor(cursor)
        editor.text_edit.setCurrentCharFormat(char_format)

    def _escape_to_pointer(self) -> None:
        """Cancel a live text box or leave the current tool and restore the cursor."""
        if self._text_editor is not None:
            self._cancel_text_box()
            return
        tab = self._active()
        if tab is not None and tab.view.annotation_tool is not None:
            tab.view.set_annotation_tool(None)
            tab.view.setFocus()
            self.statusBar().showMessage("Pointer tool active", 2500)

    def _cancel_text_box(self) -> None:
        if self._text_editor is not None:
            self._text_editor.deleteLater()
        tab = self._text_active_tab
        self._text_editor = None
        self._text_active_tab = None
        self._text_context = None
        self.text_toolbar.setEnabled(False)
        if tab is not None:
            tab.view.set_annotation_tool(None)
            tab.view.setFocus()
        self.statusBar().showMessage("Text placement cancelled · pointer tool active", 3000)

    def _commit_text_box(self) -> None:
        editor = self._text_editor
        tab = self._text_active_tab
        context = self._text_context
        if editor is None or tab is None or context is None:
            return
        text = editor.text_edit.toPlainText()
        if not text.strip():
            self.statusBar().showMessage("Type some text before placing it", 4000)
            editor.text_edit.setFocus()
            return
        geometry = editor.geometry()
        view_rect = tab.view.viewport().rect()
        left = max(0, min(geometry.left(), view_rect.right()))
        top = max(0, min(geometry.top(), view_rect.bottom()))
        right = max(left + 1, min(geometry.right(), view_rect.right()))
        bottom = max(top + 1, min(geometry.bottom(), view_rect.bottom()))
        reader = PdfReader(str(tab.edit_session.working_path), strict=False)
        if reader.is_encrypted and tab.password:
            reader.decrypt(tab.password)
        page_index = int(context["page"])
        page_box = reader.pages[page_index].mediabox
        top_left = self._viewport_to_pdf(
            tab, page_index, (float(left), float(top)),
            context["location"], context["zoom"], page_box
        )
        bottom_right = self._viewport_to_pdf(
            tab, page_index, (float(right), float(bottom)),
            context["location"], context["zoom"], page_box
        )
        pdf_rect = (top_left[0], bottom_right[1], bottom_right[0], top_left[1])
        try:
            tab.edit_session.apply_operation(
                add_rich_text_box, page_index, pdf_rect, text, self._text_style(),
                password=tab.password,
            )
            editor.deleteLater()
            self._text_editor = None
            self._text_active_tab = None
            self._text_context = None
            self.text_toolbar.setEnabled(False)
            tab._reload_working_copy(page_index)
            self._set_tab_modified(tab)
            self.statusBar().showMessage(
                "Formatted text placed · Ctrl+Z to undo · Ctrl+S to save", 5000
            )
        except Exception as exc:
            QMessageBox.warning(self, "Could not place text", str(exc))
            editor.text_edit.setFocus()

    def _handle_annotation_gesture(self, tab: PdfTab, gesture: dict) -> None:
        if self.tabs.indexOf(tab) < 0:
            return
        kind = gesture["kind"]
        page = int(gesture["page"])
        if kind == "text":
            self._begin_text_box(tab, gesture)
            return
        location = gesture["location"]
        zoom = float(gesture["zoom"])
        reader = PdfReader(str(tab.edit_session.working_path), strict=False)
        if reader.is_encrypted and tab.password:
            reader.decrypt(tab.password)
        box = reader.pages[page].mediabox
        start = self._viewport_to_pdf(tab, page, gesture["start"], location, zoom, box)
        end = self._viewport_to_pdf(tab, page, gesture["end"], location, zoom, box)
        points = [
            self._viewport_to_pdf(tab, page, point, location, zoom, box)
            for point in gesture["points"]
        ]
        if kind == "note":
            x, y = start
            x = min(max(x, float(box.left)), float(box.right) - 22)
            y = min(max(y, float(box.bottom)), float(box.top) - 22)
            rect = (x, y, x + 22, y + 22)
            text, accepted = QInputDialog.getMultiLineText(self, "Sticky note", "Note text:")
            if not accepted or not text.strip():
                return
        else:
            text = ""
            if kind == "text":
                text, accepted = QInputDialog.getMultiLineText(self, "Text box", "Text:")
                if not accepted or not text.strip():
                    return
            x1, y1 = start
            x2, y2 = end
            if abs(x2 - x1) < 3:
                x2 = x1 + 3
            if abs(y2 - y1) < 3:
                y2 = y1 + 3
            rect = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            if kind == "text" and (abs(x2 - x1) < 24 or abs(y2 - y1) < 16):
                width = min(160.0, max(3.0, float(box.right) - rect[0]))
                height = min(42.0, max(3.0, float(box.top) - rect[1]))
                rect = (rect[0], rect[1], rect[0] + width, rect[1] + height)
        try:
            tab.edit_session.apply_operation(
                add_pdf_annotation, page, kind, rect, text, points, password=tab.password
            )
            tab._reload_working_copy(page)
            self._set_tab_modified(tab)
            tool_name = kind.replace("strikeout", "strikethrough").replace("pen", "freehand")
            self.statusBar().showMessage(
                f"{tool_name.title()} annotation pending · Ctrl+S to save · Esc to stop tool", 5000
            )
        except Exception as exc:
            QMessageBox.warning(self, "Could not add annotation", str(exc))

    def _protect_copy(self) -> None:
        tab = self._active()
        if not tab:
            return
        password, accepted = QInputDialog.getText(
            self, "Password-protect a copy", "Enter a password for the new PDF:",
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return
        confirm, accepted = QInputDialog.getText(
            self, "Confirm password", "Enter the password again:",
            QLineEdit.EchoMode.Password,
        )
        if not accepted:
            return
        if password != confirm:
            QMessageBox.warning(self, "Passwords do not match", "Enter the same password twice.")
            return
        suggested = str(Path(tab.path).with_name(Path(tab.path).stem + "_protected.pdf"))
        output, _ = QFileDialog.getSaveFileName(
            self, "Save password-protected copy", suggested, "PDF files (*.pdf)"
        )
        if not output:
            return
        try:
            protect_pdf(tab.edit_session.working_path, output, password, tab.password)
            self.statusBar().showMessage(f"Created password-protected PDF: {output}", 6000)
        except Exception as exc:
            QMessageBox.warning(self, "Could not protect PDF", str(exc))

    def _merge_documents(self) -> None:
        sources, _ = QFileDialog.getOpenFileNames(self, "Choose PDFs to merge", "", "PDF files (*.pdf)")
        if len(sources) < 2:
            return
        output, _ = QFileDialog.getSaveFileName(self, "Save merged PDF as", "", "PDF files (*.pdf)")
        if not output:
            return
        try:
            merge_pdfs(sources, output)
            self._finish_page_operation(output)
        except Exception as exc:
            QMessageBox.warning(self, "Could not merge PDFs", str(exc))

    def _save_active(self) -> None:
        tab = self._active()
        if not tab:
            return
        try:
            tab.save_edits()
            self.tabs.setTabText(self.tabs.indexOf(tab), Path(tab.path).name)
            self.statusBar().showMessage("Saved changes to the original PDF.", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save PDF", str(exc))

    def _undo_active(self) -> None:
        tab = self._active()
        if not tab or not tab.edit_session.can_undo:
            return
        tab.undo_edit()
        self._set_tab_modified(tab)

    def _redo_active(self) -> None:
        tab = self._active()
        if not tab or not tab.edit_session.can_redo:
            return
        tab.redo_edit()
        self.tabs.setTabText(self.tabs.indexOf(tab), Path(tab.path).name + (" *" if tab.dirty else ""))

    def _confirm_pending_edits(self, tab: PdfTab) -> bool:
        if not tab.dirty:
            return True
        answer = QMessageBox.question(
            self, "Unsaved changes",
            f"Save changes to {Path(tab.path).name} before closing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Discard:
            tab.discard_edits()
            return True
        try:
            tab.save_edits()
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Could not save PDF", str(exc))
            return False

    def closeEvent(self, event: QCloseEvent) -> None:
        pdf_tabs = [self.tabs.widget(i) for i in range(self.tabs.count())]
        pdf_tabs = [tab for tab in pdf_tabs if isinstance(tab, PdfTab)]
        for tab in pdf_tabs:
            if not self._confirm_pending_edits(tab):
                event.ignore()
                return
        for tab in pdf_tabs:
            tab.cleanup()
        self.settings.setValue("windowGeometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        event.accept()

    def _toggle_full_screen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _set_dark_theme(self, enabled: bool) -> None:
        self.settings.setValue("darkTheme", enabled)
        if enabled:
            QApplication.instance().setStyleSheet(
                "QMainWindow,QWidget { background:#24262b; color:#e7e8ea; }"
                "QLineEdit,QSpinBox,QComboBox,QTreeView,QListView { background:#30333a; }"
            )
        else:
            QApplication.instance().setStyleSheet("")

    def _close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if isinstance(widget, PdfTab):
            if not self._confirm_pending_edits(widget):
                return
            widget.cleanup()
        self.tabs.removeTab(index)
        if widget:
            widget.deleteLater()
        if self.tabs.count() == 0:
            self._show_start_page()


def self_test() -> int:
    """Small executable smoke check used by the Windows build workflow."""
    from tempfile import NamedTemporaryFile
    from pypdf import PdfWriter

    app = QApplication.instance() or QApplication([])
    with NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"this is not a valid PDF")
        malformed_path = f.name
    with NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        valid_path = f.name
    writer = PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=612, height=792)
    with open(valid_path, "wb") as stream:
        writer.write(stream)
    try:
        document = QPdfDocument()
        result = document.load(malformed_path)
        malformed_ok = result == QPdfDocument.Error.InvalidFileFormat
        window = MainWindow([valid_path])
        window.resize(900, 700)
        window.show()
        app.processEvents()
        organize_index = next(
            i for i in range(window.workspace_tabs.count())
            if window.workspace_tabs.tabText(i) == "Organize"
        )
        window.workspace_tabs.setCurrentIndex(organize_index)
        ui_ok = (window.tool_dock.windowTitle() == "Tools · Organize"
                 and window.tool_dock.isFloating() is False)

        view = window._active().view
        nav = view.pageNavigator()
        previous_zoom = float(nav.currentZoom())

        class FakeWheel:
            def angleDelta(self):
                return QPoint(0, 120)
            def position(self):
                return QPointF(120, 120)
            def accept(self):
                pass

        view.wheelEvent(FakeWheel())
        app.processEvents()
        zoom_ok = float(nav.currentZoom()) > previous_zoom

        class FakeMouse:
            def __init__(self, button, x, y):
                self._button = button
                self._point = QPointF(x, y)
            def button(self):
                return self._button
            def position(self):
                return self._point
            def accept(self):
                pass

        scrollbar = view.verticalScrollBar()
        scrollbar.setRange(0, 1000)
        scrollbar.setValue(400)
        view.mousePressEvent(FakeMouse(Qt.MouseButton.MiddleButton, 100, 100))
        view.mouseMoveEvent(FakeMouse(Qt.MouseButton.NoButton, 100, 120))
        pan_ok = scrollbar.value() == 380
        view.mouseReleaseEvent(FakeMouse(Qt.MouseButton.MiddleButton, 100, 120))

        view.setPageMode(QPdfView.PageMode.SinglePage)
        view.setZoomMode(QPdfView.ZoomMode.FitInView)
        app.processEvents()
        view.horizontalScrollBar().setRange(0, 0)
        view.verticalScrollBar().setRange(0, 0)
        before_fit_pan = view.pageNavigator().currentLocation()
        view.mousePressEvent(FakeMouse(Qt.MouseButton.MiddleButton, 100, 100))
        view.mouseMoveEvent(FakeMouse(Qt.MouseButton.NoButton, 150, 150))
        view.mouseReleaseEvent(FakeMouse(Qt.MouseButton.MiddleButton, 150, 150))
        after_fit_pan = view.pageNavigator().currentLocation()
        fit_pan_ok = (abs(after_fit_pan.x() - before_fit_pan.x()) > 0.01
                      or abs(after_fit_pan.y() - before_fit_pan.y()) > 0.01)

        text_tab = window._active()
        nav = text_tab.view.pageNavigator()
        gesture = {
            "kind": "text", "page": max(0, nav.currentPage()),
            "start": (100.0, 100.0), "end": (300.0, 185.0),
            "points": [(100.0, 100.0), (300.0, 185.0)],
            "location": QPointF(nav.currentLocation()),
            "zoom": max(float(nav.currentZoom()), 0.1),
        }
        window._begin_text_box(text_tab, gesture)
        preview = window._text_editor
        preview.text_edit.setPlainText("Visible preview")
        window.text_bold.setChecked(True)
        app.processEvents()
        initial_width = preview.width()
        preview.resize(initial_width + 24, preview.height() + 16)
        resize_ok = preview.width() == initial_width + 24

        class FakeDrag:
            def __init__(self, x, y):
                self._point = QPointF(x, y)
            def button(self):
                return Qt.MouseButton.LeftButton
            def globalPosition(self):
                return self._point
            def accept(self):
                pass

        before_move = preview.pos()
        preview.move_handle.mousePressEvent(FakeDrag(10, 10))
        preview.move_handle.mouseMoveEvent(FakeDrag(34, 28))
        preview.move_handle.mouseReleaseEvent(FakeDrag(34, 28))
        move_ok = preview.pos() != before_move and preview.text_edit.toPlainText() == "Visible preview"
        preview.text_edit.moveCursor(QTextCursor.MoveOperation.End)
        preview.text_edit.keyPressEvent(QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_Return,
            Qt.KeyboardModifier.ControlModifier,
        ))
        newline_ok = preview.text_edit.toPlainText() == "Visible preview\\n"
        preview.text_edit.insertPlainText("second line")
        preview_ok = (newline_ok and move_ok and window.text_toolbar.isEnabled()
                      and preview.isVisible() and preview.move_handle.isVisible())
        # Plain Enter places the text; Ctrl+Enter above inserted a line break.
        preview.text_edit.keyPressEvent(QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_Return,
            Qt.KeyboardModifier.NoModifier,
        ))
        app.processEvents()
        placed_reader = PdfReader(str(text_tab.edit_session.working_path), strict=False)
        extracted = placed_reader.pages[gesture["page"]].extract_text() or ""
        placed_text = "Visible preview" in extracted and "second line" in extracted
        text_tab.view.set_annotation_tool("text")
        window._escape_to_pointer()
        escape_ok = text_tab.view.annotation_tool is None
        text_box_ok = preview_ok and resize_ok and placed_text and escape_ok
        text_tab.discard_edits()
        window.close()

        print("PDFium malformed-file smoke test:",
              "PASS" if malformed_ok else f"FAIL ({result})")
        print("Task workspaces and movable tools panel:", "PASS" if ui_ok else "FAIL")
        print("Scroll-wheel zoom:", "PASS" if zoom_ok else "FAIL")
        print("Middle-button drag pan:", "PASS" if pan_ok else "FAIL")
        print("Panning with fit-to-page scrollbars disabled:",
              "PASS" if fit_pan_ok else "FAIL")
        print("Enter places; Ctrl+Enter adds a line; Escape restores pointer:",
              "PASS" if text_box_ok else "FAIL")
        return 0 if malformed_ok and ui_ok and zoom_ok and pan_ok and fit_pan_ok and text_box_ok else 1
    finally:
        document.close()
        os.unlink(malformed_path)
        os.unlink(valid_path)
        app.quit()


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Northstar")
    app.setApplicationVersion("0.1.0")
    files = [arg for arg in sys.argv[1:] if not arg.startswith("-")]
    window = MainWindow(files)
    window.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
