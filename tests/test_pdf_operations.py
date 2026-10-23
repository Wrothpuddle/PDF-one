"""Tests for local, non-destructive page manipulation."""
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from pdf_operations import delete_page, extract_pages, merge_pdfs, rotate_page


def make_pdf(path: Path, count: int = 3, password: str | None = None) -> None:
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=612, height=792)
    if password:
        writer.encrypt(password)
    with path.open("wb") as stream:
        writer.write(stream)


def test_rotate_and_delete_preserve_source(tmp_path: Path) -> None:
    source, rotated, deleted = (tmp_path / name for name in ("source.pdf", "rotated.pdf", "deleted.pdf"))
    make_pdf(source)
    rotate_page(source, rotated, 1)
    delete_page(source, deleted, 0)
    assert PdfReader(source).pages[1].get("/Rotate", 0) == 0
    assert PdfReader(rotated).pages[1].get("/Rotate") == 90
    assert len(PdfReader(deleted).pages) == 2


def test_extract_and_merge(tmp_path: Path) -> None:
    source, other = tmp_path / "source.pdf", tmp_path / "other.pdf"
    make_pdf(source, 3)
    make_pdf(other, 2)
    extracted, merged = tmp_path / "extracted.pdf", tmp_path / "merged.pdf"
    extract_pages(source, extracted, [2, 0])
    merge_pdfs([source, other], merged)
    assert len(PdfReader(extracted).pages) == 2
    assert len(PdfReader(merged).pages) == 5


def test_malformed_and_encrypted_pdfs_fail_cleanly(tmp_path: Path) -> None:
    malformed = tmp_path / "bad.pdf"
    malformed.write_bytes(b"not a PDF")
    with pytest.raises(Exception):
        rotate_page(malformed, tmp_path / "out.pdf", 0)
    encrypted = tmp_path / "locked.pdf"
    make_pdf(encrypted, password="secret")
    with pytest.raises(PermissionError):
        delete_page(encrypted, tmp_path / "out.pdf", 0)
    delete_page(encrypted, tmp_path / "unlocked.pdf", 0, password="secret")
    assert len(PdfReader(tmp_path / "unlocked.pdf").pages) == 2


def test_invalid_page_selection_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    make_pdf(source)
    with pytest.raises(IndexError):
        extract_pages(source, tmp_path / "out.pdf", [9])
    single = tmp_path / "single.pdf"
    make_pdf(single, 1)
    with pytest.raises(ValueError):
        delete_page(single, tmp_path / "out.pdf", 0)


def test_operations_never_overwrite_source(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    make_pdf(source)
    with pytest.raises(ValueError, match="original remains unchanged"):
        rotate_page(source, source, 0)
    with pytest.raises(ValueError, match="original remains unchanged"):
        delete_page(source, source, 0)
    with pytest.raises(ValueError, match="original remains unchanged"):
        extract_pages(source, source, [0])
    with pytest.raises(ValueError, match="source PDFs remain unchanged"):
        merge_pdfs([source, source], source)
