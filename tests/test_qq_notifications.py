from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.notifications.qq_service import (
    QQNotificationService, QQSettings, QQSettingsStore, notification_image,
)
from auto_bdsp_rng.ui.qq_notifications import QQNotificationDialog


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


class FakeClient(QObject):
    bound = Signal(str, str)
    finished = Signal(bool, str)
    delivery = Signal(str, bool, str)
    log = Signal(str)
    status = Signal(str)
    busy_changed = Signal(bool)
    binding_changed = Signal(str, str, int, int)

    def __init__(self):
        super().__init__()
        self.busy = False
        self.calls = []

    def configure(self, app_id, secret):
        self.credentials = (app_id, secret)

    def check_credentials(self):
        self.finish(True)

    def bind(self, kind):
        self.busy = True
        self.busy_changed.emit(True)
        self.binding_changed.emit(kind, "482731", 60, 1)

    def send(self, targets, text, image):
        self.calls.append((targets, text, image, self.credentials))
        self.busy = True
        self.busy_changed.emit(True)

    def finish(self, ok, message="操作完成"):
        self.busy = False
        self.busy_changed.emit(False)
        self.finished.emit(ok, message)

    def cancel(self):
        if self.busy:
            self.binding_changed.emit("", "", 0, 0)
            self.finish(False, "操作已取消")


@pytest.fixture
def service(app, tmp_path):
    result = QQNotificationService(store=QQSettingsStore(tmp_path / "qq.json"), setup_client=FakeClient(), sender=FakeClient())
    result.update(app_id="APP", secret="SECRET")
    result.update(user_openid="USER")
    yield result
    result.shutdown()


def test_credentials_memory_only_and_windows_protected_storage(tmp_path):
    store = QQSettingsStore(tmp_path / "qq.json")
    settings = QQSettings(app_id="APP", secret="TOP_SECRET", user_openid="USER", enabled=True)
    store.save(settings)
    assert "TOP_SECRET" not in store.path.read_text(encoding="utf-8")
    loaded = store.load()
    assert not loaded.secret and not loaded.enabled
    pytest.importorskip("win32crypt")
    settings.remember_secret = True
    store.save(settings)
    assert "TOP_SECRET" not in store.path.read_text(encoding="utf-8")
    loaded = store.load()
    assert loaded.secret == "TOP_SECRET" and loaded.enabled
    settings.remember_secret = False
    store.save(settings)
    assert "protected_secret" not in json.loads(store.path.read_text(encoding="utf-8"))


def test_damaged_saved_secret_disables_notifications(tmp_path):
    store = QQSettingsStore(tmp_path / "qq.json")
    store.path.write_text(json.dumps({"remember_secret": True, "enabled": True, "protected_secret": "invalid"}), encoding="utf-8")
    result = store.load()
    assert not result.enabled and not result.secret
    assert "重新填写" in store.warning


def test_automatic_delivery_while_binding_and_deduplication(service, app):
    service.update(enabled=True)
    service.bind("group")
    assert service.notify_task("R1", "自动定点乱数", "已完成", target="草苗龟")
    assert service.setup.busy and service.sender.busy
    assert not service.notify_task("R1", "自动定点乱数", "已完成")
    assert not service.notify_task("R2", "自动定点乱数", "已停止")
    assert service.notify_task("R3", "自动 TID 乱数", "失败", detail="OCR 超时")
    assert len(service.sender.calls) == 1
    assert not QImage.fromData(service.sender.calls[0][2]).isNull()
    service.sender.delivery.emit("user", True, "文字、图片已提交")
    service.sender.finish(True)
    app.processEvents()
    assert len(service.sender.calls) == 2
    assert "OCR 超时" in service.sender.calls[1][1]
    assert len(service.records) == 1


def test_disable_cancels_pending_notifications(service, app):
    service.update(enabled=True)
    service.notify_task("R1", "任务", "已完成")
    service.notify_task("R2", "任务", "已完成")
    service.update(enabled=False)
    app.processEvents()
    assert len(service.sender.calls) == 1
    assert not service.sender.busy


def test_app_id_change_clears_old_recipients(service):
    service.update(enabled=True)
    service.update(app_id="NEW_APP")
    assert not service.settings.user_openid
    assert not service.settings.enabled


