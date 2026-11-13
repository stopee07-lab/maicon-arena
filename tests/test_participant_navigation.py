import sys,pathlib,unittest,math,base64,json,tempfile
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'workspace'));sys.path.insert(0,str(ROOT))
import cv2,numpy as np
from team_node import CoverageNavigator, detection_position, observed_block_center, select_beacon
from facility_detector import FacilityDetector,PUBLIC_PHOTOS
from participant_supervisor import should_retry
from robot_client import ReportWorkflow
from development import CodeStore

class ParticipantTests(unittest.TestCase):
 def test_unknown_lidar_no_crash_or_position(self):
  state={'slot':'B-1','odom':{'x':0,'y':0,'yaw':0},'scan':{'ranges':[None]*180,'angle_min':-math.pi,'angle_increment':math.tau/180,'range_max':8}}
  self.assertIsNone(detection_position({'xyxy':[310,200,330,220]},state))
  self.assertIsNone(observed_block_center((1.,4.15),state))
 def test_m1_base_does_not_stage(self):
  nav=CoverageNavigator('BLUE','B-1');nav.apply_order({'target':'RED_RADAR','search_sectors':['R-S1'],'base':'RED_MAINTENANCE'})
  self.assertIsNone(nav.staging_goal);self.assertIsNone(nav.objective)
  nav.set_objective('RED_SUPPLY_DEPOT');self.assertEqual(nav.objective_name,'RED_SUPPLY_DEPOT')
 def test_photo_detects_warped_reference(self):
  detector=FacilityDetector()
  for kind,data in PUBLIC_PHOTOS.items():
   ref=cv2.imdecode(np.frombuffer(base64.b64decode(data),np.uint8),cv2.IMREAD_COLOR);h,w=ref.shape[:2]
   src=np.float32([[0,0],[w-1,0],[w-1,h-1],[0,h-1]])
   dst=np.float32([[180,90],[440,110],[420,370],[165,355]])
   canvas=cv2.warpPerspective(ref,cv2.getPerspectiveTransform(src,dst),(640,480))
   _,jpeg=cv2.imencode('.jpg',canvas);results=detector.detect(jpeg.tobytes())
   self.assertTrue(any(c['kind']==kind for c in results),kind)
  _,blank=cv2.imencode('.jpg',np.zeros((480,640,3),np.uint8));self.assertEqual(detector.detect(blank.tobytes()),[])
 def test_saved_mujoco_render_center_photo_identification(self):
  detector=FacilityDetector()
  for kind in ('COMMS','RADAR','OUTPOST'):
   image=ROOT/'verification'/f'facility-{kind}.jpg'
   if not image.exists():self.skipTest('Saved MuJoCo render fixture unavailable')
   found=detector.detect(image.read_bytes())
   self.assertTrue(any(c['kind']==kind and abs(c['cx']-320)<8 for c in found),kind)
 def test_target_kind_not_row(self):
  state={'slot':'B-1','odom':{'x':2.8,'y':0,'yaw':0},'scan':{'ranges':[1.]*180,'angle_min':-math.pi,'angle_increment':math.tau/180,'range_max':8}}
  candidate={'kind':'RADAR','cx':320,'cy':120,'width':640,'height':480,'xyxy':[280,80,360,180],'area':8000}
  found=select_beacon([candidate],state,{'target':'RED_RADAR'})
  self.assertEqual(found['sector'],'R-S1');self.assertGreater(found['tilt'],0)
 def test_report_deadline_not_restarted(self):
  now=[10.];report=ReportWorkflow(None,clock=lambda:now[0]);report.begin(['R-1','R-2','R-3'],deadline=12.)
  self.assertEqual(report.deadline,12.);now[0]=12.;self.assertFalse(report.tick('reporting'));self.assertTrue(report.failed)
 def test_supervisor_retry_boundary(self):
  self.assertTrue(should_retry(1,2));self.assertFalse(should_retry(1,3));self.assertFalse(should_retry(0,0));self.assertFalse(should_retry(1,0,stopping=True));self.assertFalse(should_retry(1,0,finished=True))
 def test_immutable_bundle_contains_adapters(self):
  # Isolated temporary store, no writes to real deployment registry.
  with tempfile.TemporaryDirectory() as directory:
   import shutil
   root=pathlib.Path(directory);shutil.copytree(ROOT/'workspace',root/'workspace',ignore=shutil.ignore_patterns('__pycache__'))
   store=CodeStore(root);manifest=store.snapshot();path,_=store.version(manifest['id'])
   self.assertIn('facility_detector.py',manifest['files']);self.assertIn('transport_facade.py',manifest['files'])
   (path/'llm_adapter.py').chmod(0o644);(path/'llm_adapter.py').write_text('tampered')
   with self.assertRaises(Exception):store.version(manifest['id'])
if __name__=='__main__':unittest.main()
