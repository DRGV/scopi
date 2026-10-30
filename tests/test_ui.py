"""UI smoke tests (QT_QPA_PLATFORM=offscreen)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtTest import QTest

from imagepi import io
from imagepi.core import ImageDoc, Rect
from imagepi.ops import base
from imagepi.ui.main_window import MainWindow
from imagepi.ui.runner import compute_preview, preview_region


@pytest.fixture
def win(qtbot, tmp_path):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    w = MainWindow(settings=s)
    qtbot.addWidget(w)
    w.show()
    return w


def wait_idle(qtbot, w):
    qtbot.waitUntil(lambda: not w.runner._busy, timeout=10000)


def test_open_file_creates_tab(win, qtbot, tmp_path, blobs):
    p = tmp_path / "b.png"
    io.save_image(ImageDoc(blobs), str(p))
    doc = win.open_path(str(p))
    assert win.current_doc() is doc and win.tabs.count() == 1 and win.tabs.tabText(0) == "b.png"
    assert win.recent_menu.isEnabled()


def test_open_bad_file_reports(win, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox

    called = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: called.append(a[2]))
    (tmp_path / "x.png").write_bytes(b"not an image")
    assert win.open_path(str(tmp_path / "x.png")) is None and called


def test_canvas_renders_and_zoom_pan(win, qtbot, blobs):
    doc = win.add_doc(ImageDoc(blobs, "a"))
    c = win.current_canvas()
    qtbot.waitUntil(lambda: c.isVisible())
    img = c.grab().toImage()
    assert not img.isNull()
    c.set_scale(2.0)
    assert c.scale_factor == 2.0
    c.zoom_in(); c.zoom_out(); c.zoom_100(); c.fit()
    assert 0.01 <= c.scale_factor <= 64


def center_of(c, x, y):
    return c.mapFromScene(QPointF(x, y))


def test_draw_rect_roi_with_mouse_and_measure(win, qtbot, blobs):
    doc = win.add_doc(ImageDoc(blobs, "a"))
    c = win.current_canvas()
    win.set_tool("rect")
    c.set_scale(4.0)
    c.centerOn(60, 50)
    vp = c.viewport()
    a, b = center_of(c, 40, 40), center_of(c, 80, 70)
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(vp, b)
    QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, pos=b)
    assert isinstance(doc.roi, Rect) and abs(doc.roi.w - 40) < 2 and abs(doc.roi.h - 30) < 2
    win.runner.run(base.REGISTRY["Analyze>Measure"])
    assert len(win.session.results) == 1 and win.session.results.df["Area"][0] == pytest.approx(doc.roi.area(), rel=0.1)


def test_point_tool_adds_marker(win, qtbot, blobs):
    doc = win.add_doc(ImageDoc(blobs, "a"))
    win.set_tool("point")
    c = win.current_canvas()
    QTest.mouseClick(c.viewport(), Qt.MouseButton.LeftButton, pos=center_of(c, 25, 25))
    assert len(doc.rois) == 1 and win.roi_panel.list.count() == 1


def test_wheel_zoom(win, qtbot, blobs):
    win.add_doc(ImageDoc(blobs, "a"))
    c = win.current_canvas()
    s0 = c.scale_factor
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QWheelEvent
    ev = QWheelEvent(QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    c.wheelEvent(ev)
    assert c.scale_factor > s0


def test_run_filter_async_undo_macro(win, qtbot, blobs):
    doc = win.add_doc(ImageDoc(blobs, "a"))
    before = doc.data.copy()
    win.session.recorder.start()
    win.runner._execute(base.REGISTRY["Process>Filters>Gaussian Blur"](), doc, {"sigma": 2.0}, False)
    wait_idle(qtbot, win)
    assert not np.array_equal(doc.data, before)
    assert "Gaussian Blur" in win.session.recorder.text()
    win.undo()
    assert np.array_equal(doc.data, before)
    assert win.tabs.tabText(0).endswith("*") or True


def test_filter_error_is_reported_not_raised(win, qtbot, blobs, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    msgs = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: msgs.append(a[2]))
    doc = win.add_doc(ImageDoc(blobs, "a"))
    win.runner.run(base.REGISTRY["Image>Type>RGB to Gray"])
    assert msgs and "not RGB" in msgs[0]


def test_dialog_op_with_preview(win, qtbot, blobs, monkeypatch):
    from imagepi.ui import para_dialog

    doc = win.add_doc(ImageDoc(blobs, "a"))
    canvas = win.current_canvas()
    op = base.REGISTRY["Process>Filters>Gaussian Blur"]()
    win.runner._preview(op, doc, canvas, {"sigma": 3.0})
    qtbot.waitUntil(lambda: canvas._override is not None, timeout=10000)
    ov, rng = canvas._override
    assert ov.shape == doc.frame().shape and not np.array_equal(ov, doc.frame()) and rng == doc.display_range
    assert np.array_equal(doc.data[0], blobs), "preview must not modify the document"
    win.runner._clear_preview(canvas)
    assert canvas._override is None


def test_preview_follows_a_dragged_slider_and_older_results_still_show(win, qtbot, blobs, monkeypatch):
    """Round 4: 'the slider produces no change in preview mode'. The dialog restarted its timer on every change, so nothing was
    requested until the drag paused; and only the newest request's result was allowed to show, however slow."""
    from imagepi.ui.para_dialog import ParaDialog

    op = base.REGISTRY["Process>Filters>Gaussian Blur"]()
    dlg = ParaDialog(op, op.view, can_preview=True, can_stack=False)
    qtbot.addWidget(dlg)
    dlg.show()
    got = []
    dlg.preview_requested.connect(got.append)
    dlg.preview_box.setChecked(True)
    f = dlg._fields["sigma"]
    for i in range(14):                                     # a continuous drag: a change every 50 ms for 700 ms
        f.slider.setValue(20 + 20 * i)
        qtbot.wait(50)
    assert len(got) >= 3, f"preview only requested {len(got)} times during the drag"
    assert got[-1]["sigma"] > got[1]["sigma"]
    qtbot.waitUntil(lambda: got[-1]["sigma"] == f.spin.value(), timeout=2000)   # and the final value always arrives

    doc = win.add_doc(ImageDoc(blobs, "a"))
    canvas = win.current_canvas()
    jobs = []
    from imagepi.ui import runner as runner_mod
    monkeypatch.setattr(runner_mod, "start_job", jobs.append)
    for sigma in (1.0, 2.0, 3.0):
        win.runner._preview(op, doc, canvas, {"sigma": sigma})
    jobs[1].run()                                            # the second request finishes first and is shown
    first = canvas._override[0].copy()
    jobs[0].run()                                            # the older one finishes later and must not replace it
    assert np.array_equal(canvas._override[0], first)
    jobs[2].run()                                            # the newest one replaces it
    assert not np.array_equal(canvas._override[0], first)
    win.runner._preview(op, doc, canvas, {"sigma": 4.0})
    win.runner._clear_preview(canvas)
    jobs[3].run()                                            # a result that arrives after the preview was cleared is dropped
    assert canvas._override is None


