"""Operation framework: declarative parameters, filters, ROI masking, registry.

Ideas ported from ImagePy: each op declares ``view`` (parameters, from which dialogs are
generated), ``note`` flags and a ``run``. Nothing here depends on Qt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

REGISTRY: dict[str, type["Op"]] = {}


# ---------------------------------------------------------------- parameters
@dataclass
class Param:
    kind: str           # int | float | choice | bool | str
    key: str
    label: str
    default: Any
    range: tuple | None = None
    choices: list | None = None
    digits: int = 1
    unit: str = ""


def Int(key, label, rng, default=None, unit=""):
    return Param("int", key, label, rng[0] if default is None else default, range=rng, unit=unit)


def Float(key, label, rng, default=None, digits=1, unit=""):
    return Param("float", key, label, rng[0] if default is None else default, range=rng, digits=digits, unit=unit)


def Choice(key, label, choices, default=None):
    return Param("choice", key, label, choices[0] if default is None else default, choices=list(choices))


def Bool(key, label, default=False):
    return Param("bool", key, label, default)


def Str(key, label, default=""):
    return Param("str", key, label, default)


# ---------------------------------------------------------------- ops
class Op:
    title: str = ""
    category: str = ""                 # e.g. "Process>Filters"
    view: list[Param] = []
    note: frozenset[str] = frozenset()
    # Flags: "auto_snap" (undo snapshot), "auto_msk" (restrict to ROI), "preview" (live preview ok),
    #        "stack" (offer to process whole stack), "rgb_native" (op handles colour itself),
    #        "8bit"/"16bit"/"float"... are not enforced; ops should handle any real dtype.

    @classmethod
    def path(cls) -> str:
        return f"{cls.category}>{cls.title}" if cls.category else cls.title

    def default_para(self) -> dict:
        return {p.key: p.default for p in self.view}

    def full_para(self, para: dict | None) -> dict:
        out = self.default_para()
        out.update(para or {})
        return out

    def check(self, doc) -> str | None:
        """Return an error message if the op cannot run on ``doc``."""
        return None

    def view_for(self, doc) -> list[Param]:
        """Parameters to show for ``doc`` (ops can adapt ranges to the image's dtype)."""
        return self.view


class Filter(Op):
    """Per-plane image -> image op.

    ``run(img, para)`` gets a 2-D plane (a single channel for RGB images) and returns an
    array of the same shape. By default the result is cast back to the input dtype;
    set ``out_dtype`` to a numpy dtype to change the document dtype instead (thresholds, 8-bit
    conversion), or ``"keep"`` (default) / ``"native"`` to leave whatever ``run`` returns.
    """

    note = frozenset({"auto_snap", "auto_msk", "preview", "stack"})
    out_dtype: Any = "keep"

    def run(self, img: np.ndarray, para: dict) -> np.ndarray:
        raise NotImplementedError

    # Ops that need the full array / doc (e.g. colour conversion, stack ops) override this.
    def run_doc(self, doc, para: dict, stack: bool, mask: np.ndarray | None) -> np.ndarray:
        idx = list(range(doc.n_frames)) if stack else [doc.plane]
        results = []
        for i in idx:
            src = doc.data[i]
            res = self._plane(src, para, doc.rgb)
            if mask is not None and res.shape == src.shape:
                res = np.where(mask[..., None] if doc.rgb else mask, res, src.astype(res.dtype, copy=False))
            results.append(res)
        if len(idx) == doc.n_frames:
            return np.stack(results)
        full = doc.data.copy()
        full[doc.plane] = results[0]
        return full

    def _plane(self, src: np.ndarray, para: dict, rgb: bool) -> np.ndarray:
        if rgb and "rgb_native" not in self.note:
            chans = [self._one(src[..., c], para) for c in range(src.shape[-1])]
            return np.stack(chans, axis=-1)
        return self._one(src, para)

    def _one(self, img: np.ndarray, para: dict) -> np.ndarray:
        res = self.run(img, para)
        if self.out_dtype == "keep":
            return cast_like(res, img.dtype)
        if self.out_dtype == "native":
            return res
        return cast_like(res, np.dtype(self.out_dtype))


class Simple(Op):
    """Whole-document op that does not (necessarily) replace pixel data: measurements, analysis, etc."""

    note = frozenset()

    def run(self, doc, para: dict, session) -> Any:
        raise NotImplementedError


def cast_like(a: np.ndarray, dtype) -> np.ndarray:
    dtype = np.dtype(dtype)
    if a.dtype == dtype:
        return a
    if dtype.kind in "ui":
        info = np.iinfo(dtype)
        if a.dtype == np.bool_:
            return a.astype(dtype) * (info.max if dtype == np.uint8 else 1)
        return np.clip(np.rint(a), info.min, info.max).astype(dtype)
    return a.astype(dtype)


def to_float(a: np.ndarray) -> np.ndarray:
    return a.astype(np.float32 if a.dtype.itemsize <= 2 else np.float64)


# ---------------------------------------------------------------- registry
def register(cls: type[Op]) -> type[Op]:
    if not cls.title:
        raise ValueError(f"{cls.__name__} has no title")
    REGISTRY[cls.path()] = cls
    return cls


def menu_tree() -> dict:
    """Nested dict of category path -> list of op classes, for building menus."""
    tree: dict = {}
    for path, cls in sorted(REGISTRY.items()):
        node = tree
        for part in (cls.category.split(">") if cls.category else []):
            node = node.setdefault(part, {})
        node.setdefault("__ops__", []).append(cls)
    return tree


def compute_filter(op: "Filter", doc, para: dict, stack: bool = False) -> np.ndarray:
    """Pure part of running a Filter: reads ``doc`` but never mutates it, so it is safe on a worker thread."""
    err = op.check(doc)
    if err:
        raise ValueError(err)
    mask = doc.roi_mask() if "auto_msk" in op.note else None
    if "stack" not in op.note or op.out_dtype != "keep":
        # ops that change dtype must convert every plane or the stack would be inconsistent
        stack = op.out_dtype != "keep"
    return op.run_doc(doc, op.full_para(para), stack, mask)


def commit_filter(op: "Filter", doc, result: np.ndarray, session=None, para: dict | None = None, record: bool = True) -> None:
    """Apply a computed result to the document (GUI thread): undo snapshot, replace, notify, record."""
    doc.replace_data(result, snapshot=op.title if "auto_snap" in op.note else None, keep_range=op.out_dtype == "keep")
    if record and session is not None:
        session.recorder.record(op.path(), op.full_para(para))


def execute(op: Op, session, doc, para: dict | None = None, *, stack: bool = False, record: bool = True):
    """Run an op the way the UI does: validate, snapshot, ROI-mask, apply, notify, record."""
    para = op.full_para(para)
    if doc is None and not isinstance(op, Simple):
        raise RuntimeError("no image open")
    if isinstance(op, Filter):
        result = compute_filter(op, doc, para, stack)
        commit_filter(op, doc, result, session, para, record)
        return None
    err = op.check(doc) if doc is not None else None
    if err:
        raise ValueError(err)
    out = op.run(doc, para, session)
    if record and session is not None:
        session.recorder.record(op.path(), para)
    return out
