#!/usr/bin/env bash
# update.sh - Update an existing home-assistant-pi installation to a new
# released version.
#
# Usage:
#   export GITHUB_TOKEN="$(gh auth token)"
#   sudo --preserve-env=GITHUB_TOKEN ./update.sh --version 1.1.0
#   unset GITHUB_TOKEN
#   sudo ./update.sh --version 1.1.0   # public repo
#
# This script downloads ONLY the requested versioned pi-client release
# bundle from GitHub Releases (never a full repository ZIP, never via git,
# and never the azure-backend or infra folders). It verifies the bundle's
# SHA-256 checksum before doing anything else, preserves the existing
# configuration file untouched, keeps a single rollback copy of the
# previously installed version, and automatically rolls back if the new
# version fails to start.
#
# Private repositories: set the GITHUB_TOKEN environment variable (never
# pass a token as a command-line argument -- command lines are visible to
# every local user via `ps`/`/proc`). This script never writes GITHUB_TOKEN
# to disk and never places it in a subprocess's argv; the Authorization
# header is instead fed to curl as a config directive over stdin (`curl -K
# -`), which only ever shows `-K -` in `ps` output. For most private-repo
# use cases, the recommended and simplest approach is to skip this script's
# own download step entirely and use the GitHub CLI instead, which handles
# authentication for you:
#
#   gh release download "pi-v${VERSION}" --repo khanasif1/jarvis-home-automation \
#     --pattern 'home-assistant-pi-bundle-*.tar.gz' --pattern 'SHA256SUMS' \
#     --dir /some/local/download/dir
#
# then point this script at a pre-downloaded bundle directory (see
# --bundle-dir) instead of letting it perform the HTTP download itself.
set -euo pipefail

APP_NAME="home-assistant-pi"
SERVICE_USER="homeassistant"
SERVICE_GROUP="homeassistant"
INSTALL_DIR="/opt/home-assistant-pi"
INSTALL_BIN_DIR="${INSTALL_DIR}/bin"
VENV_DIR="${INSTALL_DIR}/venv"
VENV_ROLLBACK_DIR="${INSTALL_DIR}/venv.rollback"
VENV_NEW_DIR="${INSTALL_DIR}/venv.new"
VERSION_FILE="${INSTALL_DIR}/VERSION"
VERSION_ROLLBACK_FILE="${INSTALL_DIR}/VERSION.rollback"
CONFIG_DIR="/etc/home-assistant-pi"
CONFIG_FILE="${CONFIG_DIR}/config.env"
SYSTEMD_UNIT_PATH="/etc/systemd/system/${APP_NAME}.service"
SYSTEMD_UNIT_ROLLBACK_PATH="${INSTALL_DIR}/home-assistant.service.rollback"

VERSION=""
REPO="khanasif1/jarvis-home-automation"
BUNDLE_DIR=""
SERVICE_START_WAIT_SECONDS=8
WORK_DIR=""
# Which optional wake-word engine extra (if any) to install alongside the
# new wheel. Left empty means "auto-detect from the currently installed
# venv" (see detect_installed_wakeword_extra below), so that updating never
# silently drops a production wake-word engine that was previously
# installed via install.sh --wakeword-extra. Pass --wakeword-extra
# explicitly to override the detected value (e.g. to add/switch engines).
WAKEWORD_EXTRA=""

err() {
  echo "ERROR: $*" >&2
  exit 1
}

info() {
  echo "[update] $*"
}

cleanup() {
  # 10. Always delete temporary download/extraction files, on success,
  # failure, or rollback.
  if [[ -n "${WORK_DIR}" && -d "${WORK_DIR}" ]]; then
    rm -rf "${WORK_DIR}"
  fi
}
trap cleanup EXIT

on_error() {
  local exit_code=$?
  echo "ERROR: update.sh failed (exit code ${exit_code}) at line ${BASH_LINENO[0]}." >&2
  exit "${exit_code}"
}
trap on_error ERR

