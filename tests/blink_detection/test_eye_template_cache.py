from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event
from time import perf_counter
from types import SimpleNamespace

import numpy as np
import pytest

from auto_bdsp_rng.blink_detection import BlinkCaptureConfig
from auto_bdsp_rng.blink_detection import project_xs


cv2 = pytest.importorskip("cv2")


@pytest.fixture
def cache(monkeypatch):
    cache = project_xs._EyeTemplateCache(max_entries=3)
    monkeypatch.setattr(project_xs, "_EYE_TEMPLATE_CACHE", cache)
    return cache


@pytest.fixture
def decodes(monkeypatch):
    calls = []
    original_decode = cv2.imdecode

    def decode(payload, flags):
        calls.append(payload.size)
        return original_decode(payload, flags)

    monkeypatch.setattr(cv2, "imdecode", decode)
    return calls


def _config(path: Path) -> BlinkCaptureConfig:
    return BlinkCaptureConfig(eye_image_path=path, roi=(4, 4, 16, 16))


def _write(path: Path, value: int, shape: tuple[int, int] = (4, 4)) -> np.ndarray:
    image = np.full(shape, value, dtype=np.uint8)
    project_xs.write_image(path, image)
    return image


def test_eye_template_preview_reuses_decode_and_preserves_frame(cache, decodes, tmp_path):
    path = tmp_path / "中文目录" / "眼睛.png"
    eye = np.arange(16, dtype=np.uint8).reshape(4, 4) * 13
    project_xs.write_image(path, eye)
    config = _config(path)
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    frame[8:12, 9:13] = eye[:, :, None]
    original_frame = frame.copy()

    for _ in range(120):
        annotated, preview = project_xs.render_eye_preview(config, frame)
        assert preview.match_location == (9, 8)
        assert preview.template_size == (4, 4)
        assert preview.matched
        assert not np.shares_memory(annotated, frame)
        annotated.fill(123)

    assert len(decodes) == 1
    np.testing.assert_array_equal(frame, original_frame)
    np.testing.assert_array_equal(project_xs._load_eye_template(config), eye)


def test_cached_eye_template_has_immutable_pixels_and_private_metadata(cache, monkeypatch, tmp_path):
    path = tmp_path / "eye.bmp"
    original = _write(path, 17)
    source = original.copy()
    monkeypatch.setattr(project_xs, "_read_grayscale_image", lambda _path: source)
    config = _config(path)

    first = project_xs._load_eye_template(config)
    source.fill(91)
    np.testing.assert_array_equal(first, original)
    with pytest.raises(ValueError):
        first[0, 0] = 99
    with pytest.raises(ValueError):
        first.flags.writeable = True
    first.shape = (16,)
    first.dtype = np.uint16

    second = project_xs._load_eye_template(config)
    assert second is not first
    assert second.shape == (4, 4)
    assert second.dtype == np.uint8
    assert not second.flags.writeable
    np.testing.assert_array_equal(second, original)


@pytest.mark.parametrize("capture_kind", ["player", "pokemon"])
def test_legacy_tracking_mutation_does_not_pollute_cached_preview(cache, decodes, monkeypatch, tmp_path, capture_kind):
    path = tmp_path / "eye.bmp"
    _write(path, 17)
    config = replace(_config(path), monitor_window=False, blink_count=2)
    assert np.all(project_xs._load_eye_template(config) == 17)

    def legacy_tracking(eye, *_args, **_kwargs):
        assert eye.flags.writeable
        eye.fill(89)
        return ([0, 1], [0, 12], 123.0) if capture_kind == "player" else [1.25, 3.5]

    monkeypatch.setattr(
        project_xs,
        "_load_module",
        lambda _name: SimpleNamespace(tracking_blink=legacy_tracking, tracking_poke_blink=legacy_tracking),
    )
    if capture_kind == "player":
        assert project_xs.capture_player_blinks(config).blinks == (0, 1)
    else:
        assert project_xs.capture_pokemon_blinks(config).intervals == (1.25, 3.5)

    assert np.all(project_xs._load_eye_template(config) == 17)
    assert len(decodes) == 2


def test_eye_template_cache_normalizes_aliases_and_switches_paths(cache, decodes, monkeypatch, tmp_path):
    path = tmp_path / "眼睛.bmp"
    other = tmp_path / "other.bmp"
    _write(path, 17)
    _write(other, 89)
    (tmp_path / "subdir").mkdir()
    monkeypatch.chdir(tmp_path)
    aliases = [Path("眼睛.bmp"), path, tmp_path / "subdir" / ".." / "眼睛.bmp"]
    if os.name == "nt":
        aliases.append(Path(str(path).upper()))

    for alias in aliases:
        assert np.all(project_xs._load_eye_template(_config(alias)) == 17)
    assert len(decodes) == 1
    assert np.all(project_xs._load_eye_template(_config(other)) == 89)
    assert np.all(project_xs._load_eye_template(_config(path)) == 17)
    assert len(decodes) == 2


