import math

import numpy as np
import pytest

from imagepi.core import Angle, Calibration, Ellipse, ImageDoc, Line, Point, Polygon, Rect, ResultsTable, roi_from_dict
from imagepi.core import luts


def test_imagedoc_shapes():
    assert ImageDoc(np.zeros((5, 6), np.uint8)).data.shape == (1, 5, 6)
    assert ImageDoc(np.zeros((5, 6, 3), np.uint8)).rgb
    d = ImageDoc(np.zeros((4, 5, 6), np.uint16), rgb=False)
    assert d.n_frames == 4 and d.width == 6 and d.height == 5
    with pytest.raises(ValueError):
        ImageDoc(np.zeros((2, 2, 2, 2, 2)))


def test_undo_redo(doc):
    before = doc.data.copy()
    doc.replace_data(np.zeros_like(doc.data), snapshot="zero")
    assert doc.data.max() == 0
    assert doc.undo() and np.array_equal(doc.data, before)
    assert doc.redo() and doc.data.max() == 0
    assert not doc.redo()


def test_undo_redo_restore_display_range_across_a_type_change():
    data = (np.arange(60 * 80, dtype=np.uint16).reshape(60, 80) % 3264)
    d = ImageDoc(data, "c16")
    range16 = d.display_range
    assert range16[1] > 255
    d.replace_data(np.clip(data // 13, 0, 255).astype(np.uint8)[None], snapshot="8-bit")
    assert d.display_range == (0.0, 255.0)
    assert d.undo() and d.data.dtype == np.uint16 and d.display_range == range16   # was (0, 255): a washed-out image
    assert d.redo() and d.data.dtype == np.uint8 and d.display_range == (0.0, 255.0)
    # same pixel type: the user's current window is kept
    d.undo()
    d.set_display_range(100, 2000)
    d.replace_data((data // 2)[None], snapshot="half")
    d.undo()
    assert d.display_range == (100.0, 2000.0)


def test_undo_ring_bounded(doc):
    doc.UNDO_BYTES = doc.data.nbytes * 2
    for _ in range(10):
        doc.snapshot()
    assert len(doc._undo) <= 3


def test_rect_mask_and_area():
    r = Rect(2, 3, 4, 5)
    m = r.mask((20, 20))
    assert m.sum() == 20 == r.area() and m[3, 2] and not m[2, 2]


def test_rect_mask_clipped():
    assert Rect(-5, -5, 10, 10).mask((20, 20)).sum() == 25


def test_ellipse_area_close():
    e = Ellipse(10, 10, 40, 20)
    assert abs(e.mask((100, 100)).sum() - e.area()) / e.area() < 0.03


def test_polygon_area_matches_mask():
    p = Polygon([(10, 10), (50, 10), (50, 40), (10, 40)])
    assert p.area() == 1200 and p.perimeter() == 140
    assert abs(p.mask((100, 100)).sum() - 1200) <= 80


def test_line_angle_and_profile():
    l = Line(0, 0, 3, 4)
    assert l.length() == 5
    assert Line(0, 10, 10, 0).angle() == pytest.approx(45)
    plane = np.tile(np.arange(10, dtype=float), (10, 1))
    prof = Line(0, 5, 9, 5).profile(plane)
    assert prof[0] == 0 and prof[-1] == 9


def test_angle_roi():
    assert Angle((10, 0), (0, 0), (0, 10)).angle() == pytest.approx(90)
    assert Angle((10, 0), (0, 0), (10, 10)).angle() == pytest.approx(45)


def test_roi_roundtrip():
    rois = [Rect(1, 2, 3, 4), Ellipse(1, 2, 3, 4), Polygon([(0, 0), (1, 0), (1, 1)]), Line(0, 0, 1, 1), Point(2, 3), Angle((0, 1), (0, 0), (1, 0))]
    for r in rois:
        r2 = roi_from_dict(r.to_dict())
        assert r2.to_dict() == r.to_dict() and type(r2) is type(r)
        assert r.translated(1, 1).bounds()[0] == r.bounds()[0] + 1


def test_calibration():
    c = Calibration().from_known_distance(200, 0.01, "mm")
    assert c.pixel_size == pytest.approx(5e-5) and c.calibrated
    assert c.area_to_units(100) == pytest.approx(100 * 25e-10)
    units, px = Calibration(0.5, "um").scale_bar_length(1000)
    assert units == 100 and px == 200


def test_results_table(tmp_path):
    t = ResultsTable()
    t.add_row(Area=1.0, Mean=2.0)
    t.add_row(Area=3.0, Mean=4.0)
    assert len(t) == 2 and t.summary().loc["Area", "mean"] == 2.0
    t.to_csv(tmp_path / "r.csv")
    assert len(ResultsTable.from_csv(tmp_path / "r.csv")) == 2
    assert len(t.filter("Area > 2")) == 1


def test_luts(tmp_path):
    g = luts.grays()
    assert g.shape == (256, 3) and g[255].tolist() == [255, 255, 255]
    raw = (np.arange(768) % 256).astype(np.uint8)
    (tmp_path / "a.lut").write_bytes(raw.tobytes())
    assert luts.load_lut(tmp_path / "a.lut").shape == (256, 3)
    (tmp_path / "b.lut").write_bytes(b"ICOL" + bytes(28) + raw.tobytes())
    assert luts.load_lut(tmp_path / "b.lut")[1, 0] == raw[1]
    (tmp_path / "c.txt").write_text("Index\tRed\tGreen\tBlue\n" + "\n".join(f"{i}\t{i}\t0\t0" for i in range(256)))
    assert luts.load_lut(tmp_path / "c.txt")[200].tolist() == [200, 0, 0]
    (tmp_path / "d.txt").write_text("\n".join(f"{i/255} {i/255} 0" for i in range(256)))
    assert luts.load_lut(tmp_path / "d.txt")[255].tolist() == [255, 255, 0]
    for name, f in luts.BUILTIN.items():
        assert f().shape == (256, 3), name
