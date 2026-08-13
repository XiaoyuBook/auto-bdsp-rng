from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.automation.auto_rng.ocr_regions import (
    OCR_REGION_FIELDS,
    OCR_REGION_LABELS,
    NOTE_REGION_FIELDS,
    OcrRegion,
    OcrRegionConfig,
    STAT_REGION_FIELDS,
)


Recognizer = Callable[[str, OcrRegion], str]

DEFAULT_OCR_REGIONS = {
    "nature": OcrRegion(40, 110, 260, 38),
    "characteristic": OcrRegion(40, 300, 320, 38),
    "hp": OcrRegion(145, 155, 92, 34),
    "attack": OcrRegion(335, 250, 92, 34),
    "defense": OcrRegion(335, 365, 92, 34),
    "sp_attack": OcrRegion(145, 250, 92, 34),
    "sp_defense": OcrRegion(145, 365, 92, 34),
    "speed": OcrRegion(240, 470, 92, 34),
}


def load_ocr_region_config(settings: QSettings | None = None) -> OcrRegionConfig:
    settings = settings or QSettings("auto-bdsp-rng", "OcrSettings")
    values = {
        field: settings.value(f"regions/{field}", "")
        if settings.contains(f"regions/{field}")
        else DEFAULT_OCR_REGIONS[field].as_tuple()
        for field in OCR_REGION_FIELDS
    }
    return OcrRegionConfig.from_settings_dict(values)


