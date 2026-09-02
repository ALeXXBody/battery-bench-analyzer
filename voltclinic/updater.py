"""Auto-update via GitHub Releases.

VoltClinic publishes its installer to a GitHub *release* for each version tag
(e.g. ``v0.2.0``). This module checks that release feed, compares the semver
of the latest release with the running version, and — when a newer release
exists — can download the new installer to a temp file and launch it.

The check is read-only and lightweight; it runs in a background thread in the
GUI so it never blocks startup. Downloading/launching is always user-confirmed.
"""
from __future__ import annotations

import os
import platform
import re
import subprocess
import tempfile
import threading
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from . import __version__

# Where VoltClinic publishes installers. Change here if you self-host.
# Overridable for testing/mirrors: VOLTCLINIC_RELEASE_API
DEFAULT_RELEASE_API = "https://api.github.com/repos/ALeXXBody/voltclinic/releases"


def _release_api() -> str:
    return os.environ.get("VOLTCLINIC_RELEASE_API", DEFAULT_RELEASE_API)

# Asset filename the updater prefers (matches the Inno Setup output and the
# version baked into the installer, e.g. VoltClinic-0.2.0-setup.exe).
ASSET_PREFIX = "VoltClinic"

_semver_re = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)(?:[-+](\S+))?\s*$")


@dataclass
class UpdateInfo:
    """Result of a successful update check."""
    latest_version: str
    tag: str
    release_url: str
    installers: List[str] = field(default_factory=list)  # asset download URLs


@dataclass
class CheckResult:
    is_newer: bool
    update: Optional[UpdateInfo] = None
    error: Optional[str] = None


def parse_version(version: str) -> Optional[tuple]:
    m = _semver_re.match(version or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def is_newer(latest: str, current: str) -> bool:
    a = parse_version(latest)
    b = parse_version(current)
    if a is None or b is None:
        return False
    return a > b


def matches_installer(name: str) -> bool:
    """True if ``name`` is a VoltClinic Windows installer asset (pure helper)."""
    return bool(name.startswith(ASSET_PREFIX) and name.endswith("-setup.exe"))


def _platform_asset_filter() -> Callable[[dict], bool]:
    """Match release assets for this platform's installer."""
    if platform.system() == "Windows":
        return lambda a: matches_installer(a.get("name", ""))
    return lambda a: False


def fetch_latest(timeout: int = 15) -> CheckResult:
    """Query GitHub for the latest release and compare with the running version.

    Never raises: network/parse failures are folded into ``CheckResult.error``
    so callers (GUI thread, CLI) stay robust offline.
    """
    try:
        req = urllib.request.Request(_release_api() + "/latest",
                                     headers={"User-Agent": "VoltClinic/" + __version__,
                                              "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        import json
        rel = json.loads(data.decode("utf-8"))
        tag = rel.get("tag_name", "")
        latest = tag.lstrip("v") if tag else ""
        assets = rel.get("assets", [])
        filt = _platform_asset_filter()
        installers = [a["browser_download_url"] for a in assets if filt(a)]
        info = UpdateInfo(latest_version=latest, tag=tag,
                          release_url=rel.get("html_url", ""),
                          installers=installers)
        return CheckResult(is_newer=is_newer(latest, __version__), update=info)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if "404" in msg or "HTTP Error 404" in msg:
            msg = ("release feed not found - the release repository is "
                   "private or does not exist, so in-app updates are "
                   "unavailable")
        return CheckResult(is_newer=False, error=msg)


def download(url: str, dest_dir: Optional[str] = None,
             progress: Optional[Callable[[int, int], None]] = None) -> str:
    """Download ``url`` (streaming) to a temp file and return its path.

    ``progress(total, done)`` is called as chunks arrive; use for a progress UI.
    """
    dest_dir = dest_dir or tempfile.gettempdir()
    fname = url.split("/")[-1] or "voltclinic-update.exe"
    dest = os.path.join(dest_dir, fname)
    req = urllib.request.Request(url, headers={"User-Agent": "VoltClinic/" + __version__})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        done = 0
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress is not None and total:
                    progress(int(total), done)
    return dest


def launch_installer(path: str) -> bool:
    """Launch the downloaded installer (Windows). Returns success."""
    if not os.path.exists(path):
        return False
    try:
        if platform.system() == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:  # noqa: BLE001
        return False


def check_in_background(on_done: Callable[[CheckResult], None],
                        timeout: int = 15) -> threading.Thread:
    """Run ``fetch_latest`` off the GUI thread and call ``on_done`` on it.

    Callers must dispatch ``on_done`` to the UI thread (e.g. via a Qt signal /
    ``QMetaObject.invokeMethod``).
    """
    def work():
        try:
            res = fetch_latest(timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            res = CheckResult(is_newer=False, error=str(exc))
        on_done(res)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t
