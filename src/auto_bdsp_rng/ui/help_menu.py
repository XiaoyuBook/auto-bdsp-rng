from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QPushButton, QToolButton

from auto_bdsp_rng.resources import app_base_dir
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
        run_log_enabled: bool = False,
        set_run_log_enabled: Callable[[bool], bool] | None = None,
        open_run_log_dir: Callable[[], object] | None = None,
        check_updates: Callable[[], object] | None = None,
        auto_update_check_enabled: bool = True,
        set_auto_update_check_enabled: Callable[[bool], bool] | None = None,
    ) -> None:
        self.window = window
        self.open_url = open_url or self._open_url
        self.copy_text = copy_text or self._copy_text
        self.project_root = project_root or app_base_dir()
        self._run_log_enabled = bool(run_log_enabled)
        self.set_run_log_enabled = set_run_log_enabled
        self.open_run_log_dir = open_run_log_dir
        self.check_updates = check_updates
        self._auto_update_check_enabled = bool(auto_update_check_enabled)
        self.set_auto_update_check_enabled = set_auto_update_check_enabled

    def install(self, button: QToolButton | QPushButton | None = None) -> QMenu:
        self.help_menu = self._build_menu(parent=button or self.window)
        if button is None:
            self.window.menuBar().addMenu(self.help_menu)
        else:
            button.setMenu(self.help_menu)
            if isinstance(button, QToolButton):
                button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        return self.help_menu

    def _build_menu(self, parent) -> QMenu:
        menu = QMenu("帮助", parent)

        self.tutorial_action = QAction("使用教程", self.window)
        self.tutorial_action.triggered.connect(lambda: self.open_url(TUTORIAL_URL))
        menu.addAction(self.tutorial_action)

        self.about_action = QAction("关于项目", self.window)
        self.about_action.triggered.connect(self.show_about)
        menu.addAction(self.about_action)

        self.source_action = QAction("GitHub源码", self.window)
        self.source_action.triggered.connect(lambda: self.open_url(PROJECT_REPOSITORY_URL))
        menu.addAction(self.source_action)

        self.changelog_action = QAction("更新日志", self.window)
        self.changelog_action.triggered.connect(self.show_changelog)
        menu.addAction(self.changelog_action)

        self.check_updates_action = QAction("检查更新…", self.window)
        self.check_updates_action.setEnabled(self.check_updates is not None)
        self.check_updates_action.triggered.connect(self._check_updates)
        menu.addAction(self.check_updates_action)

        self.auto_update_check_action = QAction("启动时自动检查更新", self.window)
        self.auto_update_check_action.setCheckable(True)
        self.auto_update_check_action.setChecked(self._auto_update_check_enabled)
        self.auto_update_check_action.triggered.connect(self._set_auto_update_check_enabled)
        menu.addAction(self.auto_update_check_action)

        menu.addSeparator()
        self.run_log_menu = QMenu("运行日志", self.window)
        self.run_log_save_action = QAction("自动保存运行日志（保留 7 天）", self.window)
        self.run_log_save_action.setCheckable(True)
        self.run_log_save_action.setChecked(self._run_log_enabled)
        self.run_log_save_action.triggered.connect(self._set_run_log_save_enabled)
        self.run_log_menu.addAction(self.run_log_save_action)

        self.open_run_log_dir_action = QAction("打开日志目录", self.window)
        self.open_run_log_dir_action.triggered.connect(self._open_run_log_dir)
        self.run_log_menu.addAction(self.open_run_log_dir_action)
        menu.addMenu(self.run_log_menu)

        menu.addSeparator()
        self.contact_menu = QMenu("作者联系", self.window)
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
        menu.addMenu(self.contact_menu)
        return menu

    def _set_run_log_save_enabled(self, enabled: bool) -> None:
        previous = self._run_log_enabled
        try:
            actual = (
                bool(enabled)
                if self.set_run_log_enabled is None
                else bool(self.set_run_log_enabled(bool(enabled)))
            )
        except Exception:
            actual = previous
        self.set_run_log_state(actual)

    def set_run_log_state(self, enabled: bool) -> None:
        self._run_log_enabled = bool(enabled)
        self.run_log_save_action.blockSignals(True)
        self.run_log_save_action.setChecked(self._run_log_enabled)
        self.run_log_save_action.blockSignals(False)

    def _open_run_log_dir(self) -> None:
        if self.open_run_log_dir is not None:
            self.open_run_log_dir()

    def _check_updates(self) -> None:
        if self.check_updates is not None:
            self.check_updates()

    def _set_auto_update_check_enabled(self, enabled: bool) -> None:
        previous = self._auto_update_check_enabled
        try:
            actual = (
                bool(enabled)
                if self.set_auto_update_check_enabled is None
                else bool(self.set_auto_update_check_enabled(bool(enabled)))
            )
        except Exception:
            actual = previous
        self._auto_update_check_enabled = actual
        self.auto_update_check_action.blockSignals(True)
        self.auto_update_check_action.setChecked(actual)
        self.auto_update_check_action.blockSignals(False)

    def copy_author_email(self) -> None:
        self.copy_text(AUTHOR_EMAIL)
        status = self.window.statusBar()
        if status is not None:
            status.showMessage("邮箱已复制", 3000)

    def show_about(self) -> None:
        dialog = AboutDialog(
            self.window,
            open_source=lambda: self.open_url(PROJECT_REPOSITORY_URL),
            open_sponsors=self.show_sponsors,
            copy_text=self.copy_text,
        )
        dialog.exec()

    def show_sponsor(self) -> None:
        dialog = SponsorDialog(self.window)
        dialog.exec()

    def show_changelog(self) -> None:
        text = read_markdown_text(self.project_root / "CHANGELOG.md")
        dialog = MarkdownViewerDialog("更新日志", text, self.window)
        dialog.exec()

    def show_sponsors(self) -> None:
        text = read_markdown_text(self.project_root / "SPONSORS.md")
        dialog = MarkdownViewerDialog("赞助名单", text, self.window)
        dialog.exec()

    @staticmethod
    def _open_url(url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    @staticmethod
    def _copy_text(text: str) -> None:
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