@pytest.mark.parametrize("save_kind", ["in_place", "atomic_same_attributes", "size_only"])
def test_same_path_save_invalidates_cached_template(cache, decodes, tmp_path, save_kind):
    path = tmp_path / "eye.bmp"
    old_image = _write(path, 17)
    config = _config(path)
    old_template = project_xs._load_eye_template(config)
    old_stat = path.stat()
    new_shape = (5, 7) if save_kind == "size_only" else (4, 4)
    new_image = np.full(new_shape, 89, dtype=np.uint8)
    if save_kind == "atomic_same_attributes":
        project_xs.write_image(path, new_image)
    else:
        ok, encoded = cv2.imencode(".bmp", new_image)
        assert ok
        path.write_bytes(encoded.tobytes())
    # Size and file identity must invalidate even when an editor preserves mtime.
    new_mtime = old_stat.st_mtime_ns + 1_000_000 if save_kind == "in_place" else old_stat.st_mtime_ns
    os.utime(path, ns=(old_stat.st_atime_ns, new_mtime))
    if save_kind == "atomic_same_attributes":
        assert path.stat().st_size == old_stat.st_size
        assert path.stat().st_mtime_ns == old_stat.st_mtime_ns

    updated = project_xs._load_eye_template(config)

    np.testing.assert_array_equal(updated, new_image)
    np.testing.assert_array_equal(old_template, old_image)
    assert len(decodes) == 2
    assert len(cache._images) == 1


def test_missing_and_corrupt_templates_are_not_cached_as_success_or_failure(cache, decodes, tmp_path):
    path = tmp_path / "missing.bmp"
    config = _config(path)
    for _ in range(2):
        with pytest.raises(project_xs.ProjectXsCaptureConfigError, match="Cannot read eye template image"):
            project_xs._load_eye_template(config)
    assert not decodes

    _write(path, 17)
    assert np.all(project_xs._load_eye_template(config) == 17)
    path.unlink()
    with pytest.raises(project_xs.ProjectXsCaptureConfigError, match="Cannot read eye template image"):
        project_xs._load_eye_template(config)
    path.write_bytes(b"not an image")
    for _ in range(2):
        with pytest.raises(project_xs.ProjectXsCaptureConfigError, match="Cannot read eye template image"):
            project_xs._load_eye_template(config)
    assert len(decodes) == 3

    _write(path, 89)
    assert np.all(project_xs._load_eye_template(config) == 89)
    assert len(decodes) == 4


@pytest.mark.parametrize("initially_corrupt", [False, True])
def test_template_save_during_read_retries_the_new_revision(cache, decodes, monkeypatch, tmp_path, initially_corrupt):
    path = tmp_path / "eye.bmp"
    _write(path, 17)
    if initially_corrupt:
        path.write_bytes(b"incomplete image")
    original_read = project_xs._read_grayscale_image
    first_read = True

    def racing_read(path):
        nonlocal first_read
        try:
            return original_read(path)
        finally:
            if first_read:
                first_read = False
                _write(path, 89)

    monkeypatch.setattr(project_xs, "_read_grayscale_image", racing_read)
    config = _config(path)

    assert np.all(project_xs._load_eye_template(config) == 89)
    assert np.all(project_xs._load_eye_template(config) == 89)
    assert len(decodes) == 2


def test_continuously_changing_template_has_bounded_retries_and_recovers(cache, decodes, monkeypatch, tmp_path):
    path = tmp_path / "eye.bmp"
    _write(path, 17)
    original_read = project_xs._read_grayscale_image
    value = 30

    def racing_read(path):
        nonlocal value
        image = original_read(path)
        value += 1
        _write(path, value)
        return image

    monkeypatch.setattr(project_xs, "_read_grayscale_image", racing_read)
    config = _config(path)

    with pytest.raises(project_xs.ProjectXsCaptureConfigError, match="Cannot read eye template image"):
        project_xs._load_eye_template(config)
    assert len(decodes) == 3
    assert not cache._images

    monkeypatch.setattr(project_xs, "_read_grayscale_image", original_read)
    assert np.all(project_xs._load_eye_template(config) == value)
    assert len(decodes) == 4


