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
from PySide6.QtCore import QObject, QPointF, QRectF, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from ..camera import CameraBackend, CameraError, SimulatedCamera, available_backends
from ..core.calibration import Calibration, ObjectivePresets
from ..core.image import ImageDoc
from .runner import Job, start_job


def presets_path() -> Path:
    return Path(os.environ.get("IMAGEPI_PRESETS", Path.home() / ".imagepi" / "presets.json"))


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

    def __init__(self, backend: CameraBackend):
        super().__init__()
        self.backend = backend
        self.gui_busy = False
        self.dropped = 0

    @Slot()
    def pull(self):
        try:
            f = self.backend.pull()
        except Exception:
            return
        if f is None:
            return
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
        self.setMinimumSize(320, 240)
        self.setAutoFillBackground(True)
        controller.frame.connect(self.set_frame)

    def set_frame(self, arr: np.ndarray) -> None:
        arr = np.ascontiguousarray(arr)
        if arr.dtype == np.uint16:
            arr = (arr >> 8).astype(np.uint8)
        h, w = arr.shape[:2]
        self._arr = arr
        fmt = QImage.Format.Format_RGB888 if arr.ndim == 3 else QImage.Format.Format_Grayscale8
        self._img = QImage(arr.data, w, h, arr.strides[0], fmt)
        self.update()
        self.controller.frame_consumed()

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
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No live image")
            return
        t = self._target()
        p.drawImage(t, self._img)
        p.setPen(QColor(255, 255, 255))
        p.drawText(8, 18, f"{self._img.width()} x {self._img.height()}   {self.controller.fps:.1f} fps")
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

    def __init__(self, controller: CameraController, parent=None):
        super().__init__(parent)
        self.c = controller
        self.presets = load_presets()
        self._devices: list = []
        self._block = False
        self._crop: tuple[int, int, int, int] | None = None
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
        f.addRow("Gain", self.gain_slider)
        f.addRow("", self.gain_label)
        f.addRow(self.awb_btn)
        f.addRow("Temp", self.temp_spin)
        f.addRow("Tint", self.tint_spin)
        f.addRow("Crop", self.crop_btn)
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
        self.tl_folder = QPushButton(str(Path.home() / "ImagePi-timelapse"))
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
        self.exp_spin.editingFinished.connect(self._exp)
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
            return
        b = self.c.backend
        self._block = True
        try:
            self.res_combo.clear()
            self.res_combo.addItems([f"{w} x {h}" for w, h in b.resolutions])
            self.res_combo.setCurrentIndex(b.resolution_index)
            self.bits_combo.setEnabled(b.supports_16bit)
            er, gr = b.exposure_range(), b.gain_range()
            self.exp_spin.setRange(er.min / 1000, er.max / 1000)
            self.exp_spin.setValue(b.get_exposure() / 1000)
            self.gain_slider.setRange(int(gr.min), int(gr.max))
            self.gain_slider.setValue(b.get_gain())
            self.gain_label.setText(f"{b.get_gain()} %")
            t, ti = b.get_white_balance()
            self.temp_spin.setValue(t)
            self.tint_spin.setValue(ti)
            self.auto_exp.setChecked(b.get_auto_exposure())
        except Exception as e:
            self.status.setText(str(e))
        finally:
            self._block = False

    # ---------------------------------------------------------------- controls
    def _res_changed(self, i: int) -> None:
        if self._block or not self.c.connected or i < 0:
            return
        self.c.reconfigure(lambda: self.c.backend.set_resolution(i))

    def _bits_changed(self, i: int) -> None:
        if self._block or not self.c.connected:
            return
        self.c.reconfigure(lambda: self.c.backend.set_bit_depth(16 if i == 1 else 8))

    def _auto_exp(self, on: bool) -> None:
        if not self._block and self.c.connected:
            self._call(lambda: self.c.backend.set_auto_exposure(on))
            self.exp_spin.setEnabled(not on)

    def _exp(self) -> None:
        if not self._block and self.c.connected:
            self._call(lambda: self.c.backend.set_exposure(int(self.exp_spin.value() * 1000)))

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
        self._crop = (x, y, w, h)
        self.crop_btn.setText(f"Crop {w} x {h} (click: full frame)")
        self._call(lambda: self.c.backend.set_roi(x, y, w, h))

    def _clear_crop(self) -> None:
        if not self.c.connected or self._crop is None:
            return
        self._crop = None
        self.crop_btn.setText("Full frame")
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
