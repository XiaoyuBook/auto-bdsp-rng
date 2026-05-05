from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from auto_bdsp_rng.automation.easycon.models import ScriptParameter


PARAMETER_RE = re.compile(r"^(?P<indent>\s*)(?P<name>_[^=\s]+)\s*=\s*(?P<value>.*?)(?P<comment>\s+#.*)?$")
REQUIRED_MARKER = "填入这里"


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


def generate_script_file(
    script_text: str,
    source_name: str,
    generated_dir: Path,
    task_type: str | None = None,
    newline: str | None = None,
) -> Path:
    generated_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(Path(source_name).stem or "script")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parts = [stem, timestamp]
    if task_type:
        parts.append(_safe_stem(task_type))
    output = generated_dir / ("_".join(parts) + ".ecs")
    output.write_text(script_text, encoding="utf-8", newline=newline or detect_newline_style(script_text))
    return output


def prune_generated_scripts(generated_dir: Path, keep: int) -> list[Path]:
    if keep < 0 or not generated_dir.exists():
        return []
    scripts = sorted(
        (path for path in generated_dir.iterdir() if path.is_file() and path.suffix.lower() == ".ecs"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed: list[Path] = []
    for path in scripts[keep:]:
        path.unlink()
        removed.append(path)
    return removed


def _is_integer(value: str) -> bool:
    return re.fullmatch(r"[+-]?\d+", value.strip()) is not None


def _safe_stem(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]+', "_", value).strip("._") or "script"
