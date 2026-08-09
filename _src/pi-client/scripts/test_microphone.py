#!/usr/bin/env python3
"""Standalone helper: record a short clip and report microphone status.

Run this from the installed virtual environment, e.g.:

    /opt/home-assistant-pi/venv/bin/python scripts/test_microphone.py --seconds 3

This is a thin wrapper around ``home_assistant_pi.cli.cmd_test_microphone``;
the same behavior is available via the installed console script as
``home-assistant-pi test-microphone``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seconds", type=float, default=3.0, help="Recording duration in seconds"
    )
    parser.add_argument(
        "--output", type=Path, default=None, help="Optional path to save the WAV clip"
    )
    args = parser.parse_args()

    try:
        from home_assistant_pi.cli import cmd_test_microphone
    except ImportError:
        print(
            "home_assistant_pi is not installed. Activate the venv or run "
            "'pip install .' from pi-client/ first.",
            file=sys.stderr,
        )
        return 1

    try:
        print(cmd_test_microphone(seconds=args.seconds, output_path=args.output))
        return 0
    except Exception as exc:
        print(f"Microphone test failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
