import math
import unittest
from opponents import sensor_policy

def observation(now=1, slot='R-2', x=0, y=0):
    return dict(slot=slot,measured_at=now,odom=dict(x=x,y=y,yaw=0),
                scan=dict(ranges=[8.]*181,angle_min=-math.pi,angle_increment=math.tau/180,measured_at=now,received_at=now))

class Strategies(unittest.TestCase):
    def test_five_roles_have_distinct_goals(self):
        goals=[]
        for role in ('scout','defender','hunter','capturer','balanced'):
            _,m=sensor_policy(observation(),{}, {},role)
            goals.append(tuple(m['goal_world']))
        self.assertEqual(len(set(goals)),5)

    def test_capture_uses_only_published_m5_not_m1_base(self):
        order={'accepted':True,'order':{'tactical_command':'후속 거점 BLUE_SUPPLY_DEPOT'}}
        _,before=sensor_policy(observation(239),order,{},'capturer')
        order['target']={'accepted':True,'order':{'objective':'BLUE_COMMAND_POST'}}
        _,after=sensor_policy(observation(241),order,before,'capturer')
        self.assertNotEqual(before['goal_world'],after['goal_world'])
        self.assertEqual(after['goal_world'],[.28,4.15])
        self.assertEqual(after['intent'],'capture_published_target')

    def test_stale_sensor_stops_every_role(self):
        o=observation(20);o['scan']['received_at']=1
        for role in ('scout','defender','hunter','capturer','balanced'):
            commands,m=sensor_policy(o,{}, {},role)
            self.assertEqual(commands[0],('cmd_vel',dict(linear_x=0.,angular_z=0.)))

    def test_recovery_never_reverses_into_rear_obstacle(self):
        o=observation(10);o['scan']['ranges']=[.2]*181
        commands,m=sensor_policy(o,{}, {'anchor':[0,0],'anchor_at':0},'scout')
        self.assertGreaterEqual(commands[0][1]['linear_x'],0)

    def test_defender_guard_does_not_trigger_stuck_recovery(self):
        # R-2 starts at (5.84,2.5), facing negative x; defend goal x=4.67.
        o=observation(40,x=1.17,y=0)
        commands,m=sensor_policy(o,{}, {'anchor':[1.17,0],'anchor_at':0},'defender')
        self.assertEqual(m['intent'],'guard')
        self.assertEqual(commands[0][1]['linear_x'],0)

if __name__=='__main__':unittest.main()
