# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Simulated camera: synthetic microscope scene, or replay of a folder of images/video."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .base import CameraBackend, CameraError, CameraInfo, Range

_SYNTH_RES = [(2456, 1842), (1228, 922), (614, 461)]
AUTO_EXPOSURE_TARGET = 110.0   # mean gray level the simulated auto exposure steers to (0-255)


def synthetic_scene(w: int, h: int, t: float = 0.0, exposure_us: int = 10000, gain: int = 100, seed: int = 0) -> np.ndarray:
    """Cells on a bright field background; brightness follows exposure*gain."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:h, :w].astype(np.float32)
    img = np.full((h, w), 0.8, np.float32) - 0.15 * ((xx - w / 2) ** 2 + (yy - h / 2) ** 2) / (w * w / 2)
    n = 40
    cx, cy = rng.random(n) * w, rng.random(n) * h
    r = (0.015 + 0.02 * rng.random(n)) * min(w, h)
    for i in range(n):
        x0, y0 = cx[i] + 3 * np.sin(t + i), cy[i] + 3 * np.cos(t * 0.7 + i)
        x1, x2 = int(max(x0 - 2 * r[i], 0)), int(min(x0 + 2 * r[i], w))
        y1, y2 = int(max(y0 - 2 * r[i], 0)), int(min(y0 + 2 * r[i], h))
        if x2 <= x1 or y2 <= y1:
            continue
        d2 = (xx[y1:y2, x1:x2] - x0) ** 2 + (yy[y1:y2, x1:x2] - y0) ** 2
        img[y1:y2, x1:x2] -= 0.6 * np.exp(-d2 / (2 * (r[i] / 1.5) ** 2))
    gain_f = (exposure_us / 10000.0) * (gain / 100.0)
    img = np.clip(img * gain_f, 0, 1)
    img = img + rng.normal(0, 0.01, img.shape).astype(np.float32)
    g = np.clip(img * 255, 0, 255).astype(np.uint8)
    return np.dstack([g, (g * 0.95).astype(np.uint8), (g * 0.85).astype(np.uint8)])


class SimulatedCamera(CameraBackend):
    """Generates frames at ``fps``. If ``source`` is a folder/file, replays those images in a loop."""

    name = "simulated"

    def __init__(self, source: str | None = None, fps: float = 20.0):
        self.source = source
        self.fps = fps
        self._open = False
        self._res_index = 1
        self._exposure = 10000
        self._gain = 100
        self._auto_exp = False
        self._auto_limit = 350_000   # like the real camera's default longest auto exposure (us)
        self._wb = (6503, 1000)
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._latest: np.ndarray | None = None
        self._lock = threading.Lock()
        self._frames: list[np.ndarray] = []
        self._i = 0
        self._notify: Callable[[], None] | None = None
        self._event: Callable[[str], None] | None = None
        self.frame_count = 0
        self._roi: tuple[int, int, int, int] | None = None
        self._bits = 8

    @classmethod
    def enumerate(cls) -> list[CameraInfo]:
        return [CameraInfo("sim-0", "Simulated camera", cls.name, list(_SYNTH_RES))]

    def open(self, camera_id: str = "sim-0") -> None:
        if self.source:
            self._frames = _load_source(self.source)
            if not self._frames:
                raise CameraError(f"no images found in {self.source}")
        self._open = True

    def close(self) -> None:
        self.stop()
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def resolutions(self):
        if self._frames:
            h, w = self._frames[0].shape[:2]
            return [(w, h)]
        return list(_SYNTH_RES)

    def set_resolution(self, index: int) -> None:
        if self.running:
            raise CameraError("stop the camera before changing resolution")
        if not 0 <= index < len(self.resolutions):
            raise CameraError("bad resolution index")
        self._res_index = index

    @property
    def resolution_index(self) -> int:
        return min(self._res_index, len(self.resolutions) - 1)

    def set_exposure(self, microseconds: int) -> None:
        self._exposure = int(np.clip(microseconds, 100, 1_000_000))

    def get_exposure(self) -> int:
        return self._exposure

    def exposure_range(self) -> Range:
        return Range(100, 1_000_000, 10000)

    def set_gain(self, percent: int) -> None:
        self._gain = int(np.clip(percent, 100, 1000))

    def get_gain(self) -> int:
        return self._gain

    def gain_range(self) -> Range:
        return Range(100, 1000, 100)

    def set_auto_exposure(self, on: bool) -> None:
        self._auto_exp = on

    def get_auto_exposure(self) -> bool:
        return self._auto_exp

    supports_auto_exposure_limit = True

    def set_auto_exposure_limit(self, microseconds: int) -> None:
        self._auto_limit = int(np.clip(microseconds, 100, 1_000_000))

    def set_white_balance(self, temp: int, tint: int) -> None:
        self._wb = (temp, tint)

    def get_white_balance(self):
        return self._wb

    def _make(self, res_index: int | None = None) -> np.ndarray:
        if self._frames:
            f = self._frames[self._i % len(self._frames)]
            self._i += 1
        else:
            w, h = _SYNTH_RES[self.resolution_index if res_index is None else res_index]
            f = synthetic_scene(w, h, time.monotonic(), self._exposure, self._gain, seed=1)
            if self._auto_exp:
                self._auto_adjust(f)
        if self._roi is not None:
            x, y, rw, rh = self._roi
            if not self._frames and res_index is not None and res_index != self.resolution_index:
                k = _SYNTH_RES[res_index][0] / _SYNTH_RES[self.resolution_index][0]   # ROI is in preview-pixel units
                x, y, rw, rh = (int(v * k) for v in (x, y, rw, rh))
            f = f[y:y + rh, x:x + rw]
        return np.ascontiguousarray(self._oriented(f))

    def _auto_adjust(self, frame: np.ndarray) -> None:
        """Steer the exposure so the mean gray level approaches the target (a real camera does this in firmware)."""
        mean = float(frame[::8, ::8].mean())
        ratio = AUTO_EXPOSURE_TARGET / max(mean, 1.0)
        self._exposure = int(np.clip(self._exposure * ratio ** 0.5, 100, self._auto_limit))

    def set_roi(self, x: int, y: int, w: int, h: int) -> None:
        full_w, full_h = self.resolutions[self.resolution_index]
        self._roi = None if (x, y, w, h) == (0, 0, full_w, full_h) else self._roi_to_sensor(x, y, w, h, full_w, full_h)

    @property
    def supports_16bit(self) -> bool:
        return True

    def set_bit_depth(self, bits: int) -> None:
        if self.running:
            raise CameraError("stop the camera before changing bit depth")
        self._bits = bits

    # ---- streaming ----
    def start(self, on_frame, on_event=None) -> None:
        if not self._open:
            raise CameraError("camera not open")
        if self.running:
            return
        self._notify, self._event = on_frame, on_event
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._loop, name="sim-camera", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        period = 1.0 / self.fps
        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            frame = self._make()
            with self._lock:
                self._latest = frame
                self.frame_count += 1
            if self._notify:
                self._notify()
            self._stop_evt.wait(max(period - (time.monotonic() - t0), 0))

    def stop(self) -> None:
        self._stop_evt.set()
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(timeout=2)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def pull(self):
        with self._lock:
            f, self._latest = self._latest, None
        return f

    def snap(self, bits: int = 8) -> np.ndarray:
        if not self._open:
            raise CameraError("camera not open")
        f = self._make(0)   # stills are captured at the largest resolution
        if bits == 16:
            return f.astype(np.uint16) * 257
        return f

    def unplug(self) -> None:
        """Test helper: simulate a disconnect."""
        self.stop()
        self._open = False
        if self._event:
            self._event("disconnected")


def _load_source(source: str) -> list[np.ndarray]:
    p = Path(source)
    files = sorted(f for f in p.iterdir() if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")) if p.is_dir() else [p]
    out = []
    for f in files:
        if f.suffix.lower() in (".mp4", ".avi", ".mov"):
            import cv2

            cap = cv2.VideoCapture(str(f))
            while True:
                ok, fr = cap.read()
                if not ok:
                    break
                out.append(fr[..., ::-1].copy())
            cap.release()
            continue
        from .. import io

        d = io.open_image(str(f))
        a = d.frame()
        if a.dtype != np.uint8:
            lo, hi = d.display_range
            a = np.clip((a.astype(np.float32) - lo) / max(hi - lo, 1) * 255, 0, 255).astype(np.uint8)
        out.append(a if a.ndim == 3 else np.dstack([a] * 3))
    return out
