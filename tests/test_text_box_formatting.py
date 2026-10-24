"""Tests for formatted text placement as visible selectable page content."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz
import pytest
from pypdf import PdfReader
from pdf_operations import add_rich_text_box


def make_pdf(path: Path, password: str | None = None) -> None:
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    options = {}
    if password:
        options.update(encryption=fitz.PDF_ENCRYPT_AES_256,
                       user_pw=password, owner_pw="owner-secret")
    doc.save(path, **options)
    doc.close()


def test_formatted_text_box_is_page_content_selectable_and_visible(tmp_path: Path) -> None:
    source, output = tmp_path / "source.pdf", tmp_path / "with-text.pdf"
    make_pdf(source)
    style = {
        "size": 20, "bold": True, "italic": True, "underline": True,
        "letter_spacing": 1.5, "line_spacing": 1.4, "alignment": "center",
    }
    add_rich_text_box(source, output, 0, (72, 500, 360, 700),
                      "Live preview\nsecond line", style)
    rendered = fitz.open(output)
    page = rendered[0]
    extracted = page.get_text()
    assert "Live preview" in extracted
    assert "second line" in extracted
    assert not list(page.annots() or [])
    pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), clip=fitz.Rect(72, 92, 360, 292))
    assert any(pixel < 245 for pixel in pix.samples)
    spans = [span for block in page.get_text("dict")["blocks"]
             for line in block.get("lines", []) for span in line["spans"]]
    inserted = [span for span in spans if "Live preview" in span["text"]]
    assert inserted and inserted[0]["size"] == pytest.approx(20, abs=0.5)
    assert "Bold" in inserted[0]["font"]
    assert not PdfReader(source).pages[0].get("/Annots")
    rendered.close()


def test_text_box_preserves_encryption_and_rejects_bad_inputs(tmp_path: Path) -> None:
    locked, output = tmp_path / "locked.pdf", tmp_path / "edited.pdf"
    make_pdf(locked, "secret")
    with pytest.raises(PermissionError, match="valid password"):
        add_rich_text_box(locked, output, 0, (40, 100, 300, 160), "Text", {})
    add_rich_text_box(locked, output, 0, (40, 100, 300, 160), "Text", {}, "secret")
    result = fitz.open(output)
    assert result.needs_pass and result.authenticate("secret")
    assert "Text" in result[0].get_text()
    result.close()
    with pytest.raises(IndexError):
        add_rich_text_box(locked, tmp_path / "wrong-page.pdf", 5,
                          (40, 100, 300, 160), "Text", {}, "secret")
    with pytest.raises(ValueError, match="page bounds"):
        add_rich_text_box(locked, tmp_path / "outside.pdf", 0,
                          (-20, 100, 300, 160), "Text", {}, "secret")
