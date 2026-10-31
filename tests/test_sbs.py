"""Tests for the Smart Battery Data register layer (pure, no hardware)."""

import pytest

from benchanalyzer.core.bench import FakeGaugeBus
from benchanalyzer.core.sbs import (
    BS_DISCHARGING,
    BS_FULLY_CHARGED,
    BS_FULLY_DISCHARGED,
    SBS_BATTERY_STATUS,
    SBS_CURRENT,
    SBS_CYCLE_COUNT,
    SBS_DESIGN_CAPACITY,
    SBS_DESIGN_VOLTAGE,
    SBS_FULL_CHARGE_CAPACITY,
    SBS_MANUFACTURER_NAME,
    SBS_REMAINING_CAPACITY,
    SBS_SERIAL_NUMBER,
    SBS_TEMPERATURE,
    SBS_VOLTAGE,
    SBSGauge,
    charge_state_from,
    decode_manufacture_date,
    decode_status,
    raw_temp_to_celsius,
    scan_for_gauge,
    to_signed16,
)
from benchanalyzer.unlock.smbus import (
    SB_BATTERY_STATUS,
    SB_CURRENT,
    SB_CYCLE_COUNT,
    SB_DESIGN_CAPACITY,
    SB_DESIGN_VOLTAGE,
    SB_FULL_CHARGE_CAPACITY,
    SB_MANUFACTURER_NAME,
    SB_REMAINING_CAPACITY,
    SB_SERIAL_NUMBER,
    SB_TEMPERATURE,
    SB_VOLTAGE,
)


def test_sbs_golden_register_offsets():
    # Smart Battery Data spec v1.1, section 5.
    assert SBS_TEMPERATURE == 0x08
    assert SBS_VOLTAGE == 0x09
    assert SBS_CURRENT == 0x0A
    assert SBS_BATTERY_STATUS == 0x14
    assert SBS_CYCLE_COUNT == 0x15
    assert SBS_DESIGN_CAPACITY == 0x16
    assert SBS_DESIGN_VOLTAGE == 0x17
    assert SBS_SERIAL_NUMBER == 0x1A
    assert SBS_FULL_CHARGE_CAPACITY == 0x0E
    assert SBS_REMAINING_CAPACITY == 0x0D
    assert SBS_MANUFACTURER_NAME == 0x20


def test_unlock_constants_match_sbs_map():
    # The two modules must never drift apart again.
    assert SB_TEMPERATURE == SBS_TEMPERATURE
    assert SB_VOLTAGE == SBS_VOLTAGE
    assert SB_CURRENT == SBS_CURRENT
    assert SB_BATTERY_STATUS == SBS_BATTERY_STATUS
    assert SB_CYCLE_COUNT == SBS_CYCLE_COUNT
    assert SB_DESIGN_CAPACITY == SBS_DESIGN_CAPACITY
    assert SB_DESIGN_VOLTAGE == SBS_DESIGN_VOLTAGE
    assert SB_SERIAL_NUMBER == SBS_SERIAL_NUMBER
    assert SB_FULL_CHARGE_CAPACITY == SBS_FULL_CHARGE_CAPACITY
    assert SB_REMAINING_CAPACITY == SBS_REMAINING_CAPACITY
    assert SB_MANUFACTURER_NAME == SBS_MANUFACTURER_NAME


def test_to_signed16():
    assert to_signed16(1800) == 1800
    assert to_signed16(0xFFFF - 2100 + 1) == -2100  # -2100 as u16
    assert to_signed16(0x8000) == -32768
    assert to_signed16(0) == 0


def test_temperature_conversion():
    # 0.1 K resolution: 2972 = 297.2 K = 24.05 C
    assert raw_temp_to_celsius(2972) == pytest.approx(24.05, abs=0.01)
    assert raw_temp_to_celsius(2732) == pytest.approx(0.05, abs=0.01)
    assert raw_temp_to_celsius(2731) == pytest.approx(-0.05, abs=0.01)


def test_decode_status():
    assert decode_status(BS_DISCHARGING) == ["DISCHARGING"]
    assert decode_status(BS_FULLY_CHARGED) == ["FULLY_CHARGED"]
    assert decode_status(0) == []
    assert "FULLY_DISCHARGED" in decode_status(BS_FULLY_DISCHARGED)


def test_charge_state_from():
    assert charge_state_from(0, -500) is not None
    assert charge_state_from(0, -500).value == "discharging"
    assert charge_state_from(0, 700).value == "charging"
    assert charge_state_from(BS_FULLY_CHARGED, 0).value == "full"
    assert charge_state_from(BS_FULLY_DISCHARGED, 0).value == "critical"
    assert charge_state_from(0, 0).value == "unknown"


def test_decode_manufacture_date():
    # 2021-06-15: year=41 -> 41<<9 | 6<<5 | 15
    raw = (41 << 9) | (6 << 5) | 15
    assert decode_manufacture_date(raw) == "2021-06-15"
    assert decode_manufacture_date(0xFFFF) is None
    assert decode_manufacture_date(0) is None


def test_snapshot_from_demo_gauge():
    g = SBSGauge(FakeGaugeBus())
    s = g.snapshot()
    assert s.name == "DEMO-PAK-3S"
    assert s.manufacturer == "VoltTeq"
    assert s.chemistry.value == "Li-ion"
    assert s.serial_number == "0x5A1C"
    assert s.raw["design_capacity_mah"] == 4200
    assert s.raw["full_charge_capacity_mah"] == 3100
    assert s.soh_percent == pytest.approx(3100 / 4200 * 100, abs=0.1)
    assert s.voltage_mv == pytest.approx(11160, abs=200)
    assert s.temperature_c == pytest.approx(24.0, abs=1.0)
    assert s.cycle_count == 402
    assert 0 <= s.relative_soc <= 100


def test_snapshot_live_modes():
    bus = FakeGaugeBus(soc=50.0)
    g = SBSGauge(bus)
    bus.mode = "discharge"
    s = g.snapshot()
    assert s.current_ma < 0
    assert s.charge_state.value == "discharging"
    bus.mode = "charge"
    s = g.snapshot()
    assert s.current_ma > 0
    assert s.charge_state.value == "charging"
    assert s.charge_rate_mw is not None


def test_is_present_and_scan():
    bus = FakeGaugeBus()
    assert SBSGauge(bus, 0x0B).is_present()
    assert not SBSGauge(bus, 0x55).is_present()
    assert scan_for_gauge(bus) == 0x0B


def test_capacity_mode_mwh():
    bus = FakeGaugeBus()
    g = SBSGauge(bus)
    assert g.capacity_mode_is_mwh() is False
    # mAh-based snapshot keeps raw mAh and derives mWh via design voltage
    s = g.snapshot()
    assert s.raw["design_capacity_mah"] == 4200
    assert s.design_capacity_mwh == pytest.approx(4200 * 11.1, abs=1.0)
