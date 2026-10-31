"""Smart Battery Data (SBS) register access.

Implements the public Smart Battery Data Specification v1.1 register set
(SBS Implementers Forum) on top of any ``SMbusBus`` transport - which is how
VoltClinic talks to a battery on the bench: the pack's fuel gauge is wired to
a CP2112 USB-SMBus adapter and addressed at the SBS default ``0x0B``.

Register conventions (SBS v1.1):
- word registers, little-endian on the wire (handled by SMbusBus.read_word)
- capacities in mAh by default, or 10 mWh when BatteryMode.CAPACITY_MODE=1
- temperature in 0.1 K
- current in mA, signed (positive = charging, negative = discharging)
- strings as SMBus block reads whose first byte is the length
"""

from __future__ import annotations

from ..unlock.smbus import SMbusBus, SMbusError
from .models import BatteryInfo, ChargeState, normalize_chemistry

# --- SBS register map (Smart Battery Data spec v1.1) -----------------------
SBS_MANUFACTURER_ACCESS = 0x00
SBS_REMAINING_CAPACITY_ALARM = 0x01
SBS_REMAINING_TIME_ALARM = 0x02
SBS_BATTERY_MODE = 0x03
SBS_AT_RATE = 0x04
SBS_AT_RATE_TIME_TO_FULL = 0x05
SBS_AT_RATE_TIME_TO_EMPTY = 0x06
SBS_AT_RATE_OK = 0x07
SBS_TEMPERATURE = 0x08
SBS_VOLTAGE = 0x09
SBS_CURRENT = 0x0A
SBS_RELATIVE_SOC = 0x0B
SBS_ABSOLUTE_SOC = 0x0C
SBS_REMAINING_CAPACITY = 0x0D
SBS_FULL_CHARGE_CAPACITY = 0x0E
SBS_RUN_TIME_TO_EMPTY = 0x0F
SBS_AVERAGE_TIME_TO_EMPTY = 0x10
SBS_AVERAGE_TIME_TO_FULL = 0x11
SBS_CHARGING_CURRENT = 0x12
SBS_CHARGING_VOLTAGE = 0x13
SBS_BATTERY_STATUS = 0x14
SBS_CYCLE_COUNT = 0x15
SBS_DESIGN_CAPACITY = 0x16
SBS_DESIGN_VOLTAGE = 0x17
SBS_SPECIFICATION_INFO = 0x18
SBS_MANUFACTURE_DATE = 0x19
SBS_SERIAL_NUMBER = 0x1A
SBS_MANUFACTURER_NAME = 0x20
SBS_DEVICE_NAME = 0x21
SBS_DEVICE_CHEMISTRY = 0x22
SBS_MANUFACTURER_DATA = 0x23

# BatteryMode bits
BATTERY_MODE_CAPACITY_MODE = 1 << 7  # 0 = mAh, 1 = 10 mWh
BATTERY_MODE_CHARGER_MODE = 1 << 13
BATTERY_MODE_ALARM_MODE = 1 << 15

# BatteryStatus bits (0x14)
BS_ERROR = 1 << 15
BS_FULLY_DISCHARGED = 1 << 14
BS_FULLY_CHARGED = 1 << 13
BS_DISCHARGING = 1 << 12
BS_INITIALIZATION = 1 << 11
BS_REMAINING_TIME_ALARM = 1 << 10
BS_REMAINING_CAPACITY_ALARM = 1 << 9
BS_TERMINATE_DISCHARGE = 1 << 8
BS_TERMINATE_CHARGE_ALARM = 1 << 7
BS_OVERTEMP = 1 << 6
BS_TERMINATE_CHARGE = 1 << 5
BS_OVER_CHARGED = 1 << 4
# bits 3..0 are manufacturer / chemistry specific

# Typical gauge addresses on laptop packs (SBS default first)
DEFAULT_GAUGE_ADDR = 0x0B
SCAN_ADDRESSES = (0x0B, 0x08, 0x09, 0x0A, 0x0C, 0x0D, 0x0E, 0x0F)

