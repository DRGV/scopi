import ctypes
import os
import re
import sys
import threading
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from scopi.camera import CameraError, SimulatedCamera
from scopi.camera import touptek
from scopi.camera.base import PermissionDenied


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
    from scopi import io
    from scopi.core import ImageDoc

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


def test_simulated_auto_exposure_steers_to_the_target():
    from scopi.camera.simulated import AUTO_EXPOSURE_TARGET

    cam = SimulatedCamera()
    cam.open()
    cam.set_resolution(2)                       # 614x461: cheap frames
    cam.set_exposure(100_000)                   # far too bright (saturated)
    cam.set_auto_exposure(True)
    for _ in range(30):
        frame = cam._make()
    assert cam.get_exposure() < 100_000
    assert abs(float(frame.mean()) - AUTO_EXPOSURE_TARGET) < 25
    cam.set_auto_exposure(False)
    before = cam.get_exposure()
    cam._make()
    assert cam.get_exposure() == before         # manual exposure is left alone


def test_simulated_auto_exposure_respects_the_limit():
    cam = SimulatedCamera()
    cam.open()
    cam.set_resolution(2)
    cam.set_exposure(100_000)
    cam.set_auto_exposure_limit(3000)
    cam.set_auto_exposure(True)
    for _ in range(30):
        cam._make()
    assert cam.supports_auto_exposure_limit and cam.get_exposure() <= 3000


def _replay_source(tmp_path):
    import cv2

    img = np.random.default_rng(3).integers(0, 255, (12, 16, 3), dtype=np.uint8)
    cv2.imwrite(str(tmp_path / "a.png"), img[..., ::-1])
    return str(tmp_path)


def test_simulated_flips_mirror_previews_and_stills(tmp_path):
    cam = SimulatedCamera(source=_replay_source(tmp_path))
    cam.open()
    base = cam._make()
    assert cam.get_flip() == (False, False) and "flip" not in cam.metadata()
    for flip, expect in (((True, False), base[:, ::-1]), ((False, True), base[::-1]), ((True, True), base[::-1, ::-1])):
        cam.set_flip(*flip)
        got = cam._make()
        assert np.array_equal(got, expect) and got.flags.c_contiguous
        assert np.array_equal(cam.snap(), expect)
    assert cam.metadata()["flip"] == {"horizontal": True, "vertical": True}


def test_simulated_crop_is_chosen_on_the_displayed_orientation(tmp_path):
    cam = SimulatedCamera(source=_replay_source(tmp_path))
    cam.open()
    base = cam._make()
    cam.set_flip(True, True)
    shown = cam._make()
    cam.set_roi(2, 1, 5, 4)                       # a rectangle drawn on the flipped picture
    assert np.array_equal(cam._make(), shown[1:5, 2:7])
    cam.set_roi(0, 0, 16, 12)                     # the full frame again
    assert np.array_equal(cam._make(), shown) and np.array_equal(shown, base[::-1, ::-1])


# ------------------------------------------------------------------ fake ToupTek SDK
class FakeHR(Exception):
    def __init__(self, hr):
        self.hr = hr


def native_scene(w, h):
    """The picture the fake sensor sees (random texture, so a crop can be located in it), in the eyepiece view's orientation."""
    return np.random.default_rng(w * 1000 + h).integers(0, 256, (h, w, 3), dtype=np.uint8)


