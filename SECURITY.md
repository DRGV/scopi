# Security policy

## Supported versions

Scopi is pre-1.0. Security fixes go into the `main` branch and into the latest release, if there is one. Older versions are not patched: update instead.

## Reporting a vulnerability

**Please do not open a public issue or pull request for a security problem.**

Report it privately through GitHub: open the repository's **Security** tab, choose **Report a vulnerability**, or go straight to
<https://github.com/DRGV/scopi/security/advisories/new>. Please include:

- what the problem is and what an attacker gains from it;
- the Scopi version (Help > About Scopi, or `python -c "import scopi; print(scopi.__version__)"`) or commit (`git rev-parse HEAD`), the operating system and the Python version;
- the smallest set of steps that shows it, and a sample file if a file triggers it (do not send anything that contains personal data).

If you cannot use GitHub's form, email [ithinkscience@proton.me](mailto:ithinkscience@proton.me) with the same details.

Scopi is maintained by one person in spare time. You can expect an answer within **7 days** and, for a confirmed problem, a fix or a plan within **90 days**. The fix is
released together with a published advisory that credits you, unless you prefer to stay anonymous. Please keep the details private until then.

## What is in scope

Scopi is a desktop application that runs with your own permissions, opens files you choose and, optionally, talks to a USB camera. It contains no networking code and listens on
no port. These parts are worth a report:

- **Opening a crafted file** that makes Scopi run code, read or write files it should not, or corrupt memory because of how Scopi uses a decoder. (`.npy` files are read with
  pickle support off.) A file that merely crashes Scopi, or makes it slow or hungry for memory, is a bug: report it as a normal issue.
- **Macros.** A macro is text naming registered operations with JSON parameters, and playing one involves no `eval` or `exec`. A way to make a macro run arbitrary Python is in scope.
- **The installers**: `packaging/install_pi.sh` and `scripts/install_toupcam.py` (which unpacks the camera SDK archive and uses `sudo` to install the USB rule).
  The archive reader refuses entries that would land outside its destination folder.
- **The USB permission rule** `packaging/70-toupcam.rules`: it must give access to the logged-in user and the `plugdev` group only (mode 0660), never to everyone.
- **The project's own supply chain**: the GitHub workflows and the packaging metadata.

## What is out of scope

- **Plugins are code.** A file named `*_plg.py` in `~/.scopi/plugins`, in a folder listed in `SCOPI_PLUGINS`, or a package that registers a `scopi.plugins` entry point is
  ordinary Python that runs with your permissions when Scopi starts. The same goes for the camera SDK folder (`vendor/toupcam`, `~/.scopi/vendor/toupcam`,
  `SCOPI_TOUPCAM_DIR`), whose library Scopi loads. Anyone who can put files there or set those variables can already run code as you, so that is not a vulnerability in Scopi.
  Install plugins only from people you trust.
- **The ToupTek camera SDK** is proprietary software that is not part of this repository. Report problems in it to ToupTek or OMAX.
- **Vulnerabilities in a dependency** (Pillow, tifffile, OpenCV, NumPy, Qt and the others listed in the [readme](readme.md#open-source-software-scopi-uses)). Report them to
  that project. If Scopi is affected because of the way it uses the library, tell us as well.
- Attacks that need physical access to the computer, or an account that is already compromised.

## Keeping a Scopi installation safe

- Scopi's dependencies are installed from PyPI at their newest versions and are not pinned, so `pip install --upgrade -r requirements.txt` in the virtual environment picks up
  their security fixes. Keep the operating system updated as well (`sudo apt update && sudo apt full-upgrade` on Raspberry Pi OS).
- Download the camera SDK only from the vendor ([OMAX](https://omaxmicroscope.com/pages/software-download) or ToupTek).
- Run Scopi and the installers as your normal user; the installer asks for `sudo` itself, only for the system libraries and the USB rule.
- Treat image files and macros from strangers like any other downloaded file.
