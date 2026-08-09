"""Tests for home_assistant_pi.audio.capture."""

from __future__ import annotations

import struct

import pytest

import home_assistant_pi.audio.capture as capture_mod
from home_assistant_pi.audio.capture import (
    AudioCapture,
    AudioDeviceError,
    list_input_devices,
)
from home_assistant_pi.audio.vad import VoiceActivityDetector


def _loud_chunk(n_samples=320, amplitude=20000):
    return b"".join(struct.pack("<h", amplitude) for _ in range(n_samples))


def _silent_chunk(n_samples=320):
    return b"\x00\x00" * n_samples


def test_list_input_devices(monkeypatch, fake_sounddevice_factory):
    fake_sd = fake_sounddevice_factory(
        input_devices=[
            {"name": "USB Mic", "max_input_channels": 1, "default_samplerate": 16000.0},
            {"name": "HDMI Out", "max_input_channels": 0, "default_samplerate": 48000.0},
        ]
    )
    monkeypatch.setattr(capture_mod, "_sd", lambda: fake_sd)
    devices = list_input_devices()
    assert len(devices) == 1
    assert devices[0].name == "USB Mic"
    assert devices[0].index == 0


def test_list_input_devices_raises_when_sounddevice_missing(monkeypatch):
    def raise_error():
        raise AudioDeviceError("no portaudio")

    monkeypatch.setattr(capture_mod, "_sd", raise_error)
    with pytest.raises(AudioDeviceError):
        list_input_devices()


def test_record_utterance_stops_after_vad_silence(monkeypatch, fake_sounddevice_factory):
    chunks = [_loud_chunk(), _silent_chunk(), _silent_chunk(), _silent_chunk()]
    fake_sd = fake_sounddevice_factory(input_chunks=list(chunks))
    monkeypatch.setattr(capture_mod, "_sd", lambda: fake_sd)

    capture = AudioCapture(sample_rate=16000, chunk_frames=320)
    vad = VoiceActivityDetector(threshold=300.0, silence_chunks_to_stop=3)
    audio = capture.record_utterance(vad=vad, max_seconds=10.0)

    # 1 loud chunk + 3 silent chunks were consumed before VAD signalled stop.
    assert len(audio.frames) == len(b"".join(chunks))
    assert audio.sample_rate == 16000
    assert audio.channels == 1
    assert audio.sample_width == 2


def test_record_utterance_respects_max_seconds(monkeypatch, fake_sounddevice_factory):
    # Never-ending silence: the VAD alone would never stop, so max_seconds
    # must bound the recording.
    fake_sd = fake_sounddevice_factory(input_chunks=[])
    monkeypatch.setattr(capture_mod, "_sd", lambda: fake_sd)

    capture = AudioCapture(sample_rate=16000, chunk_frames=1600)
    vad = VoiceActivityDetector()
    audio = capture.record_utterance(vad=vad, max_seconds=0.2)

    expected_chunks = max(1, int((0.2 * 16000) / 1600))
    assert len(audio.frames) == expected_chunks * 1600 * 2


def test_record_utterance_wraps_stream_errors(monkeypatch, fake_sounddevice_factory):
    class BrokenStream:
        def __enter__(self):
            raise RuntimeError("device busy")

        def __exit__(self, *args):
            return False

    fake_sd = fake_sounddevice_factory()
    fake_sd.RawInputStream = lambda **kwargs: BrokenStream()
    monkeypatch.setattr(capture_mod, "_sd", lambda: fake_sd)

    capture = AudioCapture()
    with pytest.raises(AudioDeviceError, match="Microphone capture failed"):
        capture.record_utterance(max_seconds=1.0)


def test_on_chunk_callback_invoked(monkeypatch, fake_sounddevice_factory):
    chunks = [_loud_chunk(), _silent_chunk(), _silent_chunk()]
    fake_sd = fake_sounddevice_factory(input_chunks=list(chunks))
    monkeypatch.setattr(capture_mod, "_sd", lambda: fake_sd)

    seen = []
    capture = AudioCapture(chunk_frames=320)
    vad = VoiceActivityDetector(silence_chunks_to_stop=2)
    capture.record_utterance(vad=vad, on_chunk=seen.append)
    assert len(seen) == 3
