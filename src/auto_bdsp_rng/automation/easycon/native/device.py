"""Native EasyCon serial and Nintendo Switch gamepad layer.

This module mirrors the parts of ``EasyCon.Device`` used by the script
evaluator while keeping hardware behind an injectable transport.  Real serial
connections use pyserial and the original EasyCon handshake; tests and mock
runs use :class:`MemoryTransport` without touching a COM port.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum, IntFlag, StrEnum
from typing import Any, Callable, Iterable, Protocol, Self


READY = 0xA5
COMMAND_HELLO = 0x81
COMMAND_CHANGE_AMIIBO_INDEX = 0x91
REPLY_HELLO = 0x80
REPLY_ACK = 0xFF

DEFAULT_BAUDRATES = (115200, 9600)
DEFAULT_CONNECT_TIMEOUT = 1.0
DEFAULT_REPORT_INTERVAL = 0.030
DEFAULT_WRITE_TIMEOUT = 0.5
STICK_MIN = 0
STICK_CENTER = 128
STICK_MAX = 255


class DeviceError(RuntimeError):
    """Base class for native EasyCon device failures."""


class PySerialUnavailableError(DeviceError):
    """pyserial is not installed in a build that needs a real COM port."""


class DeviceConnectionError(DeviceError):
    """The transport failed to connect or stopped unexpectedly."""


class DeviceNotConnectedError(DeviceError):
    """An operation requires a connected controller."""


class DeviceCancelledError(DeviceError):
    """A cancellable controller action was interrupted."""


class TransportStatus(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class SwitchButton(IntFlag):
    Y = 0x01
    B = 0x02
    A = 0x04
    X = 0x08
    L = 0x10
    R = 0x20
    ZL = 0x40
    ZR = 0x80
    MINUS = 0x100
    PLUS = 0x200
    LCLICK = 0x400
    RCLICK = 0x800
    HOME = 0x1000
    CAPTURE = 0x2000


class SwitchHat(IntEnum):
    TOP = 0x00
    TOP_RIGHT = 0x01
    RIGHT = 0x02
    BOTTOM_RIGHT = 0x03
    BOTTOM = 0x04
    BOTTOM_LEFT = 0x05
    LEFT = 0x06
    TOP_LEFT = 0x07
    CENTER = 0x08


class DirectionKey(IntFlag):
    NONE = 0x0
    UP = 0x1
    DOWN = 0x2
    LEFT = 0x4
    RIGHT = 0x8


class GamePadKey(IntEnum):
    """Numeric values from EasyCon.Script ``GamePadKey``."""

    NONE = 0
    Y = 1
    B = 2
    A = 3
    X = 4
    L = 5
    R = 6
    ZL = 7
    ZR = 8
    MINUS = 9
    PLUS = 10
    LCLICK = 11
    RCLICK = 12
    HOME = 13
    CAPTURE = 14
    TOP = 16
    TOP_RIGHT = 17
    RIGHT = 18
    DOWN_RIGHT = 19
    DOWN = 20
    DOWN_LEFT = 21
    LEFT = 22
    TOP_LEFT = 23
    LS = 32
    RS = 33


_BUTTON_FOR_KEY: dict[GamePadKey, SwitchButton] = {
    GamePadKey.Y: SwitchButton.Y,
    GamePadKey.B: SwitchButton.B,
    GamePadKey.A: SwitchButton.A,
    GamePadKey.X: SwitchButton.X,
    GamePadKey.L: SwitchButton.L,
    GamePadKey.R: SwitchButton.R,
    GamePadKey.ZL: SwitchButton.ZL,
    GamePadKey.ZR: SwitchButton.ZR,
    GamePadKey.MINUS: SwitchButton.MINUS,
    GamePadKey.PLUS: SwitchButton.PLUS,
    GamePadKey.LCLICK: SwitchButton.LCLICK,
    GamePadKey.RCLICK: SwitchButton.RCLICK,
    GamePadKey.HOME: SwitchButton.HOME,
    GamePadKey.CAPTURE: SwitchButton.CAPTURE,
}

_DIRECTION_FOR_KEY: dict[GamePadKey, DirectionKey] = {
    GamePadKey.TOP: DirectionKey.UP,
    GamePadKey.TOP_RIGHT: DirectionKey.UP | DirectionKey.RIGHT,
    GamePadKey.RIGHT: DirectionKey.RIGHT,
    GamePadKey.DOWN_RIGHT: DirectionKey.DOWN | DirectionKey.RIGHT,
    GamePadKey.DOWN: DirectionKey.DOWN,
    GamePadKey.DOWN_LEFT: DirectionKey.DOWN | DirectionKey.LEFT,
    GamePadKey.LEFT: DirectionKey.LEFT,
    GamePadKey.TOP_LEFT: DirectionKey.UP | DirectionKey.LEFT,
}

_KEY_ALIASES = {
    "UP": "TOP",
    "UPRIGHT": "TOP_RIGHT",
    "UP_RIGHT": "TOP_RIGHT",
    "DOWNRIGHT": "DOWN_RIGHT",
    "DOWNLEFT": "DOWN_LEFT",
    "UPLEFT": "TOP_LEFT",
    "UP_LEFT": "TOP_LEFT",
    "-": "MINUS",
    "+": "PLUS",
}


def coerce_gamepad_key(value: GamePadKey | str | int) -> GamePadKey:
    if isinstance(value, GamePadKey):
        return value
    if isinstance(value, str):
        name = value.strip().upper().replace("-", "_").replace(" ", "_")
        name = _KEY_ALIASES.get(name, name)
        try:
            return GamePadKey[name]
        except KeyError as exc:
            raise ValueError(f"未知伊机控按键: {value!r}") from exc
    try:
        return GamePadKey(int(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"未知伊机控按键: {value!r}") from exc


def _clamp_byte(value: int) -> int:
    return max(STICK_MIN, min(STICK_MAX, int(value)))


def _hat_from_directions(value: DirectionKey) -> SwitchHat:
    up = bool(value & DirectionKey.UP) and not bool(value & DirectionKey.DOWN)
    down = bool(value & DirectionKey.DOWN) and not bool(value & DirectionKey.UP)
    left = bool(value & DirectionKey.LEFT) and not bool(value & DirectionKey.RIGHT)
    right = bool(value & DirectionKey.RIGHT) and not bool(value & DirectionKey.LEFT)
    return {
        (True, False, False, False): SwitchHat.TOP,
        (True, False, False, True): SwitchHat.TOP_RIGHT,
        (False, False, False, True): SwitchHat.RIGHT,
        (False, True, False, True): SwitchHat.BOTTOM_RIGHT,
        (False, True, False, False): SwitchHat.BOTTOM,
        (False, True, True, False): SwitchHat.BOTTOM_LEFT,
        (False, False, True, False): SwitchHat.LEFT,
        (True, False, True, False): SwitchHat.TOP_LEFT,
    }.get((up, down, left, right), SwitchHat.CENTER)


@dataclass(slots=True)
class SwitchReport:
    button: int = 0
    hat: int = int(SwitchHat.CENTER)
    lx: int = STICK_CENTER
    ly: int = STICK_CENTER
    rx: int = STICK_CENTER
    ry: int = STICK_CENTER

    def __post_init__(self) -> None:
        self.button = int(self.button) & 0xFFFF
        self.hat = int(self.hat) & 0xFF
        self.lx = _clamp_byte(self.lx)
        self.ly = _clamp_byte(self.ly)
        self.rx = _clamp_byte(self.rx)
        self.ry = _clamp_byte(self.ry)

    def reset(self) -> None:
        self.button = 0
        self.hat = int(SwitchHat.CENTER)
        self.lx = self.ly = self.rx = self.ry = STICK_CENTER

    def copy(self) -> "SwitchReport":
        return SwitchReport(self.button, self.hat, self.lx, self.ly, self.rx, self.ry)

    clone = copy

    def serialized_state(self) -> bytes:
        return self.button.to_bytes(2, byteorder="big", signed=False) + bytes(
            (self.hat, self.lx, self.ly, self.rx, self.ry)
        )

    def to_bytes(self) -> bytes:
        """Return EasyCon's 7-bit-clean report packet.

        The state is seven bytes.  Bytes are treated as one big-endian bit
        stream, emitted in eight 7-bit groups, and the final group receives
        the high-bit end marker.
        """

        packet: list[int] = []
        accumulator = 0
        bits = 0
        for value in self.serialized_state():
            accumulator = (accumulator << 8) | value
            bits += 8
            while bits >= 7:
                bits -= 7
                packet.append((accumulator >> bits) & 0x7F)
                accumulator &= (1 << bits) - 1
        if not packet:  # pragma: no cover - serialized_state is always non-empty
            raise DeviceError("无法生成 Switch report")
        packet[-1] |= 0x80
        return bytes(packet)

    get_bytes = to_bytes


@dataclass(frozen=True, slots=True)
class ECKey:
    name: str
    key_code: int
    down_action: Callable[[SwitchReport], None] = field(repr=False, compare=False)
    up_action: Callable[[SwitchReport], None] = field(repr=False, compare=False)

    def down(self, report: SwitchReport) -> None:
        self.down_action(report)

    def up(self, report: SwitchReport) -> None:
        self.up_action(report)


class ECKeyUtil:
    HAT_MASK = 0x10

    @staticmethod
    def button(button: SwitchButton) -> ECKey:
        numeric = int(button)
        if numeric <= 0 or numeric & (numeric - 1):
            raise ValueError("SwitchButton 必须是单个按键位")
        key_code = numeric.bit_length() - 1
        return ECKey(
            button.name,
            key_code,
            lambda report: setattr(report, "button", report.button | numeric),
            lambda report: setattr(report, "button", report.button & ~numeric & 0xFFFF),
        )

    @staticmethod
    def hat(hat: SwitchHat) -> ECKey:
        return ECKey(
            f"HAT.{hat.name}",
            int(hat) | ECKeyUtil.HAT_MASK,
            lambda report: setattr(report, "hat", int(hat)),
            lambda report: setattr(report, "hat", int(SwitchHat.CENTER)),
        )

    @staticmethod
    def left_stick(x: int, y: int) -> ECKey:
        actual_x, actual_y = _clamp_byte(x), _clamp_byte(y)
        return ECKey(
            f"LStick({actual_x},{actual_y})",
            int(GamePadKey.LS),
            lambda report: (setattr(report, "lx", actual_x), setattr(report, "ly", actual_y)),
            lambda report: (setattr(report, "lx", STICK_CENTER), setattr(report, "ly", STICK_CENTER)),
        )

    @staticmethod
    def right_stick(x: int, y: int) -> ECKey:
        actual_x, actual_y = _clamp_byte(x), _clamp_byte(y)
        return ECKey(
            f"RStick({actual_x},{actual_y})",
            int(GamePadKey.RS),
            lambda report: (setattr(report, "rx", actual_x), setattr(report, "ry", actual_y)),
            lambda report: (setattr(report, "rx", STICK_CENTER), setattr(report, "ry", STICK_CENTER)),
        )

    l_stick = left_stick
    r_stick = right_stick


def to_ec_key(key: GamePadKey | str | int, x: int = 0, y: int = 0) -> ECKey:
    actual = coerce_gamepad_key(key)
    if actual in _BUTTON_FOR_KEY:
        return ECKeyUtil.button(_BUTTON_FOR_KEY[actual])
    if actual in _DIRECTION_FOR_KEY:
        return ECKeyUtil.hat(_hat_from_directions(_DIRECTION_FOR_KEY[actual]))
    if actual is GamePadKey.LS:
        return ECKeyUtil.left_stick(x, y)
    if actual is GamePadKey.RS:
        return ECKeyUtil.right_stick(x, y)
    raise ValueError(f"按键不能转换为 ECKey: {actual.name}")


BytePredicate = Callable[[int], bool]


class DeviceTransport(Protocol):
    port: str
    baudrate: int

    @property
    def is_connected(self) -> bool: ...

    def open(self, timeout: float = DEFAULT_CONNECT_TIMEOUT) -> bool: ...

    def write(
        self,
        data: bytes | bytearray | memoryview,
        *,
        wait: bool = False,
        timeout: float = DEFAULT_WRITE_TIMEOUT,
    ) -> bool: ...

    def send_and_wait(self, data: bytes, predicate: BytePredicate, timeout: float) -> bool: ...

    def close(self, timeout: float = 1.0) -> bool: ...


class SerialPortLike(Protocol):
    is_open: bool

    @property
    def in_waiting(self) -> int: ...

    def open(self) -> None: ...

    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int | None: ...

    def close(self) -> None: ...

    def reset_input_buffer(self) -> None: ...

    def reset_output_buffer(self) -> None: ...


SerialFactory = Callable[[str, int], SerialPortLike]


def _default_serial_factory(port: str, baudrate: int) -> SerialPortLike:
    try:
        import serial  # type: ignore
    except ImportError as exc:
        raise PySerialUnavailableError("原生伊机控串口需要安装 pyserial") from exc
    return serial.Serial(
        port=port,
        baudrate=baudrate,
        timeout=0,
        write_timeout=DEFAULT_WRITE_TIMEOUT,
    )


@dataclass(slots=True)
class _WriteRequest:
    data: bytes
    done: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


@dataclass(slots=True)
class _ReplyWaiter:
    predicate: BytePredicate
    done: threading.Event = field(default_factory=threading.Event)
    matched: bool = False


class TTLSerialTransport:
    """pyserial transport matching EasyCon's asynchronous TTL client."""

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        *,
        serial_factory: SerialFactory | None = None,
        poll_interval: float = 0.001,
        on_bytes_sent: Callable[[bytes], None] | None = None,
        on_bytes_received: Callable[[bytes], None] | None = None,
    ) -> None:
        self.port = str(port)
        self.baudrate = int(baudrate)
        self.serial_factory = serial_factory or _default_serial_factory
        self.poll_interval = max(0.0005, float(poll_interval))
        self.on_bytes_sent = on_bytes_sent
        self.on_bytes_received = on_bytes_received
        self.status = TransportStatus.DISCONNECTED
        self.failure: BaseException | None = None
        self._serial: SerialPortLike | None = None
        self._outgoing: queue.Queue[_WriteRequest] = queue.Queue()
        self._waiters: list[_ReplyWaiter] = []
        self._waiter_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_connected(self) -> bool:
        return self.status is TransportStatus.CONNECTED and self._thread is not None and self._thread.is_alive()

    def open(self, timeout: float = DEFAULT_CONNECT_TIMEOUT) -> bool:
        if self.is_connected:
            return True
        self.close()
        self.status = TransportStatus.CONNECTING
        self.failure = None
        self._stop_event.clear()
        try:
            serial_port = self.serial_factory(self.port, self.baudrate)
            if not bool(getattr(serial_port, "is_open", False)):
                serial_port.open()
            serial_port.reset_input_buffer()
            serial_port.reset_output_buffer()
            self._serial = serial_port
            self._thread = threading.Thread(
                target=self._io_loop,
                name=f"EasyConSerial-{self.port}-{self.baudrate}",
                daemon=True,
            )
            self._thread.start()
            if not self.send_and_wait(
                bytes((READY, READY, COMMAND_HELLO)),
                lambda value: value == REPLY_HELLO,
                timeout=max(0.0, timeout),
            ):
                self.status = TransportStatus.ERROR
                self.close()
                return False
            self.status = TransportStatus.CONNECTED
            return True
        except BaseException as exc:
            self.failure = exc
            self.status = TransportStatus.ERROR
            self.close()
            return False

    connect = open

    def _emit(self, callback: Callable[[bytes], None] | None, data: bytes) -> None:
        if callback is None:
            return
        try:
            callback(data)
        except Exception:
            pass

    def _dispatch_received(self, data: bytes) -> None:
        self._emit(self.on_bytes_received, data)
        with self._waiter_lock:
            waiters = tuple(self._waiters)
        for waiter in waiters:
            for value in data:
                try:
                    matched = bool(waiter.predicate(value))
                except Exception:
                    matched = False
                if matched:
                    waiter.matched = True
                    waiter.done.set()
                    break

    def _io_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                serial_port = self._serial
                if serial_port is None:
                    return
                while True:
                    try:
                        request = self._outgoing.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        written = serial_port.write(request.data)
                        if written is not None and written != len(request.data):
                            raise OSError(f"串口只写入 {written}/{len(request.data)} bytes")
                        self._emit(self.on_bytes_sent, request.data)
                    except BaseException as exc:
                        request.error = exc
                        raise
                    finally:
                        request.done.set()
                        self._outgoing.task_done()
                waiting = int(getattr(serial_port, "in_waiting", 0) or 0)
                if waiting > 0:
                    received = bytes(serial_port.read(min(waiting, 2550)))
                    if received:
                        self._dispatch_received(received)
                self._stop_event.wait(self.poll_interval)
        except BaseException as exc:
            self.failure = exc
            self.status = TransportStatus.ERROR
        finally:
            while True:
                try:
                    request = self._outgoing.get_nowait()
                except queue.Empty:
                    break
                request.error = self.failure or DeviceConnectionError("串口已停止")
                request.done.set()
                self._outgoing.task_done()
            with self._waiter_lock:
                for waiter in self._waiters:
                    waiter.done.set()

    def write(
        self,
        data: bytes | bytearray | memoryview,
        *,
        wait: bool = False,
        timeout: float = DEFAULT_WRITE_TIMEOUT,
    ) -> bool:
        raw = bytes(data)
        thread = self._thread
        if not raw:
            return True
        if self._serial is None or thread is None or not thread.is_alive() or self._stop_event.is_set():
            raise DeviceNotConnectedError("伊机控串口未连接")
        request = _WriteRequest(raw)
        self._outgoing.put(request)
        if not wait:
            return True
        if not request.done.wait(max(0.0, timeout)):
            raise TimeoutError("伊机控串口写入超时")
        if request.error is not None:
            raise DeviceConnectionError(f"伊机控串口写入失败: {request.error}") from request.error
        return True

    def send_and_wait(self, data: bytes, predicate: BytePredicate, timeout: float) -> bool:
        waiter = _ReplyWaiter(predicate)
        with self._waiter_lock:
            self._waiters.append(waiter)
        try:
            self.write(data, wait=True, timeout=min(DEFAULT_WRITE_TIMEOUT, max(0.001, timeout)))
            waiter.done.wait(max(0.0, timeout))
            return waiter.matched
        finally:
            with self._waiter_lock:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)

    def close(self, timeout: float = 1.0) -> bool:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))
            if thread.is_alive():
                return False
        serial_port, self._serial = self._serial, None
        self._thread = None
        if serial_port is not None:
            try:
                serial_port.close()
            except Exception:
                pass
        if self.status is not TransportStatus.ERROR:
            self.status = TransportStatus.DISCONNECTED
        return True

    disconnect = close

    def __enter__(self) -> Self:
        if not self.open():
            raise DeviceConnectionError(f"无法连接串口 {self.port}")
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class MemoryTransport:
    """In-memory transport for mock runs and deterministic tests."""

    def __init__(
        self,
        port: str = "mock",
        baudrate: int = 115200,
        *,
        handshake_success: bool = True,
        reply_factory: Callable[[bytes], bytes] | None = None,
        history_limit: int = 10_000,
    ) -> None:
        self.port = str(port)
        self.baudrate = int(baudrate)
        self.handshake_success = bool(handshake_success)
        self.reply_factory = reply_factory or self._default_reply
        self.status = TransportStatus.DISCONNECTED
        self.writes: deque[bytes] = deque(maxlen=max(1, int(history_limit)))
        self.closed = False
        self._lock = threading.Lock()

    @staticmethod
    def _default_reply(data: bytes) -> bytes:
        if data == bytes((READY, READY, COMMAND_HELLO)):
            return bytes((REPLY_HELLO,))
        if len(data) == 3 and data[0] == READY and data[-1] == COMMAND_CHANGE_AMIIBO_INDEX:
            return bytes((REPLY_ACK,))
        return b""

    @property
    def is_connected(self) -> bool:
        return self.status is TransportStatus.CONNECTED and not self.closed

    def open(self, timeout: float = DEFAULT_CONNECT_TIMEOUT) -> bool:
        del timeout
        self.closed = False
        self.status = TransportStatus.CONNECTING
        hello = bytes((READY, READY, COMMAND_HELLO))
        with self._lock:
            self.writes.append(hello)
        reply = self.reply_factory(hello) if self.handshake_success else b""
        if REPLY_HELLO not in reply:
            self.status = TransportStatus.ERROR
            return False
        self.status = TransportStatus.CONNECTED
        return True

    connect = open

    def write(
        self,
        data: bytes | bytearray | memoryview,
        *,
        wait: bool = False,
        timeout: float = DEFAULT_WRITE_TIMEOUT,
    ) -> bool:
        del wait, timeout
        if not self.is_connected:
            raise DeviceNotConnectedError("mock 伊机控未连接")
        with self._lock:
            self.writes.append(bytes(data))
        return True

    def send_and_wait(self, data: bytes, predicate: BytePredicate, timeout: float) -> bool:
        del timeout
        self.write(data, wait=True)
        return any(predicate(value) for value in self.reply_factory(bytes(data)))

    def close(self, timeout: float = 1.0) -> bool:
        del timeout
        self.closed = True
        if self.status is not TransportStatus.ERROR:
            self.status = TransportStatus.DISCONNECTED
        return True

    disconnect = close