usage() {
  cat <<EOF
Usage: sudo $0 --version X.Y.Z [--repo OWNER/REPOSITORY] [--bundle-dir DIR] [--wakeword-extra ENGINE]

  --version X.Y.Z         Version to update to (required).
  --repo OWNER/REPOSITORY GitHub repository to download the release
                          bundle from (defaults to
                          khanasif1/jarvis-home-automation).
  --bundle-dir DIR        Use an already-downloaded bundle + SHA256SUMS
                          from DIR instead of downloading them here (e.g.
                          after fetching them yourself with
                          'gh release download'). Skips the HTTP download
                          step entirely.
  --wakeword-extra ENGINE Install this production wake-word engine's
                          optional dependency (one of: none, porcupine,
                          openwakeword) into the new environment. If
                          omitted, whichever engine is currently installed
                          is auto-detected and carried forward, so a plain
                          update never silently drops a previously
                          installed production wake-word engine.

For private repositories, set the GITHUB_TOKEN environment variable
instead of passing a token on the command line:

  export GITHUB_TOKEN="\$(gh auth token)"
  sudo --preserve-env=GITHUB_TOKEN $0 --version X.Y.Z
  unset GITHUB_TOKEN

or, preferably, download the release yourself with the GitHub CLI (which
handles auth for you) and pass --bundle-dir:

  gh release download "pi-vX.Y.Z" --repo khanasif1/jarvis-home-automation \\
    --pattern 'home-assistant-pi-bundle-*.tar.gz' --pattern 'SHA256SUMS' \\
    --dir /tmp/hap-release
  sudo $0 --version X.Y.Z --bundle-dir /tmp/hap-release
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      VERSION="${2:-}"
      shift 2
      ;;
    --repo)
      REPO="${2:-}"
      shift 2
      ;;
    --bundle-dir)
      BUNDLE_DIR="${2:-}"
      shift 2
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

[[ -n "${VERSION}" ]] || { usage; err "Missing required --version argument."; }
[[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z]+)*$ ]] \
  || err "Invalid --version '${VERSION}'. Expected a version such as 1.0.0."
if [[ -z "${BUNDLE_DIR}" ]]; then
  [[ -n "${REPO}" ]] || { usage; err "Missing required --repo argument (e.g. OWNER/REPOSITORY), or pass --bundle-dir."; }
  [[ "${REPO}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
    || err "Invalid --repo '${REPO}'. Expected OWNER/REPOSITORY."
fi
if [[ -n "${WAKEWORD_EXTRA}" ]]; then
  case "${WAKEWORD_EXTRA}" in
    none|porcupine|openwakeword) ;;
    *) err "Invalid --wakeword-extra '${WAKEWORD_EXTRA}'. Must be one of: none, porcupine, openwakeword." ;;
  esac
fi

if [[ "${EUID}" -ne 0 ]]; then
  err "This script must be run as root, e.g.: sudo $0 --version ${VERSION}"
fi

if [[ ! -x "${VENV_DIR}/bin/python3" ]]; then
  err "No existing installation found at ${INSTALL_DIR}. Run install.sh first."
fi
chown root:root "${INSTALL_DIR}"
chmod 0755 "${INSTALL_DIR}"

# detect_installed_wakeword_extra: best-effort probe of which optional
# wake-word engine dependency (if any) is importable in the currently
# installed venv, so that a plain `update.sh` run (no --wakeword-extra)
# carries forward whatever production engine install.sh previously set up,
# instead of silently reinstalling a bare venv that has fallen back to the
# non-functional-under-systemd "keyboard" engine.
detect_installed_wakeword_extra() {
  if "${VENV_DIR}/bin/python3" -c "import pvporcupine" >/dev/null 2>&1; then
    echo "porcupine"
  elif "${VENV_DIR}/bin/python3" -c "import openwakeword" >/dev/null 2>&1; then
    echo "openwakeword"
  else
    echo "none"
  fi
}

if [[ -z "${WAKEWORD_EXTRA}" ]]; then
  WAKEWORD_EXTRA="$(detect_installed_wakeword_extra)"
  if [[ "${WAKEWORD_EXTRA}" != "none" ]]; then
    info "Auto-detected currently installed wake-word extra '${WAKEWORD_EXTRA}'; carrying it forward. Pass --wakeword-extra to override."
  fi
