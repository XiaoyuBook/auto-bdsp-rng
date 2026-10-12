"""Image codecs with Python-owned, Unicode-safe file access."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any


def read_image(path: str | Path, flags: int | None = None) -> Any:
    import cv2
    import numpy as np

    source = Path(path)
    payload = source.read_bytes()
    if not payload:
        raise OSError(f"Cannot decode empty image: {source}")
    image = cv2.imdecode(
        np.frombuffer(payload, dtype=np.uint8),
        cv2.IMREAD_COLOR if flags is None else flags,
    )
    if image is None:
        raise OSError(f"Cannot decode image: {source}")
    return image


def write_image(path: str | Path, image: Any) -> Path:
    """Encode before touching the destination and publish a complete file atomically."""
    import cv2

    output = Path(path)
    ok, encoded = cv2.imencode(output.suffix, image)
    if not ok:
        raise OSError(f"Cannot encode image: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".image-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded.tobytes())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output
