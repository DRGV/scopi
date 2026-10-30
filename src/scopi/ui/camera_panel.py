# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Camera live view: controller (threads, snap, time-lapse) and the dock panel.

Threading: the backend's frame notification fires on an SDK thread and only emits a Qt signal.
A worker QThread then pulls the newest frame and hands it to the GUI thread; frames are dropped
(never queued) while the GUI is still busy with the previous one.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, QSettings, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from ..camera import CameraBackend, CameraError, SimulatedCamera, available_backends
from ..core.calibration import Calibration, ObjectivePresets
from ..core.image import ImageDoc
from .runner import Job, start_job


def presets_path() -> Path:
    return Path(os.environ.get("SCOPI_PRESETS", Path.home() / ".scopi" / "presets.json"))


def load_presets() -> ObjectivePresets:
    try:
        return ObjectivePresets.from_dict(json.loads(presets_path().read_text()))
    except (OSError, ValueError):
        return ObjectivePresets()


def save_presets(p: ObjectivePresets) -> None:
    path = presets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(p.to_dict(), indent=2))


def calibration_for_frame(preset: Calibration, full_width: int, frame_width: int) -> Calibration:
    """Presets are recorded at full resolution; scale the pixel size for a binned/low-res frame."""
    if not preset.calibrated or frame_width <= 0:
        return Calibration(preset.pixel_size, preset.unit)
    return Calibration(preset.pixel_size * full_width / frame_width, preset.unit)


class _Puller(QObject):
    """Lives in the worker thread: fetches the newest frame and forwards it unless the GUI is behind."""

    frame_ready = Signal(object)
    error = Signal(str)

    def __init__(self, backend: CameraBackend):
        super().__init__()
        self.backend = backend
        self.gui_busy = False
        self.dropped = 0
        self._last_error = ""

    @Slot()
    def pull(self):
        try:
            f = self.backend.pull()
        except Exception as e:
            msg = str(e) or type(e).__name__
            if msg != self._last_error:   # one report per distinct failure, not one per frame
                self._last_error = msg
                self.error.emit(msg)
            return
        if f is None:
            return
        self._last_error = ""
        if self.gui_busy:
            self.dropped += 1
            return
        self.gui_busy = True
        self.frame_ready.emit(f)


