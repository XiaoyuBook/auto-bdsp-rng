from auto_bdsp_rng.__main__ import main
import auto_bdsp_rng.__main__ as cli


def test_blink_config_command_prints_project_xs_config(capsys):
    assert main(["blink-config", "--project-xs-config", "config_cave.json"]) == 0

    output = capsys.readouterr().out
    assert '"roi": [' in output
    assert "config_cave.json" in output
    assert '"monitor_window": true' in output


def test_capture_frame_command_saves_preview(monkeypatch, tmp_path, capsys):
    output = tmp_path / "preview.png"

    def fake_save_preview_frame(_config, output_path):
        return output_path

    monkeypatch.setattr(cli, "save_preview_frame", fake_save_preview_frame)

    assert main(["capture-frame", "--project-xs-config", "config_cave.json", "--output", str(output)]) == 0
    assert f"saved preview frame: {output}" in capsys.readouterr().out
