"""Main window: tabbed images, menus generated from the op registry, docks and toolbar."""

from __future__ import annotations

import os

import numpy as np
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QActionGroup, QCursor, QKeySequence
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
                               QMenu, QMessageBox, QPlainTextEdit, QSlider, QTabWidget, QToolBar, QVBoxLayout, QWidget)

from .. import io
from ..core import luts
from ..core.image import ImageDoc
from ..core.roi import Rect
from ..core.session import Session
from ..ops import base
from . import theme
from .camera_panel import CameraController, CameraPanel, LiveView
from .canvas import ImageCanvas
from .dialogs import CalibrationDialog
from .panels import DisplayPanel, ResultsPanel, RoiManagerPanel
from .render import DisplayPipeline
from .runner import OpRunner

TOOL_LABELS = [("pointer", "Pointer"), ("rect", "Rectangle"), ("oval", "Oval"), ("polygon", "Polygon"),
               ("freehand", "Freehand"), ("line", "Line"), ("point", "Point"), ("angle", "Angle"), ("hand", "Hand (pan)")]
MENU_ORDER = ["Edit", "Image", "Process", "Analyze", "Plugins"]


class ImageView(QWidget):
    """Canvas plus a frame slider for stacks."""

    def __init__(self, doc: ImageDoc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.canvas = ImageCanvas(doc, DisplayPipeline())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.canvas, 1)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(6, 2, 6, 2)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setPageStep(1)   # the default of 10 is more than a short stack: a click in the groove or one wheel notch jumped to the last frame
        self.slider.setTickInterval(1)
        self.frame_label = QLabel()
        h.addWidget(self.slider, 1)
        h.addWidget(self.frame_label)
        lay.addWidget(row)
        self.row = row
        self.slider.valueChanged.connect(lambda v: doc.set_plane(v - 1))
        doc.listeners.append(self._on_doc)
        self._sync()

    def _on_doc(self, doc, what):
        if what in ("data", "plane"):
            self._sync()

    def _sync(self) -> None:
        n = self.doc.n_frames
        self.row.setVisible(n > 1)
        self.slider.blockSignals(True)
        self.slider.setRange(1, max(n, 1))
        # with only a few frames the handle can sit in a few places only and jumps between them: show where they are
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow if 1 < n <= 60 else QSlider.TickPosition.NoTicks)
        self.slider.setValue(self.doc.plane + 1)
        self.slider.blockSignals(False)
        self.frame_label.setText(f"{self.doc.plane + 1}/{n}")

    def close_view(self) -> None:
        self.canvas.close_view()
        if self._on_doc in self.doc.listeners:
            self.doc.listeners.remove(self._on_doc)


