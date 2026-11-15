"""Shared runtime selection for local Mac, Windows and Linux installations."""
import os
from pathlib import Path
import platform
import signal
import subprocess


def venv_python(root, *, system=None):
    directory = Path(root) / 'training' / '.venv'
    return directory / ('Scripts/python.exe' if (system or platform.system()) == 'Windows' else 'bin/python')


def runtime_python(root, *, env=None):
    environment = os.environ if env is None else env
    override = environment.get('MAICON_PYTHON')
    if override:
        path = Path(override).expanduser()
        return path if path.is_absolute() else Path(root) / path
    return venv_python(root)


def physics_environment(env=None, *, system=None):
    result = dict(os.environ if env is None else env)
    result.setdefault('PYTHONUNBUFFERED', '1')
    result.setdefault('PYTHONUTF8', '1')
    result.setdefault('OMP_NUM_THREADS', '2')
    if (system or platform.system()) == 'Linux' and not result.get('DISPLAY'):
        # MuJoCo reads this before importing its OpenGL bindings. CPU rendering
        # needs the distro's OSMesa library; EGL remains an explicit GPU option.
        result.setdefault('MUJOCO_GL', 'osmesa')
    return result


def stop_process(process, *, system=None, timeout=5):
    """Stop a subprocess created by us with start_new_session on POSIX."""
    if process is None or process.poll() is not None:
        return
    windows = (system or platform.system()) == 'Windows'
    try:
        if windows:
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=timeout)
    except ProcessLookupError:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if windows:
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)
