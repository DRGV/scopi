import os
import re
import sys
import threading
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from imagepi.camera import CameraError, SimulatedCamera
from imagepi.camera import touptek
from imagepi.camera.base import PermissionDenied


def wait_for(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.01)
    return False


# ------------------------------------------------------------------ simulated
def test_simulated_stream_and_drop():
    cam = SimulatedCamera(fps=100)
    cam.open()
    ev = threading.Event()
    cam.start(ev.set)
    assert ev.wait(2) and cam.running
    f = cam.pull()
    assert f.shape == (922, 1228, 3) and f.dtype == np.uint8
    assert wait_for(lambda: cam.frame_count > 3)
    assert cam.pull() is not None and cam.pull() is None   # newest frame only; none until the next arrives
    cam.close()
    assert not cam.running and not cam.is_open


def test_simulated_controls_affect_image():
    cam = SimulatedCamera()
    cam.open()
    cam.set_exposure(2000)
    dark = cam.snap().mean()
    cam.set_exposure(10000)
    bright = cam.snap().mean()
    assert bright > dark
    cam.set_gain(300)
    assert cam.get_gain() == 300
    assert cam.snap(16).dtype == np.uint16
    assert cam.snap().shape == (1842, 2456, 3)
    md = cam.metadata()
    assert md["exposure_us"] == 10000 and md["gain_percent"] == 300


def test_simulated_resolution_only_when_stopped_and_roi():
    cam = SimulatedCamera(fps=50)
    cam.open()
    cam.set_resolution(2)
    assert cam.resolution_index == 2
    cam.start(lambda: None)
    with pytest.raises(CameraError):
        cam.set_resolution(0)
    cam.stop()
    cam.set_resolution(1)
    cam.set_roi(100, 100, 200, 150)
    assert cam.snap().shape[:2] == (300, 400)    # still is full-res: ROI scaled 2x from the 1228-wide preview
    cam.set_roi(0, 0, 1228, 922)
    assert cam._roi is None
    with pytest.raises(CameraError):
        SimulatedCamera().snap()


def test_simulated_replay_folder(tmp_path):
    from imagepi import io
    from imagepi.core import ImageDoc

    for i in range(3):
        io.save_image(ImageDoc(np.full((20, 30), i * 50, np.uint8)), str(tmp_path / f"{i}.png"))
    cam = SimulatedCamera(str(tmp_path))
    cam.open()
    assert cam.resolutions == [(30, 20)]
    vals = [int(cam.snap()[0, 0, 0]) for _ in range(4)]
    assert vals == [0, 50, 100, 0]
    (tmp_path / "empty").mkdir()
    with pytest.raises(CameraError):
        SimulatedCamera(str(tmp_path / "empty")).open()


def test_simulated_unplug_event():
    cam = SimulatedCamera(fps=50)
    cam.open()
    events = []
    cam.start(lambda: None, events.append)
    cam.unplug()
    assert events == ["disconnected"] and not cam.is_open


# ------------------------------------------------------------------ fake ToupTek SDK
class FakeHR(Exception):
    def __init__(self, hr):
        self.hr = hr


