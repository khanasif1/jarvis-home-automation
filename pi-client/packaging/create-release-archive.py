#!/usr/bin/env python3
"""Create a portable Pi release archive with deterministic file permissions."""

from __future__ import annotations

import argparse
import tarfile
from pathlib import Path

_EXECUTABLE_FILES = {"install.sh", "update.sh", "uninstall.sh"}


def create_archive(source_dir: Path, output_file: Path) -> None:
    source_dir = source_dir.resolve()
    output_file = output_file.resolve()
    members = sorted(source_dir.iterdir(), key=lambda path: path.name)
    unsupported = [path.name for path in members if not path.is_file()]
    if unsupported:
        raise ValueError(
            "release staging may contain only regular files: "
            + ", ".join(unsupported)
        )

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output_file, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for source in members:
            info = archive.gettarinfo(str(source), arcname=source.name)
            info.mode = 0o755 if source.name in _EXECUTABLE_FILES else 0o644
            info.uid = 0
            info.gid = 0
            info.uname = "root"
            info.gname = "root"
            with source.open("rb") as content:
                archive.addfile(info, content)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output_file", type=Path)
    args = parser.parse_args()
    create_archive(args.source_dir, args.output_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
