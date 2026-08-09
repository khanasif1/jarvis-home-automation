#!/usr/bin/env python3
"""Developer tool: run one conversation turn using push-to-talk (Enter key)
instead of a wake-word engine, to manually exercise the full
capture -> backend -> playback pipeline without a wake-word model.

Usage:
    /opt/home-assistant-pi/venv/bin/python scripts/run_push_to_talk.py

Requires HAP_DEVICE_ID, HAP_DEVICE_TOKEN, and HAP_API_BASE_URL to be set
(e.g. via /etc/home-assistant-pi/config.env or exported in the shell).
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from home_assistant_pi.config import ConfigError, load_config
        from home_assistant_pi.logging_config import configure_logging
        from home_assistant_pi.main import build_application
    except ImportError:
        print(
            "home_assistant_pi is not installed. Activate the venv or run "
            "'pip install .' from pi-client/ first.",
            file=sys.stderr,
        )
        return 1

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    configure_logging(level=config.log_level)
    app = build_application(config)

    print("Press Enter to start a conversation turn (Ctrl+C to exit)...")
    try:
        while True:
            input()
            app.run_conversation_turn()
    except KeyboardInterrupt:
        print("\nExiting.")
        return 0
    finally:
        app.api_client.close()


if __name__ == "__main__":
    raise SystemExit(main())
