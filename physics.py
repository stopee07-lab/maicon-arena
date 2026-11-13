"""Native MuJoCo subprocess bridge. No hidden rendering fallback."""
from __future__ import annotations
import base64,json,os,queue,subprocess,threading,time,uuid
from pathlib import Path
from engine import APIError
from physics_world import build_world

class Physics:
    def __init__(self,server,root):
        self.server=server;self.root=Path(root);self.python=self.root/'training/.venv/bin/python'
        self.lock=threading.RLock();self.process=None;self.engine=None;self.runtime=None
        self.last=0.;self.started_at=0.;self.error=None;self.warning=None;self.lag_seconds=0.;self.frames={};self.sim_time=0.;self.seq=0;self.pending=queue.Queue();self.requests={};self.generation=0;self.last_generation=None
        self.requested_cameras={}
        self.directory=self.root/'physics_data';self.directory.mkdir(exist_ok=True)
    def status(self):
        running=self.process is not None and self.process.poll() is None
        age=time.monotonic()-self.last if self.last else None
        error=self.error
        if self.process and self.process.poll() is not None and self.process.returncode:error=f'Physics exited ({self.process.returncode}); physics_data/worker.log'
        return dict(installed=self.python.is_file(),running=running,connected=running and age is not None and age<2,runtime=self.runtime,provider='mujoco',renderer='MuJoCo OpenGL',sim_time=self.sim_time,frames=self.seq,frame_age_s=round(age,3) if age is not None else None,error=error,warning=self.warning,lag_seconds=self.lag_seconds)
    def start(self,runtime='practice',controlled_slot='B-1'):
        if runtime not in ('practice','mission','rehearsal'):raise APIError(422,'runtime: practice, mission, rehearsal')
        self.stop()
        if not self.python.is_file():raise APIError(503,'training/.venv Python runtime is missing')
        self.engine={'practice':self.server.practice,'mission':self.server.engine,'rehearsal':self.server.rehearsal}[runtime];self.runtime=runtime
        with self.engine.lock:
            self.engine.physical_active=True;self.engine.physical_scan={};self.engine.speed=1;self.engine._clear_motion()
            self.engine.robots[controlled_slot].strategy='external'
            if self.engine.practice_mode or self.engine.phase in ('idle','ready','finished'):self.engine.paused=False
            snapshot=self.engine.snapshot();self.last_generation=self.engine.reset_epoch
        xml,panels=build_world(snapshot['arena'],snapshot['robots']);(self.directory/'arena.xml').write_text(xml)
        self.generation+=1;self.last=0;self.frames={};self.error=None;self.warning=None;self.lag_seconds=0.;self.requested_cameras={}
        config=dict(server=f'http://127.0.0.1:{self.server.server_port}',admin_key=self.server.access['admin_key'],generation=self.generation,world=str(self.directory/'arena.xml'),panels=panels,robots=snapshot['robots'],environment=snapshot['arena'].get('environment',{}))
        config_path=self.directory/'worker.json';config_path.write_text(json.dumps(config));config_path.chmod(0o600)
        self.started_at=time.monotonic()
        with (self.directory/'worker.log').open('w') as log:
            self.process=subprocess.Popen([str(self.python),str(self.root/'physics_worker.py'),'--config',str(config_path)],cwd=self.root,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'PYTHONUNBUFFERED':'1','OMP_NUM_THREADS':'2'})
        return self.status()
    def stop(self):
        process=self.process
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
        if self.engine:
            with self.engine.lock:self.engine._clear_motion();self.engine.paused=True
            # Remains physically owned/frozen. Never silently resume kinematics.
        with self.lock:
            for req in self.requests.values():req['error']='Physics stopped';req['event'].set()
            self.requests.clear()
        self.process=None;self.warning=None;self.lag_seconds=0.;self.requested_cameras={};return self.status()
    def exchange(self,body):
        if body.get('generation')!=self.generation or not self.engine:raise APIError(409,'Stale physics session')
        samples=body.get('samples')
        if samples is not None:
            if not isinstance(samples,list) or len(samples)>5:raise APIError(422,'Physics samples must contain at most five ticks')
            for sample in samples:
                if not isinstance(sample,dict) or sample.get('dt')!=.05 or not isinstance(sample.get('robots'),list):raise APIError(422,'Physics samples require robots and dt=0.05')
        e=self.engine
        with e.lock:
            if self.last_generation!=e.reset_epoch:
                self.last_generation=e.reset_epoch;self.frames={};e.physical_scan={}
                snapshot=e.snapshot();xml,panels=build_world(snapshot['arena'],snapshot['robots'])
                return dict(reset=snapshot['robots'],generation=self.generation,world_xml=xml,panels=panels,environment=snapshot['arena'].get('environment',{}))
            self.last=time.monotonic();self.sim_time=float(body.get('sim_time',0));self.seq=int(body.get('seq',0))
            lag=body.get('lag_seconds')
            if lag is not None:
                if not isinstance(lag,(int,float)) or not 0 <= lag <= 86400:raise APIError(422,'lag_seconds must be finite and nonnegative')
                if lag>2:
                    self.lag_seconds=round(float(lag),3)
                    self.warning='처리 지연으로 일시정지했습니다. 경기 화면의 계속을 누르면 현재 위치에서 재개합니다.'
                    if not e.paused or not any(event['type']=='physics_lag' for event in e.events[-1:]):
                        e._event('physics_lag',self.warning)
                    e.paused=True;e.match_cmd='pause';e._clear_motion();e.changed.notify_all()
            elif not e.paused:
                self.warning=None;self.lag_seconds=0.
            def apply_poses(robots):
                for item in robots:
                    r=e.robots.get(item.get('slot'))
                    if r:
                        r.x,r.y,r.yaw=item['x'],item['y'],item['yaw'];r.pan=item['pan'];r.tilt=item['tilt']
                        if item.get('scan'):e.physical_scan[r.slot]=item['scan']
            if samples is not None:
                for sample in samples:
                    apply_poses(sample['robots'])
                    e.step(.05)
                # The final pose may additionally carry a freshly rendered scan.
                apply_poses(body.get('robots',[]))
            else:
                apply_poses(body.get('robots',[]))
                dt=float(body.get('dt',0))
                if dt>0:e.step(min(.05,dt))
            for slot,frame in body.get('frames',{}).items():
                self.frames[slot]={**frame,'data':base64.b64decode(frame['image_base64']),'received':self.last}
            result=body.get('capture_result')
            if result:
                with self.lock:
                    req=self.requests.get(result['id'])
                    if req:req['result']=result.get('frame');req['error']=result.get('error');req['event'].set()
            active=not e.paused and (e.practice_mode or e.phase=='running')
            commands=[]
            for r in e.robots.values():
                commands.append(dict(slot=r.slot,v=r.v if active else 0,w=r.w if active else 0,pan=r.pan_goal if active else r.pan,tilt=r.tilt_goal if active else r.tilt,gimbal_speed=r.gimbal_speed,hp=r.hp,ammo=r.ammo))
            capture=None
            while lag is None and not self.pending.empty():
                candidate=self.pending.get_nowait()
                if candidate['id'] in self.requests:capture={k:candidate[k] for k in ('id','seed','slot')};break
            with self.lock:
                self.requested_cameras={slot:until for slot,until in self.requested_cameras.items() if until>self.last}
                requested_cameras=sorted(self.requested_cameras)
            return dict(commands=commands,capture=capture,generation=self.generation,paused=e.paused,requested_cameras=requested_cameras,target_states={t['id']:t['destroyed'] for t in e.arena['targets']})
    def frame(self,key):
        if not self.status()['connected']:raise APIError(503,'MuJoCo physics/camera is not connected')
        if key not in ('B-1','drone') and self.engine and key in self.engine.robots:
            with self.lock:self.requested_cameras[key]=time.monotonic()+3
        frame=self.frames.get(key)
        max_age=4 if key in ('B-1','drone') else 1
        if not frame or time.monotonic()-frame['received']>max_age:raise APIError(503,'Physical camera frame is not ready or stale')
        return frame['data'],{'X-Captured-At':frame['captured_at'],'X-Frame-Seq':frame['seq'],'X-Camera':'mujoco-'+key,'X-Physical-Simulation':'true'}
    def capture_frame(self,seed,slot='B-1'):
        if not self.status()['connected']:raise RuntimeError('Start MuJoCo physical arena before collecting images')
        req=dict(id=uuid.uuid4().hex,seed=int(seed),slot=slot,event=threading.Event())
        with self.lock:self.requests[req['id']]=req
        self.pending.put(req)
        try:
            if not req['event'].wait(25):raise RuntimeError('Physical capture timed out')
            if req.get('error'):raise RuntimeError(req['error'])
            return req['result']
        finally:
            with self.lock:self.requests.pop(req['id'],None)
