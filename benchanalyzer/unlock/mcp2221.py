"""MCP2221A USB-to-I2C adapter driver (pure Python).

The MCP2221A is Microchip's USB HID host bridge with an I2C master engine
(VID 0x04D8 PID 0x00DD). The I2C pins are the dedicated GP1 (SCL) / GP2
(SDA) pins, which are the factory-default alternate function, so no GPIO
reconfiguration is needed - the engine is used as-is at 100 kHz (SMBus).

The host protocol is documented in the public Microchip datasheet command
tables ("USB HID Communication"). Everything here is reimplemented from
that public specification; no vendor source is reused.

Layout
------
- Part A: report framing *pure* functions (unit-tested everywhere), mirroring
  the ``cp2112`` driver's structure (Part A pure / Part B HID / Part C bus).
- Part B: ``McpLink`` - owns the open HID device, ordered 64-byte transfers.
- Part C: ``Mcp2221Smbus(SMbusBus)`` - SMBus word/block transfers.

On non-Windows hosts only Part A and the detection helper are importable.
All gauge writes still go through the consent-gated ``FuelGaugeInterop``.
"""

from __future__ import annotations

import time

from .smbus import SMbusBus, SMbusError

# ---------------------------------------------------------------------------
# Part A - protocol framing (pure functions, testable everywhere)
# ---------------------------------------------------------------------------

REPORT_LEN = 64  # every MCP2221 HID report payload is exactly 64 bytes

MCP2221_VID = 0x04D8
MCP2221_PID = 0x00DD

# Command codes (out report byte 0; echo at in report byte 0).
CMD_I2C_WRITE = 0x90  # write, ending with STOP
CMD_I2C_WRDATA_RSTART = 0x92  # write, begins with a repeated-START
CMD_I2C_WRDATA_NOSTOP = 0x94  # write, holding the bus (no STOP)
CMD_I2C_READ = 0x91  # read, ending with STOP
CMD_I2C_READ_RSTART = 0x93  # read, begins with a repeated-START
CMD_I2C_GET_DATA = 0x40  # fetch engine-side read-back bytes
CMD_I2C_PARAM_OR_STATUS = 0x10  # status query / set speed / cancel
SUB_CANCEL = 0x10  # PARAM subordinate (byte 2): cancel operation
SUB_SET_SPEED = 0x20  # PARAM subordinate (byte 3): byte 4 = divider

# Response / engine-state codes (in report byte 1 status, byte 2 state).
S_OK = 0x00
S_BUSY = 0x01
S_WRADDRL_SEND = 0x21
S_WRADDRL_TOUT = 0x23
S_ADDR_NACK = 0x25
S_START_TOUT = 0x12
S_RSTART_TOUT = 0x17
S_WRDATA_TOUT = 0x44
S_RDDATA_TOUT = 0x52
S_READ_PARTIAL = 0x54
S_READ_COMPL = 0x55
S_STOP_TOUT = 0x62
S_WRITING_NOSTOP = 0x45  # state after a 0x94 write still holding the bus
S_READ_ERR = 0x7F  # GET_DATA byte-3 value on read failure
MASK_ADDR_NACK = 0x40  # bit 6 of byte 20 of a STATUS report

_MAX_I2C_DATA = 60  # user bytes per HID report


def pad64(payload: bytes) -> bytes:
    out = bytearray(REPORT_LEN)
    out[: len(payload)] = payload
    return bytes(out)


def scl_divider(clock_hz: int = 100_000) -> int:
    """Clock divider for the SET SPEED sub-command: 12 MHz / f - 3.

    100 kHz (the SMBus standard clock) -> 117 (0x75).
    """
    return 12_000_000 // clock_hz - 3


def frame_write(cmd: int, addr: int, data: bytes) -> bytes:
    """I2C write: [cmd][len16 LE][addr<<1][data...] (datasheet 3.1.5-3.1.7)."""
    if not 0 < len(data) <= _MAX_I2C_DATA:
        raise SMbusError("MCP2221A: 1..60 write bytes per chunk")
    return (
        bytes([cmd, len(data) & 0xFF, (len(data) >> 8) & 0xFF, (addr << 1) & 0xFF])
        + data
    )


