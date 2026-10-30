# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Region-of-interest shapes. Coordinates are (x, y) in pixel space."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from skimage import draw


class Roi:
    kind = "roi"

    def mask(self, shape: tuple[int, int]) -> np.ndarray:
        """Boolean mask of shape (H, W)."""
        raise NotImplementedError

    def bounds(self) -> tuple[float, float, float, float]:
        """(x0, y0, x1, y1)."""
        raise NotImplementedError

    def area(self) -> float:
        return 0.0

    def perimeter(self) -> float:
        return 0.0

    def to_dict(self) -> dict:
        raise NotImplementedError

    def translated(self, dx: float, dy: float) -> "Roi":
        raise NotImplementedError

    @property
    def is_area(self) -> bool:
        return True


def _poly_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def _poly_perimeter(pts: np.ndarray, closed: bool = True) -> float:
    d = np.diff(pts, axis=0, append=pts[:1] if closed else None) if closed else np.diff(pts, axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


@dataclass
class Rect(Roi):
    x: float
    y: float
    w: float
    h: float
    kind = "rect"

    def mask(self, shape):
        m = np.zeros(shape, bool)
        x0, y0 = max(int(round(self.x)), 0), max(int(round(self.y)), 0)
        x1 = min(int(round(self.x + self.w)), shape[1])
        y1 = min(int(round(self.y + self.h)), shape[0])
        if x1 > x0 and y1 > y0:
            m[y0:y1, x0:x1] = True
        return m

    def bounds(self):
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    def area(self):
        return abs(self.w * self.h)

    def perimeter(self):
        return 2 * (abs(self.w) + abs(self.h))

    def to_dict(self):
        return {"kind": "rect", "x": self.x, "y": self.y, "w": self.w, "h": self.h}

    def translated(self, dx, dy):
        return Rect(self.x + dx, self.y + dy, self.w, self.h)


@dataclass
class Ellipse(Roi):
    x: float
    y: float
    w: float
    h: float
    kind = "ellipse"

    def mask(self, shape):
        m = np.zeros(shape, bool)
        cy, cx = self.y + self.h / 2 - 0.5, self.x + self.w / 2 - 0.5
        rr, cc = draw.ellipse(cy, cx, self.h / 2, self.w / 2, shape=shape)
        m[rr, cc] = True
        return m

    def bounds(self):
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    def area(self):
        return math.pi * abs(self.w) * abs(self.h) / 4

    def perimeter(self):
        a, b = abs(self.w) / 2, abs(self.h) / 2
        # Ramanujan approximation
        return math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))

    def to_dict(self):
        return {"kind": "ellipse", "x": self.x, "y": self.y, "w": self.w, "h": self.h}

    def translated(self, dx, dy):
        return Ellipse(self.x + dx, self.y + dy, self.w, self.h)


@dataclass
class Polygon(Roi):
    points: list[tuple[float, float]] = field(default_factory=list)
    kind = "polygon"

    def _arr(self):
        return np.asarray(self.points, float).reshape(-1, 2)

    def mask(self, shape):
        m = np.zeros(shape, bool)
        if len(self.points) >= 3:
            p = self._arr()
            rr, cc = draw.polygon(p[:, 1], p[:, 0], shape=shape)
            m[rr, cc] = True
        return m

    def bounds(self):
        p = self._arr()
        return (p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max())

    def area(self):
        return _poly_area(self._arr()) if len(self.points) >= 3 else 0.0

    def perimeter(self):
        return _poly_perimeter(self._arr()) if len(self.points) >= 2 else 0.0

    def to_dict(self):
        return {"kind": self.kind, "points": [list(p) for p in self.points]}

    def translated(self, dx, dy):
        return type(self)([(x + dx, y + dy) for x, y in self.points])


@dataclass
class Freehand(Polygon):
    kind = "freehand"


