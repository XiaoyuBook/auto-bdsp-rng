"""Cooperative cancellation shared by Python and native RNG searches."""
from __future__ import annotations

import threading

try:
    from auto_bdsp_rng.rng_core._native import SearchCancellation as _NativeCancellation, SearchCancelled
except ImportError:
    _NativeCancellation = None

    class SearchCancelled(RuntimeError):
        pass


class SearchCancellation:
    def __init__(self) -> None:
        self.native = _NativeCancellation() if _NativeCancellation is not None else None
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()
        if self.native is not None:
            self.native.cancel()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self.is_cancelled():
            raise SearchCancelled("Search cancelled")
