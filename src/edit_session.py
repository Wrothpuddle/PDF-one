"""Per-tab transactional edit session with disk-backed undo/redo snapshots.

Original PDFs are never changed until save() is explicitly requested.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from pdf_operations import (add_sticky_note, crop_page, delete_page, insert_pages,
                             reorder_pages, replace_page, rotate_page)


class EditSession:
    def __init__(self, original_path: str | Path) -> None:
        self.original_path = Path(original_path).resolve()
        self._temp_dir = Path(tempfile.mkdtemp(prefix="northstar-edit-"))
        self._states: list[Path] = [self.original_path]
        self._cursor = 0

    @property
    def working_path(self) -> Path:
        return self._states[self._cursor]

    @property
    def dirty(self) -> bool:
        return self._cursor != 0

    @property
    def can_undo(self) -> bool:
        return self._cursor > 0

    @property
    def can_redo(self) -> bool:
        return self._cursor < len(self._states) - 1

    def apply_operation(self, operation, *args, **kwargs) -> Path:
        """Run a page edit against the current revision and add it to undo history."""
        output = self._temp_dir / f"revision-{len(self._states)}.pdf"
        operation(self.working_path, output, *args, **kwargs)
        self._states = self._states[: self._cursor + 1]
        self._states.append(output)
        self._cursor += 1
        return output

    def reorder(self, order: list[int], password: str | None = None) -> Path:
        return self.apply_operation(reorder_pages, order, password=password)

    def crop(self, page_index: int, left: float, bottom: float, right: float,
             top: float, password: str | None = None) -> Path:
        return self.apply_operation(crop_page, page_index, left, bottom, right, top,
                                    password=password)

    def insert(self, source: str | Path, before_page: int,
               password: str | None = None) -> Path:
        return self.apply_operation(insert_pages, source, before_page, password=password)

    def replace(self, source: str | Path, page_index: int,
                password: str | None = None) -> Path:
        return self.apply_operation(replace_page, source, page_index, password=password)

    def rotate(self, page_index: int, degrees: int = 90,
               password: str | None = None) -> Path:
        output = self._temp_dir / f"revision-{len(self._states)}.pdf"
        rotate_page(self.working_path, output, page_index, degrees, password)
        self._states = self._states[: self._cursor + 1]
        self._states.append(output)
        self._cursor += 1
        return output

    def delete_page(self, page_index: int, password: str | None = None) -> Path:
        output = self._temp_dir / f"revision-{len(self._states)}.pdf"
        delete_page(self.working_path, output, page_index, password)
        self._states = self._states[: self._cursor + 1]
        self._states.append(output)
        self._cursor += 1
        return output

    def add_sticky_note(self, page_index: int, text: str,
                        password: str | None = None) -> Path:
        output = self._temp_dir / f"revision-{len(self._states)}.pdf"
        add_sticky_note(self.working_path, output, page_index, text, password)
        self._states = self._states[: self._cursor + 1]
        self._states.append(output)
        self._cursor += 1
        return output

    def undo(self) -> Path:
        if not self.can_undo:
            return self.working_path
        self._cursor -= 1
        return self.working_path

    def redo(self) -> Path:
        if not self.can_redo:
            return self.working_path
        self._cursor += 1
        return self.working_path

    def save(self) -> None:
        if not self.dirty:
            return
        staging = self.original_path.with_name(self.original_path.name + ".northstar-save.tmp")
        try:
            shutil.copy2(self.working_path, staging)
            staging.replace(self.original_path)
        finally:
            if staging.exists():
                staging.unlink()
        self._states = [self.original_path]
        self._cursor = 0

    def discard(self) -> Path:
        self._states = [self.original_path]
        self._cursor = 0
        return self.original_path

    def cleanup(self) -> None:
        shutil.rmtree(self._temp_dir, ignore_errors=True)
