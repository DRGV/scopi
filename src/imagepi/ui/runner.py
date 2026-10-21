"""Runs ops from the GUI: parameter dialog, live preview on a crop, background compute, commit."""

from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox

from ..core.image import ImageDoc, dtype_range
from ..ops import base
from .para_dialog import ParaDialog

PREVIEW_MAX = 1200   # px side of the preview crop
PREVIEW_PAD = 48     # extra border so filter edge effects fall outside the visible area


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    @Slot()
    def run(self):
        try:
            res = self.fn()
        except Exception as e:  # reported to the user, never crashes the worker
            self.signals.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.signals.done.emit(res)


_ACTIVE: set[Job] = set()


def start_job(job: Job) -> None:
    """Run ``job`` on the global pool, keeping it (and its signal object) alive until its signals are delivered.

    Without this the Python wrapper can be collected as soon as the runnable finishes and the queued
    done/failed signal is never delivered.
    """
    _ACTIVE.add(job)
    job.setAutoDelete(False)
    job.signals.done.connect(lambda *_: _ACTIVE.discard(job))
    job.signals.failed.connect(lambda *_: _ACTIVE.discard(job))
    QThreadPool.globalInstance().start(job)


def preview_region(doc: ImageDoc, view: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """Integer crop (x0, y0, x1, y1) covering the visible viewport plus padding, capped to PREVIEW_MAX."""
    vx0, vy0, vx1, vy1 = view
    cx, cy = (vx0 + vx1) / 2, (vy0 + vy1) / 2
    half = PREVIEW_MAX / 2
    x0 = max(int(max(vx0 - PREVIEW_PAD, cx - half)), 0)
    y0 = max(int(max(vy0 - PREVIEW_PAD, cy - half)), 0)
    x1 = min(int(min(vx1 + PREVIEW_PAD, cx + half)) + 1, doc.width)
    y1 = min(int(min(vy1 + PREVIEW_PAD, cy + half)) + 1, doc.height)
    if x1 <= x0 or y1 <= y0:
        return 0, 0, min(doc.width, PREVIEW_MAX), min(doc.height, PREVIEW_MAX)
    return x0, y0, x1, y1


def compute_preview(op: base.Filter, doc: ImageDoc, para: dict, region) -> tuple[np.ndarray, tuple[float, float]]:
    """Run ``op`` on a crop and paste the result into a copy of the current plane (thread-safe)."""
    x0, y0, x1, y1 = region
    plane = doc.frame()
    tmp = ImageDoc(plane[y0:y1, x0:x1][None].copy(), rgb=doc.rgb)
    if doc.roi is not None and doc.roi.is_area:
        tmp.roi = doc.roi.translated(-x0, -y0)
    res = base.compute_filter(op, tmp, para, stack=False)[0]
    if res.dtype == plane.dtype and res.ndim == plane.ndim:
        full = plane.copy()
        full[y0:y1, x0:x1] = res
        return full, doc.display_range
    full = np.zeros(plane.shape[:2] + res.shape[2:], res.dtype)
    full[y0:y1, x0:x1] = res
    if res.dtype.kind in "ui":
        rng = dtype_range(res.dtype)
        rng = (0.0, 255.0) if res.dtype == np.uint8 else rng
    else:
        rng = (float(res.min()), float(res.max()) if res.max() > res.min() else float(res.min()) + 1)
    return full, rng


class OpRunner(QObject):
    """Glue between the main window (current doc/canvas/session) and the op framework."""

    busy_changed = Signal(bool)
    message = Signal(str)
    op_finished = Signal(object)     # result of a Simple op or None

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._gen = 0
        self._busy = False

    # ---------------------------------------------------------------- entry point
    def run(self, op_cls: type[base.Op]) -> None:
        w = self.window
        op = op_cls()
        doc = w.current_doc()
        if doc is None and not isinstance(op, base.Simple):
            self._error("Open an image first.")
            return
        if isinstance(op, base.Simple) and doc is None and op_cls.title not in ("Clear Results",):
            self._error("Open an image first.")
            return
        err = op.check(doc) if doc is not None else None
        if err:
            self._error(err)
            return
        view = op.view_for(doc) if doc is not None else op.view
        if not view:
            self._execute(op, doc, {}, False)
            return
        canvas = w.current_canvas()
        is_filter = isinstance(op, base.Filter)
        dlg = ParaDialog(op, view, can_preview=is_filter and "preview" in op.note and canvas is not None,
                         can_stack=is_filter and "stack" in op.note and doc.n_frames > 1,
                         initial=w.last_para.get(op.path()), parent=w)
        if dlg.preview_box.isVisible():
            dlg.preview_requested.connect(lambda para: self._preview(op, doc, canvas, para))
            dlg.preview_cleared.connect(lambda: self._clear_preview(canvas))
        if dlg.exec():
            self._clear_preview(canvas)
            para = dlg.para()
            w.last_para[op.path()] = para
            self._execute(op, doc, para, dlg.whole_stack)
        else:
            self._clear_preview(canvas)

    # ---------------------------------------------------------------- preview
    def _preview(self, op, doc, canvas, para) -> None:
        self._gen += 1
        gen = self._gen
        v = canvas.view_rect()
        region = preview_region(doc, (v.left(), v.top(), v.right(), v.bottom()))
        job = Job(lambda: compute_preview(op, doc, para, region))

        def done(res):
            if gen == self._gen:
                canvas.set_preview(*res)

        job.signals.done.connect(done)
        job.signals.failed.connect(lambda msg: self.message.emit("Preview: " + msg))
        start_job(job)

    def _clear_preview(self, canvas) -> None:
        self._gen += 1
        if canvas is not None:
            canvas.set_preview(None)

    # ---------------------------------------------------------------- execution
    def _execute(self, op, doc, para, stack: bool) -> None:
        session = self.window.session
        if isinstance(op, base.Filter):
            self._set_busy(True)
            t0 = time.monotonic()
            job = Job(lambda: base.compute_filter(op, doc, para, stack))

            def done(result):
                try:
                    base.commit_filter(op, doc, result, session, para)
                    self.message.emit(f"{op.title}: {time.monotonic() - t0:.2f} s")
                    self.op_finished.emit(None)
                finally:
                    self._set_busy(False)

            def failed(msg):
                self._set_busy(False)
                self._error(msg)

            job.signals.done.connect(done)
            job.signals.failed.connect(failed)
            start_job(job)
            return
        QApplication.setOverrideCursor(self.window.busy_cursor())
        try:
            out = base.execute(op, session, doc, para)
            self.op_finished.emit(out)
        except Exception as e:
            self._error(f"{type(e).__name__}: {e}")
        finally:
            QApplication.restoreOverrideCursor()

    def _set_busy(self, on: bool) -> None:
        if on == self._busy:
            return
        self._busy = on
        if on:
            QApplication.setOverrideCursor(self.window.busy_cursor())
        else:
            QApplication.restoreOverrideCursor()
        self.busy_changed.emit(on)

    def _error(self, msg: str) -> None:
        QMessageBox.warning(self.window, "ImagePi", msg)
