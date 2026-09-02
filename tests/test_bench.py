"""Tests for bench sessions and the guided calibration engine."""
import threading
import time

import pytest

from voltclinic.core.bench import (
    BenchCalibration,
    FakeGaugeBus,
    GaugeSession,
    SessionHolder,
)
from voltclinic.core.sbs import SBSGauge
from voltclinic.unlock.smbus import SMbusError


def test_session_open_demo():
    s = GaugeSession.open("demo")
    assert s.addr == 0x0B
    assert s.adapter_name == "demo"
    snap = s.snapshot()
    assert snap.raw["design_capacity_mah"] == 4200
    s.close()


def test_session_open_custom_addr():
    # The demo gauge answers wherever you point it; other addrs on its bus NAK.
    s = GaugeSession.open("demo", addr=0x2A)
    assert s.addr == 0x2A
    assert s.snapshot().raw["design_capacity_mah"] == 4200
    with pytest.raises(SMbusError):
        s.bus.read_word(0x55, 0x09)
    s.close()


def test_session_open_unknown_adapter():
    with pytest.raises(SMbusError):
        GaugeSession.open("ch341")  # not implemented yet


def test_session_scan():
    s = GaugeSession.open("demo")
    assert s.scan() == 0x0B
    s.close()


def test_session_probe():
    s = GaugeSession.open("demo")
    p = s.probe()
    assert p.device_name == "DEMO-PAK-3S"
    assert p.design_capacity_mah == 4200
    assert p.voltage_mv is not None
    s.close()


def test_session_holder():
    h = SessionHolder()
    assert h.connected is False
    h.session = GaugeSession.open("demo")
    assert h.connected is True
    h.session.close()


def test_calibration_demo_runs_full_learn_cycle():
    bus = FakeGaugeBus(soc=30.0, charge_rate_pct_s=150.0,
                       discharge_rate_pct_s=200.0)
    cal = BenchCalibration(SBSGauge(bus), power_control=bus.power,
                           low_rsoc=10.0, poll_s=0.01)
    seen = []
    r = cal.run(progress=lambda m, f: seen.append((m, f)))
    assert r.cancelled is False
    assert r.cycles_completed == 1
    assert r.fcc_before == 3100
    assert r.fcc_after == 3100
    assert r.soh_before == pytest.approx(3100 / 4200 * 100, abs=0.1)
    # all three phases logged
    phases = [e for e in r.events if e.startswith("phase")]
    assert len(phases) == 3
    # power hook received the full sequence
    assert bus.mode == "idle"  # all_off at the end
    # progress reached 100%
    fracs = [f for _, f in seen if f is not None]
    assert fracs and max(fracs) == pytest.approx(1.0)


def test_calibration_operator_mode_logs_instructions():
    bus = FakeGaugeBus(soc=50.0, charge_rate_pct_s=150.0,
                       discharge_rate_pct_s=200.0)
    cal = BenchCalibration(SBSGauge(bus), power_control=None,
                           low_rsoc=10.0, poll_s=0.01)
    instructions = []

    # Simulate the operator: follow the printed instructions.
    def operator(msg, frac):
        if msg.startswith(("Connect the bench charger", "Reconnect the charger")):
            instructions.append(msg)
            bus.mode = "charge"
        elif msg.startswith("Connect the bench load"):
            instructions.append(msg)
            bus.mode = "discharge"
        elif msg.startswith(("Charge complete", "Discharge complete")):
            instructions.append(msg)

    holder = {}

    def run():
        holder["r"] = cal.run(progress=operator)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=30)
    assert not t.is_alive(), "operator-mode calibration did not finish"
    assert holder["r"].cycles_completed == 1
    assert any("charger" in m for m in instructions)
    assert any("load" in m for m in instructions)


def test_calibration_cancel():
    bus = FakeGaugeBus(soc=5.0, charge_rate_pct_s=1.0)
    cal = BenchCalibration(SBSGauge(bus), power_control=bus.power,
                           low_rsoc=10.0, poll_s=0.01)
    t = threading.Thread(target=lambda: (time.sleep(0.05), cal.cancel()),
                         daemon=True)
    t.start()
    r = cal.run(progress=lambda m, f: None)
    assert r.cancelled is True
    assert r.cycles_completed == 0