fi

BUNDLE_NAME="home-assistant-pi-bundle-${VERSION}.tar.gz"

WORK_DIR="$(mktemp -d /tmp/home-assistant-pi-update.XXXXXX)"

# download_with_auth <url> <output-path>
#
# Feeds the Authorization header to curl as a config directive via stdin
# (`curl -K -`) rather than as a `-H "Authorization: ..."` command-line
# argument. `printf` here is a bash builtin, so building this string never
# execs a separate process -- the token is never visible in any process's
# argv (e.g. via `ps`/`/proc/*/cmdline`), and it is never written to disk.
download_with_auth() {
  local url="$1" out="$2"
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    printf 'header = "Authorization: Bearer %s"\n' "${GITHUB_TOKEN}" \
      | GITHUB_TOKEN= curl -fL -K - -o "${out}" "${url}"
  else
    curl -fL -o "${out}" "${url}"
  fi
}

if [[ -n "${BUNDLE_DIR}" ]]; then
  info "Using pre-downloaded bundle from ${BUNDLE_DIR}"
  [[ -f "${BUNDLE_DIR}/${BUNDLE_NAME}" ]] || err "${BUNDLE_DIR}/${BUNDLE_NAME} not found."
  [[ -f "${BUNDLE_DIR}/SHA256SUMS" ]] || err "${BUNDLE_DIR}/SHA256SUMS not found."
  cp "${BUNDLE_DIR}/${BUNDLE_NAME}" "${WORK_DIR}/${BUNDLE_NAME}"
  cp "${BUNDLE_DIR}/SHA256SUMS" "${WORK_DIR}/SHA256SUMS"
else
  RELEASE_URL="https://github.com/${REPO}/releases/download/pi-v${VERSION}"
  info "Downloading ${BUNDLE_NAME} from ${RELEASE_URL}"

  # 1. Download only the requested release bundle (never git, never a full
  # repository ZIP, never azure-backend/infra).
  if ! download_with_auth "${RELEASE_URL}/${BUNDLE_NAME}" "${WORK_DIR}/${BUNDLE_NAME}"; then
    err "Failed to download ${BUNDLE_NAME}. Aborting update (no changes made). For private repositories, set GITHUB_TOKEN or use 'gh release download' with --bundle-dir instead."
  fi
  if ! download_with_auth "${RELEASE_URL}/SHA256SUMS" "${WORK_DIR}/SHA256SUMS"; then
    err "Failed to download SHA256SUMS. Aborting update (no changes made)."
  fi
fi

# 2. Verify the SHA-256 checksum before extracting or installing anything.
info "Verifying checksum"
(
  cd "${WORK_DIR}"
  grep -F "${BUNDLE_NAME}" SHA256SUMS > "SHA256SUMS.filtered" || err "Bundle not listed in SHA256SUMS"
  sha256sum --check "SHA256SUMS.filtered" || err "Checksum verification failed for ${BUNDLE_NAME}. Aborting."
)

info "Extracting bundle"
mkdir -p "${WORK_DIR}/bundle"
tar -xzf "${WORK_DIR}/${BUNDLE_NAME}" -C "${WORK_DIR}/bundle"

BUNDLE_VERSION_FILE="$(find "${WORK_DIR}/bundle" -maxdepth 2 -name 'VERSION' | head -n 1)"
[[ -n "${BUNDLE_VERSION_FILE}" ]] || err "No VERSION file found inside the downloaded bundle."
BUNDLE_VERSION="$(tr -d '\r\n' < "${BUNDLE_VERSION_FILE}")"
[[ "${BUNDLE_VERSION}" == "${VERSION}" ]] \
  || err "Requested version ${VERSION} does not match bundle version ${BUNDLE_VERSION}."

WHEEL_FILE="$(find "${WORK_DIR}/bundle" -maxdepth 2 -name '*.whl' | head -n 1)"
[[ -n "${WHEEL_FILE}" ]] || err "No wheel (*.whl) file found inside the downloaded bundle."

