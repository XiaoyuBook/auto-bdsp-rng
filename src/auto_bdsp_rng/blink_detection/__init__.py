"""Blink detection integration based on Project_Xs_CHN."""

from auto_bdsp_rng.blink_detection.models import (
    BlinkCaptureConfig,
    BlinkObservation,
    ProjectXsSeedResult,
    ProjectXsTrackingConfig,
    SeedState32,
)
from auto_bdsp_rng.blink_detection.project_xs import (
    ProjectXsIntegrationError,
    capture_preview_frame,
    capture_player_blinks,
    load_project_xs_config,
    recover_seed_from_observation,
    save_preview_frame,
)

__all__ = [
    "BlinkCaptureConfig",
    "BlinkObservation",
    "ProjectXsIntegrationError",
    "ProjectXsSeedResult",
    "ProjectXsTrackingConfig",
    "SeedState32",
    "capture_preview_frame",
    "capture_player_blinks",
    "load_project_xs_config",
    "recover_seed_from_observation",
    "save_preview_frame",
]
