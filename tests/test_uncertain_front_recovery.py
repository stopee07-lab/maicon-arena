"""Recorded participant-only sensor cases: uncertainty must stop, not reverse."""
import copy
import json
import pathlib
import sys
import unittest
from unittest.mock import patch
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'workspace'))
from team_node import SurveyNavigator, pose_from_odom, translation_clearance


class HistoricalFrontRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((ROOT/'tests/fixtures/uncertain-historical-front.json').read_text())

    def navigator(self, state):
        x, y, _ = pose_from_odom(state)
        nav = SurveyNavigator('BLUE', 'B-1')
        nav.order_ready = True
        nav.objective = (x+1., y)
        nav.goal = nav.progress_goal = nav.objective
        nav.best_goal_distance = 1.
        nav.path = [(x+.2, y)]
        nav.last_plan = nav.last_tick = nav.anchor_at = 100.
        nav.anchor = (x,y)
        nav.attempts = 1
        return nav

    def test_recorded_uncertain_front_waits_without_recovery_attempt(self):
        for case in self.cases:
            with self.subTest(source_time=case['source_time']):
                state = copy.deepcopy(case['state'])
                self.assertLess(translation_clearance(state), .04)
                raw = translation_clearance({**state, 'navigation_scan':state['scan']})
                self.assertTrue(raw is None or raw >= .04)
                nav = self.navigator(state)
                with patch('team_node.time.monotonic', return_value=100.):
                    self.assertEqual(nav.command(state), (0.,0.))
                self.assertEqual(nav.attempts,1)
                self.assertEqual(nav.mode,'track')

    def test_actual_raw_front_obstacle_still_starts_recovery(self):
        state = copy.deepcopy(self.cases[0]['state'])
        scan = state['scan']
        scan['ranges'] = [1.] * len(scan['ranges'])
        front = round(-scan['angle_min']/scan['angle_increment'])
        scan['ranges'][front] = .1
        state['navigation_scan'] = copy.deepcopy(scan)
        self.assertLess(translation_clearance(state), .04)
        nav = self.navigator(state)
        with patch('team_node.time.monotonic', return_value=100.):
            self.assertEqual(nav.command(state),(0.,0.))
        self.assertEqual(nav.attempts,2)
        self.assertEqual(nav.mode,'yield')

    def test_unknown_raw_front_does_not_spend_recovery_attempt(self):
        state = copy.deepcopy(self.cases[0]['state'])
        scan = state['scan']
        front = round(-scan['angle_min']/scan['angle_increment'])
        scan['ranges'][front] = None
        nav = self.navigator(state)
        with patch('team_node.time.monotonic', return_value=100.):
            self.assertEqual(nav.command(state),(0.,0.))
        self.assertEqual(nav.attempts,1)
        self.assertEqual(nav.mode,'track')


if __name__ == '__main__':
    unittest.main()
