from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from auto_bdsp_rng.blink_detection import (
    BlinkCaptureConfig,
    BlinkObservation,
    ProjectXsIntegrationError,
    ProjectXsReidentifyResult,
    ProjectXsTrackingConfig,
    SeedState32,
)
from PySide6.QtCore import QPoint, QPointF, QSettings, QThread, QTimer, Qt
from PySide6.QtGui import QPaintEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QAbstractSpinBox, QApplication, QFileDialog, QGridLayout, QGroupBox, QLabel, QPushButton, QScrollArea, QSizePolicy

from auto_bdsp_rng.automation.auto_rng import AutoRngConfig, AutoRngPhase, AutoRngProgress, AutoRngSeedResult, AutoRngTarget
from auto_bdsp_rng.automation.auto_rng.ocr_regions import OcrRegion
from auto_bdsp_rng.automation.auto_rng.runner import AutoRngRunner
from auto_bdsp_rng.automation.auto_tid_rng import AutoTidRngConfig, ProjectXsMunchlaxAdvanceCounter
from auto_bdsp_rng.automation.easycon import EasyConInstallation, EasyConRunResult, EasyConStatus
from auto_bdsp_rng.gen8_static import State8, StateFilter
from auto_bdsp_rng.rng_core import BDSPXorshift, SeedPair64
from auto_bdsp_rng.ui import MainWindow
import auto_bdsp_rng.ui.main_window as main_window_module
from auto_bdsp_rng.automation.auto_rng.runner import _NATURE_MAP
from auto_bdsp_rng.ui.main_window import NATURES_ZH, _normalize_iv_ranges, _reverse_lookup_search_span, _reverse_species_label
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel, AutoRngWorker
from auto_bdsp_rng.ui.history_panel import HistoryPanel


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(MainWindow, "_start_ocr_warmup", lambda self: None)
    application = QApplication.instance() or QApplication([])
    yield application
    for widget in application.topLevelWidgets():
        for timer in widget.findChildren(QTimer):
            timer.stop()
        widget.close()
        widget.deleteLater()
    application.processEvents()


def _set_bdsp_seed(window: MainWindow) -> None:
    window.bdsp_seed64_inputs[0].setText("123456789ABCDEF0")
    window.bdsp_seed64_inputs[1].setText("1111111122222222")


def _auto_rng_settings(tmp_path: Path) -> QSettings:
    settings = QSettings(str(tmp_path / "auto_rng.ini"), QSettings.Format.IniFormat)
    settings.clear()
    return settings


def _profile_settings(tmp_path: Path) -> QSettings:
    settings = QSettings(str(tmp_path / "profile.ini"), QSettings.Format.IniFormat)
    settings.clear()
    return settings


def _project_xs_munchlax_interval(state: SeedState32) -> float:
    rng = BDSPXorshift(state)
    temp = (rng.next() & 0x7FFFFF) / 8388607.0
    return temp * 3.0 + (1.0 - temp) * 12.0 + 0.285


def test_main_window_generates_static_results(app):
    window = MainWindow()

    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == [
        "自动定点乱数",
        "自动 TID 乱数",
        "Seed 捕捉",
        "定点数据区",
        "伊机控",
        "历史记录",
    ]
    assert window.tabs.tabText(window.tabs.currentIndex()) == "自动定点乱数"
    assert hasattr(window, "id_tab")

    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("2")
    window.generate_results()

    assert window.table.rowCount() == 3
    assert window.result_count.text() == "3 条结果"
    assert window.table.item(0, 0).text() == "0"
    assert window.table.item(0, 1).text()


def test_main_window_starts_ocr_warmup_after_ui_is_ready(app, monkeypatch):
    started = []

    monkeypatch.setattr(MainWindow, "_start_ocr_warmup", lambda self: started.append(self))

    window = MainWindow()
    app.processEvents()

    assert started == [window]


def test_static_generation_runs_in_background(app, monkeypatch):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("10000000")

    def slow_generate(_criteria):
        time.sleep(0.2)
        return []

    monkeypatch.setattr(main_window_module, "generate_static_candidates", slow_generate)

    started = time.perf_counter()
    window.generate_results()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1
    assert not window.generate_button.isEnabled()
    deadline = time.perf_counter() + 2
    while not window.generate_button.isEnabled() and time.perf_counter() < deadline:
        app.processEvents()
        QTest.qWait(10)
    assert window.generate_button.isEnabled()


def test_bdsp_max_advances_matches_pokefinder_limit(app):
    window = MainWindow()

    assert window.max_advances.validator().top() == 1_000_000_000
    assert window.max_advances.text() == "100000"


def test_bdsp_filter_tools_do_not_overlap_speed_row(app):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    window.show()
    app.processEvents()
    speed_min = window.iv_min[5]
    show_stats = window.show_stats_check

    assert window.national_dex.text() == "全国图鉴"
    assert window.shiny_charm.text() == "闪耀护符"
    assert window.oval_charm.text() == "圆形护符"
    assert speed_min.geometry().bottom() < show_stats.geometry().top()


def test_project_xs_controls_use_commit_0940b1b_left_layout(app):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.project_xs_tab)
    window.resize(1280, 760)
    window.show()
    app.processEvents()

    capture = window.capture_group.geometry()
    seed = window.seed_group.geometry()

    assert seed.x() == capture.x()
    assert seed.y() > capture.bottom()
    assert window.window_prefix.parent() is window.capture_group
    assert all(not box.isVisible() for box in window.seed32_inputs)
    assert [label.text() for label in window.seed_group.findChildren(QLabel)] == ["Seed0", "Seed1"]
    assert window.threshold.height() <= 32
    assert window.capture_button.height() <= 34


def test_project_xs_status_group_uses_seed_and_reidentify_config_selectors(app):
    window = MainWindow()
    layout = window.status_group.layout()

    assert hasattr(window, "seed_config_combo")
    assert hasattr(window, "reidentify_config_combo")
    assert window.seed_config_combo.findText("config_bebe.json") >= 0
    assert window.reidentify_config_combo.findText("config_bebe.json") >= 0
    assert layout.itemAtPosition(0, 0).widget() is window.progress_label
    assert layout.itemAtPosition(0, 1).widget() is window.progress_value
    assert layout.itemAtPosition(0, 3).widget() is window.seed_config_combo
    assert layout.itemAtPosition(1, 0).widget() is window.advances_label
    assert layout.itemAtPosition(1, 1).widget() is window.advances_value
    assert layout.itemAtPosition(1, 3).widget() is window.reidentify_config_combo
    assert window.status_group.maximumHeight() >= 148
    assert window.status_group.maximumWidth() <= 760
    assert window.refresh_seed_configs_button.isHidden()
    assert window.preview_label.minimumHeight() <= 270
    assert not window.progress_label.isHidden()
    assert not window.advances_label.isHidden()
    assert window.timer_label.isHidden()
    assert window.advance_button.isHidden()


