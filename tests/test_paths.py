# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""The folders Scopi keeps its data in are the ones usage.md documents."""

import os
from pathlib import Path

from scopi.core import luts
from scopi.plugins import loader
from scopi.ui import camera_panel


def test_data_folders_default_to_dot_scopi_in_the_home_folder():
    home = Path.home()
    if "SCOPI_LUTS" not in os.environ:
        assert luts.user_lut_dir() == home / ".scopi" / "luts"
    if "SCOPI_PRESETS" not in os.environ:
        assert camera_panel.presets_path() == home / ".scopi" / "presets.json"
    assert home / ".scopi" / "plugins" in loader.default_dirs()
