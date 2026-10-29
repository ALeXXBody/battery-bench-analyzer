"""Modern dark theme for the VoltClinic GUI (QSS)."""

from __future__ import annotations

DARK_THEME = """
QWidget {
    background-color: #1e1f24;
    color: #e6e6e6;
    font-family: 'Segoe UI', sans-serif;
    font-size: 14px;
}
QMainWindow, #rootWidget {
    background-color: #1e1f24;
}
#sidebar {
    background-color: #15161a;
    border-right: 1px solid #2c2e35;
}
QPushButton#navButton {
    background: transparent;
    border: none;
    color: #a8a8b0;
    text-align: left;
    padding: 12px 18px;
    font-size: 15px;
}
QPushButton#navButton:hover {
    background-color: #23252c;
    color: #ffffff;
}
QPushButton#navButton:checked {
    background-color: #2f3240;
    color: #4cc2ff;
    border-left: 3px solid #4cc2ff;
    padding-left: 15px;
}
#appTitle {
    font-size: 20px;
    font-weight: 600;
    color: #ffffff;
    padding: 18px;
}
#appVersion {
    font-size: 12px;
    color: #6a6f7a;
    padding: 0 18px 12px 18px;
}
#content {
    background-color: #1e1f24;
}
QLabel#cardTitle {
    font-size: 13px;
    font-weight: 600;
    color: #9aa0aa;
    letter-spacing: 1px;
}
#valueLabel {
    font-size: 14px;
    color: #e6e6e6;
}
#bigValue {
    font-size: 34px;
    font-weight: 700;
    color: #4cc2ff;
}
QFrame[card="true"] {
    background-color: #262830;
    border: 1px solid #2f313a;
    border-radius: 12px;
}
QLabel#sectionHeader {
    font-size: 22px;
    font-weight: 600;
    color: #ffffff;
}
QPushButton {
    background-color: #2c3040;
    border: 1px solid #3a3e50;
    border-radius: 8px;
    padding: 10px 18px;
    color: #ffffff;
    font-size: 14px;
}
QPushButton:hover {
    background-color: #363b50;
}
QPushButton:disabled {
    color: #666;
    background-color: #22242b;
    border-color: #2a2c33;
}
QPushButton#primary {
    background-color: #1677ff;
    border-color: #1677ff;
}
QPushButton#primary:hover {
    background-color: #3b92ff;
}
QPushButton#danger {
    background-color: #c0392b;
    border-color: #c0392b;
}
QProgressBar {
    background-color: #2f313a;
    border-radius: 8px;
    text-align: center;
    color: #ffffff;
    height: 20px;
}
QProgressBar::chunk {
    background-color: #4cc2ff;
    border-radius: 8px;
}
QTableWidget {
    background-color: #262830;
    alternate-background-color: #22242b;
    border: 1px solid #2f313a;
    border-radius: 8px;
    gridline-color: #33363f;
}
QHeaderView::section {
    background-color: #2c2e35;
    color: #9aa0aa;
    border: none;
    padding: 6px;
    font-weight: 600;
}
QScrollBar:vertical {
    background: #1e1f24;
    width: 10px;
}
QScrollBar::handle:vertical {
    background: #3a3e50;
    border-radius: 5px;
    min-height: 30px;
}
QToolTip {
    background-color: #2f313a;
    color: #e6e6e6;
    border: 1px solid #3a3e50;
}
"""
