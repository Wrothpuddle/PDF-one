"""Tool Chest persistence, drawing compare/overlay, and input parsers."""
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest

import compare
from markups import Style
from tool_chest import MY_TOOLS, RECENT, RECENT_LIMIT, ToolChest, ToolPreset


def test_tool_chest_persists_user_sets_and_recent_tools(tmp_path: Path) -> None:
    path = tmp_path / "chest.json"
    chest = ToolChest(path)
    assert "Markup" in chest.sets() and "Construction Review" in chest.sets()
    with pytest.raises(ValueError):
        chest.add("Markup", ToolPreset("x", "line"))
    tool = chest.add(MY_TOOLS, ToolPreset("Blue cloud", "cloud", Style(stroke="#0000ff").to_dict()))
    chest.add_set("Electrical")
    for number in range(RECENT_LIMIT + 3):
        chest.note_used("line", Style(width=number + 1))
    chest.note_used("line", Style(width=1))  # re-using moves it to the top, no duplicate

    reopened = ToolChest(path)
    assert reopened.find(tool.id).make_style().stroke == "#0000ff"
    assert "Electrical" in reopened.sets()
    recent = reopened.sets()[RECENT]
    assert len(recent) == RECENT_LIMIT and recent[0].make_style().width == 1
    assert len({tool.make_style().width for tool in recent}) == RECENT_LIMIT


def test_tool_chest_export_import_and_bad_files(tmp_path: Path) -> None:
    chest = ToolChest(tmp_path / "a.json")
    chest.add(MY_TOOLS, ToolPreset("Hold", "cloud", {"stroke": "#b000b5"}))
    exported = tmp_path / "share.json"
    chest.export_set(MY_TOOLS, exported)
    other = ToolChest(tmp_path / "b.json")
    names = other.import_sets(exported)
    assert names == [MY_TOOLS]
    assert other.sets()[MY_TOOLS][0].name == "Hold"
    (tmp_path / "junk.json").write_text('{"format": "other"}', encoding="utf-8")
    with pytest.raises(ValueError):
        other.import_sets(tmp_path / "junk.json")
    damaged = tmp_path / "damaged.json"
    damaged.write_text("{not json", encoding="utf-8")
    assert ToolChest(damaged).sets()[MY_TOOLS] == []  # damaged file does not crash
    with pytest.raises(ValueError):
        other.remove_set(RECENT)


def _sheet(extra: bool) -> fitz.Document:
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.draw_rect(fitz.Rect(50, 50, 250, 200), color=(0, 0, 0), width=2)
    if extra:
        page.draw_rect(fitz.Rect(400, 250, 500, 330), color=(0, 0, 0), width=2)
    return doc


def test_compare_finds_only_the_changed_area() -> None:
    old, new = _sheet(False), _sheet(True)
    regions = compare.difference_regions(old[0], new[0])
    assert len(regions) == 1
    assert regions[0].contains(fitz.Rect(400, 250, 500, 330))
    assert regions[0].width < 160
    assert compare.difference_regions(old[0], old[0]) == []


def test_overlay_colours_old_new_and_shared_content() -> None:
    old, new = _sheet(False), _sheet(True)
    pix = compare.overlay_pixmap(old[0], new[0], dpi=72)

    def pixel(x, y):
        return tuple(pix.pixel(x, y))

    assert pixel(50, 125) == pytest.approx((0, 0, 0), abs=60)       # in both: black
    red, green, blue = pixel(400, 290)                               # only in new: cyan
    assert red < 100 and green > 150 and blue > 150
    assert pixel(10, 10) == (255, 255, 255)
    reversed_pix = compare.overlay_pixmap(new[0], old[0], dpi=72)
    red, green, blue = tuple(reversed_pix.pixel(400, 290))           # only in old: red
    assert red > 150 and green < 100
    pdf = compare.overlay_pdf([(old[0], new[0])])
    with fitz.open(stream=pdf, filetype="pdf") as result:
        assert result.page_count == 1 and result[0].get_images()


def test_page_range_and_length_parsers() -> None:
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dialogs import parse_length
    from main import parse_page_ranges

    assert parse_page_ranges("1-3, 5", 6) == [0, 1, 2, 4]
    assert parse_page_ranges("3-1", 3) == [2, 1, 0]
    with pytest.raises(ValueError):
        parse_page_ranges("0", 3)
    with pytest.raises(ValueError):
        parse_page_ranges("", 3)
    assert parse_length("12'-6\"", "ft-in") == pytest.approx(12.5)
    assert parse_length("6\"", "ft-in") == pytest.approx(0.5)
    assert parse_length("3' 1 1/2\"", "ft-in") == pytest.approx(3 + 1.5 / 12)
    assert parse_length("2.5", "m") == 2.5
    with pytest.raises(ValueError):
        parse_length("abc", "m")
