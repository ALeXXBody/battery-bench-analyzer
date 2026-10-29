"""Help page: all orientation/guidance texts in one place.

Static, scrollable, read-only. The wiring section is single-sourced from
``ConnectionBar.help_text`` so the bar and this page never drift apart.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..connection import ConnectionBar


def _block(title: str, body: str) -> str:
    body = body.strip("\n")
    return f"<h3>{title}</h3><p style='white-space:pre-line'>{body}</p>"


class HelpPage(QWidget):
    @staticmethod
    def sections():
        return (
            ("Bench wiring", ConnectionBar.help_text()),
            ("Connecting",
             "Pick the adapter, set the address (0x0B for most packs), "
             "press Connect. Nothing answers? Press Scan addresses — a "
             "full sweep also toggles the clock lines, which wakes "
             "sleeping gauges and reports per-address error codes with "
             "the likely causes."),
            ("Rescue dead pack",
             "When a pack looks completely silent: Rescue toggles the bus "
             "clocks for ~15 seconds, then — if a gauge woke — reads the "
             "Safety / permanent-fail / Operation status registers and "
             "shows which failure is set. It never changes any settings; "
             "the manufacturer-access reads it needs are consent-gated "
             "with a dialog. The window stays responsive the whole time."),
            ("Cell voltages / Analysis",
             "Per-cell voltages come from the gauge's diagnostic status "
             "bank (TI public interface; one consent-gated "
             "ManufacturerAccess write selects a read-only data bank). "
             "The tab computes the max-min cell gap (balance) and a "
             "capacity / cycle / temperature verdict from the standard "
             "SBS data."),
            ("Writes and consent",
             "Any write to the gauge — EEPROM, flash, configuration "
             "registers, unlock sequences — happens only behind an "
             "explicit consent dialog. Read-only SBS data, battery "
             "information and diagnostic status banks never touch that "
             "path."),
            ("Gauge chips",
             "The base protocol is SBS v1.1 (Smart Battery Data), spoken "
             "by nearly every pack gauge: the Summary / Live data / "
             "Registers tabs work identically on any compliant chip. The "
             "TI-specific flows (per-cell DAStatus, rescue status "
             "registers) degrade gracefully to 'unavailable' on other "
             "families."),
            ("Logs",
             "Diagnostics land in %LOCALAPPDATA%\\VoltClinic\\diag.log "
             "(Windows) or ~/.voltclinic/diag.log (Linux/macOS) — include "
             "the last lines when reporting an issue."),
        )

    def __init__(self):
        super().__init__()
        self.setObjectName("content")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)

        title = QLabel("Help")
        title.setObjectName("sectionHeader")
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 8, 12, 8)
        lay.setSpacing(6)
        html = "".join(_block(t, b) for t, b in self.sections())
        body = QLabel(html)
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lay.addWidget(body)
        lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
