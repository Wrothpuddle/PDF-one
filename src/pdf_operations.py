"""Non-destructive page operations for Northstar PDF.

All operations write to a new output path. The input document is never modified.
pypdf is BSD-3-Clause licensed.
"""
from __future__ import annotations

from pathlib import Path
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Text
from pypdf.errors import PdfReadError


def _reader(path: str | Path, password: str | None = None) -> PdfReader:
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        if password is None or not reader.decrypt(password):
            raise PermissionError("The PDF is encrypted; a valid password is required.")
    if not reader.pages:
        raise ValueError("The PDF contains no pages.")
    return reader


def _require_new_output(source: str | Path, output: str | Path) -> None:
    if Path(source).resolve() == Path(output).resolve():
        raise ValueError("Choose a new output file so the original remains unchanged.")


def _write(writer: PdfWriter, output: str | Path, password: str | None = None) -> None:
    output = Path(output)
    if output.suffix.lower() != ".pdf":
        raise ValueError("Output path must end in .pdf")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.stem + ".northstar-tmp.pdf")
    try:
        if password:
            # Preserve password protection on transformed encrypted documents.
            writer.encrypt(password, password, algorithm="AES-256")
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
    _require_new_output(source, output)
    reader = _reader(source, password)
    if not 0 <= page_index < len(reader.pages):
        raise IndexError("Page index is outside the document.")
    writer = PdfWriter(clone_from=reader)
    writer.pages[page_index].rotate(degrees)
    _write(writer, output, password if reader.is_encrypted else None)


def delete_page(source: str | Path, output: str | Path, page_index: int,
                password: str | None = None) -> None:
    _require_new_output(source, output)
    reader = _reader(source, password)
    if len(reader.pages) <= 1:
        raise ValueError("Cannot delete the only page in a PDF.")
    if not 0 <= page_index < len(reader.pages):
        raise IndexError("Page index is outside the document.")
    writer = PdfWriter()
    for index, page in enumerate(reader.pages):
        if index != page_index:
            writer.add_page(page)
    _write(writer, output, password if reader.is_encrypted else None)


def extract_pages(source: str | Path, output: str | Path, page_indices: list[int],
                  password: str | None = None) -> None:
    _require_new_output(source, output)
    reader = _reader(source, password)
    if not page_indices:
        raise ValueError("Select at least one page to extract.")
    if any(index < 0 or index >= len(reader.pages) for index in page_indices):
        raise IndexError("A selected page index is outside the document.")
    writer = PdfWriter()
    for index in page_indices:
        writer.add_page(reader.pages[index])
    _write(writer, output, password if reader.is_encrypted else None)



def add_sticky_note(source: str | Path, output: str | Path, page_index: int,
                    text: str, password: str | None = None) -> None:
    """Add a local PDF text-note annotation near the top-left of a page."""
    if not text.strip():
        raise ValueError("Note text cannot be empty.")
    _require_new_output(source, output)
    reader = _reader(source, password)
    if not 0 <= page_index < len(reader.pages):
        raise IndexError("Page index is outside the document.")
    writer = PdfWriter(clone_from=reader)
    box = writer.pages[page_index].mediabox
    x = float(box.left) + 28
    y = float(box.top) - 58
    annotation = Text(rect=(x, y, x + 22, y + 22), text=text.strip())
    writer.add_annotation(page_index, annotation)
    _write(writer, output, password if reader.is_encrypted else None)




def reorder_pages(source: str | Path, output: str | Path, order: list[int],
                  password: str | None = None) -> None:
    """Rebuild a PDF in the requested zero-based page order."""
    _require_new_output(source, output)
    reader = _reader(source, password)
    expected = set(range(len(reader.pages)))
    if len(order) != len(reader.pages) or set(order) != expected:
        raise ValueError("Page order must list every page exactly once.")
    writer = PdfWriter()
    for index in order:
        writer.add_page(reader.pages[index])
    if password and reader.is_encrypted:
        writer.encrypt(password, password, algorithm="AES-256")
    _write(writer, output)


