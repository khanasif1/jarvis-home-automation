#!/usr/bin/env python3
"""List available microphone (input) and speaker (output) audio devices.

Useful for choosing values for HAP_INPUT_DEVICE / HAP_OUTPUT_DEVICE in
config.env. Requires the pi-client package (and its 'sounddevice'
dependency + PortAudio) to be installed.
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from home_assistant_pi.audio.capture import list_input_devices
        from home_assistant_pi.audio.playback import list_output_devices
    except ImportError:
        print(
            "home_assistant_pi is not installed. Activate the venv or run "
            "'pip install .' from pi-client/ first.",
            file=sys.stderr,
        )
        return 1

    print("Input devices (microphones):")
    try:
        inputs = list_input_devices()
        if not inputs:
            print("  (none found)")
        for device in inputs:
            print(
                f"  [{device.index}] {device.name} "
                f"(channels={device.max_input_channels}, "
                f"default_rate={device.default_sample_rate})"
            )
    except Exception as exc:
        print(f"  Failed to list input devices: {exc}")

    print("\nOutput devices (speakers):")
    try:
        outputs = list_output_devices()
        if not outputs:
            print("  (none found)")
        for device in outputs:
            print(
                f"  [{device.index}] {device.name} "
                f"(channels={device.max_output_channels}, "
                f"default_rate={device.default_sample_rate})"
            )
    except Exception as exc:
        print(f"  Failed to list output devices: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
