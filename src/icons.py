# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Toolbar icons drawn with QPainter, so the app ships no image files.

Each drawer paints into a 24x24 box. Icons are rendered at 1x and 2x for
high-DPI screens, in a foreground colour that follows the theme.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

RED, YELLOW, BLUE, GREEN = QColor("#e02020"), QColor("#f5c400"), QColor("#1f6feb"), QColor("#2c9f2c")


def _pen(colour: QColor, width: float = 1.7, style=Qt.PenStyle.SolidLine) -> QPen:
    pen = QPen(colour, width, style)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _poly(*points) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in points])


def _arrow_head(p: QPainter, tip: QPointF, angle: float, size: float = 5) -> None:
    left = tip - QPointF(math.cos(angle - 0.5) * size, math.sin(angle - 0.5) * size)
    right = tip - QPointF(math.cos(angle + 0.5) * size, math.sin(angle + 0.5) * size)
    p.drawPolyline(QPolygonF([left, tip, right]))


def _cloud(p: QPainter, rect: QRectF) -> None:
    path = QPainterPath()
    bumps_x, bumps_y = 4, 3
    w, h = rect.width() / bumps_x, rect.height() / bumps_y
    path.moveTo(rect.topLeft())
    for i in range(bumps_x):
        path.arcTo(QRectF(rect.left() + i * w, rect.top() - h / 2, w, h), 180, -180)
    for i in range(bumps_y):
        path.arcTo(QRectF(rect.right() - w / 2, rect.top() + i * h, w, h), 90, -180)
    for i in range(bumps_x):
        path.arcTo(QRectF(rect.right() - (i + 1) * w, rect.bottom() - h / 2, w, h), 0, -180)
    for i in range(bumps_y):
        path.arcTo(QRectF(rect.left() - w / 2, rect.bottom() - (i + 1) * h, w, h), 270, -180)
    p.drawPath(path)