def test_same_path_concurrent_template_load_decodes_once(cache, decodes, monkeypatch, tmp_path):
    path = tmp_path / "eye.bmp"
    expected = _write(path, 17)
    config = _config(path)
    original_read = project_xs._read_grayscale_image
    callers_ready = Barrier(8)
    reading = Event()
    finish_read = Event()

    def blocking_read(path):
        reading.set()
        assert finish_read.wait(5)
        return original_read(path)

    def load():
        callers_ready.wait(5)
        return project_xs._load_eye_template(config)

    monkeypatch.setattr(project_xs, "_read_grayscale_image", blocking_read)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(load) for _ in range(8)]
        try:
            assert reading.wait(5)
        finally:
            finish_read.set()
        images = [future.result(timeout=5) for future in futures]

    assert len(decodes) == 1
    assert len({id(image) for image in images}) == 8
    for image in images:
        assert not image.flags.writeable
        np.testing.assert_array_equal(image, expected)


def test_cache_hit_is_available_while_the_same_loading_stripe_is_busy(cache, decodes, monkeypatch, tmp_path):
    warm = tmp_path / "warm.bmp"
    stripe = hash(os.path.normcase(os.path.abspath(warm))) % len(cache._load_locks)
    cold = next(
        candidate
        for candidate in (tmp_path / f"cold{index}.bmp" for index in range(1024))
        if hash(os.path.normcase(os.path.abspath(candidate))) % len(cache._load_locks) == stripe
    )
    _write(warm, 17)
    _write(cold, 89)
    assert np.all(project_xs._load_eye_template(_config(warm)) == 17)
    original_read = project_xs._read_grayscale_image
    reading = Event()
    finish_read = Event()

    def blocking_read(path):
        reading.set()
        assert finish_read.wait(5)
        return original_read(path)

    monkeypatch.setattr(project_xs, "_read_grayscale_image", blocking_read)
    with ThreadPoolExecutor(max_workers=2) as executor:
        cold_future = executor.submit(project_xs._load_eye_template, _config(cold))
        try:
            assert reading.wait(5)
            warm_future = executor.submit(project_xs._load_eye_template, _config(warm))
            assert np.all(warm_future.result(timeout=1) == 17)
        finally:
            finish_read.set()
        assert np.all(cold_future.result(timeout=5) == 89)
    assert len(decodes) == 2


def test_eye_template_cache_evicts_lru_and_bounds_paths_and_revisions(decodes, monkeypatch, tmp_path):
    cache = project_xs._EyeTemplateCache(max_entries=2)
    monkeypatch.setattr(project_xs, "_EYE_TEMPLATE_CACHE", cache)
    paths = [tmp_path / f"eye{index}.bmp" for index in range(3)]
    for index, path in enumerate(paths):
        _write(path, index)
    config = _config(paths[0])
    for index in (0, 1, 0, 2, 0):
        project_xs._load_eye_template(replace(config, eye_image_path=paths[index]))
    assert len(decodes) == 3
    project_xs._load_eye_template(replace(config, eye_image_path=paths[1]))
    assert len(decodes) == 4
    assert len(cache._images) == 2

    for index in range(30):
        path = tmp_path / f"switched{index}.bmp"
        _write(path, index)
        project_xs._load_eye_template(_config(path))
        assert len(cache._images) <= 2
    for index in range(30):
        _write(paths[0], index)
        assert np.all(project_xs._load_eye_template(config) == index)
        assert len(cache._images) <= 2
    assert len(cache._load_locks) == 16


def test_eye_template_cache_decode_benchmark(cache, decodes, tmp_path):
    """Run with pytest -s to print reproducible decode counts and local timings."""
    path = tmp_path / "eye.png"
    eye = np.arange(128 * 128, dtype=np.uint8).reshape(128, 128)
    project_xs.write_image(path, eye)
    config = _config(path)
    frames = 300
    started = perf_counter()
    for _ in range(frames):
        uncached = project_xs._read_grayscale_image(path)
    uncached_seconds = perf_counter() - started
    uncached_decodes = len(decodes)

    started = perf_counter()
    for _ in range(frames):
        cached = project_xs._load_eye_template(config)
    cached_seconds = perf_counter() - started
    cached_decodes = len(decodes) - uncached_decodes

    np.testing.assert_array_equal(uncached, eye)
    np.testing.assert_array_equal(cached, eye)
    assert uncached_decodes == frames
    assert cached_decodes == 1
    print(
        f"template-cache frames={frames} "
        f"uncached_decodes={uncached_decodes} uncached_ms={uncached_seconds * 1000:.3f} "
        f"cached_decodes={cached_decodes} cached_ms={cached_seconds * 1000:.3f}"
    )
