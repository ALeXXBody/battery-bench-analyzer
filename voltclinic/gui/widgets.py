"""Small reusable card / widget builders for the GUI."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


def card(parent: QWidget | None = None) -> QFrame:
    f = QFrame(parent)
    f.setProperty("card", True)
    return f


class StatCard(QFrame):
    """A titled card whose big value can be updated via set_value()."""

    def __init__(self, title: str, value: str = "—", accent: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("card", True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        t = QLabel(title.upper())
        t.setObjectName("cardTitle")
        self._value = QLabel(value)
        self._value.setObjectName("bigValue" if accent else "valueLabel")
        self._value.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(self._value)

    def set_value(self, text: str) -> None:
        self._value.setText(text)


def stat_card(title: str, value: str = "—", accent: bool = False) -> QFrame:
    return StatCard(title, value, accent=accent)


def stat_row(field: str, grid: QGridLayout, row: int,
             value: str = "—") -> None:
    lbl = QLabel(field)
    lbl.setObjectName("cardTitle")
    val = QLabel(value)
    val.setObjectName("valueLabel")
    val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    grid.addWidget(lbl, row, 0, Qt.AlignLeft)
    grid.addWidget(val, row, 1, Qt.AlignRight)
