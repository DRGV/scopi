# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
from .registry import READERS, WRITERS, open_image, save_image, supported_read_extensions, supported_write_extensions

# importing registers the built-in formats
from . import readers, writers  # noqa: E402,F401

__all__ = ["READERS", "WRITERS", "open_image", "save_image", "supported_read_extensions", "supported_write_extensions"]
