"""Unit tests for the CP2112 protocol framing (Part A) - pure, no Windows API.

These verify the exact byte layouts documented in Silicon Labs AN495.
"""
import pytest

from voltclinic.unlock.cp2112 import (
    RID_DATA_READ_RESPONSE,
    RID_SMBUS_CONFIG,
    RID_TRANSFER_STATUS_RESPONSE,
    RID_WRITE_READ_REQUEST,
    RID_DATA_WRITE_REQUEST,
    block_to_blob,
    bytes_to_word,
    parse_read_response,
    parse_transfer_status,
    report_smbus_config,
    report_transfer_status_request,
    report_write_read_request,
    report_write_request,
    word_to_bytes,
)


def test_smbus_config_default_layout():
    rep = report_smbus_config()  # 100 kHz default
    assert len(rep) == 64
    assert rep[0] == RID_SMBUS_CONFIG
    # clock speed big-endian at offset 1 -> 0x000186A0
    assert rep[1:5] == bytes.fromhex("000186A0")
    assert rep[5] == 0x02          # device address
    assert rep[6] == 0x01          # auto send read enabled
    # read timeout 1000ms = 0x03E8 big-endian at offset 9
    assert rep[9:11] == bytes.fromhex("03E8")


def test_write_read_request_word_read():
    rep = report_write_read_request(0x0B, 2, b"\x0F")
    assert rep[0] == RID_WRITE_READ_REQUEST
    assert rep[1] == 0x16           # slave 0x0B << 1
    assert rep[2:4] == bytes.fromhex("0002")  # length BE
    assert rep[4] == 1              # target address length
    assert rep[5] == 0x0F           # command byte
    assert len(rep) == 64


def test_write_request_data_write():
    rep = report_write_request(0x0B, bytes([0x1C, 0x34, 0x12]))
    assert rep[0] == RID_DATA_WRITE_REQUEST
    assert rep[1] == 0x16
    assert rep[2] == 3              # data length
    assert rep[3:6] == bytes([0x1C, 0x34, 0x12])


def test_transfer_status_request():
    rep = report_transfer_status_request()
    assert rep[0] == 0x15
    assert rep[1] == 0x01
    assert len(rep) == 64


def test_word_endianness():
    # SMBus words are little-endian on the wire
    assert word_to_bytes(0x1234) == bytes([0x34, 0x12])
    assert bytes_to_word(bytes([0x34, 0x12])) == 0x1234


def test_parse_transfer_status():
    rep = bytes([RID_TRANSFER_STATUS_RESPONSE, 0x02, 0x00]) + b"\x00" * 61
    assert parse_transfer_status(rep) == 0x02  # complete


def test_parse_read_response_and_block():
    rep = bytes([RID_DATA_READ_RESPONSE, 8, 7, 6, 5, 4, 3, 2, 1, 0]) + b"\x00" * 61
    assert parse_read_response(rep) == bytes([7, 6, 5, 4, 3, 2, 1, 0])
    # SMBus block read: drop the leading length byte
    assert block_to_blob(bytes([3, 0xAA, 0xBB, 0xCC])) == bytes([0xAA, 0xBB, 0xCC])
