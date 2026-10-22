# ImagePi

## Introduction

The goal of ImagePi is to create a microscope image viewing and processing application written in Python that will work on a Raspberry Pi 5 (Aarch64, Linux, Wayland).

- has a user-friendly interface
- can read/save a variety of image data formats (including using a OMAX 18MP USB 3.0 C-Mount Microscope Camera (Model A35180U3) )
- supports ROI settings, drawing, measurement and other mouse operations
- can perform image filtering, morphological operations and other routine operations
- can do image segmentation, area counting, geometric measurement and density analysis.
- is able to perform data analysis, filtering, statistical analysis and others related to the parameters extracted from the image.
- features based on the fiji (Java) application found at `references/fiji`

OMAX 18MP USB 3.0 C-Mount Microscope Camera
- https://omaxmicroscope.com/products/a35180u3
- additional software: https://omaxmicroscope.com/pages/software-download
- Linux Software: `references/omax/ToupTekToupLite.x64.sh` (x86-64 only, **not usable on the Pi 5**; see "Camera SDK" below)

Reference material (the original Fiji and ImagePy checkouts, the OMAX downloads and an SDK copy) lives in `references/`, which is git-ignored and exists only on the dev Mac. See "What the reference material contains".

---

# Implementation Plan

Status: **milestones 1-4 implemented and tested on the dev Mac. Pi 5 bring-up (milestone 0) is in progress: ImagePi installs and launches on the Pi 5 and the camera is detected and opened, but starting the live view fails with `E_BUSY` (0x800700aa), so no frame from the real camera has been seen yet.** Last updated 2026-10-22. See "Implementation status" below for what is and is not verified.

## Implementation status

### Quick start (dev machine)

```
python3.13 -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/python -m imagepi [image files...]      # or: .venv/bin/imagepi
.venv/bin/python -m pytest                         # 144 tests (143 pass, 1 skips without the SDK); UI tests run offscreen
python scripts/install_toupcam.py references/omax/toupcamsdk.20260818   # camera SDK (folder or zip) -> vendor/toupcam (git-ignored)
python scripts/install_luts.py references/fiji/luts                     # optional: Fiji LUTs -> ~/.imagepi/luts
python scripts/make_test_images.py                                      # test images -> ~/imagepi-test (18 MP frame, known shapes)
```

### Quick start (Raspberry Pi 5, Raspberry Pi OS Trixie)

```
git clone https://github.com/DRGV/imagepi && cd imagepi && git switch dev   # dev until it is merged to main
bash packaging/install_pi.sh /path/to/toupcamsdk.zip                        # as your normal user: not sudo, and bash, not sh
.venv/bin/python scripts/pi_bringup.py check                               # then: render | camera | camdiag
.venv/bin/python scripts/benchmark.py                                      # display pipeline and filters on an 18 MP image
.venv/bin/imagepi                                                          # or the ImagePi entry in the application menu
```

`install_pi.sh` installs the Qt runtime libraries with apt, builds `.venv`, installs ImagePi with its dependencies and the
`test` extra, installs the SDK and udev rule, and writes the desktop launcher. It is safe to re-run. The step-by-step Pi
test plans (`references/test1.txt`, `test2.txt`, ...) are local files, not part of the repo.

### Raspberry Pi setup notes

Found during the first Pi run (2026-10-22: Pi 5, Python 3.13.5, glibc 2.41, aarch64, kernel 6.18):

