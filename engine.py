"""Authoritative, deterministic local MAICON approximation (not the official server)."""
from __future__ import annotations
import copy
import heapq
import math
import random
import threading
import rule_profile as rules
import arena_layout
import sensor_profile as sensors
from fidelity_geometry import ray_aabb, ray_disk, laser_pose, robot_receivers, ray_robot, intersects_line, crossing_fraction
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

DT = .05
RADIUS = .17
SECTORS = ('B-S1','B-S2','B-S3','CM','R-S1','R-S2','R-S3')
OBJECT_TYPES = ('MILITARY_TRUCK','MORTAR','TANK','MISSILE','AMMO_BOX','DRUM')
TARGET_TYPES = ('COMMS','OUTPOST','RADAR')
BASE_TYPES = ('COMMAND_POST','MAINTENANCE','SUPPLY_DEPOT')
STRATEGIES = ('scout','defender','hunter','capturer','balanced','external')
PHASE_KO = dict(idle='대기 전',waiting='대기',ready='준비',running='진행',reporting='보고',finished='종료')
KST = timezone(timedelta(hours=9))
PROVISIONAL = [
    '로컬 20Hz 기하 시뮬레이션이며 실물 물리·센서·최종 경기장 복제가 아닙니다.',
    '배점은 버전 설정에 보존한 잠정값이며 당일 최종 규칙·실측 확인이 필요합니다.',
    'M3는 섹터 종류·수량 완전 일치; M6 동점 순서 허용은 잠정 규칙입니다.',
    'M5는 차체-센서선의 신규 교차를 보간 시각으로 정렬하며 동시사건 정책은 시뮬레이션 가정입니다.',
    '기본 상대는 자기 센서/수신명령만 사용하는 단순 회피·사격 정책입니다. 실제 참가팀 전략과 동등하지 않습니다.',
    'JPEG는 합성 영상입니다. 실제 YOLO 정확도·Jetson 성능·ROS2 하드웨어 호환성을 보증하지 않습니다.'
]


