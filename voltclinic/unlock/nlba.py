"""NLBA USB adapter driver.

The NIP/NLBA1 is a USB battery analyzer that appears as VID:PID 1a6e:089a.
It uses a custom USB protocol for talking to Smart Battery (SBS) fuel gauges.
This implementation provides support through the SMBusBus interface.

All operations are read-mostly by default. Any EEPROM/gauge writes require
explicit consent through the existing FuelGaugeInterop mechanism.
"""
from __future__ import annotations

import struct
import time
from typing import Optional

from .smbus import SMbusBus, SMbusError

NLBA_VID = 0x1a6e
NLBA_PID = 0x089a


class NLBASmbus(SMbusBus):
    """NLBA USB-SMBus bridge driver."""

    def __init__(self):
        self._dev = None
        self._ep_out = None
        self._ep_in = None
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
                try:
                    self._dev.detach_kernel_driver(0)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self._dev.set_configuration()
        except Exception:
            pass

        # Try to find endpoints if in application mode
        try:
            cfg = self._dev.get_active_configuration()
            intf = cfg[(0, 0)]
            for ep in intf:
                if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_OUT:
                    self._ep_out = ep
                else:
                    self._ep_in = ep
        except Exception:
            pass

        # Try to exit DFU mode if present
        try:
            # DFU_DETACH
            self._dev.ctrl_transfer(0x21, 0x00, 1000, 0, 0)
            time.sleep(0.5)
        except Exception:
            pass
        try:
            # DFU_CLRSTATUS
            self._dev.ctrl_transfer(0x21, 0x04, 0, 0, 0)
            time.sleep(0.1)
        except Exception:
            pass

    def _send_command(self, cmd: bytes, timeout: int = 1000) -> bytes:
        try:
            import usb.core
            import usb.util
        except ImportError:
            raise SMbusError("pyusb required")

        if self._dev is None:
            raise SMbusError("NLBA device not connected")

        # Try bulk transfer first
        if self._ep_out is not None:
            try:
                sent = self._dev.write(self._ep_out.bEndpointAddress, cmd, timeout)
                if self._ep_in is not None:
                    try:
                        resp = self._dev.read(self._ep_in.bEndpointAddress, 64, timeout)
                        return bytes(resp)
                    except Exception:
                        pass
                return b""
            except Exception:
                pass

        # Try control transfer
        try:
            # Vendor specific control transfer
            bmRequestType = 0x40
            bRequest = 0xC0
            wValue = 0
            wIndex = 0
            self._dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, cmd, timeout)
            try:
                bmRequestType = 0xC0
                resp = self._dev.ctrl_transfer(bmRequestType, 0xC1, 0, 0, 64, timeout)
                return bytes(resp)
            except Exception:
                pass
        except Exception:
            pass

        raise SMbusError("NLBA communication failed")

    def read_word(self, addr: int, reg: int) -> int:
        try:
            import usb.core
        except ImportError:
            raise SMbusError("pyusb required")

        if self._dev is None:
            raise SMbusError("NLBA device not connected")

        # Try common NLBA command formats
        # Format 1: direct SMBus bridge style
        try:
            bmRequestType = 0xC0
            bRequest = 0xB0
            wValue = (reg << 8) | (addr & 0x7F)
            wIndex = 0
            data = self._dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, 2, 1000)
            if len(data) >= 2:
                return data[0] | (data[1] << 8)
        except Exception:
            pass

        # Format 2: alternative
        try:
            bmRequestType = 0xC0
            bRequest = 0x01
            wValue = reg
            wIndex = addr
            data = self._dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, 2, 1000)
            if len(data) >= 2:
                return data[0] | (data[1] << 8)
        except Exception:
            pass

        # Format 3: NLBA style with different request
        try:
            bmRequestType = 0xC0
            bRequest = 0xD0
            wValue = (addr << 8) | reg
            wIndex = 0x0001
            data = self._dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, 2, 1000)
            if len(data) >= 2:
                return data[0] | (data[1] << 8)
        except Exception:
            pass

        # Format 4
        try:
            bmRequestType = 0xC0
            bRequest = 0x52
            wValue = reg
            wIndex = addr
            data = self._dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, 4, 1000)
            if len(data) >= 2:
                return data[0] | (data[1] << 8)
        except Exception:
            pass

        raise SMbusError(f"NLBA read_word failed at reg 0x{reg:02X}")

    def write_word(self, addr: int, reg: int, value: int) -> None:
        try:
            import usb.core
        except ImportError:
            raise SMbusError("pyusb required")

        if self._dev is None:
            raise SMbusError("NLBA device not connected")

        try:
            bmRequestType = 0x40
            bRequest = 0xB1
            wValue = (reg << 8) | (addr & 0x7F)
            wIndex = value & 0xFFFF
            self._dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, 0, 1000)
            return
        except Exception:
            pass

        try:
            bmRequestType = 0x40
            bRequest = 0x02
            wValue = reg
            wIndex = (addr << 8) | (value & 0xFF)
            self._dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, bytes([(value >> 8) & 0xFF]), 1000)
            return
        except Exception:
            pass

        raise SMbusError(f"NLBA write_word failed at reg 0x{reg:02X}")

    def read_block(self, addr: int, reg: int, length: int) -> bytes:
        try:
            import usb.core
        except ImportError:
            raise SMbusError("pyusb required")

        if self._dev is None:
            raise SMbusError("NLBA device not connected")

        try:
            bmRequestType = 0xC0
            bRequest = 0xB2
            wValue = (reg << 8) | (addr & 0x7F)
            wIndex = min(length, 255)
            data = self._dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, length + 1, 1000)
            if not data:
                raise SMbusError(f"NLBA read_block failed at reg 0x{reg:02X}")
            n = data[0]
            if n > 0 and n + 1 <= len(data):
                return bytes(data[1 : 1 + n])
            if len(data) > 1:
                return bytes(data[1 : 1 + n]) if n < len(data) else bytes(data[1:])
            return bytes(data)
        except Exception as e:
            raise SMbusError(f"NLBA read_block failed: {e}") from e

    def close(self) -> None:
        try:
            import usb.util
            if self._dev:
                usb.util.dispose_resources(self._dev)
                self._dev = None
        except Exception:
            pass