- **The `pip` package name is not always the import name.**

  | `import` | `pip install` |
  |---|---|
  | `cv2` | `opencv-python-headless` (ImagePi's dependency; not `cv2`, not `sv2`) |
  | `skimage` | `scikit-image` |
  | `PIL` | `pillow` |

  `sv2` on PyPI is an unrelated project (a typo for `cv2`) and does not build here; if it is in the venv, `pip uninstall sv2`.
- **Use `opencv-python-headless`, not `opencv-python`.** The GUI wheel (5.0.0.93, checked) bundles its own Qt 5 in `cv2/qt/`,
  which has only an xcb plugin, and `cv2/config-3.py` points `QT_QPA_PLATFORM_PLUGIN_PATH` at it on import. ImagePi imports
  `cv2` before it creates the `QApplication`, so PySide6 then cannot find its own (Wayland or xcb) platform plugin. Both wheels
  provide `cv2`, so uninstall every OpenCV variant before installing the headless one. `install_pi.sh` now does this.
- **Run `install_pi.sh` with bash as your own user.** `sh` is dash on the Pi, not bash, and `sudo bash ...` leaves a root-owned
  venv and writes the launcher to root's home; the script now refuses both. Its glibc check no longer pipes `ldd --version`
  through `head`: under `set -o pipefail` that can end the script silently, a likely cause of the first run printing nothing.
  Trace a failing run with `bash -x packaging/install_pi.sh`.
- **The link can be slow.** PySide6 (about 260 MB of wheels), OpenCV and matplotlib took minutes and one download timed out; pip
  now runs with `--retries 10 --timeout 60`, and a re-run reuses the pip cache.
- **Keep the venv to ImagePi's dependencies.** An editor such as ReText in the same venv pulls in PyQt6 beside PySide6.
- `vendor/` is wholly git-ignored: it holds the SDK zips, a partial download and the extracted `vendor/toupcam/`.

### What exists

| Milestone | State | Where |
|---|---|---|
| 0 Pi bring-up | **In progress.** Round 1 (2026-10-22): the install script exited silently and `opencv-python` (GUI build) had been installed instead of the headless one; both fixed. Round 2: `install_pi.sh` completes, `pi_bringup.py check` passes, Qt platform is wayland, the app launches and its menus and buttons work, the camera (`U3CMOS18000KPA`) is listed and connects, but Start live view fails: `StartPullModeWithCallback` returns `E_BUSY` (0x800700aa). Round 3 (`references/test3.txt`) isolates the cause with `pi_bringup.py camdiag`. | `scripts/`, `packaging/` |
| 1 MVP | Done. `ImageDoc` (stack, calibration, LUT, ROI, memory-bounded undo), readers/writers (PNG/JPEG/BMP/TIFF/ImageJ-TIFF/OME-TIFF/NPY, 8/16-bit, RGB, stacks, DICOM if `pydicom` is installed), canvas with mip pyramid and vector ROI overlays, 8 ROI tools plus ROI manager, Measure with calibration and scale bar, filters, morphology, thresholds, declarative parameter dialogs with debounced live preview on a crop, macro record/replay/batch, plugin loader. | `src/imagepi/{core,io,ops,ui,plugins}` |
| 2 Camera | Done against a simulated backend and a fake SDK; the real SDK library loads on macOS. `CameraBackend` ABC, ToupTek backend (pull mode, `PullImageV3`, hotplug, disconnect handling, permission error hint), V4L2/OpenCV fallback, simulated backend (synthetic scene or folder/video replay), live-view panel, snap with metadata, time-lapse, per-objective calibration presets. | `src/imagepi/camera`, `ui/camera_panel.py` |
| 3 Analysis | Done. Connected components, marker watershed, SLIC, Felzenszwalb, Random Walker; Analyze Particles (size/circularity/edge filters, outlines, summary and density); `regionprops` geometry (area, perimeter, centroid, Feret, ellipse, solidity); area fraction; marker counting; results statistics (summary, filter, group-by); Z-project; extended depth of field; Fiji LUT loader (raw, ICOL, text, float text). | `ops/{analyze,segment,stack}.py`, `core/luts.py` |
| 4 Polish | Mostly done. Light/dark/auto theme, touch-friendly sizing option, pinch zoom, `.desktop` launcher, install script, strip-parallel/OpenCV filters, RAM-aware undo budget, benchmark script. | `ui/theme.py`, `packaging/` |

Not done: stitching (stretch goal), pixel classifier (scikit-learn, "later"), a separate docs set.

### Verified vs. not verified

Verified (dev Mac, Python 3.13, arm64): the pytest suite (143 pass; the 144th, `test_real_sdk_loads`, skips unless an SDK is in
`vendor/toupcam/`), including golden checks against scikit-image/SciPy values,
ROI mask/area/calibration cases, the 0.01 mm calibration-slide arithmetic, file round trips, simulated-camera live view,
snap, time-lapse and unplug, and the ToupTek backend driven by a fake SDK. `libtoupcam.dylib` loads and reports version
54.22876.20230702 (2023 SDK) and 60.32327.20260818 (2026 SDK); `test_real_sdk_loads` accepts any SDK version. Offscreen
screenshots of the light and dark themes were checked by eye.

Verified on the Pi 5 (2026-10-22, Raspberry Pi 5 Model B Rev 1.1, 16 GB, Raspberry Pi OS Trixie, kernel 6.18, Python 3.13.5, glibc 2.41):
`packaging/install_pi.sh` runs to the end (apt libraries, venv, `pip install -e`, SDK extract, udev rule, desktop launcher);
the wheels are numpy 2.5.4, scipy 1.18.1, scikit-image 0.26.0, pandas 3.1.0, matplotlib 3.11.2, PySide6-Essentials 6.12.0 and
opencv-python-headless 5.0.0.93; `pi_bringup.py check` passes and the Qt platform is `wayland`; the app launches and its menus and
buttons work. `benchmark.py` on an 18 MP 8-bit gray image (1280x720 viewport): first paint 36 ms, zoom 10-28 ms, Median r=3 54 ms,
Otsu threshold 106 ms, Gaussian sigma 2 657 ms, peak RSS 354 MB. The camera (`lsusb`: `0547:3018 Anchor Chips USB3.0 Camera`, on the USB 3 bus) is
enumerated by the SDK as `U3CMOS18000KPA` with resolutions 4912x3684, 2456x1842 and 1228x922, 1.25 um pixels, and `Open` succeeds.
`StartPullModeWithCallback` then fails with `E_BUSY` (0x800700aa) in the bring-up script and the GUI shows "No live image"; the cause is not yet known.

On the dev Mac an 18 MP 8-bit image repaints in about 20 ms (fit, 100%, 25% and 400% zoom) and a 7x7 median takes
about 33 ms; peak RSS for a 16-bit RGB 18 MP image was about 0.8 GB. These are Mac numbers, not Pi numbers.

**Not verified, and needing the hardware:**
- On the Pi 5: the pytest suite, the desktop launcher, `pi_bringup.py render` and the GUI checks with the test images (Parts C to G of `references/test2.txt` were not reported), USB 3 throughput and fps at each camera resolution.
- Any real camera: a started live view (currently `E_BUSY`), the RGB byte order and upside-down assumptions (the SDK header says RGB / not flipped on Linux and macOS, and ImagePi sets both options explicitly), 16-bit RGB48 pull, hardware ROI crop, hotplug events.
- The `V4L2Camera` fallback and DICOM reading (no device / `pydicom` not installed here).
- The `imagepi.plugins` entry-point route (folder discovery is tested).

### Camera SDK versions

`references/omax/toupcamsdk.20260818` (v60.32327.20260818) is a drop-in replacement for the 2023 SDK (v54.22876.20230702) that the
tests ran against: every constant and method the backend uses is present, `PullImageV3`/`ToupcamFrameInfoV3` are unchanged,
and the arm64 build still needs only GLIBC 2.17. Its layout differs (`linux/arm64/glibc/`, `mac/`), which the loader and
`install_toupcam.py` now both handle. Its udev rule adds vendor id `04b4` next to `0547`; `packaging/99-toupcam.rules`
has both. `toupnamsdk` (network cameras) and `uvchamsdk` (UVC-class cameras) are not used.

The Pi uses a `toupcamsdk.20260519` zip (also the 2026 layout) extracted into `vendor/toupcam/`. On the Mac `vendor/` is empty
since the reference files moved to `references/`; `references/toupcam/` is the old `vendor/toupcam/` (2023 and 2026 files
merged, loads as v60.32327.20260818). Run `install_toupcam.py` (see the Mac quick start) to repopulate `vendor/toupcam/`.

### Things found while implementing

- **The vendor `toupcam.py` has a bug in `Toupcam.HotPlug`** (still present in the 2026 SDK) on Linux/macOS: it references `__hotplug_cb` without `cls.`, so name mangling makes it raise `NameError`. `TouptekCamera.watch_hotplug` registers the callback through the library handle instead, and `unwatch_hotplug` clears it on exit.
- **`Toupcam.EnumV2()` and `HotPlug` blocked for 75 s and several minutes** on the dev Mac with no camera attached (this may be an effect of the sandbox the tools ran in). The UI therefore always enumerates and registers hotplug on worker threads.
- The wrapper looks for `libtoupcam.*` next to `toupcam.py` first; `load_sdk()` copies the right native library there from `vendor/toupcam/<platform>/`.
- OpenCV 5's `medianBlur` accepts 7x7 on 16-bit/float but OpenCV 4.x only 3x3/5x5; the median filter falls back to threaded SciPy strips on `cv2.error`.
- ROI shapes are `QGraphicsItem`s with cosmetic pens as planned; the image itself is drawn in `drawBackground` from the display pipeline rather than as a scene item, which is what makes the viewport-only rendering work.

## Decisions

| Topic | Decision |
|---|---|
| GUI toolkit | **PySide6 (Qt6)**, native Wayland. wxPython (used by original ImagePy) has no aarch64 PyPI wheel. |
| Code base | **Fresh package** (`imagepi/`) that ports *ideas* from ImagePy, not its code. |
| Target OS / Python | **Raspberry Pi OS Trixie, Python 3.13** (PySide6 aarch64 wheels are tagged `manylinux_2_39`, so glibc >= 2.39; Trixie has 2.41, Bookworm has 2.36). Develop on macOS with a Python 3.13 venv (`.venv` is now 3.13). |
| First milestone | Viewer + ROI/measure + filters. Camera is milestone 2, analysis is milestone 3. |
| Avoid | numba, wxPython, vispy, QtMultimedia (its FFmpeg plugin is a known Pi packaging risk). |

## What the reference material contains

### `references/fiji`
Only the Fiji launcher/packaging repo (212 MB of the 214 MB is `.git`). No ImageJ source, jars or menu definitions. Useful for:
- `luts/` (about 37 LUTs). Formats: raw 768-byte planar RGB, 800-byte NIH "ICOL" (32-byte header + 768 bytes), tab-separated text (`Index Red Green Blue`, header optional), float text (`cividis.txt`). Fire/Grays/Ice/Spectrum are built into `ij.jar` and must be regenerated.
- A feature checklist. Core ImageJ behavior (Analyze Particles, Measure, thresholds, scale bar) must be reimplemented from the published ImageJ docs. Fiji is GPL, so **do not copy code**.

### `references/imagepy` (wxPython, last commit Feb 2024)
- `sciapp/` is a mostly GUI-agnostic core (`App`, `Manager`, `Image`, shapes/ROI, `Table`, `Filter`/`Simple` actions). It is not importable headless as shipped (`report.py` imports wx).
- `sciwx/` and `imagepy/app` are wx-only. Plugins are discovered by file suffix (`*_plg.py`, `*_plgs.py`, `*_tol.py`) under `menus/`.
- Needs numba, wxPython, vispy; has undeclared scipy/matplotlib; uses NumPy idioms removed or deprecated in NumPy 1.24+/2.x; untested on Python 3.13+. License text is ambiguous (4-clause-style file vs "BSD 3-clause" in `setup.py`).
- **Ideas to port:**
  - `Image` model: stack, ROI, undo snapshot, LUT, unit.
  - `Filter` lifecycle and `note` flags: type check, `auto_snap`, `auto_msk`, `preview`.
  - Declarative `para`/`view` tuples that auto-generate parameter dialogs.
  - Macro-line recording (`Menu>Item{para}`) and replay.
  - Suffix-based plugin discovery.
  - The menu catalogue: File, Edit, Image, Process, Analysis, Table.
  - `RegionCounter`, which maps to `skimage.measure.regionprops`.

### Camera SDK (`references/omax/`)

| Item | Finding |
|---|---|
| `references/omax/ToupTekToupLite.x64.sh` | x86-64 only ToupLite GUI installer (no arch check, installs to `/usr/local/ToupLite`). **Will not run on the Pi 5. Not needed.** |
| `references/omax/SDK/toupcamsdk.20230702.zip` | The real SDK, v54.22876.20230702, 633 files. This is what ImagePi uses. |
| `toupnamsdk.20230702.zip` | ToupTek *network* camera SDK. Not for this USB camera. Ignore. |
| `OMAXTwainSetup.exe`, `OMAXDshowSetup.exe`, `UvcHamSetup.exe` | Windows TWAIN/DirectShow/UVC drivers. Irrelevant to the Pi. |
Inside `toupcamsdk.20230702.zip`, the files we use (inspected read-only; nothing has been run yet):
- `linux/arm64/libtoupcam.so`: verified AArch64 ELF, needs only GLIBC >= 2.17 (runs on Trixie and Bookworm). armhf/armel/x64/x86 variants are also included.
- `mac/x64+arm64/libtoupcam.dylib`: verified universal Mach-O (x86_64 + arm64), macOS 11+. It is ad-hoc/linker-signed only, not notarized. If macOS blocks loading, the likely fix is `xattr -dr com.apple.quarantine <dir>` (not yet tested). This lets us develop and test against the real camera on the dev Mac.
- `python/toupcam.py`: a single-file ctypes wrapper, no native extension to build. It loads `libtoupcam.so`/`.dylib` from a given directory or the system loader.
- `python/samples/simplest.py` (pull-mode loop) and `qt.py` (PyQt5 live view, the template for our PySide6 camera panel).
- `inc/toupcam.h` and `doc/en.html`, the full API manual.
- `linux/udev/99-toupcam.rules`: `SUBSYSTEM=="usb", ATTRS{idVendor}=="0547", MODE="0666"`. The same rule is embedded in the ToupLite installer. The camera shows up as `0547:3018` ("Anchor Chips USB3.0 Camera") on the Pi.
- `extra/imagepro`, `extra/imagelib`: headers only, no Linux libraries. Ignore (ImagePi does its own processing).

API points that shape the design:
- Use pull mode: `StartPullModeWithCallback` plus `PullImageV3(buf, bStill, bits, rowPitch, info)`. The callback must return quickly and must never call `Stop`/`Close` (deadlock).
- RGB is the default. RAW (`OPTION_RAW`) and 16-bit/RGB48 (`OPTION_BITDEPTH`, `OPTION_RGB`) are available. These options can only be changed while the camera is stopped. A `bits`/`OPTION_RGB` mismatch costs a conversion.
- `Toupcam_HotPlug` is available on Linux and macOS. Register it for plug/unplug notifications, then re-run `EnumV2`. Also handle `EVENT_DISCONNECTED`.
- On Linux a "permission denied" error means the udev rule is missing.
- Camera specs (vendor page): 1/2.3" color CMOS, 4912x3684 at 5.6 fps, 2456x1842 at 18.1 fps, 1228x922 at 32.2 fps. A full-res RGB24 frame is about 54 MB.
- **Licensing:** the SDK binaries are vendor-controlled. Do not commit them or the zip to a public repo. Keep them in a git-ignored `vendor/toupcam/` populated by an install script from the user's `references/omax/SDK` zip.

### Risks not yet verified
- Why `StartPullModeWithCallback` returns `E_BUSY` on the Pi. Candidates: another process holding the camera, the Linux `usbfs_memory_mb` limit for large transfers, USB power or bandwidth, the 2026-05-19 SDK build. `pi_bringup.py camdiag` separates them.
- Real Pi 5 USB3 throughput and fps at each camera resolution.
- Qt rendering speed under the Pi's Wayland compositor (labwc/Wayfire).
- aarch64 wheels for opencv-python-headless (the headless 5.0.0.93 aarch64 wheel exists on PyPI; installed and imports on the Pi 5, see "Verified vs. not verified").

## Architecture

A GUI-agnostic core, so it can be tested headless and on the dev Mac.

```
imagepi/                     (src layout, pyproject.toml, Python >= 3.12)
  core/      image.py (ImageDoc: ndarray stack, dtype, calibration, LUT, ROI, undo ring)
             roi.py (Rect/Ellipse/Polygon/Freehand/Line/Point/Angle -> mask via skimage.draw)
             calibration.py (um/px, per-objective presets, scale bar), manager.py
             results.py (ResultsTable on a pandas DataFrame), commands.py (macro recording)
  io/        readers.py / writers.py registry by extension (Pillow, tifffile, imageio,
             pydicom, OME-TIFF metadata, raw); 8/16-bit and RGB
  ops/       filters, morphology, threshold, segment, analyze, color
             (NumPy/SciPy/scikit-image/OpenCV-headless; each op is a plugin class with
             title, note flags and a para+view spec as in ImagePy)
  plugins/   loader.py (entry-point + folder discovery; import errors logged, not fatal)
  camera/    base.py (CameraBackend ABC: enumerate/open/exposure/gain/resolution/start/stop/snap)
             touptek.py (wraps vendored toupcam.py; linux/arm64 .so on Pi, mac .dylib on dev Mac)
             v4l2.py (OpenCV fallback), simulated.py (folder/video replay for tests)
  ui/        main_window.py, canvas.py (QGraphicsView, mip pyramid), tools.py (ROI/measure
             state machine), para_dialog.py (auto-built from view), panels (histogram,
             results, ROI manager, camera controls), theme.py
  app.py, __main__.py
tests/       pytest (core/io/ops headless; UI via pytest-qt with QT_QPA_PLATFORM=offscreen)
packaging/   99-toupcam.rules, imagepi.desktop, Pi install script
vendor/toupcam/   git-ignored SDK files (see licensing note)
```

Design points:
- **Canvas:** convert to an 8-bit display buffer only for the visible viewport, with a downsampled mip pyramid for 18 MP images. Apply window/level and LUT with `cv2.LUT` or `np.take`. Show it via a zero-copy `QImage` in a `QGraphicsView`, and add OpenGL only if measurement shows it is needed. ROI, measurement and scale-bar overlays are `QGraphicsItem`s in vector space.
- **Live view:** the SDK callback only signals. A `QThread` pulls preview frames (for example 1228x922 at about 30 fps) into preallocated buffers and emits `QImage`. Drop frames rather than queue them. A full-res still is captured on demand, with optional RAW/16-bit.
- **Threading:** run NumPy/SciPy/OpenCV work in worker threads (they release the GIL). Use a process pool for pure-Python-heavy jobs so the GIL cannot stall the UI.
- **Plugins:** the `view` tuples generate a `QDialog` with debounced live preview on a downsampled or ROI crop, so no per-op UI code is needed. The macro recorder logs lines for replay and batch runs.
- **Dependencies:** PySide6, numpy, scipy, scikit-image, opencv-python-headless, pillow, tifffile, imageio, pandas, matplotlib (consider pyqtgraph if too slow). Optional: pydicom, shapely, scikit-learn (pixel classifier, later).

## Milestones

0. **Pi bring-up spike**
   - Install Trixie and a Python 3.13 venv. Verify wheels for PySide6, scikit-image, opencv-python-headless and scipy.
   - Open a Qt window with `QT_QPA_PLATFORM=wayland` and time a full-res 18 MP pan/zoom prototype.
   - Unzip the SDK into `vendor/toupcam/`, install the udev rule, run `lsusb`, and run `simplest.py` against `linux/arm64/libtoupcam.so`. Measure fps and USB throughput at each resolution.
1. **MVP (viewer, ROI/measure, filters)**
   - Project skeleton, `ImageDoc`, and I/O (PNG/JPEG/BMP/TIFF, 8/16-bit, stacks, OME-style metadata).
   - Main window, canvas (zoom/pan/fit, LUT, brightness/contrast, histogram), undo.
   - ROI tools (rect, oval, polygon, freehand, line, point, angle) and an ROI manager.
   - Measure (length, angle, area, mean/min/max/std into a ResultsTable, CSV export), plus spatial calibration and scale bar.
   - Filters: mean, Gaussian, median, min/max, unsharp, Sobel, Laplace, DoG, background subtraction.
   - Morphology: erode, dilate, open, close, fill holes, skeleton, watershed. Thresholds: manual, Otsu, Li, Triangle, local.
   - Plugin loader, parameter dialogs, macro recorder.
2. **Camera**
   - `CameraBackend`, ToupTek backend and simulated backend. Port the SDK `qt.py` sample to PySide6.
   - Use `PullImageV3`, `HotPlug` and `EVENT_DISCONNECTED`.
   - Live-view panel (exposure, gain, white balance, resolution, ROI crop). Snap and save with metadata (exposure, gain, calibration). Time-lapse. V4L2 fallback.
3. **Analysis**
   - Segmentation: threshold to label, watershed split, SLIC, Felzenszwalb, Random Walker.
   - Analyze Particles (size/circularity filters, exclude edges, overlays, summary).
   - Geometry from `regionprops`: area, perimeter, centroid, Feret, ellipse fit, solidity.
   - Area fraction and density, marker counting, and results-table statistics (descriptive, filtering, histograms, scatter, group stats).
   - LUT library ported from `references/fiji/luts`. Stack ops (Z-project, extended depth of field). Stitching is a stretch goal.
4. **Polish and packaging:** HiDPI and touch-friendly sizing, light/dark theme, `.desktop` launcher, Pi install script, docs, and a memory/profiling pass (18 MP x 16-bit stacks; the Pi 5 has 4, 8 or 16 GB).

## Verification

- **Dev Mac:** `pytest` for core/io/ops, including golden-image tests against scikit-image values and ROI mask/area/calibration unit tests. UI smoke tests use pytest-qt offscreen. The simulated camera drives live-view tests, and the macOS dylib allows tests against the real camera.
- **Pi 5:** run `python -m imagepi` under Wayland and check the following:
  - Open an 18 MP TIFF and pan/zoom smoothly.
  - Apply Gaussian and median filters with preview and no UI freeze.
  - Draw ROIs and measure against the 0.01 mm calibration slide that ships with the camera.
  - Run threshold plus Analyze Particles on a test image.
  - Run live view at preview resolution, then snap a full-res frame and save a TIFF with metadata.
  - Watch RSS memory and CPU in `htop`.

  `scripts/make_test_images.py` writes the images for the first four checks, including `known_shapes.png`, whose results are
  exact: Analyze Particles finds 9 objects (six of 10000 px and three of 7845 px, 8 with "Exclude on edges"), and at 0.5 um/px
  the areas read 2500 um² and 1961.25 um².
