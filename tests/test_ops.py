import numpy as np
import pytest
from scipy import ndimage as ndi
from skimage import filters as skf
from skimage import measure

from imagepi import ops
from imagepi.core import ImageDoc, Rect, commands
from imagepi.ops import base


def run(session, doc, path, **para):
    return base.execute(base.REGISTRY[path](), session, doc, para)


def test_registry_and_menu_tree():
    tree = base.menu_tree()
    assert "Process" in tree and "Filters" in tree["Process"]
    assert all(c.title for c in base.REGISTRY.values())


@pytest.mark.parametrize("shape,sigma", [((60, 80), 0.3), ((60, 80), 2.0), ((200, 150), 7.5), ((9, 12), 20.0), ((1, 30), 1.0)])
def test_gaussian_helper_equals_scipy(shape, sigma):
    from imagepi.ops.filters import gaussian

    a = np.random.default_rng(3).random(shape).astype(np.float32) * 1000
    ref = ndi.gaussian_filter(a, sigma, mode="nearest")
    assert np.allclose(gaussian(a, sigma), ref, atol=1e-2)
    b = a.astype(np.float64)
    assert np.allclose(gaussian(b, sigma), ndi.gaussian_filter(b, sigma, mode="nearest"), atol=1e-6)


def test_gaussian_matches_scipy(session, doc):
    ref = ndi.gaussian_filter(doc.data[0].astype(np.float32), 2.0, mode="nearest").round().astype(np.uint8)
    run(session, doc, "Process>Filters>Gaussian Blur", sigma=2.0)
    assert np.array_equal(doc.data[0], ref) and doc.dtype == np.uint8


def test_median_matches_scipy(session):
    rng = np.random.default_rng(0)
    a = rng.integers(0, 255, (40, 40), dtype=np.uint8)
    d = ImageDoc(a)
    run(session, d, "Process>Filters>Median", radius=2)
    assert np.array_equal(d.data[0], ndi.median_filter(a, size=5, mode="nearest"))


def test_median_3x3_cv_path_matches(session):
    a = np.random.default_rng(3).integers(0, 255, (40, 40), dtype=np.uint16)
    d = ImageDoc(a)
    run(session, d, "Process>Filters>Median", radius=1)
    assert np.array_equal(d.data[0][1:-1, 1:-1], ndi.median_filter(a, size=3, mode="nearest")[1:-1, 1:-1])


def test_min_max_filters(session, doc):
    ref = ndi.maximum_filter(doc.data[0], size=5, mode="nearest")
    run(session, doc, "Process>Filters>Maximum", radius=2)
    assert np.array_equal(doc.data[0], ref)


def test_undo_after_op(session, doc):
    before = doc.data.copy()
    run(session, doc, "Process>Filters>Gaussian Blur", sigma=3.0)
    assert not np.array_equal(doc.data, before)
    doc.undo()
    assert np.array_equal(doc.data, before)


def test_roi_restricts_filter(session, doc):
    before = doc.data.copy()
    doc.set_roi(Rect(0, 0, 40, 50))
    run(session, doc, "Process>Filters>Gaussian Blur", sigma=3.0)
    assert not np.array_equal(doc.data[0, :50, :40], before[0, :50, :40])
    assert np.array_equal(doc.data[0, 50:], before[0, 50:]) and np.array_equal(doc.data[0, :, 40:], before[0, :, 40:])


def test_rgb_filter_per_channel(session):
    a = np.random.default_rng(1).integers(0, 255, (30, 30, 3), dtype=np.uint8)
    d = ImageDoc(a)
    run(session, d, "Process>Filters>Gaussian Blur", sigma=1.5)
    ref = np.stack([ndi.gaussian_filter(a[..., c].astype(np.float32), 1.5, mode="nearest").round() for c in range(3)], -1)
    assert d.rgb and np.array_equal(d.data[0], ref.astype(np.uint8))


