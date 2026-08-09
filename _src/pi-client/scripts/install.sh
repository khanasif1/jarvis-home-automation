#!/usr/bin/env bash
# install.sh - Install the home-assistant-pi voice assistant client.
#
# Usage:
#   sudo ./install.sh --version 1.0.1
#   sudo ./install.sh --version 1.0.1 --wakeword-extra porcupine
#
# This script must be run from within an extracted pi-client release
# bundle (it expects a wheel file, the systemd unit, and a config example
# to be present alongside it - see release-manifest.json). It never uses
# git and never downloads the full repository; it only installs the
# artifacts already present in the bundle directory.
#
# Safe to run more than once: re-running upgrades in place and preserves
# the existing /etc/home-assistant-pi/config.env file.
#
# After a successful install, this script copies update.sh/uninstall.sh to
# a stable location (/opt/home-assistant-pi/bin/) and cleans package caches.
# The release bundle remains intact so this installer can be rerun safely;
# operators may delete the whole bundle directory when they no longer need it.
#
# The base wheel intentionally ships without any production wake-word
# engine dependency, to keep every install lightweight: only the
# "keyboard" (stdin-driven) engine works out of the box, and it requires
# an interactive terminal, so it CANNOT trigger as an unattended systemd
# service. Pass --wakeword-extra porcupine|openwakeword to install a real
# engine's dependency alongside the wheel in this same run (see --help).
set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_NAME="home-assistant-pi"
SERVICE_USER="homeassistant"
SERVICE_GROUP="homeassistant"
INSTALL_DIR="/opt/home-assistant-pi"
VENV_DIR="${INSTALL_DIR}/venv"
VERSION_FILE="${INSTALL_DIR}/VERSION"
# Stable, install-permanent copies of update.sh/uninstall.sh, so users are
# never forced to keep the original downloaded/extracted bundle directory
# around just to be able to update or uninstall later.
INSTALL_BIN_DIR="${INSTALL_DIR}/bin"
CONFIG_DIR="/etc/home-assistant-pi"
CONFIG_FILE="${CONFIG_DIR}/config.env"
SYSTEMD_UNIT_PATH="/etc/systemd/system/${APP_NAME}.service"
SUPPORTED_ARCHES=("armv7l" "aarch64" "arm64")
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=9
# libportaudio2 is required for sounddevice's PortAudio bindings. libsndfile1
# is intentionally NOT installed: no code path uses the `soundfile` package or
# links against libsndfile (audio I/O uses the stdlib `wave` module plus raw
# PortAudio streams only), so it would only add disk footprint on every Pi.
REQUIRED_APT_PACKAGES=(python3 python3-venv python3-pip libportaudio2)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VERSION=""
FORCE=false
# Which optional wake-word engine extra (if any) to install alongside the
# base wheel. The base wheel intentionally ships with only the "keyboard"
# (stdin-driven) engine usable out of the box, to keep every Pi install
# lightweight; "keyboard" cannot function as an unattended systemd service
# (no interactive stdin), so production installs should select a real
# engine here. One of: none, porcupine, openwakeword.
WAKEWORD_EXTRA="none"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
err() {
  echo "ERROR: $*" >&2
  exit 1
}

info() {
  echo "[install] $*"
}

on_error() {
  local exit_code=$?
  echo "ERROR: install.sh failed (exit code ${exit_code}) at line ${BASH_LINENO[0]}." >&2
  exit "${exit_code}"
}
trap on_error ERR

usage() {
  cat <<EOF
Usage: sudo $0 --version X.Y.Z [--force] [--wakeword-extra ENGINE]

  --version X.Y.Z        Version being installed (for logging/metadata only;
                          the wheel file already present in this directory is
                          what actually gets installed).
  --force                 Skip architecture/OS checks (for development only).
  --wakeword-extra ENGINE Install the optional dependency for a production
                          wake-word engine alongside the base wheel. One of:
                          none (default), porcupine, openwakeword.
                          The "keyboard" engine (the config default) requires
                          an interactive terminal and CANNOT run as a systemd
                          service -- select porcupine or openwakeword here
                          for any unattended/production install, then set
                          wakeword_engine accordingly in config.env.
EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="${2:-}"
      shift 2
      ;;
    --force)
      FORCE=true
      shift
      ;;
    --wakeword-extra)
      WAKEWORD_EXTRA="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      err "Unknown argument: $1"
      ;;
  esac
