from __future__ import annotations

import threading
from dataclasses import dataclass

import pytest

from auto_bdsp_rng.automation.easycon.native.device import (
    COMMAND_CHANGE_AMIIBO_INDEX,
    COMMAND_HELLO,
    READY,
    REPLY_ACK,
    REPLY_HELLO,
    DeviceCancelledError,
    ECKeyUtil,
    GamePadKey,
    MemoryTransport,
    MockGamepad,
    NintendoSwitchDevice,
    STICK_CENTER,
    SwitchButton,
    SwitchHat,
    SwitchReport,
    TTLSerialTransport,
    TransportStatus,
    list_ports,
)


def test_switch_report_matches_easycon_seven_bit_wire_format():
    assert SwitchReport().serialized_state() == bytes((0, 0, 8, 128, 128, 128, 128))
    assert SwitchReport().to_bytes() == bytes.fromhex("00 00 01 08 04 02 01 80")
    assert SwitchReport(button=SwitchButton.A).to_bytes() == bytes.fromhex(
        "00 01 01 08 04 02 01 80"
    )
    assert SwitchReport(button=0x2345, hat=2, lx=1, ly=2, rx=3, ry=4).to_bytes() == bytes.fromhex(
        "11 51 20 20 08 08 06 84"
    )


def test_ec_key_actions_mutate_and_release_report_state():
    report = SwitchReport()
    button = ECKeyUtil.button(SwitchButton.A)
    button.down(report)
    assert report.button == SwitchButton.A
    button.up(report)
    assert report.button == 0

    hat = ECKeyUtil.hat(SwitchHat.TOP_LEFT)
    hat.down(report)
    assert report.hat == SwitchHat.TOP_LEFT
    hat.up(report)
    assert report.hat == SwitchHat.CENTER

    stick = ECKeyUtil.left_stick(0, 255)
    stick.down(report)
    assert (report.lx, report.ly) == (0, 255)
    stick.up(report)
    assert (report.lx, report.ly) == (STICK_CENTER, STICK_CENTER)


def test_device_falls_back_from_115200_to_9600():
    transports: list[MemoryTransport] = []

    def factory(port: str, baudrate: int) -> MemoryTransport:
        transport = MemoryTransport(port, baudrate, handshake_success=baudrate == 9600)
        transports.append(transport)
        return transport

    device = NintendoSwitchDevice(
        transport_factory=factory,
        fallback_delay=0,
        report_interval=0,
    )
    try:
        assert device.connect("COM9")
        assert [item.baudrate for item in transports] == [115200, 9600]
        assert transports[0].status is TransportStatus.ERROR
        assert transports[0].closed
        assert device.connected_baudrate == 9600
        assert device.is_connected
    finally:
        device.disconnect()


def test_connect_mock_uses_memory_transport_without_serial_factory():
    device = NintendoSwitchDevice(
        baudrates=(115200,),
        report_interval=0,
        fallback_delay=0,
    )
    try:
        assert device.connect("mock")
        assert isinstance(device.transport, MemoryTransport)
        device.click_buttons("A", 0)
        assert device.get_report().button == 0
    finally:
        device.disconnect()


def test_report_loop_preserves_buttons_hat_stick_and_releases_on_disconnect():
    transport = MemoryTransport()
    device = NintendoSwitchDevice(
        transport_factory=lambda _port, _baudrate: transport,
        baudrates=(115200,),
        report_interval=0,
        fallback_delay=0,
    )
    assert device.connect("mock")

    device.press_buttons("A")
    assert device.get_report().button == SwitchButton.A
    device.press_buttons("UP")
    device.press_buttons("RIGHT")
    assert device.get_report().hat == SwitchHat.TOP_RIGHT
    device.release_buttons("UP")
    assert device.get_report().hat == SwitchHat.RIGHT
    device.set_stick(GamePadKey.LS, 0, 255)
    assert (device.get_report().lx, device.get_report().ly) == (0, 255)

    assert device.disconnect()
    assert transport.closed
    assert device.report_history[-1] == SwitchReport()
    assert transport.writes[-1] == SwitchReport().to_bytes()


