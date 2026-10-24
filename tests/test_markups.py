"""Live markup creation, editing, hit-testing, listing, and measurement."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymupdf as fitz
import pytest

import markups
from markups import Style, create_markup, default_style
from pdf_document import PdfSession


def make_pdf(path: Path, pages: int = 2, text: str = "Door schedule revised") -> None:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), text, fontsize=14)
    doc.save(path)
    doc.close()


@pytest.fixture
def session(tmp_path: Path):
    source = tmp_path / "drawing.pdf"
    make_pdf(source)
    opened = PdfSession(source)
    yield opened
    opened.close()


ALL_KINDS = {
    "text": [(100, 200), (300, 260)], "callout": [(100, 300), (200, 260)],
    "note": [(400, 400)], "highlight": [(70, 85), (250, 105)],
    "underline": [(70, 85), (250, 105)], "strikeout": [(70, 85), (250, 105)],
    "squiggly": [(70, 85), (250, 105)], "pen": [(50, 500), (60, 510), (80, 505)],
    "highlighter": [(50, 520), (90, 520)], "line": [(50, 600), (150, 600)],
    "arrow": [(50, 620), (150, 640)], "polyline": [(50, 650), (80, 680), (120, 650)],
    "polygon": [(300, 500), (380, 500), (340, 560)], "cloud": [(400, 500), (480, 500), (440, 560)],
    "rectangle": [(300, 600), (380, 660)], "ellipse": [(400, 600), (480, 660)],
    "stamp": [(420, 100), (570, 145)], "redact": [(300, 700), (360, 730)],
    "length": [(100, 720), (172, 720)], "polylength": [(100, 740), (172, 740), (172, 760)],
    "area": [(450, 700), (522, 700), (522, 772), (450, 772)],
    "perimeter": [(200, 700), (272, 700), (272, 772)], "count": [(30, 30)],
    "angle": [(500, 300), (450, 300), (450, 250)],
}


def test_every_tool_creates_a_live_annotation(session: PdfSession) -> None:
    def add_all(doc):
        page = doc[0]
        return {kind: create_markup(page, kind, points, text="Check this")
                for kind, points in ALL_KINDS.items()}

    xrefs = session.apply("Add markups", add_all)
    page = session.doc[0]
    rows = {row["xref"]: row for row in markups.list_markups(session.doc)}
    for kind, xref in xrefs.items():
        assert rows[xref]["kind"] == kind
        assert rows[xref]["page"] == 0
    # Markups are annotations only: nothing is burned into the page content.
    assert b"Check this" not in page.read_contents()
    highlight = page.load_annot(xrefs["highlight"])
    assert highlight.rect.y0 < 100 < highlight.rect.y1
    assert highlight.rect.width < 200  # snapped to the words, not the dragged box


def test_markups_survive_save_and_reopen_and_stay_editable(session: PdfSession,
                                                           tmp_path: Path) -> None:
    xref = session.apply("Rectangle", lambda doc: create_markup(
        doc[0], "rectangle", [(100, 100), (200, 150)], Style(stroke="#00ff00", width=3)))
    session.save()
    session.close()
    reopened = PdfSession(tmp_path / "drawing.pdf")
    try:
        page = reopened.doc[0]
        (row,) = markups.list_markups(reopened.doc)
        assert row["kind"] == "rectangle" and row["color"] == "#00ff00"
        reopened.apply("Move", lambda doc: markups.move_markup(doc[0], row["xref"], 50, 25))
        page = reopened.doc[0]
        assert tuple(markups.markup_bounds(page, row["xref"]).tl) == pytest.approx((149, 124), abs=1.5)
    finally:
        reopened.close()
    assert xref


def test_restyle_move_resize_and_undo(session: PdfSession) -> None:
    xref = session.apply("Cloud", lambda doc: create_markup(
        doc[0], "cloud", [(100, 100), (200, 100), (150, 180)]))
    page = session.doc[0]
    style = markups.read_style(page, xref)
    assert style.stroke == "#e02020"
    style.stroke, style.width, style.dash, style.fill = "#0000ff", 3, "dashed", "#ffff00"
    session.apply("Style", lambda doc: markups.apply_style(doc[0], xref, style))
    page = session.doc[0]
    restyled = markups.read_style(page, xref)
    assert (restyled.stroke, restyled.fill, restyled.dash) == ("#0000ff", "#ffff00", "dashed")
    assert restyled.width == pytest.approx(3)
    assert markups.markup_kind(page, page.load_annot(xref)) == "cloud"

    session.apply("Move", lambda doc: markups.move_markup(doc[0], xref, 10, 20))
    page = session.doc[0]
    assert tuple(markups.markup_points(page, xref)[0]) == pytest.approx((110, 120), abs=0.01)
    session.apply("Resize", lambda doc: markups.resize_markup(
        doc[0], xref, fitz.Rect(0, 0, 200, 160)))
    page = session.doc[0]
    assert tuple(markups.markup_bounds(page, xref)) == pytest.approx((0, 0, 200, 160), abs=0.01)

    session.undo()
    session.undo()
    page = session.doc[0]
    assert tuple(markups.markup_points(page, xref)[0]) == pytest.approx((100, 100), abs=0.01)
    session.undo()
    session.undo()
    assert not list(session.doc[0].annots())
    assert not session.dirty
    session.redo()
    assert len(list(session.doc[0].annots())) == 1


def test_text_box_edit_resize_and_style(session: PdfSession) -> None:
    style = default_style("text")
    style.font_size, style.font, style.align = 18, "TiRo", "center"
    xref = session.apply("Text", lambda doc: create_markup(
        doc[0], "text", [(100, 200), (320, 260)], style, text="First"))
    session.apply("Edit", lambda doc: markups.set_text(doc[0], xref, "Second\nline"))
    session.apply("Resize", lambda doc: markups.resize_markup(
        doc[0], xref, fitz.Rect(100, 200, 400, 300)))
    page = session.doc[0]
    annot = page.load_annot(xref)
    assert annot.info["content"] == "Second\nline"
    assert tuple(annot.rect) == pytest.approx((100, 200, 400, 300), abs=0.5)
    read = markups.read_style(page, xref)
    assert read.font_size == pytest.approx(18)
    assert read.font == "TiRo" and read.align == "center"
    assert tuple(markups.markup_bounds(page, xref)) == pytest.approx((100, 200, 400, 300), abs=0.5)
    pixmap = page.get_pixmap(clip=fitz.Rect(100, 200, 400, 300))
    assert any(value < 128 for value in pixmap.samples)


def test_hit_testing_and_box_selection(session: PdfSession) -> None:
    def add(doc):
        page = doc[0]
        return (create_markup(page, "line", [(100, 100), (300, 100)]),
                create_markup(page, "rectangle", [(100, 300), (200, 400)]),
                create_markup(page, "polygon", [(400, 400), (500, 400), (450, 500)]))

    line, rect, polygon = session.apply("Add", add)
    page = session.doc[0]
    assert markups.markup_at(page, (200, 102)) == line
    assert markups.markup_at(page, (200, 120)) is None
    assert markups.markup_at(page, (150, 350)) == rect
    assert markups.markup_at(page, (450, 430)) == polygon
    assert set(markups.markups_in_rect(page, fitz.Rect(90, 90, 310, 410))) == {line, rect}


def test_markups_on_rotated_page_use_unrotated_coordinates(session: PdfSession) -> None:
    session.apply("Rotate", lambda doc: doc[0].set_rotation(90))
    page = session.doc[0]
    visual = fitz.Point(50, 60)  # on screen, top-left area of the rotated page
    unrotated = visual * page.derotation_matrix
    xref = session.apply("Rect", lambda doc: create_markup(
        doc[0], "rectangle", [unrotated, unrotated + (40, 40)]))
    page = session.doc[0]
    assert markups.markup_at(page, unrotated + (20, 20)) == xref
    shown = page.load_annot(xref).rect * page.rotation_matrix
    assert shown.contains(visual + (1, 1)) or shown.contains(visual + (-1, 1))


def test_measurements_use_page_scale_and_update_when_moved(session: PdfSession) -> None:
    scale = markups.common_scale('1/4" = 1\'-0"')
    session.apply("Scale", lambda doc: markups.set_page_scale(doc[0], scale))
    # 1/4" = 72/4 = 18 points per foot, so 180 points is 10 feet.
    xref = session.apply("Length", lambda doc: create_markup(
        doc[0], "length", [(100, 100), (280, 100)]))
    page = session.doc[0]
    value, label = markups.measurement(page, xref)
    assert value == pytest.approx(10)
    assert label == "10'-0\""
    assert page.load_annot(xref).info["content"] == label
    area = session.apply("Area", lambda doc: create_markup(
        doc[0], "area", [(100, 200), (136, 200), (136, 236), (100, 236)]))
    assert markups.measurement(session.doc[0], area)[1] == "4.00 sq ft"
    session.apply("Stretch", lambda doc: markups.set_markup_points(
        doc[0], xref, [(100, 100), (109, 100)]))
    assert markups.measurement(session.doc[0], xref)[1] == "0'-6\""
    calibrated = markups.scale_from_calibration(100, 5, "m")
    session.apply("Calibrate", lambda doc: markups.set_page_scale(doc[0], calibrated))
    assert markups.measurement(session.doc[0], xref)[0] == pytest.approx(0.45)
    angle = session.apply("Angle", lambda doc: create_markup(
        doc[0], "angle", [(200, 300), (100, 300), (100, 200)]))
    assert markups.measurement(session.doc[0], angle)[1] == "90.0°"
    totals = markups.measurement_totals(markups.list_markups(session.doc))
    assert totals["Length"][1] == 1


def test_feet_inch_formatting() -> None:
    assert markups.format_length(12.5, "ft-in") == "12'-6\""
    assert markups.format_length(1 + 1 / 96, "ft-in") == "1'-0 1/8\""
    assert markups.format_length(2.345, "m") == "2.35 m"


def test_status_author_duplicate_and_csv(session: PdfSession, tmp_path: Path) -> None:
    xref = session.apply("Rect", lambda doc: create_markup(
        doc[0], "rectangle", [(100, 100), (150, 150)], author="Reviewer"))
    session.apply("Status", lambda doc: markups.set_status(doc[0], xref, "Accepted"))
    copy = session.apply("Copy", lambda doc: markups.duplicate_markup(doc[0], xref, target=doc[1]))
    rows = markups.list_markups(session.doc)
    assert [(row["page"], row["status"], row["author"]) for row in rows] == [
        (0, "Accepted", "Reviewer"), (1, "Accepted", "Reviewer")]
    assert rows[1]["xref"] == copy
    output = tmp_path / "markups.csv"
    markups.export_markups_csv(rows, output)
    assert "Accepted" in output.read_text(encoding="utf-8")


def test_style_round_trips_through_dict() -> None:
    style = Style(stroke="#123456", width=4, dash="dotted", stamp="Draft")
    assert Style.from_dict({**style.to_dict(), "unknown": 1}) == style
    with pytest.raises(ValueError):
        markups.hex_to_rgb("blue")
