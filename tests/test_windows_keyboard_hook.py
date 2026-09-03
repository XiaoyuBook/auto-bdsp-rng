from __future__ import annotations

import queue
import threading
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt

import auto_bdsp_rng.ui.windows_keyboard_hook as hook_module
from auto_bdsp_rng.ui.windows_keyboard_hook import (
    KeyboardHookConfigurationError,
    KeyboardHookError,
    KeyboardHookUnavailableError,
    WindowsKeyboardHook,
    qt_key_to_virtual_key,
    qt_key_to_virtual_keys,
)


class FakeHookApi:
    def __init__(
        self,
        *,
        install_error: BaseException | None = None,
        uninstall_error: BaseException | None = None,
        uninstall_failures: int | None = None,
        post_quit_error: BaseException | None = None,
    ) -> None:
        self.messages: queue.Queue[tuple[int, object] | BaseException] = queue.Queue()
        self.install_error = install_error
        self.uninstall_error = uninstall_error
        self.uninstall_failures = uninstall_failures
        self.post_quit_error = post_quit_error
        self.hook_proc = None
        self.installed_handle = object()
        self.uninstalled_handles: list[object] = []
        self.call_next_events: list[tuple[object | None, int, int, int]] = []
        self.message_queue_ready = threading.Event()
        self.quit_requests: list[int] = []
        self.pressed_virtual_keys: set[int] = set()

    def prepare_message_queue(self) -> None:
        self.message_queue_ready.set()

    def current_thread_id(self) -> int:
        return 1234

    def make_hook_proc(self, callback):
        self.hook_proc = callback
        return callback

    def install_hook(self, hook_proc):
        if self.install_error is not None:
            raise self.install_error
        return self.installed_handle

    def uninstall_hook(self, hook_handle) -> None:
        self.uninstalled_handles.append(hook_handle)
        should_fail = self.uninstall_error is not None and (
            self.uninstall_failures is None or self.uninstall_failures > 0
        )
        if should_fail:
            if self.uninstall_failures is not None:
                self.uninstall_failures -= 1
            raise self.uninstall_error

    def call_next(self, hook_handle, n_code, w_param, l_param) -> int:
        self.call_next_events.append((hook_handle, n_code, w_param, l_param))
        return 777

    def virtual_key_from_lparam(self, l_param: int) -> int:
        return l_param

    def is_key_pressed(self, virtual_key: int) -> bool:
        return virtual_key in self.pressed_virtual_keys

    def get_message(self) -> tuple[int, object]:
        result = self.messages.get(timeout=2.0)
        if isinstance(result, BaseException):
            raise result
        return result

    def dispatch_message(self, message: object) -> None:
        return None

    def post_quit(self, thread_id: int) -> None:
        self.quit_requests.append(thread_id)
        self.messages.put((0, None))
        if self.post_quit_error is not None:
            raise self.post_quit_error

    def emit(self, virtual_key: int, message: int) -> int:
        assert self.hook_proc is not None
        if message in (hook_module.WM_KEYDOWN, hook_module.WM_SYSKEYDOWN):
            self.pressed_virtual_keys.add(virtual_key)
        elif message in (hook_module.WM_KEYUP, hook_module.WM_SYSKEYUP):
            self.pressed_virtual_keys.discard(virtual_key)
        return self.hook_proc(0, message, virtual_key)


