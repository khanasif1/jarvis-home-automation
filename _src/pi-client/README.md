# Raspberry Pi client

The client is deliberately limited to work that must happen near the user:

1. Pre-warm one openWakeWord TFLite model, then listen in 80 ms frames for the
   single spoken wake term “Jarvis.”
2. Say “How can I help?”, switch to 20 ms frames, and apply WebRTC VAD.
3. Keep only command audio plus a short pre-roll in memory, dispatch it to
   `POST /api/voice/stream`, and wait silently for the response.
4. Play returned 24 kHz PCM chunks immediately.
5. Say “Anything else?” and wait up to 30 seconds. Ignore speech candidates
   shorter than 160 ms.
6. Classify detected follow-up audio through `POST /api/voice/intent`. Continue
   only for `JARVIS_QUERY`; for `JARVIS_SLEEP`, play the sleep message without
   requesting another answer. True silence also sleeps locally after 30 seconds.
7. Reset and pre-warm the detector, then re-enable wake capture immediately.

The flow is half-duplex: wake-word inference is disabled during upload,
response generation, and playback. No audio file or application data is stored.

## Configuration

`/etc/home-assistant-pi/config.env` is created by the release installer:

```dotenv
HAP_API_BASE_URL=https://YOUR-FUNCTION.azurewebsites.net/api
HAP_DEVICE_GUID=00000000-0000-4000-8000-000000000000
HAP_INPUT_DEVICE=
HAP_OUTPUT_DEVICE=
HAP_WAKEWORD_THRESHOLD=0.15
HAP_WAKEWORD_MODEL_PATH=
HAP_VAD_MODE=2
HAP_NO_SPEECH_TIMEOUT_SECONDS=3.0
HAP_FOLLOWUP_TIMEOUT_SECONDS=30.0
HAP_SILENCE_TIMEOUT_SECONDS=1.2
HAP_MAX_COMMAND_SECONDS=30.0
HAP_PLAYBACK_COOLDOWN_SECONDS=0.0
HAP_LOG_LEVEL=INFO
```

Only the API URL and GUID are required. `HAP_MAX_COMMAND_SECONDS` may be
lowered but cannot exceed 30. A blank audio device uses a compatible PortAudio
default or, when no default exists, the first compatible device. Use a numeric
index from `sudo home-assistant-pi-service devices` to override automatic
selection. The wrapper runs diagnostics as the configured desktop user with
the same PipeWire environment as the systemd service.

The bundled `hey_jarvis` model file is calibrated at `0.15` for the spoken term
“Jarvis” and pre-warmed so its five initialization frames cannot swallow the
first wake attempt. After every session, reset/warm-up finishes before
`IDLE_WAKEWORD`, and the zero-second default cooldown avoids discarding an
immediate wake call. Increase `HAP_WAKEWORD_THRESHOLD` toward `0.5` only if
background audio causes false activations. Set a small nonzero
`HAP_PLAYBACK_COOLDOWN_SECONDS` only if speaker echo causes a false wake.

To change the phrase, train/export a custom openWakeWord TFLite model, copy it
to `/etc/home-assistant-pi/models/`, and set its absolute path in
`HAP_WAKEWORD_MODEL_PATH`. The directory is preserved across normal updates and
uninstalls. Run `sudo home-assistant-pi-service doctor` after restarting.

The bundled session prompts are:

- “How can I help?”
- “Anything else?”
- “I am going back to sleep mode. Wake me up if you want to talk again.”

After the follow-up prompt, phrases such as “no,” “no thanks,” “no more
queries,” “that's all,” “goodbye,” and equivalent intent select
`JARVIS_SLEEP`. Unclear/noise-only audio also selects sleep so it cannot create
an answer/prompt loop.

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

Release 2.0.7 writes unbuffered, correlated activity events directly to the
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
.\packaging\build-release.ps1 -Version 2.0.7
```

```bash
python3 -m pip install build
./packaging/build-release.sh --version 2.0.7
```

The published `home-assistant-pi-bundle-2.0.7.tar.gz` contains one wheel, three
lifecycle scripts, configuration metadata, and an internal wheel checksum. It
does not contain backend source, tests, recordings, or a virtual environment.
