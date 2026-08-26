from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices, QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng import __version__
from auto_bdsp_rng.resources import app_path
from auto_bdsp_rng.update_core import parse_version
from auto_bdsp_rng.ui.markdown_viewer import read_markdown_text


class UpdatePlanLike(Protocol):
    current_version: str
    latest_version: str
    release_url: str
    release_notes: str
    update_available: bool
    incremental_available: bool
    download_size: int
    assets: tuple[object, ...]


CheckUpdates = Callable[[str], UpdatePlanLike]
DownloadUpdate = Callable[[UpdatePlanLike, Callable[[int, int], None], threading.Event], tuple[Path, ...]]
LaunchInstaller = Callable[[tuple[Path, ...], str, str, tuple[str, ...]], object]
InstallerAvailable = Callable[[], bool]
OpenUrl = Callable[[str], object]


def _default_check_updates(current_version: str) -> UpdatePlanLike:
    from auto_bdsp_rng.update_service import check_for_updates

    return check_for_updates(current_version)


def _default_download_update(
    plan: UpdatePlanLike,
    progress_callback: Callable[[int, int], None],
    cancel_event: threading.Event,
) -> tuple[Path, ...]:
    from auto_bdsp_rng.update_service import download_update_assets

    return download_update_assets(plan, progress_callback, cancel_event)


def _default_launch_installer(
    patches: tuple[Path, ...],
    current_version: str,
    target_version: str,
    asset_digests: tuple[str, ...],
) -> object:
    from auto_bdsp_rng.update_service import launch_update_installer

    return launch_update_installer(patches, current_version, target_version, asset_digests)


def _default_installer_available() -> bool:
    from auto_bdsp_rng.update_service import has_bundled_updater

    return has_bundled_updater()


def _format_size(size: int) -> str:
    value = max(0, int(size))
    if value < 1024:
        return f"{value} B"
    if value < 1024**2:
        return f"{value / 1024:.1f} KiB"
    if value < 1024**3:
        return f"{value / 1024**2:.1f} MiB"
    return f"{value / 1024**3:.2f} GiB"


