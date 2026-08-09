"""Allow running the package as ``python -m home_assistant_pi``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
