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

from .. import __version__


def _selftest_exit(delay_s: float, pid: int) -> None:
    import time

    time.sleep(delay_s)
    try:
        with open("selftest_ok.txt", "w", encoding="ascii") as f:
            f.write(f"ok pid={pid} ver={__version__}\n")
    except OSError:
        pass
    os._exit(0)


def _install_crash_hook() -> None:
    """Route every uncaught Python error into diag.log + a user dialog."""
    from ..core.diaglog import log

    def hook(kind, value, tb):
        import traceback

        detail = "".join(traceback.format_exception(kind, value, tb))
        log("UNCAUGHT EXCEPTION:", *detail.rstrip().splitlines())
        try:
            from PySide6.QtWidgets import QMessageBox

            box = QMessageBox(
                QMessageBox.Critical, "VoltClinic error", f"{kind.__name__}: {value}"
            )
            box.setDetailedText(detail)
            box.exec()
        except Exception:  # noqa: BLE001 - dialog unavailable (headless)
            pass

    sys.excepthook = hook


def resource_path(name: str) -> str:
    """Resolve a bundled resource path in source and frozen (PyInstaller) modes."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, name)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "assets", name)


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .theme import DARK_THEME
    from .window import MainWindow

    _install_crash_hook()

    from ..core.diaglog import log as diag

    diag(
        f"VoltClinic {__version__} started (frozen={bool(getattr(sys, 'frozen', False))})"
    )

    app = QApplication(sys.argv)
    app.setApplicationName("VoltClinic")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("VoltClinic")
    app.setStyleSheet(DARK_THEME)
    icon_path = resource_path("icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    win = MainWindow()
    win.show()

    selftest = os.environ.get("BATTERYCLINIC_SELFTEST", "")
    if selftest == "update":
        # Headless update-flow test: click the real button, the window logs
        # the outcome to selftest_ok.txt and exits (see MainWindow).
        QTimer.singleShot(300, win.update_btn.click)
    elif selftest:
        threading.Thread(
            target=_selftest_exit, args=(3.0, os.getpid()), daemon=True
        ).start()
    else:
        win.trigger_update_check(silent=True)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
