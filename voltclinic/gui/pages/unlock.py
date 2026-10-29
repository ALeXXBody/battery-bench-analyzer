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

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        title = QLabel("Repair / Unlock (SMBus interop)")
        title.setObjectName("sectionHeader")
        lay.addWidget(title)

        notice = QLabel(
            "VoltClinic provides READ-ONLY access to the bench battery's "
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
        except ConsentRequired as exc:
            QMessageBox.warning(self, "Consent required", str(exc))
            return
        QMessageBox.information(
            self,
            "Write access enabled",
            "Write access enabled for this session. VoltClinic still ships no "
            "vendor register map — you decide what (if anything) to write.",
        )
