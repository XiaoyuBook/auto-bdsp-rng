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
    SHINY_DIALOG_REGION_FIELD,
    STAT_REGION_FIELDS,
)


Recognizer = Callable[[str, OcrRegion | None], str]

# Absolute source-frame coordinates calibrated against the 1920x1080 Broker output.
DEFAULT_OCR_REGIONS = {
    "nature": OcrRegion(112, 203, 230, 64),
    "characteristic": OcrRegion(103, 569, 432, 64),
    "hp": OcrRegion(517, 197, 54, 42),
    "attack": OcrRegion(735, 315, 85, 64),
    "defense": OcrRegion(717, 478, 115, 54),
    "sp_attack": OcrRegion(224, 306, 63, 67),
    "sp_defense": OcrRegion(218, 487, 85, 42),
    "speed": OcrRegion(475, 596, 85, 39),
    SHINY_DIALOG_REGION_FIELD: OcrRegion(6, 895, 1914, 175),
}


def load_ocr_region_config(settings: QSettings | None = None) -> OcrRegionConfig:
    settings = settings or QSettings("auto-bdsp-rng", "OcrSettings")
    values: dict[str, object] = {}
    for field in OCR_REGION_FIELDS:
        key = f"regions/{field}"
        if settings.contains(key):
            values[field] = settings.value(key, "")
        elif field in DEFAULT_OCR_REGIONS:
            values[field] = DEFAULT_OCR_REGIONS[field].as_tuple()
        else:
            values[field] = ""
    return OcrRegionConfig.from_settings_dict(values)


