"""Application entry for the PySide6 GUI.

Importing this module is guarded so that CLI-only use on non-GUI systems
(including this dev host) does not fail. The GUI code is only imported when
`python -m battery_clinic` runs on a machine with Qt available.
"""
from __future__ import annotations


def main() -> int:
    import sys
    from PySide6.QtWidgets import QApplication

    from .window import MainWindow
    from .theme import DARK_THEME

    app = QApplication(sys.argv)
    app.setApplicationName("BatteryClinic")
    app.setOrganizationName("BatteryClinic")
    app.setStyleSheet(DARK_THEME)

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
