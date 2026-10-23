"""Northstar PDF: a local-only Windows PDF viewer built with Qt PDF/PDFium."""
from __future__ import annotations
import os
import sys
from pathlib import Path

# Install the offline guard before loading any UI or PDF libraries.
from offline_guard import install_offline_guard
install_offline_guard()

from pdf_operations import extract_pages, merge_pdfs
from edit_session import EditSession

from PySide6.QtCore import Qt, QPointF, QSettings, QTimer
from PySide6.QtGui import QAction, QKeySequence, QCloseEvent
from PySide6.QtPdf import QPdfDocument, QPdfBookmarkModel, QPdfSearchModel
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDockWidget, QFileDialog, QInputDialog, QLabel,
    QLineEdit, QListView, QMainWindow, QMessageBox, QSpinBox, QTabWidget,
    QToolBar, QTreeView, QWidget, QHBoxLayout, QVBoxLayout, QTabBar,
)

APP_NAME = "Northstar PDF"

class PdfTab(QWidget):
    def __init__(self, path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = str(Path(path).resolve())
        self.document = QPdfDocument(self)
        self.bookmarks = QPdfBookmarkModel(self)
        self.bookmarks.setDocument(self.document)
        self.search = QPdfSearchModel(self)
        self.search.setDocument(self.document)
        self.view = QPdfView(self)
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
        self.tabs = QTabWidget(self)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.setCentralWidget(self.tabs)
        self._make_navigation_dock()
        self._make_search_dock()
        self._make_toolbar()
        self._make_menus()
        self.tabs.currentChanged.connect(self._refresh_active_document)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.statusBar().showMessage("Ready · offline")
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
        toolbar = QToolBar("PDF tools", self)
        toolbar.setMovable(False)
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
        find_action.setShortcut(QKeySequence.StandardKey.Find)
        find_action.triggered.connect(self._start_search)
        toolbar.addAction(find_action)
        self.previous_result = QAction("Previous", self)
        self.previous_result.triggered.connect(lambda: self._step_search(-1))
        toolbar.addAction(self.previous_result)
        self.next_result = QAction("Next", self)
        self.next_result.triggered.connect(lambda: self._step_search(1))
        toolbar.addAction(self.next_result)

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
        merge_action = page_menu.addAction("Merge PDFs…")
        merge_action.triggered.connect(self._merge_documents)
        view_menu = self.menuBar().addMenu("&View")
        fullscreen = view_menu.addAction("Full screen")
        fullscreen.setShortcut(QKeySequence("F11"))
        fullscreen.triggered.connect(self._toggle_full_screen)
        dark = view_menu.addAction("Dark theme")
        dark.setCheckable(True)
        dark.setChecked(bool(self.settings.value("darkTheme", False, type=bool)))
        dark.toggled.connect(self._set_dark_theme)
        if dark.isChecked():
            self._set_dark_theme(True)

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
            tab.deleteLater()
            return
        for i in range(self.tabs.count()):
            existing = self.tabs.widget(i)
            if isinstance(existing, PdfTab) and existing.path == tab.path:
                tab.deleteLater()
                self.tabs.setCurrentIndex(i)
                return
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
    app = QApplication.instance() or QApplication([])
    with NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"this is not a valid PDF")
        name = f.name
    try:
        document = QPdfDocument()
        result = document.load(name)
        ok = result == QPdfDocument.Error.InvalidFileFormat
        print("PDFium malformed-file smoke test:", "PASS" if ok else f"FAIL ({result})")
        return 0 if ok else 1
    finally:
        document.close()  # Release PDFium's file handle before deleting on Windows.
        os.unlink(name)
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
