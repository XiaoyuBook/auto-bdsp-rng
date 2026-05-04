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


def test_capture_blinks_command_outputs_seed(monkeypatch, capsys):
    class FakeObservation:
        pass

    class FakeResult:
        def as_dict(self):
            return {
                "seed_0_3": ["12345678", "9ABCDEF0", "11111111", "22222222"],
                "seed_0_1": ["123456789ABCDEF0", "1111111122222222"],
                "state_words": [0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222],
                "seed64_pair": [0x123456789ABCDEF0, 0x1111111122222222],
                "blinks": [0, 1],
                "intervals": [0, 12],
                "offset_time": 0.0,
            }

    monkeypatch.setattr(cli, "capture_player_blinks", lambda _config: FakeObservation())
    monkeypatch.setattr(cli, "recover_seed_from_observation", lambda _observation, npc=0: FakeResult())

    assert main(["capture-blinks", "--project-xs-config", "config_cave.json", "--blink-count", "2", "--npc", "0"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["npc"] == 0
    assert payload["seed_0_3"] == ["12345678", "9ABCDEF0", "11111111", "22222222"]
    assert payload["seed_0_1"] == ["123456789ABCDEF0", "1111111122222222"]


def test_reidentify_command_outputs_seed_and_advances(monkeypatch, capsys):
    class FakeObservation:
        pass

    class FakeResult:
        def as_dict(self):
            return {
                "seed_0_3": ["12345678", "9ABCDEF0", "11111111", "22222222"],
                "seed_0_1": ["123456789ABCDEF0", "1111111122222222"],
                "state_words": [0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222],
                "seed64_pair": [0x123456789ABCDEF0, 0x1111111122222222],
                "blinks": [],
                "intervals": [0, 12],
                "offset_time": 0.0,
                "advances": 42,
            }

    monkeypatch.setattr(cli, "capture_player_blinks", lambda _config: FakeObservation())
    monkeypatch.setattr(cli, "reidentify_seed_from_observation", lambda *_args, **_kwargs: FakeResult())

    assert (
        main(
            [
                "reidentify",
                "--project-xs-config",
                "config_cave.json",
                "--seed",
                "12345678",
                "9ABCDEF0",
                "11111111",
                "22222222",
                "--blink-count",
                "2",
                "--npc",
                "0",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["npc"] == 0
    assert payload["advances"] == 42
    assert payload["seed_0_1"] == ["123456789ABCDEF0", "1111111122222222"]
