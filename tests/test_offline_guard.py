# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def test_socket_creation_is_blocked_in_a_fresh_process():
    code = "from offline_guard import install_offline_guard; install_offline_guard(); import socket; socket.socket()"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "offline-only" in result.stderr

def test_application_source_does_not_import_network_modules():
    source = (ROOT / "src" / "main.py").read_text(encoding="utf-8")
    forbidden = ("PySide6.QtNetwork", "QNetworkAccessManager", "QWebEngine", "requests.", "urllib.request")
    assert not any(token in source for token in forbidden)
