"""Main application window: sidebar navigation + stacked pages."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.reader import BatteryReader
from .pages.analyze import AnalyzePage
from .pages.regen import RegenPage
from .pages.unlock import UnlockPage


class MainWindow(QWidget):
    def __init__(self, reader: BatteryReader | None = None):
        super().__init__()
        self.setWindowTitle("BatteryClinic")
        self.resize(1100, 720)
        self.reader = reader or BatteryReader()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setObjectName("rootWidget")

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(0)

        title = QLabel("BatteryClinic")
        title.setObjectName("appTitle")
        side.addWidget(title)

        self.stack = QStackedWidget()
        self.analyze_page = AnalyzePage(self.reader)
        self.regen_page = RegenPage(self.reader)
        self.unlock_page = UnlockPage()
        self.stack.addWidget(self.analyze_page)
        self.stack.addWidget(self.regen_page)
        self.stack.addWidget(self.unlock_page)

        self._nav_buttons: list[QPushButton] = []
        nav_items = [
            ("Analyze", 0),
            ("Regenerate", 1),
            ("Repair / Unlock", 2),
        ]
        for label, idx in nav_items:
            btn = QPushButton(label)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("pageIndex", idx)
            btn.clicked.connect(lambda _=False, i=idx: self._go(i, btn))
            side.addWidget(btn)
            self._nav_buttons.append(btn)
        side.addStretch(1)

        root.addWidget(sidebar)
        root.addWidget(self.stack, 1)

        self._go(0, self._nav_buttons[0])

    def _go(self, index: int, btn: QPushButton) -> None:
        for b in self._nav_buttons:
            b.setChecked(b is btn)
        self.stack.setCurrentIndex(index)
