# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
import pytest

from scopi.core.roi import Angle, Ellipse, Freehand, Line, Point, Polygon, Rect
from scopi.ui.tools import TOOLS, ToolContext


class Ctx:
    def __init__(self):
        self.roi = None
        self.markers = []
        self.ctx = ToolContext(self.commit, lambda: self.roi, self.markers.append, lambda: 3.0)

    def commit(self, r):
        self.roi = r


def mk(name):
    c = Ctx()
    return TOOLS[name](c.ctx), c


def test_rect_drag_normalises():
    t, c = mk("rect")
    t.press(50, 40)
    t.move(20, 10)
    assert t.preview.to_dict()["x"] == 20 and t.busy
    t.release(20, 10)
    assert c.roi == Rect(20, 10, 30, 30) and not t.busy


def test_line_shift_snaps_to_exact_angles_whichever_side_the_pointer_ends():
    """test5 D2: a Shift-dragged line right to left read 180 or -180 depending on whether the pointer ended above or below it,
    because cos(pi)/sin(pi) carry 1e-16 rounding errors. The snapped end point must be exactly on the 45 degree step."""
    import random

    rng = random.Random(7)
    t, c = mk("line")
    wanted = {"right": (1, 0, 0.0), "left": (-1, 0, 180.0), "up": (0, -1, 90.0), "down": (0, 1, -90.0), "up-right": (1, -1, 45.0),
              "down-left": (-1, 1, -135.0)}
    for _ in range(300):
        ax, ay = rng.uniform(50, 4800), rng.uniform(50, 3600)
        for name, (sx, sy, deg) in wanted.items():
            along = rng.uniform(40, 900)
            off = rng.uniform(-8, 8)                                     # the pointer is a little off the exact direction, either way
            ex = ax + sx * along + (off if sx == 0 else 0)
            ey = ay + sy * along + (off if sy == 0 else 0)
            t.press(ax, ay)
            t.move(ex, ey, True)
            t.release(ex, ey, True)
            if sx == 0 or sy == 0:
                assert c.roi.angle() == deg, (name, c.roi.angle())       # exact: this is where 180 / -180 and -0.0 came from
            else:
                assert c.roi.angle() == pytest.approx(deg), (name, c.roi.angle())   # diagonals involve sqrt(1/2)
    t.press(10.0, 10.0)
    t.move(60.0, 10.0, True)
    t.release(60.0, 10.0, True)
    assert str(c.roi.angle()) == "0.0"                                   # not -0.0


def test_rect_shift_square_and_click_clears():
    t, c = mk("rect")
    t.press(0, 0); t.move(30, 10, True); t.release(30, 10, True)
    assert c.roi.w == c.roi.h == 30
    t.press(5, 5); t.release(5, 5)
    assert c.roi is None


def test_oval_and_line_snap():
    t, c = mk("oval")
    t.press(0, 0); t.release(20, 10)
    assert isinstance(c.roi, Ellipse) and c.roi.w == 20
    t, c = mk("line")
    t.press(0, 0); t.move(10, 1, True); t.release(10, 1, True)
    assert c.roi.y1 == pytest.approx(0, abs=1e-9) and c.roi.length() == pytest.approx(10.05, abs=0.1)
    t.press(0, 0); t.release(0, 0)
    assert c.roi is None


def test_freehand():
    t, c = mk("freehand")
    t.press(0, 0)
    for p in [(10, 0), (10, 10), (0, 10)]:
        t.move(*p)
    t.release(0, 10)
    assert isinstance(c.roi, Freehand) and c.roi.area() == 100


def test_polygon_click_close_and_keys():
    t, c = mk("polygon")
    for p in [(0, 0), (10, 0), (10, 10)]:
        t.press(*p); t.release(*p)
    t.move(5, 5)
    assert len(t.preview.points) == 4
    t.double_click(10, 10)
    assert isinstance(c.roi, Polygon) and len(c.roi.points) == 3
    for p in [(0, 0), (10, 0), (10, 10)]:
        t.press(*p)
    t.press(1, 1)  # near first vertex closes
    assert c.roi is not None and not t.busy
    t.press(0, 0); t.key("escape")
    assert not t.busy
    t.press(0, 0); t.press(5, 0); t.press(5, 5); t.key("backspace"); t.key("enter")
    assert c.roi is None


def test_point_adds_marker():
    t, c = mk("point")
    t.press(3, 4)
    assert c.roi == Point(3, 4) and c.markers == [Point(3, 4)]


def test_angle_three_clicks():
    t, c = mk("angle")
    t.press(10, 0); t.move(0, 0); assert isinstance(t.preview, Line)
    t.press(0, 0); t.move(0, 10); assert isinstance(t.preview, Angle)
    t.press(0, 10)
    assert c.roi.angle() == pytest.approx(90)


def test_pointer_move_and_resize():
    t, c = mk("pointer")
    c.roi = Rect(10, 10, 20, 20)
    t.press(20, 20); t.move(25, 22); t.release(25, 22)
    assert c.roi == Rect(15, 12, 20, 20)
    t.press(35, 32)  # se handle
    t.move(45, 42); t.release(45, 42)
    assert c.roi == Rect(15, 12, 30, 30)
    t.press(0, 0)  # miss -> deselect
    assert c.roi is None


def test_pointer_handles_other_shapes():
    t, c = mk("pointer")
    c.roi = Line(0, 0, 10, 0)
    t.press(10, 0); t.move(10, 10); t.release(10, 10)
    assert c.roi == Line(0, 0, 10, 10)
    c.roi = Polygon([(0, 0), (10, 0), (10, 10)])
    t.press(10, 10); t.move(20, 20); t.release(20, 20)
    assert c.roi.points[2] == (20, 20)
    c.roi = Ellipse(0, 0, 20, 20)
    t.press(2, 2)   # inside bounding box corner but outside ellipse and not on handle (tol 3 -> handle nw at 0,0 is 2.8 away)
    c.roi = Ellipse(0, 0, 20, 20)
    t.release(2, 2)
    t.press(10, 10); t.move(15, 15); t.release(15, 15)
    assert c.roi.x == 5
