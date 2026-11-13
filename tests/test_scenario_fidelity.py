import tempfile,unittest,sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import Engine
from scenarios import Scenarios
class ScenarioFidelityTests(unittest.TestCase):
 def test_restore_profile_and_pose_history(self):
  with tempfile.TemporaryDirectory() as d:
   e=Engine(seed=2,sensor_profile='stress',opponent_mode='privileged');r=e.robots['B-1'];r.x=5.;r.y=3.
   physics=SimpleNamespace(stop=lambda:None,status=lambda:{})
   server=SimpleNamespace(engine=e,practice=Engine(practice_mode=True),rehearsal=Engine(),physics=physics,workbench=None)
   store=Scenarios(d,server);record=store.capture({'runtime':'mission'})
   e.control({'action':'configure','seed':12,'sensor_profile':'ideal','opponent_mode':'sensor_limited'})
   store.restore(record['id'],{},start_physics=False)
   self.assertEqual(e.sensor_profile['name'],'stress');self.assertEqual(e.opponent_mode,'privileged');self.assertEqual(e.seed,2)
   self.assertEqual(e.capture_previous_poses['B-1'],(r.x,r.y,r.yaw))