TransportFactory = Callable[[str, int], DeviceTransport]


@dataclass(slots=True)
class _ReportRequest:
    report: SwitchReport
    done: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


def _cancelled(cancel_event: object | None) -> bool:
    if cancel_event is None:
        return False
    is_set = getattr(cancel_event, "is_set", None)
    if callable(is_set):
        return bool(is_set())
    if callable(cancel_event):
        return bool(cancel_event())
    return False


def _wait_cancelable(duration_ms: int, cancel_event: object | None) -> None:
    duration = max(0.0, int(duration_ms) / 1000.0)
    if _cancelled(cancel_event):
        raise DeviceCancelledError("伊机控操作已停止")
    waiter = getattr(cancel_event, "wait", None)
    if callable(waiter):
        if bool(waiter(duration)):
            raise DeviceCancelledError("伊机控操作已停止")
        return
    deadline = time.monotonic() + duration
    while True:
        if _cancelled(cancel_event):
            raise DeviceCancelledError("伊机控操作已停止")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.005, remaining))


class NintendoSwitchDevice:
    """EasyCon-compatible controller with one serialized report loop."""

    def __init__(
        self,
        *,
        transport_factory: TransportFactory | None = None,
        baudrates: Iterable[int] = DEFAULT_BAUDRATES,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        report_interval: float = DEFAULT_REPORT_INTERVAL,
        report_timeout: float = DEFAULT_WRITE_TIMEOUT,
        fallback_delay: float = 0.1,
        action_sink: Callable[[str], None] | None = None,
        report_history_limit: int = 256,
    ) -> None:
        self.transport_factory = transport_factory or self._default_transport_factory
        self.baudrates = tuple(int(value) for value in baudrates)
        if not self.baudrates:
            raise ValueError("baudrates 不能为空")
        self.connect_timeout = max(0.001, float(connect_timeout))
        self.report_interval = max(0.0, float(report_interval))
        self.report_timeout = max(0.001, float(report_timeout))
        self.fallback_delay = max(0.0, float(fallback_delay))
        self.action_sink = action_sink
        self.port: str | None = None
        self.connected_baudrate: int | None = None
        self.transport: DeviceTransport | None = None
        self.failure: BaseException | None = None
        self._report = SwitchReport()
        self._directions = DirectionKey.NONE
        self._state_lock = threading.RLock()
        self._report_queue: queue.Queue[_ReportRequest] = queue.Queue()
        self._report_stop = threading.Event()
        self._report_thread: threading.Thread | None = None
        self.report_history: deque[SwitchReport] = deque(
            maxlen=max(1, int(report_history_limit))
        )

    @staticmethod
    def _default_transport_factory(port: str, baudrate: int) -> DeviceTransport:
        if port.strip().casefold() in {"mock", "memory", "none"}:
            return MemoryTransport(port, baudrate)
        return TTLSerialTransport(port, baudrate)

    @property
    def is_connected(self) -> bool:
        transport = self.transport
        thread = self._report_thread
        return (
            transport is not None
            and transport.is_connected
            and thread is not None
            and thread.is_alive()
            and not self._report_stop.is_set()
        )

    def _log(self, message: str) -> None:
        if self.action_sink is None:
            return
        try:
            self.action_sink(message)
        except Exception:
            pass

    def connect(self, port: str) -> bool:
        actual_port = str(port).strip()
        if not actual_port:
            return False
        if self.is_connected and self.port == actual_port:
            return True
        self.disconnect(release=False)
        self.failure = None
        for attempt, baudrate in enumerate(self.baudrates):
            transport: DeviceTransport | None = None
            try:
                transport = self.transport_factory(actual_port, baudrate)
                connected = transport.open(self.connect_timeout)
            except BaseException as exc:
                self.failure = exc
                connected = False
            if connected:
                assert transport is not None
                self.port = actual_port
                self.connected_baudrate = baudrate
                self.transport = transport
                self._report_stop.clear()
                self._report_thread = threading.Thread(
                    target=self._report_loop,
                    name="EasyConReportLoop",
                    daemon=True,
                )
                self._report_thread.start()
                self._log(f"Connected {actual_port} @ {baudrate}")
                return True
            if transport is not None:
                transport_failure = getattr(transport, "failure", None)
                if isinstance(transport_failure, BaseException):
                    self.failure = transport_failure
                try:
                    transport.close()
                except BaseException as exc:
                    self.failure = exc
            if attempt + 1 < len(self.baudrates) and self.fallback_delay:
                time.sleep(self.fallback_delay)
        self.port = None
        self.connected_baudrate = None
        self.transport = None
        return False

    def _report_loop(self) -> None:
        next_send_time = 0.0
        while not self._report_stop.is_set() or not self._report_queue.empty():
            try:
                request = self._report_queue.get(timeout=0.02)
            except queue.Empty:
                continue
            try:
                remaining = next_send_time - time.monotonic()
                if remaining > 0 and self._report_stop.wait(remaining):
                    # A queued neutral release is allowed to finish during
                    # shutdown, but ordinary pending actions are cancelled.
                    if request.report != SwitchReport():
                        raise DeviceCancelledError("伊机控报告循环已停止")
                transport = self.transport
                if transport is None or not transport.is_connected:
                    raise DeviceNotConnectedError("伊机控串口未连接")
                transport.write(request.report.to_bytes(), wait=True, timeout=self.report_timeout)
                self.report_history.append(request.report.copy())
                next_send_time = time.monotonic() + self.report_interval
            except BaseException as exc:
                request.error = exc
                self.failure = exc
            finally:
                request.done.set()
                self._report_queue.task_done()

    def _queue_report(self, report: SwitchReport, *, wait: bool = True) -> _ReportRequest:
        if not self.is_connected:
            raise DeviceNotConnectedError("伊机控设备未连接")
        request = _ReportRequest(report.copy())
        self._report_queue.put(request)
        if wait:
            timeout = max(self.report_timeout + self.report_interval + 0.1, 0.2)
            if not request.done.wait(timeout):
                raise TimeoutError("伊机控报告发送超时")
            if request.error is not None:
                if isinstance(request.error, DeviceError):
                    raise request.error
                raise DeviceConnectionError(f"伊机控报告发送失败: {request.error}") from request.error
        return request

    def get_report(self) -> SwitchReport:
        with self._state_lock:
            return self._report.copy()

    def apply_report(self, report: SwitchReport, *, wait: bool = True) -> None:
        with self._state_lock:
            self._report = report.copy()
            self._directions = DirectionKey.NONE
            snapshot = self._report.copy()
        self._queue_report(snapshot, wait=wait)

    def down(self, key: ECKey, *, wait: bool = True) -> None:
        with self._state_lock:
            key.down(self._report)
            snapshot = self._report.copy()
        self._log(f"Down {key.name}")
        self._queue_report(snapshot, wait=wait)

    def up(self, key: ECKey, *, wait: bool = True) -> None:
        with self._state_lock:
            key.up(self._report)
            snapshot = self._report.copy()
        self._log(f"Up {key.name}")
        self._queue_report(snapshot, wait=wait)

    def press_buttons(self, key: GamePadKey | str | int) -> None:
        actual = coerce_gamepad_key(key)
        with self._state_lock:
            if actual in _BUTTON_FOR_KEY:
                self._report.button |= int(_BUTTON_FOR_KEY[actual])
            elif actual in _DIRECTION_FOR_KEY:
                self._directions |= _DIRECTION_FOR_KEY[actual]
                self._report.hat = int(_hat_from_directions(self._directions))
            else:
                raise ValueError(f"不是可按下的按键: {actual.name}")
            snapshot = self._report.copy()
        self._log(f"Down {actual.name}")
        self._queue_report(snapshot)

    def release_buttons(self, key: GamePadKey | str | int) -> None:
        actual = coerce_gamepad_key(key)
        with self._state_lock:
            if actual in _BUTTON_FOR_KEY:
                self._report.button &= ~int(_BUTTON_FOR_KEY[actual]) & 0xFFFF
            elif actual in _DIRECTION_FOR_KEY:
                self._directions &= ~_DIRECTION_FOR_KEY[actual]
                self._report.hat = int(_hat_from_directions(self._directions))
            else:
                raise ValueError(f"不是可释放的按键: {actual.name}")
            snapshot = self._report.copy()
        self._log(f"Up {actual.name}")
        self._queue_report(snapshot)

    def click_buttons(
        self,
        key: GamePadKey | str | int,
        duration_ms: int,
        cancel_event: object | None = None,
    ) -> None:
        self.press_buttons(key)
        try:
            _wait_cancelable(duration_ms, cancel_event)
        finally:
            self.release_buttons(key)

    def set_stick(self, key: GamePadKey | str | int, x: int, y: int) -> None:
        actual = coerce_gamepad_key(key)
        actual_x, actual_y = _clamp_byte(x), _clamp_byte(y)
        with self._state_lock:
            if actual is GamePadKey.LS:
                self._report.lx, self._report.ly = actual_x, actual_y
            elif actual is GamePadKey.RS:
                self._report.rx, self._report.ry = actual_x, actual_y
            else:
                raise ValueError(f"不是摇杆按键: {actual.name}")
            snapshot = self._report.copy()
        self._log(f"Stick {actual.name} ({actual_x},{actual_y})")
        self._queue_report(snapshot)

    def click_stick(
        self,
        key: GamePadKey | str | int,
        x: int,
        y: int,
        duration_ms: int,
        cancel_event: object | None = None,
    ) -> None:
        actual = coerce_gamepad_key(key)
        self.set_stick(actual, x, y)
        try:
            _wait_cancelable(duration_ms, cancel_event)
        finally:
            self.set_stick(actual, STICK_CENTER, STICK_CENTER)

    def change_amiibo(self, index: int) -> bool:
        transport = self.transport
        if not self.is_connected or transport is None:
            raise DeviceNotConnectedError("伊机控设备未连接")
        value = int(index)
        if value < 0 or value > 15:
            raise ValueError("Amiibo 索引必须在 0..15")
        return transport.send_and_wait(
            bytes((READY, value & 0x0F, COMMAND_CHANGE_AMIIBO_INDEX)),
            lambda reply: reply == REPLY_ACK,
            timeout=0.2,
        )

    def reset(self, *, wait: bool = True) -> None:
        with self._state_lock:
            self._report.reset()
            self._directions = DirectionKey.NONE
            snapshot = self._report.copy()
        self._log("Reset")
        self._queue_report(snapshot, wait=wait)

    def disconnect(self, *, release: bool = True, timeout: float = 1.0) -> bool:
        transport = self.transport
        thread = self._report_thread
        released = True
        if release and self.is_connected:
            try:
                self.reset(wait=True)
            except Exception as exc:
                self.failure = exc
                released = False
        self._report_stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))
            if thread.is_alive():
                return False
        self._report_thread = None
        try:
            closed = True if transport is None else transport.close(timeout)
        except BaseException as exc:
            self.failure = exc
            closed = False
        self.transport = None
        self.port = None
        self.connected_baudrate = None
        with self._state_lock:
            self._report.reset()
            self._directions = DirectionKey.NONE
        while True:
            try:
                pending = self._report_queue.get_nowait()
            except queue.Empty:
                break
            pending.error = DeviceCancelledError("伊机控设备已断开")
            pending.done.set()
            self._report_queue.task_done()
        return released and closed

    close = disconnect

    # EasyCon.Script evaluator-compatible spellings.
    ClickButtons = click_buttons
    PressButtons = press_buttons
    ReleaseButtons = release_buttons
    ClickStick = click_stick
    SetStick = set_stick
    ChangeAmiibo = change_amiibo

    def __enter__(self) -> Self:
        if not self.is_connected:
            raise DeviceNotConnectedError("请先调用 connect()")
        return self

    def __exit__(self, *args: Any) -> None:
        self.disconnect()


