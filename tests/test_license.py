# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""The license is stated the same way everywhere: pyproject.toml, the LICENSE file and the top of every source file."""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOLDER = "Dwight Vieira"


def _sources():
    return sorted(p for folder in ("src", "tests", "scripts", "packaging") for p in (ROOT / folder).rglob("*")
                  if p.is_file() and p.suffix in (".py", ".sh") and "__pycache__" not in p.parts)


def test_every_source_file_carries_the_license_and_copyright_notice():
    expr = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["license"]
    assert expr == "GPL-3.0-or-later"
    missing = []
    for p in _sources():
        head = p.read_text().splitlines()[:4]          # after an optional shebang line
        if f"# SPDX-License-Identifier: {expr}" not in head or not any(l.startswith("# Copyright (C) ") and HOLDER in l for l in head):
            missing.append(str(p.relative_to(ROOT)))
    assert not missing, f"no license notice at the top of: {missing}. Add the two lines used by the other files."
    assert len(_sources()) > 50                          # the search really found the project's files


def test_license_file_is_the_unmodified_gpl_version_3():
    text = (ROOT / "LICENSE").read_text()
    assert text.lstrip().startswith("GNU GENERAL PUBLIC LICENSE\n                       Version 3, 29 June 2007")
    assert "END OF TERMS AND CONDITIONS" in text and len(text) == 35149    # the size of the official text from gnu.org
    assert "license-files" in (ROOT / "pyproject.toml").read_text()
