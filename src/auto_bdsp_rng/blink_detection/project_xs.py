from __future__ import annotations

import importlib
import json
import heapq
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Iterator

from auto_bdsp_rng.resources import resource_path
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
    TimelineEvent,
)

if TYPE_CHECKING:
    from auto_bdsp_rng.blink_detection.qt_window_capture import WindowTarget


PROJECT_XS_ROOT = resource_path("third_party", "Project_Xs_CHN")
PROJECT_XS_SRC = PROJECT_XS_ROOT / "src"
BROKER_NEW_FRAME_WAIT_SECONDS = 0.1


class ProjectXsIntegrationError(RuntimeError):
    """Raised when Project_Xs cannot capture or recover a seed."""


class ProjectXsCaptureConfigError(ProjectXsIntegrationError):
    """Raised when the eye template or capture ROI cannot be used for tracking."""


class ProjectXsNoFrameError(ProjectXsIntegrationError):
    """Raised when a capture source repeatedly returns no frame."""


def _template_shape(eye_image: Any) -> tuple[int, int] | None:
    """Return a grayscale template's ``(width, height)`` when available."""

    shape = getattr(eye_image, "shape", None)
    if shape is None:
        # Lightweight test doubles and legacy integrations may not expose a
        # NumPy shape.  OpenCV will provide the authoritative validation there.
        return None
    try:
        dimensions = tuple(int(value) for value in shape)
    except (TypeError, ValueError) as exc:
        raise ProjectXsCaptureConfigError("眼睛模板尺寸无法读取，请重新框选并保存眼睛模板") from exc
    if len(dimensions) < 2:
        raise ProjectXsCaptureConfigError("眼睛模板必须是至少包含宽高的图像")
    height, width = dimensions[:2]
    if width <= 0 or height <= 0:
        raise ProjectXsCaptureConfigError("眼睛模板尺寸无效，请重新框选眼睛模板")
    return width, height


def _validate_eye_template_config(
    eye_image: Any,
    config: BlinkCaptureConfig,
) -> tuple[int, int] | None:
    """Validate the static part of a template/ROI pair before opening capture."""

    template_size = _template_shape(eye_image)
    if template_size is None:
        return None
    template_width, template_height = template_size
    try:
        roi_x, roi_y, roi_width, roi_height = (int(value) for value in config.roi)
    except (TypeError, ValueError):
        raise ProjectXsCaptureConfigError("眼睛 ROI 必须包含四个整数值") from None
    if roi_x < 0 or roi_y < 0 or roi_width <= 0 or roi_height <= 0:
        raise ProjectXsCaptureConfigError(
            f"眼睛 ROI 无效：X={roi_x}, Y={roi_y}, W={roi_width}, H={roi_height}"
        )
    if roi_width < template_width or roi_height < template_height:
        raise ProjectXsCaptureConfigError(
            "ROI is smaller than the configured eye template；眼睛模板尺寸 "
            f"{template_width}x{template_height} 大于眼睛 ROI 尺寸 "
            f"{roi_width}x{roi_height}；请重新框选眼睛模板和 ROI，并保存配置"
        )
    return template_size


def _validate_frame_roi(
    frame: Any,
    config: BlinkCaptureConfig,
    *,
    template_size: tuple[int, int] | None = None,
) -> None:
    """Reject an ROI that falls outside the current captured frame."""

    shape = getattr(frame, "shape", None)
    if shape is None:
        return
    try:
        dimensions = tuple(int(value) for value in shape)
        roi_x, roi_y, roi_width, roi_height = (int(value) for value in config.roi)
    except (TypeError, ValueError) as exc:
        raise ProjectXsCaptureConfigError("眼睛 ROI 或捕捉画面尺寸无效，请重新框选并保存配置") from exc
    if len(dimensions) < 2:
        raise ProjectXsCaptureConfigError("捕捉画面没有有效的宽高，无法应用眼睛 ROI")
    frame_height, frame_width = dimensions[:2]
    template_width, template_height = template_size or (0, 0)
    if (
        frame_width <= 0
        or frame_height <= 0
        or roi_x < 0
        or roi_y < 0
        or roi_width <= 0
        or roi_height <= 0
        or roi_x + roi_width > frame_width
        or roi_y + roi_height > frame_height
        or roi_width < template_width
        or roi_height < template_height
    ):
        size_detail = ""
        if template_size is not None:
            size_detail = f"，模板为 {template_width}x{template_height}"
        raise ProjectXsCaptureConfigError(
            "眼睛 ROI 超出当前捕捉画面范围；"
            f"画面为 {frame_width}x{frame_height}，ROI 为 "
            f"X={roi_x}, Y={roi_y}, W={roi_width}, H={roi_height}；"
            f"请重新框选眼睛 ROI 并保存配置{size_detail}"
        )


