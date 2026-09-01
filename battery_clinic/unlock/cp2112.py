"""CP2112 USB-to-SMBus adapter driver (pure Python).

CP2112 is Silicon Labs' HID USB-to-SMBus bridge. It is a *native* SMBus host
controller (handles clock stretching / SMBus framing that fuel gauges need) and
presents itself to the OS as a plain HID device, so on Windows no vendor kernel
driver is required - the built-in HID stack is enough.

The host protocol is documented in Silicon Labs Application Note AN495
('CP2112 Interface Specification'). Everything here is reimplemented from that
public specification; no Silicon Labs source code is used.

Layout
------
- Part A: report packing/parsing *pure* functions (unit-tested everywhere).
- Part B: ``HidApi`` - ctypes wrapper over hid.dll + setupapi.dll.
- Part C: ``Cp2112Smbus(SMbusBus)`` - drives a CP2112 to do SMBus transfers.

On non-Windows hosts only Part A is importable. Consuming the adapter for
battery reads/writes goes through the consent-gated ``FuelGaugeInterop`` layer.
"""
from __future__ import annotations

import ctypes
import platform
import struct
from ctypes import wintypes
from typing import List, Optional

from .smbus import SMbusBus, SMbusError

# ---------------------------------------------------------------------------
# Part A - protocol framing (pure functions, testable everywhere)
# ---------------------------------------------------------------------------

RID_RESET = 0x01
RID_SMBUS_CONFIG = 0x06          # feature report
RID_DATA_READ_REQUEST = 0x10     # output
RID_WRITE_READ_REQUEST = 0x11    # output (addressed read, repeated start)
RID_DATA_READ_FORCE_SEND = 0x12  # output
RID_DATA_READ_RESPONSE = 0x13    # input
RID_DATA_WRITE_REQUEST = 0x14    # output
RID_TRANSFER_STATUS_REQUEST = 0x15  # output
RID_TRANSFER_STATUS_RESPONSE = 0x16  # input
RID_CANCEL_TRANSFER = 0x17       # output

TSTATUS_IDLE = 0x00
TSTATUS_BUSY = 0x01
TSTATUS_COMPLETE = 0x02
TSTATUS_ERROR = 0x03
TSTATUS_CRC_ERROR = 0x04
TSTATUS_ACK_ERROR = 0x05
TSTATUS_NAK_ERROR = 0x06
TSTATUS_BUS_ERROR = 0x07
TSTATUS_READ_ERROR = 0x08
TSTATUS_WRITE_ERROR = 0x09
TSTATUS_ARBITRATION_LOST = 0x0A
TSTATUS_TERMINATED = 0x0B
TSTATUS_CANCELLED = 0x0C
TSTATUS_SHORT_READ = 0x0D
TSTATUS_SMBUS_IDLE_TIMEOUT = 0x0E

CP2112_VID = 0x10C4
CP2112_PID = 0xEA90

# AN495: all reports are exactly 64 bytes, index 0 is the Report ID.
REPORT_LEN = 64
SMBUS_DEFAULT_CLOCK_HZ = 100000


def _pad64(payload: bytes) -> bytes:
    out = bytearray(REPORT_LEN)
    out[: len(payload)] = payload
    return bytes(out)


def report_smbus_config(clock_hz: int = SMBUS_DEFAULT_CLOCK_HZ,
                        device_address: int = 0x02,
                        auto_send_read: int = 1,
                        write_timeout_ms: int = 0,
                        read_timeout_ms: int = 1000,
                        scl_low_timeout: int = 0) -> bytes:
    """SMBus config feature report (AN495 4.6 / 5.6). 12 bytes, BE fields.

    Layout: ReportID(1) clock_u32(4) addr(1) auto_send_read(1)
            write_timeout_u16(2) read_timeout_u16(2) scl_low_timeout(1)
    """
    body = struct.pack(
        ">B I B B H H B",
        RID_SMBUS_CONFIG, clock_hz & 0xFFFFFFFF, device_address & 0xFF,
        auto_send_read & 0xFF, write_timeout_ms & 0xFFFF,
        read_timeout_ms & 0xFFFF, scl_low_timeout & 0xFF)
    return _pad64(body)


def report_write_read_request(slave_addr: int, length: int,
                              target_address: bytes) -> bytes:
    """Report ID 0x11 Data Write Read Request (addressed read, repeated start)."""
    if not 1 <= length <= 512:
        raise SMbusError("read length out of range")
    if not 1 <= len(target_address) <= 16:
        raise SMbusError("target address length out of range")
    buf = bytearray(REPORT_LEN)
    buf[0] = RID_WRITE_READ_REQUEST
    buf[1] = (slave_addr << 1) & 0xFF
    buf[2:4] = struct.pack(">H", length)
    buf[4] = len(target_address)
    buf[5:5 + len(target_address)] = target_address
    return bytes(buf)