class CameraController(QObject):
    frame_available = Signal()           # emitted from the SDK thread
    frame = Signal(object)               # GUI thread: ndarray (H, W, 3)
    snapped = Signal(object, dict)       # ndarray, metadata
    devices = Signal(list)               # [(backend_name, CameraInfo)]
    state_changed = Signal()
    error = Signal(str)
    event = Signal(str)                  # 'disconnected' / 'error'
    hotplug = Signal()
    timelapse_progress = Signal(int, int)
    timelapse_done = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.backend: CameraBackend | None = None
        self._thread: QThread | None = None
        self._puller: _Puller | None = None
        self.fps = 0.0
        self._count = 0
        self._t0 = time.monotonic()
        self._snapping = False
        self._timelapse: dict | None = None
        self._tl_timer = QTimer(self)
        self._tl_timer.timeout.connect(self._tl_tick)
        self.event.connect(self._on_event)

    # ---------------------------------------------------------------- discovery
    def refresh_devices(self) -> None:
        """Enumerate off the GUI thread (SDK enumeration can block for a long time)."""
        def work():
            out = [("simulated", i) for i in SimulatedCamera.enumerate()]
            for name, cls in available_backends().items():
                if name == "simulated":
                    continue
                try:
                    out += [(name, i) for i in cls.enumerate()]
                except Exception:
                    pass
            return out

        job = Job(work)
        job.signals.done.connect(self.devices.emit)
        job.signals.failed.connect(self.error.emit)
        start_job(job)

    def watch_hotplug(self) -> None:
        def work():
            try:
                from ..camera.touptek import TouptekCamera

                return TouptekCamera.watch_hotplug(lambda: self.hotplug.emit())
            except Exception:
                return False

        start_job(Job(work))

    # ---------------------------------------------------------------- lifecycle
    @property
    def connected(self) -> bool:
        return self.backend is not None and self.backend.is_open

    @property
    def live(self) -> bool:
        return self.connected and self.backend.running

    def connect_camera(self, backend: CameraBackend, camera_id: str) -> None:
        self.disconnect_camera()
        try:
            backend.open(camera_id)
        except CameraError as e:
            self.error.emit(str(e))
            return
        self.backend = backend
        self._puller = _Puller(backend)
        self._thread = QThread(self)
        self._puller.moveToThread(self._thread)
        self.frame_available.connect(self._puller.pull, Qt.ConnectionType.QueuedConnection)
        self._puller.frame_ready.connect(self._on_frame)
        self._puller.error.connect(self.error)
        self._thread.start()
        self.state_changed.emit()

    def disconnect_camera(self) -> None:
        if self._timelapse:
            self.stop_timelapse()
        b = self.backend
        if b is None:
            return
        try:
            b.stop()
        except Exception:
            pass
        if self._thread is not None:
            try:
                self.frame_available.disconnect(self._puller.pull)
            except (TypeError, RuntimeError):
                pass
            self._thread.quit()
            self._thread.wait(2000)
            self._thread = None
            self._puller = None
        try:
            b.close()
        except Exception:
            pass
        self.backend = None
        self.fps = 0.0
        self.state_changed.emit()

    def start_live(self) -> None:
        if not self.connected:
            return
        try:
            self._count = 0
            self._t0 = time.monotonic()
            self.backend.start(lambda: self.frame_available.emit(), lambda ev: self.event.emit(ev))
        except CameraError as e:
            self.error.emit(str(e))
        self.state_changed.emit()

    def stop_live(self) -> None:
        if self.connected:
            self.backend.stop()
        self.state_changed.emit()

    def reconfigure(self, fn) -> None:
        """Run ``fn`` (resolution / bit depth changes) with the camera stopped, then restore live view."""
        was_live = self.live
        if was_live:
            self.backend.stop()
        try:
            fn()
        except Exception as e:
            self.error.emit(str(e))
        if was_live:
            self.start_live()
        self.state_changed.emit()

    @Slot(object)
    def _on_frame(self, f):
        self._count += 1
        dt = time.monotonic() - self._t0
        if dt >= 1.0:
            self.fps = self._count / dt
            self._count, self._t0 = 0, time.monotonic()
        self.frame.emit(f)

    def frame_consumed(self) -> None:
        if self._puller is not None:
            self._puller.gui_busy = False

    @Slot(str)
    def _on_event(self, ev: str) -> None:
        # Delivered on the GUI thread via the queued signal; safe to stop/close here (never inside the SDK callback).
        if ev == "disconnected":
            self.disconnect_camera()
            self.error.emit("Camera disconnected.")
        elif ev == "error":
            self.error.emit("Camera reported an error.")

    # ---------------------------------------------------------------- capture
    def snap(self, bits: int = 8) -> None:
        if not self.connected or self._snapping:
            return
        self._snapping = True
        b = self.backend

        def work():
            arr = b.snap(bits)
            md = b.metadata()
            md["width"], md["height"] = arr.shape[1], arr.shape[0]   # the still, not the preview size
            md["timestamp"] = datetime.now().isoformat(timespec="seconds")
            return arr, md

        job = Job(work)

        def done(res):
            self._snapping = False
            self.snapped.emit(*res)

        def failed(msg):
            self._snapping = False
            self.error.emit(msg)

        job.signals.done.connect(done)
        job.signals.failed.connect(failed)
        start_job(job)

    # ---------------------------------------------------------------- time-lapse
    def start_timelapse(self, interval_s: float, count: int, folder: str, bits: int, make_doc) -> None:
        if not self.connected:
            self.error.emit("Connect a camera first.")
            return
        os.makedirs(folder, exist_ok=True)
        self._timelapse = {"n": 0, "count": count, "folder": folder, "bits": bits, "make_doc": make_doc}
        self._tl_timer.start(int(interval_s * 1000))
        self._tl_tick()

    def stop_timelapse(self) -> None:
        self._tl_timer.stop()
        tl, self._timelapse = self._timelapse, None
        if tl:
            self.timelapse_done.emit(tl["folder"])

    def _tl_tick(self) -> None:
        tl = self._timelapse
        if tl is None or not self.connected:
            return
        if self._snapping:
            return  # previous capture still running; skip this tick
        if tl["n"] >= tl["count"]:
            self.stop_timelapse()
            return
        self._snapping = True
        b, n = self.backend, tl["n"]
        tl["n"] += 1

        def work():
            from .. import io

            arr = b.snap(tl["bits"])
            md = b.metadata()
            md["width"], md["height"] = arr.shape[1], arr.shape[0]
            md["timestamp"] = datetime.now().isoformat(timespec="seconds")
            md["timelapse_index"] = n
            doc = tl["make_doc"](arr, md)
            path = os.path.join(tl["folder"], f"timelapse_{datetime.now():%Y%m%d_%H%M%S}_{n:04d}.tif")
            io.save_image(doc, path)
            return path

        job = Job(work)

        def done(path):
            self._snapping = False
            self.timelapse_progress.emit(n + 1, tl["count"])
            if n + 1 >= tl["count"]:
                self.stop_timelapse()

        def failed(msg):
            self._snapping = False
            self.stop_timelapse()
            self.error.emit(msg)

        job.signals.done.connect(done)
        job.signals.failed.connect(failed)
        start_job(job)


