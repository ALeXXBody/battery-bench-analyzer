"""Application entry for the PySide6 GUI.

BATTERYCLINIC_SELFTEST=1 makes the app start, render the main window, then
exit cleanly after a moment and write ``selftest_ok.txt`` to the CWD.
This allows headless verification (QT_QPA_PLATFORM=offscreen) of packaged
builds without a display. The quit is driven from a plain Python thread
because some headless setups (wine/offscreen) do not dispatch Qt timers.
"""
from __future__ import annotations

import os
import sys
import threading


def _selftest_exit(delay_s: float, pid: int) -> None:
    import time
    time.sleep(delay_s)
    try:
        with open("selftest_ok.txt", "w", encoding="ascii") as f:
            f.write(f"ok pid={pid}\n")
    except OSError:
        pass
    os._exit(0)


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from .theme import DARK_THEME
    from .window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("BatteryClinic")
    app.setOrganizationName("BatteryClinic")
    app.setStyleSheet(DARK_THEME)

    win = MainWindow()
    win.show()

    if os.environ.get("BATTERYCLINIC_SELFTEST"):
        threading.Thread(target=_selftest_exit, args=(3.0, os.getpid()),
                         daemon=True).start()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
