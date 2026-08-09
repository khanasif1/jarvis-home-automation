from __future__ import annotations

import re
from pathlib import Path

ROOT_README = Path(__file__).resolve().parents[3] / "README.md"


def test_root_readme_has_exactly_four_lifecycle_sections():
    content = ROOT_README.read_text(encoding="utf-8")
    headings = re.findall(r"^## .+$", content, flags=re.MULTILINE)

    assert headings == [
        "## 1. Install application on Pi",
        "## 2. Uninstall application on Pi",
        "## 3. Install backend in Azure",
        "## 4. Uninstall backend from Azure",
    ]


def test_root_readme_uses_idempotent_lifecycle_commands():
    content = ROOT_README.read_text(encoding="utf-8")

    assert "sudo ./install.sh --version 1.0.1" in content
    assert "sudo ./uninstall.sh --purge" in content
    assert "backend_lifecycle.py install" in content
    assert "backend_lifecycle.py uninstall" in content
    assert content.count("azd auth login") == 4
    assert "--yes" in content
