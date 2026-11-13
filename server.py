#!/usr/bin/env python3
"""Local stdlib HTTP runtime. Official-style API + isolated simulation adapter."""
from __future__ import annotations
import argparse
import hmac
import io
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import secrets
import signal
import string
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse
from datetime import datetime
from PIL import Image, ImageDraw
from engine import Engine, APIError, DT, KST, invalid

ROOT=Path(__file__).resolve().parent
VERSION='maicon-fidelity/3.0'
import fidelity_settings


def load_access(path):
    path=Path(path)
    if path.exists():
        access=json.loads(path.read_text())
        if not all(k in access for k in ('admin_key','viewer_key','teams')):raise ValueError('invalid local access.json')
        if len(access['teams'])!=6:raise ValueError('access.json requires six teams')
        return access
    alphabet=string.ascii_uppercase+string.digits
    access={'admin_key':secrets.token_urlsafe(24),'viewer_key':secrets.token_urlsafe(24),'teams':[dict(slot=slot,mission_code=''.join(secrets.choice(alphabet) for _ in range(8))) for slot in ('B-1','B-2','B-3','R-1','R-2','R-3')]}
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(access,f,indent=2)
    return access


def fallback_camera(engine,r):
    im=Image.new('RGB',(640,480),(24,30,39));draw=ImageDraw.Draw(im)
    draw.rectangle((0,240,639,479),fill=(80,82,74))
    draw.text((14,14),'SYNTHETIC SCHEMATIC - NOT REAL SENSOR',fill=(240,220,100))
    draw.text((14,35),r.slot+' | fallback ray-view',fill='white')
    import math
    for x in range(0,640,4):
        heading=r.yaw-math.radians(r.pan*.5)+(320-x)/320*math.radians(45)
        dist,kind,obj=engine.raycast(r,heading)
        size=min(340,int(150/max(.1,dist)))
        color=(90,110,125) if kind=='wall' else (215,140,75) if kind=='target' else (70,130,210) if obj and getattr(obj,'camp',None)=='BLUE' else (210,75,85)
        draw.rectangle((x,240-size/2,x+3,240+size/2),fill=color)
    return im,[]


def fallback_drone(engine):
    im=Image.new('RGB',(800,650),(220,224,219));draw=ImageDraw.Draw(im)
    def xy(x,y):return int(30+x*120),int(620-y*120)
    for b in engine.arena['obstacles']:
        x,y=xy(b['x']-b['w']/2,b['y']+b['h']/2);x2,y2=xy(b['x']+b['w']/2,b['y']-b['h']/2)
        draw.rectangle((x,y,x2,y2),fill=(86,91,92))
    for r in engine.robots.values():
        x,y=xy(r.x,r.y);color=(40,110,220) if r.camp=='BLUE' else (220,65,70)
        draw.ellipse((x-12,y-12,x+12,y+12),fill=color)
    draw.text((20,10),'SYNTHETIC DRONE - no hidden-state overlays',fill=(40,40,40))
    return im


def jpeg(image):
    buf=io.BytesIO();image.convert('RGB').save(buf,'JPEG',quality=83);return buf.getvalue()


class SimulationHTTPServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self,address,access,engine=None,*,enable_workbench=False):
        self.stopping=threading.Event()
        super().__init__(address,Handler)
        self.access=access;self.engine=engine or Engine(teams=access['teams']);self.stopping=threading.Event()
        self.local_bind=address[0] in ('127.0.0.1','localhost','::1')
        self.tick_error=None
        self.rehearsal=Engine(teams=access['teams']);self.rehearsal.rehearsal_mode=True
        self.practice=Engine(teams=access['teams'],practice_mode=True)
        self.rehearsal_start_at=None
        try:
            import camera
            self.camera=camera
        except ImportError:self.camera=None
        from physics import Physics
        from workbench import Workbench
        self.physics=Physics(self,ROOT)
        self.workbench=Workbench(ROOT,self.physics.capture_frame,lambda:dict(physics=self.physics.status(),identity=(self.physics.engine or self.engine).identity(),server_url=f'http://127.0.0.1:{self.server_port}')) if enable_workbench else None
        self.development=None;self.scenarios=None;self.notebook=None;self.ros_notebook=None;self.recordings=None
        from subgame import Subgame
        self.subgame=Subgame(self,ROOT)
        from experiment_journal import ExperimentJournal
        self.experiment_journal=ExperimentJournal(self,ROOT)
        self.execution_lock=threading.RLock()
        self.execution_cancel=threading.Event()
        self.execution_request_lock=threading.RLock();self.execution_generation=0
        if self.workbench:
            from development import Development
            from scenarios import Scenarios
            from notebook import Notebook
            from ros_notebook import RosNotebook
            from recordings import RecordingStore
            self.development=Development(ROOT,self.workbench)
            self.scenarios=Scenarios(ROOT,self)
            self.notebook=Notebook(ROOT,port=8907)
            self.ros_notebook=RosNotebook(ROOT,server_url=f'http://127.0.0.1:{self.server_port}',port=8906)
            self.recordings=RecordingStore(ROOT)

    def server_close(self):
        self.stopping.set()
        if getattr(self,'development',None):self.development.close()
        if getattr(self,'notebook',None):self.notebook.close()
        if getattr(self,'ros_notebook',None):self.ros_notebook.close()
        if getattr(self,'workbench',None):self.workbench.close()
        if hasattr(self,'physics'):self.physics.stop()
        super().server_close()

    def require_no_ros(self):
        notebook=getattr(self,'ros_notebook',None)
        state=notebook.status() if notebook else {}
        if state.get('running') or state.get('starting'):
            raise APIError(409,'ROS 실습에서 로봇을 제어하고 있습니다. ROS 실습 종료 후 코드 실행·장면 복원·환경 변경을 진행하세요')

    def execution_snapshot(self):
        """Compare the editable workspace with the bytes actually launched."""
        wb=self.workbench;source=wb.code_store.state();deployment=wb.deployment or {};controller=wb.controller_snapshot()
        running=bool(wb.controller_process and wb.controller_process.poll() is None)
        return dict(workspace_sha256=source['workspace_sha256'],controller_running=running,
            deployment_code_sha256=deployment.get('code_sha256'),running_code_sha256=controller.get('code_sha256') if running else None,
            workspace_matches_deployment=source['workspace_sha256']==deployment.get('code_sha256'),
            workspace_matches_running=bool(running and source['workspace_sha256']==controller.get('code_sha256')),
            deployment_version_id=deployment.get('code_version_id'),running_version_id=controller.get('code_version_id') if running else None,
            runtime=self.physics.status().get('runtime'),controller=controller,deployment=deployment)

    def _pause_all_runtimes(self):
        self.rehearsal_start_at=None
        for engine in (self.engine,self.rehearsal,self.practice):
            with engine.changed:
                engine.paused=True;engine.match_cmd='pause';engine._clear_motion();engine.changed.notify_all()

    def _stop_execution(self):
        self.experiment_journal.observe('user_stop')
        self._pause_all_runtimes()
        if self.subgame.active():self.subgame.stop()
        failures=[]
        for label,callback in [('참가 코드',self.workbench.stop_controller if self.workbench else None),
                ('ROS 실습',self.ros_notebook.close if self.ros_notebook else None),('물리 시뮬레이터',self.physics.stop)]:
            if callback:
                try:callback()
                except Exception:failures.append(label)
        # A final pause also clears a command that arrived while its process exited.
        self._pause_all_runtimes()
        if failures:raise APIError(503,'경기장은 일시정지했지만 다음 프로세스 종료를 확인하지 못했습니다: '+', '.join(failures))
        return dict(stopped=True,controller=self.workbench.controller_snapshot() if self.workbench else None,
            physics=self.physics.status(),runtimes={name:dict(paused=e.paused,phase=e.phase) for name,e in
                (('practice',self.practice),('mission',self.engine),('rehearsal',self.rehearsal))})

    def stop_execution(self):
        # Cancellation is signalled before waiting for an in-progress launch lock.
        with self.execution_request_lock:
            self.execution_generation+=1;self.execution_cancel.set()
        self._pause_all_runtimes()
        with self.execution_lock:return self._stop_execution()

    def execution_ticket(self):
        with self.execution_request_lock:return self.execution_generation

    def check_execution_ticket(self,ticket):
        with self.execution_request_lock:
            if ticket!=self.execution_generation or self.stopping.is_set():
                raise APIError(409,'중지 요청 이전에 대기하던 실행을 취소했습니다. 다시 실행하려면 실행 버튼을 누르세요')

    def apply_and_run(self,body):
        """One explicit action snapshots, deploys and verifies the chosen code."""
        ticket=self.execution_ticket()
        with self.execution_lock:
            self.check_execution_ticket(ticket)
            self.execution_cancel.clear()
            wb=self.workbench;mode=body.get('mode','practice');policy=body.get('policy','mission');slot=body.get('slot','B-1')
            if slot not in self.engine.robots:raise APIError(422,'Invalid UGV slot')
            if mode not in ('practice','match','rehearsal') or policy not in ('mission','observe'):
                raise APIError(422,'mode: practice/match/rehearsal, policy: mission/observe')
            model_id=body.get('model_id') or (wb.deployment or {}).get('model_id')
            model_path,model=wb._model(model_id)
            from workbench import digest
            if digest((model_path/'weights.pt').read_bytes())!=model['weights_sha256']:
                raise APIError(409,'선택한 모델 파일이 변경되어 실행할 수 없습니다')
            # Freeze valid input before disturbing any currently running process.
            with wb.code_store.lock:
                if body.get('version_id'):
                    _,version=wb.code_store.version(body['version_id'])
                else:
                    version=wb.code_store.snapshot(body.get('name') or '저장한 코드 적용·실행',expected_sha256=body.get('base_sha256'))
            runtime='mission' if mode=='match' else mode
            engine={'practice':self.practice,'mission':self.engine,'rehearsal':self.rehearsal}[runtime]
            self.check_execution_ticket(ticket)
            self._stop_execution()
            try:
                self.check_execution_ticket(ticket)
                if runtime!='practice':
                    engine.control({'action':'reset'})
                    if slot!='B-1':engine.control({'action':'strategy','slot':'B-1','strategy':'balanced'})
                    engine.control({'action':'strategy','slot':slot,'strategy':'external'})
                    engine.control({'action':'prepare'})
                else:
                    engine.control({'action':'strategy','slot':slot,'strategy':'external'})
                if runtime!='mission':self.engine.control({'action':'camera','enabled':True,'test_mode':True})
                deployment=wb.deploy(dict(model_id=model_id,slot=slot,mode=mode,policy=policy,code_version_id=version['id']))
                self.physics.start(runtime,controlled_slot=slot)
                with engine.changed:
                    engine.paused=True;engine.match_cmd='pause';engine._clear_motion();engine.changed.notify_all()
                deadline=time.monotonic()+12
                while not self.physics.status().get('connected'):
                    self.check_execution_ticket(ticket)
                    if time.monotonic()>=deadline:raise APIError(503,'물리 경기장 연결 실패. 코드 실행을 중지했습니다')
                    time.sleep(.1)
                self.check_execution_ticket(ticket)
                wb.start_controller(dict(expected_code_version_id=version['id'],expected_runtime=runtime,expected_deployment_id=deployment['id']))
                deadline=time.monotonic()+30
                while True:
                    self.check_execution_ticket(ticket)
                    controller=wb.controller_snapshot()
                    if controller.get('loaded') and controller.get('code_integrity') and (runtime=='practice' or engine.robots[slot].ready):break
                    if controller.get('status') in ('failed','exited','stopped','interrupted'):
                        raise APIError(409,'선택한 코드 실행 실패: '+str(controller.get('error') or '프로세스 로그를 확인하세요'))
                    if time.monotonic()>=deadline:
                        raise APIError(503,'모델 로드 또는 참가 코드 준비 응답을 확인하지 못했습니다. 실행을 중지했습니다. 로그를 확인하세요')
                    time.sleep(.1)
                with self.execution_request_lock:
                    self.check_execution_ticket(ticket)
                    if runtime=='practice':engine.control({'action':'resume'})
                    else:self.control_runtime(engine,{'action':'start'})
                return dict(started=True,runtime=runtime,phase=engine.phase,deployment=deployment,
                    controller=wb.controller_snapshot(),physics=self.physics.status(),code=version,execution=self.execution_snapshot())
            except Exception:
                self._stop_execution()
                raise

    def control_ros_notebook(self,body):
        with self.execution_lock:
            action=body.get('action','start')
            if action=='stop':
                self.ros_notebook.close();return self.ros_notebook.status()
            if action!='start':raise APIError(422,'action: start 또는 stop')
            process=self.workbench.controller_process
            if process and process.poll() is None:
                raise APIError(409,'실행 중인 참가 코드가 있습니다. 로봇 프로세스를 정지한 뒤 ROS 실습을 여세요')
            physics=self.physics.status()
            if not physics.get('connected') or physics.get('runtime')!='practice':
                if physics.get('connected') and physics.get('runtime')!='practice':
                    raise APIError(409,'ROS 실습은 자유연습에서 실행합니다. 실행 환경을 자유연습으로 바꾸세요')
                self.physics.start('practice')
                deadline=time.monotonic()+12
                while not self.physics.status().get('connected') and time.monotonic()<deadline:time.sleep(.1)
                if not self.physics.status().get('connected'):raise APIError(503,'물리 경기장 연결에 실패했습니다')
            if self.practice.paused:self.practice.control({'action':'resume'})
            return self.ros_notebook.start()

    def control_runtime(self, engine, body):
        if engine is self.rehearsal:
            with engine.lock:
                action=body.get('action')
                if action=='start':
                    if engine.phase!='ready':raise APIError(409,'리허설 준비 단계에서만 시작할 수 있습니다')
                    if self.rehearsal_start_at is not None:raise APIError(409,'리허설 시작 대기 중입니다')
                    self.rehearsal_start_at=time.monotonic()+5
                    engine._event('countdown','리허설은 5초 후 시작합니다')
                    return dict(accepted=True,phase=engine.phase,countdown_remaining=5)
                if action in ('reset','prepare'):self.rehearsal_start_at=None
        return engine.control(body)

    def runtime_snapshot(self, engine):
        state=engine.snapshot()
        state['runtime']='practice' if engine is self.practice else 'rehearsal' if engine is self.rehearsal else 'mission'
        if engine is self.rehearsal:state['countdown_remaining']=max(0,self.rehearsal_start_at-time.monotonic()) if self.rehearsal_start_at is not None else 0
        if engine is self.practice:state['provisional']=state['provisional']+['자유연습 HP/탄약 자동 회복은 잠금/재발사 10초 종료 시점으로 가정했습니다. 실제 회복 시점은 현장 확인 대상입니다.']
        return state

    def tick_once(self):
        try:self.experiment_journal.observe()
        except Exception as exc:self.tick_error='experiment journal: '+type(exc).__name__
        self.subgame.tick()
        if getattr(self,'scenarios',None):self.scenarios.observe()
        if self.rehearsal_start_at is not None and time.monotonic()>=self.rehearsal_start_at:
            with self.rehearsal.lock:
                self.rehearsal_start_at=None
                if self.rehearsal.phase=='ready':self.rehearsal.control({'action':'start'})
        for name,runtime in (('mission',self.engine),('rehearsal',self.rehearsal),('practice',self.practice)):
            try:
                if runtime.physical_active:
                    if runtime is self.physics.engine:
                        with runtime.changed:
                            last=self.physics.last or self.physics.started_at
                            timeout=2 if self.physics.last else 5
                            if last and time.monotonic()-last>timeout and not runtime.paused:
                                runtime.paused=True;runtime.match_cmd='pause';runtime._clear_motion()
                                runtime._event('physics_disconnected','물리 엔진 연결 끊김 · 일시정지 · 연결 복구 후 명시적 재개 필요')
                                runtime.changed.notify_all()
                    continue
                for _ in range(runtime.speed):runtime.step(DT)
            except Exception as exc:
                self.tick_error=f'{name}: {exc!r}'
                with runtime.lock:
                    runtime.paused=True;runtime._clear_motion()
                    runtime._event('runtime_error','Engine paused: '+type(exc).__name__)
                print('Simulation engine error:',name,repr(exc),flush=True)

    def start_engine(self):
        def run():
            deadline=time.monotonic()
            while not self.stopping.is_set():
                deadline+=DT
                self.tick_once()
                self.stopping.wait(max(0,deadline-time.monotonic()))
                if time.monotonic()-deadline>1:deadline=time.monotonic()
        thread=threading.Thread(target=run,name='maicon-20hz',daemon=True);thread.start();return thread


