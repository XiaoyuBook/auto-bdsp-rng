from auto_bdsp_rng.__main__ import main


def test_startup_entry(capsys):
    assert main() == 0
    assert "startup entry is ready" in capsys.readouterr().out
