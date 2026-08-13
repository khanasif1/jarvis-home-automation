# Raspberry Pi client

The client is deliberately limited to work that must happen near the user:

1. Pre-warm one openWakeWord TFLite model, then listen in 80 ms frames for
   “hey jarvis” or “hello jarvis.”
2. Greet the user, switch to 20 ms frames, and apply WebRTC VAD.
3. Keep only command audio plus a short pre-roll in memory, dispatch it to
   `POST /api/voice/stream`, and play the bundled search acknowledgement.
4. Play returned 24 kHz PCM chunks immediately.
5. Ask for another query and wait up to 30 seconds. Repeat the request/response
   loop when speech begins; otherwise play the bundled sleep message.
6. Wait 750 ms before re-enabling wake detection.

The flow is half-duplex: wake-word inference is disabled during upload,
response generation, and playback. No audio file or application data is stored.

## Configuration

`/etc/home-assistant-pi/config.env` is created by the release installer:

```dotenv
HAP_API_BASE_URL=https://YOUR-FUNCTION.azurewebsites.net/api
HAP_DEVICE_GUID=00000000-0000-4000-8000-000000000000
HAP_INPUT_DEVICE=
HAP_OUTPUT_DEVICE=
HAP_WAKEWORD_THRESHOLD=0.35
HAP_WAKEWORD_MODEL_PATH=
HAP_VAD_MODE=2
HAP_NO_SPEECH_TIMEOUT_SECONDS=3.0
HAP_FOLLOWUP_TIMEOUT_SECONDS=30.0
HAP_SILENCE_TIMEOUT_SECONDS=1.2
HAP_MAX_COMMAND_SECONDS=30.0
HAP_PLAYBACK_COOLDOWN_SECONDS=0.75
HAP_LOG_LEVEL=INFO
```

Only the API URL and GUID are required. `HAP_MAX_COMMAND_SECONDS` may be
lowered but cannot exceed 30. A blank audio device uses a compatible PortAudio
default or, when no default exists, the first compatible device. Use a numeric
index from `sudo home-assistant-pi-service devices` to override automatic
selection. The wrapper runs diagnostics as the configured desktop user with
the same PipeWire environment as the systemd service.

The bundled `hey_jarvis` model is pre-warmed so its first five initialization
frames cannot swallow the first wake attempt. The lower default threshold also
improves “hello jarvis” recognition. Tune `HAP_WAKEWORD_THRESHOLD` in the range
`0.25` to `0.5` for the room and microphone.

To change the phrase, train/export a custom openWakeWord TFLite model, copy it
to `/etc/home-assistant-pi/models/`, and set its absolute path in
`HAP_WAKEWORD_MODEL_PATH`. The directory is preserved across normal updates and
uninstalls. Run `sudo home-assistant-pi-service doctor` after restarting.

The bundled session prompts are:

- “I am your AI assistant. How can I help?”
- “I will search for your query and get back soon.”
- “Do you have another query? Please say it now.”
- “I am going back to sleep mode. Wake me up if you want to talk again.”

## Commands

```bash
home-assistant-pi run
home-assistant-pi doctor
home-assistant-pi devices
home-assistant-pi version
home-assistant-pi print-effective-config
```

The effective configuration command always redacts the Device GUID.

## Live activity

Release 2.0.4 writes unbuffered, correlated activity events directly to the
systemd journal. Monitor only new input/output activity in real time:

```bash
sudo journalctl \
  --unit home-assistant-pi.service \
  --follow \
  --lines 0 \
  --output cat |
  grep --line-buffered 'activity'
```

Each turn logs wake detection, command speech boundaries, backend response,
playback boundaries, completion, and failures with timestamps, a safe `turn=`
identifier, and byte/duration metadata. It never logs raw audio, transcript
content, the Device GUID, or credentials.

## Release build

Build output is isolated under `_src/.test-artifacts/pi-client-release/`:

```powershell
python -m pip install build
.\packaging\build-release.ps1 -Version 2.0.4
```

```bash
python3 -m pip install build
./packaging/build-release.sh --version 2.0.4
```

The published `home-assistant-pi-bundle-2.0.4.tar.gz` contains one wheel, three
lifecycle scripts, configuration metadata, and an internal wheel checksum. It
does not contain backend source, tests, recordings, or a virtual environment.