def _is_match_template_size_error(error: BaseException) -> bool:
    """Recognize OpenCV's assertion raised when a template exceeds its image."""

    details: list[str] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        details.append(str(current).lower())
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    detail = "\n".join(details)
    has_match_template = "matchtemplate" in detail or "templmatch.cpp" in detail
    has_size_assertion = (
        "_img.size" in detail
        or "_templ.size" in detail
        or "assertion failed" in detail
    )
    # Some OpenCV builds omit the function name from ``cv2.error``; the
    # private ``_img.size``/``_templ.size`` identifiers are specific enough to
    # recognize the same template-dimension assertion on their own.
    return has_size_assertion and (
        has_match_template or "_img.size" in detail or "_templ.size" in detail
    )


def _tracking_failure(prefix: str, error: BaseException) -> ProjectXsIntegrationError:
    """Convert low-level template-size assertions into an actionable error."""

    if _is_match_template_size_error(error):
        # Keep the exception single-line so automatic run logs retain their
        # timestamped format even when OpenCV emits a multi-line assertion.
        technical_detail = " ".join(str(error).split())
        detail_suffix = f"；底层错误：{technical_detail}" if technical_detail else ""
        return ProjectXsCaptureConfigError(
            "ROI is smaller than the configured eye template；眼睛模板或 ROI 配置无效，"
            "眼睛模板不能大于 ROI；"
            f"请重新框选眼睛模板和 ROI，并保存配置{detail_suffix}"
        )
    return ProjectXsIntegrationError(f"{prefix}: {error}")


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
        module = sys.modules[name]
        _patch_windowcapture_numpy()
        return module
    if not PROJECT_XS_SRC.exists():
        raise ProjectXsIntegrationError(f"Project_Xs source directory not found: {PROJECT_XS_SRC}")
    with _project_xs_import_path():
        module = importlib.import_module(name)
    _patch_windowcapture_numpy()
    return module


def _patch_windowcapture_numpy() -> None:
    windowcapture = sys.modules.get("windowcapture")
    if windowcapture is None:
        return
    np_module = getattr(windowcapture, "np", None)
    if np_module is None or getattr(np_module, "_auto_bdsp_rng_fromstring_patch", False):
        return
    original_fromstring = np_module.fromstring

    def fromstring_compat(data: object, dtype: object | None = None, *args: object, **kwargs: object) -> object:
        if isinstance(data, bytes | bytearray | memoryview):
            return np_module.frombuffer(data, dtype=dtype)
        return original_fromstring(data, dtype=dtype, *args, **kwargs)

    np_module.fromstring = fromstring_compat
    np_module._auto_bdsp_rng_fromstring_patch = True


def _load_cv2() -> ModuleType:
    try:
        return importlib.import_module("cv2")
    except ImportError as exc:
        raise ProjectXsIntegrationError("OpenCV is required for Project_Xs blink capture") from exc