def test_filter_whole_stack_vs_plane(session):
    a = np.random.default_rng(2).integers(0, 255, (3, 20, 20), dtype=np.uint8)
    d = ImageDoc(a, rgb=False)
    d.set_plane(1)
    run(session, d, "Process>Filters>Mean", radius=1)
    assert np.array_equal(d.data[0], a[0]) and not np.array_equal(d.data[1], a[1])
    run(session, d, "Process>Filters>Mean", radius=1)  # plane only again
    assert np.array_equal(d.data[2], a[2])
    base.execute(base.REGISTRY["Process>Filters>Mean"](), session, d, {"radius": 1}, stack=True)
    assert not np.array_equal(d.data[2], a[2])


def test_sobel_and_laplace_on_step_edge(session):
    a = np.zeros((20, 20), np.uint8)
    a[:, 10:] = 100
    d = ImageDoc(a.copy())
    run(session, d, "Process>Filters>Find Edges (Sobel)")
    assert d.data[0, :, 9:11].min() > 0 and d.data[0, :, :5].max() == 0
    d = ImageDoc(a.copy())
    run(session, d, "Process>Filters>Laplace")


def test_unsharp_dog_smooth_run(session, doc):
    for p in ("Process>Filters>Unsharp Mask", "Process>Filters>Difference of Gaussians", "Process>Filters>Smooth (3x3)", "Process>Filters>Mean", "Process>Filters>Minimum"):
        run(session, doc, p)
    assert doc.dtype == np.uint8


def test_subtract_background_removes_gradient(session):
    yy, xx = np.mgrid[:200, :200]
    bg = (xx * 0.5).astype(np.float32)
    img = bg.copy()
    img[90:110, 90:110] += 80
    d = ImageDoc(img.astype(np.uint8))
    run(session, d, "Process>Filters>Subtract Background", radius=30)
    out = d.data[0].astype(float)
    assert out[:20, :].mean() < 10 and out[:20, :].mean() < img[:20].mean() / 4 and out[95:105, 95:105].mean() > 50
    d2 = ImageDoc((255 - img).astype(np.uint8))
    run(session, d2, "Process>Filters>Subtract Background", radius=30, background="light")


def test_invert(session, doc):
    run(session, doc, "Edit>Invert")
    assert doc.data[0, 0, 0] == 255


@pytest.mark.parametrize("method,fn", [("Otsu", skf.threshold_otsu), ("Li", skf.threshold_li), ("Triangle", skf.threshold_triangle)])
def test_auto_threshold_matches_skimage(session, method, fn):
    rng = np.random.default_rng(5)
    a = np.clip(np.concatenate([rng.normal(60, 15, 5000), rng.normal(180, 15, 5000)]), 0, 255).astype(np.uint8).reshape(100, 100)
    d = ImageDoc(a)
    run(session, d, "Image>Adjust>Auto Threshold", method=method)
    assert d.dtype == np.uint8 and set(np.unique(d.data)) <= {0, 255}
    assert np.array_equal(d.data[0] > 0, a > fn(a))


def test_threshold_dark_objects_and_manual(session, doc):
    run(session, doc, "Image>Adjust>Auto Threshold", method="Otsu", objects="dark")
    assert doc.data[0, 0, 0] == 255 and doc.data[0, 25, 25] == 0
    d = ImageDoc(np.arange(100, dtype=np.uint8).reshape(10, 10))
    run(session, d, "Image>Adjust>Manual Threshold", lower=10, upper=19)
    assert (d.data > 0).sum() == 10


def test_threshold_whole_stack_dtype_consistent(session):
    d = ImageDoc((np.random.default_rng(0).random((3, 20, 20)) * 60000).astype(np.uint16), rgb=False)
    d.set_plane(1)
    run(session, d, "Image>Adjust>Auto Threshold", method="Otsu")
    assert d.dtype == np.uint8 and d.n_frames == 3


def test_local_threshold(session, doc):
    run(session, doc, "Image>Adjust>Local Threshold", method="mean", block=31)
    assert d_binary(doc)


def d_binary(doc):
    return set(np.unique(doc.data)) <= {0, 255}


