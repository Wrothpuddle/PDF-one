"""Tests for local, non-destructive page manipulation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from pypdf import PdfReader, PdfWriter

from pdf_operations import (crop_page, delete_page, extract_pages, insert_pages, merge_pdfs,
                             replace_page, reorder_pages, rotate_page, split_pdf)


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
    secured = PdfReader(tmp_path / "unlocked.pdf")
    assert secured.is_encrypted
    assert secured.decrypt("secret")
    assert len(secured.pages) == 2


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


def test_reorder_crop_insert_replace_and_split(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    writer = PdfWriter()
    for width in (100, 200, 300):
        writer.add_blank_page(width=width, height=792)
    with source.open("wb") as stream:
        writer.write(stream)

    reordered = tmp_path / "reordered.pdf"
    reorder_pages(source, reordered, [2, 0, 1])
    reader = PdfReader(reordered)
    assert [float(page.mediabox.width) for page in reader.pages] == [300, 100, 200]

    cropped = tmp_path / "cropped.pdf"
    crop_page(source, cropped, 0, 10, 20, 90, 700)
    box = PdfReader(cropped).pages[0].cropbox
    assert float(box.width) == 80
    assert float(box.height) == 680

    inserted = tmp_path / "inserted.pdf"
    insert_pages(source, reordered, inserted, 1)
    assert len(PdfReader(inserted).pages) == 6

    replacement = tmp_path / "replacement.pdf"
    replace_page(source, reordered, replacement, 1)
    assert float(PdfReader(replacement).pages[1].mediabox.width) == 300

    split = split_pdf(source, tmp_path / "split")
    assert len(split) == 3
    assert all(len(PdfReader(path).pages) == 1 for path in split)


def test_reorder_and_crop_reject_invalid_inputs(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    make_pdf(source)
    with pytest.raises(ValueError):
        reorder_pages(source, tmp_path / "bad-order.pdf", [0, 0, 2])
    with pytest.raises(ValueError):
        crop_page(source, tmp_path / "bad-crop.pdf", 0, 50, 50, 20, 20)
