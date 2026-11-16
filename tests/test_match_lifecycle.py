import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from engine import Engine, APIError
from server import SimulationHTTPServer


class MatchLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.s = SimulationHTTPServer.__new__(SimulationHTTPServer)
        self.s.engine = Engine()
        self.s.practice = Engine(practice_mode=True)
        self.s.rehearsal = Engine()
        self.s.rehearsal_start_at = None
        self.s.execution_lock = threading.RLock()
        self.s.execution_request_lock = threading.RLock()
        self.s.execution_generation = 0
        self.s.execution_cancel = threading.Event()
        self.s.stopping = threading.Event()
        self.s.physics = SimpleNamespace(engine=self.s.engine, status=Mock(return_value={
            'runtime': 'mission', 'running': True, 'connected': True}), stop=Mock())
        self.s.workbench = SimpleNamespace(stop_controller=Mock(),
            controller_snapshot=Mock(return_value={'loaded': True, 'code_integrity': True, 'runtime': 'mission'}),
            deployment={'mode': 'match', 'slot': 'B-1'})
        self.s.ros_notebook = None
        self.s.subgame = SimpleNamespace(active=lambda: False, tick=Mock())
        self.s.experiment_journal = SimpleNamespace(observe=Mock())
        self.s.engine.control({'action': 'strategy', 'slot': 'B-1', 'strategy': 'external'})
        self.s.engine.control({'action': 'prepare'})
        self.s.engine.robots['B-1'].ready = True

    def test_start_rejects_disconnected_physics_without_changing_phase(self):
        self.s.physics.status.return_value['connected'] = False
        with self.assertRaises(APIError):
            self.s.control_runtime(self.s.engine, {'action': 'start'})
        self.assertEqual(self.s.engine.phase, 'ready')

    def test_resume_rejects_dead_controller(self):
        self.s.engine.control({'action': 'start'})
        self.s.engine.control({'action': 'pause'})
        self.s.workbench.controller_snapshot.return_value['loaded'] = False
        with self.assertRaises(APIError):
            self.s.control_runtime(self.s.engine, {'action': 'resume'})
        self.assertTrue(self.s.engine.paused)

    def test_stop_finishes_match_and_preserves_elapsed(self):
        self.s.engine.control({'action': 'start'})
        self.s.engine.clock = 12
        result = self.s.stop_execution()
        self.assertEqual(self.s.engine.phase, 'finished')
        self.assertEqual(self.s.engine.clock, 12)
        self.assertTrue(self.s.engine.paused)
        self.assertEqual(result['runtimes']['mission']['phase'], 'finished')

    def test_reset_stops_selected_runtime_workers_before_reset(self):
        self.s.control_runtime(self.s.engine, {'action': 'reset'})
        self.s.workbench.stop_controller.assert_called_once()
        self.s.physics.stop.assert_called_once()
        self.assertEqual(self.s.engine.phase, 'idle')

    def test_reset_other_runtime_does_not_stop_active_workers(self):
        self.s.control_runtime(self.s.rehearsal, {'action': 'reset'})
        self.s.workbench.stop_controller.assert_not_called()

    def test_ready_physics_and_controller_start_normally(self):
        self.s.control_runtime(self.s.engine, {'action': 'start'})
        self.assertEqual(self.s.engine.phase, 'running')
        self.assertFalse(self.s.engine.paused)

    def test_stale_prepare_does_not_end_running_match(self):
        self.s.engine.control({'action': 'start'})
        with self.assertRaises(APIError):
            self.s.control_runtime(self.s.engine, {'action': 'prepare'})
        self.assertEqual(self.s.engine.phase, 'running')
        self.s.physics.stop.assert_not_called()

    def test_countdown_rechecks_connection_before_start(self):
        self.s.rehearsal.control({'action': 'prepare'})
        self.s.rehearsal_start_at = 0
        self.s.physics.status.return_value.update(runtime='rehearsal', connected=False)
        self.s.tick_once()
        self.assertEqual(self.s.rehearsal.phase, 'ready')
        self.assertIsNone(self.s.rehearsal_start_at)
