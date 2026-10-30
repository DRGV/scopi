from . import base
from .base import REGISTRY, Filter, Op, Simple, execute, register

# importing the modules registers their ops
from . import analyze, color, filters, morphology, segment, stack, threshold  # noqa: E402,F401

__all__ = ["REGISTRY", "Filter", "Op", "Simple", "base", "execute", "register"]
