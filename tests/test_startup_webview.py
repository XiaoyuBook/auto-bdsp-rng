from __future__ import annotations

import json
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng import app_settings
from auto_bdsp_rng.ui.startup_dialog import StartupNoticeDialog


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "WebView did not reach the expected state"
        QTest.qWait(20)


def evaluate(dialog, script):
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(5000)
    results = []

    def done(result):
        results.append(result)
        loop.quit()

    dialog.view.page().runJavaScript(script, done)
    loop.exec()
    timer.stop()
    assert results, "JavaScript callback timed out"
    return results[0]


@pytest.fixture
def chooser(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", tmp_path / "配置 with spaces" / "config.json")
    app_settings.save_settings({"other": "保留"})
    dialog = StartupNoticeDialog()
    dialog.show()
    wait_until(lambda: dialog.ready)
    yield dialog
    dialog.reject()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_welcome_click_enters_workspace_without_creating_a_guide(chooser):
    assert evaluate(chooser, "document.querySelector('.start').disabled") is False
    chooser.accept()
    assert chooser.isVisible()
    assert evaluate(chooser, "document.querySelectorAll('[data-level]').length") == 0
    evaluate(chooser, "document.querySelector('.start').click()")
    wait_until(lambda: not chooser.isVisible())
    assert chooser.acknowledged
    expected = {
        "other": "保留", "startup_notice_acknowledged": True,
    }
    assert app_settings.load_settings() == expected
    assert not app_settings.should_show_startup_notice()


def test_welcome_cancel_or_unready_submit_does_not_write(chooser):
    chooser.ready = False
    chooser.bridge.enterWorkspace()
    assert chooser.isVisible()
    chooser.reject()
    assert app_settings.load_settings() == {"other": "保留"}
    assert app_settings.should_show_startup_notice()


def test_welcome_write_failure_keeps_dialog_and_allows_retry(chooser, monkeypatch):
    def fail(*_):
        raise OSError("disk full")

    with monkeypatch.context() as scope:
        scope.setattr(app_settings.os, "replace", fail)
        evaluate(chooser, "document.querySelector('.start').click()")
        wait_until(lambda: evaluate(chooser, "document.querySelector('.feedback').textContent").startswith("无法保存"))
        assert chooser.isVisible()
        assert not chooser.acknowledged
        assert app_settings.load_settings() == {"other": "保留"}
        assert not evaluate(chooser, "document.querySelector('.start').disabled")
    evaluate(chooser, "document.querySelector('.start').click()")
    wait_until(lambda: not chooser.isVisible())
    assert chooser.acknowledged
    assert app_settings.get_experience_level() is None


@pytest.mark.parametrize("width", [390, 940])
def test_welcome_layout_keeps_content_readable(chooser, width):
    chooser.resize(width, 620)
    QTest.qWait(100)
    state = json.loads(evaluate(chooser, """JSON.stringify({
        overlap: document.querySelector('.feedback').getBoundingClientRect().top < document.querySelector('.intro').getBoundingClientRect().bottom,
        overflow: document.documentElement.scrollWidth > innerWidth,
        font: getComputedStyle(document.body).fontFamily,
        guide: document.body.textContent.includes('引导模式'),
        action: document.querySelector('.start').textContent
    })"""))
    assert not state["overlap"] and not state["overflow"]
    assert "MiSans" not in state["font"]
    assert not state["guide"]
    assert state["action"] == "进入工作区"


def test_welcome_page_stays_local_and_offers_reload_on_failure(chooser):
    page = chooser.view.page()
    assert not page.acceptNavigationRequest(QUrl("https://example.com"), page.NavigationType.NavigationTypeLinkClicked, True)
    assert not page.acceptNavigationRequest(QUrl.fromLocalFile("C:/unrelated.html"), page.NavigationType.NavigationTypeLinkClicked, True)
    chooser._loaded(False)
    assert not chooser.ready
    assert chooser.stack.currentIndex() == 1
    chooser._load_page()
    wait_until(lambda: chooser.ready)
    assert chooser.stack.currentWidget() is chooser.view
