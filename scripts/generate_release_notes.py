from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path


APP_NAME = "珍钻复刻自动乱数"
ROOT = Path(__file__).resolve().parents[1]
PACKAGE_INIT = Path("src/auto_bdsp_rng/__init__.py")
PROJECT_FILE = Path("pyproject.toml")
CHANGELOG_FILE = Path("CHANGELOG.md")
PACKAGE_VERSION_PATTERN = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)
RELEASE_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class ReleaseNotesError(ValueError):
    """Raised when release metadata cannot produce a trustworthy release body."""


def read_project_version(root: Path) -> str:
    project = tomllib.loads((root / PROJECT_FILE).read_text(encoding="utf-8"))
    version = project.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise ReleaseNotesError(f"Missing [project].version in {PROJECT_FILE}")
    return version


def read_package_version(root: Path) -> str:
    package_init = (root / PACKAGE_INIT).read_text(encoding="utf-8")
    match = PACKAGE_VERSION_PATTERN.search(package_init)
    if match is None:
        raise ReleaseNotesError(f"Missing __version__ in {PACKAGE_INIT}")
    return match.group(1)


def extract_changelog_entry(changelog: str, version: str) -> str:
    lines = changelog.splitlines()
    heading = f"# {version}"
    matching_indexes = [index for index, line in enumerate(lines) if line.strip() == heading]
    if not matching_indexes:
        raise ReleaseNotesError(f"Missing changelog heading: {heading}")
    if len(matching_indexes) > 1:
        raise ReleaseNotesError(f"Duplicate changelog heading: {heading}")

    first_top_level_heading = next(
        (index for index, line in enumerate(lines) if line.startswith("# ")),
        None,
    )
    if matching_indexes[0] != first_top_level_heading:
        raise ReleaseNotesError(f"Changelog entry is not the current top-level entry: {heading}")

    start = matching_indexes[0] + 1
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("# ")),
        len(lines),
    )
    entry = "\n".join(lines[start:end]).strip()
    if not entry:
        raise ReleaseNotesError(f"Changelog entry is empty: {heading}")
    return entry


def build_release_body(version: str, changes: str) -> str:
    archive_name = f"auto-bdsp-rng-v{version}-windows-x64.zip"
    return f"""# {APP_NAME} v{version}

## 本次更新

{changes}

## 下载

首次安装、旧版尚未内置升级器或应用提示没有增量升级链时，请下载 Windows x64 绿色版压缩包：

`{archive_name}`

请不要下载 GitHub 自动生成的 `Source code (zip)` 或 `Source code (tar.gz)`；它们是源码包，不能直接双击运行。

已安装带升级器正式版的用户，可在软件右上角选择“帮助 -> 检查更新…”，优先只下载发生变化的文件并自动重启完成升级。

## 使用方式

1. 下载 `{archive_name}`
2. 解压 zip
3. 进入 `auto-bdsp-rng` 文件夹
4. 双击 `珍钻复刻自动乱数.exe`

## 说明

- Windows x64 onedir 绿色版，不需要安装 Python。
- 请保留 exe 旁边的 `_internal`、`script`、`bridge` 等目录，不要只复制 exe。
- 应用内升级会校验 GitHub Release 资产和每个变化文件；用户修改过的脚本、Project_Xs 配置和自定义眼图会保留。
- 首次启动或首次 OCR 初始化可能较慢。
- 自动乱数流程仍需要游戏窗口、采集环境、串口/驱动、EasyCon 或兼容后端等实际运行环境。
- 本软件永久免费且开源，请勿购买倒卖版本。
"""


def generate_release_notes(root: Path, tag: str, output: Path) -> None:
    if not tag.startswith("v") or len(tag) == 1:
        raise ReleaseNotesError(f"Release tag must use the vX.Y.Z form, got {tag!r}")

    tagged_version = tag[1:]
    if RELEASE_VERSION_PATTERN.fullmatch(tagged_version) is None:
        raise ReleaseNotesError(f"Release tag must use the vX.Y.Z form, got {tag!r}")
    project_version = read_project_version(root)
    package_version = read_package_version(root)
    if tagged_version != project_version:
        raise ReleaseNotesError(
            f"Tag {tag} does not match pyproject.toml version v{project_version}"
        )
    if package_version != project_version:
        raise ReleaseNotesError(
            f"Package version {package_version} does not match pyproject.toml version {project_version}"
        )

    changelog = (root / CHANGELOG_FILE).read_text(encoding="utf-8")
    changes = extract_changelog_entry(changelog, project_version)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_release_body(project_version, changes), encoding="utf-8", newline="\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a GitHub Release body from the matching CHANGELOG entry."
    )
    parser.add_argument("--tag", required=True, help="Git tag being released, for example v2.1.7")
    parser.add_argument("--output", required=True, type=Path, help="Markdown output path")
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    try:
        generate_release_notes(root, args.tag.strip(), output)
    except (OSError, tomllib.TOMLDecodeError, ReleaseNotesError) as error:
        raise SystemExit(f"Release notes error: {error}") from error
    print(f"Generated release notes: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
