"""Animation handoff and real ROI/eye actions within guide step 4.3."""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QDialog

from auto_bdsp_rng.ui.eye_demo_dialog import EyeDemoDialog


EYE_DETAILS = ("demo", "roi_button", "roi_drag", "eye_button", "eye_drag", "roi_adjust")


class EyeGuide(QObject):
    def __init__(self, controller) -> None:
        super().__init__(controller)
        self.controller = controller
        self.window = controller.window
        self.dialog: EyeDemoDialog | None = None
        self.config_saved = False
        self._save_error = ""
        self.window.captureSelectionStarted.connect(self._started, Qt.ConnectionType.QueuedConnection)
        self.window.captureSelectionFinished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self.window.captureConfigSaved.connect(self._saved)
        self.window.captureConfigSaveFailed.connect(self._save_failed)
        for name in ("x", "y", "w", "h", "npc_count", "pokemon_npc", "timeline_npc", "advance_delay", "advance_delay_2", "display_percent", "window_prefix", "camera"):
            getattr(self.window, name).textChanged.connect(self._edited)
        for name in ("threshold", "white_delay"):
            getattr(self.window, name).valueChanged.connect(self._edited)
        self.window.config_combo.currentTextChanged.connect(self._edited)
        self.window.monitor_window.toggled.connect(self._edited)
        self.window.reidentify_1_pk_npc.toggled.connect(self._edited)

    @property
    def practicing(self) -> bool:
        c = self.controller
        return c.active and c.step == "seed_capture_tools" and c.detail in EYE_DETAILS[1:]

    @property
    def selecting_eye(self) -> bool:
        return self.practicing and self.controller.detail == "eye_drag"

    def prepare(self) -> bool:
        """Return True when the animation owns the current presentation."""
        c = self.controller
        if self.dialog is not None:
            return True
        if c.step != "seed_capture_tools":
            return False
        if c.detail == "demo":
            c.overlay.shade_all()
            self.dialog = EyeDemoDialog(self.window)
            self.dialog.learnedRequested.connect(self._learned)
            self.dialog.finished.connect(self._demo_closed)
            self.dialog.open()
            return True
        # A drag is transient. After a restart/cancel, enter through its button.
        if c.detail in ("roi_drag", "roi_adjust", "eye_drag"):
            expected = "eye" if c.detail == "eye_drag" else "roi"
            if self.window._selection_mode != expected:
                c._persist(c.step, "eye_button" if expected == "eye" else "roi_button")
        return False

    def _learned(self) -> None:
        c = self.controller
        if c._persist("seed_capture_tools", "roi_button"):
            self.dialog.finish_learning()
        else:
            self.dialog.save_failed()

    def _demo_closed(self, result: int) -> None:
        dialog, self.dialog = self.dialog, None
        dialog.deleteLater()
        if not self.controller.active:
            return
        if result == QDialog.DialogCode.Accepted:
            QTimer.singleShot(0, self.controller._show_workspace)
        else:
            self.controller.pause()

    def cancel_selection(self) -> None:
        if self.practicing and self.window._selection_mode in ("roi", "eye"):
            # Discard the unfinished drag only. Successfully applied parameters stay.
            self.window._cancel_preview_selection()

    def pause(self) -> None:
        self.config_saved = False
        self._save_error = ""
        if self.dialog is not None:
            self.dialog.reject()

    def next(self) -> None:
        self.controller._go("seed_capture_tools", "demo")

    def previous(self) -> None:
        c = self.controller
        detail = c.detail
        self.cancel_selection()
        back = {"roi_button": "", "roi_drag": "roi_button", "eye_button": "roi_button", "eye_drag": "eye_button", "roi_adjust": "eye_button"}
        c._go("seed_capture_tools", back.get(detail, ""))

    def _started(self, mode: str) -> None:
        if not self.practicing:
            return
        c = self.controller
        if (c.detail, mode) in (("roi_button", "roi"), ("eye_button", "eye")):
            c._go(c.step, "roi_drag" if mode == "roi" else "eye_drag")

    def _finished(self, mode: str, ok: bool) -> None:
        if not self.practicing:
            return
        c = self.controller
        expected = "eye" if c.detail in ("eye_button", "eye_drag") else "roi"
        if mode != expected:
            return
        if not ok:
            c._go(c.step, "eye_button" if mode == "eye" else "roi_button")
        elif mode == "eye":
            if self.window._selection_mode == "roi":
                c._go(c.step, "roi_adjust")
            else:
                c._go("seed_capture_save")
        elif c.detail == "roi_adjust":
            c._go("seed_capture_save")
        else:
            c._go(c.step, "eye_button")

    def _saved(self) -> None:
        if self.controller.active and self.controller.step == "seed_capture_save":
            self.config_saved = True
            self._save_error = ""
            self.controller._show_workspace()

    def _save_failed(self, message: str) -> None:
        if self.controller.active and self.controller.step == "seed_capture_save":
            self.config_saved = False
            self._save_error = message
            self.controller._show_workspace()

    def _edited(self, *_args) -> None:
        self.config_saved = False
        self._save_error = ""
        if self.controller.active and self.controller.step == "seed_capture_save":
            self.controller._show_workspace()

    def show_save_status(self) -> None:
        overlay = self.controller.overlay
        if overlay.waiting_for_page:
            return
        if self.config_saved or self._save_error:
            overlay.title.setText("配置已保存" if self.config_saved else "配置尚未保存")
            overlay.copy.setText("点击下一步，选择自动流程使用的配置。" if self.config_saved else self._save_error)
            overlay.relayout.start(0)
