# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Tool Chest: reusable markup tools with saved properties, grouped in sets.

Built-in sets give one tool per markup type plus common construction-review
presets. "Recent Tools" collects tools as they are used, and "My Tools" (or any
set the user creates) holds tools saved from the Properties panel. Custom sets
are stored as JSON and can be exported and imported to share with others.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from markups import KIND_LABELS, MEASURE_KINDS, STAMPS, Style, default_style

RECENT = "Recent Tools"
MY_TOOLS = "My Tools"
RECENT_LIMIT = 12


@dataclass
class ToolPreset:
    name: str
    kind: str
    style: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def make_style(self) -> Style:
        base = default_style(self.kind).to_dict()
        base.update(self.style)
        return Style.from_dict(base)

    @classmethod
    def from_dict(cls, data: dict) -> "ToolPreset":
        if not isinstance(data, dict) or data.get("kind") not in KIND_LABELS:
            raise ValueError("Not a Northstar tool definition.")
        return cls(name=str(data.get("name") or KIND_LABELS[data["kind"]]),
                   kind=data["kind"], style=dict(data.get("style") or {}),
                   id=str(data.get("id") or uuid.uuid4().hex[:12]))


def _preset(kind: str, name: str | None = None, **changes) -> ToolPreset:
    style = default_style(kind)
    for key, value in changes.items():
        setattr(style, key, value)
    return ToolPreset(name or KIND_LABELS[kind], kind, style.to_dict(), id=f"builtin-{kind}-{name}")


def builtin_sets() -> dict[str, list[ToolPreset]]:
    markup_kinds = [kind for kind in KIND_LABELS if kind not in MEASURE_KINDS
                    and kind not in ("stamp", "redact")]
    return {
        "Markup": [_preset(kind) for kind in markup_kinds],
        "Measure": [_preset(kind) for kind in ("length", "polylength", "area", "volume", "perimeter",
                                               "count", "angle")],
        "Construction Review": [
            _preset("cloud", "Revision Cloud", stroke="#e02020", width=1.5, subject="Revision"),
            _preset("cloud", "Hold Cloud", stroke="#b000b5", width=2, subject="Hold"),
            _preset("callout", "RFI Callout", subject="RFI"),
            _preset("highlighter", "Green Highlighter", stroke="#2fd12f", subject="Verified"),
            _preset("highlighter", "Red Highlighter", stroke="#ff4040", subject="Issue"),
            _preset("rectangle", "Deleted Area", stroke="#e02020", fill="#e02020", opacity=0.25,
                    subject="Delete"),
            _preset("count", "Count: Outlet", subject="Outlet"),
            _preset("count", "Count: Fixture", stroke="#1f6feb", fill="#1f6feb", subject="Fixture"),
            _preset("area", "Area: Flooring", stroke="#2c9f2c", fill="#2c9f2c", subject="Flooring"),
            _preset("volume", "Volume: 4\" Slab", depth=4 / 12, subject="Concrete slab"),
        ],
        "Stamps": [_preset("stamp", name, stamp=name, subject=f"Stamp: {name}") for name in STAMPS],
        "Redaction": [_preset("redact")],
    }


class ToolChest:
    """Built-in sets plus user sets persisted to a JSON file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.builtin = builtin_sets()
        self.user: dict[str, list[ToolPreset]] = {RECENT: [], MY_TOOLS: []}
        if self.path and self.path.exists():
            try:
                self._load(json.loads(self.path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass  # A damaged file should not stop the app from starting.

    # -- persistence -----------------------------------------------------
    def _load(self, data: dict) -> None:
        for name, tools in (data.get("sets") or {}).items():
            loaded = []
            for item in tools:
                try:
                    loaded.append(ToolPreset.from_dict(item))
                except ValueError:
                    continue
            self.user[str(name)] = loaded
        self.user.setdefault(RECENT, [])
        self.user.setdefault(MY_TOOLS, [])

    def to_dict(self) -> dict:
        return {"format": "northstar-tool-chest", "version": 1,
                "sets": {name: [asdict(tool) for tool in tools]
                         for name, tools in self.user.items()}}

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    def export_set(self, name: str, output: str | Path) -> None:
        tools = self.sets()[name]
        payload = {"format": "northstar-tool-chest", "version": 1,
                   "sets": {name: [asdict(tool) for tool in tools]}}
        Path(output).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def import_sets(self, source: str | Path) -> list[str]:
        data = json.loads(Path(source).read_text(encoding="utf-8"))
        if data.get("format") != "northstar-tool-chest":
            raise ValueError("This file is not a Northstar Tool Chest export.")
        names = []
        for name, tools in (data.get("sets") or {}).items():
            target = name if name not in self.builtin else f"{name} (imported)"
            self.user[target] = [ToolPreset.from_dict(tool) for tool in tools]
            names.append(target)
        self.save()
        return names

    # -- editing -----------------------------------------------------------
    def sets(self) -> dict[str, list[ToolPreset]]:
        return {**self.user, **self.builtin}

    def is_builtin(self, name: str) -> bool:
        return name in self.builtin

    def find(self, tool_id: str) -> ToolPreset | None:
        return next((tool for tools in self.sets().values() for tool in tools
                     if tool.id == tool_id), None)

    def add(self, set_name: str, tool: ToolPreset) -> ToolPreset:
        if self.is_builtin(set_name):
            raise ValueError("Built-in tool sets cannot be changed.")
        self.user.setdefault(set_name, []).append(tool)
        self.save()
        return tool

    def remove(self, tool_id: str) -> None:
        for tools in self.user.values():
            tools[:] = [tool for tool in tools if tool.id != tool_id]
        self.save()

    def update(self, tool_id: str, name: str | None = None, style: Style | None = None) -> None:
        for tools in self.user.values():
            for tool in tools:
                if tool.id == tool_id:
                    if name:
                        tool.name = name
                    if style is not None:
                        tool.style = style.to_dict()
        self.save()

    def add_set(self, name: str) -> None:
        name = name.strip()
        if not name or name in self.sets():
            raise ValueError("Choose a new, non-empty set name.")
        self.user[name] = []
        self.save()

    def remove_set(self, name: str) -> None:
        if name in (RECENT, MY_TOOLS) or self.is_builtin(name):
            raise ValueError("This tool set cannot be removed.")
        self.user.pop(name, None)
        self.save()

    def note_used(self, kind: str, style: Style) -> None:
        """Remember a tool+properties combination in Recent Tools."""
        data = style.to_dict()
        recent = [tool for tool in self.user[RECENT]
                  if not (tool.kind == kind and tool.style == data)]
        name = style.subject or KIND_LABELS.get(kind, kind)
        recent.insert(0, ToolPreset(name, kind, data))
        self.user[RECENT] = recent[:RECENT_LIMIT]
        self.save()
