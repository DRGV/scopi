"""Stack operations (Image > Stacks)."""

from __future__ import annotations

import cv2
import numpy as np
from scipy import ndimage as ndi

from ..core.image import ImageDoc
from .base import Choice, Float, Int, Simple, cast_like, register

CAT = "Image>Stacks"

PROJECTIONS = {
    "max": lambda a: a.max(axis=0),
    "min": lambda a: a.min(axis=0),
    "mean": lambda a: a.mean(axis=0),
    "sum": lambda a: a.sum(axis=0, dtype=np.float64),
    "median": lambda a: np.median(a, axis=0),
    "std": lambda a: a.std(axis=0),
}


def z_project(data: np.ndarray, method: str = "max", start: int = 0, stop: int | None = None) -> np.ndarray:
    sub = data[start:stop]
    res = PROJECTIONS[method](sub.astype(np.float32) if method in ("mean", "median", "std") else sub)
    if method in ("sum", "std"):
        return res.astype(np.float32)
    return cast_like(res, data.dtype)


def extended_depth_of_field(data: np.ndarray, sigma: float = 2.0) -> np.ndarray:
    """Pick, per pixel, the plane with the highest local sharpness (smoothed Laplacian energy)."""
    n = data.shape[0]
    scores = []
    for i in range(n):
        f = data[i].astype(np.float32)
        g = f if f.ndim == 2 else f.mean(axis=-1)
        lap = cv2.Laplacian(g, cv2.CV_32F, ksize=3)
        scores.append(ndi.gaussian_filter(lap ** 2, sigma))
    best = np.argmax(np.stack(scores), axis=0)
    out = np.take_along_axis(data, best[None, ..., None] if data.ndim == 4 else best[None], axis=0)[0]
    return out


class _StackOp(Simple):
    def check(self, doc):
        return None if doc is not None and doc.n_frames > 1 else "This command needs a stack"


@register
class ZProject(_StackOp):
    title, category = "Z Project", CAT
    view = [Choice("method", "Projection", list(PROJECTIONS)), Int("start", "Start frame", (1, 100000), 1), Int("stop", "Stop frame (0 = last)", (0, 100000), 0)]

    def run(self, doc, para, session):
        stop = para["stop"] or doc.n_frames
        out = z_project(doc.data, para["method"], para["start"] - 1, stop)
        new = ImageDoc(out[None], f"{para['method'].upper()}_{doc.title}", rgb=doc.rgb)
        new.calibration = doc.calibration
        new.lut = doc.lut
        session.manager.add(new)
        return new


@register
class ExtendedDepthOfField(_StackOp):
    title, category = "Extended Depth of Field", CAT
    view = [Float("sigma", "Focus smoothing", (0.0, 20.0), 2.0, 1, "px")]

    def run(self, doc, para, session):
        out = extended_depth_of_field(doc.data, para["sigma"])
        new = ImageDoc(out[None], f"EDF_{doc.title}", rgb=doc.rgb)
        new.calibration = doc.calibration
        session.manager.add(new)
        return new