class UpdateDialog(QDialog):
    downloadRequested = Signal()
    openReleaseRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, parent: QWidget | None = None, *, changelog_text: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("软件更新")
        self.setMinimumWidth(560)
        self.resize(640, 500)
        self.setModal(False)
        self._busy = False
        self._changelog_text = (
            read_markdown_text(app_path("CHANGELOG.md"))
            if changelog_text is None
            else str(changelog_text)
        )
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        self.status_label = QLabel("正在检查更新…")
        self.status_label.setObjectName("UpdateStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(self.status_label)

        versions = QGridLayout()
        versions.setHorizontalSpacing(12)
        versions.setVerticalSpacing(6)
        versions.addWidget(QLabel("当前版本"), 0, 0)
        self.current_version_label = QLabel("-")
        self.current_version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        versions.addWidget(self.current_version_label, 0, 1)
        versions.addWidget(QLabel("线上最新版本"), 1, 0)
        self.latest_version_label = QLabel("-")
        self.latest_version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        versions.addWidget(self.latest_version_label, 1, 1)
        versions.setColumnStretch(1, 1)
        layout.addLayout(versions)

        self.package_label = QLabel()
        self.package_label.setWordWrap(True)
        layout.addWidget(self.package_label)

        self.notes_label = QLabel("更新日志")
        self.notes_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.notes_label)

        self.notes_tabs = QTabWidget()
        self.release_notes = self._create_notes_browser("该版本未提供更新说明")
        self.history_notes = self._create_notes_browser("暂无更新日志")
        self.notes_tabs.addTab(self.release_notes, "新版内容")
        self.notes_tabs.addTab(self.history_notes, "历史更新")
        self.history_notes.document().setMarkdown(
            self._changelog_text,
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            | QTextDocument.MarkdownFeature.MarkdownNoHTML,
        )
        self.notes_tabs.setMinimumHeight(190)
        layout.addWidget(self.notes_tabs, 1)
        self._set_release_notes_available(False)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.release_button = QPushButton("打开 Release 页面")
        self.release_button.clicked.connect(self.openReleaseRequested.emit)
        self.release_button.hide()
        buttons.addWidget(self.release_button)
        self.download_button = QPushButton("下载并安装")
        self.download_button.clicked.connect(self.downloadRequested.emit)
        self.download_button.hide()
        buttons.addWidget(self.download_button)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    @staticmethod
    def _create_notes_browser(placeholder: str) -> QTextBrowser:
        browser = QTextBrowser()
        browser.setReadOnly(True)
        browser.setOpenLinks(False)
        browser.setOpenExternalLinks(False)
        browser.setPlaceholderText(placeholder)
        browser.document().setDefaultStyleSheet(
            "h1 { font-size: 16px; font-weight: 600; margin: 10px 0 6px 0; }"
            "h2 { font-size: 15px; font-weight: 600; margin: 8px 0 5px 0; }"
            "h3 { font-size: 14px; font-weight: 600; margin: 7px 0 4px 0; }"
        )
        return browser

    def _set_release_notes_available(self, available: bool) -> None:
        index = self.notes_tabs.indexOf(self.release_notes)
        if available and index < 0:
            self.notes_tabs.insertTab(0, self.release_notes, "新版内容")
        elif not available and index >= 0:
            self.notes_tabs.removeTab(index)
        if available:
            self.notes_tabs.setCurrentWidget(self.release_notes)
        else:
            self.notes_tabs.setCurrentWidget(self.history_notes)

    def show_checking(self, current_version: str) -> None:
        self._busy = True
        self.status_label.setText("正在检查更新…")
        self.current_version_label.setText(current_version)
        self.latest_version_label.setText("-")
        self.package_label.clear()
        self.release_notes.clear()
        self._set_release_notes_available(False)
        self.progress_bar.hide()
        self.download_button.hide()
        self.download_button.setEnabled(False)
        self.release_button.hide()
        self.release_button.setEnabled(True)
        self.close_button.setText("取消")

    def show_plan(self, plan: UpdatePlanLike, *, install_unavailable_reason: str | None = None) -> None:
        self._busy = False
        self.current_version_label.setText(str(plan.current_version))
        self.latest_version_label.setText(str(plan.latest_version))
        self.release_notes.clear()
        self.progress_bar.hide()
        self.release_button.setEnabled(True)
        self.close_button.setText("关闭")

        if not plan.update_available:
            if parse_version(str(plan.current_version)) > parse_version(str(plan.latest_version)):
                self.status_label.setText("当前版本高于线上最新正式版")
            else:
                self.status_label.setText("当前已是最新版本")
            self.package_label.clear()
            self.download_button.hide()
            self.release_button.hide()
            self._set_release_notes_available(False)
            return

        self.release_notes.document().setMarkdown(
            str(plan.release_notes or ""),
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            | QTextDocument.MarkdownFeature.MarkdownNoHTML,
        )
        self._set_release_notes_available(True)
        self.release_button.setVisible(bool(plan.release_url))
        if not plan.incremental_available:
            self.status_label.setText("检测到新版本，但没有适用于当前版本的增量升级链")
            self.package_label.setText("请前往 Release 页面下载完整版本。")
            self.download_button.hide()
            return

        self.package_label.setText(f"增量升级包：{_format_size(plan.download_size)}")
        self.download_button.show()
        if install_unavailable_reason:
            self.status_label.setText(install_unavailable_reason)
            self.download_button.setEnabled(False)
        else:
            self.status_label.setText("发现可用的新版本")
            self.download_button.setEnabled(True)

    def show_downloading(self) -> None:
        self._busy = True
        self.status_label.setText("正在下载并校验增量升级包…")
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self.download_button.setEnabled(False)
        self.release_button.setEnabled(False)
        self.close_button.setText("取消")

    def set_download_progress(self, downloaded: int, total: int) -> None:
        if total <= 0:
            self.progress_bar.setRange(0, 0)
            return
        percent = round(max(0, min(int(downloaded), int(total))) * 100 / int(total))
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(percent)
        self.progress_bar.setFormat("%p%")

    def show_installing(self) -> None:
        self._busy = True
        self.status_label.setText("下载完成，正在启动升级程序…")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(100)

    def show_error(self, message: str) -> None:
        self._busy = False
        self.status_label.setText(f"更新失败：{message}")
        self.progress_bar.hide()
        self.download_button.hide()
        self.release_button.setEnabled(True)
        self.close_button.setText("关闭")

    def reject(self) -> None:
        if self._busy:
            self.cancelRequested.emit()
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._busy:
            self.cancelRequested.emit()
        super().closeEvent(event)


