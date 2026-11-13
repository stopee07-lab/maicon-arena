import sys,pathlib,unittest,math
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'workspace'))
from team_node import CoverageNavigator,ObservationStore,sector_for

def state():return dict(slot='B-1',mode='mission',cmd='start',hp=4,hp_max=4,battery_pct=90,odom=dict(x=2.5,y=0,yaw=0),scan=dict(ranges=[2.]*181,angle_min=-math.pi,angle_increment=math.tau/180,range_max=8.))
def navigator():
 n=CoverageNavigator('BLUE','B-1');n.apply_order(dict(target='RED_COMMS',search_sectors=['CM','R-S1'],base='RED_MAINTENANCE'));return n
class Scheduling(unittest.TestCase):
 def test_route_covers_fixed_m3_sectors(self):
  n=navigator();self.assertEqual({sector_for(*p) for p in n.route},{'CM','R-S1','R-S2','R-S3'})
  self.assertEqual(sector_for(*n.route[0]),'CM')
 def test_m3_does_not_wait_for_unseen_m2(self):
  n=navigator();n.block_samples=[('a',(3.0,3.8),'CM'),('b',(3.01,3.8),'CM')];n.command(state())
  self.assertTrue(n.coverage_started);self.assertFalse(n.m2_done)
 def test_m5_preempts_coverage(self):
  n=navigator();n.block_samples=[('a',(3.,3.8),'CM'),('b',(3.,3.8),'CM')];n.set_objective('RED_SUPPLY_DEPOT');n.command(state())
  self.assertFalse(n.coverage_started);self.assertEqual(n.mission_stage,'capture')
 def test_multiple_blocks_not_averaged_into_phantom_center(self):
  n=navigator();n.block_samples=[('a',(3.,1.),'CM'),('b',(3.,4.),'CM')];n.command(state())
  self.assertFalse(n.coverage_started)
 def test_other_enemy_sector_can_be_observed(self):
  n=navigator()
  with patch('team_node.observed_block_center',return_value=(4.,2.5)):
   n.observe_panel((3.8,2.5),state(),'f')
  self.assertEqual(len(n.block_samples),1)
 def test_current_block_scope_does_not_include_rest_of_sector(self):
  n=navigator();n.coverage_started=True;n.coverage_sector='R-S1';n.coverage_center=(4.,4.)
  self.assertTrue(n.reviewing_location((4.2,4.)))
  self.assertFalse(n.reviewing_location((4.8,4.)))
 def test_m2_noisy_goal_updates_do_not_keep_resetting_path(self):
  n=navigator();n.acquire_target((4.4,4.1),now=1);n.path=[(3.,4.)]
  first=n.m2_goal;n.acquire_target((4.42,4.12),now=1.2)
  self.assertEqual(first,n.m2_goal);self.assertEqual(n.m2_acquired_at,1)
 def test_m2_approach_budget_starts_on_acquisition(self):
  n=navigator();n.acquire_target((4.4,4.1),now=1)
  with patch('team_node.time.monotonic',return_value=40):n.command(state())
  self.assertIsNone(n.m2_goal);self.assertTrue(any('M2 approach deadline' in g['reason'] for g in n.failed_goals))
if __name__=='__main__':unittest.main()

class CoverageOrder(unittest.TestCase):
 def test_first_observation_point_is_nearest_not_always_far_side(self):
  n=navigator();n.block_samples=[('a',(3.,3.8),'CM'),('b',(3.,3.8),'CM')];n.command(state())
  tasks=[p for p,observe in n.coverage_tasks];position=(2.78,4.15)
  self.assertEqual(tasks[0],min(tasks,key=lambda p:math.dist(p,position)))

class CoverageTiming(unittest.TestCase):
 def test_late_arrival_still_gets_full_observation_time(self):
  n=navigator();s=state();s['odom']['yaw']=math.atan2(3.8-4.15,3.-2.78)
  n.coverage_started=True;n.coverage_block_started=1.;n.coverage_center=(3.,3.8);n.coverage_sector='CM'
  n.coverage_tasks=[((2.78,4.15),True)];n.coverage_point_started=.1
  with patch('team_node.time.monotonic',return_value=22.2):n.command(s)
  self.assertEqual(n.coverage_index,0);self.assertEqual(n.mission_stage,'m3_observe')
  n.coverage_frames={'a','b','c'}
  with patch('team_node.time.monotonic',return_value=25.5):n.command(s)
  self.assertEqual(n.coverage_index,1);self.assertFalse(n.failed_goals)
