# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Page edits, exports, text replacement, forms, flattening, and redaction."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymupdf as fitz
import pytest

import pdf_operations as ops
from markups import create_markup


def make_pdf(path: Path, widths=(612, 612, 612), password: str | None = None,
             text: str = "Original wording") -> None:
    doc = fitz.open()
    for index, width in enumerate(widths):
        page = doc.new_page(width=width, height=792)
        page.insert_text((72, 100), f"{text} {index + 1}", fontsize=14)
    options = {}
    if password:
        options.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
    doc.save(path, **options)
    doc.close()


def widths(doc: fitz.Document) -> list[float]:
    return [doc[i].rect.width for i in range(doc.page_count)]


@pytest.fixture
def doc(tmp_path: Path):
    source = tmp_path / "source.pdf"
    make_pdf(source, (100, 200, 300))
    opened = ops.open_pdf(source)
    yield opened
    opened.close()


def test_open_rejects_malformed_and_locked_files(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a PDF")
    with pytest.raises(ValueError, match="Could not open PDF"):
        ops.open_pdf(bad)
    locked = tmp_path / "locked.pdf"
    make_pdf(locked, password="secret")
    with pytest.raises(PermissionError):
        ops.open_pdf(locked)
    ops.open_pdf(locked, "secret").close()


def test_rotate_delete_reorder_and_crop(doc: fitz.Document) -> None:
    ops.rotate_page(doc, 1)
    assert doc[1].rotation == 90
    ops.rotate_page(doc, 1, 270)
    assert doc[1].rotation == 0
    ops.reorder_pages(doc, [2, 0, 1])
    assert widths(doc) == [300, 100, 200]
    with pytest.raises(ValueError):
        ops.reorder_pages(doc, [0, 0, 2])
    ops.crop_page(doc, 0, (10, 20, 90, 700))
    assert doc[0].cropbox == fitz.Rect(10, 20, 90, 700)
    with pytest.raises(ValueError):
        ops.crop_page(doc, 0, (50, 50, 51, 51))
    ops.reset_crop(doc, 0)
    assert doc[0].cropbox == doc[0].mediabox
    ops.delete_pages(doc, [0, 2])
    assert widths(doc) == [100]
    with pytest.raises(ValueError):
        ops.delete_pages(doc, [0])
    with pytest.raises(IndexError):
        ops.rotate_page(doc, 5)


def test_insert_replace_and_blank_pages(doc: fitz.Document, tmp_path: Path) -> None:
    other = tmp_path / "other.pdf"
    make_pdf(other, (400, 500))
    assert ops.insert_pages(doc, other, 1) == 2
    assert widths(doc) == [100, 400, 500, 200, 300]
    ops.replace_page(doc, 0, other, 1)
    assert widths(doc) == [500, 400, 500, 200, 300]
    ops.insert_blank_page(doc, 5)
    assert doc.page_count == 6 and not doc[5].get_text().strip()


def test_exports_write_new_files_only(doc: fitz.Document, tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    with pytest.raises(ValueError, match="source PDF remains unchanged"):
        ops.extract_pages(doc, [0], source, source=source)
    extracted = ops.extract_pages(doc, [2, 0], tmp_path / "extracted.pdf")
    with fitz.open(extracted) as result:
        assert widths(result) == [300, 100]
    split = ops.split_pdf(doc, tmp_path / "split", "sheet")
    assert [path.name for path in split] == ["sheet-0001.pdf", "sheet-0002.pdf", "sheet-0003.pdf"]
    merged = ops.merge_pdfs([extracted, split[1]], tmp_path / "merged.pdf")
    with fitz.open(merged) as result:
        assert widths(result) == [300, 100, 200]
    with pytest.raises(ValueError):
        ops.merge_pdfs([extracted], tmp_path / "one.pdf")
    text = ops.export_text(doc, tmp_path / "text.txt").read_text(encoding="utf-8")
    assert "--- Page 3 ---" in text and "Original wording 3" in text


def test_password_protected_copy(doc: fitz.Document, tmp_path: Path) -> None:
    protected = ops.protect_pdf(doc.tobytes(), tmp_path / "protected.pdf", "local-secret")
    with fitz.open(protected) as result:
        assert result.needs_pass and result.authenticate("local-secret")
        assert result.page_count == 3
    with pytest.raises(ValueError, match="non-empty"):
        ops.protect_pdf(doc.tobytes(), tmp_path / "empty.pdf", "  ")


def test_replace_existing_text(tmp_path: Path) -> None:
    make_pdf(tmp_path / "wide.pdf")
    doc = ops.open_pdf(tmp_path / "wide.pdf")
    assert ops.replace_existing_text(doc, 0, "Original wording 1", "Updated wording") == 1
    text = doc[0].get_text()
    assert "Updated wording" in text and "Original wording" not in text
    assert ops.replace_existing_text(doc, 1, "Original wording 2", "") == 1
    assert "Original wording" not in doc[1].get_text()
    with pytest.raises(ValueError, match="No exact match"):
        ops.replace_existing_text(doc, 2, "missing", "x")
    with pytest.raises(IndexError):
        ops.replace_existing_text(doc, 9, "Original", "x")
    doc.close()


def test_form_fields_can_be_read_and_filled(tmp_path: Path) -> None:
    source = tmp_path / "form.pdf"
    form = fitz.open()
    page = form.new_page()
    for name, kind, rect in (("Name", fitz.PDF_WIDGET_TYPE_TEXT, (72, 72, 300, 92)),
                             ("Approved", fitz.PDF_WIDGET_TYPE_CHECKBOX, (72, 110, 90, 128))):
        widget = fitz.Widget()
        widget.field_name, widget.field_type, widget.rect = name, kind, fitz.Rect(rect)
        page.add_widget(widget)
    form.save(source)
    form.close()
    doc = ops.open_pdf(source)
    try:
        fields = {field["name"]: field for field in ops.read_form_fields(doc)}
        assert fields["Name"]["type"] == "Text" and fields["Approved"]["type"] == "CheckBox"
        assert ops.fill_form_fields(doc, {"Name": "Jordan", "Approved": True}) == 2
        filled = {field["name"]: field["value"] for field in ops.read_form_fields(doc)}
        assert filled["Name"] == "Jordan"
        assert filled["Approved"] not in ("Off", False, "")
    finally:
        doc.close()


def test_flatten_and_redaction(doc: fitz.Document) -> None:
    page = doc[0]
    create_markup(page, "rectangle", [(20, 200), (80, 260)])
    ops.flatten_markups(doc)
    page = doc[0]
    assert not list(page.annots()) and page.get_drawings()
    page = doc[1]
    create_markup(page, "redact", [(60, 80), (200, 110)])
    assert ops.apply_redactions(doc) == 1
    page = doc[1]
    assert "Original wording" not in page.get_text()
    assert not list(page.annots())
