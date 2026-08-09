# home-assistant-pi (pi-client)

The Raspberry Pi voice-assistant client for the jarvis-home-automation
project. This directory is a fully independent, self-contained Python
package: it can be built, tested, and installed on a Raspberry Pi **without
ever cloning the full repository, without git, and without any
azure-backend or infra source**.

It listens for a wake word, records the user's speech, sends it to the
azure-backend voice API, plays back the spoken reply, and periodically
polls for and speaks due reminders.

## What this package does NOT contain

- Azure Functions / azure-backend source or dependencies.
- Bicep templates or Azure deployment scripts.
- Backend tests.
- A bundled Python virtual environment (not portable across Pi OS
  versions/architectures - the installer creates one on the device).

## Repository layout (this directory only)

```
pi-client/
  pyproject.toml            Package metadata and build configuration.
  requirements-runtime.txt  Bounded runtime dependencies (installed on the Pi).
  requirements-dev.txt      Test/build/lint dependencies (never installed on the Pi).
  .env.example              Documented template for /etc/home-assistant-pi/config.env.
  CHANGELOG.md
  src/home_assistant_pi/    The installable Python package.
  tests/                    Unit tests (pytest).
  scripts/                  Install/update/uninstall + developer helper scripts.
  packaging/                Release-build scripts and manifest template.
  systemd/                  systemd unit template.
```

## Developing

```bash
cd pi-client
python -m venv ../.test-artifacts/venvs/pi-client
. ../.test-artifacts/venvs/pi-client/bin/activate
python -m pip install --requirement requirements-dev.txt
export PYTHONPYCACHEPREFIX="$(cd .. && pwd)/.test-artifacts/pycache/pi-client"
python -m pytest tests
```

Any generated build/test output (coverage reports, ad-hoc scratch files,
etc.) should be written under `<source-root>/.test-artifacts/`, never
scattered across `pi-client/`.

### Building the package yourself

```bash
mkdir -p .test-artifacts/pi-build/source
cp pi-client/pyproject.toml pi-client/README.md .test-artifacts/pi-build/source/
cp -a pi-client/src .test-artifacts/pi-build/source/
python -m build .test-artifacts/pi-build/source \
  --outdir .test-artifacts/pi-build/dist
```

produces `home_assistant_pi-<version>-py3-none-any.whl` and a matching
`.tar.gz` sdist under `.test-artifacts/pi-build/dist/` without leaving
setuptools metadata in `pi-client/`.

### Building a full release bundle (wheel + install scripts + systemd unit)

```bash
pi-client/packaging/build-release.sh            # Linux/macOS
pi-client/packaging/build-release.ps1            # Windows/PowerShell
```

Both scripts run the unit tests, build the wheel and sdist, assemble a
release bundle containing only what the Pi needs, and write
`SHA256SUMS` alongside the artifacts. By default all output goes to
`<source-root>/.test-artifacts/pi-client-release/dist/`; pass
`--output-dir`/`-OutputDir` to write elsewhere.

### Publishing a GitHub release manually

No workflow publishes releases. From `_src/`, build and publish explicitly:

```bash
pi-client/packaging/build-release.sh --version 1.0.0
gh release create pi-v1.0.0 .test-artifacts/pi-client-release/dist/* \
  --target main \
  --title "Home Assistant Pi 1.0.0" \
  --generate-notes
```

## Raspberry Pi installation (no git, no full repository)

The commands below install release `1.0.0` from this repository.
For a fork or a different release, replace `khanasif1`,
`jarvis-home-automation`, and `1.0.0` with the matching owner, repository, and
version.

```bash
mkdir -p ~/home-assistant-install
cd ~/home-assistant-install

curl --fail --location \
  -o home-assistant-pi-bundle.tar.gz \
  https://github.com/khanasif1/jarvis-home-automation/releases/download/pi-v1.0.0/home-assistant-pi-bundle-1.0.0.tar.gz

curl --fail --location \
  -o SHA256SUMS \
  https://github.com/khanasif1/jarvis-home-automation/releases/download/pi-v1.0.0/SHA256SUMS

sha256sum --check SHA256SUMS --ignore-missing

tar -xzf home-assistant-pi-bundle.tar.gz
sudo ./install.sh --version 1.0.0
```

Do **not** run installers with `curl ... | sudo bash`. Always download the
installer first (it is included in the bundle you extracted above) so you
can inspect it and verify its checksum before running it.

For a private repository, use the GitHub CLI credential store rather than
putting a token in a command line, file, image, installer, or service:

```bash
gh auth login
gh release download pi-v1.0.0 \
  --repo khanasif1/jarvis-home-automation \
  --pattern 'home-assistant-pi-bundle-1.0.0.tar.gz' \
  --pattern 'SHA256SUMS'
```

Never put a GitHub token in the installer script, source code, a disk
image, command line, or the systemd unit file.

### What `install.sh` does

1. Detects the Raspberry Pi CPU architecture (`armv7l` / `aarch64`) and OS,
   erroring out (or requiring `--force` for development use) if unsupported.
