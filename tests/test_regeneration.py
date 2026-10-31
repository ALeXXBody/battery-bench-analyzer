"""Tests for the regeneration/calibration engine using a scripted fake reader."""

from __future__ import annotations

import time

from benchanalyzer.core.models import BatteryInfo, ChargeState
from benchanalyzer.core.regeneration import (
    NoopPowerPolicy,
    RegenerationEngine,
    RegenerationResult,
)


class ScriptedBattery:
    """Emulates a battery going from full -> low -> full across a cycle."""

    def __init__(self, low_pct=15.0):
        self.pct = 100.0
        self.low_pct = low_pct
        self.on_ac = False
        self.polls = 0

    def info(self) -> BatteryInfo:
        self.polls += 1
        state = ChargeState.FULL if self.pct >= 99.0 else ChargeState.DISCHARGING
        return BatteryInfo(
            name="Fake",
            design_capacity_mwh=42000,
            full_capacity_mwh=31000,
            current_mwh=31000 * self.pct / 100.0,
            charge_state=state,
            ac_online=self.on_ac,
        )


class _FakeReader:
    def __init__(self, script: ScriptedBattery):
        self.script = script

    def refresh(self):
        return [self.script.info()]


def test_engine_completes_a_cycle(monkeypatch):
    script = ScriptedBattery()

    # model: discharge by 10 until low, then user plugs in and it charges up
    def advance():
        if script.pct <= script.low_pct:
            script.on_ac = True
        if script.on_ac and script.pct < 100:
            script.pct = min(100, script.pct + 10)
        elif not script.on_ac:
            script.pct = max(script.low_pct, script.pct - 10)

    monkeypatch.setattr(time, "sleep", lambda s: advance())

    engine = RegenerationEngine(
        _FakeReader(script),
        battery_index=0,
        policy=NoopPowerPolicy(),
        max_cycles=1,
        low_threshold_pct=15.0,
    )
    result = engine.run()

    assert isinstance(result, RegenerationResult)
    assert result.cycles_completed == 1
    assert result.before_soh is not None
    assert result.after_soh is not None


def test_cancel_stops_engine(monkeypatch):
    script = ScriptedBattery()
    monkeypatch.setattr(time, "sleep", lambda s: None)

    engine = RegenerationEngine(
        _FakeReader(script),
        battery_index=0,
        policy=NoopPowerPolicy(),
        max_cycles=3,
    )
    engine.cancel()
    result = engine.run()
    assert result.cancelled is True
    assert result.cycles_completed == 0
