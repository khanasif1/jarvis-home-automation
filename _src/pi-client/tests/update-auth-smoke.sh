#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
UPDATE_SCRIPT="${UPDATE_SCRIPT:-$SCRIPT_DIR/../scripts/update.sh}"
ARTIFACT_DIR="${TEST_ARTIFACT_DIR:-$SOURCE_ROOT/.test-artifacts/update-auth-smoke}"
CONFIG_CAPTURE="$ARTIFACT_DIR/curl-config"
ARGS_CAPTURE="$ARTIFACT_DIR/curl-args"
ENV_CAPTURE="$ARTIFACT_DIR/curl-token-env"
TEST_TOKEN="local-sentinel-token-123456789"

mkdir -p "$ARTIFACT_DIR"
trap 'rm -f "$CONFIG_CAPTURE" "$ARGS_CAPTURE" "$ENV_CAPTURE"' EXIT

eval "$(awk '/^download_with_auth\(\)/,/^}/ { print }' "$UPDATE_SCRIPT")"

curl() {
  printf '%s\n' "${GITHUB_TOKEN-}" > "$ENV_CAPTURE"
  printf '%s\n' "$@" > "$ARGS_CAPTURE"
  cat > "$CONFIG_CAPTURE"
}

GITHUB_TOKEN="$TEST_TOKEN"
download_with_auth "https://example.invalid/release" "$ARTIFACT_DIR/release"

grep -Fq "header = \"Authorization: Bearer $TEST_TOKEN\"" "$CONFIG_CAPTURE"
! grep -Fq '******' "$CONFIG_CAPTURE"
! grep -Fq "$TEST_TOKEN" "$ARGS_CAPTURE"
[[ -z "$(cat "$ENV_CAPTURE")" ]]
