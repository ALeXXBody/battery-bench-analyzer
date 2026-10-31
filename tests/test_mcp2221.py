"""Tests for the MCP2221A driver (pure parts + a scripted link double)."""

import pytest

from benchanalyzer.unlock.mcp2221 import (
    CMD_I2C_GET_DATA,
    CMD_I2C_PARAM_OR_STATUS,
    CMD_I2C_READ_RSTART,
    CMD_I2C_WRDATA_NOSTOP,
    CMD_I2C_WRITE,
    S_ADDR_NACK,
    S_OK,
    S_READ_COMPL,
    STATE_BYTE,
    Mcp2221Smbus,
    frame_cancel,
    frame_read,
    frame_set_speed,
    frame_status,
    frame_write,
    parse_status,
    scl_divider,
)
from benchanalyzer.unlock.smbus import SMbusError

# -- Part A: framing purity -------------------------------------------------


def test_scl_divider_100k():
    assert scl_divider(100_000) == 117  # 12 MHz / 100k - 3


def test_frame_write_layout():
    f = frame_write(CMD_I2C_WRITE, 0x0B, bytes([0x30, 0x10, 0x02]))
    assert f[0] == 0x90
    assert f[1] == 3 and f[2] == 0  # length 3, little endian
    assert f[3] == 0x16  # 0x0B << 1 (write bit 0)
    assert f[4:7] == bytes([0x30, 0x10, 0x02])


def test_frame_read_layout_repeated_start():
    f = frame_read(CMD_I2C_READ_RSTART, 0x0B, 2)
    assert f[0] == 0x93
    assert f[1] == 2 and f[2] == 0
    assert f[3] == 0x17  # (0x0B << 1) | 1 = read bit


def test_frame_size_limits():
    with pytest.raises(SMbusError):
        frame_write(CMD_I2C_WRITE, 0x0B, b"\x00" * 61)
    with pytest.raises(SMbusError):
        frame_read(CMD_I2C_READ_RSTART, 0x0B, 61)


def test_frame_status_cancel_speed():
    assert frame_status()[0] == CMD_I2C_PARAM_OR_STATUS
    cancel = frame_cancel()
    assert cancel[2] == 0x10
    speed = frame_set_speed(117)
    assert speed[3] == 0x20 and speed[4] == 117


def test_parse_status_reads_indexes():
    resp = bytearray(64)
    resp[STATE_BYTE] = S_OK
    resp[20] = 0x40  # NACK mask bit lives at byte 20
    state, nack = parse_status(bytes(resp))
    assert state == S_OK and nack is True
    resp[20] = 0x00
    state, nack = parse_status(bytes(resp))
    assert state == S_OK and nack is False


# -- Part C: transactions against a scripted link ---------------------------


class ScriptedLink:
    """Feeds prepared device responses back in transfer order."""

    def __init__(self, responses: list[bytes]):
        self.responses = list(responses)
        self.sent: list[bytes] = []

    def transfer(self, payload: bytes) -> bytes:
        self.sent.append(payload)
        return self.responses.pop(0)

    def close(self) -> None:
        pass


def _status_ok() -> bytes:
    # STATUS response: state byte 8 = 0x00 idle, NACK byte 20 = 0x00.
    return bytes([CMD_I2C_PARAM_OR_STATUS, S_OK]) + bytes(21)


def _cmd_ok(cmd: int) -> bytes:
    return bytes([cmd, S_OK, S_OK])


def _get_data(count: int, *payload: int) -> bytes:
    return bytes([CMD_I2C_GET_DATA, S_OK, S_READ_COMPL, count, *payload])


def _smbus_with(responses: list[bytes]) -> Mcp2221Smbus:
    return Mcp2221Smbus(link=ScriptedLink(responses))


