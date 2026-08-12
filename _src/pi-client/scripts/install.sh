#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_NAME="home-assistant-pi"
readonly DEFAULT_VERSION="2.0.2"
readonly INSTALL_ROOT="/opt/${APP_NAME}"
readonly CONFIG_DIR="/etc/${APP_NAME}"
readonly CONFIG_FILE="${CONFIG_DIR}/config.env"
readonly RUNTIME_USER_FILE="${CONFIG_DIR}/runtime-user"
readonly LINGER_MARKER_FILE="${CONFIG_DIR}/runtime-linger-managed"
readonly SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
readonly SERVICE_GROUP="homeassistantpi"
readonly SERVICE_CLI="/usr/local/bin/home-assistant-pi-service"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="${DEFAULT_VERSION}"
API_URL=""
DEVICE_GUID=""
RUNTIME_USER=""

usage() {
  cat <<'EOF'
Usage: sudo ./install.sh [options]

Required on first install:
  --api-url URL          Azure Function API base URL ending in /api
  --device-guid UUID     Fixed canonical lowercase device UUID

Options:
  --version VERSION      Release version to install (default: 2.0.2)
  --runtime-user USER    Desktop user whose PipeWire audio session Jarvis uses
  --help                 Show this help

Re-running the command is safe. Existing API URL and device GUID are retained
when the corresponding option is omitted. The runtime user defaults to the user
that invoked sudo and is retained for later updates.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:?--version requires a value}"; shift 2 ;;
    --api-url) API_URL="${2:?--api-url requires a value}"; shift 2 ;;
    --device-guid) DEVICE_GUID="${2:?--device-guid requires a value}"; shift 2 ;;
    --runtime-user) RUNTIME_USER="${2:?--runtime-user requires a value}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || { echo "Run this installer with sudo." >&2; exit 1; }
[[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "--version must be a semantic version such as 2.0.0." >&2
  exit 2
}
[[ -f "${SCRIPT_DIR}/VERSION" ]] || {
  echo "Missing VERSION; run install.sh from the extracted release bundle." >&2
  exit 1
}
BUNDLE_VERSION="$(tr -d '[:space:]' <"${SCRIPT_DIR}/VERSION")"
[[ "${BUNDLE_VERSION}" == "${VERSION}" ]] || {
  echo "Bundle version ${BUNDLE_VERSION} does not match --version ${VERSION}." >&2
  exit 1
}
WHEEL_FILES=("${SCRIPT_DIR}"/home_assistant_pi-"${VERSION}"-*.whl)
[[ "${#WHEEL_FILES[@]}" -eq 1 && -f "${WHEEL_FILES[0]}" ]] || {
  echo "The release bundle must contain exactly one ${VERSION} wheel." >&2
  exit 1
}
WHEEL_FILE="${WHEEL_FILES[0]}"
[[ -f "${SCRIPT_DIR}/release-manifest.json" ]] || {
  echo "Missing release-manifest.json in the extracted release bundle." >&2
  exit 1
}
EXPECTED_WHEEL_HASH="$(
  sed -n 's/.*"wheelSha256"[[:space:]]*:[[:space:]]*"\([0-9a-fA-F]\{64\}\)".*/\1/p' \
    "${SCRIPT_DIR}/release-manifest.json"
)"
[[ -n "${EXPECTED_WHEEL_HASH}" ]] || {
  echo "release-manifest.json does not contain a valid wheel checksum." >&2
  exit 1
}
echo "${EXPECTED_WHEEL_HASH}  ${WHEEL_FILE}" | sha256sum --check --status || {
  echo "The bundled wheel failed its SHA-256 integrity check." >&2
  exit 1
}

ARCH="$(dpkg --print-architecture 2>/dev/null || true)"
[[ "${ARCH}" == "arm64" ]] || {
  echo "This release requires 64-bit Raspberry Pi OS (arm64); detected '${ARCH:-unknown}'." >&2
  exit 1
}

PREVIOUS_RUNTIME_USER=""
if [[ -f "${RUNTIME_USER_FILE}" ]]; then
  PREVIOUS_RUNTIME_USER="$(tr -d '[:space:]' <"${RUNTIME_USER_FILE}")"
elif [[ -f "${SERVICE_FILE}" ]]; then
  PREVIOUS_RUNTIME_USER="$(sed -n 's/^User=//p' "${SERVICE_FILE}" | tail -n 1)"
fi

