# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Drawing sets: persistence with relative paths, sheet names, search, and reports."""
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pymupdf as fitz
import pytest

import report
import sets
from markups import create_markup


def _drawing(path: Path, sheets: list[str], password: str | None = None) -> None:
    doc = fitz.open()
    for sheet in sheets:
        page = doc.new_page(width=600, height=400)
        page.insert_text((40, 40), f"{sheet} title block")
    doc.set_toc([[1, f"{sheet} Plan", i + 1] for i, sheet in enumerate(sheets)])
    create_markup(doc[0], "cloud", [(50, 50), (150, 50), (100, 120)], author="Pat")
    options = {}
    if password:
        options.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
    doc.save(path, **options)


def test_set_round_trip_sheets_search_and_report(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "arch").mkdir(parents=True)
    _drawing(project / "arch" / "A.pdf", ["A-101", "A-102"])
    _drawing(project / "S.pdf", ["S-201"])
    _drawing(project / "locked.pdf", ["X-1"], password="pw")
    drawing_set = sets.DrawingSet(project / "Tower.nset", "Tower")
    assert drawing_set.add([project / "arch" / "A.pdf", project / "S.pdf",
                            project / "locked.pdf", project / "S.pdf", project / "notes.txt"]) == 3
    drawing_set.move(project / "S.pdf", -1)
    drawing_set.save()
    assert '"arch/A.pdf"' in (project / "Tower.nset").read_text(encoding="utf-8")

    # Moving the whole folder keeps the set working (paths are relative).
    moved = tmp_path / "moved"
    shutil.move(str(project), str(moved))
    loaded = sets.DrawingSet.load(moved / "Tower.nset")
    assert loaded.name == "Tower"
    assert [f.name for f in loaded.files] == ["S.pdf", "A.pdf", "locked.pdf"]
    assert [s.title for s in loaded.sheets(loaded.files[1])] == ["A-101 Plan", "A-102 Plan"]
    assert loaded.sheets(loaded.files[2]) is None  # encrypted: shown as locked

    hits = loaded.search("A-102")
    assert [(h["file"].name, h["page"]) for h in hits] == [("A.pdf", 1)]
    rows = loaded.markup_rows()
    assert sorted(row["file"] for row in rows) == ["A.pdf", "S.pdf"]
    output = report.write_summary(None, tmp_path / "set.pdf", "Tower", rows)
    with fitz.open(output) as result:
        text = "".join(page.get_text() for page in result)
    assert "File" in text and "A.pdf" in text and "S.pdf" in text

    loaded.remove(loaded.files[0])
    assert [f.name for f in loaded.files] == ["A.pdf", "locked.pdf"]


def test_set_rejects_other_json(tmp_path: Path) -> None:
    other = tmp_path / "other.nset"
    other.write_text('{"format": "something-else"}', encoding="utf-8")
    with pytest.raises(ValueError):
        sets.DrawingSet.load(other)
