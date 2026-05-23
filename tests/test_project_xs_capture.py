from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_bdsp_rng.blink_detection import BlinkCaptureConfig
from auto_bdsp_rng.blink_detection import project_xs


class _FakeEyeImage:
    shape = (1, 1)


class _FakeFrame:
    def __getitem__(self, _key):
        return object()


class _FakeRoi:
    def __eq__(self, _other):
        return SimpleNamespace(all=lambda: False)


class _FakeVideo:
    def read(self):
        return True, _FakeFrame()

    def set(self, *_args):
        return None

    def release(self):
        return None


class _FakeCv2:
    CAP_ANY = 0
    CAP_V4L = 0
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_BUFFERSIZE = 38
    COLOR_RGB2GRAY = 1
    TM_CCOEFF_NORMED = 5

    def __init__(self, matches: list[float]):
        self._matches = list(matches)

    def VideoCapture(self, *_args):
        return _FakeVideo()

    def cvtColor(self, *_args):
        return _FakeRoi()

    def matchTemplate(self, *_args):
        return object()

    def minMaxLoc(self, *_args):
        return None, self._matches.pop(0), None, (0, 0)

    def rectangle(self, *_args):
        return None

    def destroyAllWindows(self):
        return None


def _config(blink_count: int) -> BlinkCaptureConfig:
    return BlinkCaptureConfig(
        eye_image_path=Path("eye.png"),
        roi=(0, 0, 1, 1),
        blink_count=blink_count,
        threshold=0.9,
        monitor_window=False,
        camera=0,
    )


def test_tracking_blink_discards_only_first_blink_inside_warmup(monkeypatch):
    monkeypatch.setattr(project_xs, "_load_cv2", lambda: _FakeCv2([0.5, 1.0, 0.5, 1.0, 0.5, 1.0]))
    times = iter([0.0, 0.5, 1.3, 2.0, 2.8, 3.9, 4.7])
    monkeypatch.setattr(project_xs.time, "perf_counter", lambda: next(times))
    progress: list[tuple[int, int]] = []

    blinks, intervals, offset_time = project_xs._tracking_blink_controlled(
        _FakeEyeImage(),
        _config(2),
        should_stop=None,
        frame_callback=None,
        progress_callback=lambda done, total: progress.append((done, total)),
        show_window=False,
        discard_first_blink_within_seconds=1.0,
    )

    assert blinks == [0, 0]
    assert intervals == [1, 2]
    assert offset_time == 3.9
    assert progress == [(1, 2), (2, 2)]


def test_tracking_blink_keeps_first_blink_after_warmup(monkeypatch):
    monkeypatch.setattr(project_xs, "_load_cv2", lambda: _FakeCv2([0.5, 1.0, 0.5, 1.0]))
    times = iter([0.0, 1.2, 2.0, 3.0, 3.8])
    monkeypatch.setattr(project_xs.time, "perf_counter", lambda: next(times))

    blinks, intervals, _offset_time = project_xs._tracking_blink_controlled(
        _FakeEyeImage(),
        _config(1),
        should_stop=None,
        frame_callback=None,
        progress_callback=None,
        show_window=False,
        discard_first_blink_within_seconds=1.0,
    )

    assert blinks == [0]
    assert intervals == [1]


def test_tracking_pokemon_blink_discards_only_first_blink_inside_warmup(monkeypatch):
    monkeypatch.setattr(project_xs, "_load_cv2", lambda: _FakeCv2([0.5, 1.0, 0.5, 1.0, 0.5, 1.0]))
    times = iter([0.0, 0.5, 1.3, 2.0, 2.8, 3.9, 4.7])
    monkeypatch.setattr(project_xs.time, "perf_counter", lambda: next(times))
    progress: list[tuple[int, int]] = []

    intervals = project_xs._tracking_poke_blink_controlled(
        _FakeEyeImage(),
        _config(2),
        should_stop=None,
        frame_callback=None,
        progress_callback=lambda done, total: progress.append((done, total)),
        show_window=False,
        discard_first_blink_within_seconds=1.0,
    )

    assert intervals == pytest.approx([1.5, 1.9])
    assert progress == [(1, 2), (2, 2)]
