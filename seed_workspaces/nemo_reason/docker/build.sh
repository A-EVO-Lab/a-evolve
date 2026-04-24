#!/usr/bin/env bash
# Build the nemotron-lora-runner image.
#
# Usage: ./docker/build.sh [tag]
# Default tag: today's date (YYYY-MM-DD).

set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TAG="${1:-$(date +%Y-%m-%d)}"
IMAGE="aevolve/nemo-reasoner:${TAG}"

echo "Building ${IMAGE} from ${HERE}"
docker build \
    -t "${IMAGE}" \
    -f "${HERE}/Dockerfile" \
    "${HERE}"

echo
echo "Built ${IMAGE}"
echo "Update seed_workspaces/nemo_reason/manifest.yaml:"
echo "  training:"
echo "    docker_image: ${IMAGE}"