def wait_until(predicate, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


@pytest.mark.parametrize(
    ("qt_key", "expected"),
    [
        (Qt.Key.Key_A, (0x41,)),
        (Qt.Key.Key_7, (0x37, 0x67)),
        (Qt.Key.Key_Left, (0x25,)),
        (Qt.Key.Key_F12, (0x7B,)),
        (Qt.Key.Key_Backspace, (0x08,)),
        (Qt.Key.Key_Delete, (0x2E,)),
        (Qt.Key.Key_Control, (0xA2, 0xA3, 0x11)),
        (Qt.Key.Key_Plus, (0xBB, 0x6B)),
        (Qt.Key.Key_Minus, (0xBD, 0x6D)),
    ],
)
def test_qt_key_to_virtual_keys_covers_vpad_mapping_families(qt_key, expected):
    assert qt_key_to_virtual_keys(qt_key) == expected
    assert qt_key_to_virtual_key(qt_key) == expected[0]


def test_unsupported_qt_key_is_reported_before_hook_install():
    api = FakeHookApi()
    hook = WindowsKeyboardHook(lambda _key, _down, _control_down: None, _api=api)

    with pytest.raises(KeyboardHookConfigurationError, match="do not have a supported"):
        hook.start({Qt.Key.Key_MediaPlay})

    assert api.hook_proc is None


def test_ambiguous_virtual_key_mapping_is_rejected():
    hook = WindowsKeyboardHook(
        lambda _key, _down, _control_down: None,
        _api=FakeHookApi(),
    )

    with pytest.raises(KeyboardHookConfigurationError, match="both map"):
        hook.start({Qt.Key.Key_Plus, Qt.Key.Key_Equal})


def test_non_windows_start_reports_unavailable(monkeypatch):
    monkeypatch.setattr(hook_module.os, "name", "posix")
    hook = WindowsKeyboardHook(lambda _key, _down, _control_down: None)

    with pytest.raises(KeyboardHookUnavailableError, match="unavailable"):
        hook.start({Qt.Key.Key_A})


def test_hook_deduplicates_repeats_swallows_mapped_keys_and_captures_escape():
    api = FakeHookApi()
    events: list[tuple[int, bool, bool]] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: events.append((key, down, control_down)),
        _api=api,
    )

    assert hook.start({Qt.Key.Key_A}) is True
    assert hook.start({Qt.Key.Key_A}) is False
    assert api.emit(0x41, hook_module.WM_KEYDOWN) == 1
    assert api.emit(0x41, hook_module.WM_KEYDOWN) == 1
    assert api.emit(0x42, hook_module.WM_KEYDOWN) == 777
    assert api.emit(0x41, hook_module.WM_KEYUP) == 1
    assert api.emit(0x41, hook_module.WM_KEYUP) == 1
    assert api.emit(0x1B, hook_module.WM_SYSKEYDOWN) == 1
    assert api.emit(0x1B, hook_module.WM_SYSKEYUP) == 1

    wait_until(lambda: len(events) == 4)
    assert events == [
        (int(Qt.Key.Key_A), True, False),
        (int(Qt.Key.Key_A), False, False),
        (int(Qt.Key.Key_Escape), True, False),
        (int(Qt.Key.Key_Escape), False, False),
    ]

    assert hook.stop() is True
    assert hook.is_running is False
    assert api.quit_requests == [1234]
    assert api.uninstalled_handles == [api.installed_handle]
    assert hook.stop() is False


@pytest.mark.parametrize(
    "control_vk",
    (0x11, 0xA2, 0xA3),
    ids=("generic-control", "left-control", "right-control"),
)
def test_control_vk_passes_through_and_is_snapshotted_on_escape(control_vk):
    api = FakeHookApi()
    events: list[tuple[int, bool, bool]] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: events.append((key, down, control_down)),
        _api=api,
    )
    hook.start({Qt.Key.Key_A})

    assert api.emit(control_vk, hook_module.WM_KEYDOWN) == 777
    assert api.emit(0x1B, hook_module.WM_KEYDOWN) == 1
    assert api.emit(0x1B, hook_module.WM_KEYUP) == 1
    assert api.emit(control_vk, hook_module.WM_KEYUP) == 777
    assert api.emit(0x1B, hook_module.WM_KEYDOWN) == 1
    assert api.emit(0x1B, hook_module.WM_KEYUP) == 1

    wait_until(lambda: len(events) == 4)
    assert events == [
        (int(Qt.Key.Key_Escape), True, True),
        (int(Qt.Key.Key_Escape), False, True),
        (int(Qt.Key.Key_Escape), True, False),
        (int(Qt.Key.Key_Escape), False, False),
    ]
    hook.stop()


def test_escape_detects_control_that_was_held_before_hook_start():
    api = FakeHookApi()
    api.pressed_virtual_keys.add(0xA2)
    events: list[tuple[int, bool, bool]] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: events.append((key, down, control_down)),
        _api=api,
    )
    hook.start({Qt.Key.Key_A})

    assert api.emit(0x1B, hook_module.WM_KEYDOWN) == 1
    assert api.emit(0x1B, hook_module.WM_KEYUP) == 1

    wait_until(lambda: len(events) == 2)
    assert events == [
        (int(Qt.Key.Key_Escape), True, True),
        (int(Qt.Key.Key_Escape), False, True),
    ]
    hook.stop()