class OcrSettingsDialog(QDialog):
    regionSelectionRequested = Signal(str)
    regionDisplayRequested = Signal(str, object)
    warmupRequested = Signal()
    fullTestRequested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: QSettings | None = None,
        recognizer: Recognizer | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("OCR区域设置")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(900, 620)
        self._settings = settings or QSettings("auto-bdsp-rng", "OcrSettings")
        self._recognizer = recognizer
        self._warmup_active = False
        self._recognition_buttons: list[QPushButton] = []
        self.region_config = load_ocr_region_config(self._settings)
        self._field_rows = {field: index for index, field in enumerate(OCR_REGION_FIELDS)}
        self._build_ui()
        self._refresh_all_rows()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        hint = QLabel("点击“框选”后，在 Seed 捕捉预览图上按住右键拖拽选择区域。识别结果只在点击“识别/测试”时刷新。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget(len(OCR_REGION_FIELDS), 5, self)
        self.table.setHorizontalHeaderLabels(["项目", "区域", "状态", "操作", "上次识别"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.verticalHeader().setDefaultSectionSize(56)
        self.table.setMinimumHeight(500)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        for row, field in enumerate(OCR_REGION_FIELDS):
            self.table.setItem(row, 0, QTableWidgetItem(OCR_REGION_LABELS[field]))
            self.table.setItem(row, 1, QTableWidgetItem("未设置"))
            self.table.setItem(row, 2, QTableWidgetItem("未设置"))
            self.table.setItem(row, 4, QTableWidgetItem("未测试"))
            self.table.setCellWidget(row, 3, self._build_action_cell(field))

        actions = QHBoxLayout()
        self.warmup_button = QPushButton("预热OCR")
        self.warmup_button.clicked.connect(self.start_warmup)
        self.warmup_status = QLabel("未预热")
        actions.addWidget(self.warmup_button)
        actions.addWidget(self.warmup_status)
        actions.addStretch(1)
        self.test_current_button = QPushButton("测试当前项")
        self.test_current_button.clicked.connect(self._test_first_configured)
        self.test_all_button = QPushButton("测试全部")
        self.test_all_button.clicked.connect(self.test_all)
        defaults = QPushButton("导入默认区域")
        defaults.clicked.connect(self.import_default_regions)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        for button in (self.test_current_button, self.test_all_button, defaults, close_button):
            actions.addWidget(button)
        layout.addLayout(actions)

    def _build_action_cell(self, field: str) -> QWidget:
        widget = QWidget(self.table)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        buttons = (
            ("框选", lambda _checked=False, field=field: self.request_selection(field)),
            ("显示", lambda _checked=False, field=field: self.show_region(field)),
            ("识别", lambda _checked=False, field=field: self.recognize_field(field)),
            ("重置", lambda _checked=False, field=field: self.reset_region(field)),
        )
        for text, callback in buttons:
            button = QPushButton(text)
            button.setFixedHeight(34)
            button.setMinimumWidth(72)
            button.clicked.connect(callback)
            if text == "识别":
                self._recognition_buttons.append(button)
            layout.addWidget(button)
        layout.addStretch(1)
        return widget

    def _save_region(self, field: str) -> None:
        region = self.region_config.get(field)
        key = f"regions/{field}"
        if region is None:
            self._settings.setValue(key, "")
        else:
            self._settings.setValue(key, region.to_settings_value())
        self._settings.sync()

    def _refresh_all_rows(self) -> None:
        for field in OCR_REGION_FIELDS:
            self._refresh_row(field)

    def _refresh_row(self, field: str) -> None:
        row = self._field_rows[field]
        region = self.region_config.get(field)
        if region is None:
            region_text = "未设置"
            status = "未设置"
        else:
            region_text = f"{region.x}, {region.y}, {region.width}, {region.height}"
            status = "已设置"
        self.table.item(row, 1).setText(region_text)
        self.table.item(row, 2).setText(status)

    def set_region(self, field: str, region: OcrRegion | tuple[int, int, int, int]) -> None:
        self.region_config.set(field, region)
        self._save_region(field)
        self._refresh_row(field)

    def reset_region(self, field: str) -> None:
        self.region_config.remove(field)
        self._save_region(field)
        self._refresh_row(field)
        self.table.item(self._field_rows[field], 4).setText("未测试")

    def request_selection(self, field: str) -> None:
        self.regionSelectionRequested.emit(field)

    def show_region(self, field: str) -> None:
        region = self.region_config.get(field)
        if region is None:
            QMessageBox.information(self, "OCR区域未设置", f"请先框选“{OCR_REGION_LABELS[field]}”区域。")
            return
        self.regionDisplayRequested.emit(field, region)

    def recognize_field(self, field: str) -> None:
        if self._warmup_active:
            self.table.item(self._field_rows[field], 4).setText("等待预热完成")
            return
        region = self.region_config.get(field)
        if region is None:
            self.table.item(self._field_rows[field], 4).setText("未设置")
            return
        if self._recognizer is None:
            self.table.item(self._field_rows[field], 4).setText("不可用")
            return
        try:
            text = self._recognizer(field, region)
        except Exception as exc:
            text = f"失败: {exc}"
        self.table.item(self._field_rows[field], 4).setText(text or "空")

    def set_recognition_result(self, field: str, text: str) -> None:
        self.table.item(self._field_rows[field], 4).setText(text or "空")

    def start_warmup(self) -> None:
        if self._warmup_active:
            return
        self.show_warmup_running()
        self.warmupRequested.emit()

    def show_warmup_running(self) -> None:
        self._warmup_active = True
        self.warmup_button.setEnabled(False)
        self.warmup_button.setText("预热中…")
        self.warmup_status.setText("正在初始化 OCR")
        self.test_current_button.setEnabled(False)
        self.test_all_button.setEnabled(False)
        for button in self._recognition_buttons:
            button.setEnabled(False)

    def finish_warmup(self, success: bool, message: str) -> None:
        self._warmup_active = False
        self.warmup_button.setEnabled(True)
        self.warmup_button.setText("重新预热" if success else "预热OCR")
        self.warmup_status.setText(message)
        self.test_current_button.setEnabled(True)
        self.test_all_button.setEnabled(True)
        for button in self._recognition_buttons:
            button.setEnabled(True)

    def test_all(self) -> None:
        if self._warmup_active:
            return
        self.test_all_button.setEnabled(False)
        self.test_all_button.setText("测试中…")
        for field in NOTE_REGION_FIELDS + STAT_REGION_FIELDS:
            if self.region_config.get(field) is not None:
                self.table.item(self._field_rows[field], 4).setText("等待中")
        self.fullTestRequested.emit()

    def finish_full_test(self, success: bool, message: str) -> None:
        self.test_all_button.setEnabled(True)
        self.test_all_button.setText("测试全部")
        if not success:
            for field in OCR_REGION_FIELDS:
                if self.table.item(self._field_rows[field], 4).text() == "等待中":
                    self.table.item(self._field_rows[field], 4).setText(message)

    def _test_first_configured(self) -> None:
        for field in OCR_REGION_FIELDS:
            if self.region_config.get(field) is not None:
                self.recognize_field(field)
                return

    def import_default_regions(self) -> None:
        for field, region in DEFAULT_OCR_REGIONS.items():
            self.set_region(field, region)