def test_cancelled_click_always_queues_release():
    gamepad = MockGamepad()
    cancelled = threading.Event()
    cancelled.set()
    try:
        with pytest.raises(DeviceCancelledError, match="停止"):
            gamepad.click_buttons("B", 500, cancelled)
        assert gamepad.device.get_report().button == 0
        assert gamepad.reports[-2].button == SwitchButton.B
        assert gamepad.reports[-1].button == 0
    finally:
        gamepad.stop()


def test_amiibo_command_uses_original_easycon_command_and_ack():
    gamepad = MockGamepad()
    try:
        assert gamepad.change_amiibo(7)
        assert gamepad.memory_transport.writes[-1] == bytes(
            (READY, 7, COMMAND_CHANGE_AMIIBO_INDEX)
        )
        with pytest.raises(ValueError, match="0..15"):
            gamepad.change_amiibo(16)
    finally:
        gamepad.stop()


class FakeSerial:
    def __init__(self) -> None:
        self.is_open = True
        self.closed = False
        self.writes: list[bytes] = []
        self._received = bytearray()
        self._lock = threading.Lock()

    @property
    def in_waiting(self) -> int:
        with self._lock:
            return len(self._received)

    def open(self) -> None:
        self.is_open = True

    def reset_input_buffer(self) -> None:
        with self._lock:
            self._received.clear()

    def reset_output_buffer(self) -> None:
        return

    def write(self, data: bytes) -> int:
        raw = bytes(data)
        self.writes.append(raw)
        with self._lock:
            if raw == bytes((READY, READY, COMMAND_HELLO)):
                self._received.append(REPLY_HELLO)
            elif len(raw) == 3 and raw[-1] == COMMAND_CHANGE_AMIIBO_INDEX:
                self._received.append(REPLY_ACK)
        return len(raw)

    def read(self, size: int = 1) -> bytes:
        with self._lock:
            data = bytes(self._received[:size])
            del self._received[:size]
            return data

    def close(self) -> None:
        self.closed = True
        self.is_open = False


def test_ttl_serial_transport_handshake_io_loop_and_close():
    serial_port = FakeSerial()
    sent: list[bytes] = []
    received: list[bytes] = []
    transport = TTLSerialTransport(
        "COM3",
        115200,
        serial_factory=lambda _port, _baudrate: serial_port,
        on_bytes_sent=sent.append,
        on_bytes_received=received.append,
    )
    assert transport.open(timeout=0.2)
    assert transport.is_connected
    assert sent[0] == bytes((READY, READY, COMMAND_HELLO))
    assert received[0] == bytes((REPLY_HELLO,))

    report = SwitchReport(button=SwitchButton.HOME).to_bytes()
    assert transport.write(report, wait=True, timeout=0.2)
    assert serial_port.writes[-1] == report
    assert transport.send_and_wait(
        bytes((READY, 2, COMMAND_CHANGE_AMIIBO_INDEX)),
        lambda value: value == REPLY_ACK,
        0.2,
    )
    assert transport.close()
    assert serial_port.closed
    assert not transport.is_connected


def test_mock_gamepad_runs_without_real_serial_and_records_actions():
    gamepad = MockGamepad()
    try:
        gamepad.press_buttons("HOME")
        gamepad.release_buttons("HOME")
        gamepad.set_stick("RS", 255, 0)
        gamepad.reset()
        assert list(gamepad.actions)[-4:-2] == ["Down HOME", "Up HOME"]
        assert gamepad.reports[-1] == SwitchReport()
        assert gamepad.memory_transport.port == "mock"
    finally:
        assert gamepad.stop()


@dataclass
class PortInfo:
    device: str


def test_list_ports_is_injectable_and_deduplicates():
    assert list_ports(lambda: [PortInfo("COM7"), PortInfo("COM3"), PortInfo("COM7")]) == [
        "COM7",
        "COM3",
    ]
