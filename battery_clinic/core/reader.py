"""Battery read layer with platform backends.

On Windows we use WMI (Win32_Battery) plus PowerStatus where appropriate.
On non-Windows (and for testing) we fall back to a NullBackend that returns
No batteries, so cross-platform development and tests work cleanly.

This layer is strictly READ-ONLY. It never writes to battery hardware.
"""
from __future__ import annotations

import platform
from typing import List, Optional

from .models import BatteryInfo, ChargeState, normalize_chemistry


class BatteryReadError(Exception):
    pass


class BatteryBackend:
    name = "base"

    def enumerate_batteries(self) -> List[BatteryInfo]:
        raise NotImplementedError


class WindowsWmiBackend(BatteryBackend):
    """Reads battery data via WMI (Win32_Battery) on Windows."""

    name = "windows-wmi"

    def __init__(self):
        import win32com.client  # pywin32
        self._conn = win32com.client.Dispatch("WbemScripting.SWbemLocator")
        self._svc = self._conn.ConnectServer(".", "root\\cimv2")

    def enumerate_batteries(self) -> List[BatteryInfo]:
        out: List[BatteryInfo] = []
        cols = ",".join([
            "Name", "Manufacturer", "SerialNumber", "DesignCapacity",
            "FullChargeCapacity", "EstimatedChargeRemaining", "BatteryStatus",
            "Chemistry", "EstimatedRunTime", "DesignVoltage", "DeviceID",
        ])
        try:
            devices = self._svc.ExecQuery(
                f"SELECT {cols} FROM Win32_Battery")
        except Exception as e:  # noqa: BLE001
            raise BatteryReadError(f"WMI query failed: {e}") from e

        for i, d in enumerate(devices):
            info = BatteryInfo(
                name=d.Name or f"Battery {i + 1}",
                device_index=i,
                manufacturer=d.Manufacturer or "Unknown",
                serial_number=d.SerialNumber or "Unknown",
                chemistry=normalize_chemistry(str(d.Chemistry)),
                # WMI capacities are in mWh
                design_capacity_mwh=_to_float(d.DesignCapacity),
                full_capacity_mwh=_to_float(d.FullChargeCapacity),
                current_mwh=(_to_float(d.FullChargeCapacity)
                             * (_to_float(d.EstimatedChargeRemaining) / 100.0)
                             if d.EstimatedChargeRemaining is not None
                             else None),
                charge_state=_wmi_status_to_state(d.BatteryStatus),
                charge_rate_mw=None,
                discharge_rate_mw=None,
                firmware_version=None,
            )
            info.raw = {
                "DeviceID": str(d.DeviceID),
                "BatteryStatus": d.BatteryStatus,
            }
            out.append(info)
        return out


def _wmi_status_to_state(status) -> ChargeState:
    # 1=discharging 2=AC 3=fully charged 4=low 5=critical ...
    try:
        s = int(status)
    except (TypeError, ValueError):
        return ChargeState.UNKNOWN
    if s == 3:
        return ChargeState.FULL
    if s in (4, 5):
        return ChargeState.CRITICAL
    if s == 1:
        return ChargeState.DISCHARGING
    if s in (2, 6, 7, 8, 9):
        return ChargeState.CHARGING
    return ChargeState.UNKNOWN


def _to_float(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f >= 0 else None
    except (TypeError, ValueError):
        return None


class NullBackend(BatteryBackend):
    """Returns nothing. Used on non-Windows hosts / for tests."""

    name = "null"

    def enumerate_batteries(self) -> List[BatteryInfo]:
        return []


def default_backend() -> BatteryBackend:
    if platform.system() == "Windows":
        try:
            return WindowsWmiBackend()
        except Exception:  # noqa: BLE001
            return NullBackend()
    return NullBackend()


class BatteryReader:
    def __init__(self, backend: Optional[BatteryBackend] = None):
        self._backend = backend or default_backend()

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def refresh(self) -> List[BatteryInfo]:
        return self._backend.enumerate_batteries()
