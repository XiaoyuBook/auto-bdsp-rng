from __future__ import annotations

import importlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator
from typing import Any

from auto_bdsp_rng.blink_detection.models import (
    AdvanceEvent,
    BlinkCaptureConfig,
    BlinkObservation,
    EyePreviewResult,
    PokemonBlinkObservation,
    ProjectXsAdvanceResult,
    ProjectXsReidentifyResult,
    ProjectXsSeedResult,
    ProjectXsTidSidResult,
    ProjectXsTrackingConfig,
    SeedState32,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECT_XS_SRC = PROJECT_ROOT / "third_party" / "Project_Xs_CHN" / "src"


class ProjectXsIntegrationError(RuntimeError):
    """Raised when Project_Xs cannot capture or recover a seed."""


@contextmanager
def _project_xs_import_path() -> Iterator[None]:
    src = str(PROJECT_XS_SRC)
    inserted = False
    if src not in sys.path:
        sys.path.insert(0, src)
        inserted = True
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(src)
            except ValueError:
                pass


def _load_module(name: str) -> ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    if not PROJECT_XS_SRC.exists():
        raise ProjectXsIntegrationError(f"Project_Xs source directory not found: {PROJECT_XS_SRC}")
    with _project_xs_import_path():
        return importlib.import_module(name)


def _load_cv2() -> ModuleType:
    try:
        return importlib.import_module("cv2")
    except ImportError as exc:
        raise ProjectXsIntegrationError("OpenCV is required for Project_Xs blink capture") from exc


def _project_xs_crop(crop: tuple[int, int, int, int] | None) -> list[int] | None:
    return None if crop is None else list(crop)


def _coerce_int_tuple(value: object, *, field_name: str, length: int) -> tuple[int, ...]:
    if not isinstance(value, list | tuple) or len(value) != length:
        raise ProjectXsIntegrationError(f"Project_Xs config field {field_name!r} must contain {length} values")
    try:
        return tuple(int(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ProjectXsIntegrationError(f"Project_Xs config field {field_name!r} must contain integers") from exc


def _resolve_project_xs_config_path(config: str | Path) -> Path:
    path = Path(config)
    if path.is_absolute():
        return path
    configs_dir = PROJECT_ROOT / "third_party" / "Project_Xs_CHN" / "configs"
    return configs_dir / path


def _resolve_project_xs_asset_path(raw_path: str, *, config_path: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    project_xs_root = PROJECT_XS_SRC.parent
    candidate = (project_xs_root / path).resolve()
    if candidate.exists():
        return candidate
    return (config_path.parent / path).resolve()


def load_project_xs_config(config: str | Path, *, blink_count: int = 40) -> ProjectXsTrackingConfig:
    """Load a Project_Xs JSON config and normalize paths for this project."""

    config_path = _resolve_project_xs_config_path(config).resolve()
    try:
        with config_path.open("r", encoding="utf-8") as file:
            raw_config = json.load(file)
    except OSError as exc:
        raise ProjectXsIntegrationError(f"Cannot read Project_Xs config: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ProjectXsIntegrationError(f"Invalid Project_Xs JSON config: {config_path}") from exc

    try:
        eye_image_path = _resolve_project_xs_asset_path(str(raw_config["image"]), config_path=config_path)
        roi = _coerce_int_tuple(raw_config["view"], field_name="view", length=4)
        crop = _coerce_int_tuple(raw_config.get("crop", (0, 0, 0, 0)), field_name="crop", length=4)
    except KeyError as exc:
        raise ProjectXsIntegrationError(f"Project_Xs config is missing required field: {exc.args[0]}") from exc

    capture_config = BlinkCaptureConfig(
        eye_image_path=eye_image_path,
        roi=roi,  # type: ignore[arg-type]
        threshold=float(raw_config.get("thresh", 0.9)),
        blink_count=blink_count,
        monitor_window=bool(raw_config.get("MonitorWindow", True)),
        window_prefix=str(raw_config.get("WindowPrefix", "SysDVR-Client [PID ")),
        crop=crop,  # type: ignore[arg-type]
        camera=int(raw_config.get("camera", 0)),
    )
    return ProjectXsTrackingConfig(
        source_path=config_path,
        capture=capture_config,
        npc=int(raw_config.get("npc", 0)),
        pokemon_npc=int(raw_config.get("pokemon_npc", 0)),
        timeline_npc=int(raw_config.get("timeline_npc", 0)),
        display_percent=int(raw_config.get("display_percent", 100)),
    )


def capture_player_blinks(config: BlinkCaptureConfig) -> BlinkObservation:
    """Capture player blink observations through Project_Xs tracking logic."""

    cv2 = _load_cv2()
    rngtool = _load_module("rngtool")
    eye_image = cv2.imread(str(config.eye_image_path), cv2.IMREAD_GRAYSCALE)
    if eye_image is None:
        raise ProjectXsIntegrationError(f"Cannot read eye template image: {config.eye_image_path}")

    try:
        blinks, intervals, offset_time = rngtool.tracking_blink(
            eye_image,
            *config.roi,
            threshold=config.threshold,
            size=config.blink_count,
            monitor_window=config.monitor_window,
            window_prefix=config.window_prefix,
            crop=_project_xs_crop(config.crop),
            camera=config.camera,
            tk_window=None,
        )
    except Exception as exc:  # Project_Xs raises broad UI/capture exceptions.
        raise ProjectXsIntegrationError("Project_Xs blink tracking failed") from exc

    return BlinkObservation.from_sequences(blinks, intervals, offset_time)


def capture_pokemon_blinks(config: BlinkCaptureConfig) -> PokemonBlinkObservation:
    """Capture Pokemon blink intervals through Project_Xs tracking logic."""

    cv2 = _load_cv2()
    rngtool = _load_module("rngtool")
    eye_image = cv2.imread(str(config.eye_image_path), cv2.IMREAD_GRAYSCALE)
    if eye_image is None:
        raise ProjectXsIntegrationError(f"Cannot read eye template image: {config.eye_image_path}")

    try:
        intervals = rngtool.tracking_poke_blink(
            eye_image,
            *config.roi,
            threshold=config.threshold,
            size=config.blink_count,
            monitor_window=config.monitor_window,
            window_prefix=config.window_prefix,
            crop=_project_xs_crop(config.crop),
            camera=config.camera,
            tk_window=None,
        )
    except Exception as exc:
        raise ProjectXsIntegrationError("Project_Xs Pokemon blink tracking failed") from exc

    return PokemonBlinkObservation.from_sequence(intervals)


def capture_preview_frame(config: BlinkCaptureConfig) -> Any:
    """Capture one raw frame using the same source settings as Project_Xs."""

    cv2 = _load_cv2()
    if config.monitor_window:
        windowcapture = _load_module("windowcapture")
        video = windowcapture.WindowCapture(config.window_prefix, _project_xs_crop(config.crop))
    else:
        backend = cv2.CAP_ANY
        video = cv2.VideoCapture(config.camera, backend)
        video.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        video.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        video.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    try:
        ok, frame = video.read()
    except Exception as exc:
        raise ProjectXsIntegrationError("Project_Xs frame capture failed") from exc
    finally:
        release = getattr(video, "release", None)
        if callable(release):
            release()

    if not ok or frame is None:
        raise ProjectXsIntegrationError("Project_Xs frame capture returned an empty frame")
    return frame


def save_preview_frame(config: BlinkCaptureConfig, output_path: str | Path) -> Path:
    """Capture one preview frame and save it through OpenCV."""

    cv2 = _load_cv2()
    output = Path(output_path)
    frame = capture_preview_frame(config)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        saved = cv2.imwrite(str(output), frame)
    except Exception as exc:
        raise ProjectXsIntegrationError(f"Cannot save preview frame: {output}") from exc
    if not saved:
        raise ProjectXsIntegrationError(f"Cannot save preview frame: {output}")
    return output


def _load_eye_template(config: BlinkCaptureConfig) -> Any:
    cv2 = _load_cv2()
    eye_image = cv2.imread(str(config.eye_image_path), cv2.IMREAD_GRAYSCALE)
    if eye_image is None:
        raise ProjectXsIntegrationError(f"Cannot read eye template image: {config.eye_image_path}")
    return eye_image


def render_eye_preview(config: BlinkCaptureConfig, frame: Any) -> tuple[Any, EyePreviewResult]:
    """Draw ROI and eye-template match box on a captured frame."""

    cv2 = _load_cv2()
    eye_image = _load_eye_template(config)
    roi_x, roi_y, roi_w, roi_h = config.roi
    eye_width, eye_height = eye_image.shape[::-1]
    if roi_w < eye_width or roi_h < eye_height:
        raise ProjectXsIntegrationError("ROI is smaller than the configured eye template")

    try:
        annotated = frame.copy()
        roi = frame[roi_y : roi_y + roi_h, roi_x : roi_x + roi_w]
        roi_gray = roi if len(roi.shape) == 2 else cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
        result = cv2.matchTemplate(roi_gray, eye_image, cv2.TM_CCOEFF_NORMED)
        _, match_score, _, max_loc = cv2.minMaxLoc(result)
    except Exception as exc:
        raise ProjectXsIntegrationError("Eye template preview matching failed") from exc

    roi_bottom_right = (roi_x + roi_w, roi_y + roi_h)
    cv2.rectangle(annotated, (roi_x, roi_y), roi_bottom_right, (0, 0, 255), 2)
    match_location = (max_loc[0] + roi_x, max_loc[1] + roi_y)
    match_bottom_right = (match_location[0] + eye_width, match_location[1] + eye_height)
    color = (0, 255, 0) if match_score >= config.threshold else (0, 255, 255)
    cv2.rectangle(annotated, match_location, match_bottom_right, color, 2)

    preview = EyePreviewResult(
        roi=config.roi,
        match_score=float(match_score),
        match_location=match_location,
        template_size=(eye_width, eye_height),
        threshold=config.threshold,
    )
    return annotated, preview


def save_eye_preview(config: BlinkCaptureConfig, output_path: str | Path) -> tuple[Path, EyePreviewResult]:
    """Capture a frame, draw eye-template preview information, and save it."""

    cv2 = _load_cv2()
    output = Path(output_path)
    frame = capture_preview_frame(config)
    annotated, preview = render_eye_preview(config, frame)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        saved = cv2.imwrite(str(output), annotated)
    except Exception as exc:
        raise ProjectXsIntegrationError(f"Cannot save eye preview: {output}") from exc
    if not saved:
        raise ProjectXsIntegrationError(f"Cannot save eye preview: {output}")
    return output, preview


def recover_seed_from_observation(
    observation: BlinkObservation,
    *,
    npc: int = 0,
) -> ProjectXsSeedResult:
    """Recover and normalize Project_Xs Xorshift state from blink observations."""

    rngtool = _load_module("rngtool")
    try:
        rng = rngtool.recov(list(observation.blinks), list(observation.intervals), npc=npc)
    except AssertionError as exc:
        raise ProjectXsIntegrationError("Project_Xs could not validate the recovered seed") from exc
    except Exception as exc:
        raise ProjectXsIntegrationError("Project_Xs seed recovery failed") from exc

    try:
        state = SeedState32.from_words(rng.get_state())
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProjectXsIntegrationError("Project_Xs returned an invalid seed state") from exc

    return ProjectXsSeedResult(state=state, observation=observation)


def reidentify_seed_from_observation(
    state: SeedState32,
    observation: BlinkObservation,
    *,
    npc: int = 0,
    search_min: int = 0,
    search_max: int = 1_000_000,
) -> ProjectXsReidentifyResult:
    """Reidentify Project_Xs Xorshift state from later blink intervals."""

    rngtool = _load_module("rngtool")
    xorshift = _load_module("xorshift")
    try:
        rng = xorshift.Xorshift(*state.words)
        reidentified_rng, advances = rngtool.reidentiy_by_intervals(
            rng,
            list(observation.intervals),
            npc=npc,
            search_min=search_min,
            search_max=search_max,
            return_advance=True,
        )
    except Exception as exc:
        raise ProjectXsIntegrationError("Project_Xs reidentify failed") from exc

    if reidentified_rng is None:
        raise ProjectXsIntegrationError("Project_Xs reidentify did not find a matching state")

    try:
        reidentified_state = SeedState32.from_words(reidentified_rng.get_state())
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProjectXsIntegrationError("Project_Xs reidentify returned an invalid seed state") from exc

    return ProjectXsReidentifyResult(
        state=reidentified_state,
        observation=observation,
        advances=int(advances),
    )


def advance_seed_state(state: SeedState32, advances: int) -> ProjectXsAdvanceResult:
    """Advance a Project_Xs Xorshift state by a fixed amount."""

    if advances < 0:
        raise ProjectXsIntegrationError("Advances must be non-negative")

    xorshift = _load_module("xorshift")
    try:
        rng = xorshift.Xorshift(*state.words)
        rng.advance(advances)
        advanced_state = SeedState32.from_words(rng.get_state())
    except Exception as exc:
        raise ProjectXsIntegrationError("Project_Xs seed advance failed") from exc

    return ProjectXsAdvanceResult(state=advanced_state, advances=advances)


def recover_tidsid_seed_from_observation(observation: PokemonBlinkObservation) -> ProjectXsTidSidResult:
    """Recover Project_Xs seed from Pokemon blink intervals used by TID/SID flow."""

    rngtool = _load_module("rngtool")
    try:
        rng = rngtool.recov_by_munchlax(list(observation.intervals))
    except (AssertionError, IndexError) as exc:
        raise ProjectXsIntegrationError("Project_Xs TID/SID seed recovery could not validate intervals") from exc
    except Exception as exc:
        raise ProjectXsIntegrationError("Project_Xs TID/SID seed recovery failed") from exc

    try:
        state = SeedState32.from_words(rng.get_state())
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProjectXsIntegrationError("Project_Xs TID/SID recovery returned an invalid seed state") from exc

    return ProjectXsTidSidResult(state=state, observation=observation)


def track_advances(
    state: SeedState32,
    *,
    steps: int,
    npc: int = 0,
    start_advances: int = 0,
) -> tuple[AdvanceEvent, ...]:
    """Track future Project_Xs advance blink values from a seed state."""

    if steps < 0:
        raise ProjectXsIntegrationError("Track steps must be non-negative")
    if npc < 0:
        raise ProjectXsIntegrationError("NPC count must be non-negative")

    xorshift = _load_module("xorshift")
    try:
        rng = xorshift.Xorshift(*state.words)
        events = []
        current_advance = start_advances
        step_size = npc + 1
        for _ in range(steps):
            current_advance += step_size
            rand = rng.get_next_rand_sequence(step_size)[-1]
            events.append(AdvanceEvent(advance=current_advance, rand=int(rand)))
    except Exception as exc:
        raise ProjectXsIntegrationError("Project_Xs advance tracking failed") from exc

    return tuple(events)
