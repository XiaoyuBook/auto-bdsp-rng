from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from auto_bdsp_rng.blink_detection import (
    BlinkCaptureConfig,
    BlinkObservation,
    BrokerFrameCapture,
    PokemonBlinkObservation,
    PreviewFrameCapture,
    SeedState32,
    advance_seed_state,
    capture_pokemon_blinks,
    capture_player_blinks,
    capture_preview_frame,
    load_project_xs_config,
    plan_timeline,
    reidentify_seed_from_observation_noisy,
    reidentify_seed_from_observation,
    render_eye_preview,
    recover_seed_from_observation,
    recover_tidsid_seed_from_observation,
    save_eye_preview,
    save_preview_frame,
    save_project_xs_config,
    track_advances,
)
from auto_bdsp_rng.blink_detection.project_xs import ProjectXsIntegrationError
import auto_bdsp_rng.blink_detection.project_xs as project_xs_module


def test_broker_frame_capture_returns_private_bgr_copy_and_closes_only_client():
    original = np.arange(18, dtype=np.uint8).reshape((2, 3, 3))

    class Client:
        def __init__(self):
            self.closed = False

        def read_array(self):
            return original

        def close(self):
            self.closed = True

    client = Client()
    capture = BrokerFrameCapture(lambda: client)

    ok, frame = capture.read()
    frame[0, 0, 0] = 255
    capture.release()

    assert ok
    assert frame is not original
    assert original[0, 0, 0] == 0
    assert client.closed


def test_blink_capture_config_keeps_legacy_default_and_accepts_broker_factory(tmp_path):
    legacy = BlinkCaptureConfig(tmp_path / "eye.png", (0, 0, 1, 1))
    shared = BlinkCaptureConfig(
        tmp_path / "eye.png",
        (0, 0, 1, 1),
        source="broker",
        frame_source_factory=lambda: object(),
    )

    assert not legacy.uses_shared_video_source
    assert shared.uses_shared_video_source


class FakeRng:
    def __init__(self, *state):
        self.state = state or (0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)

    def get_state(self):
        return list(self.state)

    def advance(self, advances):
        self.state = (0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, advances)

    def get_next_rand_sequence(self, length):
        return [0x10 + index for index in range(length)]

    def next(self):
        return 0x12345671

    def rangefloat(self, minimum, maximum):
        return 0.5


def test_capture_player_blinks_can_stop_without_project_xs_control_kwargs(monkeypatch, tmp_path):
    class FakeCv2:
        CAP_ANY = 0
        CAP_V4L = 1
        CAP_PROP_FRAME_WIDTH = 3
        CAP_PROP_FRAME_HEIGHT = 4
        CAP_PROP_BUFFERSIZE = 5

        class VideoCapture:
            def __init__(self, *_args):
                self.released = False

            def set(self, *_args):
                pass

            def read(self):
                return True, np.zeros((4, 4, 3), dtype=np.uint8)

            def release(self):
                self.released = True

        IMREAD_GRAYSCALE = 0
        COLOR_RGB2GRAY = 1
        TM_CCOEFF_NORMED = 2

        imdecode = staticmethod(lambda *_args: np.zeros((1, 1), dtype=np.uint8))
        cvtColor = staticmethod(lambda frame, _mode: frame[:, :, 0])
        matchTemplate = staticmethod(lambda *_args: np.array([[0.0]], dtype=np.float32))
        minMaxLoc = staticmethod(lambda _result: (0.0, 0.0, (0, 0), (0, 0)))
        rectangle = staticmethod(lambda *_args: None)
        destroyAllWindows = staticmethod(lambda: None)

    eye = tmp_path / "eye.png"
    eye.write_bytes(b"fake")
    monkeypatch.setitem(sys.modules, "cv2", FakeCv2)
    monkeypatch.setitem(sys.modules, "numpy", np)
    monkeypatch.setitem(
        sys.modules,
        "rngtool",
        types.SimpleNamespace(tracking_blink=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unused"))),
    )

    with pytest.raises(ProjectXsIntegrationError, match="stopped"):
        capture_player_blinks(
            BlinkCaptureConfig(eye_image_path=eye, roi=(0, 0, 1, 1)),
            should_stop=lambda: True,
            frame_callback=lambda _frame: None,
            progress_callback=lambda _done, _total: None,
            show_window=False,
        )


class FakeVideoCapture:
    last_instance = None

    def __init__(self, camera, backend):
        self.camera = camera
        self.backend = backend
        self.settings = []
        self.released = False
        FakeVideoCapture.last_instance = self

    def set(self, prop, value):
        self.settings.append((prop, value))

    def read(self):
        return True, "camera-frame"

    def release(self):
        self.released = True


class FakeWindowCapture:
    last_instance = None

    def __init__(self, window_prefix, crop):
        self.window_prefix = window_prefix
        self.crop = crop
        self.released = False
        FakeWindowCapture.last_instance = self

    def read(self):
        return True, "window-frame"

    def release(self):
        self.released = True


class FakeQtWindowCapture:
    last_instance = None

    def __init__(self, target, crop):
        self.target = target
        self.crop = crop
        self.released = False
        FakeQtWindowCapture.last_instance = self

    def read(self):
        return True, "obs-frame"

    def release(self):
        self.released = True


def test_seed_state_formats_words_and_seed64_pair():
    state = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)

    assert state.format_words() == ("12345678", "9ABCDEF0", "11111111", "22222222")
    assert state.format_seed64_pair() == ("123456789ABCDEF0", "1111111122222222")


