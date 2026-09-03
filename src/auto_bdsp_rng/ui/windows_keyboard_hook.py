from __future__ import annotations

import ctypes
import os
import queue
import threading
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import Qt


WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000
_THREAD_STOP_TIMEOUT_SECONDS = 2.0

_KEY_DOWN_MESSAGES = frozenset((WM_KEYDOWN, WM_SYSKEYDOWN))
_KEY_UP_MESSAGES = frozenset((WM_KEYUP, WM_SYSKEYUP))
_STOP_DISPATCH = object()


@dataclass(frozen=True)
class _DispatchStop:
    error: BaseException | None


class KeyboardHookError(RuntimeError):
    """Base error raised by the Windows global keyboard hook."""


class KeyboardHookUnavailableError(KeyboardHookError):
    """Raised when the global hook cannot run on the current platform."""


class KeyboardHookConfigurationError(KeyboardHookError):
    """Raised when a Qt key cannot be represented unambiguously as a VK."""


def _qt_key(name: str) -> int:
    return int(getattr(Qt.Key, name))


_DIRECT_QT_TO_VK = {
    _qt_key("Key_Backspace"): (0x08,),
    _qt_key("Key_Tab"): (0x09,),
    _qt_key("Key_Backtab"): (0x09,),
    _qt_key("Key_Clear"): (0x0C,),
    _qt_key("Key_Return"): (0x0D,),
    _qt_key("Key_Enter"): (0x0D,),
    _qt_key("Key_Pause"): (0x13,),
    _qt_key("Key_Escape"): (0x1B,),
    _qt_key("Key_Space"): (0x20,),
    _qt_key("Key_PageUp"): (0x21,),
    _qt_key("Key_PageDown"): (0x22,),
    _qt_key("Key_End"): (0x23,),
    _qt_key("Key_Home"): (0x24,),
    _qt_key("Key_Left"): (0x25,),
    _qt_key("Key_Up"): (0x26,),
    _qt_key("Key_Right"): (0x27,),
    _qt_key("Key_Down"): (0x28,),
    _qt_key("Key_Print"): (0x2C,),
    _qt_key("Key_Insert"): (0x2D,),
    _qt_key("Key_Delete"): (0x2E,),
    _qt_key("Key_Help"): (0x2F,),
    _qt_key("Key_Shift"): (0xA0, 0xA1),
    _qt_key("Key_Control"): (0xA2, 0xA3),
    _qt_key("Key_Alt"): (0xA4, 0xA5),
    _qt_key("Key_Meta"): (0x5B, 0x5C),
    _qt_key("Key_Menu"): (0x5D,),
    _qt_key("Key_CapsLock"): (0x14,),
    _qt_key("Key_NumLock"): (0x90,),
    _qt_key("Key_ScrollLock"): (0x91,),
    _qt_key("Key_Semicolon"): (0xBA,),
    _qt_key("Key_Equal"): (0xBB,),
    _qt_key("Key_Plus"): (0xBB, 0x6B),
    _qt_key("Key_Comma"): (0xBC,),
    _qt_key("Key_Minus"): (0xBD, 0x6D),
    _qt_key("Key_Underscore"): (0xBD, 0x6D),
    _qt_key("Key_Period"): (0xBE,),
    _qt_key("Key_Slash"): (0xBF, 0x6F),
    _qt_key("Key_QuoteLeft"): (0xC0,),
    _qt_key("Key_BracketLeft"): (0xDB,),
    _qt_key("Key_Backslash"): (0xDC,),
    _qt_key("Key_BracketRight"): (0xDD,),
    _qt_key("Key_Apostrophe"): (0xDE,),
}


def qt_key_to_virtual_keys(qt_key: int) -> tuple[int, ...]:
    """Return all Windows virtual-key codes represented by a Qt key."""

    key = int(qt_key)
    if _qt_key("Key_A") <= key <= _qt_key("Key_Z"):
        return (key,)
    if _qt_key("Key_0") <= key <= _qt_key("Key_9"):
        digit = key - _qt_key("Key_0")
        return (0x30 + digit, 0x60 + digit)
    if _qt_key("Key_F1") <= key <= _qt_key("Key_F24"):
        return (0x70 + key - _qt_key("Key_F1"),)
    return _DIRECT_QT_TO_VK.get(key, ())


