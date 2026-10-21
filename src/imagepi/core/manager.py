"""Tracks open images and the active one."""

from __future__ import annotations

from .image import ImageDoc


class Manager:
    def __init__(self):
        self.images: list[ImageDoc] = []
        self.current: ImageDoc | None = None
        self.listeners: list = []

    def add(self, doc: ImageDoc) -> ImageDoc:
        names = {d.title for d in self.images}
        if doc.title in names:
            base, n = doc.title, 1
            while f"{base}-{n}" in names:
                n += 1
            doc.title = f"{base}-{n}"
        self.images.append(doc)
        self.current = doc
        self._notify("add", doc)
        return doc

    def remove(self, doc: ImageDoc) -> None:
        self.images.remove(doc)
        if self.current is doc:
            self.current = self.images[-1] if self.images else None
        self._notify("remove", doc)

    def activate(self, doc: ImageDoc) -> None:
        self.current = doc
        self._notify("activate", doc)

    def _notify(self, what, doc) -> None:
        for f in list(self.listeners):
            f(what, doc)
