"""One script at a time: choose, inspect, run, confirm the game, then return."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from auto_bdsp_rng.ui.script_guide_steps import PRACTICE_KINDS, SCRIPT_KINDS, position, script_run_target
from auto_bdsp_rng.ui.ocr_guide import OcrGuide
from auto_bdsp_rng.ui.script_guide_preview import ScriptGuidePreview


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
        self.panel.scriptEditRequested.connect(self._opened)
        self.easycon.nativeScriptStarted.connect(self._started)
        self.easycon.save_button.clicked.connect(lambda: QTimer.singleShot(0, self.poll))

    @property
    def active(self):
        return self.c.active and self.c.step == "auto_script_config"

    @property
    def pos(self):
        return position(self.c.detail)

    def selected(self):
        kind, _ = self.pos
        combo = getattr(self.panel, f"{kind}_script_combo", None)
        return self.panel._selected_path(combo) if combo is not None else None

    def page(self):
        kind, phase = self.pos
        return self.easycon if kind in PRACTICE_KINDS and phase != "select" else self.panel

    def go(self, kind, phase="select"):
        self._positioned = None
        self.c._go("auto_script_config", f"{kind}:{phase}")

    def prepare(self):
        if not self.active or self.pos[0] not in PRACTICE_KINDS or self.pos[1] not in ("run", "review", "retry"):
            self.preview.restore()
        if not self.active:
            return False
        if self.page() is self.panel and not self.panel._runtime_script_editor_expanded:
            self.panel._toggle_runtime_script_editor()
            QTimer.singleShot(80, self._settled)
        if self.ocr.prepare():
            return True
        kind, phase = self.pos
        if kind not in PRACTICE_KINDS or phase == "select" or self._opening:
            return False
        # Do not trust a saved line/run marker when the editor or process changed.
        if not same_path(self.selected(), self.easycon.current_script_path):
            self.go(kind)
            return True
        if phase == "review" and self.attempt is None:
            self.go(kind, "edit")
            return True
        if phase == "retry":
            self.easycon.execution.follow.setChecked(False)
            self.easycon.execution.show_current()
        return False

    def _opened(self, _path=None):
        if not self.active or self.pos[1] != "select" or self.pos[0] not in PRACTICE_KINDS:
            return
        if same_path(self.selected(), self.easycon.current_script_path) and self.window.tabs.currentWidget() is self.easycon:
            self.easycon.execution.show_current()
            self.attempt = None
            self.go(self.pos[0], "edit")

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

    def present(self):
        if not self.active or self.c.overlay.waiting_for_page:
            return
        kind, phase = self.pos
        if kind in PRACTICE_KINDS and phase in ("run", "review", "retry"):
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
        elif phase == "retry":
            self.easycon.execution.follow.setChecked(False)
            self.easycon.execution.show_current()
        self.navigation()

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
        if self.ocr.selecting or (self.pos[0] == "ocr" and self.pos[1] != "select"):
            return
        tip = self.c.overlay.tip
        if self.c.overlay.waiting_for_page:
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
                tip.next_button.setText("检查道具方向" if kind == "advance" and phase == "edit" else "准备运行")
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
                valid = not busy and self.easycon.run_button.isEnabled()
                tip.next_button.setText("重新运行")
        elif kind == "ocr":
            tip.next_button.setText("观看 OCR 演示")
        elif kind == "save":
            valid = bool(self.panel.script_save_state_label.property("saved"))
            tip.next_button.setText("完成本步")
        tip.next_button.setEnabled(bool(valid))

    def next(self):
        kind, phase = self.pos
        if kind == "ocr":
            self.go("ocr", "demo")
        elif kind == "save":
            self.c.pause()
        elif kind not in PRACTICE_KINDS:
            self._next_kind()
        elif phase == "select":
            self._open()
        elif phase == "edit" and kind == "advance":
            self.go(kind, "edit_second")
        elif phase in ("edit", "edit_second"):
            if self.easycon.has_unsaved_script_changes():
                if self.easycon.save_script() is None:
                    return
            self.go(kind, "run")
        elif phase == "retry":
            if self.easycon.has_unsaved_script_changes() and self.easycon.save_script() is None:
                return
            self.easycon.run_button.click()
        elif phase == "review" and self.completed():
            if self.easycon.has_unsaved_script_changes():
                if self.easycon.save_script() is None:
                    return
            self._next_kind()

    def _next_kind(self):
        kind, _ = self.pos
        self.attempt = None
        self._pending_skip = None
        self.preview.restore()
        self.go(SCRIPT_KINDS[SCRIPT_KINDS.index(kind) + 1])
        self.window.tabs.setCurrentWidget(self.panel)

    def previous(self):
        kind, phase = self.pos
        if kind in PRACTICE_KINDS and phase == "review":
            if self.finished():
                self.go(kind, "retry")
        elif kind in PRACTICE_KINDS and phase != "select":
            self.attempt = None
            self.go(kind, "edit" if phase not in ("edit", "edit_second") else "select")
        elif kind == "seed":
            self.c._go("easycon_recording", "practice")
        else:
            self.go(SCRIPT_KINDS[SCRIPT_KINDS.index(kind) - 1])

    def skip(self):
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
        kind, phase = self.pos
        overlay = self.c.overlay
        if (phase in ("run", "retry") and kind in PRACTICE_KINDS and overlay is not None
                and not overlay.waiting_for_page and not overlay.suspended
                and script_run_target(self.panel) not in overlay.spec.highlights):
            self.c._show_workspace()
        if self._pending_skip == kind and not self.easycon._controller_script_running():
            self._next_kind()
            return
        if kind in PRACTICE_KINDS and phase in ("run", "review", "retry") and overlay.isVisible() and not overlay.waiting_for_page and not overlay.suspended:
            self.preview.show(kind)
        self.navigation()

    def pause(self):
        self._pending_skip = None
        self.preview.restore()
        self.ocr.pause()