def qt_key_to_virtual_key(qt_key: int) -> int | None:
    """Return the primary Windows virtual-key code for a Qt key."""

    keys = qt_key_to_virtual_keys(qt_key)
    return keys[0] if keys else None


def _build_vk_mapping(mapped_qt_keys: Iterable[int]) -> dict[int, int]:
    qt_keys = {int(key) for key in mapped_qt_keys if int(key) != 0}
    qt_keys.add(_qt_key("Key_Escape"))
    unsupported: list[int] = []
    result: dict[int, int] = {}
    for qt_key in sorted(qt_keys):
        virtual_keys = qt_key_to_virtual_keys(qt_key)
        if not virtual_keys:
            unsupported.append(qt_key)
            continue
        for virtual_key in virtual_keys:
            existing = result.get(virtual_key)
            if existing is not None and existing != qt_key:
                raise KeyboardHookConfigurationError(
                    f"Qt keys {existing} and {qt_key} both map to Windows VK 0x{virtual_key:02X}"
                )
            result[virtual_key] = qt_key
    if unsupported:
        values = ", ".join(str(key) for key in unsupported)
        raise KeyboardHookConfigurationError(
            f"These Qt keys do not have a supported Windows VK mapping: {values}"
        )
    return result


class _HookApi(Protocol):
    def prepare_message_queue(self) -> None: ...

    def current_thread_id(self) -> int: ...

    def make_hook_proc(self, callback: Callable[[int, int, int], int]) -> object: ...

    def install_hook(self, hook_proc: object) -> object: ...

    def uninstall_hook(self, hook_handle: object) -> None: ...

    def call_next(self, hook_handle: object | None, n_code: int, w_param: int, l_param: int) -> int: ...

    def virtual_key_from_lparam(self, l_param: int) -> int: ...

    def get_message(self) -> tuple[int, object]: ...

    def dispatch_message(self, message: object) -> None: ...

    def post_quit(self, thread_id: int) -> None: ...


