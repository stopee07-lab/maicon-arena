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
    def module(self, name):
        self.assertIsNotNone(importlib.util.find_spec(name), f'{name} must be provided')
        return __import__(name)

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
