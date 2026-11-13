#!/usr/bin/env bash
set -euo pipefail
sim_root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$sim_root"
if [[ ! -x training/.venv/bin/python ]]; then
  printf '먼저 ./setup.sh 를 실행하세요.\n'
  exit 1
fi
training/.venv/bin/python seed_model.py
printf 'MAICON 로컬 연습: http://127.0.0.1:8887\n'
exec training/.venv/bin/python server.py "$@"

