# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Page, export, form, and redaction operations for Northstar PDF.

Functions that take a ``doc`` change an open in-memory document and are meant
to run inside ``PdfSession.apply`` (or ``apply_copy``) so they are undoable.
Functions that take an ``output`` path write a new file and never modify the
source. PyMuPDF keeps bookmarks, links, and form fields when pages are
rotated, deleted, or reordered.

PyMuPDF is AGPL-3.0-or-later or commercially licensed by Artifex.
"""
from __future__ import annotations

import os
from pathlib import Path

import pymupdf as fitz


def open_pdf(path: str | Path, password: str | None = None) -> fitz.Document:
    """Open a PDF from bytes (so the file stays unlocked) and authenticate it."""
    try:
        doc = fitz.open(stream=Path(path).read_bytes(), filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Could not open PDF: {exc}") from exc
    if doc.needs_pass and not doc.authenticate(password or ""):
        doc.close()
        raise PermissionError("The PDF is encrypted; a valid password is required.")
    if doc.page_count == 0:
        doc.close()
        raise ValueError("The PDF contains no pages.")
    return doc


def _check_page(doc: fitz.Document, index: int) -> None:
    if not 0 <= index < doc.page_count:
        raise IndexError("Page index is outside the document.")


def _write_new(doc: fitz.Document, output: str | Path, password: str | None = None,
               sources: tuple[str | Path, ...] = ()) -> Path:
    output = Path(output).resolve()
    if output.suffix.lower() != ".pdf":
        raise ValueError("Output path must end in .pdf")
    if any(Path(source).resolve() == output for source in sources):
        raise ValueError("Choose a new output file so the source PDF remains unchanged.")
    output.parent.mkdir(parents=True, exist_ok=True)
    options = {"garbage": 3, "deflate": True}
    if password:
        options.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
    staging = output.with_name(output.stem + ".northstar-tmp.pdf")
    try:
        doc.save(str(staging), **options)
        os.replace(staging, output)
    finally:
        if staging.exists():
            staging.unlink()
    return output


# -- page edits on an open document --------------------------------------

def rotate_page(doc: fitz.Document, index: int, degrees: int = 90) -> None:
    if degrees % 90:
        raise ValueError("Rotation must be a multiple of 90 degrees.")
    _check_page(doc, index)
    page = doc[index]
    page.set_rotation((page.rotation + degrees) % 360)


def delete_pages(doc: fitz.Document, indices: list[int]) -> None:
    unique = sorted(set(indices))
    if not unique:
        raise ValueError("Select at least one page to delete.")
    for index in unique:
        _check_page(doc, index)
    if len(unique) >= doc.page_count:
        raise ValueError("Cannot delete every page in a PDF.")
    doc.delete_pages(unique)


def reorder_pages(doc: fitz.Document, order: list[int]) -> None:
    """Put pages in the given zero-based order; every page must appear once."""
    if sorted(order) != list(range(doc.page_count)):
        raise ValueError("Page order must list every page exactly once.")
    if order != list(range(doc.page_count)):
        doc.select(order)


def crop_page(doc: fitz.Document, index: int, rect: fitz.Rect | tuple) -> None:
    """Set the visible crop box using unrotated page coordinates (top-left origin).

    Content outside the box stays in the file; this is not redaction.
    """
    _check_page(doc, index)
    page = doc[index]
    rect = fitz.Rect(rect) & page.mediabox
    if rect.is_empty or rect.width < 4 or rect.height < 4:
        raise ValueError("Crop box must be inside the page and have a positive size.")
    page.set_cropbox(rect)


def reset_crop(doc: fitz.Document, index: int) -> None:
    _check_page(doc, index)
    page = doc[index]
    page.set_cropbox(page.mediabox)


def insert_pages(doc: fitz.Document, source: str | Path, before_page: int,
                 password: str | None = None) -> int:
    """Insert every page of another PDF; run through PdfSession.apply_copy."""
    if not 0 <= before_page <= doc.page_count:
        raise IndexError("Insertion position is outside the document.")
    other = open_pdf(source, password)
    try:
        doc.insert_pdf(other, start_at=before_page)
        return other.page_count
    finally:
        other.close()


def insert_blank_page(doc: fitz.Document, before_page: int) -> None:
    """Insert a blank page sized like its neighbour; run through apply_copy."""
    if not 0 <= before_page <= doc.page_count:
        raise IndexError("Insertion position is outside the document.")
    neighbour = doc[min(before_page, doc.page_count - 1)].rect
    doc.new_page(before_page, width=neighbour.width, height=neighbour.height)


def replace_page(doc: fitz.Document, index: int, source: str | Path,
                 source_page: int = 0, password: str | None = None) -> None:
    """Replace one page with a page from another PDF; run through apply_copy."""
    _check_page(doc, index)
    other = open_pdf(source, password)
    try:
        _check_page(other, source_page)
        doc.insert_pdf(other, from_page=source_page, to_page=source_page, start_at=index)
        doc.delete_page(index + 1)
    finally:
        other.close()


# -- exports to new files -------------------------------------------------

def extract_pages(doc: fitz.Document, indices: list[int], output: str | Path,
                  password: str | None = None, source: str | Path | None = None) -> Path:
    if not indices:
        raise ValueError("Select at least one page to extract.")
    for index in indices:
        _check_page(doc, index)
    new = fitz.open()
    try:
        for index in indices:
            new.insert_pdf(doc, from_page=index, to_page=index)
        return _write_new(new, output, password, (source,) if source else ())
    finally:
        new.close()


def split_pdf(doc: fitz.Document, directory: str | Path, stem: str = "page",
              password: str | None = None) -> list[Path]:
    """Write each page as its own PDF in a folder."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return [extract_pages(doc, [index], directory / f"{stem}-{index + 1:04d}.pdf", password)
            for index in range(doc.page_count)]


