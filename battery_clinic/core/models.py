"""Battery data model and health calculations.

Pure data structures with no platform dependencies, so they are testable
everywhere (including on this Linux dev host).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class ChargeState(enum.Enum):
    CHARGING = "charging"
    DISCHARGING = "discharging"
    CRITICAL = "critical"
    FULL = "full"
    UNKNOWN = "unknown"


class Chemistry(enum.Enum):
    LI_ION = "Li-ion"
    LI_POLYMER = "Li-polymer"
    NI_MH = "NiMH"
    NI_CD = "NiCd"
    LFP = "LiFePO4"
    UNKNOWN = "Unknown"


def normalize_chemistry(raw: Optional[str]) -> Chemistry:
    if not raw:
        return Chemistry.UNKNOWN
    r = raw.strip().lower()
    if any(k in r for k in ("li-ion", "lion", "li ion", "lii")):
        return Chemistry.LI_ION
    if any(k in r for k in ("li-poly", "li poly", "lip", "polymer")):
        return Chemistry.LI_POLYMER
    if "nimh" in r or "ni-mh" in r:
        return Chemistry.NI_MH
    if "nicd" in r or "ni-cd" in r:
        return Chemistry.NI_CD
    if any(k in r for k in ("lifepo4", "lfp", "li-fe")):
        return Chemistry.LFP
    return Chemistry.UNKNOWN


def state_of_health(full_capacity_mwh: Optional[float],
                    design_capacity_mwh: Optional[float]) -> Optional[float]:
    """Return SoH as a percentage (0-100+) or None if unknown."""
    if full_capacity_mwh is None or design_capacity_mwh is None:
        return None
    if design_capacity_mwh <= 0:
        return None
    return (full_capacity_mwh / design_capacity_mwh) * 100.0


@dataclass
class BatteryInfo:
    """Snapshot of a battery as reported by the platform read layer."""

    name: str = "Unknown Battery"
    device_index: int = 0
    manufacturer: str = "Unknown"
    serial_number: str = "Unknown"
    chemistry: Chemistry = Chemistry.UNKNOWN
    design_capacity_mwh: Optional[float] = None
    full_capacity_mwh: Optional[float] = None
    current_mwh: Optional[float] = None          # remaining energy
    current_ma: Optional[float] = None           # signed; + charge, - discharge
    voltage_mv: Optional[float] = None
    temperature_c: Optional[float] = None
    cycle_count: Optional[int] = None
    charge_state: ChargeState = ChargeState.UNKNOWN
    ac_online: bool = False
    charge_rate_mw: Optional[float] = None
    discharge_rate_mw: Optional[float] = None
    estimated_time_remaining_min: Optional[int] = None
    firmware_version: Optional[str] = None
    raw: dict = field(default_factory=dict)
    collected_at: datetime = field(default_factory=datetime.now)

    @property
    def soh_percent(self) -> Optional[float]:
        return state_of_health(self.full_capacity_mwh, self.design_capacity_mwh)

    @property
    def remaining_percent(self) -> Optional[float]:
        if self.full_capacity_mwh in (None, 0):
            return None
        if self.current_mwh is None:
            return None
        return (self.current_mwh / self.full_capacity_mwh) * 100.0

    @property
    def health_label(self) -> str:
        soh = self.soh_percent
        if soh is None:
            return "Unknown"
        if soh >= 95:
            return "Excellent"
        if soh >= 80:
            return "Good"
        if soh >= 60:
            return "Fair"
        if soh >= 40:
            return "Worn"
        return "Critical"

    def summary_lines(self) -> list[str]:
        lines = [
            f"Battery:        {self.name}",
            f"Device index:   {self.device_index}",
            f"Manufacturer:   {self.manufacturer}",
            f"Serial:         {self.serial_number}",
            f"Chemistry:      {self.chemistry.value}",
        ]
        def fmt(v, unit):
            return f"{v:.0f} {unit}" if v is not None else "Unknown"
        lines.append(f"Design capacity:{fmt(self.design_capacity_mwh, 'mWh')}")
        lines.append(f"Full capacity:  {fmt(self.full_capacity_mwh, 'mWh')}")
        lines.append(f"State of Health:{self.health_label} "
                     f"({soh:.1f}%)" if (soh := self.soh_percent) is not None
                     else "State of Health: Unknown")
        lines.append(f"Remaining:      {fmt(self.current_mwh, 'mWh')}"
                     f" ({self.remaining_percent:.0f}%)" if self.remaining_percent is not None
                     else f"Remaining:      {fmt(self.current_mwh, 'mWh')}")
        lines.append(f"Charge rate:    {fmt(self.charge_rate_mw, 'mW')}")
        lines.append(f"Discharge rate: {fmt(self.discharge_rate_mw, 'mW')}")
        lines.append(f"Cycle count:    {self.cycle_count if self.cycle_count is not None else 'Unknown'}")
        lines.append(f"Firmware:       {self.firmware_version or 'Unknown'}")
        return lines
