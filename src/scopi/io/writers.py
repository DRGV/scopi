# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Image writers."""

from __future__ import annotations

import json

import numpy as np

from ..core.image import ImageDoc
from .registry import writer


def _meta_json(doc: ImageDoc) -> str:
    return json.dumps(doc.metadata, default=str)


def _to_8bit_display(doc: ImageDoc, a: np.ndarray) -> np.ndarray:
    if a.dtype == np.uint8:
        return a
    lo, hi = doc.display_range
    scale = 255.0 / (hi - lo) if hi > lo else 1.0
    return np.clip((a.astype(np.float64) - lo) * scale, 0, 255).astype(np.uint8)


@writer(".tif", ".tiff")
def write_tiff(doc: ImageDoc, path: str) -> None:
    """ImageJ-compatible TIFF carrying calibration and our metadata JSON in 'Info'."""
    import tifffile

    data = doc.data
    cal = doc.calibration
    ij_ok = data.dtype in (np.uint8, np.uint16, np.float32) and (not doc.rgb or data.dtype == np.uint8)
    kwargs: dict = {}
    if cal.calibrated:
        kwargs["resolution"] = (1.0 / cal.pixel_size, 1.0 / cal.pixel_size)
    if ij_ok:
        meta = {"Info": _meta_json(doc)}
        if cal.calibrated:
            meta["unit"] = "micron" if cal.unit in ("um", "µm") else cal.unit
        if doc.n_frames > 1:
            meta["images"] = doc.n_frames
            meta["slices"] = doc.n_frames
        axes = "ZYXS" if doc.rgb else "ZYX"
        arr = data if doc.n_frames > 1 else data[0]
        if doc.n_frames == 1:
            axes = axes[1:]
        tifffile.imwrite(path, arr, imagej=True, metadata={**meta, "axes": axes}, photometric="rgb" if doc.rgb else "minisblack", **kwargs)
    else:
        arr = data if doc.n_frames > 1 else data[0]
        tifffile.imwrite(path, arr, photometric="rgb" if doc.rgb else "minisblack", description=_meta_json(doc), **kwargs)


@writer(".ome.tif")
def write_ome_tiff(doc: ImageDoc, path: str) -> None:
    import tifffile

    cal = doc.calibration
    meta: dict = {"axes": ("ZYXS" if doc.rgb else "ZYX") if doc.n_frames > 1 else ("YXS" if doc.rgb else "YX")}
    if cal.calibrated:
        meta["PhysicalSizeX"] = cal.pixel_size
        meta["PhysicalSizeY"] = cal.pixel_size
        unit = "µm" if cal.unit == "um" else cal.unit
        meta["PhysicalSizeXUnit"] = unit
        meta["PhysicalSizeYUnit"] = unit
    arr = doc.data if doc.n_frames > 1 else doc.data[0]
    tifffile.imwrite(path, arr, ome=True, photometric="rgb" if doc.rgb else "minisblack", metadata=meta)


@writer(".png")
def write_png(doc: ImageDoc, path: str) -> None:
    import cv2

    a = doc.frame()
    if a.dtype not in (np.uint8, np.uint16):
        a = _to_8bit_display(doc, a)
    if doc.rgb:
        a = a[..., ::-1]  # RGB -> BGR for OpenCV
    if not cv2.imwrite(path, np.ascontiguousarray(a)):
        raise OSError(f"failed to write {path}")


@writer(".jpg", ".jpeg", ".bmp")
def write_8bit(doc: ImageDoc, path: str) -> None:
    from PIL import Image

    a = _to_8bit_display(doc, doc.frame())
    Image.fromarray(a).save(path, quality=95) if path.lower().endswith(("jpg", "jpeg")) else Image.fromarray(a).save(path)


@writer(".npy")
def write_npy(doc: ImageDoc, path: str) -> None:
    np.save(path, doc.data if doc.n_frames > 1 else doc.data[0])
