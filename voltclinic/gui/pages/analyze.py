"""Bench Analyzer page: connect to an out-of-circuit battery's gauge.

The battery sits on the bench wired to a CP2112 USB-SMBus adapter (or the
built-in demo gauge). This page opens the session, shows the identity block
and live telemetry, and shares the session with the other pages.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.bench import GaugeSession, SessionHolder
from ...unlock.smbus import SMbusError
from ..widgets import card, stat_card, stat_row


def _fmt(v, unit, digits=0):
    return f"{v:.{digits}f} {unit}" if v is not None else "—"


class AnalyzePage(QWidget):
    def __init__(self, bench: SessionHolder):
        super().__init__()
        self.setObjectName("content")
        self.bench = bench

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("Bench Analyzer")
        title.setObjectName("sectionHeader")
        header.addWidget(title)
        header.addStretch(1)
        outer.addLayout(header)

        info = QLabel(
            "Battery out of the device, on the bench: wire the pack's SMBus "
            "(SCL, SDA, GND) to the CP2112 adapter and connect. The gauge is "
            "usually at address 0x0B.")
        info.setWordWrap(True)
        outer.addWidget(info)

        # --- connection row ---
        conn = card()
        row = QHBoxLayout(conn)
        self.adapter_box = QComboBox()
        self.adapter_box.addItem("Demo gauge (no hardware)")
        self.adapter_box.addItem("CP2112 USB-SMBus adapter")
        self.adapter_box.addItem("ROTOOL USB analyzer")
        self.addr_edit = QLineEdit("0x0B")
        self.addr_edit.setFixedWidth(70)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.clicked.connect(self._connect)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self._disconnect)
        self.scan_btn = QPushButton("Scan addresses")
        self.scan_btn.clicked.connect(self._scan)
        row.addWidget(QLabel("Adapter:"))
        row.addWidget(self.adapter_box)
        row.addWidget(QLabel("Address:"))
        row.addWidget(self.addr_edit)
        row.addWidget(self.connect_btn)
        row.addWidget(self.disconnect_btn)
        row.addWidget(self.scan_btn)
        row.addStretch(1)
        outer.addWidget(conn)

        self.banner = QLabel("Not connected.")
        self.banner.setWordWrap(True)
        outer.addWidget(self.banner)

        # --- live telemetry ---
        live_row = QHBoxLayout()
        self.live_cards = [
            stat_card("Voltage", "—"),
            stat_card("Current", "—"),
            stat_card("Temperature", "—"),
            stat_card("Charge", "—"),
            stat_card("Status", "—"),
        ]
        for c in self.live_cards:
            live_row.addWidget(c)
        outer.addLayout(live_row)

        # --- identity ---
        self.detail_card = card()
        g = QGridLayout(self.detail_card)
        g.setContentsMargins(16, 14, 16, 14)
        g.setHorizontalSpacing(16)
        g.setVerticalSpacing(10)
        self._grid = g
        outer.addWidget(self.detail_card)
        outer.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll_live)

    # -- connection --

    def _selected_adapter(self) -> str:
        idx = self.adapter_box.currentIndex()
        if idx == 0:
            return "demo"
        elif idx == 1:
            return "cp2112"
        elif idx == 2:
            return "rotool"
        return "demo"

    def _selected_addr(self) -> int:
        try:
            return int(self.addr_edit.text().strip(), 16)
        except ValueError:
            return 0x0B

    def _connect(self) -> None:
        from ...core.diaglog import log as diag
        adapter = self._selected_adapter()
        addr = self._selected_addr()
        diag(f"gui connect: adapter={adapter} addr=0x{addr:02X}")
        try:
            session = GaugeSession.open(adapter, addr)
        except SMbusError as exc:
            diag(f"gui connect failed: {exc}")
            self.banner.setText(f"Connect failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            diag(f"gui connect failed (unexpected): {type(exc).__name__}: {exc}")
            self.banner.setText(f"Connect failed: {exc}")
            return
        self.bench.session = session
        self._set_connected(True)
        demo = session.adapter_name == "demo"
        self.banner.setText(
            "Connected to the DEMO gauge — everything works, no hardware."
            if demo else
            f"Connected via {session.adapter_name} at 0x{session.addr:02X}.")
        self._fill_identity()

    def _disconnect(self) -> None:
        if self.bench.session:
            self.bench.session.close()
            self.bench.session = None
        self._set_connected(False)
        self.banner.setText("Not connected.")

    def _scan(self) -> None:
        if not self.bench.session:
            QMessageBox.information(self, "Scan", "Connect an adapter first.")
            return
        found = self.bench.session.scan()
        if found is None:
            self.banner.setText("Scan: no gauge answered on any known address.")
        else:
            self.addr_edit.setText(f"0x{found:02X}")
            self.banner.setText(f"Scan: gauge answered at 0x{found:02X}.")
            self._fill_identity()

    def _set_connected(self, on: bool) -> None:
        self.connect_btn.setEnabled(not on)
        self.disconnect_btn.setEnabled(on)
        self.scan_btn.setEnabled(on)
        self.adapter_box.setEnabled(not on)
        self.addr_edit.setEnabled(not on)
        if on:
            self._timer.start()
            self._poll_live()
        else:
            self._timer.stop()
            for c in self.live_cards:
                c.set_value("—")

    # -- data --

    def _poll_live(self) -> None:
        session = self.bench.session
        if session is None:
            return
        g = session.gauge
        self.live_cards[0].set_value(_fmt(g.voltage_mv(), "mV"))
        cur = g.current_ma()
        if cur is None:
            self.live_cards[1].set_value("—")
        else:
            self.live_cards[1].set_value(f"{cur:+.0f} mA")
        self.live_cards[2].set_value(_fmt(g.temperature_c(), "°C", 1))
        soc = g.relative_soc()
        self.live_cards[3].set_value(f"{soc:.0f}%" if soc is not None else "—")
        flags = g.status_flags()
        self.live_cards[4].set_value(", ".join(flags).lower() or "idle")

    def _fill_identity(self) -> None:
        session = self.bench.session
        if session is None:
            return
        try:
            info = session.snapshot()
        except SMbusError as exc:
            self.banner.setText(f"Read failed: {exc}")
            return
        g = self._grid
        stat_row("Manufacturer", g, 0, info.manufacturer)
        stat_row("Device name", g, 1, info.name)
        stat_row("Chemistry", g, 2, info.chemistry.value)
        stat_row("Serial", g, 3, str(info.serial_number))
        stat_row("Manufacture date", g, 4, info.manufacture_date or "—")
        stat_row("Design voltage", g, 5,
                 _fmt(info.design_voltage_mv, "mV"))
        stat_row("Design capacity", g, 6,
                 f"{info.raw.get('design_capacity_mah', '—')} mAh")
        stat_row("Full charge capacity", g, 7,
                 f"{info.raw.get('full_charge_capacity_mah', '—')} mAh")
        stat_row("Cycle count", g, 8,
                 str(info.cycle_count) if info.cycle_count is not None else "—")
        soh = info.soh_percent
        stat_row("State of health", g, 9,
                 f"{info.health_label} ({soh:.1f}%)" if soh is not None else "—")