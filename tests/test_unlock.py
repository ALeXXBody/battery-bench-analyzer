"""Tests for the SMBus unlock hooks and guarded consent logic."""

import pytest

from benchanalyzer.unlock.smbus import (
    SB_CYCLE_COUNT,
    SB_DESIGN_CAPACITY,
    SB_DEVICE_NAME,
    SB_FULL_CHARGE_CAPACITY,
    SB_MANUFACTURER_NAME,
    SB_SERIAL_NUMBER,
    ConsentRequired,
    FakeSMbusBus,
    FuelGaugeInterop,
)


def _demo_gauge() -> FuelGaugeInterop:
    bus = FakeSMbusBus(
        regs={
            SB_SERIAL_NUMBER: 0x1234,
            SB_DESIGN_CAPACITY: 4200,
            SB_FULL_CHARGE_CAPACITY: 3100,
            SB_CYCLE_COUNT: 402,
        }
    )
    bus.blocks[SB_MANUFACTURER_NAME] = b"\x07" + b"TEST-CO"
    bus.blocks[SB_DEVICE_NAME] = b"\x08" + b"DEMO-PAK"
    return FuelGaugeInterop(bus)


def test_probe_reads_public_registers():
    g = _demo_gauge()
    info = g.probe()
    assert info.manufacturer == "TEST-CO"
    assert info.device_name == "DEMO-PAK"
    assert info.design_capacity_mah == 4200
    assert info.full_capacity_mah == 3100
    assert info.cycle_count == 402


def test_write_requires_consent():
    g = _demo_gauge()
    with pytest.raises(ConsentRequired):
        g.write_word(SB_CYCLE_COUNT, 0)


def test_consent_gate_requires_acknowledgement():
    g = _demo_gauge()
    with pytest.raises(ConsentRequired):
        g.grant_write_permission(acknowledged=False)


def test_write_after_consent():
    g = _demo_gauge()
    g.grant_write_permission(acknowledged=True)
    g.write_word(SB_CYCLE_COUNT, 0)
    assert g._bus.regs[SB_CYCLE_COUNT] == 0
