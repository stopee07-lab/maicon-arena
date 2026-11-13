import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from subgame import Trial
class SubgameTests(unittest.TestCase):
 def test_first_movement_starts_timer_and_two_attempts(self):
  t=Trial();t.arm(10);t.command('cmd_vel',{'linear_x':0,'angular_z':0},11);self.assertIsNone(t.started)
  t.command('gimbal',{'pan':20,'tilt':0},12);self.assertEqual(t.started,12)
  self.assertEqual(t.update(15,True)['elapsed'],3);t.arm(20);t.command('cmd_vel',{'linear_x':0,'angular_z':1},20)
  self.assertEqual(t.update(80,False)['status'],'timeout')
  with self.assertRaises(ValueError):t.arm(90)
 def test_hit_before_command_is_not_success(self):
  t=Trial();t.arm(0);self.assertEqual(t.update(20,True)['status'],'armed')
 def test_deadline_hit(self):
  t=Trial();t.arm(0);t.command('cmd_vel',{'linear_x':0,'angular_z':1},0)
  self.assertEqual(t.update(60.01,True)['status'],'timeout')
