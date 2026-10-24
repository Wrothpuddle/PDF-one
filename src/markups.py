# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Live markups: create, restyle, move, reshape, hit-test, and list annotations.

Every markup is a real PDF annotation with its own appearance, so it stays
editable after saving and shows up once (not twice) in other PDF viewers.

Coordinates are MuPDF *unrotated page* coordinates (points, top-left origin),
the same space PyMuPDF uses for annotation and text APIs. The canvas converts
from the rotated on-screen page with ``page.derotation_matrix``.

Northstar stores a few extra keys on annotations it creates:

    /NSKind    which tool made it ("cloud", "length", ...)
    /NSStatus  review status shown in the Markups List
    /NSScale   (on page objects) the measurement scale for that page

PyMuPDF is AGPL-3.0-or-later or commercially licensed by Artifex.
"""
from __future__ import annotations

import html
import json
import math
import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime

import pymupdf as fitz

KIND_KEY = "NSKind"
STATUS_KEY = "NSStatus"
SCALE_KEY = "NSScale"

# Tool kind -> label shown in toolbars, the Markups List, and as the default subject.
KIND_LABELS = {
    "text": "Text Box", "callout": "Callout", "note": "Note",
    "highlight": "Highlight", "underline": "Underline", "strikeout": "Strikethrough",
    "squiggly": "Squiggly", "pen": "Pen", "highlighter": "Highlighter",
    "line": "Line", "arrow": "Arrow", "polyline": "Polyline", "polygon": "Polygon",
    "cloud": "Cloud", "rectangle": "Rectangle", "ellipse": "Ellipse",
    "stamp": "Stamp", "redact": "Redaction",
    "length": "Length", "polylength": "Polylength", "area": "Area",
    "perimeter": "Perimeter", "count": "Count", "angle": "Angle",
}

# How each kind is drawn: by dragging a box, dragging two points, clicking a
# series of points (double-click/Enter to finish), freehand, or one click.
DRAG_RECT = {"text", "rectangle", "ellipse", "stamp", "redact",
             "highlight", "underline", "strikeout", "squiggly"}
DRAG_LINE = {"line", "arrow", "length", "callout"}
CLICK_POINTS = {"polyline", "polygon", "cloud", "polylength", "area", "perimeter", "angle"}
FREEHAND = {"pen", "highlighter"}
SINGLE_CLICK = {"note", "count"}
MEASURE_KINDS = {"length", "polylength", "area", "perimeter", "count", "angle"}
CLOSED_KINDS = {"polygon", "cloud", "area", "perimeter"}
TEXT_MARKUP_KINDS = {"highlight", "underline", "strikeout", "squiggly"}

STAMPS = ["Approved", "AsIs", "Confidential", "Departmental", "Experimental", "Expired",
          "Final", "ForComment", "ForPublicRelease", "NotApproved",
          "NotForPublicRelease", "Sold", "TopSecret", "Draft"]
STATUSES = ["", "Accepted", "Rejected", "Cancelled", "Completed", "Needs Review"]

LINE_ENDS = {
    "none": fitz.PDF_ANNOT_LE_NONE, "open": fitz.PDF_ANNOT_LE_OPEN_ARROW,
    "closed": fitz.PDF_ANNOT_LE_CLOSED_ARROW, "circle": fitz.PDF_ANNOT_LE_CIRCLE,
    "square": fitz.PDF_ANNOT_LE_SQUARE, "diamond": fitz.PDF_ANNOT_LE_DIAMOND,
    "slash": fitz.PDF_ANNOT_LE_SLASH, "butt": fitz.PDF_ANNOT_LE_BUTT,
}
DASHES = {"solid": None, "dashed": [4, 3], "dotted": [1, 2]}
_SKIP_TYPES = {fitz.PDF_ANNOT_POPUP, fitz.PDF_ANNOT_LINK, fitz.PDF_ANNOT_WIDGET}
_VERTEX_TYPES = {fitz.PDF_ANNOT_LINE, fitz.PDF_ANNOT_POLYGON, fitz.PDF_ANNOT_POLY_LINE,
                 fitz.PDF_ANNOT_INK}
_TEXT_MARKUP_TYPES = {fitz.PDF_ANNOT_HIGHLIGHT, fitz.PDF_ANNOT_UNDERLINE,
                      fitz.PDF_ANNOT_STRIKE_OUT, fitz.PDF_ANNOT_SQUIGGLY}
_TYPE_KINDS = {
    fitz.PDF_ANNOT_FREE_TEXT: "text", fitz.PDF_ANNOT_TEXT: "note",
    fitz.PDF_ANNOT_HIGHLIGHT: "highlight", fitz.PDF_ANNOT_UNDERLINE: "underline",
    fitz.PDF_ANNOT_STRIKE_OUT: "strikeout", fitz.PDF_ANNOT_SQUIGGLY: "squiggly",
    fitz.PDF_ANNOT_INK: "pen", fitz.PDF_ANNOT_LINE: "line",
    fitz.PDF_ANNOT_POLY_LINE: "polyline", fitz.PDF_ANNOT_POLYGON: "polygon",
    fitz.PDF_ANNOT_SQUARE: "rectangle", fitz.PDF_ANNOT_CIRCLE: "ellipse",
    fitz.PDF_ANNOT_STAMP: "stamp", fitz.PDF_ANNOT_REDACT: "redact",
}


@dataclass
class Style:
    """Visual properties of a markup; also what a Tool Chest tool remembers."""

    stroke: str = "#e02020"
    fill: str = ""
    width: float = 1.5
    opacity: float = 1.0
    dash: str = "solid"
    font_size: float = 12.0
    text_color: str = "#000000"
    font: str = "Helv"
    bold: bool = False
    italic: bool = False
    underline: bool = False
    align: str = "left"
    line_start: str = "none"
    line_end: str = "none"
    stamp: str = "Approved"
    subject: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Style":
        names = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in (data or {}).items() if key in names})


def default_style(kind: str) -> Style:
    style = Style(subject=KIND_LABELS.get(kind, kind.title()))
    if kind == "text":
        style.stroke = style.text_color = "#000000"
        style.width = 1.0
    elif kind == "callout":
        style.stroke = style.text_color = "#e02020"
        style.width, style.line_end = 1.0, "open"
    elif kind == "note":
        style.stroke = "#f2c200"
    elif kind == "highlight":
        style.stroke = "#ffe600"
    elif kind in ("underline", "squiggly"):
        style.stroke = "#1f6feb"
    elif kind == "highlighter":
        style.stroke, style.width, style.opacity = "#ffe600", 12.0, 0.4
    elif kind == "arrow":
        style.line_end = "open"
    elif kind == "redact":
        style.stroke, style.fill = "#000000", "#000000"
    elif kind in MEASURE_KINDS:
        style.stroke = "#1f6feb"
        if kind == "length":
            style.line_start = style.line_end = "butt"
        if kind == "area":
            style.fill, style.opacity = "#1f6feb", 0.35
        if kind == "count":
            style.stroke = style.fill = "#e02020"
    return style


# -- colour and number helpers ---------------------------------------------

def hex_to_rgb(value: str) -> tuple[float, float, float] | None:
    value = (value or "").strip().lstrip("#")
    if not value:
        return None
    if len(value) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in value):
        raise ValueError(f"Colour must be a six-digit hex value, not {value!r}.")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_to_hex(rgb) -> str:
    if not rgb:
        return ""
    if len(rgb) == 1:
        rgb = (rgb[0], rgb[0], rgb[0])
    if len(rgb) == 4:  # CMYK
        c, m, y, k = rgb
        rgb = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    return "#" + "".join(f"{max(0, min(255, round(float(v) * 255))):02x}" for v in rgb[:3])


_NUMBER = re.compile(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _numbers(text: str) -> list[float]:
    return [float(value) for value in _NUMBER.findall(text)]


def _pdf_array(values) -> str:
    return "[" + " ".join(f"{value:.4f}".rstrip("0").rstrip(".") for value in values) + "]"


def _pdf_points(page: fitz.Page, points) -> list[float]:
    """Unrotated page points -> flat list of PDF (bottom-left origin) coordinates."""
    matrix = ~page.transformation_matrix
    flat: list[float] = []
    for point in points:
        pdf = fitz.Point(point) * matrix
        flat.extend((pdf.x, pdf.y))
    return flat


def _get_string(doc: fitz.Document, xref: int, key: str) -> str:
    kind, value = doc.xref_get_key(xref, key)
    if kind == "string":
        return value
    if kind == "name":
        return value.lstrip("/")
    return ""


def _set_string(doc: fitz.Document, xref: int, key: str, value: str) -> None:
    doc.xref_set_key(xref, key, fitz.get_pdf_str(value) if value else "null")


# -- creation --------------------------------------------------------------

def text_page(page: fitz.Page, flags: int = fitz.TEXTFLAGS_WORDS) -> fitz.TextPage:
    """Text layer in unrotated coordinates, without modifying the document.

    PyMuPDF's own get_textpage() temporarily sets a rotated page's /Rotate to 0,
    which the undo journal rejects outside an edit. Extracting from the rotated
    page and mapping back with the derotation matrix gives identical results.
    """
    if page.rotation == 0:
        return page.get_textpage(flags=flags)
    textpage = fitz.TextPage(page._get_textpage(None, flags=flags,
                                                matrix=page.derotation_matrix))
    textpage.parent = page
    return textpage


def page_words(page: fitz.Page, sort: bool = False) -> list:
    return page.get_text("words", textpage=text_page(page), sort=sort)


def page_bounds(page: fitz.Page) -> fitz.Rect:
    """The visible page in unrotated, crop-box-relative coordinates."""
    return page.rect * page.derotation_matrix


# Fonts MuPDF can use for plain (non-rich-text) FreeText appearances.
FONTS = {"Helv": "Helvetica", "Cour": "Courier", "TiRo": "Times"}


def _font_name(style: Style) -> str:
    return style.font if style.font in FONTS else "Helv"


_CSS_FONTS = {"Helv": "Helvetica,sans-serif", "Cour": "Courier,monospace",
              "TiRo": "Times,serif"}


def _rich_css(style: Style) -> str:
    return (f"font-family:{_CSS_FONTS.get(style.font, _CSS_FONTS['Helv'])};"
            f"font-size:{float(style.font_size):g}pt;color:{style.text_color or '#000000'};"
            f"font-weight:{'bold' if style.bold else 'normal'};"
            f"font-style:{'italic' if style.italic else 'normal'};"
            f"text-decoration:{'underline' if style.underline else 'none'};"
            f"text-align:{style.align if style.align in ('left', 'center', 'right', 'justify') else 'left'}")


def _is_rich(doc: fitz.Document, xref: int) -> bool:
    # Check /DS, not /RC: MuPDF drops /RC whenever /Contents is set, and the
    # rich text is rewritten from /Contents + /DS on every change anyway.
    return doc.xref_get_key(xref, "DS")[0] == "string" or \
        doc.xref_get_key(xref, "RC")[0] == "string"


def _write_rich(doc: fitz.Document, xref: int, text: str, style: Style) -> None:
    """Store text as XHTML rich text (/RC) styled by the whole-box style (/DS)."""
    css = _rich_css(style)
    body = "<br/>".join(html.escape(line) for line in text.replace("\r\n", "\n").split("\n"))
    rich = ('<?xml version="1.0"?><body xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/" '
            f'xfa:APIVersion="Acrobat:8.0.0" xfa:spec="2.0.2" style="{css}"><p>{body}</p></body>')
    doc.xref_set_key(xref, "RC", fitz.get_pdf_str(rich))
    doc.xref_set_key(xref, "DS", fitz.get_pdf_str(css))


def is_rich_text(page: fitz.Page, xref: int) -> bool:
    """True for text boxes/callouts that support bold, italic, and a border colour."""
    return get_annot(page, xref).type[0] == fitz.PDF_ANNOT_FREE_TEXT and \
        _is_rich(page.parent, xref)


def _parse_css(css: str) -> dict[str, str]:
    return {key.strip().lower(): value.strip() for key, _sep, value in
            (part.partition(":") for part in css.split(";")) if key.strip()}


def _strip_callout(doc: fitz.Document, xref: int) -> None:
    """MuPDF adds a placeholder /CL to every FreeText; plain text boxes have none."""
    doc.xref_set_key(xref, "CL", "null")


def _align(style: Style) -> int:
    return {"left": 0, "center": 1, "right": 2, "justify": 3}.get(style.align, 0)


def _text_quads(page: fitz.Page, rect: fitz.Rect) -> list[fitz.Quad]:
    """One quad per text line covered by rect, so text markups hug the words."""
    lines: dict[tuple[int, int], fitz.Rect] = {}
    for x0, y0, x1, y1, _word, block, line, _number in page_words(page):
        word = fitz.Rect(x0, y0, x1, y1)
        overlap = word & rect
        if overlap.is_empty or overlap.width * overlap.height < 0.3 * word.width * word.height:
            continue
        key = (block, line)
        lines[key] = lines[key] | word if key in lines else word
    return [line.quad for line in lines.values()]


def _normalized_rect(points, minimum: float = 4.0) -> fitz.Rect:
    rect = fitz.Rect(fitz.Point(points[0]), fitz.Point(points[-1])).normalize()
    if rect.width < minimum:
        rect.x1 = rect.x0 + minimum
    if rect.height < minimum:
        rect.y1 = rect.y0 + minimum
    return rect


def create_markup(page: fitz.Page, kind: str, points, style: Style | None = None,
                  text: str = "", author: str = "") -> int:
    """Add a markup to page and return its xref. points are unrotated page coords."""
    style = style or default_style(kind)
    points = [fitz.Point(point) for point in points]
    if not points:
        raise ValueError("A markup needs at least one point.")
    rect = _normalized_rect(points)
    if not rect.intersects(page_bounds(page)):
        raise ValueError("The markup must be on the page.")
    fill = hex_to_rgb(style.fill)

    if kind == "text":
        if not text.strip():
            raise ValueError("Text cannot be empty.")
        if rect.width < 18 or rect.height < 12:
            raise ValueError("Text box is too small. Drag a larger box on the page.")
        # Rich text: MuPDF draws the border in the /DA colour and the words in
        # the /DS colour, so text and border colours can differ.
        annot = page.add_freetext_annot(
            rect, html.escape(text), richtext=True, style=_rich_css(style), fill_color=fill,
            border_width=style.width, dashes=DASHES.get(style.dash),
            opacity=style.opacity, rotate=page.rotation)
    elif kind == "callout":
        if not text.strip():
            raise ValueError("Callout text cannot be empty.")
        tip, anchor = points[0], points[-1]
        box = fitz.Rect(anchor.x, anchor.y - 18, anchor.x + 160, anchor.y + 22)
        if anchor.x < tip.x:
            box = fitz.Rect(anchor.x - 160, anchor.y - 18, anchor.x, anchor.y + 22)
        box &= page_bounds(page)
        knee = fitz.Point(box.x1 if anchor.x < tip.x else box.x0, (box.y0 + box.y1) / 2)
        annot = page.add_freetext_annot(
            box, html.escape(text), richtext=True, style=_rich_css(style), fill_color=fill,
            border_width=max(style.width, 0.5),
            dashes=DASHES.get(style.dash), callout=[tip, knee],
            line_end=LINE_ENDS.get(style.line_end, fitz.PDF_ANNOT_LE_OPEN_ARROW),
            opacity=style.opacity, rotate=page.rotation)
    elif kind == "note":
        annot = page.add_text_annot(points[0], text or "", icon="Comment")
    elif kind in TEXT_MARKUP_KINDS:
        quads = _text_quads(page, rect) or [rect.quad]
        adder = {"highlight": page.add_highlight_annot, "underline": page.add_underline_annot,
                 "strikeout": page.add_strikeout_annot,
                 "squiggly": page.add_squiggly_annot}[kind]
        annot = adder(quads=quads)
    elif kind in FREEHAND:
        if len(points) < 2:
            raise ValueError("A pen stroke needs at least two points.")
        annot = page.add_ink_annot([[(p.x, p.y) for p in points]])
    elif kind in ("line", "arrow", "length"):
        if len(points) < 2 or points[0] == points[-1]:
            raise ValueError("Drag to draw a line.")
        annot = page.add_line_annot(points[0], points[-1])
    elif kind in ("polyline", "polylength", "angle"):
        if len(points) < 2:
            raise ValueError("Click at least two points.")
        if kind == "angle" and len(points) != 3:
            raise ValueError("An angle needs exactly three points.")
        annot = page.add_polyline_annot(points)
    elif kind in CLOSED_KINDS:
        if len(points) < 3:
            raise ValueError("Click at least three points.")
        annot = page.add_polygon_annot(points)
    elif kind == "rectangle":
        annot = page.add_rect_annot(rect)
    elif kind == "ellipse":
        annot = page.add_circle_annot(rect)
    elif kind == "count":
        centre = points[0]
        annot = page.add_circle_annot(fitz.Rect(centre.x - 5, centre.y - 5,
                                                centre.x + 5, centre.y + 5))
    elif kind == "stamp":
        if rect.width < 40 or rect.height < 15:
            rect = fitz.Rect(rect.x0, rect.y0, rect.x0 + 150, rect.y0 + 45)
        index = STAMPS.index(style.stamp) if style.stamp in STAMPS else 0
        annot = page.add_stamp_annot(rect, stamp=index)
    elif kind == "redact":
        annot = page.add_redact_annot(rect, fill=fill or (0, 0, 0))
    else:
        raise ValueError(f"Unknown markup tool: {kind}")

    doc = page.parent
    _set_string(doc, annot.xref, KIND_KEY, kind)
    if kind == "text":
        _strip_callout(doc, annot.xref)
    annot.set_info(title=author or "", subject=style.subject or KIND_LABELS.get(kind, kind),
                   content=text if kind in ("note", "text", "callout") else "")
    xref = annot.xref
    if kind in ("text", "callout"):
        _write_rich(doc, xref, text, style)
    apply_style(page, xref, style)
    if kind in MEASURE_KINDS:
        refresh_measurement(page, xref)
    return xref


# -- reading and restyling -------------------------------------------------

def get_annot(page: fitz.Page, xref: int) -> fitz.Annot:
    annot = page.load_annot(xref)
    if annot is None:
        raise LookupError("That markup no longer exists.")
    return annot


def markup_kind(page: fitz.Page, annot: fitz.Annot) -> str:
    kind = _get_string(page.parent, annot.xref, KIND_KEY)
    if kind:
        return kind
    kind = _TYPE_KINDS.get(annot.type[0], annot.type[1].lower())
    if kind == "line" and annot.line_ends and annot.line_ends[1] not in (0, None):
        return "arrow"
    if kind == "text" and page.parent.xref_get_key(annot.xref, "CL")[0] == "array":
        return "callout"
    if kind == "polygon" and (annot.border or {}).get("clouds", 0) > 0:
        return "cloud"
    return kind


def _parse_da(doc: fitz.Document, xref: int) -> tuple[float, str, str]:
    """Font size, font name and text colour from a FreeText /DA string."""
    da = _get_string(doc, xref, "DA")
    size_match = re.search(r"/(\S+)\s+(-?[\d.]+)\s+Tf", da)
    colour_match = re.search(r"(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+rg", da)
    gray_match = re.search(r"(-?[\d.]+)\s+g(?:\s|$)", da)
    size = float(size_match.group(2)) if size_match else 12.0
    font = size_match.group(1) if size_match else "Helv"
    if colour_match:
        colour = rgb_to_hex([float(colour_match.group(i)) for i in (1, 2, 3)])
    elif gray_match:
        colour = rgb_to_hex([float(gray_match.group(1))])
    else:
        colour = "#000000"
    return size, font, colour


def read_style(page: fitz.Page, xref: int) -> Style:
    annot = get_annot(page, xref)
    doc = page.parent
    kind = markup_kind(page, annot)
    style = default_style(kind)
    colors = annot.colors or {}
    border = annot.border or {}
    style.stroke = rgb_to_hex(colors.get("stroke"))
    style.fill = rgb_to_hex(colors.get("fill"))
    style.width = float(border.get("width") or 0) if border.get("width", -1) >= 0 else style.width
    dashes = border.get("dashes") or []
    style.dash = "solid" if not dashes else ("dotted" if dashes[0] <= 1.5 else "dashed")
    style.opacity = annot.opacity if 0 <= annot.opacity <= 1 else 1.0
    ends = annot.line_ends or (0, 0)
    names = {value: name for name, value in LINE_ENDS.items()}
    style.line_start, style.line_end = names.get(ends[0], "none"), names.get(ends[1], "none")
    style.subject = annot.info.get("subject", "") or style.subject
    if annot.type[0] == fitz.PDF_ANNOT_FREE_TEXT and _is_rich(doc, xref):
        css = _parse_css(_get_string(doc, xref, "DS"))
        _size, _font, border_colour = _parse_da(doc, xref)
        style.stroke = border_colour
        style.fill = rgb_to_hex(colors.get("stroke") or colors.get("fill"))  # PyMuPDF reports /C as stroke
        size = re.match(r"[\d.]+", css.get("font-size", "12"))
        style.font_size = float(size.group(0)) if size else 12.0
        colour = css.get("color", "#000000")
        style.text_color = colour if re.fullmatch(r"#[0-9a-fA-F]{6}", colour) else "#000000"
        family = css.get("font-family", "").lower()
        style.font = "Cour" if "courier" in family or "mono" in family else (
            "TiRo" if "times" in family or family.startswith("serif") else "Helv")
        style.bold = css.get("font-weight", "") in ("bold", "700", "800", "900")
        style.italic = css.get("font-style", "") in ("italic", "oblique")
        style.underline = "underline" in css.get("text-decoration", "")
        style.align = css.get("text-align", "left")
    elif annot.type[0] == fitz.PDF_ANNOT_FREE_TEXT:
        size, font, colour = _parse_da(doc, xref)
        style.font_size, style.text_color = size, colour
        style.font = font if font in FONTS else "Helv"
        quadding = doc.xref_get_key(xref, "Q")
        style.align = {"1": "center", "2": "right", "3": "justify"}.get(quadding[1], "left")
        # MuPDF draws plain FreeText borders and callout lines in the text
        # colour, so the stroke always matches it; /C holds the fill.
        style.fill = rgb_to_hex(colors.get("fill") or colors.get("stroke"))
        style.stroke = colour
    elif annot.type[0] == fitz.PDF_ANNOT_STAMP:
        name = _get_string(doc, xref, "Name")
        style.stamp = name if name in STAMPS else style.stamp
    return style


def apply_style(page: fitz.Page, xref: int, style: Style) -> None:
    """Apply a Style to an existing markup and regenerate its appearance."""
    annot = get_annot(page, xref)
    kind = markup_kind(page, annot)
    annot_type = annot.type[0]
    stroke = hex_to_rgb(style.stroke)
    fill = hex_to_rgb(style.fill)
    opacity = max(0.05, min(1.0, float(style.opacity)))
    dashes = DASHES.get(style.dash)
    width = max(0.0, float(style.width))
    if style.subject:
        info = annot.info
        info["subject"] = style.subject
        annot.set_info(info)

    if annot_type == fitz.PDF_ANNOT_FREE_TEXT and _is_rich(page.parent, xref):
        doc = page.parent
        annot.set_border(width=width, dashes=dashes or [])
        annot.set_opacity(opacity)
        if kind == "callout":
            annot.set_line_ends(LINE_ENDS.get(style.line_end, 0), 0)
        _write_rich(doc, xref, annot.info.get("content", ""), style)
        if not fill:
            doc.xref_set_key(xref, "C", "null")
        annot = get_annot(page, xref)
        annot.update(text_color=stroke or (0, 0, 0), fill_color=fill, rotate=page.rotation)
        if kind != "callout":
            _strip_callout(doc, xref)
        return
    if annot_type == fitz.PDF_ANNOT_FREE_TEXT:
        annot.set_border(width=width, dashes=dashes or [])
        annot.set_opacity(opacity)
        doc = page.parent
        doc.xref_set_key(xref, "Q", str(_align(style)))
        if kind == "callout":
            annot.set_line_ends(LINE_ENDS.get(style.line_end, 0), 0)
        annot.update(fontsize=float(style.font_size), fontname=_font_name(style),
                     text_color=hex_to_rgb(style.text_color) or (0, 0, 0),
                     fill_color=fill, rotate=page.rotation)
        if kind != "callout":
            _strip_callout(doc, xref)
        return
    if annot_type == fitz.PDF_ANNOT_REDACT:
        annot.set_colors(stroke=stroke or (1, 0, 0))
        annot.update(fill_color=fill or (0, 0, 0), cross_out=True)
        return
    if annot_type == fitz.PDF_ANNOT_STAMP:
        annot.set_opacity(opacity)
        annot.update()
        return
    if annot_type in _TEXT_MARKUP_TYPES or annot_type == fitz.PDF_ANNOT_TEXT:
        annot.set_colors(stroke=stroke or (1, 0.9, 0))
        annot.set_opacity(opacity)
        annot.update()
        return

    accepts_fill = annot_type in (fitz.PDF_ANNOT_SQUARE, fitz.PDF_ANNOT_CIRCLE,
                                  fitz.PDF_ANNOT_POLYGON, fitz.PDF_ANNOT_LINE,
                                  fitz.PDF_ANNOT_POLY_LINE)
    annot.set_colors(stroke=stroke or (0, 0, 0), fill=(fill if fill else []) if accepts_fill else None)
    clouds = 2 if kind == "cloud" else 0
    if annot_type in (fitz.PDF_ANNOT_SQUARE, fitz.PDF_ANNOT_CIRCLE, fitz.PDF_ANNOT_POLYGON):
        annot.set_border(width=width, dashes=dashes or [], clouds=clouds)
    else:
        annot.set_border(width=width, dashes=dashes or [])
    if annot_type in (fitz.PDF_ANNOT_LINE, fitz.PDF_ANNOT_POLY_LINE):
        annot.set_line_ends(LINE_ENDS.get(style.line_start, 0), LINE_ENDS.get(style.line_end, 0))
    annot.set_opacity(opacity)
    annot.update()


def set_text(page: fitz.Page, xref: int, text: str) -> None:
    """Change a text box, callout, or note's text (or any markup's comment)."""
    annot = get_annot(page, xref)
    info = annot.info
    info["content"] = text
    annot.set_info(info)
    if annot.type[0] == fitz.PDF_ANNOT_FREE_TEXT:
        style = read_style(page, xref)
        apply_style(page, xref, style)
    else:
        annot.update()


def set_status(page: fitz.Page, xref: int, status: str) -> None:
    get_annot(page, xref)
    _set_string(page.parent, xref, STATUS_KEY, status)


def set_author(page: fitz.Page, xref: int, author: str) -> None:
    annot = get_annot(page, xref)
    info = annot.info
    info["title"] = author
    annot.set_info(info)


# -- geometry ----------------------------------------------------------------

def markup_points(page: fitz.Page, xref: int) -> list[fitz.Point]:
    """Editable points: vertices for lines/polygons/ink, else the rect corners."""
    annot = get_annot(page, xref)
    annot_type = annot.type[0]
    if annot_type == fitz.PDF_ANNOT_INK:
        return [fitz.Point(point) for stroke in annot.vertices or [] for point in stroke]
    if annot_type in _VERTEX_TYPES:
        return [fitz.Point(point) for point in annot.vertices or []]
    rect = annot.rect
    return [rect.tl, rect.br]


def markup_bounds(page: fitz.Page, xref: int) -> fitz.Rect:
    """Tight bounds for selection handles (vertices, not the stroke padding)."""
    annot = get_annot(page, xref)
    if annot.type[0] in _VERTEX_TYPES:
        points = markup_points(page, xref)
        if points:
            rect = fitz.Rect(points[0], points[0])
            for point in points[1:]:
                rect |= point
            return rect
    if annot.type[0] == fitz.PDF_ANNOT_FREE_TEXT:
        rect = annot.rect
        callout = page.parent.xref_get_key(xref, "CL")
        if callout[0] == "array":
            for value in _callout_points(page, callout[1]):
                rect |= value
        return rect
    return annot.rect


def _callout_points(page: fitz.Page, array: str) -> list[fitz.Point]:
    values = _numbers(array)
    matrix = page.transformation_matrix
    return [fitz.Point(values[i], values[i + 1]) * matrix for i in range(0, len(values) - 1, 2)]


def _shift_array(text: str, dx: float, dy: float) -> str:
    index = 0

    def shift(match: re.Match) -> str:
        nonlocal index
        value = float(match.group(0)) + (dx if index % 2 == 0 else dy)
        index += 1
        return f"{value:.4f}".rstrip("0").rstrip(".")

    return _NUMBER.sub(shift, text)


def move_markup(page: fitz.Page, xref: int, dx: float, dy: float) -> None:
    """Translate a markup by (dx, dy) page points, keeping its exact appearance."""
    annot = get_annot(page, xref)
    doc = page.parent
    # Unrotated page y grows downward; PDF y grows upward.
    pdf_dx, pdf_dy = dx, -dy
    for key in ("Rect", "L", "Vertices", "QuadPoints", "InkList", "CL"):
        kind, value = doc.xref_get_key(xref, key)
        if kind == "array":
            doc.xref_set_key(xref, key, _shift_array(value, pdf_dx, pdf_dy))
    popup = annot.popup_xref
    if popup:
        kind, value = doc.xref_get_key(popup, "Rect")
        if kind == "array":
            doc.xref_set_key(popup, "Rect", _shift_array(value, pdf_dx, pdf_dy))
    if markup_kind(page, get_annot(page, xref)) in MEASURE_KINDS:
        refresh_measurement(page, xref)


def set_markup_points(page: fitz.Page, xref: int, points) -> None:
    """Replace a line/polyline/polygon/ink markup's vertices and redraw it."""
    annot = get_annot(page, xref)
    doc = page.parent
    annot_type = annot.type[0]
    points = [fitz.Point(point) for point in points]
    if annot_type == fitz.PDF_ANNOT_LINE:
        if len(points) != 2:
            raise ValueError("A line has exactly two points.")
        doc.xref_set_key(xref, "L", _pdf_array(_pdf_points(page, points)))
    elif annot_type in (fitz.PDF_ANNOT_POLYGON, fitz.PDF_ANNOT_POLY_LINE):
        doc.xref_set_key(xref, "Vertices", _pdf_array(_pdf_points(page, points)))
    elif annot_type == fitz.PDF_ANNOT_INK:
        strokes = annot.vertices or []
        rebuilt, offset = [], 0
        for stroke in strokes:
            rebuilt.append(_pdf_array(_pdf_points(page, points[offset:offset + len(stroke)])))
            offset += len(stroke)
        doc.xref_set_key(xref, "InkList", "[" + " ".join(rebuilt) + "]")
    else:
        resize_markup(page, xref, fitz.Rect(points[0], points[-1]).normalize())
        return
    style = read_style(page, xref)
    apply_style(page, xref, style)
    if markup_kind(page, get_annot(page, xref)) in MEASURE_KINDS:
        refresh_measurement(page, xref)


