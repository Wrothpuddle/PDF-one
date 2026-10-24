# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Snap to content: endpoints, midpoints, and intersections of a page's linework.

Points are in unrotated, crop-box-relative page coordinates (the markup space).
Extraction runs on a one-page copy because PyMuPDF's get_drawings() edits a
rotated page's /Rotate, which the undo journal refuses outside an edit.
"""
from __future__ import annotations

import math
from collections import defaultdict

import pymupdf as fitz

import markups

MAX_SEGMENTS = 20000      # cap work on very dense drawings
INTERSECTION_CELL = 40.0  # points; segments are bucketed to find crossings
LOOKUP_CELL = 12.0

PRIORITY = {"Endpoint": 0, "Intersection": 1, "Markup": 2, "Midpoint": 3, "Center": 4}


def _segments(page: fitz.Page) -> list[tuple[fitz.Point, fitz.Point]]:
    segments = []
    for path in page.get_drawings():
        for item in path["items"]:
            op = item[0]
            if op == "l":
                segments.append((item[1], item[2]))
            elif op == "re":
                rect = item[1]
                corners = [rect.tl, rect.tr, rect.br, rect.bl]
                segments.extend((corners[i], corners[(i + 1) % 4]) for i in range(4))
            elif op == "qu":
                quad = item[1]
                corners = [quad.ul, quad.ur, quad.lr, quad.ll]
                segments.extend((corners[i], corners[(i + 1) % 4]) for i in range(4))
            elif op == "c":
                # Treat curves as their chord for endpoints; skip midpoints.
                segments.append((item[1], item[4]))
            if len(segments) >= MAX_SEGMENTS:
                return segments
    return segments


def _intersection(a: fitz.Point, b: fitz.Point, c: fitz.Point, d: fitz.Point) -> fitz.Point | None:
    r = b - a
    s = d - c
    denominator = r.x * s.y - r.y * s.x
    if abs(denominator) < 1e-9:
        return None
    t = ((c.x - a.x) * s.y - (c.y - a.y) * s.x) / denominator
    u = ((c.x - a.x) * r.y - (c.y - a.y) * r.x) / denominator
    if -1e-6 <= t <= 1 + 1e-6 and -1e-6 <= u <= 1 + 1e-6:
        return a + r * t
    return None


def content_points(doc: fitz.Document, index: int) -> list[tuple[fitz.Point, str]]:
    """Snap targets from the page's vector content."""
    scratch = fitz.open()
    try:
        scratch.insert_pdf(doc, from_page=index, to_page=index, annots=False, links=False)
        segments = _segments(scratch[0])
    finally:
        scratch.close()
    points: list[tuple[fitz.Point, str]] = []
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for number, (a, b) in enumerate(segments):
        points.append((fitz.Point(a), "Endpoint"))
        points.append((fitz.Point(b), "Endpoint"))
        if abs(b - a) > 1:
            points.append(((a + b) / 2, "Midpoint"))
        x0, x1 = sorted((a.x, b.x))
        y0, y1 = sorted((a.y, b.y))
        for cx in range(int(x0 // INTERSECTION_CELL), int(x1 // INTERSECTION_CELL) + 1):
            for cy in range(int(y0 // INTERSECTION_CELL), int(y1 // INTERSECTION_CELL) + 1):
                buckets[(cx, cy)].append(number)
    seen_pairs = set()
    for members in buckets.values():
        if len(members) > 400:
            continue  # pathological hatch patterns: skip rather than stall
        for i, first in enumerate(members):
            for second in members[i + 1:]:
                if (first, second) in seen_pairs:
                    continue
                seen_pairs.add((first, second))
                a, b = segments[first]
                c, d = segments[second]
                crossing = _intersection(a, b, c, d)
                if crossing is not None and min(abs(crossing - p) for p in (a, b, c, d)) > 0.5:
                    points.append((crossing, "Intersection"))
    return points


def markup_points(page: fitz.Page, exclude: set[int] = frozenset()) -> list[tuple[fitz.Point, str]]:
    """Vertices of existing markups, so new markups can connect to them."""
    points = []
    for annot in page.annots() or []:
        if annot.xref in exclude or annot.type[0] in (fitz.PDF_ANNOT_POPUP, fitz.PDF_ANNOT_LINK,
                                                       fitz.PDF_ANNOT_WIDGET, fitz.PDF_ANNOT_TEXT):
            continue
        if annot.type[0] == fitz.PDF_ANNOT_INK:
            continue
        for point in markups.markup_points(page, annot.xref):
            points.append((point, "Markup"))
    return points


class SnapIndex:
    """Grid lookup of snap targets for one page."""

    def __init__(self, points: list[tuple[fitz.Point, str]]) -> None:
        self.grid: dict[tuple[int, int], list[tuple[fitz.Point, str]]] = defaultdict(list)
        seen = set()
        for point, kind in points:
            key = (round(point.x, 1), round(point.y, 1))
            if key in seen:
                continue
            seen.add(key)
            self.grid[(int(point.x // LOOKUP_CELL), int(point.y // LOOKUP_CELL))].append(
                (point, kind))
        self.count = len(seen)

    def nearest(self, point: fitz.Point, radius: float) -> tuple[fitz.Point, str] | None:
        """Closest target within radius; endpoints win over midpoints at equal distance."""
        best, best_score = None, math.inf
        reach = int(radius // LOOKUP_CELL) + 1
        cx, cy = int(point.x // LOOKUP_CELL), int(point.y // LOOKUP_CELL)
        for gx in range(cx - reach, cx + reach + 1):
            for gy in range(cy - reach, cy + reach + 1):
                for target, kind in self.grid.get((gx, gy), ()):
                    distance = abs(target - point)
                    if distance > radius:
                        continue
                    score = distance + PRIORITY.get(kind, 5) * radius * 0.15
                    if score < best_score:
                        best, best_score = (target, kind), score
        return best