def report_write_request(slave_addr: int, data: bytes) -> bytes:
    """Report ID 0x14 Data Write Request."""
    if not 1 <= len(data) <= 61:
        raise SMbusError("write data length out of range")
    buf = bytearray(REPORT_LEN)
    buf[0] = RID_DATA_WRITE_REQUEST
    buf[1] = (slave_addr << 1) & 0xFF
    buf[2] = len(data)
    buf[3:3 + len(data)] = data
    return bytes(buf)


def report_transfer_status_request() -> bytes:
    return _pad64(bytes([RID_TRANSFER_STATUS_REQUEST, 0x01]))


def report_read_force_send(requested_length: int) -> bytes:
    buf = bytearray(REPORT_LEN)
    buf[0:3] = struct.pack(">B H", RID_DATA_READ_FORCE_SEND, requested_length & 0xFFFF)
    return bytes(buf)


def parse_transfer_status(report: bytes) -> int:
    if not report or report[0] != RID_TRANSFER_STATUS_RESPONSE:
        return TSTATUS_ERROR
    return report[1]


def parse_read_response(report: bytes) -> bytes:
    """0x13 Data Read Response: byte0 = count, bytes 1.. = data."""
    if not report or report[0] != RID_DATA_READ_RESPONSE:
        return b""
    count = report[1]
    return bytes(report[2:2 + count])


def word_to_bytes(value: int) -> bytes:
    """SMBus word data is transmitted least-significant byte first."""
    return bytes([value & 0xFF, (value >> 8) & 0xFF])


def bytes_to_word(data: bytes) -> int:
    """SMBus word data arrives least-significant byte first."""
    if len(data) < 2:
        raise SMbusError("short word read")
    return data[0] | (data[1] << 8)


def block_to_blob(data: bytes) -> bytes:
    """A SMBus BLOCK read returns a leading length byte; drop it."""
    if not data:
        return b""
    n = data[0]
    return bytes(data[1:1 + n])


# ---------------------------------------------------------------------------
# Part B - Windows HID + SetupAPI wrapper
# ---------------------------------------------------------------------------

class HidException(Exception):
    pass


class HidDevice:
    def __init__(self, api, handle, vid: int, pid: int, path: str,
                 in_len: int = REPORT_LEN, feat_len: int = REPORT_LEN):
        self._api = api
        self._handle = handle
        self.vid = vid
        self.pid = pid
        self.path = path
        self.in_len = in_len          # bytes to read on the input endpoint
        self.feat_len = feat_len      # feature report length (incl. report id)

    def write_output_report(self, report: bytes) -> None:
        self._api.write_file(self._handle, _pad64(report))

    def read_input_report(self, timeout_ms: int = 2000) -> bytes:
        return self._api.read_file(self._handle, self.in_len, timeout_ms)

    def set_feature(self, report: bytes) -> None:
        self._api.set_feature(self._handle, _pad64(report)[:self.feat_len])

    def get_feature(self, report_id: int, report_len: int = 64) -> bytes:
        return self._api.get_feature(self._handle, report_id, report_len)

    def close(self) -> None:
        if self._handle:
            try:
                self._api.close(self._handle)
            finally:
                self._handle = None


class _HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", wintypes.ULONG), ("VendorID", wintypes.USHORT),
                ("ProductID", wintypes.USHORT), ("VersionNumber", wintypes.USHORT)]


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD),
                ("InterfaceClassGuid", ctypes.c_byte * 16),
                ("Flags", wintypes.DWORD), ("Reserved", ctypes.c_void_p)]


