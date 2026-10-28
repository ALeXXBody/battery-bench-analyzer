"""Tests for the detailed scan hint table."""
from voltclinic.core.sbs import describe_scan


def test_all_nak_reads_like_gaue_off():
    s = describe_scan([(0x0B, 0x06), (0x08, 0x06)])
    assert "NAK" in s and "unpowered" in s


def test_bus_error_mentions_wiring():
    s = describe_scan([(0x0B, 0x07)])
    assert "swapped SDA/SCL" in s


def test_detail_codes_are_printed():
    s = describe_scan([(0x0F, 0x06)])
    assert "0x0F:06" in s
