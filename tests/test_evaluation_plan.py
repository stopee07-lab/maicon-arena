import unittest
from run_evaluation import plan,SLOTS
class EvaluationTests(unittest.TestCase):
 def test_all_slots_cross_seed_and_condition(self):
  rows=plan([1,2],['mild','stress'],SLOTS);self.assertEqual(len(rows),24)
  for seed in (1,2):
   for profile in ('mild','stress'):self.assertEqual({x['slot'] for x in rows if x['seed']==seed and x['sensor_profile']==profile},set(SLOTS))
