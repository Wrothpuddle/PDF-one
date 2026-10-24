# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Page links: read, add, remove, and auto-link sheet references.

Links are read straight from the page's /Annots objects instead of PyMuPDF's
get_links(), which caches per page (so it misses links added or undone in the
live document) and reports rectangles in rotated coordinates. Everything here
uses the markup coordinate space: unrotated, crop-box-relative points.
"""
from __future__ import annotations

import re

import pymupdf as fitz

import markups

_REF = re.compile(r"(\d+)\s+0\s+R")


def _page_index(doc: fitz.Document) -> dict[int, int]:
    return {doc.page_xref(i): i for i in range(doc.page_count)}


def _target(doc: fitz.Document, xref: int, pages: dict[int, int]) -> tuple[str, object]:
    for key in ("Dest", "A/D"):
        kind, value = doc.xref_get_key(xref, key)
        if kind == "array":
            match = _REF.search(value)
            if match and int(match.group(1)) in pages:
                return "page", pages[int(match.group(1))]
    kind, value = doc.xref_get_key(xref, "A/URI")
    if kind == "string":
        return "uri", value
    kind, value = doc.xref_get_key(xref, "A/F")
    if kind == "string":
        return "file", value
    return "unknown", None


def page_links(page: fitz.Page) -> list[dict]:
    """Links on a page: {'xref', 'rect' (page coords), 'kind', 'target'}."""
    doc = page.parent
    kind, value = doc.xref_get_key(page.xref, "Annots")
    if kind != "array":
        return []
    pages = _page_index(doc)
    found = []
    for match in _REF.finditer(value):
        xref = int(match.group(1))
        if doc.xref_get_key(xref, "Subtype") != ("name", "/Link"):
            continue
        rect_kind, rect_value = doc.xref_get_key(xref, "Rect")
        numbers = [float(n) for n in re.findall(r"-?\d*\.?\d+", rect_value)] if rect_kind == "array" else []
        if len(numbers) != 4:
            continue
        rect = (fitz.Rect(*numbers) * page.transformation_matrix).normalize()
        link_kind, target = _target(doc, xref, pages)
        found.append({"xref": xref, "rect": rect, "kind": link_kind, "target": target})
    return found


def link_at(page: fitz.Page, point: fitz.Point) -> dict | None:
    for link in reversed(page_links(page)):
        if link["rect"].contains(point):
            return link
    return None


def add_page_link(page: fitz.Page, rect: fitz.Rect, target_page: int) -> None:
    doc = page.parent
    if not 0 <= target_page < doc.page_count:
        raise IndexError("The link target page is outside the document.")
    rect = fitz.Rect(rect).normalize()
    if rect.width < 2 or rect.height < 2:
        raise ValueError("Drag a larger link area.")
    page.insert_link({"kind": fitz.LINK_GOTO, "from": rect, "page": target_page,
                      "to": fitz.Point(0, 0), "zoom": 0})


def remove_links(page: fitz.Page, xrefs: set[int] | None = None) -> int:
    """Remove the given link annotations (all links when xrefs is None)."""
    doc = page.parent
    kind, value = doc.xref_get_key(page.xref, "Annots")
    if kind != "array":
        return 0
    link_xrefs = {link["xref"] for link in page_links(page)}
    doomed = link_xrefs if xrefs is None else link_xrefs & set(xrefs)
    if not doomed:
        return 0
    kept = [f"{m.group(1)} 0 R" for m in _REF.finditer(value) if int(m.group(1)) not in doomed]
    doc.xref_set_key(page.xref, "Annots", "[" + " ".join(kept) + "]" if kept else "null")
    return len(doomed)


SHEET_ID = re.compile(r"^[A-Z]{1,3}[-.]?\d{1,4}(?:\.\d{1,3})?[A-Z]?$")


def sheet_ids(doc: fitz.Document) -> dict[str, int]:
    """Sheet numbers (e.g. A-101) -> page, from bookmarks, page labels, or title text."""
    found: dict[str, int] = {}
    for _level, title, page in doc.get_toc(simple=True):
        token = (title.split() or [""])[0].strip(":")
        if page >= 1 and SHEET_ID.match(token) and token not in found:
            found[token] = page - 1
    for index in range(doc.page_count):
        label = doc[index].get_label()
        if label and SHEET_ID.match(label) and label not in found:
            found[label] = index
    return found


def auto_link_sheets(doc: fitz.Document, ids: dict[str, int] | None = None) -> int:
    """Link every occurrence of a known sheet number to that sheet. Returns links added."""
    ids = ids if ids is not None else sheet_ids(doc)
    if not ids:
        return 0
    added = 0
    for page in doc:
        existing = [link["rect"] for link in page_links(page)]
        for word in markups.page_words(page):
            token = word[4].strip(".,;:()[]")
            target = ids.get(token)
            if target is None or target == page.number:
                continue
            rect = fitz.Rect(word[:4])
            if any(rect.intersects(other) for other in existing):
                continue
            add_page_link(page, rect, target)
            existing.append(rect)
            added += 1
    return added
