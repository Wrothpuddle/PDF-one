# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Printing: render pages (with or without markups) onto a QPrinter."""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf as fitz
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter, QTransform
from PySide6.QtPrintSupport import QPrinter

MAX_PIXELS = 40_000_000  # per page; keeps big sheets at sensible memory use
MAX_DPI = 300


@dataclass
class PrintOptions:
    markups: bool = True        # include annotations
    fit: bool = True            # scale to the printable area (else actual size)
    auto_rotate: bool = True    # turn landscape pages to fill portrait paper


def _render(page: fitz.Page, scale: float, markups: bool) -> QImage:
    longest = max(page.rect.width, page.rect.height) * scale
    shortest = min(page.rect.width, page.rect.height) * scale
    if longest * shortest > MAX_PIXELS:
        scale *= (MAX_PIXELS / (longest * shortest)) ** 0.5
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), annots=markups, alpha=False)
    return QImage(pix.samples, pix.width, pix.height, pix.stride,
                  QImage.Format.Format_RGB888).copy()


def print_pages(doc: fitz.Document, printer: QPrinter, pages: list[int],
                options: PrintOptions | None = None) -> int:
    """Print the given zero-based pages; returns how many were printed."""
    options = options or PrintOptions()
    painter = QPainter()
    if not painter.begin(printer):
        raise RuntimeError("Could not start printing.")
    printed = 0
    try:
        resolution = printer.resolution()
        for number, index in enumerate(pages):
            if number:
                printer.newPage()
            page = doc[index]
            area = QRectF(printer.pageRect(QPrinter.Unit.DevicePixel))
            area.moveTo(0, 0)
            width, height = page.rect.width, page.rect.height
            rotate = options.auto_rotate and (width > height) != (area.width() > area.height())
            if rotate:
                width, height = height, width
            points_to_device = resolution / 72
            if options.fit:
                scale = min(area.width() / (width * points_to_device),
                            area.height() / (height * points_to_device))
            else:
                scale = 1.0
            target_w = width * points_to_device * scale
            target_h = height * points_to_device * scale
            target = QRectF((area.width() - target_w) / 2 if options.fit else 0,
                            (area.height() - target_h) / 2 if options.fit else 0,
                            target_w, target_h)
            render_dpi = min(MAX_DPI, resolution) * scale
            image = _render(page, max(render_dpi / 72, 0.5), options.markups)
            if rotate:
                image = image.transformed(QTransform().rotate(90))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(target, image)
            printed += 1
    finally:
        painter.end()
    return printed


def page_list(printer: QPrinter, page_count: int, current: int) -> list[int]:
    """Pages chosen in a print dialog (all, a from-to range, or the current page)."""
    mode = printer.printRange()
    if mode == QPrinter.PrintRange.CurrentPage:
        return [current]
    if mode == QPrinter.PrintRange.PageRange and printer.fromPage() > 0:
        first = max(1, printer.fromPage())
        last = min(page_count, printer.toPage() or page_count)
        return list(range(first - 1, last))
    return list(range(page_count))

