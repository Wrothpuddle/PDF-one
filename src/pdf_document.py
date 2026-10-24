"""In-memory PDF document with undo/redo, used by every editing tool.

The PDF is read once into memory, so the source file is never locked and can be
replaced on save (Windows will not replace an open file). Edits run against
that in-memory document instead of writing a new revision to disk.

Undo uses MuPDF's built-in journal. A few MuPDF operations (creating or
inserting pages) cannot run inside a journalled operation, so those are applied
to a fresh copy of the document; undoing them swaps the previous document back
in. History therefore holds two kinds of entry:

    ("journal", doc, label)       undo/redo through doc.journal_undo/redo()
    ("swap", old_doc, new_doc, label)  undo/redo by switching documents

PyMuPDF is AGPL-3.0-or-later or commercially licensed by Artifex.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import pymupdf as fitz


class PdfSession:
    """One open PDF with its undo history and save state."""

    def __init__(self, path: str | Path, password: str | None = None) -> None:
        self.path = Path(path).resolve()
        doc = fitz.open(stream=self.path.read_bytes(), filetype="pdf")
        if not doc.is_pdf:
            doc.close()
            raise ValueError("The file is not a PDF.")
        self.encrypted = bool(doc.needs_pass)
        if self.encrypted and not doc.authenticate(password or ""):
            doc.close()
            raise PermissionError("The PDF is encrypted; a valid password is required.")
        if doc.page_count == 0:
            doc.close()
            raise ValueError("The PDF contains no pages.")
        doc.journal_enable()
        self.password = password if self.encrypted else None
        self.doc = doc
        self._history: list[tuple] = []
        self._cursor = 0
        self._saved_cursor = 0
        self.revision = 0
        self._listeners: list[Callable[[], None]] = []

    # -- state -----------------------------------------------------------
    @property
    def page_count(self) -> int:
        return self.doc.page_count

    @property
    def dirty(self) -> bool:
        return self._cursor != self._saved_cursor

    @property
    def can_undo(self) -> bool:
        return self._cursor > 0

    @property
    def can_redo(self) -> bool:
        return self._cursor < len(self._history)

    @property
    def undo_label(self) -> str:
        return self._history[self._cursor - 1][-1] if self.can_undo else ""

    @property
    def redo_label(self) -> str:
        return self._history[self._cursor][-1] if self.can_redo else ""

    def add_listener(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def _changed(self) -> None:
        self.revision += 1
        for callback in list(self._listeners):
            callback()

    # -- editing ---------------------------------------------------------
    def _drop_redo(self) -> None:
        dropped = self._history[self._cursor:]
        del self._history[self._cursor:]
        if self._saved_cursor > self._cursor:
            self._saved_cursor = -1
        keep = {id(self.doc)} | {id(entry[1]) for entry in self._history}
        keep |= {id(entry[2]) for entry in self._history if entry[0] == "swap"}
        for entry in dropped:
            if entry[0] == "swap" and id(entry[2]) not in keep:
                entry[2].close()

    def apply(self, label: str, operation: Callable[[fitz.Document], object]):
        """Run operation(doc) as one undoable step and return its result."""
        doc = self.doc
        self._drop_redo()
        doc.journal_start_op(label)
        try:
            result = operation(doc)
        except Exception:
            doc.journal_stop_op()
            # Roll back whatever the failed operation already changed.
            if doc.journal_can_do().get("undo"):
                doc.journal_undo()
            raise
        doc.journal_stop_op()
        self._history.append(("journal", doc, label))
        self._cursor += 1
        self._changed()
        return result

    def apply_copy(self, label: str, operation: Callable[[fitz.Document], object]):
        """Run operation on a copy of the document, for steps the journal cannot record."""
        old = self.doc
        scratch = fitz.open(stream=old.tobytes(), filetype="pdf")
        try:
            result = operation(scratch)
            data = scratch.tobytes()
        finally:
            scratch.close()
        new = fitz.open(stream=data, filetype="pdf")
        new.journal_enable()
        self._drop_redo()
        self._history.append(("swap", old, new, label))
        self._cursor += 1
        self.doc = new
        self._changed()
        return result

    def undo(self) -> bool:
        if not self.can_undo:
            return False
        entry = self._history[self._cursor - 1]
        if entry[0] == "journal":
            entry[1].journal_undo()
        else:
            self.doc = entry[1]
        self._cursor -= 1
        self._changed()
        return True

    def redo(self) -> bool:
        if not self.can_redo:
            return False
        entry = self._history[self._cursor]
        if entry[0] == "journal":
            entry[1].journal_redo()
        else:
            self.doc = entry[2]
        self._cursor += 1
        self._changed()
        return True

    # -- saving ----------------------------------------------------------
    def pdf_bytes(self, encrypt: bool | None = None) -> bytes:
        """Serialize the current document, compacted, optionally re-encrypted."""
        # Compact a copy: garbage collection on the live document renumbers
        # objects and would break the undo journal.
        copy = fitz.open(stream=self.doc.tobytes(), filetype="pdf")
        try:
            options = {"garbage": 3, "deflate": True}
            if self.encrypted if encrypt is None else encrypt:
                options.update(encryption=fitz.PDF_ENCRYPT_AES_256,
                               user_pw=self.password or "", owner_pw=self.password or "")
            return copy.tobytes(**options)
        finally:
            copy.close()

    def save(self, path: str | Path | None = None) -> Path:
        """Write the document to path (default: its own file) atomically."""
        target = Path(path).resolve() if path else self.path
        if target.suffix.lower() != ".pdf":
            raise ValueError("Output path must end in .pdf")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(target.name + ".northstar-save.tmp")
        try:
            staging.write_bytes(self.pdf_bytes())
            os.replace(staging, target)
        finally:
            if staging.exists():
                staging.unlink()
        self.path = target
        self._saved_cursor = self._cursor
        self._changed()
        return target

    def close(self) -> None:
        docs = {id(self.doc): self.doc}
        for entry in self._history:
            docs[id(entry[1])] = entry[1]
            if entry[0] == "swap":
                docs[id(entry[2])] = entry[2]
        for doc in docs.values():
            if not doc.is_closed:
                doc.close()
        self._history.clear()
        self._listeners.clear()
