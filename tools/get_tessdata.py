# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Download the pinned OCR language model into ./tessdata and verify it.

Used by the Windows build and for local development:

    python tools/get_tessdata.py

The model comes from tesseract-ocr/tessdata_fast 4.1.0 (Apache-2.0).
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/{lang}.traineddata"
SHA256 = {"eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"}


def main(languages: list[str]) -> int:
    folder = ROOT / "tessdata"
    folder.mkdir(exist_ok=True)
    for lang in languages:
        target = folder / f"{lang}.traineddata"
        if not target.exists():
            print(f"Downloading {lang}.traineddata …")
            with urllib.request.urlopen(URL.format(lang=lang), timeout=120) as response:
                target.write_bytes(response.read())
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        expected = SHA256.get(lang)
        if expected and digest != expected:
            target.unlink()
            print(f"{lang}.traineddata failed its checksum ({digest})", file=sys.stderr)
            return 1
        print(f"{target} OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["eng"]))