@pytest.mark.parametrize(
    "control_vk",
    (0xA2, 0xA3, 0x11),
    ids=("left-control", "right-control", "generic-control"),
)
def test_explicitly_mapped_control_is_captured_with_updated_control_snapshot(
    control_vk,
):
    api = FakeHookApi()
    events: list[tuple[int, bool, bool]] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: events.append((key, down, control_down)),
        _api=api,
    )
    hook.start({Qt.Key.Key_Control})

    assert api.emit(control_vk, hook_module.WM_KEYDOWN) == 1
    assert api.emit(control_vk, hook_module.WM_KEYUP) == 1

    wait_until(lambda: len(events) == 2)
    assert events == [
        (int(Qt.Key.Key_Control), True, True),
        (int(Qt.Key.Key_Control), False, False),
    ]
    hook.stop()


def test_control_snapshot_stays_true_until_both_control_keys_are_released():
    api = FakeHookApi()
    events: list[tuple[int, bool, bool]] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: events.append((key, down, control_down)),
        _api=api,
    )
    hook.start({Qt.Key.Key_A})

    assert api.emit(0xA2, hook_module.WM_KEYDOWN) == 777
    assert api.emit(0xA3, hook_module.WM_KEYDOWN) == 777
    assert api.emit(0xA2, hook_module.WM_KEYUP) == 777

    # Exercise the Hook's per-key tracking without the async-state fallback.
    api.pressed_virtual_keys.clear()
    assert api.emit(0x1B, hook_module.WM_KEYDOWN) == 1
    assert api.emit(0x1B, hook_module.WM_KEYUP) == 1
    assert api.emit(0xA3, hook_module.WM_KEYUP) == 777
    assert api.emit(0x1B, hook_module.WM_KEYDOWN) == 1
    assert api.emit(0x1B, hook_module.WM_KEYUP) == 1

    wait_until(lambda: len(events) == 4)
    assert events == [
        (int(Qt.Key.Key_Escape), True, True),
        (int(Qt.Key.Key_Escape), False, True),
        (int(Qt.Key.Key_Escape), True, False),
        (int(Qt.Key.Key_Escape), False, False),
    ]
    hook.stop()


def test_stop_releases_with_control_snapshot_and_clears_it_before_restart():
    api = FakeHookApi()
    events: list[tuple[int, bool, bool]] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: events.append((key, down, control_down)),
        _api=api,
    )
    hook.start({Qt.Key.Key_A})

    assert api.emit(0xA2, hook_module.WM_KEYDOWN) == 777
    assert api.emit(0x41, hook_module.WM_KEYDOWN) == 1
    wait_until(lambda: events == [(int(Qt.Key.Key_A), True, True)])

    hook.stop()
    assert events == [
        (int(Qt.Key.Key_A), True, True),
        (int(Qt.Key.Key_A), False, True),
    ]

    api.pressed_virtual_keys.discard(0xA2)
    hook.start({Qt.Key.Key_A})
    assert api.emit(0x1B, hook_module.WM_KEYDOWN) == 1
    assert api.emit(0x1B, hook_module.WM_KEYUP) == 1
    wait_until(lambda: len(events) == 4)
    assert events[-2:] == [
        (int(Qt.Key.Key_Escape), True, False),
        (int(Qt.Key.Key_Escape), False, False),
    ]
    hook.stop()


def test_disabling_mapped_keys_passes_them_through_but_still_captures_escape():
    api = FakeHookApi()
    events: list[tuple[int, bool, bool]] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: events.append((key, down, control_down)),
        _api=api,
    )
    hook.start({Qt.Key.Key_A})

    hook.set_mapped_keys_enabled(False)

    assert api.emit(0x41, hook_module.WM_KEYDOWN) == 777
    assert api.emit(0x41, hook_module.WM_KEYUP) == 777
    assert api.emit(0x1B, hook_module.WM_KEYDOWN) == 1
    assert api.emit(0x1B, hook_module.WM_KEYUP) == 1

    wait_until(lambda: len(events) == 2)
    assert events == [
        (int(Qt.Key.Key_Escape), True, False),
        (int(Qt.Key.Key_Escape), False, False),
    ]
    hook.stop()


