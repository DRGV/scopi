# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Macro recording and replay.

A macro line is ``Menu>Item{json}`` where the menu/item path is the op's full title
and the optional JSON object holds its parameters, e.g. ``Process>Filters>Gaussian Blur{"sigma": 2.0}``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class MacroLine:
    path: str
    para: dict

    def __str__(self) -> str:
        return f"{self.path}{json.dumps(self.para, sort_keys=True)}" if self.para else self.path

    @classmethod
    def parse(cls, line: str) -> "MacroLine":
        line = line.strip()
        brace = line.find("{")
        if brace < 0:
            return cls(line, {})
        return cls(line[:brace].strip(), json.loads(line[brace:]))


class Recorder:
    def __init__(self):
        self.lines: list[MacroLine] = []
        self.recording = False

    def start(self) -> None:
        self.lines.clear()
        self.recording = True

    def stop(self) -> None:
        self.recording = False

    def record(self, path: str, para: dict) -> None:
        if self.recording:
            self.lines.append(MacroLine(path, dict(para)))

    def text(self) -> str:
        return "\n".join(str(l) for l in self.lines)

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            f.write(self.text() + "\n")


def parse_macro(text: str) -> list[MacroLine]:
    return [MacroLine.parse(l) for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]


def replay(session, text_or_lines, doc=None) -> int:
    """Run macro lines against ``doc`` (default: current image). Returns lines executed."""
    from ..ops import base

    lines = parse_macro(text_or_lines) if isinstance(text_or_lines, str) else text_or_lines
    n = 0
    for ml in lines:
        op_cls = base.REGISTRY.get(ml.path)
        if op_cls is None:
            raise KeyError(f"unknown command {ml.path!r}")
        base.execute(op_cls(), session, doc or session.doc, ml.para, record=False)
        n += 1
    return n


def run_batch(session, macro_text: str, paths: list[str], save_dir: str | None = None) -> list[str]:
    """Open each file, replay the macro on it and optionally save results as TIFF."""
    import os

    from .. import io

    out = []
    for p in paths:
        doc = io.open_image(p)
        session.manager.add(doc)
        replay(session, macro_text, doc)
        if save_dir:
            dest = os.path.join(save_dir, os.path.splitext(os.path.basename(p))[0] + ".tif")
            io.save_image(doc, dest)
            out.append(dest)
        session.manager.remove(doc)
    return out