BLOCK_MAX = 32


def to_signed16(v: int) -> int:
    return v - 0x10000 if v > 0x7FFF else v


def raw_temp_to_celsius(raw: int) -> float:
    return raw / 10.0 - 273.15


def decode_status(status: int) -> list[str]:
    flags = []
    if status & BS_ERROR:
        flags.append("ERROR")
    if status & BS_FULLY_DISCHARGED:
        flags.append("FULLY_DISCHARGED")
    if status & BS_FULLY_CHARGED:
        flags.append("FULLY_CHARGED")
    if status & BS_DISCHARGING:
        flags.append("DISCHARGING")
    if status & BS_INITIALIZATION:
        flags.append("INITIALIZATION")
    if status & BS_REMAINING_TIME_ALARM:
        flags.append("TIME_ALARM")
    if status & BS_REMAINING_CAPACITY_ALARM:
        flags.append("CAPACITY_ALARM")
    if status & BS_TERMINATE_DISCHARGE:
        flags.append("TERMINATE_DISCHARGE")
    if status & BS_TERMINATE_CHARGE_ALARM:
        flags.append("TERMINATE_CHARGE_ALARM")
    if status & BS_OVERTEMP:
        flags.append("OVERTEMP")
    if status & BS_TERMINATE_CHARGE:
        flags.append("TERMINATE_CHARGE")
    if status & BS_OVER_CHARGED:
        flags.append("OVER_CHARGED")
    return flags


def charge_state_from(status: int, current_ma: int | None) -> ChargeState:
    if status & BS_DISCHARGING or (current_ma is not None and current_ma < 0):
        return ChargeState.DISCHARGING
    if current_ma is not None and current_ma > 0:
        return ChargeState.CHARGING
    if status & BS_FULLY_CHARGED:
        return ChargeState.FULL
    if status & BS_FULLY_DISCHARGED:
        return ChargeState.CRITICAL
    return ChargeState.UNKNOWN


def decode_manufacture_date(raw: int) -> str | None:
    """SBS ManufactureDate: bit0-4 day, 5-8 month, 9-15 year-1980."""
    if not raw or raw == 0xFFFF:
        return None
    day = raw & 0x1F
    month = (raw >> 5) & 0x0F
    year = 1980 + (raw >> 9)
    try:
        return f"{year:04d}-{month:02d}-{day:02d}"
    except ValueError:  # pragma: no cover
        return None


# --- full readout / register dump (bench info tabs) ------------------------

WORD_REGS = [
    (0x01, "RemainingCapacityAlarm", "mAh"),
    (0x02, "RemainingTimeAlarm", "min"),
    (0x03, "BatteryMode", ""),
    (0x04, "AtRate", ""),
    (0x05, "AtRateTimeToFull", "min"),
    (0x06, "AtRateTimeToEmpty", "min"),
    (0x07, "AtRateOK", ""),
    (0x08, "Temperature", "0.1K"),
    (0x09, "Voltage", "mV"),
    (0x0A, "Current", "mA"),
    (0x0B, "RelativeSOC", "%"),
    (0x0C, "AbsoluteSOC", "%"),
    (0x0D, "RemainingCapacity", "mAh*"),
    (0x0E, "FullChargeCapacity", "mAh*"),
    (0x0F, "RunTimeToEmpty", "min"),
    (0x10, "AverageTimeToEmpty", "min"),
    (0x11, "AverageTimeToFull", "min"),
    (0x12, "ChargingCurrent", "mA"),
    (0x13, "ChargingVoltage", "mV"),
    (0x14, "BatteryStatus", ""),
    (0x15, "CycleCount", ""),
    (0x16, "DesignCapacity", "mAh*"),
    (0x17, "DesignVoltage", "mV"),
    (0x18, "SpecificationInfo", ""),
    (0x19, "ManufactureDate", ""),
    (0x1A, "SerialNumber", ""),
]

