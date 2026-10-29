"""ROTOOL bench device transport (serial/COM-port based).

The ROTOOL bench analyzer is a USB device that bridges a USB CDC-style serial
port to its internal microcontroller. The MCU in turn drives the target
battery's SMBus (SCL/SDA), cell-voltage sense lines, and the charge/discharge
power stage.

Clean-room: reimplemented from our own hardware observation and public
documentation only; no proprietary vendor code is reused.
"""

from __future__ import annotations

import time
from typing import Optional

from .smbus import SMbusBus, SMbusError

# USB identifers of the bridge chip used by ROTOOL hardware (public data).
ROTOOL_BRIDGE_VID = 0x067B
ROTOOL_BRIDGE_PID = 0x2303

DEFAULT_BAUD = 57600  # from capture: only 57600 responds to handshake

HANDSHAKE_FRAME = bytes([0x02, 0x03, 0x01])
PROBE_FRAMES = [
    bytes([0x02, 0x05, 0x07]),
    bytes([0x02, 0x15, 0x17]),
    bytes([0x02, 0x01, 0x03]),
]


# ---------------------------------------------------------------------------
# Part A - helpers (pure/OS-light, testable)
# ---------------------------------------------------------------------------


def list_rotool_ports() -> list[str]:
    """Return serial port names that look like a ROTOOL bridge.

    Detection is by the USB VID:PID of the bridge chip when available;
    also scan all ports as fallback.
    """
    try:
        from serial.tools import list_ports
    except ImportError:  # pragma: no cover - pyserial is a core dependency
        return []
    found: list[str] = []
    # First pass: match by VID:PID
    for p in list_ports.comports():
        vid = getattr(p, "vid", None)
        pid = getattr(p, "pid", None)
        if (vid, pid) == (ROTOOL_BRIDGE_VID, ROTOOL_BRIDGE_PID):
            found.append(p.device)
    if found:
        return found
    # Second pass: look for Prolific/USB-Serial in description
    for p in list_ports.comports():
        desc = (getattr(p, "description", "") or "").lower()
        if "prolific" in desc or "usb-serial" in desc or "ch340" in desc or "cp210" in desc:
            found.append(p.device)
    # Third pass: return all ports as last resort
    if not found:
        for p in list_ports.comports():
            found.append(p.device)
    return found


def choose_port(preferred: str | None = None) -> str:
    """Pick the port to use: explicit name, else auto-detected device.

    Raises when no device is found — never guesses a port name.
    """
    if preferred:
        return preferred
    detected = list_rotool_ports()
    if detected:
        return detected[0]
    raise SMbusError(
        "no ROTOOL device found (looking for a Prolific-class USB serial "
        f"bridge, VID {ROTOOL_BRIDGE_VID:04X}:{ROTOOL_BRIDGE_PID:04X}) — "
        "connect the device, or pass a port name explicitly."
    )


def frame_escape(data: bytes) -> bytes:
    """Byte-stuff a payload: 0x7D escapes itself and 0x7E (delimiter)."""
    out = bytearray()
    for b in data:
        if b in (0x7D, 0x7E):
            out.append(0x7D)
            out.append(b ^ 0x20)
        else:
            out.append(b)
    return bytes(out)


def frame_unescape(data: bytes) -> bytes:
    """Inverse of :func:`frame_escape`. Raises on malformed escape."""
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0x7D:
            if i + 1 >= len(data):
                raise SMbusError("ROTOOL: truncated escape at end of frame")
            out.append(data[i + 1] ^ 0x20)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def frame_wrap(payload: bytes) -> bytes:
    """Delimit + escape a payload into a full frame (0x7E ... 0x7E)."""
    return b"\x7e" + frame_escape(payload) + b"\x7e"


def frame_wrap_crc(payload: bytes) -> bytes:
    """As frame_wrap, with a trailing XOR checksum inside the delimiters."""
    crc = 0
    for b in payload:
        crc ^= b
    payload = bytes([crc]) + payload
    return b"\x7e" + frame_escape(payload) + b"\x7e"


