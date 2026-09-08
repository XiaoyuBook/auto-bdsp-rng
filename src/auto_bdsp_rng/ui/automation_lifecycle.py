"""Button state for the existing cooperative automation workers.

The thread pointer owns the resource until QThread.finished, even after the
worker has returned its result. Shared availability is supplied by MainWindow.
"""

from PySide6.QtCore import QTimer, Slot


class AutomationLifecycle:
    def _init_lifecycle(self) -> None:
        self._start_available = True
        self._preparing = False
        self._stop_pending = False
        self._worker_done = False
        self._run_state_active = False

    def set_start_available(self, available: bool) -> None:
        self._start_available = bool(available)
        self._sync_run_controls()

    def set_preparing(self, preparing: bool) -> None:
        self._preparing = preparing
        if not preparing and self._runner_thread is None:
            self._stop_pending = False
        self._sync_run_controls()

    def _sync_run_controls(self) -> None:
        busy = self._preparing or self._runner_thread is not None
        start = self._start_available and not busy
        self.start_button.setEnabled(start)
        menu = self.start_button.menu()
        if menu is not None:
            for action in menu.actions():
                action.setEnabled(start)
        self.stop_button.setText("正在停止" if self._stop_pending else "停止")
        self.stop_button.setEnabled(busy and not self._stop_pending and not self._worker_done)
        if hasattr(self, "refresh_scripts_button"):
            self.refresh_scripts_button.setEnabled(self._start_available and not busy)

    def request_stop(self, reason: str | None = None) -> bool:
        if self._stop_pending or self._worker_done:
            return False
        worker = self._runner_worker
        if worker is None and not self._preparing:
            return False
        self._stop_pending = True
        self._sync_run_controls()
        try:
            if worker is not None:
                request = getattr(worker, "request_stop", None)
                if callable(request):
                    request(reason)
                else:
                    worker.stop()
            self.stopRequested.emit()
        except Exception as exc:
            self._stop_pending = False
            self._sync_run_controls()
            self.add_log(f"停止请求失败，请重试：{exc}", level="ERROR")
            return False
        return True

    def _runner_returned(self) -> None:
        self._worker_done = True
        self._sync_run_controls()

    @Slot()
    def _clear_runner_thread(self) -> None:
        thread = self._runner_thread
        if thread is not None and thread.isRunning():
            return
        sender = self.sender()
        if sender is not None and sender is not thread:
            return
        # finished is emitted before thread-local cleanup/deferred destruction
        # has necessarily ended. Keep the Python worker alive through that work.
        if thread is not None and not thread.wait(100):
            QTimer.singleShot(10, self._clear_runner_thread)
            return
        self._runner_thread = None
        self._runner_worker = None
        if thread is not None:
            thread.deleteLater()
        self._preparing = False
        self._stop_pending = False
        self._worker_done = False
        if self._run_state_active:
            self._run_state_active = False
            # Let the owner recompute shared availability before restoring Start.
            self.runStateChanged.emit(False)
        self._sync_run_controls()