def make_fake_sdk(access_denied=False, busy_start=False, deep_bits=True, scene=False, still_events=None):
    """``scene``: frames come from ``native_scene`` the way the real U3CMOS18000KPA behaves according to round 4 -- put_Roi
    is in the eyepiece view's coordinates and the delivered frames (also the cropped ones) are upside down.
    ``still_events``: how many Snap() calls get a STILLIMAGE event (the real camera stopped after the first one until restarted)."""
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
        flag = 0x8000 if deep_bits else 0x80000000   # RAW16 / RAW8 only
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
            self.auto, self.max_auto = 1, (350000, 300)   # the real camera starts in auto exposure, limit 350 ms
            self.pulled = []
            self.stopped_from_cb = False
            self.roi = None
            self.snaps = 0
            self.restarts = 0

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
        def get_AutoExpoEnable(self): return self.auto
        def put_AutoExpoEnable(self, v): self.auto = v
        def get_MaxAutoExpoTimeAGain(self): return self.max_auto
        def put_MaxAutoExpoTimeAGain(self, t, g): self.max_auto = (t, g)
        def put_TempTint(self, t, ti): self.tt = (t, ti)
        def get_TempTint(self): return getattr(self, "tt", (6503, 1000))
        def AwbOnce(self): self.awb = True
        def put_Roi(self, *a): self.roi = a
        def get_Roi(self): return self.roi or (0, 0) + self.get_Size()

        def StartPullModeWithCallback(self, fun, ctx):
            if busy_start:
                raise FakeHR(0x800700aa)
            if self.cb is not None:
                self.restarts += 1
                self.snaps = 0            # a restarted stream delivers stills again
                self.roi = None           # and (for all the test knows) forgets the crop
            self.cb, self.ctx, self.running = fun, ctx, True

        def Stop(self):
            self.running = False

        def Close(self):
            self.closed = True

        def emit(self, ev):
            self.cb(ev, self.ctx)

        def Snap(self, idx):
            self.snap_idx = idx
            self.snaps += 1
            if still_events is None or self.snaps <= still_events:
                threading.Timer(0.02, lambda: self.emit(m.TOUPCAM_EVENT_STILLIMAGE)).start()

        def PullImageV3(self, buf, still, bits, row_pitch, info):
            w, h = self.get_Size() if not still else (Model.res[self.snap_idx].width, Model.res[self.snap_idx].height)
            info.width, info.height = w, h
            if buf is None:
                return
            ctypes.c_char_p.from_param(buf)   # the real wrapper declares c_char_p: a bytearray raises ctypes.ArgumentError
            assert row_pitch == -1, "must request zero-padded rows"
            bpp = 6 if bits == 48 else 3
            assert len(buf) >= w * h * bpp
            if scene and not still:
                full_w, full_h = self.get_Size()
                x, y, rw, rh = self.roi or (0, 0, full_w, full_h)
                frame = native_scene(full_w, full_h)[y:y + rh, x:x + rw][::-1]    # delivered upside down
                info.width, info.height = rw, rh
                np.frombuffer(buf, np.uint8, count=rw * rh * 3)[:] = frame.reshape(-1)
            elif bits == 48:
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


def test_touptek_frame_buffer_is_accepted_by_c_char_p(fake_sdk):
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    for bits in (8, 16):
        cam._bits = bits
        buf = cam._alloc(64, 48)
        ctypes.c_char_p.from_param(buf)
        assert len(buf) == 64 * 48 * (6 if bits == 16 else 3)
    with pytest.raises(TypeError):
        ctypes.c_char_p.from_param(bytearray(4))   # what the first Pi run passed


def test_touptek_pull_reports_sdk_errors_but_not_pending(monkeypatch):
    sdk = make_fake_sdk()
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    cam.start(lambda: None)
    cam._frame_ready.set()
    original = sdk.cams[0].PullImageV3

    def pending(*a):
        raise FakeHR(0x8000000A)

    sdk.cams[0].PullImageV3 = pending
    assert cam.pull() is None                      # E_PENDING: no new frame, not an error
    sdk.cams[0].PullImageV3 = original

    def broken(*a):
        raise FakeHR(0x8007001F)

    cam._frame_ready.set()
    sdk.cams[0].PullImageV3 = broken
    with pytest.raises(CameraError, match="E_GEN_FAILURE"):
        cam.pull()


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


