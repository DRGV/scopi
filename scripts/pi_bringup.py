#!/usr/bin/env python3
"""Milestone 0 spike: verify the Pi 5 stack and measure what the plan lists as unverified risks.

    python scripts/pi_bringup.py check            # wheels import, glibc, Qt platform plugins
    python scripts/pi_bringup.py render           # time 18 MP pan/zoom through the real canvas (needs a display)
    python scripts/pi_bringup.py camera [--secs 5] [--exposure-ms 10 | --exposure-ms 0 --auto-limit-ms 100]
                                                   # lsusb, enumerate, fps + USB throughput per resolution (fixed short exposure by default)
    python scripts/pi_bringup.py camdiag [--res N] [--bandwidth %] [--exposure-ms MS] [--secs 3]
                                                   # the vendor SDK only (no ImagePi camera code): USB state, flags, start/pull per resolution

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
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
