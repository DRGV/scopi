# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Image readers. Each returns an ImageDoc with calibration/metadata where available."""

from __future__ import annotations

import json
import os

import numpy as np

from ..core.calibration import Calibration
from ..core.image import ImageDoc
from .registry import reader

_UM = {"um": 1.0, "µm": 1.0, "micron": 1.0, "microns": 1.0, "nm": 1e-3, "mm": 1e3, "cm": 1e4, "m": 1e6, "inch": 25400.0}


def _res_to_cal(xres, unit) -> Calibration:
    if not xres:
        return Calibration()
    return Calibration(1.0 / xres, unit)


@reader(".tif", ".tiff", ".ome.tif")
def read_tiff(path: str) -> ImageDoc:
    import tifffile

    with tifffile.TiffFile(path) as tf:
        series = tf.series[0]
        data = series.asarray()
        axes = series.axes
        page = tf.pages[0]
        ij = tf.imagej_metadata or {}
        ome = None
        if tf.is_ome:
            try:
                import xml.etree.ElementTree as ET

                root = ET.fromstring(tf.ome_metadata)
                ns = {"o": root.tag.split("}")[0].strip("{")}
                px = root.find(".//o:Pixels", ns)
                ome = px.attrib if px is not None else None
            except Exception:
                ome = None
        meta = {}
        cal = Calibration()
        if ome and "PhysicalSizeX" in ome:
            cal = Calibration(float(ome["PhysicalSizeX"]), ome.get("PhysicalSizeXUnit", "µm"))
        else:
            tags = page.tags
            xr = tags.get("XResolution")
            if xr is not None:
                num, den = xr.value
                xres = num / den if den else 0
                unit = ij.get("unit")
                if unit is None:
                    ru = tags.get("ResolutionUnit")
                    ruv = getattr(ru.value, "name", str(ru.value)) if ru is not None else "NONE"
                    unit = {"INCH": "inch", "CENTIMETER": "cm"}.get(ruv)
                if unit and xres and unit != "pixel":
                    cal = _res_to_cal(xres, "µm" if unit in ("micron", "um") else unit)
                    if ij.get("unit") is None and xres == 1.0:
                        cal = Calibration()
        info = ij.get("Info")
        if isinstance(info, str):
            try:
                meta = json.loads(info)
            except ValueError:
                meta = {"info": info}
        photometric = page.photometric.name
    data = _normalise_axes(data, axes)
    rgb = "S" in axes
    if photometric == "MINISWHITE":
        data = np.iinfo(data.dtype).max - data if data.dtype.kind == "u" else -data
    doc = ImageDoc(data, os.path.basename(path), rgb=rgb)
    doc.calibration = cal
    doc.metadata = meta
    return doc


def _normalise_axes(data: np.ndarray, axes: str) -> np.ndarray:
    """Reduce an arbitrary tifffile axes string to (N,H,W[,S])."""
    colour = "S" in axes
    keep = "YX" + ("S" if colour else "")
    # move spatial axes to the end
    order = [i for i, a in enumerate(axes) if a not in keep]
    for a in keep:
        order.append(axes.index(a))
    data = np.transpose(data, order)
    lead = data.shape[: data.ndim - len(keep)]
    n = int(np.prod(lead)) if lead else 1
    data = data.reshape((n,) + data.shape[len(lead):])
    return data


@reader(".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")
def read_pillow(path: str) -> ImageDoc:
    from PIL import Image

    with Image.open(path) as im:
        dpi = im.info.get("dpi")
        frames = []
        n = getattr(im, "n_frames", 1)
        for i in range(n):
            im.seek(i)
            mode = im.mode
            if mode in ("P", "PA", "LA", "RGBA", "CMYK", "YCbCr", "LAB", "HSV"):
                f = im.convert("RGB")
            elif mode == "1":
                f = im.convert("L")
            else:
                f = im
            a = np.asarray(f)
            if a.dtype == np.int32 and mode == "I":
                a = a.astype(np.uint16) if a.min() >= 0 and a.max() < 65536 else a
            frames.append(a)
    data = np.stack(frames)
    doc = ImageDoc(data, os.path.basename(path), rgb=data.ndim == 4)
    del dpi  # DPI is a print resolution, not a physical calibration
    return doc


@reader(".npy")
def read_npy(path: str) -> ImageDoc:
    a = np.load(path)
    return ImageDoc(a, os.path.basename(path), rgb=a.ndim == 4 or (a.ndim == 3 and a.shape[-1] in (3, 4)))


@reader(".dcm", ".dicom")
def read_dicom(path: str) -> ImageDoc:
    try:
        import pydicom
    except ImportError as e:
        raise RuntimeError("DICOM support needs the optional 'pydicom' package") from e
    ds = pydicom.dcmread(path)
    a = ds.pixel_array
    rgb = a.ndim == 4 or (a.ndim == 3 and a.shape[-1] == 3 and getattr(ds, "SamplesPerPixel", 1) == 3)
    doc = ImageDoc(a, os.path.basename(path), rgb=rgb)
    ps = getattr(ds, "PixelSpacing", None)
    if ps:
        doc.calibration = Calibration(float(ps[1]), "mm")
    return doc


@reader("*")
def read_imageio(path: str) -> ImageDoc:
    import imageio.v3 as iio

    a = iio.imread(path)
    return ImageDoc(a, os.path.basename(path), rgb=a.ndim == 4 or (a.ndim == 3 and a.shape[-1] in (3, 4)))