def test_bringup_camera_reports_each_resolution_and_pull_errors(monkeypatch, capsys):
    sdk = make_fake_sdk(busy_start=True)
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    bringup = _load_bringup()
    assert bringup.cmd_camera(types.SimpleNamespace(secs=0.05)) == 1
    out = capsys.readouterr().out
    assert out.count("FAILED  failed to start camera: E_BUSY") == 2         # both fake resolutions are tried

    sdk = make_fake_sdk()
    monkeypatch.setattr(touptek, "_toupcam", sdk)

    real_open = sdk.Toupcam.Open

    class Broken(sdk.Toupcam):
        @staticmethod
        def Open(cid):
            cam = real_open(cid)
            cam.PullImageV3 = lambda *a: (_ for _ in ()).throw(FakeHR(0x8007001F))
            return cam

    sdk.Toupcam = Broken
    original_pull_ready = touptek.TouptekCamera.pull

    def pull_ready(self):
        self._frame_ready.set()          # the fake SDK never signals a frame by itself
        return original_pull_ready(self)

    monkeypatch.setattr(touptek.TouptekCamera, "pull", pull_ready)
    assert bringup.cmd_camera(types.SimpleNamespace(secs=0.05)) == 1
    assert "E_GEN_FAILURE" in capsys.readouterr().out


def test_bringup_camdiag_pulls_frames(monkeypatch, capsys):
    sdk = make_fake_sdk()
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    bringup = _load_bringup()
    assert bringup.cmd_camdiag(types.SimpleNamespace(secs=0.1, res=1, bandwidth=0)) == 0
    out = capsys.readouterr().out
    assert "[1] 32x24: start ok" in out and "pull errors none" in out and "exposure 10.0 ms (auto on)" in out
    assert sdk.cams[0].closed


def test_bringup_camdiag_can_fix_the_exposure(monkeypatch, capsys):
    sdk = make_fake_sdk()
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    assert _load_bringup().cmd_camdiag(types.SimpleNamespace(secs=0.05, res=1, bandwidth=0, exposure_ms=20.0)) == 0
    assert "exposure 20.0 ms (auto off)" in capsys.readouterr().out
    assert sdk.cams[0].auto == 0 and sdk.cams[0].expo == 20000


def test_bringup_camera_measures_with_a_fixed_exposure_or_a_limited_auto_exposure(monkeypatch, capsys):
    """The frame rate cannot beat 1/exposure: a link measurement must not run at the camera's own 350 ms auto exposure."""
    sdk = make_fake_sdk()
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    bringup = _load_bringup()
    assert bringup.cmd_camera(types.SimpleNamespace(secs=0.05, exposure_ms=10.0, auto_limit_ms=0.0)) == 0
    out = capsys.readouterr().out
    assert "exposure: fixed 10 ms" in out and "exposure 10.0 ms (fixed)" in out
    assert sdk.cams[0].auto == 0 and sdk.cams[0].expo == 10000

    assert bringup.cmd_camera(types.SimpleNamespace(secs=0.05, exposure_ms=0.0, auto_limit_ms=100.0)) == 0
    out = capsys.readouterr().out
    assert "camera auto exposure, auto limit 100 ms" in out and "(auto)" in out
    assert sdk.cams[1].auto == 1 and sdk.cams[1].max_auto == (100_000, 300)


def test_touptek_pull_and_snap(fake_sdk):
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    got = threading.Event()
    cam.start(got.set)
    assert cam.pull() is None            # nothing signalled yet
    fake_sdk.cams[0].emit(fake_sdk.TOUPCAM_EVENT_IMAGE)
    assert got.is_set()
    f = cam.pull()
    delivered = (np.arange(64 * 48 * 3) % 251).astype(np.uint8).reshape(48, 64, 3)
    assert f.shape == (48, 64, 3) and f.dtype == np.uint8
    assert np.array_equal(f, delivered[::-1])       # this camera delivers upside down; the default view is the eyepiece's
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