def make_fake_sdk(access_denied=False, busy_start=False):
    m = types.SimpleNamespace()
    m.TOUPCAM_EVENT_IMAGE, m.TOUPCAM_EVENT_STILLIMAGE, m.TOUPCAM_EVENT_DISCONNECTED = 4, 5, 0x81
    m.TOUPCAM_EVENT_ERROR, m.TOUPCAM_EVENT_NOFRAMETIMEOUT, m.TOUPCAM_EVENT_NOPACKETTIMEOUT = 0x80, 0x82, 0x85
    m.TOUPCAM_OPTION_BYTEORDER, m.TOUPCAM_OPTION_UPSIDE_DOWN, m.TOUPCAM_OPTION_BITDEPTH, m.TOUPCAM_OPTION_RGB = 0x2a, 0x23, 6, 0xc
    m.TOUPCAM_FLAG_RAW10 = m.TOUPCAM_FLAG_RAW12 = m.TOUPCAM_FLAG_RAW14 = 0
    m.TOUPCAM_FLAG_RAW16 = 0x8000
    m.HRESULTException = FakeHR
    m.TOUPCAM_OPTION_BANDWIDTH = 0x2e

    class Info:
        width = height = 0

    m.ToupcamFrameInfoV3 = Info

    class Res:
        def __init__(self, w, h):
            self.width, self.height = w, h

    class Model:
        name = "FakeCam"
        flag = 0x8000
        xpixsz = ypixsz = 1.4
        res = [Res(64, 48), Res(32, 24)]

    class Dev:
        displayname, id, model = "Fake OMAX", "fake-id", Model()

    class Cam:
        def __init__(self):
            self.size_idx = 0
            self.cb = None
            self.running = False
            self.expo, self.gain, self.opts = 10000, 100, {}
            self.pulled = []
            self.stopped_from_cb = False

        def get_eSize(self): return self.size_idx
        def put_eSize(self, i): self.size_idx = i
        def get_Size(self): r = Model.res[self.size_idx]; return (r.width, r.height)
        def put_Option(self, o, v): self.opts[o] = v
        def get_ExpoTime(self): return self.expo
        def put_ExpoTime(self, v): self.expo = v
        def get_ExpTimeRange(self): return (100, 1000000, 10000)
        def get_ExpoAGain(self): return self.gain
        def put_ExpoAGain(self, v): self.gain = v
        def get_ExpoAGainRange(self): return (100, 500, 100)
        def get_AutoExpoEnable(self): return 0
        def put_AutoExpoEnable(self, v): pass
        def put_TempTint(self, t, ti): self.tt = (t, ti)
        def get_TempTint(self): return getattr(self, "tt", (6503, 1000))
        def AwbOnce(self): self.awb = True
        def put_Roi(self, *a): self.roi = a

        def StartPullModeWithCallback(self, fun, ctx):
            if busy_start:
                raise FakeHR(0x800700aa)
            self.cb, self.ctx, self.running = fun, ctx, True

        def Stop(self):
            self.running = False

        def Close(self):
            self.closed = True

        def emit(self, ev):
            self.cb(ev, self.ctx)

        def Snap(self, idx):
            self.snap_idx = idx
            threading.Timer(0.02, lambda: self.emit(m.TOUPCAM_EVENT_STILLIMAGE)).start()

        def PullImageV3(self, buf, still, bits, row_pitch, info):
            w, h = self.get_Size() if not still else (Model.res[self.snap_idx].width, Model.res[self.snap_idx].height)
            info.width, info.height = w, h
            if buf is None:
                return
            assert row_pitch == -1, "must request zero-padded rows"
            bpp = 6 if bits == 48 else 3
            assert len(buf) >= w * h * bpp
            if bits == 48:
                arr = np.frombuffer(buf, np.uint16, count=w * h * 3)
                arr[:] = 40000
            else:
                arr = np.frombuffer(buf, np.uint8, count=w * h * 3)
                arr[:] = np.arange(w * h * 3) % 251

    cams = []

    class Toupcam:
        @staticmethod
        def Version(): return "60.0.fake"

        @staticmethod
        def EnumV2(): return [Dev()]

        @staticmethod
        def Open(cid):
            if access_denied:
                raise FakeHR(0x80070005)
            c = Cam()
            cams.append(c)
            return c

    m.Toupcam = Toupcam
    m.cams = cams
    return m


@pytest.fixture
def fake_sdk(monkeypatch):
    sdk = make_fake_sdk()
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    return sdk


def test_touptek_enumerate_and_open(fake_sdk):
    infos = touptek.TouptekCamera.enumerate()
    assert infos[0].name == "Fake OMAX" and infos[0].resolutions == [(64, 48), (32, 24)]
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    assert cam.resolutions == [(64, 48), (32, 24)] and cam.resolution_index == 0
    assert fake_sdk.cams[0].opts[0x2a] == 0          # RGB byte order requested
    assert cam.exposure_range().max == 1000000
    cam.set_exposure(5000); cam.set_gain(200); cam.set_white_balance(5000, 900)
    assert (cam.get_exposure(), cam.get_gain(), cam.get_white_balance()) == (5000, 200, (5000, 900))
    cam.auto_white_balance(); cam.set_roi(3, 5, 31, 21)
    assert fake_sdk.cams[0].roi == (2, 4, 30, 20)    # SDK requires even values
    assert cam.supports_16bit
    cam.close()


def test_touptek_permission_denied(monkeypatch):
    monkeypatch.setattr(touptek, "_toupcam", make_fake_sdk(access_denied=True))
    with pytest.raises(PermissionDenied, match="udev"):
        touptek.TouptekCamera().open("fake-id")


def test_touptek_start_busy_names_the_error(monkeypatch):
    monkeypatch.setattr(touptek, "_toupcam", make_fake_sdk(busy_start=True))
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    with pytest.raises(CameraError, match=r"E_BUSY \(0x800700aa\).*already in use"):
        cam.start(lambda: None)
    assert not cam.running


