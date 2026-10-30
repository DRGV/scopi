#!/usr/bin/env python3
"""Populate vendor/toupcam/ from the vendor's SDK (the SDK is not redistributable; do not commit it).

    python scripts/install_toupcam.py references/omax/toupcamsdk.20260818 [--udev]   # extracted folder
    python scripts/install_toupcam.py references/omax/SDK/toupcamsdk.20230702.zip    # or the zip

Copies only what Scopi uses: the python wrapper, the header, the Linux (arm64/x64, glibc) and macOS
native libraries, and the udev rule. Both the 2023 layout (linux/arm64/, mac/x64+arm64/) and the 2026
layout (linux/arm64/glibc/, mac/) are handled. With --udev (Linux, needs sudo) installs the udev rule.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

WANTED_PREFIXES = ("linux/arm64/", "linux/x64/", "mac/", "python/toupcam.py", "inc/toupcam.h", "linux/udev/")
SKIP_PARTS = ("/musl/",)   # musl builds are for Alpine, not Raspberry Pi OS
ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip")
    ap.add_argument("--dest", default=str(ROOT / "vendor" / "toupcam"))
    ap.add_argument("--udev", action="store_true", help="install 99-toupcam.rules into /etc/udev/rules.d (sudo)")
    a = ap.parse_args()

    dest = Path(a.dest)
    src = Path(a.zip)
    if src.is_dir():
        names = sorted(str(p.relative_to(src)) for p in src.rglob("*") if p.is_file())
        opener = lambda n: open(src / n, "rb")
    else:
        z = zipfile.ZipFile(src)
        names = [n for n in z.namelist() if not n.endswith("/")]
        opener = z.open
    names = [n for n in names if n.startswith(WANTED_PREFIXES) and not any(sp in "/" + n for sp in SKIP_PARTS)
             and not n.endswith(".DS_Store")]
    if not any(n.endswith("python/toupcam.py") for n in names):
        print("this does not look like the ToupTek camera SDK (python/toupcam.py missing)", file=sys.stderr)
        return 1
    for n in names:
        target = dest / n
        target.parent.mkdir(parents=True, exist_ok=True)
        with opener(n) as f, open(target, "wb") as out:
            shutil.copyfileobj(f, out)
    # drop a stale copy of the native lib next to the wrapper so load_sdk() picks up the new one
    for stale in (dest / "python").glob("libtoupcam.*"):
        stale.unlink()
    print(f"extracted {len(names)} files to {dest}")

    if sys.platform == "darwin":
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(dest)], check=False)
    if a.udev:
        rule = dest / "linux" / "udev" / "99-toupcam.rules"
        subprocess.run(["sudo", "cp", str(rule), "/etc/udev/rules.d/99-toupcam.rules"], check=True)
        subprocess.run(["sudo", "udevadm", "control", "--reload-rules"], check=True)
        subprocess.run(["sudo", "udevadm", "trigger"], check=True)
        print("udev rule installed; replug the camera")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
