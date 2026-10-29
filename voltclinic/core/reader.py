"""Battery read layer with platform backends.

Windows backend uses the documented battery class driver interface
(SetupAPI enumeration + IOCTL_BATTERY_QUERY_TAG / _INFORMATION / _STATUS via
DeviceIoControl) implemented with pure ctypes - no pywin32 dependency.
Reference: Microsoft docs "Battery Device Information" / poclass.h.

On non-Windows (and for testing) a NullBackend returns no batteries, so
cross-platform development and tests work cleanly.

This layer is strictly READ-ONLY. It never writes to battery hardware.
"""

from __future__ import annotations

import platform

from .models import BatteryInfo, ChargeState, normalize_chemistry


class BatteryReadError(Exception):
    pass


class BatteryBackend:
    name = "base"

    def enumerate_batteries(self) -> list[BatteryInfo]:
        raise NotImplementedError


def _to_float(v) -> float | None:
    try:
        f = float(v)
        return f if f >= 0 else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Windows: SetupAPI + battery IOCTLs (pure ctypes)
# ---------------------------------------------------------------------------

if platform.system() == "Windows":  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    # GUID_DEVCLASS_BATTERY == GUID_DEVICE_BATTERY (batclass.h / MS docs)
    _GUID_BATTERY = _GUID(
        0x72631E54, 0x78A4, 0x11D0, (0xBC, 0xF7, 0x00, 0xAA, 0x00, 0xB7, 0xB3, 0x2A)
    )

    DIGCF_PRESENT = 0x02
    DIGCF_DEVICEINTERFACE = 0x10
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1)
    ERROR_INSUFFICIENT_BUFFER = 122
    ERROR_NO_MORE_ITEMS = 259
    BATTERY_TAG_INVALID = 0xFFFFFFFF
    BATTERY_UNKNOWN_CAPACITY = 0xFFFFFFFF
    BATTERY_UNKNOWN_VOLTAGE = 0xFFFFFFFF
    BATTERY_UNKNOWN_RATE = 0x80000000  # LONG_MIN sentinel

    # CTL_CODE(FILE_DEVICE_BATTERY=0x29, function, METHOD_BUFFERED, FILE_READ_ACCESS)
    IOCTL_BATTERY_QUERY_TAG = 0x294040
    IOCTL_BATTERY_QUERY_INFORMATION = 0x294044
    IOCTL_BATTERY_QUERY_STATUS = 0x29404C

    # BATTERY_QUERY_INFORMATION_LEVEL
    BQI_INFORMATION = 0
    BQI_TEMPERATURE = 2
    BQI_DEVICE_NAME = 4
    BQI_MANUFACTURE_DATE = 5
    BQI_MANUFACTURE_NAME = 6
    BQI_SERIAL_NUMBER = 8

    # BATTERY_STATUS.PowerState flags
    BATTERY_POWER_ON_LINE = 0x1
    BATTERY_DISCHARGING = 0x2
    BATTERY_CHARGING = 0x4
    BATTERY_CRITICAL = 0x8

    class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("InterfaceClassGuid", _GUID),
            ("Flags", wintypes.DWORD),
            ("Reserved", ctypes.c_void_p),
        ]

    class _SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
        # Variable-size; on x64 cbSize is 8 (pointer + DWORD packing quirk)
        _fields_ = [("cbSize", wintypes.DWORD), ("DevicePath", wintypes.WCHAR * 1)]

    class _BATTERY_QUERY_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BatteryTag", wintypes.ULONG),
            ("InformationLevel", wintypes.ULONG),
            ("AtRate", wintypes.LONG),
        ]

    class _BATTERY_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("Capabilities", wintypes.ULONG),
            ("Technology", ctypes.c_ubyte),
            ("Reserved", ctypes.c_ubyte * 3),
            ("Chemistry", ctypes.c_ubyte * 4),
            ("DesignedCapacity", wintypes.ULONG),
            ("FullChargedCapacity", wintypes.ULONG),
            ("DefaultAlert1", wintypes.ULONG),
            ("DefaultAlert2", wintypes.ULONG),
            ("CriticalBias", wintypes.ULONG),
            ("CycleCount", wintypes.ULONG),
        ]

    class _BATTERY_STATUS(ctypes.Structure):
        _fields_ = [
            ("PowerState", wintypes.ULONG),
            ("Capacity", wintypes.ULONG),
            ("Voltage", wintypes.ULONG),
            ("Rate", wintypes.LONG),
        ]

    class _SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("Reserved1", ctypes.c_ubyte),
            ("BatteryLifeTime", wintypes.DWORD),
            ("BatteryFullLifeTime", wintypes.DWORD),
        ]

    class WindowsIOCTLBackend(BatteryBackend):
        """Read batteries via the documented battery class driver IOCTLs."""

        name = "windows-ioctl"

        def __init__(self):
            self._setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
            self._setupapi.SetupDiGetClassDevsW.argtypes = [
                ctypes.POINTER(_GUID),
                wintypes.LPCWSTR,
                wintypes.HWND,
                wintypes.DWORD,
            ]
            self._setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
                wintypes.HANDLE,
                ctypes.c_void_p,
                ctypes.POINTER(_GUID),
                wintypes.DWORD,
                ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
            ]
            self._setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.c_void_p,
            ]
            self._setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
            self._kernel32.CreateFileW.restype = wintypes.HANDLE
            self._kernel32.CreateFileW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
            ]
            self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            self._kernel32.DeviceIoControl.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.c_void_p,
            ]
            self._kernel32.GetSystemPowerStatus.argtypes = [
                ctypes.POINTER(_SYSTEM_POWER_STATUS)
            ]

        # -- helpers --

        def _ac_online(self) -> bool | None:
            sps = _SYSTEM_POWER_STATUS()
            if self._kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
                if sps.ACLineStatus in (0, 1):
                    return sps.ACLineStatus == 1
            return None

        def _device_paths(self) -> list[str]:
            paths: list[str] = []
            devinfo = self._setupapi.SetupDiGetClassDevsW(
                ctypes.byref(_GUID_BATTERY),
                None,
                None,
                DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
            )
            if devinfo == INVALID_HANDLE_VALUE or not devinfo:
                return paths
            try:
                idx = 0
                while True:
                    iface = _SP_DEVICE_INTERFACE_DATA()
                    iface.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
                    if not self._setupapi.SetupDiEnumDeviceInterfaces(
                        devinfo,
                        None,
                        ctypes.byref(_GUID_BATTERY),
                        idx,
                        ctypes.byref(iface),
                    ):
                        break
                    idx += 1
                    need = wintypes.DWORD(0)
                    if (
                        self._setupapi.SetupDiGetDeviceInterfaceDetailW(
                            devinfo,
                            ctypes.byref(iface),
                            None,
                            0,
                            ctypes.byref(need),
                            None,
                        )
                        or ctypes.get_last_error() != ERROR_INSUFFICIENT_BUFFER
                    ):
                        continue
                    buf = ctypes.create_unicode_buffer(need.value)
                    detail = ctypes.cast(
                        buf, ctypes.POINTER(_SP_DEVICE_INTERFACE_DETAIL_DATA_W)
                    )
                    detail.contents.cbSize = (
                        8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
                    )
                    if self._setupapi.SetupDiGetDeviceInterfaceDetailW(
                        devinfo, ctypes.byref(iface), detail, need, None, None
                    ):
                        # Path starts inside the struct after cbSize
                        offset = ctypes.sizeof(wintypes.DWORD)
                        raw = buf.raw[offset : need.value]
                        path = raw.split("\x00", 1)[0]
                        if path:
                            paths.append(path)
            finally:
                self._setupapi.SetupDiDestroyDeviceInfoList(devinfo)
            return paths

        def _open(self, path: str):
            for access in (0x80000000 | 0x40000000, 0x80000000):  # R|W, then R
                h = self._kernel32.CreateFileW(
                    path,
                    access,
                    0x3,  # FILE_SHARE_READ|FILE_SHARE_WRITE
                    None,
                    3,  # OPEN_EXISTING
                    0,
                    None,
                )
                if h != INVALID_HANDLE_VALUE and h:
                    return h
            return None

        def _ioctl(self, h, code, in_buf, in_size, out_type, out_len):
            out = out_type()
            ret = wintypes.DWORD(0)
            ok = self._kernel32.DeviceIoControl(
                h,
                code,
                in_buf,
                in_size,
                ctypes.byref(out),
                out_len,
                ctypes.byref(ret),
                None,
            )
            return out if ok and ret.value else None

        def _query_string(self, h, tag: int, level: int) -> str | None:
            q = _BATTERY_QUERY_INFORMATION(tag, level, 0)
            buf = ctypes.create_unicode_buffer(256)
            ret = wintypes.DWORD(0)
            ok = self._kernel32.DeviceIoControl(
                h,
                IOCTL_BATTERY_QUERY_INFORMATION,
                ctypes.byref(q),
                ctypes.sizeof(q),
                buf,
                ctypes.sizeof(buf),
                ctypes.byref(ret),
                None,
            )
            if ok and ret.value:
                s = buf.value
                return s if s else None
            return None

        def _query_temperature_c(self, h, tag: int) -> float | None:
            q = _BATTERY_QUERY_INFORMATION(tag, BQI_TEMPERATURE, 0)
            out = self._ioctl(
                h,
                IOCTL_BATTERY_QUERY_INFORMATION,
                ctypes.byref(q),
                ctypes.sizeof(q),
                wintypes.ULONG,
                ctypes.sizeof(wintypes.ULONG),
            )
            if out is not None and out.value not in (0, BATTERY_TAG_INVALID):
                return (out.value / 10.0) - 273.15
            return None

        # -- main entry --

        def enumerate_batteries(self) -> list[BatteryInfo]:
            out: list[BatteryInfo] = []
            ac = self._ac_online()
            for i, path in enumerate(self._device_paths()):
                h = self._open(path)
                if not h:
                    continue
                try:
                    wait = wintypes.ULONG(0)
                    tag_s = self._ioctl(
                        h,
                        IOCTL_BATTERY_QUERY_TAG,
                        ctypes.byref(wait),
                        ctypes.sizeof(wait),
                        wintypes.ULONG,
                        ctypes.sizeof(wintypes.ULONG),
                    )
                    if tag_s is None or tag_s.value == BATTERY_TAG_INVALID:
                        continue
                    tag = tag_s.value

                    binfo = BatteryInfo(device_index=i, ac_online=bool(ac))
                    info = self._ioctl(
                        h,
                        IOCTL_BATTERY_QUERY_INFORMATION,
                        ctypes.byref(
                            _BATTERY_QUERY_INFORMATION(tag, BQI_INFORMATION, 0)
                        ),
                        ctypes.sizeof(_BATTERY_QUERY_INFORMATION),
                        _BATTERY_INFORMATION,
                        ctypes.sizeof(_BATTERY_INFORMATION),
                    )
                    if info is not None:
                        chem_str = "".join(chr(c) for c in info.Chemistry if c)
                        binfo.chemistry = normalize_chemistry(chem_str)
                        binfo.raw["chemistry_raw"] = chem_str
                        if info.DesignedCapacity != BATTERY_UNKNOWN_CAPACITY:
                            binfo.design_capacity_mwh = float(info.DesignedCapacity)
                        if info.FullChargedCapacity not in (
                            0,
                            BATTERY_UNKNOWN_CAPACITY,
                        ):
                            binfo.full_capacity_mwh = float(info.FullChargedCapacity)
                        if info.CycleCount not in (0, BATTERY_UNKNOWN_CAPACITY):
                            binfo.cycle_count = int(info.CycleCount)
                        binfo.raw["technology"] = info.Technology  # 1=rechargeable

                    binfo.manufacturer = (
                        self._query_string(h, tag, BQI_MANUFACTURE_NAME) or "Unknown"
                    )
                    dev = self._query_string(h, tag, BQI_DEVICE_NAME)
                    binfo.name = dev or f"Battery {i + 1}"
                    serial = self._query_string(h, tag, BQI_SERIAL_NUMBER)
                    binfo.serial_number = serial or "Unknown"

                    status = self._ioctl(
                        h,
                        IOCTL_BATTERY_QUERY_STATUS,
                        ctypes.byref(_BATTERY_QUERY_INFORMATION(tag, 0, 0)),
                        ctypes.sizeof(_BATTERY_QUERY_INFORMATION),
                        _BATTERY_STATUS,
                        ctypes.sizeof(_BATTERY_STATUS),
                    )
                    if status is not None:
                        ps = status.PowerState
                        if ps & BATTERY_CHARGING:
                            binfo.charge_state = ChargeState.CHARGING
                        elif ps & BATTERY_DISCHARGING:
                            binfo.charge_state = ChargeState.DISCHARGING
                        elif ps & BATTERY_CRITICAL:
                            binfo.charge_state = ChargeState.CRITICAL
                        else:
                            binfo.charge_state = ChargeState.FULL
                        if status.Capacity not in (0, BATTERY_UNKNOWN_CAPACITY):
                            binfo.current_mwh = float(status.Capacity)
                        if status.Voltage != BATTERY_UNKNOWN_VOLTAGE:
                            binfo.voltage_mv = float(status.Voltage)
                        if status.Rate != BATTERY_UNKNOWN_RATE:
                            rate = int(status.Rate)
                            # Positive rate = charging per MS docs; expose both
                            if rate > 0:
                                binfo.charge_rate_mw = float(rate)
                            elif rate < 0:
                                binfo.discharge_rate_mw = float(-rate)
                            binfo.raw["rate_mw_signed"] = rate

                    binfo.temperature_c = self._query_temperature_c(h, tag)
                    out.append(binfo)
                finally:
                    self._kernel32.CloseHandle(h)
            return out


class NullBackend(BatteryBackend):
    """Returns nothing. Used on non-Windows hosts / for tests."""

    name = "null"

    def enumerate_batteries(self) -> list[BatteryInfo]:
        return []


def default_backend() -> BatteryBackend:
    if platform.system() == "Windows":
        try:
            return WindowsIOCTLBackend()  # type: ignore[name-defined]
        except Exception:  # noqa: BLE001
            return NullBackend()
    return NullBackend()


class BatteryReader:
    def __init__(self, backend: BatteryBackend | None = None):
        self._backend = backend or default_backend()

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def refresh(self) -> list[BatteryInfo]:
        return self._backend.enumerate_batteries()
