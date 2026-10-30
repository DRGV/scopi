#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Write test images for the GUI checks on the Pi (no camera needed).

    python scripts/make_test_images.py [folder]        # default ~/scopi-test

cells_18mp_rgb.tif   4912x3684 RGB 8-bit, a full-resolution camera frame (pan/zoom/filter timing, ~54 MB)
cells_gray.png       1228x922 8-bit gray, dark cells on a bright field (threshold + particles)
cells_16bit.tif      1228x922 16-bit gray with a 12-bit range, like the camera's RAW12 (16-bit display and filters)
cells_stack.tif      600x450 8-bit gray, 3 frames of random noise (std 40) around the means 80, 128 and 176: a stack for "Process entire stack"
                     (Analyze > Measure gives each frame its StdDev: about 40 raw, about 4 after a Gaussian blur of sigma 3)
known_shapes.png     800x600 8-bit, bright shapes on a dark background with exactly known sizes:
                     6 squares of 100x100 px (one touches the right and bottom edges) and 3 discs of radius 50 px.
                     Analyze Particles must find 9 objects (8 with "Exclude on edges"); squares are 10000 px.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image

from scopi.camera.simulated import synthetic_scene

SQUARES = [(50, 50), (250, 50), (450, 50), (650, 50), (50, 250), (700, 500)]   # top-left x, y; the last touches the edges
DISCS = [(300, 300), (500, 300), (700, 300)]                                    # centre x, y
SIDE, RADIUS = 100, 50


def known_shapes() -> tuple[np.ndarray, dict]:
    img = np.full((600, 800), 40, np.uint8)
    yy, xx = np.mgrid[:600, :800]
    for x, y in SQUARES:
        img[y:y + SIDE, x:x + SIDE] = 200
    disc_px = []
    for cx, cy in DISCS:
        m = (xx - cx) ** 2 + (yy - cy) ** 2 <= RADIUS ** 2
        img[m] = 200
        disc_px.append(int(m.sum()))
    return img, {"squares": len(SQUARES), "square_px": SIDE * SIDE, "discs": len(DISCS), "disc_px": disc_px}


def gray(rgb: np.ndarray) -> np.ndarray:
    return (rgb.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)).round().astype(np.uint8)


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "~/scopi-test").expanduser()
    out.mkdir(parents=True, exist_ok=True)

    small = synthetic_scene(1228, 922)
    Image.fromarray(gray(small)).save(out / "cells_gray.png")
    tifffile.imwrite(out / "cells_16bit.tif", gray(small).astype(np.uint16) * 16)   # 0..4080, a 12-bit range

    rng = np.random.default_rng(5)
    noise = np.stack([np.clip(rng.normal(mean, 40, (450, 600)), 0, 255) for mean in (80, 128, 176)]).astype(np.uint8)
    tifffile.imwrite(out / "cells_stack.tif", noise, photometric="minisblack")

    shapes, info = known_shapes()
    Image.fromarray(shapes).save(out / "known_shapes.png")

    tifffile.imwrite(out / "cells_18mp_rgb.tif", synthetic_scene(4912, 3684), photometric="rgb")

    for p in sorted(out.iterdir()):
        print(f"{p.stat().st_size / 1e6:8.1f} MB  {p}")
    print(f"known_shapes.png: {info['squares']} squares of {info['square_px']} px, {info['discs']} discs of {info['disc_px']} px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