def test_touptek_flips_frames_and_maps_the_hardware_crop(fake_sdk):
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    cam.start(lambda: None)
    c = fake_sdk.cams[0]
    delivered = (np.arange(64 * 48 * 3) % 251).astype(np.uint8).reshape(48, 64, 3)

    def frame():
        c.emit(fake_sdk.TOUPCAM_EVENT_IMAGE)
        return cam.pull()

    assert np.array_equal(frame(), delivered[::-1])               # no flips: the eyepiece view (the camera delivers it upside down)
    cam.set_flip(True, True)                                      # user flips are relative to that view
    got = frame()
    assert np.array_equal(got, delivered[:, ::-1]) and got.flags.c_contiguous
    assert np.array_equal(cam.snap(), delivered[:, ::-1])         # stills follow the preview's orientation
    cam.set_roi(11, 7, 21, 13)                                    # odd numbers are rounded down to even first
    assert c.roi == (64 - 10 - 20, 48 - 6 - 12, 20, 12)           # the SDK wants the eyepiece view's coordinates
    cam.set_flip(False, False)
    cam.set_roi(11, 7, 21, 13)
    assert c.roi == (10, 6, 20, 12)


@pytest.mark.parametrize("flip", [(False, False), (True, False), (False, True), (True, True)])
def test_touptek_crop_shows_exactly_the_selected_rectangle(monkeypatch, flip):
    """Round 4: with Flip horizontally ticked a crop came from the vertically mirrored place. Whatever the flips, the cropped
    live view must equal that rectangle of the uncropped live view (the fake models the hardware as measured)."""
    sdk = make_fake_sdk(scene=True)
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    cam.start(lambda: None)

    def frame():
        sdk.cams[0].emit(sdk.TOUPCAM_EVENT_IMAGE)
        return cam.pull()

    cam.set_flip(*flip)
    full = frame()
    expect = native_scene(64, 48)                                 # the eyepiece view
    expect = expect[:, ::-1] if flip[0] else expect
    expect = expect[::-1] if flip[1] else expect
    assert np.array_equal(full, expect)
    x, y, w, h = 14, 6, 30, 20
    cam.set_roi(x, y, w, h)
    crop = frame()
    assert crop.shape[:2] == (h, w) and np.array_equal(crop, full[y:y + h, x:x + w])
    cam.set_roi(0, 0, 64, 48)
    assert np.array_equal(frame(), full)


def test_touptek_snap_restarts_the_stream_so_every_snap_arrives(monkeypatch):
    """Round 4: the first snap of a live view worked, every later one timed out until the live view was restarted."""
    sdk = make_fake_sdk(still_events=1)                           # the camera answers one Snap() per stream start
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    cam.start(lambda: None)
    cam.set_roi(10, 6, 20, 12)
    for _ in range(3):
        assert cam.snap(timeout=1.0).shape == (48, 64, 3)
    assert cam.snap_restarts == 3 and cam.running
    assert sdk.cams[0].roi == (10, 6, 20, 12)                     # the crop survives the restart
    cam.close()


def test_touptek_snap_retries_once_after_a_timeout(monkeypatch):
    sdk = make_fake_sdk(still_events=1)
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    monkeypatch.setattr(touptek.TouptekCamera, "RESTART_AFTER_SNAP", False)
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    cam.start(lambda: None)
    assert cam.snap(timeout=0.3).shape == (48, 64, 3) and cam.snap_restarts == 0
    assert cam.snap(timeout=0.3).shape == (48, 64, 3)             # the second one needed the restart
    assert cam.snap_restarts == 1 and cam.running
    cam.close()


def test_touptek_snap_error_says_which_events_arrived(monkeypatch):
    sdk = make_fake_sdk(still_events=0)                           # a camera that never answers
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    cam.start(lambda: None)
    sdk.cams[0].emit(sdk.TOUPCAM_EVENT_IMAGE)
    with pytest.raises(CameraError, match=r"timed out waiting for still image \(SDK events since the request: none\)"):
        cam.snap(timeout=0.1)
    assert cam.snap_restarts == 1                                 # it did restart once before giving up


