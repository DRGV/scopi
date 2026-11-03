# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""scripts/install_toupcam.py: it must not write outside its destination, whatever the zip says."""

import importlib.util
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_toupcam.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("install_toupcam", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _zip(path, entries):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return path


GOOD = {"python/toupcam.py": "# wrapper", "inc/toupcam.h": "/* header */", "linux/arm64/glibc/libtoupcam.so": "lib", "linux/udev/99-toupcam.rules": "SDK rule"}


def test_is_safe(tool):
    for ok in ("python/toupcam.py", "linux/arm64/glibc/libtoupcam.so", "mac/libtoupcam.dylib"):
        assert tool.is_safe(ok), ok
    for bad in ("../x", "linux/arm64/../../../x", "/etc/passwd", "C:/Windows/x", "C:\\x", "linux\\arm64\\..\\..\\x", "a/b/../../../c"):
        assert not tool.is_safe(bad), bad


@pytest.mark.parametrize("evil", ["linux/arm64/../../../escaped.txt", "mac/../../escaped.txt", "linux/arm64/..\\..\\..\\escaped.txt"])
def test_a_zip_entry_that_would_leave_the_destination_is_refused(tool, tmp_path, evil, capsys):
    dest = tmp_path / "vendor" / "toupcam"
    z = _zip(tmp_path / "evil.zip", {**GOOD, evil: "pwned"})
    assert tool.extract_sdk(z, dest) == 1
    assert "refusing to unpack" in capsys.readouterr().err
    assert not list(tmp_path.rglob("escaped.txt")), "nothing may be written outside the destination"
    assert not (dest / "python" / "toupcam.py").exists(), "a refused archive is not partly unpacked"


def test_a_normal_zip_still_unpacks_and_the_sdks_own_udev_rule_is_not_copied(tool, tmp_path):
    dest = tmp_path / "toupcam"
    assert tool.extract_sdk(_zip(tmp_path / "sdk.zip", GOOD), dest, keep_quarantine=True) == 0
    got = {str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()}
    assert got == {"python/toupcam.py", "inc/toupcam.h", "linux/arm64/glibc/libtoupcam.so"}, got   # Scopi installs its own, narrower rule


def test_command_line_needs_a_source_unless_it_only_installs_the_rule(tool, monkeypatch):
    with pytest.raises(SystemExit):
        tool.main([])
    called = []
    monkeypatch.setattr(tool, "install_udev_rule", lambda: called.append(1) or 0)
    assert tool.main(["--udev-only"]) == 0 and called == [1]


def test_the_udev_rule_is_only_installed_on_linux(tool, monkeypatch):
    monkeypatch.setattr(tool.sys, "platform", "darwin")
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **k: pytest.fail(f"must not run {a}"))
    assert tool.install_udev_rule() == 0