def _read_grayscale_image(path: Path) -> Any:
    cv2 = _load_cv2()
    try:
        np = importlib.import_module("numpy")
        data = np.fromfile(str(path), dtype=np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    except Exception:
        try:
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        except Exception:
            image = None
    if image is None:
        raise ProjectXsCaptureConfigError(
            f"Cannot read eye template image: {path}；请先在预览中框选并保存眼睛模板"
        )
    return image


def _project_xs_crop(crop: tuple[int, int, int, int] | None) -> list[int] | None:
    return None if crop is None else list(crop)


def _obs_window_target(config: BlinkCaptureConfig) -> WindowTarget | None:
    if not config.monitor_window:
        return None
    from auto_bdsp_rng.blink_detection.qt_window_capture import find_obs_window_target

    return find_obs_window_target(config.window_prefix)


def _open_capture_source(
    config: BlinkCaptureConfig,
    *,
    cv2: ModuleType | None = None,
    prefer_v4l: bool = False,
    wait_for_new_frame: bool = False,
) -> Any:
    if config.uses_shared_video_source:
        return BrokerFrameCapture(
            config.frame_source_factory,
            session=config.broker_session,
            wait_for_new_frame=wait_for_new_frame,
        )
    cv2 = cv2 or _load_cv2()
    if config.monitor_window:
        obs_target = _obs_window_target(config)
        if obs_target is not None:
            from auto_bdsp_rng.blink_detection.qt_window_capture import create_qt_window_capture

            return create_qt_window_capture(obs_target, _project_xs_crop(config.crop))
        windowcapture = _load_module("windowcapture")
        return windowcapture.WindowCapture(config.window_prefix, _project_xs_crop(config.crop))

    backend = cv2.CAP_V4L if prefer_v4l and sys.platform.startswith("linux") else cv2.CAP_ANY
    video = cv2.VideoCapture(config.camera, backend)
    video.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    video.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    video.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return video


def _default_broker_client(session: str | None = None) -> Any:
    """Discover the active Capture Broker without importing it at startup.

    The broker is optional for the legacy CLI and Project_Xs command paths, so
    this import deliberately happens only after a config selects the shared
    source.  A few small constructor/classmethod spellings are accepted to
    keep this adapter compatible with the standalone broker and test doubles.
    """

    try:
        module = importlib.import_module("auto_bdsp_rng.capture_broker")
    except ImportError as exc:
        raise ProjectXsIntegrationError("共享视频源组件不可用，请先启动 Broker") from exc
    client_type = getattr(module, "CaptureBrokerClient", None)
    if client_type is None:
        raise ProjectXsIntegrationError("共享视频源客户端未安装")

    for method_name in ("connect", "discover", "open"):
        method = getattr(client_type, method_name, None)
        if not callable(method):
            continue
        for kwargs in ({"path": session, "require_running": True}, {"path": session}, {}):
            if kwargs.get("path", object()) is None:
                kwargs = {key: value for key, value in kwargs.items() if key != "path"}
            try:
                client = method(**kwargs)
            except (TypeError, LookupError, FileNotFoundError):
                continue
            if client is not None:
                return client

    for args in ((session,), ()):
        if args == (None,):
            continue
        try:
            return client_type(*args)
        except TypeError:
            continue
    raise ProjectXsIntegrationError("无法连接共享视频源，请先启动 Broker")


class BrokerFrameCapture:
    """OpenCV-like read/release wrapper around one Broker consumer connection."""

    keep_open_for_preview = True

    def __init__(
        self,
        factory: Callable[[], Any] | None = None,
        *,
        session: str | None = None,
        wait_for_new_frame: bool = False,
    ) -> None:
        self._client = factory() if factory is not None else _default_broker_client(session)
        self._released = False
        self._wait_for_new_frame = bool(wait_for_new_frame)
        self._last_sequence = 0

    @staticmethod
    def _frame_from_result(result: Any) -> Any:
        if result is None:
            return None
        # A tuple follows OpenCV's ``(ok, frame)`` convention.
        if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], bool):
            return result[1] if result[0] else None
        for attribute in ("frame", "array"):
            frame = getattr(result, attribute, None)
            if frame is not None:
                return frame
        as_array = getattr(result, "as_array", None)
        if callable(as_array):
            try:
                return as_array(copy=False)
            except TypeError:
                return as_array()
        return result

    def read(self) -> tuple[bool, Any]:
        if self._released:
            raise ProjectXsIntegrationError("共享视频源客户端已经关闭")
        try:
            waiter = getattr(self._client, "wait_for_frame", None)
            if self._wait_for_new_frame and callable(waiter):
                try:
                    result = waiter(
                        after_sequence=self._last_sequence,
                        timeout=BROKER_NEW_FRAME_WAIT_SECONDS,
                    )
                except TimeoutError:
                    return False, None
                sequence = getattr(result, "sequence", None)
                if sequence is not None and int(sequence) <= self._last_sequence:
                    return False, None
            else:
                reader = None
                for name in ("read_latest", "read_array", "read", "get_latest_frame", "read_frame"):
                    candidate = getattr(self._client, name, None)
                    if callable(candidate):
                        reader = candidate
                        break
                if reader is None:
                    raise ProjectXsIntegrationError("共享视频源客户端不支持读取帧")
                result = reader()
                sequence = getattr(result, "sequence", None)
            frame = self._frame_from_result(result)
            if frame is None:
                return False, None
            # Consumers must never annotate the broker's backing memory.  Only
            # commit the sequence after the private frame is ready to return.
            copier = getattr(frame, "copy", None)
            private_frame = copier() if callable(copier) else frame
            if sequence is not None:
                self._last_sequence = int(sequence)
        except Exception as exc:
            raise ProjectXsIntegrationError(f"共享视频源读取失败: {exc}") from exc
        return True, private_frame

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        for name in ("close", "release", "disconnect", "stop"):
            method = getattr(self._client, name, None)
            if callable(method):
                try:
                    method()
                except Exception:
                    pass
                break


