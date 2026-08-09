#!/usr/bin/env python3
"""Standalone helper: play a notification sound to test the speaker.

Run this from the installed virtual environment, e.g.:

    /opt/home-assistant-pi/venv/bin/python scripts/test_speaker.py

This is a thin wrapper around ``home_assistant_pi.cli.cmd_test_speaker``;
the same behavior is available via the installed console script as
``home-assistant-pi test-speaker``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Optional WAV file to play instead of the bundled activation sound",
    )
    args = parser.parse_args()

    try:
        from home_assistant_pi.cli import cmd_test_speaker
    except ImportError:
        print(
            "home_assistant_pi is not installed. Activate the venv or run "
            "'pip install .' from pi-client/ first.",
            file=sys.stderr,
        )
        return 1

    try:
        print(cmd_test_speaker(input_path=args.input))
        return 0
    except Exception as exc:
        print(f"Speaker test failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
