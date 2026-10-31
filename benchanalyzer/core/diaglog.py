"""Diagnostics log for adapter connection attempts.

Writes timestamped lines to ``%LOCALAPPDATA%/BatteryBenchAnalyzer/diag.log`` on Windows
(or ``~/.benchanalyzer/diag.log`` elsewhere). Never raises: logging must not
break the adapter path it is observing.
"""

from __future__ import annotations

import os
import time


def _log_path() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "BatteryBenchAnalyzer", "diag.log")
    return os.path.join(os.path.expanduser("~"), ".benchanalyzer", "diag.log")


def _migrate_from_legacy(new_path: str) -> None:
    """One-time copy of the pre-rename VoltClinic diag.log, if present."""
    try:
        if os.path.exists(new_path):
            return
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
            legacy = os.path.join(base, "VoltClinic", "diag.log")
        else:
            legacy = os.path.join(os.path.expanduser("~"), ".voltclinic", "diag.log")
        if not os.path.exists(legacy):
            return
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        with open(legacy, "r", encoding="utf-8", errors="replace") as rf:
            data = rf.read()
        with open(new_path, "a", encoding="utf-8") as af:
            af.write(
                "\n[data migrated from the pre-rename VoltClinic install]\n" + data
            )
    except OSError:
        pass


def log(*lines: str) -> None:
    """Append human-readable diagnostic lines to the shared log file."""
    try:
        path = _log_path()
        _migrate_from_legacy(path)
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
