from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
import unicodedata
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
APPLICATION = "auto-bdsp-rng"
PLATFORM = "windows-x64"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST_DIR = ROOT / "dist" / APPLICATION
DEFAULT_OUTPUT_DIR = ROOT / "release"
VERSION_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
PRESERVE_PREFIXES = (
    ("script",),
    ("logs",),
    ("third_party", "project_xs_chn", "configs"),
    ("third_party", "project_xs_chn", "images", "custom"),
)


class UpdatePackageError(RuntimeError):
    pass


def _version_tuple(version: str) -> tuple[int, int, int]:
    if not isinstance(version, str) or VERSION_RE.fullmatch(version) is None:
        raise UpdatePackageError(f"Version must use X.Y.Z without leading zeroes: {version!r}")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def _validate_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise UpdatePackageError("Manifest file paths must be non-empty strings.")
    if "\\" in value or "\0" in value:
        raise UpdatePackageError(f"Unsafe manifest path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value:
        raise UpdatePackageError(f"Manifest path is not normalized: {value!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise UpdatePackageError(f"Manifest path contains traversal: {value!r}")
    for part in path.parts:
        if any(ord(character) < 32 for character in part):
            raise UpdatePackageError(f"Manifest path contains a control character: {value!r}")
        if ":" in part or part.endswith((" ", ".")):
            raise UpdatePackageError(f"Manifest path is invalid on Windows: {value!r}")
        if part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES:
            raise UpdatePackageError(f"Manifest path uses a reserved Windows name: {value!r}")
    return value


def _windows_path_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _path_components(paths: Iterable[str]) -> dict[str, str]:
    components: dict[str, str] = {}
    for path in paths:
        parts = PurePosixPath(path).parts
        for index in range(1, len(parts) + 1):
            component_path = "/".join(parts[:index])
            components[_windows_path_key(component_path)] = component_path
    return components


def preserve_if_modified(path: str) -> bool:
    parts = tuple(part.casefold() for part in PurePosixPath(path).parts)
    return any(len(parts) > len(prefix) and parts[: len(prefix)] == prefix for prefix in PRESERVE_PREFIXES)


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    before = path.stat()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
            total += len(chunk)
    after = path.stat()
    if total != before.st_size or after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
        raise UpdatePackageError(f"File changed while it was being hashed: {path}")
    return digest.hexdigest(), total


def build_manifest(dist_dir: Path, version: str) -> dict[str, Any]:
    _version_tuple(version)
    dist_dir = dist_dir.resolve()
    if not dist_dir.is_dir():
        raise UpdatePackageError(f"Distribution directory does not exist: {dist_dir}")

    entries: list[dict[str, Any]] = []
    seen_paths: dict[str, str] = {}
    for source in dist_dir.rglob("*"):
        relative = source.relative_to(dist_dir).as_posix()
        relative = _validate_relative_path(relative)
        path_key = _windows_path_key(relative)
        previous = seen_paths.get(path_key)
        if previous is not None:
            raise UpdatePackageError(f"Windows case-insensitive path collision: {previous!r} and {relative!r}")
        seen_paths[path_key] = relative

        if source.is_symlink():
            raise UpdatePackageError(f"Symbolic links are not allowed in update artifacts: {source}")
        if source.is_dir():
            continue
        if not source.is_file():
            raise UpdatePackageError(f"Unsupported distribution entry: {source}")
        sha256, size = _sha256_file(source)
        entries.append(
            {
                "path": relative,
                "size": size,
                "sha256": sha256,
                "preserve_if_modified": preserve_if_modified(relative),
            }
        )

    if not entries:
        raise UpdatePackageError(f"Distribution directory contains no files: {dist_dir}")
    entries.sort(key=lambda entry: (_windows_path_key(entry["path"]), entry["path"]))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "application": APPLICATION,
        "platform": PLATFORM,
        "version": version,
        "files": entries,
    }
    validate_manifest(manifest)
    return manifest


def validate_manifest(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise UpdatePackageError("Manifest root must be a JSON object.")
    expected_root_keys = {"schema_version", "application", "platform", "version", "files"}
    if set(value) != expected_root_keys:
        raise UpdatePackageError("Manifest root has missing or unsupported fields.")
    if type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise UpdatePackageError(f"Unsupported manifest schema_version: {value['schema_version']!r}")
    if value["application"] != APPLICATION:
        raise UpdatePackageError(f"Unexpected manifest application: {value['application']!r}")
    if value["platform"] != PLATFORM:
        raise UpdatePackageError(f"Unexpected manifest platform: {value['platform']!r}")
    _version_tuple(value["version"])

    files = value["files"]
    if not isinstance(files, list):
        raise UpdatePackageError("Manifest files must be a JSON array.")
    if not files:
        raise UpdatePackageError("Manifest files must not be empty.")
    seen_components: dict[str, str] = {}
    file_path_keys: set[str] = set()
    directory_path_keys: set[str] = set()
    ordered_paths: list[str] = []
    expected_file_keys = {"path", "size", "sha256", "preserve_if_modified"}
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != expected_file_keys:
            raise UpdatePackageError("Manifest file entry has missing or unsupported fields.")
        path = _validate_relative_path(entry["path"])
        parts = PurePosixPath(path).parts
        for index in range(1, len(parts) + 1):
            component_path = "/".join(parts[:index])
            key = _windows_path_key(component_path)
            previous_component_path = seen_components.get(key)
            if previous_component_path is not None and previous_component_path != component_path:
                raise UpdatePackageError(
                    "Windows case-insensitive path collision: "
                    f"{previous_component_path!r} and {component_path!r}"
                )
            seen_components[key] = component_path
            if index < len(parts):
                if key in file_path_keys:
                    raise UpdatePackageError(f"Manifest path is both a file and directory: {component_path!r}")
                directory_path_keys.add(key)
            else:
                if key in file_path_keys:
                    raise UpdatePackageError(f"Windows case-insensitive path collision: {path!r}")
                if key in directory_path_keys:
                    raise UpdatePackageError(f"Manifest path is both a file and directory: {path!r}")
                file_path_keys.add(key)
        ordered_paths.append(path)
        size = entry["size"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise UpdatePackageError(f"Invalid size for {path!r}: {size!r}")
        sha256 = entry["sha256"]
        if not isinstance(sha256, str) or SHA256_RE.fullmatch(sha256) is None:
            raise UpdatePackageError(f"Invalid SHA-256 for {path!r}.")
        preserve = entry["preserve_if_modified"]
        if not isinstance(preserve, bool):
            raise UpdatePackageError(f"Invalid preserve_if_modified for {path!r}.")
        if preserve != preserve_if_modified(path):
            raise UpdatePackageError(f"Incorrect preserve_if_modified policy for {path!r}.")

    expected_order = sorted(ordered_paths, key=lambda path: (_windows_path_key(path), path))
    if ordered_paths != expected_order:
        raise UpdatePackageError("Manifest files must be sorted by path.")
    return value


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise UpdatePackageError(f"Cannot read previous manifest as UTF-8: {path}") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UpdatePackageError(f"Invalid manifest JSON: {path}") from exc
    return validate_manifest(value)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_manifest(manifest: dict[str, Any], output_path: Path) -> None:
    validate_manifest(manifest)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(_json_bytes(manifest))


def build_patch_metadata(
    current_manifest: dict[str, Any], previous_manifest: dict[str, Any]
) -> dict[str, Any]:
    validate_manifest(current_manifest)
    validate_manifest(previous_manifest)
    current_version = current_manifest["version"]
    previous_version = previous_manifest["version"]
    if _version_tuple(previous_version) >= _version_tuple(current_version):
        raise UpdatePackageError(
            f"Previous manifest version {previous_version} must be older than current version {current_version}."
        )

    current_files = {entry["path"]: entry for entry in current_manifest["files"]}
    previous_files = {entry["path"]: entry for entry in previous_manifest["files"]}
    previous_case_paths = {_windows_path_key(path): path for path in previous_files}
    previous_components = _path_components(previous_files)
    current_components = _path_components(current_files)
    for key, current_component in current_components.items():
        previous_component = previous_components.get(key)
        if previous_component is not None and previous_component != current_component:
            raise UpdatePackageError(
                "Case-only path changes are not supported on Windows: "
                f"{previous_component!r} -> {current_component!r}"
            )

    current_case_paths = {_windows_path_key(path): path for path in current_files}
    for path in current_files:
        for ancestor in _parent_paths(path):
            previous_ancestor = previous_case_paths.get(_windows_path_key(ancestor))
            if previous_ancestor is not None:
                raise UpdatePackageError(
                    "File-to-directory path changes are not supported by incremental updates: "
                    f"{previous_ancestor!r} -> {path!r}"
                )
    for path in previous_files:
        for ancestor in _parent_paths(path):
            current_ancestor = current_case_paths.get(_windows_path_key(ancestor))
            if current_ancestor is not None:
                raise UpdatePackageError(
                    "Directory-to-file path changes are not supported by incremental updates: "
                    f"{path!r} -> {current_ancestor!r}"
                )

    changed: list[dict[str, Any]] = []
    for path, current in current_files.items():
        previous = previous_files.get(path)
        if previous is not None and (previous["sha256"], previous["size"]) == (
            current["sha256"],
            current["size"],
        ):
            continue
        changed.append(
            {
                "path": path,
                "size": current["size"],
                "sha256": current["sha256"],
                "previous_sha256": None if previous is None else previous["sha256"],
                "preserve_if_modified": current["preserve_if_modified"],
            }
        )

    removed = [
        {
            "path": path,
            "sha256": previous["sha256"],
            "preserve_if_modified": previous["preserve_if_modified"],
        }
        for path, previous in previous_files.items()
        if path not in current_files
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "application": APPLICATION,
        "platform": PLATFORM,
        "from_version": previous_version,
        "to_version": current_version,
        "files": changed,
        "remove": removed,
    }


def _parent_paths(path: str) -> tuple[str, ...]:
    parts = PurePosixPath(path).parts
    return tuple("/".join(parts[:index]) for index in range(1, len(parts)))


def create_patch(
    dist_dir: Path,
    current_manifest: dict[str, Any],
    previous_manifest: dict[str, Any],
    output_path: Path,
    *,
    compresslevel: int = 6,
) -> None:
    if not 0 <= compresslevel <= 9:
        raise UpdatePackageError("ZIP compresslevel must be between 0 and 9.")
    metadata = build_patch_metadata(current_manifest, previous_manifest)
    current_files = {entry["path"]: entry for entry in current_manifest["files"]}
    dist_dir = dist_dir.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    try:
        with zipfile.ZipFile(
            output_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=compresslevel,
        ) as archive:
            archive.writestr("update.json", _json_bytes(metadata))
            for entry in metadata["files"]:
                relative = entry["path"]
                source = dist_dir.joinpath(*PurePosixPath(relative).parts)
                if not source.is_file() or source.is_symlink():
                    raise UpdatePackageError(f"Patch source file is missing or unsafe: {source}")
                sha256, size = _sha256_file(source)
                expected = current_files[relative]
                if (sha256, size) != (expected["sha256"], expected["size"]):
                    raise UpdatePackageError(f"Patch source no longer matches the current manifest: {source}")
                archive.write(source, f"payload/{relative}")
    except Exception:
        if output_path.exists():
            output_path.unlink()
        raise


def read_project_version(project_file: Path = ROOT / "pyproject.toml") -> str:
    try:
        version = tomllib.loads(project_file.read_text(encoding="utf-8"))["project"]["version"]
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise UpdatePackageError(f"Cannot read project version from {project_file}") from exc
    _version_tuple(version)
    return version


def build_update_artifacts(
    dist_dir: Path,
    output_dir: Path,
    version: str,
    *,
    previous_manifest_path: Path | None = None,
    compresslevel: int = 6,
) -> tuple[Path, Path | None]:
    dist_dir = dist_dir.resolve()
    output_dir = output_dir.resolve()
    if output_dir == dist_dir or dist_dir in output_dir.parents:
        raise UpdatePackageError("Output directory must not be inside the distribution directory.")

    manifest = build_manifest(dist_dir, version)
    manifest_path = output_dir / f"{APPLICATION}-v{version}-{PLATFORM}.manifest.json"
    write_manifest(manifest, manifest_path)
    if previous_manifest_path is None:
        return manifest_path, None

    previous_manifest = load_manifest(previous_manifest_path)
    previous_version = previous_manifest["version"]
    patch_path = output_dir / (
        f"{APPLICATION}-v{previous_version}-to-v{version}-{PLATFORM}.update.zip"
    )
    create_patch(
        dist_dir,
        manifest,
        previous_manifest,
        patch_path,
        compresslevel=compresslevel,
    )
    return manifest_path, patch_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a Windows release manifest and an optional file-level update package."
    )
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--version", help="Target X.Y.Z version; defaults to pyproject.toml.")
    parser.add_argument("--previous-manifest", type=Path)
    parser.add_argument("--compresslevel", type=int, choices=range(0, 10), default=6)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        version = args.version or read_project_version()
        manifest_path, patch_path = build_update_artifacts(
            args.dist_dir,
            args.output_dir,
            version,
            previous_manifest_path=args.previous_manifest,
            compresslevel=args.compresslevel,
        )
    except UpdatePackageError as exc:
        print(f"Update artifact build failed: {exc}", file=sys.stderr)
        return 1
    print(f"Release manifest: {manifest_path}")
    if patch_path is None:
        print("Previous release manifest not provided; generated bootstrap manifest only.")
    else:
        print(f"Incremental update package: {patch_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