class NativeGamePadAdapter:
    """Small evaluator-facing facade over :class:`NintendoSwitchDevice`."""

    def __init__(self, device: NintendoSwitchDevice) -> None:
        self.device = device

    @property
    def is_connected(self) -> bool:
        return self.device.is_connected

    def click_buttons(self, key: GamePadKey | str | int, duration_ms: int, cancel_event: object | None = None) -> None:
        self.device.click_buttons(key, duration_ms, cancel_event)

    def press_buttons(self, key: GamePadKey | str | int) -> None:
        self.device.press_buttons(key)

    def release_buttons(self, key: GamePadKey | str | int) -> None:
        self.device.release_buttons(key)

    def click_stick(
        self,
        key: GamePadKey | str | int,
        x: int,
        y: int,
        duration_ms: int,
        cancel_event: object | None = None,
    ) -> None:
        self.device.click_stick(key, x, y, duration_ms, cancel_event)

    def set_stick(self, key: GamePadKey | str | int, x: int, y: int) -> None:
        self.device.set_stick(key, x, y)

    def change_amiibo(self, index: int) -> bool:
        return self.device.change_amiibo(index)

    def reset(self) -> None:
        self.device.reset()

    def stop(self) -> bool:
        return self.device.disconnect(release=True)

    close = stop
    ClickButtons = click_buttons
    PressButtons = press_buttons
    ReleaseButtons = release_buttons
    ClickStick = click_stick
    SetStick = set_stick
    ChangeAmiibo = change_amiibo


