from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from auto_bdsp_rng.data import GameVersion, StaticEncounterCategory, StaticEncounterRecord, get_static_encounters
from auto_bdsp_rng.gen8_static import Shiny, StateFilter


NATURES_ZH = (
    "勤奋",
    "怕寂寞",
    "勇敢",
    "固执",
    "顽皮",
    "大胆",
    "坦率",
    "悠闲",
    "淘气",
    "乐天",
    "胆小",
    "急躁",
    "认真",
    "爽朗",
    "天真",
    "内敛",
    "慢吞吞",
    "冷静",
    "害羞",
    "马虎",
    "温和",
    "温顺",
    "自大",
    "慎重",
    "浮躁",
)

CATEGORY_LABELS_ZH = {
    None: "全部",
    "starters": "御三家",
    "gifts": "赠送",
    "fossils": "化石",
    "stationary": "定点",
    "roamers": "游走",
    "legends": "传说",
    "ramanasParkPureSpace": "玫瑰公园（纯净空间）",
    "ramanasParkStrangeSpace": "玫瑰公园（奇异空间）",
    "mythics": "幻兽",
}

POKEMON_LABELS_ZH = {
    "Turtwig": "草苗龟",
    "Chimchar": "小火焰猴",
    "Piplup": "波加曼",
    "Eevee": "伊布",
    "Happiny egg": "小福蛋蛋",
    "Riolu egg": "利欧路蛋",
    "Omanyte": "菊石兽",
    "Kabuto": "化石盔",
    "Aerodactyl": "化石翼龙",
    "Lileep": "触手百合",
    "Anorith": "太古羽虫",
    "Cranidos": "头盖龙",
    "Shieldon": "盾甲龙",
    "Drifloon": "飘飘球",
    "Spiritomb": "花岩怪",
    "Rotom": "洛托姆",
    "Mespirit": "艾姆利多",
    "Cresselia": "克雷色利亚",
    "Uxie": "由克希",
    "Azelf": "亚克诺姆",
    "Dialga": "帝牙卢卡",
    "Palkia": "帕路奇亚",
    "Heatran": "席多蓝恩",
    "Regigigas": "雷吉奇卡斯",
    "Giratina": "骑拉帝纳",
    "Articuno": "急冻鸟",
    "Zapdos": "闪电鸟",
    "Moltres": "火焰鸟",
    "Raikou": "雷公",
    "Entei": "炎帝",
    "Suicune": "水君",
    "Regirock": "雷吉洛克",
    "Regice": "雷吉艾斯",
    "Registeel": "雷吉斯奇鲁",
    "Latias": "拉帝亚斯",
    "Latios": "拉帝欧斯",
    "Mewtwo": "超梦",
    "Lugia": "洛奇亚",
    "Ho-Oh": "凤王",
    "Kyogre": "盖欧卡",
    "Groudon": "固拉多",
    "Rayquaza": "烈空坐",
    "Mew": "梦幻",
    "Jirachi": "基拉祈",
    "Darkrai": "达克莱伊",
    "Shaymin": "谢米",
    "Arceus": "阿尔宙斯",
}


