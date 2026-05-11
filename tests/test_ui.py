from __future__ import annotations

import time
from datetime import datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from auto_bdsp_rng.blink_detection import BlinkObservation, ProjectXsReidentifyResult, SeedState32
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QFileDialog, QGridLayout, QGroupBox, QLabel, QPushButton

from auto_bdsp_rng.automation.auto_rng import AutoRngConfig, AutoRngPhase, AutoRngProgress, AutoRngSeedResult, AutoRngTarget
from auto_bdsp_rng.automation.auto_rng.runner import AutoRngRunner
from auto_bdsp_rng.automation.easycon import EasyConRunResult, EasyConStatus
from auto_bdsp_rng.gen8_static import State8
from auto_bdsp_rng.rng_core import SeedPair64
from auto_bdsp_rng.ui import MainWindow
import auto_bdsp_rng.ui.main_window as main_window_module
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel, AutoRngWorker


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _set_bdsp_seed(window: MainWindow) -> None:
    window.bdsp_seed64_inputs[0].setText("123456789ABCDEF0")
    window.bdsp_seed64_inputs[1].setText("1111111122222222")


def test_main_window_generates_static_results(app):
    window = MainWindow()

    assert [window.tabs.tabText(index) for index in range(window.tabs.count())] == [
        "Seed 捕捉",
        "定点数据区",
        "伊机控",
        "自动定点乱数",
    ]
    assert window.tabs.tabText(window.tabs.currentIndex()) == "Seed 捕捉"

    window.tabs.setCurrentWidget(window.bdsp_tab)
    _set_bdsp_seed(window)
    window.max_advances.setText("2")
    window.generate_results()

    assert window.table.rowCount() == 3
    assert window.result_count.text() == "3 条结果"
    assert window.table.item(0, 0).text() == "0"
    assert window.table.item(0, 1).text()


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


def test_reidentify_updates_seed_and_refreshes_results(app, monkeypatch):
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
    for box, text in zip(window.seed32_inputs, ["12345678", "9ABCDEF0", "11111111", "22222222"]):
        box.setText(text)

    window.reidentify_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert [box.text() for box in window.seed32_inputs] == ["AAAAAAAA", "BBBBBBBB", "CCCCCCCC", "DDDDDDDD"]
    assert window.seed64_outputs[0].text() == "AAAAAAAABBBBBBBB"
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
    for box, text in zip(window.seed32_inputs, ["12345678", "9ABCDEF0", "11111111", "22222222"]):
        box.setText(text)
    window.reidentify_1_pk_npc.setChecked(True)

    window.reidentify_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert capture_counts == [20]
    assert [box.text() for box in window.seed32_inputs] == ["AAAAAAAA", "BBBBBBBB", "CCCCCCCC", "DDDDDDDD"]
    assert window.advances_value.text() == "43"


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

    window.capture_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert window._preview_timer.isActive()
    assert window.preview_button.text() == window._text("stop_preview")


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

    window.reidentify_seed()
    window._capture_thread.join(timeout=2)
    window._poll_capture_thread()

    assert window._preview_timer.isActive()
    assert window.preview_button.text() == window._text("stop_preview")


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


def test_main_window_loads_project_xs_config_fields(app):
    window = MainWindow()
    index = window.config_combo.findText("config_bebe.json")
    window.config_combo.setCurrentIndex(index)

    assert window.window_prefix.text() == "PotPlayer"
    assert window.monitor_window.isChecked() is True
    assert window.x.text() == "516"
    assert window.y.text() == "377"
    assert window.w.text() == "38"
    assert window.h.text() == "53"
    assert window.threshold.value() == 0.7


def test_main_window_can_switch_language(app):
    window = MainWindow()

    window.language_combo.setCurrentIndex(window.language_combo.findData("en"))

    assert window.capture_group.title() == "Blink capture"
    assert window.capture_button.text() == "Capture Seed"
    assert window.tabs.tabText(2) == "EasyCon"


def test_main_window_has_auto_rng_tab(app):
    window = MainWindow()

    assert window.tabs.count() == 4
    assert window.tabs.tabText(3) == "自动定点乱数"


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
    assert config.reseed_threshold_frames == 990_000
    assert config.min_final_flash_frames == 5


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


