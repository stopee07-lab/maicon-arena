"""Stop abandoned controllers only after matching stored and live process identity.

Runs in the workstation Python, where psutil is already a pinned dependency.
Never prints command lines (they contain a mission credential).
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import subprocess


def stop_owned_controllers(root,server_url,python_path):
    import psutil
    root=Path(root).resolve();store=root/'data/workbench';controllers=store/'controllers'
    result={'stopped':[],'unverified':0,'remaining':[]};verified=[];seen=set();directories=set()
    owner=psutil.Process();owner_name=owner.username();owner_exe=Path(owner.exe()).resolve()
    for file in controllers.glob('*/launch.json'):
        try:
            record=json.loads(file.read_text());directory=file.parent.resolve()
            if Path(record['output_dir']).resolve()!=directory or directory.parent!=controllers.resolve():raise ValueError('output')
            deployment=(store/'deployments'/record['deployment_id']).resolve()
            if deployment.parent!=(store/'deployments').resolve():raise ValueError('deployment')
            script=deployment/'code/team_node.py';weights=deployment/'weights.pt'
            if Path(record['code_path']).resolve()!=script:raise ValueError('script')
            if record.get('transport')=='ros':
                container=record.get('container_name')
                if container!='maicon-rebuild-'+directory.name:raise ValueError('container identity')
                tool=root/'ros2/mac-runtime.sh'
                check=subprocess.run(['bash',str(tool),'docker','inspect',container],capture_output=True,text=True,timeout=12)
                if check.returncode:continue
                info=json.loads(check.stdout)[0];mounts=info.get('Mounts',[])
                if not any(Path(m.get('Source','')).resolve()==directory and m.get('RW') for m in mounts):raise ValueError('container output ownership')
                if not any(Path(m.get('Source','')).resolve()==script.parent and not m.get('RW') for m in mounts):raise ValueError('container code ownership')
                stopped=subprocess.run(['bash',str(tool),'docker','stop','--time','5',container],capture_output=True,text=True,timeout=12)
                result['stopped' if stopped.returncode==0 else 'remaining'].append(str(directory));continue
            children=[]
            journal=directory/'supervisor.jsonl'
            if journal.is_file():
                for line in journal.read_text(encoding='utf-8').splitlines():
                    try:event=json.loads(line)
                    except ValueError:continue
                    if isinstance(event,dict) and event.get('event')=='child_spawned':children.append(event)
            launcher=('import sys; sys.path.insert(0, '+repr(str(script.parent))+'); '
                      'from participant_supervisor import run_participant; run_participant()')
            # Match deployment + output + server + weights + owner + generation.
            # username() works on Windows too; uids()/getuid() do not.
            # Search also finds a child orphaned by a crashed supervisor; a PID alone is not proof.
            for process in psutil.process_iter():
                try:
                    if process.pid in seen or process.username()!=owner_name:continue
                    if process.create_time()<float(record['started_at'])-5:continue
                    args=process.cmdline()
                    if len(args)<2 or Path(process.exe()).resolve()!=owner_exe:continue
                    if args[1]=='-c':
                        if len(args)<3 or args[2]!=launcher:continue
                        if not any(event.get('child_pid')==process.pid
                            and isinstance(event.get('child_create_time'),(int,float))
                            and abs(event['child_create_time']-process.create_time())<.05
                            and Path(event.get('entrypoint','')).resolve()==script for event in children):continue
                    elif Path(args[1]).resolve() not in (script,script.with_name('participant_supervisor.py')):continue
                    def option(name):
                        if args.count(name)!=1:raise ValueError('option')
                        return args[args.index(name)+1]
                    if option('--server')!=server_url or Path(option('--output')).resolve()!=directory or Path(option('--weights')).resolve()!=weights:continue
                    seen.add(process.pid);verified.append((process,directory));directories.add(directory)
                except (psutil.NoSuchProcess,psutil.ZombieProcess,psutil.AccessDenied,OSError,ValueError,IndexError):continue
        except (OSError,ValueError,TypeError,KeyError,IndexError,subprocess.SubprocessError):result['unverified']+=1
    for process,_ in verified:
        try:process.terminate()
        except psutil.NoSuchProcess:pass
    _,alive=psutil.wait_procs([p for p,_ in verified],timeout=5)
    for process in alive:
        try:process.kill()
        except psutil.NoSuchProcess:pass
    _,alive=psutil.wait_procs(alive,timeout=3)
    alive_pids=set()
    for process in alive:
        try:
            if process.status()!=psutil.STATUS_ZOMBIE:alive_pids.add(process.pid)
        except psutil.NoSuchProcess:pass
    for directory in directories:
        still=any(process.pid in alive_pids for process,d in verified if d==directory)
        result['remaining' if still else 'stopped'].append(str(directory))
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--server',required=True)
    args=parser.parse_args()
    try:result=stop_owned_controllers(args.root,args.server,sys.executable)
    except Exception as exc:result={'stopped':[],'remaining':[],'error':type(exc).__name__}
    print(json.dumps(result))

if __name__=='__main__':main()