class PreviewFrameCapture:
    """Reusable frame source for the embedded live preview."""

    def __init__(self, config: BlinkCaptureConfig) -> None:
        self.config = config
        self._video = _open_capture_source(config)
        self._released = False

    @property
    def keep_open_for_preview(self) -> bool:
        return bool(getattr(self._video, "keep_open_for_preview", False))

    def read(self) -> Any:
        if self._released:
            raise ProjectXsIntegrationError("预览捕捉源已经关闭")
        try:
            ok, frame = self._video.read()
        except Exception as exc:
            raise ProjectXsIntegrationError(f"Project_Xs frame capture failed: {exc}") from exc
        if not ok or frame is None:
            raise ProjectXsIntegrationError("Project_Xs frame capture returned an empty frame")
        return frame

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        release = getattr(self._video, "release", None)
        if callable(release):
            release()


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
    configs_dir = PROJECT_XS_ROOT / "configs"
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


def _project_xs_relative_path(path: Path) -> str:
    project_xs_root = PROJECT_XS_SRC.parent.resolve()
    try:
        relative = path.resolve().relative_to(project_xs_root)
    except ValueError:
        return str(path)
    return "./" + relative.as_posix()


def _to_project_xs_config_dict(config: ProjectXsTrackingConfig) -> dict[str, object]:
    return {
        "MonitorWindow": config.capture.monitor_window,
        "WindowPrefix": config.capture.window_prefix,
        "image": _project_xs_relative_path(config.capture.eye_image_path),
        "view": list(config.capture.roi),
        "thresh": config.capture.threshold,
        "white_delay": config.white_delay,
        "advance_delay": config.advance_delay,
        "advance_delay_2": config.advance_delay_2,
        "npc": config.npc,
        "pokemon_npc": config.pokemon_npc,
        "timeline_npc": config.timeline_npc,
        "reident_1_pk_npc": config.reidentify_1_pk_npc,
        "crop": [0, 0, 0, 0] if config.capture.crop is None else list(config.capture.crop),
        "camera": config.capture.camera,
        "display_percent": config.display_percent,
    }


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
        white_delay=float(raw_config.get("white_delay", 0.0)),
        advance_delay=int(raw_config.get("advance_delay", 0)),
        advance_delay_2=int(raw_config.get("advance_delay_2", 0)),
        npc=int(raw_config.get("npc", 0)),
        pokemon_npc=int(raw_config.get("pokemon_npc", 0)),
        timeline_npc=int(raw_config.get("timeline_npc", 0)),
        display_percent=int(raw_config.get("display_percent", 100)),
        reidentify_1_pk_npc=bool(raw_config.get("reident_1_pk_npc", False)),
    )


