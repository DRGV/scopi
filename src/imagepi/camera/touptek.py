"""ToupTek / OMAX camera backend (OMAX A35180U3 and other ToupTek USB cameras).

Wraps the vendor's single-file ctypes wrapper ``toupcam.py`` which is *not* shipped with ImagePi
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
    env = os.environ.get("IMAGEPI_TOUPCAM_DIR")
    if env:
        dirs.append(Path(env))
    dirs.append(Path(__file__).resolve().parents[3] / "vendor" / "toupcam")
    dirs.append(Path.home() / ".imagepi" / "vendor" / "toupcam")
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
    0x800700AA: ("E_BUSY", "camera already in use (another ImagePi window or program?) or the USB link could not be set up"),
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
        self.dropped = 0

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

    def set_white_balance(self, temp: int, tint: int) -> None:
        self._need().put_TempTint(int(temp), int(tint))

    def get_white_balance(self) -> tuple[int, int]:
        return self._need().get_TempTint()

    def auto_white_balance(self) -> None:
        self._need().AwbOnce()

    def set_roi(self, x: int, y: int, w: int, h: int) -> None:
        """Hardware ROI crop; offsets/sizes are rounded down to even numbers as the SDK requires."""
        e = lambda v: int(v) & ~1
        self._need().put_Roi(e(x), e(y), e(w), e(h))

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

    @staticmethod
    def _callback(event, ctx) -> None:
        # Runs on an SDK thread. Only flag and notify; never call Stop/Close here.
        tc = ctx._tc
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
        if bits == 16:
            return np.frombuffer(buf, np.uint16, count=w * h * 3).reshape(h, w, 3).copy()
        return np.frombuffer(buf, np.uint8, count=w * h * 3).reshape(h, w, 3).copy()

    def pull(self):
        if not self._running or not self._frame_ready.is_set():
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

    def snap(self, bits: int = 8, timeout: float = 10.0) -> np.ndarray:
        """Capture a still at the largest still resolution (the preview must be running)."""
        cam = self._need()
        if not self._running:
            raise CameraError("start the camera before snapping")
        self._still_ready.clear()
        try:
            cam.Snap(0)
        except self._tc.HRESULTException as ex:
            raise CameraError(f"snap failed: {_hr_text(ex)}") from ex
        if not self._still_ready.wait(timeout):
            raise CameraError("timed out waiting for still image")
        info = self._tc.ToupcamFrameInfoV3()
        cam.PullImageV3(None, 1, 0, 0, info)  # peek: dimensions only
        buf = self._alloc(info.width, info.height)
        cam.PullImageV3(buf, 1, 48 if self._bits == 16 else 24, -1, info)
        return self._to_array(buf, info.width, info.height, self._bits)

    def metadata(self) -> dict:
        md = super().metadata()
        md["bits"] = self._bits
        md["model"] = getattr(self._model, "name", "")
        try:
            md["sensor_pixel_um"] = [self._model.xpixsz, self._model.ypixsz]
        except Exception:
            pass
        return md
