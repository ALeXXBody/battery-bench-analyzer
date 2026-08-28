"""Analyze page: shows battery health and capacity read from the OS."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.reader import BatteryReader
from ..widgets import card, stat_card, stat_row


def _fmt(v, unit, suffix=""):
    return f"{v:.0f} {unit}" if v is not None else "—"


class AnalyzePage(QWidget):
    def __init__(self, reader: BatteryReader):
        super().__init__()
        self.setObjectName("content")
        self.reader = reader

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("Battery Analysis")
        title.setObjectName("sectionHeader")
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("primary")
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.refresh_btn)
        outer.addLayout(header)

        self.backend_label = QLabel("")
        outer.addWidget(self.backend_label)

        # Stable container for the stat cards; cleared on each refresh.
        self.stats_slot = QWidget()
        self.stats_vbox = QVBoxLayout(self.stats_slot)
        self.stats_vbox.setContentsMargins(0, 0, 0, 0)
        self.stats_vbox.setSpacing(12)
        outer.addWidget(self.stats_slot)

        self.detail_card = card()
        self.detail_grid = QGridLayout(self.detail_card)
        self.detail_grid.setContentsMargins(16, 14, 16, 14)
        self.detail_grid.setHorizontalSpacing(16)
        self.detail_grid.setVerticalSpacing(10)
        outer.addWidget(self.detail_card)
        outer.addStretch(1)

        self.refresh()

    def _clear_stats(self) -> None:
        while self.stats_vbox.count():
            item = self.stats_vbox.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def refresh(self) -> None:
        self._clear_stats()
        batteries = self.reader.refresh()
        if not batteries:
            self.backend_label.setText(
                f"Backend: {self.reader.backend_name} — no battery detected. "
                "(This snapshot is only meaningful on a Windows laptop with a battery.)"
            )
            self.stats_vbox.addWidget(stat_card(
                "No battery", "Nothing to show on this host."))
            return

        bat = batteries[0]
        self.backend_label.setText(f"Backend: {self.reader.backend_name}")

        soh = bat.soh_percent
        health_row = QHBoxLayout()
        health_row.setSpacing(12)
        cards = [
            stat_card("State of Health",
                      f"{soh:.1f}%" if soh is not None else "—", accent=True),
            stat_card("Health", bat.health_label),
            stat_card("Remaining",
                      f"{bat.remaining_percent:.0f}%" if bat.remaining_percent is not None else "—"),
            stat_card("Chemistry", bat.chemistry.value),
        ]
        for c in cards:
            health_row.addWidget(c)
        self.stats_vbox.addLayout(health_row)

        g = self.detail_grid
        stat_row("Manufacturer", g, 0, bat.manufacturer)
        stat_row("Serial", g, 1, bat.serial_number)
        stat_row("Design capacity", g, 2, _fmt(bat.design_capacity_mwh, "mWh"))
        stat_row("Full charge capacity", g, 3, _fmt(bat.full_capacity_mwh, "mWh"))
        stat_row("Charge rate", g, 4, _fmt(bat.charge_rate_mw, "mW"))
        stat_row("Discharge rate", g, 5, _fmt(bat.discharge_rate_mw, "mW"))
        stat_row("Cycle count", g, 6, str(bat.cycle_count) if bat.cycle_count is not None else "—")
        stat_row("Firmware", g, 7, bat.firmware_version or "—")