def test_touptek_pull_is_skipped_while_the_stream_restarts(fake_sdk):
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    cam.start(lambda: None)
    fake_sdk.cams[0].emit(fake_sdk.TOUPCAM_EVENT_IMAGE)
    with cam._restart_lock:                                       # a snap is restarting the stream
        assert cam.pull() is None
    assert cam.pull() is not None


def test_touptek_auto_exposure_limit_keeps_the_gain_limit(fake_sdk):
    cam = touptek.TouptekCamera()
    cam.open("fake-id")
    cam.set_auto_exposure_limit(100_000)
    assert fake_sdk.cams[0].max_auto == (100_000, 300) and cam.supports_auto_exposure_limit

    def refuse(*_a):
        raise FakeHR(0x80004001)

    fake_sdk.cams[0].put_MaxAutoExpoTimeAGain = refuse
    with pytest.raises(CameraError, match=r"cannot limit auto exposure: E_NOTIMPL"):
        cam.set_auto_exposure_limit(50_000)


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
    from scopi.core import Calibration
    from scopi.ui.camera_panel import calibration_for_frame

    c = calibration_for_frame(Calibration(0.5, "um"), 4912, 1228)
    assert c.pixel_size == pytest.approx(2.0) and c.unit == "um"
    assert not calibration_for_frame(Calibration(), 4912, 1228).calibrated


