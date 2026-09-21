from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QObject, Signal
from PySide6.QtTest import QTest
from PySide6.QtGui import QCloseEvent, QDesktopServices, QImage
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.notifications.qq_service import (
    QQNotificationService, QQSettings, QQSettingsStore, notification_image,
)
from auto_bdsp_rng.ui.qq_notifications import QQNotificationDialog


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
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


def test_task_notification_sends_video_frame_and_freezes_queued_snapshot(service, app):
    import numpy as np
    from auto_bdsp_rng.ui.main_window import MainWindow

    service.update(enabled=True)
    frame = np.zeros((16, 24, 3), dtype=np.uint8)
    frame[:, :, 2] = 255
    window = SimpleNamespace(
        _is_closing=False, qq_notifications=service,
        _qq_task_details={"自动定点": "目标已命中"}, _latest_preview_frame=frame,
    )
    MainWindow._notify_qq_task(window, "R1", "自动定点", "已完成", "草苗龟")
    first_image = QImage.fromData(service.sender.calls[0][2])
    assert (first_image.width(), first_image.height()) == (24, 16)
    assert first_image.pixelColor(0, 0).red() > 240

    frame[:, :, :] = (255, 0, 0)
    MainWindow._notify_qq_task(window, "R2", "自动定点", "已完成", "草苗龟")
    # A later video frame must not replace the screenshot taken at task completion.
    frame[:, :, :] = (0, 255, 0)
    service.sender.finish(True)
    app.processEvents()
    queued_image = QImage.fromData(service.sender.calls[1][2])
    assert (queued_image.width(), queued_image.height()) == (24, 16)
    assert queued_image.pixelColor(0, 0).blue() > 240
    assert queued_image.pixelColor(0, 0).green() < 10


def web_js(dialog, script):
    values = []
    dialog.page.runJavaScript(script, values.append)
    deadline = time.monotonic() + 5
    while not values:
        assert time.monotonic() < deadline, "JavaScript callback timed out"
        QTest.qWait(10)
    return values[0]


def web_wait(dialog, script):
    deadline = time.monotonic() + 15
    while not web_js(dialog, script):
        assert time.monotonic() < deadline, script
        QTest.qWait(10)


@pytest.fixture
def dialog(service):
    result = QQNotificationDialog(service)
    result.resize(880, 820)
    result.show()
    web_wait(result, "document.getElementById('bdsp-qq-design')?.dataset.ready === 'true'")
    yield result
    result.close()
    result.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def click(dialog, name):
    web_js(dialog, f"document.getElementById('bd-{name}').click()")


def test_web_guide_shares_forms_and_real_binding_state(dialog, service):
    click(dialog, "guide-open")
    click(dialog, "phase-bind")
    web_wait(dialog, "document.getElementById('bd-app-id').closest('#bd-guide-credentials') !== null")
    click(dialog, "verify")
    web_wait(dialog, "!document.getElementById('bd-bind-group').disabled")
    assert service.verified
    click(dialog, "bind-group")
    web_wait(dialog, "document.getElementById('bd-countdown').textContent === '剩余 60 秒'")
    service.setup.binding_changed.emit("group", "738291", 42, 2)
    web_wait(dialog, "document.getElementById('bd-binding-code').textContent === '738 291'")
    assert "第 2 组" in web_js(dialog, "document.getElementById('bd-refresh-notice').textContent")
    assert web_js(dialog, "document.getElementById('bd-countdown').textContent") == "剩余 42 秒"
    service.setup.bound.emit("group", "NEW_GROUP")
    service.setup.binding_changed.emit("", "", 0, 0)
    service.setup.finish(True)
    click(dialog, "guide-back")
    web_wait(dialog, "document.getElementById('bd-app-id').closest('#bd-setup-main') !== null")
    assert web_js(dialog, "document.getElementById('bd-app-id').value") == "APP"
    assert service.settings.group_openid == "NEW_GROUP"
    click(dialog, "guide-open")
    click(dialog, "phase-test")
    web_wait(dialog, "document.getElementById('bd-test-send').closest('#bd-guide-test-slot') !== null")
    click(dialog, "test-send")
    assert not QImage.fromData(service.setup.calls[-1][2]).isNull()
    service.setup.finish(False, "文字已提交；图片失败")
    web_wait(dialog, "document.getElementById('bd-test-result').textContent.includes('未通过')")
    assert web_js(dialog, "document.getElementById('bd-confirm-received').hidden")


