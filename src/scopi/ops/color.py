"""Type conversion and display-related ops (Image > Type, Edit)."""

from __future__ import annotations

import numpy as np

from .base import Choice, Filter, Simple, register, cast_like

CAT = "Image>Type"


def _luma(a: np.ndarray) -> np.ndarray:
    return a.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


class _Convert(Filter):
    """Whole-document conversion; handled in run_doc so RGB can change shape."""

    note = frozenset({"auto_snap"})
    target = np.uint8

    def convert_stack(self, doc, para) -> np.ndarray:
        raise NotImplementedError

    def run_doc(self, doc, para, stack, mask):
        return self.convert_stack(doc, para)

    def check(self, doc):
        return None


@register
class To8Bit(_Convert):
    title, category = "8-bit", CAT
    out_dtype = np.uint8

    def convert_stack(self, doc, para):
        lo, hi = doc.display_range
        data = _luma(doc.data) if doc.rgb else doc.data
        if doc.data.dtype == np.uint8:
            return np.clip(data, 0, 255).astype(np.uint8)
        scale = 255.0 / (hi - lo) if hi > lo else 1.0
        return np.clip((data.astype(np.float64) - lo) * scale, 0, 255).round().astype(np.uint8)

    def run_doc(self, doc, para, stack, mask):
        return self.convert_stack(doc, para)


@register
class To16Bit(_Convert):
    title, category = "16-bit", CAT
    out_dtype = np.uint16

    def convert_stack(self, doc, para):
        d = doc.data
        if d.dtype == np.uint8:
            return d.astype(np.uint16) * 257
        lo, hi = doc.display_range
        return np.clip((d.astype(np.float64) - lo) / max(hi - lo, 1e-12) * 65535, 0, 65535).round().astype(np.uint16)


@register
class To32Bit(_Convert):
    title, category = "32-bit", CAT
    out_dtype = np.float32

    def convert_stack(self, doc, para):
        return doc.data.astype(np.float32)


@register
class ToGray(_Convert):
    title, category = "RGB to Gray", CAT
    out_dtype = "keep"

    def check(self, doc):
        return None if doc.rgb else "Image is not RGB"

    def convert_stack(self, doc, para):
        return cast_like(_luma(doc.data), doc.data.dtype)

    def run_doc(self, doc, para, stack, mask):
        return self.convert_stack(doc, para)


@register
class ApplyDisplayRange(Filter):
    """Bake the current brightness/contrast window into the pixel data (8/16-bit)."""

    title, category = "Apply Display Range", "Image>Adjust"
    note = frozenset({"auto_snap", "stack"})

    def run_doc(self, doc, para, stack, mask):
        lo, hi = doc.display_range
        mx = np.iinfo(doc.data.dtype).max if doc.data.dtype.kind in "ui" else 1.0
        out = (doc.data.astype(np.float64) - lo) / max(hi - lo, 1e-12) * mx
        return cast_like(np.clip(out, 0, mx), doc.data.dtype)


@register
class FlipH(Filter):
    title, category = "Flip Horizontally", "Image>Transform"
    note = frozenset({"auto_snap", "stack", "rgb_native"})

    def run(self, img, para):
        return img[:, ::-1] if img.ndim == 2 else img[:, ::-1, :]


@register
class FlipV(Filter):
    title, category = "Flip Vertically", "Image>Transform"
    note = frozenset({"auto_snap", "stack", "rgb_native"})

    def run(self, img, para):
        return img[::-1]


@register
class Rotate90(Filter):
    title, category = "Rotate 90 Degrees Right", "Image>Transform"
    note = frozenset({"auto_snap", "stack", "rgb_native"})

    def run(self, img, para):
        return np.rot90(img, -1).copy()


@register
class Crop(Filter):
    """Crop to the bounding box of the current area ROI."""

    title, category = "Crop", "Image"
    note = frozenset({"auto_snap", "stack", "rgb_native"})

    def check(self, doc):
        if doc is None or doc.roi is None or not doc.roi.is_area:
            return "Crop needs an area selection (rectangle, oval, polygon or freehand)."
        return None

    def run_doc(self, doc, para, stack, mask):
        x0, y0, x1, y1 = doc.roi.bounds()
        x0, y0 = max(int(np.floor(x0)), 0), max(int(np.floor(y0)), 0)
        x1, y1 = min(int(np.ceil(x1)), doc.width), min(int(np.ceil(y1)), doc.height)
        if x1 <= x0 or y1 <= y0:
            raise ValueError("Selection is outside the image")
        doc.roi = None
        doc.labels = None
        return doc.data[:, y0:y1, x0:x1].copy()