def test_seed_state_parses_hex_words():
    state = SeedState32.from_hex_words(["12345678", "9abcdef0", "11111111", "22222222"])

    assert state.words == (0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)


def test_recover_seed_from_observation_uses_project_xs_rngtool(monkeypatch):
    fake_rngtool = types.SimpleNamespace(
        recov=lambda blinks, intervals, npc=0: FakeRng(),
    )
    monkeypatch.setitem(sys.modules, "rngtool", fake_rngtool)
    observation = BlinkObservation.from_sequences([0, 1, 0], [0, 12, 24])

    result = recover_seed_from_observation(observation, npc=0)

    assert result.state.format_words() == ("12345678", "9ABCDEF0", "11111111", "22222222")
    assert result.observation == observation
    assert result.as_dict()["seed_0_1"] == ["123456789ABCDEF0", "1111111122222222"]


def test_recover_seed_from_observation_wraps_project_xs_failures(monkeypatch):
    def fail_recov(_blinks, _intervals, npc=0):
        raise AssertionError("bad seed")

    monkeypatch.setitem(sys.modules, "rngtool", types.SimpleNamespace(recov=fail_recov))
    observation = BlinkObservation.from_sequences([0], [0])

    with pytest.raises(ProjectXsIntegrationError):
        recover_seed_from_observation(observation)


def test_reidentify_seed_from_observation_prefers_native(monkeypatch):
    native_calls: list[tuple[SeedState32, tuple[int, ...], int, int, int]] = []

    def fake_native(state, observation, *, npc=0, search_min=0, search_max=0):
        native_calls.append((state, observation.intervals, npc, search_min, search_max))
        return SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD), 42

    def fail_reidentify(*_args, **_kwargs):
        raise AssertionError("Project_Xs fallback should not be used when native succeeds")

    monkeypatch.setattr(project_xs_module, "_try_native_reidentify_by_intervals", fake_native)
    monkeypatch.setitem(sys.modules, "rngtool", types.SimpleNamespace(reidentiy_by_intervals=fail_reidentify))
    state = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)
    observation = BlinkObservation.from_sequences([], [0, 12, 24])

    result = reidentify_seed_from_observation(state, observation, npc=1, search_min=10, search_max=100)

    assert native_calls == [(state, (0, 12, 24), 1, 10, 100)]
    assert result.advances == 42
    assert result.state.format_seed64_pair() == ("AAAAAAAABBBBBBBB", "CCCCCCCCDDDDDDDD")