def test_web_credentials_flush_before_verify_and_close(dialog, service):
    web_js(dialog, """const input = document.getElementById('bd-app-id');
        input.value = 'NEW_APP'; input.dispatchEvent(new Event('input', {bubbles:true}));""")
    click(dialog, "verify")
    web_wait(dialog, "document.getElementById('bd-credential-state').textContent.includes('已验证')")
    assert service.setup.credentials == ("NEW_APP", "SECRET")
    assert not service.settings.user_openid
    click(dialog, "bind-user")
    web_wait(dialog, "!document.getElementById('bd-binding').hidden")
    dialog.close()
    assert not service.setup.busy
    assert "SECRET" not in service.store.path.read_text(encoding="utf-8")
    assert "secret" not in dialog.snapshot()["settings"]
    assert dialog.profile.isOffTheRecord()


@pytest.mark.parametrize("kind", ["user", "group"])
def test_binding_save_failure_keeps_saved_recipient_and_reports_failure(dialog, service, monkeypatch, kind):
    service.update(group_openid="OLD_GROUP", group_enabled=True, enabled=True)
    old_openid = getattr(service.settings, kind + "_openid")
    results = []
    service.operation_finished.connect(lambda *args: results.append(args))
    dialog.command("navigate", {"guide": True, "phase": "bind"})
    service.verify()
    service.bind(kind)

    def fail_save(_):
        raise OSError("disk full")

    with monkeypatch.context() as scope:
        scope.setattr(service.store, "save", fail_save)
        service.setup.bound.emit(kind, "NEW_RECIPIENT")
    # The client reports network success after the service tried to persist binding.
    service.setup.status.emit("绑定成功。")
    service.setup.finish(True, "绑定成功。")
    web_wait(dialog, "document.getElementById('bd-guide-result').textContent.includes('绑定结果未能保存')")
    assert web_js(dialog, "document.getElementById('bd-guide-result').classList.contains('bd-warn')")
    assert results[-1][:2] == ("bind", False)
    assert getattr(service.settings, kind + "_openid") == old_openid
    assert getattr(service.store.load(), kind + "_openid") == old_openid

    service.bind(kind)
    service.setup.bound.emit(kind, "NEW_RECIPIENT")
    service.setup.finish(True, "绑定成功。")
    assert results[-1][:2] == ("bind", True)
    assert not service.last_error
    assert not dialog.snapshot()["feedback_error"]
    assert getattr(service.settings, kind + "_openid") == "NEW_RECIPIENT"
    assert getattr(service.store.load(), kind + "_openid") == "NEW_RECIPIENT"


@pytest.mark.parametrize("close_method", ["close", "reject", "accept"])
def test_save_failure_blocks_dialog_close_and_allows_retry(dialog, service, monkeypatch, close_method):
    dialog.command("credentials", {"app_id": "NEW_APP", "secret": "SECRET", "remember_secret": False})

    def fail_save(_):
        raise OSError("disk full")

    with monkeypatch.context() as scope:
        scope.setattr(service.store, "save", fail_save)
        getattr(dialog, close_method)()
        assert dialog.isVisible()
        assert dialog._dirty
        assert service.store.load().app_id == "APP"
        web_wait(dialog, "document.getElementById('bd-footer-status').textContent.includes('配置未能保存')")
    getattr(dialog, close_method)()
    assert not dialog.isVisible()
    assert not dialog._dirty
    assert not dialog.snapshot()["feedback_error"]
    assert service.store.load().app_id == "NEW_APP"


