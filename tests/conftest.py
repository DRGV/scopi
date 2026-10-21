import numpy as np
import pytest

from imagepi.core import ImageDoc
from imagepi.core.session import Session


@pytest.fixture
def session():
    return Session()


@pytest.fixture
def blobs():
    """Three separated discs and a gradient background, uint8."""
    img = np.zeros((100, 120), np.uint8)
    yy, xx = np.mgrid[:100, :120]
    for cy, cx, r in [(25, 25, 10), (60, 70, 15), (80, 20, 6)]:
        img[(yy - cy) ** 2 + (xx - cx) ** 2 <= r * r] = 200
    return img


@pytest.fixture
def doc(blobs):
    return ImageDoc(blobs, "blobs.png")
