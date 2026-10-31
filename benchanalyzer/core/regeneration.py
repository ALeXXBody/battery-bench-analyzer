"""Regeneration / calibration engine.

A typical laptop battery reports a State of Health that can drift low when the
fuel gauge has not been fully discharged and recharged in a long time. The
gauge recalibrates by learning actual capacity from a slow, supervised
charge->discharge->charge cycle.

IMPORTANT SAFETY: This is a SOFTWARE calibration using only the OS battery
interface. It DOES NOT:
  - open the pack,
  - short or bypass any cells,
  - force over-discharge below the OS/EC cut-off,
  - write to any fuel-gauge EEPROM.

It only:
  1. ensures the machine stays awake (power policy guard),
  2. disables sleep/hibernate for the duration,
  3. lets the battery discharge to the OS low threshold, then recharges.

The Windows low-battery threshold is intentionally preserved so Windows shuts
down before any cell is over-discharged. This is a recalibration, not a
"resurrection" - physically failed cells are a fire risk and must not be forced
back to life in software.
"""

from __future__ import annotations

import abc
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from ..core.models import BatteryInfo, ChargeState

ProgressCb = Callable[[str, float], None]  # (message, fraction 0..1)


class PowerPolicyError(Exception):
    pass


class PowerPolicyGuard(abc.ABC):
    """Ensures the machine stays awake and doesn't sleep mid-cycle."""

    @abc.abstractmethod
    def enter(self) -> None: ...

    @abc.abstractmethod
    def exit(self) -> None: ...

    @property
    def supports_prevent_sleep(self) -> bool:
        return False


class NoopPowerPolicy(PowerPolicyGuard):
    def enter(self) -> None:
        pass

    def exit(self) -> None:
        pass


class WindowsPowerPolicy(PowerPolicyGuard):
    """Uses SetThreadExecutionState to keep the system awake; cleans up on exit."""

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002

    def __init__(self):
        import ctypes

        self._ctypes = ctypes
        self._exstate = (
            getattr(ctypes, "windll", None)
            and ctypes.windll.kernel32.SetThreadExecutionState
        )
        self._active = False

    @property
    def supports_prevent_sleep(self) -> bool:
        return True

    def enter(self) -> None:
        if not self._exstate:
            raise PowerPolicyError("SetThreadExecutionState unavailable")
        self._exstate(self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED)
        self._active = True

    def exit(self) -> None:
        if self._active and self._exstate:
            self._exstate(self.ES_CONTINUOUS)
        self._active = False


def default_power_policy() -> PowerPolicyGuard:
    import platform

    if platform.system() == "Windows":
        return WindowsPowerPolicy()
    return NoopPowerPolicy()


@dataclass
class RegenerationResult:
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    cycles_completed: int = 0
    before_soh: float | None = None
    after_soh: float | None = None
    before_full_mwh: float | None = None
    after_full_mwh: float | None = None
    cancelled: bool = False
    events: list[str] = field(default_factory=list)

    @property
    def soh_delta(self) -> float | None:
        if self.before_soh is None or self.after_soh is None:
            return None
        return self.after_soh - self.before_soh


class RegenerationEngine:
    """Supervised recalibration driver.

    Parameters
    ----------
    reader: callable returning latest BatteryInfo for the target battery.
    power_policy: sleep-prevention guard.
    """

    LOW_THRESHOLD_PCT = 15.0  # stop discharge here (Windows still protects below)
    TARGET_FULL_PCT = 100.0
    POLL_SEC = 10
    MAX_CYCLES = 3

    def __init__(
        self,
        reader,
        battery_index: int = 0,
        policy: PowerPolicyGuard | None = None,
        max_cycles: int = MAX_CYCLES,
        low_threshold_pct: float = LOW_THRESHOLD_PCT,
    ):
        self._reader = reader
        self._battery_index = battery_index
        self._policy = policy or default_power_policy()
        self.max_cycles = max_cycles
        self.low_threshold_pct = low_threshold_pct
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _latest(self) -> BatteryInfo | None:
        reader = self._reader
        try:
            bats = (
                reader.refresh() if hasattr(reader, "refresh") else reader().refresh()
            )
        except Exception:  # noqa: BLE001
            return None
        if not bats or self._battery_index >= len(bats):
            return None
        return bats[self._battery_index]

    def run(self, progress: ProgressCb | None = None) -> RegenerationResult:
        result = RegenerationResult()
        before = self._latest()
        if before is None:
            raise RuntimeError("No battery found to regenerate.")
        result.before_full_mwh = before.full_capacity_mwh
        result.before_soh = before.soh_percent

        self._policy.enter()
        try:
            for cycle in range(1, self.max_cycles + 1):
                if self._cancelled:
                    break
                self._log(
                    progress,
                    f"Cycle {cycle}: discharging to {self.low_threshold_pct:.0f}%",
                    0.0,
                )
                self._discharge_to_low(progress, cycle)
                if self._cancelled:
                    break
                self._log(progress, f"Cycle {cycle}: charging to full", 0.5)
                self._charge_to_full(progress, cycle)
                result.cycles_completed = cycle
        finally:
            self._policy.exit()

        result.finished_at = datetime.now()
        after = self._latest()
        if after is not None:
            result.after_full_mwh = after.full_capacity_mwh
            result.after_soh = after.soh_percent
        result.cancelled = self._cancelled
        return result

    def _discharge_to_low(self, progress: ProgressCb, cycle: int) -> None:
        while not self._cancelled:
            info = self._latest()
            if info is None:
                raise RuntimeError("Battery became unavailable during regeneration.")
            pct = info.remaining_percent
            if pct is None:
                raise RuntimeError("Battery charge level unknown; cannot calibrate.")
            if (
                pct <= self.low_threshold_pct
                or info.charge_state == ChargeState.CRITICAL
            ):
                self._log(
                    progress, f"Cycle {cycle}: reached low threshold ({pct:.0f}%)", 0.45
                )
                return
            if info.ac_online:
                self._log(
                    progress,
                    "Plugged into AC during discharge; unplug to proceed.",
                    0.0,
                )
            frac = 0.0 + 0.45 * max(0.0, 1.0 - (pct - self.low_threshold_pct) / 100.0)
            self._log(progress, f"Discharging... {pct:.0f}%", min(frac, 0.45))
            time.sleep(self.POLL_SEC)

    def _charge_to_full(self, progress: ProgressCb, cycle: int) -> None:
        while not self._cancelled:
            info = self._latest()
            if info is None:
                raise RuntimeError("Battery became unavailable during regeneration.")
            pct = info.remaining_percent
            if pct is None:
                raise RuntimeError("Battery charge level unknown; cannot calibrate.")
            if pct >= self.TARGET_FULL_PCT or info.charge_state == ChargeState.FULL:
                self._log(progress, f"Cycle {cycle}: fully charged ({pct:.0f}%)", 1.0)
                return
            if not info.ac_online:
                self._log(progress, "Plug in AC power to recharge.", 0.5)
            frac = 0.5 + 0.5 * (pct / 100.0)
            self._log(progress, f"Charging... {pct:.0f}%", min(frac, 1.0))
            time.sleep(self.POLL_SEC)

    @staticmethod
    def _log(progress: ProgressCb | None, msg: str, frac: float) -> None:
        if progress:
            progress(msg, max(0.0, min(frac, 1.0)))
