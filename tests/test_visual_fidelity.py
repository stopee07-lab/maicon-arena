import sys,unittest
from pathlib import Path
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from physics_world import build_world
from engine import Engine
class VisualTests(unittest.TestCase):
 def test_facility_images_replace_gold_beacon(self):
  e=Engine();x,p=build_world(e.arena,e.snapshot()['robots']);tree=ET.fromstring(x)
  self.assertFalse(any(g.attrib.get('name','').startswith('beacon_') for g in tree.iter('geom')))
  self.assertEqual(len([g for g in tree.iter('geom') if g.attrib.get('name','').startswith('facility_')]),6)
  self.assertEqual(len([g for g in tree.iter('geom') if g.attrib.get('name','').startswith('receiver_')]),6)
 def test_robot_receivers_visible(self):
  e=Engine();x,_=build_world(e.arena,e.snapshot()['robots']);tree=ET.fromstring(x)
  self.assertEqual(len([g for g in tree.iter('geom') if '_receiver_' in g.attrib.get('name','')]),12)
 def test_visual_profile_deterministic(self):
  from visual_profile import apply_camera_profile
  import numpy as np
  a=np.full((480,640,3),180,dtype=np.uint8)
  b=apply_camera_profile(a,{'profile':'stress','seed':3},12)
  self.assertTrue(np.array_equal(b,apply_camera_profile(a,{'profile':'stress','seed':3},12)))
  self.assertFalse(np.array_equal(a,b))
if __name__=='__main__':unittest.main()