def test_reidentify_seed_from_observation_falls_back_to_project_xs_rngtool(monkeypatch):
    def fake_reidentify(rng, intervals, npc=0, search_min=0, search_max=0, return_advance=False):
        assert rng.get_state() == [0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222]
        assert intervals == [0, 12, 24]
        assert npc == 1
        assert search_min == 10
        assert search_max == 100
        assert return_advance is True
        return FakeRng(), 42

    monkeypatch.setattr(project_xs_module, "_try_native_reidentify_by_intervals", lambda *_args, **_kwargs: None)
    monkeypatch.setitem(sys.modules, "xorshift", types.SimpleNamespace(Xorshift=FakeRng))
    monkeypatch.setitem(sys.modules, "rngtool", types.SimpleNamespace(reidentiy_by_intervals=fake_reidentify))
    state = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)
    observation = BlinkObservation.from_sequences([], [0, 12, 24])

    result = reidentify_seed_from_observation(state, observation, npc=1, search_min=10, search_max=100)

    assert result.advances == 42
    assert result.state.format_seed64_pair() == ("123456789ABCDEF0", "1111111122222222")


def test_reidentify_seed_from_observation_uses_chinese_failure_message(monkeypatch):
    def fail_reidentify(*_args, **_kwargs):
        raise RuntimeError("failed")

    monkeypatch.setattr(project_xs_module, "_try_native_reidentify_by_intervals", lambda *_args, **_kwargs: None)
    monkeypatch.setitem(sys.modules, "xorshift", types.SimpleNamespace(Xorshift=FakeRng))
    monkeypatch.setitem(sys.modules, "rngtool", types.SimpleNamespace(reidentiy_by_intervals=fail_reidentify))
    state = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)
    observation = BlinkObservation.from_sequences([], [0, 12, 24])

    with pytest.raises(ProjectXsIntegrationError, match="Project_Xs 校正失败"):
        reidentify_seed_from_observation(state, observation)


def test_noisy_reidentify_seed_from_observation_prefers_native(monkeypatch):
    native_calls: list[tuple[SeedState32, tuple[int, ...], int, int]] = []

    def fake_native(state, observation, *, search_min=0, search_max=0):
        native_calls.append((state, observation.intervals, search_min, search_max))
        return SeedState32(0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD), 43

    def fail_reidentify_noisy(*_args, **_kwargs):
        raise AssertionError("Project_Xs noisy fallback should not be used when native succeeds")

    monkeypatch.setattr(project_xs_module, "_try_native_reidentify_by_intervals_noisy", fake_native)
    monkeypatch.setitem(sys.modules, "rngtool", types.SimpleNamespace(reidentiy_by_intervals_noisy=fail_reidentify_noisy))
    state = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)
    observation = BlinkObservation.from_sequences([], [0, 12, 24])

    result = reidentify_seed_from_observation_noisy(state, observation, search_min=10, search_max=100)

    assert native_calls == [(state, (0, 12, 24), 10, 100)]
    assert result.advances == 43
    assert result.state.format_seed64_pair() == ("AAAAAAAABBBBBBBB", "CCCCCCCCDDDDDDDD")


def test_noisy_reidentify_seed_from_observation_falls_back_to_project_xs_rngtool(monkeypatch):
    def fake_reidentify_noisy(rng, intervals, search_min=0, search_max=0):
        assert rng.get_state() == [0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222]
        assert intervals == [0, 12, 24]
        assert search_min == 10
        assert search_max == 100
        return FakeRng(), 43

    monkeypatch.setattr(project_xs_module, "_try_native_reidentify_by_intervals_noisy", lambda *_args, **_kwargs: None)
    monkeypatch.setitem(sys.modules, "xorshift", types.SimpleNamespace(Xorshift=FakeRng))
    monkeypatch.setitem(
        sys.modules,
        "rngtool",
        types.SimpleNamespace(reidentiy_by_intervals_noisy=fake_reidentify_noisy),
    )
    state = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)
    observation = BlinkObservation.from_sequences([], [0, 12, 24])

    result = reidentify_seed_from_observation_noisy(state, observation, search_min=10, search_max=100)

    assert result.advances == 43
    assert result.state.format_seed64_pair() == ("123456789ABCDEF0", "1111111122222222")


def test_noisy_reidentify_seed_from_observation_uses_chinese_failure_message(monkeypatch):
    def fail_reidentify(*_args, **_kwargs):
        raise RuntimeError("failed")

    monkeypatch.setattr(project_xs_module, "_try_native_reidentify_by_intervals_noisy", lambda *_args, **_kwargs: None)
    monkeypatch.setitem(sys.modules, "xorshift", types.SimpleNamespace(Xorshift=FakeRng))
    monkeypatch.setitem(
        sys.modules,
        "rngtool",
        types.SimpleNamespace(reidentiy_by_intervals_noisy=fail_reidentify),
    )
    state = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)
    observation = BlinkObservation.from_sequences([], [0, 12, 24])

    with pytest.raises(ProjectXsIntegrationError, match="Project_Xs 抗干扰校正失败"):
        reidentify_seed_from_observation_noisy(state, observation)


