import sys,pathlib,unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'workspace'))
from team_node import ObservationStore
class Confirmation(unittest.TestCase):
 def add(self,s,t,frame,x=3.06,y=1.45):
  with patch('team_node.time.monotonic',return_value=t):s.add('missile',x,y,.9,frame)
 def test_isolated_detections_minutes_apart_do_not_accumulate_to_confirmation(self):
  s=ObservationStore()
  for t,f in [(1,'a'),(100,'b'),(200,'c')]:self.add(s,t,f)
  self.assertFalse(s.confirmed())
 def test_three_recent_distinct_consistent_frames_confirm_and_persist(self):
  s=ObservationStore()
  for i in range(3):self.add(s,1+i*.2,str(i))
  with patch('team_node.time.monotonic',return_value=300):self.assertEqual(len(s.confirmed()),1)
 def test_reused_frame_never_confirms(self):
  s=ObservationStore()
  for i in range(5):self.add(s,1+i*.2,'cached')
  self.assertFalse(s.confirmed())
 def test_displaced_estimates_need_stable_localization(self):
  s=ObservationStore()
  for i,x in enumerate((2.90,3.10,3.23)):self.add(s,1+i*.2,str(i),x=x)
  self.assertFalse(s.confirmed())
