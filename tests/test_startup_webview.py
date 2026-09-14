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


@pytest.mark.parametrize("level", ["beginner", "expert"])
def test_welcome_click_saves_choice_and_acknowledgement(chooser, level):
    assert evaluate(chooser, "document.querySelector('.start').disabled") is True
    chooser.accept()
    assert chooser.isVisible()
    evaluate(chooser, "document.querySelector('.start').click()")
    assert app_settings.get_experience_level() is None
    # Both cards can be selected without saving; only continuing commits the choice.
    for selected in ("beginner", "expert", level):
        evaluate(chooser, f"document.querySelector('[data-level={selected}]').click()")
        assert evaluate(chooser, "document.querySelectorAll('.choice[aria-pressed=true]').length") == 1
        assert not evaluate(chooser, "document.querySelector('.start').disabled")
        assert app_settings.get_experience_level() is None
    evaluate(chooser, "document.querySelector('.start').click()")
    wait_until(lambda: not chooser.isVisible())
    assert chooser.selected_experience_level == level
    expected = {
        "other": "保留", "experience_level": level, "startup_notice_acknowledged": True,
    }
    if level == "beginner":
        progress = app_settings.get_guide_progress()
        assert progress is not None and progress["step"] == "target_selection"
        expected["guide_progress"] = progress
    assert app_settings.load_settings() == expected
    assert not app_settings.should_show_startup_notice()


def test_welcome_cancel_or_invalid_choice_does_not_write(chooser):
    for invalid in ("", "admin", "BEGINNER"):
        chooser.bridge.choose(invalid)
    evaluate(chooser, "document.querySelector('[data-level=beginner]').click()")
    chooser.reject()
    assert app_settings.load_settings() == {"other": "保留"}
    assert app_settings.should_show_startup_notice()


def test_welcome_write_failure_keeps_dialog_and_allows_retry(chooser, monkeypatch):
    def fail(*_):
        raise OSError("disk full")

    evaluate(chooser, "document.querySelector('[data-level=expert]').click()")
    with monkeypatch.context() as scope:
        scope.setattr(app_settings.os, "replace", fail)
        evaluate(chooser, "document.querySelector('.start').click()")
        wait_until(lambda: evaluate(chooser, "document.querySelector('.feedback').textContent").startswith("无法保存"))
        assert chooser.isVisible()
        assert chooser.selected_experience_level is None
        assert app_settings.load_settings() == {"other": "保留"}
        assert not evaluate(chooser, "document.querySelector('.start').disabled")
    evaluate(chooser, "document.querySelector('.start').click()")
    wait_until(lambda: not chooser.isVisible())
    assert app_settings.get_experience_level() == "expert"


@pytest.mark.parametrize("width", [390, 940])
def test_welcome_layout_keeps_hint_below_cards(chooser, width):
    chooser.resize(width, 620)
    QTest.qWait(100)
    state = json.loads(evaluate(chooser, """JSON.stringify({
        overlap: document.querySelector('.feedback').getBoundingClientRect().top < document.querySelector('.choices').getBoundingClientRect().bottom,
        overflow: document.documentElement.scrollWidth > innerWidth,
        font: getComputedStyle(document.body).fontFamily,
        description: document.querySelector('[data-level=beginner] .choice-copy').textContent,
        lead: document.body.textContent.includes('之后每次想乱新的目标')
    })"""))
    assert not state["overlap"] and not state["overflow"]
    assert "MiSans" not in state["font"]
    assert state["description"] == "进入引导模式，根据引导完成自己的第一次乱数"
    assert not state["lead"]


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
