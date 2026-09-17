"""Teach a single real run and show only that run's saved delay observation."""
from __future__ import annotations

from html import escape

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from auto_bdsp_rng.app_settings import complete_guide_progress
from auto_bdsp_rng.automation.auto_rng import AutoRngPhase, AutoRngProgress
from auto_bdsp_rng.data import get_static_encounters
from auto_bdsp_rng.ui.guide_steps import GuideStep
from auto_bdsp_rng.ui.target_dialog import POKEMON_LABELS_ZH


class FirstRunResultDialog(QDialog):
    def __init__(self, parent, title: str, value: str, copy: str, *, success: bool):
        super().__init__(parent)
        self.setWindowTitle("第一次自动乱数 · delay")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setFixedWidth(min(440, self.screen().availableGeometry().width() - 40))
        self.setStyleSheet("""
            QDialog { background: white; }
            QLabel { color: #52606D; font: 13px 'Microsoft YaHei UI'; }
            QLabel#ResultTitle { color: #202A33; font-size: 20px; font-weight: 600; }
            QLabel#ResultValue { color: #087C58; font-size: 30px; font-weight: 600; }
            QPushButton { background: white; color: #34453E; border: 1px solid #DCE5E0;
                          border-radius: 7px; padding: 8px 12px; min-height: 20px; }
            QPushButton:hover { background: #F0F8F4; }
            QPushButton:focus { border: 1px solid #087C58; outline: 0; }
            QPushButton:disabled { color: #8C9791; background: #F7F9F8; }
            QPushButton#ResultPrimary { background: #087C58; color: white; border-color: #087C58; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 22)
        layout.setSpacing(14)
        self.title = QLabel(title)
        self.title.setObjectName("ResultTitle")
        self.value = QLabel(value)
        self.value.setObjectName("ResultValue")
        self.copy = QLabel(copy)
        for label in (self.title, self.value, self.copy):
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(label)
        self.value.setVisible(bool(value))
        self.primary = QPushButton("查看最近 delay" if success else "返回准备，重新运行")
        self.primary.setObjectName("ResultPrimary")
        self.logs = QPushButton("查看日志中心")
        self.dismiss = QPushButton("知道了" if success else "稍后再试")
        for button in (self.primary, self.logs, self.dismiss):
            layout.addWidget(button)


class SampleRowAnchor(QWidget):
    """Follow the actual table cells when the dialog resizes or scrolls."""

    def __init__(self, table, row):
        super().__init__(table.viewport())
        self.table, self.row = table, row
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        table.viewport().installEventFilter(self)
        table.horizontalHeader().sectionResized.connect(self.update_cells)
        table.verticalScrollBar().valueChanged.connect(self.update_cells)
        self.update_cells()
        self.show()

    def update_cells(self, *_):
        self.setGeometry(self.table.visualRect(self.table.model().index(self.row, 0)).united(
            self.table.visualRect(self.table.model().index(self.row, 1))))

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.Show):
            self.update_cells()
        return False


class FirstRunGuide(QObject):
    def __init__(self, controller):
        super().__init__(controller)
        self.c = controller
        self.panel, self.window = controller.panel, controller.window
        self.result_dialog = None
        self.samples_overlay = None
        self.sample_anchor = None
        self.preparation_timer = QTimer(self)
        self.preparation_timer.setInterval(100)
        self.preparation_timer.timeout.connect(self._check_preparation)
        self.reset()
        self.panel.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.panel.startRequested.connect(self._requested)
        self.panel.runStarted.connect(self._started)
        self.panel.runFinished.connect(self._finished)
        self.panel.runStateChanged.connect(self._state_changed)

    def reset(self):
        self.preparation_timer.stop()
        self.preparing = False
        self.watching = False
        self.config = None
        self.baseline = ()
        self.outcome = None
        self.sample = None
        self.result = None
        self.run_id = None
        self.species_name = ""
        self.stopped = False
        if self.result_dialog is not None:
            self.result_dialog.close()

    @property
    def active(self):
        return self.c.active and self.c.step == "first_auto_run"

    def prepare(self):
        if self.c.detail in ("result", "retry") and self.result is not None:
            self.c.pause()
            self.show_result()
            return True
        # A restarted process has no live worker or trustworthy run snapshot.
        detail = "start" if self.panel.mode_combo.currentData() == "single" else "mode"
        if self.c.detail != detail:
            if not self.c._persist("first_auto_run", detail):
                self.c.pause()
                return True
        return False

    def navigation(self):
        if not self.active:
            return
        tip = self.c.overlay.tip
        tip.skip_button.hide()
        tip.next_button.setEnabled(False)
        tip.next_button.setText("请点击开始" if self.c.detail == "start" else "请选择单次")
        tip.previous_button.setText("返回脚本配置")

    def _mode_changed(self, *_):
        if self.active:
            self.c._show_workspace()

    def _requested(self, config):
        if not self.active or self.c.detail != "start" or config.loop_mode != "single":
            return
        # Warmup can precede runStarted. Keep Stop and all normal UI usable.
        if self.panel._preparing and self.panel._runner_thread is None:
            self.preparing = True
            self.config = config
            self.c.pause()
            self.preparation_timer.start()

    def _check_preparation(self):
        if self.window._is_closing or not self.preparing or not self.panel._preparing:
            self.preparation_timer.stop()
            resume = self.preparing and not self.window._is_closing
            self.preparing = False
            if resume and self.c.step == "first_auto_run":
                self.c.begin_or_resume()

    def _started(self, config):
        expected = self.c.step == "first_auto_run" and (self.preparing or (self.active and self.c.detail == "start"))
        self.preparation_timer.stop()
        self.preparing = False
        if not expected or config is None or config.loop_mode != "single":
            return
        self.config = config
        self.baseline = tuple(self.panel.delay_sample_records(config.target_species))
        record = next((record for record in get_static_encounters()
                       if int(record.template.species) == config.target_species), None)
        self.species_name = POKEMON_LABELS_ZH.get(record.description, record.description) if record else "本次精灵"
        self.run_id = getattr(self.window, "_active_auto_rng_run_id", None)
        self.outcome = self.sample = self.result = None
        self.stopped = False
        self.watching = True
        self.c._persist("first_auto_run", "running")

    def _finished(self, progress):
        if self.watching:
            self.outcome = progress
            self.stopped = self.panel._stop_pending

    def _state_changed(self, running):
        if running or not self.watching:
            return
        self.watching = False
        # runStateChanged(False) is emitted only after QThread has exited.
        # Defer the card until the owner's header/control cleanup is complete.
        QTimer.singleShot(0, self._settled)

    def _settled(self):
        if self.window._is_closing or self.c.step != "first_auto_run" or self.config is None:
            return
        completed = not self.stopped and isinstance(self.outcome, AutoRngProgress) and self.outcome.phase == AutoRngPhase.COMPLETED
        samples = [sample for sample in self.panel.delay_sample_records(self.config.target_species)
                   if sample not in self.baseline and not sample.excluded]
        self.sample = samples[-1] if completed and len(samples) == 1 else None
        success = self.sample is not None and len(self.sample.candidates) == 1
        if success:
            self.result = (True, "本轮实际 delay 已查到", f"{self.sample.candidates[0]} 帧",
                           f"已保存到「{self.species_name}」的最近 delay 样本。\n"
                           "点击「查看最近 delay」，认识以后查询这个数值的位置。")
        elif not completed:
            stopped = self.stopped or (isinstance(self.outcome, AutoRngProgress) and self.outcome.phase == AutoRngPhase.IDLE)
            self.result = (False, "本次运行已停止" if stopped else "本次运行未完成", "",
                           "本次没有可用于完成教学的 delay 结果。可以先到日志中心查看原因，调整后再单次运行。")
        elif self.sample is not None:
            candidates = self.sample.candidates
            values = " / ".join(map(str, candidates[:3])) + (" …" if len(candidates) > 3 else "")
            self.result = (False, "本轮 delay 还不能唯一确定", values + " 帧",
                           "本轮反查得到多个候选，暂时不要随意选一个填写。请核对 OCR 识别信息，再运行一轮确认。")
        elif "疑似出闪" in self.outcome.log_message:
            self.result = (False, "流程已因疑似出闪停止", "",
                           "请先确认游戏中的精灵。本轮没有执行自动反查，因此没有新的 delay 样本；请保留当前游戏现场，确认后再决定是否继续。")
        else:
            self.result = (False, "本轮尚未获得有效 delay", "",
                           "请先确认已开启自动反查并选择反查脚本，再核对 OCR、目标和同步设置。"
                           "这些都正确却仍查不到时，可适当扩大反查范围后重试。")
        if self.c._persist("first_auto_run", "result" if success else "retry"):
            self.show_result()

    def show_result(self):
        if self.window._is_closing or self.result is None:
            return
        if self.result_dialog is not None:
            self.result_dialog.raise_()
            return
        success, title, value, copy = self.result
        same_species = self.config.target_species == self.panel._delay_species_id
        recent = self.sample in self.panel.delay_sample_records(self.config.target_species)[-5:]
        if success and not same_species:
            copy += "\n当前目标已切换。请先选回本次精灵，再点击「继续引导」查看对应样本。"
        elif success and not recent:
            copy += "\n本次样本已不在最近记录中，请到日志中心查看本次运行记录。"
        dialog = FirstRunResultDialog(self.window, title, value, copy, success=success)
        self.result_dialog = dialog
        if "疑似出闪" in title:
            dialog.primary.setText("返回准备")
        dialog.primary.setEnabled(not success or (same_species and recent))
        dialog.primary.clicked.connect(self.view_samples if success else self.retry)
        dialog.logs.clicked.connect(self.view_logs)
        dialog.dismiss.clicked.connect(self.complete if success else dialog.reject)
        dialog.finished.connect(lambda _result: self._closed(dialog))
        dialog.open()

    def _closed(self, dialog):
        if self.result_dialog is dialog:
            self.result_dialog = None
        dialog.deleteLater()

    def retry(self):
        self.result_dialog.reject()
        if self.c._persist("first_auto_run", "mode"):
            self.c.begin_or_resume()

    def view_logs(self):
        self.result_dialog.reject()
        self.window.tabs.setCurrentWidget(self.window.run_records_tab)
        self.window.run_records_tab.show_logs("自动定点", run_id=self.run_id)

    def complete(self):
        try:
            complete_guide_progress()
        except (OSError, ValueError):
            QMessageBox.warning(self.window, "引导尚未完成", "无法保存引导完成状态，请检查设置目录后重试。")
            return
        if self.result_dialog is not None:
            self.result_dialog.accept()
        if self.samples_overlay is not None:
            self.panel.delay_strategy_dialog.reject()
        self.c.pause()
        self.c.refresh()
        self.result = None

    def view_samples(self):
        # Read the actual sample table; never create a demonstration sample.
        self.result_dialog.reject()
        if self.config.target_species != self.panel._delay_species_id:
            self.show_result()
            return
        self.window.tabs.setCurrentWidget(self.panel)
        self.panel.guideDialogOpened.connect(self._show_sample_guide)
        try:
            self.panel.open_delay_strategy_dialog()
        finally:
            QApplication.instance().removeEventFilter(self)
            self.panel.guideDialogOpened.disconnect(self._show_sample_guide)
            if self.samples_overlay is not None:
                self.samples_overlay.hide()
                self.samples_overlay.deleteLater()
                self.samples_overlay = None
            if self.sample_anchor is not None:
                self.sample_anchor.deleteLater()
                self.sample_anchor = None

    def _show_sample_guide(self, key):
        if key != "delay_strategy":
            return
        QTimer.singleShot(0, self._highlight_sample)

    def _highlight_sample(self):
        from auto_bdsp_rng.ui.guide import GuideSpotlight

        dialog = self.panel.delay_strategy_dialog
        if not dialog.isVisible() or self.result is None:
            return
        dialog._show_settings()
        dialog.samples_toggle.setChecked(True)
        dialog.layout().activate()
        dialog.body_widget.layout().activate()
        dialog.body_scroll.ensureWidgetVisible(dialog.recent_samples_table)
        table = dialog.recent_samples_table
        recent = list(reversed(self.panel.delay_sample_records()[-5:]))
        if self.sample not in recent:
            return
        row = recent.index(self.sample)
        # Only this run's round and delay cells are lit;
        # sample exclusions and strategy edits are not part of this short tour.
        table.scrollToItem(table.item(row, 0))
        self.sample_anchor = SampleRowAnchor(table, row)
        overlay = GuideSpotlight(self.window, dialog)
        self.samples_overlay = overlay
        overlay.configure(GuideStep("first_delay_sample", "第 6 步 · 查看 delay", "以后在这里查看实际 delay",
                                   f'这是「{escape(self.species_name)}」本轮反查的 <span style="color:#087C58; font-weight:600">'
                                   f'{self.sample.candidates[0]} 帧</span>。\n'
                                   "以后点击「delay 策略」，展开「当前精灵的样本」就能查看；日志中心也会记录反查结果。\n"
                                   "固定策略不会自动改用样本；你可以将它填入基准 delay，或选择动态策略参考有效样本。",
                                   self.sample_anchor, (self.sample_anchor,)))
        overlay.tip.previous_button.hide()
        overlay.tip.skip_button.hide()
        overlay.tip.next_button.setText("知道了，完成引导")
        overlay.tip.next_button.clicked.connect(self.complete)
        overlay.paused.connect(dialog.reject)
        overlay.reveal()
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event):
        overlay = self.samples_overlay
        kinds = (QEvent.Type.KeyPress, QEvent.Type.KeyRelease, QEvent.Type.ShortcutOverride,
                 QEvent.Type.Shortcut, QEvent.Type.Wheel)
        if event.type() not in kinds:
            return False
        if overlay is None or not isinstance(obj, QWidget) or QWidget.window(obj) is not self.panel.delay_strategy_dialog:
            return False
        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.panel.delay_strategy_dialog.reject()
                return True
            if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                target = overlay.tip.next_button if QApplication.focusWidget() is not overlay.tip.next_button else overlay.close_button
                target.setFocus(Qt.FocusReason.TabFocusReason)
                return True
        return not (obj is overlay.tip or overlay.tip.isAncestorOf(obj))
