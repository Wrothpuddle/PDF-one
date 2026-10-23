"""Per-tab transactional edit session with disk-backed undo/redo snapshots.

Original PDFs are never changed until save() is explicitly requested.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from pdf_operations import add_sticky_note, rotate_page


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

    def rotate(self, page_index: int, degrees: int = 90,
               password: str | None = None) -> Path:
        output = self._temp_dir / f"revision-{len(self._states)}.pdf"
        rotate_page(self.working_path, output, page_index, degrees, password)
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