REQUIREMENTS_FILE="$(find "${WORK_DIR}/bundle" -maxdepth 2 -name 'requirements-runtime.txt' | head -n 1 || true)"
UNIT_SRC="$(find "${WORK_DIR}/bundle" -maxdepth 2 -name '*.service' | head -n 1 || true)"
UPDATE_SRC="$(find "${WORK_DIR}/bundle" -maxdepth 2 -name 'update.sh' | head -n 1 || true)"
UNINSTALL_SRC="$(find "${WORK_DIR}/bundle" -maxdepth 2 -name 'uninstall.sh' | head -n 1 || true)"

# service_is_active: true (0) if the unit is currently active.
service_is_active() {
  systemctl is-active --quiet "${APP_NAME}.service"
}

# 3. Stop the running service before touching anything on disk. Failures
# are surfaced explicitly rather than swallowed with `|| true`: if the
# service was already inactive that's fine, but if `systemctl stop` itself
# errors out (e.g. systemd is unreachable) we must not proceed to modify a
# service we can't control.
info "Stopping ${APP_NAME} service"
if service_is_active; then
  if ! systemctl stop "${APP_NAME}.service"; then
    err "Failed to stop ${APP_NAME}.service; aborting update before making any changes."
  fi
  if service_is_active; then
    err "${APP_NAME}.service is still active after 'systemctl stop'; aborting update before making any changes."
  fi
else
  info "${APP_NAME}.service was already stopped."
fi

# 4. Configuration (device id/token/timezone/audio/wake-word settings) all
# live in CONFIG_FILE, which this script never writes to, so it is
# automatically preserved across the update.
[[ -f "${CONFIG_FILE}" ]] || info "WARNING: no existing ${CONFIG_FILE} found to preserve."

# 5. Rollback copy: keep only ONE previous version (venv, VERSION file, and
# systemd unit file all roll back together).
if [[ -d "${VENV_DIR}" ]]; then
  info "Saving rollback copy of the current installation"
  rm -rf "${VENV_ROLLBACK_DIR}"
  cp -a "${VENV_DIR}" "${VENV_ROLLBACK_DIR}"
  if [[ -f "${VERSION_FILE}" ]]; then
    cp -a "${VERSION_FILE}" "${VERSION_ROLLBACK_FILE}"
  fi
fi
# Preserve whatever systemd unit is currently installed *before* it is
# potentially overwritten below, so a rollback can restore the exact unit
# that was running before this update (not just the new one).
rm -f "${SYSTEMD_UNIT_ROLLBACK_PATH}"
if [[ -f "${SYSTEMD_UNIT_PATH}" ]]; then
  cp -a "${SYSTEMD_UNIT_PATH}" "${SYSTEMD_UNIT_ROLLBACK_PATH}"
fi

# 6. Install the new wheel into a fresh venv with --no-cache-dir, so a
# partially-failed install never corrupts the currently-running venv.
rm -rf "${VENV_NEW_DIR}"
info "Building new virtual environment"
python3 -m venv "${VENV_NEW_DIR}" || err "Failed to create new virtual environment"
"${VENV_NEW_DIR}/bin/pip" install --no-cache-dir --upgrade pip || err "Failed to upgrade pip in new venv"

if [[ "${WAKEWORD_EXTRA}" != "none" ]]; then
  WHEEL_INSTALL_TARGET="${WHEEL_FILE}[${WAKEWORD_EXTRA}]"
  info "Installing new wheel with the '${WAKEWORD_EXTRA}' wake-word extra"
else
  WHEEL_INSTALL_TARGET="${WHEEL_FILE}"
fi

PIP_INSTALL_ARGS=("${WHEEL_INSTALL_TARGET}")
if [[ -n "${REQUIREMENTS_FILE}" ]]; then
  PIP_INSTALL_ARGS=("-r" "${REQUIREMENTS_FILE}" "${WHEEL_INSTALL_TARGET}")
