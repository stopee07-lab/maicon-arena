import unittest,math
from engine import Engine
class PerturbationTests(unittest.TestCase):
 def test_stress_start_error_reproducible_and_relative_odom_zero(self):
  a=Engine(seed=12,sensor_profile='stress');b=Engine(seed=12,sensor_profile='stress');r=a.robots['B-1']
  self.assertNotEqual((r.x,r.y,r.yaw),(.28,4.15,0));self.assertEqual((r.x,r.y,r.yaw),(b.robots['B-1'].x,b.robots['B-1'].y,b.robots['B-1'].yaw))
  self.assertLessEqual(math.hypot(r.x-.28,r.y-4.15),.043);self.assertEqual(a.robot_state(r)['odom'],{'x':0.,'y':0.,'yaw':0.})
