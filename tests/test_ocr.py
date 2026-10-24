# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""OCR adds an invisible text layer that lines up with the scan."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymupdf as fitz
import pytest

import markups
import ocr
from pdf_document import PdfSession

TESSDATA = ocr.find_tessdata()
pytestmark = pytest.mark.skipif(TESSDATA is None, reason="run tools/get_tessdata.py first")


def _scan(path: Path, rotation: int) -> fitz.Rect:
    """A page that is only an image of text; returns where FOUNDATION appears on screen."""
    original = fitz.open()
    page = original.new_page(width=612, height=792)
    page.insert_text((72, 120), "GENERAL NOTES FOR FOUNDATION", fontsize=20)
    expected = page.search_for("FOUNDATION")[0]
    image = page.get_pixmap(dpi=150)
    scan = fitz.open()
    if rotation:
        # Stored sideways but displayed upright, like a rotated scanner page.
        sheet = scan.new_page(width=792, height=612)
        sheet.insert_image(sheet.rect, pixmap=image, rotate=90)
        sheet.set_rotation(90)
    else:
        sheet = scan.new_page(width=612, height=792)
        sheet.insert_image(sheet.rect, pixmap=image)
    scan.save(path)
    return expected


@pytest.mark.parametrize("rotation", [0, 90])
def test_ocr_text_is_searchable_and_aligned(tmp_path: Path, rotation: int) -> None:
    path = tmp_path / "scan.pdf"
    expected = _scan(path, rotation)
    session = PdfSession(path)
    try:
        assert not ocr.page_has_text(session.doc[0])
        added = session.apply_copy("OCR", lambda doc: ocr.ocr_page(doc[0], TESSDATA))
        assert added >= 4
        page = session.doc[0]
        assert ocr.page_has_text(page)
        words = [w[4] for w in markups.page_words(page)]
        assert "FOUNDATION" in words
        textpage = markups.text_page(page, fitz.TEXTFLAGS_SEARCH)
        (hit,) = page.search_for("FOUNDATION", textpage=textpage)
        shown = hit * page.rotation_matrix
        assert abs(shown.x0 - expected.x0) < 6 and abs(shown.x1 - expected.x1) < 6
        assert abs(shown.y1 - expected.y1) < 6
        # The text layer is invisible: the page looks the same as before.
        session.undo()
        assert not ocr.page_has_text(session.doc[0])
    finally:
        session.close()


def test_languages_lists_installed_models() -> None:
    assert "eng" in ocr.languages(TESSDATA)
