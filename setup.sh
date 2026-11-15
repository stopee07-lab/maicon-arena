#!/usr/bin/env bash
set -euo pipefail
sim_root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$sim_root"
exec "${PYTHON:-python3}" launch.py --setup "$@"

