#!/bin/bash
set -eo pipefail
source /opt/ros/humble/setup.bash
source /opt/maicon/install/setup.bash
set -u
exec "$@"
