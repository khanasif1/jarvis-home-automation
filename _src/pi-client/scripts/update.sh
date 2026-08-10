#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f /etc/home-assistant-pi/config.env ]]; then
  echo "No existing installation was found; use install.sh with --api-url and --device-guid." >&2
  exit 1
fi

exec "${SCRIPT_DIR}/install.sh" "$@"
