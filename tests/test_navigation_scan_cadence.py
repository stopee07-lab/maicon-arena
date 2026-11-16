"""Sparse-scan recovery under delayed controller polling, without a simulator."""
from datetime import datetime, timezone
import math
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'workspace'))
from team_node import TemporalScan, SurveyNavigator, rotation_clearance, translation_clearance


def observation(seq, missing=(), distance=1., x=0., yaw=0.):
    ranges = [distance] * 181
    for index in missing:
        ranges[index] = None
    return dict(clock_domain="test_seconds", measured_at=seq * .1, odom=dict(x=x, y=0., yaw=yaw),
                scan=dict(clock_domain="test_seconds",received_clock_domain="test_seconds",frame_seq=seq, measured_at=seq * .1, received_at=seq * .1,
                          ranges=ranges, angle_min=-math.pi,
                          angle_increment=math.tau / 180, range_max=8.))


class DelayedScanRecovery(unittest.TestCase):
    def test_sparse_gap_after_delayed_poll_allows_measured_turn(self):
        memory = TemporalScan()
        memory.update(observation(1), now=1.)
        raw = observation(2, missing=(90,))
        state = memory.update(raw, now=1.55)
        self.assertGreater(SurveyNavigator('BLUE', 'B-1').safe(state, 0., 1.)[1], 0.)
        self.assertIsNone(raw['scan']['ranges'][90])
        self.assertAlmostEqual(state['navigation_scan']['ranges'][90], 1. - .02 - .4 * .55)

    def test_recent_near_obstacle_stays_blocking_across_delayed_poll(self):
        memory = TemporalScan()
        old = observation(1)
        old['scan']['ranges'][90] = .28
        memory.update(old, now=1.)
        state = memory.update(observation(2, missing=(90,)), now=1.55)
        self.assertIsNotNone(translation_clearance(state))
        self.assertEqual(SurveyNavigator('BLUE', 'B-1').safe(state, .1, 0.), (0., 0.))
        self.assertEqual(SurveyNavigator('BLUE', 'B-1').safe(state, 0., 1.), (0., 0.))

    def test_full_dropout_never_replaced_by_history(self):
        memory = TemporalScan()
        memory.update(observation(1), now=1.)
        state = memory.update(observation(2, missing=range(181)), now=1.55)
        self.assertIsNone(rotation_clearance(state))
        self.assertIsNone(translation_clearance(state))

    def test_expired_raw_measurement_does_not_enable_turn(self):
        memory = TemporalScan()
        memory.update(observation(1), now=1.)
        state = memory.update(observation(2, missing=(90,)), now=1.76)
        self.assertIsNone(rotation_clearance(state))

    def test_repaired_frames_and_duplicate_raw_source_do_not_renew_expiry(self):
        memory = TemporalScan()
        original = observation(1)
        memory.update(original, now=1.)
        memory.update(original, now=1.6)
        repaired = memory.update(observation(2, missing=(90,)), now=1.65)
        self.assertEqual(repaired['scan_repair']['repaired'], 1)
        expired = memory.update(observation(3, missing=(90,)), now=1.8)
        self.assertIsNone(rotation_clearance(expired))

    def test_pose_change_keeps_gap_unknown(self):
        for x, yaw in ((.026, 0.), (0., .101)):
            with self.subTest(x=x, yaw=yaw):
                memory = TemporalScan()
                memory.update(observation(1), now=1.)
                state = memory.update(observation(2, missing=(90,), x=x, yaw=yaw), now=1.55)
                self.assertIsNone(rotation_clearance(state))


    def test_first_poll_does_not_renew_an_already_old_scan(self):
        memory = TemporalScan()
        old = observation(1, distance=.4)
        old['measured_at'] = 10.
        old['scan'].update(measured_at=9.5, received_at=9.5)
        memory.update(old, now=100.)
        current = observation(2, missing=(90,))
        current['measured_at'] = 10.55
        current['scan'].update(measured_at=10.55, received_at=10.55)
        result = memory.update(current, now=100.55)
        self.assertIsNone(rotation_clearance(result))

    def test_known_source_age_is_included_in_range_deduction(self):
        memory = TemporalScan()
        prior = observation(1, missing=(90,))
        prior['measured_at'] = 9.85
        prior['scan'].update(measured_at=9.85, received_at=9.85)
        memory.update(prior, now=99.85)
        delayed = observation(2)
        delayed['measured_at'] = 10.
        delayed['scan'].update(measured_at=9.85, received_at=10.)
        memory.update(delayed, now=100.)
        current = observation(3, missing=(90,))
        current['measured_at'] = 10.1
        current['scan'].update(measured_at=10.1, received_at=10.1)
        result = memory.update(current, now=100.3)
        self.assertAlmostEqual(result['scan_repair']['sources'][0]['age_s'], .45)
        self.assertAlmostEqual(result['navigation_scan']['ranges'][90], 1. - .02 - .4 * .45)

    def test_source_clock_expiry_cannot_be_hidden_by_short_poll_interval(self):
        memory = TemporalScan()
        old = observation(1)
        memory.update(old, now=100.)
        current = observation(11, missing=(90,))
        result = memory.update(current, now=100.1)
        self.assertIsNone(rotation_clearance(result))

    def test_unverifiable_clock_domain_does_not_enter_history(self):
        for domain in (None, 'foreign_seconds'):
            with self.subTest(domain=domain):
                memory = TemporalScan()
                old = observation(1)
                old['scan']['clock_domain'] = domain
                old['scan']['received_clock_domain'] = domain
                memory.update(old, now=1.)
                result = memory.update(observation(2, missing=(90,)), now=1.1)
                self.assertIsNone(rotation_clearance(result))


    def test_cross_domain_receipt_cannot_hide_old_capture(self):
        memory = TemporalScan()
        old = observation(1, distance=.4)
        old['scan'].update(clock_domain='physics_seconds',
            captured_at=datetime.fromtimestamp(99.5, timezone.utc).isoformat())
        memory.update(old, now=1., now_wall=100.)
        result = memory.update(observation(2, missing=(90,)), now=1.3, now_wall=100.3)
        self.assertIsNone(rotation_clearance(result))

    def test_cross_domain_capture_requires_verifiable_wall_timestamp(self):
        for stamp in (None, 'invalid', '1970-01-01T00:01:40', '1970-01-01T00:01:41+00:00'):
            with self.subTest(stamp=stamp):
                memory = TemporalScan()
                old = observation(1)
                old['scan'].update(clock_domain='physics_seconds', captured_at=stamp)
                memory.update(old, now=1., now_wall=100.)
                result = memory.update(observation(2, missing=(90,)), now=1.1, now_wall=100.1)
                self.assertIsNone(rotation_clearance(result))

    def test_cross_domain_capture_age_and_pose_are_registered(self):
        memory = TemporalScan()
        prior = observation(1, missing=(90,))
        prior['measured_at'] = 9.9
        prior['scan'].update(measured_at=9.9, received_at=9.9)
        memory.update(prior, now=1., now_wall=99.9)
        delayed = observation(2)
        delayed['measured_at'] = 10.
        delayed['scan'].update(received_at=10., clock_domain='physics_seconds',
            captured_at=datetime.fromtimestamp(99.9, timezone.utc).isoformat())
        memory.update(delayed, now=1.1, now_wall=100.)
        current = observation(3, missing=(90,))
        current['measured_at'] = 10.2
        current['scan'].update(measured_at=10.2, received_at=10.2)
        result = memory.update(current, now=1.3, now_wall=100.2)
        self.assertEqual(result['scan_repair']['repaired'], 1)
        self.assertAlmostEqual(result['scan_repair']['sources'][0]['age_s'], .3)
        self.assertAlmostEqual(result['navigation_scan']['ranges'][90], 1. - .02 - .4 * .3)


    def test_newer_raw_clearance_supersedes_old_age_discount(self):
        memory = TemporalScan()
        memory.update(observation(1, distance=.3), now=1.)
        memory.update(observation(2, distance=.3), now=1.5)
        result = memory.update(observation(3, missing=(90,), distance=.3), now=1.6)
        self.assertAlmostEqual(result['scan_repair']['sources'][0]['age_s'], .1)
        self.assertAlmostEqual(result['navigation_scan']['ranges'][90], .24)
        self.assertGreater(SurveyNavigator('BLUE', 'B-1').safe(result, 0., 1.)[1], 0.)

    def test_newer_raw_near_obstacle_supersedes_older_clear_measurement(self):
        memory = TemporalScan()
        memory.update(observation(1, distance=1.), now=1.)
        memory.update(observation(2, distance=.1), now=1.5)
        result = memory.update(observation(3, missing=(90,), distance=1.), now=1.6)
        self.assertAlmostEqual(result['scan_repair']['sources'][0]['age_s'], .1)
        self.assertAlmostEqual(result['navigation_scan']['ranges'][90], .04)
        self.assertEqual(SurveyNavigator('BLUE', 'B-1').safe(result, .1, 0.), (0., 0.))
        self.assertEqual(SurveyNavigator('BLUE', 'B-1').safe(result, 0., 1.), (0., 0.))