if [[ -z "${RUNTIME_USER}" && -f "${RUNTIME_USER_FILE}" ]]; then
  RUNTIME_USER="${PREVIOUS_RUNTIME_USER}"
fi
if [[ -z "${RUNTIME_USER}" && -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
  RUNTIME_USER="${SUDO_USER}"
fi
[[ "${RUNTIME_USER}" =~ ^[a-z_][a-z0-9_-]*$ ]] || {
  echo "Could not determine a safe desktop audio user. Rerun with --runtime-user USER." >&2
  exit 2
}
[[ "${RUNTIME_USER}" != "root" ]] || {
  echo "--runtime-user must be a non-root desktop user." >&2
  exit 2
}
id "${RUNTIME_USER}" >/dev/null 2>&1 || {
  echo "Runtime user '${RUNTIME_USER}' does not exist." >&2
  exit 2
}
RUNTIME_UID="$(id -u "${RUNTIME_USER}")"
RUNTIME_HOME="$(getent passwd "${RUNTIME_USER}" | cut -d: -f6)"
[[ "${RUNTIME_UID}" =~ ^[0-9]+$ && "${RUNTIME_HOME}" =~ ^/[A-Za-z0-9._/-]+$ && -d "${RUNTIME_HOME}" ]] || {
  echo "Runtime user '${RUNTIME_USER}' does not have a valid home directory." >&2
  exit 2
}

read_config_value() {
  local key="$1"
  [[ -f "${CONFIG_FILE}" ]] || return 0
  sed -n "s/^${key}=//p" "${CONFIG_FILE}" | tail -n 1
}

API_URL="${API_URL:-$(read_config_value HAP_API_BASE_URL)}"
DEVICE_GUID="${DEVICE_GUID:-$(read_config_value HAP_DEVICE_GUID)}"

[[ "${API_URL}" =~ ^https://[^[:space:]]+/api$ ]] || {
  echo "--api-url must be an HTTPS URL ending in /api." >&2
  exit 2
}
[[ "${DEVICE_GUID}" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$ ]] || {
  echo "--device-guid must be a canonical lowercase UUID." >&2
  exit 2
}

INPUT_DEVICE="$(read_config_value HAP_INPUT_DEVICE)"
OUTPUT_DEVICE="$(read_config_value HAP_OUTPUT_DEVICE)"
WAKEWORD_THRESHOLD="$(read_config_value HAP_WAKEWORD_THRESHOLD)"
VAD_MODE="$(read_config_value HAP_VAD_MODE)"
NO_SPEECH_TIMEOUT_SECONDS="$(read_config_value HAP_NO_SPEECH_TIMEOUT_SECONDS)"
SILENCE_TIMEOUT_SECONDS="$(read_config_value HAP_SILENCE_TIMEOUT_SECONDS)"
MAX_COMMAND_SECONDS="$(read_config_value HAP_MAX_COMMAND_SECONDS)"
PLAYBACK_COOLDOWN_SECONDS="$(read_config_value HAP_PLAYBACK_COOLDOWN_SECONDS)"
LOG_LEVEL="$(read_config_value HAP_LOG_LEVEL)"

if [[ -n "${PREVIOUS_RUNTIME_USER}" && "${PREVIOUS_RUNTIME_USER}" != "${RUNTIME_USER}" ]]; then
  echo "Migrating audio runtime from '${PREVIOUS_RUNTIME_USER}' to '${RUNTIME_USER}'; clearing stale device indexes."
  INPUT_DEVICE=""
  OUTPUT_DEVICE=""
fi

WAKEWORD_THRESHOLD="${WAKEWORD_THRESHOLD:-0.5}"
VAD_MODE="${VAD_MODE:-2}"
NO_SPEECH_TIMEOUT_SECONDS="${NO_SPEECH_TIMEOUT_SECONDS:-3.0}"
SILENCE_TIMEOUT_SECONDS="${SILENCE_TIMEOUT_SECONDS:-1.2}"
MAX_COMMAND_SECONDS="${MAX_COMMAND_SECONDS:-30.0}"
PLAYBACK_COOLDOWN_SECONDS="${PLAYBACK_COOLDOWN_SECONDS:-0.75}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates \
  libasound2-plugins \
  libportaudio2 \
  libopenblas0-pthread \
  python3 \
  python3-pip \
  python3-venv

python3 - <<'PY'
import sys

if sys.version_info[:2] != (3, 11):
    raise SystemExit(
        f"Python 3.11 is required; detected {sys.version_info.major}."
        f"{sys.version_info.minor}. Use 64-bit Raspberry Pi OS Bookworm."
    )
PY

RELEASE_DIR="${INSTALL_ROOT}/releases/${VERSION}"
CURRENT_LINK="${INSTALL_ROOT}/current"
[[ ! -e "${CURRENT_LINK}" || -L "${CURRENT_LINK}" ]] || {
  echo "${CURRENT_LINK} exists but is not a symbolic link." >&2
  exit 1
}
[[ ! -e "${RELEASE_DIR}" || -d "${RELEASE_DIR}" ]] || {
  echo "${RELEASE_DIR} exists but is not a directory." >&2
  exit 1
}
install -d -m 0755 "${INSTALL_ROOT}/releases"
ROLLBACK_DIR="${INSTALL_ROOT}/.install-rollback-${VERSION}-$$"
install -d -m 0700 "${ROLLBACK_DIR}"

SERVICE_WAS_ACTIVE=false
SERVICE_WAS_ENABLED=false
HAD_RELEASE_DIR=false
HAD_SERVICE_FILE=false
HAD_CONFIG_FILE=false
HAD_RUNTIME_USER_FILE=false
HAD_LINGER_MARKER_FILE=false
HAD_SERVICE_CLI=false
SERVICE_GROUP_EXISTED=false
LINGER_WAS_ENABLED=false
LINGER_MANAGED_BY_INSTALLER=false
PREVIOUS_MANAGED_LINGER_USER=""
PREVIOUS_MANAGED_LINGER_DISABLED=false
PREVIOUS_CURRENT_LINK=""
FILES_MUTATED=false

systemctl is-active --quiet "${APP_NAME}.service" && SERVICE_WAS_ACTIVE=true
systemctl is-enabled --quiet "${APP_NAME}.service" && SERVICE_WAS_ENABLED=true
getent group "${SERVICE_GROUP}" >/dev/null && SERVICE_GROUP_EXISTED=true
if [[ "$(loginctl show-user "${RUNTIME_USER}" --property=Linger --value 2>/dev/null || true)" == "yes" ]]; then
  LINGER_WAS_ENABLED=true
fi
if [[ -d "${RELEASE_DIR}" ]]; then
  HAD_RELEASE_DIR=true
fi
if [[ -f "${SERVICE_FILE}" ]]; then
  HAD_SERVICE_FILE=true
  cp -a "${SERVICE_FILE}" "${ROLLBACK_DIR}/service"
fi
if [[ -f "${CONFIG_FILE}" ]]; then
  HAD_CONFIG_FILE=true
  cp -a "${CONFIG_FILE}" "${ROLLBACK_DIR}/config"
fi
if [[ -f "${RUNTIME_USER_FILE}" ]]; then
  HAD_RUNTIME_USER_FILE=true
  cp -a "${RUNTIME_USER_FILE}" "${ROLLBACK_DIR}/runtime-user"
fi
if [[ -f "${LINGER_MARKER_FILE}" ]]; then
  HAD_LINGER_MARKER_FILE=true
  cp -a "${LINGER_MARKER_FILE}" "${ROLLBACK_DIR}/runtime-linger-managed"
  MANAGED_LINGER_CANDIDATE="$(tr -d '[:space:]' <"${LINGER_MARKER_FILE}")"
  if [[ "${MANAGED_LINGER_CANDIDATE}" =~ ^[a-z_][a-z0-9_-]*$ &&
    "${MANAGED_LINGER_CANDIDATE}" != "root" ]]; then
    PREVIOUS_MANAGED_LINGER_USER="${MANAGED_LINGER_CANDIDATE}"
  fi
fi
if [[ -f "${SERVICE_CLI}" ]]; then
  HAD_SERVICE_CLI=true
  cp -a "${SERVICE_CLI}" "${ROLLBACK_DIR}/service-cli"
fi
if [[ -L "${CURRENT_LINK}" ]]; then
  PREVIOUS_CURRENT_LINK="$(readlink "${CURRENT_LINK}")"
fi

rollback_install() {
  local status=$?
  trap - EXIT
  set +e
  if [[ "${status}" -ne 0 ]]; then
    if [[ "${FILES_MUTATED}" == "true" ]]; then
      echo "Installation failed; restoring the previous ${APP_NAME} release." >&2
      systemctl stop "${APP_NAME}.service"
      if [[ "${HAD_RELEASE_DIR}" == "false" ]]; then
        rm -rf -- "${RELEASE_DIR}"
      elif [[ -d "${ROLLBACK_DIR}/release" ]]; then
        rm -rf -- "${RELEASE_DIR}"
        mv "${ROLLBACK_DIR}/release" "${RELEASE_DIR}"
      fi
      rm -f -- "${CURRENT_LINK}"
      if [[ -n "${PREVIOUS_CURRENT_LINK}" ]]; then
        ln -s "${PREVIOUS_CURRENT_LINK}" "${CURRENT_LINK}"
      fi
      if [[ "${HAD_SERVICE_FILE}" == "true" ]]; then
        cp -a "${ROLLBACK_DIR}/service" "${SERVICE_FILE}"
      else
        rm -f -- "${SERVICE_FILE}"
      fi
      if [[ "${HAD_CONFIG_FILE}" == "true" ]]; then
        cp -a "${ROLLBACK_DIR}/config" "${CONFIG_FILE}"
      else
        rm -f -- "${CONFIG_FILE}"
      fi
      if [[ "${HAD_RUNTIME_USER_FILE}" == "true" ]]; then
        cp -a "${ROLLBACK_DIR}/runtime-user" "${RUNTIME_USER_FILE}"
      else
        rm -f -- "${RUNTIME_USER_FILE}"
      fi
      if [[ "${HAD_LINGER_MARKER_FILE}" == "true" ]]; then
        cp -a "${ROLLBACK_DIR}/runtime-linger-managed" "${LINGER_MARKER_FILE}"
      else
        rm -f -- "${LINGER_MARKER_FILE}"
      fi
      if [[ "${HAD_SERVICE_CLI}" == "true" ]]; then
        cp -a "${ROLLBACK_DIR}/service-cli" "${SERVICE_CLI}"
      else
        rm -f -- "${SERVICE_CLI}"
      fi
      systemctl daemon-reload
      if [[ "${SERVICE_WAS_ENABLED}" == "true" ]]; then
        systemctl enable "${APP_NAME}.service"
      else
        systemctl disable "${APP_NAME}.service"
      fi
      if [[ "${SERVICE_WAS_ACTIVE}" == "true" ]]; then
        systemctl start "${APP_NAME}.service"
      fi
    fi

    if [[ "${LINGER_WAS_ENABLED}" == "false" &&
      "$(loginctl show-user "${RUNTIME_USER}" --property=Linger --value 2>/dev/null)" == "yes" ]]; then
      loginctl disable-linger "${RUNTIME_USER}"
    fi
    if [[ "${PREVIOUS_MANAGED_LINGER_DISABLED}" == "true" ]]; then
      loginctl enable-linger "${PREVIOUS_MANAGED_LINGER_USER}"
    fi
    if [[ "${SERVICE_GROUP_EXISTED}" == "false" ]] &&
      getent group "${SERVICE_GROUP}" >/dev/null; then
      groupdel "${SERVICE_GROUP}"
    fi
  fi
  rm -rf -- "${ROLLBACK_DIR}"
  exit "${status}"
}
trap rollback_install EXIT

if [[ "${SERVICE_GROUP_EXISTED}" == "false" ]]; then
  groupadd --system "${SERVICE_GROUP}"
fi
if [[ "${LINGER_WAS_ENABLED}" == "false" ]]; then
  loginctl enable-linger "${RUNTIME_USER}"
  LINGER_MANAGED_BY_INSTALLER=true
elif [[ "${PREVIOUS_MANAGED_LINGER_USER}" == "${RUNTIME_USER}" ]]; then
  LINGER_MANAGED_BY_INSTALLER=true
fi
systemctl start "user@${RUNTIME_UID}.service"
for _ in {1..20}; do
  [[ -d "/run/user/${RUNTIME_UID}" ]] && break
  sleep 1
done
[[ -d "/run/user/${RUNTIME_UID}" ]] || {
  echo "User runtime directory /run/user/${RUNTIME_UID} was not created." >&2
  exit 1
}
USER_SYSTEMCTL=(
  runuser --user "${RUNTIME_USER}" -- env
  "HOME=${RUNTIME_HOME}"
  "XDG_RUNTIME_DIR=/run/user/${RUNTIME_UID}"
  "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${RUNTIME_UID}/bus"
  systemctl --user
)
USER_MANAGER_READY=false
for _ in {1..20}; do
  if "${USER_SYSTEMCTL[@]}" show-environment >/dev/null 2>&1; then
    USER_MANAGER_READY=true
    break
  fi
  sleep 1
done
[[ "${USER_MANAGER_READY}" == "true" ]] || {
  echo "The systemd user manager for '${RUNTIME_USER}' did not become ready." >&2
  exit 1
}
for audio_unit in pipewire.socket pipewire-pulse.socket wireplumber.service; do
  if "${USER_SYSTEMCTL[@]}" cat "${audio_unit}" >/dev/null 2>&1; then
    "${USER_SYSTEMCTL[@]}" start "${audio_unit}"
  fi
done

FILES_MUTATED=true
if [[ "${SERVICE_WAS_ACTIVE}" == "true" || "${SERVICE_WAS_ENABLED}" == "true" ]]; then
  echo "Stopping the existing ${APP_NAME} service during update."
  systemctl stop "${APP_NAME}.service"
fi
if [[ "${HAD_RELEASE_DIR}" == "true" ]]; then
  mv "${RELEASE_DIR}" "${ROLLBACK_DIR}/release"
fi
install -d -m 0755 "${RELEASE_DIR}"

python3 -m venv --copies "${RELEASE_DIR}/.venv"
"${RELEASE_DIR}/.venv/bin/python" -m pip install \
  --disable-pip-version-check \
  --no-cache-dir \
  --upgrade pip setuptools wheel
"${RELEASE_DIR}/.venv/bin/python" -m pip install \
  --disable-pip-version-check \
  --no-cache-dir \
  --requirement "${SCRIPT_DIR}/requirements-runtime.txt"
"${RELEASE_DIR}/.venv/bin/python" -m pip install \
  --disable-pip-version-check \
  --no-cache-dir \
  --no-deps \
  "openwakeword==0.6.0" \
  "${WHEEL_FILE}"

# Fetch and verify only the three TFLite files used at runtime.
"${RELEASE_DIR}/.venv/bin/python" - <<'PY'
import hashlib
import importlib.util
import urllib.request
from pathlib import Path

spec = importlib.util.find_spec("openwakeword")
if spec is None or not spec.submodule_search_locations:
    raise SystemExit("openWakeWord package location was not found")
target = Path(next(iter(spec.submodule_search_locations))) / "resources" / "models"
target.mkdir(parents=True, exist_ok=True)
base = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
models = [
    ("embedding_model.tflite", "c0aea21eb84a4ce90a08c870da41b7a7173b45269e6a3207c71d67c40f3a59d8"),
    ("melspectrogram.tflite", "96fa0adccb6e8cf95cb14465409a1a2898ee4a96a85bb9ed3c7eb0e68bf163e8"),
    ("hey_jarvis_v0.1.tflite", "14bff778604985e1b5c19f0f7bbe477a69cf281d8db34b232b3b972411f710e2"),
]
for name, expected in models:
    destination = target / name
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
        continue
    temporary = destination.with_suffix(".download")
    urllib.request.urlretrieve(f"{base}/{name}", temporary)
    actual = hashlib.sha256(temporary.read_bytes()).hexdigest()
    if actual != expected:
        temporary.unlink(missing_ok=True)
        raise SystemExit(f"SHA-256 verification failed for {name}")
    temporary.replace(destination)
PY

"${RELEASE_DIR}/.venv/bin/home-assistant-pi" version | grep -Fx "${VERSION}" >/dev/null
"${RELEASE_DIR}/.venv/bin/python" -c \
  "from home_assistant_pi.wakeword.openwakeword import validate_runtime; validate_runtime()"

install -d -m 0750 -o root -g "${SERVICE_GROUP}" "${CONFIG_DIR}"
umask 0027
cat >"${CONFIG_FILE}" <<EOF
HAP_API_BASE_URL=${API_URL}
HAP_DEVICE_GUID=${DEVICE_GUID}
HAP_INPUT_DEVICE=${INPUT_DEVICE}
HAP_OUTPUT_DEVICE=${OUTPUT_DEVICE}
HAP_WAKEWORD_THRESHOLD=${WAKEWORD_THRESHOLD}
HAP_VAD_MODE=${VAD_MODE}
HAP_NO_SPEECH_TIMEOUT_SECONDS=${NO_SPEECH_TIMEOUT_SECONDS}
HAP_SILENCE_TIMEOUT_SECONDS=${SILENCE_TIMEOUT_SECONDS}
HAP_MAX_COMMAND_SECONDS=${MAX_COMMAND_SECONDS}
HAP_PLAYBACK_COOLDOWN_SECONDS=${PLAYBACK_COOLDOWN_SECONDS}
HAP_LOG_LEVEL=${LOG_LEVEL}
EOF
chown root:"${SERVICE_GROUP}" "${CONFIG_FILE}"
chmod 0640 "${CONFIG_FILE}"
printf '%s\n' "${RUNTIME_USER}" >"${RUNTIME_USER_FILE}"
chown root:"${SERVICE_GROUP}" "${RUNTIME_USER_FILE}"
chmod 0640 "${RUNTIME_USER_FILE}"
if [[ "${LINGER_MANAGED_BY_INSTALLER}" == "true" ]]; then
  printf '%s\n' "${RUNTIME_USER}" >"${LINGER_MARKER_FILE}"
  chown root:"${SERVICE_GROUP}" "${LINGER_MARKER_FILE}"
  chmod 0640 "${LINGER_MARKER_FILE}"
else
  rm -f -- "${LINGER_MARKER_FILE}"
fi

ln -sfn "${RELEASE_DIR}" "${CURRENT_LINK}"
cat >"${SERVICE_CLI}" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
[[ "\${EUID}" -eq 0 ]] || {
  echo "Run this command with sudo." >&2
  exit 1
}
exec runuser \
  --user "${RUNTIME_USER}" \
  --group "${SERVICE_GROUP}" \
  --supp-group audio \
  -- env \
  HOME="${RUNTIME_HOME}" \
  XDG_RUNTIME_DIR="/run/user/${RUNTIME_UID}" \
  DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/${RUNTIME_UID}/bus" \
  PULSE_SERVER="unix:/run/user/${RUNTIME_UID}/pulse/native" \
  "${INSTALL_ROOT}/current/.venv/bin/home-assistant-pi" "\$@"
EOF
chmod 0755 "${SERVICE_CLI}"

cat >"${SERVICE_FILE}" <<EOF
[Unit]
Description=Jarvis wake-word voice assistant
After=network-online.target sound.target user@${RUNTIME_UID}.service
Wants=network-online.target user@${RUNTIME_UID}.service
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=simple
User=${RUNTIME_USER}
Group=${SERVICE_GROUP}
SupplementaryGroups=audio
Environment="HOME=${RUNTIME_HOME}"
Environment="XDG_RUNTIME_DIR=/run/user/${RUNTIME_UID}"
Environment="DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${RUNTIME_UID}/bus"
Environment="PULSE_SERVER=unix:/run/user/${RUNTIME_UID}/pulse/native"
EnvironmentFile=${CONFIG_FILE}
ExecStart=${INSTALL_ROOT}/current/.venv/bin/home-assistant-pi run
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=read-only
ProtectSystem=strict
MemoryMax=600M
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "${SERVICE_FILE}"

chown -R root:root "${RELEASE_DIR}"
systemctl daemon-reload
if ! "${SERVICE_CLI}" doctor; then
  echo "${APP_NAME} diagnostics failed in the configured desktop audio session." >&2
  exit 1
fi
systemctl enable "${APP_NAME}.service"
systemctl reset-failed "${APP_NAME}.service" || true
systemctl restart "${APP_NAME}.service"
sleep 10
RESTART_COUNT="$(systemctl show "${APP_NAME}.service" --property=NRestarts --value)"
if ! systemctl is-active --quiet "${APP_NAME}.service" || [[ "${RESTART_COUNT}" != "0" ]]; then
  journalctl --unit "${APP_NAME}.service" --lines 30 --no-pager >&2
  systemctl stop "${APP_NAME}.service"
  echo "${APP_NAME} did not remain stable after installation (restarts: ${RESTART_COUNT})." >&2
  exit 1
fi

if [[ -n "${PREVIOUS_MANAGED_LINGER_USER}" &&
  "${PREVIOUS_MANAGED_LINGER_USER}" != "${RUNTIME_USER}" ]] &&
  id "${PREVIOUS_MANAGED_LINGER_USER}" >/dev/null 2>&1; then
  PREVIOUS_MANAGED_LINGER_DISABLED=true
  loginctl disable-linger "${PREVIOUS_MANAGED_LINGER_USER}"
fi

echo "Installed ${APP_NAME} ${VERSION}."
echo "Desktop audio user: ${RUNTIME_USER}"
echo "Run: sudo systemctl status ${APP_NAME} --no-pager"
echo "Run: sudo ${SERVICE_CLI} doctor"
