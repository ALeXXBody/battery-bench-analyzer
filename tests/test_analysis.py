"""Tests for the analyzer-style pack analysis and consent-gated cell reads."""
import pytest

from voltclinic.core.analysis import (analyse, classify_imbalance,
                                     estimate_cell_count)
from voltclinic.core.bench import FakeGaugeBus
from voltclinic.core.rescue import read_cell_voltages
from voltclinic.unlock.smbus import SMbusError


def _info_with(design=4200, full=4000, cycles=120, design_v=11100):
    class _I:
        raw = {"design_capacity_mah": design, "full_charge_capacity_mah": full}
        cycle_count = cycles
        design_voltage_mv = design_v

        def __getattr__(self, name):
            return None
    return _I()


def test_cell_count_from_design_voltage():
    assert estimate_cell_count(11100) == 3
    assert estimate_cell_count(14800) == 4
    assert estimate_cell_count(1110) == 0
    assert estimate_cell_count(None) == 0


def test_health_classification_and_loss():
    r = analyse(_info_with(full=3960))
    assert r.soh_percent == pytest.approx(94.28, abs=0.5)
    assert r.health_class == "excellent"
    r2 = analyse(_info_with(full=2500))
    assert r2.health_class == "poor"
    assert "left on the table" in "\n".join(r2.findings)


def test_imbalance_states():
    assert classify_imbalance(5) == "balanced"
    assert classify_imbalance(35) == "acceptable drift"
    assert classify_imbalance(70) == "needs balancing"
    assert classify_imbalance(150) == "severely unbalanced"


def test_imbalance_and_cell_count_warning():
    r = analyse(_info_with(design_v=11100),  # 3S pack
                cell_voltages_mv=[3980, 3990, 4120],  # but 3 readings, gap 140
                temperature_c=25.0)
    assert r.imbalance_mv == 140
    assert r.imbalance_state == "severely unbalanced"
    assert any("imbalance" in w for w in r.warnings)


def test_cell_voltage_sanity_warnings():
    r = analyse(_info_with(), cell_voltages_mv=[2400, 4400], temperature_c=25)
    assert any("cell 1" in w and "outside" in w for w in r.warnings)
    r2 = analyse(_info_with(), cell_voltages_mv=[3800, 3810], temperature_c=48)
    assert any("charge-safe" in w for w in r2.warnings)


def test_verdict_has_content():
    r = analyse(_info_with(), cell_voltages_mv=[3980, 3985], temperature_c=25)
    assert r.verdict.startswith("Analysis:")
    text = r.render()
    assert "Analysis:" in text and "•" in text


def test_cell_read_requires_consent():
    bus = FakeGaugeBus()
    with pytest.raises(SMbusError):
        read_cell_voltages(bus, 0x0B, consent="")


def test_cell_read_off_demo_gauge():
    bus = FakeGaugeBus()          # cells 3980/3990/4000 mV
    cells = read_cell_voltages(bus, 0x0B, consent="test")
    assert cells == [3980.0, 3990.0, 4000.0]
    # the diagnostic write happened on the wire exactly once per read
    assert (0x0B, 0x00, 0x0057) in bus.writes


def test_cell_read_stops_at_non_cell_word():
    # after the 3 cells the demo bus would return garbage; extend cells
    bus = FakeGaugeBus(cells_mv=(3980, 3990, 4000, 777))
    cells = read_cell_voltages(bus, 0x0B, consent="test")
    assert cells == [3980.0, 3990.0, 4000.0]
