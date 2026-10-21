#!/usr/bin/env bash
# Install ImagePi on Raspberry Pi OS Trixie (Python 3.13, aarch64).
#   ./packaging/install_pi.sh [path/to/toupcamsdk.zip]
# Safe to re-run. Needs sudo only for apt and the udev rule.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SDK_ZIP="${1:-}"
VENV="${IMAGEPI_VENV:-$ROOT/.venv}"

if [ "$(uname -m)" != "aarch64" ]; then
  echo "warning: expected aarch64, found $(uname -m)" >&2
fi
glibc="$(ldd --version | head -1 | grep -oE '[0-9]+\.[0-9]+$')"
if [ "$(printf '%s\n2.39\n' "$glibc" | sort -V | head -1)" != "2.39" ]; then
  echo "glibc $glibc is too old: PySide6 aarch64 wheels need >= 2.39 (use Raspberry Pi OS Trixie)." >&2
  exit 1
fi

sudo apt-get update
# Qt platform plugin runtime libraries (Wayland + xcb fallback) and USB tools
sudo apt-get install -y python3 python3-venv python3-dev usbutils \
  libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 libfontconfig1 \
  libwayland-client0 libwayland-cursor0 libwayland-egl1 libxcb-cursor0 libxcb-icccm4 \
  libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-xinerama0 libxcb-shape0

python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -e "$ROOT"

if [ -n "$SDK_ZIP" ]; then
  "$VENV/bin/python" "$ROOT/scripts/install_toupcam.py" "$SDK_ZIP" --udev
else
  sudo install -m 644 "$ROOT/packaging/99-toupcam.rules" /etc/udev/rules.d/99-toupcam.rules
  sudo udevadm control --reload-rules && sudo udevadm trigger
  echo "udev rule installed. Run scripts/install_toupcam.py <toupcamsdk zip> to enable the OMAX camera."
fi

# optional: Fiji LUTs if the reference checkout is present
if [ -d "$ROOT/fiji_original/fiji/luts" ]; then
  "$VENV/bin/python" "$ROOT/scripts/install_luts.py" "$ROOT/fiji_original/fiji/luts"
fi

# desktop launcher
mkdir -p "$HOME/.local/share/applications"
sed "s|^Exec=.*|Exec=$VENV/bin/imagepi %F|" "$ROOT/packaging/imagepi.desktop" > "$HOME/.local/share/applications/imagepi.desktop"
echo "Installed. Launch with: $VENV/bin/imagepi   (or from the application menu)"
