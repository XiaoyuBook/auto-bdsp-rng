import json

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


def test_preview_eye_command_saves_annotated_preview(monkeypatch, tmp_path, capsys):
    output = tmp_path / "eye_preview.png"

    class FakePreview:
        def as_dict(self):
            return {
                "roi": [1, 2, 3, 4],
                "match_score": 0.95,
                "match_location": [5, 6],
                "template_size": [7, 8],
                "threshold": 0.9,
                "matched": True,
            }

    def fake_save_eye_preview(_config, output_path):
        return output_path, FakePreview()

    monkeypatch.setattr(cli, "save_eye_preview", fake_save_eye_preview)

    assert main(["preview-eye", "--project-xs-config", "config_cave.json", "--output", str(output)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["output"] == str(output)
    assert payload["matched"] is True
