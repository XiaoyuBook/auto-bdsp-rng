"""Shared capture-card frame broker.

The broker owns the only OpenCV ``VideoCapture`` handle.  Consumers attach to
the named shared-memory ring advertised by a small JSON manifest and read the
latest complete BGR frame.  The wire format in this module is deliberately
fixed-width and little-endian so a C# client can consume it without Python
pickle (see :data:`GLOBAL_HEADER_STRUCT` and :data:`SLOT_HEADER_STRUCT`).

The implementation is usable in-process (``CaptureBroker.start`` runs a
background thread) and from a separate process (call ``serve_forever`` in the
process entry point).  A tiny stop-command file is included in the manifest;
it gives non-Python clients an explicit, dependency-free way to ask a broker
process to stop.  The shared-memory mapping itself is never unlinked by a
client.
"""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import struct
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any, Iterable, Mapping, Protocol, Self

import numpy as np
from multiprocessing.shared_memory import SharedMemory


# ---------------------------------------------------------------------------
# Wire protocol
# ---------------------------------------------------------------------------

PROTOCOL_NAME = "auto-bdsp-rng.capture"
PROTOCOL_VERSION = 1
PROTOCOL_MAGIC = b"ABRNGFB1"  # exactly eight ASCII bytes
PIXEL_FORMAT_BGR24 = 1
BROKER_ALREADY_RUNNING_EXIT_CODE = 3
MANIFEST_ENVIRONMENT_VARIABLE = "AUTO_BDSP_RNG_CAPTURE_BROKER_MANIFEST"
LEGACY_MANIFEST_ENVIRONMENT_VARIABLE = "AUTO_BDSP_RNG_CAPTURE_MANIFEST"

DEFAULT_WIDTH = 1920
DEFAULT_HEIGHT = 1080
DEFAULT_FPS = 30.0
DEFAULT_FOURCC = "MJPG"
CAPTURE_API_DIRECTSHOW = 700
CAPTURE_API_MSMF = 1400
DEFAULT_CAPTURE_API = CAPTURE_API_MSMF
DEFAULT_SLOT_COUNT = 4
DEFAULT_FIRST_FRAME_TIMEOUT = 5.0
DEFAULT_FRAME_TIMEOUT = 1.0
DEFAULT_POLL_INTERVAL = 0.005
DEFAULT_PARENT_POLL_INTERVAL = 0.1
DEFAULT_PARENT_SHUTDOWN_TIMEOUT = 1.0
_MANIFEST_WRITE_ATTEMPTS = 5
_MANIFEST_WRITE_RETRY_INTERVAL = 0.02

# 8s + 8 * I + Q + 2 * I + Q = 64 bytes.
#
#   0  magic[8]              "ABRNGFB1"
#   8  protocol_version      u32
#  12  header_size           u32 (64)
#  16  width                 u32
#  20  height                u32
#  24  stride                u32 (bytes per row)
#  28  pixel_format          u32 (1 = BGR24)
#  32  slot_count             u32
#  36  slot_size              u32 (slot header + payload)
#  40  latest_sequence        u64
#  48  latest_slot             u32
#  52  state_code              u32
#  56  heartbeat_monotonic_ns  u64
GLOBAL_HEADER_STRUCT = struct.Struct("<8sIIIIIIIIQIIQ")
GLOBAL_HEADER_SIZE = GLOBAL_HEADER_STRUCT.size
assert GLOBAL_HEADER_SIZE == 64

# The useful fields occupy 48 bytes; each slot is aligned to a 64-byte header.
#
#   0  commit_begin            u64 (odd while writer is copying)
#   8  sequence                u64
#  16  timestamp_monotonic_ns  u64
#  24  payload_size             u32
#  28  width                    u32
#  32  height                   u32
#  36  stride                   u32
#  40  commit_end              u64 (even committed token)
#  48  reserved[16]
SLOT_HEADER_STRUCT = struct.Struct("<QQQIIIIQ")
SLOT_HEADER_SIZE = 64
assert SLOT_HEADER_STRUCT.size == 48


class BrokerState(IntEnum):
    """State values stored in the shared header and manifest."""

    STARTING = 1
    RUNNING = 2
    FAILED = 3
    STOPPED = 4

    @property
    def wire_name(self) -> str:
        return self.name.lower()

    @classmethod
    def from_wire(cls, value: int | str) -> "BrokerState":
        if isinstance(value, str):
            try:
                return cls[value.upper()]
            except KeyError as exc:
                raise ValueError(f"未知 Broker 状态: {value!r}") from exc
        return cls(int(value))


class PixelFormat(StrEnum):
    BGR24 = "BGR24"


class BrokerError(RuntimeError):
    """Base class for broker/protocol failures."""


class BrokerAlreadyRunningError(BrokerError):
    """Another live broker owns the discovery manifest."""


class BrokerManifestError(BrokerError):
    """The manifest is missing, malformed, or incompatible."""


class BrokerNotFoundError(BrokerError):
    """No active broker manifest could be found."""


class BrokerUnavailableError(BrokerError):
    """A broker is present but is not able to provide frames."""


class CaptureOpenError(BrokerError):
    """The capture device could not be opened."""


class FrameFormatError(BrokerError):
    """A frame does not match the advertised BGR24 format."""


class CaptureDevice(Protocol):
    """Minimal capture-device contract used by :class:`CaptureBroker`."""

    def open(self, device_index: int, capture_api: int) -> bool: ...

    def set_properties(self, width: int, height: int, fourcc: str, fps: float) -> None: ...

    def read(self) -> tuple[bool, np.ndarray | None]: ...

    def release(self) -> None: ...


class CaptureFactory(Protocol):
    def __call__(self, device_index: int, capture_api: int) -> CaptureDevice: ...


