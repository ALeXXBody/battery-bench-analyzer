"""Main application window: sidebar navigation + stacked pages."""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core.bench import SessionHolder
from .app import resource_path
from .connection import ConnectionBar
from .pages.analyze import AnalyzePage
from .pages.help import HelpPage
from .pages.regen import RegenPage
from .pages.unlock import UnlockPage


class _UpdateBridge(QObject):
    """Marshals updater worker-thread events onto the GUI thread.

    Qt widgets must only be touched from the GUI thread; the updater runs in
    plain Python threads, so its results travel through these signals.
    """

    result = Signal(object)
    download_progress = Signal(int, int)
    download_done = Signal(object)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Battery Bench Analyzer")
        self.resize(1100, 720)
        icon_path = resource_path("icon.ico")
        if os.path.exists(icon_path):
            from PySide6.QtGui import QIcon

            self.setWindowIcon(QIcon(icon_path))

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

        title = QLabel("Battery Bench Analyzer")
        title.setObjectName("appTitle")
        side.addWidget(title)

        ver = QLabel(f"v{__version__}")
        ver.setObjectName("appVersion")
        side.addWidget(ver)

        self.stack = QStackedWidget()
        self.bench = SessionHolder()
        self.conn_bar = ConnectionBar(self.bench)
        self.analyze_page = AnalyzePage(self.bench, self.conn_bar)
        self.regen_page = RegenPage(self.bench)
        self.unlock_page = UnlockPage(self.bench)
        self.help_page = HelpPage()
        self.stack.addWidget(self.analyze_page)
        self.stack.addWidget(self.regen_page)
        self.stack.addWidget(self.unlock_page)
        self.stack.addWidget(self.help_page)

        self._nav_buttons: list[QPushButton] = []
        nav_items = [
            ("Analyze", 0),
            ("Regenerate", 1),
            ("Repair / Unlock", 2),
            ("Help", 3),
        ]
        for label, idx in nav_items:
            btn = QPushButton(label)
            btn.setObjectName("navButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("pageIndex", idx)
            btn.clicked.connect(lambda _=False, i=idx, b=btn: self._go(i, b))
            side.addWidget(btn)
            self._nav_buttons.append(btn)
        side.addStretch(1)

        self.update_btn = QPushButton("Check for updates")
        self.update_btn.setObjectName("navButton")
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.clicked.connect(lambda: self.trigger_update_check(silent=False))
        side.addWidget(self.update_btn)

        root.addWidget(sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.conn_bar)
        content_layout.addWidget(self.stack, 1)
        root.addWidget(content, 1)

        self._go(0, self._nav_buttons[0])

        self._update_bridge = _UpdateBridge()
        self._update_bridge.result.connect(self._on_update_result)
        self._update_bridge.download_progress.connect(self._on_download_progress)
        self._update_bridge.download_done.connect(self._on_download_done)
        self._update_silent = False
        self._update_test = False

    def _go(self, index: int, btn: QPushButton) -> None:
        for b in self._nav_buttons:
            b.setChecked(b is btn)
        self.stack.setCurrentIndex(index)

    # -- auto-update --

    def trigger_update_check(self, silent: bool = False) -> None:
        from ..updater import check_in_background

        self._update_test = os.environ.get("BATTERYBENCH_SELFTEST") == "update"
        self._update_silent = silent
        self.update_btn.setEnabled(False)
        if not silent:
            self.update_btn.setText("Checking…")
        # Emitting a signal is thread-safe; the slot runs on the GUI thread.
        check_in_background(self._update_bridge.result.emit)

    def _finish_update_test(self, outcome: str) -> None:
        try:
            with open("selftest_ok.txt", "w", encoding="ascii") as f:
                f.write(f"update-test {outcome}\n")
        except OSError:
            pass
        threading.Thread(target=self._delayed_exit, daemon=True).start()

    @staticmethod
    def _delayed_exit() -> None:
        import time

        time.sleep(0.5)
        os._exit(0)

    def _on_update_result(self, result) -> None:
        self.update_btn.setEnabled(True)
        self.update_btn.setText("Check for updates")

        if result.error:
            outcome = f"error: {result.error}"
        elif result.update is not None and result.is_newer:
            outcome = (
                f"update-available: {result.update.latest_version} "
                f"({len(result.update.installers)} installer asset(s))"
            )
        else:
            outcome = "up-to-date"

        if self._update_test:
            self._finish_update_test(outcome)
            return

        if result.error:
            if not self._update_silent:
                QMessageBox.information(
                    self,
                    "Battery Bench Analyzer",
                    f"Could not check for updates: {result.error}",
                )
            return
        if not result.update or not result.is_newer:
            if not self._update_silent:
                QMessageBox.information(
                    self,
                    "Battery Bench Analyzer",
                    f"You're on the latest version (v{__version__}).",
                )
            return

        info = result.update
        if not info.installers:
            QMessageBox.information(
                self,
                "Battery Bench Analyzer",
                f"Battery Bench Analyzer {info.latest_version} is available.\n"
                f"Open {info.release_url} to download it.",
            )
            return

        ret = QMessageBox.question(
            self,
            "Update available",
            f"Battery Bench Analyzer {info.latest_version} is available "
            f"(you have v{__version__}).\n\n"
            f"Download and run the installer now?",
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._download_and_install(info.installers[0])

    def _download_and_install(self, url: str) -> None:
        from ..updater import download

        self.update_btn.setEnabled(False)
        self.update_btn.setText("Downloading… 0%")

        def work():
            try:
                path = download(
                    url,
                    progress=lambda total, done: (
                        self._update_bridge.download_progress.emit(
                            int(total), int(done)
                        )
                    ),
                )
                self._update_bridge.download_done.emit(path)
            except Exception as exc:  # noqa: BLE001
                self._update_bridge.download_done.emit(exc)

        threading.Thread(target=work, daemon=True).start()

    def _on_download_progress(self, total: int, done: int) -> None:
        pct = int(done * 100 / total) if total else 0
        self.update_btn.setText(f"Downloading… {pct}%")

    def _on_download_done(self, payload) -> None:
        self.update_btn.setEnabled(True)
        self.update_btn.setText("Check for updates")
        if isinstance(payload, Exception):
            QMessageBox.warning(self, "Download failed", str(payload))
            return
        from ..updater import launch_installer

        if launch_installer(payload):
            QMessageBox.information(
                self,
                "Updater",
                "Downloaded. The installer has been launched.\n\n"
                "Battery Bench Analyzer will close and be replaced by the new version.",
            )
            self.close()
        else:
            QMessageBox.information(
                self, "Updater", f"Saved installer to:\n{payload}\n\nRun it to install."
            )