def test_advance_seed_state_uses_project_xs_xorshift(monkeypatch):
    monkeypatch.setitem(sys.modules, "xorshift", types.SimpleNamespace(Xorshift=FakeRng))
    state = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)

    result = advance_seed_state(state, 7)

    assert result.advances == 7
    assert result.state.format_words() == ("AAAAAAAA", "BBBBBBBB", "CCCCCCCC", "00000007")


def test_capture_pokemon_blinks_uses_project_xs_rngtool(monkeypatch, tmp_path):
    fake_cv2 = types.SimpleNamespace(
        IMREAD_GRAYSCALE=0,
        imread=lambda _path, _mode: "eye-image",
    )
    fake_rngtool = types.SimpleNamespace(
        tracking_poke_blink=lambda *args, **kwargs: [1.25, 3.5],
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setitem(sys.modules, "rngtool", fake_rngtool)
    config = BlinkCaptureConfig(
        eye_image_path=tmp_path / "eye.png",
        roi=(1, 2, 3, 4),
        blink_count=2,
    )

    observation = capture_pokemon_blinks(config)

    assert observation.intervals == (1.25, 3.5)


def test_capture_player_blinks_from_obs_without_callbacks_uses_controlled(monkeypatch, tmp_path):
    target = object()
    calls = []
    monkeypatch.setattr(project_xs_module, "_read_grayscale_image", lambda _path: "eye-image")
    monkeypatch.setattr(project_xs_module, "_obs_window_target", lambda _config: target)

    def fake_controlled(eye_image, config, **kwargs):
        calls.append((eye_image, config, kwargs))
        return [0, 1], [0, 12], 123.0

    monkeypatch.setattr(project_xs_module, "_tracking_blink_controlled", fake_controlled)
    monkeypatch.setattr(
        project_xs_module,
        "_load_module",
        lambda _name: pytest.fail("OBS capture must not delegate to Project_Xs rngtool"),
    )
    config = BlinkCaptureConfig(
        eye_image_path=tmp_path / "eye.png",
        roi=(1, 2, 3, 4),
        blink_count=2,
        monitor_window=True,
        window_prefix="投影 - 源：窗口采集",
    )

    observation = capture_player_blinks(config)

    assert observation.blinks == (0, 1)
    assert observation.intervals == (0, 12)
    assert observation.offset_time == 123.0
    assert calls == [
        (
            "eye-image",
            config,
            {
                "should_stop": None,
                "frame_callback": None,
                "progress_callback": None,
                "show_window": True,
                "discard_first_blink_within_seconds": None,
            },
        )
    ]


def test_capture_pokemon_blinks_from_obs_without_callbacks_uses_controlled(monkeypatch, tmp_path):
    target = object()
    calls = []
    monkeypatch.setattr(project_xs_module, "_read_grayscale_image", lambda _path: "eye-image")
    monkeypatch.setattr(project_xs_module, "_obs_window_target", lambda _config: target)

    def fake_controlled(eye_image, config, **kwargs):
        calls.append((eye_image, config, kwargs))
        return [1.25, 3.5]

    monkeypatch.setattr(project_xs_module, "_tracking_poke_blink_controlled", fake_controlled)
    monkeypatch.setattr(
        project_xs_module,
        "_load_module",
        lambda _name: pytest.fail("OBS capture must not delegate to Project_Xs rngtool"),
    )
    config = BlinkCaptureConfig(
        eye_image_path=tmp_path / "eye.png",
        roi=(1, 2, 3, 4),
        blink_count=2,
        monitor_window=True,
        window_prefix="投影 - 源：窗口采集",
    )

    observation = capture_pokemon_blinks(config)

    assert observation.intervals == (1.25, 3.5)
    assert calls == [
        (
            "eye-image",
            config,
            {
                "should_stop": None,
                "frame_callback": None,
                "progress_callback": None,
                "show_window": True,
                "discard_first_blink_within_seconds": None,
            },
        )
    ]


def test_capture_pokemon_blinks_can_update_preview_without_popup(monkeypatch, tmp_path):
    class FakeVideoCapture:
        def __init__(self, *_args):
            self.value = 0

        def set(self, *_args):
            return None

        def read(self):
            self.value += 1
            return True, np.full((6, 6, 3), self.value, dtype=np.uint8)

        def release(self):
            return None

    match_values = iter([0.5, 0.95, 0.5])

    def fail_imshow(*_args, **_kwargs):
        raise AssertionError("Pokemon blink capture should use the embedded preview")

    fake_cv2 = types.SimpleNamespace(
        CAP_ANY=0,
        CAP_V4L=0,
        CAP_PROP_FRAME_WIDTH=3,
        CAP_PROP_FRAME_HEIGHT=4,
        CAP_PROP_BUFFERSIZE=5,
        COLOR_RGB2GRAY=6,
        TM_CCOEFF_NORMED=7,
        VideoCapture=FakeVideoCapture,
        cvtColor=lambda frame, _mode: frame[:, :, 0],
        matchTemplate=lambda *_args, **_kwargs: np.array([[1.0]]),
        minMaxLoc=lambda _result: (0.0, next(match_values), (0, 0), (0, 0)),
        rectangle=lambda *_args, **_kwargs: None,
        imshow=fail_imshow,
        waitKey=lambda _delay: -1,
        destroyAllWindows=lambda: None,
    )
    times = iter([0.0, 1.0, 1.8, 3.0])
    frames: list[np.ndarray] = []
    progress: list[tuple[int, int]] = []
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setattr(project_xs_module, "_read_grayscale_image", lambda _path: np.ones((1, 1), dtype=np.uint8))
    monkeypatch.setattr(project_xs_module.time, "perf_counter", lambda: next(times))
    config = BlinkCaptureConfig(
        eye_image_path=tmp_path / "eye.png",
        roi=(0, 0, 4, 4),
        blink_count=2,
        monitor_window=False,
    )

    observation = capture_pokemon_blinks(
        config,
        should_stop=lambda: False,
        frame_callback=frames.append,
        progress_callback=lambda done, total: progress.append((done, total)),
        show_window=False,
    )

    assert observation.intervals == (1.0, 2.0)
    assert len(frames) == 3
    assert progress == [(1, 2), (2, 2)]


def test_capture_player_blinks_keeps_project_xs_src_importable_during_tracking(monkeypatch, tmp_path):
    project_xs_src = str(project_xs_module.PROJECT_XS_SRC)
    monkeypatch.setattr(sys, "path", [path for path in sys.path if path != project_xs_src])
    fake_cv2 = types.SimpleNamespace(
        IMREAD_GRAYSCALE=0,
        imread=lambda _path, _mode: "eye-image",
    )

    def fake_tracking_blink(*_args, **_kwargs):
        assert project_xs_src in sys.path
        return [0, 1], [0, 12], 123.0

    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setitem(sys.modules, "rngtool", types.SimpleNamespace(tracking_blink=fake_tracking_blink))
    config = BlinkCaptureConfig(
        eye_image_path=tmp_path / "eye.png",
        roi=(1, 2, 3, 4),
        blink_count=2,
        monitor_window=True,
    )

    observation = capture_player_blinks(config)

    assert observation.intervals == (0, 12)
    assert project_xs_src not in sys.path


def test_load_eye_template_supports_unicode_windows_paths(tmp_path):
    cv2 = pytest.importorskip("cv2")
    unicode_dir = tmp_path / "中文路径"
    unicode_dir.mkdir()
    eye_path = unicode_dir / "eye.png"
    image = np.full((3, 4), 255, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    encoded.tofile(str(eye_path))
    config = BlinkCaptureConfig(
        eye_image_path=eye_path,
        roi=(0, 0, 4, 3),
    )

    loaded = project_xs_module._load_eye_template(config)

    assert loaded.shape == (3, 4)


def test_capture_pokemon_blinks_keeps_project_xs_src_importable_during_tracking(monkeypatch, tmp_path):
    project_xs_src = str(project_xs_module.PROJECT_XS_SRC)
    monkeypatch.setattr(sys, "path", [path for path in sys.path if path != project_xs_src])
    fake_cv2 = types.SimpleNamespace(
        IMREAD_GRAYSCALE=0,
        imread=lambda _path, _mode: "eye-image",
    )

    def fake_tracking_poke_blink(*_args, **_kwargs):
        assert project_xs_src in sys.path
        return [1.25, 3.5]

    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setitem(sys.modules, "rngtool", types.SimpleNamespace(tracking_poke_blink=fake_tracking_poke_blink))
    config = BlinkCaptureConfig(
        eye_image_path=tmp_path / "eye.png",
        roi=(1, 2, 3, 4),
        blink_count=2,
        monitor_window=True,
    )

    observation = capture_pokemon_blinks(config)

    assert observation.intervals == (1.25, 3.5)
    assert project_xs_src not in sys.path


def test_recover_tidsid_seed_from_observation_uses_project_xs_rngtool(monkeypatch):
    def fake_recov_by_munchlax(intervals):
        assert intervals == [1.25, 3.5]
        return FakeRng()

    monkeypatch.setitem(sys.modules, "rngtool", types.SimpleNamespace(recov_by_munchlax=fake_recov_by_munchlax))
    observation = PokemonBlinkObservation.from_sequence([1.25, 3.5])

    result = recover_tidsid_seed_from_observation(observation)

    assert result.state.format_seed64_pair() == ("123456789ABCDEF0", "1111111122222222")
    assert result.as_dict()["pokemon_intervals"] == [1.25, 3.5]


def test_track_advances_uses_project_xs_xorshift(monkeypatch):
    monkeypatch.setitem(sys.modules, "xorshift", types.SimpleNamespace(Xorshift=FakeRng))
    state = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)

    events = track_advances(state, steps=2, npc=1, start_advances=1)

    assert [event.advance for event in events] == [3, 5]
    assert [event.rand for event in events] == [0x11, 0x11]
    assert events[0].as_dict() == {
        "advance": 3,
        "rand": "00000011",
        "blink_value": "1",
        "is_blink": True,
    }


def test_plan_timeline_outputs_blink_and_pokemon_events(monkeypatch):
    monkeypatch.setitem(sys.modules, "xorshift", types.SimpleNamespace(Xorshift=FakeRng))
    state = SeedState32(0x12345678, 0x9ABCDEF0, 0x11111111, 0x22222222)

    events = plan_timeline(
        state,
        max_events=2,
        timeline_npc=0,
        pokemon_npc=1,
        start_advances=5,
    )

    assert [event.advance for event in events] == [6, 7]
    assert events[0].event_type == "pokemon"
    assert events[0].next_interval == pytest.approx(0.785)
    assert events[1].event_type == "blink"
    assert events[1].as_dict()["blink_value"] == "1"


def test_load_project_xs_config_from_real_submodule_config():
    config = load_project_xs_config("config_cave.json")

    assert config.source_path.name == "config_cave.json"
    assert config.capture.eye_image_path.name == "eye.png"
    assert config.capture.roi == (610, 330, 30, 30)
    assert config.capture.threshold == 0.9
    assert config.capture.monitor_window is True
    assert config.capture.crop == (0, 0, 0, 0)
    assert config.npc == 0


def test_load_project_xs_config_from_absolute_path(tmp_path):
    eye = tmp_path / "eye.png"
    eye.write_bytes(b"not-a-real-image-yet")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        """
        {
          "MonitorWindow": 0,
          "WindowPrefix": "",
          "image": "eye.png",
          "view": [1, 2, 3, 4],
          "thresh": 0.75,
          "crop": [5, 6, 7, 8],
          "camera": 2,
          "npc": 3,
          "pokemon_npc": 4,
          "timeline_npc": 5,
          "display_percent": 60
        }
        """,
        encoding="utf-8",
    )

    config = load_project_xs_config(config_path, blink_count=7)

    assert config.capture.eye_image_path == eye.resolve()
    assert config.capture.roi == (1, 2, 3, 4)
    assert config.capture.blink_count == 7
    assert config.capture.monitor_window is False
    assert config.capture.crop == (5, 6, 7, 8)
    assert config.capture.camera == 2
    assert config.npc == 3
    assert config.pokemon_npc == 4
    assert config.timeline_npc == 5
    assert config.display_percent == 60


