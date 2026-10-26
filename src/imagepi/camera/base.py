"""Camera backend interface.

Threading contract: ``start(on_frame)`` registers a *notification* that may be invoked from any
(SDK) thread. It must return immediately and never call back into the backend. The consumer then
calls ``pull()`` from its own thread to fetch the newest preview frame, dropping stale ones.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

import numpy as np


class CameraError(RuntimeError):
    pass


class PermissionDenied(CameraError):
    """Typically a missing udev rule on Linux."""


@dataclass
class CameraInfo:
    id: str
    name: str
    backend: str
    resolutions: list[tuple[int, int]] = field(default_factory=list)
    pixel_size_um: tuple[float, float] | None = None  # physical sensor pixel size


@dataclass
class Range:
    min: float
    max: float
    default: float


class CameraBackend(ABC):
    name = "base"

    # ---- discovery ----
    @classmethod
    @abstractmethod
    def enumerate(cls) -> list[CameraInfo]: ...

    # ---- lifecycle ----
    @abstractmethod
    def open(self, camera_id: str) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    # ---- configuration (resolution/bit depth only while stopped) ----
    @property
    @abstractmethod
    def resolutions(self) -> list[tuple[int, int]]: ...

    @abstractmethod
    def set_resolution(self, index: int) -> None: ...

    @property
    @abstractmethod
    def resolution_index(self) -> int: ...

    @abstractmethod
    def set_exposure(self, microseconds: int) -> None: ...

    @abstractmethod
    def get_exposure(self) -> int: ...

    @abstractmethod
    def exposure_range(self) -> Range: ...

    @abstractmethod
    def set_gain(self, percent: int) -> None: ...

    @abstractmethod
    def get_gain(self) -> int: ...

    @abstractmethod
    def gain_range(self) -> Range: ...

    def set_auto_exposure(self, on: bool) -> None:
        pass

    def get_auto_exposure(self) -> bool:
        return False

    # Auto exposure may lengthen the exposure until the live view crawls (the ToupTek default limit is 350 ms = 2.9 fps).
    supports_auto_exposure_limit = False

    def set_auto_exposure_limit(self, microseconds: int) -> None:
        """Longest exposure auto exposure may choose (it raises the gain instead). No-op where unsupported."""

    # ---- orientation ----
    # A microscope's optics often mirror or invert the picture, so the user can flip it. Applied to previews and stills.
    _flip: tuple[bool, bool] = (False, False)   # (horizontal, vertical)

    def set_flip(self, horizontal: bool, vertical: bool) -> None:
        self._flip = (bool(horizontal), bool(vertical))

    def get_flip(self) -> tuple[bool, bool]:
        return self._flip

    def _oriented(self, arr: np.ndarray) -> np.ndarray:
        """``arr`` mirrored as set by ``set_flip`` (a view; callers copy)."""
        h, v = self._flip
        if h:
            arr = arr[:, ::-1]
        if v:
            arr = arr[::-1]
        return arr

    def _roi_to_sensor(self, x: int, y: int, w: int, h: int, full_w: int, full_h: int) -> tuple[int, int, int, int]:
        """A rectangle in displayed (flipped) full-frame pixels -> the same region in unflipped sensor pixels."""
        fh, fv = self._flip
        return (full_w - x - w if fh else x, full_h - y - h if fv else y, w, h)

    def set_white_balance(self, temp: int, tint: int) -> None:
        pass

    def get_white_balance(self) -> tuple[int, int]:
        return (6503, 1000)

    def auto_white_balance(self) -> None:
        pass

    def set_roi(self, x: int, y: int, w: int, h: int) -> None:
        raise NotImplementedError

    def set_bit_depth(self, bits: int) -> None:
        if bits != 8:
            raise NotImplementedError

    @property
    def supports_16bit(self) -> bool:
        return False

    # ---- streaming ----
    @abstractmethod
    def start(self, on_frame: Callable[[], None], on_event: Callable[[str], None] | None = None) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @property
    @abstractmethod
    def running(self) -> bool: ...

    @abstractmethod
    def pull(self) -> np.ndarray | None:
        """Newest preview frame as (H, W, 3) uint8 RGB or (H, W) uint8, or None if nothing new."""

    @abstractmethod
    def snap(self, bits: int = 8) -> np.ndarray:
        """Capture a still at the largest resolution; blocks until it arrives. (H,W,3) uint8/uint16."""

    def metadata(self) -> dict:
        """Acquisition metadata to store with saved images."""
        w, h = self.resolutions[self.resolution_index]
        md = {
            "camera": self.name, "exposure_us": self.get_exposure(), "gain_percent": self.get_gain(),
            "width": w, "height": h, "white_balance": list(self.get_white_balance()),
        }
        if any(self._flip):
            md["flip"] = {"horizontal": self._flip[0], "vertical": self._flip[1]}
        return md
