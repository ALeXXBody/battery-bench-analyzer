"""Dead-battery rescue procedures for bench-connected smart packs.

From public documentation only:
- a wake loop that toggles the SMBus clocks across all SBS addresses
  (wakes sleeping bq40z-series gauges),
- gated read-out of the gauge's status registers through TI's public
  ManufacturerAccess()/ManufacturerBlockAccess() interface
  (bq40z50 technical reference manual: 0x00 MAC, 0x44 MBA, 0x23 MD,
  subcommands 0x0051 SafetyStatus / 0x0052 PFAlert / 0x0053 PFStatus /
  0x0054 OperationStatus),

so a pack that arrives "not communicating" can be diagnosed: the rescue
flow first wakes the gauge, then reads which permanent-failure or
shutdown bit is set, instead of stopping at "no gauge answered".
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from ..unlock.smbus import SMbusBus, SMbusError
from .sbs import (SCAN_ADDRESSES, SBS_MANUFACTURER_ACCESS, describe_scan,
                  scan_for_gauge, scan_for_gauge_detailed)

# TI public (bq40z50 TRM, SBS commands):
MANUFACTURER_DATA = 0x23        # ManufacturerData(), block read
MAC_PFSTATUS = 0x0053           # 4-byte PF flag word pair
MAC_OPERATIONSTATUS = 0x0054
MAC_SAFETYSTATUS = 0x0051
MAC_PFALERT = 0x0052
MAC_DASTATUS1 = 0x0057          # DAStatus1: first entry is CellVoltage1..n + telemetry
MANUFACTURER_BLOCK_ACCESS = 0x44  # ManufacturerBlockAccess(), result block


def read_cell_voltages(bus: SMbusBus, addr: int, consent: str,
                       max_cells: int = 8) -> List[float]:
    """Read cell voltages via the public TI DAStatus1 subcommand.

    On the bq40z50 (public TRM) DAStatus1() places CellVoltage1..4 as the
    first little-endian words of the ManufacturerBlockAccess result. This
    is a write on the wire (ManufacturerAccess subcommand select), so it
    is consent-gated like every other manufacturer command.

    Returns the list of plausible Li-ion cell voltages in mV (scanning
    until a word leaves the 2500-4300 mV window or max_cells is reached).
    """
    if not consent:
        raise SMbusError(
            "cell voltage reads are gated: pass operator consent")
    bus.write_word(addr, SBS_MANUFACTURER_ACCESS, MAC_DASTATUS1)
    raw = bus.read_block(addr, MANUFACTURER_BLOCK_ACCESS, 32)
    cells: List[float] = []
    for i in range(0, min(max_cells * 2, len(raw) - 1), 2):
        v = raw[i] | (raw[i + 1] << 8)
        if not (2500 <= v <= 4300):
            break
        cells.append(float(v))
    return cells


# PFStatus() named flag bits (bits 15:0, public TRM listing; 31:16 shown raw).
_PFSTATUS_BITS = [
    "SUV", "SOV", "SOCC", "SOCD", "SOT", None, "SOTF", "QIM",
    "CB", "IMP", "CD", "VIMR", "VIMA", None, None, None,
]

_PF_WARNING = (
    "permanent-failure bits are set: further interaction with the pack "
    "must go through the explicit consent-gated write path.")


def wake_and_locate(bus: SMbusBus, seconds: float = 20.0,
                    addresses=SCAN_ADDRESSES
                    ) -> Tuple[Optional[int], List[Tuple[int, int]], float]:
    """Toggle the bus until a gauge answers (or the budget expires).

    A sleeping/dead-looking bq40z-series gauge wakes when it sees clock
    activity; every scan iteration produces exactly that.
    """
    t0 = time.time()
    findings: List[Tuple[int, int]] = []
    while time.time() - t0 < seconds:
        found = scan_for_gauge(bus, addresses)
        if found is not None:
            return found, findings, time.time() - t0
        findings = scan_for_gauge_detailed(bus, addresses)
        time.sleep(0.25)
    return None, findings, time.time() - t0


def read_mac_status(bus: SMbusBus, addr: int, mac_command: int,
                    consent: str, length: int = 8) -> bytes:
    """Read a ManufacturerAccess subcommand result.

    Requires explicit ``consent`` (a written operator approval kept
    non-empty by callers) because the sequence writes into
    ManufacturerAccess() on the wire.
    """
    if not consent:
        raise SMbusError(
            "ManufacturerAccess reads are gated: pass operator consent")
    bus.write_word(addr, SBS_MANUFACTURER_ACCESS, mac_command)
    return bus.read_block(addr, MANUFACTURER_DATA, length)


def decode_word_flags(raw: bytes, bits=None) -> str:
    """Render an 8-byte status pair: bits 15:0 named, 31:16 raw-hex."""
    if len(raw) < 2:
        return "empty"
    word_lo = raw[0] | (raw[1] << 8)
    flags = [name for i, name in enumerate(bits or _PFSTATUS_BITS)
             if name and (word_lo >> i) & 1]
    head = ", ".join(flags) if flags else "none of the named bits"
    return head + "; raw=" + raw.hex(" ")


def rescue(bus: SMbusBus, consent: str, wake_seconds: float = 15.0) -> dict:
    """Full dead-battery rescue: locate, wake, diagnose.

    Returns a dict with 'addr' (None when the gauge stays silent),
    'scan' findings, and labeled raw status reads when the gauge woke.
    """
    result: dict = {"addr": None, "wrote": consent}
    found, findings, elapsed = wake_and_locate(bus, seconds=wake_seconds)
    result["scan_detail"] = findings
    result["wake_seconds"] = elapsed
    if found is None:
        result["no_gauge_hint"] = describe_scan(findings)
        return result
    result["addr"] = found
    for cmd, label in ((MAC_SAFETYSTATUS, "SafetyStatus"),
                       (MAC_PFALERT, "PFAlert"),
                       (MAC_PFSTATUS, "PFStatus"),
                       (MAC_OPERATIONSTATUS, "OperationStatus")):
        try:
            raw = read_mac_status(bus, found, cmd, consent)
            result[label] = raw
            result[label + "_rendered"] = decode_word_flags(raw)
        except SMbusError as exc:
            result[label] = None
            result[label + "_rendered"] = f"unavailable ({exc})"
    if "PFStatus" in result and result["PFStatus"]:
        w = result["PFStatus"][0] | (result["PFStatus"][1] << 8)
        if w & 0x7FDF:      # any named PF bit active
            result["warning"] = _PF_WARNING
    return result
