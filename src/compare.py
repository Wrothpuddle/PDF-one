"""Drawing revision review: page overlay and automatic difference clouds.

Overlay renders two pages in grey and combines them so content only in the
older page shows red, content only in the newer page shows cyan, and
unchanged content stays black. Compare finds changed regions and returns
rectangles that the app turns into cloud markups on the newer document.

Both work on rendered pixels, so they compare what is visible (including
scanned sheets) rather than the PDF's internal drawing commands.
"""
from __future__ import annotations

import pymupdf as fitz


def _gray(page: fitz.Page, width: int, height: int) -> bytes:
    """Render page in greyscale to exactly width x height pixels."""
    rect = page.rect
    matrix = fitz.Matrix(width / rect.width, height / rect.height)
    pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY, alpha=False)
    if (pix.width, pix.height) == (width, height) and pix.stride == width:
        return bytes(pix.samples)
    # Rounding can differ by a pixel; copy row by row into the target size.
    out = bytearray(b"\xff" * (width * height))
    rows, cols = min(height, pix.height), min(width, pix.width)
    samples = pix.samples
    for y in range(rows):
        out[y * width:y * width + cols] = samples[y * pix.stride:y * pix.stride + cols]
    return bytes(out)


def _size(page: fitz.Page, dpi: float) -> tuple[int, int]:
    return max(1, round(page.rect.width * dpi / 72)), max(1, round(page.rect.height * dpi / 72))


def overlay_pixmap(old: fitz.Page, new: fitz.Page, dpi: float = 110) -> fitz.Pixmap:
    """Red = only in old, cyan = only in new, black = in both. Sized like the new page."""
    width, height = _size(new, dpi)
    old_gray = _gray(old, width, height)
    new_gray = _gray(new, width, height)
    rgb = bytearray(width * height * 3)
    rgb[0::3] = new_gray
    rgb[1::3] = old_gray
    rgb[2::3] = old_gray
    return fitz.Pixmap(fitz.csRGB, width, height, bytes(rgb), False)


def overlay_pdf(pairs: list[tuple[fitz.Page, fitz.Page]], dpi: float = 110) -> bytes:
    """A new PDF with one overlay page per (old, new) pair."""
    out = fitz.open()
    try:
        for old, new in pairs:
            pix = overlay_pixmap(old, new, dpi)
            page = out.new_page(width=new.rect.width, height=new.rect.height)
            page.insert_image(page.rect, pixmap=pix)
        return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()


def difference_regions(old: fitz.Page, new: fitz.Page, dpi: float = 60, cell: int = 6,
                       threshold: int = 80, padding: float = 4.0,
                       min_cells: int = 1) -> list[fitz.Rect]:
    """Changed areas as rectangles in the new page's unrotated coordinates."""
    width, height = _size(new, dpi)
    a = _gray(old, width, height)
    b = _gray(new, width, height)
    # Pixel-wise |a - b| > threshold, computed with a lookup on the XOR of
    # thresholded (ink / no ink) images: fast in pure Python and robust to
    # anti-aliasing noise.
    ink = bytes(1 if value < 255 - threshold else 0 for value in range(256))
    mask_a = a.translate(ink)
    mask_b = b.translate(ink)
    diff = (int.from_bytes(mask_a, "big") ^ int.from_bytes(mask_b, "big")).to_bytes(
        len(mask_a), "big")
    cols, rows = (width + cell - 1) // cell, (height + cell - 1) // cell
    changed = set()
    for cy in range(rows):
        y0, y1 = cy * cell, min(height, (cy + 1) * cell)
        for cx in range(cols):
            x0, x1 = cx * cell, min(width, (cx + 1) * cell)
            for y in range(y0, y1):
                if diff.find(1, y * width + x0, y * width + x1) != -1:
                    changed.add((cx, cy))
                    break
    regions = []
    seen = set()
    for start in changed:
        if start in seen:
            continue
        stack, group = [start], []
        seen.add(start)
        while stack:
            cx, cy = stack.pop()
            group.append((cx, cy))
            for nx in range(cx - 2, cx + 3):  # bridge small gaps
                for ny in range(cy - 2, cy + 3):
                    if (nx, ny) in changed and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
        if len(group) < min_cells:
            continue
        xs = [c[0] for c in group]
        ys = [c[1] for c in group]
        scale = 72 / dpi
        visual = fitz.Rect(min(xs) * cell * scale - padding, min(ys) * cell * scale - padding,
                           (max(xs) + 1) * cell * scale + padding,
                           (max(ys) + 1) * cell * scale + padding) & new.rect
        regions.append(visual * new.derotation_matrix)
    regions.sort(key=lambda r: (r.y0, r.x0))
    return regions
