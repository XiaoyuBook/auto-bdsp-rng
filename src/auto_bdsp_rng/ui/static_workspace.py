"""Compact native layout for the existing BDSP search controls and models."""
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget, QGridLayout

from auto_bdsp_rng.ui.workspace_controls import configure_disclosure_button


def _clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.layout():
            _clear_layout(item.layout())
        elif item.widget():
            item.widget().hide()


def _field(title, control):
    field = QWidget()
    layout = QVBoxLayout(field)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)
    label = QLabel(title)
    label.setObjectName("WorkspaceHint")
    layout.addWidget(label)
    control.setMinimumWidth(0)
    control.setMaximumWidth(16777215)
    control.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    control.show()
    layout.addWidget(control)
    return field


class StaticWorkspaceLayout(QObject):
    def __init__(self, window, page, configuration):
        super().__init__(page)
        self.window, self.page = window, page
        self._profile()
        self._rng()
        self._target()
        self._filters()
        contents = configuration.layout()
        contents.setSpacing(4)
        contents.addWidget(window.static_group)
        contents.addWidget(window.rng_info_group)
        contents.addWidget(self.filter_header)
        contents.addWidget(window.filter_group)
        contents.addStretch(1)
        self._result_actions()
        page.installEventFilter(self)
        window.profile_group.installEventFilter(self)
        window.profile_summary.installEventFilter(self)
        window.bdsp_config_scroll.viewport().installEventFilter(self)
        self.refresh_profile()
        self.refresh_target()
        self.reflow()

    def _profile(self):
        w = self.window
        layout = w.profile_group.layout()
        _clear_layout(layout)
        layout.setDirection(QHBoxLayout.Direction.LeftToRight)
        layout.setContentsMargins(6, 2, 6, 2)
        text = QVBoxLayout()
        text.setSpacing(2)
        w.profile_summary = QLabel()
        w.profile_summary.setTextFormat(Qt.TextFormat.PlainText)
        w.profile_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        w.profile_ids_summary = QLabel()
        w.profile_ids_summary.setObjectName("WorkspaceHint")
        text.addWidget(w.profile_summary)
        text.addWidget(w.profile_ids_summary)
        layout.addLayout(text, 1)
        layout.addWidget(w.profile_manager_button)
        w.profile_manager_button.show()
        for field in (w.profile_name, w.tid, w.sid, w.tsv):
            field.textChanged.connect(self.refresh_profile)
        for field in (w.national_dex, w.shiny_charm, w.oval_charm):
            field.toggled.connect(self.refresh_profile)

    def refresh_profile(self, *_args):
        w = self.window
        title = f"{w.profile_name.text() or '未命名存档'} · {w.profile_game_value.text()}"
        w.profile_summary.setText(w.profile_summary.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, max(100, w.profile_summary.width())))
        flags = " / ".join(field.text() for field in (w.national_dex, w.shiny_charm, w.oval_charm) if field.isChecked())
        w.profile_summary.setToolTip(title + (f"\n{flags}" if flags else ""))
        w.profile_ids_summary.setText(f"TID {w.tid.text()} · SID {w.sid.text()} · TSV {w.tsv.text()}")

    def _rng(self):
        w = self.window
        grid = w.rng_info_group.layout()
        _clear_layout(grid)
        grid.setContentsMargins(4, 4, 4, 4)
        seeds = QHBoxLayout()
        seeds.setSpacing(8)
        for index, control in enumerate(w.bdsp_seed64_inputs):
            seeds.addWidget(_field(f"Seed {index}", control), 1)
        advances = QHBoxLayout()
        advances.setSpacing(8)
        for label, control in (("初始帧", w.initial_advances), ("追加帧数", w.max_advances), ("Offset", w.offset)):
            advances.addWidget(_field(label, control), 1)
        w.max_advances.setToolTip("从初始帧起追加搜索的帧数，包含首尾；0 表示只生成初始帧。")
        grid.addLayout(seeds, 0, 0, 1, 2)
        grid.addLayout(advances, 1, 0, 1, 2)

    def _target(self):
        w = self.window
        grid = w.static_group.layout()
        _clear_layout(grid)
        grid.setContentsMargins(4, 4, 4, 4)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(_field("分类", w.category_combo), 1)
        row.addWidget(_field("宝可梦", w.encounter_combo), 2)
        grid.addLayout(row, 0, 0, 1, 2)
        lead = QHBoxLayout()
        lead.setSpacing(8)
        w.lead_label.setFixedWidth(36)
        w.lead_label.show()
        lead.addWidget(w.lead_label)
        w.lead_combo.setMinimumWidth(0)
        w.lead_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        w.lead_combo.show()
        lead.addWidget(w.lead_combo, 1)
        w.target_attributes = QLabel()
        w.target_attributes.setObjectName("WorkspaceHint")
        lead.addWidget(w.target_attributes)
        grid.addLayout(lead, 1, 0, 1, 2)
        for field in (w.level_display, w.iv_count_display):
            field.textChanged.connect(self.refresh_target)
        for field in (w.template_ability_display, w.template_shiny_display):
            field.currentTextChanged.connect(self.refresh_target)

    def refresh_target(self, *_args):
        w = self.window
        w.target_attributes.setText(f"Lv.{w.level_display.text()} · {w.iv_count_display.text()}V")
        w.target_attributes.setToolTip(f"等级 {w.level_display.text()} · 特性 {w.template_ability_display.currentText()} · {w.template_shiny_display.currentText()} · 固定满个体 {w.iv_count_display.text()}")

    def _filters(self):
        w = self.window
        layout = w.filter_group.layout()
        _clear_layout(layout)
        layout.setDirection(QHBoxLayout.Direction.TopToBottom)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)
        ivs = QGridLayout()
        ivs.setHorizontalSpacing(5)
        ivs.setVerticalSpacing(4)
        for row, title in enumerate(("IV", "下限", "上限")):
            label = QLabel(title)
            label.setObjectName("WorkspaceHint")
            ivs.addWidget(label, row, 0)
        for index, title in enumerate(("HP", "攻击", "防御", "特攻", "特防", "速度"), 1):
            label = QLabel(title)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ivs.addWidget(label, 0, index)
            ivs.setColumnStretch(index, 1)
            for row, field in enumerate((w.iv_min[index - 1], w.iv_max[index - 1]), 1):
                field.setMinimumWidth(0)
                field.setMaximumWidth(16777215)
                field.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                field.setAlignment(Qt.AlignmentFlag.AlignCenter)
                field.show()
                ivs.addWidget(field, row, index)
        layout.addLayout(ivs)
        choices = QGridLayout()
        for index, (title, field) in enumerate((("特性", w.ability_filter), ("性别", w.gender_filter), ("性格", w.nature_combo), ("异色", w.shiny_filter))):
            choices.addWidget(_field(title, field), index // 2, index % 2)
        layout.addLayout(choices)
        w.size_filter_toggle = QToolButton()
        w.size_filter_toggle.setText("体型筛选")
        w.size_filter_toggle.setCheckable(True)
        configure_disclosure_button(w.size_filter_toggle)
        layout.addWidget(w.size_filter_toggle)
        w.size_filter_group = QWidget()
        sizes = QGridLayout(w.size_filter_group)
        sizes.setContentsMargins(0, 0, 0, 0)
        for row, (title, minimum, maximum) in enumerate((("身高", w.height_min, w.height_max), ("体重", w.weight_min, w.weight_max))):
            sizes.addWidget(QLabel(title), row, 0)
            for column, field in ((1, minimum), (3, maximum)):
                field.setMinimumWidth(0)
                field.setMaximumWidth(16777215)
                field.show()
                sizes.addWidget(field, row, column)
            sizes.addWidget(QLabel("—"), row, 2)
        layout.addWidget(w.size_filter_group)
        w.size_filter_group.hide()
        w.size_filter_toggle.toggled.connect(w.size_filter_group.setVisible)
        w.size_filter_toggle.toggled.connect(lambda visible: self.reveal(w.height_min) if visible else None)
        self.filter_header = QWidget()
        header = QHBoxLayout(self.filter_header)
        header.setContentsMargins(0, 0, 0, 0)
        w.filter_toggle = QToolButton()
        w.filter_toggle.setText("筛选条件")
        w.filter_toggle.setCheckable(True)
        configure_disclosure_button(w.filter_toggle)
        header.addWidget(w.filter_toggle)
        header.addStretch(1)
        w.iv_calculator_button.setMinimumWidth(0)
        w.iv_calculator_button.setMaximumWidth(16777215)
        header.addWidget(w.iv_calculator_button)
        w.iv_calculator_button.show()
        w.filter_group.hide()
        w.filter_toggle.toggled.connect(w.filter_group.setVisible)
        w.filter_toggle.toggled.connect(lambda visible: self.reveal(w.iv_min[0] if visible else w.filter_toggle))

    def reveal(self, control):
        area = self.window.bdsp_config_scroll
        QTimer.singleShot(0, area, lambda: area.ensureWidgetVisible(control, 0, 8))

    def _result_actions(self):
        w = self.window
        for control in (w.copy_button, w.export_button):
            w.results_toolbar.insertWidget(w.results_toolbar.count() - 1, control)
        meta = QWidget()
        row = QHBoxLayout(meta)
        row.setContentsMargins(0, 0, 0, 0)
        w.results_toolbar.removeWidget(w.result_count)
        row.addWidget(w.result_count)
        row.addStretch(1)
        row.addWidget(w.show_stats_check)
        w.show_stats_check.show()
        w.results_panel.layout().insertWidget(w.results_panel.layout().indexOf(w.table), meta)

    def reflow(self):
        w = self.window
        wide = self.page.width() >= 540
        w.copy_button.setVisible(wide)
        w.export_button.setVisible(wide)
        reserve = min(180, max(90, int(self.page.height() * .28)))
        w.table.setMinimumHeight(reserve)
        available = self.page.height() - w.profile_group.sizeHint().height() - reserve - 90
        w.bdsp_config_scroll.setMaximumHeight(max(80, min(360, available)))
        self.refresh_profile()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.reflow()
        return False
