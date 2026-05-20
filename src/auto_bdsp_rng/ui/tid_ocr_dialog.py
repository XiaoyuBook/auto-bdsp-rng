from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from auto_bdsp_rng.automation.auto_rng.ocr_regions import OcrRegion


TidRecognizer = Callable[[OcrRegion], str]


def load_tid_ocr_region(settings: QSettings | None = None) -> OcrRegion | None:
    settings = settings or QSettings("auto-bdsp-rng", "AutoTidRngOcr")
    return OcrRegion.from_settings_value(settings.value("tid_region", ""))


class TidOcrDialog(QDialog):
    regionSelectionRequested = Signal()
    regionDisplayRequested = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: QSettings | None = None,
        recognizer: TidRecognizer | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("TID OCR 设置")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(420, 180)
        self._settings = settings or QSettings("auto-bdsp-rng", "AutoTidRngOcr")
        self._recognizer = recognizer
        self.region = load_tid_ocr_region(self._settings)
        self._build_ui()
        self._refresh_region_text()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.region_value = QLabel("ROI：未设置")
        self.region_value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        layout.addWidget(self.region_value)

        result_row = QHBoxLayout()
        result_row.addWidget(QLabel("识别结果"))
        self.result_value = QLabel("-")
        self.result_value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        result_row.addWidget(self.result_value, 1)
        layout.addLayout(result_row)

        actions = QHBoxLayout()
        self.recognize_button = QPushButton("识别当前内容")
        self.recognize_button.clicked.connect(self.recognize_current)
        self.select_button = QPushButton("框选范围")
        self.select_button.clicked.connect(self.regionSelectionRequested.emit)
        self.show_button = QPushButton("显示范围")
        self.show_button.clicked.connect(self.show_region)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        for button in (self.recognize_button, self.select_button, self.show_button, close_button):
            button.setFixedHeight(34)
            actions.addWidget(button)
        layout.addLayout(actions)

    def set_region(self, region: OcrRegion | tuple[int, int, int, int]) -> None:
        if not isinstance(region, OcrRegion):
            region = OcrRegion(*(int(value) for value in region))
        self.region = region
        self._settings.setValue("tid_region", region.to_settings_value())
        self._settings.sync()
        self._refresh_region_text()

    def recognize_current(self) -> None:
        if self.region is None:
            self.result_value.setText("未设置 ROI")
            return
        if self._recognizer is None:
            self.result_value.setText("OCR 不可用")
            return
        try:
            text = self._recognizer(self.region)
        except Exception as exc:
            text = f"失败: {exc}"
        self.result_value.setText(text or "空")

    def show_region(self) -> None:
        if self.region is None:
            self.result_value.setText("未设置 ROI")
            return
        self.regionDisplayRequested.emit(self.region)

    def _refresh_region_text(self) -> None:
        if self.region is None:
            self.region_value.setText("ROI：未设置")
            return
        self.region_value.setText(
            f"ROI：X={self.region.x}, Y={self.region.y}, W={self.region.width}, H={self.region.height}"
        )
