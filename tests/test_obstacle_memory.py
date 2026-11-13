import sys,pathlib,unittest,math
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'workspace'))
from team_node import SurveyNavigator

def state(seq,distance):return dict(slot='B-1',odom=dict(x=0.,y=0.,yaw=0.),scan=dict(ranges=[distance],angle_min=0.,angle_increment=.035,range_max=8.,frame_seq=seq,measured_at=seq*.2))
class ObstacleMemory(unittest.TestCase):
 def scan(self,nav,seq,distance,now):
  with patch('team_node.time.monotonic',return_value=now):nav.update_scan(state(seq,distance))
 def test_confirmed_obstacle_survives_occlusion(self):
  n=SurveyNavigator('BLUE','B-1');cell=n.grid(.28+.065+1.,4.15)
  for i in range(1,5):self.scan(n,i,1.,i*.2)
  self.scan(n,5,None,10.)
  self.assertIn(cell,n.occupied)
 def test_confirmed_obstacle_clears_with_repeated_free_rays(self):
  n=SurveyNavigator('BLUE','B-1');cell=n.grid(1.345,4.15)
  for i in range(1,5):self.scan(n,i,1.,i*.2)
  self.scan(n,5,2.,2.);self.assertIn(cell,n.occupied)
  for i in range(6,9):self.scan(n,i,2.,2.+i*.2)
  self.assertNotIn(cell,n.occupied)
 def test_single_transient_return_expires(self):
  n=SurveyNavigator('BLUE','B-1');self.scan(n,1,1.,1.);self.scan(n,2,None,10.)
  self.assertNotIn(n.grid(1.345,4.15),n.occupied)
 def test_cached_scan_cannot_confirm_static_obstacle(self):
  n=SurveyNavigator('BLUE','B-1')
  for i in range(4):self.scan(n,1,1.,1.+i*.1)
  self.scan(n,2,None,10.)
  self.assertNotIn(n.grid(1.345,4.15),n.occupied)
if __name__=='__main__':unittest.main()
