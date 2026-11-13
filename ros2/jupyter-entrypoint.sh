#!/bin/bash
set -eo pipefail
source /opt/ros/humble/setup.bash
source /opt/maicon/install/setup.bash
set -u
mkdir -p "$HOME" "$JUPYTER_RUNTIME_DIR" "$IPYTHONDIR"
chmod 700 "$HOME" "$JUPYTER_RUNTIME_DIR" "$IPYTHONDIR"
bridge_pid=''
jupyter_pid=''
cleanup() {
  trap - TERM INT EXIT
  if [[ -n "$bridge_pid" ]]; then kill -TERM "$bridge_pid" 2>/dev/null || true; fi
  if [[ -n "$jupyter_pid" ]]; then kill -TERM "$jupyter_pid" 2>/dev/null || true; fi
  wait 2>/dev/null || true
}
trap cleanup TERM INT EXIT
ros2 launch maicon_sim_bridge simulator.launch.py > /tmp/maicon-bridge.log 2>&1 &
bridge_pid=$!
# The notebook only needs ROS topics/services. Keep the robot credential out of its environment.
unset MISSION_CODE
python3 -m jupyterlab --config=/config/jupyter_server_config.json &
jupyter_pid=$!
wait -n "$bridge_pid" "$jupyter_pid"