class StaticTargetForm(QWidget):
    """Editable BDSP static target and filter form with independent widget state."""

    def __init__(self, parent: QWidget | None = None, version: GameVersion = GameVersion.BD) -> None:
        super().__init__(parent)
        self._version = version
        self._records: tuple[StaticEncounterRecord, ...] = ()
        self._build_ui()
        self.refresh_encounters()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)

        settings = QGroupBox("设置")
        settings.setMaximumHeight(380)
        settings.setMaximumWidth(260)
        self.settings_group = settings
        settings_layout = QGridLayout(settings)
        settings_layout.setVerticalSpacing(8)
        self.category_combo = QComboBox()
        self.category_combo.addItem("御三家", StaticEncounterCategory.STARTERS.value)
        self.category_combo.addItem("全部", None)
        for category in StaticEncounterCategory:
            if category == StaticEncounterCategory.STARTERS:
                continue
            self.category_combo.addItem(CATEGORY_LABELS_ZH.get(category.value, category.value), category.value)
        self.category_combo.currentIndexChanged.connect(self.refresh_encounters)

        self.encounter_combo = QComboBox()
        self.encounter_combo.currentIndexChanged.connect(self._update_encounter_details)
        self.level_display = self._spin(1, 100, 1)
        self.template_ability_display = QComboBox()
        for text, value in (("0", 0), ("1", 1), ("隐藏", 2), ("0/1", 255), ("任意", 255)):
            self.template_ability_display.addItem(text, value)
        self.template_shiny_display = QComboBox()
        for text, value in (("随机", int(Shiny.RANDOM)), ("锁闪", int(Shiny.NEVER)), ("Star", int(Shiny.STAR)), ("Square", int(Shiny.SQUARE))):
            self.template_shiny_display.addItem(text, value)
        self.iv_count_display = self._spin(0, 6, 0)

        rows = (
            ("分类", self.category_combo),
            ("宝可梦", self.encounter_combo),
            ("等级", self.level_display),
            ("特性", self.template_ability_display),
            ("异色", self.template_shiny_display),
            ("IV Count", self.iv_count_display),
        )
        for row, (label, widget) in enumerate(rows):
            widget.setFixedHeight(32)
            widget.setFixedWidth(160)
            settings_layout.addWidget(QLabel(label), row, 0)
            settings_layout.addWidget(widget, row, 1)
        root.addWidget(settings)

        filters = QGroupBox("筛选项")
        filters.setMaximumHeight(380)
        outer = QHBoxLayout(filters)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(24)
        self._build_iv_filter_column(outer)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #c8c6c0;")
        sep.setMinimumHeight(240)
        outer.addWidget(sep)
        self._build_misc_filter_column(outer)
        root.addWidget(filters, 3)

    def _build_iv_filter_column(self, outer: QHBoxLayout) -> None:
        left_col = QVBoxLayout()
        left_col.setSpacing(8)
        iv_grid = QGridLayout()
        iv_grid.setVerticalSpacing(7)
        iv_grid.setHorizontalSpacing(6)
        self.iv_min: list[QSpinBox] = []
        self.iv_max: list[QSpinBox] = []
        for row, label in enumerate(("HP", "攻击", "防御", "特攻", "特防", "速度")):
            iv_grid.addWidget(_filter_label(label, 50), row, 0)
            min_spin = self._spin(0, 31, 0)
            max_spin = self._spin(0, 31, 31)
            min_spin.setFixedWidth(80)
            max_spin.setFixedWidth(80)
            self.iv_min.append(min_spin)
            self.iv_max.append(max_spin)
            iv_grid.addWidget(min_spin, row, 1)
            iv_grid.addWidget(max_spin, row, 2)
        left_col.addLayout(iv_grid)
        self.show_stats_check = QCheckBox("显示能力值")
        left_col.addWidget(self.show_stats_check)
        self.iv_calculator_button = QPushButton("个体值计算器")
        self.iv_calculator_button.setMinimumHeight(30)
        left_col.addWidget(self.iv_calculator_button)
        left_col.addStretch()
        outer.addLayout(left_col)

    def _build_misc_filter_column(self, outer: QHBoxLayout) -> None:
        right_col = QVBoxLayout()
        right_col.setSpacing(8)
        grid = QGridLayout()
        grid.setVerticalSpacing(7)
        grid.setHorizontalSpacing(8)

        self.ability_filter = QComboBox()
        for text, value in (("任意", 255), ("0", 0), ("1", 1), ("隐藏", 2)):
            self.ability_filter.addItem(text, value)
        grid.addWidget(_filter_label("特性", 70), 0, 0)
        grid.addWidget(self.ability_filter, 0, 1)

        self.gender_filter = QComboBox()
        for text, value in (("任意", 255), ("雄性", 0), ("雌性", 1), ("无性别", 2)):
            self.gender_filter.addItem(text, value)
        grid.addWidget(_filter_label("性别", 70), 1, 0)
        grid.addWidget(self.gender_filter, 1, 1)

        self.height_min = self._spin(0, 255, 0)
        self.height_max = self._spin(0, 255, 255)
        grid.addWidget(_filter_label("Height", 70), 2, 0)
        grid.addLayout(_range_row(self.height_min, self.height_max), 2, 1)

        self.nature_combo = QComboBox()
        self.nature_combo.addItem("任意", -1)
        for index, nature in enumerate(NATURES_ZH):
            self.nature_combo.addItem(nature, index)
        grid.addWidget(_filter_label("性格", 70), 3, 0)
        grid.addWidget(self.nature_combo, 3, 1)

        self.shiny_filter = QComboBox()
        for text, value in (("任意", "any"), ("异色", "shiny"), ("Star", "star"), ("Square", "square"), ("非异色", "none")):
            self.shiny_filter.addItem(text, value)
        grid.addWidget(_filter_label("异色", 70), 4, 0)
        grid.addWidget(self.shiny_filter, 4, 1)

        self.weight_min = self._spin(0, 255, 0)
        self.weight_max = self._spin(0, 255, 255)
        grid.addWidget(_filter_label("Weight", 70), 5, 0)
        grid.addLayout(_range_row(self.weight_min, self.weight_max), 5, 1)

        right_col.addLayout(grid)
        for combo in (self.ability_filter, self.gender_filter, self.nature_combo, self.shiny_filter):
            combo.setFixedHeight(32)
            combo.setFixedWidth(240)
        self.skip_filter = QCheckBox("取消筛选")
        right_col.addWidget(self.skip_filter)
        outer.addLayout(right_col, 1)

    def set_version(self, version: GameVersion) -> None:
        if self._version == version:
            return
        self._version = version
        self.refresh_encounters()

    def refresh_encounters(self) -> None:
        category = self.category_combo.currentData() if hasattr(self, "category_combo") else None
        self._records = get_static_encounters(category, self._version.value)
        current_description = getattr(self.encounter_combo.currentData(), "description", None) if hasattr(self, "encounter_combo") else None
        self.encounter_combo.blockSignals(True)
        self.encounter_combo.clear()
        for record in self._records:
            name = POKEMON_LABELS_ZH.get(record.description, record.description)
            category_text = CATEGORY_LABELS_ZH.get(record.category.value, record.category.value)
            roamer_text = " 游走" if record.template.roamer else ""
            self.encounter_combo.addItem(f"{name} [{category_text}]{roamer_text}", record)
        if current_description is not None:
            for index in range(self.encounter_combo.count()):
                if getattr(self.encounter_combo.itemData(index), "description", None) == current_description:
                    self.encounter_combo.setCurrentIndex(index)
                    break
        self.encounter_combo.blockSignals(False)
        self._update_encounter_details()

    def selected_record(self) -> StaticEncounterRecord:
        record = self.encounter_combo.currentData()
        if record is None:
            raise ValueError("Select a static encounter")
        template = replace(
            record.template,
            level=self.level_display.value(),
            ability=int(self.template_ability_display.currentData()),
            shiny=Shiny(int(self.template_shiny_display.currentData())),
            iv_count=self.iv_count_display.value(),
        )
        return replace(record, template=template)

    def current_filter(self) -> tuple[StateFilter, str]:
        shiny_mode = self.shiny_filter.currentData()
        shiny_value = {
            "any": 255,
            "shiny": 1 | 2,
            "star": 1,
            "square": 2,
            "none": 255,
        }[shiny_mode]
        nature_index = self.nature_combo.currentData()
        if nature_index == -1:
            natures = (True,) * len(NATURES_ZH)
        else:
            natures = tuple(index == nature_index for index in range(len(NATURES_ZH)))
        return (
            StateFilter.from_iv_ranges(
                [spin.value() for spin in self.iv_min],
                [spin.value() for spin in self.iv_max],
                ability=self.ability_filter.currentData(),
                gender=self.gender_filter.currentData(),
                shiny=shiny_value,
                height_min=self.height_min.value(),
                height_max=self.height_max.value(),
                weight_min=self.weight_min.value(),
                weight_max=self.weight_max.value(),
                skip=self.skip_filter.isChecked(),
                natures=natures,
            ),
            str(shiny_mode),
        )

    def summary_text(self) -> str:
        record = self.selected_record()
        return (
            f"{self.encounter_combo.currentText()} Lv.{record.template.level} "
            f"IV Count {record.template.iv_count}; shiny={self.shiny_filter.currentText()}; "
            f"Height {self.height_min.value()}-{self.height_max.value()}"
        )

    def _update_encounter_details(self) -> None:
        if not hasattr(self, "encounter_combo"):
            return
        record = self.encounter_combo.currentData()
        if record is None:
            return
        template = record.template
        self.level_display.setValue(template.level)
        self.iv_count_display.setValue(template.iv_count)
        ability_text = {0: "0", 1: "1", 2: "隐藏", 255: "0/1"}.get(template.ability, "任意")
        self.template_ability_display.setCurrentIndex(max(0, self.template_ability_display.findText(ability_text)))
        shiny_text = {
            Shiny.NEVER: "锁闪",
            Shiny.STAR: "Star",
            Shiny.SQUARE: "Square",
        }.get(template.shiny, "随机")
        self.template_shiny_display.setCurrentIndex(max(0, self.template_shiny_display.findText(shiny_text)))

    def _spin(self, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        spin.setFixedHeight(36)
        return spin


def _filter_label(text: str, width: int) -> QLabel:
    label = QLabel(text)
    label.setFixedWidth(width)
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return label


def _range_row(min_spin: QSpinBox, max_spin: QSpinBox) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(6)
    min_spin.setFixedWidth(80)
    max_spin.setFixedWidth(80)
    row.addWidget(min_spin)
    row.addWidget(max_spin)
    row.addStretch()
    return row
