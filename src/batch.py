# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Batch processing: apply the same actions to many PDFs.

Outputs go to a separate folder; the original files are never modified.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pymupdf as fitz

import links
import markups
import pdf_operations as ops
import report


@dataclass
class BatchOptions:
    auto_link: bool = False
    stamp: str = ""                 # stamp name for the first page ("" = none)
    scale: str = ""                 # COMMON_SCALES name to set on every page
    flatten: bool = False
    password: str = ""              # protect outputs with this password
    summary: bool = False           # write <name>_markup_summary.pdf
    csv: bool = False               # write <name>_markups.csv
    source_password: str = ""       # tried on encrypted inputs
    author: str = ""
    suffix: str = ""                # appended to output names (needed when folders match)


@dataclass
class BatchResult:
    source: Path
    ok: bool
    message: str
    outputs: list[Path] = field(default_factory=list)


def _stamp_first_page(doc: fitz.Document, name: str, author: str) -> None:
    page = doc[0]
    bounds = markups.page_bounds(page)
    width, height = 170.0, 50.0
    rect = fitz.Rect(bounds.x1 - width - 24, bounds.y0 + 24, bounds.x1 - 24, bounds.y0 + 24 + height)
    style = markups.default_style("stamp")
    style.stamp, style.subject = name, f"Stamp: {name}"
    markups.create_markup(page, "stamp", [rect.tl, rect.br], style, author=author)


def process_file(source: Path, folder: Path, options: BatchOptions) -> BatchResult:
    try:
        doc = ops.open_pdf(source, options.source_password or None)
    except PermissionError:
        return BatchResult(source, False, "Encrypted: enter its password to include it.")
    except Exception as exc:
        return BatchResult(source, False, str(exc))
    was_encrypted = bool(doc.metadata.get("encryption"))
    notes = []
    try:
        if options.scale:
            scale = markups.common_scale(options.scale)
            for page in doc:
                markups.set_page_scale(page, scale)
        if options.auto_link:
            notes.append(f"{links.auto_link_sheets(doc)} links")
        if options.stamp:
            _stamp_first_page(doc, options.stamp, options.author)
        rows = markups.list_markups(doc) if options.summary or options.csv else []
        if options.flatten:
            ops.flatten_markups(doc)
        stem = source.stem + options.suffix
        output = folder / f"{stem}.pdf"
        if output.resolve() == source.resolve():
            raise ValueError("The output would overwrite the original; choose another folder.")
        # Keep encrypted inputs encrypted (with their own password) unless a new
        # password was chosen for every output.
        password = options.password or (options.source_password if was_encrypted else "")
        written = [ops.write_new(doc, output, password or None, (source,))]
        if options.summary:
            written.append(report.write_summary(doc, folder / f"{stem}_markup_summary.pdf",
                                                source.name, rows))
        if options.csv:
            markups.export_markups_csv(rows, folder / f"{stem}_markups.csv")
            written.append(folder / f"{stem}_markups.csv")
        message = "Done" + (f" ({', '.join(notes)})" if notes else "")
        return BatchResult(source, True, message, written)
    except Exception as exc:
        return BatchResult(source, False, str(exc))
    finally:
        doc.close()


def run_batch(sources: list[str | Path], folder: str | Path, options: BatchOptions,
              progress: Callable[[int, str], bool] | None = None) -> list[BatchResult]:
    """Process each file; progress(index, name) may return False to stop early."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    sources = [Path(source) for source in sources]
    if not options.suffix and any(source.parent.resolve() == folder.resolve()
                                  for source in sources):
        options.suffix = "_batch"
    results = []
    for index, source in enumerate(sources):
        if progress is not None and progress(index, source.name) is False:
            break
        results.append(process_file(source, folder, options))
    return results
