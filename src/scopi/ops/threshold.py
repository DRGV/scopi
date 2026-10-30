"""Thresholding (Image > Adjust > Threshold). Output is an 8-bit mask with 255 = foreground."""

from __future__ import annotations

import numpy as np
from skimage import filters

from .base import Bool, Choice, Filter, Float, Int, register

CAT = "Image>Adjust"

AUTO_METHODS = {
    "Otsu": filters.threshold_otsu,
    "Li": filters.threshold_li,
    "Triangle": filters.threshold_triangle,
    "Isodata": filters.threshold_isodata,
    "Yen": filters.threshold_yen,
    "Mean": filters.threshold_mean,
}


def auto_threshold(img: np.ndarray, method: str) -> float:
    return float(AUTO_METHODS[method](img))


@register
class ManualThreshold(Filter):
    title, category = "Manual Threshold", CAT
    view = [Float("lower", "Lower", (-1e9, 1e9), 128.0, 1), Float("upper", "Upper", (-1e9, 1e9), 255.0, 1)]
    out_dtype = np.uint8

    def view_for(self, doc):
        lo, hi = doc.full_range() if doc.dtype.kind in "ui" else doc.display_range
        d_lo, d_hi = doc.display_range
        digits = 0 if doc.dtype.kind in "ui" else 3
        mid = (d_lo + d_hi) / 2
        return [Float("lower", "Lower", (lo, hi), float(round(mid, digits)), digits), Float("upper", "Upper", (lo, hi), float(d_hi), digits)]

    def run(self, img, para):
        return ((img >= para["lower"]) & (img <= para["upper"])).astype(np.uint8) * 255


@register
class AutoThreshold(Filter):
    title, category = "Auto Threshold", CAT
    view = [Choice("method", "Method", list(AUTO_METHODS)), Choice("objects", "Objects", ["bright", "dark"])]
    out_dtype = np.uint8
    note = frozenset({"auto_snap", "auto_msk", "preview", "stack", "rgb_native"})

    def run(self, img, para):
        if img.ndim == 3:  # colour: threshold luminance
            img = img.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
        t = auto_threshold(img, para["method"])
        m = img > t if para["objects"] == "bright" else img <= t
        return m.astype(np.uint8) * 255


@register
class LocalThreshold(Filter):
    title, category = "Local Threshold", CAT
    view = [
        Choice("method", "Method", ["gaussian", "mean", "sauvola", "niblack"]),
        Int("block", "Block size", (3, 501), 51, "px"),
        Float("offset", "Offset", (-255.0, 255.0), 0.0, 1),
        Choice("objects", "Objects", ["bright", "dark"]),
    ]
    out_dtype = np.uint8

    def run(self, img, para):
        block = para["block"] | 1
        m = para["method"]
        if m in ("gaussian", "mean"):
            t = filters.threshold_local(img, block, method=m, offset=para["offset"])
        elif m == "sauvola":
            t = filters.threshold_sauvola(img, block)
        else:
            t = filters.threshold_niblack(img, block)
        res = img > t if para["objects"] == "bright" else img <= t
        return res.astype(np.uint8) * 255
