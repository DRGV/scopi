# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""ToupTek / OMAX camera backend (OMAX A35180U3 and other ToupTek USB cameras).

Wraps the vendor's single-file ctypes wrapper ``toupcam.py`` which is *not* shipped with Scopi
(vendor licence). Run ``scripts/install_toupcam.py`` to populate ``vendor/toupcam/``.

Uses pull mode: the SDK callback only signals; frames are fetched with ``PullImageV3`` by the
consumer. The callback must return quickly and must never call Stop/Close (deadlock).
"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import platform
import shutil
import sys
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from .base import CameraBackend, CameraError, CameraInfo, PermissionDenied, Range

_toupcam = None


def _vendor_dirs() -> list[Path]:
    dirs = []
    env = os.environ.get("SCOPI_TOUPCAM_DIR")
    if env:
        dirs.append(Path(env))
    dirs.append(Path(__file__).resolve().parents[3] / "vendor" / "toupcam")
    dirs.append(Path.home() / ".scopi" / "vendor" / "toupcam")
    return dirs


def _native_candidates() -> tuple[list[str], str]:
    """Folders (relative to the SDK root) that may hold the native library, newest SDK layout first."""
    m = platform.machine().lower()
    if sys.platform == "darwin":
        return ["mac", "mac/x64+arm64"], "libtoupcam.dylib"
    if sys.platform.startswith("linux"):
        arch = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x64", "amd64": "x64", "armv7l": "armhf", "i686": "x86"}.get(m, m)
        return [f"linux/{arch}/glibc", f"linux/{arch}"], "libtoupcam.so"
    raise CameraError(f"unsupported platform {sys.platform}")


def load_sdk():
    """Import the vendor wrapper (cached). Raises CameraError with install instructions if absent."""
    global _toupcam
    if _toupcam is not None:
        return _toupcam
    subs, libname = _native_candidates()
    for root in _vendor_dirs():
        py = root / "python" / "toupcam.py"
        if not py.is_file():
            continue
        lib = next((root / sub / libname for sub in subs if (root / sub / libname).is_file()), None)
        adjacent = py.parent / libname
        if lib is not None and (not adjacent.exists() or adjacent.stat().st_mtime < lib.stat().st_mtime):
            shutil.copy2(lib, adjacent)  # the wrapper looks next to itself first
        spec = importlib.util.spec_from_file_location("toupcam", py)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["toupcam"] = mod
        spec.loader.exec_module(mod)
        _toupcam = mod
        return mod
    raise CameraError("ToupTek SDK not found. Run: python scripts/install_toupcam.py <path to toupcamsdk zip>")


def _hr(ex) -> int:
    return getattr(ex, "hr", 0) & 0xFFFFFFFF


E_ACCESSDENIED = 0x80070005
E_PENDING = 0x8000000A

# HRESULT -> (name, hint). The SDK's raw numbers are opaque, and these end up in the GUI status line.
_HR_INFO = {
    0x80070005: ("E_ACCESSDENIED", "no permission: install the udev rule and replug the camera"),
    0x800700AA: ("E_BUSY", "camera already in use (another Scopi window or program?) or the USB link could not be set up"),
    0x8007001F: ("E_GEN_FAILURE", "device not functioning: check the cable, the USB 3 port and the power supply"),
    0x8007000E: ("E_OUTOFMEMORY", "out of memory (on Linux also the usbfs buffer limit)"),
    0x80070057: ("E_INVALIDARG", "invalid argument"),
    0x80004001: ("E_NOTIMPL", "not supported by this camera"),
    0x8000FFFF: ("E_UNEXPECTED", "not allowed in the camera's current state"),
    0x80004005: ("E_FAIL", "generic failure"),
    0x8001010E: ("E_WRONG_THREAD", "called from the wrong thread"),
    0x8001011F: ("E_TIMEOUT", "timed out"),
}


def _hr_text(ex) -> str:
    """'E_BUSY (0x800700aa): camera already in use ...' for an SDK exception."""
    hr = _hr(ex)
    name, hint = _HR_INFO.get(hr, ("", ""))
    return f"{name} (0x{hr:x}): {hint}" if name else f"hr=0x{hr:x}"


