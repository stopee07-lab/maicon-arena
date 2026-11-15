#!/usr/bin/env python3
"""MAICON 설치와 시작. Python 3.12를 권장합니다."""
import argparse
from contextlib import contextmanager
from http.client import HTTPException
import hmac
import signal
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import venv
import webbrowser

from runtime_paths import physics_environment, runtime_python, stop_process, venv_python

ROOT = Path(__file__).resolve().parent


def child_environment():
    env = physics_environment()
    env['PYTHONUTF8'] = '1'
    env['PYTHONUNBUFFERED'] = '1'
    env.setdefault('YOLO_CONFIG_DIR', str(ROOT / 'data' / 'workbench' / 'yolo-config'))
    return env


def install_commands(python, *, notebook=False, system=None):
    prefix = [str(python), '-m', 'pip', 'install']
    commands = []
    if (system or platform.system()) in ('Windows', 'Linux'):
        # The PyPI Linux default pulls CUDA libraries even for CPU practice.
        commands.append(prefix + ['-r', str(ROOT / 'requirements-torch.txt'), '--index-url', 'https://download.pytorch.org/whl/cpu'])
    command = prefix + ['-r', str(ROOT / 'requirements.txt')]
    if notebook:
        command += ['-r', str(ROOT / 'requirements-notebook.txt')]
    return commands + [command]


def install(*, notebook=False):
    if os.environ.get('MAICON_PYTHON'):
        raise RuntimeError('설치는 MAICON_PYTHON 설정을 해제한 뒤 실행하세요. 다른 Python 환경은 변경하지 않습니다.')
    python = venv_python(ROOT)
    if not python.is_file():
        print('처음 설치 중입니다. 인터넷 연결이 필요하며 몇 분 걸릴 수 있습니다.', flush=True)
        venv.EnvBuilder(with_pip=True).create(ROOT / 'training' / '.venv')
    for command in install_commands(python, notebook=notebook):
        subprocess.run(command, cwd=ROOT, env=child_environment(), check=True)
    installed = subprocess.run([str(python), '-m', 'pip', 'freeze'], cwd=ROOT, env=child_environment(), check=True, capture_output=True, text=True)
    (ROOT / 'runtime').mkdir(exist_ok=True)
    (ROOT / 'runtime' / f'installed-packages-{platform.system().lower()}.txt').write_text(installed.stdout, encoding='utf-8')
    subprocess.run([str(python), str(ROOT / 'seed_model.py')], cwd=ROOT, env=child_environment(), check=True)
    print('설치 완료. 시작 파일을 열면 경기장이 열립니다.', flush=True)
    return python


def check_runtime(python, *, render=False):
    code = 'import json,sys; import PIL,numpy,mujoco,torch,ultralytics; print(json.dumps({"python":sys.version.split()[0],"mujoco":mujoco.__version__,"torch":torch.__version__}))'
    if render:
        xml = '<mujoco><worldbody><geom type="sphere" size="0.1"/></worldbody></mujoco>'
        code += f'; m=mujoco.MjModel.from_xml_string({xml!r})'
        code += '; d=mujoco.MjData(m); mujoco.mj_forward(m,d); r=mujoco.Renderer(m,64,64); r.update_scene(d); pixels=r.render(); assert pixels.shape==(64,64,3); r.close(); print("MuJoCo camera: OK (64 x 64)")'
    result = subprocess.run([str(python), '-c', code], cwd=ROOT, env=child_environment(), capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-2500:]
        hint = '\nLinux 서버에서는 docs/platforms.md의 OSMesa 설치도 확인하세요.' if sys.platform.startswith('linux') else ''
        raise RuntimeError(f'실행 환경 확인 실패. 설치 파일을 다시 실행하세요.{hint}\n{detail}')
    print(result.stdout.strip(), flush=True)


