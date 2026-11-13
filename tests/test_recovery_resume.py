import sys,pathlib,unittest,math
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'workspace'))
from team_node import SurveyNavigator
from tests.test_mission_scheduling import state
class Recovery(unittest.TestCase):
 def test_retry_after_obstacle_has_cleared(self):
  n=SurveyNavigator('BLUE','B-1');n.order_ready=True;n.objective=(3.5,4.15);n.mode='hold';n.recovery_retry_at=5.;n.reason='recovery budget exhausted; operator intervention required'
  s=state()
  with patch('team_node.time.monotonic',return_value=10):n.command(s)
  self.assertNotEqual(n.mode,'hold')
 def test_measured_turn_uses_calibrated_command_and_yaw_completion(self):
  n=SurveyNavigator('BLUE','B-1');n.mode='choose_turn';s=state()
  with patch('team_node.time.monotonic',return_value=10):v,w=n.recovery(s,10,2.78,4.15,0.)
  self.assertGreaterEqual(abs(w),1.5);self.assertGreaterEqual(n.until,15.)
  v,w=n.recovery(s,12.1,2.78,4.15,.2*n.turn_sign)
  self.assertEqual(n.mode,'turn');self.assertNotEqual(w,0)
  v,w=n.recovery(s,13.,2.78,4.15,.7*n.turn_sign)
  self.assertEqual(n.mode,'track');self.assertEqual((v,w),(0,0))
 def test_midturn_sensor_wait_retains_heading_target(self):
  n=SurveyNavigator('BLUE','B-1');n.mode='turn';n.turn_start=0.;n.turn_sign=1.;n.until=30.
  s=state();s['scan']['ranges'][20]=None
  v,w=n.recovery(s,20.,2.78,4.15,.2)
  self.assertEqual((v,w),(0,0));self.assertEqual(n.mode,'turn');self.assertEqual(n.turn_start,0.)
