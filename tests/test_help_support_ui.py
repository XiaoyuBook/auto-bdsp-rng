from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMainWindow


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_app_settings_persists_startup_notice_acknowledgement(tmp_path, monkeypatch):
    import auto_bdsp_rng.app_settings as app_settings

    settings_path = tmp_path / "settings" / "config.json"
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", settings_path)

    assert app_settings.should_show_startup_notice() is True

    app_settings.set_startup_notice_acknowledged(True)

    assert app_settings.should_show_startup_notice() is False
    assert "startup_notice_acknowledged" in settings_path.read_text(encoding="utf-8")


def test_app_settings_run_log_defaults_off_and_preserves_other_keys(tmp_path, monkeypatch):
    import auto_bdsp_rng.app_settings as app_settings

    settings_path = tmp_path / "settings" / "config.json"
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", settings_path)

    assert app_settings.is_run_log_enabled() is False

    app_settings.save_settings({"startup_notice_acknowledged": True, "other": "保留"})
    assert app_settings.set_run_log_enabled(True) is True
    assert app_settings.is_run_log_enabled() is True
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {
        "startup_notice_acknowledged": True,
        "other": "保留",
        "run_log_enabled": True,
    }

    assert app_settings.set_run_log_enabled(False) is False
    assert app_settings.is_run_log_enabled() is False
    assert json.loads(settings_path.read_text(encoding="utf-8"))["other"] == "保留"


def test_app_settings_atomic_write_preserves_existing_file_on_replace_failure(tmp_path, monkeypatch):
    import auto_bdsp_rng.app_settings as app_settings

    settings_path = tmp_path / "settings" / "config.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text('{"existing": true}', encoding="utf-8")
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", settings_path)

    def fail_replace(*_args) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(app_settings.os, "replace", fail_replace)

    with pytest.raises(OSError, match="disk full"):
        app_settings.set_run_log_enabled(True)

    assert settings_path.read_text(encoding="utf-8") == '{"existing": true}'
    assert list(settings_path.parent.glob(".config.json.*.tmp")) == []


def test_help_menu_exposes_expected_actions(app):
    from auto_bdsp_rng.ui.help_menu import HelpMenuController

    opened: list[str] = []
    copied: list[str] = []
    window = QMainWindow()
    controller = HelpMenuController(window, open_url=opened.append, copy_text=copied.append)
    controller.install()

    assert controller.help_menu.title() == "帮助"
    assert controller.tutorial_action.text() == "使用教程"
    assert controller.about_action.text() == "关于项目"
    assert controller.run_log_menu.title() == "运行日志"
    assert controller.run_log_save_action.text() == "自动保存运行日志（保留 7 天）"
    assert controller.run_log_save_action.isCheckable()
    assert controller.run_log_save_action.isChecked() is False
    assert controller.open_run_log_dir_action.text() == "打开日志目录"
    assert controller.contact_menu.title() == "作者联系"
    assert controller.email_action.text() == "邮箱：kesong2003@qq.com"
    assert controller.support_action.text() == "支持项目"
    assert controller.changelog_action.text() == "更新日志"
    assert controller.run_log_menu.menuAction() in controller.help_menu.actions()
    assert controller.contact_menu.menuAction() in controller.help_menu.actions()

    controller.tutorial_action.trigger()
    controller.bilibili_action.trigger()
    controller.github_profile_action.trigger()
    controller.email_action.trigger()

    assert opened == [
        "https://skrxiaoyu.com/2026/05/14/%E7%8F%8D%E9%92%BB%E5%A4%8D%E5%88%BB%E5%AE%9A%E7%82%B9%E8%87%AA%E5%8A%A8%E4%B9%B1%E6%95%B0/",
        "https://space.bilibili.com/269020915?spm_id_from=333.1007.0.0",
        "https://github.com/XiaoyuBook",
    ]
    assert copied == ["kesong2003@qq.com"]


def test_help_menu_run_log_actions_apply_actual_state_and_open_directory(app):
    from auto_bdsp_rng.ui.help_menu import HelpMenuController

    requested: list[bool] = []
    opened: list[bool] = []

    def reject_enable(enabled: bool) -> bool:
        requested.append(enabled)
        return False

    controller = HelpMenuController(
        QMainWindow(),
        set_run_log_enabled=reject_enable,
        open_run_log_dir=lambda: opened.append(True),
    )
    controller.install()

    controller.run_log_save_action.trigger()
    controller.open_run_log_dir_action.trigger()

    assert requested == [True]
    assert controller.run_log_save_action.isChecked() is False
    assert opened == [True]


def test_help_menu_run_log_action_restores_previous_state_when_callback_fails(app):
    from auto_bdsp_rng.ui.help_menu import HelpMenuController

    def fail(_enabled: bool) -> bool:
        raise OSError("日志目录不可写")

    controller = HelpMenuController(QMainWindow(), run_log_enabled=True, set_run_log_enabled=fail)
    controller.install()

    controller.run_log_save_action.trigger()

    assert controller.run_log_save_action.isChecked() is True


def test_help_menu_can_sync_runtime_log_failure_state(app):
    from auto_bdsp_rng.ui.help_menu import HelpMenuController

    def fail(_enabled: bool) -> bool:
        raise OSError("日志目录不可写")

    controller = HelpMenuController(QMainWindow(), run_log_enabled=True, set_run_log_enabled=fail)
    controller.install()

    controller.set_run_log_state(False)
    controller.run_log_save_action.trigger()

    assert controller.run_log_save_action.isChecked() is False


def test_sponsor_assets_are_optional(tmp_path, monkeypatch, app):
    import auto_bdsp_rng.ui.sponsor_dialog as sponsor_dialog

    monkeypatch.setattr(sponsor_dialog, "resource_path", lambda *parts: tmp_path.joinpath(*parts))

    missing = sponsor_dialog.find_sponsor_assets()
    assert missing.alipay is None
    assert missing.wechat is None

    sponsor_dir = tmp_path / "private_assets" / "sponsor"
    sponsor_dir.mkdir(parents=True)
    alipay = sponsor_dir / "alipay.jpg"
    wechat = sponsor_dir / "wechat.jpg"
    alipay.write_bytes(b"fake alipay")
    wechat.write_bytes(b"fake wechat")

    found = sponsor_dialog.find_sponsor_assets()
    assert found.alipay == alipay
    assert found.wechat == wechat


def test_about_qr_popup_shows_fallback_when_asset_missing(app, monkeypatch):
    from auto_bdsp_rng.ui.about_dialog import AboutDialog

    shown_messages: list[str] = []

    def capture_exec(dialog: QDialog) -> int:
        shown_messages.extend(label.text() for label in dialog.findChildren(QLabel) if label.text())
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", capture_exec)

    dialog = AboutDialog()
    dialog._show_qr_popup(None, "微信赞赏")

    assert any("当前构建未包含此二维码" in message for message in shown_messages)


def test_markdown_viewer_returns_missing_message(tmp_path):
    from auto_bdsp_rng.ui.markdown_viewer import read_markdown_text

    assert read_markdown_text(tmp_path / "CHANGELOG.md") == "暂无更新日志"

    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# 更新日志\n\n- test\n", encoding="utf-8")
    assert read_markdown_text(changelog).startswith("# 更新日志")
