from __future__ import annotations

import ctypes
import math
import os
import sys
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass
from typing import Literal, TypeAlias


AUTO_UI_SCALE = "auto"
MANAGED_QT_SCALE_FACTOR_MARKER = "AUTO_BDSP_RNG_MANAGED_QT_SCALE_FACTOR"
DEFAULT_UI_BASELINE = (1150, 900)
DEFAULT_UI_SCALE_MARGIN_PX = 32
MIN_UI_SCALE_PERCENT = 50
MAX_UI_SCALE_PERCENT = 125
MIN_AUTO_UI_SCALE_PERCENT = 5
MAX_AUTO_UI_SCALE_PERCENT = 100
UI_SCALE_STEP_PERCENT = 5

UiScaleSetting: TypeAlias = str | int
UiScaleSource: TypeAlias = Literal["environment", "automatic", "manual", "fallback"]


@dataclass(frozen=True, slots=True)
class DisplayMetrics:
    """Physical screen work area and its effective Windows scale."""

    work_width_px: int
    work_height_px: int
    system_scale: float


@dataclass(frozen=True, slots=True)
class UiScaleEnvironmentResult:
    """The scale selected before QApplication is constructed."""

    percent: float | None
    scale_factor: float | None
    source: UiScaleSource
    environment_value: str


DisplayMetricsProvider: TypeAlias = Callable[[], DisplayMetrics | None]


def normalize_ui_scale_setting(value: object) -> UiScaleSetting:
    """Return ``auto`` or a supported manual scale percentage."""

    if isinstance(value, str):
        text = value.strip().casefold()
        if text == AUTO_UI_SCALE:
            return AUTO_UI_SCALE
        if text.endswith("%"):
            text = text[:-1].strip()
        try:
            numeric = float(text)
        except ValueError:
            return AUTO_UI_SCALE
    elif isinstance(value, bool):
        return AUTO_UI_SCALE
    elif isinstance(value, (int, float)):
        numeric = float(value)
    else:
        return AUTO_UI_SCALE

    if not math.isfinite(numeric):
        return AUTO_UI_SCALE

    clamped = min(MAX_UI_SCALE_PERCENT, max(MIN_UI_SCALE_PERCENT, numeric))
    snapped = int(
        math.floor((clamped + UI_SCALE_STEP_PERCENT / 2) / UI_SCALE_STEP_PERCENT)
        * UI_SCALE_STEP_PERCENT
    )
    return min(MAX_UI_SCALE_PERCENT, max(MIN_UI_SCALE_PERCENT, snapped))


def calculate_auto_ui_scale_percent(
    work_width_px: object,
    work_height_px: object,
    system_scale: object,
    baseline: tuple[int, int] = DEFAULT_UI_BASELINE,
    margin_px: object = DEFAULT_UI_SCALE_MARGIN_PX,
) -> int:
    """Choose the largest 5-percent scale that fits the physical work area.

    The lower bound remains 5 percent even when no supported scale can fit.
    Invalid display metrics mean detection failed and therefore fall back to
    100 percent.
    """

    try:
        width = float(work_width_px)
        height = float(work_height_px)
        scale = float(system_scale)
        baseline_width = float(baseline[0])
        baseline_height = float(baseline[1])
        margin = max(0.0, float(margin_px))
    except (IndexError, TypeError, ValueError):
        return MAX_AUTO_UI_SCALE_PERCENT

    inputs = (width, height, scale, baseline_width, baseline_height, margin)
    if not all(math.isfinite(item) for item in inputs):
        return MAX_AUTO_UI_SCALE_PERCENT
    if min(width, height, scale, baseline_width, baseline_height) <= 0:
        return MAX_AUTO_UI_SCALE_PERCENT

    usable_width = max(0.0, width - margin)
    usable_height = max(0.0, height - margin)
    fit_percent = min(
        usable_width / (baseline_width * scale),
        usable_height / (baseline_height * scale),
    ) * 100
    stepped_percent = int(
        math.floor((fit_percent + 1e-9) / UI_SCALE_STEP_PERCENT)
        * UI_SCALE_STEP_PERCENT
    )
    return min(
        MAX_AUTO_UI_SCALE_PERCENT,
        max(MIN_AUTO_UI_SCALE_PERCENT, stepped_percent),
    )


def resolve_ui_scale_percent(
    setting: object,
    detected_metrics: DisplayMetrics | None,
    *,
    baseline: tuple[int, int] = DEFAULT_UI_BASELINE,
    margin_px: object = DEFAULT_UI_SCALE_MARGIN_PX,
) -> int:
    """Resolve a stored manual/automatic setting to a percentage."""

    normalized = normalize_ui_scale_setting(setting)
    if isinstance(normalized, int):
        return normalized
    if not _valid_display_metrics(detected_metrics):
        return MAX_AUTO_UI_SCALE_PERCENT
    assert detected_metrics is not None
    return calculate_auto_ui_scale_percent(
        detected_metrics.work_width_px,
        detected_metrics.work_height_px,
        detected_metrics.system_scale,
        baseline=baseline,
        margin_px=margin_px,
    )


