"""ImageDoc: the document model behind every open image."""

from __future__ import annotations

import copy
import os
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .calibration import Calibration
from .luts import grays
from .roi import Roi


def dtype_range(dtype) -> tuple[float, float]:
    dt = np.dtype(dtype)
    if dt.kind in "ui":
        i = np.iinfo(dt)
        return float(i.min), float(i.max)
    return 0.0, 1.0


def _undo_budget() -> int:
    """Undo snapshots may use up to 512 MB, but never more than ~10% of physical RAM (Pi 5 has 4-16 GB)."""
    try:
        ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 512 * 1024 * 1024
    return int(min(512 * 1024 * 1024, max(ram // 10, 64 * 1024 * 1024)))


class ImageDoc:
    """An image or stack.

    ``data`` is always (N, H, W) for gray or (N, H, W, C) for colour, so a single
    image is a stack of one. ``plane`` is the current frame index.
    """

    UNDO_BYTES = _undo_budget()

    def __init__(self, data: np.ndarray, title: str = "Untitled", *, rgb: bool | None = None):
        data = np.asarray(data)
        if rgb is None:
            # (H, W, 3|4) and any 4-D array are colour; a 3-D array with a tiny last axis is ambiguous
            # with a stack, so callers with stacks should pass rgb=False.
            rgb = data.ndim == 4 or (data.ndim == 3 and data.shape[-1] in (3, 4))
        if rgb and data.shape[-1] == 4:
            data = data[..., :3]
        expected = 4 if rgb else 3
        if data.ndim == expected - 1:
            data = data[None]
        if data.ndim != expected:
            raise ValueError(f"unsupported image shape {data.shape} (rgb={rgb})")
        self.data = data
        self.title = title
        self.path: str | None = None
        self.plane = 0
        self.calibration = Calibration()
        self.lut: np.ndarray = grays()
        self.display_range: tuple[float, float] = self.full_range()
        self.rois: list[Roi] = []
        self.roi: Roi | None = None
        self.metadata: dict = {}
        self.labels: np.ndarray | None = None  # overlay label image (e.g. from Analyze Particles)
        self.changed = False
        self.version = 0  # bumped whenever pixel data is replaced (cache key for the display pipeline)
        self.listeners: list = []
        # (label, pixel data, display range): the range is restored when undo/redo changes the pixel type
        self._undo: deque[tuple[str, np.ndarray, tuple[float, float]]] = deque()
        self._redo: list[tuple[str, np.ndarray, tuple[float, float]]] = []
        self.reset_range()

    # ---- geometry ----
    @property
    def rgb(self) -> bool:
        return self.data.ndim == 4

    @property
    def n_frames(self) -> int:
        return self.data.shape[0]

    @property
    def height(self) -> int:
        return self.data.shape[1]

    @property
    def width(self) -> int:
        return self.data.shape[2]

    @property
    def shape2d(self) -> tuple[int, int]:
        return self.data.shape[1], self.data.shape[2]

    @property
    def dtype(self):
        return self.data.dtype

    def frame(self, i: int | None = None) -> np.ndarray:
        return self.data[self.plane if i is None else i]

    def gray_frame(self, i: int | None = None) -> np.ndarray:
        """Single-channel view of a frame (luminance for RGB)."""
        f = self.frame(i)
        if not self.rgb:
            return f
        out = f.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
        return out.astype(f.dtype) if f.dtype.kind in "ui" else out

    def full_range(self) -> tuple[float, float]:
        return dtype_range(self.data.dtype)

    def reset_range(self) -> None:
        """Default window: full range for 8-bit (shown as acquired), percentile stretch for deeper data."""
        self.display_range = self.full_range()
        if self.data.dtype != np.uint8:
            self.auto_range()

    def auto_range(self, saturated: float = 0.35) -> None:
        """Stretch the window to the current frame, clipping ``saturated`` percent of pixels."""
        f = self.gray_frame()
        lo, hi = np.percentile(f, [saturated / 2, 100 - saturated / 2])
        if hi <= lo:
            lo, hi = float(f.min()), float(f.max())
            if hi <= lo:
                hi = lo + 1
        self.display_range = (float(lo), float(hi))

    # ---- ROI ----
    def set_roi(self, roi: Roi | None) -> None:
        self.roi = roi
        self.notify("roi")

    def roi_mask(self) -> np.ndarray | None:
        if self.roi is None or not self.roi.is_area:
            return None
        return self.roi.mask(self.shape2d)

    # ---- undo ----
    def snapshot(self, label: str = "") -> None:
        self._undo.append((label, self.data.copy(), self.display_range))
        self._redo.clear()
        total = sum(e[1].nbytes for e in self._undo)
        while len(self._undo) > 1 and total > self.UNDO_BYTES:
            total -= self._undo.popleft()[1].nbytes

    def can_undo(self) -> bool:
        return bool(self._undo)

    def undo_label(self) -> str | None:
        """Name of the change Undo would take back (None: nothing to undo), so the window can say what it did."""
        return self._undo[-1][0] if self._undo else None

    def redo_label(self) -> str | None:
        return self._redo[-1][0] if self._redo else None

    def undo(self) -> bool:
        if not self._undo:
            return False
        label, prev, rng = self._undo.pop()
        self._redo.append((label, self.data, self.display_range))
        self._swap_data(prev, rng)
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        label, nxt, rng = self._redo.pop()
        self._undo.append((label, self.data, self.display_range))
        self._swap_data(nxt, rng)
        return True

    def _swap_data(self, data: np.ndarray, display_range: tuple[float, float]) -> None:
        """Install undo/redo data. A window from another pixel type (0..255 on 16-bit data) would show a blank image."""
        if data.dtype != self.data.dtype:
            self.display_range = display_range
        self.data = data
        self.version += 1
        self.plane = min(self.plane, self.n_frames - 1)
        self.notify("data")

    def replace_data(self, data: np.ndarray, *, snapshot: str | None = None, keep_range: bool = False) -> None:
        if snapshot is not None:
            self.snapshot(snapshot)
        data = np.asarray(data)
        if data.ndim not in (3, 4):
            raise ValueError("replace_data expects a stack shaped (N,H,W) or (N,H,W,C)")
        old_dtype = self.data.dtype
        self.data = data
        self.version += 1
        self.plane = min(self.plane, self.n_frames - 1)
        self.changed = True
        if data.dtype != old_dtype and not keep_range:
            self.reset_range()
        self.notify("data")

    # ---- observers ----
    def notify(self, what: str = "data") -> None:
        for f in list(self.listeners):
            f(self, what)

    def set_plane(self, i: int) -> None:
        self.plane = int(np.clip(i, 0, self.n_frames - 1))
        self.notify("plane")

    def set_lut(self, lut: np.ndarray) -> None:
        self.lut = np.asarray(lut, np.uint8)
        self.notify("lut")

    def set_display_range(self, lo: float, hi: float) -> None:
        self.display_range = (float(lo), float(hi))
        self.notify("range")

    def info(self) -> dict:
        return {
            "title": self.title, "width": self.width, "height": self.height, "frames": self.n_frames,
            "dtype": str(self.dtype), "rgb": self.rgb, "calibration": self.calibration.to_dict(),
            "path": self.path,
        }

    def duplicate(self, title: str | None = None) -> "ImageDoc":
        d = ImageDoc(self.data.copy(), title or self.title + "-1", rgb=self.rgb)
        d.calibration = copy.copy(self.calibration)
        d.lut = self.lut.copy()
        d.display_range = self.display_range
        d.metadata = copy.deepcopy(self.metadata)
        d.rois = list(self.rois)
        return d