class OcrSettingsDialog(QDialog):
    regionSelectionRequested = Signal(str)
    regionDisplayRequested = Signal(str, object)
    recognitionRequested = Signal(str, object)
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
        self.resize(980, 680)
        self._settings = settings or QSettings("auto-bdsp-rng", "OcrSettings")
        self._recognizer = recognizer
        self._warmup_active = False
        self._full_test_active = False
        self._automation_active = False
        self._recognition_active_field: str | None = None
        self._preview_frame_shape: tuple[int, ...] | None = None
        self._row_action_buttons: list[QPushButton] = []
        self._display_buttons: list[QPushButton] = []
        self._recognition_buttons: list[QPushButton] = []
        self.region_config = load_ocr_region_config(self._settings)
        self._field_rows = {field: index for index, field in enumerate(OCR_REGION_FIELDS)}
        self._build_ui()
        if self._recognizer is not None:
            self.recognitionRequested.connect(self._run_legacy_recognition)
        self._refresh_all_rows()
        self._refresh_interaction_state()

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
        self.table.setMinimumHeight(556)
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
        self.defaults_button = QPushButton("导入默认区域")
        self.defaults_button.clicked.connect(self.import_default_regions)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        for button in (self.test_current_button, self.test_all_button, self.defaults_button, close_button):
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
            self._row_action_buttons.append(button)
            if text == "显示":
                self._display_buttons.append(button)
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

    @staticmethod
    def _format_region(region: OcrRegion) -> str:
        return f"{region.x}, {region.y}, {region.width}, {region.height}"

    def _refresh_row(self, field: str) -> None:
        row = self._field_rows[field]
        configured_region = self.region_config.get(field)
        if field == SHINY_DIALOG_REGION_FIELD and configured_region is None:
            if self._preview_frame_shape is None:
                region_text = "当前帧下方50%（X 0%-100%，Y 50%-100%）"
            else:
                effective_region = self.region_config.resolve(field, self._preview_frame_shape)
                region_text = (
                    f"有效: {self._format_region(effective_region)}"
                    if effective_region is not None
                    else "当前帧下方50%"
                )
            status = (
                "配置无效，使用默认"
                if self.region_config.has_invalid_custom(field)
                else "默认"
            )
        elif configured_region is None:
            region_text = "未设置"
            status = "未设置"
        else:
            region_text = self._format_region(configured_region)
            status = "已设置"
            if self._preview_frame_shape is not None:
                image_height, image_width = self._preview_frame_shape[:2]
                clipped = configured_region.clip(image_width, image_height)
                effective_region = self.region_config.resolve(field, self._preview_frame_shape)
                if effective_region is not None:
                    region_text = f"有效: {self._format_region(effective_region)}"
                if not clipped.is_valid():
                    status = "自定义无效，使用默认" if field == SHINY_DIALOG_REGION_FIELD else "超出当前帧"
                elif clipped != configured_region:
                    status = "已按当前帧裁剪"
        self.table.item(row, 1).setText(region_text)
        self.table.item(row, 2).setText(status)

    def set_preview_frame_shape(self, image_shape: tuple[int, ...]) -> None:
        shape = tuple(int(value) for value in image_shape)
        self._preview_frame_shape = shape if len(shape) >= 2 and shape[0] > 0 and shape[1] > 0 else None
        self._refresh_all_rows()

    def resolve_region(self, field: str, image_shape: tuple[int, ...]) -> OcrRegion | None:
        return self.region_config.resolve(field, image_shape)

    def set_region(self, field: str, region: OcrRegion | tuple[int, int, int, int]) -> None:
        if self.interaction_busy:
            return
        self.region_config.set(field, region)
        self._save_region(field)
        self._refresh_row(field)

    def reset_region(self, field: str) -> None:
        if self.interaction_busy:
            return
        self.region_config.remove(field)
        self._save_region(field)
        self._refresh_row(field)
        self.table.item(self._field_rows[field], 4).setText("未测试")

    def request_selection(self, field: str) -> None:
        if self.interaction_busy:
            return
        self.regionSelectionRequested.emit(field)

    def show_region(self, field: str) -> None:
        if self.task_busy:
            return
        region = self.region_config.get(field)
        if region is None and field != SHINY_DIALOG_REGION_FIELD:
            QMessageBox.information(self, "OCR区域未设置", f"请先框选“{OCR_REGION_LABELS[field]}”区域。")
            return
        self.regionDisplayRequested.emit(field, region)

    def recognize_field(self, field: str) -> None:
        if self._warmup_active:
            self.table.item(self._field_rows[field], 4).setText("等待预热完成")
            return
        if self._automation_active:
            self.table.item(self._field_rows[field], 4).setText("自动流程运行中")
            return
        if self.interaction_busy:
            return
        region = self.region_config.get(field)
        if region is None and field != SHINY_DIALOG_REGION_FIELD:
            self.table.item(self._field_rows[field], 4).setText("未设置")
            return
        self._recognition_active_field = field
        self.table.item(self._field_rows[field], 4).setText("识别中…")
        self._refresh_interaction_state()
        self.recognitionRequested.emit(field, region)

    def _run_legacy_recognition(self, field: str, region: object) -> None:
        if self._recognizer is None:
            return
        try:
            text = self._recognizer(field, region if isinstance(region, OcrRegion) else None)
        except Exception as exc:
            text = f"失败: {exc}"
        self.finish_recognition(field, text)

    def finish_recognition(self, field: str, text: str) -> None:
        self.table.item(self._field_rows[field], 4).setText(text or "空")
        if self._recognition_active_field == field:
            self._recognition_active_field = None
        self._refresh_interaction_state()

    def fail_recognition(self, field: str, message: str) -> None:
        self.finish_recognition(field, f"失败: {message}")

    def set_recognition_result(self, field: str, text: str) -> None:
        self.table.item(self._field_rows[field], 4).setText(text or "空")

    @property
    def task_busy(self) -> bool:
        return (
            self._warmup_active
            or self._full_test_active
            or self._recognition_active_field is not None
        )

    @property
    def interaction_busy(self) -> bool:
        return self.task_busy or self._automation_active

    def set_automation_active(self, active: bool) -> None:
        self._automation_active = bool(active)
        self._refresh_interaction_state()

    def _refresh_interaction_state(self) -> None:
        for button in self._row_action_buttons:
            if button in self._display_buttons:
                button.setEnabled(not self.task_busy)
            else:
                button.setEnabled(not self.interaction_busy)
        controls_enabled = not self.interaction_busy
        self.warmup_button.setEnabled(controls_enabled)
        self.test_current_button.setEnabled(controls_enabled)
        self.test_all_button.setEnabled(controls_enabled)
        self.defaults_button.setEnabled(controls_enabled)

    def start_warmup(self) -> None:
        if self.interaction_busy:
            return
        self.show_warmup_running()
        self.warmupRequested.emit()

    def show_warmup_running(self) -> None:
        self._warmup_active = True
        self.warmup_button.setText("预热中…")
        self.warmup_status.setText("正在初始化 OCR")
        self._refresh_interaction_state()

    def finish_warmup(self, success: bool, message: str) -> None:
        self._warmup_active = False
        self.warmup_button.setText("重新预热" if success else "预热OCR")
        self.warmup_status.setText(message)
        self._refresh_interaction_state()

    def test_all(self) -> None:
        if self.interaction_busy:
            return
        self._full_test_active = True
        self.test_all_button.setText("测试中…")
        for field in NOTE_REGION_FIELDS + STAT_REGION_FIELDS:
            if self.region_config.get(field) is not None:
                self.table.item(self._field_rows[field], 4).setText("等待中")
        self._refresh_interaction_state()
        self.fullTestRequested.emit()

    def finish_full_test(self, success: bool, message: str) -> None:
        self._full_test_active = False
        self.test_all_button.setText("测试全部")
        if not success:
            for field in NOTE_REGION_FIELDS + STAT_REGION_FIELDS:
                if self.table.item(self._field_rows[field], 4).text() == "等待中":
                    self.table.item(self._field_rows[field], 4).setText(message)
        self._refresh_interaction_state()

    def cancel_background_activity(self, message: str) -> None:
        active_field = self._recognition_active_field
        if active_field is not None:
            self.table.item(self._field_rows[active_field], 4).setText(f"失败: {message}")
        if self._full_test_active:
            for field in NOTE_REGION_FIELDS + STAT_REGION_FIELDS:
                if self.table.item(self._field_rows[field], 4).text() == "等待中":
                    self.table.item(self._field_rows[field], 4).setText(message)
        if self._warmup_active:
            self.warmup_status.setText(message)
        self._recognition_active_field = None
        self._full_test_active = False
        self._warmup_active = False
        self.warmup_button.setText("预热OCR")
        self.test_all_button.setText("测试全部")
        self._refresh_interaction_state()

    def _test_first_configured(self) -> None:
        if self.interaction_busy:
            return
        for field in OCR_REGION_FIELDS:
            if self.region_config.get(field) is not None or field == SHINY_DIALOG_REGION_FIELD:
                self.recognize_field(field)
                return

    def import_default_regions(self) -> None:
        if self.interaction_busy:
            return
        for field, region in DEFAULT_OCR_REGIONS.items():
            self.set_region(field, region)
