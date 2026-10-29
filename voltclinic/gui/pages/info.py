"""Battery Information page: NLBA-style tabbed read-out of a connected pack.

Purely read-only views over the shared bench session (connect/disconnect
lives on the Analyze page):

- Summary — identity, capacities, state of health
- Live data — every SBS telemetry register, auto-refreshed, CSV export
- Registers — full SBS register dump (words + blocks), non-destructive
"""
from __future__ import annotations

import csv
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.sbs import describe_scan, scan_for_gauge_detailed
from ...unlock.smbus import SMbusError
from ..widgets import card


class InfoPage(QWidget):
    def __init__(self, bench):
        super().__init__()
        self.setObjectName("content")
        self.bench = bench

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(12)

        title = QLabel("Battery Information")
        title.setObjectName("sectionHeader")
        outer.addWidget(title)

        self.hint = QLabel("Connect a battery on the Analyze page first.")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)

        self.tabs.addTab(self._build_summary(), "Summary")
        self.tabs.addTab(self._build_live(), "Live data")
        self.tabs.addTab(self._build_registers(), "Registers")

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

    # -- tab builders ---------------------------------------------------

    def _build_summary(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 12, 8, 12)
        lay.setSpacing(12)
        self.summary_grid_card = card()
        from PySide6.QtWidgets import QGridLayout
        from ..widgets import stat_row
        self.summary_grid = QGridLayout(self.summary_grid_card)
        self.summary_grid.setContentsMargins(16, 14, 16, 14)
        self.summary_grid.setHorizontalSpacing(24)
        self.summary_grid.setVerticalSpacing(10)
        lay.addWidget(self.summary_grid_card)
        lay.addStretch(1)
        return w

    def _build_live(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 12, 8, 12)
        lay.setSpacing(10)
        row = QHBoxLayout()
        self.live_refresh_btn = QPushButton("Refresh now")
        self.live_refresh_btn.clicked.connect(self._refresh_live)
        self.live_export_btn = QPushButton("Export CSV…")
        self.live_export_btn.clicked.connect(self._export_csv)
        self.live_rows = QLabel("—")
        row.addWidget(self.live_rows)
        row.addStretch(1)
        row.addWidget(self.live_refresh_btn)
        row.addWidget(self.live_export_btn)
        lay.addLayout(row)
        self.live_table = self._table(("Register", "Raw", "Value"))
        lay.addWidget(self.live_table, 1)
        return w

    def _build_registers(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 12, 8, 12)
        lay.setSpacing(10)
        row = QHBoxLayout()
        self.dump_refresh_btn = QPushButton("Dump registers")
        self.dump_refresh_btn.clicked.connect(self._refresh_dump)
        row.addWidget(self.dump_refresh_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.dump_table = self._table(("Reg", "Name", "Type", "Unit", "Value"))
        lay.addWidget(self.dump_table, 1)
        return w

    @staticmethod
    def _table(headers) -> QTableWidget:
        t = QTableWidget()
        t.setColumnCount(len(headers))
        t.setHorizontalHeaderLabels(list(headers))
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        return t

    def _session(self):
        return self.bench.session

    # -- lifecycle ------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._timer.start()
        self._tick()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self) -> None:
        session = self._session()
        if not session:
            self.hint.setText(
                "Connect a battery on the Analyze page first — all tabs "
                "here are read-only over that session.")
            return
        self.hint.setText(
            f"Reading {session.adapter_name} @ 0x{session.addr:02X} "
            "(read-only).")
        if self.tabs.currentIndex() == 0:
            self._refresh_summary()
        elif self.tabs.currentIndex() == 1:
            self._refresh_live()

    # -- summary tab ----------------------------------------------------

    def _refresh_summary(self) -> None:
        session = self._session()
        try:
            info = session.snapshot()
        except SMbusError as exc:
            self.hint.setText(f"Read failed: {exc}\n"
                              "Re-scan or reconnect on the Analyze page.")
            return
        # full SBS identity, fields the gauge answered
        rows = [
            ("Manufacturer", info.manufacturer),
            ("Device name", info.name),
            ("Chemistry", info.chemistry.value),
            ("Serial", str(info.serial_number)),
            ("Manufacture date", info.manufacture_date or "—"),
            ("Design voltage", f"{info.design_voltage_mv:.0f} mV"
             if info.design_voltage_mv else "—"),
            ("Design capacity", f"{info.raw.get('design_capacity_mah')} mAh"
             if info.raw.get("design_capacity_mah") else "—"),
            ("Full charge capacity",
             f"{info.raw.get('full_charge_capacity_mah')} mAh"
             if info.raw.get("full_charge_capacity_mah") else "—"),
            ("Remaining capacity",
             f"{info.raw.get('remaining_capacity_mah')} mAh"
             if info.raw.get("remaining_capacity_mah") else "—"),
            ("State of health",
             f"{info.health_label} ({info.soh_percent:.1f}%)"
             if info.soh_percent is not None else "—"),
            ("Cycle count", str(info.cycle_count)
             if info.cycle_count is not None else "—"),
            ("Voltage", f"{info.voltage_mv:.0f} mV"
             if info.voltage_mv else "—"),
            ("Current", f"{info.current_ma:+.0f} mA"
             if info.current_ma is not None else "—"),
            ("Temperature", f"{info.temperature_c:.1f} °C"
             if info.temperature_c is not None else "—"),
            ("Charge", f"{info.relative_soc:.0f}%"
             if info.relative_soc is not None else "—"),
            ("Charge state", info.charge_state.value),
            ("Status flags", info.raw.get("status_flags", "—")),
        ]
        grid = self.summary_grid
        # empty the grid before refill
        while grid.count():
            item = grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        from ..widgets import stat_row
        for r, (label, value) in enumerate(rows):
            stat_row(label, grid, r, str(value))

    # -- live tab -------------------------------------------------------

    def _refresh_live(self) -> None:
        session = self._session()
        if not session:
            return
        try:
            rows = session.gauge.full_readout()
        except SMbusError as exc:
            self.hint.setText(f"Read failed: {exc}")
            return
        self._fill_table(
            self.live_table,
            [(n, f"0x{raw:04X}" if isinstance(raw, int) else "", text)
             for n, raw, _u, text in rows])
        self.live_rows.setText(
            f"{len(rows)} registers · refreshed "
            f"{time.strftime('%H:%M:%S')}")

    def _refresh_dump(self) -> None:
        session = self._session()
        if not session:
            self.hint.setText("Connect a battery on the Analyze page first.")
            return
        try:
            rows = session.gauge.register_dump()
        except SMbusError as exc:
            self.hint.setText(f"Dump failed: {exc}")
            return
        self._fill_table(self.dump_table, rows)
        self.hint.setText(
            f"Register dump complete: {len(rows)} entries "
            "(read-only; ManufacturerAccess 0x00 is never touched here).")

    @staticmethod
    def _fill_table(table: QTableWidget, rows) -> None:
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                item = QTableWidgetItem(str(cell))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(r, c, item)

    def _export_csv(self) -> None:
        rows = []
        for r in range(self.live_table.rowCount()):
            rows.append([self.live_table.item(r, c).text() if False else
                         (self.live_table.item(r, c).text()
                          if self.live_table.item(r, c) else "")
                         for c in range(self.live_table.columnCount())])
        if not rows:
            self.hint.setText("Nothing to export yet — refresh the live "
                              "table first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export live data CSV", "battery_live.csv",
            "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            headers = [self.live_table.horizontalHeaderItem(c).text()
                       for c in range(self.live_table.columnCount())]
            w.writerow(["time"] + headers)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            for row in rows:
                w.writerow([stamp] + row)
        self.hint.setText(f"Exported: {path}")
