"""ROI/measurement tool state machines. Qt-free: the canvas feeds image-space coordinates."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from ..core.roi import Angle, Ellipse, Freehand, Line, Point, Polygon, Rect, Roi


@dataclass
class ToolContext:
    commit: Callable[[Roi | None], None]          # set the current ROI (None clears)
    get_roi: Callable[[], Roi | None]
    add_marker: Callable[[Point], None] = lambda p: None
    tolerance: Callable[[], float] = lambda: 5.0   # hit radius in image pixels (depends on zoom)


class Tool:
    name = "tool"
    cursor = "cross"
    pan = False

    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self.preview: Roi | None = None   # shape being drawn (shown by the canvas)

    def press(self, x: float, y: float, shift: bool = False) -> None: ...
    def move(self, x: float, y: float, shift: bool = False) -> None: ...
    def release(self, x: float, y: float, shift: bool = False) -> None: ...
    def double_click(self, x: float, y: float) -> None: ...
    def key(self, name: str) -> None:
        if name == "escape":
            self.cancel()

    def cancel(self) -> None:
        self.preview = None

    @property
    def busy(self) -> bool:
        return self.preview is not None


def _box(x0, y0, x1, y1, square: bool):
    w, h = x1 - x0, y1 - y0
    if square:
        m = max(abs(w), abs(h))
        w, h = math.copysign(m, w or 1), math.copysign(m, h or 1)
    return min(x0, x0 + w), min(y0, y0 + h), abs(w), abs(h)


class _BoxTool(Tool):
    shape = Rect

    def __init__(self, ctx):
        super().__init__(ctx)
        self._a: tuple[float, float] | None = None

    def press(self, x, y, shift=False):
        self._a = (x, y)
        self.preview = self.shape(x, y, 0, 0)

    def move(self, x, y, shift=False):
        if self._a:
            self.preview = self.shape(*_box(*self._a, x, y, shift))

    def release(self, x, y, shift=False):
        if not self._a:
            return
        r = self.shape(*_box(*self._a, x, y, shift))
        self._a = None
        self.preview = None
        self.ctx.commit(r if r.w >= 1 and r.h >= 1 else None)

    def cancel(self):
        self._a = None
        self.preview = None


class RectTool(_BoxTool):
    name, shape = "rect", Rect


class OvalTool(_BoxTool):
    name, shape = "oval", Ellipse


# Unit vectors of the eight 45 degree steps (image coordinates, y down), with exact zeros: cos(pi) and sin(pi) carry rounding errors
# of 1e-16, which moved the end of a "level" line off the level and made its Angle read 180 or -180 depending on which side
# of the line the pointer was (test5 D2).
_H = math.sqrt(0.5)
_SNAP = [(1.0, 0.0), (_H, _H), (0.0, 1.0), (-_H, _H), (-1.0, 0.0), (-_H, -_H), (0.0, -1.0), (_H, -_H)]


class LineTool(Tool):
    name = "line"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._a = None

    def press(self, x, y, shift=False):
        self._a = (x, y)
        self.preview = Line(x, y, x, y)

    def _end(self, x, y, shift):
        ax, ay = self._a
        if shift:  # snap to 45 degree steps
            cx, cy = _SNAP[round(math.atan2(y - ay, x - ax) / (math.pi / 4)) % 8]
            d = math.hypot(x - ax, y - ay)
            return ax + d * cx, ay + d * cy
        return x, y

    def move(self, x, y, shift=False):
        if self._a:
            ex, ey = self._end(x, y, shift)
            self.preview = Line(*self._a, ex, ey)

    def release(self, x, y, shift=False):
        if not self._a:
            return
        ex, ey = self._end(x, y, shift)
        l = Line(*self._a, ex, ey)
        self._a = None
        self.preview = None
        self.ctx.commit(l if l.length() >= 1 else None)

    def cancel(self):
        self._a = None
        self.preview = None


class FreehandTool(Tool):
    name = "freehand"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._pts: list[tuple[float, float]] = []

    def press(self, x, y, shift=False):
        self._pts = [(x, y)]
        self.preview = Freehand(list(self._pts))

    def move(self, x, y, shift=False):
        if self._pts:
            self._pts.append((x, y))
            self.preview = Freehand(list(self._pts))

    def release(self, x, y, shift=False):
        pts, self._pts, self.preview = self._pts, [], None
        self.ctx.commit(Freehand(pts) if len(pts) >= 3 and Freehand(pts).area() >= 1 else None)

    def cancel(self):
        self._pts = []
        self.preview = None


class PolygonTool(Tool):
    name = "polygon"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._pts: list[tuple[float, float]] = []

    def press(self, x, y, shift=False):
        if len(self._pts) >= 3 and math.hypot(x - self._pts[0][0], y - self._pts[0][1]) <= self.ctx.tolerance():
            self._close()
            return
        self._pts.append((x, y))
        self.preview = Polygon(list(self._pts))

    def move(self, x, y, shift=False):
        if self._pts:
            self.preview = Polygon(self._pts + [(x, y)])

    def release(self, x, y, shift=False):
        pass

    def double_click(self, x, y):
        # the double click's first press already added a vertex at this spot
        self._close()

    def key(self, name):
        if name in ("enter", "return"):
            self._close()
        elif name == "escape":
            self.cancel()
        elif name == "backspace" and self._pts:
            self._pts.pop()
            self.preview = Polygon(list(self._pts)) if self._pts else None

    def _close(self):
        pts = self._dedupe(self._pts)
        self._pts, self.preview = [], None
        self.ctx.commit(Polygon(pts) if len(pts) >= 3 and Polygon(pts).area() > 0 else None)

    @staticmethod
    def _dedupe(pts):
        out = []
        for p in pts:
            if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 0.5:
                out.append(p)
        return out

    def cancel(self):
        self._pts = []
        self.preview = None


class PointTool(Tool):
    name = "point"

    def press(self, x, y, shift=False):
        p = Point(x, y)
        self.ctx.add_marker(p)
        self.ctx.commit(p)

    @property
    def busy(self):
        return False


class AngleTool(Tool):
    name = "angle"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._pts: list[tuple[float, float]] = []

    def press(self, x, y, shift=False):
        self._pts.append((x, y))
        if len(self._pts) == 3:
            p1, v, p2 = self._pts
            self._pts, self.preview = [], None
            self.ctx.commit(Angle(p1, v, p2))
        else:
            self._show((x, y))

    def _show(self, cur):
        if len(self._pts) == 1:
            self.preview = Line(*self._pts[0], *cur)
        elif len(self._pts) == 2:
            self.preview = Angle(self._pts[0], self._pts[1], cur)

    def move(self, x, y, shift=False):
        if self._pts:
            self._show((x, y))

    def cancel(self):
        self._pts = []
        self.preview = None


class PointerTool(Tool):
    """Select and edit the current ROI: drag to move, drag handles to resize / reshape."""

    name = "pointer"
    cursor = "arrow"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._mode = None
        self._last = None
        self._handle = None

    @property
    def busy(self):
        return self._mode is not None

    def handles(self, roi: Roi) -> dict[str, tuple[float, float]]:
        if isinstance(roi, (Rect, Ellipse)):
            x0, y0, x1, y1 = roi.bounds()
            xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
            return {"nw": (x0, y0), "n": (xm, y0), "ne": (x1, y0), "e": (x1, ym),
                    "se": (x1, y1), "s": (xm, y1), "sw": (x0, y1), "w": (x0, ym)}
        if isinstance(roi, Line):
            return {"p0": (roi.x0, roi.y0), "p1": (roi.x1, roi.y1)}
        if isinstance(roi, Angle):
            return {"p0": roi.p1, "p1": roi.vertex, "p2": roi.p2}
        if isinstance(roi, Polygon) and not isinstance(roi, Freehand):
            return {f"v{i}": p for i, p in enumerate(roi.points)}
        return {}

    def _hit_handle(self, roi, x, y):
        tol = self.ctx.tolerance()
        for k, (hx, hy) in self.handles(roi).items():
            if math.hypot(x - hx, y - hy) <= tol:
                return k
        return None

    def _contains(self, roi, x, y) -> bool:
        x0, y0, x1, y1 = roi.bounds()
        tol = self.ctx.tolerance()
        if not (x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol):
            return False
        if isinstance(roi, Ellipse):
            cx, cy, a, b = (x0 + x1) / 2, (y0 + y1) / 2, max((x1 - x0) / 2, 1e-9), max((y1 - y0) / 2, 1e-9)
            return ((x - cx) / a) ** 2 + ((y - cy) / b) ** 2 <= 1.0
        if isinstance(roi, Polygon):
            from matplotlib.path import Path

            return bool(Path(roi._arr()).contains_point((x, y)))
        if isinstance(roi, Line):
            return _seg_dist(x, y, roi.x0, roi.y0, roi.x1, roi.y1) <= tol
        return True

    def press(self, x, y, shift=False):
        roi = self.ctx.get_roi()
        if roi is None:
            return
        h = self._hit_handle(roi, x, y)
        if h:
            self._mode, self._handle = "resize", h
        elif self._contains(roi, x, y):
            self._mode = "move"
        else:
            self.ctx.commit(None)
            return
        self._last = (x, y)

    def move(self, x, y, shift=False):
        roi = self.ctx.get_roi()
        if roi is None or self._mode is None:
            return
        dx, dy = x - self._last[0], y - self._last[1]
        if self._mode == "move":
            self.ctx.commit(roi.translated(dx, dy))
            self._last = (x, y)
        else:
            self.ctx.commit(self._resize(roi, self._handle, x, y))

    def release(self, x, y, shift=False):
        self._mode = self._handle = self._last = None

    def _resize(self, roi, h, x, y):
        if isinstance(roi, (Rect, Ellipse)):
            x0, y0, x1, y1 = roi.bounds()
            if "w" in h:
                x0 = x
            if "e" in h:
                x1 = x
            if h in ("n", "nw", "ne"):
                y0 = y
            if h in ("s", "sw", "se"):
                y1 = y
            return type(roi)(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        i = int(h[1:])
        if isinstance(roi, Line):
            return Line(x, y, roi.x1, roi.y1, roi.width) if i == 0 else Line(roi.x0, roi.y0, x, y, roi.width)
        if isinstance(roi, Angle):
            pts = [roi.p1, roi.vertex, roi.p2]
            pts[i] = (x, y)
            return Angle(*pts)
        pts = list(roi.points)
        pts[i] = (x, y)
        return type(roi)(pts)


def _seg_dist(px, py, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


TOOLS: dict[str, type[Tool]] = {c.name: c for c in (PointerTool, RectTool, OvalTool, PolygonTool, FreehandTool, LineTool, PointTool, AngleTool)}
