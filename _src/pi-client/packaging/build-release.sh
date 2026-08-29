#!/usr/bin/env bash
set -Eeuo pipefail
export PYTHONDONTWRITEBYTECODE=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PI_CLIENT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE_ROOT="$(cd "${PI_CLIENT_DIR}/.." && pwd)"
OUTPUT_DIR="${SOURCE_ROOT}/.test-artifacts/pi-client-release"
EXPECTED_VERSION=""
PYTHON_BIN="${PYTHON_BIN:-python3}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) EXPECTED_VERSION="${2:?--version requires a value}"; shift 2 ;;
    --output-dir) OUTPUT_DIR="${2:?--output-dir requires a value}"; shift 2 ;;
    --help|-h)
      echo "Usage: $0 [--version X.Y.Z] [--output-dir DIR]"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

VERSION="$(
  "${PYTHON_BIN}" -c "import runpy; print(runpy.run_path('${PI_CLIENT_DIR}/src/home_assistant_pi/version.py')['__version__'])"
)"
[[ -z "${EXPECTED_VERSION}" || "${EXPECTED_VERSION}" == "${VERSION}" ]] || {
  echo "Requested version ${EXPECTED_VERSION} does not match ${VERSION}." >&2
  exit 1
}

echo "[build-release] Building home-assistant-pi ${VERSION}"
rm -rf -- "${OUTPUT_DIR}"
mkdir -p "${OUTPUT_DIR}/dist" "${OUTPUT_DIR}/bundle-staging" "${OUTPUT_DIR}/build-source"
cp "${PI_CLIENT_DIR}/pyproject.toml" "${PI_CLIENT_DIR}/README.md" "${OUTPUT_DIR}/build-source/"
cp -a "${PI_CLIENT_DIR}/src" "${OUTPUT_DIR}/build-source/"
"${PYTHON_BIN}" -m build --wheel --sdist --outdir "${OUTPUT_DIR}/dist" "${OUTPUT_DIR}/build-source"
rm -rf -- "${OUTPUT_DIR}/build-source"

WHEEL_FILE="$(find "${OUTPUT_DIR}/dist" -maxdepth 1 -name '*.whl' -print -quit)"
SDIST_FILE="$(find "${OUTPUT_DIR}/dist" -maxdepth 1 -name '*.tar.gz' -print -quit)"
[[ -f "${WHEEL_FILE}" && -f "${SDIST_FILE}" ]] || {
  echo "Build did not produce a wheel and sdist." >&2
  exit 1
}

STAGE="${OUTPUT_DIR}/bundle-staging"
cp "${WHEEL_FILE}" "${PI_CLIENT_DIR}/scripts/install.sh" \
  "${PI_CLIENT_DIR}/scripts/update.sh" "${PI_CLIENT_DIR}/scripts/uninstall.sh" "${STAGE}/"
cp "${PI_CLIENT_DIR}/.env.example" "${STAGE}/config.env.example"
cp "${PI_CLIENT_DIR}/requirements-runtime.txt" "${STAGE}/"
cp "${PI_CLIENT_DIR}/THIRD_PARTY_NOTICES.md" "${STAGE}/"
printf '%s\n' "${VERSION}" >"${STAGE}/VERSION"
WHEEL_NAME="$(basename "${WHEEL_FILE}")"
WHEEL_HASH="$(sha256sum "${WHEEL_FILE}" | cut -d ' ' -f 1)"
cat >"${STAGE}/release-manifest.json" <<EOF
{
  "name": "home-assistant-pi",
  "version": "${VERSION}",
  "wheel": "${WHEEL_NAME}",
  "wheelSha256": "${WHEEL_HASH}",
  "files": [
    "${WHEEL_NAME}",
    "install.sh",
    "update.sh",
    "uninstall.sh",
    "config.env.example",
    "requirements-runtime.txt",
    "THIRD_PARTY_NOTICES.md",
    "VERSION",
    "release-manifest.json"
  ]
}
EOF

BUNDLE="${OUTPUT_DIR}/dist/home-assistant-pi-bundle-${VERSION}.tar.gz"
"${PYTHON_BIN}" "${SCRIPT_DIR}/create-release-archive.py" "${STAGE}" "${BUNDLE}"
(
  cd "${OUTPUT_DIR}/dist"
  sha256sum "$(basename "${WHEEL_FILE}")" "$(basename "${SDIST_FILE}")" \
    "$(basename "${BUNDLE}")" >SHA256SUMS
)
rm -rf -- "${STAGE}"
echo "[build-release] Artifacts written to ${OUTPUT_DIR}/dist"
