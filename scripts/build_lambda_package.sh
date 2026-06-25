#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/build/lambda"
DIST_DIR="${ROOT_DIR}/dist"
ZIP_PATH="${DIST_DIR}/license-agent-api.zip"

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}" "${DIST_DIR}"
rm -f "${ZIP_PATH}"

python3 -m pip install --upgrade \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --abi cp312 \
  --only-binary=:all: \
  -r "${ROOT_DIR}/requirements-api.txt" \
  -t "${BUILD_DIR}" >&2
cp -R "${ROOT_DIR}/src/license_agent" "${BUILD_DIR}/license_agent"

cd "${BUILD_DIR}"
zip -qr "${ZIP_PATH}" .

echo "${ZIP_PATH}"
