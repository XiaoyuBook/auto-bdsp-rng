from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping


NOTE_REGION_FIELDS = ("nature", "characteristic")
STAT_REGION_FIELDS = ("hp", "attack", "defense", "sp_attack", "sp_defense", "speed")
OCR_REGION_FIELDS = NOTE_REGION_FIELDS + STAT_REGION_FIELDS

OCR_REGION_LABELS = {
    "nature": "性格",
    "characteristic": "个性",
    "hp": "HP",
    "attack": "攻击",
    "defense": "防御",
    "sp_attack": "特攻",
    "sp_defense": "特防",
    "speed": "速度",
}

STAT_FIELD_NAMES = {
    "hp": "HP",
    "attack": "攻击",
    "defense": "防御",
    "sp_attack": "特攻",
    "sp_defense": "特防",
    "speed": "速度",
}


@dataclass(frozen=True)
class OcrRegion:
    x: int
    y: int
    width: int
    height: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)

    def is_valid(self) -> bool:
        return self.width > 0 and self.height > 0

    def clip(self, image_width: int, image_height: int) -> OcrRegion:
        left = max(0, min(int(self.x), max(0, image_width)))
        top = max(0, min(int(self.y), max(0, image_height)))
        right = max(left, min(int(self.x + self.width), max(0, image_width)))
        bottom = max(top, min(int(self.y + self.height), max(0, image_height)))
        return OcrRegion(left, top, right - left, bottom - top)

    def expanded(self, image_width: int, image_height: int, *, ratio: float = 0.12, min_pixels: int = 4) -> OcrRegion:
        grow_x = max(min_pixels, round(self.width * ratio))
        grow_y = max(min_pixels, round(self.height * ratio))
        return OcrRegion(self.x - grow_x, self.y - grow_y, self.width + grow_x * 2, self.height + grow_y * 2).clip(
            image_width,
            image_height,
        )

    def to_relative_bounds(self, image_shape: tuple[int, ...]) -> tuple[float, float, float, float]:
        image_height = int(image_shape[0])
        image_width = int(image_shape[1])
        clipped = self.clip(image_width, image_height)
        if image_width <= 0 or image_height <= 0 or not clipped.is_valid():
            return (0.0, 0.0, 0.0, 0.0)
        return (
            clipped.x / image_width,
            (clipped.x + clipped.width) / image_width,
            clipped.y / image_height,
            (clipped.y + clipped.height) / image_height,
        )

    def to_settings_value(self) -> str:
        return json.dumps(self.as_tuple(), ensure_ascii=False)

    @classmethod
    def from_settings_value(cls, value: object) -> OcrRegion | None:
        if value in (None, ""):
            return None
        raw: object = value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                raw = [part.strip() for part in text.split(",")]
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            return None
        try:
            region = cls(*(int(part) for part in raw))
        except Exception:
            return None
        return region if region.is_valid() else None


class OcrRegionConfig:
    def __init__(self, regions: Mapping[str, OcrRegion | tuple[int, int, int, int]] | None = None) -> None:
        self._regions: dict[str, OcrRegion] = {}
        for field, region in (regions or {}).items():
            self.set(field, region)

    def get(self, field: str) -> OcrRegion | None:
        return self._regions.get(field)

    def set(self, field: str, region: OcrRegion | tuple[int, int, int, int] | None) -> None:
        if field not in OCR_REGION_FIELDS:
            raise KeyError(f"Unknown OCR region field: {field}")
        if region is None:
            self._regions.pop(field, None)
            return
        if not isinstance(region, OcrRegion):
            region = OcrRegion(*(int(value) for value in region))
        if region.is_valid():
            self._regions[field] = region

    def remove(self, field: str) -> None:
        self.set(field, None)

    def has_all_stats(self) -> bool:
        return all(field in self._regions for field in STAT_REGION_FIELDS)

    def is_empty(self) -> bool:
        return not self._regions

    def items(self):
        return self._regions.items()

    def to_settings_dict(self) -> dict[str, str]:
        return {field: region.to_settings_value() for field, region in self._regions.items()}

    @classmethod
    def from_settings_dict(cls, values: Mapping[str, object]) -> OcrRegionConfig:
        config = cls()
        for field in OCR_REGION_FIELDS:
            region = OcrRegion.from_settings_value(values.get(field))
            if region is not None:
                config.set(field, region)
        return config
