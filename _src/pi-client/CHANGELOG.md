# Changelog

## 2.0.0

- Replaced push-to-talk/file uploads with “hey jarvis” activation and chunked
  PCM streaming.
- Added WebRTC VAD with 3-second no-speech cancellation, 1.2-second
  end-of-speech detection, and a 30-second hard maximum.
- Reduced the Pi runtime to wake detection, VAD, streaming, and playback.
- Replaced device registration and bearer tokens with one configured UUID.
- Removed reminders, Google integrations, local application state, and all
  committed tests.