def test_threshold_rgb_input(session):
    a = np.zeros((20, 20, 3), np.uint8)
    a[:, 10:] = 200
    d = ImageDoc(a)
    run(session, d, "Image>Adjust>Auto Threshold", method="Otsu")
    assert not d.rgb and d.data[0, 0, 15] == 255 and d.data[0, 0, 2] == 0


def binary_disc():
    a = np.zeros((50, 50), np.uint8)
    yy, xx = np.mgrid[:50, :50]
    a[(yy - 25) ** 2 + (xx - 25) ** 2 <= 100] = 255
    return a


def test_erode_dilate_open_close(session):
    base_img = binary_disc()
    d = ImageDoc(base_img.copy())
    run(session, d, "Process>Morphology>Erode", radius=2)
    assert 0 < (d.data > 0).sum() < (base_img > 0).sum()
    d = ImageDoc(base_img.copy())
    run(session, d, "Process>Morphology>Dilate", radius=2)
    assert (d.data > 0).sum() > (base_img > 0).sum()
    holey = base_img.copy()
    holey[25, 25] = 0
    d = ImageDoc(holey)
    run(session, d, "Process>Morphology>Close", radius=2)
    assert d.data[0, 25, 25] == 255
    d = ImageDoc(base_img.copy())
    d.data[0, 5, 5] = 255
    run(session, d, "Process>Morphology>Open", radius=2)
    assert d.data[0, 5, 5] == 0


def test_fill_holes_skeleton_outline_remove_small(session):
    holey = binary_disc()
    holey[22:28, 22:28] = 0
    d = ImageDoc(holey)
    run(session, d, "Process>Morphology>Fill Holes")
    assert d.data[0, 25, 25] == 255
    d = ImageDoc(binary_disc())
    run(session, d, "Process>Morphology>Skeletonize")
    assert 0 < (d.data > 0).sum() < 50
    d = ImageDoc(binary_disc())
    run(session, d, "Process>Morphology>Outline")
    assert d.data[0, 25, 25] == 0 and (d.data > 0).sum() > 20
    img = binary_disc()
    img[2, 2] = 255
    d = ImageDoc(img)
    run(session, d, "Process>Morphology>Remove Small Objects", min_size=5)
    assert d.data[0, 2, 2] == 0 and d.data[0, 25, 25] == 255


def test_watershed_splits_touching(session):
    a = np.zeros((60, 100), np.uint8)
    yy, xx = np.mgrid[:60, :100]
    a[(yy - 30) ** 2 + (xx - 35) ** 2 <= 200] = 255
    a[(yy - 30) ** 2 + (xx - 58) ** 2 <= 200] = 255
    assert measure.label(a > 0).max() == 1
    d = ImageDoc(a)
    run(session, d, "Process>Morphology>Watershed", min_distance=8)
    assert measure.label(d.data[0] > 0, connectivity=1).max() == 2


def test_type_conversion(session):
    d = ImageDoc(np.linspace(0, 1000, 400).reshape(20, 20).astype(np.uint16))
    d.set_display_range(0, 1000)
    run(session, d, "Image>Type>8-bit")
    assert d.dtype == np.uint8 and d.data.max() == 255
    run(session, d, "Image>Type>16-bit")
    assert d.dtype == np.uint16 and d.data.max() == 255 * 257
    run(session, d, "Image>Type>32-bit")
    assert d.dtype == np.float32
    rgb = ImageDoc(np.full((10, 10, 3), 100, np.uint8))
    run(session, rgb, "Image>Type>RGB to Gray")
    assert not rgb.rgb and rgb.data[0, 0, 0] == 100
    rgb = ImageDoc(np.full((10, 10, 3), 100, np.uint8))
    run(session, rgb, "Image>Type>8-bit")
    assert not rgb.rgb
    with pytest.raises(ValueError):
        run(session, ImageDoc(np.zeros((5, 5), np.uint8)), "Image>Type>RGB to Gray")


