# Contributing to Scopi

Thank you for wanting to help. Scopi is a small project maintained by one person, so the most useful contributions are the ones that are easy to review: a bug report
with exact steps, a focused fix with a test, a clearer sentence in the documentation.

Be kind and constructive in issues and pull requests. Assume good faith; critique the code, not the person.

## Ways to contribute

- **Report a bug or ask for a feature** with the [issue forms](https://github.com/DRGV/scopi/issues/new/choose). Look through the existing issues first, and read the
  [troubleshooting section](usage.md#11-troubleshooting) of the usage guide.
- **Report a security problem privately**, never as a public issue: see [SECURITY.md](SECURITY.md).
- **Test on hardware.** The only camera Scopi has been tried with is the OMAX A35180U3, and the only computers are a Raspberry Pi 5 and a Mac
  (see [Where it has been tested](readme.md#where-it-has-been-tested)). A report that something works, or does not, on another camera, Linux computer, or
  Windows is valuable.
- **Send a change.** For anything bigger than a small fix, open an issue first so we can agree on the approach before you spend time on it.

## Set up

You need Python 3.12 or newer (3.13 is the version that has been used the most). The camera SDK is **not** needed: the tests use a fake one and the simulated camera.

```
git clone https://github.com/DRGV/scopi && cd scopi
python3.13 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

On a Raspberry Pi 5, `bash packaging/install_pi.sh` does this and the system libraries too (see [usage.md](usage.md)). Install `opencv-python-headless`, never
`opencv-python`: the second bundles a Qt that stops PySide6 from loading.

## Check your change

```
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q     # about 15 seconds; the Qt tests run without a window, and nothing needs a camera
.venv/bin/ruff check .                                      # rules are in pyproject.toml: unused names, syntax errors, likely bugs
```

[CI](.github/workflows/ci.yml) runs the same two commands on Ubuntu 24.04 and macOS with Python 3.12 and 3.13, builds the package and checks it. It also runs every Monday,
because Scopi's dependencies are not pinned and a new release of one of them can break it. A pull request needs a green CI run.

CI cannot test a camera, a Raspberry Pi display or a touch screen. If your change affects them, say in the pull request what you tried it on. The step-by-step plans in
[documents/pi-test-plans](documents/pi-test-plans) show how it was done so far.

## Where things go

[documents/README.md](documents/README.md#extending-scopi) explains the layout and how to add an operation, a plugin, a file format or a camera. In short: `core/`, `io/`,
`ops/` and `camera/` know nothing about Qt widgets and are tested without a display; `ui/` builds the windows on top of them. [implementation.md](documents/implementation.md)
has the architecture and the reasons for the choices.

## What a good change looks like

- **A test goes with it.** A bug fix starts with a test that fails without the fix. Tests of dialogs go through the real entry point (`OpRunner.run`), not by calling a
  callback directly. Fakes of the camera SDK should be as strict as the real wrapper.
- **Every `.py` and `.sh` file in `src/`, `tests/`, `scripts/` and `packaging/` starts with the notice** (after a shebang, if there is one):
  ```
  # SPDX-License-Identifier: GPL-3.0-or-later
  # Copyright (C) 2026 Your Name
  ```
  In a file you write, the copyright line carries **your** name (the form is `Copyright (C) <year> <name>`); the existing files keep the maintainer's. If you change an existing
  file substantially, add your own line below the one that is there. `tests/test_license.py` fails when a file has no SPDX line or no well-formed copyright line.
- **Match the surrounding style.** Lines may be long (160 characters); ruff looks for mistakes, it does not reformat. Keep comments for the *why*.
- **Keep the documentation true.** If behaviour a person can see changes, update [usage.md](usage.md), the [readme](readme.md) or [documents/](documents/README.md) in the same
  pull request.
- **A new dependency has to earn its place.** It must be available for Linux (including aarch64 for the Pi) and macOS and be license-compatible with the GPL.
  Add it to `pyproject.toml`, to `requirements.txt` and to the table in the readme.
- **No personal data in the repository.** Test images must not contain patient or student information.

## Licensing of your contribution

Scopi is licensed under the **GNU General Public License, version 3 or (at your option) any later version** (`GPL-3.0-or-later`, see [LICENSE](LICENSE)). There is no contributor
license agreement: by sending a pull request you confirm that you wrote the change, or have the right to submit it, and that it may be distributed under that license.

Do not copy code from projects whose licenses are not compatible with it, and do not commit anything from the camera maker:

- the **ToupTek camera SDK** is proprietary; `vendor/` is git-ignored for that reason and must stay out of the repository;
- **ImagePy** has a license that cannot be combined with the GPL, and Scopi re-implements ImageJ and Fiji ideas from their published documentation without using their code.

Code from permissive (BSD, MIT, Apache-2.0) or LGPL projects can be used. Say where it came from in the pull request.

## Commits and pull requests

- Work on a branch from `main` and keep each pull request about one thing.
- Start the commit message with a short summary in the imperative ("Fix crop rectangle on a flipped live view"); use the body to explain *why*.
- Fill in the [pull request template](.github/PULL_REQUEST_TEMPLATE.md). Say what you ran and on what.
- Expect review comments; the maintainer answers when time allows.