2. Checks the Python version (3.9+ required).
3. Installs only the required OS packages (`python3`, `python3-venv`,
   `python3-pip`, `libportaudio2`) via `apt-get`.
4. Creates a dedicated system user (`homeassistant`) with no login shell.
5. Grants that user membership in the `audio` group only.
6. Creates root-owned `/opt/home-assistant-pi` and a read-only virtual
   environment at `/opt/home-assistant-pi/venv`; the runtime account cannot
   replace its own code.
7. Installs the wheel with `pip install --no-cache-dir` (runtime
   dependencies only - never test/lint/build tooling). When
   `--wakeword-extra porcupine|openwakeword` is given, the matching optional
   dependency is installed in the same command (see "Wake-word engines"
   below) before the downloaded wheel file is removed.
8. Installs the systemd unit and enables (but does not blindly start) the
   service.
9. Creates `/etc/home-assistant-pi/config.env` (or preserves an existing
   one on re-run/upgrade) owned by `root:homeassistant` with `0640`
   permissions - readable by the service via group membership, but not
   writable by the service's own runtime account.
10. Starts the service automatically only if the required configuration
    values are already present; otherwise it prints the exact commands to
    finish configuring and starting it.
11. Copies `update.sh` and `uninstall.sh` to a stable, install-permanent
    location at `/opt/home-assistant-pi/bin/` (`root:root`, `0755`), so
    future updates/uninstalls never depend on retaining this download
    directory.
12. Deletes the now-redundant downloaded wheel file and cleans up pip/apt
    caches after a successful install - see "Cleaning up after install"
    below for the one-command way to remove the rest of the download
    directory too.

Re-running `install.sh` is safe: it upgrades the wheel in place and never
overwrites an existing configuration file.

### Cleaning up after install

Once `install.sh` completes successfully, everything it needs going forward
lives under `/opt/home-assistant-pi` and `/etc/home-assistant-pi` - it has
already copied `update.sh`/`uninstall.sh` to
`/opt/home-assistant-pi/bin/` and deleted the downloaded wheel and package
caches. The entire extracted bundle directory (`~/home-assistant-install` in
the example above, including the downloaded `.tar.gz`, `SHA256SUMS`,
`install.sh`, and the now-copied `update.sh`/`uninstall.sh`) is safe to
delete in one step:

```bash
cd ~ && rm -rf ~/home-assistant-install
```

Future updates/uninstalls can be run from the stable copies instead of the
(now deleted) download directory:

```bash
sudo /opt/home-assistant-pi/bin/update.sh --version 1.1.0
sudo /opt/home-assistant-pi/bin/uninstall.sh [--purge]
```

## Updating

`update.sh` has no dependency on the original download directory: it
downloads its own fresh release bundle per run, so it can be invoked either
from a newly extracted bundle or from the stable copy `install.sh` leaves at
`/opt/home-assistant-pi/bin/update.sh`.

```bash
sudo ./update.sh --version 1.1.0
# or, from the stable installed copy (no download directory required):
sudo /opt/home-assistant-pi/bin/update.sh --version 1.1.0
```

For a **private** repository, never pass a token on the command line
(command lines are visible to every local user via `ps`/`/proc`). Instead
either export `GITHUB_TOKEN` before invoking `sudo` and preserve only that
named variable:

```bash
export GITHUB_TOKEN="$(gh auth token)"
sudo --preserve-env=GITHUB_TOKEN ./update.sh --version 1.1.0
unset GITHUB_TOKEN
```

