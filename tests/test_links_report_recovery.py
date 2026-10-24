# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Page links and sheet auto-linking, the markup summary report, and recovery copies."""
from pathlib import Path
import os
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymupdf as fitz
import pytest

import links
import markups
import report
from pdf_document import PdfSession
from recovery import STALE_AFTER, RecoveryStore


def _sheets(path: Path, rotate_second: bool = True) -> None:
    doc = fitz.open()
    for sheet in ("A-101", "A-102", "S-201"):
        page = doc.new_page(width=600, height=400)
        page.insert_text((50, 50), f"Sheet {sheet}")
        page.insert_text((50, 100), "See A-102 and S-201 for details")
    doc.set_toc([[1, "A-101 Floor plan", 1], [1, "A-102 Roof plan", 2],
                 [1, "S-201 Framing", 3]])
    if rotate_second:
        doc[1].set_rotation(90)
    doc.save(path)


def test_links_add_follow_remove_and_undo(tmp_path: Path) -> None:
    _sheets(tmp_path / "set.pdf")
    session = PdfSession(tmp_path / "set.pdf")
    try:
        session.apply("Link", lambda doc: links.add_page_link(doc[1], fitz.Rect(10, 10, 60, 30), 2))
        (link,) = links.page_links(session.doc[1])
        assert link["kind"] == "page" and link["target"] == 2
        assert tuple(link["rect"]) == pytest.approx((10, 10, 60, 30))
        assert links.link_at(session.doc[1], fitz.Point(20, 20))["target"] == 2
        assert links.link_at(session.doc[1], fitz.Point(200, 200)) is None
        with pytest.raises(IndexError):
            links.add_page_link(session.doc[0], fitz.Rect(0, 0, 10, 10), 9)
        assert links.sheet_ids(session.doc) == {"A-101": 0, "A-102": 1, "S-201": 2}
        added = session.apply("Auto", links.auto_link_sheets)
        # Each sheet links to the other two (never itself); page 2 already had one there.
        assert added == 4
        targets = {i: sorted(l["target"] for l in links.page_links(session.doc[i]))
                   for i in range(3)}
        assert targets == {0: [1, 2], 1: [2, 2], 2: [1]}
        assert session.apply("Remove", lambda doc: links.remove_links(doc[0])) == 2
        assert links.page_links(session.doc[0]) == []
        session.undo()
        assert len(links.page_links(session.doc[0])) == 2
        session.save()
    finally:
        session.close()
    with fitz.open(tmp_path / "set.pdf") as saved:
        assert {l["page"] for l in saved[0].get_links()} == {1, 2}


def test_summary_report_lists_markups_and_totals(tmp_path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    markups.set_page_scale(page, markups.common_scale('1/4" = 1\'-0"'))
    for offset in range(40):
        markups.create_markup(page, "rectangle", [(10 + offset, 10), (60 + offset, 60)],
                              author="Pat")
    markups.create_markup(page, "length", [(100, 700), (280, 700)])
    markups.create_markup(page, "note", [(300, 300)], text="Check <this> & that")
    output = report.write_summary(doc, tmp_path / "summary.pdf", "plans.pdf")
    with fitz.open(output) as result:
        text = "".join(page.get_text() for page in result)
        assert result.page_count >= 2 and result[0].rect.width > result[0].rect.height
        assert "Markup Summary" in text and "plans.pdf" in text
        assert "10'-0\"" in text and "Check <this> & that" in text
        assert f"Page 1 of {result.page_count}" in text


def test_recovery_copies_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "plans.pdf"
    _sheets(source, rotate_second=False)
    store = RecoveryStore(tmp_path / "recovery")
    session = PdfSession(source)
    try:
        session.apply("Rect", lambda doc: markups.create_markup(doc[0], "rectangle",
                                                                [(10, 10), (90, 90)]))
        store.save(session)
        assert store.pending() == []  # this instance's own copy
        other = RecoveryStore(tmp_path / "recovery")
        assert other.pending() == []  # still fresh: another instance is alive
        stale = time.time() - STALE_AFTER - 5
        for path in (tmp_path / "recovery").glob("*.json"):
            os.utime(path, (stale, stale))
        (item,) = other.pending()
        assert Path(item["original"]) == source.resolve()
        restored = other.claim(item["id"], tmp_path / "restored.pdf")
        with fitz.open(restored) as copy:
            assert len(list(copy[0].annots())) == 1
        assert other.pending() == []
        store.save(session)
        store.forget(session)
        assert list((tmp_path / "recovery").iterdir()) == []
    finally:
        session.close()
