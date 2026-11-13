import sys,pathlib,unittest,math
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'workspace'))
from team_node import SurveyNavigator,CoverageNavigator
from tests.test_mission_scheduling import state,navigator
class Corridor(unittest.TestCase):
 def test_sixty_cm_observed_gap_has_body_clear_route(self):
  n=SurveyNavigator('BLUE','B-1')
  for i in range(15,46):
   for y in (.85,1.45):n.occupied[n.grid(i*.1,y)]=0.
  path=n.plan(n.grid(1.6,1.15),n.grid(4.4,1.15))
  self.assertTrue(path)
  self.assertTrue(all(.85+.20<y<1.45-.20 for x,y in path))
 def test_observation_standoff_fits_gap_and_keeps_body_margin(self):
  n=navigator();n.block_samples=[('a',(3.06,1.65),'CM'),('b',(3.06,1.65),'CM')];n.command(state())
  south=min(p[1] for p,observe in n.coverage_tasks)
  self.assertGreaterEqual(south,1.10)
  self.assertGreaterEqual(1.65-south,.40)
class Corner(unittest.TestCase):
 def test_diagonal_corner_with_body_clearance_is_traversable(self):
  n=SurveyNavigator('BLUE','B-1');n.occupied[n.grid(1.,3.3)]=0.;n.path=[(.8,3.1)]
  self.assertTrue(n.local_path_clear(.8,3.0))
 def test_diagonal_corner_inside_swept_body_is_blocked(self):
  n=SurveyNavigator('BLUE','B-1');n.occupied[n.grid(1.,3.3)]=0.;n.path=[(.85,3.15)]
  self.assertFalse(n.local_path_clear(.8,3.0))
