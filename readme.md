# ImagePi

## Introduction

The goal of ImagePi is to create a microscope image viewing and processing application written in Python that will work on a Raspberry Pi 5 (Aarch64, Linux, Wayland).

- has a user-friendly interface
- can read/save a variety of image data formats (including using a OMAX 18MP USB 3.0 C-Mount Microscope Camera (Model A35180U3))
- supports ROI settings, drawing, measurement and other mouse operations
- can perform image filtering, morphological operations and other routine operations
- can do image segmentation, area counting, geometric measurement and density analysis.
- is able to perform data analysis, filtering, statistical analysis and others related to the parameters extracted from the image.
- features based on the fiji (Java) application found at (https://imagej.net/software/fiji/) and (https://github.com/fiji/fiji)

OMAX 18MP USB 3.0 C-Mount Microscope Camera (The only tested camera)
- https://omaxmicroscope.com/products/a35180u3
- additional software: https://omaxmicroscope.com/pages/software-download
- Linux Software: `references/omax/ToupTekToupLite.x64.sh` (x86-64 only, **not usable on the Pi 5**; see "Camera SDK" below)

---

# Implementation Plan

Status: **milestones 1-4 implemented and tested on the dev Mac. Pi 5 bring-up (milestone 0) is in progress: ImagePi installs, launches and runs fast on the Pi 5, and the simulated camera works in the app. The real camera works on the Pi from the command line and in the app. Round 5 (2026-10-30, `test4.txt`) confirmed the orientation model: with no Flip box ticked the live view matches the eyepiece, crops land exactly on the selection with every combination of the Flip boxes, snaps in a row work, line measurements agree with three calibrated objectives to about 0.1 % or better, and the camera's hardware crop was measured to land in the vertically mirrored place, as modelled. Round 5 also found that the Preview box of every filter dialog did nothing in the real app (not just the Gaussian slider): fixed in round 6 (2026-10-30, `test5.txt`): the Preview box now works on the Pi (slider, typed values, Median, Auto Threshold, an 18 MP image without freezing) and "Process entire stack" filters a whole stack. Round 6 also found a frame slider that could not stop on frame 2 and a level Shift-drag line that read 180 or -180; both, a frame around the previewed area and status-line feedback on what a filter or Undo did are fixed on the Mac and not yet re-tested on the Pi (`test6.txt`).** Last updated 2026-10-30. See "Implementation status" below for what is and is not verified.

## Implementation status

### Quick start (dev machine)

```
python3.13 -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/python -m imagepi [image files...]      # or: .venv/bin/imagepi
.venv/bin/python -m pytest                         # 202 tests (201 pass, 1 skips without the SDK); UI tests run offscreen
python scripts/install_toupcam.py references/omax/toupcamsdk.20260818   # camera SDK (folder or zip) -> vendor/toupcam (git-ignored)
python scripts/install_luts.py references/fiji/luts                     # optional: Fiji LUTs -> ~/.imagepi/luts
python scripts/make_test_images.py                                      # test images -> ~/imagepi-test (18 MP frame, known shapes, a 3-frame stack)
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
| 0 Pi bring-up | **In progress.** Round 1 (2026-10-22): the install script exited silently and `opencv-python` (GUI build) had been installed instead of the headless one; both fixed. Round 2 (2026-10-22/23): install, `pi_bringup.py check`, the then 144-test suite, launch under Wayland, the desktop launcher, 18 MP pan/zoom, the test-image checks and the simulated camera all ran on the Pi (findings fixed, see below). The real camera (`U3CMOS18000KPA`, USB 3, 5000M) enumerates and opens; the first run's `StartPullModeWithCallback` returned `E_BUSY` (0x800700aa; probably the GUI still held the camera, not confirmed) and the next run's first pull failed with `ctypes.ArgumentError` (a `bytearray` frame buffer; fixed). Round 3 (2026-10-26): the camera works in the app and from the command line; fps was exposure-limited and the image mirrored (fixed on the Mac). Round 4 (2026-10-27, `testsr.txt`, 175 tests passed on the Pi): the fixed-exposure fps match the vendor spec, orientation, crop and second-snap bugs found and fixed on the Mac. Round 5 (2026-10-30, `test4.txt`, 193 tests passed on the Pi): those fixes verified on the camera (orientation, crop, snaps, line measurements, calibration at 4X/10X/40X); the Preview box of the filter dialogs found dead and fixed on the Mac. Round 6 (2026-10-30, `test5.txt`, 195 tests passed on the Pi): the Preview box and the wider slider verified on the Pi; frame slider, level-line angle, a previewed-area frame and status-line feedback for stack filters and Undo fixed on the Mac; `test6.txt` re-tests them. | `scripts/`, `packaging/` |
| 1 MVP | Done. `ImageDoc` (stack, calibration, LUT, ROI, memory-bounded undo), readers/writers (PNG/JPEG/BMP/TIFF/ImageJ-TIFF/OME-TIFF/NPY, 8/16-bit, RGB, stacks, DICOM if `pydicom` is installed), canvas with mip pyramid and vector ROI overlays, 8 ROI tools plus ROI manager, Measure with calibration and scale bar, filters, morphology, thresholds, declarative parameter dialogs with a throttled live preview on a crop, macro record/replay/batch, plugin loader. | `src/imagepi/{core,io,ops,ui,plugins}` |
| 2 Camera | Done against a simulated backend and a fake SDK; the real SDK library loads on macOS. `CameraBackend` ABC, ToupTek backend (pull mode, `PullImageV3`, hotplug, disconnect handling, permission error hint), V4L2/OpenCV fallback, simulated backend (synthetic scene or folder/video replay), live-view panel, snap with metadata, time-lapse, per-objective calibration presets. | `src/imagepi/camera`, `ui/camera_panel.py` |
| 3 Analysis | Done. Connected components, marker watershed, SLIC, Felzenszwalb, Random Walker; Analyze Particles (size/circularity/edge filters, outlines, summary and density); `regionprops` geometry (area, perimeter, centroid, Feret, ellipse, solidity); area fraction; marker counting; results statistics (summary, filter, group-by); Z-project; extended depth of field; Fiji LUT loader (raw, ICOL, text, float text). | `ops/{analyze,segment,stack}.py`, `core/luts.py` |
| 4 Polish | Mostly done. Light/dark/auto theme, touch-friendly sizing option, pinch zoom, `.desktop` launcher, install script, strip-parallel/OpenCV filters, RAM-aware undo budget, benchmark script. | `ui/theme.py`, `packaging/` |

Not done: stitching (stretch goal), pixel classifier (scikit-learn, "later"), a separate docs set.

### Verified vs. not verified

Verified (dev Mac, Python 3.13, arm64): the pytest suite (174 pass; the 175th, `test_real_sdk_loads`, skips unless an SDK is in
`vendor/toupcam/`), including golden checks against scikit-image/SciPy values,
ROI mask/area/calibration cases, the 0.01 mm calibration-slide arithmetic, file round trips, simulated-camera live view,
snap, time-lapse and unplug, and the ToupTek backend driven by a fake SDK. `libtoupcam.dylib` loads and reports version
54.22876.20230702 (2023 SDK) and 60.32327.20260818 (2026 SDK); `test_real_sdk_loads` accepts any SDK version. Offscreen
screenshots of the light and dark themes were checked by eye.

Verified on the Pi 5 (2026-10-22/23, Raspberry Pi 5 Model B Rev 1.1, 16 GB, Raspberry Pi OS Trixie, kernel 6.18, Python 3.13.5, glibc 2.41,
27 W supply, `get_throttled` 0x0, 49 C): `packaging/install_pi.sh` runs to the end in about 2 minutes (apt libraries, venv, `pip install -e`,
SDK extract, udev rule, desktop launcher); the wheels are numpy 2.5.4, scipy 1.18.1, scikit-image 0.26.0, pandas 3.1.0, matplotlib 3.11.2,
PySide6-Essentials 6.12.0 and opencv-python-headless 5.0.0.93 (so `cv2` no longer overrides Qt's plugin path); `pi_bringup.py check` passes, the Qt
platform is `wayland`, and the 144 tests then in the suite all passed. The app launches from the terminal and from the menu, and quits at
once. 18 MP, pan/zoom through the real canvas (`pi_bringup.py render`): repaint at fit 35.7 ms, pan at 100% 31.0 ms, zoom sweep 23.9 ms median
(p95 75 ms); by eye "very smooth". `benchmark.py` (18 MP, 1280x720 viewport): gray 8-bit first paint 54 ms, Median r=3 54 ms, Otsu 103 ms,
Gaussian sigma 2 658 ms; RGB 8-bit Gaussian 2.0 s, Median r=3 235 ms, Otsu 470 ms; RGB 16-bit Gaussian 2.1 s; peak RSS 353 to 536 MB.
Test images: Analyze Particles on `cells_gray.png` found 80 particles (same as the Mac); the other `known_shapes.png` steps (threshold, edge exclusion, scale, undo, ROI tools) raised no problems, though the exact areas were not reported; the 18 MP RGB image
opens instantly and uses about 760 MB; theme is remembered; the simulated camera runs in the app (about 14 fps, limited by the simulator's
own frame generation, 42 ms per frame on the Mac) and time-lapse saved 3 files. The camera (`lsusb`: `0547:3018 Anchor Chips USB3.0 Camera`, 5000M,
device node `crw-rw-rw-`) is enumerated by the SDK as `U3CMOS18000KPA` with resolutions 4912x3684, 2456x1842 and 1228x922, 1.25 um pixels, and
`Open` succeeds. Round 2's findings, all fixed on the Mac and awaiting re-test on the Pi: the camera's `PullImageV3` was given a `bytearray` (the
vendor wrapper's `c_char_p` argument rejects it), pull errors were swallowed silently, outlines were yellow and 1 px (invisible on white objects),
the particle summary was computed but never shown, Undo after Image > Type > 8-bit left the 0-255 display range on 16-bit data (a white
image), Gaussian preview had no slider, exposure only applied on Enter, the simulated auto exposure did nothing, and the Results table showed
10000 as `1e+04`. Gaussian Blur and the gray display path now use OpenCV (Mac, 18 MP gray: Gaussian 449 to 57 ms, fit repaint about 6 ms).

Round 3 (2026-10-26, test3.txt; the 159 tests passed on the Pi in 14.6 s). The real camera `U3CMOS18000KPA` runs: `pi_bringup.py camera` gave 5.0 fps at
4912x3684, 7.5 at 2456x1842 and 7.6 at 1228x922; `camdiag` (vendor SDK only) agreed (SDK 60.31488.20260519, flags `BINSKIP_SUPPORTED BUFFER CMOS GETTEMPERATURE
RAW8 USB30`, no error from any pull, `usbfs_memory_mb` 16, camera on the 5000M bus). In the app: colours correct, gain, auto exposure, hardware crop, snap
(`imagej: True`, Info text with camera, exposure, gain, bits and calibration) and unplug during live view all worked; no tearing, but about 1 s of lag.
The frame rate was set by the exposure, not by USB: the camera starts in auto exposure with a 350 ms limit, so a bare stage gave 7.9 fps and a tissue-paper slide
2.9 fps (1/0.35 s), the same at every resolution and with the crop, and the snap's Info said `exposure_us` 350000. Other round 3 findings: the live image was
mirrored; the Exposure box and the Bit depth box were disabled but looked enabled (the dark palette had no disabled colours), so "the 16-bit setting had no
effect" (the camera has `RAW8` only, so it has no 16-bit mode); the OpenCV V4L2 backend wrote seven "can't open camera by index" lines to the log on every Refresh.
Fixes (Mac, 175 tests): ImagePi caps the auto exposure time (default 100 ms, an "Auto limit" box, remembered per camera) and the live view shows the exposure beside
the fps; Flip horizontally / vertically checkboxes (software flip of previews and stills, remembered per camera, hardware-crop coordinates mapped through the flip);
disabled controls now look disabled; a camera without a >8-bit mode shows "8-bit only"; a second crop adds the first crop's offset and a resolution change clears the
crop; the V4L2 backend probes only `/dev/video*` nodes that exist and silences OpenCV's log while probing; `pi_bringup.py camera` measures with a fixed 10 ms exposure by
default (`--exposure-ms`, `--auto-limit-ms`; `camdiag --exposure-ms`) and prints the exposure per resolution. Not yet re-tested on the Pi: all of these, and Part D of test3
(the particle outlines, summary table, undo, Gaussian slider, 18 MP save) which round 3 did not report. `references/testsr.txt` lists what to run before test3's parts E, F and G.

Round 4 (2026-10-27, `testsr.txt` and part of test3; 175 passed on the Pi in 12 s). USB link: at a fixed 10 ms exposure `pi_bringup.py camera` gave 4.8 fps at 4912x3684, 17.2 at
2456x1842 and 31.8 at 1228x922 (vendor spec 5.6 / 18.1 / 32.2; `camdiag` 4.3 / 16.7 / 31.9), so `usbfs_memory_mb` 16 is not a limit and test3 Part G is not needed. With the
auto limit at 100 ms the camera chose 21 ms on the bare stage. In the app: the overlay shows the exposure (it printed `21.052 ms`, now one decimal), Auto limit works (the user
works with 75 to 150 ms, so 100 stays the default), 32.1 / 17.0 / 4.2 fps without freezing, lag about 0.5 s at 4912x3684 and barely visible at 1228x922 (a frame takes 230 ms
at full size, so that is the camera's rate, not a bug), Bit depth reads "8-bit only", quitting leaves no `VIDEOIO`/`textinput` lines and the camera is free afterwards.
Orientation: with no boxes ticked the live image is the paper mirrored left-to-right; the eyepiece shows the paper rotated 180 degrees; ticking Flip vertically alone matches
the eyepiece. The user wants no ticks to mean the eyepiece view, for a camera mounted with its USB cable pointing to the rear of the microscope. Crop with Flip horizontally ticked
selected a region shifted upward (`a.png`, `a-cropped.png`, `a.mov`): the selection started at y 196 of 922 and the crop came from about y 42 = 922 - 196 - 684, i.e. the vertically
mirrored place, with x correct. One model explains both findings: this camera delivers its frames upside down relative to the eyepiece view, and its hardware ROI is given in the
eyepiece view's coordinates. A second snap in the same live view timed out ("Timed out waiting for still image"), restarting the live view fixed it. Gaussian Blur: "the slider
produces no change in preview mode". Test3 Part D otherwise passed: outlines easy to see (the user wants colour and width selectable later), Otsu + Analyze Particles as
expected, Undo across Image > Type > 8-bit restores histogram and Brightness/Contrast, Gaussian sigma 2 on the 18 MP RGB image about 0.5 s, `out.tif` 54,287,808 bytes and
correct, the simulated camera fine. Speed (`render`, `benchmark.py`), Pi vs round 2: repaint at fit 16.4 ms (was 35.7), pan 15.2 (31.0), zoom sweep 14.1 (23.9); 18 MP gray
Gaussian 153 ms (658), RGB Gaussian 534 ms (2040), gray median r=3 54 ms, Otsu 106 ms (gray) / 483 ms (RGB); peak RSS 356 / 527 MB; `get_throttled` 0x0. The only `textinput` lines
left came from `pi_bringup.py render`, which makes its own QApplication (now silenced too). Test3 E1: a 0.5 mm span measured 0.499998 mm (calibrated with 1 mm at 4x), but the line
row had empty X, Y and Unit columns and no pixel length, so the user could not see the pixel size.
Fixes (Mac, 193 tests): `CameraBackend.delivered_flip` (the ToupTek is `(False, True)`) and the flips are now relative to the eyepiece view, so no ticks match the eyepiece and the hardware
crop is mapped in the eyepiece view's coordinates (settings keys renamed `mirror_h`/`mirror_v` so the old saved flips are not reused); `TouptekCamera.snap` restarts the stream after
each still and once more if a still does not arrive, re-applies the crop, uses the vendor demo's peek call, and its timeout error lists the SDK events seen; the parameter dialog
throttles its preview instead of debouncing it and the runner shows older preview results when no newer one has arrived; line measurements add X, Y (the midpoint), Unit and
`Length (px)`; Set Scale reports "Scale: 1 pixel = 0.0025 mm (400 pixels per mm)" in the status line; `pi_bringup.py roitest` measures where a hardware crop lands (template match of
the crop in the full frame) and `snaptest` repeats snaps, and `render` silences the Wayland text-input line. Not yet re-tested on the Pi: all of these; the orientation model is an inference
from one crop screenshot and the user's description, `roitest` is how it gets confirmed.

Round 5 (2026-10-30, `test4.txt`, results in `references/test4results.txt`; 193 passed on the Pi in 17.9 s).
- **Orientation model confirmed on the camera.** `pi_bringup.py roitest` at all three resolutions: the hardware crop lands at the requested x but at the vertically mirrored y (1228x922: asked for y 92 with a 276-pixel-high
  crop, so the mirrored place is y 554, found at 555; at 2456x1842 asked 184, mirrored 1106, found 1105; at 4912x3684 asked 368, mirrored 2212, found 2210; the test's tolerance is 4 / 8 / 16 px, and x was found within 1 px of the request),
  and the crop's content is not mirrored (match scores 0.99). So this camera delivers frames upside down relative to the eyepiece view and its
  hardware ROI is in the eyepiece view's coordinates, which is what `delivered_flip` and the ROI mapping assume. In the app (camera cable to the rear, both Flip boxes unticked) the live view matches the eyepiece, and every crop showed
  exactly the selection: unticked, Flip horizontally, Flip vertically and both, including a second selection on the cropped picture.
- **Snaps.** `snaptest --count 4` (ImagePi's restart after each still): 2.2 / 2.1 / 2.1 / 2.1 s per 4912x3684 snap, one stream restart each, 31 to 32 live frames pulled afterwards. `snaptest --count 3 --no-restart`: 1.9 / 1.8 / 1.9 s,
  no restarts, 32 to 33 live frames afterwards. So through ImagePi's current still code the SDK delivers repeated stills without a restart when the script pulls the live frames itself; the restart costs about 0.2 s and a short blink.
  It stays on: round 4's failure was in the app, where the live-view thread pulls at the same time, and the script cannot reproduce that. In the app four or more snaps in a row all arrived, also with a crop active (the snap is still the
  full picture), and the live view carried on by itself.
- **Measuring and calibration** (the status line and Results columns added in round 4 work). Pixel size at 4912 px wide (Results `Length` / `Length (px)`): 4X 0.599 um/px (1669.3 px/mm), 10X 0.243 um/px (4118.6 px/mm), 40X 0.0607 um/px (16463.6 px/mm); 4X to 40X is a factor 9.86.
  A hand-drawn line along a known span measured 0.499993 mm (0.5 mm at 4X, scale set from a 1 mm line), 0.499482 mm (0.5 mm at 10X, -0.10 %) and 0.200012 mm (0.2 mm at 40X, +0.006 %); `Length (px)` times the pixel size equals `Length`.
  Quitting leaves no `VIDEOIO`/`textinput` line in the log, and `pi_bringup.py render` prints none either.
- **The Preview box did nothing, for every filter.** The user dragged the Gaussian slider and saw no change (as in rounds 3 and 4), and typing a value and pressing Enter "blurred the image": Enter presses the dialog's OK button, so that was the
  result being applied, not a preview. `OpRunner.run` connected the dialog's preview signals only `if dlg.preview_box.isVisible()`, which is evaluated before `exec()` and so is always False (a widget is not visible until its dialog is
  shown). The signals were never connected, and every earlier test either called `OpRunner._preview` directly or built the dialog by hand, so none went through `run()`. The throttle added in round 4 was not the cause (it stays, it follows
  a drag better). `ParaDialog.whole_stack` had the same flaw the other way round (`isVisible()` read after the dialog closed), so "Process entire stack" was always ignored.
- Smaller: a horizontal line's Angle read `-0.0` (and a level line drawn right to left `-180`): the angle is now computed as `atan2(y0 - y1, x1 - x0)`.
Fixes (Mac, 195 tests): the preview signals are connected when the op can preview, `whole_stack` uses `isVisibleTo(self)`, sliders in the parameter dialogs are at least 260 px wide (left to the layout the Gaussian slider was 84 px wide, so 1 px of
mouse travel moved Sigma by about 0.8), the angle fix, and tests that drive `OpRunner.run` through the real modal dialog (a failure inside closes the dialog instead of hanging the run): a slider move and a typed value must change the pixels the
canvas draws, cancelling must restore them, and "Process entire stack" must filter every frame. A scripted real mouse drag on the Mac now moves Sigma and changes the canvas. Not yet re-tested on the Pi: all of these.

Round 6 (2026-10-30, `test5.txt`, results in `test5results.txt`; 195 passed on the Pi in 15.0 s).
- **Preview verified on the Pi.** Gaussian Blur follows the slider while it is dragged and follows a typed Sigma (no Enter); Cancel restores the original at once; OK took 0.06 s on `cells_gray.png` and Undo restores it; Median's radius slider and
  Auto Threshold's Method/Objects boxes preview too. On the 18 MP RGB image at 100 % a change takes under a second and nothing freezes. The preview only covers a crop of at most 1200 x 1200 px, so at Fit the central square only; that is
  acceptable, but the user wants it shown, and wants to be able to move it. Open: the dialog is modal, so while it is up the main menus and the canvas cannot be used (my test text wrongly said to try a menu); moving the preview area means
  panning or zooming with the dialog open, see "Later". Until then: zoom (Ctrl+1) and pan to the spot before opening the dialog, and the preview covers what is on screen.
- **Frame slider (C1).** With 3 frames the handle only moves at the halfway points and a click in the groove jumped to the last frame, so frame 2 could not be reached with the mouse. Reproduced on the Mac (offscreen Qt uses the same Fusion style as the Pi; the macOS
  style jumps to the click and hides it): the slider's page step was Qt's default 10, more than the 1 to 3 range, so a click in the groove or one wheel notch went to the end.
- **Process entire stack works, and so does Ctrl+Z (resolved by a controlled repeat, `test5c.txt`, results in `testresults5c.txt`).** The first run of part C put all three frames through the filter with the box meant to be unticked, and then frame 1 through it a second
  time with the box meant to be ticked, with no Undo between (`resultsC.csv`: StdDev 3.79 / 3.84 / 3.77, then 2.71 / 3.84 / 3.77; 2.71 is a double blur). The repeat gave every expected result on the Pi: on a single image Ctrl+Z, Ctrl+Shift+Z, a two-step Ctrl+Z, Ctrl+Z
  from the Results table and Edit > Undo all restore the data exactly (rows identical to every digit, and identical to the Mac's); on a freshly opened stack the ticked box filters frames 1 and 3 (StdDev 3.79 / 3.77), the unticked box filters only the frame that was showing
  (frame 3: 39.23 / 3.77), and Ctrl+Z restores a whole stack either way (39.23 / 39.27). The app is right; the first run's pattern is what ticked-then-unticked with no Ctrl+Z gives, so the cause was most likely a mix-up of the two boxes, which the dialog gives no feedback about:
  the status line only said "Gaussian Blur: 0.06 s". Fixed on the Mac in round 6 (see Fixes below): it now reports "all 3 frames" / "frame 3 of 3 only", and "Undo: <op>" / "Nothing to undo".
  Also confirmed on the Pi: closing a changed image asks "Save changes to ...?" (Save / Discard / Cancel, also after Undo), and the log of that session has no `Traceback`.
- **Level line angle (D2).** Shift-drag left to right read 0, 45 degrees up-right read 45, but right to left read 180 when the pointer ended above the line and -180 below it: the Shift snap computed the end point with `cos`/`sin` of the snapped angle (1e-16 rounding errors).
  The snap now uses exact unit vectors; across 20,000 random drags per direction the axis angles are always exactly 0, 180, 90 and -90. Without Shift a hand-drawn line pointing left still reads about 180 or -180 by which side the pointer ended (the
  `atan2` convention, -180 to 180; ImageJ does the same).
- D3: no `VIDEOIO`/`textinput` lines and no `Traceback` in `gui.log`.
Fixes (Mac, 202 tests): the status line says which frames a filter changed ("Gaussian Blur: 0.06 s, frame 3 of 3 only" / ", all 3 frames"; it reports what `base.effective_stack` says really happened, since an op that changes the pixel type, such as Auto Threshold, always converts every frame, and says nothing extra on a single image) and what Undo/Redo did ("Undo: Gaussian Blur", "Nothing to undo", `ImageDoc.undo_label`/`redo_label`); the frame slider has page step 1 (a click or wheel notch moves one frame) and tick marks for stacks of up to 60 frames; the line tool's Shift snap is exact; the canvas draws a dashed "Preview area" frame when the preview leaves out more than 5 % of the
picture on screen (not at 100 % zoom, and not for a 1228 x 922 picture at Fit, which misses only 13 px strips); tests for each, including the unticked stack box. Not yet re-tested on the Pi: all of these.

On the dev Mac an 18 MP 8-bit image repaints in about 20 ms (fit, 100%, 25% and 400% zoom) and a 7x7 median takes
about 33 ms; peak RSS for a 16-bit RGB 18 MP image was about 0.8 GB. These are Mac numbers, not Pi numbers.

**Not verified, and needing the hardware:**
- On the Pi 5: round 6's fixes (the status line messages for filters on stacks and for Undo/Redo, the frame slider stepping one frame at a time with ticks, the "Preview area" frame on a big image at Fit, Shift-drag lines reading exactly 180 right to left). Everything older was re-tested in rounds 4 to 6 ("Process entire stack", Undo and the Preview box are verified on the Pi).
- Any real camera: 16-bit RGB48 pull (the U3CMOS18000KPA has none; it is untestable on it), hotplug events. Verified on the OMAX camera in rounds 3 to 5: frames, RGB byte order, hardware ROI crop and its position (`roitest`, in the eyepiece view's coordinates), the eyepiece-view orientation (`delivered_flip`), snaps in a row (script and app), unplug, fps at each resolution with a fixed 10 ms exposure, calibrated measurements at 4X/10X/40X.
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
- **`QSlider`'s page step is 10 by default, whatever the range**, so on a short slider (a 3-frame stack) a click in the groove or one wheel notch jumps to the end. The macOS style jumps to the click position instead, so the Mac never showed it; the Pi's Fusion style (which Qt's offscreen platform also uses) does. Set `setPageStep(1)`. A slider with 3 values also moves its handle only at the halfway points, so it needs tick marks.
- **Key presses in an offscreen script do nothing unless the window is active**: call `activateWindow()` and `QTest.qWaitForWindowActive(w, 1500)` before every `QTest.keyClick`, also right after a modal dialog closed, or Ctrl+M/Ctrl+Z are silently dropped (`QAction` shortcuts only fire in the active window). A failed assertion inside a callback run during a modal `exec()` leaves the dialog open and hangs the script: use `faulthandler.dump_traceback_later(...)` as a watchdog.
- **`QTest.qWait(2500)` as one long wait did not deliver a worker thread's queued result** in an offscreen script (the app looked busy for ever and a later run delivered it); polling with short `qWait(30)` calls or `qtbot.waitUntil` does. Do not read "nothing happened" from a script that waits once.
- **`QWidget.isVisible()` is False for every widget of a dialog that is not on screen**, before `exec()` and again once it has closed. Reading it there made the Preview box inert and "Process entire stack" always off for the whole project until round 5; use `isVisibleTo(dialog)` or keep the flag you passed in. A test that fakes the dialog or calls the callback directly cannot see this: drive `OpRunner.run` through a real `exec()`.
- ROI shapes are `QGraphicsItem`s with cosmetic pens as planned; the image itself is drawn in `drawBackground` from the display pipeline rather than as a scene item, which is what makes the viewport-only rendering work.

### Later (noted, not scheduled)

- **Window height:** the main window cannot be made shorter than its panels and menus need at start-up (about the combined height of the camera panel, results panel and menus), and closing the results panel only lowers the limit to what the remaining panels need. Let the panels hide part of their content (scroll areas or collapsible groups) when the window is made smaller than the default minimum.
- **Particle outlines:** let the user choose the outline colour and width (now fixed red, 2 px).
- **Preview on big images:** the live preview works on a crop of the visible area capped at 1200 x 1200 pixels (`PREVIEW_MAX`), so at Fit zoom on an 18 MP image only the central square blurs until OK is pressed (the user finds that acceptable, and a "Preview area" frame now shows it).
- **Movable preview area, and menus while a filter dialog is open (asked for in round 6):** the parameter dialog is modal (`exec()`), so the main window and the canvas are blocked while it is open. Make the dialogs of previewing filters non-modal (`show()`, handle `finished`), as ImageJ does: the user can then pan and zoom, and the preview, which already follows `canvas.view_rect()`, is recomputed on `view_changed`. Needs care: reject the dialog when its tab changes or closes, ignore a second Run while one is open, and rewrite the dialog tests (they rely on `exec()` returning). Dragging the frame itself is the other way to move it.
- **Snap restart:** try `TouptekCamera.RESTART_AFTER_SNAP = False` in the app (about 0.2 s and a blink saved per snap); it is on because round 4's second snap timed out without it.
- Full-size live view runs at about 4 fps (the camera's own rate with RGB conversion on the Pi), so about 0.5 s of lag: use 2456x1842 for focusing; Snap always takes the full size.

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
- Whether the first run's `E_BUSY` was only the GUI holding the camera. It did not come back in round 3 (camera idle, nothing else running); if it does, `references/test3.txt` Part G lists the candidates (stale process, `usbfs_memory_mb`, USB power or bandwidth, the 2026-05-19 SDK build).
- (Resolved in round 4) Real Pi 5 USB3 throughput: 31.8 / 17.2 / 4.8 fps at a fixed 10 ms exposure against the vendor's 32.2 / 18.1 / 5.6; `usbfs_memory_mb` 16 is no limit. Full size is about 15 % under spec, probably the SDK's RGB conversion on the Pi's CPU.
- (Resolved in round 5) The camera's hardware ROI is in the eyepiece view's coordinates with frames delivered upside down: `roitest` measured the vertically mirrored crop position at all three resolutions.
- Whether the stream restart after each snap is still needed in the app. `snaptest --no-restart` shows the SDK delivering repeated stills without it in a script; the app's live-view thread pulling at the same time is the untested difference.
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
- **Plugins:** the `view` tuples generate a `QDialog` with a throttled live preview on a downsampled or ROI crop, so no per-op UI code is needed. The macro recorder logs lines for replay and batch runs.
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
