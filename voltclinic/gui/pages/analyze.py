"""Bench Analyzer page: live telemetry, identity block and dead-pack rescue.

Connection lives in the app-level ConnectionBar; this page is a pure
consumer of the shared bench session.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.bench import SessionHolder
from ...unlock.smbus import SMbusError
from ..widgets import card, stat_card, stat_row


def _fmt(v, unit, digits=0):
    return f"{v:.{digits}f} {unit}" if v is not None else "—"


class AnalyzePage(QWidget):
    def __init__(self, bench: SessionHolder, conn):
        super().__init__()
        self.setObjectName("content")
        self.bench = bench
        self.conn = conn

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
            "(SCL, SDA, GND) to the adapter, connect using the bar above, "
            "then this page. The gauge is usually at address 0x0B.")
        info.setWordWrap(True)
        outer.addWidget(info)

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

        # --- rescue ---
        rescue_row = QHBoxLayout()
        self.rescue_btn = QPushButton("Rescue dead pack")
        self.rescue_btn.setToolTip(
            "Toggle the SMBus clocks until a sleeping gauge wakes, then read "
            "out its permanent-failure and operation-status registers.")
        self.rescue_btn.clicked.connect(self._rescue)
        rescue_row.addWidget(self.rescue_btn)
        rescue_row.addStretch(1)
        outer.addLayout(rescue_row)
        self._rescue_busy = False

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        outer.addWidget(self.banner)
        outer.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll_live)

        conn.state_changed.connect(self._on_state)
        self._on_state(self.bench.session is not None)

    # -- connection state --

    def _on_state(self, connected: bool) -> None:
        self.rescue_btn.setEnabled(not self._rescue_busy)
        if connected:
            self.banner.setText("")
            self._timer.start()
            self._poll_live()
            self._fill_identity()
        else:
            self._timer.stop()
            for c in self.live_cards:
                c.set_value("—")
            self._clear_grid()

    def _clear_grid(self) -> None:
        grid = self._grid
        while grid.count():
            item = grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

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
        grid = self._grid
        self._clear_grid()
        stat_row("Manufacturer", grid, 0, info.manufacturer)
        stat_row("Device name", grid, 1, info.name)
        stat_row("Chemistry", grid, 2, info.chemistry.value)
        stat_row("Serial", grid, 3, str(info.serial_number))
        stat_row("Manufacture date", grid, 4, info.manufacture_date or "—")
        stat_row("Design voltage", grid, 5,
                 _fmt(info.design_voltage_mv, "mV"))
        stat_row("Design capacity", grid, 6,
                 f"{info.raw.get('design_capacity_mah', '—')} mAh")
        stat_row("Full charge capacity", grid, 7,
                 f"{info.raw.get('full_charge_capacity_mah', '—')} mAh")
        stat_row("Cycle count", grid, 8,
                 str(info.cycle_count) if info.cycle_count is not None else "—")
        soh = info.soh_percent
        stat_row("State of health", grid, 9,
                 f"{info.health_label} ({soh:.1f}%)" if soh is not None else "—")

    # -- rescue --

    def _rescue(self) -> None:
        """Wake + diagnose a silent pack; results land in the banner."""
        if self._rescue_busy:
            return
        connected_demo = (self.bench.session is not None
                          and self.bench.session.adapter_name == "demo")
        if connected_demo:
            from ...core.bench import FakeGaugeBus
            bus = FakeGaugeBus()
        else:
            adapter = self.conn.selected_adapter()
            if adapter == "cp2112":
                from ...unlock.cp2112 import Cp2112Smbus
                try:
                    bus = Cp2112Smbus()
                except SMbusError as exc:
                    self.banner.setText(
                        f"Rescue couldn't open the adapter: {exc}")
                    return
            else:
                self.banner.setText(
                    "Rescue: choose the CP2112 adapter for now.")
                return
        QMessageBox.information(
            self, "Rescue consent",
            "The rescue loop toggles the SMBus clocks for ~15 seconds, then "
            "reads the gauge's status registers (writes to "
            "ManufacturerAccess interpreted as diagnostic reads). "
            "Confirm the pack is safe to power on the bench with nothing "
            "attached before clicking.")
        from ...core.rescue import rescue as run_rescue
        self._rescue_busy = True
        self.rescue_btn.setEnabled(False)
        self.conn.set_busy(True)
        self.banner.setText(
            "Rescuing: toggling the SMBus clocks — keep the pack powered…")
        QApplication.processEvents()
        try:
            out = run_rescue(bus, consent=self.objectName() + "-rescue-consent",
                             wake_seconds=15.0)
        except Exception as exc:  # noqa: BLE001
            self.banner.setText(f"Rescue failed: {exc}")
            out = None
        finally:
            self._rescue_busy = False
            self.rescue_btn.setEnabled(True)
            self.conn.set_busy(False)
        if out is None:
            return
        if not out.get("addr"):
            self.banner.setText(
                "Rescue: the gauge did not wake.\n" + out.get("no_gauge_hint", ""))
            return
        report = "; ".join(
            f"{lbl}={out.get(lbl + '_rendered', 'unavailable')}"
            for lbl in ("PFStatus", "OperationStatus", "SafetyStatus", "PFAlert"))
        warning = f"\nWARNING: {out['warning']}" if out.get("warning") else ""
        self.banner.setText(
            f"Rescue: gauge woke at 0x{out['addr']:02X}.\n{report}{warning}\n"
            "Press Reconnect (Connect) above to open the full session on it.")
