#!/usr/bin/env python3
"""Isolated end-to-end supervisor test. Needs the simulator's Pillow dependency.

Organizer control is used only by this test harness, never by the contestant
runner or child. The test uses an ephemeral loopback port and fresh fake codes.
"""
import argparse
import contextlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import SimulationHTTPServer
from engine import Engine


def eventually(predicate,timeout=8.):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.05)
    raise AssertionError('timed out waiting for isolated supervisor state')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).parent/'runner-smoke')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    events=args.output/'events.jsonl'
    if events.exists():raise SystemExit('Evidence exists; choose a new --output folder')
    teams=[{'slot':f'{camp}-{number}','mission_code':f'TEST{camp}{number:03d}'} for camp in ('B','R') for number in (1,2,3)]
    access={'admin_key':'isolated-test-only','viewer_key':'isolated-view-only','teams':teams}
    engine=Engine(teams=teams);engine.control({'action':'strategy','slot':'B-1','strategy':'external'})
    server=SimulationHTTPServer(('127.0.0.1',0),access,engine)
    worker=threading.Thread(target=server.serve_forever,daemon=True)
    process=None;ticker=None;phases=[];started=time.monotonic()
    try:
        with (args.output/'server.log').open('w') as server_log, (args.output/'runner.log').open('w') as runner_log, contextlib.redirect_stdout(server_log):
            worker.start();ticker=server.start_engine()
            command=[sys.executable,str(Path(__file__).with_name('team_runner.py')),
                '--server',f'http://127.0.0.1:{server.server_port}','--mission-code',teams[0]['mission_code'],
                '--events',str(events),'--once']
            process=subprocess.Popen(command,stdout=runner_log,stderr=subprocess.STDOUT)
            engine.control({'action':'prepare'});phases.append('ready')
            eventually(lambda:engine.robots['B-1'].ready)
            engine.control({'action':'start'});phases.append('running')
            eventually(lambda:abs(engine.robots['B-1'].v)>0)
            engine.control({'action':'finish'});phases.append('reporting')
            eventually(lambda:'situation' in engine.robots['B-1'].reports)
            if engine.phase=='reporting':engine.control({'action':'finish'})
            phases.append('finished')
            process.wait(timeout=5.)
            assert process.returncode==0,process.returncode
            assert engine.robots['B-1'].v==0
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=5.)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2.)
        server.stopping.set();server.shutdown();server.server_close();worker.join(timeout=2.)
        if ticker is not None:ticker.join(timeout=2.)
    records=[json.loads(line) for line in events.read_text().splitlines()]
    assert len([e for e in records if e['event']=='spawn'])==1
    assert any(e['event']=='child_stopped' for e in records)
    assert 'M6 saved and echo verified' in (args.output/'runner.log').read_text()
    result={'status':'passed','isolated_server':True,'phases':phases,'elapsed_seconds':time.monotonic()-started,
        'child_spawns':sum(e['event']=='spawn' for e in records),'automatic_restarts':sum(e['event']=='spawn' and e['restart'] for e in records),
        'ready_verified':engine.robots['B-1'].ready,'own_m6_saved':engine.robots['B-1'].reports['situation'],
        'stopped_velocity':[engine.robots['B-1'].v,engine.robots['B-1'].w],
        'no_real_ros_or_hardware_claim':True}
    (args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
