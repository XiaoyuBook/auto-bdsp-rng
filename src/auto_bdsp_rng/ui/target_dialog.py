"""目标精灵设置弹窗 —— 可添加多个筛选条件，搜索时匹配任一即可。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.data import GameVersion, StaticEncounterRecord
from auto_bdsp_rng.gen8_static import StateFilter
from auto_bdsp_rng.ui.workspace_theme import primary_button_styles
from auto_bdsp_rng.ui.static_target_form import NATURES_ZH, POKEMON_LABELS_ZH, StaticTargetForm


def _range_desc(minimum: int, maximum: int) -> str:
    return str(minimum) if minimum == maximum else f"{minimum}-{maximum}"


def _filter_desc(sf: StateFilter, shiny_mode: str) -> str:
    if sf.skip:
        return "取消筛选"

    shiny_label = {
        "any": "任意",
        "shiny": "仅异色",
        "star": "星闪",
        "square": "方闪",
        "none": "非异色",
    }.get(shiny_mode, shiny_mode)
    iv_ranges = " / ".join(
        _range_desc(lo, hi) for lo, hi in zip(sf.iv_min, sf.iv_max)
    )
    selected_natures = [
        NATURES_ZH[index]
        for index, selected in enumerate(sf.natures)
        if selected and index < len(NATURES_ZH)
    ]
    if len(selected_natures) == len(NATURES_ZH):
        nature_label = "任意"
    elif not selected_natures:
        nature_label = "无"
    elif len(selected_natures) <= 3:
        nature_label = "/".join(selected_natures)
    else:
        nature_label = f"{len(selected_natures)} 种"
    ability_label = {255: "任意", 0: "0", 1: "1", 2: "隐藏"}.get(
        sf.ability, str(sf.ability)
    )
    gender_label = {255: "任意", 0: "雄性", 1: "雌性", 2: "无性别"}.get(
        sf.gender, str(sf.gender)
    )
    return " · ".join(
        (
            f"异色：{shiny_label}",
            f"IV：{iv_ranges}",
            f"性格：{nature_label}",
            f"特性：{ability_label}",
            f"性别：{gender_label}",
            f"Height：{_range_desc(sf.height_min, sf.height_max)}",
            f"Weight：{_range_desc(sf.weight_min, sf.weight_max)}",
        )
    )


class TargetEntry(QWidget):
    """单个已添加目标的摘要行 + 删除按钮。"""

    removed = Signal(int)

    def __init__(
        self,
        index: int,
        record: StaticEncounterRecord,
        state_filter: StateFilter,
        shiny_mode: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TargetEntry")
        self._index = index
        self._record = record
        self._state_filter = state_filter
        self._shiny_mode = shiny_mode
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(10)

        self.index_label = QLabel()
        self.index_label.setObjectName("TargetEntryIndex")
        self.index_label.setFixedWidth(28)
        self.index_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        layout.addWidget(self.index_label)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        self.title_label = QLabel()
        self.title_label.setObjectName("TargetEntryTitle")
        self.summary_label = QLabel()
        self.summary_label.setObjectName("TargetEntrySummary")
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        text_column.addWidget(self.title_label)
        text_column.addWidget(self.summary_label)
        layout.addLayout(text_column, 1)

        self.delete_button = QPushButton("删除")
        self.delete_button.setObjectName("TargetDeleteButton")
        self.delete_button.setFixedSize(52, 32)
        self.delete_button.setToolTip("删除这组目标条件")
        self.delete_button.clicked.connect(lambda: self.removed.emit(self._index))
        layout.addWidget(self.delete_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.set_index(index)

    def set_index(self, index: int) -> None:
        self._index = index
        self.index_label.setText(f"{index + 1:02d}")
        name = POKEMON_LABELS_ZH.get(self._record.description, self._record.description)
        self.title_label.setText(f"{name} · Lv.{self._record.template.level}")
        self.summary_label.setText(_filter_desc(self._state_filter, self._shiny_mode))


class TargetDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        version: GameVersion = GameVersion.BD,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TargetDialog")
        self.setWindowTitle("目标精灵设置")
        self.setMinimumSize(860, 640)
        self.resize(920, 700)
        self._entries: list[TargetEntry] = []
        self.setStyleSheet(self._stylesheet() + primary_button_styles("QDialog#TargetDialog QPushButton#TargetPrimaryButton"))
        self._build_ui(version)

    def _build_ui(self, version: GameVersion) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 16)
        root.setSpacing(0)

        self.target_form = StaticTargetForm(self, version, compact=True)
        self.target_form.show_stats_check.hide()
        self.target_form.iv_calculator_button.hide()
        root.addWidget(self.target_form)

        root.addWidget(self._divider())
        add_bar = QFrame()
        add_bar.setObjectName("TargetAddBar")
        add_layout = QHBoxLayout(add_bar)
        add_layout.setContentsMargins(0, 12, 0, 12)
        add_layout.addStretch(1)
        self.add_button = QPushButton("添加目标")
        self.add_button.setObjectName("TargetPrimaryButton")
        self.add_button.setFixedSize(112, 34)
        self.add_button.clicked.connect(self._add_current)
        add_layout.addWidget(self.add_button)
        root.addWidget(add_bar)

        self.target_list_group = QFrame()
        self.target_list_group.setObjectName("TargetListSection")
        list_layout = QVBoxLayout(self.target_list_group)
        list_layout.setContentsMargins(0, 14, 0, 0)
        list_layout.setSpacing(7)
        list_header = QHBoxLayout()
        list_header.setContentsMargins(0, 0, 0, 0)
        list_header.setSpacing(8)
        list_title = QLabel("已添加目标（匹配任一即可）")
        list_title.setObjectName("TargetListTitle")
        self.target_count_label = QLabel("0 组条件")
        self.target_count_label.setObjectName("TargetMutedLabel")
        list_header.addWidget(list_title)
        list_header.addStretch(1)
        list_header.addWidget(self.target_count_label)
        list_layout.addLayout(list_header)

        self.target_scroll = QScrollArea()
        self.target_scroll.setObjectName("TargetListScroll")
        self.target_scroll.setWidgetResizable(True)
        self.target_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.target_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.target_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.target_scroll.setMinimumHeight(128)
        self.target_scroll.setMaximumHeight(205)
        self.target_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._entry_container = QWidget()
        self._entry_container.setObjectName("TargetEntryContainer")
        self._entry_layout = QVBoxLayout(self._entry_container)
        self._entry_layout.setContentsMargins(0, 0, 0, 0)
        self._entry_layout.setSpacing(0)
        self._entry_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.empty_label = QLabel("尚未添加目标")
        self.empty_label.setObjectName("TargetEmptyLabel")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setMinimumHeight(72)
        self._entry_layout.addWidget(self.empty_label)
        self.target_scroll.setWidget(self._entry_container)
        list_layout.addWidget(self.target_scroll, 1)
        root.addWidget(self.target_list_group, 1)

        root.addWidget(self._divider())
        footer = QFrame()
        footer.setObjectName("TargetFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(0, 13, 0, 0)
        footer_layout.setSpacing(8)
        footer_layout.addStretch(1)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("TargetSecondaryButton")
        self.cancel_button.setFixedSize(72, 34)
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button = QPushButton("确定")
        self.ok_button.setObjectName("TargetPrimaryButton")
        self.ok_button.setFixedSize(72, 34)
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        footer_layout.addWidget(self.cancel_button)
        footer_layout.addWidget(self.ok_button)
        root.addWidget(footer)

    @staticmethod
    def _divider() -> QFrame:
        divider = QFrame()
        divider.setObjectName("TargetSectionDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        return divider

    @staticmethod
    def _stylesheet() -> str:
        return """
            QDialog#TargetDialog {
                background: #FFFFFF;
                color: #202A33;
            }
            QDialog#TargetDialog QWidget {
                background: transparent;
                color: #202A33;
                font-family: "Noto Sans SC", "Source Han Sans SC", "Noto Sans CJK SC", "Microsoft YaHei UI", "PingFang SC", "Segoe UI", sans-serif;
                font-size: 13px;
                font-weight: 400;
            }
            QDialog#TargetDialog QLabel {
                background: transparent;
                border: 0;
                padding: 0;
            }
            QDialog#TargetDialog QGroupBox#TargetSettingsGroup,
            QDialog#TargetDialog QGroupBox#TargetFiltersGroup {
                background: #FFFFFF;
                border: 0;
                border-radius: 0;
                margin-top: 24px;
                padding: 0;
                font-size: 14px;
                font-weight: 500;
            }
            QDialog#TargetDialog QGroupBox#TargetSettingsGroup::title,
            QDialog#TargetDialog QGroupBox#TargetFiltersGroup::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 0;
                top: 0;
                padding: 0;
                color: #202A33;
            }
            QDialog#TargetDialog QComboBox,
            QDialog#TargetDialog QSpinBox {
                background: #FFFFFF;
                border: 1px solid #E0E5EB;
                border-radius: 7px;
                min-height: 32px;
                max-height: 32px;
                padding: 0 9px;
                selection-background-color: #EAF7F1;
                selection-color: #087C58;
            }
            QDialog#TargetDialog QComboBox:focus,
            QDialog#TargetDialog QSpinBox:focus {
                border-color: #087C58;
            }
            QDialog#TargetDialog QComboBox:disabled,
            QDialog#TargetDialog QSpinBox:disabled {
                background: #F7F8FA;
                color: #687480;
            }
            QDialog#TargetDialog QComboBox::drop-down {
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 30px;
                border: 0;
            }
            QDialog#TargetDialog QComboBox QAbstractItemView {
                background: #FFFFFF;
                color: #202A33;
                border: 1px solid #E0E5EB;
                selection-background-color: #EAF7F1;
                selection-color: #087C58;
                padding: 3px;
            }
            QDialog#TargetDialog QSpinBox QLineEdit {
                background: transparent;
                border: 0;
                min-height: 0;
                max-height: 16777215px;
                padding: 0;
                color: #202A33;
                font-family: "Cascadia Mono", "Consolas", monospace;
            }
            QDialog#TargetDialog QCheckBox {
                background: transparent;
                spacing: 7px;
            }
            QDialog#TargetDialog QCheckBox::indicator {
                width: 13px;
                height: 13px;
                border: 1px solid #A0A9B2;
                border-radius: 2px;
                background: #FFFFFF;
            }
            QDialog#TargetDialog QCheckBox::indicator:checked {
                background: #087C58;
                border-color: #087C58;
            }
            QDialog#TargetDialog QFrame#TargetFilterDivider,
            QDialog#TargetDialog QFrame#TargetSectionDivider {
                color: #E0E5EB;
                background: #E0E5EB;
                border: 0;
            }
            QDialog#TargetDialog QLabel#TargetRangeSeparator,
            QDialog#TargetDialog QLabel#TargetMutedLabel,
            QDialog#TargetDialog QLabel#TargetEntryIndex,
            QDialog#TargetDialog QLabel#TargetEntrySummary {
                color: #687480;
            }
            QDialog#TargetDialog QLabel#TargetRangeSeparator,
            QDialog#TargetDialog QLabel#TargetEntryIndex {
                font-family: "Cascadia Mono", "Consolas", monospace;
            }
            QDialog#TargetDialog QLabel#TargetListTitle {
                font-size: 14px;
                font-weight: 500;
            }
            QDialog#TargetDialog QLabel#TargetEntryTitle {
                font-weight: 500;
            }
            QDialog#TargetDialog QLabel#TargetEntrySummary,
            QDialog#TargetDialog QLabel#TargetEntryIndex,
            QDialog#TargetDialog QLabel#TargetMutedLabel {
                font-size: 12px;
            }
            QDialog#TargetDialog QWidget#TargetEntryContainer,
            QDialog#TargetDialog QScrollArea#TargetListScroll,
            QDialog#TargetDialog QScrollArea#TargetListScroll > QWidget > QWidget {
                background: #FFFFFF;
                border: 0;
            }
            QDialog#TargetDialog QWidget#TargetEntry {
                background: #FFFFFF;
                border: 0;
                border-bottom: 1px solid #E0E5EB;
            }
            QDialog#TargetDialog QLabel#TargetEmptyLabel {
                color: #687480;
                background: #F7F8FA;
                border-radius: 7px;
            }
            QDialog#TargetDialog QScrollBar:vertical {
                width: 8px;
                margin: 0;
                background: #F7F8FA;
            }
            QDialog#TargetDialog QScrollBar::handle:vertical {
                min-height: 24px;
                background: #C9D4CE;
                border-radius: 4px;
            }
            QDialog#TargetDialog QScrollBar::add-line:vertical,
            QDialog#TargetDialog QScrollBar::sub-line:vertical {
                height: 0;
            }
            QDialog#TargetDialog QPushButton {
                min-height: 32px;
                max-height: 34px;
                padding: 0 13px;
                border: 1px solid #E0E5EB;
                border-radius: 7px;
                background: #FFFFFF;
                color: #202A33;
                font-weight: 400;
            }
            QDialog#TargetDialog QPushButton:hover {
                background: #F3F6F4;
            }
            QDialog#TargetDialog QPushButton:focus {
                border-color: #087C58;
            }
            QDialog#TargetDialog QPushButton#TargetPrimaryButton {
                background: #087C58;
                border-color: #087C58;
                color: #FFFFFF;
                font-weight: 500;
            }
            QDialog#TargetDialog QPushButton#TargetPrimaryButton:hover {
                background: #066E4E;
                border-color: #066E4E;
            }
            QDialog#TargetDialog QPushButton#TargetDeleteButton {
                background: transparent;
                border-color: transparent;
                color: #AC4B42;
                padding: 0 7px;
                font-size: 12px;
            }
            QDialog#TargetDialog QPushButton#TargetDeleteButton:hover {
                background: #FFF4F1;
            }
        """

    def _add_current(self) -> None:
        record = self.target_form.selected_record()
        state_filter, shiny_mode = self.target_form.current_filter()
        self._append_entry(record, state_filter, shiny_mode)
        self._update_entry_state()

    def _append_entry(
        self,
        record: StaticEncounterRecord,
        state_filter: StateFilter,
        shiny_mode: str,
    ) -> None:
        entry = TargetEntry(
            len(self._entries), record, state_filter, shiny_mode, self._entry_container
        )
        entry.removed.connect(self._remove_entry)
        self._entries.append(entry)
        self._entry_layout.addWidget(entry)

    def _remove_entry(self, index: int) -> None:
        if 0 <= index < len(self._entries):
            widget = self._entries.pop(index)
            self._entry_layout.removeWidget(widget)
            widget.deleteLater()
        self._reindex()
        self._update_entry_state()

    def _reindex(self) -> None:
        for i, entry in enumerate(self._entries):
            entry.set_index(i)

    def _update_entry_state(self) -> None:
        has_entries = bool(self._entries)
        self.empty_label.setVisible(not has_entries)
        self.target_count_label.setText(f"{len(self._entries)} 组条件")
        self.target_form.category_combo.setEnabled(not has_entries)
        self.target_form.encounter_combo.setEnabled(not has_entries)

    def _clear_entries(self) -> None:
        while self._entries:
            entry = self._entries.pop()
            self._entry_layout.removeWidget(entry)
            entry.deleteLater()
        self._update_entry_state()

    def set_targets(
        self,
        targets: list[tuple[StaticEncounterRecord, StateFilter, str]],
    ) -> None:
        self._clear_entries()
        for record, sf, sm in targets:
            self._append_entry(record, sf, sm)
        if self._entries:
            # 同步 target_form 的 combo 到第一个目标的精灵
            first_record = self._entries[0]._record
            self.target_form.category_combo.setCurrentIndex(
                max(
                    0,
                    self.target_form.category_combo.findData(
                        first_record.category.value
                    ),
                )
            )
            self.target_form.refresh_encounters()
            for idx in range(self.target_form.encounter_combo.count()):
                data = self.target_form.encounter_combo.itemData(idx)
                if data is not None and getattr(data, "description", None) == first_record.description:
                    self.target_form.encounter_combo.setCurrentIndex(idx)
                    break
        self._update_entry_state()

    def get_targets(self) -> list[tuple[StaticEncounterRecord, StateFilter, str]]:
        return [(e._record, e._state_filter, e._shiny_mode) for e in self._entries]