def configure_ui_scale_environment(
    setting: object,
    environ: MutableMapping[str, str] | None = None,
    metrics_provider: DisplayMetricsProvider | None = None,
) -> UiScaleEnvironmentResult:
    """Set ``QT_SCALE_FACTOR`` before QApplication, unless already explicit."""

    target_environ = os.environ if environ is None else environ
    if (
        "QT_SCALE_FACTOR" in target_environ
        and MANAGED_QT_SCALE_FACTOR_MARKER not in target_environ
    ):
        environment_value = str(target_environ["QT_SCALE_FACTOR"])
        scale_factor = _parse_positive_float(environment_value)
        return UiScaleEnvironmentResult(
            percent=None if scale_factor is None else scale_factor * 100,
            scale_factor=scale_factor,
            source="environment",
            environment_value=environment_value,
        )

    normalized = normalize_ui_scale_setting(setting)
    if isinstance(normalized, int):
        percent = normalized
        source: UiScaleSource = "manual"
    else:
        provider = (
            detect_most_constrained_display_metrics
            if metrics_provider is None
            else metrics_provider
        )
        try:
            metrics = provider()
        except Exception:
            metrics = None
        if _valid_display_metrics(metrics):
            percent = resolve_ui_scale_percent(normalized, metrics)
            source = "automatic"
        else:
            percent = MAX_AUTO_UI_SCALE_PERCENT
            source = "fallback"

    scale_factor = percent / 100
    environment_value = _format_scale_factor(scale_factor)
    target_environ["QT_SCALE_FACTOR"] = environment_value
    target_environ[MANAGED_QT_SCALE_FACTOR_MARKER] = "1"
    return UiScaleEnvironmentResult(
        percent=float(percent),
        scale_factor=scale_factor,
        source=source,
        environment_value=environment_value,
    )


def detect_primary_display_metrics() -> DisplayMetrics | None:
    """Read physical primary-screen work-area pixels and effective DPI."""

    if sys.platform != "win32":
        return None
    try:
        return _detect_windows_primary_display_metrics()
    except Exception:
        return None


def detect_all_display_metrics() -> tuple[DisplayMetrics, ...]:
    """Read every connected Windows display, falling back to the primary."""

    if sys.platform != "win32":
        return ()
    try:
        metrics = tuple(
            metric
            for metric in _detect_windows_display_metrics()
            if _valid_display_metrics(metric)
        )
    except Exception:
        metrics = ()
    if metrics:
        return metrics
    primary = detect_primary_display_metrics()
    return () if primary is None else (primary,)


def select_most_constrained_display_metrics(
    metrics: object,
    *,
    baseline: tuple[int, int] = DEFAULT_UI_BASELINE,
    margin_px: object = DEFAULT_UI_SCALE_MARGIN_PX,
) -> DisplayMetrics | None:
    """Select the display requiring the smallest automatic UI scale."""

    try:
        valid_metrics = tuple(
            metric for metric in metrics if _valid_display_metrics(metric)
        )
    except TypeError:
        return None
    if not valid_metrics:
        return None
    return min(
        valid_metrics,
        key=lambda metric: calculate_auto_ui_scale_percent(
            metric.work_width_px,
            metric.work_height_px,
            metric.system_scale,
            baseline=baseline,
            margin_px=margin_px,
        ),
    )


def detect_most_constrained_display_metrics() -> DisplayMetrics | None:
    """Return the connected display that imposes the smallest UI scale."""

    return select_most_constrained_display_metrics(detect_all_display_metrics())


def _valid_display_metrics(metrics: DisplayMetrics | None) -> bool:
    if not isinstance(metrics, DisplayMetrics):
        return False
    values = (
        metrics.work_width_px,
        metrics.work_height_px,
        metrics.system_scale,
    )
    try:
        numeric_values = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return False
    return all(math.isfinite(value) and value > 0 for value in numeric_values)


def _parse_positive_float(value: str) -> float | None:
    try:
        parsed = float(value.strip())
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) and parsed > 0 else None


def _format_scale_factor(scale_factor: float) -> str:
    return f"{scale_factor:.2f}".rstrip("0").rstrip(".")