STRING_REGS = [
    (0x20, "ManufacturerName"),
    (0x21, "DeviceName"),
    (0x22, "DeviceChemistry"),
]


def decode_spec_info(raw: int) -> str:
    """SpecificationInfo(): SBS spec version in the low byte (0x0011 → v1.1)."""
    major = (raw >> 4) & 0x0F
    minor = raw & 0x0F
    if major:
        return f"SBS spec v{major}.{minor}; raw=0x{raw:04X}"
    return f"raw=0x{raw:04X}"


def decode_serial(raw: int) -> str:
    return f"0x{raw:04X} ({raw:d})"


def decode_manufacture_date_word(raw: int) -> str:
    if not raw or raw == 0xFFFF:
        return "—"
    return decode_manufacture_date(raw) or f"raw=0x{raw:04X}"


def fmt_word_value(reg: int, raw: int) -> str:
    """Render a word register the way an operator expects to see it."""
    if raw == 0xFFFF:
        return "unavailable (0xFFFF)"
    if reg == 0x08:
        return f"{raw_temp_to_celsius(raw):.1f} °C (raw {raw})"
    if reg == 0x0A:  # signed current
        return f"{to_signed16(raw):+d} mA"
    if reg == 0x04:  # AtRate: signed current/capacity request
        return f"{to_signed16(raw):+d}"
    if reg == 0x09:
        return f"{raw} mV ({raw / 1000:.3f} V)"
    if reg == 0x17:
        return f"{raw} mV ({raw / 1000:.2f} V)"
    if reg == 0x13:
        return f"{raw} mV charging voltage limit"
    if reg == 0x12:
        return f"{raw} mA charging current max"
    if reg == 0x01:
        return f"{raw} mAh alarm threshold"
    if reg == 0x02:
        return f"{raw} min alarm threshold"
    if reg in (0x0B, 0x0C):
        return f"{raw} %"
    if reg in (0x0D, 0x0E, 0x16):
        return f"{raw} mAh"
    if reg in (0x0F, 0x10, 0x11, 0x05, 0x06):  # minutes helpers
        return _fmt_minutes(raw)
    if reg == 0x07:  # AtRateOK is a plain boolean
        return "yes (OK)" if raw else "no (0)"
    if reg == 0x15:
        return f"{raw} cycles"
    if reg == 0x18:
        return decode_spec_info(raw)
    if reg == 0x19:
        return decode_manufacture_date_word(raw)
    if reg == 0x1A:
        return decode_serial(raw)
    if reg == 0x14:
        flags = ", ".join(decode_status(raw)).lower() or "idle"
        return f"{flags} (0x{raw:04X})"
    if reg == 0x03:
        return decode_battery_mode(raw)
    return f"{raw:d} (raw)"


def _fmt_minutes(raw: int) -> str:
    """SBS time values: 0xFFFF means the gauge cannot predict."""
    if raw == 0xFFFF:
        return "unavailable"
    if raw >= 60:
        return f"{raw} min ({raw // 60}h{raw % 60:02d})"
    return f"{raw} min"


def decode_battery_mode(raw: int) -> str:
    """BatteryMode(): the few defined SBS mode bits, decoded for operators."""
    bits = []
    if raw & 0x0001:
        bits.append("internal_charge_controller")
    if raw & 0x0002:
        bits.append("primary_battery_support")
    if raw & 0x0040:
        bits.append("condition_cycle_requested")
    if raw & 0x0080:
        bits.append("condition_flag")
    if raw & 0x0400:
        bits.append("charge_controller_enabled")
    if raw & 0x0800:
        bits.append("primary_battery")
    return f"0x{raw:04X} ({', '.join(bits).lower() or 'no mode bits set'})"