def test_reenabling_mapped_keys_restores_capture():
    api = FakeHookApi()
    events: list[tuple[int, bool, bool]] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: events.append((key, down, control_down)),
        _api=api,
    )
    hook.start({Qt.Key.Key_A})

    hook.set_mapped_keys_enabled(False)
    assert api.emit(0x41, hook_module.WM_KEYDOWN) == 777
    assert api.emit(0x41, hook_module.WM_KEYUP) == 777

    hook.set_mapped_keys_enabled(True)
    assert api.emit(0x41, hook_module.WM_KEYDOWN) == 1
    assert api.emit(0x41, hook_module.WM_KEYUP) == 1

    wait_until(lambda: len(events) == 2)
    assert events == [
        (int(Qt.Key.Key_A), True, False),
        (int(Qt.Key.Key_A), False, False),
    ]
    hook.stop()


def test_disabling_releases_held_key_and_passes_it_through_until_physical_release():
    api = FakeHookApi()
    events: list[tuple[int, bool, bool]] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: events.append((key, down, control_down)),
        _api=api,
    )
    hook.start({Qt.Key.Key_A})

    assert api.emit(0x41, hook_module.WM_KEYDOWN) == 1
    wait_until(lambda: events == [(int(Qt.Key.Key_A), True, False)])

    hook.set_mapped_keys_enabled(False)
    hook.set_mapped_keys_enabled(False)
    wait_until(
        lambda: events
        == [
            (int(Qt.Key.Key_A), True, False),
            (int(Qt.Key.Key_A), False, False),
        ]
    )

    assert api.emit(0x41, hook_module.WM_KEYDOWN) == 777
    hook.set_mapped_keys_enabled(True)
    assert api.emit(0x41, hook_module.WM_KEYDOWN) == 777
    assert api.emit(0x41, hook_module.WM_KEYUP) == 777
    assert events == [
        (int(Qt.Key.Key_A), True, False),
        (int(Qt.Key.Key_A), False, False),
    ]

    assert api.emit(0x41, hook_module.WM_KEYDOWN) == 1
    assert api.emit(0x41, hook_module.WM_KEYUP) == 1
    wait_until(lambda: len(events) == 4)
    assert events[-2:] == [
        (int(Qt.Key.Key_A), True, False),
        (int(Qt.Key.Key_A), False, False),
    ]
    hook.stop()


def test_hook_releases_keyboard_passthrough_as_soon_as_stop_is_requested():
    api = FakeHookApi()
    events: list[tuple[int, bool, bool]] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: events.append((key, down, control_down)),
        _api=api,
    )
    hook.start({Qt.Key.Key_A})

    with hook._state_lock:
        hook._stop_requested = True

    assert api.emit(0x41, hook_module.WM_KEYDOWN) == 777
    assert events == []
    assert api.call_next_events[-1][1:] == (0, hook_module.WM_KEYDOWN, 0x41)
    hook.stop()


def test_alias_virtual_keys_emit_one_logical_transition():
    api = FakeHookApi()
    events: list[tuple[int, bool, bool]] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: events.append((key, down, control_down)),
        _api=api,
    )
    hook.start({Qt.Key.Key_Plus})

    api.emit(0xBB, hook_module.WM_KEYDOWN)
    api.emit(0x6B, hook_module.WM_KEYDOWN)
    api.emit(0xBB, hook_module.WM_KEYUP)
    api.emit(0x6B, hook_module.WM_KEYUP)

    wait_until(lambda: len(events) == 2)
    assert events == [
        (int(Qt.Key.Key_Plus), True, False),
        (int(Qt.Key.Key_Plus), False, False),
    ]
    hook.stop()


