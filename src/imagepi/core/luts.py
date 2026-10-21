"""Lookup tables: built-ins plus Fiji/ImageJ LUT file formats.

Supported files: raw 768-byte planar RGB, 800-byte NIH ICOL (32-byte header),
tab/space separated text (``Index Red Green Blue`` with optional header), and
float text with one RGB triple per row in 0..1 (e.g. cividis.txt).
LUTs are always (256, 3) uint8 arrays.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

_ramp = np.arange(256, dtype=np.uint8)


def _stack(r, g, b) -> np.ndarray:
    return np.stack([np.asarray(r), np.asarray(g), np.asarray(b)], axis=1).astype(np.uint8)


def _interp(points) -> np.ndarray:
    """Piecewise-linear 256 samples from [(index, r, g, b), ...]."""
    pts = np.asarray(points, float)
    x = np.arange(256)
    return _stack(*(np.interp(x, pts[:, 0], pts[:, c]).round() for c in (1, 2, 3)))


def grays() -> np.ndarray:
    return _stack(_ramp, _ramp, _ramp)


def inverted(lut: np.ndarray) -> np.ndarray:
    return lut[::-1].copy()


def fire() -> np.ndarray:
    # Control-point reconstruction of the classic "Fire" map.
    return _interp([
        (0, 0, 0, 0), (32, 0, 0, 90), (64, 90, 0, 140), (96, 190, 0, 100), (128, 240, 40, 20),
        (160, 255, 110, 0), (192, 255, 170, 0), (224, 255, 230, 60), (255, 255, 255, 255),
    ])


def ice() -> np.ndarray:
    return _interp([
        (0, 0, 156, 255), (64, 0, 190, 240), (128, 100, 200, 255), (192, 180, 230, 255), (255, 255, 255, 255),
    ])


def spectrum() -> np.ndarray:
    import colorsys

    return np.array([[round(c * 255) for c in colorsys.hsv_to_rgb(i / 255 * 0.85, 1, 1)] for i in range(256)], np.uint8)


def channel(name: str) -> np.ndarray:
    z = np.zeros(256, np.uint8)
    cols = {"red": (_ramp, z, z), "green": (z, _ramp, z), "blue": (z, z, _ramp),
            "cyan": (z, _ramp, _ramp), "magenta": (_ramp, z, _ramp), "yellow": (_ramp, _ramp, z)}
    return _stack(*cols[name])


BUILTIN = {
    "Grays": grays,
    "Inverted Grays": lambda: inverted(grays()),
    "Fire": fire,
    "Ice": ice,
    "Spectrum": spectrum,
    "Red": lambda: channel("red"),
    "Green": lambda: channel("green"),
    "Blue": lambda: channel("blue"),
    "Cyan": lambda: channel("cyan"),
    "Magenta": lambda: channel("magenta"),
    "Yellow": lambda: channel("yellow"),
}


def _resample(a: np.ndarray) -> np.ndarray:
    if len(a) == 256:
        return a
    x = np.linspace(0, 1, len(a))
    xi = np.linspace(0, 1, 256)
    return _stack(*(np.interp(xi, x, a[:, c]).round() for c in range(3)))


def load_lut(path: str | os.PathLike) -> np.ndarray:
    data = Path(path).read_bytes()
    n = len(data)
    if n == 768:
        return np.frombuffer(data, np.uint8).reshape(3, 256).T.copy()
    if n == 800 and data[:4] == b"ICOL":
        return np.frombuffer(data[32:], np.uint8).reshape(3, 256).T.copy()
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as e:
        raise ValueError(f"unrecognised LUT format: {path}") from e
    rows = []
    for line in text.splitlines():
        parts = line.replace(",", " ").split()
        try:
            vals = [float(p) for p in parts]
        except ValueError:
            continue  # header
        if len(vals) >= 4:
            vals = vals[1:4]  # drop index column
        if len(vals) == 3:
            rows.append(vals)
    if not rows:
        raise ValueError(f"unrecognised LUT format: {path}")
    a = np.asarray(rows, float)
    if a.max() <= 1.0:
        a = a * 255
    return _resample(a.round().astype(np.uint8))


def load_lut_dir(directory: str | os.PathLike) -> dict[str, np.ndarray]:
    out = {}
    for p in sorted(Path(directory).iterdir()):
        if p.suffix.lower() in (".lut", ".txt") and p.name.lower() != "readme.txt":
            try:
                out[p.stem] = load_lut(p)
            except ValueError:
                pass
    return out


def user_lut_dir() -> Path:
    return Path(os.environ.get("IMAGEPI_LUTS", Path.home() / ".imagepi" / "luts"))


def all_luts() -> dict[str, np.ndarray]:
    luts = {k: f() for k, f in BUILTIN.items()}
    d = user_lut_dir()
    if d.is_dir():
        luts.update(load_lut_dir(d))
    return luts
