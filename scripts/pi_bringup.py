#!/usr/bin/env python3
"""Milestone 0 spike: verify the Pi 5 stack and measure what the plan lists as unverified risks.

    python scripts/pi_bringup.py check            # wheels import, glibc, Qt platform plugins
    python scripts/pi_bringup.py render           # time 18 MP pan/zoom through the real canvas (needs a display)
    python scripts/pi_bringup.py camera [--secs 5] # lsusb, enumerate, fps + USB throughput per resolution

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
    try:
        for idx, (w, h) in enumerate(cam.resolutions):
            cam.set_resolution(idx)
            n = [0]
            cam.start(lambda: None)
            t0 = time.time()
            frames = 0
            while time.time() - t0 < a.secs:
                f = cam.pull()
                if f is not None:
                    frames += 1
                else:
                    time.sleep(0.001)
            cam.stop()
            dt = time.time() - t0
            mb = frames * w * h * 3 / 1e6
            print(f"{w}x{h}: {frames / dt:5.1f} fps, {mb / dt:6.1f} MB/s decoded RGB24")
    finally:
        cam.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("render").set_defaults(fn=cmd_render)
    c = sub.add_parser("camera")
    c.add_argument("--secs", type=float, default=5.0)
    c.set_defaults(fn=cmd_camera)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
