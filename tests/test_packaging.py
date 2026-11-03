# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""The USB permission rule, the installers and the docs must agree about the rule's name, and the rule must not be world-writable."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RULE = ROOT / "packaging" / "70-toupcam.rules"
ASSIGNMENT = r'[A-Za-z_]+(\{[^}]+\})?\s*(==|!=|\+=|-=|:=|=)\s*"[^"]*"'


def _rules():
    return [l.strip() for l in RULE.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]


def test_the_rule_is_well_formed_and_not_world_writable():
    rules = _rules()
    assert len(rules) == 2 and {"0547", "04b4"} == {re.search(r'idVendor\}=="(\w+)"', r).group(1) for r in rules}
    for r in rules:
        assert re.fullmatch(f"{ASSIGNMENT}(\\s*,\\s*{ASSIGNMENT})*", r), r
        assert 'MODE="0660"' in r and 'TAG+="uaccess"' in r and 'GROUP="plugdev"' in r, r
    assert "0666" not in "\n".join(_rules()), "the SDK's own rule lets every user write to the device"


def test_the_rule_runs_before_73_seat_late_which_uaccess_needs():
    assert int(RULE.name.split("-")[0]) < 73


def test_installers_docs_and_error_messages_name_the_same_rule_file():
    name = RULE.name
    for f in ("scripts/install_toupcam.py", "src/scopi/camera/touptek.py"):
        assert name in (ROOT / f).read_text(), f
    assert 'RULE_NAME = "70-toupcam.rules"' in (ROOT / "scripts" / "install_toupcam.py").read_text()
    assert "--udev-only" in (ROOT / "packaging" / "install_pi.sh").read_text()
    assert "99-toupcam" not in (ROOT / "packaging" / "install_pi.sh").read_text(), "install_pi.sh must go through install_toupcam.py"
    assert "99-toupcam" not in (ROOT / "usage.md").read_text()


def test_installer_has_no_hook_into_the_maintainers_references_folder():
    sh = (ROOT / "packaging" / "install_pi.sh").read_text()
    assert "references/" not in sh and "SCOPI_FIJI_LUTS" in sh


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")
def test_install_script_is_valid_bash():
    subprocess.run(["bash", "-n", str(ROOT / "packaging" / "install_pi.sh")], check=True)


def test_application_icon_ships_with_the_package_and_the_launcher_points_at_it():
    from PySide6.QtGui import QImage

    from scopi.app import ICON_PATH

    assert ICON_PATH == ROOT / "src" / "scopi" / "resources" / "scopi.png" and ICON_PATH.is_file()
    assert not QImage(str(ICON_PATH)).isNull(), "scopi.png is not a readable image"
    assert not (ROOT / "scopi.png").exists(), "the icon lives in src/scopi/resources, not the repository root"
    assert "resources/*.png" in (ROOT / "pyproject.toml").read_text(), "pyproject.toml must list the icon as package data"
    assert "Icon=scopi" in (ROOT / "packaging" / "scopi.desktop").read_text()
    assert "src/scopi/resources/scopi.png" in (ROOT / "packaging" / "install_pi.sh").read_text(), "install_pi.sh must point the launcher's Icon= at the packaged file"
