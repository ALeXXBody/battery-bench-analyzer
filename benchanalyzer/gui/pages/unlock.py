"""Repair / Unlock page: SMBus gauge interop with guarded writes.

Uses the gauge session opened on the Bench Analyzer page. Read-only probe is
always allowed; any gauge write requires explicit, documented consent.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.bench import SessionHolder
from ...core.sbs import (
    SBS_BATTERY_STATUS,
    SBS_REMAINING_CAPACITY_ALARM,
    SBS_REMAINING_TIME_ALARM,
    decode_status,
)
from ...unlock.smbus import (
    ConsentRequired,
    FuelGaugeInterop,
    SMbusError,
)
from ..widgets import card


class UnlockPage(QWidget):
    def __init__(self, bench: SessionHolder):
        super().__init__()
        self.setObjectName("content")
        self.bench = bench
        self.gauge: FuelGaugeInterop | None = None
        self._writes_enabled = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        title = QLabel("Repair / Unlock (SMBus interop)")
        title.setObjectName("sectionHeader")
        lay.addWidget(title)

        notice = QLabel(
            "Battery Bench Analyzer provides READ-ONLY access to the bench battery's "
            "fuel gauge, plus an opt-in write interface. It deliberately does "
            "NOT bundle vendor 'unlock' routines, chip reflashing, or cycle "
            "wiping — those are counterfeit/unsafe on worn-out packs. "
            "See docs/UNLOCK.md."
        )
        notice.setWordWrap(True)
        lay.addWidget(notice)

        probe_card = card()
        d = QVBoxLayout(probe_card)
        d.addWidget(
            QLabel(
                "Probe the gauge opened on the Bench Analyzer page "
                "(read-only, uses the real SBS register set):"
            )
        )
        btn_row = QHBoxLayout()
        self.probe_btn = QPushButton("Probe Gauge")
        self.probe_btn.setObjectName("primary")
        self.probe_btn.clicked.connect(self._probe)
        btn_row.addWidget(self.probe_btn)
        btn_row.addStretch(1)
        d.addLayout(btn_row)
        lay.addWidget(probe_card)

        alarm_card = card()
        a = QVBoxLayout(alarm_card)
        a.addWidget(
            QLabel(
                "Alarms — SBS threshold registers 0x01/0x02 + the BatteryStatus "
                "flags (0x14). Clearing writes 0x0000 to both thresholds, the "
                "standard SBS host response that switches the ALARM flags off:"
            )
        )
        a.setSpacing(6)
        alarm_row = QHBoxLayout()
        self.alarm_read_btn = QPushButton("Read Alarms")
        self.alarm_read_btn.clicked.connect(self._read_alarms)
        self.alarm_clear_btn = QPushButton("Clear Alarms")
        self.alarm_clear_btn.setObjectName("primary")
        self.alarm_clear_btn.clicked.connect(self._clear_alarms)
        alarm_row.addWidget(self.alarm_read_btn)
        alarm_row.addWidget(self.alarm_clear_btn)
        alarm_row.addStretch(1)
        a.addLayout(alarm_row)
        lay.addWidget(alarm_card)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        lay.addWidget(self.output, 1)

        write_card = card()
        w = QVBoxLayout(write_card)
        w.addWidget(QLabel("Guarded write consent — required before any gauge write:"))
        self.consent = QCheckBox(
            "I understand that writing to a battery fuel gauge can permanently "
            "alter the pack, may be unsafe on failed cells, and could be "
            "unlawful if used to disguise a worn battery."
        )
        self.write_btn = QPushButton("Enable Write Access")
        self.write_btn.clicked.connect(self._enable_writes)
        w.addWidget(self.consent)
        w.addWidget(self.write_btn)
        lay.addWidget(write_card)

    def _interop(self) -> FuelGaugeInterop:
        session = self.bench.session
        if session is None:
            raise SMbusError(
                "No gauge connected — open one on the Bench Analyzer page "
                "first (demo gauge counts)."
            )
        return FuelGaugeInterop(session.bus, session.addr)

    def _probe(self) -> None:
        try:
            gauge = self._interop()
            info = gauge.probe()
        except SMbusError as exc:
            self.output.setPlainText(str(exc))
            return
        self.gauge = gauge
        lines = [
            f"Address:         0x{info.address:02X}",
            f"Manufacturer:    {info.manufacturer}",
            f"Device name:     {info.device_name}",
            f"Chemistry:       {info.chemistry}",
            f"Serial (hex):    0x{info.serial:04X}"
            if info.serial is not None
            else "Serial: unknown",
            f"Design capacity: {info.design_capacity_mah} mAh"
            if info.design_capacity_mah is not None
            else "Design cap: unknown",
            f"Full capacity:   {info.full_capacity_mah} mAh"
            if info.full_capacity_mah is not None
            else "Full cap: unknown",
            f"Cycle count:     {info.cycle_count}"
            if info.cycle_count is not None
            else "Cycles: unknown",
            f"Voltage:         {info.voltage_mv} mV"
            if info.voltage_mv is not None
            else "Voltage: unknown",
            f"Charge:          {info.relative_soc}%"
            if info.relative_soc is not None
            else "Charge: unknown",
            f"Temperature:     {info.temperature:.1f} °C"
            if info.temperature is not None
            else "Temperature: unknown",
        ]
        self.output.setPlainText("\n".join(lines))

    # -- alarm handling ---------------------------------------------------

    _ALARM_REGS = (
        SBS_REMAINING_CAPACITY_ALARM,
        SBS_REMAINING_TIME_ALARM,
    )

    def _session_word(self, reg: int):
        session = self.bench.session
        if session is None:
            raise SMbusError(
                "No gauge connected — open one on the Bench Analyzer page first."
            )
        return session.gauge.read_word_maybe(reg)

    _ALARM_MEANINGS = {
        "time_alarm": "runtime estimate dropped below the alarm point",
        "capacity_alarm": "remaining charge dropped below the alarm point",
        "terminate_discharge": "pack hit the low cutoff — near empty",
        "terminate_charge_alarm": "pack says STOP charging now",
        "overtemp_alarm": "temperature out of range",
        "terminate_charge": "last charge cycle ended (limit reached)",
        "over_charged": "pack reports it was over-charged",
        "error": "battery fault reported",
        "fully_discharged": "pack fully discharged",
        "fully_charged": "pack fully charged",
        "discharging": "pack is discharging right now",
        "initialization": "gauge still initializing",
    }

    @staticmethod
    def _explained_flags(status: int) -> tuple[str, str]:
        """Return (explained flag lines, residual vendor bits text)."""
        from ...core.sbs import (
            BS_DISCHARGING,
            BS_ERROR,
            BS_FULLY_CHARGED,
            BS_FULLY_DISCHARGED,
            BS_INITIALIZATION,
            BS_OVER_CHARGED,
            BS_OVERTEMP,
            BS_REMAINING_CAPACITY_ALARM,
            BS_REMAINING_TIME_ALARM,
            BS_TERMINATE_CHARGE,
            BS_TERMINATE_CHARGE_ALARM,
            BS_TERMINATE_DISCHARGE,
        )

        known = decode_status(status)
        lines = []
        for f in known:
            meaning = UnlockPage._ALARM_MEANINGS.get(f.lower(), "")
            lines.append(f"  {f.lower():<22} = {meaning}" if meaning else "")
        lines = [x for x in lines if x] or ["  no warning bits set"]
        all_bits = (
            BS_ERROR
            | BS_FULLY_DISCHARGED
            | BS_FULLY_CHARGED
            | BS_DISCHARGING
            | BS_INITIALIZATION
            | BS_REMAINING_TIME_ALARM
            | BS_REMAINING_CAPACITY_ALARM
            | BS_TERMINATE_DISCHARGE
            | BS_TERMINATE_CHARGE_ALARM
            | BS_OVERTEMP
            | BS_TERMINATE_CHARGE
            | BS_OVER_CHARGED
        )
        vendor = status & ~all_bits & 0xFFFF
        residual_text = f"vendor-specific bits 0x{vendor:04X}" if vendor else ""
        return "\n".join(lines), residual_text

    def _alarm_report(self, prefix: str = "") -> str:
        cap = self._session_word(SBS_REMAINING_CAPACITY_ALARM)
        time_ = self._session_word(SBS_REMAINING_TIME_ALARM)
        status = self._session_word(SBS_BATTERY_STATUS)
        lines = []
        if status is None:
            lines.append("Status (0x14): unavailable")
        else:
            explained, vendor_txt = self._explained_flags(status)
            lines.append(f"Status (0x14) = 0x{status:04X}:")
            lines.append(explained)
            if vendor_txt:
                lines.append(f"  plus {vendor_txt} (not defined by SBS)")
            lines.append("")
            if status & (0x0400 | 0x0200 | 0x0100):
                lines.append(
                    "Reads as: the pack is flagging an alarm state. Charge the "
                    "pack, let it settle, then click Read Alarms again."
                )
            lines.append(
                "Thresholds: capacity "
                f"{'unavailable' if cap is None else f'{cap} mAh'} · time "
                f"{'unavailable' if time_ is None else f'{time_} min'} "
                "(0 = gauge still signals alarm; see Clear Alarms)"
            )
        return "\n".join(lines)

    def _read_alarms(self) -> None:
        try:
            self.output.setPlainText(self._alarm_report("Alarms:"))
        except SMbusError as exc:
            self.output.setPlainText(f"Alarm read failed: {exc}")

    def _clear_alarms(self) -> None:
        if not self.consent.isChecked():
            QMessageBox.warning(
                self,
                "Consent required",
                "Clearing alarms writes to the gauge. Acknowledge the write "
                "consent statement below first.",
            )
            return
        interop = self._interop_no_probe()
        before = ""
        try:
            before = self._alarm_report("Alarms before:")
        except SMbusError as exc:
            self.output.setPlainText(f"Alarm read failed: {exc}")
            return
        cleared: list[str] = []
        for reg in self._ALARM_REGS:
            try:
                interop.write_word(reg, 0x0000)
                cleared.append(f"0x{reg:02X}=0x0000")
            except ConsentRequired as exc:
                self.output.setPlainText(
                    f"{before}\n\nConsent refused: {exc}\n"
                    "Click 'Enable Write Access' first."
                )
                return
            except SMbusError as exc:
                self.output.setPlainText(
                    f"{before}\n\nWrite to 0x{reg:02X} failed: {exc}"
                )
                return
        try:
            after = self._alarm_report("Alarms after:")
        except SMbusError as exc:
            after = f"(read-back failed: {exc})"
        self.output.setPlainText(
            f"{before}\n\nCleared: {', '.join(cleared)}\n\n{after}\n\n"
            "Note: if the ALARM flags persist, this gauge family clears "
            "them with a vendor-specific command — Battery Bench Analyzer does not "
            "ship vendor register maps on purpose."
        )

    def _interop_no_probe(self) -> FuelGaugeInterop:
        session = self.bench.session
        if session is None:
            raise SMbusError(
                "No gauge connected — open one on the Bench Analyzer page first."
            )
        gauge = FuelGaugeInterop(session.bus, session.addr)
        if self._writes_enabled:
            # Write access was explicitly granted this session; mirror it on
            # freshly built interop objects so guarded writes can proceed.
            gauge.grant_write_permission(True)
        return gauge

    def _enable_writes(self) -> None:
        if not self.consent.isChecked():
            QMessageBox.warning(
                self,
                "Consent required",
                "You must acknowledge the safety statement first.",
            )
            return
        if self.gauge is None:
            QMessageBox.warning(self, "No gauge", "Probe a gauge first.")
            return
        try:
            self.gauge.grant_write_permission(True)
            self._writes_enabled = True
        except ConsentRequired as exc:
            QMessageBox.warning(self, "Consent required", str(exc))
            return
        QMessageBox.information(
            self,
            "Write access enabled",
            "Write access enabled — Battery Bench Analyzer still ships no "
            "vendor register map — you decide what (if anything) to write.",
        )
