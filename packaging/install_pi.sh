#!/usr/bin/env bash
# Install ImagePi on Raspberry Pi OS Trixie (Python 3.13, aarch64).
#   bash packaging/install_pi.sh [path/to/toupcamsdk.zip-or-folder]
# Run it as your normal user, not with sudo: it calls sudo itself for apt and the udev rule, and the
# venv and launcher must belong to you. Safe to re-run. Use `bash -x packaging/install_pi.sh` to trace it.
if [ -z "${BASH_VERSION:-}" ]; then
  echo "Run this with bash, not sh:  bash $0 [toupcamsdk.zip]" >&2
  exit 1
fi
set -euo pipefail

say() { printf '\n==> %s\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Do not run this with sudo: the venv and launcher would end up owned by root." >&2
  echo "Run it as your normal user:  bash $0 $*" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SDK_SRC="${1:-}"
VENV="${IMAGEPI_VENV:-$ROOT/.venv}"
PY="$VENV/bin/python"
PIP_OPTS=(--retries 10 --timeout 60)   # the big PySide6/OpenCV wheels can stall on a slow link

say "Checking the platform"
arch="$(uname -m)"
[ "$arch" = "aarch64" ] || echo "warning: expected aarch64, found $arch" >&2
# getconf, not `ldd --version | head`: under pipefail the SIGPIPE from head can end the script silently
glibc="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}' || true)"
if [ -z "$glibc" ]; then
  echo "warning: could not read the glibc version" >&2
elif ! printf '%s\n%s\n' 2.39 "$glibc" | sort -V -C; then
  echo "glibc $glibc is too old: PySide6 aarch64 wheels need >= 2.39 (use Raspberry Pi OS Trixie)." >&2
  exit 1
fi
echo "$arch, glibc ${glibc:-unknown}"

say "Installing system libraries (sudo)"
# Qt platform plugin runtime libraries (Wayland + xcb fallback) and USB tools
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-dev usbutils \
  libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 libfontconfig1 \
  libwayland-client0 libwayland-cursor0 libwayland-egl1 libxcb-cursor0 libxcb-icccm4 \
  libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xinerama0 libxcb-shape0

say "Creating the virtual environment in $VENV"
python3 -m venv "$VENV"
"$PY" -m pip install "${PIP_OPTS[@]}" --upgrade pip

# opencv-python (the GUI build) bundles its own Qt 5 and points QT_QPA_PLATFORM_PLUGIN_PATH at it, which stops
# PySide6 from loading its platform plugin. ImagePi needs opencv-python-headless; both provide `cv2`, so remove
# every OpenCV variant first and let the install below bring back the headless one.
for gui_cv in opencv-python opencv-contrib-python; do
  if "$PY" -m pip show "$gui_cv" >/dev/null 2>&1; then
    say "Removing $gui_cv (its bundled Qt conflicts with PySide6)"
    "$PY" -m pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless
    break
  fi
done

say "Installing ImagePi and its Python dependencies (large downloads; can take several minutes)"
"$PY" -m pip install "${PIP_OPTS[@]}" -e "$ROOT[test]"

if [ -n "$SDK_SRC" ]; then
  say "Installing the ToupTek camera SDK and udev rule (sudo)"
  "$PY" "$ROOT/scripts/install_toupcam.py" "$SDK_SRC" --udev
else
  say "Installing the udev rule (sudo)"
  sudo install -m 644 "$ROOT/packaging/99-toupcam.rules" /etc/udev/rules.d/99-toupcam.rules
  sudo udevadm control --reload-rules && sudo udevadm trigger
  if [ -f "$ROOT/vendor/toupcam/python/toupcam.py" ]; then
    echo "SDK already present in vendor/toupcam."
  else
    echo "Run scripts/install_toupcam.py <toupcamsdk zip or folder> to enable the OMAX camera."
  fi
fi

# optional: Fiji LUTs if the reference checkout is present
if [ -d "$ROOT/references/fiji/luts" ]; then
  say "Installing Fiji LUTs"
  "$PY" "$ROOT/scripts/install_luts.py" "$ROOT/references/fiji/luts"
fi

say "Installing the desktop launcher"
mkdir -p "$HOME/.local/share/applications"
sed "s|^Exec=.*|Exec=$VENV/bin/imagepi %F|" "$ROOT/packaging/imagepi.desktop" > "$HOME/.local/share/applications/imagepi.desktop"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$HOME/.local/share/applications" || true
fi

say "Done"
echo "Launch with: $VENV/bin/imagepi   (or from the application menu)"
echo "Verify with: $PY $ROOT/scripts/pi_bringup.py check"