def save_project_xs_config(config: ProjectXsTrackingConfig, output_path: str | Path) -> Path:
    """Save a normalized config back to a Project_Xs-compatible JSON file."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(_to_project_xs_config_dict(config), file, ensure_ascii=False, indent=4)
            file.write("\n")
    except OSError as exc:
        raise ProjectXsIntegrationError(f"Cannot save Project_Xs config: {output}") from exc
    return output


def capture_player_blinks(
    config: BlinkCaptureConfig,
    *,
    should_stop: Callable[[], bool] | None = None,
    frame_callback: Callable[[Any], None] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    show_window: bool = True,
    discard_first_blink_within_seconds: float | None = None,
) -> BlinkObservation:
    """Capture player blink observations through Project_Xs tracking logic."""

    eye_image = _read_grayscale_image(config.eye_image_path)
    _validate_eye_template_config(eye_image, config)

    try:
        if (
            config.uses_shared_video_source
            or should_stop is not None
            or frame_callback is not None
            or progress_callback is not None
            or not show_window
            or discard_first_blink_within_seconds is not None
            or _obs_window_target(config) is not None
        ):
            blinks, intervals, offset_time = _tracking_blink_controlled(
                eye_image,
                config,
                should_stop=should_stop,
                frame_callback=frame_callback,
                progress_callback=progress_callback,
                show_window=show_window,
                discard_first_blink_within_seconds=discard_first_blink_within_seconds,
            )
        else:
            rngtool = _load_module("rngtool")
            with _project_xs_import_path():
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
    except ProjectXsIntegrationError:
        raise
    except Exception as exc:  # Project_Xs raises broad UI/capture exceptions.
        raise _tracking_failure("Project_Xs blink tracking failed", exc) from exc
    if should_stop is not None and should_stop():
        raise ProjectXsIntegrationError("Blink capture stopped")

    return BlinkObservation.from_sequences(blinks, intervals, offset_time)


def _tracking_blink_controlled(
    eye_image: Any,
    config: BlinkCaptureConfig,
    *,
    should_stop: Callable[[], bool] | None,
    frame_callback: Callable[[Any], None] | None,
    progress_callback: Callable[[int, int], None] | None,
    show_window: bool,
    discard_first_blink_within_seconds: float | None = None,
) -> tuple[list[int], list[int], float]:
    cv2 = _load_cv2()
    if should_stop is not None and should_stop():
        return [], [], 0.0
    template_size = _validate_eye_template_config(eye_image, config)
    video = _open_capture_source(
        config,
        cv2=cv2,
        prefer_v4l=True,
        wait_for_new_frame=True,
    )

    state_idle = 0xFF
    state_single = 0xF0
    state_double = 0xF1
    state = state_idle
    blinks: list[int] = []
    intervals: list[int] = []
    prev_time = 0.0
    offset_time = 0.0
    prev_roi = None
    capture_started_at = time.perf_counter()
    skipped_current_blink = False
    roi_x, roi_y, roi_w, roi_h = config.roi
    if template_size is None:
        try:
            eye_width, eye_height = eye_image.shape[::-1]
        except (AttributeError, TypeError, ValueError) as exc:
            raise ProjectXsCaptureConfigError("眼睛模板尺寸无法读取，请重新框选并保存眼睛模板") from exc
    else:
        eye_width, eye_height = template_size

    try:
        consecutive_failures = 0
        while len(blinks) < config.blink_count or state != state_idle:
            if should_stop is not None and should_stop():
                break
            ok, frame = video.read()
            if not ok or frame is None:
                consecutive_failures += 1
                if consecutive_failures > 30:  # 约3秒无画面则判定窗口未打开
                    raise ProjectXsNoFrameError(
                        "未检测到捕捉画面，请确认捕捉窗口已打开且未被最小化"
                    )
                time.sleep(0.1)
                continue
            consecutive_failures = 0
            time_counter = time.perf_counter()
            _validate_frame_roi(frame, config, template_size=template_size)
            roi = cv2.cvtColor(frame[roi_y : roi_y + roi_h, roi_x : roi_x + roi_w], cv2.COLOR_RGB2GRAY)
            if prev_roi is not None and (roi == prev_roi).all():
                continue
            prev_roi = roi
            try:
                result = cv2.matchTemplate(roi, eye_image, cv2.TM_CCOEFF_NORMED)
            except Exception as exc:
                raise _tracking_failure("Project_Xs blink tracking failed", exc) from exc
            _, match, _, max_loc = cv2.minMaxLoc(result)

            # Draw detection helpers on a private copy. Broker consumers must
            # never mutate the shared backing frame.
            display_frame = frame.copy() if callable(getattr(frame, "copy", None)) else frame
            cv2.rectangle(display_frame, (roi_x, roi_y), (roi_x + roi_w, roi_y + roi_h), (0, 0, 255), 2)
            if 0.01 < match < config.threshold:
                cv2.rectangle(display_frame, (roi_x, roi_y), (roi_x + roi_w, roi_y + roi_h), 255, 2)
                if state == state_idle:
                    should_discard = (
                        discard_first_blink_within_seconds is not None
                        and not blinks
                        and time_counter - capture_started_at <= discard_first_blink_within_seconds
                    )
                    if should_discard:
                        skipped_current_blink = True
                    else:
                        skipped_current_blink = False
                        blinks.append(0)
                        intervals.append(round((time_counter - prev_time) / 1.017))
                        if progress_callback is not None:
                            progress_callback(len(intervals), config.blink_count)
                        if len(intervals) == config.blink_count:
                            offset_time = time_counter
                    state = state_single
                    prev_time = time_counter
                elif state == state_single and time_counter - prev_time > 0.3:
                    if not skipped_current_blink:
                        blinks[-1] = 1
                    state = state_double
            else:
                match_location = (max_loc[0] + roi_x, max_loc[1] + roi_y)
                match_bottom_right = (match_location[0] + eye_width, match_location[1] + eye_height)
                cv2.rectangle(display_frame, match_location, match_bottom_right, 255, 2)

            if frame_callback is not None:
                frame_callback(display_frame)
            if show_window:
                cv2.imshow("view", display_frame)
                if cv2.waitKey(1) == ord("q"):
                    break
            if state != state_idle and time_counter - prev_time > 0.7:
                state = state_idle
                skipped_current_blink = False
    finally:
        release = getattr(video, "release", None)
        if callable(release):
            release()
        if show_window:
            cv2.destroyAllWindows()
    return blinks, intervals, offset_time


def _tracking_poke_blink_controlled(
    eye_image: Any,
    config: BlinkCaptureConfig,
    *,
    should_stop: Callable[[], bool] | None,
    frame_callback: Callable[[Any], None] | None,
    progress_callback: Callable[[int, int], None] | None,
    show_window: bool,
    discard_first_blink_within_seconds: float | None = None,
) -> list[float]:
    cv2 = _load_cv2()
    if should_stop is not None and should_stop():
        return []
    template_size = _validate_eye_template_config(eye_image, config)
    video = _open_capture_source(
        config,
        cv2=cv2,
        prefer_v4l=True,
        wait_for_new_frame=True,
    )

    state_idle = 0xFF
    state_single = 0xF0
    state = state_idle
    intervals: list[float] = []
    capture_started_at = time.perf_counter()
    prev_time = capture_started_at
    prev_roi = None
    roi_x, roi_y, roi_w, roi_h = config.roi
    if template_size is None:
        try:
            eye_width, eye_height = eye_image.shape[::-1]
        except (AttributeError, TypeError, ValueError) as exc:
            raise ProjectXsCaptureConfigError("眼睛模板尺寸无法读取，请重新框选并保存眼睛模板") from exc
    else:
        eye_width, eye_height = template_size

    try:
        consecutive_failures = 0
        while len(intervals) < config.blink_count:
            if should_stop is not None and should_stop():
                break
            ok, frame = video.read()
            if not ok or frame is None:
                consecutive_failures += 1
                if consecutive_failures > 30:
                    raise ProjectXsNoFrameError(
                        "未检测到捕捉画面，请确认捕捉窗口已打开且未被最小化"
                    )
                time.sleep(0.1)
                continue
            consecutive_failures = 0
            time_counter = time.perf_counter()
            _validate_frame_roi(frame, config, template_size=template_size)
            roi = cv2.cvtColor(frame[roi_y : roi_y + roi_h, roi_x : roi_x + roi_w], cv2.COLOR_RGB2GRAY)
            if prev_roi is not None and (roi == prev_roi).all():
                continue
            prev_roi = roi
            try:
                result = cv2.matchTemplate(roi, eye_image, cv2.TM_CCOEFF_NORMED)
            except Exception as exc:
                raise _tracking_failure("Project_Xs Pokemon blink tracking failed", exc) from exc
            _, match, _, max_loc = cv2.minMaxLoc(result)

            display_frame = frame.copy() if callable(getattr(frame, "copy", None)) else frame
            cv2.rectangle(display_frame, (roi_x, roi_y), (roi_x + roi_w, roi_y + roi_h), (0, 0, 255), 2)
            if 0.4 < match < config.threshold:
                cv2.rectangle(display_frame, (roi_x, roi_y), (roi_x + roi_w, roi_y + roi_h), 255, 2)
                if state == state_idle:
                    should_discard = (
                        discard_first_blink_within_seconds is not None
                        and not intervals
                        and time_counter - capture_started_at <= discard_first_blink_within_seconds
                    )
                    if not should_discard:
                        intervals.append(time_counter - prev_time)
                        if progress_callback is not None:
                            progress_callback(len(intervals), config.blink_count)
                    state = state_single
                    prev_time = time_counter
            else:
                match_location = (max_loc[0] + roi_x, max_loc[1] + roi_y)
                match_bottom_right = (match_location[0] + eye_width, match_location[1] + eye_height)
                cv2.rectangle(display_frame, match_location, match_bottom_right, 255, 2)

            if frame_callback is not None:
                frame_callback(display_frame)
            if show_window:
                cv2.imshow("view", display_frame)
                if cv2.waitKey(1) == ord("q"):
                    break
            if state != state_idle and time_counter - prev_time > 0.7:
                state = state_idle
    finally:
        release = getattr(video, "release", None)
        if callable(release):
            release()
        if show_window:
            cv2.destroyAllWindows()
    return intervals


def capture_pokemon_blinks(
    config: BlinkCaptureConfig,
    *,
    should_stop: Callable[[], bool] | None = None,
    frame_callback: Callable[[Any], None] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    show_window: bool = True,
    discard_first_blink_within_seconds: float | None = None,
) -> PokemonBlinkObservation:
    """Capture Pokemon blink intervals through Project_Xs tracking logic."""

    eye_image = _read_grayscale_image(config.eye_image_path)
    _validate_eye_template_config(eye_image, config)

    try:
        if (
            config.uses_shared_video_source
            or should_stop is not None
            or frame_callback is not None
            or progress_callback is not None
            or not show_window
            or discard_first_blink_within_seconds is not None
            or _obs_window_target(config) is not None
        ):
            intervals = _tracking_poke_blink_controlled(
                eye_image,
                config,
                should_stop=should_stop,
                frame_callback=frame_callback,
                progress_callback=progress_callback,
                show_window=show_window,
                discard_first_blink_within_seconds=discard_first_blink_within_seconds,
            )
        else:
            rngtool = _load_module("rngtool")
            with _project_xs_import_path():
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
    except ProjectXsIntegrationError:
        raise
    except Exception as exc:
        raise _tracking_failure("Project_Xs Pokemon blink tracking failed", exc) from exc
    if should_stop is not None and should_stop():
        raise ProjectXsIntegrationError("Pokemon blink capture stopped")

    return PokemonBlinkObservation.from_sequence(intervals)


def capture_preview_frame(config: BlinkCaptureConfig) -> Any:
    """Capture one raw frame using the same source settings as Project_Xs."""

    capture = PreviewFrameCapture(config)
    try:
        return capture.read()
    finally:
        capture.release()


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
    return _read_grayscale_image(config.eye_image_path)


def render_eye_preview(config: BlinkCaptureConfig, frame: Any) -> tuple[Any, EyePreviewResult]:
    """Draw ROI and eye-template match box on a captured frame."""

    cv2 = _load_cv2()
    eye_image = _load_eye_template(config)
    template_size = _validate_eye_template_config(eye_image, config)
    roi_x, roi_y, roi_w, roi_h = config.roi
    if template_size is None:
        try:
            eye_width, eye_height = eye_image.shape[::-1]
        except (AttributeError, TypeError, ValueError) as exc:
            raise ProjectXsCaptureConfigError("眼睛模板尺寸无法读取，请重新框选并保存眼睛模板") from exc
    else:
        eye_width, eye_height = template_size

    try:
        annotated = frame.copy()
        _validate_frame_roi(frame, config, template_size=template_size)
        roi = frame[roi_y : roi_y + roi_h, roi_x : roi_x + roi_w]
        roi_gray = roi if len(roi.shape) == 2 else cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)
        result = cv2.matchTemplate(roi_gray, eye_image, cv2.TM_CCOEFF_NORMED)
        _, match_score, _, max_loc = cv2.minMaxLoc(result)
    except ProjectXsIntegrationError:
        raise
    except Exception as exc:
        raise _tracking_failure("Eye template preview matching failed", exc) from exc

    roi_bottom_right = (roi_x + roi_w, roi_y + roi_h)
    cv2.rectangle(annotated, (roi_x, roi_y), roi_bottom_right, (0, 0, 255), 2)
    match_location = (max_loc[0] + roi_x, max_loc[1] + roi_y)
    if 0.01 < match_score < config.threshold:
        cv2.rectangle(annotated, (roi_x, roi_y), roi_bottom_right, (255, 255, 255), 2)
    else:
        match_bottom_right = (match_location[0] + eye_width, match_location[1] + eye_height)
        cv2.rectangle(annotated, match_location, match_bottom_right, (255, 255, 255), 2)

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


def _state_from_seed_pair(seed0: int, seed1: int) -> SeedState32:
    return SeedState32.from_words(
        (
            (int(seed0) >> 32) & 0xFFFFFFFF,
            int(seed0) & 0xFFFFFFFF,
            (int(seed1) >> 32) & 0xFFFFFFFF,
            int(seed1) & 0xFFFFFFFF,
        )
    )


def _try_native_reidentify_by_intervals(
    state: SeedState32,
    observation: BlinkObservation,
    *,
    npc: int = 0,
    search_min: int = 0,
    search_max: int = 1_000_000,
) -> tuple[SeedState32, int] | None:
    try:
        native = importlib.import_module("auto_bdsp_rng.rng_core._native")
        seed0, seed1 = state.seed64_pair
        result = native.reidentify_by_intervals(
            seed0,
            seed1,
            list(observation.intervals),
            int(npc),
            int(search_min),
            int(search_max),
        )
    except Exception:
        return None
    if result is None:
        return None
    try:
        native_seed0, native_seed1, advances = result
        return _state_from_seed_pair(int(native_seed0), int(native_seed1)), int(advances)
    except (TypeError, ValueError) as exc:
        raise ProjectXsIntegrationError("原生校正返回了无效结果") from exc


def _try_native_reidentify_by_intervals_noisy(
    state: SeedState32,
    observation: BlinkObservation,
    *,
    search_min: int = 0,
    search_max: int = 100_000,
) -> tuple[SeedState32, int] | None:
    try:
        native = importlib.import_module("auto_bdsp_rng.rng_core._native")
        seed0, seed1 = state.seed64_pair
        result = native.reidentify_by_intervals_noisy(
            seed0,
            seed1,
            list(observation.intervals),
            int(search_min),
            int(search_max),
        )
    except Exception:
        return None
    if result is None:
        return None
    try:
        native_seed0, native_seed1, advances = result
        return _state_from_seed_pair(int(native_seed0), int(native_seed1)), int(advances)
    except (TypeError, ValueError) as exc:
        raise ProjectXsIntegrationError("原生抗干扰校正返回了无效结果") from exc


def reidentify_seed_from_observation(
    state: SeedState32,
    observation: BlinkObservation,
    *,
    npc: int = 0,
    search_min: int = 0,
    search_max: int = 1_000_000,
) -> ProjectXsReidentifyResult:
    """Reidentify Project_Xs Xorshift state from later blink intervals."""

    native_result = _try_native_reidentify_by_intervals(
        state,
        observation,
        npc=npc,
        search_min=search_min,
        search_max=search_max,
    )
    if native_result is not None:
        reidentified_state, advances = native_result
        return ProjectXsReidentifyResult(
            state=reidentified_state,
            observation=observation,
            advances=advances,
            backend="native",
        )

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
        raise ProjectXsIntegrationError("Project_Xs 校正失败") from exc

    if reidentified_rng is None:
        raise ProjectXsIntegrationError("Project_Xs 校正未找到匹配状态")

    try:
        reidentified_state = SeedState32.from_words(reidentified_rng.get_state())
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProjectXsIntegrationError("Project_Xs 校正返回了无效 Seed 状态") from exc

    return ProjectXsReidentifyResult(
        state=reidentified_state,
        observation=observation,
        advances=int(advances),
        backend="python",
    )


def reidentify_seed_from_observation_noisy(
    state: SeedState32,
    observation: BlinkObservation,
    *,
    search_min: int = 0,
    search_max: int = 100_000,
) -> ProjectXsReidentifyResult:
    """Reidentify Project_Xs Xorshift state with the 1 Pokemon NPC noisy flow."""

    native_result = _try_native_reidentify_by_intervals_noisy(
        state,
        observation,
        search_min=search_min,
        search_max=search_max,
    )
    if native_result is not None:
        reidentified_state, advances = native_result
        return ProjectXsReidentifyResult(
            state=reidentified_state,
            observation=observation,
            advances=advances,
            backend="native",
        )

    rngtool = _load_module("rngtool")
    xorshift = _load_module("xorshift")
    try:
        rng = xorshift.Xorshift(*state.words)
        reidentified_rng, advances = rngtool.reidentiy_by_intervals_noisy(
            rng,
            list(observation.intervals),
            search_min=search_min,
            search_max=search_max,
        )
    except Exception as exc:
        raise ProjectXsIntegrationError("Project_Xs 抗干扰校正失败") from exc

    if reidentified_rng is None:
        raise ProjectXsIntegrationError("Project_Xs 抗干扰校正未找到匹配状态")

    try:
        reidentified_state = SeedState32.from_words(reidentified_rng.get_state())
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProjectXsIntegrationError("Project_Xs 抗干扰校正返回了无效 Seed 状态") from exc

    return ProjectXsReidentifyResult(
        state=reidentified_state,
        observation=observation,
        advances=int(advances),
        backend="python",
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


def plan_timeline(
    state: SeedState32,
    *,
    max_events: int,
    timeline_npc: int = 0,
    pokemon_npc: int = 0,
    start_advances: int = 0,
    start_time: float = 0.0,
) -> tuple[TimelineEvent, ...]:
    """Plan Project_Xs timeline events without sleeping or pressing keys."""

    if max_events < 0:
        raise ProjectXsIntegrationError("Timeline event count must be non-negative")
    if timeline_npc < 0 or pokemon_npc < 0:
        raise ProjectXsIntegrationError("Timeline NPC counts must be non-negative")

    xorshift = _load_module("xorshift")
    try:
        rng = xorshift.Xorshift(*state.words)
        queue: list[tuple[float, int]] = []
        for _ in range(timeline_npc + 1):
            heapq.heappush(queue, (start_time + 1.017, 0))
        for _ in range(pokemon_npc):
            interval = rng.rangefloat(3, 12) + 0.285
            heapq.heappush(queue, (start_time + interval, 1))

        events = []
        advances = start_advances
        while queue and len(events) < max_events:
            scheduled_time, event_type = heapq.heappop(queue)
            advances += 1
            if event_type == 0:
                rand = int(rng.next())
                events.append(
                    TimelineEvent(
                        advance=advances,
                        event_type="blink",
                        scheduled_time=float(scheduled_time),
                        rand=rand,
                    )
                )
                heapq.heappush(queue, (scheduled_time + 1.017, 0))
            else:
                interval = float(rng.rangefloat(3, 12) + 0.285)
                events.append(
                    TimelineEvent(
                        advance=advances,
                        event_type="pokemon",
                        scheduled_time=float(scheduled_time),
                        next_interval=interval,
                    )
                )
                heapq.heappush(queue, (scheduled_time + interval, 1))
    except Exception as exc:
        raise ProjectXsIntegrationError("Project_Xs timeline planning failed") from exc

    return tuple(events)