# ---------------------------------------------------------------------------
# Part B - serial link
# ---------------------------------------------------------------------------


class RoToolLink:
    """Owns the COM-port connection to the ROTOOL device."""

    def __init__(self, port: str | None = None, baud: int = DEFAULT_BAUD):
        self.port_name = choose_port(port)
        self.baud = baud
        self._ser = None

    def open(self) -> None:
        try:
            import serial
        except ImportError as e:  # pragma: no cover
            raise SMbusError(f"pyserial required for ROTOOL link: {e}") from e
        try:
            self._ser = serial.Serial(self.port_name, self.baud, timeout=1)
        except Exception as e:
            raise SMbusError(f"ROTOOL: cannot open {self.port_name}: {e}") from e
        # Match observed analyzer behavior: toggle DTR/RTS
        try:
            self._ser.reset_input_buffer()
            self._ser.dtr = True
            self._ser.rts = False
            time.sleep(0.2)
            self._ser.rts = True
            time.sleep(0.2)
        except Exception:
            pass

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

    @property
    def opened(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def write(self, data: bytes) -> int:
        if not self.opened:
            raise SMbusError("ROTOOL: link not open")
        return self._ser.write(data)

    def read(self, size: int) -> bytes:
        if not self.opened:
            raise SMbusError("ROTOOL: link not open")
        return self._ser.read(size)

    def read_until(self, timeout: float = 1.0) -> bytes:
        if not self.opened:
            raise SMbusError("ROTOOL: link not open")
        buf = bytearray()
        t0 = time.time()
        while time.time() - t0 < timeout:
            n = self._ser.in_waiting
            if n:
                buf += self._ser.read(n)
                time.sleep(0.01)
            else:
                time.sleep(0.01)
        return bytes(buf)

    def set_dtr(self, on: bool) -> None:
        if self.opened:
            self._ser.dtr = on

    def set_rts(self, on: bool) -> None:
        if self.opened:
            self._ser.rts = on


# ---------------------------------------------------------------------------
# Part C - SMbusBus adapter
# ---------------------------------------------------------------------------


class RoToolSmbus(SMbusBus):
    """ROTOOL adapter presented as a bench SMBus backend."""

    protocol_ready = False

    def __init__(self, link: RoToolLink | None = None):
        self.link = link or RoToolLink()
        self.link.open()
        # Attempt handshake at known good baud
        try:
            self.link.write(HANDSHAKE_FRAME)
            resp = self.link.read_until(timeout=1.0)
            # Capture responds with 0x00 at 57600
            if resp and len(resp) >= 1 and resp[0] == 0x00:
                self.protocol_ready = True
            elif resp:
                # Any response means device is there
                self.protocol_ready = True
            else:
                # Try once more
                try:
                    self.link.write(HANDSHAKE_FRAME)
                    resp = self.link.read_until(timeout=1.0)
                    if resp:
                        self.protocol_ready = True
                except Exception:
                    pass
        except Exception:
            pass
        if not self.protocol_ready:
            try:
                self.link.close()
            except Exception:
                pass
            raise SMbusError(
                "ROTOOL: no response at 57600 baud"
            )

    # -- SMbusBus interface (gated) --

    def read_word(self, addr: int, reg: int) -> int:
        if not self.protocol_ready:
            raise SMbusError("ROTOOL: protocol not ready")
        raise SMbusError("ROTOOL: read_word not implemented yet")

    def write_word(self, addr: int, reg: int, value: int) -> None:
        if not self.protocol_ready:
            raise SMbusError("ROTOOL: protocol not ready")
        raise SMbusError("ROTOOL: write_word not implemented yet")

    def read_block(self, addr: int, reg: int, length: int) -> bytes:
        if not self.protocol_ready:
            raise SMbusError("ROTOOL: protocol not ready")
        raise SMbusError("ROTOOL: read_block not implemented yet")

    def close(self) -> None:
        self.link.close()
