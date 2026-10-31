"""Tests for the tabbed info readouts (register dump / full readout)."""
from voltclinic.core.bench import FakeGaugeBus
from voltclinic.core.sbs import (SBSGauge, decode_manufacture_date_word,
                                 decode_spec_info, fmt_word_value)


def test_full_readout_names_registers():
    g = SBSGauge(FakeGaugeBus(), 0x0B)
    rows = {name: raw for name, raw, _u, _text in g.full_readout()}
    assert rows["Voltage"] > 0
    assert rows["CycleCount"] == 402
    assert rows["DesignCapacity"] == 4200


def test_full_readout_marks_missing_registers():
    g = SBSGauge(FakeGaugeBus(), 0x0B)
    rows = {name: raw for name, raw, _u, _text in g.full_readout()}
    # RunTimeToEmpty is not implemented by the demo gauge -> None
    assert rows["RunTimeToEmpty"] is None


def test_register_dump_includes_blocks_and_skips_mac():
    g = SBSGauge(FakeGaugeBus(), 0x0B)
    rows = g.register_dump()
    regs = {r[0]: r[1] for r in rows}
    assert regs.get("0x20") == "ManufacturerName"
    assert regs.get("0x23") == "ManufacturerData"
    types = {r[2] for r in rows}
    assert types == {"word", "block"}
    # ManufacturerAccess (0x00) must never be read in a passive dump
    assert "0x00" not in regs


def test_register_dump_voltage_line():
    g = SBSGauge(FakeGaugeBus(), 0x0B)
    row = next(r for r in g.register_dump() if r[0] == "0x09")
    _reg, _name, _type, unit, text = row
    assert unit == "mV"
    assert text.startswith("11160 mV") and "11.160 V" in text  # decoded + raw


def test_word_value_human_readable():
    # every plain number register now carries its unit in the text
    assert fmt_word_value(0x0B, 62) == "62 %"
    assert fmt_word_value(0x0E, 3100).endswith("mAh")
    assert fmt_word_value(0x15, 402) == "402 cycles"
    assert fmt_word_value(0x07, 1).startswith("yes")
    # time registers translate 0xFFFF and minutes
    assert fmt_word_value(0x10, 0xFFFF) == "unavailable (0xFFFF)"
    assert "1h05" in fmt_word_value(0x0F, 65)
    # BatteryMode decodes definable bits with raw hex retained
    assert "primary_battery" in fmt_word_value(0x03, 0x0800)
    assert "0x0000" in fmt_word_value(0x03, 0)


def test_word_value_formatting():
    # temperature converts 0.1 K to degC
    assert "°C" in fmt_word_value(0x08, (25 + 273.15) * 10)
    # current is signed
    assert fmt_word_value(0x0A, 0xFF7C).startswith("-")  # -132 mA
    # status decodes flags
    assert "fully" in fmt_word_value(0x14, 0x2000)
    # serial
    assert fmt_word_value(0x1A, 0x1234).startswith("0x1234")


def test_spec_and_date_decoders():
    assert "v1.1" in decode_spec_info(0x0011)
    assert decode_manufacture_date_word(0xFFFF) == "—"
    assert decode_manufacture_date_word(0) == "—"
