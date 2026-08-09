"""Shared pytest fixtures for the pi-client test suite.

Tests never touch real audio hardware or the network: fixtures here
provide fake ``sounddevice``-like modules and other test doubles so the
full suite runs quickly and deterministically in CI.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS_DIR = REPO_ROOT / ".test-artifacts" / "pi-client" / "tests"


@pytest.fixture()
def artifacts_dir() -> Path:
    """A per-test-session scratch directory under the repo's .test-artifacts/."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    return ARTIFACTS_DIR


class FakeStream:
    """A fake PortAudio Raw{Input,Output}Stream supporting the subset of
    the API used by home_assistant_pi.audio.capture/playback."""

    def __init__(self, chunks=None, on_write=None, **kwargs):
        self.kwargs = kwargs
        self._chunks = list(chunks) if chunks is not None else []
        self._on_write = on_write
        self.written = bytearray()
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    def read(self, frames):
        if self._chunks:
            chunk = self._chunks.pop(0)
        else:
            chunk = b"\x00\x00" * frames
        return chunk, False

    def write(self, data):
        self.written.extend(data)
        if self._on_write is not None:
            self._on_write(data)


def make_fake_sounddevice(
    input_chunks=None,
    input_devices=None,
    output_devices=None,
    raise_on_import=None,
):
    """Build a minimal fake module mimicking the parts of ``sounddevice``
    that home_assistant_pi.audio uses."""
    if raise_on_import is not None:
        raise raise_on_import

    module = types.ModuleType("sounddevice")

    def query_devices():
        devices = []
        for d in input_devices or []:
            devices.append(d)
        for d in output_devices or []:
            devices.append(d)
        return devices

    module.query_devices = query_devices
    module.RawInputStream = lambda **kwargs: FakeStream(chunks=input_chunks, **kwargs)
    module.RawOutputStream = lambda **kwargs: FakeStream(**kwargs)
    return module


@pytest.fixture()
def fake_sounddevice_factory():
    return make_fake_sounddevice