class _KbdLlHookStruct(ctypes.Structure):
    _fields_ = (
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class _Message(ctypes.Structure):
    _fields_ = (
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
        ("lPrivate", wintypes.DWORD),
    )


class _CtypesWindowsHookApi:
    def __init__(self) -> None:
        if os.name != "nt":
            raise KeyboardHookUnavailableError("Windows global keyboard hooks are only available on Windows")

        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._hook_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        self._user32.SetWindowsHookExW.argtypes = (
            ctypes.c_int,
            self._hook_proc_type,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        )
        self._user32.SetWindowsHookExW.restype = wintypes.HANDLE
        self._user32.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
        self._user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        self._user32.CallNextHookEx.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self._user32.CallNextHookEx.restype = ctypes.c_ssize_t
        self._user32.PeekMessageW.argtypes = (
            ctypes.POINTER(_Message),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        )
        self._user32.PeekMessageW.restype = wintypes.BOOL
        self._user32.GetMessageW.argtypes = (
            ctypes.POINTER(_Message),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        )
        self._user32.GetMessageW.restype = wintypes.BOOL
        self._user32.TranslateMessage.argtypes = (ctypes.POINTER(_Message),)
        self._user32.TranslateMessage.restype = wintypes.BOOL
        self._user32.DispatchMessageW.argtypes = (ctypes.POINTER(_Message),)
        self._user32.DispatchMessageW.restype = ctypes.c_ssize_t
        self._user32.PostThreadMessageW.argtypes = (
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self._user32.PostThreadMessageW.restype = wintypes.BOOL
        self._kernel32.GetCurrentThreadId.argtypes = ()
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self._kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        self._kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    @staticmethod
    def _win_error(action: str) -> KeyboardHookError:
        code = ctypes.get_last_error()
        detail = ctypes.FormatError(code).strip() if code else "unknown Windows error"
        return KeyboardHookError(f"{action} failed: {detail} (error {code})")

    def prepare_message_queue(self) -> None:
        message = _Message()
        self._user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_NOREMOVE)

    def current_thread_id(self) -> int:
        return int(self._kernel32.GetCurrentThreadId())

    def make_hook_proc(self, callback: Callable[[int, int, int], int]) -> object:
        return self._hook_proc_type(callback)

    def install_hook(self, hook_proc: object) -> object:
        module_handle = self._kernel32.GetModuleHandleW(None)
        if not module_handle:
            raise self._win_error("GetModuleHandleW")
        handle = self._user32.SetWindowsHookExW(WH_KEYBOARD_LL, hook_proc, module_handle, 0)
        if not handle:
            raise self._win_error("SetWindowsHookExW")
        return handle

    def uninstall_hook(self, hook_handle: object) -> None:
        if not self._user32.UnhookWindowsHookEx(hook_handle):
            code = ctypes.get_last_error()
            # ERROR_INVALID_HOOK_HANDLE means another shutdown path already removed it.
            if code != 1404:
                raise self._win_error("UnhookWindowsHookEx")

    def call_next(self, hook_handle: object | None, n_code: int, w_param: int, l_param: int) -> int:
        return int(self._user32.CallNextHookEx(hook_handle, n_code, w_param, l_param))

    def virtual_key_from_lparam(self, l_param: int) -> int:
        data = ctypes.cast(l_param, ctypes.POINTER(_KbdLlHookStruct)).contents
        return int(data.vkCode)

    def get_message(self) -> tuple[int, object]:
        message = _Message()
        result = int(self._user32.GetMessageW(ctypes.byref(message), None, 0, 0))
        if result == -1:
            raise self._win_error("GetMessageW")
        return result, message

    def dispatch_message(self, message: object) -> None:
        typed_message = message
        self._user32.TranslateMessage(ctypes.byref(typed_message))
        self._user32.DispatchMessageW(ctypes.byref(typed_message))

    def post_quit(self, thread_id: int) -> None:
        if not self._user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0):
            raise self._win_error("PostThreadMessageW(WM_QUIT)")


