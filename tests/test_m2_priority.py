import sys,pathlib,unittest,math
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'workspace'))
from team_node import CoverageNavigator

def nav():
 n=CoverageNavigator('BLUE','B-1');n.apply_order(dict(target='RED_COMMS',search_sectors=['CM','R-S1'],base='RED_MAINTENANCE'));n.coverage_started=True;n.coverage_center=(3.,3.);n.coverage_tasks=[((2.5,3.),True)];n.coverage_block_started=1.;return n
class Priority(unittest.TestCase):
 def test_three_fresh_target_frames_preempt_coverage_without_marking_block_complete(self):
  n=nav()
  for i in range(3):n.consider_target((4.3,.8),(4.9,.7),str(i),now=10+i*.2)
  self.assertEqual(n.m2_goal,(4.3,.8));self.assertFalse(n.coverage_started);self.assertFalse(n.covered_blocks)
  self.assertEqual(n.m2_target_position,(4.9,.7))
 def test_cached_or_old_frame_does_not_preempt(self):
  n=nav()
  for t in [1.,1.2,1.4]:n.consider_target((4.3,.8),(4.9,.7),'same',now=t)
  self.assertIsNone(n.m2_goal);self.assertTrue(n.coverage_started)
  for t in [3.,5.,7.]:n.consider_target((4.3,.8),(4.9,.7),str(t),now=t)
  self.assertIsNone(n.m2_goal)
 def test_m5_keeps_precedence(self):
  n=nav();n.set_objective('RED_MAINTENANCE')
  for i in range(3):n.consider_target((4.3,.8),(4.9,.7),str(i),now=10+i*.2)
  self.assertIsNone(n.m2_goal);self.assertTrue(n.official_capture)
class Approach(unittest.TestCase):
 def state(self):
  return dict(slot='B-1',mode='mission',cmd='start',hp=4,hp_max=4,battery_pct=90,odom=dict(x=3.72,y=-2.65,yaw=0),scan=dict(ranges=[2.]*181,angle_min=-math.pi,angle_increment=math.tau/180,range_max=8.))
 def test_clear_last_half_meter_is_not_fixed_creep_speed(self):
  n=CoverageNavigator('BLUE','B-1');n.order_ready=True;n.m2_goal=(4.4,1.5);n.m2_acquired_at=10.;n.m2_target_position=(5.,1.5)
  with patch('team_node.time.monotonic',return_value=11.):v,w=n.command(self.state())
  self.assertGreater(v,.10)
 def test_arrival_stops_translation_before_aiming(self):
  n=CoverageNavigator('BLUE','B-1');n.order_ready=True;n.m2_goal=(4.05,1.5);n.m2_acquired_at=10.;n.m2_target_position=(5.,1.5)
  with patch('team_node.time.monotonic',return_value=11.):v,w=n.command(self.state())
  self.assertEqual(v,0.);self.assertEqual(n.mission_stage,'m2_aim')
if __name__=='__main__':unittest.main()
