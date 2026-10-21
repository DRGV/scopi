"""Session: shared application state that ops and the UI both operate on."""

from __future__ import annotations

from .commands import Recorder
from .manager import Manager
from .results import ResultsTable


class Session:
    def __init__(self):
        self.manager = Manager()
        self.results = ResultsTable("Results")
        self.recorder = Recorder()

    @property
    def doc(self):
        return self.manager.current
