# Using Scopi

How to install Scopi, start it, open images, connect the microscope camera and take measurements. For what the application can do see the
[readme](readme.md); for how it was built and tested see [documents/](documents/README.md).

- [1. What you need](#1-what-you-need)
- [2. Install on a Raspberry Pi 5](#2-install-on-a-raspberry-pi-5)
- [3. Install on a Mac or another computer](#3-install-on-a-mac-or-another-computer)
- [4. Start Scopi and find your way around](#4-start-scopi-and-find-your-way-around)
- [5. Working with images](#5-working-with-images)
- [6. Calibrate and measure](#6-calibrate-and-measure)
- [7. Process and analyse](#7-process-and-analyse)
- [8. Using the camera](#8-using-the-camera)
- [9. Where Scopi keeps things](#9-where-scopi-keeps-things)
- [10. Helper scripts](#10-helper-scripts)
- [11. Troubleshooting](#11-troubleshooting)
- [12. Keyboard shortcuts](#12-keyboard-shortcuts)

## 1. What you need

| | |
|---|---|
| Computer | A Raspberry Pi 5 (Raspberry Pi OS **Trixie**, 64-bit, Python 3.13) is the target. A Mac has also been used, for viewing, processing and developing. Other Linux computers and Windows have not been tried. Python 3.13 is the version that has been tested; the project declares 3.12 or newer, which is untested. |
| Camera (optional) | The **OMAX 18 MP USB 3.0 C-mount camera (A35180U3)**, which is a ToupTek `U3CMOS18000KPA`. It is the only camera that has been tested (see [Other cameras](#other-cameras)). Plug it into a **USB 3 (blue) port**. Without it everything except the live camera works, and a simulated camera is built in for trying the camera panel. |
| Camera SDK | The ToupTek SDK (`toupcamsdk*.zip`) is the vendor's software and is **not** included in this repository; download it from the [OMAX software page](https://omaxmicroscope.com/pages/software-download) or from ToupTek. Only the camera needs it. |
| Network | The installer downloads several hundred MB of Python packages (PySide6 alone is about 260 MB). On a slow link it can take several minutes; it retries by itself. |
| Python packages | Listed in [requirements.txt](requirements.txt). The installers below take care of them. |

## 2. Install on a Raspberry Pi 5

Do this as your normal user, **not with `sudo`**, and run the script with **`bash`** (not `sh`).

1. Update Raspberry Pi OS Trixie and make sure `git` is installed:
   ```
   sudo apt update && sudo apt full-upgrade -y && sudo apt install -y git
   ```
2. Get the code:
   ```
   git clone https://github.com/DRGV/scopi
   cd scopi
   ```
3. Download the ToupTek camera SDK zip (see section 1) and note where it is, for example `~/Downloads/toupcamsdk.20260519.zip`.
4. Run the installer, giving it the SDK zip (make sure to update the download location and filename as needed; leave the zip out if you have no camera yet):
   ```
   bash packaging/install_pi.sh ~/Downloads/toupcamsdk.20260519.zip
   ```
   It installs the Qt runtime libraries with `apt` (it asks for your password), creates the virtual environment `.venv`, installs Scopi and everything it
   needs, installs the camera SDK into `vendor/toupcam/` and the USB permission rule (`udev`, see section 11) so a normal user may open the camera, and adds an
   **Scopi** entry to the application menu. It is safe to run again if something went wrong half-way. To see what it does step by step:
   `bash -x packaging/install_pi.sh ...`.
5. Check the installation:
   ```
   .venv/bin/python scripts/pi_bringup.py check
   ```
   It prints the Python and glibc versions, imports each package (`ok` or `FAIL`) and reports the Qt platform in use (`wayland` on the Pi's own desktop).
6. Start it from the application menu (**Scopi**) or from the terminal:
   ```
   .venv/bin/scopi
   ```
   You can pass it image files on the command line: `.venv/bin/scopi cells.tif`.

Adding the camera SDK later: `.venv/bin/python scripts/install_toupcam.py ~/Downloads/toupcamsdk.20260519.zip --udev`, then unplug and re-plug the camera.

Optional extras:
- **Test images** (a 54 MB 18 MP picture, a 3-frame stack and shapes whose areas are known exactly): `.venv/bin/python scripts/make_test_images.py` writes them
  to `~/scopi-test`.
- **Fiji look-up tables**, if you have a Fiji copy: `.venv/bin/python scripts/install_luts.py /path/to/fiji/luts`, or have the installer do it by running
  `SCOPI_FIJI_LUTS=/path/to/fiji/luts bash packaging/install_pi.sh`.
- **Run the test suite**: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q` (expect every test to pass; one is skipped if the SDK is missing).

## 3. Install on a Mac or another computer

A Mac is the only computer other than the Pi that has been used. Other Linux computers and Windows should be able to view and process pictures but have not been tried, and the camera driver only knows how to work with macOS and Linux.

```
git clone https://github.com/DRGV/scopi
cd scopi
python3.13 -m venv .venv                # 3.13 is the tested version (3.12 is declared but untested)
.venv/bin/pip install -e ".[test]"      # Scopi, its dependencies from pyproject.toml, and pytest
.venv/bin/python -m scopi             # or: .venv/bin/scopi [image files...]
```

`pip install -r requirements.txt` followed by `pip install -e .` does the same with the dependency list from [requirements.txt](requirements.txt). Add
`pydicom` (`pip install pydicom`) if you want to open DICOM files.

To use the camera on a Mac, populate `vendor/toupcam/` from the SDK: `.venv/bin/python scripts/install_toupcam.py /path/to/toupcamsdk.zip`. On macOS the script also
removes the quarantine flag that macOS puts on downloaded files, from the extracted SDK files only, because macOS may refuse to load a downloaded library otherwise;
add `--keep-quarantine` to leave it.
Run the tests with `.venv/bin/python -m pytest` (the Qt tests run without a window).

Important: install **`opencv-python-headless`**, never `opencv-python`. The GUI build of OpenCV carries its own Qt that stops PySide6 from starting.
Both provide `import cv2`, so uninstall every OpenCV variant before installing the headless one (`pip uninstall opencv-python opencv-contrib-python`).

## 4. Start Scopi and find your way around

```
.venv/bin/scopi              # or  python -m scopi  from the project folder, or the Scopi entry in the application menu
```

The window has:

- **Menus**: File, Edit, Image, Process, Analyze, Camera, Macro, View, Help.
- **A toolbar** with the drawing tools (Pointer, Rectangle, Oval, Polygon, Freehand, Line, Point, Angle, Hand), the zoom buttons (Fit, 100 %, +, -) and **Measure**.
- **Tabs**, one per open image. A stack (several frames) gets a frame slider under the picture with a label such as `2/3`. Click the slider and use
  Left/Right (one frame), Home or End; clicking in the groove or turning the wheel also moves one frame.
- **Panels** (show or hide them from the View menu): **Camera** (left), **Brightness / Contrast** and **ROI Manager** (right) and **Results** (bottom).
- **A status line** at the bottom. It tells you what just happened ("Gaussian Blur: 0.06 s, all 3 frames", "Undo: Gaussian Blur", the scale after Set Scale)
  for a few seconds, so look at it after a command.

View > Theme switches between light, dark and automatic; "Touch-friendly Sizing" makes controls larger for a touch screen.

## 5. Working with images

- **Open**: File > Open (Ctrl+O), drag a file onto the window, or File > Open Recent. Scopi reads PNG, JPEG, BMP, GIF, WebP, TIFF (8- and 16-bit,
  color, stacks, ImageJ and OME-TIFF), NumPy `.npy` and, with `pydicom`, DICOM.
- **Save**: File > Save As (Ctrl+S). The format follows the extension: `.tif` (the default; an ImageJ-compatible TIFF that keeps the scale, the camera
  settings and the stack), `.png`, `.jpg`, `.bmp`, `.ome.tif` or `.npy`. Use TIFF for anything you want to measure again later.
- **Close**: Ctrl+W. Scopi asks "Save changes to ...?" if the image was changed; Discard throws the changes away.
- **Zoom and pan**: Ctrl+= and Ctrl+- zoom, Ctrl+1 is actual size, Ctrl+0 fits the window; drag with the middle mouse button, hold Space and drag, or pick the Hand
  tool to pan. Pinch works on a touch screen. Even an 18 MP picture pans and zooms smoothly because only the visible part is drawn.
- **Brightness and contrast**: the **Brightness / Contrast** panel has the histogram, Min and Max (or **Auto** / **Reset**), a Log view, the look-up table
  (Grays, Fire, Ice, Spectrum, the primary colors and any Fiji LUTs you installed) and **Invert LUT**. This changes the display only, not the pixels.
  Image > Adjust > Apply Display Range makes it permanent.
- **Undo and redo**: Ctrl+Z and Ctrl+Shift+Z, as many steps as memory allows. The status line names the step.
- **Several images**: Image > Duplicate (Ctrl+Shift+D) copies the current one.

## 6. Calibrate and measure

Until an image has a scale, measurements are in pixels. To calibrate, you need something of known size in the picture, such as a stage micrometer.

1. Draw a **Line** along a known distance (for example 1 mm on the micrometer).
2. **Image > Set Scale** (or Analyze > Set Scale): enter the *Known distance* and the *Unit* (`mm`, `um`, ...). The dialog fills in the pixel length of your line.
   The status line then shows, for example, `Scale: 1 pixel = 0.000599 mm (1669 pixels per mm)`.
3. To reuse the scale for a microscope objective, type a name such as `10x` and click **Save as objective preset**. Presets are used by the camera panel
   (section 8) and by every later snap.
4. **Measure** with the Line tool (length and angle) or an area tool (area, perimeter, mean, minimum, maximum, standard deviation, position) and press
   **Ctrl+M** (or the Measure button). Each press adds a row to the **Results** panel. With no selection, Measure reports the whole picture.
   The `Length (px)` column lets you check the pixel size by hand: `Length (px)` times the pixel size equals `Length`.
5. **Analyze > Show Results** opens the table; **Save CSV...** exports it, **Summary** shows statistics, **Clear** empties it.

Hold **Shift** while drawing a line to snap it to steps of 45 degrees. The ROI Manager (Ctrl+T adds the current selection) keeps several selections,
and Analyze > Measure All ROIs measures them in one go. Image > Show Scale Bar draws a scale bar once the image is calibrated.

## 7. Process and analyse

- **Filters** are in Process > Filters (Gaussian Blur, Median, Mean, Minimum, Maximum, Unsharp Mask, Find Edges, Laplace, Difference of Gaussians, Subtract
  Background). A parameter window opens. Tick **Preview** to see the result on the picture while you move the slider or type a value; on a large picture only a
  part (marked "Preview area") is previewed until you press OK. On a stack, tick **Process entire stack** to change every frame, otherwise only the frame that is
  showing changes; the status line says which.
- **Select an area first** (Rectangle, Oval, ...) to limit a filter to that area.
- **Threshold**: Image > Adjust > Auto Threshold (Otsu, Li, Triangle, Isodata, Yen, Mean), Manual Threshold or Local Threshold turns the picture into black and white.
- **Count objects**: threshold, then **Analyze > Analyze Particles** (minimum and maximum size, circularity, exclude objects on the edges, outlines, a summary
  row). Outlines are drawn on the picture and every particle gets a row in Results; Area Fraction and Count Markers are next to it.
- **Morphology and segmentation**: Process > Morphology (Erode, Dilate, Open, Close, Fill Holes, Skeletonize, Outline, Remove Small Objects, Watershed) and Process >
  Segment (connected components, marker watershed, SLIC, Felzenszwalb, Random Walker).
- **Stacks**: Image > Stacks > Z Project (maximum, minimum, mean, sum, median, standard deviation) and Extended Depth of Field.
- **Macros**: Macro > Start Recording, do some steps, Stop Recording & Show to see them as text; Run Macro replays one and Batch Process runs one over many files.

## 8. Using the camera

### Connect

1. Plug the camera into a USB 3 port and make sure nothing else is using it (the camera can only be opened by one program at a time; close any script such as
   `pi_bringup.py` before starting the application).
2. Open the **Camera** panel: **Camera > Camera Panel** (Ctrl+Shift+C).
3. Click **Refresh**. The list shows every camera found, written as `name [driver]`, for example `U3CMOS18000KPA [touptek]`. A built-in
   `[simulated]` camera is always listed; it makes a moving test picture so you can try everything without hardware.
4. Select the camera and click **Connect**. The panel fills in the camera's resolutions and settings.
5. Choose a **Resolution** (this camera offers 4912 x 3684, 2456 x 1842 and 1228 x 922) and click **Start live view**. The picture appears in a tab called **Live**.

### Live view and snaps are different things

| | Live view | Snap |
|---|---|---|
| What it is | A continuous preview stream at the **resolution you chose** | One still picture at the camera's **full resolution** (4912 x 3684), whatever the live resolution is |
| Speed | 1228 x 922: about 32 frames per second; 2456 x 1842: about 17; 4912 x 3684: about 5 (at a short fixed exposure) | About 2 seconds for a full-size picture |
| Use it for | Focusing, finding the specimen, framing the picture | The picture you keep and measure |
| Result | Nothing is stored | A new image tab named `snap_HHMMSS`, not saved yet |

To take a picture: click **Snap** in the camera panel, or Camera > Snap, or press **Ctrl+Shift+S**. The new tab holds the full-resolution picture with the camera
settings (exposure, gain, bit depth, objective) and the scale of the chosen objective. **Save it with Ctrl+S as a TIFF**, which keeps all of that. The live view
blinks for a moment and carries on by itself, so you can snap again straight away. Focus with the smaller live resolution (it is smoother) and snap at full size.

### Exposure, gain and color

- **Auto exposure** is on when you connect. The camera may then use exposure times up to the **Auto limit** (default 100 ms); a long limit makes the live view
  lag because the frame rate cannot exceed 1000 divided by the exposure in milliseconds. Untick it and type an **Exposure** for a fixed time.
- **Gain** is the slider (100 % is no extra gain). **Auto white balance** (or **Temp** and **Tint**) sets the colors: click it with a white or grey area in view.
- **Bit depth** reads "8-bit only" on this camera; cameras with a deeper mode offer it there.
- The live view shows the frame rate and the exposure it is using.

### Crop, flip and orientation

- **Crop**: drag a rectangle on the live view. The camera then sends only that part (faster and smaller); a second rectangle on the cropped picture narrows it
  further. Click the **Crop** button (it reads "Full frame" or "Crop W x H (click: full frame)") to go back. A snap is always the full frame, even with a crop active.
- **Orientation** has **Flip horizontally** and **Flip vertically**. With neither ticked, the picture matches what you see through the eyepiece when the camera is
  mounted with its USB cable pointing to the rear of the microscope. Tick a box only if your camera is mounted differently. The settings apply to the live view and
  to snaps and are remembered for each camera.

### Calibrate for each objective

Take a snap of the stage micrometer with an objective, draw a line along a known distance, use Image > Set Scale and **Save as objective preset** with a name such
as `10x` (section 6). Repeat for each objective. Then pick the objective in the **Objective** list of the camera panel's **Calibration** group before you snap:
every snap gets that scale, and measuring in it gives real units. Presets are stored in `~/.scopi/presets.json`. Measured with the OMAX camera on a
0.01 mm stage micrometer, known spans of 0.2 to 0.5 mm read within about 0.1 % at 4x, 10x and 40x.

### Time-lapse

In the **Time-lapse** group set the **Interval** (seconds), the number of **Frames** and the **Folder** (default `~/Scopi-timelapse`), then **Start time-lapse**.
Each frame is a full-resolution snap saved there as `timelapse_YYYYMMDD_HHMMSS_NNNN.tif`. The button turns into **Stop time-lapse**; it stops by itself after the
last frame. A frame is skipped if the previous one is still being captured.

### Disconnecting

Click **Disconnect**, or just unplug the camera; Scopi notices and stops the live view. Quit Scopi before running any camera script.

### Other cameras

Scopi has been tested with one camera, the OMAX A35180U3. Other cameras that use the same ToupTek SDK should work too: OMAX and ToupTek models, and any brand whose
own software is ToupView or ToupLite. Scopi reads the resolutions, pixel size, exposure and gain ranges and bit depths from the SDK, and the USB permission rule is the SDK's own. But nothing else has been tried, so check each new camera by:

1. Plug it in, click **Refresh**, **Connect** and **Start live view** as above.
2. **Check the orientation** against the eyepiece. Scopi assumes ToupTek frames arrive upside down, which was measured on one model only. If the live view is wrong,
   tick **Flip horizontally** or **Flip vertically**. If a crop lands in the wrong place, close Scopi and run `.venv/bin/python scripts/pi_bringup.py roitest` with a
   still, textured slide in focus (lens tissue works). The OMAX A35180U3 prints `RESULT: horizontal ['as'] vertical ['MIRRORED']`, which is what Scopi assumes; a
   different result means that model needs a different `delivered_flip` in `src/scopi/camera/touptek.py` (a one-line change that applies to every ToupTek camera).
3. **Calibrate its objectives again under new names.** Objective presets are stored by name and are not tied to a camera, so a `10x` made with one camera gives wrong
   sizes on another (the pixel size differs). Name them per camera, such as `10x-cam2`, and choose the right one in the **Objective** list.
4. Not handled or never tried: monochrome cameras (no special handling), cooling controls on cooled cameras (not offered), bit depths above 8 (the code exists but the
   OMAX has none), models without a hardware crop (not checked), and ToupTek's UVC-class cameras (not supported by this driver; the generic USB (V4L2) fallback might work).

## 9. Where Scopi keeps things

| What | Where |
|---|---|
| Camera SDK | `vendor/toupcam/` inside the project (or `~/.scopi/vendor/toupcam/`, or the folder in `SCOPI_TOUPCAM_DIR`) |
| Objective calibrations | `~/.scopi/presets.json` (change with `SCOPI_PRESETS`) |
| Look-up tables you add | `~/.scopi/luts/` (change with `SCOPI_LUTS`) |
| Plugins | `~/.scopi/plugins/` (files named `*_plg.py`; more folders in `SCOPI_PLUGINS`) |
| Time-lapse pictures | `~/Scopi-timelapse/` unless you chose another folder |
| Window layout, theme, recent files, camera flips | Qt settings of "Scopi" (`~/.config/Scopi/` on Linux) |
| Test images | `~/scopi-test/` (made by `scripts/make_test_images.py`) |

## 10. Helper scripts

All are run from the project folder with the virtual environment's Python (`.venv/bin/python scripts/<name>.py`).

| Script | What it does |
|---|---|
| `pi_bringup.py check` | Confirms the Raspberry Pi stack: Python and glibc versions, that every package imports, and the Qt platform in use |
| `pi_bringup.py render` | Times pan and zoom of an 18 MP picture through the real canvas (needs a display) |
| `pi_bringup.py camera` | Lists USB devices, finds the camera and measures frames per second and USB throughput at each resolution |
| `pi_bringup.py camdiag` | The same with the vendor SDK only, for telling a camera problem from a Scopi problem |
| `pi_bringup.py roitest`, `snaptest` | Check where a hardware crop lands and that snaps in a row all arrive |
| `benchmark.py` | Times the display pipeline and the filters on an 18 MP image |
| `make_test_images.py` | Writes the test images to `~/scopi-test` |
| `install_toupcam.py` | Copies the camera SDK from the vendor's zip or folder into `vendor/toupcam/` (`--udev` also installs the USB rule, `--udev-only` installs just the rule) |
| `install_luts.py` | Installs look-up tables from a Fiji copy |

## 11. Troubleshooting

| Problem | What to do |
|---|---|
| The installer prints nothing or stops at once | You ran it with `sh` or `sudo`. Run `bash packaging/install_pi.sh` as your normal user. |
| "glibc ... is too old" | PySide6 needs glibc 2.39 or newer: use Raspberry Pi OS **Trixie**, not Bookworm. |
| The window does not open and Qt says it cannot load a platform plugin | `opencv-python` (the GUI build) is installed. `pip uninstall opencv-python opencv-contrib-python`, then `pip install opencv-python-headless`. |
| Downloads time out | The link is slow. Run the installer again; pip retries and reuses what it already downloaded. |
| The camera is not in the list | Click **Refresh**. Check it is on a USB 3 port, run `lsusb` (the OMAX camera shows as `0547:3018`), and that the SDK is installed (`vendor/toupcam/`). Re-plug the camera after installing the SDK or the USB rule. |
| "Permission denied" when connecting | The USB rule is missing: `.venv/bin/python scripts/install_toupcam.py --udev-only`, then re-plug the camera. The rule gives access to whoever is logged in at the Pi's own screen and to the group `plugdev`, so over SSH your user must be in that group: check with `groups`, add it with `sudo usermod -aG plugdev $USER` and log in again. |
| The camera is busy or will not start | Another program holds it (a script, a second Scopi). Close it, then Disconnect and Connect again. |
| The live view lags | At 4912 x 3684 the camera itself delivers only about 5 frames per second. Focus with 2456 x 1842 or 1228 x 922 and snap at full size. A long **Auto limit** also lowers the frame rate. |
| The live picture is upside down or mirrored compared with the eyepiece | Use the **Flip horizontally** / **Flip vertically** boxes (section 8). |
| Measurements are in pixels | The image has no scale: use Image > Set Scale, or choose an **Objective** in the camera panel before snapping. |
| Ctrl+Z seems to do nothing | Look at the status line: it says "Undo: <step>" or "Nothing to undo". Undo works on the current image tab. |
| A filter changed only one frame of a stack | "Process entire stack" was not ticked; the status line says "frame N of M only". Undo (Ctrl+Z) and run it again with the box ticked. |
| Messages such as "Trying to disable ... textinput" in the terminal | Harmless Wayland noise; Scopi switches that message off. |

## 12. Keyboard shortcuts

| Keys | Action | Keys | Action |
|---|---|---|---|
| Ctrl+O | Open | Ctrl+M | Measure |
| Ctrl+S | Save As | Ctrl+T | Add selection to the ROI Manager |
| Ctrl+W | Close the image | Ctrl+A / Ctrl+Shift+A | Select all / select none |
| Ctrl+Q | Quit | Ctrl+Shift+D | Duplicate the image |
| Ctrl+Z | Undo | Ctrl+= / Ctrl+- | Zoom in / out |
| Ctrl+Shift+Z | Redo | Ctrl+1 / Ctrl+0 | Actual size / fit to window |
| Ctrl+Shift+C | Show the camera panel | Ctrl+Shift+S | Snap |
| Space + drag | Pan | Shift + drag a line | Snap the line to 45 degree steps |