def test_transforms(session):
    a = np.arange(12, dtype=np.uint8).reshape(3, 4)
    d = ImageDoc(a.copy()); run(session, d, "Image>Transform>Flip Horizontally"); assert np.array_equal(d.data[0], a[:, ::-1])
    d = ImageDoc(a.copy()); run(session, d, "Image>Transform>Flip Vertically"); assert np.array_equal(d.data[0], a[::-1])
    d = ImageDoc(a.copy()); run(session, d, "Image>Transform>Rotate 90 Degrees Right"); assert d.data.shape == (1, 4, 3)
    rgb = ImageDoc(np.zeros((3, 4, 3), np.uint8)); run(session, rgb, "Image>Transform>Flip Horizontally"); assert rgb.data.shape == (1, 3, 4, 3)


def test_apply_display_range(session):
    d = ImageDoc(np.linspace(0, 200, 100).reshape(10, 10).astype(np.uint8))
    d.set_display_range(50, 150)
    run(session, d, "Image>Adjust>Apply Display Range")
    assert d.data.min() == 0 and d.data.max() == 255


# ---------------------------------------------------------------- analysis
def test_measure_whole_image_and_roi(session, doc):
    row = run(session, doc, "Analyze>Measure")
    assert row["Area"] == 100 * 120 and row["Max"] == 200
    doc.set_roi(Rect(15, 15, 20, 20))
    row = run(session, doc, "Analyze>Measure")
    assert row["Area"] == 400 and row["Mean"] > 100
    assert len(session.results) == 2


def test_measure_uses_calibration(session, doc):
    doc.calibration.pixel_size, doc.calibration.unit = 0.5, "um"
    doc.set_roi(Rect(0, 0, 10, 10))
    row = run(session, doc, "Analyze>Measure")
    assert row["Area"] == pytest.approx(25.0) and row["Unit"] == "um"
    from imagepi.core import Line
    doc.set_roi(Line(0, 0, 30, 40))
    assert run(session, doc, "Analyze>Measure")["Length"] == pytest.approx(25.0)


def test_measure_on_calibration_slide(session):
    """Verification case from the plan: 0.01 mm graduation measured via calibration."""
    from imagepi.core import Calibration, Line
    d = ImageDoc(np.zeros((50, 600), np.uint8))
    d.calibration = Calibration().from_known_distance(200, 0.01, "mm")
    d.set_roi(Line(0, 10, 400, 10))
    assert run(session, d, "Analyze>Measure")["Length"] == pytest.approx(0.02)


def test_measure_point_angle_and_all(session, doc):
    from imagepi.core import Angle, Point
    doc.rois = [Point(25, 25), Angle((10, 0), (0, 0), (0, 10))]
    rows = run(session, doc, "Analyze>Measure All ROIs")
    assert rows[0]["Value"] == 200 and rows[1]["Angle"] == pytest.approx(90)
    assert run(session, doc, "Analyze>Count Markers") == 1


def test_empty_roi_raises(session, doc):
    doc.set_roi(Rect(500, 500, 10, 10))
    with pytest.raises(ValueError):
        run(session, doc, "Analyze>Measure")


def test_area_fraction(session, doc):
    row = run(session, doc, "Analyze>Area Fraction")
    assert row["%Area"] == pytest.approx(100 * (doc.data > 0).mean())


def test_analyze_particles_counts_and_geometry(session, doc):
    df = run(session, doc, "Analyze>Analyze Particles")
    assert len(df) == 3
    areas = sorted(df["Area"])
    assert areas[0] == pytest.approx(np.pi * 36, rel=0.1) and areas[2] == pytest.approx(np.pi * 225, rel=0.05)
    assert df["Circ."].min() > 0.7 and (df["Solidity"] > 0.9).all()
    s = doc.metadata["particle_summary"]
    assert s["Count"] == 3 and s["Density"] == pytest.approx(3 / (100 * 120))
    assert doc.labels.max() >= 3
    assert len(session.results) == 3
    assert len(session.summary) == 1 and session.summary.df.loc[0, "Count"] == 3     # shown under the results
    run(session, doc, "Analyze>Clear Results")
    assert len(session.results) == 0 and len(session.summary) == 0


