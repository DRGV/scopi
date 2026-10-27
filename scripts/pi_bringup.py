#!/usr/bin/env python3
"""Milestone 0 spike: verify the Pi 5 stack and measure what the plan lists as unverified risks.

    python scripts/pi_bringup.py check            # wheels import, glibc, Qt platform plugins
    python scripts/pi_bringup.py render           # time 18 MP pan/zoom through the real canvas (needs a display)
    python scripts/pi_bringup.py camera [--secs 5] [--exposure-ms 10 | --exposure-ms 0 --auto-limit-ms 100]
                                                   # lsusb, enumerate, fps + USB throughput per resolution (fixed short exposure by default)
    python scripts/pi_bringup.py camdiag [--res N] [--bandwidth %] [--exposure-ms MS] [--secs 3]
                                                   # the vendor SDK only (no ImagePi camera code): USB state, flags, start/pull per resolution
    python scripts/pi_bringup.py roitest [--res N] [--exposure-ms 10]
                                                   # where does a hardware crop land? compares the cropped frame with the full frame
    python scripts/pi_bringup.py snaptest [--count 4] [--res N] [--no-restart]
                                                   # several snaps in a row through ImagePi's camera code: does every one arrive?

Run `render` under Wayland (QT_QPA_PLATFORM=wayland) on the Pi; use QT_QPA_PLATFORM=offscreen elsewhere.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import time


def cmd_check(_a) -> int:
    print(f"python {platform.python_version()} on {platform.machine()} {platform.system()} {platform.release()}")
    try:
        print("glibc", platform.libc_ver())
    except Exception:
        pass
    ok = True
    for name in ("numpy", "scipy", "skimage", "cv2", "PIL", "tifffile", "imageio", "pandas", "matplotlib", "PySide6"):
        try:
            m = __import__(name)
            print(f"  ok   {name:<12s} {getattr(m, '__version__', '')}")
        except Exception as e:
            ok = False
            print(f"  FAIL {name:<12s} {e}")
    try:
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance() or QGuiApplication(sys.argv)
        print("  Qt platform:", app.platformName())
    except Exception as e:
        ok = False
        print("  FAIL Qt:", e)
    return 0 if ok else 1


def cmd_render(a) -> int:
    import os

    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.wayland.textinput.warning=false")   # harmless focus noise (the app silences it too)
    import numpy as np
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication

    from imagepi.core import ImageDoc
    from imagepi.ui.canvas import ImageCanvas

    app = QApplication(sys.argv)
    rng = np.random.default_rng(0)
    img = (rng.random((3684, 4912), dtype=np.float32) * 255).astype(np.uint8)
    doc = ImageDoc(img[None], "18MP")
    c = ImageCanvas(doc)
    c.resize(1280, 720)
    c.show()
    app.processEvents()

    def frame_times(label, step, n=60):
        ts = []
        for i in range(n):
            t0 = time.perf_counter()
            step(i)
            c.viewport().repaint()
            app.processEvents()
            ts.append((time.perf_counter() - t0) * 1000)
        ts.sort()
        print(f"{label:<28s} median {ts[len(ts) // 2]:6.1f} ms  p95 {ts[int(len(ts) * 0.95)]:6.1f} ms  (~{1000 / ts[len(ts) // 2]:.0f} fps)")

    c.fit()
    frame_times("repaint at fit zoom", lambda i: None)
    c.set_scale(1.0)
    frame_times("pan at 100%", lambda i: c.centerOn(QPointF(1500 + i * 30, 1500 + i * 10)))
    frame_times("zoom sweep 5%..400%", lambda i: c.set_scale(0.05 * (1.1 ** i)), n=50)
    return 0


def cmd_camera(a) -> int:
    if shutil.which("lsusb"):
        out = subprocess.run(["lsusb"], capture_output=True, text=True).stdout
        print(out.strip())
        print("ToupTek/OMAX (0547) lines:", [l for l in out.splitlines() if "0547" in l] or "none found")
    from imagepi.camera.base import CameraError
    from imagepi.camera.touptek import TouptekCamera

    try:
        infos = TouptekCamera.enumerate()
    except CameraError as e:
        print(e)
        return 1
    if not infos:
        print("no ToupTek camera found")
        return 1
    info = infos[0]
    print("found:", info.name, info.resolutions, "pixel size (um):", info.pixel_size_um)
    cam = TouptekCamera()
    cam.open(info.id)
    failed = 0
    exposure_ms = getattr(a, "exposure_ms", 0.0)
    auto_limit_ms = getattr(a, "auto_limit_ms", 0.0)
    try:
        if exposure_ms:
            # The frame rate can never beat 1 / exposure time, and the camera starts in auto exposure (up to 350 ms): measure
            # the USB link with a short fixed exposure, not the exposure the auto mode happened to choose.
            cam.set_auto_exposure(False)
            cam.set_exposure(int(exposure_ms * 1000))
        elif auto_limit_ms:
            cam.set_auto_exposure_limit(int(auto_limit_ms * 1000))
        print(f"exposure: {'fixed ' + format(exposure_ms, 'g') + ' ms' if exposure_ms else 'camera auto exposure'}"
              + (f", auto limit {auto_limit_ms:g} ms" if auto_limit_ms and not exposure_ms else ""))
        for idx, (w, h) in enumerate(cam.resolutions):
            try:
                cam.set_resolution(idx)
                cam.start(lambda: None)
            except CameraError as e:
                failed += 1
                print(f"{w}x{h}: FAILED  {e}")
                continue
            t0 = time.time()
            frames = 0
            errors: set[str] = set()
            while time.time() - t0 < a.secs:
                try:
                    f = cam.pull()
                except CameraError as e:
                    errors.add(str(e))
                    f = None
                if f is not None:
                    frames += 1
                else:
                    time.sleep(0.001)
            dt = time.time() - t0
            try:
                chosen = f", exposure {cam.get_exposure() / 1000:.1f} ms ({'auto' if cam.get_auto_exposure() else 'fixed'})"
            except Exception:
                chosen = ""
            cam.stop()
            mb = frames * w * h * 3 / 1e6
            print(f"{w}x{h}: {frames / dt:5.1f} fps, {mb / dt:6.1f} MB/s decoded RGB24{chosen}" + (f"   errors: {sorted(errors)}" if errors else ""))
            if errors and not frames:
                failed += 1
    finally:
        cam.close()
    return 1 if failed else 0


def cmd_camdiag(a) -> int:
    """Use the vendor SDK directly, step by step, so a failure can be pinned on the camera/USB/SDK rather than on ImagePi."""
    import ctypes
    import os

    from imagepi.camera.base import CameraError
    from imagepi.camera.touptek import _hr, _hr_text, load_sdk

    try:
        print("usbfs_memory_mb:", open("/sys/module/usbcore/parameters/usbfs_memory_mb").read().strip())
    except OSError:
        print("usbfs_memory_mb: n/a")
    for cmd in (["lsusb", "-t"],):
        if shutil.which(cmd[0]):
            print("$", " ".join(cmd))
            print(subprocess.run(cmd, capture_output=True, text=True).stdout.rstrip())
    if shutil.which("pgrep"):
        mine = {str(os.getpid()), str(os.getppid())}   # this process and the shell that started it
        others = [l for l in subprocess.run(["pgrep", "-af", "imagepi|pi_bringup|toupcam"], capture_output=True, text=True).stdout.splitlines()
                  if l.split()[0] not in mine and "pgrep" not in l]
        print("other ImagePi/camera processes:", others or "none")

    try:
        tc = load_sdk()
    except CameraError as e:
        print(e)
        return 1
    version = tc.Toupcam.Version()
    print("SDK version:", version.decode() if isinstance(version, bytes) else version)
    devs = tc.Toupcam.EnumV2()
    if not devs:
        print("EnumV2: no camera")
        return 1
    d = devs[0]
    m = d.model
    flags = [n[len("TOUPCAM_FLAG_"):] for n in dir(tc) if n.startswith("TOUPCAM_FLAG_") and m.flag & getattr(tc, n)]
    print(f"camera: {d.displayname}  id={d.id}  model={m.name}")
    print("resolutions:", [(r.width, r.height) for r in m.res])
    print("flags:", " ".join(flags))
    events = {getattr(tc, n): n[len("TOUPCAM_EVENT_"):] for n in dir(tc) if n.startswith("TOUPCAM_EVENT_")}

    cam = tc.Toupcam.Open(d.id)
    if not cam:
        print("Open: returned None (camera busy or not accessible)")
        return 1
    print("Open: ok")
    failed = 0
    try:
        if a.bandwidth:
            try:
                cam.put_Option(tc.TOUPCAM_OPTION_BANDWIDTH, a.bandwidth)
                print("bandwidth set to", a.bandwidth, "%")
            except (tc.HRESULTException, AttributeError) as ex:
                print("bandwidth option failed:", _hr_text(ex) if hasattr(ex, "hr") else ex)
        exposure_ms = getattr(a, "exposure_ms", 0.0)
        if exposure_ms:   # a short fixed exposure, so the frame rate shows the USB link and not the auto exposure time
            try:
                cam.put_AutoExpoEnable(0)
                cam.put_ExpoTime(int(exposure_ms * 1000))
                print("auto exposure off, exposure", exposure_ms, "ms")
            except tc.HRESULTException as ex:
                print("exposure option failed:", _hr_text(ex))
        order = [a.res] if a.res is not None else list(range(len(m.res) - 1, -1, -1))   # smallest first
        for idx in order:
            w, h = m.res[idx].width, m.res[idx].height
            seen: dict[str, int] = {}

            def on_event(ev, _ctx, seen=seen):
                seen[events.get(ev, hex(ev))] = seen.get(events.get(ev, hex(ev)), 0) + 1

            try:
                cam.put_eSize(idx)
                bw, bh = cam.get_Size()
                buf = ctypes.create_string_buffer(bw * bh * 3)   # the wrapper's argument is c_char_p: bytearray is rejected
                cam.StartPullModeWithCallback(on_event, None)
            except tc.HRESULTException as ex:
                failed += 1
                print(f"[{idx}] {w}x{h}: start FAILED  {_hr_text(ex)}")
                continue
            except Exception as ex:
                failed += 1
                print(f"[{idx}] {w}x{h}: start FAILED  {type(ex).__name__}: {ex}")
                continue
            info = tc.ToupcamFrameInfoV3()
            frames, pull_err = 0, set()
            t0 = time.time()
            while time.time() - t0 < a.secs:
                try:
                    cam.PullImageV3(buf, 0, 24, -1, info)
                    frames += 1
                except tc.HRESULTException as ex:
                    if _hr(ex) != 0x8000000A:   # E_PENDING just means "no new frame yet"
                        pull_err.add(_hr_text(ex))
                    time.sleep(0.005)
                except Exception as ex:
                    pull_err.add(f"{type(ex).__name__}: {ex}")
                    break
            dt = time.time() - t0
            try:
                chosen = f", exposure {cam.get_ExpoTime() / 1000:.1f} ms (auto {'on' if cam.get_AutoExpoEnable() else 'off'})"
            except Exception:
                chosen = ""
            cam.Stop()
            print(f"[{idx}] {w}x{h}: start ok, {frames} frames pulled ({frames / dt:.1f} fps){chosen}, events {seen or 'none'}, pull errors {sorted(pull_err) or 'none'}")
            if not frames:
                failed += 1
    finally:
        cam.Close()
    return 1 if failed else 0


def _even(v: float) -> int:
    return int(v) & ~1


def locate_crop(full, crop):
    """Where does ``crop`` sit inside ``full`` (both H x W x 3 uint8 RGB)?

    Returns ``(mirrored, score, x, y)``: the best of the four mirror states of the crop (``"no"``, ``"vertically"``,
    ``"horizontally"``, ``"both"``), its normalised correlation (1.0 = identical) and the top-left corner in full-frame pixels.
    """
    import cv2
    import numpy as np

    g_full = cv2.cvtColor(np.ascontiguousarray(full), cv2.COLOR_RGB2GRAY)
    g_crop = cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_RGB2GRAY)
    if g_crop.std() < 2.0 or g_full.std() < 2.0:     # a blank scene correlates "perfectly" everywhere (OpenCV returns 1.0)
        return ("no", 0.0, 0, 0)
    s = min(1.0, 600.0 / max(g_full.shape))                 # match on a small copy: the 18 MP frame would take minutes
    if s < 1.0:
        g_full = cv2.resize(g_full, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        g_crop = cv2.resize(g_crop, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    best = ("no", -1.0, 0, 0)
    for name, t in (("no", g_crop), ("vertically", g_crop[::-1]), ("horizontally", g_crop[:, ::-1]), ("both", g_crop[::-1, ::-1])):
        res = np.nan_to_num(cv2.matchTemplate(g_full, np.ascontiguousarray(t), cv2.TM_CCOEFF_NORMED))
        _, score, _, loc = cv2.minMaxLoc(res)
        if score > best[1]:
            best = (name, float(score), int(round(loc[0] / s)), int(round(loc[1] / s)))
    return best


def axis_verdict(requested: int, found: int, size: int, full: int, tol: int) -> str:
    """Compare where a crop was requested with where it was found along one axis."""
    if abs(found - requested) <= tol:
        return "as requested"
    if abs(found - (full - requested - size)) <= tol:
        return f"MIRRORED (requested {requested}, found {found} = {full} - {requested} - {size})"
    return f"OFFSET by {found - requested:+d} px (requested {requested}, found {found})"


def _grab(cam, shape, n=3, timeout=20.0):
    """Mean of the next ``n`` live frames that are ``shape`` = (height, width) big, as uint8; None if they never arrive."""
    import numpy as np

    frames, t0 = [], time.time()
    while len(frames) < n and time.time() - t0 < timeout:
        f = cam.pull()
        if f is None:
            time.sleep(0.002)
        elif f.shape[:2] == tuple(shape):
            frames.append(f.astype(np.float32))
    return np.mean(frames, axis=0).astype(np.uint8) if len(frames) == n else None


def _open_first_touptek():
    from imagepi.camera.base import CameraError
    from imagepi.camera.touptek import TouptekCamera

    try:
        infos = TouptekCamera.enumerate()
    except CameraError as e:
        print(e)
        return None, None
    if not infos:
        print("no ToupTek camera found")
        return None, None
    print("found:", infos[0].name, infos[0].resolutions)
    cam = TouptekCamera()
    cam.open(infos[0].id)
    return cam, infos[0]


def cmd_roitest(a) -> int:
    """Where does a hardware crop land? Needs a still, textured scene (tissue paper), lamp on, nothing moving.

    Raw frames only (no ImagePi mirroring): for each resolution a full frame, then a crop requested at 15 % / 10 % of the
    frame with 30 % x 30 % size (off-centre on purpose), and a template match of the crop in the full frame.
    """
    from imagepi.camera.base import CameraError

    cam, _info = _open_first_touptek()
    if cam is None:
        return 1
    cam.delivered_flip = (False, False)      # the mirroring is what is being measured
    cam.set_flip(False, False)
    failed, verdicts = 0, []
    try:
        cam.set_auto_exposure(False)
        cam.set_exposure(int(a.exposure_ms * 1000))
        print(f"exposure: fixed {a.exposure_ms:g} ms")
        order = [a.res] if a.res is not None else list(range(len(cam.resolutions) - 1, -1, -1))   # smallest first
        for idx in order:
            W, H = cam.resolutions[idx]
            x, y, w, h = _even(W * 0.15), _even(H * 0.10), _even(W * 0.30), _even(H * 0.30)
            try:
                cam.set_resolution(idx)
                cam.start(lambda: None)
                full = _grab(cam, (H, W))
                if full is None:
                    raise CameraError(f"no {W}x{H} frames arrived")
                cam.set_roi(x, y, w, h)
                crop = _grab(cam, (h, w))
                if crop is None:
                    raise CameraError(f"after the crop request no {w}x{h} frames arrived")
                try:
                    readback = tuple(cam._cam.get_Roi())
                except Exception:
                    readback = "n/a"
                cam.set_roi(0, 0, W, H)
            except CameraError as e:
                failed += 1
                print(f"[{idx}] {W}x{H}: FAILED  {e}")
                continue
            finally:
                cam.stop()
            mirrored, score, fx, fy = locate_crop(full, crop)
            tol = max(4, int(2 * max(W, H) / 600))
            print(f"[{idx}] {W}x{H}: crop requested at x={x} y={y} size {w}x{h}; get_Roi returned {readback}")
            if score < 0.6:
                failed += 1
                print(f"     no reliable match (best score {score:.2f}): use a still, textured scene such as tissue paper and keep it still")
                continue
            hx, hy = axis_verdict(x, fx, w, W, tol), axis_verdict(y, fy, h, H, tol)
            print(f"     found in the full frame at x={fx} y={fy} (match {score:.2f}); crop content mirrored against the full frame: {mirrored}")
            print(f"     horizontal position: {hx};  vertical position: {hy}")
            verdicts.append((hx.split(" ")[0], hy.split(" ")[0]))
    finally:
        cam.close()
    if verdicts:
        print("RESULT: horizontal", sorted({v[0] for v in verdicts}), "vertical", sorted({v[1] for v in verdicts}),
              "(as = as requested, MIRRORED = measured from the opposite edge)")
    return 1 if failed else 0


LIVE_AFTER_SNAP_S = 1.0


def cmd_snaptest(a) -> int:
    """Several snaps in a row through ImagePi's camera backend. Round 4: the second snap of a run never arrived."""
    from imagepi.camera.base import CameraError

    cam, _info = _open_first_touptek()
    if cam is None:
        return 1
    if a.no_restart:
        cam.RESTART_AFTER_SNAP = False
    failed = 0
    try:
        cam.set_auto_exposure(False)
        cam.set_exposure(int(a.exposure_ms * 1000))
        idx = a.res if a.res is not None else len(cam.resolutions) - 1
        cam.set_resolution(idx)
        print(f"preview {cam.resolutions[idx][0]}x{cam.resolutions[idx][1]}, exposure {a.exposure_ms:g} ms, restart after each snap: {cam.RESTART_AFTER_SNAP}")
        cam.start(lambda: None)
        for i in range(a.count):
            n0 = cam.snap_restarts
            t0 = time.time()
            try:
                arr = cam.snap()
            except CameraError as e:
                failed += 1
                print(f"snap {i + 1}: FAILED after {time.time() - t0:.1f} s  {e}")
                continue
            dt = time.time() - t0
            live, t1 = 0, time.time()
            while time.time() - t1 < LIVE_AFTER_SNAP_S:   # the live view must keep running after a snap
                if cam.pull() is not None:
                    live += 1
                else:
                    time.sleep(0.002)
            print(f"snap {i + 1}: {arr.shape[1]}x{arr.shape[0]} in {dt:.2f} s, stream restarts {cam.snap_restarts - n0}, live frames pulled afterwards: {live}")
            if not live:
                failed += 1
    finally:
        cam.close()
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("render").set_defaults(fn=cmd_render)
    c = sub.add_parser("camera")
    c.add_argument("--secs", type=float, default=5.0)
    c.add_argument("--exposure-ms", type=float, default=10.0,
                   help="fixed exposure in ms, auto exposure off (default 10: measures the USB link). 0 = leave the camera in auto exposure")
    c.add_argument("--auto-limit-ms", type=float, default=0.0,
                   help="with --exposure-ms 0: longest exposure auto exposure may use, in ms (default: the camera's 350)")
    c.set_defaults(fn=cmd_camera)
    d = sub.add_parser("camdiag")
    d.add_argument("--secs", type=float, default=3.0)
    d.add_argument("--res", type=int, default=None, help="only this resolution index (default: all, smallest first)")
    d.add_argument("--bandwidth", type=int, default=0, help="set the camera's USB bandwidth option to this percent (1-100) first")
    d.add_argument("--exposure-ms", type=float, default=0.0, help="auto exposure off and this fixed exposure (default 0: leave the camera as it is)")
    d.set_defaults(fn=cmd_camdiag)
    r = sub.add_parser("roitest")
    r.add_argument("--res", type=int, default=None, help="only this resolution index (default: all, smallest first)")
    r.add_argument("--exposure-ms", type=float, default=10.0)
    r.set_defaults(fn=cmd_roitest)
    t = sub.add_parser("snaptest")
    t.add_argument("--count", type=int, default=4)
    t.add_argument("--res", type=int, default=None, help="preview resolution index (default: the smallest, like the app's live view)")
    t.add_argument("--exposure-ms", type=float, default=10.0)
    t.add_argument("--no-restart", action="store_true", help="do not restart the stream after each snap (shows the SDK's own behaviour)")
    t.set_defaults(fn=cmd_snaptest)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
