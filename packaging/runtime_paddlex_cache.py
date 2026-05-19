from __future__ import annotations

import os
import sys
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


cache_home = _base_dir() / "paddlex_cache"
if (cache_home / "official_models").exists():
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(cache_home))
