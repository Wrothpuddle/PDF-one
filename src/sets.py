# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Sets: a named list of PDFs treated as one drawing package.

A set is a small JSON file (*.nset) listing PDF paths relative to the set
file, so a project folder can be moved or shared with its set. Sheet names
come from each page's bookmark title, else its page label, else its number.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz

import markups
import pdf_operations as ops

SET_SUFFIX = ".nset"
FORMAT = "northstar-set"


@dataclass
class Sheet:
    page: int
    title: str


@dataclass
class DrawingSet:
    path: Path
    name: str = "Drawing Set"
    files: list[Path] = field(default_factory=list)
    _sheet_cache: dict = field(default_factory=dict, repr=False)

    # -- persistence ---------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> "DrawingSet":
        path = Path(path).resolve()
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != FORMAT:
            raise ValueError("This file is not a Northstar set.")
        files = [(path.parent / entry).resolve() for entry in data.get("files", [])]
        return cls(path, str(data.get("name") or path.stem), files)

    def save(self) -> None:
        def relative(file: Path) -> str:
            try:
                return os.path.relpath(file, self.path.parent).replace(os.sep, "/")
            except ValueError:  # another drive on Windows
                return str(file)
        payload = {"format": FORMAT, "version": 1, "name": self.name,
                   "files": [relative(file) for file in self.files]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # -- editing -----------------------------------------------------------------
    def add(self, paths: list[str | Path]) -> int:
        added = 0
        for path in paths:
            path = Path(path).resolve()
            if path.suffix.lower() == ".pdf" and path not in self.files:
                self.files.append(path)
                added += 1
        return added

    def remove(self, path: str | Path) -> None:
        self.files = [file for file in self.files if file != Path(path).resolve()]

    def move(self, path: str | Path, step: int) -> None:
        path = Path(path).resolve()
        index = self.files.index(path)
        target = max(0, min(len(self.files) - 1, index + step))
        self.files.insert(target, self.files.pop(index))

    # -- reading ---------------------------------------------------------------
    def sheets(self, file: Path) -> list[Sheet] | None:
        """Sheets of one file (None if it is missing, locked, or unreadable)."""
        try:
            stamp = file.stat().st_mtime_ns
        except OSError:
            return None
        cached = self._sheet_cache.get(file)
        if cached and cached[0] == stamp:
            return cached[1]
        try:
            doc = ops.open_pdf(file)
        except Exception:
            return None
        try:
            titles: dict[int, str] = {}
            for _level, title, page in doc.get_toc(simple=True):
                if page >= 1 and page - 1 not in titles:
                    titles[page - 1] = title.strip()
            result = []
            for index in range(doc.page_count):
                label = doc[index].get_label()
                result.append(Sheet(index, titles.get(index) or
                                    (f"{label}" if label else f"Page {index + 1}")))
        finally:
            doc.close()
        self._sheet_cache[file] = (stamp, result)
        return result

    def search(self, text: str, open_docs: dict[Path, fitz.Document] | None = None,
               limit: int = 500) -> list[dict]:
        """Find text in every file: [{'file', 'page', 'rect', 'context'}]."""
        from panels import search_document
        hits = []
        for file in self.files:
            doc = (open_docs or {}).get(file)
            close = False
            if doc is None:
                try:
                    doc = ops.open_pdf(file)
                    close = True
                except Exception:
                    continue
            try:
                for page, rect, context in search_document(doc, text):
                    hits.append({"file": file, "page": page, "rect": rect, "context": context})
                    if len(hits) >= limit:
                        return hits
            finally:
                if close:
                    doc.close()
        return hits

    def markup_rows(self, open_docs: dict[Path, fitz.Document] | None = None) -> list[dict]:
        """Markups List rows for every file, each tagged with its file name."""
        rows = []
        for file in self.files:
            doc = (open_docs or {}).get(file)
            close = False
            if doc is None:
                try:
                    doc = ops.open_pdf(file)
                    close = True
                except Exception:
                    continue
            try:
                for row in markups.list_markups(doc):
                    row["file"] = file.name
                    rows.append(row)
            finally:
                if close:
                    doc.close()
        return rows