class HidApi:
    """Minimal ctypes wrapper over hid.dll + setupapi.dll (HID stack)."""

    def __init__(self):
        try:
            self._hid = ctypes.WinDLL("hid.dll")
            self._setupapi = ctypes.WinDLL("setupapi.dll")
            self._kernel32 = ctypes.WinDLL("kernel32.dll")
        except OSError as e:  # pragma: no cover
            raise HidException(f"could not load HID/SetupAPI: {e}") from e

        guid_t = ctypes.POINTER(ctypes.c_byte * 16)
        self._hid.HidD_GetHidGuid.argtypes = [guid_t]
        self._hid.HidD_GetAttributes.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_HIDD_ATTRIBUTES)]
        self._hid.HidD_SetFeature.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
        self._hid.HidD_GetFeature.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]

        self._setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
        self._setupapi.SetupDiGetClassDevsW.argtypes = [
            guid_t, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
        self._setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, guid_t, wintypes.DWORD,
            ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA)]
        self._setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_SP_DEVICE_INTERFACE_DATA),
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p]
        self._setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

        self._kernel32.CreateFileW.restype = wintypes.HANDLE
        self._kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.ReadFile.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self._kernel32.WriteFile.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]

    def enumerate_hid_paths(self) -> List[tuple]:
        result: List[tuple] = []
        guid = (ctypes.c_byte * 16)()
        self._hid.HidD_GetHidGuid(ctypes.byref(guid))
        devinfo = self._setupapi.SetupDiGetClassDevsW(
            ctypes.byref(guid), None, None, 0x12)  # PRESENT | DEVICEINTERFACE
        if not devinfo or devinfo == wintypes.HANDLE(-1):
            return result
        try:
            idx = 0
            while True:
                iface = _SP_DEVICE_INTERFACE_DATA()
                iface.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
                if not self._setupapi.SetupDiEnumDeviceInterfaces(
                        devinfo, None, ctypes.byref(guid), idx,
                        ctypes.byref(iface)):
                    break
                idx += 1
                need = wintypes.DWORD(0)
                self._setupapi.SetupDiGetDeviceInterfaceDetailW(
                    devinfo, ctypes.byref(iface), None, 0,
                    ctypes.byref(need), None)
                if need.value < 8:
                    continue

                class _Detail(ctypes.Structure):
                    _fields_ = [("cbSize", wintypes.DWORD),
                                ("DevicePath", wintypes.WCHAR * int(need.value))]
                detail = _Detail()
                detail.cbSize = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
                ok = self._setupapi.SetupDiGetDeviceInterfaceDetailW(
                    devinfo, ctypes.byref(iface), ctypes.byref(detail),
                    need, None, None)
                if not ok or not detail.DevicePath:
                    continue
                path = str(detail.DevicePath)
                vid, pid = self._path_ids(path)
                result.append((path, vid, pid))
        finally:
            self._setupapi.SetupDiDestroyDeviceInfoList(devinfo)
        return result

    @staticmethod
    def _path_ids(path: str):
        vid = pid = 0
        try:
            vid = int(path.split("VID_", 1)[1][:4], 16)
            pid = int(path.split("PID_", 1)[1][:4], 16)
        except (IndexError, ValueError):
            pass
        return vid, pid

    def open_device(self, path: str, vid: int, pid: int) -> HidDevice:
        handle = self._kernel32.CreateFileW(
            path, 0xC0000000, 0x3, None, 3, 0, None)
        if not handle or handle == wintypes.HANDLE(-1):
            raise HidException(f"failed to open HID device: {path}")
        try:
            attrs = _HIDD_ATTRIBUTES()
            attrs.Size = ctypes.sizeof(_HIDD_ATTRIBUTES)
            self._hid.HidD_GetAttributes(handle, ctypes.byref(attrs))
        except Exception:  # pragma: no cover
            attrs = None
        v = getattr(attrs, "VendorID", vid)
        p = getattr(attrs, "ProductID", pid)
        return HidDevice(self, handle, v, p, path,
                         in_len=REPORT_LEN, feat_len=REPORT_LEN)

    def write_file(self, handle, report: bytes) -> None:
        buf = (ctypes.c_ubyte * len(report)).from_buffer_copy(report)
        n = wintypes.DWORD(0)
        ok = self._kernel32.WriteFile(handle, buf, len(report),
                                      ctypes.byref(n), None)
        if not ok:
            raise HidException("HID write failed "
                               f"(GetLastError={ctypes.get_last_error()})")

    def read_file(self, handle, size: int, timeout_ms: int) -> bytes:
        buf = (ctypes.c_ubyte * size)()
        n = wintypes.DWORD(0)
        ok = self._kernel32.ReadFile(handle, buf, size, ctypes.byref(n), None)
        if not ok:
            raise HidException("HID read failed "
                               f"(GetLastError={ctypes.get_last_error()})")
        return bytes(buf)[: n.value]

    def set_feature(self, handle, report: bytes) -> None:
        buf = (ctypes.c_ubyte * len(report)).from_buffer_copy(report)
        ok = self._hid.HidD_SetFeature(handle, ctypes.byref(buf), len(report))
        if not ok:
            raise HidException("HidD_SetFeature failed "
                               f"(GetLastError={ctypes.get_last_error()})")

    def get_feature(self, handle, report_id: int, report_len: int) -> bytes:
        buf = (ctypes.c_ubyte * report_len)()
        buf[0] = report_id
        ok = self._hid.HidD_GetFeature(handle, ctypes.byref(buf), report_len)
        if not ok:
            raise HidException("HidD_GetFeature failed "
                               f"(GetLastError={ctypes.get_last_error()})")
        return bytes(buf)

    def close(self, handle) -> None:
        self._kernel32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# Part C - CP2112 SMBus driver