done

[[ -n "${VERSION}" ]] || err "Missing required --version argument. See --help."
[[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z]+)*$ ]] \
  || err "Invalid --version '${VERSION}'. Expected a version such as 1.0.0."

if [[ -f "${SCRIPT_DIR}/VERSION" ]]; then
  BUNDLE_VERSION="$(tr -d '\r\n' < "${SCRIPT_DIR}/VERSION")"
  [[ "${BUNDLE_VERSION}" == "${VERSION}" ]] \
    || err "Requested version ${VERSION} does not match bundle version ${BUNDLE_VERSION}."
fi

case "${WAKEWORD_EXTRA}" in
  none|porcupine|openwakeword) ;;
  *) err "Invalid --wakeword-extra '${WAKEWORD_EXTRA}'. Must be one of: none, porcupine, openwakeword." ;;
esac

# ---------------------------------------------------------------------------
# Preconditions
# ---------------------------------------------------------------------------
if [[ "${EUID}" -ne 0 ]]; then
  err "This installer must be run as root, e.g.: sudo $0 --version ${VERSION}"
fi

info "Installing home-assistant-pi ${VERSION}"

# 1. Architecture check.
ARCH="$(uname -m)"
arch_supported=false
for a in "${SUPPORTED_ARCHES[@]}"; do
  [[ "${ARCH}" == "${a}" ]] && arch_supported=true
done
if [[ "${arch_supported}" != "true" ]]; then
  if [[ "${FORCE}" == "true" ]]; then
    info "WARNING: unsupported architecture '${ARCH}' - continuing due to --force"
  else
    err "Unsupported architecture '${ARCH}'. Supported: ${SUPPORTED_ARCHES[*]}. Use --force to override."
  fi
fi

# 2. OS check (Raspberry Pi OS / Debian-based).
if [[ -r /etc/os-release ]]; then
  . /etc/os-release
  if [[ "${ID:-}" != "raspbian" && "${ID_LIKE:-}" != *debian* && "${ID:-}" != "debian" ]]; then
    if [[ "${FORCE}" == "true" ]]; then
      info "WARNING: unrecognized OS '${PRETTY_NAME:-unknown}' - continuing due to --force"
    else
      err "Unsupported OS '${PRETTY_NAME:-unknown}'. This installer targets Raspberry Pi OS (Debian-based). Use --force to override."
    fi
  fi
else
  if [[ "${FORCE}" != "true" ]]; then
    err "Cannot determine OS (missing /etc/os-release). Use --force to override."
  fi
fi

# Python version check.
if ! command -v python3 >/dev/null 2>&1; then
  err "python3 is required but was not found on PATH."
fi
PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
PY_MAJOR="${PY_VER%%.*}"
PY_MINOR="${PY_VER##*.}"
if (( PY_MAJOR < MIN_PYTHON_MAJOR || (PY_MAJOR == MIN_PYTHON_MAJOR && PY_MINOR < MIN_PYTHON_MINOR) )); then
  err "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is required, found ${PY_VER}."
fi
info "Python ${PY_VER} OK"

# ---------------------------------------------------------------------------
# 3. Install required OS packages only.
# ---------------------------------------------------------------------------
info "Installing required OS packages: ${REQUIRED_APT_PACKAGES[*]}"
export DEBIAN_FRONTEND=noninteractive
if ! apt-get update; then
  err "apt-get update failed. Aborting (no dependency installation attempted)."
fi
if ! apt-get install -y --no-install-recommends "${REQUIRED_APT_PACKAGES[@]}"; then
  err "Failed to install required OS packages. Aborting installation."
