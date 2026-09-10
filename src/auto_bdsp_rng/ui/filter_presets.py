"""User-owned filter schemes; applying a scheme only edits the existing form."""
import json

from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox, QToolButton
from auto_bdsp_rng.ui.table_workbench import TABLE_TOOL_STYLE


class FilterPresetButton(QToolButton):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.settings = window._profile_settings
        self.setText("筛选方案")
        self.setFixedHeight(32)
        self.setStyleSheet(TABLE_TOOL_STYLE)
        self.setToolTip("保存或恢复当前筛选条件；应用后点击生成。Seed、训练家及目标精灵保持当前选择。")
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu = QMenu(self)
        self.setMenu(self.menu)
        self.menu.aboutToShow.connect(self._rebuild)

    def controls(self):
        w = self.window
        return {
            **{f"iv_min_{i}": control for i, control in enumerate(w.iv_min)},
            **{f"iv_max_{i}": control for i, control in enumerate(w.iv_max)},
            **{name: getattr(w, name) for name in ("height_min", "height_max", "weight_min", "weight_max",
                                                   "ability_filter", "gender_filter", "nature_combo", "shiny_filter", "skip_filter")},
        }

    def values(self):
        values = {}
        for key, control in self.controls().items():
            if hasattr(control, "currentData"):
                values[key] = control.currentData()
            elif hasattr(control, "isChecked"):
                values[key] = control.isChecked()
            else:
                values[key] = int(control.text() or 0)
        self.validate(values)
        return values

    def validate(self, values):
        controls = self.controls()
        if not isinstance(values, dict) or set(values) != set(controls):
            raise ValueError("筛选方案字段不完整，请重新保存。")
        for key, control in controls.items():
            value = values[key]
            if hasattr(control, "currentData"):
                if control.findData(value) < 0:
                    raise ValueError("筛选方案中的选项已不可用。")
            elif hasattr(control, "isChecked"):
                if type(value) is not bool:
                    raise ValueError("筛选开关无效。")
            elif type(value) is not int or not 0 <= value <= (31 if key.startswith("iv_") else 255):
                raise ValueError("个体值须为 0–31，身高 / 体重参数须为 0–255。")
        for base in [f"iv_{{}}_{i}" for i in range(6)] + ["height_{}", "weight_{}"]:
            if values[base.format("min")] > values[base.format("max")]:
                raise ValueError("筛选下限不能大于上限。")

    def schemes(self):
        try:
            value = json.loads(str(self.settings.value("filter_presets/v1", "{}")))
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}

    def save_named(self, name):
        name = name.strip()
        if not name or len(name) > 60:
            raise ValueError("方案名称须为 1–60 个字符。")
        values = self.values()
        schemes = self.schemes()
        schemes[name] = values
        self.settings.setValue("filter_presets/v1", json.dumps(schemes, ensure_ascii=False))
        self.settings.sync()

    def apply_named(self, name):
        values = self.schemes()[name]
        self.validate(values)  # Validate everything before editing any field.
        for key, control in self.controls().items():
            if hasattr(control, "currentData"):
                control.setCurrentIndex(control.findData(values[key]))
            elif hasattr(control, "isChecked"):
                control.setChecked(values[key])
            else:
                control.setText(str(values[key]))
        self.window.statusBar().showMessage(f"已应用筛选方案“{name}”，点击生成更新结果。", 6000)

    def _save(self):
        name, ok = QInputDialog.getText(self, "保存筛选方案", "方案名称（同名将覆盖）：")
        if not ok:
            return
        if name.strip() in self.schemes() and QMessageBox.question(self, "覆盖筛选方案", f"覆盖“{name.strip()}”？") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.save_named(name)
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))

    def _apply(self, name):
        try:
            self.apply_named(name)
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self, "无法应用", str(exc))

    def _delete(self, name):
        schemes = self.schemes()
        schemes.pop(name, None)
        self.settings.setValue("filter_presets/v1", json.dumps(schemes, ensure_ascii=False))

    def _rebuild(self):
        self.menu.clear()
        self.menu.addAction("保存当前筛选条件…", self._save)
        self.menu.addSeparator()
        names = list(self.schemes())
        if not names:
            self.menu.addAction("尚无方案，先设置条件再保存").setEnabled(False)
        for name in names:
            self.menu.addAction(name, lambda n=name: self._apply(n))
        if names:
            delete = self.menu.addMenu("删除方案")
            for name in names:
                delete.addAction(name, lambda n=name: self._delete(n))