def test_auto_rng_panel_has_editable_target_form_and_no_old_main_regions(app):
    panel = AutoRngPanel()
    group_titles = {group.title() for group in panel.findChildren(QGroupBox)}

    assert "定点目标 / 存档信息 / 个体筛选" not in group_titles
    assert "候选结果" not in group_titles
    assert "目标精灵设置" in group_titles
    assert not hasattr(panel, "candidate_table")
    assert not hasattr(panel, "search_target_summary")

    target_form = panel.target_form
    assert target_form.category_combo.count() > 0
    assert target_form.encounter_combo.count() > 0
    assert target_form.iv_min[0].value() == 0
    assert target_form.iv_max[0].value() == 31
    assert target_form.height_min.value() == 0
    assert target_form.height_max.value() == 255
    assert target_form.shiny_filter.findText("Square") >= 0
    assert not hasattr(panel, "parameter_preview")
    assert not hasattr(panel, "preview_button")
    assert panel.log_view.isReadOnly() is True
    labels = {label.text() for label in panel.findChildren(QLabel)}
    assert "重新测 seed 阈值" not in labels
    assert "最小 final flash frames" not in labels


def test_auto_rng_summary_uses_chinese_labels_and_hides_seed_and_locked_target(app):
    panel = AutoRngPanel()
    group_titles = {group.title() for group in panel.findChildren(QGroupBox)}
    visible_labels = {label.text() for label in panel.findChildren(QLabel)}

    assert "锁定目标" in group_titles
    assert "Seed" not in visible_labels
    assert "触发帧" not in visible_labels
    assert "剩余" not in visible_labels
    assert "raw target" not in visible_labels
    assert "trigger advances" not in visible_labels
    assert "current advances" not in visible_labels
    assert "remaining_to_trigger" not in visible_labels
    assert "final flash_frames" not in visible_labels
    assert {"当前循环", "当前阶段", "原始目标帧", "delay", "当前帧", "最终闪帧"} <= visible_labels
    assert not hasattr(panel, "summary_seed")
    assert not hasattr(panel, "summary_trigger")
    assert not hasattr(panel, "summary_remaining")
    assert not hasattr(panel, "summary_target")
    assert isinstance(panel.locked_target_view.layout(), QGridLayout)
    assert panel.locked_target_view.layout().itemAtPosition(0, 0).widget().text() == "状态"
    assert panel.locked_target_view.layout().itemAtPosition(1, 0).widget().text() == "未锁定"
    assert panel.locked_target_view.layout().itemAtPosition(0, 1).widget().text() == "PID"
    assert panel.locked_target_view.layout().itemAtPosition(1, 1).widget().text() == "-"
    assert 70 <= panel.summary_group.maximumHeight() <= 110
    assert panel.summary_group.maximumWidth() == 16777215
    assert panel.target_form_group.maximumHeight() <= 390
    assert panel.target_form.settings_group.maximumWidth() <= 260


def test_auto_rng_page_uses_compact_toolbar_and_fixed_left_sidebar(app):
    panel = AutoRngPanel()

    assert 56 <= panel.toolbar.maximumHeight() <= 64
    assert panel.mode_combo.width() == 110
    assert panel.loop_count.width() == 80
    assert panel.start_button.height() == 34
    assert panel.stop_button.height() == 34
    assert panel.config_panel.minimumWidth() == 300
    assert panel.config_panel.minimumWidth() == panel.config_panel.maximumWidth()
    assert panel.strategy_group.maximumHeight() == 16777215  # 未设固定高度
    assert panel.script_group.maximumHeight() == 16777215  # 未设固定高度
    assert panel.max_advances.width() <= 150
    assert panel.seed_script_combo.width() <= 170
    assert panel.refresh_scripts_button.width() <= 250
    assert not any(button.text() == "参数预览" for button in panel.findChildren(QPushButton))


def test_auto_rng_target_form_hides_iv_calculator_and_stats_toggle(app):
    panel = AutoRngPanel()

    assert panel.target_form.iv_calculator_button.isHidden()
    assert panel.target_form.show_stats_check.isHidden()


def test_auto_rng_stop_button_requests_runner_stop_immediately(app):
    panel = AutoRngPanel()
    stops: list[str] = []
    emissions: list[str] = []
    panel.stopRequested.connect(lambda: emissions.append("emitted"))
    panel._runner_worker = SimpleNamespace(stop=lambda: stops.append("stopped"))

    panel.stop_button.click()

    assert stops == ["stopped"]
    assert emissions == ["emitted"]


