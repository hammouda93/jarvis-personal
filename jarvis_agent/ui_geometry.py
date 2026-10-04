"""Explicit screenshot/crop/physical-screen transforms, independent of Windows."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


def valid_rect(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in value):
        return None
    rect = tuple(float(x) for x in value)
    if not all(math.isfinite(x) for x in rect) or rect[2] <= rect[0] or rect[3] <= rect[1]:
        return None
    return rect


def normalized_box(value: Any, domain: int = 1000) -> tuple[float, ...] | None:
    rect = valid_rect(value)
    if rect is None or domain not in (999, 1000):
        return None
    return rect if all(0 <= x <= domain for x in rect) else None


@dataclass(frozen=True)
class CaptureGeometry:
    screen_bounds: tuple[float, float, float, float]
    image_width: int
    image_height: int
    crop: tuple[float, float, float, float] | None = None
    normalized_domain: int = 1000

    def __post_init__(self):
        if valid_rect(self.screen_bounds) is None:
            raise ValueError("Invalid physical screen bounds")
        if self.image_width <= 0 or self.image_height <= 0 or self.normalized_domain not in (999, 1000):
            raise ValueError("Invalid image geometry")
        if self.crop is not None:
            rect = valid_rect(self.crop)
            width = self.screen_bounds[2] - self.screen_bounds[0]
            height = self.screen_bounds[3] - self.screen_bounds[1]
            if rect is None or rect[0] < 0 or rect[1] < 0 or rect[2] > width or rect[3] > height:
                raise ValueError("Crop outside the original window")

    @property
    def physical_crop(self) -> tuple[float, float, float, float]:
        left, top, right, bottom = self.screen_bounds
        if self.crop is None:
            return left, top, right, bottom
        x1, y1, x2, y2 = self.crop
        return left + x1, top + y1, left + x2, top + y2

    def box_to_screen(self, box: Any) -> tuple[float, float, float, float]:
        rect = normalized_box(box, self.normalized_domain)
        if rect is None:
            raise ValueError("Invalid normalized box")
        l, t, r, b = self.physical_crop
        scale = float(self.normalized_domain)
        x1, y1, x2, y2 = rect
        return l+x1/scale*(r-l), t+y1/scale*(b-t), l+x2/scale*(r-l), t+y2/scale*(b-t)

    def click_point(self, box: Any) -> tuple[int, int]:
        l, t, r, b = self.box_to_screen(box)
        point = int(round((l+r)/2)), int(round((t+b)/2))
        # Right/bottom edges are exclusive. Never deliver onto an adjacent window.
        outer = self.physical_crop
        if not (outer[0] <= point[0] < outer[2] and outer[1] <= point[1] < outer[3]):
            raise ValueError("Click point outside capture")
        return point

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> CaptureGeometry:
        bounds = valid_rect(metadata.get("bounds"))
        if bounds is None:
            raise ValueError("Missing capture bounds")
        return cls(
            bounds, int(metadata.get("captured_width") or bounds[2]-bounds[0]),
            int(metadata.get("captured_height") or bounds[3]-bounds[1]),
            tuple(metadata["crop"]) if metadata.get("crop") is not None else None,
            int(metadata.get("normalized_domain") or 1000),
        )


def intersection_over_union(a: Any, b: Any) -> float:
    a, b = valid_rect(a), valid_rect(b)
    if a is None or b is None:
        return 0.0
    intersection = max(0.0, min(a[2], b[2])-max(a[0], b[0])) * max(
        0.0, min(a[3], b[3])-max(a[1], b[1])
    )
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
    return intersection/union if union > 0 else 0.0