def test_save_project_xs_config_round_trips(tmp_path):
    config = load_project_xs_config("config_cave.json")
    output = tmp_path / "saved_config.json"

    saved = save_project_xs_config(config, output)
    reloaded = load_project_xs_config(saved)

    assert saved == output
    assert reloaded.capture.eye_image_path == config.capture.eye_image_path
    assert reloaded.capture.roi == config.capture.roi
    assert reloaded.capture.threshold == config.capture.threshold
    assert reloaded.capture.monitor_window == config.capture.monitor_window
    assert reloaded.npc == config.npc


def test_capture_preview_frame_from_camera(monkeypatch, tmp_path):
    fake_cv2 = types.SimpleNamespace(
        CAP_ANY=100,
        CAP_PROP_FRAME_WIDTH=1,
        CAP_PROP_FRAME_HEIGHT=2,
        CAP_PROP_BUFFERSIZE=3,
        VideoCapture=FakeVideoCapture,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setattr(
        project_xs_module,
        "_obs_window_target",
        lambda _config: pytest.fail("Camera capture must not inspect OBS windows"),
    )
    config = BlinkCaptureConfig(
        eye_image_path=tmp_path / "eye.png",
        roi=(1, 2, 3, 4),
        monitor_window=False,
        camera=2,
    )

    frame = capture_preview_frame(config)

    video = FakeVideoCapture.last_instance
    assert frame == "camera-frame"
    assert video.camera == 2
    assert video.backend == 100
    assert video.released is True
    assert (1, 1920) in video.settings
    assert (2, 1080) in video.settings
    assert (3, 1) in video.settings


def test_capture_preview_frame_from_window(monkeypatch, tmp_path):
    import auto_bdsp_rng.blink_detection.qt_window_capture as qt_window_capture

    fake_cv2 = types.SimpleNamespace()
    fake_windowcapture = types.SimpleNamespace(WindowCapture=FakeWindowCapture)
    fake_win32gui = types.SimpleNamespace(
        EnumWindows=lambda callback, context: callback(123, context),
        IsWindowVisible=lambda hwnd: hwnd == 123,
        GetWindowText=lambda hwnd: "PotPlayer" if hwnd == 123 else "",
    )
    monkeypatch.setattr(qt_window_capture.sys, "platform", "win32")
    monkeypatch.setattr(qt_window_capture, "_process_name_for_window", lambda _hwnd: "potplayermini64.exe")
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(sys.modules, "windowcapture", fake_windowcapture)
    config = BlinkCaptureConfig(
        eye_image_path=tmp_path / "eye.png",
        roi=(1, 2, 3, 4),
        monitor_window=True,
        window_prefix="PotPlayer",
        crop=(0, 0, 0, 0),
    )

    frame = capture_preview_frame(config)

    video = FakeWindowCapture.last_instance
    assert frame == "window-frame"
    assert video.window_prefix == "PotPlayer"
    assert video.crop == [0, 0, 0, 0]
    assert video.released is True


def test_capture_preview_frame_from_obs_uses_qt_window_capture(monkeypatch, tmp_path):
    import auto_bdsp_rng.blink_detection.qt_window_capture as qt_window_capture

    title = "投影 - 源：窗口采集 2"
    fake_win32gui = types.SimpleNamespace(
        EnumWindows=lambda callback, context: callback(321, context),
        IsWindowVisible=lambda hwnd: hwnd == 321,
        GetWindowText=lambda hwnd: title if hwnd == 321 else "",
    )
    fake_windowcapture = types.SimpleNamespace(
        WindowCapture=lambda *_args: pytest.fail("OBS capture must not use Project_Xs WindowCapture")
    )
    monkeypatch.setattr(qt_window_capture.sys, "platform", "win32")
    monkeypatch.setattr(qt_window_capture, "_process_name_for_window", lambda _hwnd: "obs64.exe")
    monkeypatch.setattr(qt_window_capture, "QtWindowCapture", FakeQtWindowCapture)
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(sys.modules, "windowcapture", fake_windowcapture)
    monkeypatch.setitem(sys.modules, "cv2", types.SimpleNamespace())
    config = BlinkCaptureConfig(
        eye_image_path=tmp_path / "eye.png",
        roi=(1, 2, 3, 4),
        monitor_window=True,
        window_prefix="投影 - 源：窗口采集",
        crop=(5, 6, 7, 8),
    )

    frame = capture_preview_frame(config)

    video = FakeQtWindowCapture.last_instance
    assert frame == "obs-frame"
    assert video.target.hwnd == 321
    assert video.target.title == title
    assert video.target.process_name == "obs64.exe"
    assert video.crop == [5, 6, 7, 8]
    assert video.released is True


def test_preview_frame_capture_release_is_idempotent(monkeypatch, tmp_path):
    class FakeReusableCapture:
        def __init__(self):
            self.release_count = 0

        def read(self):
            return True, "preview-frame"

        def release(self):
            self.release_count += 1

    video = FakeReusableCapture()
    monkeypatch.setattr(project_xs_module, "_open_capture_source", lambda _config: video)
    config = BlinkCaptureConfig(
        eye_image_path=tmp_path / "eye.png",
        roi=(1, 2, 3, 4),
        monitor_window=False,
    )
    capture = PreviewFrameCapture(config)

    assert capture.read() == "preview-frame"
    capture.release()
    capture.release()

    assert video.release_count == 1
    with pytest.raises(ProjectXsIntegrationError, match="已经关闭"):
        capture.read()


def test_capture_preview_frame_releases_source_when_read_fails(monkeypatch, tmp_path):
    class FailingCapture:
        def __init__(self):
            self.released = False

        def read(self):
            raise RuntimeError("read failed")

        def release(self):
            self.released = True

    video = FailingCapture()
    monkeypatch.setattr(project_xs_module, "_open_capture_source", lambda _config: video)
    config = BlinkCaptureConfig(
        eye_image_path=tmp_path / "eye.png",
        roi=(1, 2, 3, 4),
        monitor_window=False,
    )

    with pytest.raises(ProjectXsIntegrationError, match="read failed"):
        capture_preview_frame(config)

    assert video.released is True


def test_save_preview_frame_writes_output(monkeypatch, tmp_path):
    saved_paths = []
    fake_cv2 = types.SimpleNamespace(
        CAP_ANY=100,
        CAP_PROP_FRAME_WIDTH=1,
        CAP_PROP_FRAME_HEIGHT=2,
        CAP_PROP_BUFFERSIZE=3,
        VideoCapture=FakeVideoCapture,
        imwrite=lambda path, frame: saved_paths.append((path, frame)) or True,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    output = tmp_path / "debug" / "preview.png"
    config = BlinkCaptureConfig(
        eye_image_path=tmp_path / "eye.png",
        roi=(1, 2, 3, 4),
        monitor_window=False,
    )

    assert save_preview_frame(config, output) == output
    assert saved_paths == [(str(output), "camera-frame")]


def test_render_eye_preview_matches_template(tmp_path):
    import cv2

    eye = np.full((4, 4), 255, dtype=np.uint8)
    eye_path = tmp_path / "eye.png"
    assert cv2.imwrite(str(eye_path), eye)
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    frame[8:12, 9:13] = 255
    config = BlinkCaptureConfig(
        eye_image_path=eye_path,
        roi=(6, 5, 10, 10),
        threshold=0.5,
    )

    annotated, preview = render_eye_preview(config, frame)

    assert annotated.shape == frame.shape
    assert preview.matched is True
    assert preview.match_score >= 0.5
    assert preview.template_size == (4, 4)
    assert preview.roi == (6, 5, 10, 10)


def test_save_eye_preview_writes_annotated_output(monkeypatch, tmp_path):
    import cv2

    eye = np.full((4, 4), 255, dtype=np.uint8)
    eye_path = tmp_path / "eye.png"
    assert cv2.imwrite(str(eye_path), eye)
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    frame[8:12, 9:13] = 255
    saved_paths = []
    config = BlinkCaptureConfig(
        eye_image_path=eye_path,
        roi=(6, 5, 10, 10),
        threshold=0.5,
        monitor_window=False,
    )

    monkeypatch.setattr("auto_bdsp_rng.blink_detection.project_xs.capture_preview_frame", lambda _config: frame)
    monkeypatch.setattr(cv2, "imwrite", lambda path, image: saved_paths.append((path, image.shape)) or True)
    output = tmp_path / "debug" / "eye_preview.png"

    saved, preview = save_eye_preview(config, output)

    assert saved == output
    assert preview.matched is True
    assert saved_paths == [(str(output), frame.shape)]
