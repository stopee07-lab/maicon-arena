"""Own a local JupyterLab server for the actual participant workspace."""
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class NotebookError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class Notebook:
    def __init__(self, root, *, python_path=None, port=8888, probe=True):
        self.root = Path(root).resolve()
        self.workspace = self.root / 'workspace'
        self.store = self.root / 'data' / 'workbench' / 'jupyter'
        self.python = str(python_path or self.root / 'training/.venv/bin/python')
        self.port = int(port)
        self.process = None
        self.started_at = None
        self.token = None
        self.available = False
        self.versions = {}
        self.detail = 'JupyterLab 실행 환경을 확인하지 않았습니다.'
        self.lock = threading.RLock()
        self.log_thread = None
        if probe:
            self._probe()

    def _probe(self):
        try:
            result = subprocess.run(
                [self.python, '-c', 'import importlib.metadata as m; import jupyterlab, ipykernel; import json; print(json.dumps({k:m.version(k) for k in ("jupyterlab","jupyter-server","ipykernel")}))'],
                capture_output=True, text=True, timeout=15, check=True,
            )
            self.versions = json.loads(result.stdout.strip())
            self.available = True
            self.detail = 'Mac의 실제 Python 편집기·노트북·터미널을 엽니다.'
        except (OSError, subprocess.SubprocessError, ValueError):
            self.detail = 'training/.venv에 JupyterLab과 ipykernel을 설치해야 합니다.'

    def _prepare(self):
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.store.mkdir(parents=True, exist_ok=True)
        self.store.chmod(0o700)
        token_path = self.store / 'token'
        if token_path.is_file():
            self.token = token_path.read_text().strip()
        if not self.token or len(self.token) < 40:
            self.token = secrets.token_urlsafe(40)
            token_path.write_text(self.token)
        token_path.chmod(0o600)
        config = {
            'ServerApp': {
                'ip': '127.0.0.1', 'port': self.port, 'port_retries': 0,
                'open_browser': False, 'root_dir': str(self.workspace),
                'default_url': '/lab/tree/team_node.py', 'allow_remote_access': False,
                'log_level': 'WARN', 'shutdown_no_activity_timeout': 0,
                'use_redirect_file': False,
            },
            'IdentityProvider': {'token': self.token},
            'MappingKernelManager': {'default_kernel_name': 'maicon-python'},
        }
        config_path = self.store / 'server_config.json'
        config_path.write_text(json.dumps(config, indent=2))
        config_path.chmod(0o600)
        kernel_dir = self.store / 'share/jupyter/kernels/maicon-python'
        kernel_dir.mkdir(parents=True, exist_ok=True)
        kernel = {
            'argv': [self.python, '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
            'display_name': 'MAICON · Mac Python', 'language': 'python',
            'env': {'YOLO_CONFIG_DIR': str(self.root / 'data/workbench/yolo-config')},
        }
        (kernel_dir / 'kernel.json').write_text(json.dumps(kernel, ensure_ascii=False, indent=2))
        for name in ('runtime', 'config', 'ipython'):
            (self.store / name).mkdir(exist_ok=True)
            (self.store / name).chmod(0o700)
        return config_path

    def _ready(self):
        if not self.process or self.process.poll() is not None or not self.token:
            return False
        request = Request(f'http://127.0.0.1:{self.port}/api/status', headers={'Authorization': 'token ' + self.token})
        try:
            with urlopen(request, timeout=.5) as response:
                data = json.loads(response.read())
                return response.status == 200 and 'kernels' in data
        except (OSError, URLError, HTTPError, ValueError):
            return False

    def status(self):
        with self.lock:
            alive = bool(self.process and self.process.poll() is None)
            running = alive and self._ready()
            url = f'http://127.0.0.1:{self.port}/lab/tree/team_node.py?token={quote(self.token)}' if running else None
            return {
                'available': self.available, 'running': bool(running), 'starting': alive and not running,
                'url': url, 'port': self.port, 'python': self.python,
                'workspace_path': str(self.workspace), 'versions': dict(self.versions),
                'runtime': 'Mac Python · 로컬 HTTP 어댑터', 'detail': self.detail,
                'pid': self.process.pid if alive else None,
            }

    def _drain_log(self, process):
        log_path = self.store / 'server.log'
        with log_path.open('a', encoding='utf-8') as log:
            log_path.chmod(0o600)
            for line in process.stdout:
                log.write(line.replace(self.token or '', '[TOKEN]') if self.token else line)
                log.flush()
        process.stdout.close()

    def start(self):
        with self.lock:
            if self.process and self.process.poll() is None:
                return self.status()
            if not self.available:
                raise NotebookError(503, self.detail)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind(('127.0.0.1', self.port))
                except OSError as exc:
                    raise NotebookError(409, f'포트 {self.port}를 다른 프로그램이 사용 중입니다. 기존 서버는 종료하지 않았습니다.') from exc
            config_path = self._prepare()
            env = os.environ.copy()
            env.update({
                'JUPYTER_CONFIG_DIR': str(self.store / 'config'),
                'JUPYTER_RUNTIME_DIR': str(self.store / 'runtime'),
                'JUPYTER_PATH': str(self.store / 'share/jupyter'),
                'IPYTHONDIR': str(self.store / 'ipython'),
                'YOLO_CONFIG_DIR': str(self.root / 'data/workbench/yolo-config'),
                'PATH': str(Path(self.python).parent) + os.pathsep + env.get('PATH', ''),
            })
            # The token stays in a private config file, never command-line arguments.
            self.process = subprocess.Popen(
                [self.python, '-m', 'jupyterlab', '--config', str(config_path)],
                cwd=self.workspace, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, start_new_session=True,
            )
            self.started_at = time.time()
            self.log_thread = threading.Thread(target=self._drain_log, args=(self.process,), daemon=True)
            self.log_thread.start()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    self.detail = 'JupyterLab 시작에 실패했습니다. data/workbench/jupyter/server.log를 확인하세요.'
                    raise NotebookError(503, self.detail)
                if self._ready():
                    self.detail = 'Mac의 실제 Python 편집기·노트북·터미널이 실행 중입니다.'
                    break
                time.sleep(.2)
            return self.status()

    def close(self):
        with self.lock:
            process = self.process
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                except ProcessLookupError:
                    pass
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=3)
            self.process = None
            if self.log_thread:
                self.log_thread.join(timeout=1)
                self.log_thread = None
            if self.available:
                self.detail = 'JupyterLab이 정지되어 있습니다. 열기 버튼으로 다시 실행합니다.'
