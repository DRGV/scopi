# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Spatial filters (Process > Filters)."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np
from scipy import ndimage as ndi
from skimage import restoration

from .base import Choice, Filter, Float, Int, register, to_float

CAT = "Process>Filters"


@register
class Mean(Filter):
    title, category = "Mean", CAT
    view = [Int("radius", "Radius", (1, 50), 2, "px")]

    def run(self, img, para):
        return ndi.uniform_filter(to_float(img), size=2 * para["radius"] + 1, mode="nearest")


def gaussian(a: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur with SciPy's kernel (truncated at 4 sigma, edge pixels replicated), run by OpenCV.

    Same result to float precision, but OpenCV is SIMD and multi-threaded: 12 ms instead of 400 ms for an 18 MP plane on
    a Mac, and the Pi's 0.66 s (gray) / 2 s (RGB) for sigma 2 were the slowest thing in the benchmark.
    """
    r = int(4.0 * sigma + 0.5)
    try:
        return cv2.GaussianBlur(a, (2 * r + 1, 2 * r + 1), sigma, borderType=cv2.BORDER_REPLICATE)
    except cv2.error:
        return ndi.gaussian_filter(a, sigma, mode="nearest")


@register
class Gaussian(Filter):
    title, category = "Gaussian Blur", CAT
    view = [Float("sigma", "Sigma", (0.1, 50.0), 2.0, 1, "px")]

    def run(self, img, para):
        return gaussian(to_float(img), para["sigma"])


@register
class Median(Filter):
    title, category = "Median", CAT
    view = [Int("radius", "Radius", (1, 30), 2, "px")]

    def run(self, img, para):
        k = 2 * para["radius"] + 1
        if img.dtype in (np.uint8, np.uint16, np.float32):
            try:
                # OpenCV's medianBlur is far faster than scipy; large kernels on 16-bit/float need a newer OpenCV
                return cv2.medianBlur(np.ascontiguousarray(img), k)
            except cv2.error:
                pass
        return _strip_parallel(lambda a: ndi.median_filter(a, size=k, mode="nearest"), img, k // 2)


def _strip_parallel(fn, img: np.ndarray, halo: int, strips: int | None = None) -> np.ndarray:
    """Apply a neighbourhood filter on horizontal strips in threads (scipy releases the GIL)."""
    n = strips or min(os.cpu_count() or 1, 8)
    h = img.shape[0]
    if n <= 1 or h < 4 * n * max(halo, 1):
        return fn(img)
    edges = np.linspace(0, h, n + 1).astype(int)
    out = np.empty_like(img)

    def work(i):
        y0, y1 = edges[i], edges[i + 1]
        s0, s1 = max(y0 - halo, 0), min(y1 + halo, h)
        res = fn(img[s0:s1])
        out[y0:y1] = res[y0 - s0:res.shape[0] - (s1 - y1)]

    with ThreadPoolExecutor(n) as ex:
        list(ex.map(work, range(n)))
    return out


@register
class Minimum(Filter):
    title, category = "Minimum", CAT
    view = [Int("radius", "Radius", (1, 50), 2, "px")]

    def run(self, img, para):
        k = 2 * para["radius"] + 1
        return _strip_parallel(lambda a: ndi.minimum_filter(a, size=k, mode="nearest"), img, k // 2)


@register
class Maximum(Filter):
    title, category = "Maximum", CAT
    view = [Int("radius", "Radius", (1, 50), 2, "px")]

    def run(self, img, para):
        k = 2 * para["radius"] + 1
        return _strip_parallel(lambda a: ndi.maximum_filter(a, size=k, mode="nearest"), img, k // 2)


@register
class UnsharpMask(Filter):
    title, category = "Unsharp Mask", CAT
    view = [Float("sigma", "Sigma", (0.1, 50.0), 1.0, 1, "px"), Float("weight", "Weight", (0.1, 0.9), 0.6, 2)]

    def run(self, img, para):
        f = to_float(img)
        blur = ndi.gaussian_filter(f, para["sigma"], mode="nearest")
        w = para["weight"]
        return (f - w * blur) / (1 - w)


@register
class Sobel(Filter):
    title, category = "Find Edges (Sobel)", CAT

    def run(self, img, para):
        f = to_float(img)
        return np.hypot(ndi.sobel(f, 0, mode="nearest"), ndi.sobel(f, 1, mode="nearest"))


@register
class Laplace(Filter):
    title, category = "Laplace", CAT
    view = [Float("sigma", "Pre-smoothing sigma", (0.0, 20.0), 0.0, 1, "px")]

    def run(self, img, para):
        f = to_float(img)
        if para["sigma"] > 0:
            return ndi.gaussian_laplace(f, para["sigma"], mode="nearest")
        return ndi.laplace(f, mode="nearest")


@register
class DoG(Filter):
    title, category = "Difference of Gaussians", CAT
    view = [Float("sigma1", "Sigma 1", (0.1, 50.0), 1.0, 1, "px"), Float("sigma2", "Sigma 2", (0.1, 100.0), 2.0, 1, "px")]

    def run(self, img, para):
        f = to_float(img)
        return ndi.gaussian_filter(f, para["sigma1"], mode="nearest") - ndi.gaussian_filter(f, para["sigma2"], mode="nearest")


@register
class SubtractBackground(Filter):
    """Rolling-ball background subtraction."""

    title, category = "Subtract Background", CAT
    view = [Int("radius", "Rolling ball radius", (1, 500), 50, "px"), Choice("background", "Background", ["dark", "light"])]

    def run(self, img, para):
        f = to_float(img)
        light = para["background"] == "light"
        if light:
            f = f.max() - f
        r = para["radius"]
        shrink = 1 if r <= 10 else min(int(r // 10), 8)
        small = f[::shrink, ::shrink] if shrink > 1 else f
        bg = restoration.rolling_ball(small, radius=max(r / shrink, 1))
        if shrink > 1:
            bg = cv2.resize(bg.astype(np.float32), (f.shape[1], f.shape[0]), interpolation=cv2.INTER_LINEAR)
        bg = np.minimum(bg, f)
        out = f - bg
        return (out.max() - out) if light else out


@register
class Invert(Filter):
    title, category = "Invert", "Edit"
    note = frozenset({"auto_snap", "auto_msk", "preview", "stack"})

    def run(self, img, para):
        if img.dtype.kind in "ui":
            return np.iinfo(img.dtype).max - img
        return img.max() + img.min() - img


@register
class Smooth(Filter):
    title, category = "Smooth (3x3)", CAT

    def run(self, img, para):
        return ndi.uniform_filter(to_float(img), size=3, mode="nearest")
