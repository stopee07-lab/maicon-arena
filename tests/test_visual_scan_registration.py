import sys,pathlib,unittest,math
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'workspace'))
from team_node import detection_position,observed_block_center

def state():
 return dict(slot='B-1',odom=dict(x=.1,y=0.,yaw=.1),pan=0,scan=dict(ranges=[1.]*181,angle_min=-math.pi,angle_increment=math.tau/180,range_max=8),scan_registration=dict(odom=dict(x=0.,y=0.,yaw=0.)))
class Registration(unittest.TestCase):
 def test_missing_registered_scan_pose_defers_localization(self):
  s=state();s['scan_registration']=None
  self.assertIsNone(detection_position({'xyxy':[310,200,330,220]},s))
  self.assertIsNone(observed_block_center((1.345,4.15),s))
 def test_image_bearing_uses_registered_lidar_origin(self):
  s=state();p=detection_position({'xyxy':[310,200,330,220]},s)
  self.assertAlmostEqual(p[0],.345+math.cos(.1),places=6)
  self.assertAlmostEqual(p[1],4.15+math.sin(.1),places=6)
 def test_block_face_fit_uses_scan_pose_not_current_pose(self):
  s=state();s['odom']['yaw']=math.pi/2
  # Wall face is x=1.345 in the LiDAR acquisition frame.
  s['scan']['ranges']=[1./math.cos(-math.pi+i*math.tau/180) if abs(-math.pi+i*math.tau/180)<.3 else None for i in range(181)]
  p=observed_block_center((1.345,4.15),s)
  self.assertIsNotNone(p);self.assertAlmostEqual(p[0],1.545,places=6);self.assertAlmostEqual(p[1],4.15,places=6)
