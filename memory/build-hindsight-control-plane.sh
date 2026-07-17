#!/usr/bin/env bash
set -euo pipefail

readonly HINDSIGHT_REVISION="92f433c90409636804c0797071a4abbe141f76c5"
readonly DEFAULT_IMAGE="telefire-hindsight-control-plane:0.8.4-bank-name.1"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly PATCH_FILE="${SCRIPT_DIR}/patches/bee6f5d1-bank-name-search.patch"

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/hindsight" >&2
  exit 2
fi

source_dir="$(realpath -- "$1")"
image="${HINDSIGHT_CONTROL_PLANE_IMAGE:-${DEFAULT_IMAGE}}"

if ! git -C "${source_dir}" cat-file -e "${HINDSIGHT_REVISION}^{commit}" 2>/dev/null; then
  echo "Hindsight revision ${HINDSIGHT_REVISION} is unavailable in ${source_dir}" >&2
  exit 1
fi

build_dir="$(mktemp -d "${TMPDIR:-/tmp}/telefire-hindsight.XXXXXX")"
trap 'rm -rf -- "${build_dir}"' EXIT

git -C "${source_dir}" archive "${HINDSIGHT_REVISION}" | tar -x -C "${build_dir}"
patch --batch --forward -d "${build_dir}" -p1 < "${PATCH_FILE}"

docker build \
  --file "${build_dir}/docker/standalone/Dockerfile" \
  --target cp-only \
  --build-arg INCLUDE_API=false \
  --build-arg INCLUDE_CP=true \
  --build-arg INCLUDE_LOCAL_MODELS=false \
  --build-arg PRELOAD_ML_MODELS=false \
  --label "org.opencontainers.image.source=https://github.com/vectorize-io/hindsight" \
  --label "org.opencontainers.image.revision=${HINDSIGHT_REVISION}" \
  --label "org.opencontainers.image.version=0.8.4-bank-name.1" \
  --label "io.telefire.hindsight.control-plane-patch=bee6f5d114c09a7bf51a2ef1d5357e0bdf0d9c2d" \
  --tag "${image}" \
  "${build_dir}"
