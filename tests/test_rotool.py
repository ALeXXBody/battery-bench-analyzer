"""Tests for the ROTOOL serial transport (pure parts, no hardware)."""

from unittest import mock

import pytest

from benchanalyzer.unlock.rotool import (
    choose_port,
    frame_escape,
    frame_unescape,
    frame_wrap,
    frame_wrap_crc,
)
from benchanalyzer.unlock.smbus import SMbusError


def test_frame_escape_passthrough():
    assert frame_escape(b"\x01\x02\x7f") == b"\x01\x02\x7f"


def test_frame_escape_delimiter_and_escape_byte():
    assert frame_escape(b"\x7e") == b"\x7d\x5e"
    assert frame_escape(b"\x7d") == b"\x7d\x5d"
    assert frame_escape(b"\x7e\x7d\x01") == b"\x7d\x5e\x7d\x5d\x01"


def test_frame_unescape_roundtrip():
    payload = b"\x7e\x7d\x01\x02"
    assert frame_unescape(frame_escape(payload)) == payload


def test_frame_unescape_malformed():
    with pytest.raises(SMbusError):
        frame_unescape(b"\x01\x7d")  # escape with no following byte


def test_frame_wrap_delimiters():
    f = frame_wrap(b"\x01\x02\x7e")
    assert f[0] == 0x7E and f[-1] == 0x7E
    assert frame_unescape(f[1:-1]) == b"\x01\x02\x7e"


def test_frame_wrap_crc_layout():
    payload = b"\x01\x02"
    f = frame_wrap_crc(payload)
    # [0]=7E, [1]=crc, [2:]=escaped payload, [-1]=7E
    assert f[0] == 0x7E and f[-1] == 0x7E
    assert frame_unescape(f[2:-1]) == payload
    assert f[1] == 0x01 ^ 0x02


def test_frame_crc_matches_xor():
    payload = bytes([0x0F, 0xF0, 0xAA])
    crc = 0
    for b in payload:
        crc ^= b
    assert frame_wrap_crc(payload)[1] == crc


def test_choose_port_prefers_explicit():
    assert choose_port("COM7") == "COM7"


def test_choose_port_raises_without_device():
    # No explicit name, no detected device: must raise, never guess a port.
    with mock.patch("benchanalyzer.unlock.rotool.list_rotool_ports", return_value=[]):
        with pytest.raises(SMbusError):
            choose_port(None)


def test_choose_port_uses_detected_device():
    with mock.patch(
        "benchanalyzer.unlock.rotool.list_rotool_ports", return_value=["/dev/ttyUSB0"]
    ):
        assert choose_port(None) == "/dev/ttyUSB0"


def test_cp2112_configure_builds_valid_config_report():
    """_configure must only use kwargs that report_smbus_config supports."""
    from benchanalyzer.unlock import cp2112 as cp2112_mod

    class _StubDev:
        def __init__(self):
            self.stored = []

        def set_feature(self, rpt):
            self.stored.append(rpt)

    obj = cp2112_mod.Cp2112Smbus.__new__(cp2112_mod.Cp2112Smbus)
    obj._dev = _StubDev()
    obj._clock = 100000
    obj._configure()
    rpt = obj._dev.stored[0]
    assert rpt[0] == 0x06 and len(rpt) == 64  # feature report: ID 0x06 padded


def test_rotool_smbus_gated_without_protocol():
    from benchanalyzer.unlock.rotool import RoToolSmbus

    with pytest.raises(SMbusError):
        RoToolSmbus()
