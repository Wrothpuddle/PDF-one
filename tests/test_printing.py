# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Printing renders pages (optionally with markups) onto the printer."""
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication

from markups import Style, create_markup
from printing import PrintOptions, page_list, print_pages


def _printer(path: Path) -> QPrinter:
    QApplication.instance() or QApplication([])
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(path))
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.Letter))
    printer.setPageOrientation(QPageLayout.Orientation.Portrait)
    printer.setResolution(150)
    return printer


def _red_pixels(path: Path, page: int = 0) -> int:
    with fitz.open(path) as doc:
        pix = doc[page].get_pixmap(dpi=40)
        samples = pix.samples
        return sum(1 for i in range(0, len(samples), pix.n)
                   if samples[i] > 200 and samples[i + 1] < 80 and samples[i + 2] < 80)


def test_print_with_and_without_markups(tmp_path: Path) -> None:
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=1224, height=792)  # landscape tabloid sheet
    create_markup(doc[1], "rectangle", [(100, 100), (1100, 700)],
                  Style(stroke="#ff0000", fill="#ff0000"))
    with_markups, without = tmp_path / "with.pdf", tmp_path / "without.pdf"
    assert print_pages(doc, _printer(with_markups), [1, 2]) == 2
    print_pages(doc, _printer(without), [1], PrintOptions(markups=False))
    with fitz.open(with_markups) as result:
        assert result.page_count == 2
        assert result[0].rect.width < result[0].rect.height  # portrait paper
    assert _red_pixels(with_markups) > 100
    assert _red_pixels(without) == 0


def test_page_list_follows_print_range(tmp_path: Path) -> None:
    printer = _printer(tmp_path / "x.pdf")
    assert page_list(printer, 4, 2) == [0, 1, 2, 3]
    printer.setPrintRange(QPrinter.PrintRange.CurrentPage)
    assert page_list(printer, 4, 2) == [2]
    printer.setPrintRange(QPrinter.PrintRange.PageRange)
    printer.setFromTo(2, 3)
    assert page_list(printer, 4, 0) == [1, 2]
