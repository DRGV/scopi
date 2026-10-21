"""Plugin discovery: ``imagepi.plugins`` entry points plus ``*_plg.py`` files in plugin folders.

A plugin module registers ops by importing ``imagepi.ops.register`` and decorating Op subclasses.
Import errors are logged and never fatal.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("imagepi.plugins")

SUFFIX = "_plg.py"


def default_dirs() -> list[Path]:
    dirs = [Path.home() / ".imagepi" / "plugins"]
    extra = os.environ.get("IMAGEPI_PLUGINS")
    if extra:
        dirs += [Path(p) for p in extra.split(os.pathsep) if p]
    return dirs


def discover(dirs: list[Path] | None = None) -> list[Path]:
    found: list[Path] = []
    for d in dirs if dirs is not None else default_dirs():
        if d.is_dir():
            found += sorted(d.rglob(f"*{SUFFIX}"))
    return found


def _load_file(path: Path) -> bool:
    name = f"imagepi_plugin_{path.stem}_{abs(hash(str(path)))}"
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return True
    except Exception:
        sys.modules.pop(name, None)
        log.exception("failed to load plugin %s", path)
        return False


def load_all(dirs: list[Path] | None = None) -> dict:
    """Load entry-point and folder plugins. Returns {'loaded': [...], 'failed': [...]}."""
    loaded, failed = [], []
    for ep in importlib.metadata.entry_points(group="imagepi.plugins"):
        try:
            ep.load()
            loaded.append(ep.name)
        except Exception:
            log.exception("failed to load plugin entry point %s", ep.name)
            failed.append(ep.name)
    for p in discover(dirs):
        (loaded if _load_file(p) else failed).append(str(p))
    return {"loaded": loaded, "failed": failed}
