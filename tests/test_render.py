import numpy as np
import pytest

from imagepi.core import luts
from imagepi.ui.render import DisplayPipeline, histogram, value_at


def render(plane, view, size, rng=(0, 255), lut=None, **kw):
    return DisplayPipeline().render(plane, rng, lut if lut is not None else luts.grays(), view, size, token=1, **kw)


def test_one_to_one_is_exact():
    a = np.random.default_rng(0).integers(0, 255, (50, 60), dtype=np.uint8)
    out = render(a, (0, 0, 60, 50), (60, 50))
    assert np.array_equal(out[..., 0], a) and out.shape == (50, 60, 3)


def test_zoom_in_replicates_pixels():
    a = np.arange(16, dtype=np.uint8).reshape(4, 4) * 16
    out = render(a, (0, 0, 4, 4), (40, 40))
    assert np.array_equal(out[::10, ::10, 0], a) and np.array_equal(out[:10, :10, 0], np.full((10, 10), a[0, 0]))


def test_pan_view_offset():
    a = np.arange(100, dtype=np.uint8).reshape(10, 10)
    out = render(a, (2, 3, 7, 8), (5, 5))
    assert np.array_equal(out[..., 0], a[3:8, 2:7])


def test_outside_image_is_background():
    a = np.full((10, 10), 200, np.uint8)
    out = render(a, (-5, -5, 15, 15), (40, 40))
    assert tuple(out[0, 0]) == (40, 40, 40) and out[20, 20, 0] == 200


def test_zoom_out_uses_pyramid_and_averages():
    a = np.zeros((400, 400), np.uint8)
    a[::2] = 255  # fine stripes -> mean ~127 when downsampled
    p = DisplayPipeline()
    out = p.render(a, (0, 255), luts.grays(), (0, 0, 400, 400), (50, 50), token=1)
    assert len(p._levels) >= 3 and abs(int(out[10:40, 10:40, 0].mean()) - 127) < 10


def test_pyramid_cached_per_token():
    a = np.zeros((256, 256), np.uint8)
    p = DisplayPipeline()
    p.render(a, (0, 255), None, (0, 0, 256, 256), (32, 32), token=1)
    levels = p._levels
    p.render(a, (0, 255), None, (0, 0, 256, 256), (32, 32), token=1)
    assert p._levels is levels
    p.render(a, (0, 255), None, (0, 0, 256, 256), (32, 32), token=2)
    assert p._levels is not levels


def test_window_level_16bit():
    a = np.array([[0, 500, 1000, 2000]], np.uint16)
    out = render(a, (0, 0, 4, 1), (4, 1), rng=(500, 1000))
    assert out[0, :, 0].tolist() == [0, 0, 255, 255]


def test_window_float_and_lut():
    a = np.array([[0.0, 0.5, 1.0]], np.float32)
    out = render(a, (0, 0, 3, 1), (3, 1), rng=(0, 1), lut=luts.channel("red"))
    assert out[0, 2].tolist() == [255, 0, 0] and out[0, 1, 0] in (127, 128)


def test_rgb_passthrough_and_window():
    a = np.zeros((2, 2, 3), np.uint8)
    a[0, 0] = (10, 20, 30)
    out = render(a, (0, 0, 2, 2), (2, 2))
    assert out[0, 0].tolist() == [10, 20, 30]
    out = render(a, (0, 0, 2, 2), (2, 2), rng=(0, 100))
    assert out[0, 0].tolist() == [25, 51, 76]


def test_int32_plane_supported():
    a = np.arange(16, dtype=np.int32).reshape(4, 4)
    render(a, (0, 0, 4, 4), (4, 4), rng=(0, 15))


def test_label_outlines():
    a = np.full((20, 20), 100, np.uint8)
    lab = np.zeros((20, 20), np.int32)
    lab[5:15, 5:15] = 1
    out = render(a, (0, 0, 20, 20), (20, 20), labels=lab)
    assert out[5, 8].tolist() == [255, 220, 0] and out[10, 10, 0] == 100 and out[0, 0, 0] == 100


def test_degenerate_inputs():
    a = np.zeros((10, 10), np.uint8)
    assert render(a, (0, 0, 10, 10), (0, 0)).shape == (0, 0, 3)
    assert render(a, (100, 100, 110, 110), (10, 10)).max() == 40


def test_histogram_and_value_at():
    a = np.zeros((100, 100), np.uint8)
    a[:50] = 255
    h = histogram(a, 0, 255)
    assert h[0] == 5000 and h[255] == 5000 and h.sum() == 10000
    big = np.zeros((4000, 4000), np.uint8)
    assert histogram(big, 0, 255).sum() <= 2_100_000
    assert value_at(a, 3.7, 3.2) == 255 and value_at(a, -1, 0) is None and value_at(a, 99.5, 99.5) == 0