class OpenCVCapture:
    """OpenCV adapter matching the EasyCon capture settings.

    Importing OpenCV is delayed until an instance is created, which keeps the
    protocol/client usable in environments that only need shared-memory reads.
    """

    def __init__(self, device_index: int = 0, capture_api: int = DEFAULT_CAPTURE_API) -> None:
        try:
            import cv2  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on installation
            raise CaptureOpenError(f"无法导入 OpenCV: {exc}") from exc
        self._cv2 = cv2
        self.device_index = int(device_index)
        self.capture_api = int(capture_api)
        self._capture: Any | None = None

    def open(self, device_index: int | None = None, capture_api: int | None = None) -> bool:
        if device_index is not None:
            self.device_index = int(device_index)
        if capture_api is not None:
            self.capture_api = int(capture_api)
        self._capture = self._cv2.VideoCapture(self.device_index, self.capture_api)
        return bool(self._capture.isOpened())

    def set_properties(self, width: int, height: int, fourcc: str, fps: float) -> None:
        if self._capture is None:
            return
        cv2 = self._cv2
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
        code = cv2.VideoWriter_fourcc(*fourcc)
        self._capture.set(cv2.CAP_PROP_FOURCC, code)
        self._capture.set(cv2.CAP_PROP_FPS, float(fps))

    def read(self) -> tuple[bool, np.ndarray | None]:
        if self._capture is None or not self._capture.isOpened():
            return False, None
        ok, frame = self._capture.read()
        if not ok or frame is None or getattr(frame, "size", 0) == 0:
            return False, None
        return True, frame

    def release(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            try:
                capture.release()
            except Exception:
                pass

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()


def default_manifest_path() -> Path:
    """Return the per-user discovery path used by the broker.

    Windows uses ``%LOCALAPPDATA%``.  A temp-directory fallback keeps source
    checkouts and tests portable. ``AUTO_BDSP_RNG_CAPTURE_BROKER_MANIFEST``
    overrides either location for integration tests or a portable install;
    the shorter legacy spelling remains accepted for early protocol builds.
    """

    override = os.environ.get(MANIFEST_ENVIRONMENT_VARIABLE) or os.environ.get(
        LEGACY_MANIFEST_ENVIRONMENT_VARIABLE
    )
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "auto_bdsp_rng" / "capture_broker.json"
    return Path(tempfile.gettempdir()) / "auto-bdsp_rng" / "capture_broker.json"


def _stop_command_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(f"{manifest_path.stem}.stop.json")


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class _ProcessStatus(StrEnum):
    ALIVE = "alive"
    DEAD = "dead"
    UNKNOWN = "unknown"


def _open_windows_process_wait_handle(pid: int) -> tuple[Any | None, Any | None, _ProcessStatus]:
    """Open an exact process identity using only the non-invasive wait right."""

    import ctypes

    SYNCHRONIZE = 0x00100000
    ERROR_INVALID_PARAMETER = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
    if handle:
        return handle, kernel32, _ProcessStatus.ALIVE
    if ctypes.get_last_error() == ERROR_INVALID_PARAMETER:
        return None, None, _ProcessStatus.DEAD
    return None, None, _ProcessStatus.UNKNOWN


def _wait_windows_process_handle(handle: Any, kernel32: Any) -> _ProcessStatus:
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258
    result = int(kernel32.WaitForSingleObject(handle, 0))
    if result == WAIT_OBJECT_0:
        return _ProcessStatus.DEAD
    if result == WAIT_TIMEOUT:
        return _ProcessStatus.ALIVE
    return _ProcessStatus.UNKNOWN


def _process_status(pid: int) -> _ProcessStatus:
    if pid <= 0:
        return _ProcessStatus.DEAD
    if os.name == "nt":
        handle, kernel32, status = _open_windows_process_wait_handle(pid)
        if handle is None or kernel32 is None:
            return status
        try:
            return _wait_windows_process_handle(handle, kernel32)
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return _ProcessStatus.DEAD
    except PermissionError:
        return _ProcessStatus.ALIVE
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return _ProcessStatus.DEAD
        if exc.errno == errno.EPERM:
            return _ProcessStatus.ALIVE
        return _ProcessStatus.UNKNOWN
    return _ProcessStatus.ALIVE


def _pid_is_alive(pid: int) -> bool:
    """Return false only when process death has been established."""

    return _process_status(pid) is not _ProcessStatus.DEAD


def _normalized_manifest_identity(manifest_path: Path) -> str:
    expanded = Path(manifest_path).expanduser()
    return os.path.normcase(os.path.normpath(os.path.abspath(str(expanded))))


def _broker_mutex_name(manifest_path: Path) -> str:
    identity = _normalized_manifest_identity(manifest_path).encode("utf-8", errors="surrogatepass")
    digest = hashlib.sha256(identity).hexdigest()
    return f"Local\\auto_bdsp_rng_capture_{digest}"


_LOCAL_BROKER_MUTEX_LOCK = threading.Lock()
_LOCAL_BROKER_MUTEX_NAMES: set[str] = set()


class _BrokerLifetimeMutex:
    """Serialize Broker owners for one discovery manifest on Windows."""

    def __init__(self, manifest_path: Path) -> None:
        self.name = _broker_mutex_name(manifest_path)
        self._owned = False
        self._locally_reserved = False
        self._holder_thread: threading.Thread | None = None
        self._ready_event = threading.Event()
        self._release_event = threading.Event()
        self._acquire_error: BaseException | None = None
        self._release_error: BrokerError | None = None

    def _reserve_local_name(self) -> None:
        with _LOCAL_BROKER_MUTEX_LOCK:
            if self.name in _LOCAL_BROKER_MUTEX_NAMES:
                raise BrokerAlreadyRunningError("已有共享视频源正在启动或运行")
            _LOCAL_BROKER_MUTEX_NAMES.add(self.name)
            self._locally_reserved = True

    def _release_local_name(self) -> None:
        if not self._locally_reserved:
            return
        with _LOCAL_BROKER_MUTEX_LOCK:
            _LOCAL_BROKER_MUTEX_NAMES.discard(self.name)
            self._locally_reserved = False

    def _hold_windows_mutex(self) -> None:
        handle: Any | None = None
        kernel32: Any | None = None
        acquired = False
        try:
            import ctypes

            WAIT_OBJECT_0 = 0
            WAIT_ABANDONED = 128
            WAIT_TIMEOUT = 258
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel32.WaitForSingleObject.restype = ctypes.c_ulong
            kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
            kernel32.ReleaseMutex.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int

            handle = kernel32.CreateMutexW(None, False, self.name)
            if not handle:
                raise BrokerError(
                    f"无法创建共享视频源启动锁 (winerror={ctypes.get_last_error()})"
                )
            result = int(kernel32.WaitForSingleObject(handle, 0))
            if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
                acquired = True
                self._owned = True
                self._ready_event.set()
                self._release_event.wait()
                if not kernel32.ReleaseMutex(handle):
                    self._release_error = BrokerError(
                        f"无法释放共享视频源启动锁 (winerror={ctypes.get_last_error()})"
                    )
                else:
                    self._owned = False
            elif result == WAIT_TIMEOUT:
                raise BrokerAlreadyRunningError("已有共享视频源正在启动或运行")
            else:
                raise BrokerError(
                    f"无法获取共享视频源启动锁 (wait={result}, winerror={ctypes.get_last_error()})"
                )
        except BaseException as exc:
            self._acquire_error = exc
        finally:
            if (
                handle is not None
                and kernel32 is not None
                and not kernel32.CloseHandle(handle)
                and acquired
            ):
                if self._release_error is None:
                    self._release_error = BrokerError(
                        f"无法关闭共享视频源启动锁 (winerror={ctypes.get_last_error()})"
                    )
            self._ready_event.set()

    def acquire(self) -> None:
        if os.name != "nt":
            return
        if self._release_error is not None:
            raise self._release_error
        if self._owned:
            raise BrokerError("共享视频源启动锁仍由上一生命周期持有")

        self._reserve_local_name()
        self._ready_event = threading.Event()
        self._release_event = threading.Event()
        self._acquire_error = None
        self._release_error = None
        self._holder_thread = threading.Thread(
            target=self._hold_windows_mutex,
            name="CaptureBrokerMutex",
            daemon=True,
        )
        self._holder_thread.start()
        self._ready_event.wait()
        if self._acquire_error is not None:
            error = self._acquire_error
            self._holder_thread.join()
            self._holder_thread = None
            self._release_local_name()
            raise error
        if not self._owned:
            self._holder_thread.join()
            self._holder_thread = None
            self._release_local_name()
            raise BrokerError("共享视频源启动锁未确认获得所有权")

    def release(self) -> None:
        thread = self._holder_thread
        if thread is None:
            if self._release_error is not None:
                raise self._release_error
            self._release_local_name()
            return
        self._release_event.set()
        thread.join()
        self._holder_thread = None
        if self._release_error is not None:
            raise self._release_error
        self._release_local_name()


class _ParentProcessGuard:
    """Track the exact GUI process that spawned a standalone Broker."""

    def __init__(self, pid: int) -> None:
        self.pid = max(0, int(pid))
        self._handle: Any | None = None
        self._kernel32: Any | None = None
        self._initial_status = _ProcessStatus.ALIVE
        if self.pid <= 0 or os.name != "nt":
            return

        self._handle, self._kernel32, self._initial_status = _open_windows_process_wait_handle(
            self.pid
        )

    def status(self) -> _ProcessStatus:
        if self.pid <= 0:
            return _ProcessStatus.ALIVE
        if self._handle is not None and self._kernel32 is not None:
            return _wait_windows_process_handle(self._handle, self._kernel32)
        if os.name == "nt":
            if self._initial_status is _ProcessStatus.DEAD:
                return _ProcessStatus.DEAD
            handle, kernel32, status = _open_windows_process_wait_handle(self.pid)
            if handle is not None and kernel32 is not None:
                self._handle = handle
                self._kernel32 = kernel32
                return _wait_windows_process_handle(handle, kernel32)
            return status
        if os.name != "nt" and os.getppid() != self.pid:
            return _ProcessStatus.DEAD
        return _process_status(self.pid)

    def is_alive(self) -> bool:
        return self.status() is not _ProcessStatus.DEAD

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None and self._kernel32 is not None:
            self._kernel32.CloseHandle(handle)
        self._kernel32 = None


@dataclass(frozen=True, slots=True)
class BrokerManifest:
    """JSON discovery record for one broker session."""

    schema_version: int
    protocol: str
    session_id: str
    pid: int
    state: BrokerState
    mapping_name: str
    manifest_path: str
    control_path: str
    header_size: int
    slot_header_size: int
    slot_count: int
    slot_size: int
    width: int
    height: int
    stride: int
    pixel_format: str
    capture: Mapping[str, Any] = field(default_factory=dict)
    first_frame_timeout_seconds: float = DEFAULT_FIRST_FRAME_TIMEOUT
    frame_timeout_seconds: float = DEFAULT_FRAME_TIMEOUT
    updated_at_ns: int = 0
    parent_pid: int = 0
    failure_message: str = ""

    @property
    def state_code(self) -> int:
        return int(self.state)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "protocol": self.protocol,
            "session_id": self.session_id,
            "pid": int(self.pid),
            "parent_pid": int(self.parent_pid),
            "state": self.state.wire_name,
            "state_code": int(self.state),
            "mapping_name": self.mapping_name,
            "manifest_path": self.manifest_path,
            "control_path": self.control_path,
            "header_size": int(self.header_size),
            "slot_header_size": int(self.slot_header_size),
            "slot_count": int(self.slot_count),
            "slot_size": int(self.slot_size),
            "width": int(self.width),
            "height": int(self.height),
            "stride": int(self.stride),
            "pixel_format": self.pixel_format,
            "capture": dict(self.capture),
            "timeouts": {
                "first_frame_seconds": float(self.first_frame_timeout_seconds),
                "frame_seconds": float(self.frame_timeout_seconds),
            },
            "first_frame_timeout_seconds": float(self.first_frame_timeout_seconds),
            "frame_timeout_seconds": float(self.frame_timeout_seconds),
            "updated_at_ns": int(self.updated_at_ns),
            "failure_message": self.failure_message,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, source: Path | None = None) -> "BrokerManifest":
        try:
            schema = int(raw["schema_version"])
            protocol = str(raw["protocol"])
            session_id = str(raw["session_id"])
            pid = int(raw["pid"])
            state_raw = raw.get("state_code", raw.get("state"))
            state = BrokerState.from_wire(state_raw)  # type: ignore[arg-type]
            mapping_name = str(raw["mapping_name"])
            manifest_path = str(raw.get("manifest_path", source or ""))
            control_path = str(raw.get("control_path", ""))
            header_size = int(raw["header_size"])
            slot_header_size = int(raw["slot_header_size"])
            slot_count = int(raw["slot_count"])
            slot_size = int(raw["slot_size"])
            width = int(raw["width"])
            height = int(raw["height"])
            stride = int(raw["stride"])
            pixel_format = str(raw["pixel_format"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BrokerManifestError(f"共享视频源 manifest 字段无效: {exc}") from exc
        if schema != PROTOCOL_VERSION or protocol != PROTOCOL_NAME:
            raise BrokerManifestError(f"不支持的共享视频源协议: {protocol!r} v{schema}")
        if header_size != GLOBAL_HEADER_SIZE or slot_header_size != SLOT_HEADER_SIZE:
            raise BrokerManifestError("共享视频源 header 大小不匹配")
        if slot_count < 2 or slot_size <= SLOT_HEADER_SIZE:
            raise BrokerManifestError("共享视频源 ring 参数无效")
        if width <= 0 or height <= 0 or stride < width * 3:
            raise BrokerManifestError("共享视频源分辨率或 stride 无效")
        if slot_size != SLOT_HEADER_SIZE + stride * height:
            raise BrokerManifestError("共享视频源 slot_size 与帧大小不匹配")
        if pixel_format != PixelFormat.BGR24.value:
            raise BrokerManifestError(f"不支持的共享视频源像素格式: {pixel_format!r}")
        try:
            capture_raw = raw.get("capture") or {}
            if not isinstance(capture_raw, Mapping):
                raise TypeError("capture 必须是 JSON 对象")
            timeouts = raw.get("timeouts")
            if timeouts is not None and not isinstance(timeouts, Mapping):
                raise TypeError("timeouts 必须是 JSON 对象")
            if isinstance(timeouts, Mapping):
                first_timeout = float(
                    timeouts.get(
                        "first_frame_seconds",
                        raw.get("first_frame_timeout_seconds", DEFAULT_FIRST_FRAME_TIMEOUT),
                    )
                )
                frame_timeout = float(
                    timeouts.get(
                        "frame_seconds",
                        raw.get("frame_timeout_seconds", DEFAULT_FRAME_TIMEOUT),
                    )
                )
            else:
                first_timeout = float(
                    raw.get("first_frame_timeout_seconds", DEFAULT_FIRST_FRAME_TIMEOUT)
                )
                frame_timeout = float(
                    raw.get("frame_timeout_seconds", DEFAULT_FRAME_TIMEOUT)
                )
            updated_at_ns = int(raw.get("updated_at_ns", 0))
            parent_pid = int(raw.get("parent_pid", 0) or 0)
            failure_message = str(raw.get("failure_message", "") or "")
        except (TypeError, ValueError, OverflowError) as exc:
            raise BrokerManifestError(f"共享视频源 manifest 字段无效: {exc}") from exc
        if parent_pid < 0:
            raise BrokerManifestError("共享视频源 manifest parent_pid 不能为负数")
        if (
            not math.isfinite(first_timeout)
            or not math.isfinite(frame_timeout)
            or first_timeout <= 0
            or frame_timeout <= 0
        ):
            raise BrokerManifestError("共享视频源 manifest 超时参数必须是正有限数")
        return cls(
            schema_version=schema,
            protocol=protocol,
            session_id=session_id,
            pid=pid,
            state=state,
            mapping_name=mapping_name,
            manifest_path=manifest_path,
            control_path=control_path,
            header_size=header_size,
            slot_header_size=slot_header_size,
            slot_count=slot_count,
            slot_size=slot_size,
            width=width,
            height=height,
            stride=stride,
            pixel_format=pixel_format,
            capture=dict(capture_raw),
            first_frame_timeout_seconds=first_timeout,
            frame_timeout_seconds=frame_timeout,
            updated_at_ns=updated_at_ns,
            parent_pid=parent_pid,
            failure_message=failure_message,
        )

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> "BrokerManifest":
        manifest_path = Path(path) if path is not None else default_manifest_path()
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise BrokerNotFoundError(f"未找到共享视频源 manifest: {manifest_path}") from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BrokerManifestError(f"无法读取共享视频源 manifest: {manifest_path}: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise BrokerManifestError("共享视频源 manifest 必须是 JSON 对象")
        return cls.from_dict(raw, source=manifest_path)

    def write(self, path: str | os.PathLike[str] | None = None) -> Path:
        target = Path(path) if path is not None else Path(self.manifest_path)
        _atomic_json_write(target, self.to_dict())
        return target


@dataclass(frozen=True, slots=True)
class FramePacket:
    """One immutable snapshot returned to a consumer."""

    sequence: int
    timestamp_ns: int
    width: int
    height: int
    stride: int
    data: bytes

    @property
    def payload_size(self) -> int:
        return len(self.data)

    def as_array(self, *, copy: bool = True) -> np.ndarray:
        expected = self.stride * self.height
        if len(self.data) != expected:
            raise FrameFormatError(f"帧 payload 长度 {len(self.data)} != {expected}")
        raw = np.frombuffer(self.data, dtype=np.uint8)
        if self.stride == self.width * 3:
            array = raw.reshape((self.height, self.width, 3))
        else:
            array = raw.reshape((self.height, self.stride))[:, : self.width * 3].reshape(
                (self.height, self.width, 3)
            )
        return array.copy() if copy else array


class FrameRing:
    """Fixed-layout shared-memory ring for one writer and many readers."""

    def __init__(
        self,
        shared_memory: SharedMemory,
        *,
        width: int,
        height: int,
        stride: int,
        slot_count: int,
        slot_size: int,
        owner: bool,
    ) -> None:
        self._shm = shared_memory
        self._buf = shared_memory.buf
        self.width = int(width)
        self.height = int(height)
        self.stride = int(stride)
        self.slot_count = int(slot_count)
        self.slot_size = int(slot_size)
        self.owner = bool(owner)
        self.payload_size = self.stride * self.height
        self._write_lock = threading.Lock()
        self._next_sequence = self._read_latest_sequence()

    @classmethod
    def create(
        cls,
        *,
        width: int,
        height: int,
        slot_count: int = DEFAULT_SLOT_COUNT,
        mapping_name: str | None = None,
        state: BrokerState = BrokerState.STARTING,
    ) -> "FrameRing":
        width = int(width)
        height = int(height)
        slot_count = int(slot_count)
        stride = width * 3
        if width <= 0 or height <= 0:
            raise ValueError("width/height 必须为正数")
        if slot_count < 2:
            raise ValueError("slot_count 至少为 2")
        slot_size = SLOT_HEADER_SIZE + stride * height
        total_size = GLOBAL_HEADER_SIZE + slot_count * slot_size
        name = mapping_name or f"auto_bdsp_rng_capture_{uuid.uuid4().hex}"
        try:
            shm = SharedMemory(name=name, create=True, size=total_size)
        except FileExistsError as exc:
            raise BrokerError(f"共享视频源 mapping 已存在: {name}") from exc
        ring = cls(
            shm,
            width=width,
            height=height,
            stride=stride,
            slot_count=slot_count,
            slot_size=slot_size,
            owner=True,
        )
        ring._buf[:] = b"\x00" * total_size
        ring._write_global(state=state, latest_sequence=0, latest_slot=0, heartbeat_ns=time.monotonic_ns())
        return ring

    @classmethod
    def open_from_manifest(cls, manifest: BrokerManifest) -> "FrameRing":
        expected_size = manifest.header_size + manifest.slot_count * manifest.slot_size
        try:
            shm = SharedMemory(name=manifest.mapping_name, create=False)
        except FileNotFoundError as exc:
            raise BrokerUnavailableError(f"共享视频源 mapping 不存在: {manifest.mapping_name}") from exc
        if shm.size < expected_size:
            shm.close()
            raise BrokerManifestError("共享视频源 mapping 大小不足")
        try:
            ring = cls(
                shm,
                width=manifest.width,
                height=manifest.height,
                stride=manifest.stride,
                slot_count=manifest.slot_count,
                slot_size=manifest.slot_size,
                owner=False,
            )
            ring._validate_global_header()
            return ring
        except Exception:
            shm.close()
            raise

    def _read_global(self) -> tuple[Any, ...]:
        return GLOBAL_HEADER_STRUCT.unpack_from(self._buf, 0)

    def _write_global(
        self,
        *,
        state: BrokerState | None = None,
        latest_sequence: int | None = None,
        latest_slot: int | None = None,
        heartbeat_ns: int | None = None,
    ) -> None:
        current = self._read_global() if self._buf[:8].tobytes() == PROTOCOL_MAGIC else None
        if current is None:
            current_state = BrokerState.STARTING
            current_seq = 0
            current_slot = 0
            current_heartbeat = 0
        else:
            current_state = BrokerState.from_wire(int(current[11]))
            current_seq = int(current[9])
            current_slot = int(current[10])
            current_heartbeat = int(current[12])
        values = (
            PROTOCOL_MAGIC,
            PROTOCOL_VERSION,
            GLOBAL_HEADER_SIZE,
            self.width,
            self.height,
            self.stride,
            PIXEL_FORMAT_BGR24,
            self.slot_count,
            self.slot_size,
            current_seq if latest_sequence is None else int(latest_sequence),
            current_slot if latest_slot is None else int(latest_slot),
            int(current_state if state is None else state),
            current_heartbeat if heartbeat_ns is None else int(heartbeat_ns),
        )
        GLOBAL_HEADER_STRUCT.pack_into(self._buf, 0, *values)

    def _validate_global_header(self) -> None:
        values = self._read_global()
        if values[0] != PROTOCOL_MAGIC or values[1] != PROTOCOL_VERSION:
            raise BrokerManifestError("共享视频源 magic/version 不匹配")
        if values[2] != GLOBAL_HEADER_SIZE:
            raise BrokerManifestError("共享视频源 global header 大小不匹配")
        if values[3:8] != (self.width, self.height, self.stride, PIXEL_FORMAT_BGR24, self.slot_count):
            raise BrokerManifestError("共享视频源参数与 manifest 不匹配")
        if values[8] != self.slot_size:
            raise BrokerManifestError("共享视频源 slot_size 与 manifest 不匹配")

    def _read_latest_sequence(self) -> int:
        try:
            values = self._read_global()
            if values[0] == PROTOCOL_MAGIC and values[1] == PROTOCOL_VERSION:
                return int(values[9])
        except (struct.error, ValueError):
            pass
        return 0

    @property
    def state(self) -> BrokerState:
        try:
            return BrokerState.from_wire(int(self._read_global()[11]))
        except (struct.error, ValueError):
            return BrokerState.FAILED

    @property
    def latest_sequence(self) -> int:
        return int(self._read_global()[9])

    @property
    def heartbeat_ns(self) -> int:
        return int(self._read_global()[12])

    def set_state(self, state: BrokerState, *, heartbeat_ns: int | None = None) -> None:
        # The fixed fields never change after mapping creation. Publish mutable
        # aligned scalars individually so readers cannot observe a torn 64-byte
        # header while the writer is committing the next frame.
        struct.pack_into("<I", self._buf, 52, int(state))
        struct.pack_into(
            "<Q",
            self._buf,
            56,
            time.monotonic_ns() if heartbeat_ns is None else int(heartbeat_ns),
        )

    def heartbeat(self, timestamp_ns: int | None = None) -> None:
        struct.pack_into(
            "<Q",
            self._buf,
            56,
            time.monotonic_ns() if timestamp_ns is None else int(timestamp_ns),
        )

    def write(self, frame: np.ndarray, *, timestamp_ns: int | None = None) -> FramePacket:
        """Commit one frame and return its sequence metadata.

        Only the broker writer calls this method.  The odd/even commit tokens
        let readers detect a slot being overwritten while they copy it.
        """

        if not isinstance(frame, np.ndarray):
            frame = np.asarray(frame)
        if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
            raise FrameFormatError("共享视频源帧必须是 uint8 的 HxWx3 BGR 数组")
        if frame.shape[0] != self.height or frame.shape[1] != self.width:
            raise FrameFormatError(
                f"共享视频源帧尺寸 {frame.shape[1]}x{frame.shape[0]} != {self.width}x{self.height}"
            )
        frame = np.ascontiguousarray(frame)
        payload = frame.tobytes(order="C")
        return self.write_bytes(payload, timestamp_ns=timestamp_ns)

    def write_bytes(self, payload: bytes | bytearray | memoryview, *, timestamp_ns: int | None = None) -> FramePacket:
        raw = bytes(payload)
        if len(raw) != self.payload_size:
            raise FrameFormatError(f"共享视频源 payload 长度 {len(raw)} != {self.payload_size}")
        with self._write_lock:
            self._next_sequence += 1
            sequence = self._next_sequence
            slot_index = sequence % self.slot_count
            offset = GLOBAL_HEADER_SIZE + slot_index * self.slot_size
            commit_odd = (sequence << 1) | 1
            commit_even = sequence << 1
            # Mark the slot in-flight before touching metadata or payload.
            struct.pack_into("<Q", self._buf, offset, commit_odd)
            SLOT_HEADER_STRUCT.pack_into(
                self._buf,
                offset,
                commit_odd,
                sequence,
                time.monotonic_ns() if timestamp_ns is None else int(timestamp_ns),
                len(raw),
                self.width,
                self.height,
                self.stride,
                0,
            )
            payload_offset = offset + SLOT_HEADER_SIZE
            self._buf[payload_offset : payload_offset + len(raw)] = raw
            # Publish only after all payload bytes are visible.
            struct.pack_into("<Q", self._buf, offset + 40, commit_even)
            struct.pack_into("<Q", self._buf, offset, commit_even)
            timestamp = struct.unpack_from("<Q", self._buf, offset + 16)[0]
            # Publish metadata in dependency order. Readers treat sequence as
            # the commit point, so it must be written after latest_slot.
            struct.pack_into("<I", self._buf, 48, slot_index)
            struct.pack_into("<Q", self._buf, 56, time.monotonic_ns())
            struct.pack_into("<Q", self._buf, 40, sequence)
        return FramePacket(sequence, timestamp, self.width, self.height, self.stride, raw)

    def _read_slot(self, slot_index: int) -> FramePacket | None:
        if slot_index < 0 or slot_index >= self.slot_count:
            return None
        offset = GLOBAL_HEADER_SIZE + slot_index * self.slot_size
        # A bounded retry prevents a slow reader from spinning forever while
        # the writer cycles through a high-rate ring.
        for _ in range(4):
            begin = struct.unpack_from("<Q", self._buf, offset)[0]
            if begin == 0 or begin & 1:
                continue
            fields = SLOT_HEADER_STRUCT.unpack_from(self._buf, offset)
            sequence = int(fields[1])
            timestamp = int(fields[2])
            payload_size = int(fields[3])
            width = int(fields[4])
            height = int(fields[5])
            stride = int(fields[6])
            payload_offset = offset + SLOT_HEADER_SIZE
            if payload_size <= 0 or payload_size > self.slot_size - SLOT_HEADER_SIZE:
                return None
            raw = bytes(self._buf[payload_offset : payload_offset + payload_size])
            end = struct.unpack_from("<Q", self._buf, offset + 40)[0]
            begin_after = struct.unpack_from("<Q", self._buf, offset)[0]
            if begin == end == begin_after and not (end & 1) and end == sequence << 1:
                if width != self.width or height != self.height or stride != self.stride:
                    return None
                if payload_size != self.payload_size:
                    return None
                return FramePacket(sequence, timestamp, width, height, stride, raw)
        return None

    def _read_slot_metadata(self, slot_index: int) -> tuple[int, int] | None:
        """Return committed ``(sequence, timestamp_ns)`` without copying payload."""

        if slot_index < 0 or slot_index >= self.slot_count:
            return None
        offset = GLOBAL_HEADER_SIZE + slot_index * self.slot_size
        for _ in range(4):
            begin = struct.unpack_from("<Q", self._buf, offset)[0]
            if begin == 0 or begin & 1:
                continue
            fields = SLOT_HEADER_STRUCT.unpack_from(self._buf, offset)
            sequence = int(fields[1])
            timestamp = int(fields[2])
            payload_size = int(fields[3])
            width = int(fields[4])
            height = int(fields[5])
            stride = int(fields[6])
            end = struct.unpack_from("<Q", self._buf, offset + 40)[0]
            begin_after = struct.unpack_from("<Q", self._buf, offset)[0]
            if begin == end == begin_after and not (end & 1) and end == sequence << 1:
                if width != self.width or height != self.height or stride != self.stride:
                    return None
                if payload_size != self.payload_size:
                    return None
                return sequence, timestamp
        return None

    def read_latest(self) -> FramePacket | None:
        """Return the newest complete frame, or ``None`` before first frame."""

        try:
            values = self._read_global()
            latest = int(values[9])
            latest_slot = int(values[10])
        except (struct.error, ValueError):
            return None
        if latest <= 0:
            return None
        packet = self._read_slot(latest_slot)
        if packet is not None and packet.sequence == latest:
            return packet
        # The global pointer may have been observed between writes.  Scan the
        # ring and choose the greatest valid sequence as a fallback.
        candidates = [self._read_slot(index) for index in range(self.slot_count)]
        valid = [candidate for candidate in candidates if candidate is not None]
        return max(valid, key=lambda item: item.sequence, default=None)

    def read_latest_metadata(self) -> tuple[int, int] | None:
        """Return newest committed sequence/timestamp without copying frame bytes."""

        try:
            values = self._read_global()
            latest = int(values[9])
            latest_slot = int(values[10])
        except (struct.error, ValueError):
            return None
        if latest <= 0:
            return None
        metadata = self._read_slot_metadata(latest_slot)
        if metadata is not None and metadata[0] == latest:
            return metadata
        candidates = [self._read_slot_metadata(index) for index in range(self.slot_count)]
        valid = [candidate for candidate in candidates if candidate is not None]
        return max(valid, key=lambda item: item[0], default=None)

    def snapshot_header(self) -> dict[str, int]:
        values = self._read_global()
        return {
            "width": int(values[3]),
            "height": int(values[4]),
            "stride": int(values[5]),
            "pixel_format": int(values[6]),
            "slot_count": int(values[7]),
            "slot_size": int(values[8]),
            "latest_sequence": int(values[9]),
            "latest_slot": int(values[10]),
            "state_code": int(values[11]),
            "heartbeat_monotonic_ns": int(values[12]),
        }

    def close(self, *, unlink: bool = False) -> None:
        # Release the exported memoryview first, otherwise CPython raises
        # BufferError when SharedMemory.close() is called.
        buf = getattr(self, "_buf", None)
        self._buf = None  # type: ignore[assignment]
        if buf is not None:
            try:
                buf.release()
            except Exception:
                pass
        try:
            self._shm.close()
        finally:
            if unlink and self.owner:
                try:
                    self._shm.unlink()
                except FileNotFoundError:
                    pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close(unlink=self.owner)


def discover_manifest(
    path: str | os.PathLike[str] | None = None,
    *,
    require_running: bool = False,
    require_live_pid: bool = False,
) -> BrokerManifest:
    """Load the current manifest and optionally enforce its state/PID."""

    manifest = BrokerManifest.load(path)
    if require_live_pid and not _pid_is_alive(manifest.pid):
        raise BrokerNotFoundError(f"共享视频源进程已退出: pid={manifest.pid}")
    if require_running and manifest.state is not BrokerState.RUNNING:
        raise BrokerUnavailableError(f"共享视频源当前状态为 {manifest.state.wire_name}")
    return manifest


class CaptureBrokerClient:
    """Read-only client for one broker session."""

    def __init__(self, manifest: BrokerManifest, ring: FrameRing, manifest_path: Path) -> None:
        self.manifest = manifest
        self._ring = ring
        self.manifest_path = manifest_path
        self._closed = False

    @classmethod
    def connect(
        cls,
        path: str | os.PathLike[str] | None = None,
        *,
        require_running: bool = False,
        require_live_pid: bool = False,
    ) -> "CaptureBrokerClient":
        manifest_path = Path(path) if path is not None else default_manifest_path()
        manifest = discover_manifest(
            manifest_path,
            require_running=require_running,
            require_live_pid=require_live_pid,
        )
        ring = FrameRing.open_from_manifest(manifest)
        # State can change between JSON read and mapping open; callers asking
        # for a running source get a deterministic error instead of old data.
        if require_running and ring.state is not BrokerState.RUNNING:
            state = ring.state
            ring.close()
            raise BrokerUnavailableError(f"共享视频源当前状态为 {state.wire_name}")
        return cls(manifest, ring, manifest_path)

    discover = connect

    @property
    def state(self) -> BrokerState:
        self._ensure_open()
        return self._ring.state

    @property
    def latest_sequence(self) -> int:
        self._ensure_open()
        return self._ring.latest_sequence

    @property
    def heartbeat_ns(self) -> int:
        self._ensure_open()
        return self._ring.heartbeat_ns

    def snapshot_header(self) -> dict[str, int]:
        """Return ring health metadata without copying the current frame."""

        self._ensure_open()
        return self._ring.snapshot_header()

    def read_latest_metadata(self) -> tuple[int, int] | None:
        """Return the newest ``(sequence, timestamp_ns)`` pair, if any."""

        self._ensure_open()
        return self._ring.read_latest_metadata()

    def read_latest(self, *, allow_unavailable: bool = False) -> FramePacket | None:
        self._ensure_open()
        sampled_at_ns = time.monotonic_ns()
        packet = self._ring.read_latest()
        if not allow_unavailable:
            self._ensure_available(
                None if packet is None else packet.timestamp_ns,
                sampled_at_ns=sampled_at_ns,
            )
        return packet

    def read(self, *, allow_unavailable: bool = False) -> FramePacket | None:
        return self.read_latest(allow_unavailable=allow_unavailable)

    def read_array(self, *, allow_unavailable: bool = False) -> np.ndarray | None:
        packet = self.read_latest(allow_unavailable=allow_unavailable)
        return None if packet is None else packet.as_array()

    def wait_for_frame(
        self,
        *,
        after_sequence: int = 0,
        timeout: float | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> FramePacket:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while True:
            self._ensure_open()
            metadata = self._ring.read_latest_metadata()
            self._ensure_available(None if metadata is None else metadata[1])
            if metadata is not None and metadata[0] > after_sequence:
                sampled_at_ns = time.monotonic_ns()
                packet = self._ring.read_latest()
                self._ensure_available(
                    None if packet is None else packet.timestamp_ns,
                    sampled_at_ns=sampled_at_ns,
                )
                if packet is not None and packet.sequence > after_sequence:
                    return packet
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("等待共享视频源新帧超时")
            time.sleep(max(0.0005, poll_interval))

    def request_stop(self) -> None:
        """Ask a broker process to stop through its manifest control file."""

        self._ensure_open()
        control_path = Path(self.manifest.control_path) if self.manifest.control_path else _stop_command_path(self.manifest_path)
        _atomic_json_write(
            control_path,
            {"session_id": self.manifest.session_id, "command": "stop", "requested_at_ns": time.monotonic_ns()},
        )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._ring.close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise BrokerError("共享视频源 client 已关闭")

    def _ensure_available(
        self,
        frame_timestamp_ns: int | None = None,
        *,
        sampled_at_ns: int | None = None,
    ) -> None:
        state = self._ring.state
        if state in (BrokerState.FAILED, BrokerState.STOPPED):
            raise BrokerUnavailableError(f"共享视频源当前状态为 {state.wire_name}")
        if state is not BrokerState.RUNNING or frame_timestamp_ns is None:
            return
        timeout_ns = max(1, int(self.manifest.frame_timeout_seconds * 1_000_000_000))
        # Slot timestamps are protected by the frame commit tokens; the global
        # heartbeat can be observed while its scalar update is still in flight.
        timestamp_ns = int(frame_timestamp_ns)
        observed_at_ns = time.monotonic_ns() if sampled_at_ns is None else int(sampled_at_ns)
        age_ns = observed_at_ns - timestamp_ns
        timestamp_is_future = age_ns < 0 and timestamp_ns > time.monotonic_ns()
        if timestamp_ns <= 0 or timestamp_is_future or age_ns >= timeout_ns:
            raise BrokerUnavailableError(
                f"共享视频源连续 {self.manifest.frame_timeout_seconds:g} 秒没有新帧"
            )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class CaptureBroker:
    """Capture-card owner and shared-frame producer.

    ``start(wait=True)`` is the convenient UI-facing API: it returns ``True``
    after the first frame is committed, or ``False`` after the five-second
    startup deadline.  ``serve_forever`` is suitable as a subprocess entry
    point and keeps the source alive until an explicit stop/failure.
    """

    def __init__(
        self,
        device_index: int,
        capture_api: int = DEFAULT_CAPTURE_API,
        *,
        manifest_path: str | os.PathLike[str] | None = None,
        capture_factory: CaptureFactory | None = None,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        fps: float = DEFAULT_FPS,
        fourcc: str = DEFAULT_FOURCC,
        slot_count: int = DEFAULT_SLOT_COUNT,
        first_frame_timeout: float = DEFAULT_FIRST_FRAME_TIMEOUT,
        frame_timeout: float = DEFAULT_FRAME_TIMEOUT,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        session_id: str | None = None,
        parent_pid: int = 0,
        parent_poll_interval: float = DEFAULT_PARENT_POLL_INTERVAL,
        parent_shutdown_timeout: float = DEFAULT_PARENT_SHUTDOWN_TIMEOUT,
    ) -> None:
        self.device_index = int(device_index)
        self.capture_api = int(capture_api)
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps)
        self.fourcc = str(fourcc)
        self.slot_count = int(slot_count)
        self.first_frame_timeout = float(first_frame_timeout)
        self.frame_timeout = float(frame_timeout)
        self.poll_interval = max(0.0005, float(poll_interval))
        self.parent_pid = max(0, int(parent_pid))
        self.parent_poll_interval = max(0.025, float(parent_poll_interval))
        self.parent_shutdown_timeout = max(0.0, float(parent_shutdown_timeout))
        self.manifest_path = Path(manifest_path) if manifest_path is not None else default_manifest_path()
        self._lifecycle_mutex = _BrokerLifetimeMutex(self.manifest_path)
        self.capture_factory = capture_factory or (lambda index, api: OpenCVCapture(index, api))
        self.session_id = session_id or uuid.uuid4().hex
        self.control_path = _stop_command_path(self.manifest_path)
        self._ring: FrameRing | None = None
        self._capture: CaptureDevice | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._first_frame_event = threading.Event()
        self._done_event = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._state = BrokerState.STOPPED
        self._failure: BaseException | None = None
        self._manifest: BrokerManifest | None = None

    @property
    def state(self) -> BrokerState:
        with self._state_lock:
            return self._state

    @property
    def failure(self) -> BaseException | None:
        return self._failure

    @property
    def manifest(self) -> BrokerManifest | None:
        return self._manifest

    @property
    def ring(self) -> FrameRing | None:
        return self._ring

    def _set_state(self, state: BrokerState, *, failure: BaseException | None = None) -> bool:
        with self._state_lock:
            current = self._state
            if state is BrokerState.RUNNING and (
                current is not BrokerState.STARTING or self._stop_event.is_set()
            ):
                return False
            if state is BrokerState.FAILED and current not in (
                BrokerState.STARTING,
                BrokerState.RUNNING,
                BrokerState.FAILED,
            ):
                return False
            self._state = state
            if failure is not None:
                self._failure = failure
            if self._ring is not None:
                try:
                    self._ring.set_state(state)
                except Exception:
                    pass
            self._write_manifest(
                state,
                strict=state in (BrokerState.RUNNING, BrokerState.FAILED),
            )
            return True

    def _build_manifest(self, state: BrokerState) -> BrokerManifest:
        assert self._ring is not None
        return BrokerManifest(
            schema_version=PROTOCOL_VERSION,
            protocol=PROTOCOL_NAME,
            session_id=self.session_id,
            pid=os.getpid(),
            parent_pid=self.parent_pid,
            state=state,
            mapping_name=self._ring._shm.name,
            manifest_path=str(self.manifest_path),
            control_path=str(self.control_path),
            header_size=GLOBAL_HEADER_SIZE,
            slot_header_size=SLOT_HEADER_SIZE,
            slot_count=self.slot_count,
            slot_size=self._ring.slot_size,
            width=self.width,
            height=self.height,
            stride=self._ring.stride,
            pixel_format=PixelFormat.BGR24.value,
            capture={
                "device_index": self.device_index,
                "api": self.capture_api,
                "fourcc": self.fourcc,
                "fps": self.fps,
            },
            first_frame_timeout_seconds=self.first_frame_timeout,
            frame_timeout_seconds=self.frame_timeout,
            updated_at_ns=time.monotonic_ns(),
            failure_message=str(self._failure or ""),
        )

    def _write_manifest(self, state: BrokerState, *, strict: bool = False) -> None:
        if self._ring is None:
            return
        manifest = self._build_manifest(state)
        last_error: OSError | None = None
        for attempt in range(_MANIFEST_WRITE_ATTEMPTS):
            try:
                manifest.write(self.manifest_path)
            except OSError as exc:
                last_error = exc
                if attempt + 1 < _MANIFEST_WRITE_ATTEMPTS:
                    time.sleep(_MANIFEST_WRITE_RETRY_INTERVAL)
                continue
            self._manifest = manifest
            return
        # Preserve the intended state for local diagnostics even when Windows
        # temporarily or permanently prevents replacing the discovery file.
        self._manifest = manifest
        if strict and last_error is not None:
            raise last_error

    def _check_existing_manifest(self) -> None:
        try:
            existing = BrokerManifest.load(self.manifest_path)
        except BrokerError:
            return
        if existing.state in (BrokerState.STARTING, BrokerState.RUNNING) and _pid_is_alive(existing.pid):
            raise BrokerAlreadyRunningError(
                f"已有共享视频源正在运行 (pid={existing.pid}, session={existing.session_id})"
            )
        # Stale control requests must not stop the new session.
        try:
            Path(existing.control_path or self.control_path).unlink()
        except (FileNotFoundError, OSError):
            pass

    def start(self, *, wait: bool = True, timeout: float | None = None) -> bool:
        with self._lifecycle_lock:
            with self._state_lock:
                if self._thread is not None and self._thread.is_alive():
                    raise BrokerError("共享视频源已经启动")
                if self._ring is not None:
                    raise BrokerError("重新启动共享视频源前必须先调用 stop()")
                self._lifecycle_mutex.acquire()
                try:
                    self._check_existing_manifest()
                    self._stop_event.clear()
                    self._first_frame_event.clear()
                    self._done_event.clear()
                    self._failure = None
                    self._ring = FrameRing.create(
                        width=self.width,
                        height=self.height,
                        slot_count=self.slot_count,
                        state=BrokerState.STARTING,
                    )
                    self._state = BrokerState.STARTING
                    self._write_manifest(BrokerState.STARTING, strict=True)
                    self._thread = threading.Thread(
                        target=self._run,
                        name="CaptureBroker",
                        daemon=True,
                    )
                    self._thread.start()
                except BaseException:
                    ring, self._ring = self._ring, None
                    try:
                        if ring is not None:
                            ring.close(unlink=True)
                        self._thread = None
                        self._state = BrokerState.STOPPED
                        try:
                            current = BrokerManifest.load(self.manifest_path)
                            if current.session_id == self.session_id:
                                self.manifest_path.unlink()
                        except (BrokerError, OSError):
                            pass
                    finally:
                        self._lifecycle_mutex.release()
                    raise
        if not wait:
            return True
        wait_timeout = self.first_frame_timeout if timeout is None else max(0.0, timeout)
        if not self._first_frame_event.wait(wait_timeout):
            # A RUNNING manifest publish can briefly outlive the event wait
            # while Windows retries an atomic replace. Recheck under the same
            # state lock before committing the timeout transition.
            with self._state_lock:
                if self._state is BrokerState.RUNNING:
                    return True
                if self._state is not BrokerState.STARTING:
                    return False
                self._fail(CaptureOpenError("共享视频源首帧等待超时"))
                return False
        return self.state is BrokerState.RUNNING

    def start_async(self) -> None:
        self.start(wait=False)

    def _open_capture(self) -> CaptureDevice:
        capture = self.capture_factory(self.device_index, self.capture_api)
        try:
            opened = capture.open(self.device_index, self.capture_api)
            if not opened:
                raise CaptureOpenError("采集卡打开失败，设备可能正被其他程序占用，或当前设备/API不可用")
            capture.set_properties(self.width, self.height, self.fourcc, self.fps)
            return capture
        except Exception:
            try:
                capture.release()
            except Exception:
                pass
            raise

    def _poll_stop_command(self) -> bool:
        try:
            raw = json.loads(self.control_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return False
        if not isinstance(raw, Mapping):
            return False
        if raw.get("session_id") != self.session_id or raw.get("command") != "stop":
            return False
        try:
            self.control_path.unlink()
        except OSError:
            pass
        return True

    def _run(self) -> None:
        first_deadline = time.monotonic() + self.first_frame_timeout
        last_frame_time: float | None = None
        try:
            self._capture = self._open_capture()
            while not self._stop_event.is_set():
                if self._poll_stop_command():
                    self._stop_event.set()
                    break
                try:
                    ok, frame = self._capture.read()
                except BaseException as exc:
                    if self._stop_event.is_set():
                        break
                    self._fail(exc)
                    return
                if self._stop_event.is_set():
                    break
                now = time.monotonic()
                if not self._first_frame_event.is_set() and now >= first_deadline:
                    self._fail(CaptureOpenError("共享视频源首帧等待超时"))
                    return
                if last_frame_time is not None and now - last_frame_time >= self.frame_timeout:
                    self._fail(
                        BrokerUnavailableError(
                            f"共享视频源连续无新帧超过 {self.frame_timeout:g} 秒"
                        )
                    )
                    return
                if ok and frame is not None:
                    try:
                        assert self._ring is not None
                        self._ring.write(frame)
                    except BaseException as exc:
                        self._fail(exc)
                        return
                    last_frame_time = now
                    if not self._first_frame_event.is_set():
                        if not self._set_state(BrokerState.RUNNING):
                            break
                        self._first_frame_event.set()
                    else:
                        self._ring.heartbeat()
                else:
                    time.sleep(self.poll_interval)
        except BaseException as exc:
            self._fail(exc)
            return
        finally:
            capture, self._capture = self._capture, None
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    pass
            if self._stop_event.is_set() and self.state is not BrokerState.FAILED:
                self._set_state(BrokerState.STOPPED)
            self._first_frame_event.set()
            self._done_event.set()

    def _fail(self, error: BaseException) -> None:
        self._stop_event.set()
        try:
            self._set_state(BrokerState.FAILED, failure=error)
        except OSError:
            # State and ring were already changed before manifest publication.
            # The child now exits promptly so the controller cannot mistake a
            # stale STARTING file for a live capture indefinitely.
            pass
        finally:
            self._first_frame_event.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done_event.wait(timeout)

    def stop(
        self,
        *,
        timeout: float = 2.0,
        unlink: bool = True,
        remove_manifest: bool = True,
    ) -> bool:
        with self._lifecycle_lock:
            self._stop_event.set()
            thread = self._thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(max(0.0, timeout))
                if thread.is_alive():
                    # Do not close the mapping underneath an in-flight writer.
                    # A subprocess owner may escalate to process termination after
                    # this bounded cooperative stop reports failure.
                    return False
            try:
                if self.state is not BrokerState.FAILED:
                    self._set_state(BrokerState.STOPPED)
                ring, self._ring = self._ring, None
                if ring is not None:
                    ring.close(unlink=unlink)
                self._thread = None
                try:
                    self.control_path.unlink()
                except OSError:
                    pass
                if remove_manifest:
                    try:
                        # Do not remove a newer session that reused the same manifest path.
                        current = BrokerManifest.load(self.manifest_path)
                        if current.session_id == self.session_id:
                            self.manifest_path.unlink()
                    except (BrokerError, OSError):
                        pass
                return True
            finally:
                self._lifecycle_mutex.release()

    def serve_forever(self) -> bool:
        """Start synchronously and keep serving until stop/failure."""

        parent_guard = _ParentProcessGuard(self.parent_pid)
        try:
            if not self.start(wait=True):
                # Keep the small JSON failure record long enough for the GUI
                # controller to report the real device error. The controller
                # removes it after observing this child exit.
                self.stop(remove_manifest=False)
                return False

            parent_lost = False
            while not self.wait(self.parent_poll_interval):
                if parent_guard.status() is _ProcessStatus.DEAD:
                    parent_lost = True
                    break

            if parent_lost:
                # A blocked OpenCV read must not keep an orphan process alive.
                # The capture thread is daemonized, so returning from this
                # standalone child lets the OS close the device handle even if
                # cooperative cleanup cannot finish within the grace period.
                self.stop(timeout=self.parent_shutdown_timeout)
                return True

            succeeded = self.state is BrokerState.STOPPED
            self.stop(remove_manifest=self.state is not BrokerState.FAILED)
            return succeeded
        finally:
            parent_guard.close()

    def __enter__(self) -> Self:
        if not self.start(wait=True):
            raise CaptureOpenError("共享视频源启动失败")
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()


class FakeCapture:
    """Deterministic capture adapter for tests and protocol smoke checks.

    ``frames`` can contain arrays or ``None`` (a failed read).  With
    ``repeat=True`` the last frame repeats forever; otherwise exhausted input
    produces failed reads.  ``read_delay`` models a slow/paused driver.
    """

    def __init__(
        self,
        frames: Iterable[np.ndarray | None] = (),
        *,
        open_result: bool = True,
        repeat: bool = False,
        read_delay: float = 0.0,
    ) -> None:
        self.frames = list(frames)
        self.open_result = bool(open_result)
        self.repeat = bool(repeat)
        self.read_delay = max(0.0, float(read_delay))
        self.opened = False
        self.released = False
        self.open_calls = 0
        self.read_calls = 0
        self.properties: tuple[int, int, str, float] | None = None
        self._index = 0

    def open(self, device_index: int, capture_api: int) -> bool:
        self.open_calls += 1
        self.opened = self.open_result
        return self.opened

    def set_properties(self, width: int, height: int, fourcc: str, fps: float) -> None:
        self.properties = (int(width), int(height), str(fourcc), float(fps))

    def read(self) -> tuple[bool, np.ndarray | None]:
        self.read_calls += 1
        if self.read_delay:
            time.sleep(self.read_delay)
        if not self.opened or not self.frames:
            return False, None
        if self._index >= len(self.frames):
            if not self.repeat:
                return False, None
            self._index = len(self.frames) - 1
        frame = self.frames[self._index]
        if self._index < len(self.frames) - 1 or not self.repeat:
            self._index += 1
        return (frame is not None), frame

    def release(self) -> None:
        self.released = True
        self.opened = False


__all__ = [
    "BROKER_ALREADY_RUNNING_EXIT_CODE",
    "BrokerAlreadyRunningError",
    "BrokerError",
    "BrokerManifest",
    "BrokerManifestError",
    "BrokerNotFoundError",
    "BrokerState",
    "BrokerUnavailableError",
    "CaptureBroker",
    "CaptureBrokerClient",
    "CaptureDevice",
    "CaptureFactory",
    "CaptureOpenError",
    "CAPTURE_API_DIRECTSHOW",
    "CAPTURE_API_MSMF",
    "DEFAULT_CAPTURE_API",
    "DEFAULT_FIRST_FRAME_TIMEOUT",
    "DEFAULT_FOURCC",
    "DEFAULT_FPS",
    "DEFAULT_FRAME_TIMEOUT",
    "DEFAULT_HEIGHT",
    "DEFAULT_POLL_INTERVAL",
    "DEFAULT_SLOT_COUNT",
    "DEFAULT_WIDTH",
    "FakeCapture",
    "FrameFormatError",
    "FramePacket",
    "FrameRing",
    "GLOBAL_HEADER_SIZE",
    "GLOBAL_HEADER_STRUCT",
    "LEGACY_MANIFEST_ENVIRONMENT_VARIABLE",
    "MANIFEST_ENVIRONMENT_VARIABLE",
    "PIXEL_FORMAT_BGR24",
    "PROTOCOL_MAGIC",
    "PROTOCOL_NAME",
    "PROTOCOL_VERSION",
    "SLOT_HEADER_SIZE",
    "SLOT_HEADER_STRUCT",
    "default_manifest_path",
    "discover_manifest",
]