def resize_markup(page: fitz.Page, xref: int, new_rect: fitz.Rect) -> None:
    """Fit a markup into new_rect (scaling vertices for line-type markups)."""
    new_rect = fitz.Rect(new_rect).normalize()
    if new_rect.width < 2 or new_rect.height < 2:
        raise ValueError("The markup is too small.")
    annot = get_annot(page, xref)
    annot_type = annot.type[0]
    old = markup_bounds(page, xref)
    if annot_type in _VERTEX_TYPES:
        sx = new_rect.width / old.width if old.width else 1.0
        sy = new_rect.height / old.height if old.height else 1.0
        scaled = [fitz.Point(new_rect.x0 + (p.x - old.x0) * sx,
                             new_rect.y0 + (p.y - old.y0) * sy)
                  for p in markup_points(page, xref)]
        set_markup_points(page, xref, scaled)
        return
    if annot_type in _TEXT_MARKUP_TYPES:
        doc = page.parent
        kind, value = doc.xref_get_key(xref, "QuadPoints")
        values = _numbers(value) if kind == "array" else []
        matrix = page.transformation_matrix
        corners = [fitz.Point(values[i], values[i + 1]) * matrix
                   for i in range(0, len(values) - 1, 2)]
        sx = new_rect.width / old.width if old.width else 1.0
        sy = new_rect.height / old.height if old.height else 1.0
        moved = [fitz.Point(new_rect.x0 + (p.x - old.x0) * sx, new_rect.y0 + (p.y - old.y0) * sy)
                 for p in corners]
        doc.xref_set_key(xref, "QuadPoints", _pdf_array(_pdf_points(page, moved)))
        annot = get_annot(page, xref)
        annot.update()
        return
    if annot_type == fitz.PDF_ANNOT_FREE_TEXT:
        style = read_style(page, xref)
        callout = page.parent.xref_get_key(xref, "CL")
        if callout[0] == "array":
            # Keep the arrow tip; re-attach the knee to the moved box.
            tip = _callout_points(page, callout[1])[0]
            knee = fitz.Point(new_rect.x1 if tip.x > new_rect.x1 else new_rect.x0,
                              (new_rect.y0 + new_rect.y1) / 2)
            page.parent.xref_set_key(xref, "CL", _pdf_array(_pdf_points(page, [tip, knee])))
        annot.set_rect(new_rect)
        apply_style(page, xref, style)  # also strips the placeholder /CL
        return
    annot.set_rect(new_rect)
    if annot_type == fitz.PDF_ANNOT_STAMP:
        annot.update()
        return
    apply_style(page, xref, read_style(page, xref))


