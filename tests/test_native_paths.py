from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from auto_bdsp_rng import native_paths


@pytest.fixture
def native_cache(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "native_cache"
    monkeypatch.setenv("AUTO_BDSP_RNG_NATIVE_CACHE_HOME", str(root))
    monkeypatch.setattr(native_paths, "needs_ascii_path", lambda path: not str(path.absolute()).isascii())
    return root


def test_native_copy_is_reused_repaired_and_updated(native_cache: Path, tmp_path: Path) -> None:
    source = tmp_path / "中文用户" / "模型"
    source.mkdir(parents=True)
    (source / "inference.json").write_bytes(b"model one")
    (source / "inference.pdiparams").write_bytes(b"parameters")
    (source / ".cache").mkdir()
    (source / ".cache" / "generated").write_bytes(b"do not copy")

    prepared = native_paths.ascii_resource_directory(source, "paddle")
    assert str(prepared).isascii()
    assert prepared.is_relative_to(native_cache)
    assert not (prepared / ".cache").exists()
    model = prepared / "inference.json"
    modified_at = model.stat().st_mtime_ns
    assert native_paths.ascii_resource_directory(source, "paddle") == prepared
    assert model.stat().st_mtime_ns == modified_at

    model.write_bytes(b"corrupt cached model")
    assert native_paths.ascii_resource_directory(source, "paddle") == prepared
    assert model.read_bytes() == b"model one"

    (source / "inference.json").write_bytes(b"model two")
    updated = native_paths.ascii_resource_directory(source, "paddle")
    assert updated != prepared
    assert (updated / "inference.json").read_bytes() == b"model two"
    assert model.read_bytes() == b"model one"


def test_ascii_resources_keep_the_original_path(native_cache: Path, tmp_path: Path) -> None:
    source = tmp_path / "english"
    source.mkdir()
    assert native_paths.ascii_resource_directory(source, "paddle") == source
    assert not native_cache.exists()


def test_only_requested_model_directories_are_copied(native_cache: Path, tmp_path: Path) -> None:
    source = tmp_path / "中文安装目录"
    for name in ("mobile", "server"):
        model = source / "official_models" / name
        model.mkdir(parents=True)
        (model / "model").write_bytes(name.encode("ascii"))
    prepared = native_paths.ascii_resource_directory(
        source, "paddle", subdirectories=("official_models/mobile",)
    )
    assert (prepared / "official_models" / "mobile" / "model").read_bytes() == b"mobile"
    assert not (prepared / "official_models" / "server").exists()


def test_native_cache_skips_chinese_and_unwritable_roots(monkeypatch, tmp_path: Path) -> None:
    chinese = tmp_path / "中文用户名"
    blocked = tmp_path / "blocked"
    blocked.write_bytes(b"file, not a directory")
    writable = tmp_path / "writable"
    monkeypatch.setattr(native_paths, "_cache_candidates", lambda: [chinese, blocked, writable])

    assert native_paths.native_cache_dir("paddle") == writable / "paddle"
    assert not chinese.exists()


def test_native_cache_failure_explains_how_to_select_a_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(native_paths, "_cache_candidates", lambda: [tmp_path / "中文用户"])
    with pytest.raises(native_paths.NativePathError, match="AUTO_BDSP_RNG_NATIVE_CACHE_HOME"):
        native_paths.native_cache_dir("paddle")


def test_native_cache_skips_long_roots_even_when_they_are_ascii(monkeypatch, tmp_path: Path) -> None:
    long_root = tmp_path / ("a" * max(1, 248 - len(str(tmp_path / "paddle")) - 80))
    short_root = tmp_path / "short"
    monkeypatch.setattr(native_paths, "_cache_candidates", lambda: [long_root, short_root])

    assert native_paths.native_cache_dir("paddle") == short_root / "paddle"
    assert not long_root.exists()


def test_relative_cache_override_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("AUTO_BDSP_RNG_NATIVE_CACHE_HOME", "relative-cache")
    with pytest.raises(native_paths.NativePathError, match="绝对路径"):
        native_paths.native_cache_dir("paddle")


def test_failed_copy_is_not_published(monkeypatch, native_cache: Path, tmp_path: Path) -> None:
    source = tmp_path / "中文安装目录"
    source.mkdir()
    (source / "model").write_bytes(b"valid")

    def fail_copy(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(native_paths.shutil, "copyfile", fail_copy)
    with pytest.raises(OSError, match="disk full"):
        native_paths.ascii_resource_directory(source, "paddle")
    assert not list((native_cache / "paddle").iterdir())


def test_simultaneous_preparations_return_a_complete_copy(native_cache: Path, tmp_path: Path) -> None:
    source = tmp_path / "中文安装目录"
    source.mkdir()
    (source / "model").write_bytes(b"model" * 1000)
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda _: native_paths.ascii_resource_directory(source, "paddle"), range(4)))
    assert len(set(paths)) == 1
    assert (paths[0] / "model").read_bytes() == b"model" * 1000
