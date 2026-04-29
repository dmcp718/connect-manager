"""Single source of truth for the app version.

The version lives in /VERSION at the repo root. The Dockerfile COPYs it
into /app so containers see the same string the file system does. Falls
back to "0.0.0+unknown" if the file is missing (so a misbuilt image
doesn't crash on startup).
"""

from __future__ import annotations

from pathlib import Path


def _read_version() -> str:
    candidates = (
        Path(__file__).resolve().parent / "VERSION",
        Path(__file__).resolve().parent.parent / "VERSION",
    )
    for p in candidates:
        if p.is_file():
            value = p.read_text(encoding="utf-8").strip()
            if value:
                return value
    return "0.0.0+unknown"


__version__ = _read_version()