(the token is fed to `curl` as a config directive over stdin, so it is
never written to disk and never appears in any subprocess's argv), or,
preferably, let the GitHub CLI (`gh`, which you must have already
authenticated with `gh auth login`) do the download and hand this script a
local directory instead:

```bash
gh release download "pi-v1.1.0" --repo khanasif1/jarvis-home-automation \
  --pattern 'home-assistant-pi-bundle-*.tar.gz' --pattern 'SHA256SUMS' \
  --dir /tmp/hap-release
sudo ./update.sh --version 1.1.0 --bundle-dir /tmp/hap-release
```

`update.sh` downloads/uses **only** the requested release bundle plus its
checksums (never git, never a repository ZIP, never azure-backend/infra),
verifies its SHA-256 checksum, stops the service (and confirms it actually
stopped), keeps the existing `config.env` completely untouched (so device
ID, device token, timezone, audio device, and wake-word settings all
survive), keeps one rollback copy of the previous virtual environment
*and* the previous systemd unit file, installs the new wheel with
`--no-cache-dir`, and restarts the service. If the new version fails to
start, it automatically rolls back to the previous version (venv, VERSION
file, and systemd unit) and only reports the rollback as successful once
`systemctl is-active` confirms the restored service is actually running;
if the rollback itself fails to start, it says so explicitly instead of
claiming success. Temporary download/extraction files are always removed, whether the update
succeeds, fails, or rolls back. After a successful health check the updater
also refreshes the stable `/opt/home-assistant-pi/bin/update.sh` and
`uninstall.sh` copies from the new bundle; failed releases never replace the
known-good maintenance tools.

By default, `update.sh` auto-detects and preserves whichever wake-word
extra (`porcupine`/`openwakeword`) is already installed, so a production
install never silently loses its working wake-word engine. Pass
`--wakeword-extra ENGINE` explicitly to switch engines during an update.

## Uninstalling

`uninstall.sh` only touches fixed system paths and does not depend on the
original download directory either, so it can be run from the stable copy
just as well as from a freshly extracted bundle:

```bash
sudo ./uninstall.sh            # keeps /etc/home-assistant-pi for a future reinstall
sudo ./uninstall.sh --purge    # also removes configuration and the dedicated user/group

# or, from the stable installed copy:
sudo /opt/home-assistant-pi/bin/uninstall.sh [--purge]
```

## CLI commands

Once installed, the `home-assistant-pi` console script is available inside
the venv (`/opt/home-assistant-pi/venv/bin/home-assistant-pi`):

```bash
home-assistant-pi --version
home-assistant-pi doctor            # configuration + hardware status, secrets never shown
home-assistant-pi test-microphone   # records a short clip and reports on it
home-assistant-pi test-speaker      # plays a notification sound
home-assistant-pi disk-usage        # reports installed application disk usage
home-assistant-pi run               # runs the main assistant loop (used by systemd)
```

## Configuration

All configuration is via environment variables (normally provided through
`/etc/home-assistant-pi/config.env`, loaded by systemd's `EnvironmentFile=`
directive). See `.env.example` for the full list with defaults and
descriptions. Secrets (the device token) are never logged, printed, or
included in `doctor` output beyond a masked preview.

`HAP_API_BASE_URL` must be the infrastructure output ending in `/api` (for
example, `https://myapp.azurewebsites.net/api`), not just the Function App
host name.

## Disk-space and logging notes

- No raw or conversation audio is ever persisted to disk; temporary WAV
  data lives only in memory during a request.
- The application writes to stdout/stderr only; systemd/journald captures
  and rotates logs, so there are no unbounded application log files. To
  bound journal disk usage, set (e.g.) `SystemMaxUse=50M` in
  `/etc/systemd/journald.conf` (or a drop-in under
  `/etc/systemd/journald.conf.d/`) and run `sudo systemctl restart
  systemd-journald`.
- `update.sh` retains only the current and one previous version for
  rollback, and removes stale downloads/temp files after every run.
- No Docker, no cloned repository, and no Azure SDK packages are used on
  the device.

## Wake-word engines

Set `HAP_WAKEWORD_ENGINE` in `config.env` to one of:

- `keyboard` (default): no model files or extra dependencies; treats
  pressing Enter on the controlling terminal as the wake event. **Useful
  only for interactive development/testing** - it requires a real TTY on
  stdin, which systemd never provides (stdin is always `/dev/null` there).
  Running the service with this engine would otherwise look "active"
  forever while never actually detecting a wake word. To make this
  impossible to do by accident:
  - `home-assistant-pi doctor` **fails** the `wakeword_engine` check when
    `keyboard` is configured and stdin is not a TTY (i.e. exactly the
    conditions the real service runs under), and **warns** even when run
    interactively, since the service itself never is.
  - `home-assistant-pi run` (what the systemd unit executes) refuses to
    start under the same non-interactive+keyboard combination, printing a
    clear "Wake-word engine error" and exiting non-zero (so systemd marks
    the unit as failed) instead of spinning silently.
- `porcupine`: Picovoice Porcupine. Requires the optional
  `home-assistant-pi[porcupine]` extra and `HAP_PORCUPINE_ACCESS_KEY`.
- `openwakeword`: open-source openWakeWord models. Requires the optional
  `home-assistant-pi[openwakeword]` extra.

### Production installation path

The base wheel is intentionally kept lightweight (no wake-word engine
dependencies beyond the stdlib/`keyboard` path). For any real deployment,
install one of the two production extras **at install time**, in the same
`pip install --no-cache-dir` invocation that installs the base wheel, before
the downloaded wheel file is cleaned up:

```bash
sudo ./install.sh --version 1.0.0 --wakeword-extra porcupine
# or
sudo ./install.sh --version 1.0.0 --wakeword-extra openwakeword
```

Then set the matching engine (and, for Porcupine, an access key) in
`/etc/home-assistant-pi/config.env`:

```bash
HAP_WAKEWORD_ENGINE=porcupine
HAP_PORCUPINE_ACCESS_KEY=your-key-here
```

`update.sh` auto-detects whichever extra is currently installed and carries
it forward by default, so routine updates never silently downgrade a
production install back to the `keyboard` engine; pass `--wakeword-extra`
explicitly to switch engines during an update instead.

## Relationship to the rest of the monorepo

This package has **no runtime import** of anything under `azure-backend/`
or `infra/`. Its API request/response shapes are hand-maintained in
`src/home_assistant_pi/api/models.py` to match the project's API contract;
it does not read `contracts/` at build or run time, so the wheel remains
installable even if only this `pi-client/` directory is present on disk.
