"""Repair / Unlock page: SMBus gauge interop with guarded writes.

Read-only probe is always available (in demo mode). Any EEPROM write path
requires explicit consent and a real adapter driver — which is intentionally
not bundled.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...unlock.smbus import (
    ConsentRequired,
    FakeSMbusBus,
    FuelGaugeInterop,
    SB_CYCLE_COUNT,
    SB_DESIGN_CAPACITY,
    SB_DEVICE_NAME,
    SB_FULL_CHARGE_CAPACITY,
    SB_MANUFACTURER_NAME,
    SB_SERIAL_NUMBER,
    SMbusBus,
)
from ..widgets import card


class UnlockPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("content")
        self.gauge: FuelGaugeInterop | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        title = QLabel("Repair / Unlock (SMBus interop)")
        title.setObjectName("sectionHeader")
        lay.addWidget(title)

        notice = QLabel(
            "VoltClinic provides READ-ONLY access to a Smart Battery "
            "fuel gauge, plus an opt-in write interface. It deliberately does "
            "NOT bundle vendor 'unlock' routines, chip reflashing, or cycle "
            "wiping — those are counterfeit/unsafe on worn-out packs. "
            "See docs/UNLOCK.md."
        )
        notice.setWordWrap(True)
        lay.addWidget(notice)

        demo_card = card()
        d = QVBoxLayout(demo_card)
        demo_label = QLabel("Inspect a battery gauge (demo uses an in-memory fake):")
        d.addWidget(demo_label)

        sel_row = QHBoxLayout()
        self.adapter_box = QComboBox()
        self.adapter_box.addItem("Demo gauge (in-memory fake)")
        self.adapter_box.addItem("CP2112 USB-SMBus adapter")
        self.adapter_box.setEnabled(False)
        sel_row.addWidget(QLabel("Adapter:"))
        sel_row.addWidget(self.adapter_box, 1)
        btn_row = QHBoxLayout()
        self.probe_btn = QPushButton("Probe Gauge")
        self.probe_btn.setObjectName("primary")
        self.probe_btn.clicked.connect(self._probe)
        btn_row.addWidget(self.probe_btn)
        btn_row.addStretch(1)
        d.addLayout(sel_row)
        d.addLayout(btn_row)
        lay.addWidget(demo_card)

        self.detect_btn = QPushButton("Detect CP2112 adapter")
        self.detect_btn.clicked.connect(self._detect)
        lay.addWidget(self.detect_btn)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        lay.addWidget(self.output, 1)

        write_card = card()
        w = QVBoxLayout(write_card)
        w.addWidget(QLabel("Guarded write consent — required before any EEPROM write:"))
        self.consent = QCheckBox(
            "I understand that writing to a battery fuel gauge can permanently "
            "alter the pack, may be unsafe on failed cells, and could be "
            "unlawful if used to disguise a worn battery.")
        self.write_btn = QPushButton("Enable Write Access")
        self.write_btn.clicked.connect(self._enable_writes)
        w.addWidget(self.consent)
        w.addWidget(self.write_btn)
        lay.addWidget(write_card)

    def _detect(self) -> None:
        from ...unlock.cp2112 import Cp2112Smbus
        try:
            adapters = Cp2112Smbus.find_adapters()
        except Exception as exc:  # noqa: BLE001
            self.output.append(f"[CP2112 detect failed:] {exc}")
            return
        if adapters:
            self.adapter_box.setCurrentIndex(1)
            self.adapter_box.setEnabled(True)
            self.output.setPlainText(
                "CP2112 adapter detected:\n" +
                "\n".join(f"  {p}  (VID 0x{v:04X} / PID 0x{i:04X})"
                          for p, v, i in adapters) +
                "\n\nClick 'Probe Gauge' to read via the real adapter.")
        else:
            self.adapter_box.setCurrentIndex(0)
            self.output.setPlainText(
                "No CP2112 adapter found (VID 0x10C4 / PID 0xEA90).\n"
                "The demo gauge is selected; plug in a CP2112 and re-detect.")

    def _probe(self) -> None:
        bus: SMbusBus
        use_real = self.adapter_box.currentIndex() == 1
        suffix = "(demo data; wire your own SMbusBus driver for real hardware)"
        try:
            if use_real:
                from ...unlock.cp2112 import Cp2112Smbus
                bus = Cp2112Smbus()
                suffix = "(real CP2112 USB-SMBus adapter)"
            else:
                bus = FakeSMbusBus(regs={
                    SB_SERIAL_NUMBER: 0x1234,
                    SB_DESIGN_CAPACITY: 4200,
                    SB_FULL_CHARGE_CAPACITY: 3100,
                    SB_CYCLE_COUNT: 402,
                })
                bus.blocks[SB_MANUFACTURER_NAME] = b"\x07" + b"TEST-CO"
                bus.blocks[SB_DEVICE_NAME] = b"\x08" + b"DEMO-PAK"
        except Exception as exc:  # noqa: BLE001
            self.output.append(f"[init failed:] {exc}")
            return

        self.gauge = FuelGaugeInterop(bus)
        info = self.gauge.probe()
        lines = [
            f"Address:         0x{info.address:02X}",
            f"Manufacturer:    {info.manufacturer}",
            f"Device name:     {info.device_name}",
            f"Chemistry:       {info.chemistry}",
            f"Serial (hex):    0x{info.serial:04X}" if info.serial is not None else "Serial: unknown",
            f"Design capacity: {info.design_capacity_mah} mAh" if info.design_capacity_mah is not None else "Design: unknown",
            f"Full capacity:   {info.full_capacity_mah} mAh" if info.full_capacity_mah is not None else "Full: unknown",
            f"Cycle count:     {info.cycle_count}" if info.cycle_count is not None else "Cycles: unknown",
            "",
            suffix,
        ]
        self.output.setPlainText("\n".join(lines))

    def _enable_writes(self) -> None:
        if not self.consent.isChecked():
            QMessageBox.warning(self, "Consent required",
                                "You must acknowledge the safety statement first.")
            return
        if self.gauge is None:
            QMessageBox.warning(self, "No gauge",
                                "Probe a gauge first.")
            return
        try:
            self.gauge.grant_write_permission(True)
        except ConsentRequired as exc:
            QMessageBox.warning(self, "Consent required", str(exc))
            return
        QMessageBox.information(
            self, "Write access enabled",
            "Write access enabled for this session. In this demo there is still "
            "no register map, so no writes are actually performed.")
