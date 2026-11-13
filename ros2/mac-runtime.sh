#!/bin/bash
# Dedicated local VM; never select a global Docker context or edit ~/.ssh/config.
set -euo pipefail
SIM_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export COLIMA_HOME="$HOME/.maicon-rebuild-colima"
export LIMA_HOME="$HOME/.maicon-rebuild-lima"
export DOCKER_CONFIG="$SIM_ROOT/runtime/docker"
CONTEXT=colima-maicon-rebuild
case "${1:-status}" in
  start)
    mkdir -p "$SIM_ROOT/workspace" "$SIM_ROOT/data/ros-jupyter"
    colima start maicon-rebuild --vm-type vz --arch aarch64 --cpus 4 --memory 8 \
      --disk 30 --root-disk 12 --runtime docker \
      --mount "$SIM_ROOT:w" \
      --activate=false --ssh-config=false
    ;;
  stop) colima stop maicon-rebuild ;;
  status) colima status maicon-rebuild ;;
  build)
    DOCKER_BUILDKIT=0 docker --context "$CONTEXT" build \
      -f "$SIM_ROOT/ros2/Dockerfile" -t maicon-rebuild-ros2:humble "$SIM_ROOT"
    ;;
  build-participant)
    docker --context "$CONTEXT" build -f "$SIM_ROOT/ros2/Participant.Dockerfile" -t maicon-rebuild-participant:humble "$SIM_ROOT"
    ;;
  build-jupyter)
    DOCKER_BUILDKIT=0 docker --context "$CONTEXT" build \
      -f "$SIM_ROOT/ros2/Jupyter.Dockerfile" -t maicon-ros-jupyter:humble "$SIM_ROOT"
    ;;
  compose)
    shift
    docker --context "$CONTEXT" compose --project-name maicon-rebuild \
      --file "$SIM_ROOT/ros2/compose.yaml" "$@"
    ;;
  docker)
    shift
    docker --context "$CONTEXT" "$@"
    ;;
  *) echo 'Usage: ros2/mac-runtime.sh start|stop|status|build|build-jupyter|compose ARGS|docker ARGS' >&2; exit 2 ;;
esac