class SBSGauge:
    """Typed reads/writes of the SBS register set over an SMbusBus."""

    def __init__(self, bus: SMbusBus, addr: int = DEFAULT_GAUGE_ADDR):
        self.bus = bus
        self.addr = addr

    # --- low-level helpers ---

    def read_word(self, reg: int) -> int:
        return self.bus.read_word(self.addr, reg)

    def read_word_maybe(self, reg: int) -> int | None:
        try:
            return self.read_word(reg)
        except SMbusError:
            return None

    def read_block_maybe(self, reg: int, length: int = BLOCK_MAX) -> bytes | None:
        try:
            return self.bus.read_block(self.addr, reg, length)
        except SMbusError:
            return None

    def write_word(self, reg: int, value: int) -> None:
        self.bus.write_word(self.addr, reg, value)

    # --- typed register accessors ---

    def temperature_c(self) -> float | None:
        raw = self.read_word_maybe(SBS_TEMPERATURE)
        if raw is None or raw in (0, 0xFFFF):
            return None
        return raw_temp_to_celsius(raw)

    def voltage_mv(self) -> float | None:
        raw = self.read_word_maybe(SBS_VOLTAGE)
        return float(raw) if raw is not None and raw != 0xFFFF else None

    def current_ma(self) -> int | None:
        raw = self.read_word_maybe(SBS_CURRENT)
        return to_signed16(raw) if raw is not None and raw != 0xFFFF else None

    def relative_soc(self) -> float | None:
        raw = self.read_word_maybe(SBS_RELATIVE_SOC)
        return float(raw) if raw is not None and raw <= 100 else None

    def absolute_soc(self) -> float | None:
        raw = self.read_word_maybe(SBS_ABSOLUTE_SOC)
        return float(raw) if raw is not None and raw <= 100 else None

    def battery_status(self) -> int | None:
        return self.read_word_maybe(SBS_BATTERY_STATUS)

    def status_flags(self) -> list[str]:
        st = self.battery_status()
        return decode_status(st) if st is not None else []

    def battery_mode(self) -> int | None:
        return self.read_word_maybe(SBS_BATTERY_MODE)

    def capacity_mode_is_mwh(self) -> bool:
        mode = self.battery_mode()
        return bool(mode is not None and mode & BATTERY_MODE_CAPACITY_MODE)

    def design_voltage_mv(self) -> float | None:
        raw = self.read_word_maybe(SBS_DESIGN_VOLTAGE)
        return float(raw) if raw is not None and raw != 0xFFFF else None

    def _cap_raw(self, reg: int) -> int | None:
        raw = self.read_word_maybe(reg)
        if raw is None or raw in (0, 0xFFFF):
            return None
        return raw

    def design_capacity(self) -> int | None:
        """DesignCapacity in native units (mAh, or 10 mWh in CAPACITY_MODE)."""
        return self._cap_raw(SBS_DESIGN_CAPACITY)

    def full_charge_capacity(self) -> int | None:
        return self._cap_raw(SBS_FULL_CHARGE_CAPACITY)

    def remaining_capacity(self) -> int | None:
        return self._cap_raw(SBS_REMAINING_CAPACITY)

    def cycle_count(self) -> int | None:
        raw = self.read_word_maybe(SBS_CYCLE_COUNT)
        return raw if raw is not None and raw != 0xFFFF else None

    def serial_number(self) -> str | None:
        raw = self.read_word_maybe(SBS_SERIAL_NUMBER)
        return f"0x{raw:04X}" if raw is not None and raw != 0xFFFF else None

    def manufacture_date(self) -> str | None:
        raw = self.read_word_maybe(SBS_MANUFACTURE_DATE)
        return decode_manufacture_date(raw) if raw is not None else None

    # --- strings (block reads: byte0 = length) ---

    def _read_string(self, reg: int) -> str | None:
        raw = self.read_block_maybe(reg)
        if not raw:
            return None
        n = raw[0]
        if n == 0 or n == 0xFF:
            return None
        return (
            bytes(raw[1 : 1 + n]).decode("ascii", "replace").strip("\x00").strip()
            or None
        )

    def manufacturer_name(self) -> str | None:
        return self._read_string(SBS_MANUFACTURER_NAME)

    def device_name(self) -> str | None:
        return self._read_string(SBS_DEVICE_NAME)

    def device_chemistry(self) -> str | None:
        return self._read_string(SBS_DEVICE_CHEMISTRY)

    def manufacturer_data(self) -> bytes | None:
        raw = self.read_block_maybe(SBS_MANUFACTURER_DATA)
        if not raw:
            return None
        n = raw[0]
        return bytes(raw[1 : 1 + n]) or None

    # --- capacity unit conversion -------------------------------------

    def _to_mwh(self, cap_native: int) -> float:
        if self.capacity_mode_is_mwh():
            return float(cap_native) * 10.0
        nominal = self.design_voltage_mv() or 11100.0  # typical 3S Li-ion
        return cap_native * nominal / 1000.0

    # --- aggregate snapshot -------------------------------------------

    def is_present(self) -> bool:
        """Cheap probe: a real SBS gauge answers a Voltage or Status read."""
        return (
            self.read_word_maybe(SBS_VOLTAGE) is not None
            or self.read_word_maybe(SBS_BATTERY_STATUS) is not None
        )

    def snapshot(self) -> BatteryInfo:
        """One full read pass -> BatteryInfo (raises SMbusError if absent)."""
        if not self.is_present():
            raise SMbusError(f"no SBS gauge answered at 0x{self.addr:02X}")

        info = BatteryInfo(device_index=0)
        info.name = self.device_name() or f"Gauge 0x{self.addr:02X}"
        info.manufacturer = self.manufacturer_name() or "Unknown"
        info.serial_number = self.serial_number() or "Unknown"
        chem = self.device_chemistry()
        info.chemistry = normalize_chemistry(chem)
        if chem:
            info.raw["chemistry_raw"] = chem

        info.voltage_mv = self.voltage_mv()
        cur = self.current_ma()
        info.current_ma = float(cur) if cur is not None else None
        info.temperature_c = self.temperature_c()
        info.cycle_count = self.cycle_count()
        info.relative_soc = self.relative_soc()
        info.absolute_soc = self.absolute_soc()
        status = self.battery_status()
        if status is not None:
            info.raw["battery_status"] = status
            info.raw["status_flags"] = " ".join(decode_status(status)) or "none"
        info.charge_state = charge_state_from(status or 0, cur)

        design = self.design_capacity()
        full = self.full_charge_capacity()
        remaining = self.remaining_capacity()
        if design:
            info.design_capacity_mwh = self._to_mwh(design)
            info.raw["design_capacity_mah"] = design
        if full:
            info.full_capacity_mwh = self._to_mwh(full)
            info.raw["full_charge_capacity_mah"] = full
        if remaining:
            info.current_mwh = self._to_mwh(remaining)
            info.raw["remaining_capacity_mah"] = remaining

        info.design_voltage_mv = self.design_voltage_mv()
        info.manufacture_date = self.manufacture_date()
        info.raw["capacity_mode_mwh"] = self.capacity_mode_is_mwh()

        if info.voltage_mv is not None and cur:
            watts = abs(cur) * info.voltage_mv / 1e6
            if cur > 0:
                info.charge_rate_mw = watts * 1000.0
            else:
                info.discharge_rate_mw = watts * 1000.0

        return info

    # --- read-only full dump (info tabs) -------------------------------

    def full_readout(self) -> list:
        """One pass over every SBS word register -> [(name, raw, unit, text)]."""
        out = []
        for reg, name, unit in WORD_REGS:
            raw = self.read_word_maybe(reg)
            text = fmt_word_value(reg, raw) if raw is not None else "—"
            out.append((name, raw, unit, text))
        return out

    def register_dump(self) -> list:
        """Full read-only dump: words + string/data blocks.

        Non-destructive: ManufacturerAccess (0x00) is NOT touched here —
        its access is consent-gated elsewhere.
        """
        rows = []
        for reg, name, unit in WORD_REGS:
            raw = self.read_word_maybe(reg)
            text = fmt_word_value(reg, raw) if raw is not None else "—"
            rows.append((f"0x{reg:02X}", name, "word", unit, text))
        for reg, name in STRING_REGS:
            s = self._read_string(reg)
            rows.append((f"0x{reg:02X}", name, "block", "", s or "—"))
        raw23 = self.manufacturer_data()
        rows.append(
            ("0x23", "ManufacturerData", "block", "", raw23.hex(" ") if raw23 else "—")
        )
        # ManufacturerInfo() is a plain pass-through block read on SBS
        # gauges; publishes firmware / IC details on most gauge families.
        raw70 = self.read_block_maybe(0x70)
        rows.append(
            ("0x70", "ManufacturerInfo", "block", "", raw70.hex(" ") if raw70 else "—")
        )
        return rows

    # --- guarded write path (SBS ManufacturerAccess) -------------------

    def manufacturer_command(self, word: int) -> None:
        """Write ManufacturerAccess. Callers MUST be consent-gated."""
        self.write_word(SBS_MANUFACTURER_ACCESS, word)


