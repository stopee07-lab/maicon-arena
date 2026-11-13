"""A real Ubuntu/ROS 2 JupyterLab session, separate from the Mac kernel."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import threading
import time
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


class RosNotebookError(Exception):
    def __init__(self,status,message):
        self.status=status
        super().__init__(message)


class RosNotebook:
    IMAGE='maicon-ros-jupyter:humble'
    NAME='maicon-rebuild-ros-jupyter'

    def __init__(self,root,server_url='http://127.0.0.1:8765',*,port=8889,probe=True):
        self.root=Path(root).resolve();self.workspace=self.root/'workspace'
        self.store=self.root/'data/ros-jupyter';self.script=self.root/'ros2/mac-runtime.sh'
        parsed=urlparse(server_url)
        if parsed.scheme!='http' or parsed.hostname not in ('localhost','127.0.0.1','::1'):
            raise RosNotebookError(422,'ROS 실습은 이 Mac의 로컬 경기 서버에만 연결합니다.')
        self.server_url=f'http://host.docker.internal:{parsed.port or 80}'
        self.port=int(port);self.owner=hashlib.sha256(str(self.root).encode()).hexdigest()[:20]
        self.token=None;self.lock=threading.RLock();self.starting=False;self.installed=False
        self.detail='Ubuntu 22.04 · ROS 2 Humble 실습 환경'
        if (self.store/'token').is_file():self.token=(self.store/'token').read_text().strip()
        self.tools_available=bool(shutil.which('colima') and shutil.which('docker'))
        if probe and self.tools_available:self.installed=self._image_available()

    def _command(self,args,*,timeout=15,check=True):
        result=subprocess.run(['bash',str(self.script),*args],cwd=self.root,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
        if check and result.returncode:
            raise RosNotebookError(503,'ROS 실습 실행 명령이 실패했습니다. ros-jupyter 실행 기록을 확인하세요.')
        return result

    def _image_available(self):
        try:return self._command(['docker','image','inspect',self.IMAGE,'--format','{{.Id}}'],check=False,timeout=8).returncode==0
        except (OSError,subprocess.SubprocessError):return False

    def _container(self):
        try:
            result=self._command(['docker','inspect','--format',
                '{{.State.Running}}|{{index .Config.Labels "maicon.owner"}}|{{.Id}}',self.NAME],check=False,timeout=5)
            if result.returncode:return {'running':False,'owned':False,'exists':False}
            running,owner,identifier=result.stdout.strip().split('|')
            return {'running':running=='true','owned':owner==self.owner,'exists':True,'id':identifier}
        except (OSError,ValueError,subprocess.SubprocessError):return {'running':False,'owned':False,'exists':False}

    def _ready(self):
        if not self.token:return False
        request=Request(f'http://127.0.0.1:{self.port}/api/status',headers={'Authorization':'token '+self.token})
        try:
            with urlopen(request,timeout=.6) as response:return response.status==200 and 'kernels' in json.load(response)
        except (OSError,ValueError):return False

    def status(self):
        state=self._container() if self.tools_available else {'running':False,'owned':False}
        ready=bool(state.get('running') and state.get('owned') and self._ready())
        return dict(running=ready,starting=self.starting or bool(state.get('running') and state.get('owned') and not ready),
            installed=self.installed or ready,available=self.tools_available,port=self.port,
            url=f'http://127.0.0.1:{self.port}/lab/tree/examples/00.%EC%8B%A4%EC%8A%B5%EB%AA%A9%EC%B0%A8.ipynb?token={quote(self.token)}' if ready else None,
            detail=self.detail,runtime='Ubuntu 22.04 · ROS 2 Humble',kernel='maicon-ros2',
            workspace_path=str(self.workspace),container_path='/team',container=self.NAME,
            interface_package='sim_mil_interfaces',physical_hardware=False)

    def _prepare(self):
        self.workspace.mkdir(parents=True,exist_ok=True);self.store.mkdir(parents=True,exist_ok=True);self.store.chmod(0o700)
        if not self.token or len(self.token)<40:self.token=secrets.token_urlsafe(40)
        token_path=self.store/'token';token_path.write_text(self.token);token_path.chmod(0o600)
        access=json.loads((self.root/'data/access.json').read_text())
        team=next((t for t in access['teams'] if t['slot']=='B-1'),None)
        if not team:raise RosNotebookError(409,'B-1의 로컬 임무코드가 없습니다.')
        env={'SIM_SERVER':self.server_url,'MISSION_CODE':team['mission_code'],'SIM_MODE':'practice',
             'SIM_READ_ONLY':'false','ROS_DOMAIN_ID':'44','ROS_LOCALHOST_ONLY':'1','ROBOT_NAMESPACE':''}
        env_path=self.store/'bridge.env'
        env_path.write_text(''.join(f'{k}={v}\n' for k,v in env.items()));env_path.chmod(0o600)
        config={'ServerApp':{'ip':'0.0.0.0','port':8889,'port_retries':0,'open_browser':False,'root_dir':'/team',
            'default_url':'/lab/tree/examples/00.실습목차.ipynb','allow_remote_access':True,'allow_root':False,
            'log_level':30,'shutdown_no_activity_timeout':0,'use_redirect_file':False},
            'IdentityProvider':{'token':self.token},'MappingKernelManager':{'default_kernel_name':'maicon-ros2'}}
        config_path=self.store/'jupyter_server_config.json'
        config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2));config_path.chmod(0o600)

    def _run_command(self):
        return ['docker','run','--detach','--name',self.NAME,'--label','maicon.owner='+self.owner,
            '--user',f'{os.getuid()}:{os.getgid()}','--init','--env-file',str(self.store/'bridge.env'),
            '--publish',f'127.0.0.1:{self.port}:8889',
            '--mount',f'type=bind,source={self.workspace},target=/team',
            '--mount',f'type=bind,source={self.store},target=/config,readonly',self.IMAGE]

    def _save_logs(self):
        if not self.store.is_dir():return
        try:
            result=self._command(['docker','logs','--tail','200',self.NAME],check=False,timeout=8)
            text=result.stdout+result.stderr
            if self.token:text=text.replace(self.token,'[TOKEN]')
            access=json.loads((self.root/'data/access.json').read_text())
            for team in access.get('teams',[]):text=text.replace(team['mission_code'],'[MISSION_CODE]')
            path=self.store/'server.log';path.write_text(text);path.chmod(0o600)
        except (OSError,ValueError,subprocess.SubprocessError):pass

    def start(self):
        with self.lock:
            if self.starting:raise RosNotebookError(409,'ROS 실습 환경을 시작하고 있습니다. 잠시 후 확인하세요.')
            if self.status()['running']:return self.status()
            if not self.tools_available:raise RosNotebookError(503,'Colima와 Docker CLI가 필요합니다.')
            self.starting=True
        try:
            self._prepare()
            with socket.socket() as sock:
                if sock.connect_ex(('127.0.0.1',self.port))==0:
                    raise RosNotebookError(409,f'{self.port} 포트를 다른 프로그램이 사용하고 있습니다.')
            self.detail='전용 Ubuntu VM을 시작하고 있습니다.'
            self._command(['start'],timeout=60)
            self.installed=self._image_available()
            if not self.installed:raise RosNotebookError(503,'ROS 실습 이미지가 없습니다. ros2/mac-runtime.sh build-jupyter를 먼저 실행하세요.')
            existing=self._container()
            if existing.get('exists') and not existing.get('owned'):raise RosNotebookError(409,'같은 이름의 다른 컨테이너가 있습니다. 기존 컨테이너는 변경하지 않았습니다.')
            other=self._command(['docker','ps','--filter','label=com.docker.compose.project=maicon-rebuild','--format','{{.Names}}'],check=False)
            if other.stdout.strip():raise RosNotebookError(409,'별도의 ROS 브리지가 실행 중입니다. 해당 브리지를 먼저 정지하세요.')
            if existing.get('exists'):self._command(['docker','rm','--force',self.NAME])
            self.detail='ROS 2 커널과 센서 브리지를 시작하고 있습니다.'
            self._command(self._run_command(),timeout=30)
            deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                if self._ready():
                    self.detail='영상 순서의 노트북을 실제 ROS 2 커널에서 실행합니다. 센서는 로컬 물리 시뮬레이터에 연결됩니다.'
                    self.starting=False
                    return self.status()
                state=self._container()
                if not state.get('running'):break
                time.sleep(.25)
            self._save_logs()
            raise RosNotebookError(503,'ROS JupyterLab 준비에 실패했습니다. data/ros-jupyter/server.log를 확인하세요.')
        except subprocess.TimeoutExpired:
            raise RosNotebookError(503,'ROS 실습 환경 시작 시간이 초과됐습니다. 전용 VM 상태를 확인하세요.')
        finally:self.starting=False

    def close(self):
        with self.lock:
            state=self._container()
            if state.get('owned'):
                self._save_logs()
                self._command(['docker','stop','--time','5',self.NAME],check=False,timeout=12)
                self._command(['docker','rm',self.NAME],check=False,timeout=8)
            self.detail='ROS 실습 환경이 정지되어 있습니다. 열기 버튼으로 다시 실행합니다.'

