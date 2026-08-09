"""Logging setup for the pi-client.

Logs are written to stdout/stderr only. When running under systemd, journald
captures stdout/stderr automatically, so the application never writes or
rotates its own log files (see the disk-space requirements in
``../Prompt/SolutionPrompt.md``). journald's own size limits can be configured
via ``/etc/systemd/journald.conf`` (see ``SystemMaxUse=``), which is
documented in ``pi-client/README.md``.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(level: str = "INFO", *, force: bool = False) -> None:
    """Configure root logging to write formatted records to stdout.

    Args:
        level: A standard logging level name (e.g. ``"INFO"``, ``"DEBUG"``).
        force: Reconfigure even if :func:`configure_logging` already ran in
            this process (mainly useful for tests).
    """
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Remove any pre-existing handlers so repeated calls (e.g. in tests) do
    # not duplicate log output.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT))
    root.addHandler(handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module logger, configuring logging with defaults if needed."""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