class LiveView(QWidget):
    """Paints the newest frame fit to the widget; drag to select a crop rectangle."""

    crop_selected = Signal(int, int, int, int)   # x, y, w, h in frame pixels

    def __init__(self, controller: CameraController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._img: QImage | None = None
        self._arr = None
        self._sel: tuple[QPointF, QPointF] | None = None
        self._why = ""   # last camera error, shown while there is no image
        self._exp_shown = (0.0, "")   # (time asked, text): the exposure is read from the camera at most twice a second
        self.setMinimumSize(320, 240)
        self.setAutoFillBackground(True)
        controller.frame.connect(self.set_frame)
        controller.error.connect(self._set_why)

    def _set_why(self, msg: str) -> None:
        self._why = msg
        self.update()

    def set_frame(self, arr: np.ndarray) -> None:
        arr = np.ascontiguousarray(arr)
        if arr.dtype == np.uint16:
            arr = (arr >> 8).astype(np.uint8)
        h, w = arr.shape[:2]
        self._arr = arr
        self._why = ""
        fmt = QImage.Format.Format_RGB888 if arr.ndim == 3 else QImage.Format.Format_Grayscale8
        self._img = QImage(arr.data, w, h, arr.strides[0], fmt)
        self.update()
        self.controller.frame_consumed()

    def overlay_text(self) -> str:
        """Size, frame rate and exposure time: a frame rate below 1000 / exposure (ms) is explained by the exposure."""
        text = f"{self._img.width()} x {self._img.height()}   {self.controller.fps:.1f} fps"
        now = time.monotonic()
        if now - self._exp_shown[0] >= 0.5:
            shown = ""
            b = self.controller.backend
            if b is not None:
                try:
                    shown = f"   exposure {round(b.get_exposure() / 1000, 1):g} ms" + (" (auto)" if b.get_auto_exposure() else "")
                except Exception:
                    pass
            self._exp_shown = (now, shown)
        return text + self._exp_shown[1]

    def _target(self) -> QRectF:
        if self._img is None:
            return QRectF()
        s = min(self.width() / self._img.width(), self.height() / self._img.height())
        w, h = self._img.width() * s, self._img.height() * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(30, 30, 30))
        if self._img is None:
            p.setPen(QColor(180, 180, 180))
            p.drawText(self.rect().adjusted(16, 0, -16, 0), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       "No live image" + (f"\n\n{self._why}" if self._why else ""))
            return
        t = self._target()
        p.drawImage(t, self._img)
        p.setPen(QColor(255, 255, 255))
        p.drawText(8, 18, self.overlay_text())
        if self._sel:
            a, b = self._sel
            p.setPen(QPen(QColor(255, 235, 0), 1.5, Qt.PenStyle.DashLine))
            p.drawRect(QRectF(a, b).normalized())

    def _to_frame(self, pt: QPointF) -> QPointF:
        t = self._target()
        s = self._img.width() / t.width()
        return QPointF((pt.x() - t.left()) * s, (pt.y() - t.top()) * s)

    def mousePressEvent(self, e):
        if self._img is not None:
            self._sel = (e.position(), e.position())

    def mouseMoveEvent(self, e):
        if self._sel:
            self._sel = (self._sel[0], e.position())
            self.update()

    def mouseReleaseEvent(self, e):
        if not self._sel or self._img is None:
            return
        a, b = self._to_frame(self._sel[0]), self._to_frame(e.position())
        self._sel = None
        r = QRectF(a, b).normalized().intersected(QRectF(0, 0, self._img.width(), self._img.height()))
        self.update()
        if r.width() >= 16 and r.height() >= 16:
            self.crop_selected.emit(int(r.x()), int(r.y()), int(r.width()), int(r.height()))