def _load_bringup():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("pi_bringup", Path(__file__).resolve().parents[1] / "scripts" / "pi_bringup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bringup_camdiag_reports_start_failure_per_resolution(monkeypatch, capsys):
    monkeypatch.setattr(touptek, "_toupcam", make_fake_sdk(busy_start=True))
    bringup = _load_bringup()
    a = types.SimpleNamespace(secs=0.05, res=None, bandwidth=50)
    assert bringup.cmd_camdiag(a) == 1
    out = capsys.readouterr().out
    assert "SDK version: 60.0.fake" in out and "Open: ok" in out
    assert out.count("start FAILED  E_BUSY (0x800700aa)") == 2    # both fake resolutions, none aborts the run


def test_bringup_camdiag_pulls_frames(monkeypatch, capsys):
    sdk = make_fake_sdk()
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    bringup = _load_bringup()
    assert bringup.cmd_camdiag(types.SimpleNamespace(secs=0.1, res=1, bandwidth=0)) == 0
    out = capsys.readouterr().out
    assert "[1] 32x24: start ok" in out and "pull errors none" in out
    assert sdk.cams[0].closed


def test_touptek_pull_and_snap(fake_sdk):
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    got = threading.Event()
    cam.start(got.set)
    assert cam.pull() is None            # nothing signalled yet
    fake_sdk.cams[0].emit(fake_sdk.TOUPCAM_EVENT_IMAGE)
    assert got.is_set()
    f = cam.pull()
    assert f.shape == (48, 64, 3) and f.dtype == np.uint8 and f[0, 0, 1] == 1
    assert cam.pull() is None            # flag cleared: frames are dropped, not replayed
    s = cam.snap()
    assert s.shape == (48, 64, 3)
    with pytest.raises(CameraError):
        cam.set_resolution(1)            # must stop first
    cam.stop()
    cam.set_resolution(1)
    cam.set_bit_depth(16)
    cam.start(lambda: None)
    s16 = cam.snap(16)
    assert s16.dtype == np.uint16 and s16.shape == (48, 64, 3) and s16[0, 0, 0] == 40000   # still at index 0
    md = cam.metadata()
    assert md["bits"] == 16 and md["model"] == "FakeCam" and md["width"] == 32   # preview size
    cam.close()


def test_touptek_events_dispatch(fake_sdk):
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    events = []
    cam.start(lambda: None, events.append)
    c = fake_sdk.cams[0]
    c.emit(fake_sdk.TOUPCAM_EVENT_DISCONNECTED)
    c.emit(fake_sdk.TOUPCAM_EVENT_NOFRAMETIMEOUT)
    assert events == ["disconnected", "error"]
    assert not c.stopped_from_cb and c.running   # callback never stops/closes the camera itself


def test_touptek_snap_requires_running(fake_sdk):
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    with pytest.raises(CameraError):
        cam.snap()
    with pytest.raises(CameraError):
        touptek.TouptekCamera().get_gain()


def test_sdk_missing_message(monkeypatch, tmp_path):
    monkeypatch.setattr(touptek, "_toupcam", None)
    monkeypatch.setattr(touptek, "_vendor_dirs", lambda: [tmp_path])
    with pytest.raises(CameraError, match="install_toupcam"):
        touptek.load_sdk()


@pytest.mark.skipif(not os.path.exists(os.path.join(os.path.dirname(__file__), "..", "vendor", "toupcam", "python", "toupcam.py")),
                    reason="SDK not installed in vendor/toupcam")
def test_real_sdk_loads():
    """The vendor wrapper and native library load on this platform (no camera needed)."""
    monkey = touptek._toupcam
    touptek._toupcam = None
    try:
        tc = touptek.load_sdk()
        version = tc.Toupcam.Version()
        version = version.decode() if isinstance(version, bytes) else str(version)
        assert re.match(r"\d+\.\d+", version), version  # any SDK build (2023 was 54.x, 2026 is 59.x or later)
    finally:
        touptek._toupcam = monkey


def test_calibration_scales_with_resolution():
    from imagepi.core import Calibration
    from imagepi.ui.camera_panel import calibration_for_frame

    c = calibration_for_frame(Calibration(0.5, "um"), 4912, 1228)
    assert c.pixel_size == pytest.approx(2.0) and c.unit == "um"
    assert not calibration_for_frame(Calibration(), 4912, 1228).calibrated


# ------------------------------------------------------------------ UI integration
@pytest.fixture
def win(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from imagepi.ui.main_window import MainWindow

    monkeypatch.setenv("IMAGEPI_PRESETS", str(tmp_path / "presets.json"))
    w = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(w)
    w.show()
    return w


def test_live_view_snap_and_disconnect(win, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from imagepi.camera import SimulatedCamera

    cp, ctl = win.camera_panel, win.camera
    cp.connect_btn.click()
    assert ctl.connected and cp.live_btn.isEnabled()
    cp.res_combo.setCurrentIndex(2)           # smaller preview for speed
    assert ctl.backend.resolution_index == 2
    ctl.backend.fps = 60
    cp.live_btn.click()
    assert win._live_tab is not None and ctl.live
    qtbot.waitUntil(lambda: win._live_tab._img is not None, timeout=5000)
    assert win._live_tab._img.width() == 614
    qtbot.waitUntil(lambda: ctl.fps > 0 or ctl._count > 3, timeout=5000)
    assert not win._live_tab.grab().toImage().isNull()

    cp.exp_spin.setValue(20.0); cp.exp_spin.editingFinished.emit()
    assert ctl.backend.get_exposure() == 20000
    cp.gain_slider.setValue(250)
    assert ctl.backend.get_gain() == 250
    cp.auto_exp.setChecked(True)
    assert ctl.backend.get_auto_exposure()
    cp.bits_combo.setCurrentIndex(1)           # reconfigure restarts live view
    assert ctl.live and ctl.backend._bits == 16
    cp.bits_combo.setCurrentIndex(0)

    from imagepi.core import Calibration
    cp.presets.add("10x", Calibration(0.5, "um"))
    cp._reload_presets()
    cp.preset_combo.setCurrentText("10x")
    n = win.tabs.count()
    cp.snap_btn.click()
    qtbot.waitUntil(lambda: win.tabs.count() == n + 1, timeout=15000)
    doc = win.current_doc()
    assert doc.width == 2456 and doc.calibration.pixel_size == pytest.approx(0.5)
    assert doc.metadata["width"] == 2456
    assert doc.metadata["camera"] == "simulated" and doc.metadata["objective"] == "10x" and "timestamp" in doc.metadata

    # crop via live-view drag
    cp.set_crop(100, 100, 200, 100)
    assert "Crop" in cp.crop_btn.text()
    cp.crop_btn.click()
    assert cp.crop_btn.text() == "Full frame"

    msgs = []
    ctl.error.connect(msgs.append)
    ctl.backend.unplug()
    qtbot.waitUntil(lambda: not ctl.connected, timeout=5000)
    assert msgs and "disconnected" in msgs[-1]
    assert win._live_tab is None


def test_timelapse(win, qtbot, tmp_path):
    cp, ctl = win.camera_panel, win.camera
    cp.connect_btn.click()
    ctl.backend.set_resolution(2)
    cp.tl_folder.setText(str(tmp_path / "tl"))
    cp.tl_interval.setValue(0.1)
    cp.tl_count.setValue(3)
    cp.live_btn.click()
    cp._toggle_timelapse()
    qtbot.waitUntil(lambda: ctl._timelapse is None, timeout=30000)
    files = sorted((tmp_path / "tl").glob("*.tif"))
    assert len(files) == 3
    from imagepi import io
    d = io.open_image(str(files[0]))
    assert d.metadata["timelapse_index"] == 0 and d.rgb
    cp.connect_btn.click()
    assert not ctl.connected


@pytest.mark.parametrize("layout", ["new", "old"])
def test_load_sdk_finds_native_lib_in_either_layout(monkeypatch, tmp_path, layout):
    cands, libname = touptek._native_candidates()
    sub = cands[0] if layout == "new" else cands[-1]
    (tmp_path / "python").mkdir()
    (tmp_path / "python" / "toupcam.py").write_text("MARKER = 1\n")
    (tmp_path / sub).mkdir(parents=True)
    (tmp_path / sub / libname).write_bytes(b"lib")
    monkeypatch.setattr(touptek, "_toupcam", None)
    monkeypatch.setattr(touptek, "_vendor_dirs", lambda: [tmp_path])
    try:
        assert touptek.load_sdk().MARKER == 1
        assert (tmp_path / "python" / libname).read_bytes() == b"lib"
    finally:
        sys.modules.pop("toupcam", None)
        monkeypatch.setattr(touptek, "_toupcam", None)


def test_install_script_handles_folder_and_zip(tmp_path):
    import subprocess, zipfile
    src = tmp_path / "sdk"
    for rel in ("python/toupcam.py", "inc/toupcam.h", "linux/arm64/glibc/libtoupcam.so", "linux/arm64/musl/libtoupcam.so",
                "mac/libtoupcam.dylib", "win/x64/toupcam.dll", "linux/udev/99-toupcam.rules"):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(rel)
    script = os.path.join(os.path.dirname(__file__), "..", "scripts", "install_toupcam.py")
    out = tmp_path / "out"
    subprocess.run([sys.executable, script, str(src), "--dest", str(out)], check=True, capture_output=True)
    got = {str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()}
    assert "linux/arm64/glibc/libtoupcam.so" in got and "mac/libtoupcam.dylib" in got
    assert not any("musl" in g or g.startswith("win") for g in got)
    z = tmp_path / "sdk.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src))
    out2 = tmp_path / "out2"
    subprocess.run([sys.executable, script, str(z), "--dest", str(out2)], check=True, capture_output=True)
    assert (out2 / "python" / "toupcam.py").exists()
