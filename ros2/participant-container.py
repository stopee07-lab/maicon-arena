"""Run local ROS bridge and participant together; the container init reaps both."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
args=sys.argv[1:]
p=argparse.ArgumentParser(add_help=False);p.add_argument('--server');p.add_argument('--mission-code');p.add_argument('--mode',default='match')
parsed,_=p.parse_known_args(args[1:])
env=dict(os.environ);env['PYTHONPATH']=str(Path(args[0]).parent)+os.pathsep+env.get('PYTHONPATH','')
env.update(SIM_SERVER=parsed.server,MISSION_CODE=parsed.mission_code,SIM_MODE=parsed.mode,ROBOT_NAMESPACE=env.get('MAICON_ROS_NAMESPACE',''))
# This bridge is explicitly a local simulator transport, never started on hardware.
bridge=subprocess.Popen(['ros2','launch','maicon_sim_bridge','simulator.launch.py'],env=env)
child=None;stopping=False

def stop(*_):
    global stopping
    stopping=True
    for process in (child,bridge):
        if process and process.poll() is None:process.terminate()
signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
try:
    child=subprocess.Popen(['python3',*args],env=env)
    while child.poll() is None and bridge.poll() is None and not stopping:time.sleep(.2)
    code=child.poll()
    if code is None:code=1
finally:
    stop()
    for process in (child,bridge):
        if process:
            try:process.wait(5.)
            except subprocess.TimeoutExpired:process.kill();process.wait()
raise SystemExit(code)
