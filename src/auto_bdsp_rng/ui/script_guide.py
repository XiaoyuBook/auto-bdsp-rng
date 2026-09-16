"""One script at a time: choose, inspect, run, confirm the game, then return."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from auto_bdsp_rng.ui.script_guide_steps import PRACTICE_KINDS, SCRIPT_KINDS, position, script_run_target
from auto_bdsp_rng.ui.ocr_guide import OcrGuide
from auto_bdsp_rng.ui.script_guide_preview import ScriptGuidePreview
from auto_bdsp_rng.ui.script_capture_guide import ScriptCaptureGuide
from auto_bdsp_rng.ui.script_capture_steps import CAPTURE_PHASES
from auto_bdsp_rng.automation.easycon import parse_script_parameters


EDIT_PHASES = {"edit", "edit_second", "advance_frames", "retry"}
PREVIEW_PHASES = {"run", "review", "restore", "ball_check", "ball_restore", "capture_config", "capture_save", "capture_start", "capture_wait"} | EDIT_PHASES
CONTROL_PHASES = {"restore", "ball_check", "ball_restore"}


def same_path(first, second) -> bool:
    return bool(first and second and Path(first).resolve() == Path(second).resolve())


class ScriptGuide(QObject):
    def __init__(self, controller) -> None:
        super().__init__(controller)
        self.c = controller
        self.window, self.panel = controller.window, controller.panel
        self.easycon = self.window.easycon_tab
        self.attempt = None
        self._positioned = None
        self._opening = False
        self._pending_skip = None
        self.ocr = OcrGuide(controller)
        self.preview = ScriptGuidePreview(controller)
        self.capture = ScriptCaptureGuide(self)
        self.panel.scriptEditRequested.connect(self._opened)
        self.easycon.nativeScriptStarted.connect(self._started)
        self.easycon.save_button.clicked.connect(lambda: QTimer.singleShot(0, self.poll))

    @property
    def active(self):
        return self.c.active and self.c.step == "auto_script_config"

    @property
    def pos(self):
        return position(self.c.detail)

    @property
    def replay_target(self):
        parts = self.c.detail.split(":")
        return parts[2] if len(parts) > 2 and parts[2] in ("hit", "reverse") else ""

    def selected(self):
        kind, _ = self.pos
        combo = getattr(self.panel, f"{kind}_script_combo", None)
        return self.panel._selected_path(combo) if combo is not None else None

    def page(self):
        kind, phase = self.pos
        if self.ocr.needs_control:
            return self.easycon
        if phase in CAPTURE_PHASES:
            return self.window.project_xs_tab
        return self.easycon if kind in PRACTICE_KINDS and phase != "select" else self.panel

    def go(self, kind, phase="select", *, replay=None):
        if self.pos[0] == "ocr" and kind != "ocr" and not self.leave_control():
            return
        if self.pos[1] in EDIT_PHASES and phase not in EDIT_PHASES and not self.leave_control():
            return
        self._positioned = None
        target = self.replay_target if replay is None else replay
        self.c._go("auto_script_config", f"{kind}:{phase}" + (":" + target if target else ""))

    def prepare(self):
        if not self.active or self.pos[0] not in PRACTICE_KINDS or self.pos[1] not in PREVIEW_PHASES:
            self.preview.restore()
        if not self.active:
            return False
        if self.page() is self.panel and not self.panel._runtime_script_editor_expanded:
            self.panel._toggle_runtime_script_editor()
            QTimer.singleShot(80, self._settled)
        if self.ocr.prepare():
            return True
        if self.capture.prepare():
            return True
        kind, phase = self.pos
        if phase in CAPTURE_PHASES:
            return False
        if phase == "replay_open":
            return False
        if kind not in PRACTICE_KINDS or phase == "select" or self._opening:
            return False
        # Do not trust a saved line/run marker when the editor or process changed.
        if not same_path(self.selected(), self.easycon.current_script_path):
            self.go(kind)
            return True
        if phase == "review" and self.attempt is None:
            self.go(kind, "edit")
            return True
        if phase in ("retry", "advance_frames"):
            self.easycon.execution.follow.setChecked(False)
            self.easycon.execution.show_current()
        return False

    def _opened(self, _path=None):
        if not self.active or self.pos[1] != "select" or self.pos[0] not in PRACTICE_KINDS:
            return
        if same_path(self.selected(), self.easycon.current_script_path) and self.window.tabs.currentWidget() is self.easycon:
            self.easycon.execution.show_current()
            self.attempt = None
            phase = ("advance_frames" if self.pos[0] == "advance" else "run") if self.replay_target else "ball_check" if self.pos[0] == "reverse" else "edit"
            self.go(self.pos[0], phase)

    def _settled(self):
        if self.active and self.c.overlay.isVisible() and not self.c.overlay.suspended:
            self.c.overlay.reposition()

    def _open(self):
        path = self.selected()
        if path is None or not path.is_file() or self.easycon._controller_script_running() or self.easycon._recording:
            return
        self._opening = True
        try:
            # This existing entry preserves Save/Discard/Cancel for unsaved drafts.
            self.window._open_automation_script_editor(path)
        finally:
            self._opening = False
        self._opened(path)

    def open_replay(self):
        path = self.selected()
        if path is None or not path.is_file():
            self.go(self.pos[0], "select")
            return
        self._opening = True
        try:
            self.window._open_automation_script_editor(path)
        finally:
            self._opening = False
        if same_path(path, self.easycon.current_script_path):
            self.easycon.execution.follow.setChecked(False)
            self.easycon.execution.show_current()
            self.go(self.pos[0], "advance_frames" if self.pos[0] == "advance" else "run")

    def next_replay(self):
        kind, _ = self.pos
        target = self.replay_target
        if kind == target:
            self._next_kind()
            return
        order = ("seed", "advance", "hit", "reverse")
        self.attempt = None
        self.go(order[order.index(kind) + 1], "replay_open")
        self.open_replay()

    def present(self):
        if not self.active or self.c.overlay.waiting_for_page:
            return
        kind, phase = self.pos
        if kind in PRACTICE_KINDS and phase in PREVIEW_PHASES:
            self.preview.show(kind)
        if phase in ("edit", "edit_second"):
            path = self.selected()
            marker = (kind, phase, str(path))
            if marker != self._positioned:
                self._positioned = marker
                self.easycon.execution.follow.setChecked(False)
                self.easycon.execution.show_current()
                expected = {"seed": ("bdsp测种.txt", 24), "advance": ("bdsp过帧.txt", 7 if phase == "edit" else 127),
                            "reverse": ("捕捉反查脚本.txt", 4)}.get(kind)
                if expected and path and path.name.casefold() == expected[0]:
                    self.easycon.editor.go_to_line(expected[1])
        elif phase in ("retry", "advance_frames"):
            self.easycon.execution.follow.setChecked(False)
            self.easycon.execution.show_current()
            if phase == "advance_frames":
                parameter = next((p for p in parse_script_parameters(self.easycon.editor.toPlainText()) if p.name == "_目标帧数"), None)
                if parameter is not None:
                    self.easycon.editor.go_to_line(parameter.line_index + 1)
        self.navigation()

    def frames_ready(self):
        parameters = parse_script_parameters(self.easycon.editor.toPlainText())
        field = next((p for p in parameters if p.name == "_目标帧数"), None)
        return field is None or field.value.strip() == "1000"

    @property
    def controlling(self):
        return self.active and (self.pos[1] in CONTROL_PHASES or
                                (self.ocr.active and bool(self.easycon._vpad_input_source)) or
                                (self.pos[1] in EDIT_PHASES and bool(self.easycon._vpad_input_source)))

    def leave_control(self):
        return self.easycon._deactivate_virtual_controller(hide_overlay=True, log=False)

    def snapshot(self):
        if self.attempt is None:
            return None
        trace, baseline, path, text = self.attempt
        if trace is not getattr(self.easycon.native_backend, "execution_trace", None):
            return None
        snap = trace.snapshot()
        if snap.run_id != baseline + 1 or not same_path(snap.source, path):
            return None
        return snap

    def completed(self):
        snap = self.snapshot()
        source = next((text for path, text in snap.sources if same_path(path, self.selected())), None) if snap else None
        return bool(snap and snap.point and snap.state == "completed" and not self.easycon._controller_script_running()
                    and same_path(self.selected(), self.attempt[2])
                    and source is not None and self.easycon.editor.toPlainText() == source.replace('\r\n', '\n').replace('\r', '\n'))

    def finished(self):
        snap = self.snapshot()
        return bool(snap and snap.state in ("completed", "failed", "stopped")
                    and not self.easycon._controller_script_running())

    def navigation(self):
        if not self.active or self.c.overlay is None:
            return
        if self.ocr.control_navigation():
            return
        if self.ocr.selecting or (self.pos[0] == "ocr" and self.pos[1] != "select"):
            return
        tip = self.c.overlay.tip
        if self.c.overlay.waiting_for_page:
            return
        if self.capture.navigation():
            return
        kind, phase = self.pos
        tip.previous_button.show()
        tip.skip_button.show()
        tip.previous_button.setText("脚本有问题" if phase == "review" else "上一步")
        tip.next_button.setText("下一步")
        tip.skip_button.setText("跳过" if phase == "review" else "跳过本脚本" if kind in PRACTICE_KINDS else "跳过")
        busy = self.easycon._controller_script_running() or self.easycon._recording
        tip.previous_button.setEnabled(not busy)
        tip.skip_button.setEnabled(not busy and kind in PRACTICE_KINDS)
        valid = not busy
        if kind in PRACTICE_KINDS:
            if phase == "select":
                path = self.selected()
                valid = valid and path is not None and path.is_file()
                tip.next_button.setText("打开并检查")
            elif phase in ("edit", "edit_second"):
                tip.next_button.setText("检查道具方向" if kind == "advance" and phase == "edit" else "设置试跑帧数" if kind == "advance" else "准备运行")
            elif phase == "advance_frames":
                valid = not busy and self.frames_ready()
                tip.next_button.setText("已确认 1000 帧")
            elif phase in CONTROL_PHASES:
                tip.next_button.setText("已确认大师球位置" if phase == "ball_check" else "已复原，检查脚本")
            elif phase == "run":
                valid = False  # The real Run button is the only way to start.
            elif phase == "review":
                valid = self.completed()
                tip.next_button.setText("脚本没问题")
                tip.previous_button.setEnabled(self.finished() and self._pending_skip is None)
                tip.skip_button.setEnabled(self._pending_skip is None)
                if self._pending_skip is not None:
                    valid = False
                    tip.skip_button.setText("正在停止…")
                status = "脚本运行中，请观察独立预览；结束后可确认结果。"
                if self.finished():
                    status = "运行已结束，请核对画面并选择结果。" if valid else "本次已停止、失败或源码已变化，请修改并重新运行。"
                tip.status.setText(status)
            elif phase == "retry":
                valid = not busy and (bool(self.easycon.editor.toPlainText().strip()) if kind in ("hit", "reverse") else self.easycon.run_button.isEnabled())
                tip.next_button.setText("修改完成" if kind in ("hit", "reverse") else "重新运行")
        elif kind == "ocr":
            tip.next_button.setText("打开 OCR 设置")
        elif kind == "save":
            valid = bool(self.panel.script_save_state_label.property("saved"))
            tip.next_button.setText("完成本步")
        tip.next_button.setEnabled(bool(valid))
        if self.replay_target:
            tip.skip_button.hide()

    def next(self):
        if self.ocr.needs_control:
            return
        kind, phase = self.pos
        if phase in CAPTURE_PHASES:
            self.capture.next()
        elif phase == "replay_open":
            self.open_replay()
        elif phase in CONTROL_PHASES:
            if phase == "ball_check":
                self.go(kind, "ball_restore")
            elif self.leave_control():
                self.go(kind, "edit" if phase == "ball_restore" else "retry")
        elif kind == "ocr":
            self.ocr.go("initial_test")
        elif kind == "save":
            self.c.pause()
        elif kind not in PRACTICE_KINDS:
            self._next_kind()
        elif phase == "select":
            self._open()
        elif phase == "edit" and kind == "advance":
            self.go(kind, "edit_second")
        elif kind == "advance" and phase == "edit_second":
            self.go(kind, "advance_frames")
        elif phase in ("edit", "edit_second", "advance_frames"):
            if self.easycon.has_unsaved_script_changes():
                if self.easycon.save_script() is None:
                    return
            self.go(kind, "run")
        elif phase == "retry":
            if kind == "advance" and not self.frames_ready():
                self.go(kind, "advance_frames")
                return
            if self.easycon.has_unsaved_script_changes() and self.easycon.save_script() is None:
                return
            if kind in ("hit", "reverse"):
                self.attempt = None
                self.go("seed", "replay_open", replay=self.replay_target or kind)
                self.open_replay()
            else:
                self.easycon.run_button.click()
        elif phase == "review" and self.completed():
            if self.easycon.has_unsaved_script_changes():
                if self.easycon.save_script() is None:
                    return
            if self.replay_target:
                self.next_replay()
            elif kind in ("seed", "advance"):
                self.capture.enter("capture_config" if kind == "seed" else "capture_start")
            else:
                self._next_kind()

    def _next_kind(self):
        kind, _ = self.pos
        self.attempt = None
        self._pending_skip = None
        self.preview.restore()
        self.go(SCRIPT_KINDS[SCRIPT_KINDS.index(kind) + 1], replay="")

    def previous(self):
        kind, phase = self.pos
        if phase in CAPTURE_PHASES:
            self.capture.previous()
        elif phase in CONTROL_PHASES:
            if self.leave_control():
                self.go(kind, "ball_check" if phase == "ball_restore" else "edit")
        elif kind in PRACTICE_KINDS and phase == "review":
            if self.finished():
                self.go(kind, "restore" if kind == "advance" else "retry")
        elif kind in PRACTICE_KINDS and phase != "select":
            self.attempt = None
            self.go(kind, "edit" if phase not in ("edit", "edit_second") else "select")
        elif kind == "seed":
            self.c._go("easycon_recording", "practice")
        else:
            self.go(SCRIPT_KINDS[SCRIPT_KINDS.index(kind) - 1])

    def skip(self):
        if self.controlling and not self.leave_control():
            return
        if self.easycon._controller_script_running():
            self._pending_skip = self.pos[0]
            self.easycon.stop_button.click()
            self.navigation()
            return
        self._next_kind()

    def _started(self):
        if not self.active or self.pos[0] not in PRACTICE_KINDS or self.pos[1] not in ("run", "retry"):
            return
        path = self.selected()
        if not same_path(path, self.easycon.current_script_path):
            return
        trace = getattr(self.easycon.native_backend, "execution_trace", None)
        if trace is None:
            return
        self.attempt = (trace, trace.snapshot().run_id, path, self.easycon.editor.toPlainText())
        # The follower has cleared the previous run by this signal. Enabling it
        # earlier would reopen the old snapshot after editing and disable Run.
        self.easycon.execution.follow.setChecked(True)
        self.go(self.pos[0], "review")

    def poll(self):
        if not self.active:
            return
        self.ocr.poll()
        self.capture.poll()
        kind, phase = self.pos
        overlay = self.c.overlay
        if (phase in ("run", "retry") and kind in PRACTICE_KINDS and overlay is not None
                and not (phase == "retry" and kind in ("hit", "reverse"))
                and not overlay.waiting_for_page and not overlay.suspended
                and script_run_target(self.panel) not in overlay.spec.highlights):
            self.c._show_workspace()
        if self._pending_skip == kind and not self.easycon._controller_script_running():
            self._next_kind()
            return
        if kind in PRACTICE_KINDS and phase in PREVIEW_PHASES and overlay.isVisible() and not overlay.waiting_for_page and not overlay.suspended:
            self.preview.show(kind)
        self.navigation()

    def pause(self):
        self.capture.pause()
        if self.pos[1] in CONTROL_PHASES | EDIT_PHASES or (self.c.step == "auto_script_config" and self.pos[0] == "ocr"):
            self.leave_control()
        self._pending_skip = None
        self.preview.restore()
        self.ocr.pause()
