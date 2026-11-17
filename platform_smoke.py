"""Run a real model/physics lifecycle on a disposable checkout (CI only)."""
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

from runtime_paths import physics_environment, runtime_python, stop_process

ROOT = Path(__file__).resolve().parent


def main():
    if (ROOT / 'data/workbench/deployment.json').exists():
        raise RuntimeError('Use a fresh checkout: this smoke test must not replace an existing deployment')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    (ROOT / 'runtime').mkdir(exist_ok=True)
    result = {'platform': platform.system(), 'architecture': platform.machine(), 'success': False}
    key = None

    def api(path, body=None):
        headers = {'Content-Type': 'application/json'}
        if key:
            headers['X-Admin-Key'] = key
        request = urllib.request.Request(base + path, headers=headers,
            data=None if body is None else json.dumps(body).encode())
        with urllib.request.urlopen(request, timeout=100) as response:
            return json.load(response)

    with (ROOT / 'runtime/platform-smoke-server.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([str(runtime_python(ROOT)), str(ROOT / 'launch.py'),
            '--no-browser', '--port', str(port)], cwd=ROOT, env=physics_environment(),
            start_new_session=os.name == 'posix', stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError('Launcher exited before server became ready')
                try:
                    key = api('/api/config')['admin_key']
                    break
                except OSError:
                    time.sleep(.25)
            else:
                raise RuntimeError('Server readiness timeout')
            models = api('/api/workbench/status')['models']
            launch = api('/api/development/apply-run', {'mode': 'match', 'policy': 'mission',
                'slot': 'B-1', 'model_id': models[0]['id']})
            assert launch['started'] and launch['controller']['loaded']
            assert launch['controller']['code_integrity'] and launch['physics']['connected']
            initial = api('/api/admin/state')
            origins = {r['slot']: (r['x'], r['y']) for r in initial['robots']}
            # Software-rendered CI cameras can run far below real time.
            # Keep the same eight simulated seconds and movement assertion.
            # Windows llvmpipe measured 7.75 simulated seconds in 300 wall
            # seconds. This is an installation/lifecycle check, not a real-time
            # performance promise; retain all eight simulated seconds.
            movement_timeout = 600 if os.name == 'nt' else 300
            deadline = time.monotonic() + movement_timeout
            next_progress = time.monotonic() + 30
            while time.monotonic() < deadline:
                state = api('/api/admin/state')
                assert not state['paused'], 'Unexpected automatic pause'
                if state['elapsed'] - initial['elapsed'] >= 8:
                    break
                if time.monotonic() >= next_progress:
                    print(json.dumps({'platform': platform.system(), 'elapsed': state['elapsed'], 'paused': state['paused']}), flush=True)
                    next_progress = time.monotonic() + 30
                time.sleep(.3)
            else:
                raise RuntimeError(f'Match did not reach eight simulated seconds within {movement_timeout} wall seconds')
            distances = {r['slot']: ((r['x']-origins[r['slot']][0])**2 +
                (r['y']-origins[r['slot']][1])**2)**.5 for r in state['robots']}
            assert distances['B-1'] > .03, 'Participant robot did not move'
            api('/api/admin/control', {'action': 'speed', 'value': 5})
            assert api('/api/admin/state')['speed'] == 5
            api('/api/admin/control', {'action': 'pause'})
            paused = api('/api/admin/state')['elapsed']
            time.sleep(.5)
            assert api('/api/admin/state')['elapsed'] == paused
            deadline = time.monotonic() + 60
            while not api('/api/physics/status')['connected']:
                if time.monotonic() >= deadline:
                    raise RuntimeError('No fresh physics frame before resume')
                time.sleep(.1)
            api('/api/admin/control', {'action': 'resume'})
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if api('/api/admin/state')['elapsed'] > paused:
                    break
                time.sleep(.3)
            else:
                raise AssertionError('Match did not resume within 60 wall seconds')
            stopped = api('/api/development/stop', {})
            assert stopped['runtimes']['mission']['phase'] == 'finished'
            assert not stopped['physics']['running']
            result.update(success=True, elapsed=state['elapsed'], displacement_m=distances,
                code_sha256=launch['deployment']['code_sha256'],
                model_sha256=launch['deployment']['weights_sha256'],
                checks=['loaded model', 'physics connected', 'participant movement',
                    'speed selection', 'pause/resume', 'owned processes stopped'])
        except Exception as exc:
            result['error'] = type(exc).__name__ + ': ' + str(exc)
            if isinstance(exc, urllib.error.HTTPError):
                result['http_detail'] = json.load(exc).get('detail')
            # Capture only the owned participant log; remove generated access
            # credentials before placing diagnostics in the public CI artifact.
            access = json.loads((ROOT / 'data/access.json').read_text(encoding='utf-8')) if (ROOT / 'data/access.json').exists() else {}
            secrets = []
            def collect(value):
                if isinstance(value, dict):
                    for name, item in value.items():
                        if isinstance(item, str) and ('key' in name or 'code' in name):
                            secrets.append(item)
                        else:
                            collect(item)
                elif isinstance(value, list):
                    for item in value:
                        collect(item)
            collect(access)
            logs = sorted((ROOT / 'data/workbench/controllers').glob('*/worker.log'))
            diagnostic_logs = {'physics_log_tail': ROOT / 'physics_data/worker.log'}
            if logs:
                diagnostic_logs['participant_log_tail'] = logs[-1]
            for name, path in diagnostic_logs.items():
                if not path.exists():
                    continue
                tail = path.read_text(encoding='utf-8', errors='replace')[-6000:]
                for secret in secrets:
                    if secret:
                        tail = tail.replace(secret, '[REDACTED]')
                result[name] = tail
            if key:
                try:
                    state = api('/api/admin/state')
                    result['failure_state'] = {k: state.get(k) for k in ('phase','paused','elapsed','events')}
                    result['failure_physics'] = api('/api/physics/status')
                except Exception:
                    pass
            raise
        finally:
            if key:
                try:
                    api('/api/development/stop', {})
                except Exception:
                    pass
            stop_process(process, timeout=20)
            (ROOT / 'runtime/platform-smoke-result.json').write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
