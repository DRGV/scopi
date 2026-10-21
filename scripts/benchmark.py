#!/usr/bin/env python3
"""Performance / memory check for large images. Run on the Pi 5 (and the dev Mac for comparison).

    python scripts/benchmark.py [--width 4912 --height 3684] [--rgb] [--bits 16]

Prints render times for fit/zoomed/pan views through the display pipeline (the per-paint cost of
the canvas), filter times, and peak RSS. Frames at 30 fps need render < ~33 ms.
"""

from __future__ import annotations

import argparse
import resource
import sys
import time

import numpy as np

from imagepi.core import ImageDoc, luts
from imagepi.ops import base
from imagepi.ui.render import DisplayPipeline


def timed(label, fn, n=5):
    fn()
    t = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        t.append((time.perf_counter() - t0) * 1000)
    print(f"{label:<44s} median {np.median(t):8.1f} ms   min {min(t):8.1f} ms")


def rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024) if sys.platform == "darwin" else r / 1024


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=4912)
    ap.add_argument("--height", type=int, default=3684)
    ap.add_argument("--bits", type=int, default=8, choices=[8, 16])
    ap.add_argument("--rgb", action="store_true")
    ap.add_argument("--viewport", default="1280x720")
    a = ap.parse_args()
    vw, vh = (int(v) for v in a.viewport.split("x"))
    dt = np.uint8 if a.bits == 8 else np.uint16
    shape = (a.height, a.width) + ((3,) if a.rgb else ())
    rng = np.random.default_rng(0)
    data = (rng.random(shape, dtype=np.float32) * (255 if a.bits == 8 else 4095)).astype(dt)
    print(f"image {a.width}x{a.height} {'RGB' if a.rgb else 'gray'} {a.bits}-bit = {data.nbytes / 1e6:.0f} MB, viewport {vw}x{vh}")
    doc = ImageDoc(data[None], "bench", rgb=a.rgb)
    pipe = DisplayPipeline()
    lut = luts.grays()
    plane, rg = doc.frame(), doc.display_range
    W, H = a.width, a.height
    fit = min(vw / W, vh / H)
    timed("pyramid build + fit view (first paint)", lambda: DisplayPipeline().render(plane, rg, lut, (0, 0, W, H), (int(W * fit), int(H * fit)), token=1), n=3)
    timed("fit view (cached pyramid)", lambda: pipe.render(plane, rg, lut, (0, 0, W, H), (int(W * fit), int(H * fit)), token=1))
    timed("zoom 100% viewport", lambda: pipe.render(plane, rg, lut, (1000, 1000, 1000 + vw, 1000 + vh), (vw, vh), token=1))
    timed("zoom 25% viewport", lambda: pipe.render(plane, rg, lut, (0, 0, vw * 4, vh * 4), (vw, vh), token=1))
    timed("zoom 400% viewport", lambda: pipe.render(plane, rg, lut, (1000, 1000, 1000 + vw / 4, 1000 + vh / 4), (vw, vh), token=1))
    for path, para in (("Process>Filters>Gaussian Blur", {"sigma": 2.0}), ("Process>Filters>Median", {"radius": 1}),
                       ("Process>Filters>Median", {"radius": 3}), ("Image>Adjust>Auto Threshold", {"method": "Otsu"})):
        op = base.REGISTRY[path]()
        timed(f"{path} {para} (full image)", lambda op=op, para=para: base.compute_filter(op, doc, para), n=2)
    print(f"peak RSS {rss_mb():.0f} MB, undo budget {ImageDoc.UNDO_BYTES / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
