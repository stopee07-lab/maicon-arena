#!/usr/bin/env python3
"""Start isolated loopback simulator, then test a real ROS2 Docker bridge."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import Engine
from server import SimulationHTTPServer, Handler


class IsolatedProbeHandler(Handler):
    """Image-only HTTP failure keeps state.camera_available true on purpose."""
    def do_GET(self):
        if self.path.startswith('/sim/robot/camera?') and self.server.probe_camera_fault:
            self._send(503,{'detail':'isolated camera transport fault'});return
        super().do_GET()

    def do_POST(self):
        self.server.probe_posts.append(self.path)
        if self.path=='/test/camera-fault' and self.headers.get('X-Admin-Key')=='isolated-ros-test-only':
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.server.probe_camera_fault=bool(body['enabled'])
            self._send(200,{'accepted':True});return
        super().do_POST()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--context',default='colima-maicon-sim')
    parser.add_argument('--image',default='maicon-local-ros2:humble')
    parser.add_argument('--read-only',action='store_true')
    parser.add_argument('--output',type=Path,default=Path(__file__).parent/'verification'/'runtime')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parents[1];env=dict(os.environ,DOCKER_CONFIG=str(root/'runtime/docker'))
    cli=['docker','--context',args.context];name='maicon-ros-verification';started=time.monotonic()
    def docker(*args,check=True):return subprocess.run([*cli,*args],env=env,check=check,capture_output=True,text=True)
    existing=docker('ps','-a','--filter','name=^/'+name+'$','--format','{{.Names}}')
    if existing.stdout.strip():raise SystemExit('Test container already exists; inspect it before retrying')
    teams=[{'slot':f'{camp}-{n}','mission_code':f'ROS{camp}{n:04d}'} for camp in ('B','R') for n in (1,2,3)]
    access={'admin_key':'isolated-ros-test-only','viewer_key':'isolated-ros-view-only','teams':teams}
    engine=Engine(teams=teams)
    for slot in engine.robots:engine.control({'action':'strategy','slot':slot,'strategy':'external'})
    server=SimulationHTTPServer(('127.0.0.1',0),access,engine)
    server.RequestHandlerClass=IsolatedProbeHandler;server.probe_camera_fault=False;server.probe_posts=[]
    worker=threading.Thread(target=server.serve_forever,daemon=True);ticker=None;created=False;team=None
    try:
        with (args.output/'server.log').open('w') as log,contextlib.redirect_stdout(log):
            worker.start();ticker=server.start_engine()
            docker('run','-d','--name',name,'--init','-e',f'SIM_SERVER=http://host.docker.internal:{server.server_port}','-e','MISSION_CODE=ROSB0001','-e','ROS_DOMAIN_ID=43','-e','SIM_READ_ONLY='+str(args.read_only).lower(),args.image)
            created=True
            script='read_sensors.py' if args.read_only else 'verify_runtime.py'
            docker('cp',str(Path(__file__).with_name(script)),name+':/workspace/verify_runtime.py')
            probe=docker('exec',name,'/entrypoint.sh','python3','/workspace/verify_runtime.py',check=False)
            (args.output/'probe.stdout').write_text(probe.stdout);(args.output/'probe.stderr').write_text(probe.stderr)
            if probe.returncode:raise RuntimeError(f'ROS probe failed ({probe.returncode}); inspect {args.output}/probe.stderr')
            result=json.loads(probe.stdout);result['elapsed_seconds']=time.monotonic()-started
            if args.read_only:
                assert not server.probe_posts,server.probe_posts
                result['http_post_count']=0
                docker('cp',name+':/tmp/ros-sensor-proof/ros-camera.png',str(args.output/'ros-camera.png'))
            else:
                # Exercise the supplied contestant entry point, beyond a ROS test node.
                engine.control({'action':'reset'})
                for slot in engine.robots:engine.control({'action':'strategy','slot':slot,'strategy':'external'})
                def eventually(predicate,timeout=10):
                    deadline=time.monotonic()+timeout
                    while time.monotonic()<deadline:
                        if predicate():return
                        if team.poll() is not None:raise RuntimeError('ROS team example exited early; inspect team.log')
                        time.sleep(.05)
                    raise AssertionError('ROS team example timed out; inspect team.log')
                with (args.output/'team.log').open('w') as team_log:
                    team=subprocess.Popen([*cli,'exec',name,'/entrypoint.sh','python3','/workspace/examples/team_node.py',
                        '--server',f'http://host.docker.internal:{server.server_port}','--mission-code','ROSB0001'],
                        env=env,stdout=team_log,stderr=subprocess.STDOUT)
                    engine.control({'action':'prepare'});eventually(lambda:engine.robots['B-1'].ready)
                    engine.control({'action':'start'});eventually(lambda:engine.robots['B-1'].v>0)
                    engine.control({'action':'finish'});eventually(lambda:'situation' in engine.robots['B-1'].reports)
                    if engine.phase=='reporting':engine.control({'action':'finish'})
                    team.wait(timeout=5);assert team.returncode==0
                    assert engine.robots['B-1'].v==0
                result['team_example']={'ready_read_back':True,'ros_twist_drive':True,'m6_saved':engine.robots['B-1'].reports['situation'],
                    'stop_exit_code':team.returncode,'weights_inference_tested':False}
            result['docker_context']=args.context;result['container_image_id']=docker('inspect','--format','{{.Image}}',name).stdout.strip()
            result['host_network']='Colima built-in host.docker.internal, no host-gateway override'
            (args.output/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    finally:
        if created:
            docker('stop','--time','5',name,check=False)
            (args.output/'bridge.log').write_text(docker('logs',name,check=False).stdout)
            docker('rm',name,check=False)
        if team is not None and team.poll() is None:team.terminate();team.wait(timeout=3)
        server.stopping.set();server.shutdown();server.server_close();worker.join(timeout=2)
        if ticker:ticker.join(timeout=2)
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
