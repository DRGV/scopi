#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Populate vendor/toupcam/ from the vendor's SDK (the SDK is not redistributable; do not commit it).

    python scripts/install_toupcam.py /path/to/toupcamsdk-folder [--udev]    # an extracted SDK folder
    python scripts/install_toupcam.py /path/to/toupcamsdk.zip [--udev]       # or the zip as downloaded
    python scripts/install_toupcam.py --udev-only                            # only the USB permission rule (Linux, needs sudo)

Copies only what Scopi uses: the python wrapper, the header, and the Linux (arm64/x64, glibc) and macOS native libraries. Both the 2023
layout (linux/arm64/, mac/x64+arm64/) and the 2026 layout (linux/arm64/glibc/, mac/) are handled. An entry of the zip that is an absolute
path or contains ".." is refused: it would be written outside the destination folder.

--udev (Linux, needs sudo) installs Scopi's own rule, packaging/70-toupcam.rules, as /etc/udev/rules.d/70-toupcam.rules. It is narrower than
the SDK's rule, which makes the camera writable by every user, so the SDK's rule is not copied. The older 99-toupcam.rules that earlier
installs left in /etc/udev/rules.d is removed, because it would still grant that access.

On macOS the quarantine flag that Gatekeeper puts on downloaded files is removed from the extracted files (what
`xattr -dr com.apple.quarantine` does), because macOS may refuse to load a downloaded library otherwise. Use --keep-quarantine to leave it.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

WANTED_PREFIXES = ("linux/arm64/", "linux/x64/", "mac/", "python/toupcam.py", "inc/toupcam.h")   # not linux/udev/: Scopi has its own rule
SKIP_PARTS = ("/musl/",)   # musl builds are for Alpine, not Raspberry Pi OS
ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = Path("/etc/udev/rules.d")
RULE_NAME = "70-toupcam.rules"
OLD_RULE_NAME = "99-toupcam.rules"   # what earlier installs wrote: the SDK's world-writable rule


def is_safe(name: str) -> bool:
    """False for an absolute path, a Windows drive path or any '..' component: such an entry would land outside the destination."""
    posix = name.replace("\\", "/")
    return not posix.startswith("/") and not re.match(r"[A-Za-z]:", posix) and ".." not in PurePosixPath(posix).parts


def extract_sdk(src: Path, dest: Path, keep_quarantine: bool = False) -> int:
    if src.is_dir():
        names = sorted(str(p.relative_to(src)) for p in src.rglob("*") if p.is_file())
        opener = lambda n: open(src / n, "rb")
    else:
        z = zipfile.ZipFile(src)
        names = [n for n in z.namelist() if not n.endswith("/")]
        opener = z.open
    names = [n for n in names if n.startswith(WANTED_PREFIXES) and not any(sp in "/" + n for sp in SKIP_PARTS)
             and not n.endswith(".DS_Store")]
    unsafe = [n for n in names if not is_safe(n)]
    if unsafe:
        print(f"refusing to unpack {src}: {len(unsafe)} entries would be written outside {dest} (for example {unsafe[0]!r})", file=sys.stderr)
        return 1
    if not any(n.endswith("python/toupcam.py") for n in names):
        print("this does not look like the ToupTek camera SDK (python/toupcam.py missing)", file=sys.stderr)
        return 1
    root = dest.resolve()
    for n in names:
        target = dest / n
        if root not in target.resolve().parents:   # belt and braces: a path that still ends up outside (for example through a symlink)
            print(f"refusing to write {target}: it is outside {dest}", file=sys.stderr)
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        with opener(n) as f, open(target, "wb") as out:
            shutil.copyfileobj(f, out)
    # drop a stale copy of the native lib next to the wrapper so load_sdk() picks up the new one
    for stale in (dest / "python").glob("libtoupcam.*"):
        stale.unlink()
    print(f"extracted {len(names)} files to {dest}")

    if sys.platform == "darwin" and not keep_quarantine:
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(dest)], check=False)
        print("removed macOS's quarantine flag from the extracted files (use --keep-quarantine to leave it)")
    return 0


def install_udev_rule() -> int:
    if not sys.platform.startswith("linux"):
        print("the USB permission rule is only needed on Linux: skipped")
        return 0
    rule = ROOT / "packaging" / RULE_NAME
    subprocess.run(["sudo", "install", "-m", "644", str(rule), str(RULES_DIR / RULE_NAME)], check=True)
    old = RULES_DIR / OLD_RULE_NAME
    if old.is_file() and 'MODE="0666"' in old.read_text(errors="replace"):
        subprocess.run(["sudo", "rm", "-f", str(old)], check=True)
        print(f"removed the old world-writable rule {old}")
    subprocess.run(["sudo", "udevadm", "control", "--reload-rules"], check=True)
    subprocess.run(["sudo", "udevadm", "trigger"], check=True)
    print(f"udev rule {RULE_NAME} installed; replug the camera")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Install the ToupTek camera SDK for Scopi.")
    ap.add_argument("zip", nargs="?", help="the SDK zip, or the folder it was extracted to")
    ap.add_argument("--dest", default=str(ROOT / "vendor" / "toupcam"))
    ap.add_argument("--udev", action="store_true", help=f"also install {RULE_NAME} into {RULES_DIR} (Linux, sudo)")
    ap.add_argument("--udev-only", action="store_true", help="only install the USB permission rule, no SDK")
    ap.add_argument("--keep-quarantine", action="store_true", help="on macOS, do not remove the quarantine flag from the extracted files")
    a = ap.parse_args(argv)
    if a.udev_only:
        return install_udev_rule()
    if not a.zip:
        ap.error("give the SDK zip or folder, or use --udev-only")
    rc = extract_sdk(Path(a.zip), Path(a.dest), keep_quarantine=a.keep_quarantine)
    if rc == 0 and a.udev:
        rc = install_udev_rule()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
