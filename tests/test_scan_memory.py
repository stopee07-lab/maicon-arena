import sys,pathlib,math,unittest,copy
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'workspace'))
from team_node import TemporalScan,arc_clearance

def state(seq=1,missing=(),x=0,yaw=0):
 ranges=[1.]*181
 for i in missing:ranges[i]=None
 return dict(measured_at=seq*.1,odom=dict(x=x,y=0.,yaw=yaw),scan=dict(frame_seq=seq,measured_at=seq*.1,received_at=seq*.1,ranges=ranges,angle_min=-math.pi,angle_increment=math.tau/180,range_max=8.))
class ScanMemory(unittest.TestCase):
 def test_short_lived_measured_gap_can_restore_rotation(self):
  memory=TemporalScan();memory.update(state(),now=1.)
  current=state(2,[90]);result=memory.update(current,now=1.1)
  self.assertIsNotNone(arc_clearance(result,0,math.pi));self.assertIsNone(current['scan']['ranges'][90])
  self.assertLess(result['navigation_scan']['ranges'][90],1.)
 def test_unknown_without_history_remains_unknown(self):
  self.assertIsNone(arc_clearance(TemporalScan().update(state(1,[90]),now=1.)))
 def test_all_missing_scan_not_replaced(self):
  m=TemporalScan();m.update(state(),now=1)
  self.assertIsNone(arc_clearance(m.update(state(2,range(181)),now=1.1)))
 def test_expired_measurements_not_reused(self):
  m=TemporalScan();m.update(state(),now=1)
  self.assertIsNone(arc_clearance(m.update(state(2,[90]),now=1.5)))
 def test_translation_invalidates_old_rays(self):
  m=TemporalScan();m.update(state(),now=1)
  self.assertIsNone(arc_clearance(m.update(state(2,[90],x=.1),now=1.1)))
 def test_current_obstacle_always_wins(self):
  m=TemporalScan();m.update(state(),now=1);current=state(2);current['scan']['ranges'][90]=.10
  self.assertEqual(arc_clearance(m.update(current,now=1.1)),.10)
 def test_cached_source_does_not_refresh_age(self):
  m=TemporalScan();m.update(state(),now=1);m.update(state(),now=1.18)
  self.assertIsNone(arc_clearance(m.update(state(2,[90]),now=1.3)))
 def test_yaw_alignment_uses_correct_historical_direction(self):
  m=TemporalScan();old=state();old['scan']['ranges'][91]=.5;m.update(old,now=1)
  result=m.update(state(2,[90],yaw=math.tau/180),now=1.1)
  self.assertLess(result['navigation_scan']['ranges'][90],.5)
if __name__=='__main__':unittest.main()

class RecoveryQuality(unittest.TestCase):
 def test_missing_measurement_does_not_consume_escape_attempt(self):
  from team_node import SurveyNavigator
  n=SurveyNavigator('BLUE','B-1');n.mode='choose_turn';n.attempts=1
  s=state(1,range(181));s['slot']='B-1'
  for i in range(10):n.recovery(s,10+i*.2,.28,4.15,0)
  self.assertEqual(n.attempts,1);self.assertEqual(n.mode,'choose_turn')
 def test_waiting_for_rotation_scan_does_not_age_progress_timer(self):
  from team_node import SurveyNavigator
  from unittest.mock import patch
  n=SurveyNavigator('BLUE','B-1');n.anchor_at=1.
  with patch('team_node.time.monotonic',return_value=20.):n.safe(state(1,[90]),0.,1.)
  self.assertEqual(n.anchor_at,20.)

class Registration(unittest.TestCase):
 def test_cached_scan_keeps_source_pose_while_robot_turns(self):
  m=TemporalScan();a=state(1);a['measured_at']=.1;m.update(a,now=1)
  b=state(1,yaw=.2);b['measured_at']=.2;r=m.update(b,now=1.1)
  self.assertEqual(r['scan_registration']['odom']['yaw'],0.)
  self.assertAlmostEqual(r['scan_registration']['age_s'],.1)
 def test_mapping_uses_registered_pose(self):
  from team_node import SurveyNavigator
  n=SurveyNavigator('BLUE','B-1');s=state(1,x=1.,yaw=1.)
  s['slot']='B-1';s['scan']['ranges']=[1.];s['scan']['angle_min']=0.
  s['scan_registration']={'odom':dict(x=0.,y=0.,yaw=0.),'stationary':True,'source_time':.1,'age_s':0.}
  n.update_scan(s);self.assertIn(n.grid(1.345,4.15),n.occupied)
 def test_missing_pose_bracket_defers_mapping(self):
  m=TemporalScan();s=state(2);s['measured_at']=1.;s['scan']['received_at']=.1;r=m.update(s,now=1)
  self.assertIsNone(r['scan_registration'])
