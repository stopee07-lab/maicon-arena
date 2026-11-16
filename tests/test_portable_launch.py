"""Launcher boundaries: platform paths, renderer choice and owned processes."""
import importlib.util
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PortableLaunch(unittest.TestCase):
    def test_physics_child_does_not_inherit_launcher_lifetime_pipe(self):
        # Keep the parent's stdin open with an outstanding read, just like
        # server --watch-launcher. A noninteractive child must receive EOF on
        # its own stdin without consuming or waiting on that lifetime pipe.
        script = '''
import os, sys, tempfile, threading
from pathlib import Path
from types import SimpleNamespace
from engine import Engine
from physics import Physics
threading.Thread(target=lambda: sys.stdin.buffer.read(1), daemon=True).start()
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    (root/'physics_worker.py').write_text("import sys; print('EOF', repr(sys.stdin.read()), flush=True)")
    server = SimpleNamespace(engine=Engine(), practice=Engine(practice_mode=True),
        rehearsal=Engine(), server_port=1, access={'admin_key':'fixture'})
    physics = Physics(server, root)
    physics.python = Path(sys.executable)
    try:
        physics.start('mission')
        assert physics.process.wait(timeout=3) == 0
        assert "EOF ''" in (root/'physics_data/worker.log').read_text()
    finally:
        physics.stop()
os._exit(0)
'''
        process = subprocess.Popen([sys.executable, '-c', script], cwd=ROOT,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            code = process.wait(timeout=12)
            self.assertEqual(code, 0, process.stderr.read().decode('utf-8', 'replace'))
        finally:
            process.stdin.close()
            if process.poll() is None:
                process.kill(); process.wait()
            process.stdout.close(); process.stderr.close()

    def test_code_snapshot_preserves_bytes_with_windows_text_newlines(self):
        from development import CodeStore, DevelopmentError, EDITABLE
        original_write = Path.write_text
        def windows_write(path, text, *args, **kwargs):
            if kwargs.get('newline') is None:
                kwargs['newline'] = '\r\n'
            return original_write(path, text, *args, **kwargs)
        for ending in (b'\n', b'\r\n'):
            with self.subTest(ending=ending), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root/'workspace').mkdir()
                source = b'value = 1' + ending + b'print(value)' + ending
                for name in EDITABLE:
                    (root/'workspace'/name).write_bytes(source)
                store = CodeStore(root)
                with patch.object(Path, 'write_text', windows_write):
                    manifest = store.snapshot()
                folder, _ = store.version(manifest['id'])
                for name in EDITABLE:
                    self.assertEqual((folder/name).read_bytes(), source)
                entry = folder/'team_node.py'
                entry.chmod(0o644)
                entry.write_bytes(source + b'# modified\n')
                with self.assertRaises(DevelopmentError):
                    store.version(manifest['id'])

    def test_korean_help_with_legacy_console_encoding(self):
        env = dict(os.environ, PYTHONIOENCODING='cp1252', PYTHONUTF8='0')
        result = subprocess.run([sys.executable, str(ROOT/'launch.py'), '--help'],
                                env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        self.assertIn('MAICON', result.stdout.decode('utf-8'))

    def module(self, name):
        self.assertIsNotNone(importlib.util.find_spec(name), f'{name} must be provided')
        return __import__(name)

    def test_existing_instance_requires_same_local_access(self):
        import io,json
        launch=self.module('launch')
        with tempfile.TemporaryDirectory() as directory, patch.object(launch,'ROOT',Path(directory)):
            access=Path(directory)/'data/access.json';access.parent.mkdir();access.write_text(json.dumps({'admin_key':'same-key'}))
            for key,expected in [('same-key',True),('another-key',False)]:
                with patch.object(launch,'urlopen',return_value=io.BytesIO(json.dumps({'admin_key':key}).encode())):
                    self.assertEqual(launch.existing_instance(12345),expected)

    def test_repeat_launch_opens_existing_instance_without_new_process(self):
        launch=self.module('launch')
        with patch.object(launch,'existing_instance',return_value=True), patch.object(launch.webbrowser,'open') as browser, patch.object(launch.subprocess,'Popen') as popen:
            self.assertEqual(launch.main(['--port','12345']),0)
            browser.assert_called_once_with('http://127.0.0.1:12345/?page=run')
            popen.assert_not_called()

    def test_shutdown_signal_interrupts_then_restores_handlers(self):
        import signal
        launch=self.module('launch');before=signal.getsignal(signal.SIGTERM)
        with launch.shutdown_signals():
            with self.assertRaises(KeyboardInterrupt):signal.getsignal(signal.SIGTERM)(signal.SIGTERM,None)
        self.assertEqual(signal.getsignal(signal.SIGTERM),before)

    def test_windows_and_posix_venv_paths(self):
        r = self.module('runtime_paths')
        root = Path('/project')
        self.assertEqual(r.venv_python(root, system='Windows'), root/'training/.venv/Scripts/python.exe')
        self.assertEqual(r.venv_python(root, system='Linux'), root/'training/.venv/bin/python')
        self.assertEqual(r.venv_python(root, system='Darwin'), root/'training/.venv/bin/python')

    def test_explicit_runtime_override_does_not_depend_on_os(self):
        r = self.module('runtime_paths')
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(r.runtime_python(tmp, env={'MAICON_PYTHON':sys.executable}), Path(sys.executable))
            self.assertEqual(r.runtime_python(tmp, env={}), r.venv_python(tmp))

    def test_headless_linux_selects_software_rendering_and_preserves_override(self):
        r = self.module('runtime_paths')
        self.assertEqual(r.physics_environment({}, system='Linux')['MUJOCO_GL'], 'osmesa')
        self.assertEqual(r.physics_environment({'MUJOCO_GL':'egl'}, system='Linux')['MUJOCO_GL'], 'egl')
        self.assertNotIn('MUJOCO_GL', r.physics_environment({'DISPLAY':':0'}, system='Linux'))
        self.assertNotIn('MUJOCO_GL', r.physics_environment({}, system='Windows'))
        self.assertNotIn('MUJOCO_GL', r.physics_environment({}, system='Darwin'))

    def test_windows_shutdown_never_uses_posix_process_groups(self):
        r = self.module('runtime_paths')
        proc = Mock(); proc.poll.return_value = None
        with patch.object(r.os, 'killpg', create=True) as killpg:
            r.stop_process(proc, system='Windows')
        proc.terminate.assert_called_once(); proc.wait.assert_called_once()
        killpg.assert_not_called()

    def test_real_owned_process_is_stopped(self):
        r = self.module('runtime_paths')
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=(os.name == 'posix'))
        try:
            r.stop_process(proc)
            self.assertIsNotNone(proc.poll())
        finally:
            if proc.poll() is None: proc.kill(); proc.wait()

    def test_wait_does_not_accept_another_server_when_child_has_failed(self):
        launch = self.module('launch')
        proc = Mock(); proc.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, '시작'):
            launch.wait_ready(proc, 'http://127.0.0.1:1', timeout=.01)

    def test_launcher_help_works_without_installing_packages(self):
        self.assertTrue((ROOT/'launch.py').is_file(), 'portable launcher must exist')
        result = subprocess.run([sys.executable, str(ROOT/'launch.py'), '--help'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--no-browser', result.stdout)
        self.assertIn('--check', result.stdout)

    def test_launcher_refuses_an_occupied_port(self):
        launch = self.module('launch')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            with self.assertRaisesRegex(RuntimeError, '이미 사용'):
                launch.ensure_port_available(listener.getsockname()[1])

    def test_recently_closed_listener_can_be_reused(self):
        launch=self.module('launch')
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1];listener.listen()
            client=socket.create_connection(('127.0.0.1',port))
            accepted,_=listener.accept();accepted.shutdown(socket.SHUT_WR)
            self.assertEqual(client.recv(1),b'');client.close();accepted.close()
        launch.ensure_port_available(port)

    def test_launcher_uses_utf8_for_windows_child_files(self):
        launch = self.module('launch')
        with patch.dict(os.environ, {'PYTHONUTF8':'0'}):
            self.assertEqual(launch.child_environment()['PYTHONUTF8'], '1')

    def test_camera_check_passes_valid_python_to_the_runtime(self):
        launch = self.module('launch')
        def run(command, **kwargs):
            compile(command[2], '<camera-check>', 'exec')
            return subprocess.CompletedProcess(command, 0, stdout='camera check completed', stderr='')
        with patch.object(launch.subprocess, 'run', side_effect=run):
            launch.check_runtime(sys.executable, render=True)

    def test_linux_and_windows_install_cpu_torch_before_other_packages(self):
        launch = self.module('launch')
        for system in ('Linux', 'Windows'):
            commands = launch.install_commands(Path(sys.executable), system=system)
            self.assertIn('https://download.pytorch.org/whl/cpu', commands[0])
            self.assertEqual(commands[1][-1], str(ROOT/'requirements.txt'))
        mac = launch.install_commands(Path(sys.executable), system='Darwin')
        self.assertEqual(len(mac), 1)
        self.assertNotIn('--index-url', mac[0])


if __name__ == '__main__': unittest.main()
