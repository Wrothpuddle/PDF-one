"""Non-destructive page operations for Northstar PDF.

All operations write to a new output path. The input document is never modified.
pypdf is BSD-3-Clause licensed.
"""
from __future__ import annotations

from pathlib import Path
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError


def _reader(path: str | Path, password: str | None = None) -> PdfReader:
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        if password is None or not reader.decrypt(password):
            raise PermissionError("The PDF is encrypted; a valid password is required.")
    if not reader.pages:
        raise ValueError("The PDF contains no pages.")
    return reader


def _write(writer: PdfWriter, output: str | Path) -> None:
    output = Path(output)
    if output.suffix.lower() != ".pdf":
        raise ValueError("Output path must end in .pdf")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.stem + ".northstar-tmp.pdf")
    try:
        with temp.open("wb") as stream:
            writer.write(stream)
        temp.replace(output)
    finally:
        if temp.exists():
            temp.unlink()


def rotate_page(source: str | Path, output: str | Path, page_index: int,
                degrees: int = 90, password: str | None = None) -> None:
    if degrees not in (90, 180, 270):
        raise ValueError("Rotation must be 90, 180, or 270 degrees.")
    reader = _reader(source, password)
    if not 0 <= page_index < len(reader.pages):
        raise IndexError("Page index is outside the document.")
    writer = PdfWriter(clone_from=reader)
    writer.pages[page_index].rotate(degrees)
    _write(writer, output)


def delete_page(source: str | Path, output: str | Path, page_index: int,
                password: str | None = None) -> None:
    reader = _reader(source, password)
    if len(reader.pages) <= 1:
        raise ValueError("Cannot delete the only page in a PDF.")
    if not 0 <= page_index < len(reader.pages):
        raise IndexError("Page index is outside the document.")
    writer = PdfWriter()
    for index, page in enumerate(reader.pages):
        if index != page_index:
            writer.add_page(page)
    _write(writer, output)


def extract_pages(source: str | Path, output: str | Path, page_indices: list[int],
                  password: str | None = None) -> None:
    reader = _reader(source, password)
    if not page_indices:
        raise ValueError("Select at least one page to extract.")
    if any(index < 0 or index >= len(reader.pages) for index in page_indices):
        raise IndexError("A selected page index is outside the document.")
    writer = PdfWriter()
    for index in page_indices:
        writer.add_page(reader.pages[index])
    _write(writer, output)


def merge_pdfs(sources: list[str | Path], output: str | Path) -> None:
    if len(sources) < 2:
        raise ValueError("Choose at least two PDFs to merge.")
    writer = PdfWriter()
    for source in sources:
        reader = _reader(source)
        for page in reader.pages:
            writer.add_page(page)
    _write(writer, output)
