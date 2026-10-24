# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Snap targets from page linework and markups."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymupdf as fitz

import snap
from markups import create_markup
from pdf_document import PdfSession


def _plan(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.draw_rect(fitz.Rect(100, 100, 300, 250), color=(0, 0, 0))
    page.draw_line((50, 175), (350, 175), color=(0, 0, 0))  # crosses the rectangle
    doc.save(path)


def test_endpoints_midpoints_and_intersections(tmp_path: Path) -> None:
    _plan(tmp_path / "plan.pdf")
    with fitz.open(tmp_path / "plan.pdf") as doc:
        points = snap.content_points(doc, 0)
    kinds = {(round(p.x), round(p.y)): kind for p, kind in points}
    assert kinds[(100, 100)] == "Endpoint"
    assert kinds[(200, 100)] == "Midpoint"
    assert kinds[(100, 175)] == "Intersection" and kinds[(300, 175)] == "Intersection"
    index = snap.SnapIndex(points)
    point, kind = index.nearest(fitz.Point(103, 98), 6)
    assert (round(point.x), round(point.y), kind) == (100, 100, "Endpoint")
    assert index.nearest(fitz.Point(200, 300), 6) is None


def test_snapping_on_rotated_cropped_page_keeps_journal_working(tmp_path: Path) -> None:
    _plan(tmp_path / "plan.pdf")
    session = PdfSession(tmp_path / "plan.pdf")
    try:
        session.apply("Crop+rotate", lambda doc: (
            doc[0].set_cropbox(fitz.Rect(20, 10, 580, 390)), doc[0].set_rotation(90)))
        points = snap.content_points(session.doc, 0)
        corners = {(round(p.x), round(p.y)) for p, kind in points if kind == "Endpoint"}
        assert (80, 90) in corners  # (100, 100) shifted by the crop box origin
        xref = session.apply("Line", lambda doc: create_markup(doc[0], "line",
                                                               [(10, 10), (60, 40)]))
        page = session.doc[0]
        assert {(round(p.x), round(p.y)) for p, _ in snap.markup_points(page)} == {(10, 10), (60, 40)}
        assert snap.markup_points(page, {xref}) == []
        assert session.can_undo
        session.undo()
        session.undo()
        assert session.doc[0].rotation == 0
    finally:
        session.close()