def scan_for_gauge(bus: SMbusBus, addresses=SCAN_ADDRESSES) -> int | None:
    """Return the first address that answers like an SBS gauge, else None."""
    for addr in addresses:
        g = SBSGauge(bus, addr)
        if g.is_present():
            return addr
    return None


def scan_for_gauge_detailed(bus: SMbusBus, addresses=SCAN_ADDRESSES) -> list:
    """Probe every address, returning [(addr, status_code), ...] failures.

    The batch scan toggles the bus clocks on every address, which doubles as
    a wake-up sequence for sleeping SBS gauges (e.g. a bq40z-series in
    SMBus-sleep). Callers combine this with scan_for_gauge to probe first,
    then only report details if nothing answered.
    """
    findings = []
    for addr in addresses:
        try:
            bus.read_word(addr, SBS_VOLTAGE)
        except SMbusError as exc:
            findings.append((addr, exc.status if isinstance(exc.status, int) else -1))
    return findings


def describe_scan(findings: list) -> str:
    """Human-readable hint table from a scan_for_gauge_detailed result."""
    codes = {c for _, c in findings}
    lines = []
    if codes <= {0x06} or codes == {-1}:
        lines.append(
            "every address NAKed: the gauge did not answer the "
            "SMBus code — most likely causes, in order:"
        )
        lines.append(
            "1) the pack is unpowered (cells below ~2.5 V/cell "
            "means the gauge is shut down — charge the pack briefly)"
        )
        lines.append(
            "2) the gauge has fallen asleep and woke but no host "
            "answered (the scan just toggled clock; scan again)"
        )
        lines.append(
            "3) missing pull-ups on SDA/SCL (the CP2112 board has "
            "protoboarded pull-ups; bare chips need external 10k)"
        )
    elif 0x07 in codes or 0x04 in codes:
        lines.append(
            "the adapter saw bus-level errors (CRC/bus): typical "
            "of swapped SDA/SCL, a broken pull-up resistor, or "
            "two devices driving the bus. Unpower everything and "
            "re-wire; never connect SDA/SCL to a voltage source."
        )
    elif 0x05 in codes:
        lines.append(
            "ACK error on targeted data: the slave is not at that "
            "address; then the gate was stuck partially."
        )
    else:
        lines.append(
            "mixed adapter statuses; retry the scan, and if it "
            "persists share these codes with support."
        )
    detail = ", ".join(
        f"0x{a:02X}:{c:02X}" if c >= 0 else f"0x{a:02X}:?" for a, c in findings
    )
    lines.append(f"detail: {detail}")
    return "\n".join(lines)