def test_tidsid_capture_updates_seed_inputs(app, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    calls: list[tuple[int, bool, bool, bool]] = []

    def fake_capture(config, **kwargs):
        calls.append(
            (
                config.blink_count,
                kwargs.get("show_window"),
                callable(kwargs.get("frame_callback")),
                callable(kwargs.get("progress_callback")),
            )
        )
        kwargs["progress_callback"](config.blink_count, config.blink_count)
        return SimpleNamespace(intervals=[1, 2, 3])

    monkeypatch.setattr(main_window_module, "capture_pokemon_blinks", fake_capture)
    monkeypatch.setattr(
        main_window_module,
        "recover_tidsid_seed_from_observation",
        lambda observation: SimpleNamespace(state=seed_state, observation=observation),
    )
    window._latest_preview_frame = object()

    window.capture_tidsid_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert calls == [(64, False, True, True)]
    assert [box.text() for box in window.seed32_inputs] == ["AAAAAAAA", "BBBBBBBB", "CCCCCCCC", "DDDDDDDD"]
    assert window.seed64_outputs[0].text() == "AAAAAAAABBBBBBBB"
    assert [box.text() for box in window.auto_tid_rng_tab.tid_seed_inputs] == list(seed_state.format_seed64_pair())
    assert window.auto_tid_rng_tab.id_table.rowCount() == window.auto_tid_rng_tab.frame_threshold.value() + 1
    assert window.tidsid_button.isEnabled()


@pytest.mark.parametrize(
    ("total", "expected_milestones"),
    [
        (7, []),
        (20, [10]),
        (40, [10, 20, 30]),
        (64, [10, 20, 30, 40, 50, 60]),
    ],
)
def test_capture_progress_keep_awake_milestones(app, monkeypatch, total, expected_milestones):
    window = MainWindow()
    emitted: list[tuple[int, int]] = []
    monkeypatch.setattr(window.easycon_tab, "request_capture_keep_awake", lambda *_args, **_kwargs: None)
    window.captureKeepAwakeRequested.connect(lambda done, count: emitted.append((done, count)))
    progress_callback = window._wrap_capture_progress_with_keep_awake(lambda _done, _total: None)

    for done in range(1, total + 1):
        progress_callback(done, total)
        if done == 10:
            progress_callback(done, total)

    assert emitted == [(milestone, total) for milestone in expected_milestones]


def test_capture_keep_awake_sends_short_l_and_ignores_failure(app, monkeypatch):
    window = MainWindow()
    calls: list[tuple[int, int, dict[str, object]]] = []
    logs: list[tuple[str, str]] = []

    def capture_press(done: int, total: int, **kwargs) -> None:
        calls.append((done, total, kwargs))

    monkeypatch.setattr(window.easycon_tab, "request_capture_keep_awake", capture_press)
    window._handle_capture_keep_awake_requested(10, 40)

    assert calls == [
        (
            10,
            40,
            {
                "duration_ms": 100,
            },
        )
    ]

    monkeypatch.setattr(
        window.easycon_tab,
        "request_capture_keep_awake",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(window.easycon_tab, "_append_log", lambda level, message: logs.append((level, message)))

    window._handle_capture_keep_awake_requested(20, 40)

    assert logs == [("warn", "捕捉亮屏保活发送 L 失败，继续捕捉: offline")]


def test_capture_keep_awake_cli_slot_does_not_run_delayed_discovery(app, monkeypatch, tmp_path):
    window = MainWindow()
    cli_index = window.easycon_tab.backend_mode.findData("cli")
    window.easycon_tab.backend_mode.setCurrentIndex(cli_index)
    window.easycon_tab.installation = EasyConInstallation(
        path=tmp_path / "ezcon.exe",
        version="test",
        source="test",
    )
    discovery_called = threading.Event()
    starts: list[tuple[str, int]] = []

    def delayed_discovery() -> None:
        discovery_called.set()
        time.sleep(0.5)

    def capture_start(log_label: str, duration_ms: int) -> bool:
        starts.append((log_label, duration_ms))
        return True

    monkeypatch.setattr(window.easycon_tab, "detect_easycon", delayed_discovery)
    monkeypatch.setattr(window.easycon_tab, "_start_capture_keep_awake_cli", capture_start)

    started_at = time.monotonic()
    window._handle_capture_keep_awake_requested(10, 40)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.1
    assert not discovery_called.is_set()
    assert starts == [("捕捉亮屏保活 10/40", 100)]


def test_capture_keep_awake_bridge_does_not_block_gui_or_create_unbounded_tasks(app):
    window = MainWindow()
    backend_started = threading.Event()
    backend_release = threading.Event()
    calls: list[tuple[str, int, float | None, bool]] = []

    class BlockingBridgeBackend:
        def press(
            self,
            button: str,
            duration_ms: int,
            *,
            timeout_seconds: float | None = None,
            terminate_on_timeout: bool = False,
        ) -> None:
            calls.append((button, duration_ms, timeout_seconds, terminate_on_timeout))
            backend_started.set()
            backend_release.wait(timeout=1)
            raise RuntimeError("Bridge request timed out")

    bridge_index = window.easycon_tab.backend_mode.findData("bridge")
    window.easycon_tab.backend_mode.setCurrentIndex(bridge_index)
    window.easycon_tab.bridge_backend = BlockingBridgeBackend()
    window.easycon_tab.bridge_status = EasyConStatus.BRIDGE_CONNECTED

    started_at = time.monotonic()
    window._handle_capture_keep_awake_requested(10, 40)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.1
    assert backend_started.wait(timeout=0.5)
    for milestone in (20, 30, 30, 30):
        window._handle_capture_keep_awake_requested(milestone, 40)
    assert calls == [("L", 100, 2.0, True)]

    class AliveCaptureThread:
        @staticmethod
        def is_alive() -> bool:
            return True

    window._capture_thread = AliveCaptureThread()
    window._capture_cancel.clear()
    window.capture_button.click()
    assert window._capture_cancel.is_set()
    window._capture_thread = None

    backend_release.set()
    deadline = time.monotonic() + 1
    while window.easycon_tab._capture_keep_awake_future is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()

    assert window.easycon_tab._capture_keep_awake_future is None
    assert "捕捉亮屏保活发送 L 失败，继续捕捉: Bridge request timed out" in window.easycon_tab.log_view.toPlainText()


def test_tidsid_capture_starts_project_xs_munchlax_tracking(app, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0x01020304, 0x11121314, 0x21222324, 0x31323334)
    monkeypatch.setattr(main_window_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        main_window_module,
        "capture_pokemon_blinks",
        lambda config, **kwargs: SimpleNamespace(intervals=[]),
    )
    monkeypatch.setattr(
        main_window_module,
        "recover_tidsid_seed_from_observation",
        lambda observation: SimpleNamespace(state=seed_state, observation=observation),
    )
    window._latest_preview_frame = object()

    window.capture_tidsid_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert isinstance(window._advance_counter, ProjectXsMunchlaxAdvanceCounter)
    assert window._advance_counter.current_advances == 0
    assert window._advance_counter.next_tick_at == pytest.approx(100.0 + _project_xs_munchlax_interval(seed_state))


def test_tidsid_tracking_timer_schedules_next_munchlax_blink(app, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0x01020304, 0x11121314, 0x21222324, 0x31323334)
    now_values = iter([100.0, 100.0, 100.0])
    monkeypatch.setattr(main_window_module.time, "monotonic", lambda: next(now_values))
    monkeypatch.setattr(
        main_window_module,
        "capture_pokemon_blinks",
        lambda config, **kwargs: SimpleNamespace(intervals=[]),
    )
    monkeypatch.setattr(
        main_window_module,
        "recover_tidsid_seed_from_observation",
        lambda observation: SimpleNamespace(state=seed_state, observation=observation),
    )
    window._latest_preview_frame = object()

    window.capture_tidsid_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    expected_ms = round(_project_xs_munchlax_interval(seed_state) * 1000)
    assert window._advance_timer.interval() == expected_ms


def test_munchlax_advance_tick_reschedules_to_following_blink(app, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0x01020304, 0x11121314, 0x21222324, 0x31323334)
    counter = ProjectXsMunchlaxAdvanceCounter()
    counter.reset(current_advances=0, seed=seed_state, now=100.0)
    first_tick = counter.next_tick_at
    window._advance_counter = counter
    window._advance_timer.setInterval(1018)
    monkeypatch.setattr(main_window_module.time, "monotonic", lambda: first_tick)

    window._advance_tick()

    assert window._tracked_advances == 1
    assert window._advance_timer.interval() == round((counter.next_tick_at - first_tick) * 1000)


def test_blink_parameter_spinboxes_ignore_mouse_wheel(app):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.project_xs_tab)
    window.show()
    app.processEvents()
    window.threshold.setValue(0.5)
    window.threshold.setFocus()

    wheel = QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(window.threshold, wheel)

    assert window.threshold.value() == 0.5


def test_bdsp_table_uses_pokefinder_cell_interactions(app):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("30")
    iv_header_count = len(window._result_headers())
    window.show_stats_check.setChecked(True)
    window.generate_results()

    height_column = window._result_headers().index("身高")
    window.table.item(5, height_column).setText("208")
    window.table.setCurrentCell(0, height_column)
    QTest.keyClicks(window.table, "208")

    assert window.table.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectItems
    assert window.table.currentColumn() == height_column
    assert window.table.currentItem().text().startswith("208")
    assert len(window._result_headers()) == iv_header_count
    assert "HP能力" in window._result_headers()
    assert "HP" not in window._result_headers()
    assert window._result_headers()[7:13] == ["HP能力", "攻击能力", "防御能力", "特攻能力", "特防能力", "速度能力"]


def test_main_window_tab_labels_are_copyable(app):
    window = MainWindow()
    for tab_index in range(window.tabs.count()):
        tab = window.tabs.widget(tab_index)
        labels = [label for label in tab.findChildren(QLabel) if label.text()]
        for label in labels:
            flags = label.textInteractionFlags()
            assert flags & Qt.TextInteractionFlag.TextSelectableByMouse
            assert flags & Qt.TextInteractionFlag.TextSelectableByKeyboard


def test_auto_rng_reverse_lookup_window_is_configurable(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path)

    panel.reverse_lookup_window.setValue(500)
    config = panel.build_config()

    assert config.reverse_lookup_window == 500
    assert panel.reverse_lookup_window.maximum() == 10_000
    assert panel.reverse_lookup_window.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons


def test_auto_rng_strategy_parameters_have_hover_explanations(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_auto_rng_settings(tmp_path))
    form = panel.strategy_group.layout()

    explained_rows = (
        (panel.max_advances, "过 100 万帧大约需要 10 分钟"),
        (panel.fixed_delay, "delay 越大，撞闪脚本启动得越早"),
        (panel.max_wait_frames, "越依赖过帧脚本接近目标"),
        (panel.reseeding_threshold, "若重测 Seed 或校正后"),
        (panel.shiny_threshold_seconds, "判定为疑似闪光并停止自动流程"),
    )
    for field, expected_text in explained_rows:
        assert expected_text in field.toolTip()
        label = form.labelForField(field)
        assert label is not None
        assert label.toolTip() == field.toolTip()

    assert form.labelForField(panel.max_advances).text() == "搜索范围"
    assert form.labelForField(panel.shiny_threshold_seconds).text() == "闪光阈值（秒）"
    for control in (
        panel.sync_combo,
        panel.sync_nature_input,
        panel.auto_reverse_combo,
        panel.reverse_lookup_window,
    ):
        assert control.toolTip() == ""


def test_auto_rng_panel_persists_exit_script_and_reseeding_threshold(app, tmp_path):
    (tmp_path / "BDSP测种.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "bdsp过帧.txt").write_text("_目标帧数 = 100\n", encoding="utf-8")
    (tmp_path / "谢米.txt").write_text("_闪帧 = 100\n", encoding="utf-8")
    (tmp_path / "离开地下.txt").write_text("B 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path)

    panel.exit_script_combo.setCurrentIndex(panel.exit_script_combo.findText("离开地下.txt"))
    panel.reseeding_threshold.setValue(12345)
    config = panel.build_config()

    assert config.exit_script_path == tmp_path / "离开地下.txt"
    assert config.reseeding_threshold == 12345
    assert panel.refresh_scripts_button.parent() is panel.script_group


def test_auto_rng_panel_defaults_reseeding_threshold_to_500000_without_saved_setting(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_auto_rng_settings(tmp_path))

    assert panel.reseeding_threshold.value() == 500_000
    assert panel.build_config().reseeding_threshold == 500_000


def test_auto_rng_panel_restores_saved_reseeding_threshold(app, tmp_path):
    settings = _auto_rng_settings(tmp_path)
    settings.setValue("reseeding_threshold", 12345)

    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)

    assert panel.reseeding_threshold.value() == 12345
    assert panel.build_config().reseeding_threshold == 12345


def test_reverse_lookup_search_span_uses_symmetric_window():
    assert _reverse_lookup_search_span(500, 500) == (0, 1000, 1000)
    assert _reverse_lookup_search_span(2000, 500) == (1500, 2500, 1000)
    assert _reverse_lookup_search_span(20_000, 20_000) == (10_000, 30_000, 20_000)


def test_reverse_species_label_uses_chinese_names():
    assert _reverse_species_label("Registeel") == "雷吉斯奇鲁"
    assert _reverse_species_label("Regirock") == "雷吉洛克"
    assert _reverse_species_label("Unknownmon") == "Unknownmon"


def test_reverse_lookup_groups_include_legendary_birds():
    assert main_window_module._reverse_lookup_group_descriptions("Articuno") == ("Articuno", "Zapdos", "Moltres")
    assert main_window_module._reverse_lookup_group_descriptions("Zapdos") == ("Articuno", "Zapdos", "Moltres")
    assert main_window_module._reverse_lookup_group_descriptions("Moltres") == ("Articuno", "Zapdos", "Moltres")
    assert main_window_module._reverse_lookup_group_descriptions("Unknownmon") == ("Unknownmon",)


def test_main_window_waits_around_right_after_notes_ocr(app, monkeypatch):
    window = MainWindow()
    events = []

    monkeypatch.setattr(main_window_module.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    monkeypatch.setattr(window, "_send_easycon_right", lambda log_details=True: events.append(("right", log_details)))

    window._pause_ocr_and_turn_to_stats_page()

    assert events == [("sleep", 0.1), ("right", True), ("sleep", 0.1)]


def test_main_window_can_silence_internal_right_logs(app, monkeypatch):
    window = MainWindow()
    events = []

    monkeypatch.setattr(main_window_module.time, "sleep", lambda seconds: events.append(("sleep", seconds)))
    monkeypatch.setattr(window, "_send_easycon_right", lambda log_details=True: events.append(("right", log_details)))

    window._pause_ocr_and_turn_to_stats_page(log_details=False)

    assert events == [("sleep", 0.1), ("right", False), ("sleep", 0.1)]


def test_auto_rng_nature_map_matches_ui_nature_order():
    assert [_NATURE_MAP[name] for name in NATURES_ZH] == list(range(25))
    assert _NATURE_MAP["温顺"] == 21


def test_normalize_iv_ranges_rejects_impossible_native_sentinel():
    assert _normalize_iv_ranges([(30, 31), (31, 0), (0, 3), (24, 26), (30, 31), (30, 31)]) is None
    assert _normalize_iv_ranges([(30, 31), (10, 13), (17, 23), (30, 31), (27, 29), (30, 31)]) == (
        [30, 10, 17, 30, 27, 30],
        [31, 13, 23, 31, 29, 31],
    )


def test_bdsp_characteristic_matches_pokefinder_tie_break_and_translation(app):
    window = MainWindow()
    state = State8(
        advances=14113,
        ec=0x38458EDC,
        sidtid=0,
        pid=0xE4B100B9,
        ivs=(31, 31, 31, 9, 23, 31),
        ability=1,
        gender=2,
        level=30,
        nature=16,
        shiny=2,
        height=165,
        weight=135,
    )

    assert window._characteristic_text(state) == "经常睡午觉"


def test_main_window_exports_txt_from_results(app, monkeypatch, tmp_path):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("2")
    window.generate_results()
    output = tmp_path / "results.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(output), "Text files (*.txt)"))

    window.export_results_txt()

    text = output.read_text(encoding="utf-8")
    assert text.startswith("帧数\tEC\tPID")
    assert "\t个性\n" in text.splitlines()[0] + "\n"


def test_main_window_syncs_seed64_display(app):
    window = MainWindow()

    assert window.seed64_outputs[0].text() == ""
    assert window.seed64_outputs[1].text() == ""


def test_seed_update_auto_refreshes_existing_results(app):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("2")
    window.generate_results()
    first_ec = window.table.item(0, 1).text()

    window.bdsp_seed64_inputs[0].setText("0000000000000001")
    window.bdsp_seed64_inputs[1].setText("0000000000000002")
    window._sync_state32_from_bdsp_seed64()

    assert window.table.rowCount() == 3
    assert window.table.item(0, 1).text() != first_ec


def test_reidentify_updates_advances_and_keeps_seed(app, monkeypatch):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("2")
    window.generate_results()
    observation = BlinkObservation.from_sequences([0, 1, 0], [0, 12, 24])
    state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)

    capture_counts: list[int] = []

    def fake_capture(config, *args, **kwargs):
        capture_counts.append(config.blink_count)
        return observation

    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.capture_player_blinks", fake_capture)
    monkeypatch.setattr(
        "auto_bdsp_rng.ui.main_window.reidentify_seed_from_observation",
        lambda *_args, **_kwargs: ProjectXsReidentifyResult(state=state, observation=observation, advances=42),
    )
    original_words = ["12345678", "9ABCDEF0", "11111111", "22222222"]
    for box, text in zip(window.seed32_inputs, original_words):
        box.setText(text)
    window._latest_preview_frame = object()

    window.reidentify_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert [box.text() for box in window.seed32_inputs] == original_words
    assert int(window.advances_value.text()) >= 42
    assert capture_counts == [7]
    assert window.result_count.text() == "3 条结果"


def test_reidentify_noisy_option_uses_20_blinks_and_noisy_reidentify(app, monkeypatch):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    observation = BlinkObservation.from_sequences([0, 1, 0], [0, 12, 24])
    state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    capture_counts: list[int] = []

    def fake_capture(config, *args, **kwargs):
        capture_counts.append(config.blink_count)
        return observation

    def fail_regular(*_args, **_kwargs):
        raise AssertionError("regular reidentify should not be used")

    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.capture_player_blinks", fake_capture)
    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.reidentify_seed_from_observation", fail_regular)
    monkeypatch.setattr(
        "auto_bdsp_rng.ui.main_window.reidentify_seed_from_observation_noisy",
        lambda *_args, **_kwargs: ProjectXsReidentifyResult(state=state, observation=observation, advances=43),
    )
    original_words = ["12345678", "9ABCDEF0", "11111111", "22222222"]
    for box, text in zip(window.seed32_inputs, original_words):
        box.setText(text)
    window.reidentify_1_pk_npc.setChecked(True)
    window._latest_preview_frame = object()

    window.reidentify_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert capture_counts == [20]
    assert [box.text() for box in window.seed32_inputs] == original_words
    assert window.advances_value.text() == "43"


def test_reidentify_noisy_uses_tracked_advances_before_stopping_tracking(app, monkeypatch):
    window = MainWindow()
    observation = BlinkObservation.from_sequences([], [0, 12, 24])
    window.reidentify_1_pk_npc.setChecked(True)
    window._tracked_advances = 120_000
    window.max_advances.setText("100000")
    window._latest_preview_frame = object()
    search_ranges: list[tuple[int, int]] = []

    def fake_capture(*_args, **_kwargs):
        return observation

    def fake_noisy(_state, _observation, **kwargs):
        search_ranges.append((kwargs["search_min"], kwargs["search_max"]))
        return ProjectXsReidentifyResult(
            state=SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD),
            observation=observation,
            advances=110_000,
        )

    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation_noisy", fake_noisy)
    for box, text in zip(window.seed32_inputs, ["12345678", "9ABCDEF0", "11111111", "22222222"]):
        box.setText(text)

    window.reidentify_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert search_ranges == [(110_000, 100_000)]


def test_capture_seed_restores_running_preview(app, monkeypatch):
    window = MainWindow()
    observation = SimpleNamespace(offset_time=100.0)
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.capture_player_blinks", lambda *_args, **_kwargs: observation)
    monkeypatch.setattr(
        "auto_bdsp_rng.ui.main_window.recover_seed_from_observation",
        lambda *_args, **_kwargs: SimpleNamespace(state=seed_state),
    )
    window._preview_timer.start()
    window.preview_button.setText(window._text("stop_preview"))
    window._latest_preview_frame = object()

    window.capture_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    preview_active = window._preview_timer.isActive()
    preview_label = window.preview_button.text()
    window._preview_timer.stop()

    assert preview_active
    assert preview_label == window._text("stop_preview")


def test_capture_seed_initializes_preview_when_no_frame_was_seen(app, monkeypatch):
    window = MainWindow()
    observation = SimpleNamespace(offset_time=100.0)
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    preview_updates: list[str] = []
    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.capture_player_blinks", lambda *_args, **_kwargs: observation)
    monkeypatch.setattr(
        "auto_bdsp_rng.ui.main_window.recover_seed_from_observation",
        lambda *_args, **_kwargs: SimpleNamespace(state=seed_state),
    )

    def fake_update_preview() -> None:
        preview_updates.append("updated")
        window._latest_preview_frame = object()

    monkeypatch.setattr(window, "_update_preview_frame", fake_update_preview)

    window.capture_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert preview_updates == ["updated"]
    assert window._latest_preview_frame is not None


def test_reidentify_restores_running_preview(app, monkeypatch):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    observation = BlinkObservation.from_sequences([0, 1, 0], [0, 12, 24])
    state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.capture_player_blinks", lambda *_args, **_kwargs: observation)
    monkeypatch.setattr(
        "auto_bdsp_rng.ui.main_window.reidentify_seed_from_observation",
        lambda *_args, **_kwargs: ProjectXsReidentifyResult(state=state, observation=observation, advances=42),
    )
    for box, text in zip(window.seed32_inputs, ["12345678", "9ABCDEF0", "11111111", "22222222"]):
        box.setText(text)
    window._preview_timer.start()
    window.preview_button.setText(window._text("stop_preview"))
    window._latest_preview_frame = object()

    window.reidentify_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    preview_active = window._preview_timer.isActive()
    preview_label = window.preview_button.text()
    window._preview_timer.stop()

    assert preview_active
    assert preview_label == window._text("stop_preview")


def test_reidentify_initializes_preview_when_no_frame_was_seen(app, monkeypatch):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    observation = BlinkObservation.from_sequences([0, 1, 0], [0, 12, 24])
    state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    preview_updates: list[str] = []
    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.capture_player_blinks", lambda *_args, **_kwargs: observation)
    monkeypatch.setattr(
        "auto_bdsp_rng.ui.main_window.reidentify_seed_from_observation",
        lambda *_args, **_kwargs: ProjectXsReidentifyResult(state=state, observation=observation, advances=42),
    )
    for box, text in zip(window.seed32_inputs, ["12345678", "9ABCDEF0", "11111111", "22222222"]):
        box.setText(text)

    def fake_update_preview() -> None:
        preview_updates.append("updated")
        window._latest_preview_frame = object()

    monkeypatch.setattr(window, "_update_preview_frame", fake_update_preview)

    window.reidentify_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert preview_updates == ["updated"]
    assert window._latest_preview_frame is not None


def test_main_window_loads_project_xs_config_fields(app, monkeypatch, tmp_path):
    config_dir = tmp_path / "project_xs_configs"
    config_dir.mkdir()
    (config_dir / "config_bebe.json").write_text(
        """{
    "MonitorWindow": true,
    "WindowPrefix": "PotPlayer",
    "image": "./images/bebe/eye2.png",
    "view": [516, 377, 38, 53],
    "thresh": 0.7,
    "white_delay": 0.0,
    "advance_delay": 0,
    "advance_delay_2": 0,
    "npc": 1,
    "pokemon_npc": 0,
    "timeline_npc": 0,
    "crop": [0, 0, 0, 0],
    "camera": 0,
    "display_percent": 80
}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(main_window_module, "PROJECT_XS_CONFIGS", config_dir)
    window = MainWindow()
    index = window.config_combo.findText("config_bebe.json")
    assert index >= 0
    window.config_combo.setCurrentIndex(index)

    assert window.window_prefix.text() == "PotPlayer"
    assert window.monitor_window.isChecked() is True
    assert window.x.text() == "516"
    assert window.y.text() == "377"
    assert window.w.text() == "38"
    assert window.h.text() == "53"
    assert window.threshold.value() == 0.7


def test_main_window_header_shows_auto_rng_runtime_status(app):
    window = MainWindow()

    assert not hasattr(window, "language_combo")
    assert not hasattr(window, "language_label")
    assert not hasattr(window, "seed_badge")
    assert window.auto_loop_badge.text() == "循环 0"
    assert window.auto_phase_badge.text() == "阶段 空闲"
    assert window.auto_advance_badge.text() == "advance 0"

    window.auto_rng_tab.autoProgressChanged.emit(
        AutoRngProgress(
            phase=AutoRngPhase.RUN_HIT_SCRIPT,
            loop_index=3,
            current_advances=4567,
        )
    )

    assert window.auto_loop_badge.text() == "循环 3"
    assert window.auto_phase_badge.text() == "阶段 运行撞闪脚本"
    assert window.auto_advance_badge.text() == "advance 4567"


def test_main_window_has_auto_rng_tab(app):
    window = MainWindow()

    assert window.tabs.count() == 6
    assert window.tabs.tabText(0) == "自动定点乱数"
    assert window.tabs.tabText(1) == "自动 TID 乱数"
    assert window.tabs.tabText(5) == "历史记录"
    assert hasattr(window, "id_tab")


def test_id_panel_syncs_seed_pair_and_generates_results(app):
    window = MainWindow()

    _set_bdsp_seed(window)
    window._sync_state32_from_bdsp_seed64()

    assert [box.text() for box in window.id_tab.seed_inputs] == ["123456789ABCDEF0", "1111111122222222"]

    window.id_tab.seed_inputs[0].setText("AAAAAAAA55555555")
    window.id_tab.seed_inputs[1].setText("33333333CCCCCCCC")
    window.id_tab._emit_seed_changed()

    assert [box.text() for box in window.bdsp_seed64_inputs] == ["AAAAAAAA55555555", "33333333CCCCCCCC"]

    window.id_tab.max_advances.setValue(3)
    window.id_tab.generate_results()

    assert window.id_tab.table.rowCount() == 3
    assert window.id_tab.table.columnCount() == 5


def test_auto_rng_panel_blocks_start_when_required_script_parameter_is_missing(app, tmp_path):
    (tmp_path / "BDSP测种.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "bdsp过帧.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "谢米.txt").write_text("_闪帧 = 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path)
    emitted = []
    panel.startRequested.connect(lambda payload: emitted.append(payload))
    panel.hit_script_combo.setCurrentIndex(panel.hit_script_combo.findText("谢米.txt"))

    panel.start_button.click()

    assert emitted == []
    assert "缺少必需参数 _目标帧数" in panel.log_view.toPlainText()


def test_auto_rng_start_button_uses_primary_toolbutton_style(app):
    window = MainWindow()

    assert window.auto_rng_tab.start_button.isEnabled()
    assert window.auto_rng_tab.start_button.objectName() == "PrimaryButton"
    assert "QToolButton#PrimaryButton" in window.styleSheet()


def test_profile_version_syncs_auto_rng_target_choices(app):
    window = MainWindow()

    window._set_profile_version(main_window_module.GameVersion.SP)

    assert window.auto_rng_tab._target_version == main_window_module.GameVersion.SP
    form = window.auto_rng_tab.target_form
    form.category_combo.setCurrentIndex(form.category_combo.findData("ramanasParkPureSpace"))
    bird_descriptions = [
        form.encounter_combo.itemData(index).description
        for index in range(form.encounter_combo.count())
        if form.encounter_combo.itemData(index).description in {"Articuno", "Zapdos", "Moltres"}
    ]
    assert bird_descriptions == ["Articuno", "Zapdos", "Moltres"]


def test_main_window_restores_profile_settings(app, tmp_path):
    settings = _profile_settings(tmp_path)
    settings.setValue("name", "Pearl main")
    settings.setValue("tid", 24680)
    settings.setValue("sid", 13579)
    settings.setValue("version", main_window_module.GameVersion.SP.value)
    settings.setValue("national_dex", True)
    settings.setValue("shiny_charm", True)
    settings.setValue("oval_charm", True)

    window = MainWindow(profile_settings=settings)

    assert window.profile_name.text() == "Pearl main"
    assert window.tid.text() == "24680"
    assert window.sid.text() == "13579"
    assert window.tsv.text() == str(24680 ^ 13579)
    assert window._profile_version == main_window_module.GameVersion.SP
    assert window.profile_game_value.text() == "明亮珍珠"
    assert window.auto_rng_tab._target_version == main_window_module.GameVersion.SP
    assert window.national_dex.isChecked()
    assert window.shiny_charm.isChecked()
    assert window.oval_charm.isChecked()


def test_main_window_saves_profile_settings_on_close(app, tmp_path):
    settings = _profile_settings(tmp_path)
    window = MainWindow(profile_settings=settings)
    window.profile_name.setText("Diamond main")
    window.tid.setText("11111")
    window.sid.setText("22222")
    window.national_dex.setChecked(True)
    window.shiny_charm.setChecked(False)
    window.oval_charm.setChecked(True)
    window._set_profile_version(main_window_module.GameVersion.BD)

    window.close()

    assert settings.value("name") == "Diamond main"
    assert int(settings.value("tid")) == 11111
    assert int(settings.value("sid")) == 22222
    assert settings.value("version") == main_window_module.GameVersion.BD.value
    assert settings.value("national_dex", type=bool)
    assert not settings.value("shiny_charm", type=bool)
    assert settings.value("oval_charm", type=bool)


def test_auto_rng_panel_emits_config_when_starting_with_valid_scripts(app, tmp_path):
    (tmp_path / "BDSP测种.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "bdsp过帧.txt").write_text("_目标帧数 = 100\n", encoding="utf-8")
    (tmp_path / "谢米.txt").write_text("_闪帧 = 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path)
    emitted: list[AutoRngConfig] = []
    panel.startRequested.connect(lambda config: emitted.append(config))
    panel.hit_script_combo.setCurrentIndex(panel.hit_script_combo.findText("谢米.txt"))
    panel.fixed_delay.setValue(1200)
    panel.max_wait_frames.setValue(300)

    panel.start_button.click()

    assert len(emitted) == 1
    config = emitted[0]
    assert isinstance(config, AutoRngConfig)
    assert config.script_dir == tmp_path
    assert config.seed_script_path == tmp_path / "BDSP测种.txt"
    assert config.advance_script_path == tmp_path / "bdsp过帧.txt"
    assert config.hit_script_path == tmp_path / "谢米.txt"
    assert config.fixed_delay == 1200
    assert config.max_wait_frames == 300
    assert config.start_phase == AutoRngPhase.RUN_SEED_SCRIPT
    assert config.reseed_threshold_frames == 900_000
    assert config.min_final_flash_frames == 5


def test_auto_rng_panel_can_start_from_capture_seed_via_menu(app, tmp_path):
    (tmp_path / "BDSP测种.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "bdsp过帧.txt").write_text("_目标帧数 = 100\n", encoding="utf-8")
    (tmp_path / "谢米.txt").write_text("_闪帧 = 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path)
    emitted: list[AutoRngConfig] = []
    panel.startRequested.connect(lambda config: emitted.append(config))
    panel.hit_script_combo.setCurrentIndex(panel.hit_script_combo.findText("谢米.txt"))

    panel.start_from_capture_action.trigger()

    assert len(emitted) == 1
    assert emitted[0].start_phase == AutoRngPhase.CAPTURE_SEED
    assert emitted[0].seed_script_path == tmp_path / "BDSP测种.txt"


def test_auto_rng_panel_can_start_from_reidentify_via_menu(app, tmp_path):
    (tmp_path / "BDSP测种.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "bdsp过帧.txt").write_text("_目标帧数 = 100\n", encoding="utf-8")
    (tmp_path / "谢米.txt").write_text("_闪帧 = 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path)
    emitted: list[AutoRngConfig] = []
    panel.startRequested.connect(lambda config: emitted.append(config))
    panel.hit_script_combo.setCurrentIndex(panel.hit_script_combo.findText("谢米.txt"))

    panel.start_from_reidentify_action.trigger()

    assert len(emitted) == 1
    assert emitted[0].start_phase == AutoRngPhase.REIDENTIFY


def test_main_window_reidentify_start_requires_existing_seed(app, tmp_path, monkeypatch):
    window = MainWindow()
    config = AutoRngConfig(script_dir=tmp_path, start_phase=AutoRngPhase.REIDENTIFY)
    started: list[object] = []
    warnings: list[str] = []
    monkeypatch.setattr(window.auto_rng_tab, "run_with_runner", started.append)
    monkeypatch.setattr(main_window_module.QMessageBox, "warning", lambda _parent, _title, text: warnings.append(text))
    for box in window.seed32_inputs:
        box.clear()

    window._start_auto_rng(config)

    assert not started
    assert warnings
    assert "Seed" in warnings[0]


def test_main_window_exposes_shiny_threshold_calibration_button_on_seed_capture_tab(app):
    window = MainWindow()

    assert window.calibrate_shiny_threshold_button.text() == "校准闪光判定"


def test_shiny_threshold_calibration_runs_in_background_without_wait_cursor(app, monkeypatch):
    window = MainWindow()
    cursor_states: list[bool] = []
    shown: list[float] = []

    def fake_measure_keyword_interval(*_args, **_kwargs):
        cursor_states.append(QApplication.overrideCursor() is not None)
        time.sleep(0.15)
        return SimpleNamespace(interval_seconds=2.5)

    monkeypatch.setattr(main_window_module, "measure_keyword_interval", fake_measure_keyword_interval)
    monkeypatch.setattr(window, "_show_shiny_threshold_dialog", lambda interval: shown.append(interval))

    started_at = time.monotonic()
    window.calibrate_shiny_threshold()
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.1
    assert "[闪光判定校准] 开始监控" in window.auto_rng_tab.log_view.toPlainText()
    assert window.calibrate_shiny_threshold_button.text() == "停止校准"
    for _ in range(20):
        if shown:
            break
        QTest.qWait(50)
    assert cursor_states == [False]
    assert shown == [2.5]
    assert window.calibrate_shiny_threshold_button.text() == "校准闪光判定"


def test_auto_rng_panel_includes_editable_shiny_threshold_in_config(app, tmp_path):
    (tmp_path / "BDSP测种.txt").write_text("A 100\n", encoding="utf-8")
    (tmp_path / "bdsp过帧.txt").write_text("_目标帧数 = 100\n", encoding="utf-8")
    (tmp_path / "谢米.txt").write_text("_闪帧 = 100\n", encoding="utf-8")
    panel = AutoRngPanel(script_dir=tmp_path)
    panel.hit_script_combo.setCurrentIndex(panel.hit_script_combo.findText("谢米.txt"))
    panel.shiny_threshold_seconds.setValue(2.8)

    config = panel.build_config()

    assert config.shiny_threshold_seconds == 2.8


def test_auto_rng_panel_defaults_shiny_threshold_to_four_seconds_without_saved_setting(app, tmp_path):
    panel = AutoRngPanel(script_dir=tmp_path, settings=_auto_rng_settings(tmp_path))

    assert panel.shiny_threshold_seconds.value() == 4.0
    assert panel.build_config().shiny_threshold_seconds == 4.0


def test_auto_rng_panel_restores_saved_shiny_threshold(app, tmp_path):
    settings = _auto_rng_settings(tmp_path)
    settings.setValue("shiny_threshold", 2.5)

    panel = AutoRngPanel(script_dir=tmp_path, settings=settings)

    assert panel.shiny_threshold_seconds.value() == 2.5
    assert panel.build_config().shiny_threshold_seconds == 2.5


def test_auto_rng_panel_has_target_button_and_no_old_main_regions(app):
    panel = AutoRngPanel()
    group_titles = {group.title() for group in panel.findChildren(QGroupBox)}

    assert "目标精灵设置" not in group_titles
    assert "运行摘要" not in group_titles
    assert "定点目标 / 存档信息 / 个体筛选" not in group_titles
    assert "候选结果" not in group_titles
    assert not hasattr(panel, "candidate_table")
    assert not hasattr(panel, "search_target_summary")
    assert hasattr(panel, "target_button")
    assert panel.target_button.text() == "目标精灵设置..."
    assert not hasattr(panel, "parameter_preview")
    assert not hasattr(panel, "preview_button")
    assert panel.log_view.isReadOnly() is True
    labels = {label.text() for label in panel.findChildren(QLabel)}
    assert "重新测 seed 阈值" not in labels
    assert "最小 final flash frames" not in labels


def test_auto_rng_panel_uses_full_width_log_without_summary_group(app):
    panel = AutoRngPanel()
    group_titles = {group.title() for group in panel.findChildren(QGroupBox)}
    visible_labels = {label.text() for label in panel.findChildren(QLabel)}

    assert "运行摘要" not in group_titles
    assert "Seed" not in visible_labels
    assert "触发帧" not in visible_labels
    assert "剩余" not in visible_labels
    assert "raw target" not in visible_labels
    assert "trigger advances" not in visible_labels
    assert "current advances" not in visible_labels
    assert "remaining_to_trigger" not in visible_labels
    assert "final flash_frames" not in visible_labels
    assert not ({"当前循环", "当前阶段", "原始目标帧", "当前帧", "最终闪帧"} & visible_labels)
    assert not hasattr(panel, "summary_seed")
    assert not hasattr(panel, "summary_group")
    assert not hasattr(panel, "summary_trigger")
    assert not hasattr(panel, "summary_remaining")
    assert not hasattr(panel, "summary_target")
    assert panel.log_group.maximumWidth() == 16777215
    assert len([group for group in panel.findChildren(QGroupBox) if group.title() == "日志"]) == 1
    assert panel.content_grid.itemAtPosition(1, 0).widget() is panel.log_group
    index = panel.content_grid.indexOf(panel.log_group)
    assert index >= 0
    row, column, row_span, column_span = panel.content_grid.getItemPosition(index)
    assert (row, column, row_span, column_span) == (1, 0, 1, 2)
    assert panel.content_grid.itemAtPosition(0, 0).widget() is panel.config_panel
    assert panel.content_grid.itemAtPosition(0, 1).widget() is panel.runtime_panel


def test_auto_rng_page_uses_compact_toolbar_and_fixed_left_sidebar(app):
    panel = AutoRngPanel()

    assert 56 <= panel.toolbar.maximumHeight() <= 64
    assert panel.mode_combo.width() == 120
    assert panel.loop_count.width() == 80
    assert panel.start_button.height() == 34
    assert panel.stop_button.height() == 34
    assert panel.config_panel.minimumWidth() >= 430
    assert panel.config_panel.minimumWidth() == panel.config_panel.maximumWidth()
    assert panel.strategy_group.minimumHeight() < 400
    assert panel.strategy_group.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Preferred
    assert panel.strategy_group.maximumHeight() == 16777215  # 未设固定高度
    assert panel.script_group.maximumHeight() == 16777215  # 未设固定高度
    assert panel.max_advances.width() >= 200
    assert panel.seed_script_combo.width() <= 170
    assert panel.refresh_scripts_button.width() <= 250
    assert not any(button.text() == "参数预览" for button in panel.findChildren(QPushButton))


def test_auto_rng_target_summary_uses_chinese_compact_rows_and_scroll(app):
    panel = AutoRngPanel()
    from auto_bdsp_rng.data import get_static_encounters

    record = next(r for r in get_static_encounters() if r.description == "Shaymin")
    panel.set_targets([
        (record, StateFilter(height_min=0, height_max=0, shiny=2), "square"),
        (record, StateFilter(height_min=255, height_max=255, shiny=2), "square"),
    ])

    assert panel.target_summary_title.text() == "精灵筛选列表：谢米"
    assert panel.target_summary_labels[0].text() == "1. 异色：方闪 | 身高：0"
    assert panel.target_summary_labels[1].text() == "2. 异色：方闪 | 身高：255"
    assert "任意" not in panel.target_summary_labels[0].text()
    assert "Height" not in panel.target_summary_labels[0].text()
    assert "Weight" not in panel.target_summary_labels[0].text()
    assert panel.target_summary_scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert panel.target_summary_scroll.maximumHeight() <= 150


def test_auto_rng_target_summary_scrolls_when_many_conditions(app):
    panel = AutoRngPanel()
    from auto_bdsp_rng.data import get_static_encounters

    record = next(r for r in get_static_encounters() if r.description == "Shaymin")
    panel.set_targets([
        (record, StateFilter(height_min=i, height_max=i, shiny=2), "square")
        for i in range(12)
    ])

    assert len(panel.target_summary_labels) == 12
    assert panel.target_summary_scroll.maximumHeight() <= 150


def test_auto_rng_panel_restores_persisted_multi_targets(app, tmp_path):
    from auto_bdsp_rng.data import get_static_encounters

    first = AutoRngPanel(script_dir=tmp_path)
    first._settings.clear()
    record = next(r for r in get_static_encounters() if r.description == "Shaymin")
    first.set_targets([
        (record, StateFilter(height_min=0, height_max=0, weight_min=10, weight_max=20, shiny=2), "square"),
        (record, StateFilter(height_min=255, height_max=255, ability=1, gender=0, shiny=2), "square"),
    ])
    first.close()
    app.processEvents()

    restored = AutoRngPanel(script_dir=tmp_path)

    targets = restored.targets()
    assert len(targets) == 2
    assert [target[0].description for target in targets] == ["Shaymin", "Shaymin"]
    assert targets[0][1].height_min == 0
    assert targets[0][1].height_max == 0
    assert targets[0][1].weight_min == 10
    assert targets[0][1].weight_max == 20
    assert targets[1][1].height_min == 255
    assert targets[1][1].height_max == 255
    assert targets[1][1].ability == 1
    assert targets[1][1].gender == 0
    assert [target[2] for target in targets] == ["square", "square"]
    assert restored.target_summary_title.text() == "精灵筛选列表：谢米"
    restored._settings.clear()


def test_auto_rng_stop_button_requests_runner_stop_immediately(app):
    panel = AutoRngPanel()
    stops: list[str] = []
    emissions: list[str] = []
    panel.stopRequested.connect(lambda: emissions.append("emitted"))
    panel._runner_worker = SimpleNamespace(stop=lambda: stops.append("stopped"))

    panel.stop_button.click()

    assert stops == ["stopped"]
    assert emissions == ["emitted"]


def test_auto_rng_panel_apply_progress_updates_summary_and_log(app):
    panel = AutoRngPanel()

    panel.apply_progress(
        AutoRngProgress(
            phase=AutoRngPhase.RUN_HIT_SCRIPT,
            loop_index=2,
            seed_text="seed-1",
            locked_target=AutoRngTarget(raw_target_advances=1300, label="Shaymin"),
            raw_target_advances=1300,
            fixed_delay=1200,
            trigger_advances=100,
            current_advances=0,
            remaining_to_trigger=100,
            final_flash_frames=100,
            log_message="最终撞闪剩余 100 帧",
        )
    )

    assert panel.status_badge.text() == "运行撞闪脚本"
    assert "最终撞闪剩余 100 帧" in panel.log_view.toPlainText()


def test_auto_rng_progress_current_advances_syncs_header_counter(app):
    window = MainWindow()
    window._advance_timer.stop()
    window._advance_step = 1
    window._tracked_advances = 990
    window._update_auto_rng_header(advances=990)

    window._apply_auto_rng_header_progress(
        AutoRngProgress(
            phase=AutoRngPhase.RUN_HIT_SCRIPT,
            loop_index=1,
            current_advances=1000,
        )
    )

    assert window._tracked_advances == 1000
    assert window.auto_advance_badge.text() == "advance 1000"
    assert not window._advance_timer.isActive()


def test_auto_seed_captured_does_not_start_local_advance_timer(app):
    window = MainWindow()
    window._advance_timer.stop()

    window._handle_auto_seed_captured(
        AutoRngSeedResult(
            seed=SeedPair64(0x1111111122222222, 0x3333333344444444),
            current_advances=1234,
            npc=0,
        )
    )

    assert window._tracked_advances == 1234
    assert window.auto_advance_badge.text() == "advance 1234"
    assert not window._advance_timer.isActive()


def test_advance_tick_catches_up_from_elapsed_time(app, monkeypatch):
    window = MainWindow()
    current_time = [100.0]
    monkeypatch.setattr(main_window_module.time, "monotonic", lambda: current_time[0])

    window._start_auto_advance_tracking(
        AutoRngSeedResult(
            seed=SeedPair64(0, 0),
            current_advances=990,
            npc=0,
            measured_at=100.0,
        )
    )
    current_time[0] = 100.0 + 10 * 1.018
    window._advance_tick()

    assert window._tracked_advances == 1000
    assert window.auto_advance_badge.text() == "advance 1000"


def test_history_panel_reverse_lookup_candidates_are_single_line(app):
    panel = HistoryPanel()
    state = SimpleNamespace(
        advances=1234,
        ec=0xAABBCCDD,
        pid=0x11223344,
        ivs=(31, 30, 29, 28, 27, 26),
        ability=1,
        gender=0,
        nature=0,
        shiny=2,
        height=255,
        weight=12,
    )

    panel.reverse_lookup_results([state], characteristic="喜欢吃东西", delays=[99])

    lines = [line for line in panel.text_view.toPlainText().splitlines() if line.strip()]
    reverse_lines = [line for line in lines if "反查候选" in line]
    assert len(reverse_lines) == 1
    assert "adv=1234" in reverse_lines[0]
    assert "delay=99" in reverse_lines[0]
    assert "EC=AABBCCDD" in reverse_lines[0]
    assert "PID=11223344" in reverse_lines[0]
    assert "HP=31" in reverse_lines[0]
    assert "身高=255" in reverse_lines[0]
    assert "体重=12" in reverse_lines[0]
    assert not any(line.strip().startswith("EC:") for line in lines)


def test_history_panel_candidates_use_configured_delay(app):
    panel = HistoryPanel()
    state = SimpleNamespace(
        advances=1234,
        ec=0xAABBCCDD,
        pid=0x11223344,
        ivs=(31, 30, 29, 28, 27, 26),
        ability=1,
        gender=0,
        nature=0,
        shiny=2,
        height=255,
        weight=12,
    )

    panel.candidates_found([state], locked_index=0, candidate_delay=321)

    text = panel.text_view.toPlainText()
    assert "adv=1234" in text
    assert "delay=321" in text
    assert "delay=1234" not in text


def test_auto_rng_log_adds_timestamp(app, monkeypatch):
    panel = AutoRngPanel()

    class FixedDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 5, 14, 12, 34, 56)

    monkeypatch.setattr("auto_bdsp_rng.ui.auto_rng_panel.datetime", FixedDatetime)

    panel.add_log("第一行\n[01:02:03] 已有时间")

    lines = panel.log_view.toPlainText().splitlines()
    assert lines[0] == "[12:34:56] 第一行"
    assert lines[1] == "[01:02:03] 已有时间"


def test_auto_rng_log_sink_receives_levels_and_cannot_break_ui(app):
    events: list[tuple[str, str]] = []
    panel = AutoRngPanel(run_log_sink=lambda level, message: events.append((level, message)))

    panel.add_log("普通事件")
    panel.apply_progress(AutoRngProgress(phase=AutoRngPhase.FAILED, log_message="流程失败"))
    panel._runner_failed("worker 失败")

    assert events == [
        ("INFO", "普通事件"),
        ("ERROR", "流程失败"),
        ("ERROR", "worker 失败"),
    ]

    def broken_sink(_level: str, _message: str) -> None:
        raise OSError("disk full")

    panel._run_log_sink = broken_sink
    panel.add_log("仍写入界面")

    assert "仍写入界面" in panel.log_view.toPlainText()




def test_auto_rng_worker_emits_progress_and_finished(app):
    progress = AutoRngProgress(phase=AutoRngPhase.COMPLETED, log_message="完成")

    class FakeRunner:
        def __init__(self) -> None:
            self.progress_callback = None
            self.log_callback = None
            self.stopped = False

        def run(self) -> AutoRngProgress:
            self.progress_callback(progress)
            if self.log_callback is not None:
                self.log_callback("完成")
            return progress

        def stop(self) -> None:
            self.stopped = True

    runner = FakeRunner()
    worker = AutoRngWorker(runner)
    progress_events: list[AutoRngProgress] = []
    logs: list[str] = []
    finished: list[AutoRngProgress] = []
    worker.progressChanged.connect(progress_events.append)
    worker.logEmitted.connect(logs.append)
    worker.finished.connect(finished.append)

    worker.run()
    worker.stop()

    assert progress_events == [progress]
    assert logs == []
    assert finished == [progress]
    assert runner.stopped is True


def test_main_window_starts_auto_rng_runner_from_panel_signal(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_script = tmp_path / "BDSP测种.txt"
    advance_script = tmp_path / "bdsp过帧.txt"
    hit_script = tmp_path / "谢米.txt"
    seed_script.write_text("A 100\n", encoding="utf-8")
    advance_script.write_text("_目标帧数 = 100\n", encoding="utf-8")
    hit_script.write_text("_闪帧 = 100\n", encoding="utf-8")
    config = AutoRngConfig(
        script_dir=tmp_path,
        seed_script_path=seed_script,
        advance_script_path=advance_script,
        hit_script_path=hit_script,
    )
    started: list[AutoRngRunner] = []
    window._latest_preview_frame = object()
    monkeypatch.setattr(window.auto_rng_tab, "run_with_runner", started.append)

    window._start_auto_rng(config)

    assert len(started) == 1
    assert isinstance(started[0], AutoRngRunner)
    assert started[0].config == AutoRngConfig(
        script_dir=tmp_path,
        seed_script_path=seed_script,
        advance_script_path=advance_script,
        hit_script_path=hit_script,
        seed_config_path=window._selected_auto_seed_config_path(),
        reidentify_config_path=window._selected_auto_reidentify_config_path(),
    )


def test_main_window_auto_rng_start_prepares_preview_frame_when_inactive(app, tmp_path, monkeypatch):
    window = MainWindow()
    config = AutoRngConfig(script_dir=tmp_path)
    started: list[AutoRngRunner] = []
    preview_updates: list[str] = []

    def fake_update_preview() -> None:
        preview_updates.append("updated")
        window._latest_preview_frame = object()

    monkeypatch.setattr(window, "_update_preview_frame", fake_update_preview)
    monkeypatch.setattr(window, "_ensure_bridge_connected", lambda: True)
    monkeypatch.setattr(window.auto_rng_tab, "run_with_runner", started.append)

    window._start_auto_rng(config)

    assert not window._preview_timer.isActive()
    assert preview_updates == ["updated"]
    assert len(started) == 1


def test_main_window_auto_rng_services_search_with_bdsp_snapshot(app, tmp_path):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("2")
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path, max_advances=2))

    candidates = services.search_candidates(AutoRngSeedResult(seed=window._current_seed_pair()))

    assert [state.advances for state in candidates] == [0, 1, 2]
    assert "搜索目标" in window.auto_rng_tab.log_view.toPlainText()


def test_zoom_recovery_pauses_preview_and_waits_before_capturing(app, monkeypatch):
    window = MainWindow()
    capture_config = BlinkCaptureConfig(Path("eye.png"), (0, 0, 1, 1), monitor_window=False)
    events = []
    window._preview_timer.start()

    monkeypatch.setattr(main_window_module.time, "sleep", lambda seconds: events.append(("wait", seconds)))
    monkeypatch.setattr(main_window_module, "capture_preview_frame", lambda _config: events.append(("capture",)) or object())

    def fake_recover(capture_frame, _run_script_text, *, should_stop):
        assert not window._preview_timer.isActive()
        assert should_stop() is False
        capture_frame()
        events.append(("recover",))
        return True

    monkeypatch.setattr(main_window_module, "recover_zoom_overlay", fake_recover)

    assert window._recover_zoom_mode_with_preview_paused(capture_config, lambda _text, _name: None) is True
    assert events == [("wait", 0.3), ("capture",), ("recover",)]
    assert window._preview_timer.isActive()


def test_auto_rng_zoom_recovery_service_uses_preview_safe_recovery(app, tmp_path, monkeypatch):
    window = MainWindow()
    capture_config = BlinkCaptureConfig(Path("eye.png"), (0, 0, 1, 1), monitor_window=False)
    tracking_config = ProjectXsTrackingConfig(Path("seed.json"), capture_config)
    calls = []
    monkeypatch.setattr(main_window_module, "load_project_xs_config", lambda *_args, **_kwargs: tracking_config)
    monkeypatch.setattr(
        window,
        "_recover_zoom_mode_with_preview_paused",
        lambda actual_capture, _run_script: calls.append(actual_capture) or True,
    )

    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    assert services.recover_zoom_mode() is True
    assert calls == [capture_config]


def test_auto_tid_zoom_recovery_service_uses_preview_safe_recovery(app, tmp_path, monkeypatch):
    window = MainWindow()
    capture_config = BlinkCaptureConfig(Path("eye.png"), (0, 0, 1, 1), monitor_window=False)
    tracking_config = ProjectXsTrackingConfig(Path("seed.json"), capture_config)
    calls = []
    monkeypatch.setattr(main_window_module, "load_project_xs_config", lambda *_args, **_kwargs: tracking_config)
    monkeypatch.setattr(
        window,
        "_recover_zoom_mode_with_preview_paused",
        lambda actual_capture, _run_script: calls.append(actual_capture) or True,
    )

    services = window._build_auto_tid_rng_services(AutoTidRngConfig(script_dir=tmp_path))

    assert services.recover_zoom_mode() is True
    assert calls == [capture_config]


def test_main_window_auto_rng_services_search_uses_multi_targets(app, tmp_path, monkeypatch):
    window = MainWindow()
    _set_bdsp_seed(window)
    window.max_advances.setText("2")
    window.auto_rng_tab.max_advances.setValue(9)
    # 直接设置 _targets 列表模拟已添加目标
    from auto_bdsp_rng.data import get_static_encounters
    from auto_bdsp_rng.gen8_static import StateFilter
    records = [r for r in get_static_encounters() if r.description == "Shaymin"]
    if records:
        sf = StateFilter(height_min=0, height_max=0)
        window.auto_rng_tab._targets = [(records[0], sf, "square")]
    captured = []

    def fake_generate(criteria, targets):
        captured.append((criteria, targets))
        return []

    monkeypatch.setattr(main_window_module, "generate_static_candidates_multi", fake_generate)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path, max_advances=9))

    services.search_candidates(AutoRngSeedResult(seed=window._current_seed_pair()))

    assert len(captured) == 1
    criteria, targets = captured[0]
    assert criteria.record.description == "Shaymin"
    assert criteria.max_advances == 9
    assert targets[0].shiny_mode == "square"
    assert targets[0].state_filter.height_min == 0
    assert targets[0].state_filter.height_max == 0


def test_main_window_auto_rng_capture_service_uses_project_xs(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)
    observation = BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=100.0)
    captured_counts: list[int] = []
    warmup_windows: list[float | None] = []
    recovered_observations: list[BlinkObservation] = []

    def fake_capture(config, *_args, **kwargs):
        captured_counts.append(config.blink_count)
        warmup_windows.append(kwargs.get("discard_first_blink_within_seconds"))
        return observation

    def fake_recover(actual_observation, npc):
        recovered_observations.append(actual_observation)
        return SimpleNamespace(state=seed_state, advances=0)

    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(main_window_module, "recover_seed_from_observation", fake_recover)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    result = services.capture_seed()

    assert result.seed == seed_state
    assert result.current_advances == 0
    assert result.seed_text == "1111111122222222 3333333344444444"
    assert captured_counts == [40]
    assert warmup_windows == [1.0]
    assert recovered_observations[0].blinks == (1, 0)
    assert recovered_observations[0].intervals == (12, 24)


def test_main_window_auto_rng_capture_syncs_seed_tab_and_bdsp_results(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)
    observation = BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=100.0)
    displayed_frames: list[object] = []
    generated = []

    def fake_capture(_config, **kwargs):
        kwargs["progress_callback"](3, 40)
        kwargs["frame_callback"]("frame-1")
        return observation

    def fake_generate(criteria):
        generated.append(criteria)
        return []

    monkeypatch.setattr(window, "_display_frame", lambda frame: displayed_frames.append(frame))
    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(
        main_window_module,
        "recover_seed_from_observation",
        lambda actual_observation, npc: SimpleNamespace(state=seed_state, advances=0),
    )
    monkeypatch.setattr(main_window_module, "generate_static_candidates", fake_generate)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path, max_advances=9))

    result = services.capture_seed()
    QApplication.processEvents()

    assert result.seed == seed_state
    assert displayed_frames == ["frame-1"]
    assert window.progress_value.text() == "3/40"
    assert [box.text() for box in window.seed32_inputs] == ["11111111", "22222222", "33333333", "44444444"]
    assert [box.text() for box in window.bdsp_seed64_inputs] == ["1111111122222222", "3333333344444444"]
    _, target_filter, _ = window.auto_rng_tab.targets()[0]
    assert window.iv_min[0].text() == str(target_filter.iv_min[0])
    assert window.iv_max[0].text() == str(target_filter.iv_max[0])
    assert window.height_max.text() == str(target_filter.height_max)
    assert len(generated) >= 1
    assert generated[-1].seed == seed_state.to_seed_pair64()
    assert generated[-1].state_filter.iv_min[0] == target_filter.iv_min[0]


def test_main_window_auto_rng_capture_stop_does_not_show_failure_dialog(app, tmp_path, monkeypatch):
    window = MainWindow()
    warnings: list[tuple[str, str]] = []

    def fake_capture(_config, **_kwargs):
        window._capture_cancel.set()
        raise ProjectXsIntegrationError("Blink capture stopped")

    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda _parent, title, text: warnings.append((title, text)),
    )
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    with pytest.raises(ProjectXsIntegrationError, match="Blink capture stopped"):
        services.capture_seed()

    assert warnings == []


def test_main_window_auto_rng_capture_preview_controls_run_on_ui_thread(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)
    observation = SimpleNamespace(offset_time=100.0)
    ui_thread = window.thread()
    touched: list[str] = []

    def assert_ui_thread(name: str) -> None:
        assert QThread.currentThread() == ui_thread
        touched.append(name)

    window._preview_timer.start()
    original_stop_preview = window._preview_timer.stop
    original_start_preview = window._preview_timer.start

    def stop_preview() -> None:
        assert_ui_thread("stop_preview")
        original_stop_preview()

    def start_preview() -> None:
        assert_ui_thread("start_preview")
        original_start_preview()

    monkeypatch.setattr(window._preview_timer, "stop", stop_preview)
    monkeypatch.setattr(window._preview_timer, "start", start_preview)
    monkeypatch.setattr(window.preview_label, "clear", lambda: assert_ui_thread("clear_preview"))
    monkeypatch.setattr(window.preview_button, "setText", lambda _text: assert_ui_thread("set_preview_text"))
    monkeypatch.setattr(window.preview_label, "setText", lambda _text: assert_ui_thread("set_preview_label"))
    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", lambda *_args, **_kwargs: observation)
    monkeypatch.setattr(
        main_window_module,
        "recover_seed_from_observation",
        lambda actual_observation, npc: SimpleNamespace(state=seed_state, advances=0),
    )
    monkeypatch.setattr(main_window_module, "generate_static_candidates", lambda _criteria: [])
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    class CaptureRunner:
        def run(self):
            services.capture_seed()
            return AutoRngProgress(phase=AutoRngPhase.IDLE)

        def stop(self) -> None:
            pass

    window.auto_rng_tab.run_with_runner(CaptureRunner())
    deadline = time.monotonic() + 2.0
    while window.auto_rng_tab._runner_thread is not None and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()

    assert window.auto_rng_tab._runner_thread is None
    assert "stop_preview" in touched
    assert "start_preview" in touched
    window._preview_timer.stop()
    window.close()


def test_main_window_auto_rng_reidentify_service_uses_project_xs(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    calls: list[SeedState32] = []
    capture_counts: list[int] = []
    warmup_windows: list[float | None] = []
    passed_observations: list[BlinkObservation] = []
    observation = BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=100.0)

    def fake_capture(config, *_args, **kwargs):
        capture_counts.append(config.blink_count)
        warmup_windows.append(kwargs.get("discard_first_blink_within_seconds"))
        return observation

    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 105.0)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)

    def fake_reidentify(current_state, actual_observation, **_kwargs):
        calls.append(current_state)
        passed_observations.append(actual_observation)
        return SimpleNamespace(state=seed_state, advances=42)

    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation", fake_reidentify)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    result = services.reidentify(AutoRngSeedResult(seed=SeedPair64(0x1111111122222222, 0x3333333344444444)))
    QApplication.processEvents()

    assert calls == [SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)]
    assert result.seed == SeedPair64(0x1111111122222222, 0x3333333344444444)
    assert result.current_advances == 47
    assert capture_counts == [7]
    assert warmup_windows == [1.0]
    assert passed_observations[0].blinks == (1, 0)
    assert passed_observations[0].intervals == (12, 24)
    assert int(window.advances_value.text()) >= 47
    assert not window._advance_timer.isActive()


def test_main_window_auto_rng_reidentify_uses_hint_limited_regular_search_range(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    observation = BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=0.0)
    search_ranges: list[tuple[int, int, int]] = []

    def fake_load_config(path, blink_count):
        return ProjectXsTrackingConfig(
            source_path=tmp_path / Path(str(path)).name,
            capture=BlinkCaptureConfig(
                eye_image_path=tmp_path / "eye.png",
                roi=(0, 0, 1, 1),
                blink_count=blink_count,
            ),
            npc=2,
        )

    def fake_capture(config, *_args, **_kwargs):
        return observation

    def fake_reidentify(current_state, _observation, **kwargs):
        search_ranges.append((kwargs["search_min"], kwargs["search_max"], kwargs["npc"]))
        return ProjectXsReidentifyResult(state=seed_state, observation=observation, advances=50_100)

    monkeypatch.setattr(main_window_module, "load_project_xs_config", fake_load_config)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation", fake_reidentify)

    services = window._build_auto_rng_services(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_config_path=str(tmp_path / "seed.json"),
            reidentify_config_path=str(tmp_path / "exit.json"),
            max_advances=2_000_000,
        )
    )

    services.reidentify(
        AutoRngSeedResult(
            seed=SeedPair64(0x1111111122222222, 0x3333333344444444),
            expected_advances_hint=50_000,
        )
    )

    assert search_ranges == [(40_000, 70_000, 2)]


def test_main_window_auto_rng_reidentify_uses_hint_limited_noisy_search_window(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    observation = BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=0.0)
    search_ranges: list[tuple[int, int]] = []

    def fake_load_config(path, blink_count):
        return ProjectXsTrackingConfig(
            source_path=tmp_path / Path(str(path)).name,
            capture=BlinkCaptureConfig(
                eye_image_path=tmp_path / "eye.png",
                roi=(0, 0, 1, 1),
                blink_count=blink_count,
            ),
            npc=2,
            pokemon_npc=1,
        )

    def fake_capture(config, *_args, **_kwargs):
        return observation

    def fake_noisy(current_state, _observation, **kwargs):
        search_ranges.append((kwargs["search_min"], kwargs["search_max"]))
        return ProjectXsReidentifyResult(state=seed_state, observation=observation, advances=50_100)

    monkeypatch.setattr(main_window_module, "load_project_xs_config", fake_load_config)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation_noisy", fake_noisy)

    services = window._build_auto_rng_services(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_config_path=str(tmp_path / "seed.json"),
            reidentify_config_path=str(tmp_path / "exit.json"),
            max_advances=2_000_000,
        )
    )

    services.reidentify(
        AutoRngSeedResult(
            seed=SeedPair64(0x1111111122222222, 0x3333333344444444),
            expected_advances_hint=50_000,
            after_exit_reseed=True,
        )
    )

    assert search_ranges == [(40_000, 30_000)]


def test_main_window_auto_rng_exit_reidentify_caps_noisy_search_without_hint(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    observation = BlinkObservation.from_sequences([1, 0], [12, 24], offset_time=0.0)
    search_ranges: list[tuple[int, int]] = []

    def fake_load_config(path, blink_count):
        return ProjectXsTrackingConfig(
            source_path=tmp_path / Path(str(path)).name,
            capture=BlinkCaptureConfig(
                eye_image_path=tmp_path / "eye.png",
                roi=(0, 0, 1, 1),
                blink_count=blink_count,
            ),
            pokemon_npc=1,
        )

    def fake_capture(config, *_args, **_kwargs):
        return observation

    def fake_noisy(current_state, _observation, **kwargs):
        search_ranges.append((kwargs["search_min"], kwargs["search_max"]))
        return ProjectXsReidentifyResult(state=seed_state, observation=observation, advances=42)

    monkeypatch.setattr(main_window_module, "load_project_xs_config", fake_load_config)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation_noisy", fake_noisy)

    services = window._build_auto_rng_services(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_config_path=str(tmp_path / "seed.json"),
            reidentify_config_path=str(tmp_path / "exit.json"),
            max_advances=2_000_000,
        )
    )

    services.reidentify_exit(AutoRngSeedResult(seed=SeedPair64(0x1111111122222222, 0x3333333344444444)))

    assert search_ranges == [(0, 100_000)]


def test_main_window_auto_rng_reidentify_after_exit_uses_reidentify_config(app, tmp_path, monkeypatch):
    window = MainWindow()
    observation = SimpleNamespace(offset_time=100.0)
    loaded: list[tuple[str, int]] = []
    capture_counts: list[int] = []
    normal_calls: list[SeedState32] = []
    noisy_calls: list[SeedState32] = []

    def fake_load_config(path, blink_count):
        loaded.append((str(path), blink_count))
        return ProjectXsTrackingConfig(
            source_path=tmp_path / Path(str(path)).name,
            capture=BlinkCaptureConfig(
                eye_image_path=tmp_path / "eye.png",
                roi=(0, 0, 1, 1),
                blink_count=blink_count,
            ),
            npc=7,
            pokemon_npc=2,
            timeline_npc=3,
            white_delay=0.5,
            advance_delay=11,
            advance_delay_2=13,
        )

    def fake_capture(config, *_args, **_kwargs):
        capture_counts.append(config.blink_count)
        return observation

    def fake_reidentify(current_state, _observation, **_kwargs):
        normal_calls.append(current_state)
        return ProjectXsReidentifyResult(state=current_state, observation=observation, advances=42)

    def fake_noisy(current_state, _observation, **_kwargs):
        noisy_calls.append(current_state)
        return ProjectXsReidentifyResult(state=current_state, observation=observation, advances=42)

    monkeypatch.setattr(main_window_module, "load_project_xs_config", fake_load_config)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation", fake_reidentify)
    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation_noisy", fake_noisy)
    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 105.0)

    services = window._build_auto_rng_services(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_config_path=str(tmp_path / "seed.json"),
            reidentify_config_path=str(tmp_path / "exit.json"),
        )
    )

    result = services.reidentify(
        AutoRngSeedResult(
            seed=SeedPair64(0x1111111122222222, 0x3333333344444444),
            after_exit_reseed=True,
        )
    )

    assert loaded == [
        (str(tmp_path / "seed.json"), 40),
        (str(tmp_path / "exit.json"), 20),
    ]
    assert capture_counts == [20]
    assert normal_calls == []
    assert noisy_calls == [SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)]
    assert result.current_advances == 82
    assert result.npc == 7
    assert result.after_exit_reseed is True
    assert result.advance_mode == "timeline"
    assert result.pokemon_npc == 2
    assert result.timeline_npc == 3
    assert result.white_delay == 0.5
    assert result.advance_delay == 11
    assert result.advance_delay_2 == 13


def test_main_window_auto_rng_exit_reidentify_uses_reidentify_config(app, tmp_path, monkeypatch):
    window = MainWindow()
    observation = SimpleNamespace(offset_time=100.0)
    loaded: list[tuple[str, int]] = []
    capture_counts: list[int] = []
    noisy_calls: list[SeedState32] = []

    def fake_load_config(path, blink_count):
        loaded.append((str(path), blink_count))
        return ProjectXsTrackingConfig(
            source_path=tmp_path / Path(str(path)).name,
            capture=BlinkCaptureConfig(
                eye_image_path=tmp_path / "eye.png",
                roi=(0, 0, 1, 1),
                blink_count=blink_count,
            ),
            npc=0,
            pokemon_npc=2,
            timeline_npc=3,
            white_delay=0.5,
            advance_delay=11,
            advance_delay_2=13,
        )

    def fake_capture(config, *_args, **_kwargs):
        capture_counts.append(config.blink_count)
        return observation

    def fake_noisy(current_state, _observation, **_kwargs):
        noisy_calls.append(current_state)
        return ProjectXsReidentifyResult(state=current_state, observation=observation, advances=42)

    monkeypatch.setattr(main_window_module, "load_project_xs_config", fake_load_config)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)
    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation_noisy", fake_noisy)
    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 105.0)

    services = window._build_auto_rng_services(
        AutoRngConfig(
            script_dir=tmp_path,
            seed_config_path=str(tmp_path / "seed.json"),
            reidentify_config_path=str(tmp_path / "exit.json"),
        )
    )

    result = services.reidentify_exit(
        AutoRngSeedResult(seed=SeedPair64(0x1111111122222222, 0x3333333344444444))
    )
    QApplication.processEvents()

    assert loaded == [
        (str(tmp_path / "seed.json"), 40),
        (str(tmp_path / "exit.json"), 20),
    ]
    assert capture_counts == [20]
    assert noisy_calls == [SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)]
    assert result.current_advances == 47
    assert result.npc == 0
    assert result.advance_mode == "timeline"
    assert result.pokemon_npc == 2
    assert result.timeline_npc == 3
    assert result.white_delay == 0.5
    assert result.advance_delay == 11
    assert result.advance_delay_2 == 13


def test_main_window_auto_rng_run_script_service_uses_bridge(app, tmp_path, monkeypatch):
    window = MainWindow()
    calls: list[tuple[str, str]] = []

    class FakeBackend:
        def run_script_text(self, script_text: str, name: str) -> str:
            calls.append((script_text, name))
            return "ok"

    window.easycon_tab.backend_mode.setCurrentIndex(0)
    window.easycon_tab.bridge_status = EasyConStatus.BRIDGE_CONNECTED
    monkeypatch.setattr(window.easycon_tab, "_ensure_bridge_backend", lambda: FakeBackend())
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    assert services.run_script_text("A 100", "hit.txt") == "ok"
    assert calls == [("A 100", "hit.txt")]


def test_main_window_auto_rng_cli_settles_keep_awake_before_script(app, tmp_path, monkeypatch):
    window = MainWindow()
    events: list[str] = []

    class FakeCliBackend:
        def run_script_text(self, script_text: str, name: str, *, port: str) -> str:
            events.append(f"run:{port}:{name}:{script_text}")
            return "ok"

    cli_index = window.easycon_tab.backend_mode.findData("cli")
    window.easycon_tab.backend_mode.setCurrentIndex(cli_index)
    window.easycon_tab.port_combo.addItem("COM7")
    window.easycon_tab.port_combo.setCurrentText("COM7")
    monkeypatch.setattr(
        window.easycon_tab,
        "prepare_for_external_cli_script",
        lambda: events.append("settled") or True,
    )
    monkeypatch.setattr(main_window_module, "CliEasyConBackend", FakeCliBackend)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    assert services.run_script_text("A 100", "hit.txt") == "ok"
    assert events == ["settled", "run:COM7:hit.txt:A 100"]


def test_main_window_auto_rng_run_script_syncs_easycon_status_and_output(app, tmp_path, monkeypatch):
    window = MainWindow()
    started = datetime(2026, 5, 8, 12, 0, 0)
    ended = datetime(2026, 5, 8, 12, 0, 1)

    class FakeBackend:
        def run_script_text(self, script_text: str, name: str) -> EasyConRunResult:
            return EasyConRunResult(
                status=EasyConStatus.COMPLETED,
                exit_code=0,
                started_at=started,
                ended_at=ended,
                script_path=tmp_path / name,
                port="COM1",
                stdout="done\n",
            )

    window.easycon_tab.backend_mode.setCurrentIndex(0)
    window.easycon_tab.bridge_status = EasyConStatus.BRIDGE_CONNECTED
    monkeypatch.setattr(window.easycon_tab, "_ensure_bridge_backend", lambda: FakeBackend())
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    services.run_script_text("A 100", "hit.txt")
    QApplication.processEvents()

    assert window.easycon_tab.task_state_text == "已完成"
    assert "自动流程运行脚本: hit.txt" in window.easycon_tab.log_view.toPlainText()
    assert "done" in window.easycon_tab.log_view.toPlainText()
    assert window.easycon_tab.easycon_status.currentMessage() == "已完成，连接保持"


def test_main_window_auto_rng_run_script_raises_on_easycon_failure(app, tmp_path, monkeypatch):
    window = MainWindow()
    started = datetime(2026, 5, 24, 12, 0, 0)
    ended = datetime(2026, 5, 24, 12, 0, 1)
    finished_results: list[object] = []
    failed_messages: list[str] = []
    window.autoScriptFinished.connect(finished_results.append)
    window.autoScriptFailed.connect(failed_messages.append)

    class FakeBackend:
        def run_script_text(self, script_text: str, name: str) -> EasyConRunResult:
            return EasyConRunResult(
                status=EasyConStatus.FAILED,
                exit_code=1,
                started_at=started,
                ended_at=ended,
                script_path=tmp_path / name,
                port="COM7",
                stdout="CLI 模式[right_press]: 准备\n",
                stderr="串口连接失败: COM7\n",
            )

    window.easycon_tab.backend_mode.setCurrentIndex(0)
    window.easycon_tab.bridge_status = EasyConStatus.BRIDGE_CONNECTED
    monkeypatch.setattr(window.easycon_tab, "_ensure_bridge_backend", lambda: FakeBackend())
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    with pytest.raises(RuntimeError, match="串口连接失败"):
        services.run_script_text("A 100", "BDSP测种.txt")
    QApplication.processEvents()

    assert "串口连接失败" in window.easycon_tab.log_view.toPlainText()
    assert len(finished_results) == 1
    assert failed_messages == []


def test_main_window_applies_selected_roi(app, monkeypatch):
    window = MainWindow()

    class FakeImage:
        shape = (10, 12)

    fake_cv2 = type("FakeCv2", (), {"IMREAD_GRAYSCALE": 0, "imread": staticmethod(lambda *_args: FakeImage())})
    monkeypatch.setitem(__import__("sys").modules, "cv2", fake_cv2)

    window.apply_selected_roi((20, 30, 40, 50))

    assert window.x.text() == "20"
    assert window.y.text() == "30"
    assert window.w.text() == "40"
    assert window.h.text() == "50"


def test_preview_selection_cancel_keeps_previous_roi(app, monkeypatch):
    window = MainWindow()
    window.x.setText("1")
    window.y.setText("2")
    window.w.setText("30")
    window.h.setText("40")
    window._selection_mode = "roi"
    window._roi_before_selection = (1, 2, 30, 40)
    window.preview_label.set_selection_enabled(True)
    monkeypatch.setattr(window, "_confirm_preview_selection", lambda _roi: False)

    window._handle_preview_selection((10, 20, 50, 60))

    assert (window.x.text(), window.y.text(), window.w.text(), window.h.text()) == ("1", "2", "30", "40")
    assert window._selection_mode is None


def test_preview_selection_cancel_keeps_previous_eye_path(app, monkeypatch, tmp_path):
    window = MainWindow()
    old_eye = tmp_path / "old_eye.png"
    window._eye_image_path = old_eye
    window._selection_mode = "eye"
    window.preview_label.set_selection_enabled(True)
    called = []
    monkeypatch.setattr(window, "_confirm_preview_selection", lambda _roi: False)
    monkeypatch.setattr(window, "apply_selected_eye", lambda _roi: called.append(_roi))

    window._handle_preview_selection((10, 20, 30, 40))

    assert window._eye_image_path == old_eye
    assert called == []
    assert window._selection_mode is None


def test_preview_label_stores_ocr_overlay_region(app):
    window = MainWindow()

    window.preview_label.set_ocr_overlay("nature", OcrRegion(10, 20, 30, 40))

    assert window.preview_label._ocr_overlay_field == "nature"
    assert window.preview_label._ocr_overlay_region == OcrRegion(10, 20, 30, 40)


def test_preview_label_paints_drag_and_ocr_overlay_with_one_painter(app, monkeypatch):
    window = MainWindow()
    label = window.preview_label
    label.resize(200, 120)
    label.set_image_geometry(200, 120, label.rect())
    label.set_selection_enabled(True)
    label._drag_start = QPoint(10, 10)
    label._drag_current = QPoint(80, 50)
    label.set_ocr_overlay("nature", OcrRegion(20, 20, 40, 30))
    active = {"count": 0}

    class FakePainter:
        def __init__(self, _device):
            if active["count"]:
                raise RuntimeError("nested painter")
            active["count"] += 1

        def setPen(self, _pen):
            pass

        def drawRect(self, _rect):
            pass

        def end(self):
            active["count"] = 0

        def __del__(self):
            active["count"] = 0

    monkeypatch.setattr(main_window_module, "QPainter", FakePainter)

    label.paintEvent(QPaintEvent(label.rect()))


def test_ocr_region_selection_confirm_emits_field_and_roi(app, monkeypatch):
    window = MainWindow()
    emitted = []
    window.ocrRegionSelected.connect(lambda field, roi: emitted.append((field, roi)))
    window._selection_mode = "ocr_region"
    window._ocr_selection_field = "characteristic"
    window.preview_label.set_selection_enabled(True)
    monkeypatch.setattr(window, "_confirm_preview_selection", lambda _roi: True)

    window._handle_preview_selection((10, 20, 30, 40))

    assert emitted == [("characteristic", (10, 20, 30, 40))]
    assert window._selection_mode is None
    assert window._ocr_selection_field is None
    assert window.preview_label._ocr_overlay_region == OcrRegion(10, 20, 30, 40)


def test_ocr_region_selection_cancel_does_not_emit(app, monkeypatch):
    window = MainWindow()
    emitted = []
    window.ocrRegionSelected.connect(lambda field, roi: emitted.append((field, roi)))
    window._selection_mode = "ocr_region"
    window._ocr_selection_field = "nature"
    window.preview_label.set_selection_enabled(True)
    monkeypatch.setattr(window, "_confirm_preview_selection", lambda _roi: False)

    window._handle_preview_selection((10, 20, 30, 40))

    assert emitted == []
    assert window._selection_mode is None
    assert window._ocr_selection_field is None