def _detect_windows_primary_display_metrics() -> DisplayMetrics:
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    monitor_from_window = user32.MonitorFromWindow
    monitor_from_window.argtypes = [wintypes.HWND, wintypes.DWORD]
    monitor_from_window.restype = wintypes.HANDLE
    get_monitor_info = user32.GetMonitorInfoW
    get_monitor_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    get_monitor_info.restype = wintypes.BOOL

    set_thread_dpi_context = getattr(user32, "SetThreadDpiAwarenessContext", None)
    previous_dpi_context = None
    if set_thread_dpi_context is not None:
        set_thread_dpi_context.argtypes = [ctypes.c_void_p]
        set_thread_dpi_context.restype = ctypes.c_void_p
        previous_dpi_context = set_thread_dpi_context(ctypes.c_void_p(-4))

    try:
        monitor = monitor_from_window(None, 1)
        if not monitor:
            raise OSError(ctypes.get_last_error(), "MonitorFromWindow failed")
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not get_monitor_info(monitor, ctypes.byref(info)):
            raise OSError(ctypes.get_last_error(), "GetMonitorInfoW failed")
        width = int(info.rcWork.right - info.rcWork.left)
        height = int(info.rcWork.bottom - info.rcWork.top)
        dpi = _get_monitor_effective_dpi(monitor, user32)
    finally:
        if set_thread_dpi_context is not None and previous_dpi_context:
            set_thread_dpi_context(previous_dpi_context)

    metrics = DisplayMetrics(width, height, dpi / 96.0)
    if not _valid_display_metrics(metrics):
        raise ValueError("Windows returned invalid primary display metrics")
    return metrics


def _detect_windows_display_metrics() -> tuple[DisplayMetrics, ...]:
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_monitor_info = user32.GetMonitorInfoW
    get_monitor_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    get_monitor_info.restype = wintypes.BOOL
    monitor_enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HANDLE,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )
    enum_display_monitors = user32.EnumDisplayMonitors
    enum_display_monitors.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        monitor_enum_proc,
        wintypes.LPARAM,
    ]
    enum_display_monitors.restype = wintypes.BOOL

    detected: list[DisplayMetrics] = []

    @monitor_enum_proc
    def append_monitor_metrics(monitor, _hdc, _rect, _data):
        try:
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            if not get_monitor_info(monitor, ctypes.byref(info)):
                return True
            metric = DisplayMetrics(
                int(info.rcWork.right - info.rcWork.left),
                int(info.rcWork.bottom - info.rcWork.top),
                _get_monitor_effective_dpi(monitor, user32) / 96.0,
            )
            if _valid_display_metrics(metric):
                detected.append(metric)
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        return True

    set_thread_dpi_context = getattr(user32, "SetThreadDpiAwarenessContext", None)
    previous_dpi_context = None
    if set_thread_dpi_context is not None:
        set_thread_dpi_context.argtypes = [ctypes.c_void_p]
        set_thread_dpi_context.restype = ctypes.c_void_p
        previous_dpi_context = set_thread_dpi_context(ctypes.c_void_p(-4))

    try:
        if not enum_display_monitors(None, None, append_monitor_metrics, 0):
            raise OSError(ctypes.get_last_error(), "EnumDisplayMonitors failed")
    finally:
        if set_thread_dpi_context is not None and previous_dpi_context:
            set_thread_dpi_context(previous_dpi_context)

    if not detected:
        raise OSError("EnumDisplayMonitors returned no usable displays")
    return tuple(detected)


def _get_monitor_effective_dpi(monitor: object, user32: object) -> int:
    from ctypes import wintypes

    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        get_dpi_for_monitor = shcore.GetDpiForMonitor
        get_dpi_for_monitor.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT),
        ]
        get_dpi_for_monitor.restype = ctypes.c_long
        dpi_x = wintypes.UINT()
        dpi_y = wintypes.UINT()
        if get_dpi_for_monitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
            if dpi_x.value > 0:
                return int(dpi_x.value)
    except (AttributeError, OSError):
        pass

    get_dpi_for_system = getattr(user32, "GetDpiForSystem", None)
    if get_dpi_for_system is not None:
        get_dpi_for_system.argtypes = []
        get_dpi_for_system.restype = wintypes.UINT
        dpi = int(get_dpi_for_system())
        if dpi > 0:
            return dpi
    return 96


__all__ = [
    "AUTO_UI_SCALE",
    "DEFAULT_UI_BASELINE",
    "DEFAULT_UI_SCALE_MARGIN_PX",
    "DisplayMetrics",
    "MAX_AUTO_UI_SCALE_PERCENT",
    "MAX_UI_SCALE_PERCENT",
    "MANAGED_QT_SCALE_FACTOR_MARKER",
    "MIN_AUTO_UI_SCALE_PERCENT",
    "MIN_UI_SCALE_PERCENT",
    "UI_SCALE_STEP_PERCENT",
    "UiScaleEnvironmentResult",
    "calculate_auto_ui_scale_percent",
    "configure_ui_scale_environment",
    "detect_all_display_metrics",
    "detect_most_constrained_display_metrics",
    "detect_primary_display_metrics",
    "normalize_ui_scale_setting",
    "resolve_ui_scale_percent",
    "select_most_constrained_display_metrics",
]
