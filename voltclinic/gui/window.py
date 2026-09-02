"""Main application window: sidebar navigation + stacked pages."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
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
from .pages.analyze import AnalyzePage
from .pages.regen import RegenPage
from .pages.unlock import UnlockPage


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VoltClinic")
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

        title = QLabel("VoltClinic")
        title.setObjectName("appTitle")
        side.addWidget(title)

        ver = QLabel(f"v{__version__}")
        ver.setObjectName("appVersion")
        side.addWidget(ver)

        self.stack = QStackedWidget()
        self.bench = SessionHolder()
        self.analyze_page = AnalyzePage(self.bench)
        self.regen_page = RegenPage(self.bench)
        self.unlock_page = UnlockPage(self.bench)
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
        root.addWidget(self.stack, 1)

        self._go(0, self._nav_buttons[0])

    def _go(self, index: int, btn: QPushButton) -> None:
        for b in self._nav_buttons:
            b.setChecked(b is btn)
        self.stack.setCurrentIndex(index)

    # -- auto-update --

    def trigger_update_check(self, silent: bool = False) -> None:
        from ..updater import CheckResult, check_in_background
        self.update_btn.setEnabled(False)
        if not silent:
            self.update_btn.setText("Checking…")
        self._update_silent = silent

        def done(result: CheckResult) -> None:
            self.update_btn.setEnabled(True)
            self.update_btn.setText("Check for updates")
            self._on_update_result(result)

        check_in_background(done)

    def _on_update_result(self, result) -> None:
        if result.error:
            if not self._update_silent:
                QMessageBox.information(self, "VoltClinic",
                                        f"Could not check for updates: {result.error}")
            return
        if not result.update or not result.is_newer:
            if not self._update_silent:
                QMessageBox.information(self, "VoltClinic",
                                        f"You're on the latest version (v{__version__}).")
            return

        info = result.update
        if not info.installers:
            QMessageBox.information(self, "VoltClinic",
                                    f"VoltClinic {info.latest_version} is available.\n"
                                    f"Open {info.release_url} to download it.")
            return

        ret = QMessageBox.question(
            self, "Update available",
            f"VoltClinic {info.latest_version} is available "
            f"(you have v{__version__}).\n\n"
            f"Download and run the installer now?")
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._download_and_install(info.installers[0])

    def _download_and_install(self, url: str) -> None:
        from ..updater import download, launch_installer
        self.update_btn.setEnabled(False)
        self.update_btn.setText("Downloading…")
        try:
            path = download(url, progress=None)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Download failed", str(exc))
            self.update_btn.setEnabled(True)
            self.update_btn.setText("Check for updates")
            return
        self.update_btn.setEnabled(True)
        self.update_btn.setText("Check for updates")
        if launch_installer(path):
            QMessageBox.information(
                self, "Updater",
                "Downloaded. The installer has been launched.\n\n"
                "VoltClinic will close and be replaced by the new version.")
            self.close()
        else:
            QMessageBox.information(
                self, "Updater", f"Saved installer to:\n{path}\n\nRun it to install.")