fi

# ---------------------------------------------------------------------------
# 4/5. Dedicated system user with only the audio group it needs.
# ---------------------------------------------------------------------------
if ! getent group "${SERVICE_GROUP}" >/dev/null 2>&1; then
  info "Creating group ${SERVICE_GROUP}"
  groupadd --system "${SERVICE_GROUP}" || err "Failed to create group ${SERVICE_GROUP}"
fi

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
  info "Creating system user ${SERVICE_USER}"
  useradd --system \
    --gid "${SERVICE_GROUP}" \
    --home-dir "${INSTALL_DIR}" \
    --no-create-home \
    --shell /usr/sbin/nologin \
    "${SERVICE_USER}" || err "Failed to create user ${SERVICE_USER}"
else
  info "System user ${SERVICE_USER} already exists"
fi

if getent group audio >/dev/null 2>&1; then
  usermod -aG audio "${SERVICE_USER}" || err "Failed to add ${SERVICE_USER} to the audio group"
else
  info "WARNING: 'audio' group not found; microphone/speaker access may require manual configuration"
fi

# ---------------------------------------------------------------------------
# 6/7. Application directory and virtual environment.
# ---------------------------------------------------------------------------
mkdir -p "${INSTALL_DIR}"
chown root:root "${INSTALL_DIR}"
chmod 0755 "${INSTALL_DIR}"

if [[ ! -x "${VENV_DIR}/bin/python3" ]]; then
  info "Creating virtual environment at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}" || err "Failed to create virtual environment"
else
  info "Reusing existing virtual environment at ${VENV_DIR}"
fi
chown -R root:root "${VENV_DIR}"

# ---------------------------------------------------------------------------
# 8/9. Install the wheel (runtime dependencies only), never dev/test deps.
# ---------------------------------------------------------------------------
WHEEL_FILE="$(find "${SCRIPT_DIR}" -maxdepth 1 -name '*.whl' | head -n 1)"
[[ -n "${WHEEL_FILE}" ]] || err "No wheel (*.whl) file found next to install.sh. Is the bundle intact?"

# When a production wake-word engine was requested via --wakeword-extra,
# install its optional dependency (e.g. pvporcupine/openwakeword) alongside
# the base wheel in the same pip invocation, while the wheel file is still
# present -- extras are requested from the local wheel path itself (pip
# supports `pip install '/path/to/pkg.whl[extra]'`), so no PyPI/index lookup
# beyond the extra's own dependency is required, and --no-cache-dir still
# applies to everything installed here.
if [[ "${WAKEWORD_EXTRA}" != "none" ]]; then
  WHEEL_INSTALL_TARGET="${WHEEL_FILE}[${WAKEWORD_EXTRA}]"
  info "Installing $(basename "${WHEEL_FILE}") with the '${WAKEWORD_EXTRA}' wake-word extra"
else
  WHEEL_INSTALL_TARGET="${WHEEL_FILE}"
  info "Installing $(basename "${WHEEL_FILE}") (no production wake-word extra requested;"
  info "  the 'keyboard' default engine will NOT work as a systemd service -- re-run with"
  info "  --wakeword-extra porcupine|openwakeword, or pip install the extra manually later,"
  info "  before relying on this install to run unattended. See README.md.)"
fi

if ! "${VENV_DIR}/bin/pip" install --no-cache-dir --upgrade pip; then
  err "Failed to upgrade pip inside the virtual environment."
fi

PIP_INSTALL_ARGS=("${WHEEL_INSTALL_TARGET}")
if [[ -f "${SCRIPT_DIR}/requirements-runtime.txt" ]]; then
  PIP_INSTALL_ARGS=("-r" "${SCRIPT_DIR}/requirements-runtime.txt" "${WHEEL_INSTALL_TARGET}")
fi
if ! "${VENV_DIR}/bin/pip" install --no-cache-dir "${PIP_INSTALL_ARGS[@]}"; then
  err "Failed to install home-assistant-pi into the virtual environment. Aborting."
