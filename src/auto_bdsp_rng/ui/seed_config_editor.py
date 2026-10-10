"""Edit a task's Project_Xs file in place, using the existing capture controls."""
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QSignalBlocker, QTimer, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout

from auto_bdsp_rng.blink_detection import ProjectXsIntegrationError, load_project_xs_config
from auto_bdsp_rng.ui.workspace_controls import configure_disclosure_button
from auto_bdsp_rng.ui.workspace_theme import ui_styles


class SeedConfigEditorController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.binding = None
        self.panel = None
        self.previous_config = None
        self.previous_selection = None
        self.eye_drafts = set()
        self.testing = False
        self.start_tooltips = []
        self.editor = QFrame(window)
        self.editor.setObjectName("SeedConfigEditor")
        self.editor.hide()
        layout = QVBoxLayout(self.editor)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.title = QLabel()
        self.title.setObjectName("SeedEditorTitle")
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.title)
        note = QLabel("保存后影响所有使用此配置的任务；取消会放弃本次参数和框选修改。")
        note.setObjectName("SeedEditorNote")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.form_layout = QVBoxLayout()
        self.form_layout.setContentsMargins(0, 0, 0, 0)
        self.form_layout.setSpacing(10)
        layout.addLayout(self.form_layout)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("高级时序参数")
        self.advanced_toggle.setCheckable(True)
        configure_disclosure_button(self.advanced_toggle)
        self.advanced_toggle.toggled.connect(window.capture_timing_group.setVisible)
        self.form_layout.addWidget(self.advanced_toggle)
        self.status = QLabel()
        self.status.setObjectName("SeedEditorNote")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.test_button = QPushButton("测试捕捉")
        self.test_button.clicked.connect(self.test_capture)
        self.save_button = QPushButton("保存")
        self.save_button.clicked.connect(window.save_current_config)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self.cancel)
        actions.addWidget(self.test_button)
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self.editor.setStyleSheet(ui_styles('''
            QFrame#SeedConfigEditor {
                background: #F8FAFB; border: 1px solid #DDE5EB; border-radius: 8px;
            }
            QLabel#SeedEditorTitle { font-size: 13px; font-weight: 500; }
            QLabel#SeedEditorNote { color: #64707D; font-size: 11px; background: transparent; }
            QFrame#SeedConfigEditor QPushButton { min-height: 28px; padding: 0 8px; }
        '''))
        window.captureConfigSaved.connect(self._saved)
        window.captureConfigSaveFailed.connect(self._save_failed)
        window.manualCaptureFinished.connect(self._capture_finished)
        window.captureSelectionStarted.connect(lambda _mode: self.refresh())
        window.captureSelectionFinished.connect(lambda _mode, _ok: self.refresh())
        window._capture_timer.timeout.connect(self.refresh)
        window.tabs.currentChanged.connect(lambda _index: self.refresh())

    @property
    def active(self):
        return self.binding is not None

    def _bindings(self):
        w = self.window
        return (w.auto_rng_tab.seed_config_binding, w.auto_tid_rng_tab.seed_config_binding,
                w.auto_rng_tab.reidentify_config_binding)

    def open(self, binding, path):
        w = self.window
        if self.active:
            self.reveal()
            w.statusBar().showMessage("请先保存或取消当前配置编辑，再编辑其他配置。")
            return
        if w._capture_thread is not None or w._selection_mode is not None or any(
            p._preparing or p._runner_thread is not None for p in (w.auto_rng_tab, w.auto_tid_rng_tab)
        ):
            w.statusBar().showMessage("请先结束捕捉、框选或自动任务，再编辑配置。")
            return
        try:
            target = load_project_xs_config(path)
            self.previous_config = w._config_from_form()
        except (ProjectXsIntegrationError, OSError, ValueError) as exc:
            w._show_error("打开配置失败", exc)
            return
        self.binding = binding
        self.previous_selection = (w.config_combo.currentData(), w.config_combo.currentText())
        self.panel = w.auto_tid_rng_tab if binding is w.auto_tid_rng_tab.seed_config_binding else w.auto_rng_tab
        self.testing = False
        self.eye_drafts.clear()
        index = w.config_combo.findData(path)
        if index < 0:
            w.config_combo.addItem(Path(path).name, path)
            index = w.config_combo.count() - 1
        with QSignalBlocker(w.config_combo):
            w.config_combo.setCurrentIndex(index)
        w._apply_capture_config_to_form(target)
        self.title.setText(f"配置编辑 · {Path(path).name}")
        self.title.setToolTip(str(path))
        self.status.setText("使用当前参数测试捕捉；参数在点击保存后写入配置文件。")
        binding.layout().addWidget(self.editor)
        self.form_layout.insertWidget(0, w.capture_recognition_group)
        self.form_layout.addWidget(w.capture_timing_group)
        w.capture_recognition_group.show()
        self.advanced_toggle.setChecked(True)
        w.capture_timing_group.show()
        self.editor.show()
        self.start_tooltips = [
            (control, control.toolTip())
            for panel in (w.auto_rng_tab, w.auto_tid_rng_tab)
            for control in (panel.start_button, *panel.start_menu.actions())
        ]
        for view in self._bindings():
            view.selector.setEnabled(False)
        w.config_combo.setEnabled(False)
        self.refresh()
        w._refresh_automation_start_state()
        self.reveal()

    def reveal(self):
        if not self.active:
            return
        w = self.window
        w.tabs.setCurrentWidget(self.panel)
        w.reveal_page_configuration(self.panel)
        self.panel.config_groups.reveal(self.binding)
        scroll = self.panel.config_scroll if self.panel is w.auto_tid_rng_tab else self.panel.config_panel
        def scroll_to_editor():
            if self.active and self.editor.isVisible():
                top = self.binding.mapTo(scroll.widget(), QPoint(0, 0)).y()
                scroll.verticalScrollBar().setValue(max(0, top - 24))
        QTimer.singleShot(0, self.editor, scroll_to_editor)

    def refresh(self):
        if not self.active:
            return
        w = self.window
        capturing = w._capture_thread is not None
        selecting = w._selection_mode is not None
        self.test_button.setText("停止捕捉" if capturing else "测试捕捉")
        self.test_button.setEnabled(not selecting)
        self.save_button.setEnabled(not capturing and not selecting)
        self.cancel_button.setEnabled(not capturing)
        w.capture_recognition_group.setEnabled(not capturing)
        w.capture_timing_group.setEnabled(not capturing)
        w._refresh_capture_config_summary()
        w.video_overlay.set_configuration_mode(
            self.editor.isVisible() or w.tabs.currentWidget() is w.project_xs_tab
        )

    def test_capture(self):
        w = self.window
        if w._capture_thread is not None:
            w._capture_cancel.set()
            self.status.setText("正在停止测试捕捉…")
            return
        if not self.active or w._selection_mode is not None:
            return
        if w._automation_script_busy():
            self.status.setText("请等待伊机控脚本结束后再测试捕捉。")
            return
        self.testing = True
        self.status.setText("正在测试捕捉，进度和结果显示在右侧视频。")
        if self.binding is w.auto_tid_rng_tab.seed_config_binding:
            w.capture_tidsid_seed()
        elif self.binding is w.auto_rng_tab.reidentify_config_binding:
            w.reidentify_seed()
        else:
            w.capture_seed()
        if w._capture_thread is None:
            self.testing = False
            self.status.setText("捕捉未启动，请检查视频源和配置。")
        self.refresh()

    def _capture_finished(self, _mode, _thread, success, message):
        if self.active and self.testing:
            self.testing = False
            self.status.setText("测试捕捉成功。" if success else f"测试捕捉未完成：{message}")
            self.refresh()

    def _saved(self):
        if self.active:
            self.close(saved=True)

    def _save_failed(self, message):
        if self.active:
            self.status.setText(f"保存失败：{message}")

    def cancel(self):
        if self.active and self.window._capture_thread is None:
            if self.window._selection_mode is not None:
                self.window._cancel_preview_selection()
            self.close(saved=False)

    def close(self, *, saved):
        w = self.window
        self.editor.hide()
        for index, group in enumerate((w.capture_recognition_group, w.capture_timing_group)):
            w.project_xs_config_scroll.widget().layout().insertWidget(index, group)
            group.setEnabled(True)
            group.show()
        self.binding.layout().removeWidget(self.editor)
        self.editor.setParent(w)
        keep = w._eye_image_path if saved else None
        for path in self.eye_drafts - {keep}:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                w._write_run_log("Seed 捕捉", f"清理眼睛模板草稿失败：{exc}", level="WARNING")
        self.eye_drafts.clear()
        if not saved:
            previous = self.previous_config
            with QSignalBlocker(w.config_combo):
                data, text = self.previous_selection
                index = w.config_combo.findData(data) if data is not None else -1
                if data is not None and index < 0:
                    w.config_combo.addItem(Path(str(data)).name, data)
                    index = w.config_combo.count() - 1
                w.config_combo.setCurrentIndex(index)
                if data is None:
                    w.config_combo.setEditText(text)
            w._apply_capture_config_to_form(previous)
        self.binding = self.panel = self.previous_config = None
        self.previous_selection = None
        for view in self._bindings():
            view.selector.setEnabled(True)
        w.config_combo.setEnabled(True)
        for control, tooltip in self.start_tooltips:
            control.setToolTip(tooltip)
        self.start_tooltips.clear()
        w.video_overlay.set_configuration_mode(w.tabs.currentWidget() is w.project_xs_tab)
        w._refresh_task_config_bindings()
        w._refresh_automation_start_state()

    def eye_output_path(self, directory, config_name):
        """A draft eye must not overwrite the template of a saved configuration."""
        import uuid

        path = directory / f"{config_name}_eye_{uuid.uuid4().hex[:12]}.png"
        self.eye_drafts.add(path)
        return path