def test_main_window_close_respects_unsaved_qq_credentials(dialog, service, monkeypatch):
    from auto_bdsp_rng.ui.main_window import MainWindow

    dialog.command("credentials", {"app_id": "NEW_APP", "secret": "SECRET", "remember_secret": False})

    def fail_save(_):
        raise OSError("disk full")

    window = SimpleNamespace(
        easycon_tab=SimpleNamespace(prepare_for_close_confirmation=lambda: None),
        _confirm_unsaved_easycon_script=lambda: True,
        _qq_dialog=dialog, show_qq_notifications=dialog.show, _is_closing=False,
    )
    with monkeypatch.context() as scope:
        scope.setattr(service.store, "save", fail_save)
        event = QCloseEvent()
        MainWindow.closeEvent(window, event)
        assert not event.isAccepted()
        assert not window._is_closing
        assert not service._closed
        assert dialog.isVisible() and dialog._dirty
    dialog.close()
    assert service.store.load().app_id == "NEW_APP"


def test_tutorial_platform_and_avatar_links_open_correct_targets(dialog, monkeypatch):
    from auto_bdsp_rng.resources import app_icon_path

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    entry_url = dialog.page.url()
    dialog.command("navigate", {"guide": True, "step": 0})
    web_wait(dialog, "document.querySelector('#bd-step-action a') !== null")
    web_js(dialog, "document.querySelector('#bd-step-action a').click()")
    web_wait(dialog, "document.querySelector('#bd-step-action a') !== null")
    assert opened[-1].toString() == "https://q.qq.com/#/apps"

    dialog.command("navigate", {"step": 5})
    web_wait(dialog, "document.querySelector('#bd-step-detail a')?.textContent === '打开软件头像图片'")
    web_js(dialog, "document.querySelector('#bd-step-detail a').click()")
    web_wait(dialog, "document.querySelector('#bd-step-detail a') !== null")
    assert opened[-1].isLocalFile()
    assert opened[-1].toLocalFile() == str(app_icon_path()).replace('\\', '/')
    assert len(opened) == 2
    assert dialog.page.url() == entry_url
    dialog.command("navigate", {"step": 6})
    web_wait(dialog, "document.querySelector('#bd-step-detail a') === null")


def test_leaving_binding_page_cancels_and_records_are_literal_text(dialog, service):
    click(dialog, "verify")
    web_wait(dialog, "!document.getElementById('bd-bind-user').disabled")
    click(dialog, "bind-user")
    web_wait(dialog, "!document.getElementById('bd-binding').hidden")
    click(dialog, "tab-records")
    web_wait(dialog, "document.getElementById('bd-binding').hidden")
    assert not service.setup.busy
    service._record("图文测试", "user", False, "<img src=x onerror=alert(1)> 上传失败")
    web_wait(dialog, "document.getElementById('bd-record-table').textContent.includes('上传失败')")
    assert web_js(dialog, "document.querySelector('#bd-record-table img') === null")


@pytest.mark.parametrize("size", [(880, 820), (680, 520)])
def test_web_tutorial_original_images_and_visible_layout(dialog, size):
    dialog.resize(*size)
    click(dialog, "guide-open")
    web_js(dialog, "document.querySelector('[data-step=\"11\"]').click()")
    web_wait(dialog, "document.getElementById('bd-step-image').complete && document.getElementById('bd-step-image').naturalWidth > 0")
    assert web_js(dialog, "document.querySelectorAll('[data-step]').length") == 12
    assert not web_js(dialog, "document.documentElement.scrollWidth > innerWidth")
    assert web_js(dialog, "document.getElementById('bd-next').getBoundingClientRect().bottom <= innerHeight")
    web_js(dialog, "document.querySelector('.bd-screenshot-stage').click()")
    web_wait(dialog, "document.getElementById('bd-zoom').open")
    click(dialog, "zoom-close")


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
