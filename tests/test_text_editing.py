"""Tests for local, non-destructive existing-text replacement."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fitz
import pytest
from pdf_operations import replace_existing_text


def make_text_pdf(path: Path, encrypted: str | None = None) -> None:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Original wording", fontsize=14)
    options = {}
    if encrypted:
        options.update(encryption=fitz.PDF_ENCRYPT_AES_256,
                       user_pw=encrypted, owner_pw="owner-secret")
    doc.save(path, **options)
    doc.close()


def test_replace_text_writes_new_pdf_and_preserves_source(tmp_path: Path) -> None:
    source, output = tmp_path / "source.pdf", tmp_path / "edited.pdf"
    make_text_pdf(source)
    count = replace_existing_text(source, output, 0, "Original wording", "Updated wording")
    assert count == 1
    assert "Original wording" in fitz.open(source)[0].get_text()
    edited = fitz.open(output)
    assert "Updated wording" in edited[0].get_text()
    assert "Original wording" not in edited[0].get_text()
    edited.close()


def test_replace_text_removes_text_when_replacement_is_empty(tmp_path: Path) -> None:
    source, output = tmp_path / "source.pdf", tmp_path / "edited.pdf"
    make_text_pdf(source)
    assert replace_existing_text(source, output, 0, "Original wording", "") == 1
    assert "Original wording" not in fitz.open(output)[0].get_text()


def test_replace_text_handles_malformed_and_encrypted_pdfs(tmp_path: Path) -> None:
    malformed = tmp_path / "bad.pdf"
    malformed.write_bytes(b"not a PDF")
    with pytest.raises(ValueError, match="Could not open PDF"):
        replace_existing_text(malformed, tmp_path / "bad-output.pdf", 0, "a", "b")

    locked, output = tmp_path / "locked.pdf", tmp_path / "unlocked-output.pdf"
    make_text_pdf(locked, encrypted="secret")
    with pytest.raises(PermissionError, match="valid password"):
        replace_existing_text(locked, output, 0, "Original wording", "Updated")
    assert replace_existing_text(locked, output, 0, "Original wording", "Updated", "secret") == 1
    result = fitz.open(output)
    assert result.needs_pass and result.authenticate("secret")
    assert "Updated" in result[0].get_text()
    result.close()


def test_replace_text_rejects_bad_page_and_nonmatches(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    make_text_pdf(source)
    with pytest.raises(IndexError):
        replace_existing_text(source, tmp_path / "bad-page.pdf", 4, "Original", "Updated")
    with pytest.raises(ValueError, match="No exact match"):
        replace_existing_text(source, tmp_path / "no-match.pdf", 0, "missing", "Updated")
