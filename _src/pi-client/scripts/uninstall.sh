#!/usr/bin/env bash
# uninstall.sh - Remove the home-assistant-pi voice assistant client.
#
# Usage:
#   sudo ./uninstall.sh            # keep configuration for a future reinstall
#   sudo ./uninstall.sh --purge    # also remove configuration and the
#                                  # dedicated system user/group
#
# Safe to run more than once; every step tolerates the corresponding
# resource already being absent.
set -euo pipefail

APP_NAME="home-assistant-pi"
SERVICE_USER="homeassistant"
SERVICE_GROUP="homeassistant"
INSTALL_DIR="/opt/home-assistant-pi"
CONFIG_DIR="/etc/home-assistant-pi"
SYSTEMD_UNIT_PATH="/etc/systemd/system/${APP_NAME}.service"

PURGE=false

err() {
  echo "ERROR: $*" >&2
  exit 1
}

info() {
  echo "[uninstall] $*"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge)
      PURGE=true
      shift
      ;;
    -h|--help)
      echo "Usage: sudo $0 [--purge]"
      exit 0
      ;;
    *)
      err "Unknown argument: $1"
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  err "This script must be run as root, e.g.: sudo $0"
fi

info "Stopping and disabling the ${APP_NAME} service (if present)"
systemctl stop "${APP_NAME}.service" 2>/dev/null || true
systemctl disable "${APP_NAME}.service" 2>/dev/null || true

if [[ -f "${SYSTEMD_UNIT_PATH}" ]]; then
  rm -f "${SYSTEMD_UNIT_PATH}"
  systemctl daemon-reload || true
  info "Removed systemd unit ${SYSTEMD_UNIT_PATH}"
fi

if [[ -d "${INSTALL_DIR}" ]]; then
  rm -rf "${INSTALL_DIR}"
  info "Removed application directory ${INSTALL_DIR}"
else
  info "Application directory ${INSTALL_DIR} not present; nothing to remove"
fi

if [[ "${PURGE}" == "true" ]]; then
  if [[ -d "${CONFIG_DIR}" ]]; then
    rm -rf "${CONFIG_DIR}"
    info "Removed configuration directory ${CONFIG_DIR} (--purge)"
  fi
  if id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    if userdel "${SERVICE_USER}"; then
      info "Removed system user ${SERVICE_USER} (--purge)"
    else
      err "Failed to remove system user ${SERVICE_USER}"
    fi
  fi
  if getent group "${SERVICE_GROUP}" >/dev/null 2>&1; then
    if groupdel "${SERVICE_GROUP}"; then
      info "Removed system group ${SERVICE_GROUP} (--purge)"
    else
      err "Failed to remove system group ${SERVICE_GROUP}; it may still be in use"
    fi
  fi
else
  info "Configuration directory ${CONFIG_DIR} was preserved. Re-run with --purge to remove it."
fi

info "Uninstall complete."
