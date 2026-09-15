"""Local WebView for the approved ROI/eye animation, without simulated edits."""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from auto_bdsp_rng.resources import resource_path


class _DemoPage(QWebEnginePage):
    def __init__(self, url: QUrl, parent: QObject) -> None:
        super().__init__(parent)
        self._url = url

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):  # noqa: N802
        return is_main_frame and url == self._url


class _DemoBridge(QObject):
    failed = Signal(str)

    def __init__(self, dialog: EyeDemoDialog) -> None:
        super().__init__(dialog)
        self.dialog = dialog

    @Slot()
    def pageReady(self) -> None:
        self.dialog.ready = True
        self.dialog.load_timeout.stop()

    @Slot()
    def learned(self) -> None:
        if self.dialog.ready and not self.dialog.submitting:
            self.dialog.submitting = True
            self.dialog.learnedRequested.emit()

    @Slot()
    def pause(self) -> None:
        self.dialog.reject()


class EyeDemoDialog(QDialog):
    learnedRequested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("4.3 · 框选 ROI 与眼睛模板演示")
        self.setModal(True)
        self.setMinimumSize(360, 360)
        area = parent.screen().availableGeometry()
        height = min(940, area.height() - 80)
        self.resize(min(1060, area.width() - 40, round((height - 76) * 1586 / 1280)), height)
        self.ready = False
        self.submitting = False
        self.acknowledged = False
        self.page_url = QUrl.fromLocalFile(str(resource_path("docs", "assets", "guide-eye", "index.html")))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget(self)
        layout.addWidget(self.stack)
        self.view = QWebEngineView(self)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        page = _DemoPage(self.page_url, self.view)
        page.setBackgroundColor(QColor("#161d19"))
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self.view.setPage(page)
        self.bridge = _DemoBridge(self)
        self.channel = QWebChannel(page)
        self.channel.registerObject("eyeGuideBridge", self.bridge)
        page.setWebChannel(self.channel)
        self.stack.addWidget(self.view)
        fallback = QWidget(self)
        errors = QVBoxLayout(fallback)
        errors.addStretch()
        label = QLabel("演示加载失败，请重新加载后继续。", fallback)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        errors.addWidget(label)
        retry = QPushButton("重新加载", fallback)
        retry.clicked.connect(self.load_page)
        errors.addWidget(retry, alignment=Qt.AlignmentFlag.AlignCenter)
        close = QPushButton("暂时收起", fallback)
        close.clicked.connect(self.reject)
        errors.addWidget(close, alignment=Qt.AlignmentFlag.AlignCenter)
        errors.addStretch()
        self.stack.addWidget(fallback)
        self.load_timeout = QTimer(self)
        self.load_timeout.setSingleShot(True)
        self.load_timeout.timeout.connect(lambda: self._loaded(False))
        self.view.loadFinished.connect(self._loaded)
        page.renderProcessTerminated.connect(lambda *_: self._loaded(False))
        self.load_page()

    def load_page(self) -> None:
        self.ready = self.submitting = False
        self.stack.setCurrentIndex(0)
        self.load_timeout.start(15000)
        self.view.setUrl(self.page_url)

    def _loaded(self, ok: bool) -> None:
        if not ok:
            self.load_timeout.stop()
            self.ready = False
            self.stack.setCurrentIndex(1)

    def save_failed(self) -> None:
        self.submitting = False
        self.bridge.failed.emit("无法保存引导进度，请检查设置目录是否可写后重试。")

    def finish_learning(self) -> None:
        self.acknowledged = True
        self.accept()

    def accept(self) -> None:
        if self.acknowledged:
            super().accept()

    def done(self, result: int) -> None:
        self.load_timeout.stop()
        self.view.stop()
        super().done(result)
