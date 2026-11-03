## What and why

<!-- What does this change, and what problem does it solve? Link the issue (Fixes #123). -->

## How I checked it

<!-- Say what you ran and on what. CI cannot test a camera, a Raspberry Pi display or touch input. -->

- [ ] `QT_QPA_PLATFORM=offscreen python -m pytest -q` passes
- [ ] `ruff check .` passes
- [ ] Tried by hand on: <!-- e.g. Raspberry Pi 5 + OMAX A35180U3, macOS, simulated camera, or "not needed: no visible change" -->

## Checklist

- [ ] There is a test for the change (a bug fix has one that fails without the fix)
- [ ] New `.py` and `.sh` files start with the two license lines (see CONTRIBUTING.md)
- [ ] `usage.md`, the readme or `documents/` are updated if anything a person can see has changed
- [ ] A new dependency is listed in `pyproject.toml`, `requirements.txt` and the readme table
- [ ] No camera SDK files, personal data or code from a project with an incompatible license (ImagePy, the ToupTek SDK) is included
