"""Use actual manual capture results to gate the next automation script."""
from PySide6.QtCore import Qt

from auto_bdsp_rng.ui.script_capture_steps import CAPTURE_PHASES


class ScriptCaptureGuide:
    def __init__(self, scripts):
        self.s = scripts
        self.c, self.w = scripts.c, scripts.window
        self.attempt = None
        self.result = None
        self.saved = None
        self.revision = 0
        self.error = ""
        self.w.manualCaptureStarted.connect(self._started)
        self.w.manualCaptureFinished.connect(self._finished)
        self.w.captureConfigSaved.connect(self._saved)
        self.w.captureConfigSaveFailed.connect(self._save_failed)
        self.w.captureSelectionStarted.connect(self._selection_started, Qt.ConnectionType.QueuedConnection)
        self.w.captureSelectionFinished.connect(self._selection_finished, Qt.ConnectionType.QueuedConnection)

    @property
    def active(self):
        return self.s.active and self.s.pos[1] in CAPTURE_PHASES

    def signature(self):
        w = self.w
        names = ("x", "y", "w", "h", "npc_count", "pokemon_npc", "timeline_npc", "advance_delay", "advance_delay_2")
        return (w.config_combo.currentData(), str(w._eye_image_path), self.revision,
                *(getattr(w, name).text() for name in names), w.threshold.value(), w.white_delay.value(),
                w.reidentify_1_pk_npc.isChecked())

    def enter(self, phase="capture_config"):
        self.attempt = self.result = self.saved = None
        self.error = ""
        self.s.go(self.s.pos[0], phase)

    def prepare(self):
        if not self.active:
            return False
        kind, phase = self.s.pos
        if phase == "capture_roi" and self.w._selection_mode not in ("eye", "roi"):
            self.s.go(kind, "capture_config")
            return True
        if phase in ("capture_wait", "capture_result") and self.attempt is None:
            self.s.go(kind, "capture_start")
            return True
        return False

    def _saved(self):
        if self.active:
            self.saved = self.signature()
            self.error = ""
            self.navigation()

    def _save_failed(self, message):
        if self.active:
            self.saved = None
            self.error = message
            self.navigation()

    def _selection_started(self, mode):
        if self.active and self.s.pos[1] in ("capture_config", "capture_roi") and mode in ("eye", "roi"):
            self.s.go(self.s.pos[0], "capture_roi")

    def _selection_finished(self, mode, ok):
        if mode in ("eye", "roi") and ok:
            self.revision += 1
        if self.active and self.s.pos[1] == "capture_roi":
            self.s.go(self.s.pos[0], "capture_roi" if self.w._selection_mode in ("eye", "roi") else "capture_config")

    def _started(self, mode, token):
        kind, phase = self.s.pos
        if not self.active or phase != "capture_start" or mode != ("reidentify" if kind == "advance" else "seed"):
            return
        self.attempt = (token, kind, self.signature())
        self.result = None
        self.error = ""
        self.s.go(kind, "capture_wait")

    def _finished(self, mode, token, ok, message):
        if self.attempt is None or token is not self.attempt[0]:
            return
        kind = self.attempt[1]
        if mode != ("reidentify" if kind == "advance" else "seed"):
            return
        ok = ok and self.attempt[2] == self.signature()
        self.result = (bool(ok), tuple(box.text() for box in self.w.seed32_inputs))
        self.error = message or ("" if ok else "捕捉期间配置发生变化，请确认配置后重试。")
        if self.active and self.s.pos == (kind, "capture_wait"):
            self.s.go(kind, "capture_result" if ok else "capture_failed")

    def succeeded(self):
        return bool(self.attempt and self.result and self.result[0] and self.attempt[2] == self.signature()
                    and self.result[1] == tuple(box.text() for box in self.w.seed32_inputs)
                    and not self.w._is_capturing())

    def navigation(self):
        if not self.active or self.c.overlay.waiting_for_page:
            return False
        tip = self.c.overlay.tip
        kind, phase = self.s.pos
        busy = self.w._is_capturing()
        tip.previous_button.setText("上一步")
        tip.previous_button.setVisible(phase not in ("capture_wait", "capture_result"))
        tip.previous_button.setEnabled(not busy)
        tip.skip_button.hide()
        tip.next_button.setText("下一步")
        tip.next_button.setEnabled(not busy and phase in ("capture_config", "capture_failed"))
        tip.status.setVisible(phase in ("capture_wait", "capture_save", "capture_failed"))
        if phase == "capture_config":
            tip.next_button.setText("配置没问题")
        elif phase == "capture_save":
            tip.status.setText(self.error or ("配置已保存。" if self.saved == self.signature() else "请先保存当前配置。"))
            tip.next_button.setEnabled(not busy and self.saved == self.signature())
        elif phase == "capture_result":
            tip.next_button.setText("确认成功，下一步")
            tip.next_button.setEnabled(self.succeeded())
            tip.previous_button.setText("修改配置")
            tip.previous_button.show()
            tip.status.setVisible(not self.succeeded())
            tip.status.setText("配置或 Seed 已变化，请点击「修改配置」重新验证。")
        elif phase == "capture_failed":
            tip.next_button.setText("修改配置")
            tip.previous_button.setText("修改过帧脚本" if kind == "advance" else "修改测种脚本")
            tip.status.setText(self.error or "本次未成功，请选择调整方向。")
        elif phase == "capture_wait":
            with self.w._capture_lock:
                done, total = self.w._capture_progress
            suffix = " · 请选择 Mock 结果" if self.w._mock_capture_dialog is not None else " · 正在计算结果" if done >= total else " · 请保持画面稳定"
            tip.status.setText(f"眨眼进度 {done}/{total}" + suffix)
        if tip.status.isVisible():
            self.c.overlay.relayout.start(0)
        return True

    def next(self):
        kind, phase = self.s.pos
        if phase in ("capture_config", "capture_save"):
            self.s.go(kind, "capture_save" if phase == "capture_config" else "capture_start")
        elif phase == "capture_result" and self.succeeded():
            self.s._next_kind()
        elif phase == "capture_failed":
            self.enter()

    def previous(self):
        kind, phase = self.s.pos
        if phase == "capture_failed":
            self.s.go(kind, "restore" if kind == "advance" else "retry")
        elif phase == "capture_config":
            self.s.go(kind, "restore" if kind == "advance" else "retry")
        elif phase == "capture_roi":
            self.w._cancel_preview_selection()
            self.s.go(kind, "capture_config")
        else:
            self.s.go(kind, "capture_config")

    def poll(self):
        if not self.active:
            return
        if self.s.pos[1] == "capture_wait" and self.result is not None:
            self.s.go(self.s.pos[0], "capture_result" if self.result[0] else "capture_failed")
        self.navigation()

    def pause(self):
        if self.c.step == "auto_script_config" and self.s.pos[1] == "capture_roi":
            self.w._cancel_preview_selection()
