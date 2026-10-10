import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from auto_bdsp_rng.ui.auto_rng_panel import AutoRngPanel
from auto_bdsp_rng.ui.auto_tid_rng_panel import AutoTidRngPanel
from auto_bdsp_rng.ui.script_combo_box import ScriptComboBox


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application = QApplication.instance() or QApplication([])
    yield application
    for widget in application.topLevelWidgets():
        for timer in widget.findChildren(QTimer):
            timer.stop()
        widget.close()
        widget.deleteLater()
    application.processEvents()


def selector(app):
    combo = ScriptComboBox(lambda: None)
    combo.addItem("请选择", None)
    for name in ("BDSP测种.txt", "谢米撞闪.txt", "谢米反查.ecs", "游走反查.txt"):
        combo.addItem(name, f"D:/scripts/{name}")
    combo.setCurrentIndex(1)
    combo.resize(200, 32)
    combo.show()
    app.processEvents()
    return combo


@pytest.mark.parametrize(
    ("query", "expected"),
    (
        ("反查", ["谢米反查.ecs", "游走反查.txt"]),
        ("bdsp", ["BDSP测种.txt"]),
        (".ECS", ["谢米反查.ecs"]),
    ),
)
def test_search_filters_names_without_changing_selection(app, query, expected):
    combo = selector(app)
    selected = combo.currentData()
    changes = []
    combo.currentIndexChanged.connect(changes.append)
    QTest.mouseClick(combo, Qt.MouseButton.LeftButton)
    popup = combo.search_popup
    assert popup.isVisible()
    popup.search.setText(query)

    assert [popup.proxy.index(row, 0).data() for row in range(popup.proxy.rowCount())] == expected
    assert combo.currentData() == selected
    assert combo.count() == 5
    assert changes == []


def test_search_keyboard_selection_maps_to_the_original_script(app):
    combo = selector(app)
    activated = []
    combo.activated.connect(activated.append)
    combo.showPopup()
    popup = combo.search_popup
    popup.search.setText("反查")
    QTest.keyClick(popup.search, Qt.Key.Key_Down)
    QTest.keyClick(popup.search, Qt.Key.Key_Return)

    assert combo.currentText() == "游走反查.txt"
    assert combo.currentData() == "D:/scripts/游走反查.txt"
    assert activated == [4]
    assert not popup.isVisible()


def test_search_mouse_selection_and_clearing_the_optional_choice(app):
    combo = selector(app)
    combo.showPopup()
    popup = combo.search_popup
    popup.search.setText("谢米")
    app.processEvents()
    target = popup.proxy.index(1, 0)
    QTest.mouseClick(popup.results.viewport(), Qt.MouseButton.LeftButton,
                     pos=popup.results.visualRect(target).center())

    assert combo.currentData() == "D:/scripts/谢米反查.ecs"
    assert not popup.isVisible()
    combo.showPopup()
    assert popup.search.text() == ""
    assert popup.proxy.rowCount() == combo.count()
    target = popup.proxy.index(0, 0)
    popup.results.scrollTo(target)
    app.processEvents()
    QTest.mouseClick(popup.results.viewport(), Qt.MouseButton.LeftButton,
                     pos=popup.results.visualRect(target).center())
    assert combo.currentData() is None
    assert combo.currentText() == "请选择"


def test_no_match_and_escape_keep_selection_and_reopening_resets_search(app):
    combo = selector(app)
    selected = combo.currentData()
    combo.showPopup()
    popup = combo.search_popup
    popup.search.setText("不存在的脚本")
    assert popup.proxy.rowCount() == 0
    assert popup.empty_label.isVisible()
    QTest.keyClick(popup.search, Qt.Key.Key_Return)
    assert combo.currentData() == selected
    assert popup.isVisible()
    QTest.keyClick(popup.search, Qt.Key.Key_Escape)
    assert not popup.isVisible()
    combo.showPopup()
    assert popup.search.text() == ""
    assert popup.proxy.rowCount() == combo.count()
    assert combo.currentData() == selected


def test_many_scripts_keep_search_visible_and_results_scrollable(app):
    combo = selector(app)
    for row in range(30):
        combo.addItem(f"脚本 {row}.txt", f"D:/scripts/{row}.txt")
    combo.showPopup()
    app.processEvents()
    popup = combo.search_popup

    assert popup.search.height() >= popup.search.sizeHint().height()
    assert popup.results.verticalScrollBar().maximum() > 0
    full_height = popup.height()
    popup.search.setText("脚本 29")
    app.processEvents()
    assert popup.proxy.rowCount() == 1
    assert popup.height() < full_height / 2
    assert popup.search.height() >= popup.search.sizeHint().height()
    popup.search.clear()
    app.processEvents()
    target = popup.proxy.index(popup.proxy.rowCount() - 1, 0)
    popup.results.scrollTo(target)
    app.processEvents()
    QTest.mouseClick(popup.results.viewport(), Qt.MouseButton.LeftButton,
                     pos=popup.results.visualRect(target).center())

    assert combo.currentData() == "D:/scripts/29.txt"
    assert not popup.isVisible()


@pytest.mark.parametrize("panel_type", (AutoRngPanel, AutoTidRngPanel))
def test_script_search_refreshes_edits_and_persists_the_selected_file(app, tmp_path, panel_type):
    for name in ("BDSP测种.txt", "取名.txt", "bdsp过帧.txt"):
        (tmp_path / name).write_text("WAIT 10\n", encoding="utf-8")
    settings = QSettings(str(tmp_path / "scripts.ini"), QSettings.Format.IniFormat)
    panel = panel_type(script_dir=tmp_path, settings=settings)
    if isinstance(panel, AutoTidRngPanel):
        panel.add_target_display_tid(123456)
    panel.resize(700, 900)
    panel.show()
    app.processEvents()
    path = tmp_path / "自定义测种.txt"
    path.write_text("WAIT 10\n", encoding="utf-8")
    combo = panel.seed_script_combo
    selected = combo.currentData()
    combo.showPopup()
    popup = combo.search_popup
    assert combo.findData(str(path)) >= 0
    popup.search.setText("自定义")
    assert combo.currentData() == selected
    QTest.keyClick(popup.search, Qt.Key.Key_Return)

    assert panel.build_config().seed_script_path == path
    assert panel.task_save_state_label.text() == "有未保存修改"
    edits = []
    panel.scriptEditRequested.connect(edits.append)
    panel.script_edit_buttons[combo].click()
    assert edits == [path]
    panel.save_task_button.click()
    settings.sync()
    assert settings.value("seed_script") == str(path)
    restored = panel_type(script_dir=tmp_path, settings=settings)
    assert restored.build_config().seed_script_path == path
