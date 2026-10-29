"""App-level connection bar: one adapter selector shared by every page.

Lives at the top of the main window, above the page stack. Owns nothing
about a page — it only opens/closes the session in the shared
``SessionHolder`` and notifies the pages, which are pure read consumers.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.bench import GaugeSession
from ..unlock.smbus import SMbusError


class ConnectionBar(QWidget):
    """Adapter / address / connect-disconnect-scan / status, always visible."""

    state_changed = Signal(bool)   # True = a session is now open

    def __init__(self, bench):
        super().__init__()
        self.bench = bench
        self.setObjectName("connBar")

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        title = QLabel("Connection")
        title.setObjectName("cardTitle")
        row.addWidget(title)

        self.adapter_box = QComboBox()
        self.adapter_box.addItem("CP2112 USB-SMBus adapter")
        self.adapter_box.addItem("ROTOOL USB analyzer")
        row.addWidget(self.adapter_box)

        row.addWidget(QLabel("Address:"))
        self.addr_edit = QLineEdit("0x0B")
        self.addr_edit.setFixedWidth(64)
        row.addWidget(self.addr_edit)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("primary")
        self.connect_btn.clicked.connect(self._connect)
        row.addWidget(self.connect_btn)

        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self._disconnect)
        row.addWidget(self.disconnect_btn)

        self.scan_btn = QPushButton("Scan addresses")
        self.scan_btn.clicked.connect(self._scan)
        row.addWidget(self.scan_btn)

        self.demo_btn = QPushButton("Demo")
        self.demo_btn.setToolTip(
            "Start the built-in simulated battery so every feature can be "
            "explored without hardware. Only available when no adapter "
            "is connected.")
        self.demo_btn.clicked.connect(self._demo)
        row.addWidget(self.demo_btn)

        row.addStretch(1)
        self.status = QLabel("Not connected")
        self.status.setObjectName("connStatus")
        row.addWidget(self.status)

        guide = QLabel(
            "Bench wiring: the pack's gauge D→SDA, C→SCL, one \"-\"→GND; "
            "SBS gauges usually answer at address 0x0B (Scan finds it).")
        guide.setObjectName("connGuide")
        guide.setWordWrap(True)
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 4)
        row2.addWidget(guide, 1)

        bar = QVBoxLayout(self)
        bar.setContentsMargins(16, 8, 16, 4)
        bar.setSpacing(2)
        bar.addLayout(row)
        bar.addLayout(row2)

    # -- public API used by pages (e.g. rescue) -------------------------

    def selected_adapter(self) -> str:
        idx = self.adapter_box.currentIndex()
        return ("cp2112", "rotool")[idx if 0 <= idx <= 1 else 0]

    def selected_addr(self) -> int:
        try:
            return int(self.addr_edit.text().strip(), 16)
        except ValueError:
            return 0x0B

    def set_busy(self, busy: bool) -> None:
        for w in (self.adapter_box, self.addr_edit, self.connect_btn,
                  self.disconnect_btn, self.scan_btn, self.demo_btn):
            w.setEnabled(not busy)
        if busy:
            self.disconnect_btn.setEnabled(True)  # allow escape mid-operation

    # -- connected-state plumbing ----------------------------------------

    def _set_connected(self, on: bool) -> None:
        self.connect_btn.setEnabled(not on)
        self.disconnect_btn.setEnabled(on)
        self.adapter_box.setEnabled(not on)
        self.addr_edit.setEnabled(not on)
        self.demo_btn.setEnabled(not on)
        self.state_changed.emit(on)

    # -- actions ---------------------------------------------------------

    def _demo(self) -> None:
        """Open the simulated battery (button is disabled while connected)."""
        from ..core.diaglog import log as diag
        try:
            session = GaugeSession.open("demo")
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Demo failed: {exc}")
            return
        self.bench.session = session
        self._set_connected(True)
        diag("connect bar: demo gauge opened")
        self.status.setText("Connected to the DEMO gauge")

    def _connect(self) -> None:
        from ..core.diaglog import log as diag
        adapter = self.selected_adapter()
        addr = self.selected_addr()
        diag(f"connect bar: adapter={adapter} addr=0x{addr:02X}")
        try:
            session = GaugeSession.open(adapter, addr)
        except SMbusError as exc:
            diag(f"connect failed: {exc}")
            self.status.setText(f"Connect failed: {exc}"[:80] + "…"
                                if len(str(exc)) > 80 else f"Connect failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            diag(f"connect failed (unexpected): {type(exc).__name__}: {exc}")
            self.status.setText(f"Connect failed: {exc}")
            return
        self.bench.session = session
        self._set_connected(True)
        if session.adapter_name == "demo":
            self.status.setText("Connected to the DEMO gauge")
        else:
            self.status.setText(
                f"Connected: {session.adapter_name} @ 0x{session.addr:02X}")

    def _disconnect(self) -> None:
        if self.bench.session:
            self.bench.session.close()
            self.bench.session = None
        self._set_connected(False)
        self.status.setText("Not connected")

    def _scan(self) -> None:
        session = self.bench.session
        if session is None:
            self.status.setText("Connect first, then scan.")
            return
        found = session.scan()
        if found is not None:
            self.addr_edit.setText(f"0x{found:02X}")
            self.status.setText(f"Scan: gauge answered at 0x{found:02X} "
                                "(reconnect to use it)")
            return
        from ..core.sbs import describe_scan, scan_for_gauge_detailed
        self.status.setText(
            "Scan: no gauge answered.\n"
            + describe_scan(scan_for_gauge_detailed(session.bus)))
