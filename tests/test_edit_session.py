"""Transactional editing and undo/redo tests."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pypdf import PdfReader, PdfWriter
from edit_session import EditSession


def make_pdf(path: Path, password: str | None = None) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    if password:
        writer.encrypt(password, password, algorithm="AES-256")
    with path.open("wb") as stream:
        writer.write(stream)


def test_rotation_is_staged_then_saved_to_same_path(tmp_path: Path) -> None:
    source = tmp_path / "doc.pdf"
    make_pdf(source)
    session = EditSession(source)
    try:
        working = session.rotate(0)
        assert session.dirty
        assert working != source
        assert PdfReader(source).pages[0].get("/Rotate", 0) == 0
        assert PdfReader(working).pages[0].get("/Rotate") == 90
        session.undo()
        assert not session.dirty
        session.redo()
        session.save()
        assert not session.dirty
        assert PdfReader(source).pages[0].get("/Rotate") == 90
    finally:
        session.cleanup()


def test_discard_keeps_original_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "doc.pdf"
    make_pdf(source)
    session = EditSession(source)
    try:
        session.rotate(1, 180)
        session.discard()
        assert not session.dirty
        assert PdfReader(source).pages[1].get("/Rotate", 0) == 0
    finally:
        session.cleanup()


def test_staged_rotation_preserves_encryption(tmp_path: Path) -> None:
    source = tmp_path / "locked.pdf"
    make_pdf(source, password="secret")
    session = EditSession(source)
    try:
        session.rotate(0, password="secret")
        reader = PdfReader(session.working_path)
        assert reader.is_encrypted
        assert reader.decrypt("secret")
        assert reader.pages[0].get("/Rotate") == 90
    finally:
        session.cleanup()
