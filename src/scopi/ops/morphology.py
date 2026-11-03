# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Binary/grayscale morphology (Process > Morphology)."""

from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi
from skimage import morphology as morph
from skimage import segmentation
from skimage.measure import label as sk_label

from .base import Choice, Filter, Int, register

CAT = "Process>Morphology"


def _se(para):
    r = para["radius"]
    return morph.disk(r) if para["shape"] == "disk" else morph.footprint_rectangle((2 * r + 1, 2 * r + 1))


def _binary(img) -> np.ndarray:
    return img > 0


def _like(mask: np.ndarray, img: np.ndarray) -> np.ndarray:
    """Return a mask in the image's dtype using 255 (or max) for foreground."""
    if img.dtype.kind in "ui":
        return mask.astype(img.dtype) * (np.iinfo(img.dtype).max if img.dtype == np.uint8 else 1)
    return mask.astype(img.dtype)


class _Morph(Filter):
    view = [Int("radius", "Radius", (1, 50), 1, "px"), Choice("shape", "Structuring element", ["disk", "square"])]
    binary_only = False
    func = None

    def run(self, img, para):
        se = _se(para)
        if self.binary_only:
            return _like(type(self).func(_binary(img), se), img)
        return type(self).func(img, se)


@register
class Erode(_Morph):
    title, category = "Erode", CAT
    func = staticmethod(lambda a, se: ndi.grey_erosion(a, footprint=se) if a.dtype != bool else ndi.binary_erosion(a, se))


@register
class Dilate(_Morph):
    title, category = "Dilate", CAT
    func = staticmethod(lambda a, se: ndi.grey_dilation(a, footprint=se) if a.dtype != bool else ndi.binary_dilation(a, se))


@register
class Open(_Morph):
    title, category = "Open", CAT
    func = staticmethod(lambda a, se: ndi.grey_opening(a, footprint=se) if a.dtype != bool else ndi.binary_opening(a, se))


@register
class Close(_Morph):
    title, category = "Close", CAT
    func = staticmethod(lambda a, se: ndi.grey_closing(a, footprint=se) if a.dtype != bool else ndi.binary_closing(a, se))


@register
class FillHoles(Filter):
    title, category = "Fill Holes", CAT

    def run(self, img, para):
        return _like(ndi.binary_fill_holes(_binary(img)), img)


@register
class Skeletonize(Filter):
    title, category = "Skeletonize", CAT

    def run(self, img, para):
        return _like(morph.skeletonize(_binary(img)), img)


@register
class Outline(Filter):
    title, category = "Outline", CAT

    def run(self, img, para):
        m = _binary(img)
        return _like(m & ~ndi.binary_erosion(m, morph.disk(1)), img)


@register
class RemoveSmall(Filter):
    title, category = "Remove Small Objects", CAT
    view = [Int("min_size", "Minimum size", (1, 100000), 50, "px")]

    def run(self, img, para):
        return _like(morph.remove_small_objects(_binary(img), max_size=para["min_size"] - 1), img)


@register
class BinaryWatershed(Filter):
    """Split touching objects: distance transform + h-maxima markers (ImageJ 'Watershed')."""

    title, category = "Watershed", CAT
    view = [Int("min_distance", "Minimum separation", (1, 100), 5, "px")]

    def run(self, img, para):
        from skimage.feature import peak_local_max

        m = _binary(img)
        dist = ndi.distance_transform_edt(m)
        coords = peak_local_max(dist, min_distance=para["min_distance"], labels=sk_label(m), exclude_border=False)
        markers = np.zeros(m.shape, np.int32)
        markers[tuple(coords.T)] = np.arange(1, len(coords) + 1)
        labels = segmentation.watershed(-dist, markers, mask=m, watershed_line=True)
        return _like(labels > 0, img)
