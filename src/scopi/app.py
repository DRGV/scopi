# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Application entry point."""

from __future__ import annotations

import logging
import os
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Native Wayland on the Pi when available (Qt picks it automatically; this only sets a default).
    if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY") and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "wayland"
        # Qt's Wayland text-input plugin warns "Trying to disable ... but 0x0 is focused" on every focus change; harmless noise.
        os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.wayland.textinput.warning=false")

    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from .migrate import migrate_settings, migrate_user_dir
    from .plugins import load_all
    from .ui import theme
    from .ui.main_window import MainWindow

    migrate_user_dir()   # Scopi was called ImagePi: carry ~/.imagepi (objective presets, plugins, LUTs) over before anything reads it
    app = QApplication(argv)
    app.setApplicationName("Scopi")
    app.setOrganizationName("Scopi")
    settings = QSettings("Scopi", "Scopi")
    migrate_settings(settings)   # ... and the old Qt settings (theme, window layout, camera flips), only while the new ones are empty
    theme.apply_theme(app, settings.value("theme", "auto"), str(settings.value("touch", "false")).lower() == "true")
    res = load_all()
    if res["failed"]:
        logging.getLogger("scopi").warning("plugins failed to load: %s", res["failed"])
    win = MainWindow(settings=settings)
    win.show()
    for p in argv[1:]:
        if os.path.exists(p):
            win.open_path(p)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