class APIError(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail
        super().__init__(str(detail))


def invalid(field, message, where='body'):
    raise APIError(422, [{'loc':[where,field], 'msg':message, 'type':'value_error'}])


def number(body,key,low,high,default=None):
    value=body.get(key,default)
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low <= value <= high:
        invalid(key,f'{key}는 {low}~{high} 범위의 유한한 숫자여야 합니다')
    return float(value)


def angle(x): return (x+math.pi)%(2*math.pi)-math.pi


def ray_box(x,y,dx,dy,box,inflate=0):
    near,far=0.,float('inf')
    for p,d,c,size in ((x,dx,box['x'],box['w']), (y,dy,box['y'],box['h'])):
        lo,hi=c-size/2-inflate,c+size/2+inflate
        if abs(d)<1e-12:
            if p<lo or p>hi:return None
        else:
            a,b=(lo-p)/d,(hi-p)/d
            near,far=max(near,min(a,b)),min(far,max(a,b))
            if near>far:return None
    return near if far>=0 else None


def ray_circle(x,y,dx,dy,cx,cy,radius):
    ox,oy=x-cx,y-cy
    proj=ox*dx+oy*dy
    disc=proj*proj-(ox*ox+oy*oy-radius*radius)
    if disc<0:return None
    root=math.sqrt(disc)
    if -proj+root<0:return None
    return max(0.,-proj-root)


@dataclass
class Robot:
    slot: str
    camp: str
    mission_code: str
    x: float
    y: float
    yaw: float
    strategy: str
    pan: float=0.
    tilt: float=0.
    hp: int=4
    ammo: int=8
    ready: bool=False
    included: bool=True
    laser: bool=False
    faults: dict=field(default_factory=lambda:dict(camera=False,lidar=False,network=False))
    reports: dict=field(default_factory=dict)
    v: float=0.
    w: float=0.
    command_at: float=-999.
    shot_at: float=-999.
    beam_until: float=0.
    hit_lock_until: float=0.
    drive_lock_until: float=0.
    invulnerable_until: float=0.
    pan_goal: float=0.
    tilt_goal: float=0.
    gimbal_speed: float=80.
    oled: dict=field(default_factory=dict)
    oled_at: float=-999.
    origin: tuple=()
    route: list=field(default_factory=list)
    route_goal: tuple=()
    route_at: float=-999.
    exposure: dict=field(default_factory=dict)
    bot_waypoint: int=0
    bot_report_at: float=-999.
    shot_aim: tuple|None=None

    def __post_init__(self):self.origin=(self.x,self.y,self.yaw)


class Engine:
    def __init__(self, seed=20261112, teams=None, practice_mode=False, sensor_profile="mild", opponent_mode="sensor_limited", rule_profile=rules.PROFILE_ID):
        self.lock=threading.RLock()
        self.changed=threading.Condition(self.lock)
        self.seed=seed
        self.rule_profile=rules.get_profile(rule_profile)
        self.sensor_profile=sensors.get_profile(sensor_profile)
        if opponent_mode not in ('sensor_limited','privileged'):raise ValueError('unknown opponent mode')
        self.opponent_mode=opponent_mode
        self.difficulty=0
        self.physical_active=False;self.physical_scan={};self.reset_epoch=0
        self.teams=teams
        self.practice_mode=practice_mode
        self.reset()

    def reset(self):
        rules.validate_profile(self.rule_profile)
        self.reset_epoch+=1;self.physical_scan={};self._scan_cache={};self.capture_previous_poses={}
        self.phase='idle';self.paused=False;self.elapsed=0.;self.report_elapsed=0.;self.speed=1
        self.clock=0.;self.io_clock=0.;self.match_cmd='idle';self.events=[];self.history=[];self.opponent_policy_log=[];self.winning_capture=None
        self.order_issued=None;self.target_issued=None;self.sensor_occupied={};self.bonus={}
        self.test_mode=False;self.camera_enabled=True;self.drone_cache={}
        self.started_wall=datetime.now(KST)
        self.rng=random.Random(self.seed)
        self.arena=self._make_arena()
        self.robots={}
        defaults=['balanced','scout','defender','hunter','capturer','balanced']
        for i,slot in enumerate(['B-1','B-2','B-3','R-1','R-2','R-3']):
            camp='BLUE' if slot[0]=='B' else 'RED'
            token=next((t['mission_code'] for t in self.teams or [] if t['slot']==slot),f'SIM{slot[0]}000{slot[-1]}')
            self.robots[slot]=Robot(slot,camp,token,.28 if camp=='BLUE' else 5.84,4.15-(i%3)*1.65,0. if camp=='BLUE' else math.pi,defaults[i])
        if self.sensor_profile['name']=='stress':
            # Unmeasured placement uncertainty, separate from odometry drift.
            for r in self.robots.values():
                jitter=random.Random(f'start-pose-v1:{self.seed}:{r.slot}')
                r.x+=jitter.uniform(-.03,.03);r.y+=jitter.uniform(-.03,.03);r.yaw=angle(r.yaw+jitter.uniform(-math.radians(4),math.radians(4)))
                r.origin=(r.x,r.y,r.yaw)
            self.arena['environment']['start_pose_uncertainty']={'position_each_axis_m':.03,'yaw_degrees':4,'status':'uncalibrated stress assumption; contestant gets relative odometry only'}
        if self.practice_mode:
            for r in self.robots.values():r.strategy='external';r.ready=True
        if self.physical_active:self.robots['B-1'].strategy='external'
        self.answers={}
        for r in self.robots.values():
            enemy='RED' if r.camp=='BLUE' else 'BLUE';s='R' if r.camp=='BLUE' else 'B';n=int(r.slot[-1])-1
            self.answers[r.slot]=dict(target=f'{enemy}_{TARGET_TYPES[n]}',search_sectors=sorted([f'{s}-S{n+1}','CM']),base=f'{enemy}_{BASE_TYPES[(n+1)%3]}')
        objective_rng=random.Random(f'm5-v2:{self.seed}')
        self.objectives={camp:('RED' if camp=='BLUE' else 'BLUE')+'_'+objective_rng.choice(BASE_TYPES) for camp in ('BLUE','RED')}
        self.capture_previous_poses={r.slot:(r.x,r.y,r.yaw) for r in self.robots.values()}
        self._event('reset','새 로컬 시뮬레이션을 초기화했습니다')

    def _make_arena(self):
        arena=arena_layout.make_arena(self.seed,self.rule_profile['geometry'])
        arena['environment']['sensor_profile']=copy.deepcopy(self.sensor_profile)
        arena['environment']['profile']=self.sensor_profile['name']
        arena['environment']['environment_profile']=self.sensor_profile['name']
        return arena

    def identity(self):
        return dict(rule_profile=self.rule_profile['id'],environment_profile=self.sensor_profile['name'],sensor_profile=self.sensor_profile['name'],layout_version=arena_layout.LAYOUT_VERSION,seed=self.seed,opponent_mode=self.opponent_mode,opponent_policy_version='sensor-five-roles-v3' if self.opponent_mode=='sensor_limited' else 'privileged-legacy-v1')

    def _event(self,kind,message,slot=None):
        ev=dict(time=round(self.elapsed,3),type=kind,message=message)
        if slot:ev['slot']=slot
        self.events.append(ev)

    def _clear_motion(self):
        for r in self.robots.values():
            r.v=r.w=0.;r.command_at=-999.;r.laser=False;r.beam_until=0.;r.exposure.clear()
            r.pan_goal=r.pan;r.tilt_goal=r.tilt;r.shot_aim=None

    def control(self,data):
        with self.changed:
            action=data.get('action')
            if action=='configure':
                if self.phase not in ('idle','finished'):raise APIError(409,'설정은 초기 대기/종료 단계에서만 변경할 수 있습니다')
                seed=data.get('seed',self.seed)
                if type(seed) is not int or not 0<=seed<2**63:invalid('seed','0 이상 2^63 미만 정수 필요')
                mode=data.get('opponent_mode',self.opponent_mode)
                if mode not in ('sensor_limited','privileged'):invalid('opponent_mode','sensor_limited 또는 privileged')
                try:profile=rules.get_profile(data.get('rule_profile',self.rule_profile['id']))
                except ValueError as exc:invalid('rule_profile',str(exc))
                try:sensor=sensors.get_profile(data.get('sensor_profile',self.sensor_profile['name']))
                except ValueError:invalid('sensor_profile','mild/ideal/stress/official_only 필요')
                self.seed=seed;self.rule_profile=profile;self.sensor_profile=sensor;self.opponent_mode=mode;self.reset()
            elif action=='reset':self.reset()
            elif action=='prepare':
                if self.phase not in ('idle','finished'):raise APIError(409,'초기화한 뒤 준비하세요')
                if self.phase=='finished':self.reset()
                self.phase='ready';self.match_cmd='standby'
                for r in self.robots.values():r.ready=r.strategy!='external' and not r.faults['network']
                self._event('phase','준비: 선택 상대 정책 초기화, 외부 코드는 /ready 제출 필요')
            elif action=='start':
                if self.phase!='ready':raise APIError(409,'준비 단계에서만 시작할 수 있습니다')
                self.phase='running';self.paused=False;self.match_cmd='start';self.started_wall=datetime.now(KST)
                self.order_issued=self.started_wall.strftime('%Y-%m-%d %H:%M:%S')
                for r in self.robots.values():
                    r.included=r.ready;r.origin=(r.x,r.y,r.yaw)
                    if not r.included:self._event('excluded','준비 미완료: 이번 경기 제외 및 0점',r.slot)
                self._event('phase','START · 전술명령 게시 · 제한시간 5분')
            elif action=='pause':
                if not self.practice_mode and self.phase not in ('running','reporting'):raise APIError(409,'진행/보고 단계에서만 일시정지 가능')
                self.paused=True;self.match_cmd='pause';self._clear_motion();self._event('pause','일시정지 · 시계 및 물리 제어 정지')
            elif action=='resume':
                if not self.paused:raise APIError(409,'일시정지 상태가 아닙니다')
                self.paused=False;self.match_cmd='resume' if self.phase=='running' or self.practice_mode else 'report';self._clear_motion();self._event('resume','재개 · 외부 주행/팬틸트 명령 재전송 필요')
            elif action=='finish':
                if self.phase=='running':self._reporting('운영자 경기 종료')
                elif self.phase=='reporting':self._finish()
                else:raise APIError(409,'종료할 진행 경기가 없습니다')
            elif action=='camera':
                for key in ('enabled','test_mode'):
                    if key in data and not isinstance(data[key],bool):invalid(key,'boolean 값 필요')
                if 'enabled' in data:self.camera_enabled=data['enabled']
                if 'test_mode' in data:self.test_mode=data['test_mode']
                self.drone_cache.clear()
            elif action=='difficulty':
                value=data.get('value')
                if type(value) is not int or value not in (0,1,2):invalid('value','난이도는 정수 0, 1, 2')
                if self.phase in ('running','reporting'):raise APIError(409,'경기를 초기화한 뒤 난이도를 변경하세요')
                self.difficulty=value
                for r in self.robots.values():
                    r.route=[];r.route_at=-999.
                    if hasattr(r,'navigation_memory'):del r.navigation_memory
                self._event('difficulty',f'내장 5대 난이도 {value}')
            elif action=='speed':
                if isinstance(data.get('value'),bool) or data.get('value') not in (1,5,20):invalid('value','배속은 1, 5, 20')
                
                if self.physical_active and data['value']!=1:raise APIError(409,'물리/센서 실행은 1배속만 지원합니다')
                self.speed=int(data['value'])
            elif action in ('fault','strategy'):
                r=self.robots.get(data.get('slot'))
                if not r:invalid('slot','B-1~B-3 또는 R-1~R-3')
                if action=='fault':
                    kind=data.get('kind')
                    if kind not in r.faults or not isinstance(data.get('enabled'),bool):invalid('kind','camera/lidar/network 및 enabled boolean 필요')
                    r.faults[kind]=data['enabled']
                    if kind=='network' and data['enabled']:r.v=r.w=0.;r.laser=False;r.exposure.clear()
                    self._event('fault',f'{kind}: {data["enabled"]}',r.slot)
                else:
                    if data.get('strategy') not in STRATEGIES:invalid('strategy','알 수 없는 전략')
                    r.strategy=data['strategy'];r.v=r.w=0.;r.route=[];r.command_at=-999.
                    self._event('strategy',f'전략: {r.strategy}',r.slot)
            else:invalid('action','알 수 없는 운영 명령')
            self.changed.notify_all()
            return {'accepted':True,'phase':self.phase}

    def authenticate(self,code,where='body'):
        if code is None:invalid('mission_code','Field required',where)
        if not isinstance(code,str):invalid('mission_code','8자리 영문/숫자 문자열이어야 합니다',where)
        for r in self.robots.values():
            if r.mission_code.upper()==code.upper():return r
        raise APIError(401,'임무코드가 올바르지 않습니다')

    def me(self,r):return dict(slot=r.slot if r.included else None,camp=r.camp if r.included else None,phase=self.phase,ready=r.ready)

    def order(self,r,kind):
        if getattr(self,'rehearsal_mode',False) and self.phase=='idle':return dict(accepted=False,reason='진행 중인 리허설이 없습니다')
        issued=self.order_issued if kind=='order' else self.target_issued
        if self.order_issued and not r.included:return dict(accepted=False,reason='이 경기에 편성되지 않은 팀입니다')
        if not issued:return dict(accepted=False,reason='임무는 경기 진행이 시작되면 게시됩니다' if kind=='order' else 'M5 목표거점은 종료 60초 전에 게시됩니다')
        if kind=='order':
            ans=self.answers[r.slot];target_label=dict(COMMS='통신시설',OUTPOST='감시초소',RADAR='레이더시설')[ans['target'].split('_',1)[1]]
            command=f'상대 진영 {target_label}({ans["target"]})를 타격하라. 탐색 계획은 {", ".join(ans["search_sectors"])}로 보고하고, 후속 작전 거점은 {ans["base"]}로 설정하라.'
            return dict(accepted=True,order=dict(tactical_command=command,issued_at=issued))
        objective=self.objectives[r.camp]
        return dict(accepted=True,order=dict(objective=objective,objective_label=dict(COMMAND_POST='작전지휘소',MAINTENANCE='정비소',SUPPLY_DEPOT='군수보급소')[objective.split('_',1)[1]],issued_at=issued))

    def _phase_required(self,r,expected,label):
        if self.phase!=expected:raise APIError(409,f"{label}는 '{PHASE_KO[expected]}' 단계에서만 받습니다 (지금은 '{PHASE_KO[self.phase]}')")
        if not r.included:raise APIError(409,'이번 경기에서 빠진 팀입니다 — 준비완료되지 않았습니다')

    def report(self,kind,body):
        with self.changed:
            r=self.authenticate(body.get('mission_code'))
            if r.faults['network']:raise APIError(503,'로컬 네트워크 장애 주입 중')
            if kind=='ready':
                self._phase_required(r,'ready','준비완료');r.ready=True;self._event('ready','준비완료 접수',r.slot);return {'accepted':True}
            expected='reporting' if kind=='situation' else 'running'
            labels={'interpretation':'명령 해석 보고(M1)','detection':'객체 인식 보고(M3)','situation':'상황보고'}
            if kind not in labels:raise APIError(404,'알 수 없는 보고 경로')
            self._phase_required(r,expected,labels[kind])
            if kind=='interpretation':
                if 'sector' in body:invalid('sector','옛 sector 필드 대신 search_sectors를 사용하세요')
                if not any(k in body and body[k] is not None for k in ('target','search_sectors','base')):invalid('body','보고 항목이 하나 이상 필요합니다')
                got=dict(target=None,search_sectors=None,base=None)
                for key,values in [('target',TARGET_TYPES),('base',BASE_TYPES)]:
                    value=body.get(key)
                    if value is not None:
                        if not isinstance(value,str) or value.upper() not in [c+'_'+v for c in ('BLUE','RED') for v in values]:invalid(key,f'{key}는 지정된 시설 보고값으로 적습니다')
                        got[key]=value.upper()
                if body.get('search_sectors') is not None:
                    got['search_sectors']=self._sector_list(body['search_sectors'])
            elif kind=='detection':
                sectors=body.get('sectors')
                if not isinstance(sectors,list) or not 1<=len(sectors)<=7:invalid('sectors','섹터 목록 1~7개가 필요합니다')
                got={'sectors':[]};seen=set()
                for item in sectors:
                    if not isinstance(item,dict):invalid('sectors','섹터 항목은 객체여야 합니다')
                    sector=item.get('sector')
                    if not isinstance(sector,str) or sector.upper() not in SECTORS:invalid('sector','섹터는 이름으로 적습니다: '+', '.join(SECTORS))
                    sector=sector.upper()
                    if sector in seen:invalid('sector','같은 섹터를 두 번 적지 않습니다')
                    seen.add(sector);objects=item.get('objects')
                    if not isinstance(objects,list):invalid('objects','객체 목록이 필요합니다')
                    normalized=[];types=set()
                    for obj in objects:
                        if not isinstance(obj,dict):invalid('objects','객체 항목 형식이 틀렸습니다')
                        typ=obj.get('type');count=obj.get('count')
                        if not isinstance(typ,str) or typ.upper() not in OBJECT_TYPES:invalid('type','허용 객체 종류: '+', '.join(OBJECT_TYPES))
                        typ=typ.upper()
                        if typ in types:invalid('type','같은 종류는 합쳐서 보고하세요')
                        types.add(typ)
                        if isinstance(count,bool) or not isinstance(count,int) or not 0<=count<=99:invalid('count','개수는 0~99 정수여야 합니다')
                        normalized.append(dict(type=typ,count=count))
                    got['sectors'].append(dict(sector=sector,objects=normalized))
                got['sectors'].sort(key=lambda x:x['sector'])
            else:
                ranking=body.get('ranking');prefix='R' if r.camp=='BLUE' else 'B'
                if not isinstance(ranking,list) or not 1<=len(ranking)<=3:invalid('ranking','상대 호출부호를 위험한 순서로 1~3개 적습니다')
                normalized=[]
                for item in ranking:
                    if not isinstance(item,str) or item.upper() not in [f'{prefix}-{i}' for i in (1,2,3)]:invalid('ranking',f'위험도 순위는 상대 진영({prefix}-1~{prefix}-3)만 적습니다')
                    normalized.append(item.upper())
                if len(set(normalized))!=len(normalized):invalid('ranking','같은 UGV를 두 번 적지 않습니다')
                got={'ranking':normalized}
            r.reports[kind]=copy.deepcopy(got)
            self._event('report',f'{kind} 보고 접수 (마지막 제출로 교체)',r.slot)
            if kind=='situation' and all('situation' in x.reports for x in self.robots.values() if x.included):self._finish()
            self.changed.notify_all()
            return dict(accepted=True,received=got)

    def _sector_list(self,value):
        if not isinstance(value,list) or not 1<=len(value)<=7:invalid('search_sectors','섹터 목록 1~7개가 필요합니다')
        result=[]
        for item in value:
            if not isinstance(item,str) or item.upper() not in SECTORS:invalid('search_sectors','섹터는 이름으로 적습니다: '+', '.join(SECTORS))
            result.append(item.upper())
        if len(set(result))!=len(result):invalid('search_sectors','중복 섹터는 허용하지 않습니다')
        return sorted(result)

    def truth_detection(self,r):
        prefix='R' if r.camp=='BLUE' else 'B'
        return [dict(sector=s,objects=[dict(type=k,count=v) for k,v in sorted(Counter(o['type'] for o in self.arena['objects'] if o['sector']==s).items())]) for s in (f'{prefix}-S1',f'{prefix}-S2',f'{prefix}-S3','CM')]

    def score(self,r,include_bonus=True):
        rules.validate_profile(self.rule_profile)
        if self.phase in ('idle','waiting','ready') or self.practice_mode or getattr(self,'rehearsal_mode',False) or not r.included:return dict(m1=0,m2=0,m3=0,m4=0,m5=0,m6=0,bonus=0,total=0)
        ans=self.answers[r.slot];rep=r.reports.get('interpretation',{})
        m1=sum(value for key,value in self.rule_profile['m1'].items() if rep.get(key)==ans[key])
        destroyed={t['id'] for t in self.arena['targets'] if t['destroyed']}
        m2=(self.rule_profile['m2']['assigned'] if ans['target'] in destroyed else 0)+(self.rule_profile['m2']['alliance_all'] if all(self.answers[o.slot]['target'] in destroyed for o in self.robots.values() if o.camp==r.camp) else 0)
        reports={s['sector']:{o['type']:o['count'] for o in s['objects'] if o['count']} for s in r.reports.get('detection',{}).get('sectors',[])}
        m3=0
        for s in self.truth_detection(r):
            truth={o['type']:o['count'] for o in s['objects']}
            if reports.get(s['sector'])==truth:m3+=self.rule_profile['m3']['CM'] if s['sector']=='CM' else self.rule_profile['m3']['sector']
        m4=self.rule_profile['m4'][max(0,min(4,r.hp))]
        m5=self.rule_profile['m5'] if self.winning_capture==r.camp else 0
        m6=0
        ranking=r.reports.get('situation',{}).get('ranking',[])
        if ranking:
            opponents=[o for o in self.robots.values() if o.camp!=r.camp]
            risks={o.slot:max(0,o.hp)/4+max(0,o.ammo)/8+self._m3(o)/30 for o in opponents}
            levels=sorted(risks.values(),reverse=True)
            matches=[abs(risks[slot]-levels[i])<1e-9 for i,slot in enumerate(ranking)]
            m6=self.rule_profile['m6']['all'] if len(matches)==3 and all(matches) else self.rule_profile['m6']['first'] if matches[0] else self.rule_profile['m6']['other'] if any(matches[1:]) else 0
        bonus=self.bonus.get(r.camp,0) if include_bonus else 0
        return dict(m1=m1,m2=m2,m3=m3,m4=m4,m5=m5,m6=m6,bonus=bonus,total=m1+m2+m3+m4+m5+m6+bonus)

    def _m3(self,r):
        reports={s['sector']:{o['type']:o['count'] for o in s['objects'] if o['count']} for s in r.reports.get('detection',{}).get('sectors',[])}
        return sum(self.rule_profile['m3']['CM'] if s['sector']=='CM' else self.rule_profile['m3']['sector'] for s in self.truth_detection(r) if reports.get(s['sector'])=={o['type']:o['count'] for o in s['objects']})

    def _reporting(self,reason):
        self.phase='reporting';self.match_cmd='report';self.paused=False;self.report_elapsed=0.;self._clear_motion()
        self._event('phase',reason+' · REPORT · M6 보고 30초');self.changed.notify_all()

    def _finish(self):
        self.phase='finished';self.match_cmd='stop';self.paused=False;self._clear_motion()
        totals={camp:sum(self.score(r,False)['total'] for r in self.robots.values() if r.camp==camp) for camp in ('BLUE','RED')}
        self.bonus={camp:self.rule_profile['alliance_bonus'] if totals[camp]>=totals['RED' if camp=='BLUE' else 'BLUE'] else 0 for camp in totals}
        self._event('phase','STOP · 보고 마감 · 로컬 잠정 점수 확정');self.changed.notify_all()

    def robot_command(self,r,kind,body):
        with self.lock:
            if r.faults['network']:raise APIError(503,'로컬 네트워크 장애 주입 중')
            if kind=='strategy':
                if body.get('strategy')!='external':invalid('strategy','참가팀은 자기 external 제어만 선택할 수 있습니다')
                r.strategy='external';r.v=r.w=0.;r.route=[];return {'accepted':True}
            allowed=(self.practice_mode or self.phase=='running') and not self.paused and r.included
            if kind=='cmd_vel':
                v=number(body,'linear_x',-.6,.6);w=number(body,'angular_z',-3,3)
                if allowed and self.clock>=r.drive_lock_until:r.v=v;r.w=w;r.command_at=self.clock
                else:r.v=r.w=0.
                return {'accepted':allowed and self.clock>=r.drive_lock_until}
            if kind=='gimbal':
                pan=number(body,'pan',-100,100);tilt=number(body,'tilt',-100,100)
                speed=number(body,'speed',1,270,80);number(body,'acc',1,254,20)
                locked=not allowed or self.clock<r.hit_lock_until
                if not locked:r.pan_goal=pan;r.tilt_goal=tilt;r.gimbal_speed=speed
                return {'message':'locked' if locked else 'ok'}
            if kind=='fire':
                if not allowed or self.clock<r.hit_lock_until:return {'message':'locked','left_ms':max(0,math.ceil((r.hit_lock_until-self.clock)*1000))}
                if r.ammo<=0:return {'message':'empty','left_ms':0}
                left=max(0.,10-(self.clock-r.shot_at))
                if left>1e-8:return {'message':'reloading','left_ms':math.ceil(left*1000)}
                r.ammo-=1;r.shot_at=self.clock;r.beam_until=self.clock+5;r.laser=True;r.exposure.clear()
                self._event('fire','레이저 발사 · 5초 조사',r.slot)
                return {'message':'fired','left_ms':10000}
            if kind=='oled':
                line=body.get('line');text=body.get('text')
                if isinstance(line,bool) or not isinstance(line,int) or line not in (0,1,2,3):invalid('line','line은 0~3')
                if not isinstance(text,str) or len(text)>21 or any(ord(c)<32 or ord(c)>126 for c in text):invalid('text','영문·숫자·기호 최대 21자')
                if self.io_clock-r.oled_at<.5-1e-8:raise APIError(429,'OLED 전송 간격은 0.5초 이상입니다')
                r.oled[line]=text;r.oled_at=self.io_clock;return {'accepted':True}
            raise APIError(404,'알 수 없는 로봇 명령')

    def free(self,x,y,ignore=None,robots=True):
        if not RADIUS<=x<=self.arena['width']-RADIUS or not RADIUS<=y<=self.arena['height']-RADIUS:return False
        for box in self.arena['obstacles']:
            dx=max(abs(x-box['x'])-box['w']/2,0);dy=max(abs(y-box['y'])-box['h']/2,0)
            if dx*dx+dy*dy<RADIUS*RADIUS-1e-10:return False
        if robots:
            for other in self.robots.values():
                if other is not ignore and math.hypot(x-other.x,y-other.y)<2*RADIUS-1e-10:return False
        return True

    def raycast(self,r,heading,max_range=8.,include_targets=True):
        dx,dy=math.cos(heading),math.sin(heading);hits=[]
        # Perimeter is an opaque wall.
        for d in [(self.arena['width']-r.x)/dx if dx>1e-9 else -r.x/dx if dx<-1e-9 else max_range,(self.arena['height']-r.y)/dy if dy>1e-9 else -r.y/dy if dy<-1e-9 else max_range]:
            if d>=0:hits.append((d,'wall',None))
        targets={t['obstacle_id']:t for t in self.arena['targets'] if 'obstacle_id' in t}
        for box in self.arena['obstacles']:
            dist=ray_box(r.x,r.y,dx,dy,box)
            if dist is not None:
                t=targets.get(box['id'])
                hits.append((dist,'target' if t and include_targets else 'wall',t if t and include_targets else box))
        for t in self.arena['targets']:
            if t.get('obstacle_id') in {b['id'] for b in self.arena['obstacles']}:continue
            dist=ray_circle(r.x,r.y,dx,dy,t['x'],t['y'],.2)
            if dist is not None:hits.append((dist,'target',t))
        for other in self.robots.values():
            if other is r:continue
            dist=ray_circle(r.x,r.y,dx,dy,other.x,other.y,RADIUS)
            if dist is not None:hits.append((dist,'robot',other))
        if not hits:return max_range,'none',None
        hit=min(hits,key=lambda h:h[0]);return hit if hit[0]<=max_range else (max_range,'none',None)

    def laser_raycast(self,r,max_range=8.):
        origin,direction=laser_pose(r,self.rule_profile['geometry']);hits=[]
        def add(distance,kind,obj):
            if distance is not None and 0<=distance<=max_range:hits.append((distance,kind,obj))
        for box in self.arena['obstacles']:
            add(ray_aabb(origin,direction,(box['x']-box['w']/2,box['y']-box['h']/2,0),(box['x']+box['w']/2,box['y']+box['h']/2,box.get('height',.4))),'wall',box)
        for target in self.arena['targets']:
            receiver=target.get('receiver')
            if receiver:add(ray_disk(origin,direction,receiver),'target',target)
        for other in self.robots.values():
            if other is r:continue
            add(ray_robot(origin,direction,other,self.rule_profile['geometry']),'wall',other)
            for receiver in robot_receivers(other,self.rule_profile['geometry']):
                add(ray_disk(origin,direction,receiver),'robot',other)
        for axis,bound in [(0,0.),(0,self.arena['width']),(1,0.),(1,self.arena['height'])]:
            if abs(direction[axis])<1e-12:continue
            distance=(bound-origin[axis])/direction[axis]
            z=origin[2]+distance*direction[2]
            if 0<=z<=.4:add(distance,'wall',None)
        if direction[2]<-1e-12:add(-origin[2]/direction[2],'wall',None)
        return min(hits,key=lambda h:h[0]) if hits else (max_range,'none',None)

    def scan(self,r):
        period=.2
        if r.faults['lidar']:
            return dict(ranges=[],angle_min=-math.pi,angle_increment=math.tau/180,range_max=8.,measured_at=None,clock_domain='simulation_seconds',fault='lidar_unavailable')
        if self.physical_active:
            raw=self.physical_scan.get(r.slot)
            if not raw:return dict(ranges=[],angle_min=-math.pi,angle_increment=math.tau/180,range_max=8.,measured_at=None,clock_domain='simulation_seconds',fault='awaiting_physics_measurement')
            measured=raw.get('captured_sim_time',raw.get('measured_at'))
            if measured is None:
                # Old worker measurements have unknown age; never stamp a stale scan as fresh.
                out=copy.deepcopy(raw);out.update(measured_at=None,timestamp_status='unknown legacy worker measurement time');return out
            key=('physical',raw.get('frame_seq'),measured)
        else:
            tick=int((self.clock+1e-9)/period);key=('geometric',tick);measured=self.clock
            raw=None
        cached=self._scan_cache.get(r.slot)
        if cached and cached[0]==key:return copy.deepcopy(cached[1])
        if raw is None:raw=dict(ranges=[self.raycast(r,r.yaw-math.pi+i*math.tau/180,8.)[0] for i in range(180)],angle_min=-math.pi,angle_increment=math.tau/180,range_max=8.)
        result=sensors.scan(raw,self.seed,r.slot,self.sensor_profile,measured)
        result.update(clock_domain='physics_seconds' if self.physical_active else 'simulation_seconds',received_at=round(self.clock,6),received_clock_domain='simulation_seconds')
        self._scan_cache[r.slot]=(key,result)
        return copy.deepcopy(result)

    def robot_state(self,r):
        if r.faults['network']:raise APIError(503,'로컬 네트워크 장애 주입 중')
        ox,oy,oyaw=r.origin;dx,dy=r.x-ox,r.y-oy
        odom=sensors.odometry(dict(x=dx*math.cos(oyaw)+dy*math.sin(oyaw),y=-dx*math.sin(oyaw)+dy*math.cos(oyaw),yaw=angle(r.yaw-oyaw)),self.seed,r.slot,self.clock,self.sensor_profile)
        state=dict(slot=r.slot,mode='practice' if self.practice_mode else 'mission',cmd=self.match_cmd,hp=r.hp,hp_max=4,battery_pct=round(max(0.,95-self.io_clock*self.sensor_profile['battery_pct_per_s']),3),odom=odom,imu=dict(yaw=odom['yaw']),scan=self.scan(r),camera_available=not r.faults['camera'],measured_at=round(self.clock,6),clock_domain='simulation_seconds',sensor_contract=dict(profile=self.sensor_profile['name'],official_mil_state_fields=['hp','hp_max','battery_pct'],scan_status=self.sensor_profile['scan_topic_status'],assumptions=self.sensor_profile['status']))
        state['simulation_extras']=['pan','tilt','ammo','laser'] if self.sensor_profile['extras'] else []
        if self.sensor_profile['extras']:state.update(pan=r.pan,tilt=r.tilt,laser=r.laser,ammo=r.ammo)
        return state

    def snapshot(self):
        with self.lock:
            robots=[]
            for r in self.robots.values():
                robots.append(dict(slot=r.slot,camp=r.camp,x=r.x,y=r.y,yaw=r.yaw,pan=r.pan,tilt=r.tilt,hp=r.hp,ammo=r.ammo,strategy=r.strategy,ready=r.ready,laser=r.laser,beam_range=round(self.laser_raycast(r,8.)[0],4) if r.laser else 0.,score=self.score(r),faults=dict(r.faults),included=r.included,receivers=robot_receivers(r,self.rule_profile['geometry']),policy_label='external contestant' if r.strategy=='external' else 'sensor-limited '+r.strategy+' policy' if self.opponent_mode=='sensor_limited' else 'privileged demonstration bot'))
            return dict(identity=self.identity(),rule_profile=self.rule_profile['id'],environment_profile=self.sensor_profile['name'],sensor_profile=self.sensor_profile['name'],opponent_mode=self.opponent_mode,difficulty=self.difficulty,phase=self.phase,paused=self.paused,elapsed=self.elapsed,report_elapsed=self.report_elapsed,speed=self.speed,robots=robots,arena=copy.deepcopy(self.arena),events=copy.deepcopy(self.events[-120:]),provisional=PROVISIONAL,seed=self.seed,winning_capture=self.winning_capture)

    def export(self):
        return dict(simulation_only=True,schema_version=2,identity=self.identity(),rule_configuration=copy.deepcopy(self.rule_profile),sensor_configuration=copy.deepcopy(self.sensor_profile),source_snapshot='2026-11-04 API + local QA rechecked 2026-11-12',state=self.snapshot(),events=copy.deepcopy(self.events),history=copy.deepcopy(self.history),opponent_policy_log=copy.deepcopy(self.opponent_policy_log),reports={r.slot:copy.deepcopy(r.reports) for r in self.robots.values()},answers=copy.deepcopy(self.answers),objectives=self.objectives)

    def _path(self,r,gx,gy):
        cell=.2;nx=int(self.arena['width']/cell);ny=int(self.arena['height']/cell)
        start=(round(r.x/cell),round(r.y/cell));goal=(round(gx/cell),round(gy/cell))
        queue=[(0,start)];cost={start:0};prev={};end=start;closest=start;closest_distance=math.hypot(r.x-gx,r.y-gy)
        for _ in range(1400):
            if not queue:break
            _,node=heapq.heappop(queue)
            goal_distance=math.hypot(node[0]*cell-gx,node[1]*cell-gy)
            if goal_distance<closest_distance:closest=node;closest_distance=goal_distance
            if goal_distance<.26:closest=node;break
            for ox,oy in [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]:
                nxt=(node[0]+ox,node[1]+oy)
                if not 1<=nxt[0]<nx or not 1<=nxt[1]<ny or not self.free(nxt[0]*cell,nxt[1]*cell,r,True):continue
                # Diagonal edges must not cut inflated solid corners.
                if not self.free((node[0]+ox*.5)*cell,(node[1]+oy*.5)*cell,r,True):continue
                distance=cost[node]+math.hypot(ox,oy)
                if distance<cost.get(nxt,float('inf')):
                    cost[nxt]=distance;prev[nxt]=node;heapq.heappush(queue,(distance+math.hypot(nxt[0]-goal[0],nxt[1]-goal[1]),nxt))
        end=closest;path=[]
        while end!=start and end in prev:path.append((end[0]*cell,end[1]*cell));end=prev[end]
        return list(reversed(path))

    def _bot(self,r):
        if self.opponent_mode=='sensor_limited':
            self._sensor_bot(r);return
        if not r.included or r.faults['network']:return
        # Explicitly privileged synthetic policies: these reports are demonstration fixtures.
        if not self.practice_mode and self.elapsed>2 and 'interpretation' not in r.reports:
            ans=copy.deepcopy(self.answers[r.slot])
            if r.strategy=='hunter':ans['search_sectors']=['CM']
            self.report('interpretation',{'mission_code':r.mission_code,**ans})
        if not self.practice_mode and self.elapsed>15 and self.elapsed-r.bot_report_at>28:
            truth=self.truth_detection(r);n={'scout':4,'balanced':3,'capturer':2,'defender':2,'hunter':1}.get(r.strategy,1)
            self.report('detection',dict(mission_code=r.mission_code,sectors=truth[:n]));r.bot_report_at=self.elapsed
        idx=int(r.slot[-1])-1;enemy='RED' if r.camp=='BLUE' else 'BLUE'
        target=next((t for t in self.arena['targets'] if t['id']==self.answers[r.slot]['target'] and not t['destroyed']),None)
        goal=None;aim=None
        if self.target_issued:
            b=next(b for b in self.arena['bases'] if b['id']==self.objectives[r.camp]);goal=(b['x'],b['y'])
        elif r.strategy=='defender':
            goal=(1.85 if r.camp=='BLUE' else 4.27,4.1-idx*1.6)
            foes=[o for o in self.robots.values() if o.camp==enemy]
            nearest=min(foes,key=lambda o:math.hypot(o.x-r.x,o.y-r.y))
            if math.hypot(nearest.x-r.x,nearest.y-r.y)<3:aim=(nearest.x,nearest.y)
        elif r.strategy=='hunter':
            foes=[o for o in self.robots.values() if o.camp==enemy]
            victim=min(foes,key=lambda o:math.hypot(o.x-r.x,o.y-r.y))
            aim=(victim.x,victim.y);goal=(victim.x+(.6 if r.camp=='RED' else -.6),victim.y)
        elif target and r.strategy in ('balanced','capturer'):
            aim=(target['x'],target['y']);goal=(target['x']+(.8 if r.camp=='RED' else -.8),target['y']-.2)
        else:
            points=[(2.72,4.4),(3.4,3.2),(2.72,1.7),(3.4,.55)]
            if r.strategy=='capturer':points=[(4.9 if r.camp=='BLUE' else 1.2,3.35),(4.9 if r.camp=='BLUE' else 1.2,1.7)]
            goal=points[(r.bot_waypoint+idx)%len(points)]
            if math.hypot(r.x-goal[0],r.y-goal[1])<.3:r.bot_waypoint+=1
            if target:aim=(target['x'],target['y'])
        if self.difficulty>=2:
            from opponents import objective
            goal,aim=objective(self,r,goal,aim)
        if r.laser and r.shot_aim:aim=r.shot_aim;goal=None
        if aim and self.clock>=r.hit_lock_until:
            bearing=math.atan2(aim[1]-r.y,aim[0]-r.x);relative=angle(bearing-r.yaw)
            r.pan_goal=max(-100,min(100,-math.degrees(relative)/.5));r.tilt_goal=0
            actual=r.yaw-math.radians(r.pan*.5);distance,kind,obj=self.raycast(r,actual)
            aligned=abs(angle(bearing-actual))<.025 and abs(r.tilt)<3
            legitimate=(kind=='robot' and obj.camp!=r.camp) or (kind=='target' and not obj['destroyed'])
            if self.difficulty>=1 and kind=='target' and obj['camp']==r.camp:legitimate=False
            if aligned and legitimate:
                response=self.robot_command(r,'fire',{})
                if response['message']=='fired':r.shot_aim=aim;goal=None
        if r.laser:r.v=r.w=0;r.command_at=self.clock;return
        if not goal:r.v=r.w=0;return
        if self.difficulty>=1:
            from opponents import navigate
            navigate(self,r,goal);return
        if self.clock-r.route_at>2.5 or not r.route or math.hypot(goal[0]-(r.route_goal or goal)[0],goal[1]-(r.route_goal or goal)[1])>.4:
            r.route=self._path(r,*goal);r.route_at=self.clock;r.route_goal=goal
        while r.route and math.hypot(r.route[0][0]-r.x,r.route[0][1]-r.y)<.13:r.route.pop(0)
        gx,gy=r.route[0] if r.route else goal
        error=angle(math.atan2(gy-r.y,gx-r.x)-r.yaw)
        r.w=max(-2.2,min(2.2,error*3));r.v=(.36 if r.strategy=='scout' else .25 if r.strategy=='defender' else .3)*max(0,math.cos(error))
        if math.hypot(gx-r.x,gy-r.y)<.1:r.v=0
        # Local avoidance changes angular command while preserving collision authority.
        if not self.free(r.x+math.cos(r.yaw)*.18,r.y+math.sin(r.yaw)*.18,r):
            r.v=0
            if abs(error)<.2:
                r.w=1.2 if idx%2==0 else -1.2
                r.route_at=-999.
        r.command_at=self.clock

    def step(self,dt=DT):
        rules.validate_profile(self.rule_profile)
        if dt<=0 or dt>.051:raise ValueError('step must be in (0, 0.05]; server uses fixed 20Hz')
        with self.changed:
            self.io_clock+=dt
            for robot in self.robots.values():
                if self.io_clock-robot.oled_at>=10-1e-8:robot.oled.clear()
            if self.paused or (not self.practice_mode and self.phase not in ('running','reporting')):return
            self.clock+=dt
            if self.phase=='reporting':
                self.report_elapsed+=dt
                for r in list(self.robots.values()):
                    if self.phase!='reporting':break
                    if self.opponent_mode=='privileged' and r.strategy!='external' and r.included and not r.faults['network'] and 'situation' not in r.reports and self.report_elapsed>=3+int(r.slot[-1]):
                        opponents=[o for o in self.robots.values() if o.camp!=r.camp]
                        opponents.sort(key=lambda o:-(max(0,o.hp)/4+max(0,o.ammo)/8+self._m3(o)/30))
                        self.report('situation',dict(mission_code=r.mission_code,ranking=[o.slot for o in opponents]))
                if self.phase=='reporting' and self.report_elapsed>=self.rule_profile['report_duration']-1e-8:self._finish()
                return
            before_poses=self.capture_previous_poses if self.physical_active else {r.slot:(r.x,r.y,r.yaw) for r in self.robots.values()}
            self.elapsed+=dt
            if not self.practice_mode and not self.target_issued and self.elapsed>=self.rule_profile['m5_publish_at']-1e-8:
                self.target_issued=datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
                self.sensor_occupied={b['id']:self._occupied(b) for b in self.arena['bases']}
                self._event('target','M5 목표거점 게시 · 기존 점유 불인정');self.changed.notify_all()
            for r in self.robots.values():
                if self.practice_mode:
                    if self.clock>=r.hit_lock_until-1e-8:r.hp=4
                    if self.clock-r.shot_at>=10-1e-8:r.ammo=8
                if r.strategy!='external':self._bot(r)
                if not r.included or r.faults['network'] or self.clock-r.command_at>.5+1e-8 or self.clock<r.drive_lock_until:r.v=r.w=0
                if not self.physical_active:
                    r.yaw=angle(r.yaw+r.w*dt)
                    nx,ny=r.x+r.v*math.cos(r.yaw)*dt,r.y+r.v*math.sin(r.yaw)*dt
                    if self.free(nx,ny,r):r.x,r.y=nx,ny
                    if self.clock>=r.hit_lock_until:
                        r.pan+=max(-r.gimbal_speed*dt/.5,min(r.gimbal_speed*dt/.5,r.pan_goal-r.pan))
                        r.tilt+=max(-r.gimbal_speed*dt/.15,min(r.gimbal_speed*dt/.15,r.tilt_goal-r.tilt))
                if self.clock>=r.beam_until-1e-8:r.laser=False;r.exposure.clear();r.shot_aim=None
            for r in self.robots.values():
                if not r.laser:continue
                distance,kind,obj=self.laser_raycast(r,8.)
                valid=kind in ('target','robot')
                key=(kind,obj['id'] if kind=='target' else obj.slot) if valid else None
                if not valid:r.exposure.clear();continue
                r.exposure={key:r.exposure.get(key,0.)+dt}
                if r.exposure[key]<2-1e-8:continue
                if kind=='target' and not obj['destroyed']:
                    obj['destroyed']=True;self._event('target_hit',f'{obj["id"]} 유효타격',r.slot)
                elif kind=='robot' and self.clock>=obj.invulnerable_until-1e-8:
                    obj.hp=max(0,obj.hp-1);obj.invulnerable_until=self.clock+10;obj.hit_lock_until=self.clock+10
                    if obj.hp==0:obj.drive_lock_until=self.clock+10;obj.v=obj.w=0
                    obj.laser=False;obj.exposure.clear();obj.pan_goal=obj.pan;obj.tilt_goal=obj.tilt
                    self._event('hit',f'{obj.slot} 피격 · HP {obj.hp}/4 · 10초 무적/조준·발사 잠금',r.slot)
                r.exposure[key]=0.
            if self.target_issued:
                self._capture_crossings(before_poses,dt)
            self.capture_previous_poses={r.slot:(r.x,r.y,r.yaw) for r in self.robots.values()}
            if not self.practice_mode and self.phase=='running' and self.elapsed>=self.rule_profile['duration']-1e-8:
                self.elapsed=self.rule_profile['duration'];self._reporting('5분 제한시간 종료')
            if int((self.elapsed-dt)*2)!=int(self.elapsed*2):
                self.history.append(dict(time=round(self.elapsed,3),robots=[dict(slot=r.slot,x=round(r.x,4),y=round(r.y,4),yaw=round(r.yaw,4),hp=r.hp,ammo=r.ammo) for r in self.robots.values()]))

    def _line(self,b):
        return b.get('sensor_line',dict(x=b['x']+(b['w']/2 if b['camp']=='BLUE' else -b['w']/2),y_min=b['y']-b['h']/2,y_max=b['y']+b['h']/2))

    def _occupied(self,b):
        return any(r.included and intersects_line((r.x,r.y,r.yaw),self._line(b),self.rule_profile['geometry']) for r in self.robots.values())

    def _capture_crossings(self,before_poses,dt):
        events=[]
        for base in self.arena['bases']:
            was=self.sensor_occupied.get(base['id'],False)
            self.sensor_occupied[base['id']]=self._occupied(base)
            if was:continue
            camp=next((c for c,objective in self.objectives.items() if objective==base['id']),None)
            if camp is None:continue
            for robot in self.robots.values():
                if not robot.included or robot.slot not in before_poses:continue
                fraction=crossing_fraction(before_poses[robot.slot],(robot.x,robot.y,robot.yaw),self._line(base),self.rule_profile['geometry'])
                if fraction is not None:
                    event_time=self.elapsed-dt+fraction*dt
                    if self.rule_profile['m5_publish_at']<=event_time<self.rule_profile['duration']:
                        events.append((event_time,camp,base['id'],robot.slot))
        if events:
            earliest=min(event[0] for event in events)
            event=min((ev for ev in events if ev[0]-earliest<=1e-8),key=lambda ev:(ev[1],ev[2],ev[3]))
            when,camp,base_id,slot=event;self.winning_capture=camp
            self.events.append(dict(time=round(when,9),type='capture',message=f'{camp} 목표거점 {base_id} 신규 진입',slot=slot,base=base_id,event_time=when,tie_policy=self.rule_profile['capture_tie_policy']))
            self._reporting('M5 점령으로 조기 종료')

    def _sensor_bot(self,r):
        if not r.included or r.faults['network']:return
        from opponents import sensor_policy
        if self.clock-getattr(r,'sensor_policy_at',-999.)<.2-1e-9:return
        r.sensor_policy_at=self.clock
        observation=self.robot_state(r)
        order=self.order(r,'order')
        order['target']=self.order(r,'target')
        policy=getattr(r,'sensor_policy_memory',{})
        commands,policy=sensor_policy(observation,order,policy,r.strategy)
        r.sensor_policy_memory=policy
        self.opponent_policy_log.append(dict(time=round(self.clock,6),slot=r.slot,policy_version='sensor-five-roles-v3',observation=copy.deepcopy(observation),own_order=copy.deepcopy(order),role=r.strategy,decision=copy.deepcopy(policy),commands=copy.deepcopy(commands)))
        for kind,body in commands:
            self.robot_command(r,kind,body)
