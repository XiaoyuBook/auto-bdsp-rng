from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu

from auto_bdsp_rng.ui.about_dialog import AboutDialog
from auto_bdsp_rng.ui.markdown_viewer import MarkdownViewerDialog, read_markdown_text
from auto_bdsp_rng.ui.sponsor_dialog import SponsorDialog


PROJECT_REPOSITORY_URL = "https://github.com/XiaoyuBook/auto-bdsp-rng"
SPONSORS_URL = "https://github.com/XiaoyuBook/auto-bdsp-rng/blob/main/SPONSORS.md"
AUTHOR_GITHUB_URL = "https://github.com/XiaoyuBook"
BILIBILI_URL = "https://space.bilibili.com/269020915?spm_id_from=333.1007.0.0"
TUTORIAL_URL = "https://skrxiaoyu.com/2026/05/14/%E7%8F%8D%E9%92%BB%E5%A4%8D%E5%88%BB%E5%AE%9A%E7%82%B9%E8%87%AA%E5%8A%A8%E4%B9%B1%E6%95%B0/"
AUTHOR_EMAIL = "kesong2003@qq.com"


class HelpMenuController:
    def __init__(
        self,
        window: QMainWindow,
        *,
        open_url: Callable[[str], object] | None = None,
        copy_text: Callable[[str], object] | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.window = window
        self.open_url = open_url or self._open_url
        self.copy_text = copy_text or self._copy_text
        self.project_root = project_root or Path(__file__).resolve().parents[3]

    def install(self) -> QMenu:
        self.help_menu = self.window.menuBar().addMenu("帮助")

        self.tutorial_action = QAction("使用教程", self.window)
        self.tutorial_action.triggered.connect(lambda: self.open_url(TUTORIAL_URL))
        self.help_menu.addAction(self.tutorial_action)

        self.about_action = QAction("关于项目", self.window)
        self.about_action.triggered.connect(self.show_about)
        self.help_menu.addAction(self.about_action)

        self.contact_menu = self.help_menu.addMenu("作者联系")
        self.email_action = QAction(f"邮箱：{AUTHOR_EMAIL}", self.window)
        self.email_action.triggered.connect(self.copy_author_email)
        self.contact_menu.addAction(self.email_action)

        self.bilibili_action = QAction("B站主页", self.window)
        self.bilibili_action.triggered.connect(lambda: self.open_url(BILIBILI_URL))
        self.contact_menu.addAction(self.bilibili_action)

        self.github_profile_action = QAction("GitHub主页", self.window)
        self.github_profile_action.triggered.connect(lambda: self.open_url(AUTHOR_GITHUB_URL))
        self.contact_menu.addAction(self.github_profile_action)

        self.support_action = QAction("支持项目", self.window)
        self.support_action.triggered.connect(self.show_sponsor)
        self.contact_menu.addAction(self.support_action)

        self.changelog_action = QAction("更新日志", self.window)
        self.changelog_action.triggered.connect(self.show_changelog)
        self.help_menu.addAction(self.changelog_action)
        return self.help_menu

    def copy_author_email(self) -> None:
        self.copy_text(AUTHOR_EMAIL)
        status = self.window.statusBar()
        if status is not None:
            status.showMessage("邮箱已复制", 3000)

    def show_about(self) -> None:
        dialog = AboutDialog(
            self.window,
            open_source=lambda: self.open_url(PROJECT_REPOSITORY_URL),
            open_sponsors=lambda: self.open_url(SPONSORS_URL),
        )
        dialog.exec()

    def show_sponsor(self) -> None:
        dialog = SponsorDialog(self.window)
        dialog.exec()

    def show_changelog(self) -> None:
        text = read_markdown_text(self.project_root / "CHANGELOG.md")
        dialog = MarkdownViewerDialog("更新日志", text, self.window)
        dialog.exec()

    @staticmethod
    def _open_url(url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    @staticmethod
    def _copy_text(text: str) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
