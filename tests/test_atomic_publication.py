import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'workspace'))
import team_node


class AtomicPublicationTests(unittest.TestCase):
    def modules(self):
        spec = importlib.util.spec_from_file_location('example_publication', ROOT/'examples/learned_team.py')
        example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(example)
        return (team_node, example)

    def sharing_error(self):
        error = PermissionError('reader has the destination open')
        error.winerror = 5
        return error

    def test_transient_reader_lock_does_not_kill_status_publication(self):
        for module in self.modules():
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)/'status.json'
                target.write_text('{"frame": 1}')
                replace = module.os.replace
                calls = [0]
                def contention(source, destination):
                    calls[0] += 1
                    if calls[0] < 3:
                        raise self.sharing_error()
                    replace(source, destination)
                with patch.object(module.os, 'replace', side_effect=contention), patch.object(module.time, 'sleep'):
                    module.atomic_json(target, {'frame': 2, 'status': '실행'})
                self.assertEqual(json.loads(target.read_text()), {'frame': 2, 'status': '실행'})
                self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_persistent_lock_preserves_previous_complete_status(self):
        for module in self.modules():
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)/'status.json';target.write_text('{"frame": 1}')
                with patch.object(module.os, 'replace', side_effect=self.sharing_error()) as replace, patch.object(module.time, 'sleep'):
                    with self.assertRaises(PermissionError):
                        module.atomic_json(target, {'frame': 2})
                self.assertEqual(replace.call_count, 10)
                self.assertEqual(json.loads(target.read_text()), {'frame': 1})
                self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_camera_file_uses_the_same_bounded_replacement(self):
        for module in self.modules():
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as directory:
                source=Path(directory)/'.frame.jpg';source.write_bytes(b'new frame')
                target=Path(directory)/'frame.jpg';target.write_bytes(b'old frame')
                original=module.os.replace
                calls=[0]
                def contention(source, destination):
                    calls[0]+=1
                    if calls[0]==1:raise self.sharing_error()
                    original(source, destination)
                with patch.object(module.os, 'replace', side_effect=contention) as replace, patch.object(module.time, 'sleep'):
                    module.replace_published_file(source, target)
                    self.assertEqual(replace.call_count, 2)
                self.assertEqual(target.read_bytes(), b'new frame')