class CameraPanel(QWidget):
    """Dock contents: connect, live, exposure/gain/white-balance, resolution, snap, time-lapse."""

    new_image = Signal(object)       # ImageDoc
    live_view_requested = Signal(bool)

    AUTO_LIMIT_MS = 100.0   # default longest auto exposure: at least ~10 fps in live view (the camera's own default is 350 ms)

    def __init__(self, controller: CameraController, parent=None, settings: QSettings | None = None):
        super().__init__(parent)
        self.c = controller
        self.settings = settings
        self.presets = load_presets()
        self._devices: list = []
        self._block = False
        self._configured = None      # the backend whose saved flips and auto exposure limit were applied
        self._device_key = ""        # settings key of the selected camera (its display name)
        self._crop: tuple[int, int, int, int] | None = None   # active crop in displayed full-frame pixels
        lay = QVBoxLayout(self)

        box = QGroupBox("Device")
        f = QFormLayout(box)
        self.dev_combo = QComboBox()
        self.refresh_btn = QPushButton("Refresh")
        self.connect_btn = QPushButton("Connect")
        row = QHBoxLayout()
        row.addWidget(self.refresh_btn)
        row.addWidget(self.connect_btn)
        f.addRow(self.dev_combo)
        f.addRow(row)
        lay.addWidget(box)

        self.live_btn = QPushButton("Start live view")
        self.snap_btn = QPushButton("Snap")
        row = QHBoxLayout()
        row.addWidget(self.live_btn)
        row.addWidget(self.snap_btn)
        lay.addLayout(row)

        box = QGroupBox("Acquisition")
        f = QFormLayout(box)
        self.res_combo = QComboBox()
        self.bits_combo = QComboBox()
        self.bits_combo.addItems(["8-bit", "16-bit"])
        self.auto_exp = QCheckBox("Auto exposure")
        self.exp_spin = QDoubleSpinBox()
        self.exp_spin.setSuffix(" ms")
        self.exp_spin.setDecimals(2)
        self.limit_spin = QDoubleSpinBox()
        self.limit_spin.setSuffix(" ms")
        self.limit_spin.setDecimals(0)
        self.limit_spin.setRange(1, 1000)
        self.limit_spin.setValue(self.AUTO_LIMIT_MS)
        self.limit_spin.setToolTip("Longest exposure auto exposure may use. Beyond it the camera raises the gain instead. "
                                   "Frames per second cannot exceed 1000 / this value, so a long limit makes the live view lag.")
        self.flip_h = QCheckBox("Flip horizontally")
        self.flip_v = QCheckBox("Flip vertically")
        where = ("With neither box ticked the picture matches the view through the eyepiece (camera mounted with its USB cable "
                 "pointing to the rear of the microscope). Applies to the live view and to snaps, and is remembered for this camera.")
        self.flip_h.setToolTip("Mirror left and right. " + where)
        self.flip_v.setToolTip("Mirror top and bottom. " + where)
        self.gain_slider = QSlider(Qt.Orientation.Horizontal)
        self.gain_label = QLabel("100 %")
        self.awb_btn = QPushButton("Auto white balance")
        self.temp_spin, self.tint_spin = QSpinBox(), QSpinBox()
        self.temp_spin.setRange(2000, 15000)
        self.tint_spin.setRange(200, 2500)
        self.crop_btn = QPushButton("Full frame")
        f.addRow("Resolution", self.res_combo)
        f.addRow("Bit depth", self.bits_combo)
        f.addRow(self.auto_exp)
        f.addRow("Exposure", self.exp_spin)
        f.addRow("Auto limit", self.limit_spin)
        f.addRow("Gain", self.gain_slider)
        f.addRow("", self.gain_label)
        f.addRow(self.awb_btn)
        f.addRow("Temp", self.temp_spin)
        f.addRow("Tint", self.tint_spin)
        f.addRow("Crop", self.crop_btn)
        row = QHBoxLayout()
        row.addWidget(self.flip_h)
        row.addWidget(self.flip_v)
        f.addRow("Orientation", row)
        lay.addWidget(box)
        self.acq_box = box

        box = QGroupBox("Calibration")
        f = QFormLayout(box)
        self.preset_combo = QComboBox()
        self._reload_presets()
        f.addRow("Objective", self.preset_combo)
        lay.addWidget(box)

        box = QGroupBox("Time-lapse")
        f = QFormLayout(box)
        self.tl_interval = QDoubleSpinBox()
        self.tl_interval.setRange(0.1, 86400)
        self.tl_interval.setValue(5.0)
        self.tl_interval.setSuffix(" s")
        self.tl_count = QSpinBox()
        self.tl_count.setRange(1, 100000)
        self.tl_count.setValue(10)
        self.tl_folder = QPushButton(str(Path.home() / "Scopi-timelapse"))
        self.tl_btn = QPushButton("Start time-lapse")
        self.tl_status = QLabel("")
        f.addRow("Interval", self.tl_interval)
        f.addRow("Frames", self.tl_count)
        f.addRow("Folder", self.tl_folder)
        f.addRow(self.tl_btn)
        f.addRow(self.tl_status)
        lay.addWidget(box)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addStretch(1)

        self.refresh_btn.clicked.connect(self.c.refresh_devices)
        self.connect_btn.clicked.connect(self._toggle_connect)
        self.live_btn.clicked.connect(self._toggle_live)
        self.snap_btn.clicked.connect(self._snap)
        self.res_combo.currentIndexChanged.connect(self._res_changed)
        self.bits_combo.currentIndexChanged.connect(self._bits_changed)
        self.auto_exp.toggled.connect(self._auto_exp)
        # apply while typing or stepping (after a short pause), not only on Enter or focus loss
        self._exp_timer = QTimer(self)
        self._exp_timer.setSingleShot(True)
        self._exp_timer.setInterval(300)
        self._exp_timer.timeout.connect(self._exp)
        self.exp_spin.valueChanged.connect(self._exp_edited)
        self.exp_spin.editingFinished.connect(self._exp)   # Enter or focus loss applies at once
        # while auto exposure runs, show the value the camera chose
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(500)
        self._auto_timer.timeout.connect(self._follow_auto_exposure)
        self._limit_timer = QTimer(self)
        self._limit_timer.setSingleShot(True)
        self._limit_timer.setInterval(300)
        self._limit_timer.timeout.connect(self._apply_limit)
        self.limit_spin.valueChanged.connect(lambda _v: self._limit_timer.start())
        self.limit_spin.editingFinished.connect(self._apply_limit)
        self.flip_h.toggled.connect(self._flip_changed)
        self.flip_v.toggled.connect(self._flip_changed)
        self.gain_slider.valueChanged.connect(self._gain)
        self.awb_btn.clicked.connect(lambda: self._call(lambda: self.c.backend.auto_white_balance()))
        self.temp_spin.editingFinished.connect(self._wb)
        self.tint_spin.editingFinished.connect(self._wb)
        self.crop_btn.clicked.connect(self._clear_crop)
        self.tl_folder.clicked.connect(self._pick_folder)
        self.tl_btn.clicked.connect(self._toggle_timelapse)
        self.c.devices.connect(self._set_devices)
        self.c.state_changed.connect(self._sync)
        self.c.error.connect(lambda m: self.status.setText(m))
        self.c.snapped.connect(self._snapped)
        self.c.timelapse_progress.connect(lambda n, t: self.tl_status.setText(f"{n}/{t} saved"))
        self.c.timelapse_done.connect(lambda d: self.tl_status.setText(f"Done: {d}"))
        self._set_devices([("simulated", i) for i in SimulatedCamera.enumerate()])
        self._sync()

    # ---------------------------------------------------------------- helpers
    def _reload_presets(self) -> None:
        cur = self.preset_combo.currentText()
        self.preset_combo.clear()
        self.preset_combo.addItem("(uncalibrated)")
        self.preset_combo.addItems(sorted(self.presets.presets))
        i = self.preset_combo.findText(cur)
        if i >= 0:
            self.preset_combo.setCurrentIndex(i)

    def reload_presets(self) -> None:
        self.presets = load_presets()
        self._reload_presets()

    def _call(self, fn) -> None:
        try:
            fn()
        except Exception as e:
            self.status.setText(str(e))

    def _set_devices(self, devs: list) -> None:
        self._devices = devs
        self.dev_combo.clear()
        for name, info in devs:
            self.dev_combo.addItem(f"{info.name} [{name}]")

    def _toggle_connect(self) -> None:
        if self.c.connected:
            self.c.disconnect_camera()
            return
        i = self.dev_combo.currentIndex()
        if i < 0:
            return
        name, info = self._devices[i]
        backend = available_backends()[name]()
        self._device_key = f"{name}/{info.name}".replace(" ", "_")
        self.c.connect_camera(backend, info.id)

    def _toggle_live(self) -> None:
        if self.c.live:
            self.c.stop_live()
        else:
            self.live_view_requested.emit(True)
            self.c.start_live()

    def _sync(self) -> None:
        conn = self.c.connected
        self.connect_btn.setText("Disconnect" if conn else "Connect")
        self.live_btn.setText("Stop live view" if self.c.live else "Start live view")
        for w in (self.live_btn, self.snap_btn, self.acq_box, self.tl_btn):
            w.setEnabled(conn)
        self.dev_combo.setEnabled(not conn)
        if not conn:
            self.status.setText(self.status.text() if self.status.text().startswith("Camera") else "")
            self._configured = None
            self._crop = None
            self.crop_btn.setText("Full frame")
            return
        b = self.c.backend
        if b is not self._configured:
            self._configured = b
            self._configure_new_camera(b)
        self._block = True
        try:
            self.res_combo.clear()
            self.res_combo.addItems([f"{w} x {h}" for w, h in b.resolutions])
            self.res_combo.setCurrentIndex(b.resolution_index)
            self._show_bit_depths(b)
            er, gr = b.exposure_range(), b.gain_range()
            self.exp_spin.setRange(er.min / 1000, er.max / 1000)
            self.limit_spin.setRange(max(1.0, er.min / 1000), er.max / 1000)
            self.exp_spin.setValue(b.get_exposure() / 1000)
            self.gain_slider.setRange(int(gr.min), int(gr.max))
            self.gain_slider.setValue(b.get_gain())
            self.gain_label.setText(f"{b.get_gain()} %")
            t, ti = b.get_white_balance()
            self.temp_spin.setValue(t)
            self.tint_spin.setValue(ti)
            self.auto_exp.setChecked(b.get_auto_exposure())
            self._auto_exposure_ui(self.auto_exp.isChecked())
            self.limit_spin.setEnabled(b.supports_auto_exposure_limit)
        except Exception as e:
            self.status.setText(str(e))
        finally:
            self._block = False

    def _show_bit_depths(self, b) -> None:
        """A camera without a >8-bit mode says so (a plain disabled box looked like a click that did nothing)."""
        want = ["8-bit", "16-bit"] if b.supports_16bit else ["8-bit only"]
        if [self.bits_combo.itemText(i) for i in range(self.bits_combo.count())] != want:
            self.bits_combo.clear()
            self.bits_combo.addItems(want)
        self.bits_combo.setEnabled(b.supports_16bit)
        self.bits_combo.setToolTip("" if b.supports_16bit else "This camera has no mode deeper than 8 bits per channel.")

    def _setting(self, name: str, default):
        if self.settings is None or not self._device_key:
            return default
        return self.settings.value(f"camera/{self._device_key}/{name}", default)

    def _save_setting(self, name: str, value) -> None:
        if self.settings is not None and self._device_key:
            self.settings.setValue(f"camera/{self._device_key}/{name}", value)

    def _configure_new_camera(self, b) -> None:
        """Once per connection: restore this camera's saved orientation and cap the auto exposure time."""
        # "mirror_h/v" (not the old "flip_h/v", which held the whole flip before the eyepiece view became the default)
        flips = [str(self._setting(n, "false")).lower() == "true" for n in ("mirror_h", "mirror_v")]
        limit = float(self._setting("auto_limit_ms", self.AUTO_LIMIT_MS))
        self._block = True
        try:
            self.flip_h.setChecked(flips[0])
            self.flip_v.setChecked(flips[1])
            self.limit_spin.setValue(limit)
        finally:
            self._block = False
        self._call(lambda: b.set_flip(*flips))
        if b.supports_auto_exposure_limit:
            self._call(lambda: b.set_auto_exposure_limit(int(limit * 1000)))

    # ---------------------------------------------------------------- controls
    def _res_changed(self, i: int) -> None:
        if self._block or not self.c.connected or i < 0:
            return
        self.c.reconfigure(lambda: self.c.backend.set_resolution(i))
        self._reset_crop()           # a new resolution starts uncropped

    def _bits_changed(self, i: int) -> None:
        if self._block or not self.c.connected:
            return
        self.c.reconfigure(lambda: self.c.backend.set_bit_depth(16 if i == 1 else 8))

    def _auto_exp(self, on: bool) -> None:
        if not self._block and self.c.connected:
            self._call(lambda: self.c.backend.set_auto_exposure(on))
            self._auto_exposure_ui(on)

    def _auto_exposure_ui(self, on: bool) -> None:
        self.exp_spin.setEnabled(not on)
        if on:
            self._exp_timer.stop()
            self._auto_timer.start()
        else:
            self._auto_timer.stop()

    def _follow_auto_exposure(self) -> None:
        if not self.c.connected:
            self._auto_timer.stop()
            return
        try:
            ms = self.c.backend.get_exposure() / 1000
        except Exception:
            return
        self.exp_spin.blockSignals(True)
        self.exp_spin.setValue(ms)
        self.exp_spin.blockSignals(False)

    def _exp_edited(self) -> None:
        if not self._block and self.c.connected and not self.auto_exp.isChecked():
            self._exp_timer.start()

    def _exp(self) -> None:
        self._exp_timer.stop()
        if not self._block and self.c.connected:
            self._call(lambda: self.c.backend.set_exposure(int(self.exp_spin.value() * 1000)))

    def _apply_limit(self) -> None:
        self._limit_timer.stop()
        if self._block or not self.c.connected or not self.c.backend.supports_auto_exposure_limit:
            return
        ms = self.limit_spin.value()
        self._save_setting("auto_limit_ms", ms)
        self._call(lambda: self.c.backend.set_auto_exposure_limit(int(ms * 1000)))

    def _flip_changed(self, _on: bool) -> None:
        if self._block or not self.c.connected:
            return
        h, v = self.flip_h.isChecked(), self.flip_v.isChecked()
        self._save_setting("mirror_h", h)
        self._save_setting("mirror_v", v)
        self._call(lambda: self.c.backend.set_flip(h, v))
        self._reset_crop()           # the crop was chosen on the old orientation

    def _gain(self, v: int) -> None:
        self.gain_label.setText(f"{v} %")
        if not self._block and self.c.connected:
            self._call(lambda: self.c.backend.set_gain(v))

    def _wb(self) -> None:
        if not self._block and self.c.connected:
            self._call(lambda: self.c.backend.set_white_balance(self.temp_spin.value(), self.tint_spin.value()))

    def set_crop(self, x: int, y: int, w: int, h: int) -> None:
        """Hardware ROI from a rectangle selected on the live view (frame coordinates)."""
        if not self.c.connected:
            return
        if self._crop is not None:   # the rectangle was drawn on the already cropped picture: add the crop's own offset
            x, y = x + self._crop[0], y + self._crop[1]
        self._crop = (x, y, w, h)
        self.crop_btn.setText(f"Crop {w} x {h} (click: full frame)")
        self._call(lambda: self.c.backend.set_roi(x, y, w, h))

    def _clear_crop(self) -> None:
        if not self.c.connected or self._crop is None:
            return
        self._reset_crop()

    def _reset_crop(self) -> None:
        """Back to the full frame (only touches the camera if a crop was active)."""
        if self._crop is None:
            return
        self._crop = None
        self.crop_btn.setText("Full frame")
        if self.c.connected:
            w, h = self.c.backend.resolutions[self.c.backend.resolution_index]
            self._call(lambda: self.c.backend.set_roi(0, 0, w, h))

    # ---------------------------------------------------------------- capture
    def _bits(self) -> int:
        return 16 if self.bits_combo.currentIndex() == 1 and self.bits_combo.isEnabled() else 8

    def _snap(self) -> None:
        self.status.setText("Capturing…")
        self.c.snap(self._bits())

    def make_doc(self, arr: np.ndarray, md: dict) -> ImageDoc:
        doc = ImageDoc(arr, datetime.now().strftime("snap_%H%M%S"), rgb=arr.ndim == 3)
        name = self.preset_combo.currentText()
        if name in self.presets.presets and self.c.backend is not None:
            full = max(w for w, _ in self.c.backend.resolutions)
            doc.calibration = calibration_for_frame(self.presets.get(name), full, arr.shape[1])
            md["objective"] = name
        md["calibration"] = doc.calibration.to_dict()
        doc.metadata = md
        return doc

    def _snapped(self, arr, md) -> None:
        self.status.setText(f"Captured {arr.shape[1]} x {arr.shape[0]}")
        self.new_image.emit(self.make_doc(arr, md))

    def _pick_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Time-lapse folder", self.tl_folder.text())
        if d:
            self.tl_folder.setText(d)

    def _toggle_timelapse(self) -> None:
        if self.c._timelapse:
            self.c.stop_timelapse()
            self.tl_btn.setText("Start time-lapse")
            return
        self.tl_btn.setText("Stop time-lapse")
        self.c.timelapse_done.connect(lambda _: self.tl_btn.setText("Start time-lapse"))
        self.c.start_timelapse(self.tl_interval.value(), self.tl_count.value(), self.tl_folder.text(), self._bits(),
                               lambda arr, md: self.make_doc(arr, md))
