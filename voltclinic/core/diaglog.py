"""Diagnostics log for adapter connection attempts.

Writes timestamped lines to ``%LOCALAPPDATA%/VoltClinic/diag.log`` on Windows
(or ``~/.voltclinic/diag.log`` elsewhere). Never raises: logging must not
break the adapter path it is observing.
"""

from __future__ import annotations

import os
import time


def _log_path() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "VoltClinic", "diag.log")
    return os.path.join(os.path.expanduser("~"), ".voltclinic", "diag.log")


def log(*lines: str) -> None:
    """Append human-readable diagnostic lines to the shared log file."""
    try:
        path = _log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.writelines(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {line}\n" for line in lines
            )
    except OSError:
        pass


def summary(paths: list[tuple]) -> str:
    """Render an enumeration result: [(path, vid, pid), ...]."""
    if not paths:
        return "<no HID interfaces enumerated>"
    out = []
    for p, v, i in paths:
        short = p[-70:] if len(p) > 75 else p
        out.append(f"  {v:04X}:{i:04X}  {short}")
    return "\n".join(out)