def merge_pdfs(sources: list[str | Path], output: str | Path) -> Path:
    if len(sources) < 2:
        raise ValueError("Choose at least two PDFs to merge.")
    merged = fitz.open()
    try:
        for source in sources:
            other = open_pdf(source)
            try:
                merged.insert_pdf(other)
            finally:
                other.close()
        return _write_new(merged, output, sources=tuple(sources))
    finally:
        merged.close()


def protect_pdf(pdf_bytes: bytes, output: str | Path, password: str,
                source: str | Path | None = None) -> Path:
    """Write an AES-256 password-protected copy of an unencrypted PDF byte string."""
    if not password or not password.strip():
        raise ValueError("Choose a non-empty password.")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return _write_new(doc, output, password, (source,) if source else ())
    finally:
        doc.close()


def export_text(doc: fitz.Document, output: str | Path) -> Path:
    output = Path(output)
    from markups import text_page
    parts = []
    for page in doc:
        parts.append(f"--- Page {page.number + 1} ---\n"
                     f"{page.get_text(textpage=text_page(page, fitz.TEXTFLAGS_TEXT))}")
    text = "\n\n".join(parts)
    output.write_text(text, encoding="utf-8")
    return output


# -- text, forms, flattening, redaction -----------------------------------

def replace_existing_text(doc: fitz.Document, page_index: int, old_text: str,
                          new_text: str) -> int:
    """Replace exact selectable page text using redaction.

    Font size and colour are taken from the first matching span. This does not
    reflow surrounding paragraphs or reconstruct scanned page text.
    """
    if not old_text.strip():
        raise ValueError("Enter the existing text to replace.")
    _check_page(doc, page_index)
    page = doc[page_index]
    rectangles = page.search_for(old_text)
    if not rectangles:
        raise ValueError("No exact match was found on the current page.")
    spans = [span for block in page.get_text("dict").get("blocks", [])
             for line in block.get("lines", []) for span in line.get("spans", [])]
    style = next((span for span in spans if old_text in span.get("text", "")), {})
    size = max(5.0, min(72.0, float(style.get("size", 11.0))))
    value = int(style.get("color", 0))
    color = ((value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255)
    for rect in rectangles:
        page.add_redact_annot(rect, fill=(1, 1, 1), cross_out=False)
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                          graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    if new_text:
        for rect in rectangles:
            page.insert_text((rect.x0, min(rect.y1 - 1, rect.y0 + size)), new_text,
                             fontsize=size, color=color, overlay=True)
    return len(rectangles)


def read_form_fields(doc: fitz.Document) -> list[dict]:
    """List fillable fields with their page, type, value, and choices."""
    fields = []
    for page in doc:
        for widget in page.widgets() or []:
            toggles = widget.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX,
                                            fitz.PDF_WIDGET_TYPE_RADIOBUTTON)
            fields.append({
                "page": page.number,
                "name": widget.field_name or "",
                "type": widget.field_type_string,
                "value": widget.field_value,
                "choices": list(widget.choice_values or []),
                "on_state": widget.on_state() if toggles else None,
            })
    return fields


def fill_form_fields(doc: fitz.Document, values: dict[str, object]) -> int:
    """Set field values by name. Checkboxes accept True/False. Returns fields changed."""
    changed = 0
    for page in doc:
        for widget in page.widgets() or []:
            if widget.field_name not in values:
                continue
            value = values[widget.field_name]
            if widget.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX,
                                     fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
                if isinstance(value, str):
                    value = value.strip().lower() not in ("", "off", "false", "no", "0")
                value = widget.on_state() if value else "Off"
            widget.field_value = value
            widget.update()
            changed += 1
    return changed


def flatten_markups(doc: fitz.Document) -> None:
    """Burn all markups into page content so they can no longer be edited."""
    doc.bake(annots=True, widgets=False)


def apply_redactions(doc: fitz.Document) -> int:
    """Permanently remove content under every redaction markup. Returns pages changed."""
    changed = 0
    for page in doc:
        if any(annot.type[0] == fitz.PDF_ANNOT_REDACT for annot in page.annots() or []):
            page.apply_redactions()
            changed += 1
    return changed
