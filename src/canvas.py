# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Page canvas: renders PDF pages with PyMuPDF and handles every mouse tool.

Scene units are PDF points of the *visible* (rotated, cropped) page, so the
view's scale is the zoom factor. Each page is a PageItem positioned in a
vertical strip. Pages render lazily when they scroll into view; at high zoom a
second, sharper render of just the visible part of the page is layered on top
so large-format drawings stay crisp without rendering enormous bitmaps.

The canvas never edits the document itself. It emits signals with points in
unrotated page coordinates (see markups.py) and the window applies them
through the PdfSession so they are undoable.
"""
from __future__ import annotations

import math

import pymupdf as fitz
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QImage, QKeySequence, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QApplication, QGraphicsItem, QGraphicsPathItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem,
                               QGraphicsView, QPlainTextEdit)

import markups
import snap
from pdf_document import PdfSession

PAGE_GAP = 18.0
MAX_BASE_PIXELS = 3000  # longest side of the whole-page render
MIN_ZOOM, MAX_ZOOM = 0.05, 32.0
HANDLE = 7.0  # selection handle size in screen pixels
SELECT_COLOR = QColor("#1683d8")


def pixmap_to_qimage(pix: fitz.Pixmap) -> QImage:
    fmt = QImage.Format.Format_RGB888 if pix.n == 3 else QImage.Format.Format_RGBA8888
    return QImage(pix.samples, pix.width, pix.height, pix.stride, fmt).copy()


class PageItem(QGraphicsItem):
    """One page: a whole-page render plus an optional sharper visible-area patch."""

    def __init__(self, index: int, width: float, height: float) -> None:
        super().__init__()
        self.index = index
        self.width, self.height = width, height
        self.base: QImage | None = None
        self.base_scale = 0.0
        self.detail: QImage | None = None
        self.detail_rect = QRectF()
        self.detail_scale = 0.0
        self.stale = True
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption)

    def boundingRect(self) -> QRectF:
        return QRectF(-4, -4, self.width + 8, self.height + 8)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        page = QRectF(0, 0, self.width, self.height)
        painter.fillRect(page.translated(2, 3), QColor(0, 0, 0, 45))
        painter.fillRect(page, Qt.GlobalColor.white)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self.base is not None:
            painter.drawImage(page, self.base)
        if self.detail is not None:
            painter.drawImage(self.detail_rect, self.detail)
        painter.setPen(QPen(QColor(0, 0, 0, 70), 0))
        painter.drawRect(page)


class InlineTextEditor(QPlainTextEdit):
    """Editor overlaid on the page for typing text box and callout text."""

    committed = Signal(str)
    cancelled = Signal()

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setPlaceholderText("Type, then click outside or press Ctrl+Enter")
        self.setStyleSheet("QPlainTextEdit { background: rgba(255,255,255,235); color:#111;"
                           " border: 2px solid #1683d8; }")
        self._done = False

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._finish(cancel=True)
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._finish()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        QTimer.singleShot(0, self._finish)

    def _finish(self, cancel: bool = False) -> None:
        if self._done:
            return
        self._done = True
        if cancel or not self.toPlainText().strip():
            self.cancelled.emit()
        else:
            self.committed.emit(self.toPlainText())
        self.deleteLater()


class PdfCanvas(QGraphicsView):
    """Continuous or single-page view of a PdfSession with markup tools."""

    createRequested = Signal(str, int, list, str)        # kind, page, points, text
    moveRequested = Signal(int, list, float, float)      # page, xrefs, dx, dy
    resizeRequested = Signal(int, int, object)           # page, xref, fitz.Rect
    pointsRequested = Signal(int, int, list)             # page, xref, points
    editTextRequested = Signal(int, int)                 # page, xref
    deleteRequested = Signal()
    selectionChanged = Signal()
    areaRequested = Signal(str, int, object)             # purpose, page, fitz.Rect
    lineRequested = Signal(str, int, list)               # purpose, page, points
    pageChanged = Signal(int)
    zoomChanged = Signal(float)
    toolFinished = Signal()
    textSelected = Signal(str)
    contextRequested = Signal(object, int, object)       # global pos, page, point

    def __init__(self, session: PdfSession, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#59626b"))
        self.setRenderHints(QPainter.RenderHint.Antialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.pages: list[PageItem] = []
        self.single_page = False
        self.current_page = 0
        self.zoom = 1.0
        self.fit_mode = "width"  # "width", "page", or "" for a fixed zoom
        self.tool = "select"
        self.selection: list[tuple[int, int]] = []  # (page, xref)
        self.ortho = False
        self.snap_enabled = True
        self._snap_content: dict[int, tuple[tuple, list]] = {}
        self._snap_index: dict[int, snap.SnapIndex] = {}
        self._snap_exclude: set[int] = set()
        self._last_snap: tuple[fitz.Point, str] | None = None
        self._snap_items: list[QGraphicsItem] = []
        self._overlay: list[QGraphicsItem] = []
        self._labels: list[QGraphicsItem] = []
        self._search_items: list[QGraphicsItem] = []
        self._text_items: list[QGraphicsItem] = []
        self._preview: QGraphicsPathItem | None = None
        self._drag: dict | None = None
        self._points: list[QPointF] = []
        self._points_page = -1
        self._pan_origin = None
        self._space_down = False
        self._selected_text = ""
        self._words_cache: dict[int, list] = {}
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(40)
        self._render_timer.timeout.connect(self._render_visible)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.horizontalScrollBar().valueChanged.connect(self._scrolled)
        session.add_listener(self.document_changed)
        self.layout_pages()

    # -- layout and rendering ------------------------------------------------
    def page(self, index: int) -> fitz.Page:
        return self.session.doc[index]

    def layout_pages(self) -> None:
        scene = self.scene()
        for item in self.pages:
            scene.removeItem(item)
        self.pages = []
        doc = self.session.doc
        self.current_page = max(0, min(self.current_page, doc.page_count - 1))
        indices = [self.current_page] if self.single_page else range(doc.page_count)
        widest = max((doc[i].rect.width for i in indices), default=612)
        y = PAGE_GAP
        for index in indices:
            rect = doc[index].rect
            item = PageItem(index, rect.width, rect.height)
            item.setPos((widest - rect.width) / 2 + PAGE_GAP, y)
            scene.addItem(item)
            self.pages.append(item)
            y += rect.height + PAGE_GAP
        scene.setSceneRect(QRectF(0, 0, widest + 2 * PAGE_GAP, y))
        self._words_cache.clear()
        self.refresh_overlays()
        self._apply_fit()
        self.schedule_render()

    def document_changed(self) -> None:
        """Called by the session after every edit, undo, or redo."""
        doc = self.session.doc
        sizes = [(item.index, item.width, item.height) for item in self.pages]
        indices = [self.current_page] if self.single_page else range(doc.page_count)
        expected = [(i, doc[i].rect.width, doc[i].rect.height) for i in indices
                    if i < doc.page_count]
        self.selection = [(p, x) for p, x in self.selection
                          if p < doc.page_count and self._annot_exists(p, x)]
        self._snap_index.clear()
        if sizes != expected or self.current_page >= doc.page_count:
            keep_page = min(self.current_page, doc.page_count - 1)
            self.current_page = keep_page
            self.layout_pages()
            self.go_to_page(keep_page)
        else:
            for item in self.pages:
                item.stale = True
            self._words_cache.clear()
            self.refresh_overlays()
            self.schedule_render()
        self.selectionChanged.emit()

    def _annot_exists(self, page: int, xref: int) -> bool:
        try:
            return self.page(page).load_annot(xref) is not None
        except Exception:
            return False

    def schedule_render(self) -> None:
        self._render_timer.start()

    def _scrolled(self) -> None:
        self.schedule_render()
        page = self.page_at_viewport_center()
        if page != self.current_page and not self.single_page:
            self.current_page = page
            self.pageChanged.emit(page)

    def visible_scene_rect(self) -> QRectF:
        return self.mapToScene(self.viewport().rect()).boundingRect()

    def _render_visible(self) -> None:
        visible = self.visible_scene_rect()
        ratio = self.devicePixelRatioF()
        desired = self.zoom * ratio
        doc = self.session.doc
        for item in self.pages:
            page_rect = QRectF(item.pos().x(), item.pos().y(), item.width, item.height)
            if not page_rect.intersects(visible.adjusted(0, -visible.height(), 0, visible.height())):
                if item.base is not None and abs(item.pos().y() - visible.y()) > 6 * visible.height():
                    item.base = None  # free memory far off screen
                    item.detail = None
                continue
            if item.index >= doc.page_count:
                continue
            page = doc[item.index]
            longest = max(item.width, item.height)
            base_scale = min(desired, MAX_BASE_PIXELS / longest)
            if item.stale or item.base is None or abs(item.base_scale - base_scale) > 0.15 * base_scale:
                pix = page.get_pixmap(matrix=fitz.Matrix(base_scale, base_scale), alpha=False)
                item.base, item.base_scale = pixmap_to_qimage(pix), base_scale
                item.detail = None
            if desired > base_scale * 1.1:
                local = visible.intersected(page_rect).translated(-item.pos())
                if not local.isEmpty():
                    covered = item.detail is not None and item.detail_rect.contains(local) \
                        and abs(item.detail_scale - desired) < 0.01 and not item.stale
                    if not covered:
                        margin = 0.25
                        local = local.adjusted(-local.width() * margin, -local.height() * margin,
                                               local.width() * margin, local.height() * margin)
                        local = local.intersected(QRectF(0, 0, item.width, item.height))
                        clip = fitz.Rect(local.left(), local.top(), local.right(), local.bottom())
                        pix = page.get_pixmap(matrix=fitz.Matrix(desired, desired), clip=clip,
                                              alpha=False)
                        item.detail = pixmap_to_qimage(pix)
                        item.detail_rect = QRectF(clip.x0, clip.y0, clip.width, clip.height)
                        item.detail_scale = desired
            else:
                item.detail = None
            item.stale = False
            item.update()

    # -- zoom and navigation -------------------------------------------------
    def set_zoom(self, zoom: float, anchor: QPointF | None = None) -> None:
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        anchor_view = anchor if anchor is not None else QPointF(self.viewport().rect().center())
        scene_point = self.mapToScene(anchor_view.toPoint())
        self.resetTransform()
        self.scale(zoom, zoom)
        self.zoom = zoom
        # Keep the point under the cursor (or centre) fixed on screen.
        new_view = self.mapFromScene(scene_point)
        delta = new_view - anchor_view.toPoint()
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + delta.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + delta.y())
        self.refresh_overlays(document=False)
        self.zoomChanged.emit(zoom)
        self.schedule_render()

    def zoom_by(self, factor: float, anchor: QPointF | None = None) -> None:
        self.fit_mode = ""
        self.set_zoom(self.zoom * factor, anchor)

    def fit(self, mode: str) -> None:
        self.fit_mode = mode
        self._apply_fit()

    def _apply_fit(self) -> None:
        if not self.fit_mode or not self.pages:
            if self.pages:
                self.set_zoom(self.zoom)
            return
        page = self.current_page
        item = next((p for p in self.pages if p.index == page), self.pages[0])
        view = self.viewport().rect()
        zoom = (view.width() - 30) / (item.width + 2 * PAGE_GAP)
        if self.fit_mode == "page":
            zoom = min(zoom, (view.height() - 20) / (item.height + 2 * PAGE_GAP))
        # Zooming scrolls, which would change current_page; return to the same page.
        self.set_zoom(max(MIN_ZOOM, zoom))
        self.go_to_page(page)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.fit_mode:
            self._apply_fit()
        self.schedule_render()

    def go_to_page(self, index: int, point: fitz.Point | None = None) -> None:
        index = max(0, min(index, self.session.doc.page_count - 1))
        if self.single_page and index != self.current_page:
            self.current_page = index
            self.layout_pages()
        self.current_page = index
        item = self.page_item(index)
        if item is None:
            return
        if point is None:
            target = item.pos() + QPointF(0, -PAGE_GAP / 2)
            self.verticalScrollBar().setValue(int(self.mapFromScene(target).y()
                                                  + self.verticalScrollBar().value()))
        else:
            self.centerOn(item.pos() + self.to_visual(index, point))
        self.pageChanged.emit(index)
        self.schedule_render()

    def page_item(self, index: int) -> PageItem | None:
        return next((item for item in self.pages if item.index == index), None)

    def page_at_viewport_center(self) -> int:
        centre = self.mapToScene(self.viewport().rect().center())
        best, distance = self.current_page, float("inf")
        for item in self.pages:
            top, bottom = item.pos().y(), item.pos().y() + item.height
            d = 0 if top <= centre.y() <= bottom else min(abs(centre.y() - top),
                                                           abs(centre.y() - bottom))
            if d < distance:
                best, distance = item.index, d
        return best

    def set_single_page(self, single: bool) -> None:
        self.single_page = single
        self.layout_pages()
        self.go_to_page(self.current_page)

    # -- coordinates ---------------------------------------------------------
    def to_page(self, index: int, visual: QPointF) -> fitz.Point:
        """Visible page point -> unrotated page coordinates."""
        return fitz.Point(visual.x(), visual.y()) * self.page(index).derotation_matrix

    def to_visual(self, index: int, point) -> QPointF:
        visual = fitz.Point(point) * self.page(index).rotation_matrix
        return QPointF(visual.x, visual.y)

    def visual_rect(self, index: int, rect: fitz.Rect) -> QRectF:
        visual = fitz.Rect(rect) * self.page(index).rotation_matrix
        return QRectF(visual.x0, visual.y0, visual.width, visual.height)

    def hit_page(self, view_pos, clamp: bool = False) -> tuple[int, QPointF] | None:
        """Page under a viewport position and the point on its visible page."""
        scene = self.mapToScene(view_pos.toPoint() if hasattr(view_pos, "toPoint") else view_pos)
        return self.hit_scene(scene, clamp)

    def hit_scene(self, scene: QPointF, clamp: bool = False) -> tuple[int, QPointF] | None:
        """Page containing a scene point and the point on its visible page."""
        for item in self.pages:
            local = scene - item.pos()
            if 0 <= local.x() <= item.width and 0 <= local.y() <= item.height:
                return item.index, local
        if clamp and self._points_page >= 0:
            item = self.page_item(self._points_page)
            if item is not None:
                local = scene - item.pos()
                return item.index, QPointF(min(max(local.x(), 0), item.width),
                                           min(max(local.y(), 0), item.height))
        return None

    def _clamp_to_page(self, index: int, scene: QPointF) -> QPointF:
        item = self.page_item(index)
        local = scene - item.pos()
        return QPointF(min(max(local.x(), 0), item.width), min(max(local.y(), 0), item.height))

    # -- snapping ----------------------------------------------------------------
    def _snappable(self) -> bool:
        if not self.snap_enabled or self._modifiers() & Qt.KeyboardModifier.ShiftModifier:
            return False
        if self.tool == "select":
            return bool(self._drag and self._drag.get("mode") == "handle")
        kind = self._drawing_kind()
        return kind not in markups.FREEHAND and kind not in markups.TEXT_MARKUP_KINDS \
            and self.tool not in ("pan", "text_select", "text", "zoom_area", "snapshot")

    def _page_snap_index(self, index: int) -> snap.SnapIndex:
        page = self.page(index)
        doc = self.session.doc
        # Content points only change when the page's drawing does; key them on
        # the content streams, rotation, and crop box instead of every edit.
        key = (page.rotation, tuple(page.cropbox),
               tuple(len(doc.xref_stream_raw(x) or b"") for x in page.get_contents()))
        cached = self._snap_content.get(index)
        if cached is None or cached[0] != key:
            try:
                cached = (key, snap.content_points(doc, index))
            except Exception:
                cached = (key, [])
            self._snap_content[index] = cached
            self._snap_index.pop(index, None)
        if index not in self._snap_index:
            self._snap_index[index] = snap.SnapIndex(
                cached[1] + snap.markup_points(page, self._snap_exclude))
        return self._snap_index[index]

    def _snap(self, scene: QPointF) -> QPointF:
        """Move a scene point onto nearby linework and show what it snapped to."""
        self._last_snap = None
        self._clear_items(self._snap_items)
        if not self._snappable():
            return scene
        hit = self.hit_scene(scene, clamp=bool(self._points))
        if hit is None:
            return scene
        index, local = hit
        if self._points and index != self._points_page:
            return scene
        found = self._page_snap_index(index).nearest(self.to_page(index, local), 9 / self.zoom)
        if found is None:
            return scene
        self._last_snap = found
        snapped = self.page_item(index).pos() + self.to_visual(index, found[0])
        self._show_snap_marker(snapped, found[1])
        return snapped

    def _show_snap_marker(self, scene: QPointF, kind: str) -> None:
        colour = QColor("#e8590c")
        marker = QGraphicsRectItem(-5, -5, 10, 10)
        marker.setPen(QPen(colour, 2))
        marker.setBrush(Qt.BrushStyle.NoBrush)
        marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        marker.setPos(scene)
        marker.setZValue(60)
        label = QGraphicsSimpleTextItem(kind)
        label.setBrush(colour)
        label.setFont(QFont("Segoe UI", 8))
        label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        label.setPos(scene)
        label.setTransform(label.transform().translate(8, 6))
        label.setZValue(60)
        for item in (marker, label):
            self.scene().addItem(item)
            self._snap_items.append(item)

    def set_snap(self, enabled: bool) -> None:
        self.snap_enabled = enabled
        self._clear_items(self._snap_items)

    # -- tools -----------------------------------------------------------------
    def set_tool(self, tool: str) -> None:
        self.cancel_drawing()
        self.tool = tool
        self._restore_cursor()
        if tool != "select":
            self.set_selection([])
        if tool != "text_select":
            self._clear_items(self._text_items)

    def _restore_cursor(self) -> None:
        tool = self.tool
        if tool == "pan":
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        elif tool == "text_select":
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        elif tool == "select":
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def cancel_drawing(self) -> None:
        self._points = []
        self._points_page = -1
        self._drag = None
        if self._preview is not None:
            self.scene().removeItem(self._preview)
            self._preview = None

    def _drawing_kind(self) -> str:
        return {"calibrate": "line", "crop": "rect", "snapshot": "rect",
                "zoom_area": "rect"}.get(self.tool, self.tool)

    def _ensure_preview(self, kind: str) -> QGraphicsPathItem:
        if self._preview is None:
            self._preview = QGraphicsPathItem()
            pen = QPen(SELECT_COLOR, 0, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            pen.setWidthF(1.5)
            self._preview.setPen(pen)
            if kind in ("highlighter",):
                self._preview.setPen(QPen(QColor(255, 230, 0, 140), 12))
            self._preview.setZValue(50)
            self.scene().addItem(self._preview)
        return self._preview

    def _constrain(self, start: QPointF, end: QPointF) -> QPointF:
        """Shift (or ortho mode) snaps lines to 45-degree steps."""
        modifiers = self._modifiers()
        if self._last_snap is not None:
            return end  # an exact snap beats the 45-degree constraint
        if not (self.ortho or modifiers & Qt.KeyboardModifier.ShiftModifier):
            return end
        delta = end - start
        length = math.hypot(delta.x(), delta.y())
        angle = round(math.atan2(delta.y(), delta.x()) / (math.pi / 4)) * (math.pi / 4)
        return start + QPointF(math.cos(angle) * length, math.sin(angle) * length)

    def _modifiers(self):
        return QApplication.keyboardModifiers()

    def _update_preview(self, scene_end: QPointF) -> None:
        drag = self._drag
        kind = self._drawing_kind()
        if kind in markups.CLICK_POINTS:
            if not self._points:
                return
            item = self.page_item(self._points_page)
            path = QPainterPath(self._points[0] + item.pos())
            for point in self._points[1:]:
                path.lineTo(point + item.pos())
            end = self._constrain(self._points[-1] + item.pos(), scene_end)
            path.lineTo(end)
            if kind in markups.CLOSED_KINDS and len(self._points) > 1:
                path.lineTo(self._points[0] + item.pos())
            self._ensure_preview(kind).setPath(path)
            return
        if drag is None:
            return
        start = drag["scene_start"]
        if kind in markups.FREEHAND:
            path = QPainterPath(drag["path"][0])
            for point in drag["path"][1:]:
                path.lineTo(point)
        elif kind in markups.DRAG_LINE or kind == "line":
            path = QPainterPath(start)
            path.lineTo(self._constrain(start, scene_end))
        else:
            path = QPainterPath()
            rect = QRectF(start, scene_end).normalized()
            if kind == "ellipse":
                path.addEllipse(rect)
            else:
                path.addRect(rect)
        self._ensure_preview(kind).setPath(path)

    def finish_points(self) -> None:
        """Complete a click-points tool (double-click or Enter)."""
        kind = self._drawing_kind()
        if kind not in markups.CLICK_POINTS or self._points_page < 0:
            return
        points = [self.to_page(self._points_page, p) for p in self._points]
        # A double-click adds the same point twice; drop consecutive duplicates.
        unique = [points[0]] if points else []
        for point in points[1:]:
            if abs(point - unique[-1]) > 0.5:
                unique.append(point)
        page = self._points_page
        self.cancel_drawing()
        if kind == "angle":
            unique = unique[:3]
        self.createRequested.emit(kind, page, unique, "")

    # -- selection -----------------------------------------------------------
    def set_selection(self, selection: list[tuple[int, int]]) -> None:
        self.selection = list(dict.fromkeys(selection))
        self.refresh_overlays(document=False)
        self.selectionChanged.emit()

    def _clear_items(self, items: list) -> None:
        for item in items:
            if item.scene() is not None:
                self.scene().removeItem(item)
        items.clear()

    def refresh_overlays(self, document: bool = True) -> None:
        """Redraw selection handles; with document=True also labels and search hits.

        Labels ignore view transforms and search rectangles scale with the view,
        so zooming and selecting only need the (screen-sized) handles redrawn.
        """
        self._clear_items(self._overlay)
        scene = self.scene()
        handle = HANDLE / max(self.zoom, 0.01)
        doc = self.session.doc
        for index, xref in self.selection:
            item = self.page_item(index)
            if item is None or index >= doc.page_count:
                continue
            page = doc[index]
            try:
                bounds = markups.markup_bounds(page, xref)
                annot = page.load_annot(xref)
            except Exception:
                continue
            rect = self.visual_rect(index, bounds).translated(item.pos())
            pen = QPen(SELECT_COLOR, 1.2, Qt.PenStyle.DashLine)
            pen.setCosmetic(True)
            outline = scene.addRect(rect, pen)
            outline.setZValue(40)
            self._overlay.append(outline)
            if len(self.selection) != 1:
                continue
            handles = []
            if annot.type[0] in (fitz.PDF_ANNOT_LINE, fitz.PDF_ANNOT_POLYGON,
                                 fitz.PDF_ANNOT_POLY_LINE):
                for number, point in enumerate(markups.markup_points(page, xref)):
                    handles.append(("vertex", number, self.to_visual(index, point) + item.pos()))
            elif annot.type[0] not in (fitz.PDF_ANNOT_TEXT, fitz.PDF_ANNOT_INK) and \
                    markups.markup_kind(page, annot) != "count":
                for name, point in (("tl", rect.topLeft()), ("tr", rect.topRight()),
                                    ("bl", rect.bottomLeft()), ("br", rect.bottomRight()),
                                    ("t", QPointF(rect.center().x(), rect.top())),
                                    ("b", QPointF(rect.center().x(), rect.bottom())),
                                    ("l", QPointF(rect.left(), rect.center().y())),
                                    ("r", QPointF(rect.right(), rect.center().y()))):
                    handles.append(("corner", name, point))
            for kind, key, point in handles:
                square = QRectF(point.x() - handle / 2, point.y() - handle / 2, handle, handle)
                handle_pen = QPen(SELECT_COLOR, 1)
                handle_pen.setCosmetic(True)
                shape = scene.addRect(square, handle_pen, QBrush(Qt.GlobalColor.white))
                shape.setZValue(41)
                shape.setData(0, (kind, key, index, xref))
                self._overlay.append(shape)
        if document:
            self._clear_items(self._labels)
            self._draw_measure_labels()
            self._redraw_search()

    def _draw_measure_labels(self) -> None:
        doc = self.session.doc
        font = QFont("Segoe UI", 9)
        font.setBold(True)
        for item in self.pages:
            if item.index >= doc.page_count:
                continue
            page = doc[item.index]
            for annot in page.annots() or []:
                kind = markups.markup_kind(page, annot)
                if kind not in markups.MEASURE_KINDS or kind in ("length", "count"):
                    continue
                result = markups.measurement(page, annot.xref)
                if not result:
                    continue
                anchor = self.to_visual(item.index, markups.label_anchor(page, annot.xref))
                text = QGraphicsSimpleTextItem(result[1])
                text.setFont(font)
                text.setBrush(QColor("#0b3d91"))
                text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
                bounds = text.boundingRect()
                text.setPos(anchor + item.pos())
                text.setZValue(31)
                box = QGraphicsRectItem(bounds.adjusted(-3, -1, 3, 1))
                box.setBrush(QColor(255, 255, 255, 220))
                box.setPen(QPen(QColor("#0b3d91"), 0))
                box.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
                box.setPos(anchor + item.pos())
                box.setZValue(30)
                self.scene().addItem(box)
                self.scene().addItem(text)
                self._labels.extend((box, text))

    def _handle_at(self, scene_point: QPointF):
        for item in self.scene().items(scene_point):
            data = item.data(0)
            if data:
                return data
        return None

    # -- search and text highlights -----------------------------------------
    def show_search_hits(self, hits: list[tuple[int, fitz.Rect]], current: int = -1) -> None:
        self._search_hits = hits
        self._search_current = current
        self._redraw_search()
        if 0 <= current < len(hits):
            page, rect = hits[current]
            self.go_to_page(page, rect.tl)

    def _redraw_search(self) -> None:
        self._clear_items(self._search_items)
        for number, (index, rect) in enumerate(getattr(self, "_search_hits", [])):
            item = self.page_item(index)
            if item is None:
                continue
            colour = QColor(255, 120, 0, 110) if number == getattr(self, "_search_current", -1) \
                else QColor(255, 220, 0, 90)
            shape = self.scene().addRect(self.visual_rect(index, rect).translated(item.pos()),
                                         QPen(Qt.PenStyle.NoPen), QBrush(colour))
            shape.setZValue(20)
            self._search_items.append(shape)

    def _words(self, index: int) -> list:
        if index not in self._words_cache:
            self._words_cache[index] = markups.page_words(self.page(index), sort=True)
        return self._words_cache[index]

    def _select_text(self, index: int, start: QPointF, end: QPointF) -> None:
        self._clear_items(self._text_items)
        a, b = self.to_page(index, start), self.to_page(index, end)
        area = fitz.Rect(a, b).normalize()
        chosen = [word for word in self._words(index) if fitz.Rect(word[:4]).intersects(area)]
        item = self.page_item(index)
        for word in chosen:
            rect = self.visual_rect(index, fitz.Rect(word[:4])).translated(item.pos())
            shape = self.scene().addRect(rect, QPen(Qt.PenStyle.NoPen),
                                         QBrush(QColor(22, 131, 216, 80)))
            shape.setZValue(21)
            self._text_items.append(shape)
        lines: dict[tuple, list[str]] = {}
        for word in chosen:
            lines.setdefault((word[5], word[6]), []).append(word[4])
        self._selected_text = "\n".join(" ".join(words) for words in lines.values())
        self.textSelected.emit(self._selected_text)

    @property
    def selected_text(self) -> str:
        return self._selected_text

    # -- mouse and keyboard ----------------------------------------------------
    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            super().wheelEvent(event)  # Ctrl+wheel scrolls
            return
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            bar = self.horizontalScrollBar()
            bar.setValue(bar.value() - event.angleDelta().y())
            return
        delta = event.angleDelta().y()
        if delta:
            self.zoom_by(1.18 ** (delta / 120.0), event.position())
        event.accept()

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        position = event.position()
        scene = self.mapToScene(position.toPoint())
        panning = (event.button() == Qt.MouseButton.MiddleButton
                   or (event.button() == Qt.MouseButton.LeftButton
                       and (self.tool == "pan" or self._space_down)))
        if panning:
            self._pan_origin = (position, self.horizontalScrollBar().value(),
                                self.verticalScrollBar().value())
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() == Qt.MouseButton.RightButton:
            if self._points:
                self.finish_points()
                return
            hit = self.hit_page(position)
            if hit and self.tool == "select":
                index, local = hit
                xref = markups.markup_at(self.page(index), self.to_page(index, local),
                                         4 / self.zoom)
                if xref and (index, xref) not in self.selection:
                    self.set_selection([(index, xref)])
                self.contextRequested.emit(event.globalPosition().toPoint(), index,
                                           self.to_page(index, local))
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        kind = self._drawing_kind()
        if self.tool != "select":
            scene = self._snap(scene)
        else:
            self._last_snap = None
        if self.tool == "select":
            self._press_select(event, scene, position)
            return
        if self.tool == "text_select":
            hit = self.hit_page(position)
            if hit:
                self._drag = {"mode": "text", "page": hit[0], "start": hit[1]}
            return
        if kind in markups.CLICK_POINTS:
            hit = self.hit_scene(scene, clamp=bool(self._points))
            if hit is None:
                return
            index, local = hit
            if self._points and index != self._points_page:
                return
            if not self._points:
                self._points_page = index
            elif self._points:
                item = self.page_item(index)
                local = self._constrain(self._points[-1] + item.pos(), scene) - item.pos()
            self._points.append(local)
            if kind == "angle" and len(self._points) == 3:
                self.finish_points()
            return
        hit = self.hit_scene(scene)
        if hit is None:
            return
        index, local = hit
        if kind in markups.SINGLE_CLICK:
            self.createRequested.emit(kind, index, [self.to_page(index, local)], "")
            return
        self._drag = {"mode": "draw", "page": index, "start": local, "scene_start": scene,
                      "path": [scene]}

    def _press_select(self, event, scene: QPointF, position) -> None:
        handle = self._handle_at(scene)
        if handle:
            kind, key, index, xref = handle
            bounds = markups.markup_bounds(self.page(index), xref)
            # Do not snap a vertex onto the markup being reshaped.
            self._snap_exclude = {xref}
            self._snap_index.pop(index, None)
            self._drag = {"mode": "handle", "kind": kind, "key": key, "page": index,
                          "xref": xref, "bounds": bounds, "scene_start": scene,
                          "points": markups.markup_points(self.page(index), xref)}
            return
        hit = self.hit_page(position)
        if hit is None:
            self.set_selection([])
            return
        index, local = hit
        point = self.to_page(index, local)
        xref = markups.markup_at(self.page(index), point, 4 / self.zoom)
        additive = bool(event.modifiers() & (Qt.KeyboardModifier.ShiftModifier
                                             | Qt.KeyboardModifier.ControlModifier))
        if xref:
            if additive:
                current = list(self.selection)
                if (index, xref) in current:
                    current.remove((index, xref))
                else:
                    current.append((index, xref))
                self.set_selection(current)
            elif (index, xref) not in self.selection:
                self.set_selection([(index, xref)])
            same_page = [x for p, x in self.selection if p == index]
            if (index, xref) in self.selection:
                self._drag = {"mode": "move", "page": index, "xrefs": same_page,
                              "scene_start": scene, "start": point}
            return
        if not additive:
            self.set_selection([])
        self._drag = {"mode": "box", "page": index, "start": local, "scene_start": scene}

    def mouseMoveEvent(self, event) -> None:
        position = event.position()
        scene = self.mapToScene(position.toPoint())
        if self._pan_origin is not None:
            origin, h, v = self._pan_origin
            delta = position - origin
            self.horizontalScrollBar().setValue(int(h - delta.x()))
            self.verticalScrollBar().setValue(int(v - delta.y()))
            return
        if self._snappable():
            scene = self._snap(scene)
        else:
            self._last_snap = None
            if self._snap_items:
                self._clear_items(self._snap_items)
        if self._points:
            self._update_preview(scene)
            return
        drag = self._drag
        if drag is None:
            if self.tool == "select":
                handle = self._handle_at(scene)
                cursor = Qt.CursorShape.ArrowCursor
                if handle:
                    cursor = Qt.CursorShape.SizeAllCursor if handle[0] == "vertex" else {
                        "tl": Qt.CursorShape.SizeFDiagCursor, "br": Qt.CursorShape.SizeFDiagCursor,
                        "tr": Qt.CursorShape.SizeBDiagCursor, "bl": Qt.CursorShape.SizeBDiagCursor,
                        "t": Qt.CursorShape.SizeVerCursor, "b": Qt.CursorShape.SizeVerCursor,
                        "l": Qt.CursorShape.SizeHorCursor, "r": Qt.CursorShape.SizeHorCursor,
                    }[handle[1]]
                self.viewport().setCursor(cursor)
            return
        mode = drag["mode"]
        if mode == "draw":
            drag["path"].append(scene)
            self._update_preview(scene)
        elif mode == "text":
            self._select_text(drag["page"], drag["start"], self._clamp_to_page(drag["page"], scene))
        elif mode in ("move", "handle", "box"):
            self._update_select_preview(drag, scene)

    def _update_select_preview(self, drag: dict, scene: QPointF) -> None:
        item = self.page_item(drag["page"])
        path = QPainterPath()
        if drag["mode"] == "box":
            path.addRect(QRectF(drag["scene_start"], scene).normalized())
        elif drag["mode"] == "move":
            delta = scene - drag["scene_start"]
            for xref in drag["xrefs"]:
                bounds = markups.markup_bounds(self.page(drag["page"]), xref)
                path.addRect(self.visual_rect(drag["page"], bounds).translated(item.pos() + delta))
        else:
            new = self._handle_result(drag, scene)
            if isinstance(new, fitz.Rect):
                path.addRect(self.visual_rect(drag["page"], new).translated(item.pos()))
            else:
                visual = [self.to_visual(drag["page"], p) + item.pos() for p in new]
                path.moveTo(visual[0])
                for point in visual[1:]:
                    path.lineTo(point)
                # Keep the page referenced while using its annotation: PyMuPDF
                # frees annotations together with their page.
                page = self.page(drag["page"])
                annot = page.load_annot(drag["xref"])
                if annot.type[0] == fitz.PDF_ANNOT_POLYGON:
                    path.closeSubpath()
        self._ensure_preview("select").setPath(path)

    def _handle_result(self, drag: dict, scene: QPointF):
        """New rect (corner handle) or vertex list (vertex handle) for a handle drag."""
        index = drag["page"]
        item = self.page_item(index)
        point = self.to_page(index, self._clamp_to_page(index, scene))
        if drag["kind"] == "vertex":
            points = list(drag["points"])
            number = drag["key"]
            if self._modifiers() & Qt.KeyboardModifier.ShiftModifier or self.ortho:
                neighbour = points[number - 1] if number > 0 else points[min(1, len(points) - 1)]
                start = self.to_visual(index, neighbour) + item.pos()
                point = self.to_page(index, self._constrain(start, scene) - item.pos())
            points[number] = point
            return points
        # Corner handles work on the visible rectangle so rotation feels natural.
        bounds = self.visual_rect(index, drag["bounds"])
        local = self._clamp_to_page(index, scene)
        left, top, right, bottom = bounds.left(), bounds.top(), bounds.right(), bounds.bottom()
        key = drag["key"]
        if "l" in key:
            left = local.x()
        if "r" in key:
            right = local.x()
        if "t" in key:
            top = local.y()
        if "b" in key:
            bottom = local.y()
        visual = fitz.Rect(left, top, right, bottom).normalize()
        return visual * self.page(index).derotation_matrix

    def mouseReleaseEvent(self, event) -> None:
        position = event.position()
        scene = self.mapToScene(position.toPoint())
        if self._pan_origin is not None and event.button() in (Qt.MouseButton.MiddleButton,
                                                               Qt.MouseButton.LeftButton):
            self._pan_origin = None
            self._restore_cursor()
            return
        if self._snappable():
            scene = self._snap(scene)
        else:
            self._last_snap = None
        self._clear_items(self._snap_items)
        if self._snap_exclude:
            self._snap_exclude = set()
            self._snap_index.clear()
        drag, self._drag = self._drag, None
        if self._preview is not None and not self._points:
            self.scene().removeItem(self._preview)
            self._preview = None
        if drag is None or event.button() != Qt.MouseButton.LeftButton:
            return
        index = drag["page"]
        mode = drag["mode"]
        moved = (scene - drag["scene_start"]).manhattanLength() * self.zoom > 3 \
            if "scene_start" in drag else True
        if mode == "move":
            if moved:
                end = self.to_page(index, self._clamp_to_page(index, scene))
                start = drag["start"]
                self.moveRequested.emit(index, drag["xrefs"], end.x - start.x, end.y - start.y)
            return
        if mode == "handle":
            if moved:
                result = self._handle_result(drag, scene)
                if isinstance(result, fitz.Rect):
                    self.resizeRequested.emit(index, drag["xref"], result)
                else:
                    self.pointsRequested.emit(index, drag["xref"], result)
            return
        if mode == "box":
            if moved:
                end = self._clamp_to_page(index, scene)
                rect = fitz.Rect(self.to_page(index, drag["start"]), self.to_page(index, end))
                found = markups.markups_in_rect(self.page(index), rect.normalize())
                self.set_selection(list(self.selection) + [(index, x) for x in found])
            return
        if mode == "text":
            return
        # Drawing tools.
        kind = self._drawing_kind()
        end_local = self._clamp_to_page(index, scene)
        start_page = self.to_page(index, drag["start"])
        if kind in markups.FREEHAND:
            points = [self.to_page(index, self._clamp_to_page(index, p)) for p in drag["path"]]
            thinned = [points[0]]
            for point in points[1:]:
                if abs(point - thinned[-1]) * self.zoom >= 2:
                    thinned.append(point)
            if len(thinned) >= 2:
                self.createRequested.emit(kind, index, thinned, "")
            return
        if kind in markups.DRAG_LINE or kind == "line":
            end_scene = self._constrain(drag["scene_start"], scene)
            end = self.to_page(index, self._clamp_to_page(index, end_scene))
            if not moved:
                return
            if self.tool == "calibrate":
                self.lineRequested.emit("calibrate", index, [start_page, end])
            elif kind == "callout":
                self._open_editor(index, "callout", [start_page, end],
                                  QRectF(end_scene.x(), end_scene.y() - 18, 170, 46))
            else:
                self.createRequested.emit(kind, index, [start_page, end], "")
            return
        end = self.to_page(index, end_local)
        rect = fitz.Rect(start_page, end).normalize()
        if self.tool in ("crop", "snapshot", "zoom_area"):
            if moved:
                if self.tool == "zoom_area":
                    self._zoom_to(index, rect)
                else:
                    self.areaRequested.emit(self.tool, index, rect)
            return
        if kind == "text":
            visual = QRectF(drag["scene_start"], scene).normalized()
            if visual.width() * self.zoom < 40 or visual.height() * self.zoom < 24:
                visual = QRectF(drag["scene_start"], drag["scene_start"] + QPointF(
                    220 / max(self.zoom, 0.2), 70 / max(self.zoom, 0.2)))
            item = self.page_item(index)
            local = visual.translated(-item.pos()).intersected(
                QRectF(0, 0, item.width, item.height))
            box = fitz.Rect(local.left(), local.top(), local.right(), local.bottom())
            box = box * self.page(index).derotation_matrix
            self._open_editor(index, "text", [box.tl, box.br],
                              local.translated(item.pos()))
            return
        if not moved and kind != "stamp":
            return
        self.createRequested.emit(kind, index, [start_page, end], "")

    def _zoom_to(self, index: int, rect: fitz.Rect) -> None:
        visual = self.visual_rect(index, rect)
        view = self.viewport().rect()
        if visual.width() < 2 or visual.height() < 2:
            return
        self.fit_mode = ""
        self.set_zoom(min(view.width() / visual.width(), view.height() / visual.height()) * 0.95)
        self.centerOn(visual.center() + self.page_item(index).pos())

    def _open_editor(self, index: int, kind: str, points: list, scene_rect: QRectF) -> None:
        editor = InlineTextEditor(self.viewport())
        top_left = self.mapFromScene(scene_rect.topLeft())
        bottom_right = self.mapFromScene(scene_rect.bottomRight())
        editor.setGeometry(top_left.x(), top_left.y(), max(120, bottom_right.x() - top_left.x()),
                           max(48, bottom_right.y() - top_left.y()))
        editor.committed.connect(lambda text: self.createRequested.emit(kind, index, points, text))
        editor.show()
        editor.setFocus()
        self.editor = editor

    def mouseDoubleClickEvent(self, event) -> None:
        if self._points:
            self.finish_points()
            return
        if self.tool == "select":
            hit = self.hit_page(event.position())
            if hit:
                index, local = hit
                xref = markups.markup_at(self.page(index), self.to_page(index, local),
                                         4 / self.zoom)
                if xref:
                    self.set_selection([(index, xref)])
                    self.editTextRequested.emit(index, xref)
                    return
        if self.tool == "text_select":
            hit = self.hit_page(event.position())
            if hit:
                index, local = hit
                point = self.to_page(index, local)
                for word in self._words(index):
                    if fitz.Rect(word[:4]).contains(point):
                        rect = self.visual_rect(index, fitz.Rect(word[:4]))
                        self._select_text(index, rect.topLeft(), rect.bottomRight())
                        return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = True
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if self._points:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.finish_points()
                return
            if key == Qt.Key.Key_Backspace:
                self._points.pop()
                if not self._points:
                    self.cancel_drawing()
                else:
                    self._update_preview(self.mapToScene(self.mapFromGlobal(QCursor.pos())))
                return
            if key == Qt.Key.Key_Escape:
                self.cancel_drawing()
                return
        if key == Qt.Key.Key_Escape:
            self.cancel_drawing()
            if self.selection:
                self.set_selection([])
            self.toolFinished.emit()
            return
        if self.tool == "select" and self.selection:
            if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self.deleteRequested.emit()
                return
            step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
            moves = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                     Qt.Key.Key_Up: (0, -step), Qt.Key.Key_Down: (0, step)}
            if key in moves:
                dx, dy = moves[key]
                page = self.selection[0][0]
                # Arrow keys move in screen directions; convert to page space.
                origin = self.to_page(page, QPointF(0, 0))
                moved = self.to_page(page, QPointF(dx, dy))
                self.moveRequested.emit(page, [x for p, x in self.selection if p == page],
                                        moved.x - origin.x, moved.y - origin.y)
                return
        if event.matches(QKeySequence.StandardKey.Copy) and self.tool == "text_select":
            QApplication.clipboard().setText(self._selected_text)
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._space_down = False
            self._restore_cursor()
            return
        super().keyReleaseEvent(event)

    def render_area(self, index: int, rect: fitz.Rect, dpi: int = 200) -> QPixmap:
        """Render part of a page (unrotated rect) for snapshots."""
        page = self.page(index)
        clip = fitz.Rect(rect) * page.rotation_matrix
        pix = page.get_pixmap(dpi=dpi, clip=clip.normalize(), alpha=False)
        return QPixmap.fromImage(pixmap_to_qimage(pix))

    def page_thumbnail(self, index: int, size: int = 160) -> QPixmap:
        page = self.page(index)
        scale = size / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return QPixmap.fromImage(pixmap_to_qimage(pix))

