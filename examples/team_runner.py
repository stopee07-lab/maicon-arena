#!/usr/bin/env python3
"""Local approximation of PDF2 p18 team-node supervision; no browser code execution.

Starts a user-selected local Python script on observed ready/standby. A nonzero
exit gets at most three automatic restarts per observed round. report keeps the
child alive; finished/stop/reset terminates it. This is not the organizer daemon.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from robot_client import ApiError, TeamClient


class TeamSupervisor:
    MAX_RESTARTS = 3

    def __init__(self, command, restart_delay=.5, stop_grace=2., clock=time.monotonic, event_sink=None):
        self.command=list(command);self.restart_delay=restart_delay;self.stop_grace=stop_grace
        self.clock=clock;self.event_sink=event_sink
        self.child=None;self.events=[];self.round_number=0;self.restarts=0
        self.last_phase=None;self.active=False;self.launched=False;self.pending_restart=False
        self.next_restart=0.

    def emit(self,event,**details):
        record={'event':event,'round':self.round_number,'at_monotonic':self.clock(),**details}
        self.events.append(record)
        if self.event_sink:self.event_sink(record)

    def spawn(self,restart=False):
        if restart:self.restarts+=1
        self.child=subprocess.Popen(self.command,start_new_session=(os.name=='posix'))
        self.launched=True;self.pending_restart=False
        self.emit('spawn',pid=self.child.pid,restart=restart,restarts=self.restarts)

    def stop_child(self,reason):
        if self.child is None:return
        process=self.child
        if process.poll() is None:
            try:
                if os.name=='posix':os.killpg(process.pid,signal.SIGTERM)
                else:process.terminate()
                process.wait(timeout=self.stop_grace)
            except subprocess.TimeoutExpired:
                if os.name=='posix':os.killpg(process.pid,signal.SIGKILL)
                else:process.kill()
                process.wait(timeout=2.)
            except ProcessLookupError:process.wait(timeout=2.)
        self.emit('child_stopped',pid=process.pid,reason=reason,returncode=process.returncode)
        self.child=None;self.pending_restart=False

    def tick(self,phase,cmd):
        if phase not in ('idle','waiting','ready','running','reporting','finished'):
            raise ValueError('unknown phase; refusing to launch a child')
        if phase=='finished' or cmd=='stop':
            self.stop_child('finished/stop');self.active=False;self.pending_restart=False
            self.last_phase=phase;return
        if phase in ('idle','waiting'):
            self.stop_child('reset/waiting');self.active=False;self.pending_restart=False
            self.last_phase=phase;return
        # Official /me has no match ID. Only an observed return to ready is a new round.
        if phase=='ready' and (self.last_phase!='ready' or not self.active):
            self.stop_child('new round')
            self.round_number+=1;self.restarts=0;self.active=True;self.launched=False
            self.pending_restart=False;self.emit('round_ready')
        self.last_phase=phase
        if not self.active:return  # Late attachment during running cannot invent a standby event.
        if self.child is not None and self.child.poll() is not None:
            code=self.child.returncode;pid=self.child.pid;self.child=None
            self.emit('child_exit',pid=pid,returncode=code)
            if code!=0 and self.restarts<self.MAX_RESTARTS:
                self.pending_restart=True;self.next_restart=self.clock()+self.restart_delay
            elif code!=0:
                self.emit('restart_limit',limit=self.MAX_RESTARTS)
        if not self.launched and phase=='ready' and cmd=='standby':
            self.spawn()
        elif self.pending_restart and phase in ('ready','running','reporting') and self.clock()>=self.next_restart:
            self.spawn(restart=True)

    def close(self):
        self.stop_child('supervisor shutdown');self.active=False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',default='http://127.0.0.1:8765')
    parser.add_argument('--mission-code',required=True)
    parser.add_argument('--mode',choices=['match','rehearsal'],default='match')
    parser.add_argument('--script',type=Path,default=Path(__file__).with_name('mac_team.py'),
                        help='User-selected local Python file; executed directly without a shell')
    parser.add_argument('--poll',type=float,default=.2)
    parser.add_argument('--events',type=Path,help='Optional JSONL supervisor evidence, without credentials')
    parser.add_argument('--once',action='store_true',help='Exit after the observed round reaches finished/stop')
    parser.add_argument('script_args',nargs=argparse.REMAINDER,help='Child arguments after --')
    args=parser.parse_args()
    script=args.script.expanduser().resolve()
    if not script.is_file() or script.suffix!='.py':parser.error('--script must name an existing local .py file')
    if args.poll<.05:parser.error('--poll must be at least .05 seconds')
    child_args=args.script_args[1:] if args.script_args[:1]==['--'] else args.script_args
    command=[sys.executable,str(script),'--server',args.server,'--mission-code',args.mission_code,'--mode',args.mode]
    if script==Path(__file__).with_name('mac_team.py').resolve():command+=['--duration','86400']
    command+=child_args
    client=TeamClient(args.server,args.mission_code,
        api_prefix='/api/rehearsal' if args.mode=='rehearsal' else '/api/report',
        robot_prefix='/sim/rehearsal/robot' if args.mode=='rehearsal' else '/sim/robot')
    event_file=None
    if args.events:
        args.events.parent.mkdir(parents=True,exist_ok=True);event_file=args.events.open('a')
    def record(event):
        line=json.dumps(event,ensure_ascii=False)
        print(line,flush=True)
        if event_file:event_file.write(line+'\n');event_file.flush()
    runner=TeamSupervisor(command,event_sink=record)
    keep_running=[True]
    def shutdown(*_):keep_running[0]=False
    signal.signal(signal.SIGINT,shutdown);signal.signal(signal.SIGTERM,shutdown)
    last_error=0.
    try:
        while keep_running[0]:
            started=time.monotonic()
            try:
                me=client.report_get('me')
                cmd=('stop' if me['phase']=='finished' else 'idle' if me['phase'] in ('idle','waiting')
                     else client.sim_get('state')['cmd'])
                runner.tick(me['phase'],cmd)
                if args.once and runner.round_number and (me['phase']=='finished' or cmd=='stop'):break
            except ApiError as exc:
                # No launch/restart on stale phase. Child and server retain their own watchdogs.
                try:client.stop()
                except ApiError:pass
                if time.monotonic()-last_error>2:
                    record({'event':'transport_wait','status':exc.status});last_error=time.monotonic()
                if exc.status in (401,422):raise
            time.sleep(max(0,args.poll-(time.monotonic()-started)))
    finally:
        runner.close()
        try:client.stop()
        except ApiError:pass
        if event_file:event_file.close()

if __name__=='__main__':main()
