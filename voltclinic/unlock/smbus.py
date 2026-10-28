"""Optional SMBus / fuel-gauge interop hooks.

This module provides a documented, safe *extension interface* for skilled
users who want to talk to a laptop battery's SMBus fuel gauge directly. It does
NOT ship a working vendor-protocol reflash, and it deliberately does not
implement any proprietary chip unlock.

Why: Most "battery unlock" tools work by writing to the fuel-gauge EEPROM to
zero the cycle count or reset a "permanent failure" flag. Doing that on a
serviceable but swapped pack may be fine under fair-use, but on a genuinely
failed pack it is a fire risk, and "resurrecting" worn-out packs to resell them
is counterfeiting in most jurisdictions. We therefore:

  - expose a clean SMBus read/write interface you can drive,
  - require explicit, documented consent before *any* EEPROM write,
  - leave vendor protocol specifics out (you supply the register map).

This keeps the tool general and legal to distribute.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

from ..core.models import BatteryInfo

# Smart Battery Data (SBS) v1.1 register addresses - public spec.
# NOTE: these MUST match voltclinic.core.sbs (kept in sync by tests).
SB_MANUFACTURER_ACCESS = 0x00
SB_BATTERY_MODE = 0x03
SB_TEMPERATURE = 0x08
SB_VOLTAGE = 0x09
SB_CURRENT = 0x0A
SB_RELATIVE_SOC = 0x0B
SB_ABSOLUTE_SOC = 0x0C
SB_REMAINING_CAPACITY = 0x0D
SB_FULL_CHARGE_CAPACITY = 0x0E
SB_BATTERY_STATUS = 0x14
SB_CYCLE_COUNT = 0x15
SB_DESIGN_CAPACITY = 0x16
SB_DESIGN_VOLTAGE = 0x17
SB_SPECIFICATION_INFO = 0x18
SB_MANUFACTURE_DATE = 0x19
SB_SERIAL_NUMBER = 0x1A
SB_MANUFACTURER_NAME = 0x20
SB_DEVICE_NAME = 0x21
SB_DEVICE_CHEMISTRY = 0x22
SB_MANUFACTURER_DATA = 0x23


class SMbusError(Exception):
    """SMBus/bench transport failure.

    ``status`` optionally carries the adapter-level transfer error code so
    diagnosis code can distinguish NAK (silent slave) from bus errors.
    """
    def __init__(self, message: str = "", status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class SMbusBus(abc.ABC):
    """Abstract SMBus access. The I2C address is typically 0x0B for the
    gauge on the SMBus and requires an adapter (e.g. FTDI/CH341 USB-SMBus)."""

    @abc.abstractmethod
    def read_word(self, addr: int, reg: int) -> int:
        ...

    @abc.abstractmethod
    def write_word(self, addr: int, reg: int, value: int) -> None:
        ...

    @abc.abstractmethod
    def read_block(self, addr: int, reg: int, length: int) -> bytes:
        ...


_ENABLE_WRITES = ("You must read and acknowledge the safety & legal statement "
                  "before enabling EEPROM writes.")


class ConsentRequired(SMbusError):
    pass


@dataclass
class GaugeProbeResult:
    address: int
    manufacturer: Optional[str] = None
    device_name: Optional[str] = None
    chemistry: Optional[str] = None
    serial: Optional[int] = None
    design_capacity_mah: Optional[int] = None
    full_capacity_mah: Optional[int] = None
    cycle_count: Optional[int] = None
    relative_soc: Optional[int] = None
    voltage_mv: Optional[int] = None
    temperature: Optional[float] = None
    registers: Dict[str, int] = field(default_factory=dict)


class FuelGaugeInterop:
    """High-level, read-mostly access to a Smart Battery fuel gauge.

    Writes require an explicit opt-in token obtained from
    `grant_write_permission` after the user reads the legal notice.
    """

    GAUGE_ADDR_DEFAULT = 0x0B

    def __init__(self, bus: SMbusBus, addr: int = GAUGE_ADDR_DEFAULT):
        self._bus = bus
        self._addr = addr
        self._write_consent: Optional[str] = None

    def probe(self) -> GaugeProbeResult:
        bustr = lambda r: self._read_string(r)  # noqa: E731
        res = GaugeProbeResult(address=self._addr)
        try:
            res.manufacturer = bustr(SB_MANUFACTURER_NAME)
        except SMbusError:
            pass
        try:
            res.device_name = bustr(SB_DEVICE_NAME)
        except SMbusError:
            pass
        try:
            res.chemistry = bustr(SB_DEVICE_CHEMISTRY)
        except SMbusError:
            pass
        res.serial = self._read_maybe(SB_SERIAL_NUMBER)
        res.design_capacity_mah = self._read_maybe(SB_DESIGN_CAPACITY)
        res.full_capacity_mah = self._read_maybe(SB_FULL_CHARGE_CAPACITY)
        res.cycle_count = self._read_maybe(SB_CYCLE_COUNT)
        res.relative_soc = self._read_maybe(SB_RELATIVE_SOC)
        res.voltage_mv = self._read_maybe(SB_VOLTAGE)
        res.temperature = self._temperature_c()
        return res

    def read_manufacturer_block(self) -> bytes:
        """Read the public ManufacturerData block (may be partial on-pack info)."""
        return self._bus.read_block(self._addr, SB_MANUFACTURER_DATA, 32)

    # --- writes (guarded) ---

    def grant_write_permission(self, acknowledged: bool) -> None:
        if not acknowledged:
            raise ConsentRequired(_ENABLE_WRITES)
        self._write_consent = datetime.now().isoformat()

    def write_word(self, reg: int, value: int) -> None:
        self._require_consent()
        self._bus.write_word(self._addr, reg, value)

    def _require_consent(self) -> None:
        if not self._write_consent:
            raise ConsentRequired(_ENABLE_WRITES)

    # --- helpers ---

    def _read_maybe(self, reg: int) -> Optional[int]:
        try:
            return self._bus.read_word(self._addr, reg)
        except SMbusError:
            return None

    def _temperature_c(self) -> Optional[float]:
        raw = self._read_maybe(SB_TEMPERATURE)
        if raw is None or raw in (0, 0xFFFF):
            return None
        return raw / 10.0 - 273.15

    def _read_string(self, reg: int) -> str:
        raw = self._bus.read_block(self._addr, reg, 32)
        # SMBus "Bytes" format: first byte = string length, rest are chars.
        if not raw:
            raise SMbusError("empty block")
        n = raw[0]
        chars = raw[1:1 + n]
        return b"".join(bytes([c]) for c in chars).decode("ascii", "replace").strip("\x00").strip()


# Dummy bus for development/testing (and to prove the interface works offline).
class FakeSMbusBus(SMbusBus):
    """A controllable in-memory bus used for tests and the demo path."""

    def __init__(self, regs: Optional[Dict[int, int]] = None):
        self.regs = dict(regs or {})
        self.blocks: Dict[int, bytes] = {}
        self.writes: List[tuple] = []

    def read_word(self, addr: int, reg: int) -> int:
        if addr != FuelGaugeInterop.GAUGE_ADDR_DEFAULT:
            raise SMbusError("bad addr")
        if reg not in self.regs:
            raise SMbusError("no data")
        return self.regs[reg]

    def write_word(self, addr: int, reg: int, value: int) -> None:
        self.writes.append((addr, reg, value))
        self.regs[reg] = value

    def read_block(self, addr: int, reg: int, length: int) -> bytes:
        return self.blocks.get(reg, b"")
