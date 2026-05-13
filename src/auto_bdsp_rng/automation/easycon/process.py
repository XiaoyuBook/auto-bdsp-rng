from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence


def no_window_subprocess_kwargs() -> dict[str, int]:
    if sys.platform != "win32":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def start_process(args: Sequence[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **no_window_subprocess_kwargs(),
    )
