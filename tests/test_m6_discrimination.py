import unittest
from engine import Engine
class M6Discrimination(unittest.TestCase):
 def test_distinct_threat_wrong_rank_is_not_full_score(self):
  e=Engine();e.phase='reporting';r=e.robots['B-1'];r.ready=True;e.robots['R-1'].hp=4;e.robots['R-1'].ammo=8;e.robots['R-2'].hp=2;e.robots['R-2'].ammo=4;e.robots['R-3'].hp=0;e.robots['R-3'].ammo=0
  r.reports['situation']={'ranking':['R-1','R-2','R-3']};self.assertEqual(e.score(r)['m6'],15)
  r.reports['situation']={'ranking':['R-3','R-2','R-1']};self.assertEqual(e.score(r)['m6'],5)
 def test_equal_risks_do_not_measure_ranking_skill(self):
  e=Engine();e.phase='reporting';r=e.robots['B-1'];r.ready=True
  for order in (['R-1','R-2','R-3'],['R-3','R-1','R-2']):r.reports['situation']={'ranking':order};self.assertEqual(e.score(r)['m6'],15)