# ---------------------------------------------------------------------------

class Cp2112Smbus(SMbusBus):
    """Drive a CP2112 to perform SMBus word/block transfers to a slave device.

    Implements ``SMbusBus`` so the existing consent-gated ``FuelGaugeInterop``
    works unchanged against real hardware.
    """

    def __init__(self, device: Optional[HidDevice] = None,
                 clock_hz: int = SMBUS_DEFAULT_CLOCK_HZ):
        api = self._api()
        self._api = api
        self._clock = clock_hz
        if device is not None:
            self._dev = device
            self._owned = False
        else:
            self._dev = self._open_first(api)
            self._owned = True
        if self._dev is None:
            raise SMbusError("no CP2112 adapter found")
        self._configure()

    @staticmethod
    def _api() -> HidApi:
        if platform.system() != "Windows":  # pragma: no cover - Linux/dev only
            raise SMbusError(
                "CP2112 requires Windows (HID API). On Linux use the hidraw "
                "kernel interface; build the adapter on a Windows host.")
        try:
            return HidApi()
        except HidException as e:
            raise SMbusError(str(e)) from e

    @staticmethod
    def find_adapters() -> List[tuple]:
        """Return [(path, vid, pid), ...] for connected CP2112 adapters."""
        if platform.system() != "Windows":  # pragma: no cover
            return []
        try:
            api = HidApi()
        except HidException:  # pragma: no cover
            return []
        return [(p, v, i) for (p, v, i) in api.enumerate_hid_paths()
                if v == CP2112_VID and i == CP2112_PID]

    @staticmethod
    def _open_first(api: HidApi):
        for path, vid, pid in api.enumerate_hid_paths():
            if vid == CP2112_VID and pid == CP2112_PID:
                try:
                    return api.open_device(path, vid, pid)
                except HidException:
                    continue
        return None

    def _configure(self) -> None:
        cfg = report_smbus_config(clock_hz=self._clock, auto_send_read=1,
                                  read_timeout_ms=1000, retry_time=1)
        self._dev.set_feature(cfg)

    def close(self) -> None:
        if self._owned and self._dev is not None:
            self._dev.close()
            self._dev = None

    # -- SMbusBus interface --

    def read_word(self, addr: int, reg: int) -> int:
        return bytes_to_word(self._write_read(addr, bytes([reg]), 2))

    def write_word(self, addr: int, reg: int, value: int) -> None:
        self._write(addr, bytes([reg & 0xFF]) + word_to_bytes(value))

    def read_byte(self, addr: int, reg: int) -> int:
        payload = self._write_read(addr, bytes([reg]), 1)
        return payload[0]

    def read_block(self, addr: int, reg: int, length: int) -> bytes:
        # Return the raw SMBus BLOCK payload (byte0 = byte-count, per the
        # Smart-Battery "Bytes" format) so the higher-level readers that expect
        # the leading length byte (e.g. FuelGaugeInterop._read_string) work.
        return self._write_read(addr, bytes([reg]), length)

    # -- transfer plumbing --

    def _write_read(self, addr: int, target: bytes, length: int) -> bytes:
        self._dev.write_output_report(
            report_write_read_request(addr, length, target))
        self._wait_complete()
        if length:
            return self._read_data(length)
        return b""

    def _write(self, addr: int, data: bytes) -> None:
        self._dev.write_output_report(report_write_request(addr, data))
        self._wait_complete()

    def _wait_complete(self, attempts: int = 60) -> None:
        for _ in range(attempts):
            self._dev.write_output_report(report_transfer_status_request())
            resp = self._dev.read_input_report(2000)
            status = parse_transfer_status(resp)
            if status == TSTATUS_COMPLETE or status == TSTATUS_IDLE:
                return
            if status == TSTATUS_BUSY:
                continue
            raise SMbusError(f"SMBus transfer failed (status 0x{status:02X})")
        raise SMbusError("SMBus transfer timed out")

    def _read_data(self, length: int) -> bytes:
        # auto_send_read=1 => the 0x13 response follows the completion status.
        for _ in range(5):
            resp = self._dev.read_input_report(3000)
            if not resp:
                continue
            if resp[0] == RID_DATA_READ_RESPONSE:
                return parse_read_response(resp)
        # fall back to a forced read response
        self._dev.write_output_report(report_read_force_send(length))
        resp = self._dev.read_input_report(3000)
        if resp and resp[0] == RID_DATA_READ_RESPONSE:
            return parse_read_response(resp)
        raise SMbusError("no data read response received")
