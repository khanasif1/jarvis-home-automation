# Changelog

All notable changes to the pi-client package are documented in this file.
The format loosely follows [Keep a Changelog](https://keepachangelog.com/),
and this project uses simple semantic-ish versioning for Pi releases
(`pi-vMAJOR.MINOR.PATCH` git tags trigger the release workflow).

## [1.0.0] - Unreleased

### Added

- Initial independent `pi-client` package: state machine, configuration
  loading/validation, journald-friendly logging, audio capture/playback,
  WAV helpers, a lightweight energy-based VAD, pluggable wake-word engines
  (`keyboard`, `porcupine`, `openwakeword`), a backend API client, and a
  reminder poller.
- `home-assistant-pi` CLI with `--version`, `doctor`, `test-microphone`,
  `test-speaker`, `disk-usage`, and `run` commands.
- Idempotent `install.sh`, `update.sh`, and `uninstall.sh` scripts that
  never use git or download the full repository, install only runtime
  dependencies with `pip install --no-cache-dir`, run as a dedicated
  `homeassistant` system user, preserve configuration across upgrades, and
  automatically roll back a failed update.
- systemd unit template (`systemd/home-assistant.service`).
- Release packaging scripts (`packaging/build-release.sh`,
  `packaging/build-release.ps1`) that build the wheel, sdist, a minimal Pi
  release bundle, and `SHA256SUMS`.
- Small bundled notification sound assets (activation, cancellation,
  offline).
- Unit test suite under `tests/`.
