#!/usr/bin/env python3
"""Verify native sensor -> ROS transport through an enforcing GET-only proxy.

Only own state/camera requests can reach the existing local match. No organizer,
strategy, actuator, ready or report request is forwarded, including at shutdown.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from urllib.request import urlopen
from urllib.error import HTTPError


def main():
    root=Path(__file__).resolve().parents[1]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--slot',default='B-1')
    parser.add_argument('--output',type=Path,default=root/'ros2/verification/native-read-only')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    token=next(t['mission_code'] for t in json.loads((root/'data/access.json').read_text())['teams'] if t['slot']==args.slot)
    audit={'GET /sim/robot/state':0,'GET /sim/robot/camera':0,'denied_mutation_requests':0,'denied_other_paths':0}
    class ReadOnlyProxy(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            audit['denied_mutation_requests']+=1;self.send_error(405)
        do_PUT=do_DELETE=do_PATCH=do_POST
        def do_GET(self):
            path=urlsplit(self.path).path
            if path not in ('/sim/robot/state','/sim/robot/camera'):
                audit['denied_other_paths']+=1;self.send_error(403);return
            audit['GET '+path]+=1
            try:
                with urlopen(f'http://127.0.0.1:{args.port}'+self.path,timeout=2) as response:
                    data=response.read();self.send_response(response.status)
                    for key,value in response.headers.items():
                        if key.lower()=='content-type' or key.lower().startswith('x-'):self.send_header(key,value)
                    self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            except HTTPError as exc:self.send_error(exc.code)
            except OSError:self.send_error(503)
    proxy=ThreadingHTTPServer(('127.0.0.1',0),ReadOnlyProxy);proxy.daemon_threads=True
    thread=threading.Thread(target=proxy.serve_forever,daemon=True);thread.start()
    env=dict(os.environ,DOCKER_CONFIG=str(root/'runtime/docker'),MISSION_CODE=token)
    cli=['docker','--context','colima-maicon-sim'];name='maicon-ros-native-readonly';created=False
    def docker(*command,check=True):return subprocess.run([*cli,*command],env=env,capture_output=True,text=True,check=check)
    if docker('ps','-a','--filter','name=^/'+name+'$','--format','{{.Names}}').stdout.strip():
        proxy.shutdown();proxy.server_close();raise SystemExit('Test container exists; inspect before retrying')
    try:
        docker('run','-d','--init','--name',name,'-e','MISSION_CODE','-e',f'SIM_SERVER=http://host.docker.internal:{proxy.server_port}',
            '-e','SIM_MODE=match','-e','SIM_READ_ONLY=true','-e','ROS_DOMAIN_ID=44','maicon-local-ros2:humble');created=True
        docker('cp',str(Path(__file__).with_name('read_sensors.py')),name+':/workspace/read_sensors.py')
        probe=docker('exec',name,'/entrypoint.sh','python3','/workspace/read_sensors.py',check=False)
        (args.output/'probe.stdout').write_text(probe.stdout);(args.output/'probe.stderr').write_text(probe.stderr)
        if probe.returncode:raise RuntimeError('Read-only native ROS probe failed; inspect probe.stderr')
        result=json.loads(probe.stdout)
        headers={k.lower():v for k,v in result['own_http_camera_headers'].items()}
        assert headers.get('x-physical-simulation')=='true',headers
        assert headers.get('x-camera','').startswith('mujoco-'),headers
        assert headers.get('x-captured-at','').endswith('+09:00'),headers
        assert int(headers['x-frame-seq'])>0
        docker('cp',name+':/tmp/ros-sensor-proof/ros-camera.png',str(args.output/'ros-camera.png'))
        result['native_physics_camera_test']=True;result['slot']=args.slot
        result['container_image_id']=docker('inspect','--format','{{.Image}}',name).stdout.strip()
    finally:
        if created:
            docker('stop','--time','5',name,check=False)
            logs=docker('logs',name,check=False)
            (args.output/'bridge.log').write_text(logs.stdout+logs.stderr)
            docker('rm',name,check=False)
        proxy.shutdown();proxy.server_close();thread.join(timeout=2)
        (args.output/'http-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    assert audit['denied_mutation_requests']==0 and audit['denied_other_paths']==0,audit
    result['get_only_proxy_audit']=audit
    (args.output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