class WindowsKeyboardHook:
    """Non-blocking Windows ``WH_KEYBOARD_LL`` keyboard event source.

    The hook thread only deduplicates and queues mapped key transitions. The
    supplied callback runs serially on a separate dispatcher thread, so it may
    forward events to Qt or a device worker without delaying the system hook.
    ``Escape`` is always captured in addition to the configured Qt keys.
    ``hook_stopped`` runs on the dispatcher after any synthetic key releases;
    its argument is ``None`` for an explicit clean stop, otherwise the error
    that ended the hook. Both callbacks must marshal UI work to the Qt thread.
    """

    def __init__(
        self,
        key_event: Callable[[int, bool], None],
        *,
        hook_stopped: Callable[[BaseException | None], None] | None = None,
        _api: _HookApi | None = None,
    ) -> None:
        if not callable(key_event):
            raise TypeError("key_event must be callable")
        if hook_stopped is not None and not callable(hook_stopped):
            raise TypeError("hook_stopped must be callable or None")
        self._key_event = key_event
        self._hook_stopped = hook_stopped
        self._api = _api
        self._state_lock = threading.RLock()
        self._pressed_lock = threading.Lock()
        self._pressed_vks: set[int] = set()
        self._vk_to_qt: dict[int, int] = {}
        self._event_queue: queue.Queue[tuple[int, bool] | object] | None = None
        self._hook_thread: threading.Thread | None = None
        self._dispatch_thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._hook_handle: object | None = None
        self._hook_proc: object | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._last_error: BaseException | None = None
        self._lifecycle_error: BaseException | None = None
        self._pending_uninstall_error: BaseException | None = None
        self._stop_requested = False

    @staticmethod
    def is_supported() -> bool:
        return os.name == "nt"

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            thread_running = self._hook_thread is not None and self._hook_thread.is_alive()
            return thread_running or self._hook_handle is not None

    @property
    def last_error(self) -> BaseException | None:
        with self._state_lock:
            return self._last_error

    def start(self, mapped_qt_keys: Iterable[int]) -> bool:
        self._finish_previous_dispatcher()
        with self._state_lock:
            active_thread = self._hook_thread is not None and self._hook_thread.is_alive()
            pending_handle = self._hook_handle
            api = self._api
        if active_thread:
            return False
        if pending_handle is not None:
            if api is None:
                raise KeyboardHookError("Previous Windows keyboard hook has no Win32 API owner")
            try:
                self._uninstall_hook_handle(api, pending_handle)
            except BaseException as exc:
                raise KeyboardHookError(
                    f"Previous Windows keyboard hook is still installed: {exc}"
                ) from exc

        with self._state_lock:
            if self._hook_thread is not None:
                if self._hook_thread.is_alive():
                    return False
                self._clear_finished_threads_locked()
            if self._api is None:
                if not self.is_supported():
                    raise KeyboardHookUnavailableError(
                        "Windows global keyboard hooks are unavailable on this platform"
                    )
                self._api = _CtypesWindowsHookApi()

            self._vk_to_qt = _build_vk_mapping(mapped_qt_keys)
            self._pressed_vks.clear()
            self._ready.clear()
            self._startup_error = None
            self._last_error = None
            self._lifecycle_error = None
            self._pending_uninstall_error = None
            self._stop_requested = False
            self._event_queue = queue.Queue()
            self._dispatch_thread = threading.Thread(
                target=self._dispatch_events,
                name="EasyCon keyboard dispatch",
                daemon=True,
            )
            self._hook_thread = threading.Thread(
                target=self._run_hook,
                name="EasyCon Windows keyboard hook",
                daemon=True,
            )
            dispatch_thread = self._dispatch_thread
            hook_thread = self._hook_thread
            dispatch_thread.start()
            hook_thread.start()

        if not self._ready.wait(timeout=5.0):
            self.stop()
            raise KeyboardHookError("Timed out while starting the Windows keyboard hook")
        with self._state_lock:
            startup_error = self._startup_error
        if startup_error is not None:
            self.stop()
            if isinstance(startup_error, KeyboardHookError):
                raise startup_error
            raise KeyboardHookError(f"Failed to start the Windows keyboard hook: {startup_error}") from startup_error
        return True

    def stop(self) -> bool:
        with self._state_lock:
            hook_thread = self._hook_thread
            dispatch_thread = self._dispatch_thread
            thread_id = self._thread_id
            api = self._api
            lifecycle_error_before_stop = self._lifecycle_error
            pending_handle = self._hook_handle
            self._stop_requested = True
        if hook_thread is None and dispatch_thread is None and pending_handle is None:
            return False

        post_error: BaseException | None = None
        if hook_thread is not None and hook_thread.is_alive() and thread_id is not None and api is not None:
            try:
                api.post_quit(thread_id)
            except BaseException as exc:
                post_error = exc
                self._record_lifecycle_error(exc)

        current = threading.current_thread()
        if hook_thread is not None and hook_thread is not current:
            hook_thread.join(timeout=_THREAD_STOP_TIMEOUT_SECONDS)
        if hook_thread is not None and hook_thread.is_alive():
            raise KeyboardHookError(
                f"Windows keyboard hook thread did not stop within "
                f"{_THREAD_STOP_TIMEOUT_SECONDS:g} seconds"
            )

        uninstall_error: BaseException | None = None
        with self._state_lock:
            pending_handle = self._hook_handle
        if pending_handle is not None:
            if api is None:
                uninstall_error = KeyboardHookError(
                    "Windows keyboard hook has no Win32 API owner for cleanup"
                )
                self._record_lifecycle_error(uninstall_error)
            else:
                try:
                    self._uninstall_hook_handle(api, pending_handle)
                except BaseException as exc:
                    uninstall_error = exc

        if dispatch_thread is not None and dispatch_thread is not current:
            dispatch_thread.join(timeout=_THREAD_STOP_TIMEOUT_SECONDS)
        if dispatch_thread is not None and dispatch_thread.is_alive() and dispatch_thread is not current:
            raise KeyboardHookError(
                f"Keyboard event dispatcher did not stop within "
                f"{_THREAD_STOP_TIMEOUT_SECONDS:g} seconds"
            )

        with self._state_lock:
            lifecycle_error_after_stop = self._lifecycle_error
            self._clear_finished_threads_locked()
        if post_error is not None:
            raise KeyboardHookError(f"Could not request keyboard hook shutdown: {post_error}") from post_error
        if uninstall_error is not None:
            raise KeyboardHookError(
                f"Could not uninstall the Windows keyboard hook: {uninstall_error}"
            ) from uninstall_error
        if lifecycle_error_before_stop is None and lifecycle_error_after_stop is not None:
            if isinstance(lifecycle_error_after_stop, KeyboardHookError):
                raise lifecycle_error_after_stop
            raise KeyboardHookError(
                f"Windows keyboard hook shutdown failed: {lifecycle_error_after_stop}"
            ) from lifecycle_error_after_stop
        return True

    def _finish_previous_dispatcher(self) -> None:
        with self._state_lock:
            hook_thread = self._hook_thread
            dispatch_thread = self._dispatch_thread
        if hook_thread is not None and hook_thread.is_alive():
            return
        if dispatch_thread is not None and dispatch_thread.is_alive():
            if dispatch_thread is threading.current_thread():
                raise KeyboardHookError(
                    "Cannot restart the keyboard hook from its dispatcher callback"
                )
            dispatch_thread.join(timeout=_THREAD_STOP_TIMEOUT_SECONDS)
            if dispatch_thread.is_alive():
                raise KeyboardHookError(
                    "Previous keyboard event dispatcher is still stopping"
                )
        with self._state_lock:
            self._clear_finished_threads_locked()

    def _clear_finished_threads_locked(self) -> None:
        if self._hook_thread is not None and not self._hook_thread.is_alive():
            self._hook_thread = None
            self._thread_id = None
            if self._hook_handle is None:
                self._hook_proc = None
        if self._dispatch_thread is not None and not self._dispatch_thread.is_alive():
            self._dispatch_thread = None
            self._event_queue = None

    def _run_hook(self) -> None:
        api = self._api
        assert api is not None
        hook_handle: object | None = None
        ready_was_set = False
        try:
            api.prepare_message_queue()
            thread_id = api.current_thread_id()
            hook_proc = api.make_hook_proc(self._hook_callback)
            hook_handle = api.install_hook(hook_proc)
            with self._state_lock:
                self._thread_id = thread_id
                self._hook_proc = hook_proc
                self._hook_handle = hook_handle
            self._ready.set()
            ready_was_set = True

            while True:
                result, message = api.get_message()
                if result == 0:
                    with self._state_lock:
                        stop_requested = self._stop_requested
                    if not stop_requested:
                        self._record_lifecycle_error(
                            KeyboardHookError("Windows keyboard hook message loop stopped unexpectedly")
                        )
                    break
                api.dispatch_message(message)
        except BaseException as exc:
            with self._state_lock:
                if not ready_was_set:
                    self._startup_error = exc
                else:
                    self._record_lifecycle_error_locked(exc)
        finally:
            if hook_handle is not None:
                try:
                    self._uninstall_hook_handle(api, hook_handle)
                except BaseException:
                    pass
            if not ready_was_set:
                event_queue = self._event_queue
                if event_queue is not None:
                    event_queue.put(_STOP_DISPATCH)
                self._ready.set()
                return

            self._queue_all_releases()
            event_queue = self._event_queue
            if event_queue is not None:
                with self._state_lock:
                    lifecycle_error = self._lifecycle_error
                event_queue.put(_DispatchStop(lifecycle_error))
            self._ready.set()

    def _uninstall_hook_handle(self, api: _HookApi, hook_handle: object) -> None:
        try:
            api.uninstall_hook(hook_handle)
        except BaseException as exc:
            with self._state_lock:
                if self._hook_handle is hook_handle and self._pending_uninstall_error is None:
                    self._pending_uninstall_error = exc
                self._record_lifecycle_error_locked(exc)
            raise

        with self._state_lock:
            if self._hook_handle is not hook_handle:
                return
            pending_error = self._pending_uninstall_error
            self._hook_handle = None
            self._hook_proc = None
            self._pending_uninstall_error = None
            if pending_error is not None and self._lifecycle_error is pending_error:
                self._lifecycle_error = None

    def _record_lifecycle_error(self, exc: BaseException) -> None:
        with self._state_lock:
            self._record_lifecycle_error_locked(exc)

    def _record_lifecycle_error_locked(self, exc: BaseException) -> None:
        if self._lifecycle_error is None:
            self._lifecycle_error = exc
        self._last_error = exc

    def _hook_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        api = self._api
        hook_handle = self._hook_handle
        if api is None:
            return 0
        try:
            with self._state_lock:
                stop_requested = self._stop_requested
            if stop_requested:
                return api.call_next(hook_handle, n_code, int(w_param), int(l_param))
            message = int(w_param)
            if n_code >= 0 and message in _KEY_DOWN_MESSAGES | _KEY_UP_MESSAGES:
                virtual_key = api.virtual_key_from_lparam(l_param)
                qt_key = self._vk_to_qt.get(virtual_key)
                if qt_key is not None:
                    down = message in _KEY_DOWN_MESSAGES
                    emit = False
                    with self._pressed_lock:
                        if down and virtual_key not in self._pressed_vks:
                            already_active = any(
                                self._vk_to_qt.get(pressed_vk) == qt_key
                                for pressed_vk in self._pressed_vks
                            )
                            self._pressed_vks.add(virtual_key)
                            emit = not already_active
                        elif not down and virtual_key in self._pressed_vks:
                            self._pressed_vks.remove(virtual_key)
                            emit = not any(
                                self._vk_to_qt.get(pressed_vk) == qt_key
                                for pressed_vk in self._pressed_vks
                            )
                    if emit:
                        event_queue = self._event_queue
                        if event_queue is not None:
                            event_queue.put_nowait((qt_key, down))
                    return 1
        except BaseException as exc:
            with self._state_lock:
                if self._last_error is None:
                    self._last_error = exc
        try:
            return api.call_next(hook_handle, n_code, int(w_param), int(l_param))
        except BaseException as exc:
            with self._state_lock:
                self._last_error = exc
            return 0

    def _queue_all_releases(self) -> None:
        event_queue = self._event_queue
        if event_queue is None:
            return
        with self._pressed_lock:
            pressed = sorted(self._pressed_vks)
            self._pressed_vks.clear()
        released_qt_keys: set[int] = set()
        for virtual_key in pressed:
            qt_key = self._vk_to_qt.get(virtual_key)
            if qt_key is not None and qt_key not in released_qt_keys:
                released_qt_keys.add(qt_key)
                event_queue.put_nowait((qt_key, False))

    def _dispatch_events(self) -> None:
        event_queue = self._event_queue
        assert event_queue is not None
        while True:
            event = event_queue.get()
            if event is _STOP_DISPATCH:
                return
            if isinstance(event, _DispatchStop):
                if self._hook_stopped is not None:
                    try:
                        self._hook_stopped(event.error)
                    except BaseException as exc:
                        with self._state_lock:
                            self._last_error = exc
                return
            qt_key, down = event
            try:
                self._key_event(qt_key, down)
            except BaseException as exc:
                with self._state_lock:
                    self._last_error = exc


__all__ = (
    "KeyboardHookConfigurationError",
    "KeyboardHookError",
    "KeyboardHookUnavailableError",
    "WindowsKeyboardHook",
    "qt_key_to_virtual_key",
    "qt_key_to_virtual_keys",
)
