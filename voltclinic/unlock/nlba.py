"""NLBA USB adapter driver.

The NIP/NLBA1 is a USB battery analyzer that appears as VID:PID 1a6e:089a.
It uses a custom USB protocol for talking to Smart Battery (SBS) fuel gauges.
This implementation provides basic read support through the SMBusBus interface.

All operations are read-mostly by default. Any EEPROM/gauge writes require
explicit consent through the existing FuelGaugeInterop mechanism.
"""
from __future__ import annotations

import struct
from typing import Optional

from .smbus import SMbusBus, SMbusError

NLBA_VID = 0x1a6e
NLBA_PID = 0x089a


class NLBASmbus(SMbusBus):
    """NLBA USB-SMBus bridge driver."""

    def __init__(self):
        self._dev = None
        self._handle = None
        self._connect()

    def _connect(self) -> None:
        try:
            import usb.core
            import usb.util
        except ImportError as e:
            raise SMbusError(
                f"pyusb required for NLBA adapter: {e}. "
                "Install with: pip install pyusb libusb"
            ) from e

        self._dev = usb.core.find(idVendor=NLBA_VID, idProduct=NLBA_PID)
        if self._dev is None:
            raise SMbusError("NLBA device not found (VID:PID 1a6e:089a)")

        try:
            if self._dev.is_kernel_driver_active(0):
                self._dev.detach_kernel_driver(0)
        except Exception:
            pass

        try:
            self._dev.set_configuration()
        except Exception:
            pass

    def read_word(self, addr: int, reg: int) -> int:
        # Try using common NLBA command patterns
        # This is a basic implementation - may need refinement
        try:
            import usb.core
            import usb.util
        except ImportError:
            raise SMbusError("pyusb required")

        if self._dev is None:
            raise SMbusError("NLBA device not connected")

        # Try control transfer pattern
        try:
            # Standard SMBus read word via NLBA
            # This is speculative - may need protocol capture
            bmRequestType = 0xC0  # Vendor request, device to host
            bRequest = 0xB0  # Read command
            wValue = (reg << 8) | addr
            wIndex = 0
            data = self._dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, 2)
            if len(data) == 2:
                return data[0] | (data[1] << 8)
            raise SMbusError(f"NLBA read_word failed at reg 0x{reg:02X}")
        except usb.core.USBError as e:
            raise SMbusError(f"NLBA read_word error: {e}") from e

    def write_word(self, addr: int, reg: int, value: int) -> None:
        try:
            import usb.core
        except ImportError:
            raise SMbusError("pyusb required")

        if self._dev is None:
            raise SMbusError("NLBA device not connected")

        try:
            bmRequestType = 0x40  # Vendor request, host to device
            bRequest = 0xB1  # Write command
            wValue = (reg << 8) | addr
            wIndex = value & 0xFFFF
            self._dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, 0)
        except usb.core.USBError as e:
            raise SMbusError(f"NLBA write_word error: {e}") from e

    def read_block(self, addr: int, reg: int, length: int) -> bytes:
        try:
            import usb.core
        except ImportError:
            raise SMbusError("pyusb required")

        if self._dev is None:
            raise SMbusError("NLBA device not connected")

        try:
            bmRequestType = 0xC0
            bRequest = 0xB2  # Block read
            wValue = (reg << 8) | addr
            wIndex = length & 0xFFFF
            data = self._dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, length + 1)
            if not data:
                raise SMbusError(f"NLBA read_block failed at reg 0x{reg:02X}")
            # First byte is usually length
            n = data[0]
            if n > 0 and n + 1 <= len(data):
                return bytes(data[1 : 1 + n])
            return bytes(data[1:]) if len(data) > 1 else bytes(data)
        except usb.core.USBError as e:
            raise SMbusError(f"NLBA read_block error: {e}") from e

    def close(self) -> None:
        try:
            import usb.util
            if self._dev:
                usb.util.dispose_resources(self._dev)
                self._dev = None
        except Exception:
            pass
