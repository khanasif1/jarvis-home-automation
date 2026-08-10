# Raspberry Pi client

The client is deliberately limited to work that must happen near the user:

1. Capture 16 kHz mono PCM in 80 ms frames while one openWakeWord TFLite model
   listens for “hey jarvis.”
2. Play the activation cue, switch to 20 ms frames, and apply WebRTC VAD.
3. Upload chunks immediately to `POST /api/voice/stream`.
4. Stop after 1.2 seconds of trailing silence, 3 seconds without command
   speech, or the 30-second hard limit.
5. Play returned 24 kHz PCM chunks immediately, then wait 750 ms before
   re-enabling wake detection.

The flow is half-duplex: wake-word inference is disabled during upload,
response generation, and playback. No audio file or application data is stored.

## Configuration

`/etc/home-assistant-pi/config.env` is created by the release installer:

```dotenv
HAP_API_BASE_URL=https://YOUR-FUNCTION.azurewebsites.net/api
HAP_DEVICE_GUID=00000000-0000-4000-8000-000000000000
HAP_INPUT_DEVICE=
HAP_OUTPUT_DEVICE=
HAP_WAKEWORD_THRESHOLD=0.5
HAP_VAD_MODE=2
HAP_NO_SPEECH_TIMEOUT_SECONDS=3.0
HAP_SILENCE_TIMEOUT_SECONDS=1.2
HAP_MAX_COMMAND_SECONDS=30.0
HAP_PLAYBACK_COOLDOWN_SECONDS=0.75
HAP_LOG_LEVEL=INFO
```

Only the API URL and GUID are required. `HAP_MAX_COMMAND_SECONDS` may be
lowered but cannot exceed 30.

## Commands

```bash
home-assistant-pi run
home-assistant-pi doctor
home-assistant-pi devices
home-assistant-pi version
home-assistant-pi print-effective-config
```

The effective configuration command always redacts the Device GUID.

## Release build

Build output is isolated under `_src/.test-artifacts/pi-client-release/`:

```powershell
python -m pip install build
.\packaging\build-release.ps1 -Version 2.0.0
```

```bash
python3 -m pip install build
./packaging/build-release.sh --version 2.0.0
```

The published `home-assistant-pi-bundle-2.0.0.tar.gz` contains one wheel, three
lifecycle scripts, configuration metadata, and an internal wheel checksum. It
does not contain backend source, tests, recordings, or a virtual environment.