def delete_markup(page: fitz.Page, xref: int) -> None:
    page.delete_annot(get_annot(page, xref))


def duplicate_markup(page: fitz.Page, xref: int, dx: float = 12, dy: float = 12,
                     target: fitz.Page | None = None) -> int:
    """Copy a markup (optionally onto another page) and return the new xref."""
    target = target or page
    annot = get_annot(page, xref)
    kind = markup_kind(page, annot)
    style = read_style(page, xref)
    info = annot.info
    text = info.get("content", "")
    if kind in TEXT_MARKUP_KINDS:
        bounds = markup_bounds(page, xref)
        points = [bounds.tl + (dx, dy), bounds.br + (dx, dy)]
    elif kind == "callout":
        callout = page.parent.xref_get_key(xref, "CL")
        tip = _callout_points(page, callout[1])[0] if callout[0] == "array" else annot.rect.tl
        points = [tip + (dx, dy), annot.rect.tl + (dx, dy + 18)]
    elif kind == "count":
        points = [annot.rect.tl + (5 + dx, 5 + dy)]
    else:
        points = [point + (dx, dy) for point in markup_points(page, xref)]
    if kind == "pen" or kind == "highlighter":
        points = points or [annot.rect.tl, annot.rect.br]
    new_xref = create_markup(target, kind, points, style, text=text, author=info.get("title", ""))
    status = _get_string(page.parent, xref, STATUS_KEY)
    if status:
        set_status(target, new_xref, status)
    if kind == "text":
        resize_markup(target, new_xref, annot.rect + (dx, dy, dx, dy))
    return new_xref


