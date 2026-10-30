"""The rename from ImagePi to Scopi must not lose a user's saved calibrations or settings."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from scopi import migrate


def _old_home(tmp_path):
    old = tmp_path / ".imagepi"
    (old / "luts").mkdir(parents=True)
    (old / "presets.json").write_text(json.dumps({"10x": {"pixel_size": 0.000243, "unit": "mm"}}))
    (old / "luts" / "mine.lut").write_bytes(b"\x00" * 768)
    return old


def test_user_dir_is_copied_not_moved(tmp_path):
    old = _old_home(tmp_path)
    assert migrate.migrate_user_dir(tmp_path) is True
    new = tmp_path / ".scopi"
    assert json.loads((new / "presets.json").read_text())["10x"]["pixel_size"] == 0.000243     # the calibrations survive
    assert (new / "luts" / "mine.lut").read_bytes() == b"\x00" * 768
    assert (old / "presets.json").exists(), "the old folder is a backup and must be left alone"


def test_user_dir_never_overwrites_and_does_nothing_without_an_old_folder(tmp_path):
    assert migrate.migrate_user_dir(tmp_path) is False and not (tmp_path / ".scopi").exists()   # nothing to carry over
    old = _old_home(tmp_path)
    (tmp_path / ".scopi").mkdir()
    (tmp_path / ".scopi" / "presets.json").write_text("{}")
    assert migrate.migrate_user_dir(tmp_path) is False                                         # Scopi data already exists: it wins
    assert (tmp_path / ".scopi" / "presets.json").read_text() == "{}"
    assert (old / "presets.json").exists()


def test_user_dir_is_copied_once(tmp_path):
    _old_home(tmp_path)
    assert migrate.migrate_user_dir(tmp_path) is True
    (tmp_path / ".scopi" / "presets.json").write_text('{"4x": {}}')                            # the user keeps working in the new place
    assert migrate.migrate_user_dir(tmp_path) is False
    assert (tmp_path / ".scopi" / "presets.json").read_text() == '{"4x": {}}'


def test_a_failed_copy_leaves_no_half_made_folder_so_the_next_start_retries(tmp_path, monkeypatch):
    _old_home(tmp_path)

    def boom(*a, **k):
        (tmp_path / ".scopi").mkdir()
        raise OSError("disk full")
    monkeypatch.setattr(migrate.shutil, "copytree", boom)
    assert migrate.migrate_user_dir(tmp_path) is False
    assert not (tmp_path / ".scopi").exists()
    monkeypatch.undo()
    assert migrate.migrate_user_dir(tmp_path) is True


def _settings(tmp_path, name):
    from PySide6.QtCore import QSettings
    return QSettings(str(tmp_path / name), QSettings.Format.IniFormat)


def test_settings_are_copied_into_empty_new_settings(tmp_path):
    old, new = _settings(tmp_path, "old.ini"), _settings(tmp_path, "new.ini")
    old.setValue("theme", "dark")
    old.setValue("touch", "true")
    old.setValue("camera/U3CMOS18000KPA/mirror_v", "true")                                     # grouped, per-camera keys
    old.setValue("camera/U3CMOS18000KPA/auto_limit_ms", 100)
    old.sync()
    assert migrate.migrate_settings(new, old) == 4
    for key in old.allKeys():
        assert new.value(key) == old.value(key), key
    assert old.value("theme") == "dark", "the old settings stay as they were"


def test_settings_that_already_exist_are_never_overwritten(tmp_path):
    old, new = _settings(tmp_path, "old.ini"), _settings(tmp_path, "new.ini")
    old.setValue("theme", "dark")
    old.sync()
    new.setValue("theme", "light")
    new.sync()
    assert migrate.migrate_settings(new, old) == 0
    assert new.value("theme") == "light"


def test_no_old_settings_means_nothing_to_copy(tmp_path):
    assert migrate.migrate_settings(_settings(tmp_path, "new.ini"), _settings(tmp_path, "old.ini")) == 0


def test_the_application_uses_the_new_names():
    """The data folders and settings names that users depend on, and the entry point, all say Scopi."""
    from pathlib import Path

    import scopi
    from scopi.core import luts
    from scopi.plugins import loader
    from scopi.ui import camera_panel

    assert Path.home() / ".scopi" / "luts" == luts.user_lut_dir() or "SCOPI_LUTS" in os.environ
    assert Path.home() / ".scopi" / "plugins" in loader.default_dirs()
    assert camera_panel.presets_path().parent.name == ".scopi" or "SCOPI_PRESETS" in os.environ
    assert "Scopi" in scopi.__doc__ and "ImagePi" not in scopi.__doc__
