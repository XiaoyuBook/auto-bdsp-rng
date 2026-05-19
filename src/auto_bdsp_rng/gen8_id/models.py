from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence


@dataclass(frozen=True)
class IDState8:
    advances: int
    tid: int
    sid: int
    tsv: int
    display_tid: int


@dataclass(frozen=True)
class IDFilter:
    tid: Sequence[int] | None = None
    sid: Sequence[int] | None = None
    tsv: Sequence[int] | None = None
    display_tid: Sequence[int] | None = None

    def compare_state(self, state: IDState8) -> bool:
        if self.tid is not None and state.tid not in self.tid:
            return False
        if self.sid is not None and state.sid not in self.sid:
            return False
        if self.tsv is not None and state.tsv not in self.tsv:
            return False
        if self.display_tid is not None and state.display_tid not in self.display_tid:
            return False
        return True
