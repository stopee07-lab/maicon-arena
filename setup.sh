#!/usr/bin/env bash
set -euo pipefail
sim_root="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$sim_root"
sim_python="${PYTHON:-python3}"
"$sim_python" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer required; tested with 3.14"'
"$sim_python" -m venv training/.venv
training/.venv/bin/python -m pip install -r requirements.lock.txt -r requirements-notebook.txt
training/.venv/bin/python seed_model.py
printf '설치 완료. ./start.command 로 실행하세요.\n'