def _segment_distance(point: fitz.Point, a: fitz.Point, b: fitz.Point) -> float:
    ab = b - a
    length = ab.x * ab.x + ab.y * ab.y
    if length == 0:
        return abs(point - a)
    t = max(0.0, min(1.0, ((point.x - a.x) * ab.x + (point.y - a.y) * ab.y) / length))
    return abs(point - (a + ab * t))


def _inside_polygon(point: fitz.Point, polygon: list[fitz.Point]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i, current in enumerate(polygon):
        previous = polygon[j]
        if (current.y > point.y) != (previous.y > point.y):
            cross = (previous.x - current.x) * (point.y - current.y) / (previous.y - current.y) + current.x
            if point.x < cross:
                inside = not inside
        j = i
    return inside


def markup_at(page: fitz.Page, point, tolerance: float = 4.0) -> int | None:
    """Return the xref of the top-most markup under point, or None."""
    point = fitz.Point(point)
    hit = None
    for annot in page.annots() or []:
        annot_type = annot.type[0]
        if annot_type in _SKIP_TYPES:
            continue
        if annot_type in (fitz.PDF_ANNOT_LINE, fitz.PDF_ANNOT_POLY_LINE, fitz.PDF_ANNOT_INK):
            reach = tolerance + float((annot.border or {}).get("width") or 1) / 2
            if annot_type == fitz.PDF_ANNOT_INK:
                strokes = [[fitz.Point(p) for p in stroke] for stroke in annot.vertices or []]
            else:
                strokes = [[fitz.Point(p) for p in annot.vertices or []]]
            if any(_segment_distance(point, stroke[i], stroke[i + 1]) <= reach
                   for stroke in strokes for i in range(len(stroke) - 1)):
                hit = annot.xref
            continue
        if annot_type == fitz.PDF_ANNOT_POLYGON:
            polygon = [fitz.Point(p) for p in annot.vertices or []]
            if len(polygon) >= 3 and (_inside_polygon(point, polygon) or any(
                    _segment_distance(point, polygon[i - 1], polygon[i]) <= tolerance
                    for i in range(len(polygon)))):
                hit = annot.xref
            continue
        if annot_type == fitz.PDF_ANNOT_FREE_TEXT:
            if markup_bounds(page, annot.xref).contains(point) and (
                    annot.rect + (-tolerance, -tolerance, tolerance, tolerance)).contains(point):
                hit = annot.xref
                continue
            callout = page.parent.xref_get_key(annot.xref, "CL")
            if callout[0] == "array":
                line = _callout_points(page, callout[1])
                if any(_segment_distance(point, line[i], line[i + 1]) <= tolerance
                       for i in range(len(line) - 1)):
                    hit = annot.xref
            continue
        if (annot.rect + (-tolerance, -tolerance, tolerance, tolerance)).contains(point):
            hit = annot.xref
    return hit


def markups_in_rect(page: fitz.Page, rect: fitz.Rect) -> list[int]:
    """Markups whose bounds lie entirely inside rect (for box selection)."""
    rect = fitz.Rect(rect).normalize()
    return [annot.xref for annot in page.annots() or []
            if annot.type[0] not in _SKIP_TYPES and rect.contains(markup_bounds(page, annot.xref))]


# -- measurement ------------------------------------------------------------

@dataclass
class Scale:
    """Real-world units per PDF point, plus how to display them."""

    units_per_point: float = 1 / 72  # default: paper inches
    unit: str = "in"
    label: str = "1:1 (paper)"
    precision: int = 2

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "Scale":
        data = json.loads(text)
        return cls(**{key: data[key] for key in ("units_per_point", "unit", "label", "precision")
                      if key in data})


UNITS = ["in", "ft", "ft-in", "mm", "cm", "m", "pt"]
# Real inches per unit, used to turn "1/4 in = 1 ft" into units_per_point.
_INCHES = {"in": 1.0, "ft": 12.0, "ft-in": 12.0, "mm": 1 / 25.4, "cm": 1 / 2.54,
           "m": 1 / 0.0254, "pt": 1 / 72}


def scale_from_ratio(paper_length: float, paper_unit: str, real_length: float,
                     real_unit: str, label: str = "") -> Scale:
    """E.g. 0.25 in on paper = 1 ft real (architectural 1/4" = 1'-0")."""
    if paper_length <= 0 or real_length <= 0:
        raise ValueError("Scale lengths must be positive.")
    paper_points = paper_length * _INCHES[paper_unit] * 72
    return Scale(real_length / paper_points, real_unit,
                 label or f"{paper_length:g} {paper_unit} = {real_length:g} {real_unit}")


def scale_from_calibration(measured_points: float, real_length: float, unit: str) -> Scale:
    if measured_points <= 0 or real_length <= 0:
        raise ValueError("Draw a line and enter a positive known length.")
    return Scale(real_length / measured_points, unit,
                 f"Calibrated: {measured_points:.1f} pt = {format_length(real_length, unit)}")


COMMON_SCALES = {
    '1/16" = 1\'-0"': (1 / 16, "in", 1, "ft-in"), '1/8" = 1\'-0"': (1 / 8, "in", 1, "ft-in"),
    '3/16" = 1\'-0"': (3 / 16, "in", 1, "ft-in"), '1/4" = 1\'-0"': (1 / 4, "in", 1, "ft-in"),
    '3/8" = 1\'-0"': (3 / 8, "in", 1, "ft-in"), '1/2" = 1\'-0"': (1 / 2, "in", 1, "ft-in"),
    '3/4" = 1\'-0"': (3 / 4, "in", 1, "ft-in"), '1" = 1\'-0"': (1, "in", 1, "ft-in"),
    '1" = 10\'': (1, "in", 10, "ft"), '1" = 20\'': (1, "in", 20, "ft"),
    '1" = 50\'': (1, "in", 50, "ft"), '1" = 100\'': (1, "in", 100, "ft"),
    "1:50": (1, "mm", 50, "mm"), "1:100": (1, "mm", 100, "mm"),
    "1:200": (1, "mm", 200, "mm"), "1:500": (1, "mm", 500, "mm"),
}


def common_scale(name: str) -> Scale:
    paper, paper_unit, real, real_unit = COMMON_SCALES[name]
    scale = scale_from_ratio(paper, paper_unit, real, real_unit, name)
    if real_unit == "mm":
        scale.unit = "m"
        scale.units_per_point /= 1000
    return scale


def page_scale(page: fitz.Page) -> Scale:
    text = _get_string(page.parent, page.xref, SCALE_KEY)
    if text:
        try:
            return Scale.from_json(text)
        except (ValueError, TypeError):
            pass
    return Scale()


def set_page_scale(page: fitz.Page, scale: Scale) -> None:
    _set_string(page.parent, page.xref, SCALE_KEY, scale.to_json())
    for annot in list(page.annots() or []):
        if markup_kind(page, annot) in MEASURE_KINDS:
            refresh_measurement(page, annot.xref)


def format_length(value: float, unit: str, precision: int = 2) -> str:
    if unit == "ft-in":
        negative = value < 0
        total_sixteenths = round(abs(value) * 12 * 16)
        feet, rest = divmod(total_sixteenths, 12 * 16)
        inches, sixteenths = divmod(rest, 16)
        fraction = ""
        if sixteenths:
            denominator = 16
            while sixteenths % 2 == 0:
                sixteenths //= 2
                denominator //= 2
            fraction = f" {sixteenths}/{denominator}"
        return f"{'-' if negative else ''}{feet}'-{inches}{fraction}\""
    return f"{value:.{precision}f} {unit}"


def format_area(value: float, unit: str, precision: int = 2) -> str:
    unit = "ft" if unit == "ft-in" else unit
    return f"{value:.{precision}f} sq {unit}"


def _polyline_length(points: list[fitz.Point], closed: bool = False) -> float:
    total = sum(abs(points[i + 1] - points[i]) for i in range(len(points) - 1))
    if closed and len(points) > 2:
        total += abs(points[0] - points[-1])
    return total


def _polygon_area(points: list[fitz.Point]) -> float:
    return abs(sum(points[i - 1].x * points[i].y - points[i].x * points[i - 1].y
                   for i in range(len(points)))) / 2


def measurement(page: fitz.Page, xref: int) -> tuple[float, str] | None:
    """(value, display text) for a measurement markup, using the page scale."""
    annot = get_annot(page, xref)
    kind = markup_kind(page, annot)
    if kind not in MEASURE_KINDS:
        return None
    scale = page_scale(page)
    points = markup_points(page, xref)
    if kind == "count":
        return 1.0, "1"
    if kind == "angle":
        if len(points) != 3:
            return None
        a, b, c = points
        first = math.atan2(a.y - b.y, a.x - b.x)
        second = math.atan2(c.y - b.y, c.x - b.x)
        degrees = abs(math.degrees(first - second)) % 360
        degrees = 360 - degrees if degrees > 180 else degrees
        return degrees, f"{degrees:.1f}°"
    if kind == "area":
        value = _polygon_area(points) * scale.units_per_point ** 2
        return value, format_area(value, scale.unit, scale.precision)
    value = _polyline_length(points, closed=kind == "perimeter") * scale.units_per_point
    return value, format_length(value, scale.unit, scale.precision)


def refresh_measurement(page: fitz.Page, xref: int) -> None:
    """Write the measured value into the markup's comment (and line caption)."""
    result = measurement(page, xref)
    if result is None:
        return
    annot = get_annot(page, xref)
    info = annot.info
    info["content"] = result[1]
    annot.set_info(info)
    if annot.type[0] == fitz.PDF_ANNOT_LINE:
        page.parent.xref_set_key(xref, "Cap", "true")
        annot = get_annot(page, xref)
        annot.update()


def label_anchor(page: fitz.Page, xref: int) -> fitz.Point:
    """Where the canvas draws a measurement label (centroid or midpoint)."""
    points = markup_points(page, xref)
    if not points:
        return get_annot(page, xref).rect.tl
    if len(points) == 3 and markup_kind(page, get_annot(page, xref)) == "angle":
        return points[1]
    x = sum(p.x for p in points) / len(points)
    y = sum(p.y for p in points) / len(points)
    return fitz.Point(x, y)


# -- listing ------------------------------------------------------------------

def _format_date(value: str) -> str:
    if not value:
        return ""
    match = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?", value)
    if not match:
        return value
    parts = [int(part or 0) for part in match.groups()]
    try:
        return datetime(*parts).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def list_markups(doc: fitz.Document) -> list[dict]:
    """Rows for the Markups List: one per markup on every page."""
    rows = []
    for page in doc:
        for annot in page.annots() or []:
            if annot.type[0] in _SKIP_TYPES:
                continue
            kind = markup_kind(page, annot)
            info = annot.info
            colors = annot.colors or {}
            result = measurement(page, annot.xref) if kind in MEASURE_KINDS else None
            rows.append({
                "page": page.number,
                "xref": annot.xref,
                "kind": kind,
                "type": KIND_LABELS.get(kind, annot.type[1]),
                "subject": info.get("subject", ""),
                "author": info.get("title", ""),
                "comment": info.get("content", ""),
                "date": _format_date(info.get("modDate") or info.get("creationDate", "")),
                "status": _get_string(doc, annot.xref, STATUS_KEY),
                "color": rgb_to_hex(colors.get("stroke") or colors.get("fill")),
                "measurement": result[1] if result else "",
                "value": result[0] if result else None,
            })
    return rows


def export_markups_csv(rows: list[dict], output) -> None:
    import csv
    columns = ["page", "type", "subject", "comment", "author", "date", "status",
               "color", "measurement"]
    with open(output, "w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow([column.title() for column in columns])
        for row in rows:
            writer.writerow([row["page"] + 1 if column == "page" else row.get(column, "")
                             for column in columns])


def measurement_totals(rows: list[dict]) -> dict[str, tuple[float, int]]:
    """Total value and count of measurement rows grouped by subject."""
    totals: dict[str, tuple[float, int]] = {}
    for row in rows:
        if row["value"] is None:
            continue
        key = row["subject"] or row["type"]
        value, count = totals.get(key, (0.0, 0))
        totals[key] = (value + row["value"], count + 1)
    return totals
