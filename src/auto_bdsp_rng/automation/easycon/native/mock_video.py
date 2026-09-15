"""Static video client used only by the development device entry points."""

from collections.abc import Callable

import numpy as np


class MockVideoClient:
    def __init__(self, frame: np.ndarray, is_current: Callable[[], bool]) -> None:
        self._frame = frame
        self._is_current = is_current
        self._closed = False

    def read_array(self) -> np.ndarray:
        if self._closed or not self._is_current():
            raise RuntimeError("Mock 视频源已断开")
        return self._frame.copy()

    def close(self) -> None:
        self._closed = True
