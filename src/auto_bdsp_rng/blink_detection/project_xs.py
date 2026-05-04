from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator

from auto_bdsp_rng.blink_detection.models import (
    BlinkCaptureConfig,
    BlinkObservation,
    ProjectXsSeedResult,
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
            crop=config.crop,
            camera=config.camera,
            tk_window=None,
        )
    except Exception as exc:  # Project_Xs raises broad UI/capture exceptions.
        raise ProjectXsIntegrationError("Project_Xs blink tracking failed") from exc

    return BlinkObservation.from_sequences(blinks, intervals, offset_time)


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
