# Scopi
**Microscope image viewing, measurement and analysis for the Raspberry Pi 5, with live view and snaps from a USB microscope camera.**

[![CI](https://github.com/DRGV/scopi/actions/workflows/ci.yml/badge.svg)](https://github.com/DRGV/scopi/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)

Scopi is a stand-alone Python application (Qt, via PySide6) modelled on **[ImageJ](https://imagej.net/ij/) and [Fiji](https://imagej.net/software/fiji/)**.
It gives a classroom or a bench the familiar ImageJ way of working (open a picture, draw a selection, set the scale, measure, filter, threshold, count particles)
on a Raspberry Pi 5 with the **OMAX 18 MP USB 3.0 C-mount microscope camera (model A35180U3)** attached. It has also been run on a Mac for viewing and
processing pictures (other Linux computers and Windows are untested), and it should work with other OMAX or ToupTek based USB cameras (see
[Other cameras](usage.md#other-cameras)).

- **[usage.md](usage.md)**: installation, starting Scopi, adding the camera, live view versus snaps, calibration, troubleshooting
- **[requirements.txt](requirements.txt)**: the Python packages it needs
- **[documents/](documents/README.md)**: implementation notes, design decisions, test history and how to extend the project
- **[CONTRIBUTING.md](CONTRIBUTING.md)**: set up a development copy, run the tests, send a change
- **[SECURITY.md](SECURITY.md)**: how to report a vulnerability privately, and what counts as one

## What Scopi can do

### View and manage images
- Opens PNG, JPEG, BMP, GIF, WebP, TIFF (8-bit and 16-bit grey, color, multi-frame stacks, ImageJ TIFF and OME-TIFF), NumPy `.npy` files and, with the optional
  `pydicom`, DICOM. Saves TIFF (ImageJ-compatible, or OME-TIFF), PNG, JPEG, BMP and `.npy`.
- Tabs for several images, drag-and-drop, recent files, a frame slider for stacks, undo and redo that is limited by available memory, not by a fixed count.
- Pans and zooms an 18-megapixel picture smoothly on a Pi 5: only the visible part is drawn, from a pyramid of reduced copies.
- Brightness and contrast with a histogram, 11 built-in look-up tables (Grays, Fire, Ice, Spectrum and more) plus any LUT files you add, such as those from Fiji.
- Light, dark or automatic theme, touch-friendly sizing and pinch zoom.

### Select, calibrate and measure
- Selection tools: rectangle, oval, polygon, freehand, line, point and angle, with a pan tool and an ROI Manager to keep, show and measure many selections.
- Measure area, perimeter, mean, minimum, maximum, standard deviation, position, line length and angle, and point value into a **Results** table that exports to CSV
  and summarises its columns. Selections can limit a filter or a measurement to part of the picture.
- **Spatial calibration** from a known distance (for example a stage micrometer) with a scale bar, and **named presets for each microscope objective**.
  Every measurement is reported in real units, and the pixel size is shown so you can check it.

### Process
- Filters: Gaussian blur, median, mean, minimum, maximum, unsharp mask, edge detection (Sobel), Laplace, difference of Gaussians, rolling-ball background
  subtraction and a 3 x 3 smooth.
- Thresholds: automatic (Otsu, Li, Triangle, Isodata, Yen, Mean), manual and local. Morphology: erode, dilate, open, close, fill holes, skeletonize, outline,
  remove small objects, watershed. Conversion between 8-, 16- and 32-bit and RGB to grey, crop, flip and rotate.
- Every parameter dialog has a **live preview** that follows its slider. Filters  can be applied to one frame or to a whole stack (the status line says which).
- Stacks: Z projection (maximum, minimum, mean, sum, median, standard deviation) and extended depth of field.

### Analyse
- **Analyze Particles**: size and circularity limits, exclusion of objects on the edge, outlines drawn on the picture, one row per particle and a summary.
  Area fraction, marker counting and shape measurements (area, perimeter, centroid, Feret diameter, fitted ellipse, solidity) for labelled regions.
- Segmentation: connected components, marker watershed, SLIC superpixels, Felzenszwalb and Random Walker.

### Automate and extend
- A macro recorder that logs every step as text, replays it and runs it over many files. Plugins are single Python files (`*_plg.py`) dropped into
  `~/.scopi/plugins`, or packages that register an entry point.
- Each operation declares its parameters once; Scopi builds the dialog, the live preview and the macro recording from that declaration.

### Microscope camera
- Connects to the ToupTek-based **OMAX A35180U3** (4912 x 3684, 1.25 um pixels) through the vendor's SDK, with a fallback for ordinary USB (V4L2) cameras and a
  built-in simulated camera for trying everything without hardware.
- **Live view** in its own tab at the resolution you choose (about 32 frames per second at 1228 x 922) and **snaps** at the full 4912 x 3684, saved as
  ImageJ TIFFs that keep the exposure, gain, objective and scale.
- Auto exposure with a limit (so the live view stays quick), manual exposure, gain, white balance, a hardware crop chosen by dragging a rectangle on the live view,
  flips relative to the view through the eyepiece, interval time-lapse to a folder, and clean handling of an unplugged camera.

## Where it has been tested

| | |
|---|---|
| Target | Raspberry Pi 5 (16 GB), Raspberry Pi OS Trixie (64-bit), Python 3.13, native Wayland |
| Camera | OMAX 18 MP USB 3.0 C-mount camera A35180U3 (ToupTek `U3CMOS18000KPA`): **the only camera tested** |
| Calibration | Checked with a 0.01 mm stage micrometer at 4x, 10x and 40x: known distances of 0.2 to 0.5 mm read within about 0.1 % |
| Development | macOS (Apple silicon), Python 3.13 |
| Automated tests | 228 tests (`pytest`), all passing on the Mac; the Qt tests run without a display. The 204 that existed in the last Pi round all passed on the Pi |
| Not tested | Python 3.12 on the Pi (the automated tests do pass under 3.12 on the Mac), other Linux computers, Windows, any camera other than the one above, the V4L2 fallback camera and DICOM files (no device and no `pydicom` on the test machines) |

The history of what was tested, found and fixed on the Pi is in [documents/implementation.md](documents/implementation.md).

## Getting started

```
git clone https://github.com/DRGV/scopi && cd scopi
bash packaging/install_pi.sh /path/to/toupcamsdk.zip     # Raspberry Pi 5; see usage.md for a Mac or no camera
.venv/bin/scopi
```

[usage.md](usage.md) explains all of this step by step, including how to add the camera, take a snap, calibrate an objective and fix common problems.

## What is in the repository

| Folder or file | Contents |
|---|---|
| `src/scopi/` | The application: `core` (image, selections, calibration, results), `io` (file formats), `ops` (filters, morphology, thresholds, segmentation, analysis), `camera` (ToupTek, V4L2, simulated), `ui` (windows, canvas, panels, camera panel), `plugins`, `resources` (the application icon) |
| `tests/` | The automated tests (`pytest`) |
| `scripts/` | Helpers: Pi bring-up and diagnostics, benchmark, test images, camera SDK and LUT installers |
| `packaging/` | Raspberry Pi installer, desktop launcher, USB permission rule for the camera |
| `documents/` | [Implementation notes and reference material](documents/implementation.md) and the [Raspberry Pi test plans](documents/pi-test-plans) |
| `LICENSE`, `usage.md`, `requirements.txt`, `pyproject.toml` | The GPL-3.0 license text, usage guide, Python packages, project metadata |
| `CONTRIBUTING.md`, `SECURITY.md`, `CITATION.cff` | How to contribute, how to report a vulnerability, how to cite Scopi |
| `.github/` | The CI workflow (lint, tests on Linux and macOS, package check), issue and pull request templates, Dependabot |
| `vendor/` | Where the camera SDK is installed on your machine (not in the repository; see below) |

## Thanks

Scopi would not exist without **ImageJ** and **Fiji**. A huge thank you to the people who made them.

**ImageJ** was created by Wayne Rasband at the US National Institutes of Health and has been shaped for decades by its users and plugin authors. It taught
a generation of scientists and students how to measure a picture. **Fiji** ("Fiji Is Just ImageJ"), built by a community of developers, turned it into the
batteries-included platform that many laboratories rely on. The menus, the selection tools, the way *Set Scale*, *Measure*, *Analyze Particles*, *Threshold*
and the Results table work, and the idea of macros and plugins all come from them. Scopi re-implements these ideas in Python from ImageJ's published
documentation so that they run well on a Raspberry Pi; **no ImageJ or Fiji code is used or included**.

If you use ImageJ or Fiji in your work, please cite them:

- Schneider, C. A., Rasband, W. S. and Eliceiri, K. W. (2012). NIH Image to ImageJ: 25 years of image analysis. *Nature Methods* 9, 671-675.
  [doi:10.1038/nmeth.2089](https://doi.org/10.1038/nmeth.2089)
- Schindelin, J. et al. (2012). Fiji: an open-source platform for biological-image analysis. *Nature Methods* 9, 676-682.
  [doi:10.1038/nmeth.2019](https://doi.org/10.1038/nmeth.2019)

Projects: [ImageJ](https://imagej.net/ij/), [Fiji](https://imagej.net/software/fiji/), [Fiji source](https://github.com/fiji/fiji).

To cite Scopi itself, see [CITATION.cff](CITATION.cff) (GitHub's "Cite this repository" button reads it).

Also thanks to **[ImagePy](https://github.com/Image-Py/imagepy)** (Python, BSD-style license), an earlier attempt at an ImageJ-like application in Python. Its design
ideas (parameter dialogs generated from a declaration, macro recording, plugin discovery by file name) inspired parts of Scopi; no ImagePy code was copied.

And to **ToupTek** and **OMAX** for the camera and its SDK, and to the authors and maintainers of every package listed below.

## Open-source software Scopi uses

None of these is bundled in the repository: `pip` installs them from PyPI under their own licenses (see [requirements.txt](requirements.txt)). The licenses
below are the ones the installed packages declare; please read each project's own license text before redistributing anything.

| Package | What Scopi uses it for | License | Project |
|---|---|---|---|
| Qt for Python: **PySide6-Essentials**, Shiboken6 (Qt 6) | The whole user interface | LGPL-3.0, GPL-2.0 or GPL-3.0 (or a commercial license from Qt) | [qt.io/qt-for-python](https://www.qt.io/qt-for-python) |
| **NumPy** | Image arrays and numeric work | BSD-3-Clause | [numpy.org](https://numpy.org) |
| **SciPy** | Filters, morphology, interpolation | BSD-3-Clause | [scipy.org](https://scipy.org) |
| **scikit-image** | Thresholds, segmentation, region measurements, drawing shapes | BSD-3-Clause | [scikit-image.org](https://scikit-image.org) |
| **OpenCV** (`opencv-python-headless`) | Fast Gaussian and median filters, resizing and the display pipeline | Apache-2.0 (the wheels also bundle other libraries, such as FFmpeg under the LGPL) | [opencv.org](https://opencv.org), [opencv-python](https://github.com/opencv/opencv-python) |
| **Pillow** | PNG, JPEG, BMP, GIF and WebP files | MIT-CMU (HPND) | [python-pillow.org](https://python-pillow.org) |
| **tifffile** | TIFF, ImageJ TIFF and OME-TIFF files | BSD-3-Clause | [github.com/cgohlke/tifffile](https://github.com/cgohlke/tifffile) |
| **imageio** | Reading other image formats | BSD-2-Clause | [imageio.readthedocs.io](https://imageio.readthedocs.io) |
| **pandas** | The Results tables | BSD-3-Clause | [pandas.pydata.org](https://pandas.pydata.org) |
| **Matplotlib** | Point-in-polygon geometry for polygon and freehand selections | Matplotlib license (PSF-based) | [matplotlib.org](https://matplotlib.org) |
| **pydicom** (optional) | Reading DICOM files | MIT | [pydicom.github.io](https://pydicom.github.io) |
| **pytest**, **pytest-qt** (tests only) | The automated tests | MIT | [pytest.org](https://pytest.org), [pytest-qt](https://github.com/pytest-dev/pytest-qt) |
| **Python** | The language and standard library | PSF license | [python.org](https://www.python.org) |

## The camera SDK is not open source and is not included

The **ToupTek camera SDK** (`toupcam`, a vendor library plus its Python wrapper) is proprietary software under the vendor's own terms. It is **not** part of this
repository and must not be committed to it: `vendor/` is git-ignored. Each user downloads the SDK from the vendor
([OMAX software page](https://omaxmicroscope.com/pages/software-download), or ToupTek) and installs it with `scripts/install_toupcam.py`. Scopi loads its
shared library at run time. Without it, Scopi works as an image viewer and analysis tool, and the simulated camera is still available. OMAX's own
ToupLite program (an x86-64 installer on the same page) is not needed and does not run on a Raspberry Pi.

## Affiliation and trademarks

Scopi is an independent project. It is not affiliated with, endorsed by or sponsored by OMAX, ToupTek, Raspberry Pi Ltd, the US National Institutes of Health, or the
ImageJ, Fiji or ImagePy projects. Raspberry Pi is a trademark of Raspberry Pi Ltd; all other product and company names are the trademarks of their owners. They are
used here only to say which hardware and software Scopi works with or is modelled on.

## License

Copyright (C) 2026 Dwight Vieira

Scopi is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later version** (SPDX: `GPL-3.0-or-later`). It is distributed in the hope that it will be
useful, but **without any warranty**, without even the implied warranty of merchantability or fitness for a particular purpose. The full text is in
[LICENSE](LICENSE); a plain-language summary is at [gnu.org/licenses/gpl-3.0](https://www.gnu.org/licenses/gpl-3.0.html). You may use, study, share and change
Scopi. If you distribute it, or a version you have changed, you must do so under the same license and make the source available. Every source file carries a
short notice with the license identifier and the copyright line.

The packages in the table above keep their own licenses. They are permissive (BSD, MIT, Apache-2.0, PSF) or LGPL licenses, which can be combined with GPL version 3
code; this is a summary, not legal advice. The **ToupTek camera SDK** is separate proprietary software: you download it yourself, Scopi loads it at run time and it is
not distributed with Scopi, so it is not covered by the GPL.
