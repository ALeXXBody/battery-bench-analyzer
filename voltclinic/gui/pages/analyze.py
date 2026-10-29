"""Bench Analyzer page: one merged bench-analysis workspace.

Connection lives in the app-level ConnectionBar; this page is a pure
consumer of the shared bench session and is organised as tabs:

- Overview — live telemetry cards + identity/health block + rescue banner
- Cells    — consent-gated per-cell voltages, balance analysis, verdict
- Live data — full telemetry table + CSV export
- Registers — read-only SBS register dump
"""

from __future__ import annotations

import csv
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.analysis import analyse
from ...core.bench import SessionHolder
from ...unlock.smbus import SMbusError
from ..widgets import card, stat_card, stat_row


def _fmt(v, unit, digits=0):
    return f"{v:.{digits}f} {unit}" if v is not None else "—"


class _RescueBridge(QObject):
    """Marshals the rescue worker thread's result onto the GUI thread."""

    result = Signal(object)


class AnalyzePage(QWidget):
    def __init__(self, bench: SessionHolder, conn):
        super().__init__()
        self.setObjectName("content")
        self.bench = bench
        self.conn = conn

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(12)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)

        # compact dynamic status line (refresh/result/error messages only)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)
        self.tabs.addTab(self._build_overview(), "Overview")
        self.tabs.addTab(self._build_cells(), "Cells / Analysis")
        self.tabs.addTab(self._build_live(), "Live data")
        self.tabs.addTab(self._build_registers(), "Registers")

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        self._rescue_busy = False

        conn.state_changed.connect(self._on_state)
        self._on_state(self.bench.session is not None)

    # ------------------------------------------------------------------
    # tab construction
    # ------------------------------------------------------------------

    def _build_overview(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 10, 4, 4)
        lay.setSpacing(12)

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
        lay.addLayout(live_row)

        self.detail_card = card()
        grid = QGridLayout(self.detail_card)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        self._grid = grid
        lay.addWidget(self.detail_card)

        rescue_row = QHBoxLayout()
        self.rescue_btn = QPushButton("Rescue dead pack")
        self.rescue_btn.setToolTip(
            "Toggle the SMBus clocks until a sleeping gauge wakes, then "
            "read out its permanent-failure and operation-status registers."
        )
        self.rescue_btn.clicked.connect(self._rescue)
        rescue_row.addWidget(self.rescue_btn)
        rescue_row.addStretch(1)
        lay.addLayout(rescue_row)

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.banner.setCursor(Qt.CursorShape.IBeamCursor)
        lay.addWidget(self.banner)
        lay.addStretch(1)
        return w

    def _build_cells(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 10, 4, 4)
        lay.setSpacing(10)

        row = QHBoxLayout()
        self.cell_read_btn = QPushButton("Read cell voltages…")
        self.cell_read_btn.setToolTip(
            "Selects the gauge's DAStatus bank through ManufacturerAccess "
            "(one diagnostic write, consent-gated) and reads the per-cell "
            "voltages it publishes on ManufacturerBlockAccess."
        )
        self.cell_read_btn.clicked.connect(self._read_cells)
        row.addWidget(self.cell_read_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self.cells_grid = QGridLayout()
        self.cells_grid.setHorizontalSpacing(18)
        self.cells_grid.setVerticalSpacing(8)
        lay.addLayout(self.cells_grid)

        self.balance_label = QLabel("")
        self.balance_label.setObjectName("valueLabel")
        lay.addWidget(self.balance_label)

        self.verdict = QLabel("")
        self.verdict.setWordWrap(True)
        self.verdict.setObjectName("valueLabel")
        lay.addWidget(self.verdict)
        lay.addStretch(1)

        self._cell_consent = False
        self._last_cells = None
        return w

    def _build_live(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 10, 4, 4)
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
        lay.setContentsMargins(4, 10, 4, 4)
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
            QHeaderView.ResizeMode.ResizeToContents
        )
        t.horizontalHeader().setStretchLastSection(True)
        return t

    # ------------------------------------------------------------------
    # lifecycle / state
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def _on_state(self, connected: bool) -> None:
        self._cell_consent = False  # consent is per-connection
        self._last_cells = None
        self.rescue_btn.setEnabled(not self._rescue_busy)
        self.cell_read_btn.setEnabled(True)
        if connected:
            self.banner.setText("")
            self.hint.setText("")
            self._fill_identity()
        else:
            self._timer.stop()
            if hasattr(self, "live_cards"):
                for c in self.live_cards:
                    c.set_value("—")
            self._clear_grid()
            self._fill_cells(banner="Not connected — use the connection bar.")

    def _clear_grid(self) -> None:
        grid = self._grid
        while grid.count():
            item = grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _tick(self) -> None:
        if self.bench.session is None:
            return
        idx = self.tabs.currentIndex()
        if idx == 0:
            self._poll_live()
        elif idx == 1 and self._cell_consent:
            self._read_cells(silent=True)
        elif idx == 2:
            self._refresh_live()

    # ------------------------------------------------------------------
    # overview
    # ------------------------------------------------------------------

    def _poll_live(self) -> None:
        session = self.bench.session
        if session is None:
            return
        g = session.gauge
        self.live_cards[0].set_value(_fmt(g.voltage_mv(), "mV"))
        cur = g.current_ma()
        self.live_cards[1].set_value("—" if cur is None else f"{cur:+.0f} mA")
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
        stat_row("Design voltage", grid, 5, _fmt(info.design_voltage_mv, "mV"))
        stat_row(
            "Design capacity",
            grid,
            6,
            f"{info.raw.get('design_capacity_mah', '—')} mAh",
        )
        stat_row(
            "Full charge capacity",
            grid,
            7,
            f"{info.raw.get('full_charge_capacity_mah', '—')} mAh",
        )
        stat_row(
            "Cycle count",
            grid,
            8,
            str(info.cycle_count) if info.cycle_count is not None else "—",
        )
        stat_row(
            "State of health",
            grid,
            9,
            f"{info.health_label} ({info.soh_percent:.1f}%)"
            if info.soh_percent is not None
            else "—",
        )

    # ------------------------------------------------------------------
    # cells / analysis
    # ------------------------------------------------------------------

    def _read_cells(self, silent: bool = False) -> None:
        session = self.bench.session
        if session is None:
            self.hint.setText("Open an adapter with the connection bar above.")
            return
        if not self._cell_consent:
            if silent:
                return
            ans = QMessageBox.question(
                self,
                "Cell voltage consent",
                "Reading the per-cell voltages selects the gauge's DASTatus "
                "bank through ManufacturerAccess — that is one diagnostic "
                "WRITE command on the SMBus (public TI interface; it selects "
                "a read-only data bank, it does not change any setting).\n\n"
                "Allow diagnostic ManufacturerAccess writes for this "
                "connection?",
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
            self._cell_consent = True
        from ...core.rescue import read_cell_voltages

        try:
            cells = read_cell_voltages(
                session.bus, session.addr, consent=self.objectName() + "-cells-consent"
            )
        except SMbusError as exc:
            self._fill_cells(banner=f"Cell voltage read failed: {exc}")
            return
        self._last_cells = cells
        self._fill_cells()

    def _fill_cells(self, banner: str | None = None) -> None:
        grid = self.cells_grid
        while grid.count():
            item = grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if banner:
            self.balance_label.setText(banner)
            self.verdict.setText("")
            return
        session = self.bench.session
        if session is None:
            return
        temp = session.gauge.temperature_c()
        try:
            info = session.snapshot()
        except SMbusError:
            info = None
        cells = self._last_cells or []
        if not cells:
            self.balance_label.setText(
                "No cell voltages read yet — press the button above."
            )
            return
        for i, v in enumerate(cells):
            lbl = QLabel(f"Cell {i + 1}")
            lbl.setObjectName("cardTitle")
            val = QLabel(f"{v:.0f} mV")
            val.setObjectName("valueLabel")
            grid.addWidget(
                lbl,
                i // 4,
                (i % 4) * 2 + 0,
            )
            grid.addWidget(val, i // 4, (i % 4) * 2 + 1)
        if info is None:
            if len(cells) >= 2:
                self.balance_label.setText(
                    f"Balance: max-min gap {max(cells) - min(cells):.0f} mV."
                )
            self.verdict.setText(
                "(identity snapshot unavailable — balance read from cells only)"
            )
            return
        result = analyse(info, cell_voltages_mv=cells, temperature_c=temp)
        self.balance_label.setText(
            f"Balance: max-min gap {result.imbalance_mv:.0f} mV — "
            f"{result.imbalance_state}."
            if result.imbalance_mv is not None
            else "Balance: needs at least two cell readings."
        )
        self.verdict.setText(result.render())

    # ------------------------------------------------------------------
    # live + registers (read-only dumps)
    # ------------------------------------------------------------------

    def _refresh_live(self) -> None:
        session = self.bench.session
        if not session:
            self.hint.setText("Open an adapter with the connection bar above.")
            return
        try:
            rows = session.gauge.full_readout()
        except SMbusError as exc:
            self.hint.setText(f"Read failed: {exc}")
            return
        self._fill_table(
            self.live_table,
            [
                (n, f"0x{raw:04X}" if isinstance(raw, int) else "", text)
                for n, raw, _u, text in rows
            ],
        )
        self.live_rows.setText(
            f"{len(rows)} registers · refreshed {time.strftime('%H:%M:%S')}"
        )

    def _refresh_dump(self) -> None:
        session = self.bench.session
        if not session:
            self.hint.setText("Open an adapter with the connection bar above.")
            return
        try:
            rows = session.gauge.register_dump()
        except SMbusError as exc:
            self.hint.setText(f"Dump failed: {exc}")
            return
        self._fill_table(self.dump_table, rows)
        self.hint.setText(
            f"Register dump complete: {len(rows)} entries "
            "(read-only; ManufacturerAccess 0x00 is never touched here)."
        )

    @staticmethod
    def _fill_table(table: QTableWidget, rows) -> None:
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                item = QTableWidgetItem(str(cell))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(r, c, item)

    def _export_csv(self) -> None:
        live = getattr(self, "live_table", None)
        if live is None or live.rowCount() == 0:
            self.hint.setText("Nothing to export yet — refresh the live table first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export live data CSV", "battery_live.csv", "CSV (*.csv)"
        )
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            headers = [
                live.horizontalHeaderItem(c).text() for c in range(live.columnCount())
            ]
            w.writerow(["time"] + headers)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            for r in range(live.rowCount()):
                w.writerow(
                    [stamp]
                    + [
                        live.item(r, c).text() if live.item(r, c) else ""
                        for c in range(live.columnCount())
                    ]
                )
        self.hint.setText(f"Exported: {path}")

    # ------------------------------------------------------------------
    # rescue
    # ------------------------------------------------------------------

    def _rescue(self) -> None:
        """Start wake + diagnose on a background thread (GUI stays live)."""
        if self._rescue_busy:
            return
        connected_demo = (
            self.bench.session is not None and self.bench.session.adapter_name == "demo"
        )
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
                    self.banner.setText(f"Rescue couldn't open the adapter: {exc}")
                    return
            else:
                self.banner.setText("Select CP2112 in the connection bar.")
                return
        QMessageBox.information(
            self,
            "Rescue consent",
            "The rescue loop toggles the SMBus clocks for ~15 seconds, then "
            "reads the gauge's status registers (writes to "
            "ManufacturerAccess interpreted as diagnostic reads). "
            "Confirm the pack is safe to power on the bench with nothing "
            "attached before clicking.",
        )
        from ...core.rescue import rescue as run_rescue

        self._rescue_busy = True
        self.rescue_btn.setEnabled(False)
        self.conn.set_busy(True)
        self.banner.setText(
            "Rescuing: toggling the SMBus clocks — keep the pack powered… "
            "(the window stays responsive; this takes ~15 seconds)"
        )
        self._rescue_bridge = _RescueBridge()
        self._rescue_bridge.result.connect(self._on_rescue_result)
        threading.Thread(
            target=self._rescue_worker, args=(bus, run_rescue), daemon=True
        ).start()

    def _rescue_worker(self, bus, run_rescue) -> None:
        try:
            out = run_rescue(
                bus, consent=self.objectName() + "-rescue-consent", wake_seconds=15.0
            )
        except Exception as exc:  # noqa: BLE001
            out = {"error": f"{type(exc).__name__}: {exc}"}
        self._rescue_bridge.result.emit(out)

    def _on_rescue_result(self, out: dict) -> None:
        self._rescue_busy = False
        self.rescue_btn.setEnabled(True)
        self.conn.set_busy(False)
        if out.get("error"):
            self.banner.setText(f"Rescue failed: {out['error']}")
            return
        if not out.get("addr"):
            self.banner.setText(
                "Rescue: the gauge did not wake.\n" + out.get("no_gauge_hint", "")
            )
            return
        report = "; ".join(
            f"{lbl}={out.get(lbl + '_rendered', 'unavailable')}"
            for lbl in ("PFStatus", "OperationStatus", "SafetyStatus", "PFAlert")
        )
        warning = f"\nWARNING: {out['warning']}" if out.get("warning") else ""
        self.banner.setText(
            f"Rescue: gauge woke at 0x{out['addr']:02X}.\n{report}{warning}\n"
            "Press Connect above to open the full session on it."
        )
