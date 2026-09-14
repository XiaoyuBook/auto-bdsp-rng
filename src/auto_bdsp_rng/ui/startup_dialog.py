"""The local WebView used by the first-launch mode chooser."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QPoint, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from auto_bdsp_rng.app_settings import ExperienceLevel, set_experience_level
from auto_bdsp_rng.resources import resource_path


class _WelcomePage(QWebEnginePage):
    def __init__(self, url: QUrl, parent: QObject) -> None:
        super().__init__(parent)
        self._welcome_url = url

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):  # noqa: N802
        return is_main_frame and url == self._welcome_url


class _WelcomeBridge(QObject):
    saveFailed = Signal(str)

    def __init__(self, dialog: StartupNoticeDialog) -> None:
        super().__init__(dialog)
        self._dialog = dialog

    @Slot()
    def pageReady(self) -> None:
        self._dialog.ready = True

    @Slot(str)
    def choose(self, level: str) -> None:
        self._dialog.submit_choice(level)


class StartupNoticeDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        save_choice: Callable[[ExperienceLevel], object] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择乱数方式")
        self.setObjectName("StartupNoticeDialog")
        self.setModal(True)
        self.resize(940, 620)
        self.setMinimumSize(360, 360)
        self.ready = False
        self._submitting = False
        self._positioned = False
        self.selected_experience_level: ExperienceLevel | None = None
        self._save_choice = save_choice or (
            lambda level: set_experience_level(level, acknowledge_startup=True)
        )
        self.page_url = QUrl.fromLocalFile(str(resource_path("docs", "assets", "welcome", "index.html")))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget(self)
        layout.addWidget(self.stack)
        self.view = QWebEngineView(self)
        page = _WelcomePage(self.page_url, self.view)
        page.setBackgroundColor(QColor("#e9efec"))
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        self.view.setPage(page)
        self.bridge = _WelcomeBridge(self)
        self.channel = QWebChannel(page)
        self.channel.registerObject("welcomeBridge", self.bridge)
        page.setWebChannel(self.channel)
        self.stack.addWidget(self.view)

        error_page = QWidget(self)
        error_layout = QVBoxLayout(error_page)
        error_layout.addStretch()
        error_text = QLabel("首次启动页面加载失败，请重试。", error_page)
        error_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        error_layout.addWidget(error_text)
        retry = QPushButton("重新加载", error_page)
        retry.clicked.connect(self._load_page)
        error_layout.addWidget(retry, alignment=Qt.AlignmentFlag.AlignCenter)
        close = QPushButton("关闭", error_page)
        close.clicked.connect(self.reject)
        error_layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignCenter)
        error_layout.addStretch()
        self.stack.addWidget(error_page)
        self.view.loadStarted.connect(self._loading)
        self.view.loadFinished.connect(self._loaded)
        page.renderProcessTerminated.connect(lambda *_: self._loaded(False))
        self._load_page()

    def _load_page(self) -> None:
        self.ready = False
        self.stack.setCurrentIndex(0)
        self.view.setUrl(self.page_url)

    def _loading(self) -> None:
        self.ready = False

    def _loaded(self, ok: bool) -> None:
        if not ok:
            self.ready = False
            self.stack.setCurrentIndex(1)

    def submit_choice(self, level: str) -> None:
        if not self.ready or self._submitting or level not in ("beginner", "expert"):
            return
        self._submitting = True
        selected: ExperienceLevel = "beginner" if level == "beginner" else "expert"
        try:
            self._save_choice(selected)
        except OSError:
            self._submitting = False
            self.bridge.saveFailed.emit("无法保存选择，请检查设置目录是否可写后重试。")
            return
        self.selected_experience_level = selected
        self.accept()

    def accept(self) -> None:
        if self.selected_experience_level is not None:
            super().accept()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._positioned:
            return
        self._positioned = True
        parent = self.parentWidget()
        screen = parent.screen() if parent is not None else self.screen()
        area = screen.availableGeometry()
        self.resize(min(940, area.width() - 32), min(620, area.height() - 64))
        center = parent.frameGeometry().center() if parent is not None else area.center()
        frame = self.frameGeometry()
        frame.moveCenter(center)
        frame.moveLeft(max(area.left(), min(frame.left(), area.right() - frame.width() + 1)))
        frame.moveTop(max(area.top(), min(frame.top(), area.bottom() - frame.height() + 1)))
        self.move(QPoint(frame.left(), frame.top()))