def test_slow_consumer_never_blocks_low_level_hook_callback():
    api = FakeHookApi()
    callback_started = threading.Event()
    unblock_callback = threading.Event()

    def slow_callback(_key: int, _down: bool, _control_down: bool) -> None:
        callback_started.set()
        unblock_callback.wait(timeout=1.0)

    hook = WindowsKeyboardHook(slow_callback, _api=api)
    hook.start({Qt.Key.Key_A})

    started_at = time.monotonic()
    assert api.emit(0x41, hook_module.WM_KEYDOWN) == 1
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.1
    assert callback_started.wait(timeout=1.0)
    unblock_callback.set()
    hook.stop()


def test_stop_dispatches_release_for_every_active_logical_key():
    api = FakeHookApi()
    events: list[tuple[int, bool, bool]] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: events.append((key, down, control_down)),
        _api=api,
    )
    hook.start({Qt.Key.Key_A})

    api.emit(0x41, hook_module.WM_KEYDOWN)
    wait_until(lambda: events == [(int(Qt.Key.Key_A), True, False)])
    hook.stop()

    assert events == [
        (int(Qt.Key.Key_A), True, False),
        (int(Qt.Key.Key_A), False, False),
    ]


def test_unexpected_message_loop_exit_releases_keys_then_notifies_consumer():
    api = FakeHookApi()
    timeline: list[tuple[str, object]] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: timeline.append(
            ("key", (key, down, control_down))
        ),
        hook_stopped=lambda error: timeline.append(("stopped", error)),
        _api=api,
    )
    hook.start({Qt.Key.Key_A})
    api.emit(0x41, hook_module.WM_KEYDOWN)
    wait_until(lambda: len(timeline) == 1)

    api.messages.put((0, None))

    wait_until(lambda: len(timeline) == 3)
    assert timeline[0] == ("key", (int(Qt.Key.Key_A), True, False))
    assert timeline[1] == ("key", (int(Qt.Key.Key_A), False, False))
    assert timeline[2][0] == "stopped"
    assert isinstance(timeline[2][1], KeyboardHookError)
    assert "unexpectedly" in str(timeline[2][1])
    assert hook.is_running is False
    assert hook.stop() is True


def test_get_message_failure_releases_keys_and_reports_lifecycle_error():
    api = FakeHookApi()
    events: list[tuple[int, bool, bool]] = []
    stopped_errors: list[BaseException | None] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: events.append((key, down, control_down)),
        hook_stopped=stopped_errors.append,
        _api=api,
    )
    hook.start({Qt.Key.Key_A})
    api.emit(0x41, hook_module.WM_KEYDOWN)
    wait_until(lambda: len(events) == 1)

    api.messages.put(OSError("GetMessage failed"))

    wait_until(lambda: len(stopped_errors) == 1)
    assert events[-1] == (int(Qt.Key.Key_A), False, False)
    assert isinstance(stopped_errors[0], OSError)
    assert "GetMessage failed" in str(stopped_errors[0])
    assert isinstance(hook.last_error, OSError)
    hook.stop()


def test_uninstall_failure_is_reported_after_release_queue_is_drained():
    api = FakeHookApi(uninstall_error=OSError("unhook failed"))
    events: list[tuple[int, bool, bool]] = []
    stopped_errors: list[BaseException | None] = []
    hook = WindowsKeyboardHook(
        lambda key, down, control_down: events.append((key, down, control_down)),
        hook_stopped=stopped_errors.append,
        _api=api,
    )
    hook.start({Qt.Key.Key_A})
    api.emit(0x41, hook_module.WM_KEYDOWN)
    wait_until(lambda: len(events) == 1)

    with pytest.raises(KeyboardHookError, match="unhook failed"):
        hook.stop()

    assert events[-1] == (int(Qt.Key.Key_A), False, False)
    assert len(stopped_errors) == 1
    assert isinstance(stopped_errors[0], OSError)
    assert hook.is_running is True
    assert hook._hook_handle is api.installed_handle
    assert hook._hook_proc is not None
    assert api.uninstalled_handles == [api.installed_handle, api.installed_handle]

    api.uninstall_error = None
    assert hook.stop() is True
    assert hook.is_running is False
    assert hook._hook_handle is None
    assert hook._hook_proc is None
    assert api.uninstalled_handles == [
        api.installed_handle,
        api.installed_handle,
        api.installed_handle,
    ]
    assert hook.stop() is False