class Handler(BaseHTTPRequestHandler):
    server_version=VERSION
    protocol_version='HTTP/1.1'

    def log_message(self,fmt,*args):
        # Never print mission codes or credentials in URL query parameters.
        print(f'{self.client_address[0]} {self.command} {urlparse(self.path).path} {args[1] if len(args)>1 else ""}',flush=True)

    def _send(self,status,data,content_type='application/json; charset=utf-8',headers=None):
        if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Simulation-Only','true');self.send_header('X-Simulator-Version',VERSION)
        for k,v in (headers or {}).items():self.send_header(k,str(v))
        self.end_headers()
        if self.command!='HEAD':self.wfile.write(data)

    def _auth(self,viewer=False):
        header='X-Viewer-Key' if viewer else 'X-Admin-Key';key='viewer_key' if viewer else 'admin_key'
        actual=self.headers.get(header,'')
        if not actual or not hmac.compare_digest(actual,self.server.access[key]):raise APIError(401,'운영자/관전자 인증키가 올바르지 않습니다')

    def _local_request(self):
        host=urlparse('//'+self.headers.get('Host','')).hostname
        origin=self.headers.get('Origin')
        try:client=ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:client=False
        if not self.server.local_bind or not client or host not in ('localhost','127.0.0.1','::1'):return False
        if origin:
            parsed=urlparse(origin)
            if parsed.hostname not in ('localhost','127.0.0.1','::1') or parsed.port!=self.server.server_port:return False
        return True

    def _body(self):
        if self.headers.get('Transfer-Encoding'):raise APIError(400,'Chunked request bodies are not supported')
        try:length=int(self.headers.get('Content-Length','0'))
        except ValueError:raise APIError(400,'Invalid Content-Length')
        limit=32*1024*1024 if urlparse(self.path).path in ('/api/workbench/datasets/import','/api/physics/exchange') else 1048576
        if not 0<length<=limit:raise APIError(413,f'JSON body exceeds {limit} byte limit')
        if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':raise APIError(415,'Content-Type: application/json이 필요합니다')
        try:body=json.loads(self.rfile.read(length),parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite JSON')))
        except (ValueError,UnicodeError):invalid('body','올바른 JSON이 필요합니다')
        if not isinstance(body,dict):invalid('body','JSON 객체가 필요합니다')
        return body

    def do_GET(self):self._dispatch(False)
    def do_POST(self):self._dispatch(True)
    def do_HEAD(self):self._dispatch(False)
    def do_OPTIONS(self):self._send(405,{'detail':'Cross-origin API access is disabled; use this local origin or a local Python client'})

    def _dispatch(self,post):
        try:
            parsed=urlparse(self.path);path=parsed.path.rstrip('/') or '/';query=parse_qs(parsed.query)
            args={k:v[-1] for k,v in query.items()};e=self.server.engine
            if path=='/api/recordings' or path.startswith('/api/recordings/'):
                from recordings import handle_recording_request
                handle_recording_request(self,post,path);return
            body=self._body() if post else {}
            if path=='/api/subgame':
                self._auth()
                if post:
                    action=body.get('action')
                    if action in ('begin','new'):
                        ticket=self.server.execution_ticket()
                        with self.server.execution_lock:
                            self.server.check_execution_ticket(ticket)
                            result=self.server.subgame.begin(new=action=='new',ticket=ticket)
                    elif action=='stop':
                        if self.server.subgame.active():self.server.stop_execution()
                        result=self.server.subgame.snapshot()
                    else:raise APIError(422,'action: new/begin/stop')
                else:result=self.server.subgame.snapshot()
                self._send(200,result);return
            if path=='/api/fidelity':
                self._auth()
                if post:
                    if any(x.phase in ('ready','running','reporting') for x in (self.server.engine,self.server.rehearsal)):raise APIError(409,'경기를 종료·초기화한 뒤 환경을 변경하세요.')
                    if self.server.workbench and self.server.workbench.controller_process and self.server.workbench.controller_process.poll() is None:raise APIError(409,'참가 코드 실행을 먼저 종료하세요.')
                    self.server.require_no_ros()
                    try:settings=fidelity_settings.save(ROOT,body)
                    except ValueError as ex:raise APIError(422,str(ex))
                    self.server.physics.stop()
                    for target in (self.server.engine,self.server.practice,self.server.rehearsal):
                        target.control(dict(action='configure',**{k:settings[k] for k in ('seed','sensor_profile','opponent_mode')}))
                self._send(200,dict(settings=fidelity_settings.public(ROOT),identity=self.server.engine.snapshot().get('identity',{})));return
            locked=fidelity_settings.read(ROOT)['contest_lock'] and any(x.phase in ('running','reporting') for x in (self.server.engine,self.server.rehearsal))
            if locked and post:
                safe=path.endswith('/stop') or path.endswith('/cancel') or path=='/api/development/cases'
                if not safe and (path.startswith('/api/development/') or path.startswith('/api/workbench/')):raise APIError(409,'본선 모의 경기 중에는 코드·학습·배포 변경이 잠깁니다. 실행을 종료한 뒤 수정하세요.')
            if path.startswith('/api/development/'):
                self._auth()
                if not self.server.development:raise APIError(503,'Development workbench disabled on this test runtime')
                route=path[len('/api/development'):];method='POST' if post else 'GET'
                from development import DevelopmentError
                from notebook import NotebookError
                from ros_notebook import RosNotebookError
                from workbench import WorkbenchError
                try:
                    if route=='/apply-run' and post:result=(200,self.server.apply_and_run(body))
                    elif route=='/stop' and post:result=(200,self.server.stop_execution())
                    elif route=='/status' and not post:
                        result=(200,{**self.server.development.status(),'execution':self.server.execution_snapshot()})
                    elif route.startswith('/cases') or route=='/diagnostics':
                        if post:
                            ticket=self.server.execution_ticket()
                            with self.server.execution_lock:
                                self.server.check_execution_ticket(ticket)
                                self.server.require_no_ros()
                                result=self.server.scenarios.handle(method,route,body,args)
                        else:result=self.server.scenarios.handle(method,route,body,args)
                    elif route=='/jupyter':
                        result=(200,self.server.notebook.start() if post else self.server.notebook.status())
                    elif route=='/ros-jupyter':
                        result=(200,self.server.control_ros_notebook(body) if post else self.server.ros_notebook.status())
                    else:
                        if route=='/assistant' and post and body.get('case_id'):
                            body['context']=self.server.scenarios.context(body['case_id'])
                        result=self.server.development.handle(method,route,body,args)
                except (DevelopmentError,NotebookError,RosNotebookError,WorkbenchError) as exc:raise APIError(exc.status,str(exc))
                self._send(*result);return
            if path.startswith('/api/workbench/'):
                self._auth()
                if not self.server.workbench:raise APIError(503,'Workbench disabled on this test runtime')
                if post and path in ('/api/workbench/controller/start','/api/workbench/deploy'):
                    ticket=self.server.execution_ticket()
                    with self.server.execution_lock:
                        self.server.check_execution_ticket(ticket)
                        if path.endswith('/start'):self.server.require_no_ros()
                        result=self.server.workbench.handle('POST',path[len('/api/workbench'):],body,args)
                else:result=self.server.workbench.handle('POST' if post else 'GET',path[len('/api/workbench'):],body,args)
                self._send(*result);return
            if path.startswith('/api/physics/'):
                self._auth();physics=self.server.physics
                if path=='/api/physics/status' and not post:self._send(200,physics.status());return
                if path=='/api/physics/exchange' and post:self._send(200,physics.exchange(body));return
                if path=='/api/physics/control' and post:
                    action=body.get('action')
                    if action=='stop':result=self.server.stop_execution()['physics']
                    else:
                        ticket=self.server.execution_ticket()
                        with self.server.execution_lock:
                            self.server.check_execution_ticket(ticket)
                            if action!='start':raise APIError(422,'action: start or stop')
                            self.server.require_no_ros()
                            workbench=self.server.workbench
                            if workbench and workbench.controller_process and workbench.controller_process.poll() is None:
                                raise APIError(409,'참가 코드가 실행 중입니다. 실행 종료 후 환경을 바꾸거나 코드 화면의 적용·실행을 사용하세요')
                            result=physics.start(body.get('runtime','practice'))
                    self._send(200,result);return
                raise APIError(404,'Unknown physics route')
            if path.startswith('/api/admin/rehearsal/') or path.startswith('/api/view/rehearsal/'):
                e=self.server.rehearsal;path=path.replace('/rehearsal/','/',1)
            elif path.startswith('/api/admin/practice/') or path.startswith('/api/view/practice/'):
                e=self.server.practice;path=path.replace('/practice/','/',1)
            elif path.startswith('/sim/rehearsal/robot/'):
                e=self.server.rehearsal;path=path.replace('/rehearsal/','/',1)
            elif path.startswith('/sim/practice/robot/'):
                e=self.server.practice;path=path.replace('/practice/','/',1)
            elif path.startswith('/api/rehearsal/'):
                e=self.server.rehearsal;path=path.replace('/rehearsal/','/report/',1)
                if path.endswith('/drone'):raise APIError(404,'리허설 창구에는 drone이 없습니다. /api/report/drone?camera=test1을 사용하세요')
            if path=='/api/config' and not post:
                if not self._local_request():raise APIError(403,'접속 정보는 loopback의 동일 출처에서만 확인할 수 있습니다')
                self._send(200,self.server.access);return
            if path.startswith('/api/admin/'):
                self._auth()
                if path=='/api/admin/control' and post:self._send(200,self.server.control_runtime(e,body));return
                if post:raise APIError(404,'알 수 없는 운영 경로')
                with e.lock:
                    if path=='/api/admin/state':result=self.server.runtime_snapshot(e)
                    elif path=='/api/admin/events':result=e.events
                    elif path=='/api/admin/export':result=e.export()
                    elif path=='/api/admin/training-frame':
                        if not self.server.camera or not hasattr(self.server.camera,'training_frame'):raise APIError(503,'합성 학습 프레임 생성 모듈이 설치되지 않았습니다')
                        slot=args.get('slot','B-1')
                        if slot not in e.robots:invalid('slot','알 수 없는 slot','query')
                        try:seed=int(args.get('seed','0'))
                        except ValueError:invalid('seed','정수 seed 필요','query')
                        result=self.server.camera.training_frame(e,slot,seed)
                    else:raise APIError(404,'알 수 없는 운영 경로')
                self._send(200,result);return
            if path=='/api/view/state' and not post:
                self._auth(True);self._send(200,self.server.runtime_snapshot(e));return
            if path.startswith('/api/report/'):
                kind=path.rsplit('/',1)[1]
                if post:
                    if kind not in ('ready','interpretation','detection','situation'):raise APIError(404,'알 수 없는 보고 경로')
                    self._send(200,e.report(kind,body));return
                with e.changed:
                    r=e.authenticate(args.get('mission_code'),'query')
                    if r.faults['network']:raise APIError(503,'로컬 네트워크 장애 주입 중')
                    if kind=='me':result=e.me(r)
                    elif kind in ('order','target'):
                        raw=args.get('wait','0')
                        try:wait=int(raw)
                        except ValueError:invalid('wait','wait는 0~120 정수','query')
                        if not 0<=wait<=120:invalid('wait','wait는 0~120 정수','query')
                        deadline=time.monotonic()+wait
                        generation=e.started_wall
                        while True:
                            result=e.order(r,kind)
                            if result['accepted'] or not r.included or (getattr(e,'rehearsal_mode',False) and e.phase=='idle') or time.monotonic()>=deadline or e.started_wall!=generation:break
                            e.changed.wait(max(0,deadline-time.monotonic()))
                            r=e.authenticate(args.get('mission_code'),'query')
                    elif kind=='drone':
                        data,headers=self._drone(r,args)
                        self._send(200,data,'image/jpeg',headers);return
                    else:raise APIError(404,'알 수 없는 보고 경로')
                self._send(200,result);return
            if path.startswith('/sim/robot/'):
                kind=path.rsplit('/',1)[1]
                with e.lock:
                    r=e.authenticate(body.get('mission_code') if post else args.get('mission_code'),'body' if post else 'query')
                    if post:
                        result=e.robot_command(r,kind,body)
                        self.server.subgame.command(e,r,kind,body,result)
                    elif kind=='state':result=e.robot_state(r)
                    elif kind=='camera':
                        if r.faults['network'] or r.faults['camera']:raise APIError(503,'로컬 카메라/네트워크 장애 주입 중')
                        if e.physical_active:
                            if e is not self.server.physics.engine:raise APIError(503,'이 경기장의 물리 엔진을 먼저 시작하세요')
                            data,headers=self.server.physics.frame(r.slot);self._send(200,data,'image/jpeg',headers);return
                        if self.server.workbench:raise APIError(503,'물리 경기장을 먼저 시작하세요. 대체 영상은 사용하지 않습니다')
                        image,_=self.server.camera.render_camera(e,r) if self.server.camera else fallback_camera(e,r)
                        self._send(200,jpeg(image),'image/jpeg',{'X-Captured-At':datetime.now(KST).isoformat(timespec='milliseconds'),'X-Camera':'robot-synthetic'});return
                    else:raise APIError(404,'알 수 없는 로봇 경로')
                self._send(200,result);return
            if path.startswith('/api/') or path.startswith('/sim/') or post:raise APIError(404,'알 수 없는 경로')
            root=(ROOT/'web').resolve();relative=unquote(path).lstrip('/') or 'workbench.html';file=(root/relative).resolve()
            if not file.is_relative_to(root) or not file.is_file():raise APIError(404,'파일이 없습니다')
            if file.suffix.lower() not in ('.html','.js','.css','.json','.png','.jpg','.jpeg','.svg','.ico','.woff2','.glb','.map'):raise APIError(403,'허용되지 않은 파일 형식')
            mime=mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            self._send(200,file.read_bytes(),mime)
        except APIError as err:self._send(err.status,{'detail':err.detail})
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception as err:
            print('HTTP internal error:',type(err).__name__,str(err),flush=True)
            self._send(500,{'detail':'로컬 시뮬레이터 내부 오류 — 서버 로그 확인'})

    def _drone(self,r,args):
        e=self.server.engine;camera=args.get('camera')
        if camera and camera not in ('main','test1','test2'):invalid('camera','camera는 main·test1·test2','query')
        practice=bool(camera and e.test_mode)
        if not practice:
            if e.phase!='running':raise APIError(409,f'드론 화면은 경기 진행 중에만 받을 수 있습니다 (지금: {e.phase})'+(' — 지금은 테스트 모드가 아닙니다' if camera else ''))
            if not r.included:raise APIError(409,'지금 경기에 편성된 팀만 드론 화면을 받을 수 있습니다')
        destroyed=sum(t['destroyed'] for t in e.arena['targets'] if t['camp']==r.camp)
        if not practice and destroyed>=3:raise APIError(403,'우리 진영 시설물 3개가 파괴되어 드론 화면을 받을 수 없습니다')
        if not e.camera_enabled:raise APIError(503,'카메라 수신이 꺼져 있습니다')
        interval=1. if practice else (1.,2.,5.)[destroyed]
        camera=camera if practice else 'main';key=(camera,'test' if practice else r.camp)
        source=self.server.practice if practice and camera=='test1' else self.server.rehearsal if practice and camera=='test2' else e
        generation=(source.reset_epoch,self.server.physics.generation if source.physical_active else 0)
        if source.physical_active and (source is not self.server.physics.engine or not self.server.physics.status()['connected']):raise APIError(503,'드론 물리 카메라 연결이 끊겼습니다')
        cached=e.drone_cache.get(key)
        if not cached or cached.get('generation')!=generation or source.io_clock-cached['clock']>=interval-1e-8:
            if source.physical_active:
                if source is not self.server.physics.engine:raise APIError(503,'이 드론 카메라의 물리 경기장을 먼저 시작하세요')
                raw,physical_headers=self.server.physics.frame('drone');captured=physical_headers['X-Captured-At']
            else:
                if self.server.workbench:raise APIError(503,'물리 경기장을 먼저 시작하세요. 대체 영상은 사용하지 않습니다')
                image=self.server.camera.render_drone(e) if self.server.camera and hasattr(self.server.camera,'render_drone') else fallback_drone(e)
                raw=jpeg(image);captured=datetime.now(KST).isoformat(timespec='milliseconds')
            cached=dict(clock=source.io_clock,generation=generation,data=raw,captured=captured);e.drone_cache[key]=cached
        headers={'X-Captured-At':cached['captured'],'X-Frame-Interval-S':str(interval),'X-Camera':camera,'X-Test-Mode':'on' if practice else 'off'}
        if not practice:headers.update({'X-Camp':r.camp,'X-Destroyed':str(destroyed)})
        return cached['data'],headers


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8887)
    parser.add_argument('--seed',type=int,default=20261112);parser.add_argument('--access-file',default=str(ROOT/'data'/'access.json'))
    args=parser.parse_args();access=load_access(args.access_file)
    server=SimulationHTTPServer((args.host,args.port),access,Engine(args.seed,access['teams']),enable_workbench=True)
    settings=fidelity_settings.read(ROOT)
    for target in (server.engine,server.practice,server.rehearsal):target.control(dict(action='configure',**{k:settings[k] for k in ('seed','sensor_profile','opponent_mode')}))
    server.start_engine();print(f'Local MAICON simulator: http://{args.host}:{server.server_port} (simulation only)',flush=True)
    print(f'Access keys stored locally: {Path(args.access_file).resolve()}; no credentials in logs',flush=True)
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:pass
    finally:server.stopping.set();server.server_close()

if __name__=='__main__':main()
