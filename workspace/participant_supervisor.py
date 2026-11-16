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


def run_participant():
    """Install the pre-M5 idle policy for the supervised participant process.

    Uses only participant odometry and existing clearance-gated navigation.
    Calibration and behavior are proposals for the local HTTP simulation.
    """
    import math
    import team_node

    class ContinuingCoverageNavigator(team_node.CoverageNavigator):
        def __init__(self,camp,slot):
            super().__init__(camp,slot)
            self.idle_pose=None;self.idle_since=None;self.idle_tick=None
            self.relocating=False;self.relocation_origin=None
            self.idle_departures=0

        def reset_idle(self):
            self.idle_pose=None;self.idle_since=None;self.idle_tick=None

        def suspend(self):
            super().suspend()
            self.reset_idle()
            self.relocating=False;self.relocation_origin=None

        def apply_order(self,order):
            super().apply_order(order)
            self.reset_idle()
            self.relocating=False;self.relocation_origin=None

        def set_objective(self,name):
            # Accepted M5 always preempts this patrol policy.
            self.reset_idle()
            self.relocating=False;self.relocation_origin=None
            super().set_objective(name)

        def observe_panel(self,location,state,frame):
            # Inference and ObservationStore remain active during relocation.
            # Do not create another inspection stop before leaving this position.
            if self.relocating:return
            return super().observe_panel(location,state,frame)

        def consider_target(self,goal,position,frame,now=None):
            if self.relocating:return
            return super().consider_target(goal,position,frame,now)

        def depart_idle_position(self,position,now):
            if not self.route:return False
            start=self.route_index%len(self.route)
            indices=[(start+offset)%len(self.route) for offset in range(1,len(self.route)+1)]
            index=next((i for i in indices if math.dist(position,self.route[i])>.45),None)
            if index is None:return False
            if self.coverage_started and self.coverage_index<len(self.coverage_tasks):
                point,_=self.coverage_tasks[self.coverage_index]
                self.failed_goals.append({'goal':point,'reason':'two-second idle departure; inspection incomplete, evidence retained'})
                self.failed_goals=self.failed_goals[-20:]
                self.coverage_index+=1
                self.coverage_point_started=None;self.coverage_arrived_at=None
                self.coverage_settled=None;self.coverage_frames=set()
            self.route_index=index
            self.coverage_reset_motion()
            self.goal=None
            self.relocating=True;self.relocation_origin=position
            self.idle_departures+=1
            self.idle_pose=None;self.idle_since=now
            self.reason='two-second idle departure; continue observation until official M5'
            return True

        def command(self,state):
            now=time.monotonic()
            # Preserve operator, match, resource, M1 validation and beam holds.
            # M2 aiming/emission is an action, not idle waiting for M5.
            eligible=(self.order_ready and not self.hold and not self.official_capture
                and self.objective is None and not self.retreat
                and team_node.SafeMotion.allowed(state)
                and state.get('cmd') not in ('pause','stop')
                and not state.get('laser')
                and not (self.m2_goal is not None and not self.m2_done))
            battery=state.get('battery_pct')
            hp=state.get('hp');maximum=state.get('hp_max')
            if isinstance(battery,(int,float)) and battery<=20:eligible=False
            if isinstance(hp,(int,float)) and hp<=0:eligible=False
            if (isinstance(hp,(int,float)) and isinstance(maximum,(int,float))
                    and maximum>0 and hp<=maximum*.5):eligible=False
            if not eligible:
                self.reset_idle()
                self.relocating=False;self.relocation_origin=None
                return super().command(state)
            pose=team_node.pose_from_odom(state)
            if not all(math.isfinite(value) for value in pose):
                self.reset_idle()
                return super().command(state)
            position=pose[:2]
            if (self.idle_pose is None or self.idle_tick is None
                    or now-self.idle_tick>1.):
                self.idle_pose=pose;self.idle_since=now
            elif (math.dist(position,self.idle_pose[:2])>=.015
                    or abs(team_node.angle(pose[2]-self.idle_pose[2]))>=.04):
                # Actual movement counts; repeatedly issuing a blocked command does not.
                self.idle_pose=pose;self.idle_since=now
            self.idle_tick=now
            if self.relocating and math.dist(position,self.relocation_origin)>=.35:
                self.relocating=False;self.relocation_origin=None
                self.coverage_reset_motion()
            if now-self.idle_since>=2.:
                self.depart_idle_position(position,now)
                self.idle_pose=pose;self.idle_since=now
            if self.relocating:
                # Skip stationary coverage stops while relocating, but retain the
                # same planner, recovery logic, sensor checks and safety margins.
                if self.route_index>=len(self.route):
                    self.depart_idle_position(position,now)
                result=team_node.SurveyNavigator.command(self,state)
                if self.retreat:
                    self.reset_idle()
                    self.relocating=False;self.relocation_origin=None
                elif self.mission_stage=='survey':
                    self.goal_reason='pre-M5 continuous survey after two-second idle'
                return result
            return super().command(state)

        def snapshot(self):
            result=super().snapshot()
            result.update(pre_m5_relocating=self.relocating,
                idle_departures=self.idle_departures,
                idle_elapsed_s=None if self.idle_since is None else
                    round(max(0.,time.monotonic()-self.idle_since),2))
            return result

    team_node.CoverageNavigator=ContinuingCoverageNavigator
    team_node.main()


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
        launcher=('import sys; sys.path.insert(0, '+repr(str(Path(__file__).resolve().parent))+'); '
                  'from participant_supervisor import run_participant; run_participant()')
        child=subprocess.Popen([sys.executable,'-c',launcher,*argv])
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
