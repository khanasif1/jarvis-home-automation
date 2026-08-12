#!/usr/bin/env bash
set -Eeuo pipefail

readonly APP_NAME="home-assistant-pi"
readonly DEFAULT_VERSION="2.0.1"
readonly INSTALL_ROOT="/opt/${APP_NAME}"
readonly CONFIG_DIR="/etc/${APP_NAME}"
readonly CONFIG_FILE="${CONFIG_DIR}/config.env"
readonly SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
readonly SERVICE_USER="homeassistantpi"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="${DEFAULT_VERSION}"
API_URL=""
DEVICE_GUID=""

usage() {
  cat <<'EOF'
Usage: sudo ./install.sh [options]

Required on first install:
  --api-url URL          Azure Function API base URL ending in /api
  --device-guid UUID     Fixed canonical lowercase device UUID

Options:
  --version VERSION      Release version to install (default: 2.0.1)
  --help                 Show this help

Re-running the command is safe. Existing API URL and device GUID are retained
when the corresponding option is omitted.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:?--version requires a value}"; shift 2 ;;
    --api-url) API_URL="${2:?--api-url requires a value}"; shift 2 ;;
    --device-guid) DEVICE_GUID="${2:?--device-guid requires a value}"; shift 2 ;;
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

if ! getent group "${SERVICE_USER}" >/dev/null; then
  groupadd --system "${SERVICE_USER}"
fi
if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd \
    --system \
    --gid "${SERVICE_USER}" \
    --groups audio \
    --home-dir "${INSTALL_ROOT}" \
    --no-create-home \
    --shell /usr/sbin/nologin \
    "${SERVICE_USER}"
else
  usermod --append --groups audio "${SERVICE_USER}"
fi

RELEASE_DIR="${INSTALL_ROOT}/releases/${VERSION}"
rm -rf -- "${RELEASE_DIR}"
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

install -d -m 0750 -o root -g "${SERVICE_USER}" "${CONFIG_DIR}"
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
chown root:"${SERVICE_USER}" "${CONFIG_FILE}"
chmod 0640 "${CONFIG_FILE}"

ln -sfn "${RELEASE_DIR}" "${INSTALL_ROOT}/current"
cat >"${SERVICE_FILE}" <<EOF
[Unit]
Description=Jarvis wake-word voice assistant
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
SupplementaryGroups=audio
EnvironmentFile=${CONFIG_FILE}
ExecStart=${INSTALL_ROOT}/current/.venv/bin/home-assistant-pi run
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
MemoryMax=600M
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "${SERVICE_FILE}"

chown -R root:root "${RELEASE_DIR}"
systemctl daemon-reload
systemctl enable "${APP_NAME}.service"
systemctl reset-failed "${APP_NAME}.service" || true
systemctl restart "${APP_NAME}.service"
sleep 10
RESTART_COUNT="$(systemctl show "${APP_NAME}.service" --property=NRestarts --value)"
if ! systemctl is-active --quiet "${APP_NAME}.service" || [[ "${RESTART_COUNT}" != "0" ]]; then
  journalctl --unit "${APP_NAME}.service" --lines 30 --no-pager >&2
  echo "${APP_NAME} did not remain stable after installation (restarts: ${RESTART_COUNT})." >&2
  exit 1
fi

echo "Installed ${APP_NAME} ${VERSION}."
echo "Run: sudo systemctl status ${APP_NAME} --no-pager"
echo "Run: sudo ${INSTALL_ROOT}/current/.venv/bin/home-assistant-pi doctor"
