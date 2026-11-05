# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dwight Vieira
"""The project metadata agrees with itself (one version, one license, the Python versions CI really runs) and the files GitHub looks for are there."""

import re
import tomllib
from pathlib import Path

import scopi

ROOT = Path(__file__).resolve().parents[1]
PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
CITATION = (ROOT / "CITATION.cff").read_text()
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def _cff(key: str) -> str:
    return re.search(rf'^{key}:\s*"?([^"\n]+?)"?\s*$', CITATION, re.M).group(1)


def test_version_is_the_same_in_pyproject_package_and_citation_file():
    assert PROJECT["version"] == scopi.__version__ == _cff("version")


def test_license_and_repository_are_the_same_in_pyproject_and_citation_file():
    assert PROJECT["license"] == _cff("license")
    assert PROJECT["urls"]["Source"] == _cff("repository-code")
    assert all(url.startswith(PROJECT["urls"]["Source"]) for url in PROJECT["urls"].values())


def test_no_license_classifier_next_to_the_spdx_expression():
    assert not [c for c in PROJECT["classifiers"] if c.startswith("License ::")], "PEP 639: the `license` expression replaces the classifier, and setuptools rejects both"


def test_python_classifiers_are_the_versions_ci_runs():
    declared = {m.group(1) for c in PROJECT["classifiers"] if (m := re.fullmatch(r"Programming Language :: Python :: (3\.\d+)", c))}
    ci = set(re.findall(r'"(3\.\d+)"', re.search(r"^\s*python: \[(.*)\]", (ROOT / ".github" / "workflows" / "ci.yml").read_text(), re.M).group(1)))
    assert declared == ci, "claim only the Python versions the CI matrix tests, and test every one claimed"
    assert min(ci, key=lambda v: int(v.split(".")[1])) == PROJECT["requires-python"].removeprefix(">="), "requires-python is the oldest version in the matrix"


def test_community_files_exist():
    for name in ("CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md", "CITATION.cff", ".editorconfig", ".gitattributes", ".github/CODEOWNERS", ".github/dependabot.yml",
                 ".github/PULL_REQUEST_TEMPLATE.md", ".github/ISSUE_TEMPLATE/config.yml", ".github/ISSUE_TEMPLATE/bug_report.yml",
                 ".github/ISSUE_TEMPLATE/feature_request.yml"):
        assert (ROOT / name).is_file(), name


def test_workflows_pin_actions_to_commit_hashes_and_start_with_read_only_rights():
    assert WORKFLOWS, "no workflow found"
    for wf in WORKFLOWS:
        text = wf.read_text()
        unpinned = [m for m in re.findall(r"^\s*-?\s*uses:\s*(\S+)", text, re.M) if not re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", m) and not m.startswith("./")]
        assert not unpinned, f"{wf.name}: pin these to a full commit hash (with the tag in a comment): {unpinned}"
        assert re.search(r"^permissions:\s*\n\s+contents: read\s*$", text, re.M), f"{wf.name}: start with `permissions: contents: read` and widen per job only where needed"
        assert "pull_request_target" not in text, f"{wf.name}: pull_request_target runs fork code with secrets"
