"""Version consistency tests."""

from __future__ import annotations

import re
from pathlib import Path

from home_assistant_pi.version import __version__, get_version

PYPROJECT_PATH = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_get_version_matches_dunder_version():
    assert get_version() == __version__


def test_version_is_semver_like():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def test_version_matches_pyproject():
    # Avoid a hard dependency on tomllib (3.11+ only) / tomli (3.9-3.10)
    # for a single-value check; a targeted regex is sufficient here and
    # keeps the dev dependency list minimal.
    text = PYPROJECT_PATH.read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert match is not None, "Could not find version= in pyproject.toml"
    assert match.group(1) == __version__