def test_auto_rng_locked_target_view_shows_single_target_details(app):
    panel = AutoRngPanel()
    state = State8(
        advances=1000,
        ec=0x45EBFFE9,
        sidtid=0,
        pid=0xB3B242E2,
        ivs=(4, 29, 5, 0, 18, 14),
        ability=1,
        gender=0,
        level=5,
        nature=0,
        shiny=2,
        height=33,
        weight=155,
    )

    panel.apply_progress(
        AutoRngProgress(
            phase=AutoRngPhase.DECIDE_ADVANCE,
            locked_target=AutoRngTarget(raw_target_advances=1000, state=state),
            raw_target_advances=1000,
            fixed_delay=100,
            trigger_advances=900,
            current_advances=0,
            remaining_to_trigger=900,
        )
    )

    values = panel.locked_target_view.values
    assert values["Status"].text() == "已锁定"
    assert values["PID"].text() == "B3B242E2"
    assert values["Shiny"].text() == "方闪"
    assert values["Nature"].text() == "勤奋"
    assert values["Ability"].text() == "1"
    assert values["IVs"].text() == "HP 4 / 攻击 29 / 防御 5 / 特攻 0 / 特防 18 / 速度 14"
    assert values["Gender"].text() == "雄"
    assert values["Height"].text() == "33"
    assert values["Weight"].text() == "155"
    assert "Characteristic" not in values
    assert "Adv" not in values
    assert "EC" not in values
    assert "raw / trigger / delay" not in values
    assert "current / remaining / final" not in values

    locked_layout = panel.locked_target_view.layout()
    assert locked_layout.itemAtPosition(0, 6).widget().text() == "IVs"
    assert locked_layout.columnStretch(6) > locked_layout.columnStretch(1)
    for column in range(locked_layout.columnCount()):
        label_item = locked_layout.itemAtPosition(0, column)
        value_item = locked_layout.itemAtPosition(1, column)
        if label_item is not None:
            assert label_item.widget().objectName() == "MutedLabel"
        if value_item is not None:
            assert value_item.widget().objectName() == "Badge"


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

    assert panel.status_badge.text() == "RunHitScript"
    assert panel.summary_loop.text() == "2"
    assert panel.summary_raw.text() == "1300"
    assert panel.summary_delay.text() == "1200"
    assert panel.summary_current.text() == "0"
    assert panel.summary_flash.text() == "100"
    assert "最终撞闪剩余 100 帧" in panel.log_view.toPlainText()


def test_auto_rng_locked_target_view_clears_when_progress_has_no_target(app):
    panel = AutoRngPanel()
    panel.apply_progress(
        AutoRngProgress(
            phase=AutoRngPhase.DECIDE_ADVANCE,
            locked_target=AutoRngTarget(raw_target_advances=1000),
            raw_target_advances=1000,
        )
    )

    panel.apply_progress(AutoRngProgress(phase=AutoRngPhase.SEARCH_TARGET, log_message="最终剩余帧过近，放弃本目标"))

    assert panel.locked_target_view.values["Status"].text() == "未锁定"


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
    assert started[0].config == config


def test_main_window_auto_rng_start_opens_preview_when_inactive(app, tmp_path, monkeypatch):
    window = MainWindow()
    config = AutoRngConfig(script_dir=tmp_path)
    started: list[AutoRngRunner] = []
    preview_updates: list[str] = []

    def fake_update_preview() -> None:
        preview_updates.append("updated")
        window._latest_preview_frame = object()

    monkeypatch.setattr(window, "_update_preview_frame", fake_update_preview)
    monkeypatch.setattr(window.auto_rng_tab, "run_with_runner", started.append)

    window._start_auto_rng(config)

    assert window._preview_timer.isActive()
    assert window.preview_button.text() == window._text("stop_preview")
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


def test_main_window_auto_rng_services_search_uses_auto_target_form(app, tmp_path, monkeypatch):
    window = MainWindow()
    _set_bdsp_seed(window)
    window.max_advances.setText("2")
    window.auto_rng_tab.max_advances.setValue(9)
    target_form = window.auto_rng_tab.target_form
    target_form.category_combo.setCurrentIndex(target_form.category_combo.findData("mythics"))
    target_form.encounter_combo.setCurrentIndex(target_form.encounter_combo.findText("谢米 [幻兽]"))
    target_form.iv_count_display.setValue(3)
    target_form.height_min.setValue(0)
    target_form.height_max.setValue(0)
    target_form.shiny_filter.setCurrentIndex(target_form.shiny_filter.findText("Square"))
    captured = []

    def fake_generate(criteria):
        captured.append(criteria)
        return []

    monkeypatch.setattr(main_window_module, "generate_static_candidates", fake_generate)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path, max_advances=9))

    services.search_candidates(AutoRngSeedResult(seed=window._current_seed_pair()))

    assert len(captured) == 1
    criteria = captured[0]
    assert criteria.record.description == "Shaymin"
    assert criteria.record.template.iv_count == 3
    assert criteria.max_advances == 9
    assert criteria.shiny_mode == "square"
    assert criteria.state_filter.height_min == 0
    assert criteria.state_filter.height_max == 0


