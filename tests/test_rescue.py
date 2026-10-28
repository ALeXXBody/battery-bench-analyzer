"""Tests for the dead-battery rescue flow (fake bus only)."""
from voltclinic.core.bench import FakeGaugeBus
from voltclinic.core.rescue import rescue, decode_word_flags


def test_rescue_locates_demo_gauge():
    out = rescue(FakeGaugeBus(), consent="test", wake_seconds=1.0)
    assert out["addr"] == 0x0B
    assert "PFStatus" in out


def test_bit_decoder_names_active_bits():
    raw = bytes([0b00000010, 0x00])     # SOV (bit1) set
    s = decode_word_flags(raw)
    assert "SOV" in s