# ------------------------------------------------------------------ UI integration
@pytest.fixture
def win(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from scopi.ui.main_window import MainWindow

    monkeypatch.setenv("SCOPI_PRESETS", str(tmp_path / "presets.json"))
    w = MainWindow(settings=QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(w)
    w.show()
    return w


def test_live_view_snap_and_disconnect(win, qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from scopi.camera import SimulatedCamera

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

    from scopi.core import Calibration
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


def test_exposure_applies_without_enter_and_follows_auto(win, qtbot):
    cp, ctl = win.camera_panel, win.camera
    cp.connect_btn.click()
    cp.res_combo.setCurrentIndex(2)
    ctl.backend.fps = 60
    cp.live_btn.click()
    qtbot.waitUntil(lambda: win._live_tab is not None and win._live_tab._img is not None, timeout=5000)

    cp.exp_spin.setValue(40.0)                  # no Enter, no focus change
    qtbot.waitUntil(lambda: ctl.backend.get_exposure() == 40000, timeout=3000)

    cp.auto_exp.setChecked(True)
    assert ctl.backend.get_auto_exposure() and not cp.exp_spin.isEnabled()
    qtbot.waitUntil(lambda: ctl.backend.get_exposure() != 40000, timeout=5000)   # the camera moves it
    qtbot.waitUntil(lambda: abs(cp.exp_spin.value() * 1000 - ctl.backend.get_exposure()) < 3000, timeout=5000)
    assert ctl.backend.get_auto_exposure()      # following the camera did not turn auto exposure off

    cp.auto_exp.setChecked(False)
    assert cp.exp_spin.isEnabled()


def test_pull_failure_is_reported_once(qtbot):
    from scopi.camera import SimulatedCamera
    from scopi.ui.camera_panel import CameraController

    class Broken(SimulatedCamera):
        def pull(self):
            raise CameraError("failed to read a frame: E_GEN_FAILURE")

    ctl = CameraController()
    msgs = []
    ctl.error.connect(msgs.append)
    ctl.connect_camera(Broken(), "sim-0")
    try:
        for _ in range(5):
            ctl.frame_available.emit()
        qtbot.waitUntil(lambda: bool(msgs), timeout=3000)
        qtbot.wait(200)
        assert msgs == ["failed to read a frame: E_GEN_FAILURE"]
    finally:
        ctl.disconnect_camera()


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
    from scopi import io
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


# ------------------------------------------------------------------ OpenCV (V4L2) enumeration
def test_v4l2_probes_only_existing_video_nodes_on_linux(monkeypatch):
    from scopi.camera import v4l2

    monkeypatch.setattr(v4l2.sys, "platform", "linux")
    monkeypatch.setattr(v4l2.glob, "glob", lambda pat: ["/dev/video7", "/dev/video0", "/dev/video2", "/dev/video-extra"])
    assert v4l2.V4L2Camera._candidates() == [0, 2, 7]
    monkeypatch.setattr(v4l2.glob, "glob", lambda pat: [])
    assert v4l2.V4L2Camera._candidates() == []
    assert v4l2.V4L2Camera.enumerate() == []             # nothing to probe, so OpenCV is not even loaded for it


def test_v4l2_enumerate_is_quiet_and_restores_the_log_level(monkeypatch):
    import cv2
    from scopi.camera import v4l2

    seen = []

    class Cap:
        def __init__(self, i):
            seen.append((i, cv2.utils.logging.getLogLevel()))

        def isOpened(self): return True
        def release(self): pass

    before = cv2.utils.logging.getLogLevel()
    monkeypatch.setattr(v4l2.V4L2Camera, "_candidates", classmethod(lambda cls: [3]))
    monkeypatch.setattr(cv2, "VideoCapture", Cap)
    infos = v4l2.V4L2Camera.enumerate()
    assert [i.id for i in infos] == ["3"]
    assert seen == [(3, cv2.utils.logging.LOG_LEVEL_ERROR)]      # warnings about failed probes are off while probing
    assert cv2.utils.logging.getLogLevel() == before


def test_v4l2_frames_follow_the_flips():
    from scopi.camera.v4l2 import V4L2Camera

    cam = V4L2Camera()
    cam.set_flip(True, False)
    bgr = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    out = cam._oriented(bgr[..., ::-1]).copy()
    assert np.array_equal(out, bgr[..., ::-1][:, ::-1])


# ------------------------------------------------------------------ panel: orientation, auto exposure limit, crop, bit depth
def test_flip_checkboxes_apply_persist_and_reset_the_crop(win):
    cp, ctl = win.camera_panel, win.camera
    cp.connect_btn.click()
    b = ctl.backend
    assert b.get_flip() == (False, False) and not cp.flip_h.isChecked()
    cp.set_crop(100, 100, 200, 100)
    assert cp._crop is not None and b._roi is not None

    cp.flip_h.setChecked(True)
    assert b.get_flip() == (True, False)
    assert cp._crop is None and cp.crop_btn.text() == "Full frame" and b._roi is None   # the old crop meant another region
    cp.flip_v.setChecked(True)
    assert b.get_flip() == (True, True)
    assert str(win.settings.value("camera/simulated/Simulated_camera/mirror_h")).lower() == "true"

    cp.connect_btn.click()                                  # disconnect
    assert not ctl.connected
    cp.connect_btn.click()                                  # a new connection restores the camera's saved orientation
    assert ctl.backend is not b and ctl.backend.get_flip() == (True, True)
    assert cp.flip_h.isChecked() and cp.flip_v.isChecked()
    cp.flip_h.setChecked(False)
    cp.flip_v.setChecked(False)
    assert ctl.backend.get_flip() == (False, False)


def test_auto_exposure_limit_applies_on_connect_and_when_edited(win):
    cp, ctl = win.camera_panel, win.camera
    cp.connect_btn.click()
    b = ctl.backend
    assert cp.limit_spin.isEnabled() and b._auto_limit == int(cp.AUTO_LIMIT_MS * 1000)    # capped at once: live view stays quick
    cp.limit_spin.setValue(40)
    cp.limit_spin.editingFinished.emit()
    assert b._auto_limit == 40_000
    cp.connect_btn.click()
    cp.connect_btn.click()
    assert ctl.backend._auto_limit == 40_000 and cp.limit_spin.value() == 40   # remembered for this camera


def test_a_second_crop_adds_the_first_crops_offset_and_a_new_resolution_clears_it(win):
    cp, ctl = win.camera_panel, win.camera
    cp.connect_btn.click()
    b = ctl.backend
    cp.set_crop(100, 100, 400, 300)
    assert b._roi == (100, 100, 400, 300)
    cp.set_crop(10, 20, 100, 80)                        # drawn on the cropped picture
    assert cp._crop == (110, 120, 100, 80) and b._roi == (110, 120, 100, 80)
    cp.res_combo.setCurrentIndex(2)
    assert cp._crop is None and b._roi is None and cp.crop_btn.text() == "Full frame"


def test_a_camera_without_a_16bit_mode_says_so(win):
    from scopi.camera import SimulatedCamera

    class EightBit(SimulatedCamera):
        supports_16bit = False

    cp, ctl = win.camera_panel, win.camera
    ctl.connect_camera(EightBit(), "sim-0")
    assert [cp.bits_combo.itemText(i) for i in range(cp.bits_combo.count())] == ["8-bit only"]
    assert not cp.bits_combo.isEnabled() and "8 bits" in cp.bits_combo.toolTip()
    assert cp._bits() == 8
    ctl.disconnect_camera()
    cp.connect_btn.click()                              # a 16-bit capable camera gets both choices back
    assert [cp.bits_combo.itemText(i) for i in range(cp.bits_combo.count())] == ["8-bit", "16-bit"]
    assert cp.bits_combo.isEnabled()


def test_live_view_overlay_shows_the_exposure_that_limits_the_frame_rate(win, qtbot):
    cp, ctl = win.camera_panel, win.camera
    cp.connect_btn.click()
    cp.res_combo.setCurrentIndex(2)
    ctl.backend.fps = 60
    cp.live_btn.click()
    qtbot.waitUntil(lambda: win._live_tab is not None and win._live_tab._img is not None, timeout=5000)
    ctl.backend.set_exposure(350_000)
    live = win._live_tab
    live._exp_shown = (0.0, "")                          # force a fresh read
    assert "614 x 461" in live.overlay_text() and "exposure 350 ms" in live.overlay_text()
    live._exp_shown = (0.0, "")
    ctl.backend.set_auto_exposure(True)
    assert "(auto)" in live.overlay_text()


# ------------------------------------------------------------------ bringup diagnostics: roitest, snaptest
def _signal_frames(monkeypatch):
    original = touptek.TouptekCamera.pull

    def pull_ready(self):
        self._frame_ready.set()          # the fake SDK never signals a frame by itself
        return original(self)

    monkeypatch.setattr(touptek.TouptekCamera, "pull", pull_ready)


def test_locate_crop_finds_the_place_and_the_mirroring():
    bringup = _load_bringup()
    full = np.random.default_rng(3).integers(0, 256, (90, 120, 3), dtype=np.uint8)
    crop = full[10:40, 70:100]
    assert bringup.locate_crop(full, crop)[0:1] + bringup.locate_crop(full, crop)[2:] == ("no", 70, 10)
    mirrored, score, x, y = bringup.locate_crop(full, crop[::-1])
    assert (mirrored, x, y) == ("vertically", 70, 10) and score > 0.99
    assert bringup.locate_crop(full, crop[:, ::-1])[0] == "horizontally"
    flat = bringup.locate_crop(np.full((90, 120, 3), 7, np.uint8), np.full((30, 30, 3), 7, np.uint8))
    assert flat[1] < 0.6                                           # no texture: no reliable match, never a false verdict


def test_axis_verdict_tells_mirrored_from_shifted():
    bringup = _load_bringup()
    assert bringup.axis_verdict(100, 102, 300, 922, tol=4) == "as requested"
    assert bringup.axis_verdict(100, 922 - 100 - 300, 300, 922, tol=4).startswith("MIRRORED")
    assert bringup.axis_verdict(100, 250, 300, 922, tol=4).startswith("OFFSET by +150")


def test_bringup_roitest_measures_a_mirrored_vertical_axis(monkeypatch, capsys):
    """The fake models the camera as measured in round 4: the hardware crop's vertical axis is mirrored against the delivered frame."""
    sdk = make_fake_sdk(scene=True)
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    _signal_frames(monkeypatch)
    assert _load_bringup().cmd_roitest(types.SimpleNamespace(res=None, exposure_ms=10.0)) == 0
    out = capsys.readouterr().out
    assert out.count("horizontal position: as requested") == 2 and out.count("vertical position: MIRRORED") == 2
    assert "crop content mirrored against the full frame: no" in out
    assert "RESULT: horizontal ['as'] vertical ['MIRRORED']" in out
    assert sdk.cams[0].roi == (0, 0, 64, 48)                               # left at the full frame
    assert sdk.cams[0].closed


def test_bringup_roitest_says_when_there_is_nothing_to_match(monkeypatch, capsys):
    sdk = make_fake_sdk()                                                    # the plain test pattern repeats every 251 bytes: no unique match
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    _signal_frames(monkeypatch)
    monkeypatch.setattr(sdk.Toupcam, "Open", staticmethod(lambda cid: _flat_cam(sdk)))
    rc = _load_bringup().cmd_roitest(types.SimpleNamespace(res=0, exposure_ms=10.0))
    assert rc == 1 and "no reliable match" in capsys.readouterr().out


def _flat_cam(sdk):
    class Flat:
        """A camera looking at a blank wall."""
        def __init__(self):
            self.size, self.roi, self.closed = (64, 48), None, False
        def get_eSize(self): return 0
        def put_eSize(self, i): self.size = ((64, 48), (32, 24))[i]
        def get_Size(self): return self.size
        def put_Option(self, *a): pass
        def put_AutoExpoEnable(self, v): pass
        def put_ExpoTime(self, v): pass
        def get_ExpoTime(self): return 10000
        def put_Roi(self, *a): self.roi = a
        def get_Roi(self): return self.roi
        def StartPullModeWithCallback(self, fun, ctx): self.cb, self.ctx = fun, ctx
        def Stop(self): pass
        def Close(self): self.closed = True
        def PullImageV3(self, buf, still, bits, row_pitch, info):
            x, y, w, h = self.roi or (0, 0) + self.size
            info.width, info.height = w, h
            np.frombuffer(buf, np.uint8, count=w * h * 3)[:] = 90
    return Flat()


def test_bringup_snaptest_reports_every_snap(monkeypatch, capsys):
    sdk = make_fake_sdk(still_events=1)
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    _signal_frames(monkeypatch)
    bringup = _load_bringup()
    monkeypatch.setattr(bringup, "LIVE_AFTER_SNAP_S", 0.05)
    assert bringup.cmd_snaptest(types.SimpleNamespace(count=3, res=None, exposure_ms=10.0, no_restart=False)) == 0
    out = capsys.readouterr().out
    assert "restart after each snap: True" in out
    assert [l for l in out.splitlines() if l.startswith("snap ")].__len__() == 3 and "stream restarts 1" in out and "FAILED" not in out


def test_bringup_snaptest_without_restart_shows_the_sdk_behaviour(monkeypatch, capsys):
    sdk = make_fake_sdk(still_events=1)
    monkeypatch.setattr(touptek, "_toupcam", sdk)
    _signal_frames(monkeypatch)
    monkeypatch.setattr(touptek.TouptekCamera, "_still_timeout", lambda self: 0.2)
    bringup = _load_bringup()
    monkeypatch.setattr(bringup, "LIVE_AFTER_SNAP_S", 0.05)
    assert bringup.cmd_snaptest(types.SimpleNamespace(count=2, res=None, exposure_ms=10.0, no_restart=True)) == 0
    out = capsys.readouterr().out
    assert "restart after each snap: False" in out
    assert "snap 1: 64x48" in out and "stream restarts 0" in out and "snap 2: 64x48" in out and "stream restarts 1" in out