fi
printf '%s\n' "${VERSION}" > "${VERSION_FILE}"
chown root:root "${VERSION_FILE}"
chmod 0644 "${VERSION_FILE}"

# ---------------------------------------------------------------------------
# 10. Install the systemd unit.
# ---------------------------------------------------------------------------
UNIT_SRC="$(find "${SCRIPT_DIR}" -maxdepth 1 -name '*.service' | head -n 1)"
[[ -n "${UNIT_SRC}" ]] || err "No systemd unit (*.service) file found in the bundle."
cp "${UNIT_SRC}" "${SYSTEMD_UNIT_PATH}"
chmod 0644 "${SYSTEMD_UNIT_PATH}"
systemctl daemon-reload || err "systemctl daemon-reload failed"

# ---------------------------------------------------------------------------
# 11/12/13. Configuration file: create on first install, preserve on upgrade.
# ---------------------------------------------------------------------------
# Ownership is root:${SERVICE_GROUP}, NOT ${SERVICE_USER}:${SERVICE_GROUP} --
# the service's own runtime account must not be able to modify its own
# configuration (device token, API URL, etc.). systemd's EnvironmentFile=
# directive is read by the systemd manager (PID 1, running as root) before
# it drops privileges to User=${SERVICE_USER}, so root ownership does not
# prevent the service from starting. ${SERVICE_USER} remains a member of
# the ${SERVICE_GROUP} group so manual CLI diagnostics (e.g. sourcing
# config.env before running `home-assistant-pi doctor` by hand) still work
# via group-read (0640), without granting write access.
mkdir -p "${CONFIG_DIR}"
chown "root:${SERVICE_GROUP}" "${CONFIG_DIR}"
chmod 0750 "${CONFIG_DIR}"

CONFIG_EXAMPLE="$(find "${SCRIPT_DIR}" -maxdepth 1 -name 'config.env.example' | head -n 1)"
if [[ -f "${CONFIG_FILE}" ]]; then
  info "Preserving existing configuration at ${CONFIG_FILE}"
else
  [[ -n "${CONFIG_EXAMPLE}" ]] || err "No config.env.example found in the bundle."
  cp "${CONFIG_EXAMPLE}" "${CONFIG_FILE}"
  info "Created new configuration file at ${CONFIG_FILE} (edit it before starting the service)"
fi
chown "root:${SERVICE_GROUP}" "${CONFIG_FILE}"
chmod 0640 "${CONFIG_FILE}"

# ---------------------------------------------------------------------------
# 14. Enable (but do not unconditionally start) the service.
# ---------------------------------------------------------------------------
systemctl enable "${APP_NAME}.service" || err "Failed to enable the ${APP_NAME} service"

# ---------------------------------------------------------------------------
# 15. Start only if configuration already looks complete (idempotent re-run).
# ---------------------------------------------------------------------------
config_is_complete() {
  local key
  for key in HAP_DEVICE_ID HAP_DEVICE_TOKEN HAP_API_BASE_URL; do
    if ! grep -Eq "^${key}=.+" "${CONFIG_FILE}"; then
      return 1
    fi
  done
  return 0
}

if config_is_complete; then
  info "Configuration already complete; (re)starting the service"
  systemctl restart "${APP_NAME}.service" || err "Failed to start the ${APP_NAME} service"
else
  info "Configuration is incomplete; the service will not be started automatically."
fi

