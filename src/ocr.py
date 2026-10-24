# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""OCR: add an invisible, searchable text layer to scanned pages.

MuPDF has Tesseract built in; it only needs a language model
("eng.traineddata", from the Apache-2.0 tessdata_fast project). The build
bundles one in a ``tessdata`` folder next to the program.

Each recognised word is written as invisible text (render mode 3) stretched
to the word's box, so search, text selection, and text-snapping highlights
line up with the scan. Pages must read upright on screen; rotate sideways
scans first.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pymupdf as fitz

import markups

MAX_PIXELS = 50_000_000  # cap for large sheets; Tesseract time grows with area
TESSDATA_SHA256 = {"eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"}


def find_tessdata() -> str | None:
    """Folder holding *.traineddata files, or None if OCR is unavailable."""
    candidates = [os.environ.get("TESSDATA_PREFIX", "")]
    for base in (getattr(sys, "_MEIPASS", ""), Path(sys.executable).parent,
                 Path(__file__).resolve().parent.parent):
        if str(base):
            candidates.append(str(Path(base) / "tessdata"))
    for folder in candidates:
        if folder and any(Path(folder).glob("*.traineddata")):
            return folder
    try:
        return fitz.get_tessdata()
    except Exception:
        return None


def languages(tessdata: str | None = None) -> list[str]:
    folder = tessdata or find_tessdata()
    if not folder:
        return []
    return sorted(path.stem for path in Path(folder).glob("*.traineddata") if path.stem != "osd")


def page_has_text(page: fitz.Page, minimum_words: int = 3) -> bool:
    return len(markups.page_words(page)) >= minimum_words


def ocr_page(page: fitz.Page, tessdata: str, language: str = "eng", dpi: int = 300) -> int:
    """Recognise the page's visible content and add invisible text. Returns words added.

    Run inside PdfSession.apply_copy: insert_text embeds its font through its own
    journal step, which cannot nest inside PdfSession.apply.
    """
    rect = page.rect
    pixels = (rect.width * dpi / 72) * (rect.height * dpi / 72)
    if pixels > MAX_PIXELS:
        dpi = int(dpi * (MAX_PIXELS / pixels) ** 0.5)
    pix = page.get_pixmap(dpi=dpi, annots=False)
    ocr_doc = fitz.open("pdf", pix.pdfocr_tobytes(language=language, tessdata=tessdata))
    try:
        ocr_page_ = ocr_doc[0]
        to_visual = fitz.Matrix(rect.width / ocr_page_.rect.width,
                                rect.height / ocr_page_.rect.height)
        sideways = page.rotation in (90, 270)
        added = 0
        for x0, y0, x1, y1, word, *_ in ocr_page_.get_text("words"):
            word = word.strip()
            box = fitz.Rect(x0, y0, x1, y1) * to_visual  # on the page as seen
            if not word or box.is_empty or box.height < 2:
                continue
            size = box.height * 0.8
            natural = fitz.get_text_length(word, "helv", size)
            stretch = box.width / natural if natural else 1.0
            origin = fitz.Point(box.x0, box.y1 - box.height * 0.2) * page.derotation_matrix
            # Stretch along the text direction, which is vertical on sideways pages.
            morph = fitz.Matrix(1, 0, 0, stretch, 0, 0) if sideways else \
                fitz.Matrix(stretch, 0, 0, 1, 0, 0)
            page.insert_text(origin, word, fontsize=size, fontname="helv", render_mode=3,
                             rotate=page.rotation, morph=(origin, morph))
            added += 1
        return added
    finally:
        ocr_doc.close()