class TouptekCamera(CameraBackend):
    name = "touptek"
    delivered_flip = (False, True)   # frames arrive upside down against the eyepiece view and the hardware ROI axes (see CameraBackend)
    RESTART_AFTER_SNAP = True        # the second snap of a run never delivered its still (round 4); a restarted stream does

    def __init__(self):
        self._tc = None
        self._cam = None
        self._model = None
        self._res_index = 0
        self._running = False
        self._notify: Callable[[], None] | None = None
        self._event: Callable[[str], None] | None = None
        self._buf = None
        self._still_buf = None
        self._frame_ready = threading.Event()
        self._still_ready = threading.Event()
        self._lock = threading.Lock()
        self._bits = 8
        self._raw = False
        self._auto_exp = False
        self._events: dict[int, int] = {}
        self._restart_lock = threading.Lock()
        self._roi: tuple[int, int, int, int] | None = None   # active hardware crop, in the camera's own coordinates
        self.dropped = 0
        self.snap_restarts = 0     # how often a snap had to restart the stream (the bringup snaptest reports it)

    # ---- discovery ----
    @classmethod
    def enumerate(cls) -> list[CameraInfo]:
        tc = load_sdk()
        out = []
        for d in tc.Toupcam.EnumV2():
            res = [(r.width, r.height) for r in d.model.res]
            out.append(CameraInfo(d.id, d.displayname, cls.name, res, (d.model.xpixsz, d.model.ypixsz)))
        return out

    @classmethod
    def watch_hotplug(cls, callback: Callable[[], None]) -> bool:
        """Call ``callback`` (from an SDK thread) on USB plug/unplug, then re-run enumerate().

        The vendor wrapper's own HotPlug() raises NameError on Linux/macOS (a name-mangling bug), so we
        register through the underlying library handle. Returns False if unavailable (poll instead).
        """
        import ctypes

        tc = load_sdk()
        try:
            tc.Toupcam.EnumV2()  # forces library load
            lib = tc.Toupcam._Toupcam__lib
            proto = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
            cls._hotplug_cb = proto(lambda _ctx: callback())  # keep a reference
            lib.Toupcam_HotPlug.argtypes = [proto, ctypes.c_void_p]
            lib.Toupcam_HotPlug.restype = None
            lib.Toupcam_HotPlug(cls._hotplug_cb, None)
            return True
        except Exception:
            return False

    @classmethod
    def unwatch_hotplug(cls) -> None:
        """Unregister the hotplug callback. Do this before exit: the SDK's monitor thread can otherwise delay shutdown."""
        import ctypes

        try:
            lib = load_sdk().Toupcam._Toupcam__lib
            proto = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
            lib.Toupcam_HotPlug.argtypes = [proto, ctypes.c_void_p]
            lib.Toupcam_HotPlug(proto(0), None)
        except Exception:
            pass
        cls._hotplug_cb = None

    # ---- lifecycle ----
    def open(self, camera_id: str) -> None:
        self._tc = load_sdk()
        try:
            self._cam = self._tc.Toupcam.Open(camera_id)
        except self._tc.HRESULTException as ex:
            if _hr(ex) == E_ACCESSDENIED:
                raise PermissionDenied("permission denied opening the camera: install packaging/99-toupcam.rules (udev) and replug") from ex
            raise CameraError(f"failed to open camera: {_hr_text(ex)}") from ex
        if not self._cam:
            raise CameraError("failed to open camera")
        for d in self._tc.Toupcam.EnumV2():
            if d.id == camera_id:
                self._model = d.model
        self._res_index = self._cam.get_eSize() if hasattr(self._cam, "get_eSize") else 0
        try:
            self._cam.put_Option(self._tc.TOUPCAM_OPTION_BYTEORDER, 0)  # RGB (default on Linux/macOS)
            self._cam.put_Option(self._tc.TOUPCAM_OPTION_UPSIDE_DOWN, 0)
        except self._tc.HRESULTException:
            pass

    def close(self) -> None:
        self.stop()
        if self._cam is not None:
            self._cam.Close()
            self._cam = None

    @property
    def is_open(self) -> bool:
        return self._cam is not None

    def _need(self):
        if self._cam is None:
            raise CameraError("camera not open")
        return self._cam

    # ---- configuration ----
    @property
    def resolutions(self):
        if self._model is None:
            return []
        return [(r.width, r.height) for r in self._model.res]

    def set_resolution(self, index: int) -> None:
        cam = self._need()
        if self._running:
            raise CameraError("stop the camera before changing resolution")
        cam.put_eSize(index)
        self._res_index = index
        self._roi = None

    @property
    def resolution_index(self) -> int:
        return self._res_index

    def set_exposure(self, microseconds: int) -> None:
        self._need().put_ExpoTime(int(microseconds))

    def get_exposure(self) -> int:
        return self._need().get_ExpoTime()

    def exposure_range(self) -> Range:
        lo, hi, df = self._need().get_ExpTimeRange()
        return Range(lo, hi, df)

    def set_gain(self, percent: int) -> None:
        self._need().put_ExpoAGain(int(percent))

    def get_gain(self) -> int:
        return self._need().get_ExpoAGain()

    def gain_range(self) -> Range:
        lo, hi, df = self._need().get_ExpoAGainRange()
        return Range(lo, hi, df)

    def set_auto_exposure(self, on: bool) -> None:
        self._need().put_AutoExpoEnable(1 if on else 0)
        self._auto_exp = on

    def get_auto_exposure(self) -> bool:
        return bool(self._need().get_AutoExpoEnable())

    supports_auto_exposure_limit = True

    def set_auto_exposure_limit(self, microseconds: int) -> None:
        """The camera starts with auto exposure on and a 350 ms limit: a dim slide then gives 2.9 fps and a laggy picture."""
        cam = self._need()
        try:
            _longest, max_gain = cam.get_MaxAutoExpoTimeAGain()   # keep the gain limit as it is
            cam.put_MaxAutoExpoTimeAGain(int(microseconds), int(max_gain))
        except self._tc.HRESULTException as ex:
            raise CameraError(f"cannot limit auto exposure: {_hr_text(ex)}") from ex

    def set_white_balance(self, temp: int, tint: int) -> None:
        self._need().put_TempTint(int(temp), int(tint))

    def get_white_balance(self) -> tuple[int, int]:
        return self._need().get_TempTint()

    def auto_white_balance(self) -> None:
        self._need().AwbOnce()

    def set_roi(self, x: int, y: int, w: int, h: int) -> None:
        """Hardware ROI crop. The rectangle is in displayed full-frame pixels (after the flips); offsets/sizes are
        rounded down to even numbers as the SDK requires. The SDK wants it in the camera's own axes (the eyepiece view's)."""
        e = lambda v: int(v) & ~1
        full_w, full_h = self.resolutions[self._res_index]
        roi = self._roi_to_sensor(e(x), e(y), e(w), e(h), full_w, full_h)
        self._need().put_Roi(*roi)
        self._roi = roi if roi != (0, 0, full_w, full_h) else None

    @property
    def supports_16bit(self) -> bool:
        if self._model is None:
            return False
        tc = self._tc
        return bool(self._model.flag & (tc.TOUPCAM_FLAG_RAW10 | tc.TOUPCAM_FLAG_RAW12 | tc.TOUPCAM_FLAG_RAW14 | tc.TOUPCAM_FLAG_RAW16))

    def set_bit_depth(self, bits: int) -> None:
        """8 or 16. Only while stopped (the SDK returns E_WRONG_THREAD otherwise)."""
        cam = self._need()
        if self._running:
            raise CameraError("stop the camera before changing bit depth")
        if bits == 16 and not self.supports_16bit:
            raise CameraError("this camera has no >8-bit mode")
        tc = self._tc
        cam.put_Option(tc.TOUPCAM_OPTION_BITDEPTH, 1 if bits == 16 else 0)
        cam.put_Option(tc.TOUPCAM_OPTION_RGB, 1 if bits == 16 else 0)  # RGB48 vs RGB24: avoids a conversion on pull
        self._bits = bits

    # ---- streaming ----
    def _dims(self) -> tuple[int, int]:
        return self._need().get_Size()

    def _alloc(self, w: int, h: int):
        """Frame buffer. The vendor wrapper declares the argument as c_char_p, which rejects a bytearray
        (ctypes.ArgumentError on the first pull); a ctypes char array is accepted and numpy can wrap it."""
        bpp = 6 if self._bits == 16 else 3
        return ctypes.create_string_buffer(w * h * bpp)

    def start(self, on_frame, on_event=None) -> None:
        cam = self._need()
        if self._running:
            return
        self._notify, self._event = on_frame, on_event
        w, h = self._dims()
        self._buf = self._alloc(w, h)
        self._frame_ready.clear()
        try:
            cam.StartPullModeWithCallback(self._callback, self)
        except self._tc.HRESULTException as ex:
            raise CameraError(f"failed to start camera: {_hr_text(ex)}") from ex
        self._running = True

    def _restart_stream(self) -> None:
        """Stop and start the stream again with the same callbacks: what 'stop live view, start live view' does."""
        cam = self._need()
        with self._restart_lock:
            self.snap_restarts += 1
            self._running = False
            cam.Stop()
            self._frame_ready.clear()
            try:
                cam.StartPullModeWithCallback(self._callback, self)
                if self._roi is not None:
                    cam.put_Roi(*self._roi)
            except self._tc.HRESULTException as ex:
                raise CameraError(f"failed to restart the camera: {_hr_text(ex)}") from ex
            self._running = True

    def event_summary(self) -> str:
        """The SDK events seen since the last snap request, e.g. 'IMAGE x37, EXPOSURE x2' (for error messages)."""
        names = {v: k[len("TOUPCAM_EVENT_"):] for k, v in vars(self._tc).items() if k.startswith("TOUPCAM_EVENT_")}
        return ", ".join(f"{names.get(e, hex(e))} x{n}" for e, n in sorted(self._events.items())) or "none"

    @staticmethod
    def _callback(event, ctx) -> None:
        # Runs on an SDK thread. Only flag and notify; never call Stop/Close here.
        tc = ctx._tc
        ctx._events[event] = ctx._events.get(event, 0) + 1
        if event == tc.TOUPCAM_EVENT_IMAGE:
            ctx._frame_ready.set()
            n = ctx._notify
            if n:
                n()
        elif event == tc.TOUPCAM_EVENT_STILLIMAGE:
            ctx._still_ready.set()
        elif event == tc.TOUPCAM_EVENT_DISCONNECTED:
            if ctx._event:
                ctx._event("disconnected")
        elif event in (tc.TOUPCAM_EVENT_ERROR, tc.TOUPCAM_EVENT_NOFRAMETIMEOUT, tc.TOUPCAM_EVENT_NOPACKETTIMEOUT):
            if ctx._event:
                ctx._event("error")

    def stop(self) -> None:
        if self._cam is not None and self._running:
            self._running = False
            self._cam.Stop()

    @property
    def running(self) -> bool:
        return self._running

    def _to_array(self, buf, w: int, h: int, bits: int) -> np.ndarray:
        dtype = np.uint16 if bits == 16 else np.uint8
        return self._oriented(np.frombuffer(buf, dtype, count=w * h * 3).reshape(h, w, 3)).copy()   # one copy, C-ordered

    def pull(self):
        if not self._running or not self._frame_ready.is_set():
            return None
        if not self._restart_lock.acquire(blocking=False):   # the stream is being restarted: no frame now
            return None
        try:
            if not self._running:
                return None
            self._frame_ready.clear()
            info = self._tc.ToupcamFrameInfoV3()
            try:
                # rowPitch -1 = zero padding (rows are exactly width*bytes-per-pixel)
                self._cam.PullImageV3(self._buf, 0, 48 if self._bits == 16 else 24, -1, info)
            except self._tc.HRESULTException as ex:
                if _hr(ex) == E_PENDING:   # no new frame yet
                    return None
                raise CameraError(f"failed to read a frame: {_hr_text(ex)}") from ex
            return self._to_array(self._buf, info.width, info.height, self._bits)
        finally:
            self._restart_lock.release()

    def _still_timeout(self) -> float:
        try:
            return max(5.0, 2.0 * self.get_exposure() / 1e6 + 4.0)   # a long exposure delays the still
        except Exception:
            return 10.0

    def _pull_still(self) -> np.ndarray:
        cam = self._need()
        depth = 48 if self._bits == 16 else 24
        info = self._tc.ToupcamFrameInfoV3()
        try:
            cam.PullImageV3(None, 1, depth, 0, info)   # peek: dimensions only (as the vendor's demo does)
            buf = self._alloc(info.width, info.height)
            cam.PullImageV3(buf, 1, depth, -1, info)
        except self._tc.HRESULTException as ex:
            raise CameraError(f"failed to read the still image: {_hr_text(ex)}") from ex
        return self._to_array(buf, info.width, info.height, self._bits)

    def snap(self, bits: int = 8, timeout: float | None = None) -> np.ndarray:
        """Capture a still at the largest still resolution (the preview must be running).

        Round 4 on the Pi: the first snap after starting the live view worked and every later one timed out until the live
        view was stopped and started again. So the stream is restarted after each still, and once more (then asking again)
        if a still does not arrive."""
        cam = self._need()
        if not self._running:
            raise CameraError("start the camera before snapping")
        wait = self._still_timeout() if timeout is None else timeout
        for attempt in (1, 2):
            self._still_ready.clear()
            self._events.clear()
            try:
                cam.Snap(0)
            except self._tc.HRESULTException as ex:
                raise CameraError(f"snap failed: {_hr_text(ex)}") from ex
            if self._still_ready.wait(wait):
                break
            if attempt == 2:
                raise CameraError(f"timed out waiting for still image (SDK events since the request: {self.event_summary()})")
            self._restart_stream()
        arr = self._pull_still()
        if self.RESTART_AFTER_SNAP:
            self._restart_stream()
        return arr

    def metadata(self) -> dict:
        md = super().metadata()
        md["bits"] = self._bits
        md["model"] = getattr(self._model, "name", "")
        try:
            md["sensor_pixel_um"] = [self._model.xpixsz, self._model.ypixsz]
        except Exception:
            pass
        return md
