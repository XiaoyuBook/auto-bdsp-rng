import threading

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from auto_bdsp_rng.ui.easycon_panel import EasyConPanel
from tests.test_easycon_panel import FakeNativeBackend, app
from tests.ui_wait import close_all_widgets


def test_widget_cleanup_preserves_standalone_easycon_owner_when_shutdown_fails(app, monkeypatch):
    class FailingBackend(FakeNativeBackend):
        fail_close = True

        def __init__(self):
            super().__init__()
            self.close_threads = []

        def close_if_idle(self):
            return False

        def close(self):
            self.close_threads.append(threading.get_ident())
            if self.fail_close:
                raise OSError("backend still active")
            super().close()

    backend = FailingBackend()
    panel = EasyConPanel(native_backend=backend, isolated_demo=True)
    peer = QWidget()
    peer_timer = QTimer(peer)
    peer_timer.start(10)
    delete_calls = []
    for name, root in (("easycon", panel), ("peer", peer)):
        original_delete = root.deleteLater

        def delete_root(name=name, original_delete=original_delete):
            delete_calls.append(name)
            original_delete()

        monkeypatch.setattr(root, "deleteLater", delete_root)

    try:
        with pytest.raises(AssertionError, match="Standalone EasyCon shutdown failed"):
            close_all_widgets(app)
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        assert isValid(panel) and isValid(peer)
        assert not delete_calls and peer_timer.isActive()
        assert not panel._shutdown_in_progress and not panel._shutdown_complete
        assert panel.native_backend is backend and not backend.closed
        assert panel._native_lifecycle_thread is None
        assert "backend still active" in panel.log_view.toPlainText()
        assert len(backend.close_threads) == 1
        assert backend.close_threads[0] != threading.get_ident()
    finally:
        backend.fail_close = False
        close_all_widgets(app)

    assert backend.closed and len(backend.close_threads) == 2
    assert set(delete_calls) == {"easycon", "peer"}
    assert not isValid(panel) and not isValid(peer)