# ---------------------------------------------------------------------------
# 16. Install update.sh/uninstall.sh to a stable, install-permanent location.
# ---------------------------------------------------------------------------
# These scripts have no dependency on files in the download/extraction
# directory (update.sh downloads its own fresh bundle per run; uninstall.sh
# only touches fixed system paths), so copying them here means the only
# usable updater/uninstaller is never dependent on retaining an old download
# directory -- the extracted bundle can be deleted entirely after install.
info "Installing update/uninstall tooling to ${INSTALL_BIN_DIR}"
mkdir -p "${INSTALL_BIN_DIR}"
chmod 0755 "${INSTALL_BIN_DIR}"
UPDATE_SRC="$(find "${SCRIPT_DIR}" -maxdepth 1 -name 'update.sh' | head -n 1 || true)"
UNINSTALL_SRC="$(find "${SCRIPT_DIR}" -maxdepth 1 -name 'uninstall.sh' | head -n 1 || true)"
if [[ -n "${UPDATE_SRC}" ]]; then
  cp "${UPDATE_SRC}" "${INSTALL_BIN_DIR}/update.sh"
  chmod 0755 "${INSTALL_BIN_DIR}/update.sh"
else
  info "WARNING: update.sh not found in the bundle; the stable copy at ${INSTALL_BIN_DIR}/update.sh was not (re)installed."
fi
if [[ -n "${UNINSTALL_SRC}" ]]; then
  cp "${UNINSTALL_SRC}" "${INSTALL_BIN_DIR}/uninstall.sh"
  chmod 0755 "${INSTALL_BIN_DIR}/uninstall.sh"
else
  info "WARNING: uninstall.sh not found in the bundle; the stable copy at ${INSTALL_BIN_DIR}/uninstall.sh was not (re)installed."
fi
chown -R root:root "${INSTALL_BIN_DIR}"

# ---------------------------------------------------------------------------
# 17. Clean up temporary package caches. Keep the wheel in the extracted
# release bundle so running this installer again with the same inputs remains
# safe and produces the same installed state.
# ---------------------------------------------------------------------------
apt-get clean || true
rm -rf /root/.cache/pip "${INSTALL_DIR}"/.cache 2>/dev/null || true

# ---------------------------------------------------------------------------
# 18. Print the exact commands needed to configure and start the service.
# ---------------------------------------------------------------------------
echo
info "Installation complete."
echo
echo "Next steps:"
echo "  1. Edit configuration:   sudo nano ${CONFIG_FILE}"
echo "     (set HAP_DEVICE_ID, HAP_DEVICE_TOKEN, and HAP_API_BASE_URL)"
if [[ "${WAKEWORD_EXTRA}" != "none" ]]; then
  echo "     (also set HAP_WAKEWORD_ENGINE=${WAKEWORD_EXTRA}, and, for porcupine,"
  echo "     HAP_PORCUPINE_ACCESS_KEY -- its extra was installed with --wakeword-extra)"
else
  echo "     WARNING: no production wake-word extra was installed (--wakeword-extra"
  echo "     was not given), so HAP_WAKEWORD_ENGINE must stay 'keyboard', which"
  echo "     CANNOT trigger under systemd (no interactive stdin). 'doctor' and the"
  echo "     service will both fail/warn until you re-run this installer with"
  echo "     --wakeword-extra porcupine|openwakeword (or pip install the extra"
  echo "     into ${VENV_DIR} manually) and set HAP_WAKEWORD_ENGINE accordingly."
fi
echo "  2. Start the service:    sudo systemctl start ${APP_NAME}.service"
echo "  3. Check status:         sudo systemctl status ${APP_NAME}.service"
echo "  4. Follow logs:          sudo journalctl -u ${APP_NAME}.service -f"
echo "  5. Run diagnostics:      sudo -u ${SERVICE_USER} ${VENV_DIR}/bin/home-assistant-pi doctor"
echo
echo "update.sh/uninstall.sh have been copied to ${INSTALL_BIN_DIR}, package"
echo "caches were removed, and this release bundle remains rerunnable. When it"
echo "is no longer needed, delete the entire download folder in one step:"
echo "  cd .. && rm -rf $(basename "${SCRIPT_DIR}")"
echo "Future updates/uninstalls can be run from the stable copies instead:"
echo "  sudo ${INSTALL_BIN_DIR}/update.sh --version X.Y.Z"
echo "  sudo ${INSTALL_BIN_DIR}/uninstall.sh [--purge]"
echo
