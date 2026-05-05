from __future__ import annotations

import subprocess
from collections.abc import Sequence


def start_process(args: Sequence[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
