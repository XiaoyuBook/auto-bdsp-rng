"""Bounded event-loop waits for asynchronous UI completion and cleanup."""
from PySide6.QtCore import QElapsedTimer, QEvent, QTimer
from PySide6.QtTest import QTest
from shiboken6 import isValid


def wait_until(predicate, timeout_ms=5000):
    deadline = QElapsedTimer()
    deadline.start()
    while not predicate() and deadline.elapsed() < timeout_ms:
        QTest.qWait(10)
    assert predicate(), "UI operation did not finish before timeout"


def wait_search(search):
    wait_until(lambda: search.worker_thread is None)
    search.poll()


def close_widget(widget):
    if not isValid(widget):
        return
    widget.close()
    search = getattr(widget, "_static_search", None)
    if search is not None:
        panel = widget.easycon_tab
        wait_until(lambda: search._closed and not panel._shutdown_in_progress and not widget._easycon_close_pending)
    elif hasattr(widget, "_shutdown_in_progress") and hasattr(widget, "nativeShutdownFinished"):
        widget.shutdown()
        wait_until(lambda: not widget._shutdown_in_progress)
        assert widget._shutdown_complete, "Standalone EasyCon shutdown failed; resource owners remain active"


def close_all_widgets(app):
    # Popups/dialogs with a parent are owned by their root. Deleting one while
    # asynchronous root cleanup is still running can destroy a live control.
    roots = [widget for widget in app.topLevelWidgets() if isValid(widget) and widget.parent() is None]
    for widget in roots:
        if not isValid(widget):
            continue
        close_widget(widget)

    # A parentless window can still be logically owned by another root (PiP,
    # for example). Complete every staged close before deleting any roots;
    # a later close wait pumps events and would otherwise delete its display.
    for widget in roots:
        if isValid(widget):
            # Staged shutdown owns timers until it accepts the close. Stopping
            # them before waiting can strand an already-pending close forever.
            for timer in widget.findChildren(QTimer):
                timer.stop()
            widget.deleteLater()
    # processEvents alone does not guarantee delivery of DeferredDelete. Keep
    # Qt destruction on the GUI thread before another test starts a worker.
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