def test_analyze_particles_filters(session, doc):
    df = run(session, doc, "Analyze>Analyze Particles", min_size=200.0)
    assert len(df) == 2
    session.results.clear()
    d = ImageDoc(binary_disc())
    d.data[0, 0:5, 0:5] = 255
    assert len(run(session, d, "Analyze>Analyze Particles", exclude_edges=True)) == 1
    d.calibration.pixel_size, d.calibration.unit = 2.0, "um"
    df = run(session, d, "Analyze>Analyze Particles", min_size=150.0, max_size=2000.0)
    assert len(df) == 1 and df["Area"][0] == pytest.approx(317 * 4, rel=0.02)


def test_particles_circularity_filter(session):
    a = np.zeros((60, 100), np.uint8)
    a[10:50, 10:30] = 255  # rectangle
    yy, xx = np.mgrid[:60, :100]
    a[(yy - 30) ** 2 + (xx - 70) ** 2 <= 100] = 255
    d = ImageDoc(a)
    df = run(session, d, "Analyze>Analyze Particles", min_circ=0.9)
    assert len(df) == 1 and df["X"][0] > 60


def test_measure_labels_requires_labels(session, doc):
    with pytest.raises(ValueError):
        run(session, doc, "Analyze>Measure Labels")
    run(session, doc, "Analyze>Analyze Particles")
    session.results.clear()
    run(session, doc, "Analyze>Measure Labels")
    assert len(session.results) == 3


# ---------------------------------------------------------------- segmentation
def test_label_components(session, doc):
    run(session, doc, "Process>Segment>Label Connected Components")
    assert doc.dtype == np.uint16 and doc.data.max() == 3


def test_marker_watershed_label(session):
    a = np.zeros((60, 100), np.uint8)
    yy, xx = np.mgrid[:60, :100]
    a[(yy - 30) ** 2 + (xx - 35) ** 2 <= 200] = 255
    a[(yy - 30) ** 2 + (xx - 58) ** 2 <= 200] = 255
    d = ImageDoc(a)
    run(session, d, "Process>Segment>Marker Watershed", min_distance=8, sigma=1.0)
    assert d.data.max() == 2


def test_slic_felzenszwalb_random_walker(session):
    rng = np.random.default_rng(0)
    a = np.zeros((40, 40), np.uint8)
    a[:, 20:] = 200
    a = np.clip(a + rng.normal(0, 8, a.shape), 0, 255).astype(np.uint8)
    d = ImageDoc(a.copy()); run(session, d, "Process>Segment>SLIC Superpixels", n_segments=10); assert d.data.max() >= 2
    d = ImageDoc(a.copy()); run(session, d, "Process>Segment>Felzenszwalb", scale=300.0); assert d.data.max() >= 2
    d = ImageDoc(a.copy()); run(session, d, "Process>Segment>Random Walker")
    assert (d.data[0][:, :10] == 1).all() and (d.data[0][:, 30:] == 2).all()
    rgb = ImageDoc(np.dstack([a, a, a]))
    run(session, rgb, "Process>Segment>SLIC Superpixels", n_segments=10)
    assert not rgb.rgb


def test_random_walker_needs_seeds(session):
    with pytest.raises(ValueError):
        run(session, ImageDoc(np.full((20, 20), 5, np.uint8)), "Process>Segment>Random Walker")


def test_labels_to_overlay(session, doc):
    run(session, doc, "Process>Segment>Label Connected Components")
    run(session, doc, "Process>Segment>Labels to Overlay")
    assert doc.labels.max() == 3


# ---------------------------------------------------------------- stacks
def test_z_project_methods(session):
    a = np.random.default_rng(0).integers(0, 200, (5, 10, 10), dtype=np.uint8)
    for m, ref in [("max", a.max(0)), ("min", a.min(0)), ("mean", a.mean(0).round())]:
        d = ImageDoc(a, rgb=False)
        session.manager.add(d)
        new = run(session, d, "Image>Stacks>Z Project", method=m)
        assert np.array_equal(new.data[0], ref.astype(np.uint8)), m
    new = run(session, d, "Image>Stacks>Z Project", method="sum")
    assert new.dtype == np.float32 and new.data[0, 0, 0] == a[:, 0, 0].sum()
    new = run(session, d, "Image>Stacks>Z Project", method="max", start=2, stop=3)
    assert np.array_equal(new.data[0], a[1:3].max(0))
    with pytest.raises(ValueError):
        run(session, ImageDoc(a[0]), "Image>Stacks>Z Project")


