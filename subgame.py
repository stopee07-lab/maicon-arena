"""Separate seat-selection practice. 60 s / two trials, unmeasured receiver geometry."""
import math,copy,json,uuid,os,tempfile
from pathlib import Path
from engine import APIError
class Trial:
 def __init__(self):self.attempts=[];self.started=None;self.status='idle';self.elapsed=0
 def arm(self,now):
  if self.status in ('armed','running'):raise ValueError('현재 시도를 먼저 종료하세요')
  if len(self.attempts)>=2:raise ValueError('두 번의 시도를 마쳤습니다. 새 연습 묶음을 만드세요')
  self.started=None;self.status='armed';self.elapsed=0
 def command(self,kind,body,now):
  moves=(kind=='cmd_vel' and any(abs(body.get(k,0))>1e-6 for k in ('linear_x','angular_z'))) or (kind=='gimbal' and any(abs(body.get(k,0))>1e-6 for k in ('pan','tilt')))
  if self.status=='armed' and moves:self.started=now;self.status='running'
 def update(self,now,hit):
  if self.status=='running':
   self.elapsed=max(0,now-self.started)
   if hit and self.elapsed<=60+1e-8:self.finish('success')
   elif self.elapsed>=60:self.finish('timeout')
  return self.snapshot()
 def finish(self,status):
  if self.status in ('armed','running'):
   self.status=status;self.attempts.append(dict(status=status,elapsed=round(self.elapsed,3)))
 def snapshot(self):return dict(status=self.status,elapsed=round(self.elapsed,3),attempt=len(self.attempts)+(self.status in ('armed','running')),attempts=copy.deepcopy(self.attempts),limit=60,max_attempts=2)
class Subgame:
 def __init__(self,server,root):self.server=server;self.root=Path(root);self.trial=Trial();self.identifier=None;self.epoch=None
 def active(self):return self.trial.status in ('armed','running')
 def begin(self,new=False,ticket=None):
  s=self.server
  if self.active():raise APIError(409,'현재 서브게임을 먼저 종료하세요')
  if any(e.phase in ('ready','running','reporting') for e in (s.engine,s.rehearsal)):raise APIError(409,'본 경기를 종료·초기화하세요')
  if s.workbench and s.workbench.controller_process and s.workbench.controller_process.poll() is None:raise APIError(409,'참가 코드를 먼저 정지하세요')
  s.require_no_ros()
  if new or self.identifier is None:self.trial=Trial();self.identifier='subgame-'+uuid.uuid4().hex[:12]
  try:self.trial.arm(0)
  except ValueError as ex:raise APIError(409,str(ex))
  s.physics.stop();e=s.practice
  if ticket is not None:
   try:s.check_execution_ticket(ticket)
   except APIError:self.trial.finish('aborted');self.save();raise
  with e.lock:
   e.reset();r=e.robots['B-1'];r.x=3.06;r.y=1.;r.yaw=-math.pi/2;r.origin=(r.x,r.y,r.yaw)
   for robot in e.robots.values():robot.strategy='external'
   target=copy.deepcopy(next(t for t in e.arena['targets'] if t['id']=='RED_RADAR'))
   target.update(x=3.06,y=4.2,normal=[0,-1],destroyed=False)
   target['receiver'].update(x=3.06,y=3.995,normal=[0,-1,0])
   e.arena['targets']=[target];e.arena['objects']=[];e.arena['obstacles']=[dict(id='RED_RADAR',x=3.06,y=4.2,w=.4,h=.4,height=.4,kind='target')]
   e.arena['environment'].update(subgame=True,layout_version='subgame-3m-v1');self.epoch=e.reset_epoch
  try:
   if ticket is not None:s.check_execution_ticket(ticket)
   s.physics.start('practice')
   if ticket is not None:s.check_execution_ticket(ticket)
  except Exception:
   self.trial.finish('aborted');s.physics.stop()
   with e.lock:e.paused=True;e._clear_motion()
   self.save();raise
  self.save();return self.snapshot()
 def command(self,e,r,kind,body,result):
  if e is self.server.practice and r.slot=='B-1' and self.active() and (result.get('accepted') or result.get('message')=='ok'):
   self.trial.command(kind,body,e.clock)
 def tick(self):
  if not self.active():return
  e=self.server.practice
  with e.lock:
   if self.epoch!=e.reset_epoch:self.trial.finish('aborted')
   else:self.trial.update(e.clock,any(t['destroyed'] for t in e.arena['targets']))
   if not self.active():e.paused=True;e._clear_motion();self.save()
 def stop(self):
  e=self.server.practice
  with e.lock:self.trial.finish('aborted');e.paused=True;e._clear_motion()
  self.save();return self.snapshot()
 def save(self):
  if self.identifier:
   p=self.root/'data/subgames';p.mkdir(parents=True,exist_ok=True)
   fd,tmp=tempfile.mkstemp(dir=p,prefix='.save-')
   with os.fdopen(fd,'w') as f:json.dump(self.snapshot(),f,ensure_ascii=False,indent=2)
   os.replace(tmp,p/(self.identifier+'.json'))
 def snapshot(self):return dict(id=self.identifier,**self.trial.snapshot(),rule='교육1 p27: 180도 반대 출발, 약3m 레이더시설, 첫 주행/팬틸트 명령부터60초,2회',assumption='수광부 실측치 미확인; 본 경기의 동일한2초연속 ray 판정 사용',identity=self.server.practice.identity())
