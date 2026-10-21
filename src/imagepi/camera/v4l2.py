"""OpenCV (V4L2 / AVFoundation) fallback for generic UVC cameras."""

from __future__ import annotations

import threading
from typing import Callable

import numpy as np

from .base import CameraBackend, CameraError, CameraInfo, Range


class V4L2Camera(CameraBackend):
    name = "v4l2"
    MAX_PROBE = 6

    def __init__(self):
        self._cap = None
        self._index = 0
        self._res = [(640, 480), (1280, 720), (1920, 1080)]
        self._res_index = 0
        self._thread = None
        self._stop = threading.Event()
        self._latest = None
        self._lock = threading.Lock()
        self._notify = None
        self._exposure = 10000
        self._gain = 100

    @classmethod
    def enumerate(cls) -> list[CameraInfo]:
        import cv2

        out = []
        for i in range(cls.MAX_PROBE):
            cap = cv2.VideoCapture(i)
            try:
                if cap.isOpened():
                    out.append(CameraInfo(str(i), f"Video device {i}", cls.name, [(640, 480), (1280, 720), (1920, 1080)]))
            finally:
                cap.release()
        return out

    def open(self, camera_id: str) -> None:
        import cv2

        self._index = int(camera_id)
        self._cap = cv2.VideoCapture(self._index)
        if not self._cap.isOpened():
            self._cap = None
            raise CameraError(f"cannot open video device {camera_id}")
        self._apply_resolution()

    def _apply_resolution(self) -> None:
        import cv2

        w, h = self._res[self._res_index]
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)

    def close(self) -> None:
        self.stop()
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def is_open(self) -> bool:
        return self._cap is not None

    @property
    def resolutions(self):
        return list(self._res)

    def set_resolution(self, index: int) -> None:
        if self.running:
            raise CameraError("stop the camera before changing resolution")
        self._res_index = index
        if self._cap is not None:
            self._apply_resolution()

    @property
    def resolution_index(self) -> int:
        return self._res_index

    def set_exposure(self, microseconds: int) -> None:
        import cv2

        self._exposure = int(microseconds)
        if self._cap is not None:
            self._cap.set(cv2.CAP_PROP_EXPOSURE, microseconds / 1e6)

    def get_exposure(self) -> int:
        return self._exposure

    def exposure_range(self) -> Range:
        return Range(100, 1_000_000, 10000)

    def set_gain(self, percent: int) -> None:
        import cv2

        self._gain = int(percent)
        if self._cap is not None:
            self._cap.set(cv2.CAP_PROP_GAIN, percent)

    def get_gain(self) -> int:
        return self._gain

    def gain_range(self) -> Range:
        return Range(100, 1000, 100)

    def start(self, on_frame, on_event=None) -> None:
        if self._cap is None:
            raise CameraError("camera not open")
        if self.running:
            return
        self._notify = on_frame
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="v4l2-camera")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            ok, fr = self._cap.read()
            if not ok:
                self._stop.wait(0.02)
                continue
            with self._lock:
                self._latest = fr[..., ::-1].copy()
            if self._notify:
                self._notify()

    def stop(self) -> None:
        self._stop.set()
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
        if self._cap is None:
            raise CameraError("camera not open")
        was_running = self.running
        if was_running:
            # frames come from the shared capture; use the newest one
            import time

            for _ in range(50):
                with self._lock:
                    f = self._latest
                if f is not None:
                    return f.copy()
                time.sleep(0.02)
            raise CameraError("no frame available")
        ok, fr = self._cap.read()
        if not ok:
            raise CameraError("failed to read frame")
        return fr[..., ::-1].copy()
