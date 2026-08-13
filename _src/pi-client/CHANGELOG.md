# Changelog

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