@dataclass
class Line(Roi):
    x0: float
    y0: float
    x1: float
    y1: float
    width: float = 1.0
    kind = "line"

    @property
    def is_area(self):
        return False

    def length(self):
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)

    def angle(self) -> float:
        """Degrees, counter-clockwise from +x with y pointing up (ImageJ convention)."""
        return math.degrees(math.atan2(self.y0 - self.y1, self.x1 - self.x0))   # not -(y1 - y0): that is -0.0 (and -180) for a level line

    def perimeter(self):
        return self.length()

    def mask(self, shape):
        m = np.zeros(shape, bool)
        rr, cc = draw.line(int(round(self.y0)), int(round(self.x0)), int(round(self.y1)), int(round(self.x1)))
        ok = (rr >= 0) & (rr < shape[0]) & (cc >= 0) & (cc < shape[1])
        m[rr[ok], cc[ok]] = True
        return m

    def profile(self, plane: np.ndarray) -> np.ndarray:
        """Intensity profile along the line (linear interpolation)."""
        from scipy.ndimage import map_coordinates

        n = max(int(round(self.length())) + 1, 2)
        xs = np.linspace(self.x0, self.x1, n)
        ys = np.linspace(self.y0, self.y1, n)
        return map_coordinates(plane.astype(float), [ys, xs], order=1, mode="nearest")

    def bounds(self):
        return (min(self.x0, self.x1), min(self.y0, self.y1), max(self.x0, self.x1), max(self.y0, self.y1))

    def to_dict(self):
        return {"kind": "line", "x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1, "width": self.width}

    def translated(self, dx, dy):
        return Line(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy, self.width)


@dataclass
class Point(Roi):
    x: float
    y: float
    kind = "point"

    @property
    def is_area(self):
        return False

    def mask(self, shape):
        m = np.zeros(shape, bool)
        xi, yi = int(round(self.x)), int(round(self.y))
        if 0 <= yi < shape[0] and 0 <= xi < shape[1]:
            m[yi, xi] = True
        return m

    def bounds(self):
        return (self.x, self.y, self.x, self.y)

    def to_dict(self):
        return {"kind": "point", "x": self.x, "y": self.y}

    def translated(self, dx, dy):
        return Point(self.x + dx, self.y + dy)


@dataclass
class Angle(Roi):
    """Three points: arm end, vertex, arm end. Angle measured at the vertex."""

    p1: tuple[float, float]
    vertex: tuple[float, float]
    p2: tuple[float, float]
    kind = "angle"

    @property
    def is_area(self):
        return False

    def angle(self) -> float:
        a = np.subtract(self.p1, self.vertex).astype(float)
        b = np.subtract(self.p2, self.vertex).astype(float)
        na, nb = np.linalg.norm(a), np.linalg.norm(b)
        if na == 0 or nb == 0:
            return 0.0
        c = np.clip(np.dot(a, b) / (na * nb), -1, 1)
        return math.degrees(math.acos(c))

    def mask(self, shape):
        m = np.zeros(shape, bool)
        for a, b in ((self.p1, self.vertex), (self.vertex, self.p2)):
            m |= Line(a[0], a[1], b[0], b[1]).mask(shape)
        return m

    def bounds(self):
        xs = [self.p1[0], self.vertex[0], self.p2[0]]
        ys = [self.p1[1], self.vertex[1], self.p2[1]]
        return (min(xs), min(ys), max(xs), max(ys))

    def to_dict(self):
        return {"kind": "angle", "p1": list(self.p1), "vertex": list(self.vertex), "p2": list(self.p2)}

    def translated(self, dx, dy):
        sh = lambda p: (p[0] + dx, p[1] + dy)
        return Angle(sh(self.p1), sh(self.vertex), sh(self.p2))


def roi_from_dict(d: dict) -> Roi:
    k = d["kind"]
    if k == "rect":
        return Rect(d["x"], d["y"], d["w"], d["h"])
    if k == "ellipse":
        return Ellipse(d["x"], d["y"], d["w"], d["h"])
    if k in ("polygon", "freehand"):
        cls = Polygon if k == "polygon" else Freehand
        return cls([tuple(p) for p in d["points"]])
    if k == "line":
        return Line(d["x0"], d["y0"], d["x1"], d["y1"], d.get("width", 1.0))
    if k == "point":
        return Point(d["x"], d["y"])
    if k == "angle":
        return Angle(tuple(d["p1"]), tuple(d["vertex"]), tuple(d["p2"]))
    raise ValueError(f"unknown ROI kind {k!r}")
