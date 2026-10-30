# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Spatial calibration: pixel size, unit and scale-bar helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

UNIT_TO_UM = {"nm": 1e-3, "um": 1.0, "µm": 1.0, "mm": 1e3, "cm": 1e4, "m": 1e6, "pixel": None}


@dataclass
class Calibration:
    pixel_size: float = 1.0  # units per pixel
    unit: str = "pixel"

    @property
    def calibrated(self) -> bool:
        return self.unit != "pixel" and self.pixel_size > 0

    def to_units(self, pixels: float) -> float:
        return pixels * self.pixel_size

    def area_to_units(self, pixels2: float) -> float:
        return pixels2 * self.pixel_size ** 2

    def from_known_distance(self, pixel_length: float, known: float, unit: str) -> "Calibration":
        if pixel_length <= 0 or known <= 0:
            raise ValueError("pixel_length and known distance must be positive")
        return Calibration(known / pixel_length, unit)

    def scale_bar_length(self, image_width_px: int, fraction: float = 0.2) -> tuple[float, float]:
        """Return (length_in_units, length_in_pixels) of a 'nice' bar ~fraction of the width."""
        if not self.calibrated:
            return float(image_width_px * fraction), image_width_px * fraction
        target = image_width_px * fraction * self.pixel_size
        exp = math.floor(math.log10(target))
        base = 10 ** exp
        best = base
        for m in (1, 2, 5, 10):
            if m * base <= target:
                best = m * base
        return best, best / self.pixel_size

    def describe(self) -> str:
        """'1 pixel = 0.00203 mm (492.6 pixels per mm)', for the status line after Set Scale."""
        if not self.calibrated:
            return "Scale cleared: measurements are in pixels"
        return f"Scale: 1 pixel = {self.pixel_size:.6g} {self.unit} ({1 / self.pixel_size:.6g} pixels per {self.unit})"

    def to_dict(self) -> dict:
        return {"pixel_size": self.pixel_size, "unit": self.unit}

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        return cls(float(d.get("pixel_size", 1.0)), d.get("unit", "pixel"))


@dataclass
class ObjectivePresets:
    """Named per-objective calibrations, e.g. from the 0.01 mm stage micrometer."""

    presets: dict[str, Calibration] = field(default_factory=dict)

    def add(self, name: str, cal: Calibration) -> None:
        self.presets[name] = cal

    def get(self, name: str) -> Calibration:
        return self.presets[name]

    def to_dict(self) -> dict:
        return {k: v.to_dict() for k, v in self.presets.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "ObjectivePresets":
        return cls({k: Calibration.from_dict(v) for k, v in d.items()})
