# ImagePi documents

Material for people who want to understand, test or extend ImagePi. To *use* ImagePi read the [usage guide](../usage.md); for what it can do read the
[readme](../readme.md).

| | |
|---|---|
| [implementation.md](implementation.md) | How ImagePi was built and tested: implementation status, what was verified on which machine, design decisions, architecture, milestones, what Fiji / ImagePy / the camera SDK taught us, and the problems found and fixed |
| [pi-test-plans/](pi-test-plans) | The step-by-step Raspberry Pi 5 test plans (below) |
| This page | A short guide to extending ImagePi: new operations, plugins, file formats, cameras, tests |

## The Raspberry Pi test plans

Each plan is a plain-text checklist for a person sitting at the Pi: what to run, what to expect, and exactly what to send back. They were written one round
at a time, each starting from what the previous round found, so [implementation.md](implementation.md) is the place to read the findings and these files are the
places to repeat the checks. They are written for the maintainer's own Pi and camera (user `sch`, the project in `~/Code/imagepi`, test images in
`~/imagepi-test`) and sometimes name files that are not in the repository, such as the raw result files of earlier rounds; adapt the names to your set-up.
Run `scripts/make_test_images.py` first: several steps use the images it writes.

| Plan | What it covers |
|---|---|
| [test1.txt](pi-test-plans/test1.txt) | Round 1: install with `packaging/install_pi.sh`, `pi_bringup.py check`, `render` and `camera`, `benchmark.py` |
| [test2.txt](pi-test-plans/test2.txt) | Round 2: the application on the Pi (launch under Wayland, 18 MP pan and zoom, filters, particles, undo, the simulated camera) and the first real-camera run |
| [test3.txt](pi-test-plans/test3.txt) | Round 3: the camera from the command line and in the application, re-tests of round 2's fixes, and a contingency plan if the camera reports "busy" |
| [testsr.txt](pi-test-plans/testsr.txt) | A re-test to run before the later parts of test3: auto-exposure limit, image orientation, snaps, results table |
| [test4.txt](pi-test-plans/test4.txt) | Orientation against the eyepiece, where a hardware crop lands (`pi_bringup.py roitest`), snaps in a row (`snaptest`), the Gaussian slider, calibrated line measurements |
| [test5.txt](pi-test-plans/test5.txt) | The Preview box, "Process entire stack", wider sliders and the angle of a level line |
| [test5c.txt](pi-test-plans/test5c.txt) | A controlled repeat of the stack and Ctrl+Z checks with exact numbers to compare |
| [test6.txt](pi-test-plans/test6.txt) | The status-line messages, the frame slider, the "Preview area" frame and exact Shift-snapped angles |

To test a new Pi, a new release or a different camera, run the plans in order up to the latest and then write the next one the same way (stop at the first
failure, say exactly what to send back). The automated tests (`python -m pytest`, below) cover everything that does not need the hardware.

## Extending ImagePi

The code is organised so that the GUI-free parts can be tested without a display: `core/` (image document, selections, calibration, results), `io/`, `ops/` and
`camera/` know nothing about Qt widgets; `ui/` builds the windows on top of them. [implementation.md](implementation.md) has the architecture and the reasons for
the choices.

### Add an operation (a filter, a threshold, an analysis)

An operation is a class that declares its parameters once. ImagePi builds the menu entry, the parameter dialog, the live preview, the "Process entire stack" box
and the macro recording from that declaration (`src/imagepi/ops/base.py`).

- A **`Filter`** turns a 2-D plane into a plane of the same shape (it is called once per channel of a colour image, and once per frame of a stack). Set `note` to
  choose what ImagePi offers: `preview`, `stack` (the "Process entire stack" box), `auto_snap` (an undo step) and `auto_msk` (limit to the current selection).
- A **`Simple`** operation works on the whole document and can add rows to the Results table (measurements, particle analysis).
- Parameters are `Int`, `Float`, `Choice`, `Bool` and `Str` entries in `view`, with ranges and defaults. A bounded `Int` or `Float` gets a slider as well as a box.
- Put built-in operations in the modules of `src/imagepi/ops/` and decorate the class with `@register`. Add a test to `tests/test_ops.py`; the existing tests
  compare filters with SciPy and scikit-image results.

### Add a plugin without touching ImagePi

Any file named `*_plg.py` in `~/.imagepi/plugins/` (or in a folder listed in the `IMAGEPI_PLUGINS` environment variable) is loaded when ImagePi starts; a package can
also register an entry point in the group `imagepi.plugins`. A plugin that fails to import is logged and skipped, never fatal. This complete plugin adds
**Process > Filters > Gamma...** with a slider and a live preview:

```python
# ~/.imagepi/plugins/gamma_plg.py
import numpy as np

from imagepi.ops import Filter, register
from imagepi.ops.base import Float


@register
class Gamma(Filter):
    title, category = "Gamma", "Process>Filters"                     # the menu path: Process > Filters > Gamma...
    view = [Float("gamma", "Gamma", (0.1, 5.0), 1.0, 2)]             # label, range, default, decimals

    def run(self, img, para):
        top = float(np.iinfo(img.dtype).max) if img.dtype.kind in "ui" else float(img.max()) or 1.0
        return top * (img.astype(np.float32) / top) ** para["gamma"]   # ImagePi casts the result back to the image's own type
```

### Add a file format

`src/imagepi/io/registry.py` keeps readers and writers by file extension. Decorate a function with `@reader(".ext", ...)` that returns an `ImageDoc`, or with
`@writer(".ext", ...)` that saves one (see `readers.py` and `writers.py`), and add a round-trip test to `tests/test_io.py`.

### Add a camera

Subclass `CameraBackend` (`src/imagepi/camera/base.py`) and implement `enumerate`, `open`, `close`, resolutions, exposure and gain, `start`, `stop`, `pull` and
`snap`; then add it to `available_backends()` in `camera/__init__.py`. `touptek.py` (a wrapper around the vendor's SDK) and `simulated.py` (a synthetic scene,
or a folder or video replayed as a camera) are the two templates. The base class already does the orientation and hardware-crop mapping, so a new backend only
declares how its frames arrive relative to the view through the eyepiece. The camera tests drive the ToupTek backend through a fake SDK that is as strict as the
real wrapper (it rejects the wrong argument types), which found a real bug once: keep your fakes strict.

### Run the tests

```
.venv/bin/python -m pip install -e ".[test]"
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q      # 204 tests, about 15 seconds; the Qt tests run without a window
```

`tests/test_ui.py` drives the real windows. When you test a dialog, go through the real entry point (`OpRunner.run` with a modal dialog) and not by calling the
callback directly: that is how a Preview box that was never connected stayed hidden for four rounds. Offscreen scripts and tests need the window to be active
(`activateWindow()` and `QTest.qWaitForWindowActive`) before sending key presses.

### Things that will bite you

- Use `opencv-python-headless`, never `opencv-python` (its bundled Qt stops PySide6 from loading).
- `QWidget.isVisible()` is false for everything in a dialog that is not on screen; use `isVisibleTo(dialog)`.
- The ToupTek SDK is vendor software: do not commit it. `vendor/` is git-ignored.
- On the Pi, `QT_QPA_PLATFORM=offscreen` is for tests; the application itself picks Wayland.