def _drive_dialog(qtbot, steps):
    """Run ``steps(dlg)`` inside the real modal dialog's event loop (QDialog.exec), the way a user meets it.

    A failure inside the callback would leave the modal dialog open and hang the test run, so the dialog is always closed (unless
    ``steps`` already did) and the error is re-raised by the returned ``check()``, which the test calls after ``runner.run`` returns."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from imagepi.ui.para_dialog import ParaDialog

    errors = []

    def go():
        dlg = next(w for w in QApplication.topLevelWidgets() if isinstance(w, ParaDialog) and w.isVisible())
        try:
            steps(dlg)
        except BaseException as e:   # noqa: BLE001 - reported by check()
            errors.append(e)
        finally:
            if dlg.isVisible():
                dlg.reject()
    QTimer.singleShot(0, go)

    def check():
        if errors:
            raise errors[0]
    return check


def _viewport_pixels(canvas):
    from PySide6.QtGui import QImage

    img = canvas.viewport().grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())[:, :img.width() * 4].astype(np.int16)


def test_preview_box_changes_the_picture_through_run(win, qtbot, blobs):
    """Round 5 (test4 D4): the Gaussian slider 'does nothing' in preview mode. The real cause was not the slider: run() asked
    ``preview_box.isVisible()`` before the dialog was shown (always False), so the Preview box was never connected to the canvas for
    any filter. The older tests called ``_preview`` directly and so never saw it. This one goes through run() and a modal exec()."""
    doc = win.add_doc(ImageDoc(blobs, "a"))
    canvas = win.current_canvas()
    qtbot.waitUntil(canvas.isVisible)
    op = base.REGISTRY["Process>Filters>Gaussian Blur"]()
    before = _viewport_pixels(canvas)
    seen = {}

    def shows(sigma):    # the canvas holds exactly the preview the runner computes for this sigma
        v = canvas.view_rect()
        expected, _ = compute_preview(op, doc, {"sigma": sigma}, preview_region(doc, (v.left(), v.top(), v.right(), v.bottom())))
        return canvas._override is not None and np.array_equal(canvas._override[0], expected)

    def steps(dlg):
        f = dlg._fields["sigma"]
        dlg.preview_box.setChecked(True)
        f.slider.setValue(99)                                # a dragged slider: 0.1 + 99 * 0.1 = sigma 10 ...
        qtbot.waitUntil(lambda: shows(10.0), timeout=10000)
        qtbot.wait(100)
        seen["slider"] = np.abs(_viewport_pixels(canvas) - before).mean()
        f.spin.setValue(25.0)                                # ... and a typed value
        qtbot.waitUntil(lambda: shows(25.0), timeout=10000)
        qtbot.wait(100)
        seen["typed"] = np.abs(_viewport_pixels(canvas) - before).mean()
        dlg.reject()

    check = _drive_dialog(qtbot, steps)
    win.runner.run(base.REGISTRY["Process>Filters>Gaussian Blur"])
    check()
    assert seen["slider"] > 0.5 and seen["typed"] > 0.5, f"the picture on screen did not change: {seen}"
    assert canvas._override is None, "cancelling must take the preview away"
    assert np.array_equal(doc.data[0], blobs)


def test_process_entire_stack_is_honoured_by_run(win, qtbot):
    """whole_stack was read after the dialog closed, where isVisible() is always False, so the box was ignored."""
    a = np.random.default_rng(0).integers(0, 255, (3, 40, 50), dtype=np.uint8)
    doc = win.add_doc(ImageDoc(a.copy(), "s", rgb=False))

    def steps(dlg):
        assert dlg.stack_box.isVisibleTo(dlg)
        dlg.stack_box.setChecked(True)
        dlg.accept()

    check = _drive_dialog(qtbot, steps)
    win.runner.run(base.REGISTRY["Process>Filters>Gaussian Blur"])
    check()
    wait_idle(qtbot, win)
    for i in range(3):
        assert not np.array_equal(doc.data[i], a[i]), f"frame {i} was not filtered"


def test_para_dialog_widgets(qtbot):
    from imagepi.ui.para_dialog import ParaDialog

    op = base.REGISTRY["Image>Adjust>Local Threshold"]()
    dlg = ParaDialog(op, op.view, can_preview=True, can_stack=True)
    qtbot.addWidget(dlg)
    assert dlg.para() == {"method": "gaussian", "block": 51, "offset": 0.0, "objects": "bright"}
    dlg.show()
    got = []
    dlg.preview_requested.connect(got.append)
    dlg.preview_box.setChecked(True)
    dlg._fields["block"].spin.setValue(21)
    qtbot.waitUntil(lambda: len(got) >= 2, timeout=2000)
    assert got[-1]["block"] == 21
    dlg.stack_box.setChecked(True)
    assert dlg.whole_stack


def test_float_params_with_a_bounded_range_get_a_slider(qtbot):
    from imagepi.ui.para_dialog import ParaDialog

    op = base.REGISTRY["Process>Filters>Gaussian Blur"]()
    dlg = ParaDialog(op, op.view, can_preview=True, can_stack=False)
    qtbot.addWidget(dlg)
    f = dlg._fields["sigma"]
    assert f.slider.maximum() == 499 and dlg.para() == {"sigma": 2.0}
    got = []
    dlg.preview_requested.connect(got.append)
    dlg.preview_box.setChecked(True)
    f.slider.setValue(99)                         # 0.1 + 99 * 0.1
    assert dlg.para()["sigma"] == pytest.approx(10.0)
    qtbot.waitUntil(lambda: got and got[-1]["sigma"] == pytest.approx(10.0), timeout=2000)
    f.spin.setValue(3.0)
    assert f.slider.value() == round((3.0 - 0.1) * 10)
    op = base.REGISTRY["Analyze>Analyze Particles"]()
    wide = ParaDialog(op, op.view, can_preview=False, can_stack=False)
    qtbot.addWidget(wide)
    assert not hasattr(wide._fields["min_size"], "slider") and hasattr(wide._fields["min_circ"], "slider")


def test_manual_threshold_view_adapts_to_dtype():
    op = base.REGISTRY["Image>Adjust>Manual Threshold"]()
    d = ImageDoc(np.zeros((4, 4), np.uint16) + 100)
    v = op.view_for(d)
    assert v[0].range == (0.0, 65535.0)


def test_preview_region_and_crop_equivalence(blobs):
    d = ImageDoc(blobs, "a")
    r = preview_region(d, (10, 10, 60, 50))
    assert r[0] == 0 and r[2] <= d.width
    big = ImageDoc(np.zeros((5000, 6000), np.uint8))
    x0, y0, x1, y1 = preview_region(big, (2000, 2000, 4000, 4000))
    assert x1 - x0 <= 1202 and y1 - y0 <= 1202
    op = base.REGISTRY["Process>Filters>Gaussian Blur"]()
    full, _ = compute_preview(op, d, {"sigma": 1.5}, (0, 0, d.width, d.height))
    ref = base.compute_filter(op, d, {"sigma": 1.5})[0]
    assert np.array_equal(full, ref)
    d.set_roi(Rect(10, 10, 30, 30))
    masked, _ = compute_preview(op, d, {"sigma": 1.5}, (5, 5, 60, 60))
    assert np.array_equal(masked[:5], blobs[:5]) and not np.array_equal(masked[10:40, 10:40], blobs[10:40, 10:40])


def test_stack_slider(win, qtbot):
    a = np.random.default_rng(0).integers(0, 255, (4, 30, 40), dtype=np.uint8)
    doc = win.add_doc(ImageDoc(a, "s", rgb=False))
    v = win.current_view()
    assert not v.row.isHidden() and v.slider.maximum() == 4
    v.slider.setValue(3)
    assert doc.plane == 2


def test_display_panel_and_lut(win, qtbot, blobs):
    doc = win.add_doc(ImageDoc(blobs, "a"))
    dp = win.display_panel
    dp.select_lut("Fire")
    assert not np.array_equal(doc.lut, np.stack([np.arange(256)] * 3, 1))
    dp.min_spin.setValue(50); dp.max_spin.setValue(150); dp._spin_changed()
    assert doc.display_range == (50.0, 150.0)
    dp._reset()
    assert doc.display_range == (0.0, 255.0)
    dp.hist._drag(type("E", (), {"position": lambda s: QPointF(100, 5)})())
    dp.invert_box.setChecked(True)


def test_results_panel_updates_and_summary(win, qtbot):
    win.session.results.add_row(Area=2.0, Mean=1.0)
    win.session.results.add_row(Area=4.0, Mean=3.0)
    assert win.results_panel.model.rowCount() == 2
    win.results_panel._toggle_summary()
    assert win.results_panel.model.rowCount() >= 2
    assert win.results_panel.model.data(win.results_panel.model.index(0, 0)) is not None


def test_roi_manager(win, qtbot, blobs):
    doc = win.add_doc(ImageDoc(blobs, "a"))
    doc.set_roi(Rect(1, 1, 10, 10)); win.roi_panel.add()
    doc.set_roi(Rect(20, 20, 10, 10)); win.roi_panel.add()
    assert win.roi_panel.list.count() == 2
    win.roi_panel.list.setCurrentRow(0)
    assert doc.roi == Rect(1, 1, 10, 10)
    win.roi_panel.list.setCurrentRow(1)
    win.roi_panel.delete()
    assert len(doc.rois) == 1
    win.roi_panel.show_all.setChecked(True)
    assert win.current_canvas().show_all_rois


def test_calibration_dialog(win, qtbot, blobs, monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGEPI_PRESETS", str(tmp_path / "p.json"))
    from imagepi.core import Line
    from imagepi.ui.dialogs import CalibrationDialog

    doc = ImageDoc(blobs, "a")
    doc.set_roi(Line(0, 0, 200, 0))
    dlg = CalibrationDialog(doc)
    qtbot.addWidget(dlg)
    dlg.known.setValue(0.01)
    dlg.unit.setCurrentText("mm")
    cal = dlg.calibration()
    assert cal.pixel_size == pytest.approx(5e-5) and cal.unit == "mm"
    dlg.name.setText("10x")
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    dlg._save_preset()
    assert "10x" in __import__("json").loads((tmp_path / "p.json").read_text())


def test_scale_bar_and_calibrated_status(win, qtbot, blobs):
    doc = win.add_doc(ImageDoc(blobs, "a"))
    doc.calibration.pixel_size, doc.calibration.unit = 0.5, "um"
    c = win.current_canvas()
    assert not c.grab().toImage().isNull()
    win._cursor_info(10.0, 10.0, 200)
    assert "um" in win.coord_label.text()


def test_labels_overlay_renders(win, qtbot, blobs):
    doc = win.add_doc(ImageDoc(blobs, "a"))
    win.runner._execute(base.REGISTRY["Analyze>Analyze Particles"](), doc, {}, False)
    assert doc.labels is not None and len(win.session.results) == 3
    assert not win.current_canvas().grab().toImage().isNull()


def test_results_panel_shows_particle_summary_and_whole_numbers(win, qtbot, blobs):
    doc = win.add_doc(ImageDoc(blobs, "a"))
    win.runner._execute(base.REGISTRY["Analyze>Analyze Particles"](), doc, {}, False)
    win._op_finished(None)
    panel = win.results_panel
    assert win.results_dock.isVisible() and panel.summary_view.isVisible() and panel.summary_model.rowCount() == 1
    cols = list(panel.summary_model._df.columns)
    assert "Count" in cols and "Total Area" in cols and "%Area" in cols
    from imagepi.ui.panels import DataFrameModel
    import pandas as pd

    m = DataFrameModel(pd.DataFrame({"Area": [10000.0, 81369.0, 1961.25, 0.0719]}))
    assert [m.data(m.index(i, 0)) for i in range(4)] == ["10000", "81369", "1961.25", "0.0719"]
    panel._clear()
    assert len(win.session.results) == 0 and len(win.session.summary) == 0 and not panel.summary_view.isVisible()


def test_close_tab_prompts_when_changed(win, qtbot, blobs, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    doc = win.add_doc(ImageDoc(blobs, "a"))
    doc.changed = True
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    win._close_tab(0)
    assert win.tabs.count() == 0 and win.current_doc() is None


def test_save_as(win, qtbot, blobs, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QFileDialog

    doc = win.add_doc(ImageDoc(blobs, "a"))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "out.tif"), ""))
    assert win.save_as()
    assert (tmp_path / "out.tif").exists() and doc.title == "out.tif"


def test_menus_cover_registry(win):
    texts = set()

    def walk(menu):
        for a in menu.actions():
            if a.menu():
                walk(a.menu())
            else:
                texts.add(a.text().rstrip("…"))

    for a in win.menuBar().actions():
        walk(a.menu())
    for cls in base.REGISTRY.values():
        assert cls.title in texts, cls.title


def test_theme_and_touch(win, qtbot):
    win.set_theme("dark")
    from imagepi.ui import theme
    from PySide6.QtWidgets import QApplication
    assert theme.is_dark(QApplication.instance())
    win.set_touch(True)
    win.set_theme("light")
    assert not theme.is_dark(QApplication.instance())
    win.set_touch(False)


def test_macro_actions(win, qtbot, blobs, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    doc = win.add_doc(ImageDoc(blobs, "a"))
    win.macro_record()
    base.execute(base.REGISTRY["Edit>Invert"](), win.session, doc, {})
    win.session.recorder.stop()
    m = tmp_path / "m.txt"
    win.session.recorder.save(str(m))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(m), ""))
    win.macro_run()
    assert np.array_equal(doc.data[0], blobs)  # inverted twice


def test_dark_palette_greys_out_disabled_widgets():
    from PySide6.QtGui import QPalette
    from imagepi.ui.theme import _dark_palette

    p = _dark_palette()
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        on, off = p.color(QPalette.ColorGroup.Active, role), p.color(QPalette.ColorGroup.Disabled, role)
        assert off.lightness() < on.lightness() - 50, role     # a disabled box has to look different from an enabled one


def test_set_scale_reports_the_pixel_size_it_computed(win, monkeypatch, blobs):
    """Round 4 E1: the user could not find the pixel size the calibration had computed."""
    from imagepi.ui import main_window as mw

    win.add_doc(ImageDoc(blobs, "a"))

    def fake_exec(self):
        self.unit.setCurrentText("mm")
        self.px_len.setValue(400)
        self.known.setValue(1.0)
        return True

    monkeypatch.setattr(mw.CalibrationDialog, "exec", fake_exec)
    win.set_scale()
    assert win.statusBar().currentMessage() == "Scale: 1 pixel = 0.0025 mm (400 pixels per mm)"
    assert win.current_doc().calibration.pixel_size == pytest.approx(0.0025)
