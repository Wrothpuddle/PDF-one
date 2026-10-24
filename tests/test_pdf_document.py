"""In-memory session: undo/redo across journal and copy steps, save, encryption."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymupdf as fitz
import pytest

import pdf_operations as ops
from markups import create_markup
from pdf_document import PdfSession


def make_pdf(path: Path, pages: int = 3, password: str | None = None) -> None:
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Sheet {index + 1}")
    doc.set_toc([[1, f"Sheet {index + 1}", index + 1] for index in range(pages)])
    options = {}
    if password:
        options.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
    doc.save(path, **options)
    doc.close()


def page_labels(doc: fitz.Document) -> list[str]:
    return [doc[i].get_text().strip() for i in range(doc.page_count)]


def test_edits_stay_in_memory_until_saved(tmp_path: Path) -> None:
    source = tmp_path / "plans.pdf"
    make_pdf(source)
    original = source.read_bytes()
    session = PdfSession(source)
    try:
        session.apply("Rotate", lambda doc: ops.rotate_page(doc, 1))
        assert session.dirty and session.undo_label == "Rotate"
        assert source.read_bytes() == original
        session.save()
        assert not session.dirty
        with fitz.open(source) as saved:
            assert saved[1].rotation == 90
        session.undo()
        assert session.dirty
    finally:
        session.close()


def test_undo_redo_mixes_journal_and_copy_steps(tmp_path: Path) -> None:
    source, extra = tmp_path / "plans.pdf", tmp_path / "extra.pdf"
    make_pdf(source, 3)
    make_pdf(extra, 2)
    session = PdfSession(source)
    try:
        session.apply("Markup", lambda doc: create_markup(doc[0], "rectangle", [(10, 10), (90, 90)]))
        session.apply_copy("Insert", lambda doc: ops.insert_pages(doc, extra, 1))
        assert page_labels(session.doc) == ["Sheet 1", "Sheet 1", "Sheet 2", "Sheet 2", "Sheet 3"]
        session.apply("Delete", lambda doc: ops.delete_pages(doc, [4]))
        assert session.doc.page_count == 4
        assert len(list(session.doc[0].annots())) == 1
        session.undo()
        assert session.doc.page_count == 5
        session.undo()
        assert session.doc.page_count == 3
        assert len(list(session.doc[0].annots())) == 1
        session.undo()
        assert not list(session.doc[0].annots())
        assert not session.can_undo
        session.redo()
        session.redo()
        session.redo()
        assert session.doc.page_count == 4
        assert not session.can_redo
        # A new edit after undo drops the redo branch.
        session.undo()
        session.apply("Rotate", lambda doc: ops.rotate_page(doc, 0))
        assert not session.can_redo and session.doc.page_count == 5
    finally:
        session.close()


def test_failed_operation_rolls_back(tmp_path: Path) -> None:
    source = tmp_path / "plans.pdf"
    make_pdf(source)
    session = PdfSession(source)
    try:
        def broken(doc):
            ops.rotate_page(doc, 0)
            raise ValueError("boom")

        with pytest.raises(ValueError):
            session.apply("Broken", broken)
        assert session.doc[0].rotation == 0
        assert not session.dirty and not session.can_undo
    finally:
        session.close()


def test_page_operations_keep_bookmarks(tmp_path: Path) -> None:
    source = tmp_path / "plans.pdf"
    make_pdf(source, 4)
    session = PdfSession(source)
    try:
        session.apply("Reorder", lambda doc: ops.reorder_pages(doc, [3, 2, 1, 0]))
        session.apply("Delete", lambda doc: ops.delete_pages(doc, [0]))
        session.save()
    finally:
        session.close()
    with fitz.open(source) as saved:
        assert page_labels(saved) == ["Sheet 3", "Sheet 2", "Sheet 1"]
        toc = {title: page for _level, title, page in saved.get_toc()}
        assert toc["Sheet 1"] == 3 and toc["Sheet 3"] == 1


def test_encrypted_pdf_stays_encrypted(tmp_path: Path) -> None:
    source = tmp_path / "locked.pdf"
    make_pdf(source, 2, password="secret")
    with pytest.raises(PermissionError):
        PdfSession(source)
    session = PdfSession(source, "secret")
    try:
        session.apply("Markup", lambda doc: create_markup(doc[0], "ellipse", [(10, 10), (90, 90)]))
        session.save()
    finally:
        session.close()
    with fitz.open(source) as saved:
        assert saved.needs_pass and saved.authenticate("secret")
        assert len(list(saved[0].annots())) == 1


def test_save_as_moves_the_session(tmp_path: Path) -> None:
    source, copy = tmp_path / "plans.pdf", tmp_path / "copy" / "plans-rev1.pdf"
    make_pdf(source)
    session = PdfSession(source)
    try:
        session.apply("Rotate", lambda doc: ops.rotate_page(doc, 0))
        session.save(copy)
        assert session.path == copy.resolve() and not session.dirty
    finally:
        session.close()
    with fitz.open(source) as untouched:
        assert untouched[0].rotation == 0


def test_text_reads_on_rotated_pages_do_not_break_the_journal(tmp_path: Path) -> None:
    """PyMuPDF's get_textpage() edits /Rotate, which the undo journal rejects."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import markups
    from panels import search_document

    source = tmp_path / "plans.pdf"
    make_pdf(source)
    session = PdfSession(source)
    try:
        session.apply("Rotate", lambda doc: ops.rotate_page(doc, 1))
        page = session.doc[1]
        words = markups.page_words(page)
        assert [w[4] for w in words] == ["Sheet", "2"]
        with fitz.open(source) as reference:
            assert words[0][:4] == pytest.approx(reference[1].get_text("words")[0][:4])
        hits = search_document(session.doc, "Sheet")
        assert [page for page, _rect, _context in hits] == [0, 1, 2]
        assert "Sheet 2" in ops.export_text(session.doc, tmp_path / "t.txt").read_text()
        session.apply_copy("Bookmarks", lambda doc: doc.set_toc([[1, "Only", 2]]))
        assert session.doc.get_toc() == [[1, "Only", 2]]
        session.undo()
        assert len(session.doc.get_toc()) == 3
        assert session.can_undo  # the rotation is still undoable
    finally:
        session.close()