def test_read_word_full_sequence():
    # init (set speed + idle status), then: pointer write (no STOP),
    # completion status, read (repeated START) + GET data round.
    responses = [
        _cmd_ok(CMD_I2C_PARAM_OR_STATUS),
        _status_ok(),
        _cmd_ok(CMD_I2C_WRDATA_NOSTOP),
        _status_ok(),
        _cmd_ok(CMD_I2C_READ_RSTART),
        _get_data(2, 0x0E, 0x0F),
    ]
    bus = _smbus_with(responses)
    assert bus.read_word(0x0B, 0x09) == 0x0F0E  # SMBus word little endian
    link = bus._link
    assert link.sent[2][:4] == bytes([CMD_I2C_WRDATA_NOSTOP, 1, 0, 0x16])
    assert link.sent[4][:4] == bytes([CMD_I2C_READ_RSTART, 2, 0, 0x17])


def test_write_word_sequence():
    responses = [
        _cmd_ok(CMD_I2C_PARAM_OR_STATUS),
        _status_ok(),
        _cmd_ok(CMD_I2C_WRITE),
        _status_ok(),
    ]
    bus = _smbus_with(responses)
    bus.write_word(0x0B, 0x00, 0x0057)
    link = bus._link
    assert link.sent[2][:4] == bytes([CMD_I2C_WRITE, 3, 0, 0x16])
    assert link.sent[2][4:7] == bytes([0x00, 0x57, 0x00])


def test_block_read_returns_raw_count_byte():
    # SBS BLOCK payloads keep the leading byte-count byte (the CP2112
    # driver passes it through too; FuelGaugeInterop._read_string relies on it).
    responses = [
        _cmd_ok(CMD_I2C_PARAM_OR_STATUS),
        _status_ok(),
        _cmd_ok(CMD_I2C_WRDATA_NOSTOP),
        _status_ok(),
        _cmd_ok(CMD_I2C_READ_RSTART),
        _get_data(4, 7, ord("A"), ord("B"), ord("C")),
    ]
    bus = _smbus_with(responses)
    raw = bus.read_block(0x0B, 0x20, 4)
    assert raw[0] == 7 and raw[1:] == b"ABC"


def test_get_data_busy_retries_then_completes():
    responses = [
        _cmd_ok(CMD_I2C_PARAM_OR_STATUS),
        _status_ok(),
        _cmd_ok(CMD_I2C_WRDATA_NOSTOP),
        _status_ok(),
        _cmd_ok(CMD_I2C_READ_RSTART),
        bytes([CMD_I2C_GET_DATA, 0x01, S_OK, 0]),  # engine busy fetching
        _get_data(2, 0x34, 0x12),
    ]
    bus = _smbus_with(responses)
    assert bus.read_word(0x0B, 0x09) == 0x1234


def test_status_nack_raises_no_device():
    # A silent (absent) slave NACKs the address: STATUS byte 20 bit 6.
    nack_status = bytes([CMD_I2C_PARAM_OR_STATUS, S_OK]) + bytes(18) + bytes([0x40])
    responses = [
        _cmd_ok(CMD_I2C_PARAM_OR_STATUS),
        _status_ok(),
        _cmd_ok(CMD_I2C_WRDATA_NOSTOP),
        nack_status,
    ]
    bus = _smbus_with(responses)
    with pytest.raises(SMbusError) as excinfo:
        bus.read_word(0x0B, 0x08)
    assert "no device" in str(excinfo.value)
    assert excinfo.value.status == S_ADDR_NACK


def test_command_nack_raises_no_device():
    # The download command itself can carry 0x25 as the engine state.
    responses = [
        _cmd_ok(CMD_I2C_PARAM_OR_STATUS),
        _status_ok(),
        bytes([CMD_I2C_WRDATA_NOSTOP, S_OK, S_ADDR_NACK]),
    ]
    bus = _smbus_with(responses)
    with pytest.raises(SMbusError) as excinfo:
        bus.read_word(0x0B, 0x08)
    assert "no device" in str(excinfo.value)
