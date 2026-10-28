"""Bench battery analysis: adapter sessions + guided calibration.

The battery under test sits on a bench, out of its host device, wired to a
CP2112 USB-SMBus adapter (SCL/SDA/GND). This module owns the connection to the
gauge, provides a realistic in-memory demo gauge (so the UI/CLI work without
hardware), and implements the guided learn-cycle calibration: the operator
switches the bench charger/load, the app monitors the gauge and records what
the gauge learns.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

from ..unlock.smbus import SMbusBus, SMbusError
from .sbs import (
    BS_FULLY_CHARGED,
    BS_FULLY_DISCHARGED,
    BS_DISCHARGING,
    DEFAULT_GAUGE_ADDR,
    SBSGauge,
    SCAN_ADDRESSES,
    decode_status,
)

PowerControl = Callable[[str], None]  # "charge_on" | "charge_off" | "load_on" | "load_off" | "all_off"
ProgressCb = Callable[[str, Optional[float]], None]  # frac None = status-only


# ---------------------------------------------------------------------------
# Demo gauge: an in-memory SBS responder for development / no-hardware demos
# ---------------------------------------------------------------------------

def _prefixed(data: bytes) -> bytes:
    return bytes([len(data)]) + data


class FakeGaugeBus(SMbusBus):
    """Simulated 3S Li-ion smart battery answering at ``addr`` (default 0x0B).

    Telemetry evolves over time; ``mode`` simulates what the bench power
    hardware does: "idle" | "charge" | "discharge".
    """

    def __init__(self, addr: int = DEFAULT_GAUGE_ADDR,
                 design_mah: int = 4200, full_mah: int = 3100,
                 cycles: int = 402, soc: float = 60.0,
                 charge_rate_pct_s: float = 6.0,
                 discharge_rate_pct_s: float = 8.0):
        self.addr = addr
        self.design_mah = design_mah
        self.full_mah = full_mah
        self.cycles = cycles
        self.soc = soc
        self.charge_rate_pct_s = charge_rate_pct_s
        self.discharge_rate_pct_s = discharge_rate_pct_s
        self.mode = "idle"
        self.writes: List[tuple] = []
        self._last = time.monotonic()
        self.blocks = {
            0x20: _prefixed(b"VoltTeq"),
            0x21: _prefixed(b"DEMO-PAK-3S"),
            0x22: _prefixed(b"Li-ion"),
        }

    # -- simulation --

    def _advance(self) -> None:
        now = time.monotonic()
        dt = min(now - self._last, 5.0)
        self._last = now
        if self.mode == "charge":
            self.soc = min(100.0, self.soc + self.charge_rate_pct_s * dt)
        elif self.mode == "discharge":
            self.soc = max(0.0, self.soc - self.discharge_rate_pct_s * dt)

    def _voltage_mv(self) -> int:
        v = 9.0 + 3.6 * (self.soc / 100.0)
        if self.mode == "charge":
            v += 0.25
        elif self.mode == "discharge":
            v -= 0.2 * (1.0 - self.soc / 100.0)
        return int(v * 1000)

    def _current_ma(self) -> int:
        if self.mode == "charge":
            return 1800
        if self.mode == "discharge":
            return -2100
        return 0

    def _temp_c(self) -> float:
        base = 24.0
        if self.mode in ("charge", "discharge"):
            base += 3.5
        return base

    def _status(self) -> int:
        st = 0
        if self.mode == "discharge" or self._current_ma() < 0:
            st |= BS_DISCHARGING
        if self.soc >= 100.0:
            st |= BS_FULLY_CHARGED
        if self.soc <= 0.0:
            st |= BS_FULLY_DISCHARGED
        return st

    # -- SMbusBus interface --

    def _check(self, addr: int) -> None:
        if addr != self.addr:
            raise SMbusError(f"no device at 0x{addr:02X}")

    def read_word(self, addr: int, reg: int) -> int:
        self._check(addr)
        self._advance()
        if reg == 0x08:
            return int((self._temp_c() + 273.15) * 10)
        if reg == 0x09:
            return self._voltage_mv()
        if reg == 0x0A:
            return self._current_ma() & 0xFFFF
        if reg == 0x0B:
            return int(round(self.soc))
        if reg == 0x0C:
            return int(round(self.soc * self.full_mah / self.design_mah))
        if reg == 0x0D:
            return int(round(self.full_mah * self.soc / 100.0))
        if reg == 0x0E:
            return self.full_mah
        if reg == 0x14:
            return self._status()
        if reg == 0x15:
            return self.cycles
        if reg == 0x16:
            return self.design_mah
        if reg == 0x17:
            return 11100
        if reg == 0x1A:
            return 0x5A1C
        if reg == 0x03:
            return 0
        if reg == 0x12:
            return 2200
        if reg == 0x13:
            return 12600
        raise SMbusError(f"unsupported register 0x{reg:02X}")

    def write_word(self, addr: int, reg: int, value: int) -> None:
        self._check(addr)
        self.writes.append((addr, reg, value))

    def read_block(self, addr: int, reg: int, length: int) -> bytes:
        self._check(addr)
        if reg in self.blocks:
            return self.blocks[reg]
        raise SMbusError(f"unsupported block register 0x{reg:02X}")

    # -- bench power simulation (used as PowerControl by the demo) --

    def power(self, command: str) -> None:
        if command == "charge_on":
            self.mode = "charge"
        elif command == "load_on":
            self.mode = "discharge"
        elif command == "all_off":
            self.mode = "idle"


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

class GaugeSession:
    """An open connection to one gauge on one adapter."""

    def __init__(self, bus: SMbusBus, addr: int, adapter_name: str,
                 owned: bool = True):
        self.bus = bus
        self.addr = addr
        self.adapter_name = adapter_name
        self.gauge = SBSGauge(bus, addr)
        self._owned = owned

    @classmethod
    def open(cls, adapter: str = "demo",
             addr: int = DEFAULT_GAUGE_ADDR) -> "GaugeSession":
        """Open a session; raises SMbusError when the gauge does not answer."""
        if adapter == "demo":
            bus = FakeGaugeBus(addr=addr)
        elif adapter == "cp2112":
            from ..unlock.cp2112 import Cp2112Smbus
            bus = Cp2112Smbus()
        elif adapter == "nlba":
            from ..unlock.nlba import NLBASmbus
            bus = NLBASmbus()
        else:
            raise SMbusError(f"unknown adapter: {adapter!r}")
        g = SBSGauge(bus, addr)
        if not g.is_present():
            close = getattr(bus, "close", None)
            if close:
                close()
            raise SMbusError(
                f"No SBS gauge answered at 0x{addr:02X} on {adapter}. "
                "Check wiring (SCL/SDA/GND) and the gauge address.")
        return cls(bus, addr, adapter)

    def scan(self) -> Optional[int]:
        """Re-scan this bus for a gauge; updates and returns the address."""
        found = scan_for_gauge(self.bus)
        if found is not None:
            self.addr = found
            self.gauge = SBSGauge(self.bus, found)
        return found

    def snapshot(self):
        return self.gauge.snapshot()

    def probe(self):
        from ..unlock.smbus import FuelGaugeInterop
        return FuelGaugeInterop(self.bus, self.addr).probe()

    def close(self) -> None:
        if self._owned:
            close = getattr(self.bus, "close", None)
            if close:
                close()


class SessionHolder:
    """Plain shared state: the one gauge session used across pages/CLI."""

    def __init__(self):
        self.session: Optional[GaugeSession] = None

    @property
    def connected(self) -> bool:
        return self.session is not None


def scan_for_gauge(bus: SMbusBus,
                   addresses=SCAN_ADDRESSES) -> Optional[int]:
    from .sbs import scan_for_gauge as _scan
    return _scan(bus, addresses)


# ---------------------------------------------------------------------------
# Guided bench calibration (learn cycle): operator switches power, app watches
# ---------------------------------------------------------------------------

@dataclass
class BenchCalibrationResult:
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    fcc_before: Optional[int] = None      # FullChargeCapacity, native units
    fcc_after: Optional[int] = None
    soh_before: Optional[float] = None
    soh_after: Optional[float] = None
    cycles_completed: int = 0
    cancelled: bool = False
    events: List[str] = field(default_factory=list)

    @property
    def fcc_delta(self) -> Optional[int]:
        if self.fcc_before is None or self.fcc_after is None:
            return None
        return self.fcc_after - self.fcc_before


class BenchCalibration:
    """Supervised learn cycle for a bench battery.

    The app never controls the charge/load hardware (a future PowerControl
    hook can). Without the hook it tells the operator what to switch, and
    watches the gauge until each phase's exit condition is met:

    1. CHARGE until the gauge reports FULLY_CHARGED (or ~100%),
    2. DISCHARGE to the low threshold (gauge alarms / FULLY_DISCHARGED),
    3. RECHARGE to full, so the gauge re-learns FullChargeCapacity.

    The gauge's own firmware and the operator's bench supply remain the safety
    limits; the app only reads.
    """

    def __init__(self, gauge: SBSGauge,
                 power_control: Optional[PowerControl] = None,
                 low_rsoc: float = 10.0,
                 poll_s: float = 2.0):
        self.gauge = gauge
        self.power_control = power_control
        self.low_rsoc = low_rsoc
        self.poll_s = poll_s
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _power(self, command: str, log: List[str], progress: ProgressCb,
               instruction: str) -> None:
        if self.power_control is not None:
            self.power_control(command)
            log.append(f"[power] {command}")
        else:
            log.append(f"[operator] {instruction}")
            progress(instruction, None)  # None frac => status-only update

    def _telemetry(self):
        rsoc = self.gauge.relative_soc()
        status = self.gauge.battery_status() or 0
        return rsoc, status

    def _wait(self, progress: ProgressCb, log: List[str],
              phase: str, done: Callable[[float, int], bool],
              frac: Callable[[float], float]) -> None:
        while True:
            if self._cancelled:
                raise _Cancelled()
            rsoc, status = self._telemetry()
            if rsoc is None:
                raise SMbusError("gauge link lost during calibration")
            flags = " ".join(decode_status(status)) or "idle"
            progress(f"{phase}: {rsoc:.0f}% ({flags})", frac(rsoc))
            if done(rsoc, status):
                return
            time.sleep(self.poll_s)

    def run(self, progress: Optional[ProgressCb] = None) -> BenchCalibrationResult:
        result = BenchCalibrationResult()
        log = result.events

        def cb(msg, frac):
            if progress:
                progress(msg, frac)

        design = self.gauge.design_capacity()
        result.fcc_before = self.gauge.full_charge_capacity()
        if design and result.fcc_before:
            result.soh_before = result.fcc_before / design * 100.0
        log.append(f"learn cycle start: FCC={result.fcc_before} "
                   f"(design={design}), low threshold={self.low_rsoc:.0f}%")

        try:
            log.append("phase 1/3: charge")
            self._power("charge_on", log, cb,
                        "Connect the bench charger and let the pack charge to full.")
            self._wait(cb, log, "Charge",
                       lambda rsoc, st: bool(st & BS_FULLY_CHARGED) or rsoc >= 99.5,
                       lambda rsoc: 0.33 * rsoc / 100.0)
            self._power("charge_off", log, cb, "Charge complete: switch the charger off.")

            log.append("phase 2/3: discharge")
            self._power("load_on", log, cb,
                        "Connect the bench load and discharge the pack.")
            self._wait(cb, log, "Discharge",
                       lambda rsoc, st: rsoc <= self.low_rsoc or bool(st & BS_FULLY_DISCHARGED),
                       lambda rsoc: 0.33 + 0.33 * (100.0 - rsoc) / max(1.0, 100.0 - self.low_rsoc))
            self._power("load_off", log, cb, "Discharge complete: switch the load off.")

            log.append("phase 3/3: recharge")
            self._power("charge_on", log, cb,
                        "Reconnect the charger and recharge to full.")
            self._wait(cb, log, "Recharge",
                       lambda rsoc, st: bool(st & BS_FULLY_CHARGED) or rsoc >= 99.5,
                       lambda rsoc: 0.66 + 0.34 * rsoc / 100.0)
            self._power("charge_off", log, cb, "Recharge complete: switch the charger off.")
            result.cycles_completed = 1
            cb("Learn cycle complete.", 1.0)
        except _Cancelled:
            result.cancelled = True
            cb("Calibration cancelled.", None)
        finally:
            if self.power_control is not None:
                try:
                    self.power_control("all_off")
                except Exception:  # noqa: BLE001
                    pass
            else:
                log.append("[operator] Session ended: switch charger/load off.")

        result.fcc_after = self.gauge.full_charge_capacity()
        if design and result.fcc_after:
            result.soh_after = result.fcc_after / design * 100.0
        log.append(f"learn cycle end: FCC={result.fcc_after} "
                   f"(delta={result.fcc_delta})")
        result.finished_at = datetime.now()
        return result


class _Cancelled(Exception):
    pass
