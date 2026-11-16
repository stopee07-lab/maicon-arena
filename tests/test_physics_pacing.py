import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from engine import Engine
from physics_worker import Runtime

class PhysicsPacingTests(unittest.TestCase):
    def runtime(self, speed, duration, count=60):
        r=Runtime.__new__(Runtime);r.running=True;r.speed=speed;r.paused=False
        r.r=Mock();r.dr=Mock();r.d=SimpleNamespace(time=0);r.seq=0
        now=[0.];calls=[]
        def tick(n):
            calls.append(n);r.d.time+=n*.05;now[0]+=duration
            if len(calls)>=count:r.running=False
        r.tick=tick;r.exchange=Mock(side_effect=AssertionError('Wall debt must not pause a connected runtime'))
        with patch('physics_worker.time.monotonic',side_effect=lambda:now[0]),patch('physics_worker.time.sleep',side_effect=lambda n:now.__setitem__(0,now[0]+n)):
            r.run()
        return r,calls,now[0]
    def test_slow_renderer_keeps_fixed_steps_without_accumulated_debt_pause(self):
        r,calls,elapsed=self.runtime(1,.12)
        self.assertEqual(calls,[1]*60)
        self.assertAlmostEqual(r.d.time,3.)
        self.assertAlmostEqual(elapsed,7.2)
        r.exchange.assert_not_called()
    def test_requested_speed_paces_real_fixed_steps(self):
        for speed in (1,5,20):
            with self.subTest(speed=speed):
                r,calls,elapsed=self.runtime(speed,.001,20)
                self.assertTrue(all(n==min(5,speed) for n in calls))
                self.assertAlmostEqual(r.d.time/elapsed,speed)
    def test_physical_engine_accepts_acceleration_and_records_it(self):
        e=Engine();e.physical_active=True
        e.control({'action':'speed','value':5})
        self.assertEqual(e.speed,5)
        self.assertEqual(e.events[-1]['type'],'speed')