def crop_page(source: str | Path, output: str | Path, page_index: int,
              left: float, bottom: float, right: float, top: float,
              password: str | None = None) -> None:
    """Set a page's visible crop box. Page content outside the box remains in the file."""
    _require_new_output(source, output)
    reader = _reader(source, password)
    if not 0 <= page_index < len(reader.pages):
        raise IndexError("Page index is outside the document.")
    writer = PdfWriter(clone_from=reader)
    page = writer.pages[page_index]
    media = page.mediabox
    if not (float(media.left) <= left < right <= float(media.right)
            and float(media.bottom) <= bottom < top <= float(media.top)):
        raise ValueError("Crop box must be inside the page bounds and have positive size.")
    page.cropbox.lower_left = (left, bottom)
    page.cropbox.upper_right = (right, top)
    _write(writer, output, password if reader.is_encrypted else None)


def insert_pages(source: str | Path, insert_source: str | Path, output: str | Path,
                 before_page: int, password: str | None = None) -> None:
    """Insert all pages from a second PDF before a zero-based page position."""
    _require_new_output(source, output)
    if Path(insert_source).resolve() == Path(output).resolve():
        raise ValueError("Choose an output different from the inserted PDF.")
    reader = _reader(source, password)
    inserted = _reader(insert_source)
    if not 0 <= before_page <= len(reader.pages):
        raise IndexError("Insertion position is outside the document.")
    writer = PdfWriter()
    for i in range(len(reader.pages) + 1):
        if i == before_page:
            for page in inserted.pages:
                writer.add_page(page)
        if i < len(reader.pages):
            writer.add_page(reader.pages[i])
    if password and reader.is_encrypted:
        writer.encrypt(password, password, algorithm="AES-256")
    _write(writer, output)


def replace_page(source: str | Path, replacement_source: str | Path, output: str | Path,
                 page_index: int, password: str | None = None) -> None:
    """Replace one page with the first page from a second PDF."""
    _require_new_output(source, output)
    if Path(replacement_source).resolve() == Path(output).resolve():
        raise ValueError("Choose an output different from the replacement PDF.")
    reader = _reader(source, password)
    replacement = _reader(replacement_source)
    if not 0 <= page_index < len(reader.pages):
        raise IndexError("Page index is outside the document.")
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        writer.add_page(replacement.pages[0] if i == page_index else page)
    if password and reader.is_encrypted:
        writer.encrypt(password, password, algorithm="AES-256")
    _write(writer, output)


def split_pdf(source: str | Path, output_directory: str | Path,
              password: str | None = None) -> list[Path]:
    """Write each source page as an individual PDF in a local folder."""
    reader = _reader(source, password)
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)
        if password and reader.is_encrypted:
            writer.encrypt(password, password, algorithm="AES-256")
        target = directory / f"page-{index + 1:04d}.pdf"
        _write(writer, target)
        written.append(target)
    return written


def extract_text_file(source: str | Path, output: str | Path,
                      password: str | None = None) -> None:
    reader = _reader(source, password)
    text = "\n\n".join(f"--- Page {index + 1} ---\n{page.extract_text() or ''}"
                        for index, page in enumerate(reader.pages))
    Path(output).write_text(text, encoding="utf-8")


def read_form_fields(source: str | Path, password: str | None = None) -> dict[str, str]:
    reader = _reader(source, password)
    fields = reader.get_fields() or {}
    return {name: str(field.get("/V", "")) for name, field in fields.items()}


def fill_form_fields(source: str | Path, output: str | Path, values: dict[str, str],
                     password: str | None = None) -> None:
    _require_new_output(source, output)
    reader = _reader(source, password)
    writer = PdfWriter(clone_from=reader)
    writer.update_page_form_field_values(None, values, auto_regenerate=False)
    _write(writer, output, password if reader.is_encrypted else None)



def merge_pdfs(sources: list[str | Path], output: str | Path) -> None:
    if len(sources) < 2:
        raise ValueError("Choose at least two PDFs to merge.")
    output_resolved = Path(output).resolve()
    if any(Path(source).resolve() == output_resolved for source in sources):
        raise ValueError("Choose a new output file so the source PDFs remain unchanged.")
    writer = PdfWriter()
    for source in sources:
        reader = _reader(source)
        for page in reader.pages:
            writer.add_page(page)
    _write(writer, output)