def test_transient_thread_uninstall_failure_is_retried_by_same_stop_call():
    api = FakeHookApi(
        uninstall_error=OSError("transient unhook failure"),
        uninstall_failures=1,
    )
    stopped_errors: list[BaseException | None] = []
    hook = WindowsKeyboardHook(
        lambda _key, _down, _control_down: None,
        hook_stopped=stopped_errors.append,
        _api=api,
    )
    hook.start({Qt.Key.Key_A})

    assert hook.stop() is True

    assert len(api.uninstalled_handles) == 2
    assert hook._hook_handle is None
    assert hook._hook_proc is None
    assert hook.is_running is False
    assert len(stopped_errors) == 1
    assert isinstance(stopped_errors[0], OSError)


def test_start_refuses_to_replace_hook_until_pending_uninstall_succeeds():
    api = FakeHookApi(uninstall_error=OSError("persistent unhook failure"))
    hook = WindowsKeyboardHook(lambda _key, _down, _control_down: None, _api=api)
    hook.start({Qt.Key.Key_A})
    original_proc = hook._hook_proc
    original_handle = hook._hook_handle

    api.messages.put((0, None))
    wait_until(lambda: hook._hook_thread is not None and not hook._hook_thread.is_alive())

    with pytest.raises(KeyboardHookError, match="still installed.*persistent unhook failure"):
        hook.start({Qt.Key.Key_B})

    assert hook._hook_handle is original_handle
    assert hook._hook_proc is original_proc
    assert hook.is_running is True

    api.uninstall_error = None
    assert hook.start({Qt.Key.Key_B}) is True
    hook.stop()


def test_post_quit_failure_is_clear_even_if_message_loop_also_exits():
    api = FakeHookApi(post_quit_error=OSError("post failed"))
    hook = WindowsKeyboardHook(lambda _key, _down, _control_down: None, _api=api)
    hook.start({Qt.Key.Key_A})

    with pytest.raises(KeyboardHookError, match="request keyboard hook shutdown.*post failed"):
        hook.stop()

    assert hook.is_running is False


def test_stop_timeout_can_be_retried_after_slow_dispatcher_finishes(monkeypatch):
    monkeypatch.setattr(hook_module, "_THREAD_STOP_TIMEOUT_SECONDS", 0.05)
    api = FakeHookApi()
    callback_started = threading.Event()
    unblock_callback = threading.Event()

    def slow_down_callback(_key: int, down: bool, _control_down: bool) -> None:
        if down:
            callback_started.set()
            unblock_callback.wait(timeout=1.0)

    hook = WindowsKeyboardHook(slow_down_callback, _api=api)
    hook.start({Qt.Key.Key_A})
    api.emit(0x41, hook_module.WM_KEYDOWN)
    assert callback_started.wait(timeout=1.0)

    with pytest.raises(KeyboardHookError, match="dispatcher did not stop"):
        hook.stop()

    unblock_callback.set()
    wait_until(lambda: hook._dispatch_thread is not None and not hook._dispatch_thread.is_alive())
    assert hook.stop() is True
    assert hook.stop() is False


def test_callback_failure_is_contained_and_later_events_continue():
    api = FakeHookApi()
    calls: list[tuple[int, bool, bool]] = []

    def failing_callback(key: int, down: bool, control_down: bool) -> None:
        calls.append((key, down, control_down))
        if len(calls) == 1:
            raise ValueError("consumer failed")

    hook = WindowsKeyboardHook(failing_callback, _api=api)
    hook.start({Qt.Key.Key_A})
    api.emit(0x41, hook_module.WM_KEYDOWN)
    api.emit(0x41, hook_module.WM_KEYUP)

    wait_until(lambda: len(calls) == 2)
    assert isinstance(hook.last_error, ValueError)
    hook.stop()


def test_install_failure_is_wrapped_and_dispatcher_is_stopped():
    api = FakeHookApi(install_error=OSError("installation denied"))
    stopped_errors: list[BaseException | None] = []
    hook = WindowsKeyboardHook(
        lambda _key, _down, _control_down: None,
        hook_stopped=stopped_errors.append,
        _api=api,
    )

    with pytest.raises(KeyboardHookError, match="installation denied"):
        hook.start({Qt.Key.Key_A})

    assert hook.is_running is False
    assert stopped_errors == []
