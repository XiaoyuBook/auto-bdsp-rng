from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from auto_bdsp_rng.blink_detection import BlinkObservation, ProjectXsReidentifyResult, SeedState32
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QApplication, QFileDialog

from auto_bdsp_rng.automation.auto_rng import AutoRngConfig
from auto_bdsp_rng.ui import MainWindow
from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel


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

    monkeypatch.setattr("auto_bdsp_rng.ui.main_window.capture_player_blinks", lambda *args, **kwargs: observation)
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
    assert window.advances_value.text() == "42"
    assert window.result_count.text() == "3 条结果"


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
