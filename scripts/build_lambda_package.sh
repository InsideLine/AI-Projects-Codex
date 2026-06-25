#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/build/lambda"
DIST_DIR="${ROOT_DIR}/dist"
ZIP_PATH="${DIST_DIR}/license-agent-api.zip"

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}" "${DIST_DIR}"

python3 -m pip install --upgrade -r "${ROOT_DIR}/requirements-api.txt" -t "${BUILD_DIR}"
cp -R "${ROOT_DIR}/src/license_agent" "${BUILD_DIR}/license_agent"

cd "${BUILD_DIR}"
zip -qr "${ZIP_PATH}" .

echo "${ZIP_PATH}"
