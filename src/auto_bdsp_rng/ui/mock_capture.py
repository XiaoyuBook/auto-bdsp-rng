"""Developer-only manual capture results, chosen explicitly by the user."""
from dataclasses import dataclass
from threading import Event, Thread

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton

from auto_bdsp_rng.blink_detection import SeedState32
from auto_bdsp_rng.ui.workspace_controls import ConnectionDialog, PrimaryButton


class MockCaptureFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class MockCaptureResult:
    state: SeedState32
    advances: int = 1000


def is_mock_video(window):
    return bool(window._dev_mock_devices and window._video_source_connected
                and window._mock_video_frame is not None)


def start_mock_capture(window, mode):
    if not is_mock_video(window):
        return False
    w = window
    correcting = mode == "reidentify"
    total = w._reidentify_blink_count() if correcting else 40
    action = "校正" if correcting else "捕捉 Seed"
    try:
        state = SeedState32.from_hex_words([box.text() for box in w.seed32_inputs])
    except Exception:
        state = SeedState32(0x12345678, 0x9ABCDEF0, 0x13579BDF, 0x2468ACE0)
    w._stop_advance_tracking()
    w._set_preview_selection_enabled(False)
    w._capture_cancel.clear()
    w._capture_result = w._capture_error = w._capture_frame = None
    w._capture_mode = mode
    w._capture_progress = (total, total)
    w.progress_value.setText(f"{total}/{total}")
    w.capture_button.setText(w._text("stop_capture"))
    w.reidentify_button.setEnabled(False)
    w.tidsid_button.setEnabled(False)

    dialog = ConnectionDialog(f"Mock · {action}", "MockCaptureDialog", w)
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    dialog.close_button.clicked.disconnect()
    dialog.close_button.clicked.connect(dialog.reject)
    label = QLabel(f"眨眼进度 {total}/{total} · 已完成")
    dialog.body_layout.addWidget(label)
    progress = QProgressBar()
    progress.setRange(0, total)
    progress.setValue(total)
    progress.setTextVisible(False)
    progress.setFixedHeight(8)
    progress.setStyleSheet("QProgressBar { border: 0; background: #EAF7F1; border-radius: 4px; } "
                          "QProgressBar::chunk { background: #087C58; border-radius: 4px; }")
    dialog.body_layout.addWidget(progress)
    copy = QLabel("选择本次模拟结果，测试成功流程或失败后的调整流程。\n这是开发测试数据，不代表实际游戏结果。")
    copy.setWordWrap(True)
    dialog.body_layout.addWidget(copy)
    dialog.success_button = PrimaryButton("模拟成功")
    dialog.failure_button = QPushButton("模拟失败")
    dialog.footer_layout.addWidget(dialog.failure_button)
    dialog.footer_layout.addWidget(dialog.success_button)
    for button in (dialog.success_button, dialog.failure_button):
        button.setAutoDefault(False)
    dialog.success_button.clicked.connect(dialog.accept)
    dialog.failure_button.clicked.connect(lambda: dialog.done(2))
    decided = Event()
    answer = []

    def chosen(result):
        answer.append(result)
        if result == 0:
            w._capture_cancel.set()
        decided.set()

    dialog.finished.connect(chosen)
    w._mock_capture_dialog = dialog

    def run():
        while not decided.wait(0.05):
            if w._capture_cancel.is_set():
                return
        if w._capture_cancel.is_set():
            return
        if answer[0] == 1:
            w._capture_result = MockCaptureResult(state)
        else:
            w._capture_error = MockCaptureFailure(f"Mock：已选择模拟{action}失败，请调整后重试。")

    w._capture_thread = Thread(target=run, daemon=True)
    w._capture_thread.start()
    w.manualCaptureStarted.emit(mode, w._capture_thread)
    w._capture_timer.start()
    w.statusBar().showMessage(f"Mock {action}：眨眼进度已完成，等待选择模拟结果。")
    w._write_run_log("Seed 捕捉", f"Mock {action}；模拟进度 {total}/{total}，等待手动选择结果")
    dialog.adjustSize()
    dialog.show()
    dialog.raise_()
    return True