class _CheckThread(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, current_version: str, check_updates: CheckUpdates, parent: QObject) -> None:
        super().__init__(parent)
        self._current_version = current_version
        self._check_updates = check_updates
        self._cancelled = threading.Event()

    def run(self) -> None:
        try:
            plan = self._check_updates(self._current_version)
        except Exception as exc:
            if not self._cancelled.is_set():
                self.failed.emit(str(exc))
        else:
            if not self._cancelled.is_set():
                self.completed.emit(plan)

    def stop(self) -> None:
        self._cancelled.set()


class _DownloadThread(QThread):
    progressChanged = Signal(int, int)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, plan: UpdatePlanLike, download_update: DownloadUpdate, parent: QObject) -> None:
        super().__init__(parent)
        self._plan = plan
        self._download_update = download_update
        self._cancel_event = threading.Event()

    def run(self) -> None:
        try:
            patches = self._download_update(
                self._plan,
                self.progressChanged.emit,
                self._cancel_event,
            )
        except Exception as exc:
            if self._cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(str(exc))
        else:
            if self._cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.completed.emit(tuple(Path(path) for path in patches))

    def stop(self) -> None:
        self._cancel_event.set()


class UpdateController(QObject):
    busyChanged = Signal(bool)

    def __init__(
        self,
        window: QMainWindow,
        *,
        current_version: str = __version__,
        check_updates: CheckUpdates | None = None,
        download_update: DownloadUpdate | None = None,
        launch_installer: LaunchInstaller | None = None,
        installer_available: InstallerAvailable | None = None,
        frozen: bool | None = None,
        open_url: OpenUrl | None = None,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.current_version = current_version
        self._check_updates = check_updates or _default_check_updates
        self._download_update = download_update or _default_download_update
        self._launch_installer = launch_installer or _default_launch_installer
        self._installer_available = installer_available or _default_installer_available
        self._frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
        self._open_url = open_url or self._open_release_url
        self._plan: UpdatePlanLike | None = None
        self._check_thread: _CheckThread | None = None
        self._download_thread: _DownloadThread | None = None
        self._downloaded_patches: tuple[Path, ...] | None = None
        self._download_cancel_requested = False
        self.dialog = UpdateDialog(window)
        self.dialog.downloadRequested.connect(self._start_download)
        self.dialog.openReleaseRequested.connect(self._open_current_release)
        self.dialog.cancelRequested.connect(self.cancel)

    def check_for_updates(self) -> None:
        if self._operation_running():
            self._show_dialog()
            return
        self._plan = None
        self.dialog.show_checking(self.current_version)
        self._show_dialog()
        thread = _CheckThread(self.current_version, self._check_updates, self)
        thread.completed.connect(self._check_completed)
        thread.failed.connect(self._operation_failed)
        thread.finished.connect(lambda owned=thread: self._check_thread_finished(owned))
        thread.finished.connect(thread.deleteLater)
        self._check_thread = thread
        self.busyChanged.emit(True)
        thread.start()

    def cancel(self) -> None:
        self._downloaded_patches = None
        if self._check_thread is not None:
            self._check_thread.stop()
        if self._download_thread is not None:
            self._download_cancel_requested = True
            self._download_thread.stop()

    def shutdown(self, *, wait_ms: int = 12_000) -> bool:
        self.cancel()
        stopped = True
        for thread in (self._check_thread, self._download_thread):
            if thread is None or not thread.isRunning():
                continue
            if not thread.wait(wait_ms):
                stopped = False
        return stopped

    @Slot(object)
    def _check_completed(self, plan: UpdatePlanLike) -> None:
        self._plan = plan
        unavailable_reason: str | None = None
        if plan.update_available and plan.incremental_available:
            if not self._frozen:
                unavailable_reason = "源码运行模式不支持应用内安装，请前往 Release 页面更新"
            elif not self._installer_available():
                unavailable_reason = "当前构建未包含升级程序，请前往 Release 页面更新"
        self.dialog.show_plan(plan, install_unavailable_reason=unavailable_reason)

    @Slot()
    def _start_download(self) -> None:
        plan = self._plan
        if plan is None or not plan.update_available or not plan.incremental_available:
            return
        if not self._frozen or not self._installer_available() or self._download_thread is not None:
            return
        self._downloaded_patches = None
        self._download_cancel_requested = False
        self.dialog.show_downloading()
        thread = _DownloadThread(plan, self._download_update, self)
        thread.progressChanged.connect(self.dialog.set_download_progress)
        thread.completed.connect(self._download_completed)
        thread.failed.connect(self._operation_failed)
        thread.cancelled.connect(self._download_cancelled)
        thread.finished.connect(lambda owned=thread: self._download_thread_finished(owned))
        thread.finished.connect(thread.deleteLater)
        self._download_thread = thread
        self.busyChanged.emit(True)
        thread.start()

    @Slot(object)
    def _download_completed(self, patches: tuple[Path, ...]) -> None:
        if self._download_cancel_requested:
            return
        self._downloaded_patches = patches
        self.dialog.show_installing()

    def _install_downloaded_update(self, patches: tuple[Path, ...]) -> None:
        plan = self._plan
        if plan is None:
            return
        asset_digests = tuple(str(getattr(asset, "digest", "")) for asset in plan.assets)
        try:
            process = self._launch_installer(
                patches,
                plan.current_version,
                plan.latest_version,
                asset_digests,
            )
        except Exception as exc:
            self._operation_failed(str(exc))
            return
        self.dialog.accept()
        if not self.window.close():
            stop_error = _stop_installer_process(process)
            if stop_error is None:
                message = "主窗口未能关闭，升级程序已停止"
            else:
                message = f"主窗口未能关闭，且无法确认升级程序已停止：{stop_error}"
            self._operation_failed(message)
            return
        approve = getattr(process, "approve", None)
        if callable(approve):
            try:
                approve()
            except Exception as exc:
                self._operation_failed(
                    f"无法授权升级程序开始安装：{exc}；关闭程序后将由升级器重新启动旧版"
                )

    @Slot()
    def _download_cancelled(self) -> None:
        self._downloaded_patches = None
        if self.dialog.isVisible():
            self.dialog.reject()

    @Slot(str)
    def _operation_failed(self, message: str) -> None:
        self.dialog.show_error(message or "未知错误")
        self._show_dialog()

    def _open_current_release(self) -> None:
        plan = self._plan
        if plan is not None and plan.release_url:
            self._open_url(plan.release_url)

    def _check_thread_finished(self, thread: _CheckThread) -> None:
        if self._check_thread is thread:
            self._check_thread = None
        self._emit_busy_state()

    def _download_thread_finished(self, thread: _DownloadThread) -> None:
        if self._download_thread is thread:
            self._download_thread = None
        patches = self._downloaded_patches
        self._downloaded_patches = None
        if self._download_cancel_requested:
            patches = None
        self._download_cancel_requested = False
        self._emit_busy_state()
        if patches is not None:
            self._install_downloaded_update(patches)

    def _operation_running(self) -> bool:
        return self._check_thread is not None or self._download_thread is not None

    def _emit_busy_state(self) -> None:
        self.busyChanged.emit(self._operation_running())

    def _show_dialog(self) -> None:
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    @staticmethod
    def _open_release_url(url: str) -> bool:
        return QDesktopServices.openUrl(QUrl(url))


def _stop_installer_process(process: object, *, timeout_seconds: float = 2.0) -> str | None:
    cancel = getattr(process, "cancel", None)
    if callable(cancel):
        try:
            cancel()
        except Exception:
            pass
    poll = getattr(process, "poll", None)
    try:
        if callable(poll) and poll() is not None:
            return None
        terminate = getattr(process, "terminate", None)
        wait = getattr(process, "wait", None)
        if not callable(terminate) or not callable(wait):
            return "升级程序句柄不支持终止和等待"
        terminate()
        try:
            wait(timeout=timeout_seconds)
            return None
        except subprocess.TimeoutExpired:
            kill = getattr(process, "kill", None)
            if not callable(kill):
                return "升级程序终止超时且不支持强制结束"
            kill()
            wait(timeout=timeout_seconds)
            return None
    except Exception as exc:
        return str(exc)
