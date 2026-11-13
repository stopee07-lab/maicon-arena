#!/usr/bin/env python3
"""Run a low-speed example team on macOS with Python stdlib; no ROS or ML needed."""
import argparse
import json
from pathlib import Path
import signal
import time
from robot_client import ApiError, ReportWorkflow, SafeMotion, TeamClient, forward_clearance, sensors_healthy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', default='http://127.0.0.1:8765')
    parser.add_argument('--mission-code', required=True)
    parser.add_argument('--duration', type=float, default=360.)
    parser.add_argument('--speed', type=float, default=.10)
    parser.add_argument('--ranking', nargs='+', help='Own observation-based M6 estimate; otherwise a fixed untrained prior')
    parser.add_argument('--reports', type=Path, help='Optional JSON with full interpretation and detection reports from your own perception')
    parser.add_argument('--camera-output', type=Path, help='Save first own synthetic camera JPEG')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--mode',choices=['match','practice','rehearsal'],default='match')
    mode.add_argument('--practice',dest='mode',action='store_const',const='practice')
    mode.add_argument('--rehearsal',dest='mode',action='store_const',const='rehearsal')
    args = parser.parse_args()
    api_prefix='/api/rehearsal' if args.mode=='rehearsal' else '/api/report'
    robot_prefix='/sim/robot' if args.mode=='match' else '/sim/'+args.mode+'/robot'
    client = TeamClient(args.server, args.mission_code,api_prefix=api_prefix,robot_prefix=robot_prefix)
    motion = SafeMotion(); report = ReportWorkflow(client)
    keep_running = [True]
    def stop_signal(*_): keep_running[0] = False
    signal.signal(signal.SIGINT, stop_signal); signal.signal(signal.SIGTERM, stop_signal)
    reports = json.loads(args.reports.read_text()) if args.reports else {}
    sent = set(); next_ready=0.; next_reports=0.; next_me=0.; me={}; previous_cmd=None
    started = time.monotonic(); initialized=False; last_error=0.
    order_received=False;target_received=False;next_mission=0.
    try:
        client.sim_post('strategy', {'strategy':'external'})
        while keep_running[0] and time.monotonic()-started < args.duration:
            tick=time.monotonic()
            try:
                state=client.sim_get('state'); motion.observe(state)
                if not initialized:
                    jpeg, _ = client.sim_get('camera')
                    if args.camera_output:
                        args.camera_output.parent.mkdir(parents=True, exist_ok=True)
                        args.camera_output.write_bytes(jpeg)
                    initialized=True
                    print('Own sensor initialization complete; no ML model is loaded.', flush=True)
                if state['cmd'] != previous_cmd:
                    print(f"match: {state['mode']} / {state['cmd']}", flush=True)
                    previous_cmd=state['cmd']
                if tick >= next_me or state['cmd'] in ('report','stop'):
                    me=({'phase':'running','ready':True,'camp':'BLUE' if state['slot'].startswith('B') else 'RED'}
                        if args.mode=='practice' else client.report_get('me')); next_me=tick+.5
                if me.get('phase') == 'finished' or state['cmd']=='stop': break
                if initialized and me.get('phase')=='ready' and not me.get('ready') and tick>=next_ready:
                    next_ready=tick+2.
                    try:
                        response=client.report_post('ready',{})
                        print('ready response:', response, flush=True)
                        me=client.report_get('me')  # Read back final state; response alone is not proof.
                    except ApiError as exc:
                        if exc.status not in (0,409,429,500,502,503,504): raise
                if state['cmd']=='report' or me.get('phase')=='reporting':
                    motion.clear(); client.stop()
                    if report.payload is None:
                        opponents='R' if me.get('camp')=='BLUE' else 'B'
                        ranking=args.ranking or [f'{opponents}-{i}' for i in (1,2,3)]
                        if not args.ranking: print('M6: fixed uninformed prior; no enemy truth or trained estimator.', flush=True)
                        report.begin(ranking)
                    was_done=report.done
                    report.tick(me.get('phase'))
                    if report.done and not was_done: print('M6 saved and echo verified:',report.payload, flush=True)
                elif SafeMotion.allowed(state) and state['cmd']!='pause' and sensors_healthy(state) and me.get('phase')=='running':
                    # Recompute after every start/resume. Own scan only; no arena/admin lookup.
                    obstacle=forward_clearance(state)<.30
                    motion.command(0. if obstacle else args.speed, .45 if obstacle else .12)
                    linear,angular=motion.output()
                    client.sim_post('cmd_vel',{'linear_x':linear,'angular_z':angular})
                    if args.mode!='practice' and tick>=next_mission:
                        next_mission=tick+1.
                        if not order_received:
                            reply=client.report_get('order',wait=0)
                            order_received=reply.get('accepted') is True
                            if order_received:print('M1 tactical order:',reply['order'],flush=True)
                        if not target_received:
                            reply=client.report_get('target',wait=0)
                            target_received=reply.get('accepted') is True
                            if target_received:print('M5 objective:',reply['order'],flush=True)
                    if args.mode!='practice' and tick>=next_reports:
                        next_reports=tick+2.
                        for name in ('interpretation','detection'):
                            if name in reports and name not in sent:
                                response=client.report_post(name,reports[name])
                                if response.get('accepted') is True:
                                    sent.add(name); print(name,'accepted:',response.get('received'),flush=True)
                else:
                    motion.clear(); client.stop()
            except ApiError as exc:
                motion.clear()
                try: client.stop()
                except ApiError: pass  # Server's own 0.5s watchdog remains authoritative.
                if exc.status in (401,422): raise
                if tick-last_error>2:
                    print('Transport/phase retry; motion stopped:',exc,flush=True); last_error=tick
                me={}  # Do not retain a phase across a transport failure.
                next_me=0.
            time.sleep(max(0., .1-(time.monotonic()-tick)))
    finally:
        motion.clear()
        try: client.stop()
        except ApiError: pass
    print(json.dumps({'m6_saved':report.done,'m6_failed':report.failed,'reports_sent':sorted(sent)},ensure_ascii=False))


if __name__ == '__main__': main()
