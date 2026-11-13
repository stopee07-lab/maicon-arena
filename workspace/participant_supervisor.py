#!/usr/bin/env python3
"""Bounded process recovery; clean/manual exits never respawn. Append-only journal."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from robot_client import TeamClient, ApiError

def process_create_time(pid):
    try:
        import psutil
        return psutil.Process(pid).create_time()
    except Exception:
        try:
            from datetime import datetime
            value=subprocess.check_output(['ps','-o','lstart=','-p',str(pid)],text=True).strip()
            return datetime.strptime(value,'%a %b %d %H:%M:%S %Y').timestamp()
        except Exception:return None

def should_retry(returncode,retries,stopping=False,finished=False):
    return returncode!=0 and retries<3 and not stopping and not finished

def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--server',required=True);parser.add_argument('--mission-code',required=True)
    parser.add_argument('--mode',default='match');parser.add_argument('--output',type=Path,required=True)
    args,_=parser.parse_known_args(argv);args.output.mkdir(parents=True,exist_ok=True)
    stop=threading.Event();child=None;retries=0
    client=TeamClient(args.server,args.mission_code,timeout=.8,api_prefix='/api/rehearsal' if args.mode=='rehearsal' else '/api/report')
    def halt(*_):
        stop.set()
        if child is not None and child.poll() is None:child.terminate()
    signal.signal(signal.SIGTERM,halt);signal.signal(signal.SIGINT,halt)
    def journal(event,**data):
        with (args.output/'supervisor.jsonl').open('a') as f:f.write(json.dumps({'time':time.time(),'event':event,**data})+'\n')
    while not stop.is_set():
        # Waiting/ready still launch so sensors/models initialize and ready can be sent.
        try:phase='running' if args.mode=='practice' else client.report_get('me').get('phase')
        except ApiError as exc:
            journal('standby',reason=str(exc));stop.wait(1.);continue
        if phase=='finished':journal('finished_no_restart');return 0
        journal('launch',attempt=retries+1,phase=phase)
        child=subprocess.Popen([sys.executable,str(Path(__file__).with_name('team_node.py')),*argv])
        journal('child_spawned',child_pid=child.pid,child_create_time=process_create_time(child.pid),supervisor_pid=os.getpid(),entrypoint=str(Path(__file__).with_name('team_node.py')))
        while child.poll() is None:
            if stop.wait(.3):
                child.terminate()
                try:child.wait(4.)
                except subprocess.TimeoutExpired:child.kill();child.wait()
                break
        code=child.returncode;journal('exit',returncode=code,manual_stop=stop.is_set())
        if not should_retry(code,retries,stop.is_set()):return 0 if stop.is_set() else code
        retries+=1;journal('retry_scheduled',retry=retries,maximum=3);stop.wait(min(3.,retries))
    return 0
if __name__=='__main__':raise SystemExit(main())
