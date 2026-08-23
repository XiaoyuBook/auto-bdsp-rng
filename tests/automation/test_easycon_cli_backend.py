from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from auto_bdsp_rng.automation.easycon import (
    CliEasyConBackend,
    EasyConInstallation,
    EasyConRunTask,
    EasyConStatus,
    classify_cli_failure,
    cli_connection_notice,
    extract_compile_error_line,
    generate_script_file,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_cli_backend_runs_minimal_script_with_mock(tmp_path):
    ezcon = _write_fake_ezcon(tmp_path)
    script = tmp_path / "minimal.ecs"
    script.write_text("WAIT 50\n", encoding="utf-8")
    backend = CliEasyConBackend(EasyConInstallation(path=ezcon, version="fake", source="test"))

    result = backend.run_script(EasyConRunTask(script_path=script, port="", mock=True))

    assert result.status == EasyConStatus.COMPLETED
    assert result.exit_code == 0
    assert result.port == "mock"
    assert "fake ezcon run" in result.stdout
    assert str(script) in result.stdout


def test_cli_backend_runs_generated_existing_script_with_mock(tmp_path):
    ezcon = _write_fake_ezcon(tmp_path)
    source = PROJECT_ROOT / "script" / "BDSP测种.txt"
    generated = generate_script_file(source.read_text(encoding="utf-8"), source.name, tmp_path / ".generated")
    backend = CliEasyConBackend(EasyConInstallation(path=ezcon, version="fake", source="test"))

    result = backend.run_script(EasyConRunTask(script_path=generated, port="COM7", mock=True))

    assert result.status == EasyConStatus.COMPLETED
    assert result.port == "mock"
    assert generated.suffix == ".ecs"
    assert "port=mock" in result.stdout


def test_cli_backend_classifies_compile_failure_and_line(tmp_path):
    ezcon = _write_fake_ezcon(tmp_path)
    script = tmp_path / "bad.ecs"
    script.write_text("WAIT 1\nCOMPILE_ERROR\n", encoding="utf-8")
    backend = CliEasyConBackend(EasyConInstallation(path=ezcon, version="fake", source="test"))

    result = backend.run_script(EasyConRunTask(script_path=script, port="", mock=True))

    assert result.status == EasyConStatus.FAILED
    assert result.exit_code == 2
    assert classify_cli_failure(result.stdout, result.stderr, result.exit_code) == "script_compile_failed"
    assert extract_compile_error_line(result.stdout, result.stderr) == 2


def test_cli_backend_stop_current_script_terminates_running_process(tmp_path, monkeypatch):
    driver = tmp_path / "run"
    driver.write_text(
        "\n".join(
            [
                "from pathlib import Path",
                "import sys",
                "import time",
                "Path(sys.argv[1] + '.started').write_text('ready', encoding='utf-8')",
                "print('fake ezcon started', flush=True)",
                "time.sleep(30)",
            ]
        ),
        encoding="utf-8",
    )
    script = tmp_path / "long-running.ecs"
    script.write_text("WAIT 30000\n", encoding="utf-8")
    started_marker = Path(f"{script}.started")
    monkeypatch.chdir(tmp_path)
    backend = CliEasyConBackend(
        EasyConInstallation(path=Path(sys.executable), version="fake", source="test")
    )
    results = []
    errors = []

    def run_script() -> None:
        try:
            results.append(backend.run_script(EasyConRunTask(script_path=script, port="", mock=True)))
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_script, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 5.0
        while not started_marker.exists() and time.monotonic() < deadline:
            if not worker.is_alive():
                break
            time.sleep(0.01)
        assert started_marker.exists()
        assert backend.status() == EasyConStatus.RUNNING

        backend.stop_current_script()
        worker.join(timeout=5.0)

        assert not worker.is_alive()
        assert errors == []
        assert len(results) == 1
        assert results[0].status == EasyConStatus.CANCELLED
        assert results[0].exit_code == 130
        assert "fake ezcon started" in results[0].stdout
        assert backend.status() == EasyConStatus.CANCELLED
    finally:
        process = backend._process
        if process is not None and process.poll() is None:
            process.kill()
        worker.join(timeout=5.0)


def test_cli_notice_never_claims_long_lived_connection():
    notice = cli_connection_notice()

    assert "不是真实长期连接" in notice
    assert "每次运行脚本都会启动 ezcon.exe 并重新连接" in notice
    assert "不满足最终验收" in notice


def _write_fake_ezcon(tmp_path: Path) -> Path:
    ezcon = tmp_path / "fake_ezcon.cmd"
    ezcon.write_text(
        "\n".join(
            [
                "@echo off",
                "if \"%1\"==\"--version\" goto version",
                "if \"%1\"==\"port\" goto port",
                "if \"%1\"==\"run\" goto run",
                "exit /b 9",
                ":version",
                "echo fake-ezcon-1.0",
                "exit /b 0",
                ":port",
                "echo COM7",
                "exit /b 0",
                ":run",
                "echo fake ezcon run script=%2 port=%4",
                "echo %2 | findstr /C:\"bad.ecs\" >nul",
                "if not errorlevel 1 goto compile_error",
                "exit /b 0",
                ":compile_error",
                "echo compile error at line 2 1>&2",
                "exit /b 2",
            ]
        ),
        encoding="utf-8",
        newline="\r\n",
    )
    return ezcon
