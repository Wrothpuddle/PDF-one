# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Autosave copies of unsaved documents, offered back after a crash.

Each dirty document is written to the recovery folder as <id>.pdf plus a
small <id>.json naming the original file. Copies are deleted when the
document is saved, closed normally, or discarded.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

# A running instance refreshes its files every minute; anything untouched for
# longer than this belongs to an instance that is no longer running.
STALE_AFTER = 180.0


class RecoveryStore:
    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)
        self._ids: dict[int, str] = {}  # id(session) -> recovery id

    def save(self, session) -> Path:
        """Write the session's current state; replaces its previous copy."""
        self.folder.mkdir(parents=True, exist_ok=True)
        key = self._ids.setdefault(id(session), uuid.uuid4().hex[:12])
        pdf = self.folder / f"{key}.pdf"
        staging = pdf.with_suffix(".tmp")
        staging.write_bytes(session.pdf_bytes())
        os.replace(staging, pdf)
        (self.folder / f"{key}.json").write_text(json.dumps({
            "original": str(session.path), "saved": time.time(),
            "encrypted": session.encrypted}), encoding="utf-8")
        return pdf

    def heartbeat(self) -> None:
        """Mark this instance's copies as still in use."""
        now = time.time()
        for key in self._ids.values():
            meta = self.folder / f"{key}.json"
            if meta.exists():
                os.utime(meta, (now, now))

    def forget(self, session) -> None:
        key = self._ids.pop(id(session), None)
        if key:
            for suffix in (".pdf", ".json", ".tmp"):
                (self.folder / f"{key}{suffix}").unlink(missing_ok=True)

    def pending(self) -> list[dict]:
        """Recovery copies left behind by a previous run (newest first)."""
        if not self.folder.is_dir():
            return []
        mine = set(self._ids.values())
        found = []
        now = time.time()
        for meta in self.folder.glob("*.json"):
            if meta.stem in mine or now - meta.stat().st_mtime < STALE_AFTER:
                continue
            pdf = meta.with_suffix(".pdf")
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {}
            if pdf.is_file():
                found.append({"id": meta.stem, "pdf": pdf, "original": info.get("original", ""),
                              "saved": float(info.get("saved", pdf.stat().st_mtime))})
            else:
                meta.unlink(missing_ok=True)
        return sorted(found, key=lambda item: item["saved"], reverse=True)

    def discard(self, recovery_id: str) -> None:
        for suffix in (".pdf", ".json", ".tmp"):
            (self.folder / f"{recovery_id}{suffix}").unlink(missing_ok=True)

    def claim(self, recovery_id: str, destination: str | Path) -> Path:
        """Move a recovery copy to destination (e.g. a temp file to open)."""
        destination = Path(destination)
        os.replace(self.folder / f"{recovery_id}.pdf", destination)
        self.discard(recovery_id)
        return destination
