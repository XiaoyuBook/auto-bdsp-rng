"""Blink detection integration based on Project_Xs_CHN."""

from auto_bdsp_rng.blink_detection.models import (
    BlinkCaptureConfig,
    BlinkObservation,
    ProjectXsSeedResult,
    SeedState32,
)
from auto_bdsp_rng.blink_detection.project_xs import (
    ProjectXsIntegrationError,
    capture_player_blinks,
    recover_seed_from_observation,
)

__all__ = [
    "BlinkCaptureConfig",
    "BlinkObservation",
    "ProjectXsIntegrationError",
    "ProjectXsSeedResult",
    "SeedState32",
    "capture_player_blinks",
    "recover_seed_from_observation",
]
