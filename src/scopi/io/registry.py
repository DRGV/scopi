# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Extension-keyed reader/writer registry."""

from __future__ import annotations

import os
from typing import Callable

from ..core.image import ImageDoc

READERS: dict[str, Callable[[str], ImageDoc]] = {}
WRITERS: dict[str, Callable[[ImageDoc, str], None]] = {}


def _ext(path: str) -> str:
    low = os.fspath(path).lower()
    if low.endswith(".ome.tif") or low.endswith(".ome.tiff"):
        return ".ome.tif"
    return os.path.splitext(low)[1]


def reader(*exts):
    def deco(fn):
        for e in exts:
            READERS[e] = fn
        return fn
    return deco


def writer(*exts):
    def deco(fn):
        for e in exts:
            WRITERS[e] = fn
        return fn
    return deco


def open_image(path: str) -> ImageDoc:
    path = os.fspath(path)
    fn = READERS.get(_ext(path))
    if fn is None:
        # last resort: let imageio try
        fn = READERS["*"]
    doc = fn(path)
    doc.path = path
    if not doc.title or doc.title == "Untitled":
        doc.title = os.path.basename(path)
    doc.changed = False
    return doc


def save_image(doc: ImageDoc, path: str) -> None:
    path = os.fspath(path)
    fn = WRITERS.get(_ext(path))
    if fn is None:
        raise ValueError(f"cannot write {_ext(path) or 'this'} format; supported: {', '.join(sorted(WRITERS))}")
    fn(doc, path)
    doc.path = path
    doc.changed = False


def supported_read_extensions() -> list[str]:
    return sorted(e for e in READERS if e != "*")


def supported_write_extensions() -> list[str]:
    return sorted(WRITERS)