def frame_read(cmd: int, addr: int, length: int) -> bytes:
    """I2C read: [cmd][len16 LE][(addr<<1)|1] (datasheet 3.1.8/3.1.9)."""
    if not 0 < length <= _MAX_I2C_DATA:
        raise SMbusError("MCP2221A: 1..60 read bytes per chunk")
    return bytes([cmd, length & 0xFF, (length >> 8) & 0xFF, ((addr << 1) | 1) & 0xFF])


def frame_status() -> bytes:
    """Bare PARAM/OR STATUS command = I2C engine status query."""
    return bytes([CMD_I2C_PARAM_OR_STATUS])


def frame_cancel() -> bytes:
    """Cancel the current I2C operation and release the bus."""
    return bytes([CMD_I2C_PARAM_OR_STATUS, 0x00, SUB_CANCEL])


def frame_set_speed(divider: int) -> bytes:
    """Set the I2C engine clock divider."""
    return bytes(
        [
            CMD_I2C_PARAM_OR_STATUS,
            0x00,
            0x00,
            SUB_SET_SPEED,
            divider & 0xFF,
        ]
    )


# STATUS report layout indexes (datasheet STATUS/SET parameters response).
STATE_BYTE = 8  # current engine state
NACK_BYTE = 20  # per-transfer NACK mask bits
WADDRESS_BYTE = 4  # configured W address (diagnostics)


def parse_status(resp: bytes) -> tuple[int, bool]:
    """(engine-state, addr-nacked) from a STATUS response packet."""
    state = resp[STATE_BYTE] if len(resp) > STATE_BYTE else 0
    nack = len(resp) > NACK_BYTE and bool(resp[NACK_BYTE] & MASK_ADDR_NACK)
    return state, nack


def is_busy(state: int) -> bool:
    """True while the engine is mid-transaction (byte-1 busy or state busy)."""
    return state in (S_BUSY, S_WRADDRL_SEND)


def is_recoverable(state: int) -> bool:
    """Transients we may simply retry."""
    return state in (S_BUSY, S_WRADDRL_SEND, S_WRITING_NOSTOP)


def is_timeout(state: int) -> bool:
    """Unrecoverable engine timeouts (bus/hardware trouble)."""
    return state in (
        S_START_TOUT,
        S_RSTART_TOUT,
        S_WRADDRL_TOUT,
        S_WRDATA_TOUT,
        S_RDDATA_TOUT,
        S_STOP_TOUT,
    )


# ---------------------------------------------------------------------------
# Part B - HID transport link (Windows; reuses cp2112's HidApi wrapper)
# ---------------------------------------------------------------------------


class McpLink:
    """Owns the open HID device and performs ordered 64-byte transfers."""

    def __init__(self, device=None, path: str | None = None):
        self._api, self._handle, self._dev = self._open(device, path)
        self._owned = device is None and self._dev is not None

    @staticmethod
    def _diag(message: str) -> None:
        try:
            from ..core.diaglog import log

            log(message)
        except Exception:  # noqa: BLE001 - diagnostics must never raise
            pass

    @staticmethod
    def _open(device=None, path: str | None = None):
        if device is not None:
            return device._api, device._handle, device
        try:
            from .cp2112 import make_hid_api

            api = make_hid_api()
        except Exception as e:
            raise SMbusError(str(e)) from e
        seen = api.enumerate_hid_paths()
        matches = [
            (p, v, i) for (p, v, i) in seen if (v, i) == (MCP2221_VID, MCP2221_PID)
        ]
        if path:
            matches = [(p, v, i) for (p, v, i) in matches if p == path]
        if not matches:
            raise SMbusError(
                f"no MCP2221A adapter found "
                f"(VID {MCP2221_VID:04X} PID {MCP2221_PID:04X})"
            )
        for p, v, i in matches:
            try:
                # No report IDs: Windows HID input/output reports are the
                # 64 data bytes preceded by a report-id byte -> 65 total.
                dev = api.open_device(p, v, i)
                dev.in_len = 65
                return api, dev._handle, dev
            except Exception as exc:  # noqa: BLE001 - try next interface
                McpLink._diag(f"mcp2221: open failed for {p}: {exc}")
        raise SMbusError("MCP2221A present but could not be opened (in use?)")

    # -- transfer primitives --

    def write_report(self, payload: bytes) -> None:
        import platform as _p

        if _p.system() == "Linux":
            # hidraw: the MCP2221 descriptor uses no report IDs, so the
            # raw payload goes straight onto the wire (no id slot).
            self._api.write_file(self._handle, pad64(payload))
            return
        buffer = bytes([0x00]) + pad64(payload)
        try:
            self._api.write_file(self._handle, buffer)
            return
        except Exception:  # noqa: BLE001 - some stacks size reports at 64
            self._diag("mcp2221: 65-byte write failed, retrying at 64 bytes")
        self._api.write_file(self._handle, pad64(payload))

    def read_report(self) -> bytes:
        return self._api.read_file(self._handle, 65, 2000)

    def transfer(self, payload: bytes) -> bytes:
        """Send one command report and return the (id-stripped) response."""

        for _ in (0, 1):
            self.write_report(payload)
            raw = self.read_report()
            resp = raw[1:] if raw and raw[0] == 0x00 and len(raw) > 64 else raw
            if resp and resp[0] == payload[0]:
                return resp
            self._diag(
                f"mcp2221: unexpected response for 0x{payload[0]:02X} "
                f"(got {raw[:4].hex() if raw else '<empty>'}) - resyncing"
            )
        raise SMbusError("MCP2221A: HID session out of sync - replug the adapter")

    def close(self) -> None:
        if self._owned and self._dev is not None:
            try:
                self._dev.close()
            except Exception:  # noqa: BLE001
                pass
            self._owned = False
            self._dev = None


