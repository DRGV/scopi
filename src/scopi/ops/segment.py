# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Segmentation (Process > Segment). Ops here produce label images (uint16)."""

from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi
from skimage import measure, segmentation

from .base import Choice, Filter, Float, Int, Simple, register

CAT = "Process>Segment"


def _gray(a):
    return a if a.ndim == 2 else a.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


class _LabelFilter(Filter):
    """Filter whose output is a label image replacing the pixel data."""

    out_dtype = np.uint16
    note = frozenset({"auto_snap", "stack", "rgb_native"})

    def check(self, doc):
        return None

    def run_doc(self, doc, para, stack, mask):
        return np.stack([self._labels(doc.data[i], para) for i in range(doc.n_frames)]).astype(np.uint16)


@register
class ConnectedComponents(_LabelFilter):
    title, category = "Label Connected Components", CAT
    view = [Choice("connectivity", "Connectivity", ["8", "4"])]

    def _labels(self, img, para):
        return measure.label(_gray(img) > 0, connectivity=2 if para["connectivity"] == "8" else 1)


@register
class SlicSegment(_LabelFilter):
    title, category = "SLIC Superpixels", CAT
    view = [Int("n_segments", "Segments", (2, 10000), 100), Float("compactness", "Compactness", (0.01, 100.0), 10.0, 2)]

    def _labels(self, img, para):
        f = img.astype(np.float32)
        f = (f - f.min()) / max(f.max() - f.min(), 1e-12)
        return segmentation.slic(f, n_segments=para["n_segments"], compactness=para["compactness"],
                                 channel_axis=-1 if f.ndim == 3 else None, start_label=1)


@register
class Felzenszwalb(_LabelFilter):
    title, category = "Felzenszwalb", CAT
    view = [Float("scale", "Scale", (1.0, 5000.0), 100.0, 1), Float("sigma", "Sigma", (0.0, 10.0), 0.8, 2), Int("min_size", "Min size", (1, 10000), 50)]

    def _labels(self, img, para):
        f = img.astype(np.float32)
        f = (f - f.min()) / max(f.max() - f.min(), 1e-12)
        return segmentation.felzenszwalb(f, scale=para["scale"], sigma=para["sigma"], min_size=para["min_size"],
                                         channel_axis=-1 if f.ndim == 3 else None) + 1


@register
class RandomWalker(_LabelFilter):
    """Seeds: pixels below ``low`` become background (1), above ``high`` foreground (2). Output: 1/2 labels."""

    title, category = "Random Walker", CAT
    view = [Float("low", "Background below (fraction)", (0.0, 1.0), 0.2, 2), Float("high", "Foreground above (fraction)", (0.0, 1.0), 0.8, 2),
            Float("beta", "Beta", (1.0, 5000.0), 130.0, 0)]

    def _labels(self, img, para):
        g = _gray(img).astype(np.float32)
        g = (g - g.min()) / max(g.max() - g.min(), 1e-12)
        markers = np.zeros(g.shape, np.int32)
        markers[g < para["low"]] = 1
        markers[g > para["high"]] = 2
        if not (markers == 1).any() or not (markers == 2).any():
            raise ValueError("Random Walker needs both background and foreground seeds; adjust the seed fractions")
        return segmentation.random_walker(g, markers, beta=para["beta"], mode="cg_j")


@register
class LabelWatershed(_LabelFilter):
    """Watershed on gradient from marker maxima of a smoothed image; splits touching blobs in a binary image."""

    title, category = "Marker Watershed", CAT
    view = [Float("sigma", "Smoothing sigma", (0.0, 20.0), 2.0, 1), Int("min_distance", "Min peak distance", (1, 200), 10)]

    def _labels(self, img, para):
        from skimage.feature import peak_local_max

        g = _gray(img).astype(np.float32)
        mask = g > 0 if set(np.unique(g)) <= {0, g.max()} else g > g.mean()
        dist = ndi.distance_transform_edt(mask)
        if para["sigma"] > 0:
            dist = ndi.gaussian_filter(dist, para["sigma"])
        peaks = peak_local_max(dist, min_distance=para["min_distance"], labels=measure.label(mask), exclude_border=False)
        markers = np.zeros(mask.shape, np.int32)
        markers[tuple(peaks.T)] = np.arange(1, len(peaks) + 1)
        return segmentation.watershed(-dist, markers, mask=mask)


@register
class LabelsToOverlay(Simple):
    """Show the current label image as an overlay on a new copy... keeps the labels as overlay of the same image."""

    title, category = "Labels to Overlay", CAT

    def run(self, doc, para, session):
        if np.issubdtype(doc.dtype, np.integer) and not doc.rgb:
            doc.labels = doc.frame().astype(np.int32)
            doc.notify("labels")
        else:
            raise ValueError("Image must be a label (integer) image")
