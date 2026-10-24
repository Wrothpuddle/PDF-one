"""Tests for formatted, visible FreeText box placement."""
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


def test_formatted_text_box_is_visible_and_retains_styles(tmp_path: Path) -> None:
    source, output = tmp_path / "source.pdf", tmp_path / "with-text.pdf"
    make_pdf(source)
    style = {
        "size": 20, "bold": True, "italic": True, "underline": True,
        "letter_spacing": 1.5, "line_spacing": 1.4, "alignment": "center",
    }
    add_rich_text_box(source, output, 0, (72, 500, 360, 700),
                      "Live preview\\nsecond line", style)
    annotation = PdfReader(output).pages[0]["/Annots"][0].get_object()
    assert annotation["/Subtype"] == "/FreeText"
    rich = str(annotation["/RC"])
    for marker in ("font-size:20.00pt", "font-weight:bold", "font-style:italic",
                   "text-decoration:underline", "letter-spacing:1.50pt",
                   "line-height:140%", "text-align:center", "second line"):
        assert marker in rich
    rendered = fitz.open(output)
    assert len(list(rendered[0].annots() or [])) == 1
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
    result.close()
    with pytest.raises(IndexError):
        add_rich_text_box(locked, tmp_path / "wrong-page.pdf", 5,
                          (40, 100, 300, 160), "Text", {}, "secret")
