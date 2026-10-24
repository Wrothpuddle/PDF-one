"""Non-destructive page operations for Northstar PDF.

All operations write to a new output path. The input document is never modified.
pypdf is BSD-3-Clause licensed.
"""
from __future__ import annotations

from pathlib import Path
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Ellipse, FreeText, Highlight, Line, PolyLine, Rectangle, Text
from pypdf.generic import (ArrayObject, DictionaryObject, FloatObject, NameObject,
                           NumberObject, RectangleObject, TextStringObject)
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


def replace_existing_text(source: str | Path, output: str | Path, page_index: int,
                          old_text: str, new_text: str,
                          password: str | None = None) -> int:
    """Replace exact selectable page text in a new PDF using redaction.

    Font family/metrics are approximated from the first matching span. This
    does not reflow surrounding paragraphs or reconstruct scanned page text.
    PyMuPDF is AGPL-3.0-or-later or commercially licensed by Artifex.
    """
    import fitz

    _require_new_output(source, output)
    if not old_text.strip():
        raise ValueError("Enter the existing text to replace.")
    try:
        doc = fitz.open(str(source))
    except Exception as exc:
        raise ValueError(f"Could not open PDF: {exc}") from exc
    was_encrypted = doc.needs_pass
    try:
        if was_encrypted and not doc.authenticate(password or ""):
            raise PermissionError("The PDF is encrypted; a valid password is required.")
        if not 0 <= page_index < doc.page_count:
            raise IndexError("Page index is outside the document.")
        page = doc[page_index]
        rectangles = page.search_for(old_text)
        if not rectangles:
            raise ValueError("No exact match was found on the current page.")
        spans = [span for block in page.get_text("dict").get("blocks", [])
                 for line in block.get("lines", []) for span in line.get("spans", [])]
        style = next((span for span in spans if old_text in span.get("text", "")), {})
        size = max(5.0, min(72.0, float(style.get("size", 11.0))))
        value = int(style.get("color", 0))
        color = ((value >> 16 & 255) / 255, (value >> 8 & 255) / 255,
                 (value & 255) / 255)
        for rect in rectangles:
            page.add_redact_annot(rect, fill=(1, 1, 1), cross_out=False)
        page.apply_redactions(images=0, graphics=0)
        if new_text:
            for rect in rectangles:
                page.insert_text((rect.x0, min(rect.y1 - 1, rect.y0 + size)),
                                 new_text, fontsize=size, color=color, overlay=True)
        options = {"garbage": 3, "deflate": True}
        if was_encrypted:
            options.update(encryption=fitz.PDF_ENCRYPT_AES_256,
                           user_pw=password or "", owner_pw=password or "")
        doc.save(str(output), **options)
        return len(rectangles)
    finally:
        doc.close()


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


def protect_pdf(source: str | Path, output: str | Path, password: str,
                source_password: str | None = None) -> None:
    """Create an AES-256 password-protected copy without modifying the source."""
    if not password or not password.strip():
        raise ValueError("Choose a non-empty password.")
    _require_new_output(source, output)
    reader = _reader(source, source_password)
    writer = PdfWriter(clone_from=reader)
    writer.encrypt(password, password, algorithm="AES-256")
    _write(writer, output)



def add_rich_text_box(source: str | Path, output: str | Path, page_index: int,
                       rect: tuple[float, float, float, float], text: str,
                       style: dict, password: str | None = None) -> None:
    """Write a formatted text box into page content so every viewer displays it.

    Text remains selectable and searchable. It is staged as a normal PDF edit;
    subsequent moving/resizing requires undoing and placing it again.
    """
    import html
    import fitz

    _require_new_output(source, output)
    if not text.strip():
        raise ValueError("Text box cannot be empty.")
    try:
        doc = fitz.open(str(source))
    except Exception as exc:
        raise ValueError(f"Could not open PDF: {exc}") from exc
    encrypted = doc.needs_pass
    try:
        if encrypted and not doc.authenticate(password or ""):
            raise PermissionError("The PDF is encrypted; a valid password is required.")
        if not 0 <= page_index < doc.page_count:
            raise IndexError("Page index is outside the document.")
        page = doc[page_index]
        pdf_rect = fitz.Rect(*(float(value) for value in rect))
        if pdf_rect.is_empty or pdf_rect.width < 18 or pdf_rect.height < 12:
            raise ValueError("Text box is too small. Drag a larger box on the page.")
        pdf_bounds = fitz.Rect(0, 0, page.rect.width, page.rect.height) * ~page.transformation_matrix
        if not pdf_bounds.contains(pdf_rect):
            raise ValueError("Text box must fit inside the page bounds.")
        page_rect = pdf_rect * page.transformation_matrix

        size = max(4.0, min(144.0, float(style.get("size", 12))))
        letter_spacing = max(0.0, min(20.0, float(style.get("letter_spacing", 0))))
        line_spacing = max(0.5, min(4.0, float(style.get("line_spacing", 1.0))))
        alignment = str(style.get("alignment", "left")).lower()
        if alignment not in {"left", "center", "right", "justify"}:
            raise ValueError("Unknown text alignment.")
        weight = "bold" if style.get("bold") else "normal"
        italic = "italic" if style.get("italic") else "normal"
        decoration = "underline" if style.get("underline") else "none"
        escaped = html.escape(text).replace("\\n", "<br/>")
        markup = (
            f'<p style="font-family:sans-serif;font-size:{size:.2f}pt;'
            f'font-weight:{weight};font-style:{italic};text-decoration:{decoration};'
            f'letter-spacing:{letter_spacing:.2f}pt;line-height:{line_spacing * 100:.0f}%;'
            f'text-align:{alignment};margin:0">{escaped}</p>'
        )
        # Keep a small inset so the text does not touch the visible box border.
        text_rect = page_rect + (5, 4, -5, -4)
        spare_height, _scale = page.insert_htmlbox(text_rect, markup, scale_low=1.0, overlay=True)
        if spare_height < 0:
            raise ValueError("The text does not fit in this box. Resize the box or reduce the font size.")
        page.draw_rect(page_rect, color=(0.18, 0.43, 0.82), width=0.7, overlay=True)
        options = {"garbage": 3, "deflate": True}
        if encrypted:
            options.update(encryption=fitz.PDF_ENCRYPT_AES_256,
                           user_pw=password or "", owner_pw=password or "")
        doc.save(str(output), **options)
    finally:
        doc.close()


