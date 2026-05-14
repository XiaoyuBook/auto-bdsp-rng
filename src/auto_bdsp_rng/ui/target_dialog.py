"""目标精灵设置弹窗 —— 可添加多个筛选条件，搜索时匹配任一即可。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.data import GameVersion, StaticEncounterRecord
from auto_bdsp_rng.gen8_static import Shiny, StateFilter
from auto_bdsp_rng.ui.static_target_form import NATURES_ZH, POKEMON_LABELS_ZH, StaticTargetForm


def _filter_desc(record: StaticEncounterRecord, sf: StateFilter, shiny_mode: str) -> str:
    zh_name = POKEMON_LABELS_ZH.get(record.description, record.description)
    parts = [zh_name]
    shiny_label = {"any": "异色:任意", "star": "异色:星闪", "square": "异色:方闪", "none": "非异色"}.get(shiny_mode, f"异色:{shiny_mode}")
    parts.append(shiny_label)
    if sf.iv_min != (0,) * 6 or sf.iv_max != (31,) * 6:
        iv_list = [f"{lo}-{hi}" if lo != hi else str(lo) for lo, hi in zip(sf.iv_min, sf.iv_max)]
        parts.append(f"IVs:{'/'.join(iv_list)}")
    if not all(sf.natures):
        locked = [NATURES_ZH[i] for i, v in enumerate(sf.natures) if v]
        if 1 <= len(locked) <= 3:
            parts.append(f"性格:{','.join(locked)}")
        elif len(locked) < 25:
            parts.append(f"性格:{len(locked)}种")
    if sf.ability != 255:
        parts.append(f"特性:{sf.ability}")
    if sf.gender != 255:
        gender_label = {0: "雄", 1: "雌", 2: "无"}.get(sf.gender, str(sf.gender))
        parts.append(f"性别:{gender_label}")
    if sf.height_min != 0 or sf.height_max != 255:
        parts.append(f"身高:{sf.height_min}" if sf.height_min == sf.height_max else f"身高:{sf.height_min}-{sf.height_max}")
    if sf.weight_min != 0 or sf.weight_max != 255:
        parts.append(f"体重:{sf.weight_min}" if sf.weight_min == sf.weight_max else f"体重:{sf.weight_min}-{sf.weight_max}")
    return "  ".join(parts)


class TargetEntry(QWidget):
    """单个已添加目标的摘要行 + 删除按钮。"""
    removed = Signal(int)

    def __init__(self, index: int, record: StaticEncounterRecord, state_filter: StateFilter, shiny_mode: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._index = index
        self._record = record
        self._state_filter = state_filter
        self._shiny_mode = shiny_mode
        desc = _filter_desc(record, state_filter, shiny_mode)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        label = QLabel(f"#{index + 1}  {desc}")
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        del_btn = QPushButton("删除")
        del_btn.setFixedWidth(50)
        del_btn.clicked.connect(lambda: self.removed.emit(self._index))
        layout.addWidget(del_btn)


class TargetDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, version: GameVersion = GameVersion.BD) -> None:
        super().__init__(parent)
        self.setWindowTitle("目标精灵设置")
        self.setMinimumSize(520, 600)
        self._entries: list[TargetEntry] = []
        self._build_ui(version)

    def _build_ui(self, version: GameVersion) -> None:
        root = QVBoxLayout(self)
        self.target_form = StaticTargetForm(self, version)
        self.target_form.show_stats_check.hide()
        self.target_form.iv_calculator_button.hide()
        root.addWidget(self.target_form)

        add_btn = QPushButton("添加目标")
        add_btn.setFixedHeight(36)
        add_btn.clicked.connect(self._add_current)
        root.addWidget(add_btn)

        list_group = QGroupBox("已添加目标（匹配任一即可）")
        list_layout = QVBoxLayout(list_group)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._entry_container = QWidget()
        self._entry_layout = QVBoxLayout(self._entry_container)
        self._entry_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self._entry_container)
        list_layout.addWidget(scroll)
        root.addWidget(list_group, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("确定")
        ok_btn.setFixedHeight(34)
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedHeight(34)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        root.addLayout(btn_row)

    def _add_current(self) -> None:
        record = self.target_form.selected_record()
        state_filter, shiny_mode = self.target_form.current_filter()
        entry = TargetEntry(len(self._entries), record, state_filter, shiny_mode)
        entry.removed.connect(self._remove_entry)
        self._entries.append(entry)
        self._entry_layout.addWidget(entry)
        if len(self._entries) == 1:
            self.target_form.category_combo.setEnabled(False)
            self.target_form.encounter_combo.setEnabled(False)

    def _remove_entry(self, index: int) -> None:
        if 0 <= index < len(self._entries):
            widget = self._entries.pop(index)
            self._entry_layout.removeWidget(widget)
            widget.deleteLater()
        if not self._entries:
            self.target_form.category_combo.setEnabled(True)
            self.target_form.encounter_combo.setEnabled(True)
        self._reindex()

    def _reindex(self) -> None:
        for i, entry in enumerate(self._entries):
            entry._index = i
            label = entry.findChild(QLabel)
            if label:
                desc = _filter_desc(entry._record, entry._state_filter, entry._shiny_mode)
                label.setText(f"#{i + 1}  {desc}")

    def set_targets(self, targets: list[tuple[object, object, str]]) -> None:
        for record, sf, sm in targets:
            entry = TargetEntry(len(self._entries), record, sf, sm)
            entry.removed.connect(self._remove_entry)
            self._entries.append(entry)
            self._entry_layout.addWidget(entry)
        if self._entries:
            # 同步 target_form 的 combo 到第一个目标的精灵
            first_record = self._entries[0]._record
            self.target_form.category_combo.setCurrentIndex(
                max(0, self.target_form.category_combo.findData(first_record.category.value))
            )
            self.target_form.refresh_encounters()
            for idx in range(self.target_form.encounter_combo.count()):
                data = self.target_form.encounter_combo.itemData(idx)
                if data is not None and getattr(data, "description", None) == first_record.description:
                    self.target_form.encounter_combo.setCurrentIndex(idx)
                    break
            self.target_form.category_combo.setEnabled(False)
            self.target_form.encounter_combo.setEnabled(False)

    def get_targets(self) -> list[tuple[StaticEncounterRecord, StateFilter, str]]:
        return [(e._record, e._state_filter, e._shiny_mode) for e in self._entries]
