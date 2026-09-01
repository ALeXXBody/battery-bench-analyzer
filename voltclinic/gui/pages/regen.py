"""Regenerate page: supervised calibration cycle with live progress."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.reader import BatteryReader
from ...core.regeneration import RegenerationEngine, RegenerationResult
from ..widgets import card, stat_card


class _Bridge(QObject):
    progress = Signal(str, float)
    done = Signal(object)


def _pct(v):
    return f"{v:.1f}%" if v is not None else "—"


class RegenPage(QWidget):
    def __init__(self, reader: BatteryReader):
        super().__init__()
        self.setObjectName("content")
        self.reader = reader
        self.engine: RegenerationEngine | None = None
        self.bridge = _Bridge()
        self.bridge.progress.connect(self._on_progress)
        self.bridge.done.connect(self._on_done)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        title = QLabel("Regenerate / Recalibrate")
        title.setObjectName("sectionHeader")
        lay.addWidget(title)

        info = QLabel(
            "Performs a supervised charge→discharge→charge cycle so the fuel "
            "gauge re-learns real capacity. The machine is kept awake, and "
            "Windows' low-battery shutdown still protects the cells. This is "
            "a software recalibration only — it does not open the pack or "
            "force failed cells back to life."
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        before_card = card()
        bl = QHBoxLayout(before_card)
        self.soh_before = stat_card("SoH before", "—")
        self.cap_before = stat_card("Capacity", "—")
        bl.addWidget(self.soh_before)
        bl.addWidget(self.cap_before)
        lay.addWidget(before_card)

        after_card = card()
        al = QHBoxLayout(after_card)
        self.soh_after = stat_card("SoH after", "—")
        self.delta = stat_card("Delta", "—", accent=True)
        self.cycles_done = stat_card("Cycles", "0")
        al.addWidget(self.soh_after)
        al.addWidget(self.delta)
        al.addWidget(self.cycles_done)
        lay.addWidget(after_card)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.status = QLabel("Idle.")
        self.status.setWordWrap(True)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("Start Calibration")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._start)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.cancel_btn)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        lay.addStretch(1)

    def _start(self) -> None:
        self.engine = RegenerationEngine(self.reader, battery_index=0)
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText("Calibration running… keep the laptop plugged in as prompted.")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        try:
            result = self.engine.run(
                progress=lambda msg, frac: self.bridge.progress.emit(msg, frac))
        except Exception as exc:  # noqa: BLE001
            self.bridge.done.emit(exc)
            return
        self.bridge.done.emit(result)

    def _cancel(self) -> None:
        if self.engine:
            self.engine.cancel()
        self.status.setText("Cancel requested (finishes current safe endpoint)…")

    def _on_progress(self, msg: str, frac: float) -> None:
        self.status.setText(msg)
        self.progress.setValue(int(frac * 100))

    def _on_done(self, payload) -> None:
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if isinstance(payload, Exception):
            self.status.setText(f"Calibration ended with error: {payload}")
            return
        result: RegenerationResult = payload
        self.soh_before.set_value(_pct(result.before_soh))
        self.soh_after.set_value(_pct(result.after_soh))
        delta = result.soh_delta
        self.delta.set_value(f"{delta:+.1f}%" if delta is not None else "—")
        self.cycles_done.set_value(str(result.cycles_completed))
        tail = " (cancelled)" if result.cancelled else ""
        self.status.setText(
            f"Calibration finished. SoH "
            f"{_pct(result.before_soh)} → {_pct(result.after_soh)}.{tail}")
