from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path

from auto_bdsp_rng.automation.easycon.models import ScriptParameter


PARAMETER_RE = re.compile(r"^(?P<indent>\s*)(?P<name>_[^=\s]+)\s*=\s*(?P<value>.*?)(?P<comment>\s+#.*)?$")
REQUIRED_MARKER = "填入这里"
LEGACY_GENERATED_DIR_NAME = ".generated"
LEGACY_GENERATED_SCRIPT_RE = re.compile(r"^.+_\d{8}_\d{6}(?:_.+)?\.ecs$", re.IGNORECASE)
TEMPORARY_CLI_SCRIPT_PREFIX = "auto-bdsp-rng-easycon-"


def detect_newline_style(text: str) -> str:
    crlf_count = text.count("\r\n")
    lf_count = text.count("\n") - crlf_count
    if crlf_count > lf_count:
        return "\r\n"
    return "\n"


def scan_builtin_scripts(script_dir: Path) -> list[Path]:
    if not script_dir.exists():
        return []
    return sorted(
        (path for path in script_dir.iterdir() if path.is_file() and path.suffix.lower() in {".txt", ".ecs"}),
        key=lambda path: path.name.casefold(),
    )


def parse_script_parameters(text: str) -> list[ScriptParameter]:
    parameters: list[ScriptParameter] = []
    for index, line in enumerate(text.splitlines()):
        match = PARAMETER_RE.match(line)
        if match is None:
            continue
        value = match.group("value").rstrip()
        comment = (match.group("comment") or "").strip()
        parameters.append(
            ScriptParameter(
                name=match.group("name"),
                value=value,
                default=value,
                required=value == REQUIRED_MARKER,
                is_integer=_is_integer(value),
                comment=comment[1:].strip() if comment.startswith("#") else comment,
                line_index=index,
            )
        )
    return parameters


def apply_parameter_values(text: str, values: dict[str, str | int]) -> str:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        newline = ""
        body = line
        if line.endswith("\r\n"):
            body = line[:-2]
            newline = "\r\n"
        elif line.endswith("\n"):
            body = line[:-1]
            newline = "\n"
        match = PARAMETER_RE.match(body)
        if match is None:
            continue
        name = match.group("name")
        if name not in values:
            continue
        comment = match.group("comment") or ""
        lines[index] = f"{match.group('indent')}{name} = {values[name]}{comment}{newline}"
    return "".join(lines)


def create_temporary_cli_script(
    script_text: str,
    *,
    newline: str | None = None,
    temp_dir: Path | None = None,
) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=TEMPORARY_CLI_SCRIPT_PREFIX,
        suffix=".txt",
        dir=temp_dir,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline=newline or detect_newline_style(script_text),
        ) as handle:
            handle.write(script_text)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def remove_temporary_cli_script(path: Path | None) -> None:
    if path is not None:
        path.unlink(missing_ok=True)


def discard_legacy_generated_snapshots(script_dir: Path) -> int:
    generated_dir = script_dir / LEGACY_GENERATED_DIR_NAME
    try:
        directory_stat = generated_dir.lstat()
    except FileNotFoundError:
        return 0
    if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode):
        raise OSError(f"旧版临时脚本路径不是安全目录：{generated_dir}")

    entries = list(generated_dir.iterdir())
    for entry in entries:
        entry_stat = entry.lstat()
        if (
            stat.S_ISLNK(entry_stat.st_mode)
            or not stat.S_ISREG(entry_stat.st_mode)
            or LEGACY_GENERATED_SCRIPT_RE.fullmatch(entry.name) is None
        ):
            raise OSError(f"旧版临时脚本目录包含未知内容，已保留：{entry}")

    for entry in entries:
        entry.unlink()
    generated_dir.rmdir()
    return len(entries)


def _is_integer(value: str) -> bool:
    return re.fullmatch(r"[+-]?\d+", value.strip()) is not None
