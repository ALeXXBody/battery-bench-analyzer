"""Tests for the battery data model and health calculations."""
import pytest

from battery_clinic.core.models import (
    BatteryInfo,
    ChargeState,
    Chemistry,
    normalize_chemistry,
    state_of_health,
)


@pytest.mark.parametrize("raw,expected", [
    ("Li-Ion", Chemistry.LI_ION),
    ("LION", Chemistry.LI_ION),
    ("Lithium Polymer", Chemistry.LI_POLYMER),
    ("li-poly", Chemistry.LI_POLYMER),
    ("NiMH", Chemistry.NI_MH),
    ("NiCd", Chemistry.NI_CD),
    ("LiFePO4", Chemistry.LFP),
    ("Bogus", Chemistry.UNKNOWN),
    (None, Chemistry.UNKNOWN),
    ("", Chemistry.UNKNOWN),
])
def test_normalize_chemistry(raw, expected):
    assert normalize_chemistry(raw) == expected


def test_state_of_health():
    assert state_of_health(3100, 4200) == pytest.approx(73.809, rel=1e-3)
    assert state_of_health(None, 4200) is None
    assert state_of_health(3100, None) is None
    assert state_of_health(3100, 0) is None


def test_battery_info_properties():
    info = BatteryInfo(
        name="Test",
        design_capacity_mwh=42000,
        full_capacity_mwh=31000,
        current_mwh=15500,
        charge_state=ChargeState.DISCHARGING,
    )
    assert info.soh_percent == pytest.approx(73.809, rel=1e-3)
    assert info.remaining_percent == pytest.approx(50.0)
    assert info.health_label == "Fair"
