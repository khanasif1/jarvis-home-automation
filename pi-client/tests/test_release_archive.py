from __future__ import annotations

import subprocess
import sys
import tarfile
from pathlib import Path


def test_release_archive_assigns_portable_permissions(tmp_path: Path):
    source_dir = tmp_path / "staging"
    source_dir.mkdir()
    for name in ("install.sh", "update.sh", "uninstall.sh", "VERSION"):
        (source_dir / name).write_text(name, encoding="utf-8")
    archive_path = tmp_path / "bundle.tar.gz"
    helper = Path(__file__).parents[1] / "packaging" / "create-release-archive.py"

    subprocess.run(
        [sys.executable, str(helper), str(source_dir), str(archive_path)],
        check=True,
    )

    with tarfile.open(archive_path, "r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}

    assert set(members) == {"install.sh", "update.sh", "uninstall.sh", "VERSION"}
    assert members["install.sh"].mode == 0o755
    assert members["update.sh"].mode == 0o755
    assert members["uninstall.sh"].mode == 0o755
    assert members["VERSION"].mode == 0o644
    assert all(member.uid == 0 and member.gid == 0 for member in members.values())