fi
if ! "${VENV_NEW_DIR}/bin/pip" install --no-cache-dir "${PIP_INSTALL_ARGS[@]}"; then
  info "Install failed; removing incomplete new environment and restarting the current installation."
  rm -rf "${VENV_NEW_DIR}"
  if ! systemctl start "${APP_NAME}.service"; then
    err "Failed to install the new version AND failed to restart the previous version. Manual intervention required: check 'systemctl status ${APP_NAME}.service'."
  fi
  err "Failed to install the new version. The previous version was left in place and restarted."
fi

# Swap the new venv into place.
rm -rf "${VENV_DIR}"
mv "${VENV_NEW_DIR}" "${VENV_DIR}"
chown -R root:root "${VENV_DIR}"
echo "${VERSION}" > "${VERSION_FILE}"
chown root:root "${VERSION_FILE}"
chmod 0644 "${VERSION_FILE}"

if [[ -n "${UNIT_SRC}" ]]; then
  cp "${UNIT_SRC}" "${SYSTEMD_UNIT_PATH}"
  chmod 0644 "${SYSTEMD_UNIT_PATH}"
  systemctl daemon-reload
fi

# 7. Restart the service on the new version. A failed `systemctl start`
# invocation is reported (not silently swallowed); either way we still
# proceed to the is-active check below, which is the authoritative signal.
info "Starting ${APP_NAME} service on version ${VERSION}"
if ! systemctl start "${APP_NAME}.service"; then
  info "WARNING: 'systemctl start' reported failure; checking actual service state before deciding to roll back."
fi
sleep "${SERVICE_START_WAIT_SECONDS}"

# 8/9. Verify the new version stayed running; roll back automatically if not.
if service_is_active; then
  # Refresh the stable maintenance tools only after the new service has
  # remained active, so a failed release cannot replace the known-good
  # updater/uninstaller.
  mkdir -p "${INSTALL_BIN_DIR}"
  if [[ -n "${UPDATE_SRC}" ]]; then
    install -o root -g root -m 0755 "${UPDATE_SRC}" "${INSTALL_BIN_DIR}/update.sh"
  fi
  if [[ -n "${UNINSTALL_SRC}" ]]; then
    install -o root -g root -m 0755 "${UNINSTALL_SRC}" "${INSTALL_BIN_DIR}/uninstall.sh"
  fi
  info "Update to ${VERSION} succeeded and the service is active."
  # Only one rollback version is retained; the previous rollback (two
  # versions back) has already been discarded above.
  exit 0
fi

info "New version failed to start; rolling back to the previous version."
systemctl stop "${APP_NAME}.service" 2>/dev/null || true
rm -rf "${VENV_DIR}"
if [[ -d "${VENV_ROLLBACK_DIR}" ]]; then
  mv "${VENV_ROLLBACK_DIR}" "${VENV_DIR}"
  chown -R root:root "${VENV_DIR}"
  if [[ -f "${VERSION_ROLLBACK_FILE}" ]]; then
    mv "${VERSION_ROLLBACK_FILE}" "${VERSION_FILE}"
  fi
  if [[ -f "${SYSTEMD_UNIT_ROLLBACK_PATH}" ]]; then
    mv "${SYSTEMD_UNIT_ROLLBACK_PATH}" "${SYSTEMD_UNIT_PATH}"
    systemctl daemon-reload
  fi
  if ! systemctl start "${APP_NAME}.service"; then
    err "Update to ${VERSION} failed to start, AND the rollback service failed to start ('systemctl start' returned non-zero). Manual intervention required: check 'systemctl status ${APP_NAME}.service'."
  fi
  sleep "${SERVICE_START_WAIT_SECONDS}"
  if ! service_is_active; then
    err "Update to ${VERSION} failed to start, AND the rolled-back version is not active after restart. Manual intervention required: check 'systemctl status ${APP_NAME}.service'."
  fi
  err "Update to ${VERSION} failed to start; automatically rolled back to the previous version, which is confirmed active."
else
  err "Update to ${VERSION} failed to start, and no rollback copy was available. Manual intervention required."
fi
