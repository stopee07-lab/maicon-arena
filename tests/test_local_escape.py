import sys,pathlib,unittest,math
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'workspace'))
from team_node import SurveyNavigator
from tests.test_footprint_clearance import room
class Escape(unittest.TestCase):
 def test_forward_escape_when_rear_too_close_to_rotate(self):
  n=SurveyNavigator('BLUE','B-1');s=room(front=1.,rear=.18,side=1.)
  self.assertEqual(n.escape_direction(s),1)
 def test_reverse_escape_when_front_too_close_to_rotate(self):
  n=SurveyNavigator('BLUE','B-1');s=room(front=.18,rear=1.,side=1.)
  self.assertEqual(n.escape_direction(s),-1)
 def test_no_escape_when_missing_scan_or_both_directions_blocked(self):
  n=SurveyNavigator('BLUE','B-1');s=room(front=.18,rear=.18,side=1.)
  self.assertEqual(n.escape_direction(s),0)
  s=room(front=1.,rear=.18,side=1.);s['scan']['ranges'][50]=None
  self.assertEqual(n.escape_direction(s),0)
 def test_clearance_decreasing_toward_corner_not_accepted(self):
  n=SurveyNavigator('BLUE','B-1');s=room(front=1.,rear=1.,side=1.)
  # Close obstacle forward-left: forward movement would reduce separation.
  a=math.atan2(.18,.12-.065);i=round((a+math.pi)/(math.tau/180));s['scan']['ranges'][i]=math.hypot(.18,.12-.065)
  self.assertEqual(n.escape_direction(s),-1)
 def test_half_grid_free_ray_is_cleared(self):
  n=SurveyNavigator('BLUE','B-1');key=n.grid(.28+.065+.15,4.15);n.occupied[key]=0.
  s=room(side=2.);s.update(slot='B-1',odom=dict(x=0,y=0,yaw=0));s['scan'].update(frame_seq=1,measured_at=1)
  with patch('team_node.time.monotonic',return_value=1):n.update_scan(s)
  self.assertNotIn(key,n.occupied)

 def test_recorded_inflated_start_has_short_forward_connector(self):
  import json
  s=json.loads((pathlib.Path(__file__).parent/'fixtures/f-start-39.2.json').read_text())
  self.assertEqual(SurveyNavigator('BLUE','B-1').escape_direction(s),1)
