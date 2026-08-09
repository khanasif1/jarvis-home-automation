"""home_assistant_pi: Raspberry Pi voice-assistant client.

This package contains only the code required to run the voice-assistant
client on a Raspberry Pi. It has no runtime dependency on the azure-backend
or infra components of the wider repository, and is designed to be built,
tested, packaged, and installed completely independently of them.
"""

from __future__ import annotations

from .version import __version__, get_version

__all__ = ["__version__", "get_version"]
