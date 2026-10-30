import numpy as np
import pytest

from scopi import io
from scopi.core import Calibration, ImageDoc


@pytest.mark.parametrize("ext", [".png", ".tif", ".bmp", ".npy"])
def test_roundtrip_gray8(tmp_path, blobs, ext):
    p = tmp_path / f"a{ext}"
    io.save_image(ImageDoc(blobs), str(p))
    d = io.open_image(str(p))
    assert d.data.shape == (1, 100, 120) and np.array_equal(d.data[0], blobs)


def test_roundtrip_jpeg(tmp_path, blobs):
    io.save_image(ImageDoc(blobs), str(tmp_path / "a.jpg"))
    d = io.open_image(str(tmp_path / "a.jpg"))
    assert d.data.shape == (1, 100, 120) and abs(int(d.data.mean()) - int(blobs.mean())) < 3


@pytest.mark.parametrize("ext", [".png", ".tif"])
def test_roundtrip_16bit(tmp_path, ext):
    a = (np.arange(60 * 80).reshape(60, 80) * 7).astype(np.uint16)
    io.save_image(ImageDoc(a), str(tmp_path / f"a{ext}"))
    d = io.open_image(str(tmp_path / f"a{ext}"))
    assert d.dtype == np.uint16 and np.array_equal(d.data[0], a)


@pytest.mark.parametrize("ext", [".png", ".tif", ".ome.tif"])
def test_roundtrip_rgb(tmp_path, ext):
    a = np.random.default_rng(0).integers(0, 255, (30, 40, 3), dtype=np.uint8)
    io.save_image(ImageDoc(a), str(tmp_path / f"a{ext}"))
    d = io.open_image(str(tmp_path / f"a{ext}"))
    assert d.rgb and np.array_equal(d.data[0], a)


@pytest.mark.parametrize("ext", [".tif", ".ome.tif"])
def test_stack_and_calibration_roundtrip(tmp_path, ext):
    a = np.random.default_rng(1).integers(0, 255, (5, 30, 40), dtype=np.uint8)
    doc = ImageDoc(a, rgb=False)
    doc.calibration = Calibration(0.25, "µm")
    io.save_image(doc, str(tmp_path / f"s{ext}"))
    d = io.open_image(str(tmp_path / f"s{ext}"))
    assert d.n_frames == 5 and not d.rgb and np.array_equal(d.data, a)
    assert d.calibration.calibrated and d.calibration.pixel_size == pytest.approx(0.25, rel=1e-4)


def test_float_tiff_and_metadata(tmp_path):
    a = np.random.default_rng(2).random((20, 30)).astype(np.float64)
    io.save_image(ImageDoc(a), str(tmp_path / "f.tif"))
    assert np.allclose(io.open_image(str(tmp_path / "f.tif")).data[0], a)
    doc = ImageDoc((a * 1000).astype(np.uint16))
    doc.metadata = {"exposure_ms": 12.5, "gain": 100}
    io.save_image(doc, str(tmp_path / "m.tif"))
    assert io.open_image(str(tmp_path / "m.tif")).metadata == {"exposure_ms": 12.5, "gain": 100}


def test_unknown_write_extension(tmp_path, doc):
    with pytest.raises(ValueError):
        io.save_image(doc, str(tmp_path / "x.xyz"))


def test_16bit_to_jpeg_uses_display_range(tmp_path):
    a = np.linspace(0, 1000, 100 * 100).reshape(100, 100).astype(np.uint16)
    d = ImageDoc(a)
    d.display_range = (0, 1000)
    io.save_image(d, str(tmp_path / "j.jpg"))
    assert io.open_image(str(tmp_path / "j.jpg")).data.max() > 200
