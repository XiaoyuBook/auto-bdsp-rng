from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow

from auto_bdsp_rng.ui.update_dialog import UpdateController, UpdateDialog


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    yield application
    for widget in application.topLevelWidgets():
        for timer in widget.findChildren(QTimer):
            timer.stop()
        widget.close()
        widget.deleteLater()
    application.processEvents()


def _plan(**overrides):
    values = {
        "current_version": "2.1.7",
        "latest_version": "2.2.0",
        "release_url": "https://example.invalid/release",
        "release_notes": "- 新增增量升级",
        "update_available": True,
        "incremental_available": True,
        "download_size": 3 * 1024 * 1024,
        "assets": (SimpleNamespace(digest=f"sha256:{'a' * 64}"),),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _wait_until(predicate, *, timeout_ms: int = 2000) -> None:
    remaining = timeout_ms
    while not predicate() and remaining > 0:
        QTest.qWait(10)
        remaining -= 10
    assert predicate()


def test_update_dialog_shows_incremental_version_and_size(app):
    dialog = UpdateDialog()

    dialog.show_plan(_plan())

    assert dialog.current_version_label.text() == "2.1.7"
    assert dialog.latest_version_label.text() == "2.2.0"
    assert dialog.package_label.text() == "增量升级包：3.0 MiB"
    assert dialog.download_button.isVisibleTo(dialog)
    assert "新增增量升级" in dialog.release_notes.toPlainText()


def test_update_controller_source_mode_opens_release_instead_of_installing(app):
    opened: list[str] = []
    window = QMainWindow()
    controller = UpdateController(
        window,
        current_version="2.1.7",
        check_updates=lambda _version: _plan(),
        frozen=False,
        open_url=opened.append,
    )

    controller.check_for_updates()
    _wait_until(lambda: controller.dialog.latest_version_label.text() == "2.2.0")

    assert controller.dialog.download_button.isEnabled() is False
    assert "源码运行模式" in controller.dialog.status_label.text()
    controller.dialog.release_button.click()
    assert opened == ["https://example.invalid/release"]


def test_update_controller_downloads_launches_installer_and_closes_window(app, tmp_path):
    events: list[object] = []
    patch = tmp_path / "2.1.7-to-2.2.0.patch"
    patch.write_bytes(b"patch")

    class TrackingWindow(QMainWindow):
        def closeEvent(self, event) -> None:  # type: ignore[override]
            super().closeEvent(event)
            events.append("closed")

    window = TrackingWindow()
    window.show()

    class InstallerHandle:
        def approve(self) -> None:
            events.append(("approved", window.isVisible()))

    def download(plan, progress_callback, cancel_event: threading.Event):
        assert plan.latest_version == "2.2.0"
        assert cancel_event.is_set() is False
        progress_callback(1, 2)
        progress_callback(2, 2)
        return (patch,)

    controller = UpdateController(
        window,
        current_version="2.1.7",
        check_updates=lambda _version: _plan(),
        download_update=download,
        launch_installer=lambda patches, current, target, digests: (
            events.append((patches, current, target, digests)) or InstallerHandle()
        ),
        installer_available=lambda: True,
        frozen=True,
    )

    controller.check_for_updates()
    _wait_until(
        lambda: controller.dialog.download_button.isVisible()
        and controller.dialog.download_button.isEnabled()
    )
    controller.dialog.download_button.click()
    _wait_until(lambda: bool(events))
    _wait_until(lambda: not window.isVisible())

    assert events == [
        ((patch,), "2.1.7", "2.2.0", (f"sha256:{'a' * 64}",)),
        "closed",
        ("approved", False),
    ]


def test_update_controller_without_incremental_chain_only_opens_release(app):
    opened: list[str] = []
    window = QMainWindow()
    controller = UpdateController(
        window,
        current_version="2.0.0",
        check_updates=lambda _version: _plan(
            current_version="2.0.0",
            incremental_available=False,
            download_size=0,
        ),
        frozen=True,
        open_url=opened.append,
    )

    controller.check_for_updates()
    _wait_until(lambda: controller.dialog.latest_version_label.text() == "2.2.0")

    assert controller.dialog.download_button.isHidden()
    assert "没有适用于当前版本的增量升级链" in controller.dialog.status_label.text()
    controller.dialog.release_button.click()
    assert opened == ["https://example.invalid/release"]


def test_closing_update_dialog_cancels_download_without_launching(app, tmp_path):
    started = threading.Event()
    stopped = threading.Event()
    launched: list[bool] = []
    window = QMainWindow()

    def download(_plan, _progress_callback, cancel_event: threading.Event):
        started.set()
        if cancel_event.wait(timeout=2):
            stopped.set()
        return (tmp_path / "cancelled.patch",)

    controller = UpdateController(
        window,
        current_version="2.1.7",
        check_updates=lambda _version: _plan(),
        download_update=download,
        launch_installer=lambda *_args: launched.append(True),
        installer_available=lambda: True,
        frozen=True,
    )

    controller.check_for_updates()
    _wait_until(
        lambda: controller.dialog.download_button.isVisible()
        and controller.dialog.download_button.isEnabled()
    )
    controller.dialog.download_button.click()
    _wait_until(started.is_set)
    controller.dialog.close()
    _wait_until(stopped.is_set)
    _wait_until(lambda: controller._download_thread is None)

    assert launched == []


def test_cancel_before_queued_download_completion_never_launches_installer(app, tmp_path):
    launched: list[object] = []
    window = QMainWindow()
    controller = UpdateController(
        window,
        current_version="2.1.7",
        launch_installer=lambda *args: launched.append(args),
        installer_available=lambda: True,
        frozen=True,
    )
    controller._plan = _plan()

    class PendingDownload:
        def stop(self) -> None:
            pass

    pending = PendingDownload()
    controller._download_thread = pending  # type: ignore[assignment]

    controller.cancel()
    controller._download_completed((tmp_path / "late.patch",))
    controller._download_thread_finished(pending)  # type: ignore[arg-type]

    assert controller._downloaded_patches is None
    assert launched == []


def test_refused_window_close_stops_installer_with_kill_fallback(app, tmp_path):
    events: list[object] = []

    class RefusingWindow(QMainWindow):
        def closeEvent(self, event) -> None:  # type: ignore[override]
            event.ignore()

    class StubbornInstaller:
        def __init__(self) -> None:
            self.wait_calls = 0

        def poll(self):
            return None

        def cancel(self) -> None:
            events.append("cancel")

        def approve(self) -> None:
            events.append("approve")

        def terminate(self) -> None:
            events.append("terminate")

        def wait(self, *, timeout: float):
            events.append(("wait", timeout))
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("updater", timeout)
            return 1

        def kill(self) -> None:
            events.append("kill")

    window = RefusingWindow()
    window.show()
    installer = StubbornInstaller()
    controller = UpdateController(
        window,
        current_version="2.1.7",
        launch_installer=lambda *_args: installer,
        installer_available=lambda: True,
        frozen=True,
    )
    controller._plan = _plan()

    controller._install_downloaded_update((tmp_path / "update.zip",))

    assert events == ["cancel", "terminate", ("wait", 2.0), "kill", ("wait", 2.0)]
    assert "approve" not in events
    assert "升级程序已停止" in controller.dialog.status_label.text()


def test_refused_window_close_does_not_preemptively_cancel_active_update(app):
    stopped: list[bool] = []

    class RefusingWindow(QMainWindow):
        def closeEvent(self, event) -> None:  # type: ignore[override]
            event.ignore()

    class ActiveCheck:
        def stop(self) -> None:
            stopped.append(True)

    window = RefusingWindow()
    controller = UpdateController(window, current_version="2.1.7")
    controller._check_thread = ActiveCheck()  # type: ignore[assignment]

    assert window.close() is False
    assert stopped == []
