"""Display pipeline: raw plane -> 8-bit RGB for the visible viewport only.

Qt-free so it can be tested headless. For large images a lazily built mip pyramid (2x
INTER_AREA downsamples of the raw data) means zoomed-out views touch only a small image;
window/level and the LUT are applied to the viewport crop, never to the whole 18 MP plane.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

BACKGROUND = (40, 40, 40)
OUTLINE = (255, 220, 0)


def _cv_ok(a: np.ndarray) -> np.ndarray:
    """OpenCV resize/warp support 8U/16U/16S/32F/64F."""
    if a.dtype in (np.uint8, np.uint16, np.int16, np.float32, np.float64):
        return a
    return a.astype(np.float32)


class DisplayPipeline:
    def __init__(self):
        self._token = None
        self._levels: list[np.ndarray] = []

    def _pyramid(self, plane: np.ndarray, token, level: int) -> np.ndarray:
        if token is None or token != self._token:
            self._token = token
            self._levels = [_cv_ok(plane)]
        while len(self._levels) <= level:
            prev = self._levels[-1]
            h, w = prev.shape[:2]
            if h < 2 or w < 2:
                return prev
            self._levels.append(cv2.resize(prev, (max(w // 2, 1), max(h // 2, 1)), interpolation=cv2.INTER_AREA))
        return self._levels[level]

    @staticmethod
    def window(crop: np.ndarray, lo: float, hi: float, lut: np.ndarray | None) -> np.ndarray:
        """Window/level + LUT on a (h, w) or (h, w, 3) crop -> (h, w, 3) uint8."""
        if crop.dtype == np.uint8 and lo == 0 and hi == 255:
            idx = crop
        elif crop.dtype in (np.uint8, np.uint16) and crop.ndim >= 2:
            n = 256 if crop.dtype == np.uint8 else 65536
            table = np.clip((np.arange(n, dtype=np.float32) - lo) * (255.0 / (hi - lo if hi > lo else 1.0)), 0, 255).astype(np.uint8)
            idx = table[crop]
        else:
            scale = 255.0 / (hi - lo if hi > lo else 1.0)
            idx = np.clip((crop.astype(np.float32) - lo) * scale, 0, 255).astype(np.uint8)
        if idx.ndim == 3:
            return np.ascontiguousarray(idx)
        return (lut if lut is not None else _GRAYS)[idx]

    def render(self, plane: np.ndarray, display_range: tuple[float, float], lut: np.ndarray | None,
               view: tuple[float, float, float, float], out_size: tuple[int, int], *, token=None,
               labels: np.ndarray | None = None, background=BACKGROUND) -> np.ndarray:
        """Render image-space rectangle ``view`` = (x0, y0, x1, y1) into an (out_h, out_w, 3) uint8 buffer."""
        out_w, out_h = out_size
        x0, y0, x1, y1 = view
        out = np.empty((out_h, out_w, 3), np.uint8)
        out[:] = background
        if out_w <= 0 or out_h <= 0 or x1 <= x0 or y1 <= y0:
            return out
        scale = out_w / (x1 - x0)
        H, W = plane.shape[:2]
        level = max(0, math.floor(math.log2(1.0 / scale))) if scale < 1 else 0
        src = self._pyramid(plane, token, level)
        level = min(level, len(self._levels) - 1)
        f = 2 ** level
        lh, lw = src.shape[:2]
        # visible crop at this level (with 1px margin for interpolation)
        cx0, cy0 = max(int(math.floor(x0 / f)) - 1, 0), max(int(math.floor(y0 / f)) - 1, 0)
        cx1, cy1 = min(int(math.ceil(x1 / f)) + 1, lw), min(int(math.ceil(y1 / f)) + 1, lh)
        if cx1 <= cx0 or cy1 <= cy0:
            return out
        crop = src[cy0:cy1, cx0:cx1]
        lo, hi = display_range
        rgb = self.window(crop, lo, hi, lut)
        s = scale * f
        M = np.array([[s, 0, ((cx0 + 0.5) * f - x0) * scale - 0.5],
                      [0, s, ((cy0 + 0.5) * f - y0) * scale - 0.5]], np.float64)
        interp = cv2.INTER_NEAREST if s >= 1.0 else cv2.INTER_LINEAR
        cv2.warpAffine(rgb, M, (out_w, out_h), dst=out, flags=interp, borderMode=cv2.BORDER_TRANSPARENT)
        if labels is not None and labels.shape[:2] == (H, W):
            self._draw_outlines(out, labels, view, scale)
        return out

    @staticmethod
    def _draw_outlines(out: np.ndarray, labels: np.ndarray, view, scale: float) -> None:
        x0, y0, x1, y1 = view
        out_h, out_w = out.shape[:2]
        cx0, cy0 = max(int(math.floor(x0)) - 1, 0), max(int(math.floor(y0)) - 1, 0)
        cx1, cy1 = min(int(math.ceil(x1)) + 1, labels.shape[1]), min(int(math.ceil(y1)) + 1, labels.shape[0])
        if cx1 <= cx0 or cy1 <= cy0:
            return
        crop = labels[cy0:cy1, cx0:cx1].astype(np.float32)
        M = np.array([[scale, 0, ((cx0 + 0.5) - x0) * scale - 0.5], [0, scale, ((cy0 + 0.5) - y0) * scale - 0.5]], np.float64)
        warped = cv2.warpAffine(crop, M, (out_w, out_h), flags=cv2.INTER_NEAREST, borderValue=0)
        edge = np.zeros(warped.shape, bool)
        dx = warped[:, 1:] != warped[:, :-1]
        dy = warped[1:, :] != warped[:-1, :]
        edge[:, 1:] |= dx & (warped[:, 1:] > 0)
        edge[:, :-1] |= dx & (warped[:, :-1] > 0)
        edge[1:, :] |= dy & (warped[1:, :] > 0)
        edge[:-1, :] |= dy & (warped[:-1, :] > 0)
        out[edge] = OUTLINE


_GRAYS = np.stack([np.arange(256, dtype=np.uint8)] * 3, axis=1)


def histogram(plane: np.ndarray, lo: float, hi: float, bins: int = 256, max_samples: int = 2_000_000) -> np.ndarray:
    """Histogram of a plane over [lo, hi]; strided subsampling keeps 18 MP planes responsive."""
    a = plane if plane.ndim == 2 else plane.astype(np.float32).mean(axis=-1)
    step = max(math.ceil(math.sqrt(a.size / max_samples)), 1)
    if step > 1:
        a = a[::step, ::step]
    if hi <= lo:
        hi = lo + 1
    h, _ = np.histogram(a, bins=bins, range=(lo, hi))
    return h


def value_at(plane: np.ndarray, x: float, y: float):
    xi, yi = int(math.floor(x)), int(math.floor(y))
    if 0 <= yi < plane.shape[0] and 0 <= xi < plane.shape[1]:
        v = plane[yi, xi]
        return v.tolist() if hasattr(v, "tolist") else v
    return None
