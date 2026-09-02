"""Tests for the GitHub-Releases updater's pure logic + live-HTTP behavior."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import voltclinic.updater as up
from voltclinic.updater import (
    CheckResult,
    fetch_latest,
    is_newer,
    matches_installer,
    parse_version,
)


def test_parse_version():
    assert parse_version("0.2.0") == (0, 2, 0)
    assert parse_version("v1.10.0") == (1, 10, 0)
    assert parse_version("1.0.0-beta.1") == (1, 0, 0)
    assert parse_version("garbage") is None
    assert parse_version("") is None


def test_is_newer():
    assert is_newer("0.2.0", "0.1.0")
    assert is_newer("1.0.0", "0.9.9")
    assert is_newer("v0.2.0", "0.2.0") is False  # equal
    assert is_newer("0.1.0", "0.2.0") is False   # older
    assert is_newer("0.2.0", "nonsense") is False  # bad current


def test_matches_installer():
    assert matches_installer("VoltClinic-0.2.0-setup.exe")
    assert matches_installer("VoltClinic-setup.exe")
    assert not matches_installer("VoltClinic.exe")       # portable exe, not installer
    assert not matches_installer("OtherClinic-0.2.0-setup.exe")
    assert not matches_installer("VoltClinic-0.2.0.msi")
    assert not matches_installer("")


# ---------------------------------------------------------------------------
# Live HTTP behavior against a local server (no mocks, no external network)
# ---------------------------------------------------------------------------

def _serve(handler_cls):
    srv = HTTPServer(("127.0.0.1", 0), handler_cls)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class _Feed(BaseHTTPRequestHandler):
    payload = {}
    status = 200

    def do_GET(self):
        if self.path.endswith("/latest"):
            body = json.dumps(self.payload).encode()
            self.send_response(self.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture()
def feed_server(monkeypatch):
    holder = {"srv": None}

    def _start(status=200, payload=None):
        attrs = {"payload": payload if payload is not None else {}, "status": status}
        cls = type("_FeedInst", (_Feed,), attrs)
        srv, url = _serve(cls)
        holder["srv"] = srv
        monkeypatch.setenv("VOLTCLINIC_RELEASE_API", url + "/releases")
        return url

    yield _start
    if holder["srv"] is not None:
        holder["srv"].shutdown()


def test_fetch_latest_404_reports_real_error_plus_hint(feed_server):
    feed_server(status=404)
    r = fetch_latest(timeout=5)
    assert r.is_newer is False
    assert r.update is None
    # the REAL http error is preserved, not replaced by a hardcoded claim
    assert "HTTP 404" in r.error
    assert "private" in r.error  # hint only, conditional wording
    assert "until the feed is reachable" in r.error


def test_fetch_latest_newer_release_detected(feed_server):
    payload = {
        "tag_name": "v9.9.9",
        "html_url": "http://example.invalid/r",
        "assets": [
            {"name": "VoltClinic.exe",
             "browser_download_url": "http://x/VoltClinic.exe"},
            {"name": "VoltClinic-9.9.9-setup.exe",
             "browser_download_url": "http://x/VoltClinic-9.9.9-setup.exe"},
        ],
    }
    feed_server(status=200, payload=payload)
    r = fetch_latest(timeout=5)
    assert r.error is None
    assert r.is_newer is True
    assert r.update.latest_version == "9.9.9"
    # only the -setup.exe asset counts as an installer
    assert r.update.installers == ["http://x/VoltClinic-9.9.9-setup.exe"]


def test_fetch_latest_same_version_is_up_to_date(feed_server):
    payload = {"tag_name": "v" + __import__("voltclinic").__version__, "assets": []}
    feed_server(status=200, payload=payload)
    r = fetch_latest(timeout=5)
    assert r.error is None
    assert r.is_newer is False
