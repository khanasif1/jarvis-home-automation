"""Tests for home_assistant_pi.audio.playback."""

from __future__ import annotations

import pytest

import home_assistant_pi.audio.playback as playback_mod
from home_assistant_pi.audio.playback import (
    AudioDeviceError,
    AudioPlayback,
    list_output_devices,
)
from home_assistant_pi.audio.wav import WavAudio


def test_list_output_devices(monkeypatch, fake_sounddevice_factory):
    fake_sd = fake_sounddevice_factory(
        output_devices=[
            {"name": "3.5mm Jack", "max_output_channels": 2, "default_samplerate": 44100.0},
            {"name": "Mic In", "max_output_channels": 0, "default_samplerate": 16000.0},
        ]
    )
    monkeypatch.setattr(playback_mod, "_sd", lambda: fake_sd)
    devices = list_output_devices()
    assert len(devices) == 1
    assert devices[0].name == "3.5mm Jack"


def test_play_writes_frames_to_stream(monkeypatch, fake_sounddevice_factory):
    written = {}

    class RecordingStream:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def write(self, data):
            written["data"] = data

    fake_sd = fake_sounddevice_factory()
    fake_sd.RawOutputStream = lambda **kwargs: RecordingStream(**kwargs)
    monkeypatch.setattr(playback_mod, "_sd", lambda: fake_sd)

    audio = WavAudio(frames=b"\x01\x02" * 100, sample_rate=16000)
    AudioPlayback().play(audio)
    assert written["data"] == audio.frames


def test_play_wraps_stream_errors(monkeypatch, fake_sounddevice_factory):
    fake_sd = fake_sounddevice_factory()

    def broken(**kwargs):
        raise RuntimeError("no such device")

    fake_sd.RawOutputStream = broken
    monkeypatch.setattr(playback_mod, "_sd", lambda: fake_sd)

    with pytest.raises(AudioDeviceError, match="Speaker playback failed"):
        AudioPlayback().play(WavAudio(frames=b"\x00\x00", sample_rate=16000))


def test_missing_sounddevice_raises_audio_device_error(monkeypatch):
    def raise_error():
        raise AudioDeviceError("sounddevice missing")

    monkeypatch.setattr(playback_mod, "_sd", raise_error)
    with pytest.raises(AudioDeviceError):
        AudioPlayback().play(WavAudio(frames=b"\x00\x00", sample_rate=16000))