def _draw(name: str, p: QPainter, fg: QColor) -> None:
    pen = _pen(fg)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    if name == "select":
        p.setBrush(QBrush(fg))
        p.drawPolygon(_poly((6, 3), (6, 19), (10, 15), (13, 21), (15.5, 20), (12.5, 14), (18, 14)))
    elif name == "pan":
        for angle in (0, 90, 180, 270):
            rad = math.radians(angle)
            end = QPointF(12 + 9 * math.cos(rad), 12 + 9 * math.sin(rad))
            p.drawLine(QPointF(12, 12), end)
            _arrow_head(p, end, rad, 4)
    elif name == "text_select":
        p.drawLine(QPointF(12, 4), QPointF(12, 20))
        p.drawLine(QPointF(9, 4), QPointF(15, 4))
        p.drawLine(QPointF(9, 20), QPointF(15, 20))
        p.fillRect(QRectF(3, 9, 6, 6), QColor(31, 111, 235, 90))
        p.fillRect(QRectF(15, 9, 6, 6), QColor(31, 111, 235, 90))
    elif name in ("zoom_area", "zoom_in", "zoom_out"):
        p.drawEllipse(QRectF(4, 4, 12, 12))
        p.setPen(_pen(fg, 2.6))
        p.drawLine(QPointF(14.5, 14.5), QPointF(20, 20))
        p.setPen(pen)
        if name != "zoom_area":
            p.drawLine(QPointF(7, 10), QPointF(13, 10))
        if name == "zoom_in":
            p.drawLine(QPointF(10, 7), QPointF(10, 13))
        if name == "zoom_area":
            p.setPen(_pen(fg, 1.2, Qt.PenStyle.DotLine))
            p.drawRect(QRectF(7, 7, 6, 6))
    elif name == "fit_width":
        p.drawRect(QRectF(5, 4, 14, 16))
        p.drawLine(QPointF(2, 12), QPointF(22, 12))
        _arrow_head(p, QPointF(2, 12), math.pi, 3)
        _arrow_head(p, QPointF(22, 12), 0, 3)
    elif name == "fit_page":
        p.drawRect(QRectF(5, 3, 14, 18))
        p.drawRect(QRectF(8, 7, 8, 10))
    elif name in ("prev_page", "next_page"):
        direction = -1 if name == "prev_page" else 1
        p.drawRect(QRectF(6, 3, 12, 18))
        p.drawPolyline(_poly((12 - 2 * direction, 9), (12 + 2 * direction, 12), (12 - 2 * direction, 15)))
    elif name == "crop":
        p.drawPolyline(_poly((7, 2), (7, 17), (22, 17)))
        p.drawPolyline(_poly((2, 7), (17, 7), (17, 22)))
    elif name == "snapshot":
        p.drawRoundedRect(QRectF(3, 7, 18, 13), 2, 2)
        p.drawRect(QRectF(8, 4, 8, 3))
        p.drawEllipse(QRectF(8.5, 9.5, 7, 7))
    elif name == "calibrate":
        p.drawRect(QRectF(2, 9, 20, 7))
        for x in range(4, 22, 3):
            p.drawLine(QPointF(x, 9), QPointF(x, 12 if (x - 4) % 6 else 13.5))
        p.setPen(_pen(GREEN, 2))
        p.drawPolyline(_poly((13, 5), (15, 7), (20, 2)))
    elif name == "redact":
        p.fillRect(QRectF(3, 9, 18, 6), fg)
        p.drawLine(QPointF(3, 5), QPointF(12, 5))
        p.drawLine(QPointF(3, 19), QPointF(16, 19))
    elif name == "text":
        p.setPen(_pen(fg, 1.2))
        p.drawRect(QRectF(2.5, 3.5, 19, 17))
        font = QFont("Arial", 12, QFont.Weight.Bold)
        p.setFont(font)
        p.drawText(QRectF(2, 3, 20, 18), Qt.AlignmentFlag.AlignCenter, "T")
    elif name == "callout":
        p.setPen(_pen(RED, 1.6))
        p.drawRect(QRectF(9, 3, 13, 9))
        p.drawLine(QPointF(9, 7.5), QPointF(3, 20))
        _arrow_head(p, QPointF(3, 20), math.atan2(12.5, -6), 4)
    elif name == "note":
        p.setBrush(QBrush(YELLOW))
        p.setPen(_pen(QColor("#9a7a00"), 1.3))
        p.drawPolygon(_poly((4, 4), (20, 4), (20, 15), (14, 20), (4, 20)))
        p.drawPolyline(_poly((14, 20), (14, 15), (20, 15)))
    elif name == "highlight":
        p.fillRect(QRectF(2, 8, 20, 9), QColor(245, 196, 0, 150))
        p.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        p.drawText(QRectF(2, 6, 20, 13), Qt.AlignmentFlag.AlignCenter, "ab")
    elif name == "underline":
        p.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        p.drawText(QRectF(2, 2, 20, 16), Qt.AlignmentFlag.AlignCenter, "U")
        p.setPen(_pen(BLUE, 2))
        p.drawLine(QPointF(5, 20), QPointF(19, 20))
    elif name == "strikeout":
        p.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        p.drawText(QRectF(2, 3, 20, 18), Qt.AlignmentFlag.AlignCenter, "S")
        p.setPen(_pen(RED, 2))
        p.drawLine(QPointF(4, 12.5), QPointF(20, 12.5))
    elif name == "pen":
        path = QPainterPath(QPointF(3, 16))
        path.cubicTo(QPointF(7, 4), QPointF(10, 22), QPointF(14, 10))
        path.cubicTo(QPointF(16, 5), QPointF(19, 12), QPointF(21, 8))
        p.setPen(_pen(RED, 2))
        p.drawPath(path)
    elif name == "highlighter":
        p.setPen(_pen(QColor(245, 196, 0, 190), 6))
        p.drawLine(QPointF(4, 17), QPointF(20, 7))
    elif name == "line":
        p.setPen(_pen(RED, 2))
        p.drawLine(QPointF(4, 20), QPointF(20, 4))
    elif name == "arrow":
        p.setPen(_pen(RED, 2))
        p.drawLine(QPointF(4, 20), QPointF(20, 4))
        _arrow_head(p, QPointF(20, 4), -math.pi / 4, 6)
    elif name == "polyline":
        p.setPen(_pen(RED, 2))
        p.drawPolyline(_poly((3, 18), (9, 7), (15, 15), (21, 5)))
    elif name == "polygon":
        p.setPen(_pen(RED, 2))
        p.drawPolygon(_poly((12, 3), (21, 10), (17, 21), (7, 21), (3, 10)))
    elif name == "cloud":
        p.setPen(_pen(RED, 1.5))
        _cloud(p, QRectF(5, 6, 14, 12))
    elif name == "rectangle":
        p.setPen(_pen(RED, 2))
        p.drawRect(QRectF(4, 6, 16, 12))
    elif name == "ellipse":
        p.setPen(_pen(RED, 2))
        p.drawEllipse(QRectF(3, 6, 18, 12))
    elif name == "stamp":
        p.setPen(_pen(RED, 1.6))
        p.drawRoundedRect(QRectF(2, 7, 20, 10), 2, 2)
        p.setFont(QFont("Arial", 6, QFont.Weight.Bold))
        p.drawText(QRectF(2, 7, 20, 10), Qt.AlignmentFlag.AlignCenter, "OK")
    elif name in ("length", "polylength"):
        p.setPen(_pen(BLUE, 1.6))
        if name == "length":
            p.drawLine(QPointF(3, 14), QPointF(21, 14))
            for x in (3, 21):
                p.drawLine(QPointF(x, 10), QPointF(x, 18))
            p.setFont(QFont("Arial", 6))
            p.drawText(QRectF(3, 3, 18, 9), Qt.AlignmentFlag.AlignCenter, "10'")
        else:
            p.drawPolyline(_poly((3, 19), (9, 9), (15, 16), (21, 5)))
            for x, y in ((3, 19), (9, 9), (15, 16), (21, 5)):
                p.drawEllipse(QPointF(x, y), 1.4, 1.4)
    elif name == "area":
        p.setPen(_pen(BLUE, 1.6))
        p.setBrush(QColor(31, 111, 235, 80))
        p.drawPolygon(_poly((4, 5), (20, 5), (20, 13), (12, 13), (12, 20), (4, 20)))
    elif name == "perimeter":
        p.setPen(_pen(BLUE, 1.8, Qt.PenStyle.DashLine))
        p.drawPolygon(_poly((4, 5), (20, 5), (20, 19), (4, 19)))
    elif name == "count":
        p.setBrush(QBrush(RED))
        p.setPen(Qt.PenStyle.NoPen)
        for x, y in ((6, 7), (16, 6), (8, 17), (18, 16)):
            p.drawEllipse(QPointF(x, y), 2.6, 2.6)
    elif name == "angle":
        p.setPen(_pen(BLUE, 1.7))
        p.drawPolyline(_poly((21, 20), (4, 20), (16, 5)))
        p.drawArc(QRectF(-2, 14, 14, 12), 0, 52 * 16)
    elif name == "lock":
        p.drawRoundedRect(QRectF(5, 11, 14, 10), 2, 2)
        p.drawArc(QRectF(8, 4, 8, 12), 0, 180 * 16)
    elif name == "snap":
        p.setPen(_pen(QColor("#e8590c"), 1.8))
        p.drawRect(QRectF(8, 8, 8, 8))
        p.setPen(_pen(fg, 1.3))
        p.drawLine(QPointF(2, 12), QPointF(22, 12))
        p.drawLine(QPointF(12, 2), QPointF(12, 22))
    elif name in ("undo", "redo"):
        path = QPainterPath()
        if name == "undo":
            path.moveTo(5, 10)
            path.cubicTo(QPointF(9, 4), QPointF(20, 5), QPointF(19, 15))
            p.drawPath(path)
            _arrow_head(p, QPointF(5, 10), math.radians(150), 5)
        else:
            path.moveTo(19, 10)
            path.cubicTo(QPointF(15, 4), QPointF(4, 5), QPointF(5, 15))
            p.drawPath(path)
            _arrow_head(p, QPointF(19, 10), math.radians(30), 5)
    elif name == "open":
        p.drawPolygon(_poly((2, 6), (9, 6), (11, 8), (21, 8), (21, 19), (2, 19)))
    elif name == "save":
        p.drawRoundedRect(QRectF(3, 3, 18, 18), 2, 2)
        p.drawRect(QRectF(7, 3, 10, 6))
        p.drawRect(QRectF(7, 13, 10, 8))
    elif name == "print":
        p.drawRect(QRectF(6, 3, 12, 6))
        p.drawRoundedRect(QRectF(2, 9, 20, 8), 2, 2)
        p.setBrush(QBrush(QColor("white")))
        p.drawRect(QRectF(6, 14, 12, 7))
    else:
        p.drawRect(QRectF(5, 5, 14, 14))


def make_icon(name: str, foreground: QColor | str = "#2b2b2b") -> QIcon:
    fg = QColor(foreground)
    icon = QIcon()
    for scale in (1, 2):
        pixmap = QPixmap(24 * scale, 24 * scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.scale(scale, scale)
        _draw(name, painter, fg)
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        icon.addPixmap(pixmap)
    return icon


ICON_NAMES = ["select", "pan", "text_select", "zoom_area", "zoom_in", "zoom_out", "fit_width",
              "fit_page", "prev_page", "next_page", "crop", "snapshot", "calibrate", "redact",
              "text", "callout", "note", "highlight", "underline", "strikeout", "pen",
              "highlighter", "line", "arrow", "polyline", "polygon", "cloud", "rectangle",
              "ellipse", "stamp", "length", "polylength", "area", "perimeter", "count", "angle",
              "lock", "snap", "undo", "redo", "open", "save", "print"]