def test_cached_bgr_frame_becomes_correct_jpeg(app):
    import numpy as np
    frame = np.zeros((16, 24, 3), dtype=np.uint8)
    frame[:, :, 2] = 255
    image = QImage.fromData(notification_image(frame))
    assert image.width() == 24 and image.height() == 16
    assert image.pixelColor(0, 0).red() > 240
    assert image.pixelColor(0, 0).blue() < 10


def test_native_guide_uses_same_fields_and_binding_controls(service, app):
    dialog = QQNotificationDialog(service)
    dialog.resize(920, 750)
    dialog.show()
    try:
        dialog.open_guide()
        dialog._set_phase(1)
        app.processEvents()
        assert dialog.guide_form_layouts[0].indexOf(dialog.credentials) >= 0
        assert dialog.app_id.isVisible() and dialog.secret.isVisible()
        dialog.verify_button.click()
        assert service.verified
        dialog.bind_buttons["group"].click()
        assert dialog.code.text() == "482 731"
        assert dialog.countdown.text() == "剩余 60 秒"
        service.setup.binding_changed.emit("group", "738291", 60, 2)
        assert "第 2 组" in dialog.refresh_notice.text()
        assert dialog.code.text() == "738 291"
        service.setup.bound.emit("group", "NEW_GROUP")
        service.setup.binding_changed.emit("", "", 0, 0)
        service.setup.finish(True)
        dialog.return_to_settings()
        assert dialog.setup_form_layout.indexOf(dialog.credentials) >= 0
        assert dialog.app_id.text() == "APP"
        assert service.settings.group_openid == "NEW_GROUP"
        dialog.open_guide()
        dialog._set_phase(2)
        assert dialog.guide_test_layout.indexOf(dialog.test_panel) >= 0
        dialog.send_button.click()
        assert not QImage.fromData(service.setup.calls[-1][2]).isNull()
        service.setup.finish(False, "文字已提交；图片失败")
        assert "未通过" in dialog.test_result.text()
        assert not dialog.confirm_button.isVisible()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_closing_native_dialog_cancels_binding(service, app):
    dialog = QQNotificationDialog(service)
    dialog.show()
    dialog.verify_button.click()
    dialog.bind_buttons["user"].click()
    assert service.setup.busy
    dialog.close()
    assert not service.setup.busy
    assert "SECRET" not in service.store.path.read_text(encoding="utf-8")
    dialog.deleteLater()


@pytest.mark.parametrize("task", ["rng", "tid"])
def test_main_window_notifies_only_once_per_finished_run(task):
    from auto_bdsp_rng.ui.main_window import MainWindow
    notifications = []
    window = SimpleNamespace(
        _active_auto_rng_run_id="R", _active_auto_rng_round_id=1,
        _active_auto_tid_run_id="T", _active_auto_tid_round_id=2,
        auto_rng_tab=SimpleNamespace(status_badge=SimpleNamespace(text=lambda: "状态：已完成")),
        auto_tid_rng_tab=SimpleNamespace(status_badge=SimpleNamespace(text=lambda: "状态：失败"), _last_progress=SimpleNamespace(target_tid=12345)),
        history_tab=SimpleNamespace(finish_run=lambda *a, **kw: None, current_target_label="草苗龟"),
        run_records_tab=SimpleNamespace(set_run_finished=lambda *a: None),
        _update_auto_rng_header=lambda **kw: None,
        _run_outcome_from_status=MainWindow._run_outcome_from_status,
        _notify_qq_task=lambda *args: notifications.append(args),
    )
    handler = MainWindow._handle_auto_rng_run_state_changed if task == "rng" else MainWindow._handle_auto_tid_run_state_changed
    handler(window, False)
    handler(window, False)
    assert len(notifications) == 1
    assert notifications[0][2] == ("已完成" if task == "rng" else "失败")


def test_close_in_progress_suppresses_task_notification():
    from auto_bdsp_rng.ui.main_window import MainWindow
    MainWindow._notify_qq_task(SimpleNamespace(_is_closing=True), "R", "自动定点", "已停止", "目标")
