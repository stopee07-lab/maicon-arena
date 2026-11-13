import unittest,sys,json,copy,importlib.util
from pathlib import Path
P=Path(__file__).resolve().parent
sys.path.insert(0,str(P.parent/'workspace'))
class PolicyTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  spec=importlib.util.find_spec('team_node');cls.policy=__import__('team_node') if spec else None
 def test_regression_true_printed_photos_are_not_discarded(self):
  self.assertIsNotNone(self.policy,'Missing explicit tested perception fusion policy')
  for row in json.loads((P/'regression-image-cases.json').read_text()):
   d=row['detection'];answer=self.policy.photo_decision(d);self.assertTrue(answer['accepted']);self.assertEqual(answer['type'],d['type'])
 def test_rejects_unconfirmed_or_disagreeing_photos(self):
  self.assertIsNotNone(self.policy)
  cases=[dict(type='tank',confidence=.99,photo=None),dict(type='tank',confidence=.99,photo=dict(type='mortar',score=.7,margin=.01,accepted=False)),dict(type='tank',confidence=.99,photo=dict(type='tank',score=.3,margin=.2,accepted=False)),dict(type='tank',confidence=.60,photo=dict(type='tank',score=.7,margin=.03,accepted=False))]
  for d in cases:self.assertFalse(self.policy.photo_decision(d)['accepted'])
 def states(self):
  base=dict(measured_at=10.,odom=dict(x=0.,y=0.,yaw=0.),scan_registration=dict(age_s=.1),camera_available=True)
  current=copy.deepcopy(base);current['measured_at']=10.2;current['odom']['x']=.02
  return base,current
 def test_accepts_fresh_slow_registered_motion(self):
  self.assertIsNotNone(self.policy);previous,current=self.states();self.assertTrue(self.policy.moving_evidence(current,previous,100.,100.2)['accepted'])
 def test_rejects_stale_frames_scan_fast_motion_rotation_and_missing_pose(self):
  self.assertIsNotNone(self.policy);previous,current=self.states()
  for alteration in ['stale_camera','stale_scan','speed','turn','no_registration','no_previous']:
   c=copy.deepcopy(current);p=copy.deepcopy(previous);captured=100.;now=100.2
   if alteration=='stale_camera':now=101.
   if alteration=='stale_scan':c['scan_registration']['age_s']=.6
   if alteration=='speed':c['odom']['x']=.3
   if alteration=='turn':c['odom']['yaw']=.5
   if alteration=='no_registration':c['scan_registration']=None
   if alteration=='no_previous':p=None
   self.assertFalse(self.policy.moving_evidence(c,p,captured,now)['accepted'],alteration)
class PipelineTests(unittest.TestCase):
 def test_three_real_regression_detections_reach_one_report_and_client_payload(self):
  from unittest.mock import patch
  import team_node as node
  from team_node import photo_decision
  from robot_client import TeamClient
  detection=json.loads((P/'regression-image-cases.json').read_text())[0]['detection']
  self.assertFalse(detection['photo']['accepted'])
  decision=photo_decision(detection);store=node.ObservationStore()
  for i in range(3):
   with patch.object(node.time,'monotonic',return_value=10+i*.2):
    store.add(decision['type'],4.1,4.0,decision['confidence'],('camera',i))
  report=node.m3_report_snapshot(store,'BLUE',{'target':'RED_COMMS'})
  self.assertEqual(report,[{'sector':'R-S1','objects':[{'type':decision['type'],'count':1}]}])
  client=TeamClient('http://127.0.0.1:1','fixture-token')
  captured={}
  def request(path,**kw):captured.update(path=path,payload=kw['payload']);return {'accepted':True,'received':kw['payload']}
  with patch.object(client,'_request',side_effect=request):reply=client.report_post('detection',{'sectors':report})
  self.assertTrue(reply['accepted']);self.assertEqual(captured['path'],'/api/report/detection');self.assertEqual(captured['payload'],{'sectors':report})
 def test_explicit_observation_time_replays_without_wall_clock(self):
  from unittest.mock import patch
  import team_node as node
  store=node.ObservationStore()
  import inspect
  self.assertIn('observed_at',inspect.signature(store.add).parameters)
  with patch.object(node.time,'monotonic',return_value=9000.):
   for i in range(3):store.add('ammo_box',4.1,4.0,.95,str(i),observed_at=10+i*.2)
  self.assertEqual(len(store.confirmed()),1)
  self.assertEqual(store.tracks[0]['last_seen'],10.4)
 def test_duplicate_frames_and_unstable_locations_do_not_confirm(self):
  from unittest.mock import patch
  import team_node as node
  for points,frames in [([(4.1,4.)]*4,['same']*4), ([(4.,4.),(4.15,4.),(4.23,4.)],['a','b','c'])]:
   store=node.ObservationStore()
   for i,(point,frame) in enumerate(zip(points,frames)):
    with patch.object(node.time,'monotonic',return_value=10+i*.2):store.add('ammo_box',*point,.95,frame)
   self.assertFalse(store.confirmed())

if __name__=='__main__':unittest.main()