class SmallHeadingTrimFallback(unittest.TestCase):
    def test_unknown_side_defers_small_trim_but_keeps_verified_translation(self):
        state = observation(1, missing=(30,))
        nav = SurveyNavigator('BLUE', 'B-1')
        self.assertEqual(nav.safe(state, .1, .05), (.1, 0.))

    def test_side_outside_translation_corridor_defers_unsafe_rotation(self):
        state = observation(1)
        state['scan']['ranges'][135] = .18
        self.assertEqual(SurveyNavigator('BLUE', 'B-1').safe(state, .1, .05), (.1, 0.))

    def test_unknown_front_still_stops_small_trim_translation(self):
        state = observation(1, missing=(90,))
        self.assertEqual(SurveyNavigator('BLUE', 'B-1').safe(state, .1, .05), (0., 0.))

    def test_front_obstacle_still_stops_small_trim_translation(self):
        state = observation(1)
        state['scan']['ranges'][90] = .08  # inside the calibrated nose + margin
        self.assertEqual(SurveyNavigator('BLUE', 'B-1').safe(state, .1, .05), (0., 0.))

    def test_large_turn_or_turn_in_place_never_uses_fallback(self):
        state = observation(1, missing=(30,))
        for linear, angular in ((.1, .121), (.1, -.121), (0., .05), (-.1, .05)):
            with self.subTest(linear=linear, angular=angular):
                self.assertEqual(SurveyNavigator('BLUE', 'B-1').safe(state, linear, angular), (0., 0.))

    def test_fallback_keeps_translation_braking_cap(self):
        state = observation(1, missing=(30,))
        state['scan']['ranges'][90] = .15
        nav = SurveyNavigator('BLUE', 'B-1')
        straight = nav.safe(state, .2, 0.)
        self.assertGreater(straight[0], 0.)
        self.assertLess(straight[0], .2)
        self.assertEqual(nav.safe(state, .2, .05), straight)


if __name__ == '__main__':
    unittest.main()
