# Changelog

## 2.0.5

- Classify every detected follow-up utterance through Foundry as exactly
  `JARVIS_QUERY` or `JARVIS_SLEEP`.
- End immediately on “no more queries,” equivalent stop phrases, and
  unclear/noise-only audio without generating another answer.
- Require 160 ms of continuous VAD-positive audio before accepting speech and
  enforce the follow-up timeout against monotonic wall-clock time.

## 2.0.4

- Pre-warm openWakeWord after startup/reset and migrate the default detection
  threshold from `0.5` to `0.35` for better first-attempt activation.
- Support an optional custom openWakeWord TFLite model through
  `HAP_WAKEWORD_MODEL_PATH`.
- Add local greeting, search acknowledgement, follow-up, and sleep speech.
- Keep a session open for repeated queries and close it after 30 seconds without
  follow-up speech.
- Exclude long pre-speech silence from uploaded PCM while retaining a short
  pre-roll so the first command syllable is not clipped.

## 2.0.3

- Emit correlated `activity` events for wake detection, input capture and
  speech boundaries, backend response, output playback, completion, and errors.
- Include safe byte/duration metadata without recording audio, prompts,
  responses, the device GUID, or other credentials.
- Send unbuffered service output explicitly to the systemd journal for reliable
  real-time monitoring.

## 2.0.2

- Run the systemd service in the installing desktop user's PipeWire session so
  desktop microphones and resampling outputs remain visible.
- Clear numeric audio indexes when migrating from the isolated legacy service
  account because PortAudio indexes are user-session specific.
- Add `home-assistant-pi-service` for `doctor` and `devices` commands in the
  exact environment used by systemd.
- Stop failed installations instead of leaving a rapid restart loop running.

## 2.0.1

- Select the first compatible 16 kHz microphone when PortAudio has no default
  input instead of failing on device `-1`.
- Validate configured/default microphone and speaker selections in `doctor`.
- Make installation fail when the systemd service enters a restart loop.

## 2.0.0

- Replaced push-to-talk/file uploads with “hey jarvis” activation and chunked
  PCM streaming.
- Added WebRTC VAD with 3-second no-speech cancellation, 1.2-second
  end-of-speech detection, and a 30-second hard maximum.
- Reduced the Pi runtime to wake detection, VAD, streaming, and playback.
- Replaced device registration and bearer tokens with one configured UUID.
- Removed reminders, Google integrations, local application state, and all
  committed tests.
