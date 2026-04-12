#!/usr/bin/env bash
set -euo pipefail

REGISTRY="192.168.178.29:5000"
IMAGE_NAME="stock-portfolio-tracker"
TAG="${1:-latest}"

FULL_TAG="${REGISTRY}/${IMAGE_NAME}:${TAG}"

echo "Building ${FULL_TAG} ..."
docker build -t "${FULL_TAG}" .

echo "Pushing ${FULL_TAG} ..."
docker push "${FULL_TAG}"

echo "Done: ${FULL_TAG}"