def test_extended_depth_of_field(session):
    rng = np.random.default_rng(0)
    tex = rng.integers(0, 255, (60, 60)).astype(np.float32)
    from scipy.ndimage import gaussian_filter
    blurred = gaussian_filter(tex, 4)
    p0, p1 = blurred.copy(), blurred.copy()
    p0[:, :30] = tex[:, :30]    # left sharp in plane 0
    p1[:, 30:] = tex[:, 30:]    # right sharp in plane 1
    d = ImageDoc(np.stack([p0, p1]).astype(np.uint8), rgb=False)
    session.manager.add(d)
    out = run(session, d, "Image>Stacks>Extended Depth of Field", sigma=2.0).data[0]
    err_edf = np.abs(out.astype(float) - tex.astype(np.uint8)).mean()
    err_single = np.abs(d.data[0].astype(float) - tex.astype(np.uint8)).mean()
    assert err_edf < err_single * 0.7


# ---------------------------------------------------------------- macros
def test_macro_record_and_replay(session, doc, blobs):
    session.manager.add(doc)
    session.recorder.start()
    run(session, doc, "Process>Filters>Gaussian Blur", sigma=1.5)
    run(session, doc, "Image>Adjust>Auto Threshold", method="Otsu")
    session.recorder.stop()
    text = session.recorder.text()
    assert text.splitlines()[0].startswith('Process>Filters>Gaussian Blur{"sigma": 1.5}')
    expected = doc.data.copy()
    other = ImageDoc(blobs.copy())
    session.manager.add(other)
    assert commands.replay(session, text, other) == 2
    assert np.array_equal(other.data, expected)


def test_macro_unknown_command(session, doc):
    with pytest.raises(KeyError):
        commands.replay(session, "Nope>Nothing", doc)


def test_batch(session, tmp_path, blobs):
    from imagepi import io
    paths = []
    for i in range(2):
        p = tmp_path / f"in{i}.png"
        io.save_image(ImageDoc(blobs), str(p))
        paths.append(str(p))
    out = commands.run_batch(session, 'Image>Adjust>Auto Threshold{"method": "Otsu"}', paths, str(tmp_path))
    assert len(out) == 2 and set(np.unique(io.open_image(out[0]).data)) == {0, 255}
    assert session.manager.images == []


def test_plugin_loader(tmp_path):
    from imagepi.plugins import load_all
    (tmp_path / "hello_plg.py").write_text(
        "from imagepi.ops.base import Filter, register\n"
        "@register\nclass Halve(Filter):\n    title='Halve'; category='Plugins'\n    def run(self, img, para): return img // 2\n")
    (tmp_path / "broken_plg.py").write_text("raise RuntimeError('boom')\n")
    res = load_all([tmp_path])
    assert len(res["loaded"]) >= 1 and any("broken" in f for f in res["failed"])
    assert "Plugins>Halve" in base.REGISTRY
    del base.REGISTRY["Plugins>Halve"]


def test_strip_parallel_matches_single_thread():
    from imagepi.ops.filters import _strip_parallel
    a = np.random.default_rng(0).integers(0, 4095, (200, 150), dtype=np.uint16)
    f = lambda x: ndi.median_filter(x, size=9, mode="nearest")
    assert np.array_equal(_strip_parallel(f, a, 4, strips=4), f(a))
    assert np.array_equal(_strip_parallel(f, a, 4, strips=1), f(a))


def test_median_large_kernel_all_dtypes(session):
    a = np.random.default_rng(0).integers(0, 4095, (120, 100), dtype=np.uint16)
    for arr in (a, a.astype(np.float32), (a // 16).astype(np.uint8)):
        d = ImageDoc(arr)
        run(session, d, "Process>Filters>Median", radius=6)
        assert np.array_equal(d.data[0], ndi.median_filter(arr, size=13, mode="nearest"))
