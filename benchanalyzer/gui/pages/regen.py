"""Regenerate page: guided learn-cycle calibration for the bench battery.

The operator switches the bench charger / electronic load; the app watches the
gauge through the phases and records what it learns. No hardware is driven by
the app itself.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.bench import BenchCalibration, BenchCalibrationResult, SessionHolder
from ..widgets import card, stat_card


class _Bridge(QObject):
    progress = Signal(str, object)
    done = Signal(object)


def _pct(v):
    return f"{v:.1f}%" if v is not None else "—"


class RegenPage(QWidget):
    def __init__(self, bench: SessionHolder):
        super().__init__()
        self.setObjectName("content")
        self.bench = bench
        self.engine: BenchCalibration | None = None
        self._log_lines: list[str] = []
        self.bridge = _Bridge()
        self.bridge.progress.connect(self._on_progress)
        self.bridge.done.connect(self._on_done)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(16)

        title = QLabel("Regenerate / Calibrate (bench)")
        title.setObjectName("sectionHeader")
        lay.addWidget(title)

        info = QLabel(
            "Runs a guided learn cycle so the gauge re-learns real capacity: "
            "charge to full → discharge to the low threshold → recharge to "
            "full. YOU switch the bench charger and load when prompted — the "
            "app monitors the gauge and records the results. The pack's own "
            "firmware remains the safety limit; never leave a pack unattended "
            "on the bench."
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        before_card = card()
        bl = QHBoxLayout(before_card)
        self.fcc_before = stat_card("FCC before", "—")
        self.soh_before = stat_card("SoH before", "—")
        bl.addWidget(self.fcc_before)
        bl.addWidget(self.soh_before)
        lay.addWidget(before_card)

        after_card = card()
        al = QHBoxLayout(after_card)
        self.fcc_after = stat_card("FCC after", "—")
        self.soh_after = stat_card("SoH after", "—")
        self.delta = stat_card("FCC delta", "—", accent=True)
        al.addWidget(self.fcc_after)
        al.addWidget(self.soh_after)
        al.addWidget(self.delta)
        lay.addWidget(after_card)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        self.status = QLabel("Connect a gauge on the Bench Analyzer page first.")
        self.status.setWordWrap(True)
        lay.addWidget(self.progress)
        lay.addWidget(self.status)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        lay.addWidget(self.log, 1)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton("Start Learn Cycle")
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

    def _log_append(self, line: str) -> None:
        self._log_lines.append(line)
        self.log.setPlainText("\n".join(self._log_lines[-400:]))

    def _start(self) -> None:
        session = self.bench.session
        if session is None:
            self.status.setText(
                "No gauge connected — open one on the Bench Analyzer page."
            )
            return
        demo = session.adapter_name == "demo"
        power = session.bus.power if demo else None
        self.engine = BenchCalibration(
            session.gauge, power_control=power, low_rsoc=10.0, poll_s=1.0
        )
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setValue(0)
        self._log_append(f"session: {session.adapter_name} @ 0x{session.addr:02X}")
        self.status.setText("Starting learn cycle…")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        try:
            result = self.engine.run(
                progress=lambda msg, frac: self.bridge.progress.emit(msg, frac)
            )
        except Exception as exc:  # noqa: BLE001
            self.bridge.done.emit(exc)
            return
        self.bridge.done.emit(result)

    def _cancel(self) -> None:
        if self.engine:
            self.engine.cancel()
        self.status.setText("Cancel requested (switch charger/load off)…")

    def _on_progress(self, msg: str, frac) -> None:
        self.status.setText(msg)
        if frac is not None:
            self.progress.setValue(int(frac * 100))

    def _on_done(self, payload) -> None:
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if isinstance(payload, Exception):
            self.status.setText(f"Calibration ended with error: {payload}")
            return
        result: BenchCalibrationResult = payload
        self.fcc_before.set_value(str(result.fcc_before or "—"))
        self.fcc_after.set_value(str(result.fcc_after or "—"))
        self.soh_before.set_value(_pct(result.soh_before))
        self.soh_after.set_value(_pct(result.soh_after))
        d = result.fcc_delta
        self.delta.set_value(f"{d:+d}" if d is not None else "—")
        for ev in result.events:
            self._log_append(ev)
        tail = " (cancelled)" if result.cancelled else ""
        self.status.setText(
            f"Learn cycle finished. FCC {result.fcc_before} → {result.fcc_after}.{tail}"
        )
