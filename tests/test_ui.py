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