class MainWindow(QMainWindow):
    def __init__(self, session: Session | None = None, settings: QSettings | None = None):
        super().__init__()
        self.session = session or Session()
        self.settings = settings or QSettings("ImagePi", "ImagePi")
        self.last_para: dict[str, dict] = {}
        self.setWindowTitle("ImagePi")
        self.resize(1280, 800)
        self.setAcceptDrops(True)
        self.runner = OpRunner(self)
        self.camera = CameraController(self)
        self._tool_name = "pointer"
        self._views: list[ImageView] = []
        self._live_tab: LiveView | None = None

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.setCentralWidget(self.tabs)

        self.display_panel = DisplayPanel()
        self.results_panel = ResultsPanel(self.session.results, self.session.summary)
        self.roi_panel = RoiManagerPanel()
        self.camera_panel = CameraPanel(self.camera, settings=self.settings)
        self.display_dock = self._dock("Brightness / Contrast", self.display_panel, Qt.DockWidgetArea.RightDockWidgetArea)
        self.roi_dock = self._dock("ROI Manager", self.roi_panel, Qt.DockWidgetArea.RightDockWidgetArea)
        self.results_dock = self._dock("Results", self.results_panel, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.camera_dock = self._dock("Camera", self.camera_panel, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.camera_dock.hide()
        self.tabifyDockWidget(self.display_dock, self.roi_dock)
        self.display_dock.raise_()

        self.coord_label = QLabel("")
        self.zoom_label = QLabel("")
        self.statusBar().addWidget(self.coord_label, 1)
        self.statusBar().addPermanentWidget(self.zoom_label)

        self._build_menus()
        self._build_toolbar()

        self.session.manager.listeners.append(self._manager_event)
        self.runner.message.connect(lambda m: self.statusBar().showMessage(m, 5000))
        self.runner.busy_changed.connect(lambda b: self.tabs.setEnabled(not b))
        self.runner.op_finished.connect(self._op_finished)
        self.roi_panel.measure_requested.connect(self._measure_all)
        self.roi_panel.changed.connect(self._show_all_rois)
        self.camera.error.connect(lambda m: self.statusBar().showMessage(m, 8000))
        self.camera_panel.new_image.connect(self.add_doc)
        self.camera_panel.live_view_requested.connect(lambda _: self._ensure_live_tab())
        self.camera.state_changed.connect(self._camera_state)
        self.display_panel.set_doc(None)
        self.roi_panel.set_doc(None)
        self._restore()

    # ---------------------------------------------------------------- construction
    def _dock(self, title: str, widget: QWidget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName(title)
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    def _act(self, menu: QMenu, text: str, fn, shortcut=None, checkable=False, checked=False) -> QAction:
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if checkable:
            a.setCheckable(True)
            a.setChecked(checked)
            a.toggled.connect(fn)
        else:
            a.triggered.connect(lambda _=False: fn())
        menu.addAction(a)
        return a

    def _populate(self, menu: QMenu, node: dict) -> None:
        for key in sorted(k for k in node if k != "__ops__"):
            self._populate(menu.addMenu(key), node[key])
        if node.get("__ops__") and menu.actions():
            menu.addSeparator()
        for cls in sorted(node.get("__ops__", []), key=lambda c: c.title):
            a = QAction(cls.title + ("…" if cls.view else ""), self)
            a.triggered.connect(lambda _=False, c=cls: self.runner.run(c))
            menu.addAction(a)

    def _build_menus(self) -> None:
        mb = self.menuBar()
        tree = base.menu_tree()

        m = mb.addMenu("&File")
        self._act(m, "Open…", self.open_dialog, "Ctrl+O")
        self.recent_menu = m.addMenu("Open Recent")
        self._act(m, "Save As…", self.save_as, "Ctrl+S")
        self._act(m, "Close", lambda: self._close_tab(self.tabs.currentIndex()), "Ctrl+W")
        m.addSeparator()
        self._act(m, "Quit", self.close, "Ctrl+Q")

        m = mb.addMenu("&Edit")
        self._act(m, "Undo", self.undo, "Ctrl+Z")
        self._act(m, "Redo", self.redo, "Ctrl+Shift+Z")
        m.addSeparator()
        self._act(m, "Select All", self.select_all, "Ctrl+A")
        self._act(m, "Select None", lambda: self._with_doc(lambda d: d.set_roi(None)), "Ctrl+Shift+A")
        self._act(m, "Add to ROI Manager", self.roi_panel.add, "Ctrl+T")
        m.addSeparator()
        self._populate(m, tree.get("Edit", {}))

        m = mb.addMenu("&Image")
        self._act(m, "Duplicate", self.duplicate, "Ctrl+Shift+D")
        self._act(m, "Set Scale…", self.set_scale)
        self._act(m, "Show Scale Bar", self._toggle_scale_bar, checkable=True, checked=True)
        lut_menu = m.addMenu("Lookup Tables")
        for name in luts.all_luts():
            self._act(lut_menu, name, lambda n=name: self.display_panel.select_lut(n))
        m.addSeparator()
        node = dict(tree.get("Image", {}))
        self._populate(m, node)

        for name in ("Process", "Analyze"):
            m = mb.addMenu(f"&{name}")
            if name == "Analyze":
                self._act(m, "Set Scale…", self.set_scale)
                self._act(m, "Show Results", lambda: (self.results_dock.show(), self.results_dock.raise_()))
                m.addSeparator()
            self._populate(m, tree.get(name, {}))
        extra = [k for k in tree if k not in MENU_ORDER and k != "__ops__"]
        for k in extra + (["Plugins"] if "Plugins" in tree else []):
            if k in MENU_ORDER and k != "Plugins":
                continue
            self._populate(mb.addMenu(k), tree[k])

        m = mb.addMenu("&Camera")
        self._act(m, "Camera Panel", lambda: (self.camera_dock.show(), self.camera_dock.raise_()), "Ctrl+Shift+C")
        self._act(m, "Snap", lambda: self.camera_panel._snap(), "Ctrl+Shift+S")
        self._act(m, "Refresh Devices", self.camera.refresh_devices)

        m = mb.addMenu("&Macro")
        self._act(m, "Start Recording", self.macro_record)
        self._act(m, "Stop Recording && Show", self.macro_stop)
        self._act(m, "Run Macro…", self.macro_run)
        self._act(m, "Batch Process…", self.macro_batch)

        m = mb.addMenu("&View")
        self._act(m, "Zoom In", lambda: self._with_canvas(lambda c: c.zoom_in()), "Ctrl+=")
        self._act(m, "Zoom Out", lambda: self._with_canvas(lambda c: c.zoom_out()), "Ctrl+-")
        self._act(m, "Actual Size (100%)", lambda: self._with_canvas(lambda c: c.zoom_100()), "Ctrl+1")
        self._act(m, "Fit to Window", lambda: self._with_canvas(lambda c: c.fit()), "Ctrl+0")
        m.addSeparator()
        tm = m.addMenu("Theme")
        grp = QActionGroup(self)
        cur = self.settings.value("theme", "auto")
        for t in ("auto", "light", "dark"):
            a = QAction(t.capitalize(), self, checkable=True)
            a.setChecked(t == cur)
            a.triggered.connect(lambda _=False, t=t: self.set_theme(t))
            grp.addAction(a)
            tm.addAction(a)
        self._act(m, "Touch-friendly Sizing", self.set_touch, checkable=True,
                  checked=str(self.settings.value("touch", "false")).lower() == "true")
        m.addSeparator()
        for d in (self.display_dock, self.roi_dock, self.results_dock, self.camera_dock):
            m.addAction(d.toggleViewAction())

        m = mb.addMenu("&Help")
        self._act(m, "About ImagePi", self.about)
        self._refresh_recent()

    def _build_toolbar(self) -> None:
        tb = QToolBar("Tools")
        tb.setObjectName("Tools")
        tb.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)
        grp = QActionGroup(self)
        self._tool_actions: dict[str, QAction] = {}
        for key, label in TOOL_LABELS:
            a = QAction(label, self, checkable=True)
            a.setToolTip(label)
            a.setChecked(key == self._tool_name)
            a.triggered.connect(lambda _=False, k=key: self.set_tool(k))
            grp.addAction(a)
            tb.addAction(a)
            self._tool_actions[key] = a
        tb.addSeparator()
        for text, fn in (("Fit", lambda c: c.fit()), ("100%", lambda c: c.zoom_100()), ("+", lambda c: c.zoom_in()), ("−", lambda c: c.zoom_out())):
            a = QAction(text, self)
            a.triggered.connect(lambda _=False, fn=fn: self._with_canvas(fn))
            tb.addAction(a)
        tb.addSeparator()
        a = QAction("Measure", self)
        a.setShortcut(QKeySequence("Ctrl+M"))
        a.triggered.connect(lambda: self.runner.run(base.REGISTRY["Analyze>Measure"]))
        tb.addAction(a)

    # ---------------------------------------------------------------- state access
    def current_view(self) -> ImageView | None:
        w = self.tabs.currentWidget()
        return w if isinstance(w, ImageView) else None

    def current_canvas(self) -> ImageCanvas | None:
        v = self.current_view()
        return v.canvas if v else None

    def current_doc(self) -> ImageDoc | None:
        v = self.current_view()
        return v.doc if v else None

    def _with_doc(self, fn) -> None:
        d = self.current_doc()
        if d is not None:
            fn(d)

    def _with_canvas(self, fn) -> None:
        c = self.current_canvas()
        if c is not None:
            fn(c)

    def busy_cursor(self):
        return QCursor(Qt.CursorShape.WaitCursor)

    # ---------------------------------------------------------------- documents
    def add_doc(self, doc: ImageDoc) -> ImageDoc:
        return self.session.manager.add(doc)

    def _manager_event(self, what: str, doc: ImageDoc) -> None:
        if what == "add":
            view = ImageView(doc)
            view.canvas.cursor_info.connect(self._cursor_info)
            view.canvas.zoom_changed.connect(lambda s: self.zoom_label.setText(f"{s * 100:.0f}%"))
            self._apply_tool(view.canvas)
            doc.listeners.append(self._doc_changed)
            self._views.append(view)
            i = self.tabs.addTab(view, doc.title)
            self.tabs.setCurrentIndex(i)
        elif what == "remove":
            for v in list(self._views):
                if v.doc is doc:
                    i = self.tabs.indexOf(v)
                    if i >= 0:
                        self.tabs.removeTab(i)
                    v.close_view()
                    if self._doc_changed in doc.listeners:
                        doc.listeners.remove(self._doc_changed)
                    self._views.remove(v)
                    v.deleteLater()

    def _doc_changed(self, doc, what) -> None:
        for v in self._views:
            if v.doc is doc:
                i = self.tabs.indexOf(v)
                if i >= 0:
                    self.tabs.setTabText(i, doc.title + (" *" if doc.changed else ""))

    def _tab_changed(self, i: int) -> None:
        v = self.current_view()
        doc = v.doc if v else None
        self.display_panel.set_doc(doc)
        self.roi_panel.set_doc(doc)
        if doc is not None:
            self.session.manager.activate(doc)
            self.zoom_label.setText(f"{v.canvas.scale_factor * 100:.0f}%")
            self.setWindowTitle(f"ImagePi - {doc.title}")
        else:
            self.setWindowTitle("ImagePi")

    def _close_tab(self, i: int) -> None:
        w = self.tabs.widget(i)
        if w is self._live_tab:
            self.camera.stop_live()
            self.tabs.removeTab(i)
            self._live_tab = None
            return
        if not isinstance(w, ImageView):
            return
        doc = w.doc
        if doc.changed:
            r = QMessageBox.question(self, "Close", f"Save changes to {doc.title}?",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return
            if r == QMessageBox.StandardButton.Save and not self.save_as(doc):
                return
        self.session.manager.remove(doc)

    def _cursor_info(self, x: float, y: float, v) -> None:
        d = self.current_doc()
        if d is None:
            return
        cal = d.calibration
        pos = f"x={x:.1f} y={y:.1f}"
        if cal.calibrated:
            pos += f"  ({cal.to_units(x):.4g}, {cal.to_units(y):.4g} {cal.unit})"
        if v is not None:
            vs = ", ".join(f"{c:g}" for c in v) if isinstance(v, list) else f"{v:g}"
            pos += f"   value={vs}"
        self.coord_label.setText(pos)

    # ---------------------------------------------------------------- file
    def open_dialog(self) -> None:
        exts = " ".join(f"*{e}" for e in io.supported_read_extensions())
        paths, _ = QFileDialog.getOpenFileNames(self, "Open image", self.settings.value("last_dir", ""), f"Images ({exts});;All files (*)")
        for p in paths:
            self.open_path(p)

    def open_path(self, path: str) -> ImageDoc | None:
        try:
            doc = io.open_image(path)
        except Exception as e:
            QMessageBox.warning(self, "Open", f"Could not open {os.path.basename(path)}:\n{e}")
            return None
        self.settings.setValue("last_dir", os.path.dirname(path))
        recent = [p for p in self._recent() if p != path][:9]
        self.settings.setValue("recent", [path] + recent)
        self._refresh_recent()
        return self.add_doc(doc)

    def _recent(self) -> list[str]:
        r = self.settings.value("recent", [])
        return [r] if isinstance(r, str) else list(r or [])

    def _refresh_recent(self) -> None:
        self.recent_menu.clear()
        for p in self._recent():
            a = QAction(p, self)
            a.triggered.connect(lambda _=False, p=p: self.open_path(p))
            self.recent_menu.addAction(a)
        self.recent_menu.setEnabled(bool(self.recent_menu.actions()))

    def save_as(self, doc: ImageDoc | None = None) -> bool:
        doc = doc or self.current_doc()
        if doc is None:
            return False
        exts = " ".join(f"*{e}" for e in io.supported_write_extensions())
        start = doc.path or os.path.splitext(doc.title)[0] + ".tif"
        path, _ = QFileDialog.getSaveFileName(self, "Save image", start, f"Images ({exts})")
        if not path:
            return False
        if not os.path.splitext(path)[1]:
            path += ".tif"
        try:
            io.save_image(doc, path)
        except Exception as e:
            QMessageBox.warning(self, "Save", str(e))
            return False
        doc.title = os.path.basename(path)
        doc.notify("title")
        self._doc_changed(doc, "title")
        return True

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            if u.isLocalFile():
                self.open_path(u.toLocalFile())

    # ---------------------------------------------------------------- edit / image
    def undo(self) -> None:
        self._undo_redo("Undo", "undo")

    def redo(self) -> None:
        self._undo_redo("Redo", "redo")

    def _undo_redo(self, verb: str, which: str) -> None:
        """Undo or redo and say what happened in the status line (round 6: nobody could tell whether Ctrl+Z had done anything)."""
        d = self.current_doc()
        if d is None:
            return
        label = getattr(d, f"{which}_label")()
        if label is None:
            self.statusBar().showMessage(f"Nothing to {which}", 5000)
            return
        getattr(d, which)()
        self.statusBar().showMessage(f"{verb}: {label}" if label else verb, 5000)

    def select_all(self) -> None:
        self._with_doc(lambda d: d.set_roi(Rect(0, 0, d.width, d.height)))

    def duplicate(self) -> None:
        d = self.current_doc()
        if d is None:
            return
        n = d.duplicate()
        if d.roi is not None and d.roi.is_area and d.n_frames >= 1:
            x0, y0, x1, y1 = (int(round(v)) for v in d.roi.bounds())
            n.data = n.data[:, max(y0, 0):y1, max(x0, 0):x1].copy()
            n.reset_range()
        self.add_doc(n)

    def set_scale(self) -> None:
        d = self.current_doc()
        if d is None:
            return
        dlg = CalibrationDialog(d, self)
        if dlg.exec():
            d.calibration = dlg.calibration()
            d.notify("calibration")
            self.camera_panel.reload_presets()
            self.statusBar().showMessage(d.calibration.describe(), 15000)

    def _toggle_scale_bar(self, on: bool) -> None:
        for v in self._views:
            v.canvas.show_scale_bar = on
            v.canvas.viewport().update()

    def _show_all_rois(self) -> None:
        on = self.roi_panel.show_all.isChecked()
        for v in self._views:
            v.canvas.show_all_rois = on
            v.canvas.rebuild_overlays()

    def _measure_all(self) -> None:
        self.runner.run(base.REGISTRY["Analyze>Measure All ROIs"])

    def _op_finished(self, result) -> None:
        d = self.current_doc()
        if d is not None and d.labels is not None:
            d.notify("labels")
        if len(self.session.results) or len(self.session.summary):
            self.results_dock.show()

    # ---------------------------------------------------------------- tools
    def set_tool(self, name: str) -> None:
        self._tool_name = name
        for v in self._views:
            self._apply_tool(v.canvas)

    def _apply_tool(self, canvas: ImageCanvas) -> None:
        if self._tool_name == "hand":
            canvas.set_hand_tool()
        else:
            canvas.set_tool(self._tool_name)

    # ---------------------------------------------------------------- camera
    def _ensure_live_tab(self) -> None:
        if self._live_tab is None:
            self._live_tab = LiveView(self.camera)
            self._live_tab.crop_selected.connect(self.camera_panel.set_crop)
            self.tabs.addTab(self._live_tab, "Live")
        self.tabs.setCurrentWidget(self._live_tab)

    def _camera_state(self) -> None:
        if not self.camera.connected and self._live_tab is not None:
            i = self.tabs.indexOf(self._live_tab)
            if i >= 0:
                self.tabs.removeTab(i)
            self._live_tab = None

    # ---------------------------------------------------------------- macros
    def macro_record(self) -> None:
        self.session.recorder.start()
        self.statusBar().showMessage("Recording macro…", 4000)

    def macro_stop(self) -> None:
        self.session.recorder.stop()
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Recorded macro")
        text = self.session.recorder.text() or "(nothing recorded)"
        dlg.setText("Recorded commands:")
        dlg.setInformativeText(text)
        save = dlg.addButton("Save…", QMessageBox.ButtonRole.AcceptRole)
        dlg.addButton(QMessageBox.StandardButton.Close)
        dlg.exec()
        if dlg.clickedButton() is save and self.session.recorder.lines:
            path, _ = QFileDialog.getSaveFileName(self, "Save macro", "macro.txt", "Macro (*.txt)")
            if path:
                self.session.recorder.save(path)

    def macro_run(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Run macro", "", "Macro (*.txt);;All files (*)")
        d = self.current_doc()
        if not path or d is None:
            return
        from ..core import commands

        try:
            n = commands.replay(self.session, open(path).read(), d)
            self.statusBar().showMessage(f"Ran {n} command(s)", 5000)
        except Exception as e:
            QMessageBox.warning(self, "Macro", f"{type(e).__name__}: {e}")

    def macro_batch(self) -> None:
        mpath, _ = QFileDialog.getOpenFileName(self, "Macro to apply", "", "Macro (*.txt)")
        if not mpath:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Images to process")
        if not files:
            return
        out = QFileDialog.getExistingDirectory(self, "Save results to")
        if not out:
            return
        from ..core import commands

        QApplication.setOverrideCursor(self.busy_cursor())
        try:
            done = commands.run_batch(self.session, open(mpath).read(), files, out)
            self.statusBar().showMessage(f"Processed {len(done)} image(s) into {out}", 8000)
        except Exception as e:
            QMessageBox.warning(self, "Batch", f"{type(e).__name__}: {e}")
        finally:
            QApplication.restoreOverrideCursor()

    # ---------------------------------------------------------------- misc
    def set_theme(self, mode: str) -> None:
        self.settings.setValue("theme", mode)
        theme.apply_theme(QApplication.instance(), mode, str(self.settings.value("touch", "false")).lower() == "true")

    def set_touch(self, on: bool) -> None:
        self.settings.setValue("touch", "true" if on else "false")
        theme.apply_theme(QApplication.instance(), self.settings.value("theme", "auto"), on)

    def about(self) -> None:
        QMessageBox.about(self, "ImagePi", "ImagePi\nMicroscope image viewing and processing for the Raspberry Pi 5.")

    def _restore(self) -> None:
        g = self.settings.value("geometry")
        if g is not None:
            self.restoreGeometry(g)

    def closeEvent(self, e):
        self.camera.disconnect_camera()
        try:
            from ..camera.touptek import TouptekCamera

            TouptekCamera.unwatch_hotplug()
        except Exception:
            pass
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(e)
