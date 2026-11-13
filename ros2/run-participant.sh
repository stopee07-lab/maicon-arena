#!/bin/bash
# Each process uses the new runtime only. Deployment/output paths retain their absolute mounts.
set -euo pipefail
SIM_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export COLIMA_HOME="$HOME/.maicon-rebuild-colima"
export LIMA_HOME="$HOME/.maicon-rebuild-lima"
export DOCKER_CONFIG="$SIM_ROOT/runtime/docker"
TASK_ARGS=()
CODE_DIR="$(cd "$(dirname "$1")" && pwd)"
WEIGHTS=""
OUTPUT=""
PREVIOUS=""
for value in "$@"; do
  if [[ "$PREVIOUS" == "--weights" ]]; then WEIGHTS="$value"; fi
  if [[ "$PREVIOUS" == "--output" ]]; then OUTPUT="$value"; fi
  PREVIOUS="$value"
  value="${value/127.0.0.1/host.docker.internal}"
  TASK_ARGS+=("$value")
done
[[ -f "$WEIGHTS" && -d "$OUTPUT" ]] || { echo "Weights/output are required" >&2; exit 2; }
exec docker --context colima-maicon-rebuild run --rm --init --network host --name "maicon-rebuild-$(basename "$OUTPUT")" \
  -v "$CODE_DIR:$CODE_DIR:ro" -v "$WEIGHTS:$WEIGHTS:ro" -v "$OUTPUT:$OUTPUT" -w "$OUTPUT" \
  -e MAICON_TRANSPORT=ros -e MAICON_LLM_MODE -e MAICON_LLM_PROVIDER -e LLM_ENDPOINT -e LLM_MODEL -e LLM_KEY \
  -e MAICON_LLM_ENDPOINT -e MAICON_LLM_MODEL -e MAICON_LLM_KEY \
  -e MAICON_ROS_SLOT -e MAICON_ROS_NAMESPACE -e MAICON_ROS_SCAN_TOPIC -e MAICON_ROS_INTERFACE_PACKAGE \
  -e ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-42}" maicon-rebuild-participant:humble python3 /participant-container.py "${TASK_ARGS[@]}"
