from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from auto_bdsp_rng.ui.eye_demo_dialog import EyeDemoDialog
from tests.test_ui import app  # noqa: F401
from tests.test_startup_webview import evaluate, wait_until


@pytest.fixture
def demo(app):
    parent = QWidget()
    parent.show()
    dialog = EyeDemoDialog(parent)
    dialog.show()
    wait_until(lambda: dialog.ready)
    yield dialog
    dialog.reject()
    dialog.deleteLater()
    parent.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_animation_loads_loops_pauses_and_waits_for_native_ack(demo):
    assert evaluate(demo, "document.querySelector('.source').naturalWidth") == 1586
    initial = evaluate(demo, "document.querySelector('.elapsed').style.width")
    # Chromium may throttle the first offscreen frame; wait for observed motion
    # instead of assuming its compositor always presents within 200 ms.
    wait_until(lambda: evaluate(demo, "document.querySelector('.elapsed').style.width") != initial, timeout=5)
    evaluate(demo, "document.querySelector('.pause').click()")
    paused = evaluate(demo, "document.querySelector('.elapsed').style.width")
    QTest.qWait(120)
    assert evaluate(demo, "document.querySelector('.elapsed').style.width") == paused
    requested = []
    demo.learnedRequested.connect(lambda: requested.append(True))
    demo.accept()
    assert demo.isVisible()
    evaluate(demo, "document.querySelector('.learned').click()")
    wait_until(lambda: bool(requested))
    assert demo.isVisible() and demo.submitting
    demo.save_failed()
    wait_until(lambda: not evaluate(demo, "document.querySelector('.learned').disabled"))
    assert evaluate(demo, "document.querySelector('.error').textContent").startswith("无法保存")
    demo.finish_learning()
    assert not demo.isVisible()


@pytest.mark.parametrize("width,height", [(1060, 860), (860, 600), (640, 520), (390, 600)])
def test_animation_keeps_controls_visible_and_picture_proportional(demo, width, height):
    demo.resize(width, height)
    QTest.qWait(100)
    state = json.loads(evaluate(demo, """JSON.stringify((()=>{
      const scene=document.querySelector('.scene').getBoundingClientRect();
      const footer=document.querySelector('.demo-footer').getBoundingClientRect();
      const learned=document.querySelector('.learned').getBoundingClientRect();
      return {overflow:document.documentElement.scrollWidth>innerWidth,
        ratio:scene.width/scene.height,footer:footer.bottom,button:learned.bottom,height:innerHeight};
    })())"""))
    assert not state["overflow"]
    assert state["ratio"] == pytest.approx(1586 / 1280, abs=.002)
    assert state["footer"] <= state["height"] + 1
    assert state["button"] <= state["height"]


def test_animation_failure_reload_and_local_navigation(demo):
    page = demo.view.page()
    assert not page.acceptNavigationRequest(QUrl("https://example.com"), page.NavigationType.NavigationTypeLinkClicked, True)
    demo._loaded(False)
    assert not demo.ready and demo.stack.currentIndex() == 1
    demo.load_page()
    wait_until(lambda: demo.ready)
    assert demo.stack.currentIndex() == 0
