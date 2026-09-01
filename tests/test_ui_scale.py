from __future__ import annotations

import math

import pytest

from auto_bdsp_rng import ui_scale
from auto_bdsp_rng.ui_scale import (
    DisplayMetrics,
    MANAGED_QT_SCALE_FACTOR_MARKER,
    calculate_auto_ui_scale_percent,
    configure_ui_scale_environment,
    detect_all_display_metrics,
    detect_most_constrained_display_metrics,
    detect_primary_display_metrics,
    normalize_ui_scale_setting,
    resolve_ui_scale_percent,
    select_most_constrained_display_metrics,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "auto"),
        (" AUTO ", "auto"),
        ("invalid", "auto"),
        (True, "auto"),
        (math.nan, "auto"),
        (80, 80),
        (80.0, 80),
        ("80%", 80),
        (72, 70),
        (73, 75),
        (1, 50),
        (999, 125),
    ],
)
def test_normalize_ui_scale_setting(value, expected):
    assert normalize_ui_scale_setting(value) == expected


@pytest.mark.parametrize(
    ("width", "height", "system_scale", "expected"),
    [
        (1182, 932, 1.0, 100),
        (2560, 1440, 1.0, 100),
        (1366, 768, 1.0, 80),
        (1920, 1080, 1.5, 75),
        (1920, 1080, 2.0, 55),
        (1366, 728, 1.75, 40),
        (640, 480, 1.0, 45),
        (50, 50, 1.0, 5),
    ],
)
def test_calculate_auto_ui_scale_percent(width, height, system_scale, expected):
    assert calculate_auto_ui_scale_percent(width, height, system_scale) == expected


def test_calculate_auto_ui_scale_uses_largest_step_that_fits():
    assert calculate_auto_ui_scale_percent(952, 752, 1.0) == 80
    assert calculate_auto_ui_scale_percent(951, 751, 1.0) == 75


@pytest.mark.parametrize(
    ("width", "height", "system_scale"),
    [
        (0, 1080, 1.0),
        (1920, -1, 1.0),
        (1920, 1080, 0),
        ("bad", 1080, 1.0),
        (1920, 1080, math.inf),
    ],
)
def test_calculate_auto_ui_scale_invalid_metrics_fall_back_to_100(
    width, height, system_scale
):
    assert calculate_auto_ui_scale_percent(width, height, system_scale) == 100


def test_resolve_ui_scale_percent_handles_manual_auto_and_missing_metrics():
    metrics = DisplayMetrics(1366, 768, 1.0)

    assert resolve_ui_scale_percent(90, metrics) == 90
    assert resolve_ui_scale_percent("auto", metrics) == 80
    assert resolve_ui_scale_percent("auto", None) == 100


def test_select_most_constrained_display_metrics_uses_smallest_auto_scale():
    spacious = DisplayMetrics(2560, 1400, 1.0)
    compact_high_dpi = DisplayMetrics(1366, 728, 1.75)
    medium = DisplayMetrics(1920, 1040, 1.5)

    selected = select_most_constrained_display_metrics(
        (spacious, compact_high_dpi, medium)
    )

    assert selected is compact_high_dpi
    assert resolve_ui_scale_percent("auto", selected) == 40


def test_select_most_constrained_display_metrics_ignores_invalid_values():
    valid = DisplayMetrics(1920, 1040, 1.0)

    assert select_most_constrained_display_metrics((None, valid)) is valid
    assert select_most_constrained_display_metrics((None, "invalid")) is None


def test_configure_environment_preserves_explicit_external_factor():
    environ = {"QT_SCALE_FACTOR": "1.25"}

    def unexpected_provider():
        raise AssertionError("metrics must not be detected for an external scale")

    result = configure_ui_scale_environment("auto", environ, unexpected_provider)

    assert environ == {"QT_SCALE_FACTOR": "1.25"}
    assert result.source == "environment"
    assert result.percent == 125
    assert result.scale_factor == 1.25
    assert result.environment_value == "1.25"


def test_configure_environment_preserves_invalid_external_factor():
    environ = {"QT_SCALE_FACTOR": "managed-elsewhere"}

    result = configure_ui_scale_environment(80, environ, lambda: None)

    assert environ["QT_SCALE_FACTOR"] == "managed-elsewhere"
    assert result.source == "environment"
    assert result.percent is None
    assert result.scale_factor is None


