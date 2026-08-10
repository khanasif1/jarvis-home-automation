#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="home-assistant-pi"
PURGE_CONFIG=false

usage() {
  cat <<'EOF'
Usage: sudo ./uninstall.sh [--purge-config]

By default, the device GUID and API URL remain in
/etc/home-assistant-pi/config.env for a later reinstall. Use --purge-config
to remove them too.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge-config) PURGE_CONFIG=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[[ "${EUID}" -eq 0 ]] || { echo "Run this uninstaller with sudo." >&2; exit 1; }

SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
if [[ -f "${SERVICE_FILE}" ]]; then
  systemctl disable --now "${APP_NAME}.service"
fi
rm -f -- "${SERVICE_FILE}"
systemctl daemon-reload
rm -rf -- "/opt/${APP_NAME}"

if [[ "${PURGE_CONFIG}" == "true" ]]; then
  rm -rf -- "/etc/${APP_NAME}"
fi
if id homeassistantpi >/dev/null 2>&1; then
  userdel homeassistantpi
fi
if getent group homeassistantpi >/dev/null; then
  groupdel homeassistantpi
fi

echo "Uninstalled ${APP_NAME}."
if [[ "${PURGE_CONFIG}" != "true" ]]; then
  echo "Configuration retained in /etc/${APP_NAME}."
fi