def test_main_window_auto_rng_capture_service_uses_project_xs(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)
    observation = SimpleNamespace(offset_time=100.0)
    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(main_window_module, "capture_player_blinks", lambda *_args, **_kwargs: observation)
    monkeypatch.setattr(
        main_window_module,
        "recover_seed_from_observation",
        lambda actual_observation, npc: SimpleNamespace(state=seed_state, advances=0),
    )
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    result = services.capture_seed()

    assert result.seed == seed_state
    assert result.current_advances == 0
    assert result.seed_text == "1111111122222222 3333333344444444"


def test_main_window_auto_rng_capture_syncs_seed_tab_and_bdsp_results(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)
    observation = SimpleNamespace(offset_time=100.0)
    displayed_frames: list[object] = []
    generated = []
    target_form = window.auto_rng_tab.target_form
    target_form.iv_min[0].setValue(31)
    target_form.iv_max[0].setValue(31)
    target_form.height_min.setValue(0)
    target_form.height_max.setValue(0)

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
    assert window.iv_min[0].text() == "31"
    assert window.iv_max[0].text() == "31"
    assert window.height_max.text() == "0"
    assert len(generated) >= 1
    assert generated[-1].seed == seed_state.to_seed_pair64()
    assert generated[-1].state_filter.iv_min[0] == 31


def test_main_window_auto_rng_reidentify_service_uses_project_xs(app, tmp_path, monkeypatch):
    window = MainWindow()
    seed_state = SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD)
    calls: list[SeedState32] = []
    capture_counts: list[int] = []

    def fake_capture(config, *_args, **_kwargs):
        capture_counts.append(config.blink_count)
        return SimpleNamespace(offset_time=0.0)

    monkeypatch.setattr(main_window_module, "capture_player_blinks", fake_capture)

    def fake_reidentify(current_state, _observation, **_kwargs):
        calls.append(current_state)
        return SimpleNamespace(state=seed_state, advances=42)

    monkeypatch.setattr(main_window_module, "reidentify_seed_from_observation", fake_reidentify)
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    result = services.reidentify(AutoRngSeedResult(seed=SeedPair64(0x1111111122222222, 0x3333333344444444)))
    QApplication.processEvents()

    assert calls == [SeedState32(0x11111111, 0x22222222, 0x33333333, 0x44444444)]
    assert result.seed == seed_state
    assert result.current_advances == 42
    assert capture_counts == [7]
    assert int(window.advances_value.text()) >= 42
    assert window._advance_timer.isActive()


def test_main_window_auto_rng_run_script_service_uses_bridge(app, tmp_path, monkeypatch):
    window = MainWindow()
    calls: list[tuple[str, str]] = []

    class FakeBackend:
        def run_script_text(self, script_text: str, name: str) -> str:
            calls.append((script_text, name))
            return "ok"

    window.easycon_tab.bridge_status = EasyConStatus.BRIDGE_CONNECTED
    monkeypatch.setattr(window.easycon_tab, "_ensure_bridge_backend", lambda: FakeBackend())
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    assert services.run_script_text("A 100", "hit.txt") == "ok"
    assert calls == [("A 100", "hit.txt")]


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

    window.easycon_tab.bridge_status = EasyConStatus.BRIDGE_CONNECTED
    monkeypatch.setattr(window.easycon_tab, "_ensure_bridge_backend", lambda: FakeBackend())
    services = window._build_auto_rng_services(AutoRngConfig(script_dir=tmp_path))

    services.run_script_text("A 100", "hit.txt")
    QApplication.processEvents()

    assert window.easycon_tab.task_state_text == "已完成"
    assert "自动流程运行脚本: hit.txt" in window.easycon_tab.log_view.toPlainText()
    assert "done" in window.easycon_tab.log_view.toPlainText()
    assert window.easycon_tab.easycon_status.currentMessage() == "已完成，连接保持"


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
