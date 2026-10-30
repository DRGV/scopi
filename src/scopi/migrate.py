"""Carry a user's data over from the application's old name (ImagePi) to Scopi, once, without losing anything.

Scopi was called ImagePi until 2026-10-30. What the old name left on a machine:

- ``~/.imagepi/``: the objective calibration presets, look-up tables, plugins and a private copy of the camera SDK;
- the Qt settings of "ImagePi": theme, window layout, recent files and the flips and auto-exposure limit chosen for each camera.

Both are *copied*, never moved or deleted: the old data stays where it was as a backup, and can be removed by hand once everything works.
Nothing is overwritten: data that already exists under the new name wins.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

log = logging.getLogger("scopi.migrate")

OLD_DIR, NEW_DIR = ".imagepi", ".scopi"
OLD_ORG = OLD_APP = "ImagePi"


def migrate_user_dir(home: Path | None = None) -> bool:
    """Copy ``~/.imagepi`` to ``~/.scopi`` if the old folder exists and the new one does not. True if a copy was made."""
    home = Path(home) if home is not None else Path.home()
    old, new = home / OLD_DIR, home / NEW_DIR
    if not old.is_dir() or new.exists():
        return False
    try:
        shutil.copytree(old, new)
    except OSError:
        log.exception("could not copy %s to %s", old, new)
        shutil.rmtree(new, ignore_errors=True)   # a half-made folder would stop the next start from trying again
        return False
    log.info("Copied your saved data (objective presets, plugins, look-up tables) from %s to %s; the old folder was left as it was", old, new)
    return True


def migrate_settings(new, old=None) -> int:
    """Copy every key of the old "ImagePi" Qt settings into ``new`` if ``new`` is still empty. Returns how many keys were copied."""
    from PySide6.QtCore import QSettings

    old = old if old is not None else QSettings(OLD_ORG, OLD_APP)
    keys = old.allKeys()
    if new.allKeys() or not keys:
        return 0
    for key in keys:
        new.setValue(key, old.value(key))
    new.sync()
    log.info("Copied %d saved settings (theme, window layout, camera flips) from the old ImagePi settings", len(keys))
    return len(keys)