def add_pdf_annotation(source: str | Path, output: str | Path, page_index: int,
                       kind: str, rect: tuple[float, float, float, float],
                       text: str = "", vertices: list[tuple[float, float]] | None = None,
                       password: str | None = None, color: str = "ffd54f") -> None:
    """Add a standard PDF text or drawing annotation as a new revision."""
    _require_new_output(source, output)
    reader = _reader(source, password)
    if not 0 <= page_index < len(reader.pages):
        raise IndexError("Page index is outside the document.")
    x1, y1, x2, y2 = (float(value) for value in rect)
    if not (x1 < x2 and y1 < y2):
        raise ValueError("Annotation area must have positive width and height.")
    page_box = reader.pages[page_index].mediabox
    if not (float(page_box.left) <= x1 < x2 <= float(page_box.right)
            and float(page_box.bottom) <= y1 < y2 <= float(page_box.top)):
        raise ValueError("Annotation must fit within the page bounds.")
    color = color.strip().lstrip("#")
    if len(color) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in color):
        raise ValueError("Annotation color must be a six-digit RGB hex value.")

    if kind == "note":
        if not text.strip():
            raise ValueError("Note text cannot be empty.")
        annotation = Text(rect=(x1, y1, x2, y2), text=text.strip())
    elif kind == "text":
        if not text.strip():
            raise ValueError("Text cannot be empty.")
        annotation = FreeText(
            text=text.strip(), rect=(x1, y1, x2, y2), font="Helvetica",
            font_size="12pt", font_color="000000", border_color=color,
            background_color="ffffff",
        )
    elif kind == "highlight":
        points = ArrayObject([
            FloatObject(x1), FloatObject(y2), FloatObject(x2), FloatObject(y2),
            FloatObject(x1), FloatObject(y1), FloatObject(x2), FloatObject(y1),
        ])
        annotation = Highlight(rect=(x1, y1, x2, y2), quad_points=points,
                               highlight_color=color)
    elif kind in ("underline", "strikeout"):
        subtype = "/Underline" if kind == "underline" else "/StrikeOut"
        annotation = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject(subtype),
            NameObject("/Rect"): RectangleObject((x1, y1, x2, y2)),
            NameObject("/QuadPoints"): ArrayObject([
                FloatObject(x1), FloatObject(y2), FloatObject(x2), FloatObject(y2),
                FloatObject(x1), FloatObject(y1), FloatObject(x2), FloatObject(y1),
            ]),
            NameObject("/C"): ArrayObject([
                FloatObject(int(color[i:i + 2], 16) / 255) for i in (0, 2, 4)
            ]),
            NameObject("/Contents"): TextStringObject(kind.title()),
        })
    elif kind == "rectangle":
        annotation = Rectangle(rect=(x1, y1, x2, y2))
        annotation[NameObject("/C")] = ArrayObject([
            FloatObject(int(color[i:i + 2], 16) / 255) for i in (0, 2, 4)
        ])
    elif kind == "ellipse":
        annotation = Ellipse(rect=(x1, y1, x2, y2))
        annotation[NameObject("/C")] = ArrayObject([
            FloatObject(int(color[i:i + 2], 16) / 255) for i in (0, 2, 4)
        ])
    elif kind in ("line", "arrow"):
        annotation = Line((x1, y1), (x2, y2), (x1, y1, x2, y2))
        annotation[NameObject("/C")] = ArrayObject([
            FloatObject(int(color[i:i + 2], 16) / 255) for i in (0, 2, 4)
        ])
        if kind == "arrow":
            annotation[NameObject("/LE")] = ArrayObject([
                NameObject("/None"), NameObject("/OpenArrow")
            ])
    elif kind == "pen":
        path_points = vertices or [(x1, y1), (x2, y2)]
        if len(path_points) < 2:
            raise ValueError("A pen stroke needs at least two points.")
        annotation = PolyLine(path_points)
        annotation[NameObject("/C")] = ArrayObject([
            FloatObject(int(color[i:i + 2], 16) / 255) for i in (0, 2, 4)
        ])
    else:
        raise ValueError(f"Unknown annotation tool: {kind}")

    writer = PdfWriter(clone_from=reader)
    writer.add_annotation(page_index, annotation)
    _write(writer, output, password if reader.is_encrypted else None)
