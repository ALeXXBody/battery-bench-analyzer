"""Tests for the GitHub-Releases updater's pure logic (no network)."""
import pytest

from voltclinic.updater import is_newer, matches_installer, parse_version


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
