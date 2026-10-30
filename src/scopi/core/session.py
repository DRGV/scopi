# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""Session: shared application state that ops and the UI both operate on."""

from __future__ import annotations

from .commands import Recorder
from .manager import Manager
from .results import ResultsTable


class Session:
    def __init__(self):
        self.manager = Manager()
        self.results = ResultsTable("Results")
        self.summary = ResultsTable("Summary")   # one row per Analyze Particles run
        self.recorder = Recorder()

    @property
    def doc(self):
        return self.manager.current
