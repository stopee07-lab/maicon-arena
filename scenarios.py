"""Local development checkpoints. Never supplied to a contestant process."""
from __future__ import annotations
import copy
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid
from engine import APIError

STATE_FIELDS = ('rule_profile','sensor_profile','opponent_mode','opponent_policy_log','difficulty','seed','arena','phase','elapsed','report_elapsed','clock','io_clock',
    'winning_capture','order_issued','target_issued','sensor_occupied','bonus',
    'test_mode','camera_enabled','answers','objectives','events','history')
LIMITATIONS = ['6대 위치·방향, 카메라 각도, 경기 시간·규칙·장애 상태를 복원합니다.',
    '물리 속도, 진행 중인 발광·연속피격 판정, 실행 프로그램의 내부 메모리는 초기화합니다. 과거 동작의 완전한 재생은 아닙니다.',
    '코드 수정 후 비교하는 로컬 연습 기능입니다. 공식 경기 중 코드 변경 허용 여부와 별개입니다.']

def write_json(path,data):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,indent=2))
    temp.replace(path)

def tuples(value):
    return tuple(tuples(x) for x in value) if isinstance(value,list) else value


class Scenarios:
    def __init__(self,root,server):
        self.root=Path(root);self.server=server
        self.store=self.root/'data'/'development'/'cases';self.store.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.motion={}

    def engine(self,runtime):
        if runtime not in ('practice','mission','rehearsal'):raise APIError(422,'실행 환경은 practice, mission, rehearsal입니다')
        return {'practice':self.server.practice,'mission':self.server.engine,'rehearsal':self.server.rehearsal}[runtime]

    def observe(self):
        for runtime in ('practice','mission','rehearsal'):
            e=self.engine(runtime)
            with e.lock:
                r=e.robots['B-1'];previous=self.motion.get(runtime)
                if not previous or previous['epoch']!=e.reset_epoch or math.hypot(r.x-previous['x'],r.y-previous['y'])>.025:
                    self.motion[runtime]=dict(x=r.x,y=r.y,epoch=e.reset_epoch,clock=e.clock)

    def diagnostics(self,runtime):
        e=self.engine(runtime);self.observe()
        with e.lock:
            r=e.robots['B-1'];scan=e.scan(r);ranges=scan.get('ranges',[])
            forward=[v for i,v in enumerate(ranges) if isinstance(v,(int,float)) and math.isfinite(v) and v>0
                and abs(scan.get('angle_min',-math.pi)+i*scan.get('angle_increment',0))<=math.radians(20)]
            result=dict(runtime=runtime,front_clearance_m=round(min(forward),3) if forward else None,
                command={'linear':r.v,'angular':r.w},stationary_seconds=round(max(0,e.clock-self.motion.get(runtime,{'clock':e.clock})['clock']),2),
                paused=e.paused,faults=copy.deepcopy(r.faults),goal=None,path=[],
                goal_note='목표 좌표·계획 경로는 현재 참가자 코드에서 미제공',flags=[])
            if e.paused:result['flags'].append('운영자 일시정지')
            for kind,enabled in r.faults.items():
                if enabled:result['flags'].append(f'{kind} 장애 주입')
            if not forward:result['flags'].append('전방 라이다 미수신')
            if forward and min(forward)<.3:result['flags'].append('전방 30cm 이내 물체 관측')
            if result['stationary_seconds']>=3 and not e.paused:result['flags'].append('3초 이상 위치 변화 2.5cm 미만')
            if abs(r.w)>.1 and abs(r.v)<.02:result['flags'].append('회전 명령 관측')
        wb=self.server.workbench
        deployment_runtime=('mission' if (wb.deployment or {}).get('mode')=='match' else (wb.deployment or {}).get('mode')) if wb else None
        if wb and deployment_runtime==runtime:
            c=wb.controller_snapshot()
            result['navigation']=c.get('navigation',{})
            result['controller_command']=c.get('velocity_command')
            result['sensor_healthy']=c.get('sensor_healthy')
            result['code_version_id']=c.get('code_version_id')
        result['note']='센서와 명령의 관측값입니다. 정지·회전의 원인을 확정한 진단은 아닙니다.'
        return result

    def path(self,identifier):
        if not re.fullmatch(r'case-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}',str(identifier)):raise APIError(404,'저장된 장면을 찾을 수 없습니다')
        path=self.store/identifier
        if not (path/'case.json').is_file():raise APIError(404,'저장된 장면을 찾을 수 없습니다')
        return path

    def read(self,identifier):
        return json.loads((self.path(identifier)/'case.json').read_text())

    def list(self):
        rows=[]
        for path in sorted(self.store.glob('case-*/case.json'),reverse=True):
            data=json.loads(path.read_text());rows.append({k:data.get(k) for k in
                ('id','name','created_at','runtime','code_version_id','model_id','summary','has_frame','limitations')})
        return {'cases':rows}

    def capture(self,body):
        runtime=body.get('runtime','practice');e=self.engine(runtime)
        physical=self.server.physics.status()
        if physical.get('connected') and physical.get('runtime')!=runtime:
            raise APIError(409,'화면의 실행 환경과 물리 경기장이 다릅니다. 현재 물리 경기장 환경을 선택하세요')
        with self.lock:
            diagnostics=self.diagnostics(runtime);wb=self.server.workbench
            controller=wb.controller_snapshot() if wb else {}
            deployment=copy.deepcopy(wb.deployment) if wb and wb.deployment else None
            expected_mode='match' if runtime=='mission' else runtime
            if deployment and deployment.get('mode')!=expected_mode:
                if wb.controller_process and wb.controller_process.poll() is None:
                    raise APIError(409,'다른 실행 환경의 로봇 프로세스를 먼저 정지하세요')
                # Reuse the selected source/model for this scene, not stale observations.
                deployment['mode']=expected_mode;controller={}
            with e.changed:
                e.paused=True;e.match_cmd='pause';e._clear_motion();e.changed.notify_all()
            if runtime=='rehearsal':self.server.rehearsal_start_at=None
            if wb:wb.stop_controller()
            identifier=f'case-{time.strftime("%Y%m%d-%H%M%S")}-{uuid.uuid4().hex[:8]}'
            path=self.store/identifier;path.mkdir()
            with e.lock:
                scene={key:copy.deepcopy(getattr(e,key)) for key in STATE_FIELDS}
                scene['robots']={}
                for slot,r in e.robots.items():
                    record=asdict(r);record.pop('mission_code',None)
                    record['exposure']=[[list(k),v] for k,v in r.exposure.items()]
                    scene['robots'][slot]=record
                scene['rng']=e.rng.getstate();scene['started_wall']=e.started_wall.isoformat()
                try:sensor=e.robot_state(e.robots['B-1'])
                except APIError as err:sensor={'unavailable':err.detail}
            write_json(path/'scene.json',scene)
            record=dict(id=identifier,name=str(body.get('name') or '문제 장면')[:120],created_at=time.time(),runtime=runtime,
                summary=f'{e.elapsed:.1f}초 · '+(' / '.join(diagnostics['flags']) or '현재 장면'),
                deployment=deployment,code_version_id=deployment.get('code_version_id') if deployment else None,
                model_id=deployment.get('model_id') if deployment else None,diagnostics=diagnostics,
                controller=controller,own_sensor=sensor,has_frame=False,limitations=LIMITATIONS,
                scene_sha256=hashlib.sha256((path/'scene.json').read_bytes()).hexdigest())
            physics=self.server.physics
            if physics.status().get('runtime')==runtime:
                try:
                    raw,headers=physics.frame('B-1');(path/'frame.jpg').write_bytes(raw)
                    record.update(has_frame=True,frame_captured_at=headers.get('X-Captured-At'))
                except (APIError,AttributeError):pass
            write_json(path/'case.json',record)
            return record

    def restore(self,identifier,body,start_physics=True):
        with self.lock:
            record=self.read(identifier);path=self.path(identifier);raw=(path/'scene.json').read_bytes()
            if hashlib.sha256(raw).hexdigest()!=record['scene_sha256']:raise APIError(409,'저장 장면이 변경되었습니다. 복원하지 않았습니다')
            scene=json.loads(raw);e=self.engine(record['runtime']);wb=self.server.workbench
            if any(k not in scene for k in ('rule_profile','sensor_profile','opponent_mode')):raise APIError(409,'이전 환경의 장면입니다. 규칙·센서 프로필이 없어서 새 환경에 복원하지 않습니다.')
            import rule_profile
            try:rule_profile.validate_profile(scene['rule_profile'])
            except ValueError as exc:raise APIError(409,str(exc))
            deployment=record.get('deployment');version=body.get('code_version_id') or record.get('code_version_id')
            if wb and not deployment:
                raise APIError(409,'이 장면에는 적용한 인식 모델이 없습니다. 로봇에 적용에서 모델·코드를 준비한 뒤 장면을 다시 저장하세요')
            if wb:
                # Validate selected immutable version before changing a running scene.
                if version:wb.code_store.version(version)
                wb.stop_controller()
            self.server.physics.stop()
            if wb and deployment:
                config={k:deployment[k] for k in ('model_id','policy','slot')}
                config['mode']='match' if record['runtime']=='mission' else record['runtime']
                if version:config['code_version_id']=version
                deployment=wb.deploy(config)
            with e.changed:
                e.reset()
                for key in STATE_FIELDS:setattr(e,key,copy.deepcopy(scene[key]))
                for slot,record_robot in scene['robots'].items():
                    r=e.robots[slot]
                    for key,value in record_robot.items():
                        if key in ('slot','camp','mission_code'):continue
                        if key=='exposure':value={tuple(k):v for k,v in value}
                        if key in ('origin','route_goal','shot_aim') and value is not None:value=tuple(value)
                        setattr(r,key,copy.deepcopy(value))
                e.rng.setstate(tuples(scene['rng']));e.started_wall=datetime.fromisoformat(scene['started_wall'])
                e.capture_previous_poses={r.slot:(r.x,r.y,r.yaw) for r in e.robots.values()};e._scan_cache={}
                e.drone_cache={};e.physical_scan={};e.paused=True;e.speed=1;e.match_cmd='pause';e._clear_motion()
                e._event('development_restore','저장 장면 복원 · 코드 실행 및 재개 대기')
                e.changed.notify_all()
            self.server.rehearsal_start_at=None;self.motion.pop(record['runtime'],None)
            if start_physics:
                self.server.physics.start(record['runtime'])
                with e.lock:e.paused=True;e.match_cmd='pause';e._clear_motion()
                deadline=time.monotonic()+12
                while not self.server.physics.status().get('connected') and time.monotonic()<deadline:time.sleep(.1)
                if not self.server.physics.status().get('connected'):raise APIError(503,'장면은 복원했지만 물리 엔진 연결에 실패했습니다. 실행 환경을 확인하세요')
            return dict(restored=True,id=identifier,runtime=record['runtime'],paused=True,deployment=deployment,
                physics=self.server.physics.status(),limitations=LIMITATIONS)

    def context(self,identifier):
        record=self.read(identifier)
        c=record.get('controller',{})
        # Only own observations; organizer truth remains inside the replay store.
        return dict(case_id=identifier,name=record['name'],runtime=record['runtime'],
            diagnostics=record['diagnostics'],own_sensor=record['own_sensor'],
            code_version_id=record.get('code_version_id'),model_id=record.get('model_id'),
            controller={k:c.get(k) for k in ('status','navigation','velocity_command','sensor_healthy','transport_error','camera_error','logs')},
            limitations=LIMITATIONS)

    def handle(self,method,route,body,query):
        if route=='/diagnostics' and method=='GET':return 200,self.diagnostics(query.get('runtime','practice'))
        if route=='/cases':
            if method=='GET':return 200,self.list()
            if method=='POST':return 201,self.capture(body)
        parts=route.strip('/').split('/')
        if len(parts)>=2 and parts[0]=='cases':
            if len(parts)==2 and method=='GET':return 200,self.read(parts[1])
            if len(parts)==3 and parts[2]=='restore' and method=='POST':return 200,self.restore(parts[1],body)
            if len(parts)==3 and parts[2]=='context' and method=='GET':return 200,self.context(parts[1])
            if len(parts)==3 and parts[2]=='frame.jpg' and method=='GET':
                path=self.path(parts[1])/'frame.jpg'
                if not path.is_file():raise APIError(404,'장면 저장 시 카메라 영상이 없었습니다')
                return 200,path.read_bytes(),'image/jpeg'
        raise APIError(404,'알 수 없는 장면 경로')
