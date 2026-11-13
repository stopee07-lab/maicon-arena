import sys,pathlib,unittest,math
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'workspace'))
from team_node import translation_clearance,rotation_clearance,SurveyNavigator

def room(front=1.,rear=1.,side=.21):
 ranges=[]
 for i in range(181):
  a=-math.pi+i*math.tau/180;dx=math.cos(a);dy=math.sin(a);hits=[]
  if dx>1e-8:hits.append((front-.065)/dx)
  if dx< -1e-8:hits.append((-rear-.065)/dx)
  if abs(dy)>1e-8:hits.append(side/abs(dy))
  ranges.append(min(hits))
 return dict(scan=dict(ranges=ranges,angle_min=-math.pi,angle_increment=math.tau/180,range_max=8.))
class Footprint(unittest.TestCase):
 def test_clear_narrow_corridor_allows_straight_motion(self):
  s=room();self.assertGreater(translation_clearance(s,1),.8)
  self.assertGreater(SurveyNavigator('BLUE','B-1').safe(s,.1,0)[0],0)
 def test_actual_front_obstacle_stops(self):
  s=room(front=.14);self.assertLessEqual(translation_clearance(s,1),0)
  self.assertEqual(SurveyNavigator('BLUE','B-1').safe(s,.1,0),(0.,0.))
 def test_rear_offset_not_mistaken_for_body_clearance(self):
  s=room(rear=.18,side=1);self.assertLess(rotation_clearance(s),.20)
  self.assertEqual(SurveyNavigator('BLUE','B-1').safe(s,0,1),(0.,0.))
 def test_unknown_beam_remains_stop(self):
  s=room();s['scan']['ranges'][90]=None
  self.assertIsNone(translation_clearance(s,1));self.assertIsNone(rotation_clearance(s))
 def test_blocking_obstacle_on_inside_edge_detected(self):
  s=room(side=1.);s['scan']['ranges'][100]=.18
  self.assertLess(translation_clearance(s,1),.10)
if __name__=='__main__':unittest.main()