def wait_ready(process, url, *, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('경기장 서버 시작에 실패했습니다. 위의 오류를 확인하세요.')
        try:
            with urlopen(url + '/api/config', timeout=.5) as response:
                config = json.loads(response.read())
                if response.status == 200 and isinstance(config, dict) and 'admin_key' in config:
                    return
        except (OSError, URLError, ValueError):
            pass
        time.sleep(.15)
    raise RuntimeError('경기장 서버 시작 시간이 초과되었습니다. 위의 오류를 확인하세요.')


def existing_instance(port):
    """Reuse only a responding server with this checkout's private identity."""
    try:
        access=json.loads((ROOT/'data/access.json').read_text(encoding='utf-8'))
        expected=access.get('admin_key')
        if not isinstance(expected,str) or not expected:return False
        with urlopen(f'http://127.0.0.1:{port}/api/config',timeout=1) as response:
            actual=json.loads(response.read(65536)).get('admin_key')
        return isinstance(actual,str) and hmac.compare_digest(expected,actual)
    except (OSError,ValueError,HTTPException,AttributeError):
        return False


@contextmanager
def shutdown_signals():
    """Closing the owning terminal must also close its server and workers."""
    def interrupt(*_):raise KeyboardInterrupt()
    previous={}
    for name in ('SIGTERM','SIGHUP'):
        sig=getattr(signal,name,None)
        if sig is not None:
            previous[sig]=signal.signal(sig,interrupt)
    try:
        yield
    finally:
        for sig,handler in previous.items():signal.signal(sig,handler)


def ensure_port_available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(('127.0.0.1', port))
        except OSError as exc:
            raise RuntimeError(f'{port}번 포트를 이미 사용 중입니다. 기존 시작 창을 확인하거나 --port {port+1 if port<65535 else 8887}로 실행하세요.') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup', action='store_true', help='필요한 Python 패키지 설치 후 종료')
    parser.add_argument('--notebook', action='store_true', help='설치할 때 JupyterLab도 추가')
    parser.add_argument('--check', action='store_true', help='패키지와 실제 MuJoCo 카메라 확인 후 종료')
    parser.add_argument('--no-browser', action='store_true', help='브라우저 자동 열기 끄기 (Linux 서버)')
    parser.add_argument('--port', type=int, default=8887, help='로컬 경기장 포트 (기본 8887)')
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11):
        parser.error('Python 3.11 이상이 필요합니다. Python 3.12를 권장합니다.')
    if not 1024 <= args.port <= 65535:
        parser.error('--port는 1024에서 65535 사이여야 합니다.')
    try:
        if not (args.setup or args.notebook or args.check) and existing_instance(args.port):
            url=f'http://127.0.0.1:{args.port}/welcome.html'
            print(f'이미 실행 중인 경기장입니다: {url}\n원래 실행 창은 켜 두세요.',flush=True)
            if not args.no_browser:webbrowser.open(url)
            return 0
        python = runtime_python(ROOT)
        if args.setup or args.notebook:
            install(notebook=args.notebook)
            return 0
        if not python.is_file():
            if os.environ.get('MAICON_PYTHON') or args.check:
                raise RuntimeError('Python 실행 환경이 없습니다. 설치 파일을 먼저 실행하세요.')
            python = install()
        check_runtime(python, render=args.check)
        if args.check:
            return 0
        ensure_port_available(args.port)
        subprocess.run([str(python), str(ROOT / 'seed_model.py')], cwd=ROOT, env=child_environment(), check=True)
        url = f'http://127.0.0.1:{args.port}'
        with shutdown_signals():
            process = subprocess.Popen([str(python), str(ROOT / 'server.py'), '--host', '127.0.0.1', '--port', str(args.port)], cwd=ROOT, env=child_environment(), start_new_session=(os.name == 'posix'))
            try:
                wait_ready(process, url)
                print(f'경기장: {url}/welcome.html\n이 창을 켜 두세요. 종료하려면 Ctrl+C를 누르세요.', flush=True)
                if not args.no_browser:
                    webbrowser.open(url + '/welcome.html')
                try:
                    return process.wait()
                except KeyboardInterrupt:
                    # Windows consoles send Ctrl+C to both processes. Give the
                    # server time to close workers before the forced fallback.
                    if os.name == 'nt':
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            pass
                    raise
            finally:
                stop_process(process, timeout=15)
    except KeyboardInterrupt:
        print('\n경기장을 종료했습니다.')
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'\n{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