# ---------------------------------------------------------------------------
# Part C - SMbusBus adapter
# ---------------------------------------------------------------------------


class Mcp2221Smbus(SMbusBus):
    """Drive an MCP2221A to perform SMBus word/block transfers.

    Implements ``SMbusBus`` so the existing consent-gated
    ``FuelGaugeInterop`` works unchanged against real hardware.
    """

    def __init__(
        self,
        link: McpLink | None = None,
        device=None,
        clock_hz: int = 100_000,
    ):
        self._link = link if link is not None else McpLink(device=device)
        self.state = 0
        self._configure(clock_hz)
        self._ensure_idle()

    # -- diagnostics --

    @staticmethod
    def _diag(message: str) -> None:
        try:
            from ..core.diaglog import log

            log(message)
        except Exception:  # noqa: BLE001 - diagnostics must never raise
            pass

    # -- engine plumbing --

    def _configure(self, clock_hz: int) -> None:
        resp = self._link.transfer(frame_set_speed(scl_divider(clock_hz)))
        if resp[1] != S_OK:
            self._diag(f"mcp2221: set-speed status 0x{resp[1]:02X} (continuing)")

    def _cancel(self) -> None:
        resp = self._link.transfer(frame_cancel())
        state = resp[2] if len(resp) > 2 else 0
        if resp[1] == S_OK and state == SUB_CANCEL:
            time.sleep(0.001)  # bus release needs a few hundred microseconds

    def _ensure_idle(self) -> None:
        """Recover a stuck engine before starting a new transaction."""
        try:
            resp = self._link.transfer(frame_status())
        except SMbusError:
            return
        self.state, nack = parse_status(resp)
        if nack or not (self.state == S_OK or is_recoverable(self.state)):
            self._cancel()

    def _raise(self, state: int, addr: int) -> None:
        # Only the explicit 0x25 status (or the STATUS-report NACK mask at
        # byte 20) means the slave didn't ACK - other bit-6 states (0x45,
        # 0x54, 0x55) are ordinary engine states and must not read as NACK.
        if state == S_ADDR_NACK:
            raise SMbusError(f"no device at 0x{addr:02X}", status=S_ADDR_NACK)
        raise SMbusError(
            f"MCP2221A I2C failure on 0x{addr:02X} (state 0x{state:02X})",
            status=state,
        )

    def _write_chunk(self, cmd: int, addr: int, data: bytes) -> None:
        for _ in range(3):
            resp = self._link.transfer(frame_write(cmd, addr, data))
            state = resp[2] if len(resp) > 2 else 0
            self.state = state
            if state == S_ADDR_NACK:
                self._raise(S_ADDR_NACK, addr)
            if is_timeout(state):
                self._raise(state, addr)
            if resp[1] == S_OK or (is_recoverable(state) and resp[1] != S_OK):
                break
            time.sleep(0.001)
        else:
            self._cancel()
            self._raise(state, addr)
        # Poll STATUS until the engine reports done (or NACK).
        for _ in range(50):
            resp = self._link.transfer(frame_status())
            self.state, nack = parse_status(resp)
            if nack:
                self._raise(S_ADDR_NACK, addr)
            if self.state == S_OK or (
                cmd == CMD_I2C_WRDATA_NOSTOP and self.state == S_WRITING_NOSTOP
            ):
                return
            if is_timeout(self.state):
                self._raise(self.state, addr)
            time.sleep(0.001)
        self._cancel()
        raise SMbusError("MCP2221A: write completion timed out")

    def _read_chunk(self, cmd: int, addr: int, length: int) -> bytes:
        resp = self._link.transfer(frame_read(cmd, addr, length))
        state = resp[2] if len(resp) > 2 else 0
        self.state = state
        if resp[1] != S_OK:
            self._raise(state, addr)
        out = bytearray()
        remaining = length
        # GET_DATA rounds: each returns up to 60 bytes; repeat until done.
        for _ in range(10 * length + 20):
            resp = self._link.transfer(bytes([CMD_I2C_GET_DATA]))
            s1 = resp[1] if len(resp) > 1 else 0
            s2 = resp[2] if len(resp) > 2 else 0
            count = resp[3] if len(resp) > 3 else 0
            self.state = s2
            if s1 == S_BUSY:
                time.sleep(0.001)
                continue
            if s1 != S_OK:
                self._raise(s1, addr)
            if s2 == S_ADDR_NACK:
                self._raise(S_ADDR_NACK, addr)
            if count == S_READ_ERR:
                time.sleep(0.001)
                continue
            chunk = (
                resp[4 : 4 + min(count, remaining)]
                if 0 < count <= _MAX_I2C_DATA
                else b""
            )
            out += chunk
            remaining -= len(chunk)
            if remaining <= 0 or s2 in (S_READ_COMPL, S_READ_PARTIAL):
                break
            if not chunk:
                time.sleep(0.001)
        if remaining > 0:
            raise SMbusError(
                f"MCP2221A: short read from 0x{addr:02X} ({len(out)}/{length} bytes)"
            )
        return bytes(out)

    # -- SMbusBus interface --

    def read_word(self, addr: int, reg: int) -> int:
        # SMBus read word: write the register pointer with no STOP, then
        # read 2 bytes with a repeated-START (datasheet 3.1.7 + 3.1.9).
        self._write_chunk(CMD_I2C_WRDATA_NOSTOP, addr, bytes([reg & 0xFF]))
        data = self._read_chunk(CMD_I2C_READ_RSTART, addr, 2)
        return data[0] | (data[1] << 8)

    def write_word(self, addr: int, reg: int, value: int) -> None:
        payload = bytes([reg & 0xFF, value & 0xFF, (value >> 8) & 0xFF])
        self._write_chunk(CMD_I2C_WRITE, addr, payload)

    def read_byte(self, addr: int, reg: int) -> int:
        self._write_chunk(CMD_I2C_WRDATA_NOSTOP, addr, bytes([reg & 0xFF]))
        return self._read_chunk(CMD_I2C_READ_RSTART, addr, 1)[0]

    def read_block(self, addr: int, reg: int, length: int) -> bytes:
        # Raw SMBus BLOCK payload (leading byte-count byte, matching the
        # CP2112 driver's convention used by FuelGaugeInterop._read_string).
        self._write_chunk(CMD_I2C_WRDATA_NOSTOP, addr, bytes([reg & 0xFF]))
        return self._read_chunk(CMD_I2C_READ_RSTART, addr, length)

    def write_block(self, addr: int, reg: int, data: bytes) -> None:
        payload = bytes([reg & 0xFF]) + bytes(data[: _MAX_I2C_DATA - 1])
        self._write_chunk(CMD_I2C_WRITE, addr, payload)

    def close(self) -> None:
        self._link.close()


# -- enumeration helper --


def list_mcp_adapters() -> list[str]:
    """HID device paths of connected MCP2221A adapters (Windows/Linux/macOS)."""
    try:
        from .cp2112 import make_hid_api

        api = make_hid_api()
    except Exception:  # noqa: BLE001
        return []
    return [
        p
        for (p, v, i) in api.enumerate_hid_paths()
        if v == MCP2221_VID and i == MCP2221_PID
    ]
