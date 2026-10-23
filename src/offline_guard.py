"""Fail closed if Python code tries to open a network socket or URL request."""
from __future__ import annotations
import sys

_installed = False

def install_offline_guard() -> None:
    global _installed
    if _installed:
        return
    def deny_network(event: str, args: tuple[object, ...]) -> None:
        if event.startswith("socket.") or event.startswith("urllib.") or event.startswith("http.client."):
            raise PermissionError(f"Northstar PDF is offline-only: blocked {event}")
    sys.addaudithook(deny_network)
    _installed = True