def test_configure_environment_sets_manual_factor_without_detecting_metrics():
    environ: dict[str, str] = {}

    def unexpected_provider():
        raise AssertionError("metrics must not be detected for a manual scale")

    result = configure_ui_scale_environment(80, environ, unexpected_provider)

    assert environ["QT_SCALE_FACTOR"] == "0.8"
    assert environ[MANAGED_QT_SCALE_FACTOR_MARKER] == "1"
    assert result.source == "manual"
    assert result.percent == 80
    assert result.scale_factor == 0.8


def test_configure_environment_sets_automatic_factor():
    environ: dict[str, str] = {}
    metrics = DisplayMetrics(1920, 1080, 1.5)

    result = configure_ui_scale_environment("auto", environ, lambda: metrics)

    assert environ["QT_SCALE_FACTOR"] == "0.75"
    assert environ[MANAGED_QT_SCALE_FACTOR_MARKER] == "1"
    assert result.source == "automatic"
    assert result.percent == 75
    assert result.scale_factor == 0.75


def test_configure_environment_falls_back_when_metrics_provider_fails():
    environ: dict[str, str] = {}

    def failing_provider():
        raise OSError("display API unavailable")

    result = configure_ui_scale_environment("auto", environ, failing_provider)

    assert environ["QT_SCALE_FACTOR"] == "1"
    assert result.source == "fallback"
    assert result.percent == 100
    assert result.scale_factor == 1.0


def test_configure_environment_overwrites_inherited_managed_factor():
    environ = {
        "QT_SCALE_FACTOR": "0.8",
        MANAGED_QT_SCALE_FACTOR_MARKER: "1",
    }

    result = configure_ui_scale_environment(95, environ, lambda: None)

    assert environ["QT_SCALE_FACTOR"] == "0.95"
    assert environ[MANAGED_QT_SCALE_FACTOR_MARKER] == "1"
    assert result.source == "manual"
    assert result.percent == 95
    assert result.scale_factor == 0.95


def test_detect_primary_display_metrics_returns_none_off_windows(monkeypatch):
    monkeypatch.setattr(ui_scale.sys, "platform", "linux")
    monkeypatch.setattr(
        ui_scale,
        "_detect_windows_primary_display_metrics",
        lambda: pytest.fail("Windows APIs must not be called"),
    )

    assert detect_primary_display_metrics() is None


def test_detect_all_display_metrics_uses_every_windows_display(monkeypatch):
    detected = (
        DisplayMetrics(2560, 1400, 1.0),
        DisplayMetrics(1366, 728, 1.75),
    )
    monkeypatch.setattr(ui_scale.sys, "platform", "win32")
    monkeypatch.setattr(ui_scale, "_detect_windows_display_metrics", lambda: detected)
    monkeypatch.setattr(
        ui_scale,
        "detect_primary_display_metrics",
        lambda: pytest.fail("primary fallback must not run"),
    )

    assert detect_all_display_metrics() == detected
    assert detect_most_constrained_display_metrics() == detected[1]


def test_detect_all_display_metrics_falls_back_to_primary(monkeypatch):
    primary = DisplayMetrics(1920, 1040, 1.25)
    monkeypatch.setattr(ui_scale.sys, "platform", "win32")
    monkeypatch.setattr(
        ui_scale,
        "_detect_windows_display_metrics",
        lambda: (_ for _ in ()).throw(RuntimeError("enumeration unavailable")),
    )
    monkeypatch.setattr(ui_scale, "detect_primary_display_metrics", lambda: primary)

    assert detect_all_display_metrics() == (primary,)


def test_configure_environment_defaults_to_most_constrained_display(monkeypatch):
    environ: dict[str, str] = {}
    constrained = DisplayMetrics(1366, 728, 1.75)
    monkeypatch.setattr(
        ui_scale,
        "detect_most_constrained_display_metrics",
        lambda: constrained,
    )

    result = configure_ui_scale_environment("auto", environ)

    assert environ["QT_SCALE_FACTOR"] == "0.4"
    assert result.source == "automatic"
    assert result.percent == 40
