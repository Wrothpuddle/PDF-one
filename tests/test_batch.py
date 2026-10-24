# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Batch processing writes new files and never changes the originals."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymupdf as fitz

import links
import markups
from batch import BatchOptions, run_batch


def _sheet_set(path: Path, password: str | None = None) -> None:
    doc = fitz.open()
    for sheet in ("A-101", "A-102"):
        page = doc.new_page(width=600, height=400)
        page.insert_text((50, 50), f"Sheet {sheet}")
        page.insert_text((50, 100), "See A-101 and A-102")
    doc.set_toc([[1, "A-101 Plan", 1], [1, "A-102 Roof", 2]])
    markups.create_markup(doc[0], "rectangle", [(100, 150), (200, 250)], author="Pat")
    options = {}
    if password:
        options.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
    doc.save(path, **options)


def test_batch_actions_and_outputs(tmp_path: Path) -> None:
    sources = [tmp_path / "one.pdf", tmp_path / "two.pdf", tmp_path / "locked.pdf"]
    _sheet_set(sources[0])
    _sheet_set(sources[1])
    _sheet_set(sources[2], password="secret")
    (tmp_path / "broken.pdf").write_bytes(b"not a pdf")
    originals = {path: path.read_bytes() for path in sources}
    seen = []
    results = run_batch(
        sources + [tmp_path / "broken.pdf"], tmp_path / "out",
        BatchOptions(auto_link=True, stamp="Approved", scale='1/4" = 1\'-0"',
                     summary=True, csv=True, author="Batch"),
        progress=lambda index, name: seen.append(name) or True)
    assert [r.ok for r in results] == [True, True, False, False]
    assert "password" in results[2].message
    assert seen == ["one.pdf", "two.pdf", "locked.pdf", "broken.pdf"]
    for path, data in originals.items():
        assert path.read_bytes() == data
    out = tmp_path / "out"
    assert sorted(p.name for p in out.iterdir()) == [
        "one.pdf", "one_markup_summary.pdf", "one_markups.csv",
        "two.pdf", "two_markup_summary.pdf", "two_markups.csv"]
    with fitz.open(out / "one.pdf") as doc:
        kinds = [row["kind"] for row in markups.list_markups(doc)]
        assert sorted(kinds) == ["rectangle", "stamp"]
        assert markups.page_scale(doc[1]).label == '1/4" = 1\'-0"'
        assert {link["target"] for link in links.page_links(doc[0])} == {1}
    assert "Stamp: Approved" in (out / "one_markups.csv").read_text(encoding="utf-8")


def test_batch_flatten_protect_and_same_folder(tmp_path: Path) -> None:
    source = tmp_path / "plan.pdf"
    _sheet_set(source, password="old")
    results = run_batch([source], tmp_path, BatchOptions(flatten=True, password="new",
                                                         source_password="old"))
    assert results[0].ok, results[0].message
    output = tmp_path / "plan_batch.pdf"  # same folder: suffix avoids overwriting
    with fitz.open(output) as doc:
        assert doc.needs_pass and not doc.authenticate("old") and doc.authenticate("new")
        assert not list(doc[0].annots())
    keep = run_batch([source], tmp_path / "kept", BatchOptions(scale="1:100",
                                                               source_password="old"))
    with fitz.open(keep[0].outputs[0]) as doc:
        assert doc.needs_pass and doc.authenticate("old")  # stays encrypted