class MockGamepad(NativeGamePadAdapter):
    """Connected no-hardware gamepad for panel mock mode and evaluator tests."""

    def __init__(self, *, action_sink: Callable[[str], None] | None = None) -> None:
        self.memory_transport = MemoryTransport()
        self.actions: deque[str] = deque(maxlen=10_000)

        def sink(message: str) -> None:
            self.actions.append(message)
            if action_sink is not None:
                action_sink(message)

        device = NintendoSwitchDevice(
            transport_factory=lambda _port, _baudrate: self.memory_transport,
            baudrates=(115200,),
            report_interval=0.0,
            fallback_delay=0.0,
            action_sink=sink,
        )
        if not device.connect("mock"):
            raise DeviceConnectionError("无法启动 mock 伊机控")
        super().__init__(device)

    @property
    def reports(self) -> tuple[SwitchReport, ...]:
        return tuple(report.copy() for report in self.device.report_history)


def list_ports(comports: Callable[[], Iterable[Any]] | None = None) -> list[str]:
    """List COM ports through pyserial without importing it for mock runs."""

    if comports is None:
        try:
            from serial.tools import list_ports as serial_list_ports  # type: ignore
        except ImportError as exc:
            raise PySerialUnavailableError("枚举伊机控串口需要安装 pyserial") from exc
        comports = serial_list_ports.comports
    ports: list[str] = []
    for item in comports():
        value = str(getattr(item, "device", item)).strip()
        if value and value not in ports:
            ports.append(value)
    return ports


__all__ = [
    "COMMAND_CHANGE_AMIIBO_INDEX",
    "COMMAND_HELLO",
    "DEFAULT_BAUDRATES",
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_REPORT_INTERVAL",
    "DeviceCancelledError",
    "DeviceConnectionError",
    "DeviceError",
    "DeviceNotConnectedError",
    "DeviceTransport",
    "DirectionKey",
    "ECKey",
    "ECKeyUtil",
    "GamePadKey",
    "MemoryTransport",
    "MockGamepad",
    "NativeGamePadAdapter",
    "NintendoSwitchDevice",
    "PySerialUnavailableError",
    "READY",
    "REPLY_ACK",
    "REPLY_HELLO",
    "STICK_CENTER",
    "STICK_MAX",
    "STICK_MIN",
    "SwitchButton",
    "SwitchHat",
    "SwitchReport",
    "TTLSerialTransport",
    "TransportStatus",
    "coerce_gamepad_key",
    "list_ports",
    "to_ec_key",
]
