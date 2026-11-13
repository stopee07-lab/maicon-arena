"""Behavior regressions for explicitly provisional, local-only fidelity models."""
import copy
import math
import unittest
from collections import Counter
from engine import Engine, APIError, Robot

class FidelityTests(unittest.TestCase):
    def external(self, **kw):
        e=Engine(**kw)
        for r in e.robots.values():r.strategy='external';r.ready=True
        e.control({'action':'prepare'})
        for r in e.robots.values():r.ready=True
        e.control({'action':'start'})
        return e

    def test_100_seed_layout_clearances_reproducibility_and_variation(self):
        signatures=set();counts=set();positions=set()
        for seed in range(100):
            e=Engine(seed=seed);a=e.arena
            self.assertEqual(a,Engine(seed=seed).arena)
            boxes=a['obstacles']
            for b in boxes:
                self.assertGreaterEqual(min(b['x']-b['w']/2,6.12-b['x']-b['w']/2,b['y']-b['h']/2,5-b['y']-b['h']/2),.4-1e-9,(seed,b))
            for i,b in enumerate(boxes):
                for c in boxes[i+1:]:
                    gap=math.hypot(max(0,abs(b['x']-c['x'])-(b['w']+c['w'])/2),max(0,abs(b['y']-c['y'])-(b['h']+c['h'])/2))
                    self.assertGreaterEqual(gap,.4-1e-9,(seed,b,c))
            blue=sorted((round(t['x'],6),round(t['y'],6)) for t in a['targets'] if t['camp']=='BLUE')
            red=sorted((round(6.12-t['x'],6),round(t['y'],6)) for t in a['targets'] if t['camp']=='RED')
            self.assertEqual(blue,red)
            for sector in ('B-S1','B-S2','B-S3','CM','R-S1','R-S2','R-S3'):
                n=len({o['obstacle_id'] for o in a['objects'] if o['sector']==sector})
                self.assertIn(n,(1,2));counts.add(n)
            self.assertTrue(any(b.get('kind')=='decor' for b in boxes))
            signatures.add(tuple((t['id'],t['x'],t['y']) for t in a['targets']))
            positions.add(tuple((b['x'],b['y']) for b in boxes if b.get('kind')=='object'))
        self.assertGreater(len(signatures),5);self.assertGreater(len(positions),50);self.assertEqual(counts,{1,2})

    def test_100_seeds_all_obstacles_and_m3_counts_are_mirrored(self):
        independent_classes=False
        for seed in range(100):
            arena=Engine(seed=seed).arena
            def signature(box,mirror=False):
                return (box['kind'],round(6.12-box['x'] if mirror else box['x'],6),round(box['y'],6),box['w'],box['h'],box['height'])
            self.assertEqual(Counter(signature(b) for b in arena['obstacles']),Counter(signature(b,True) for b in arena['obstacles']),seed)
            for i in (1,2,3):
                blue=[o for o in arena['objects'] if o['sector']==f'B-S{i}']
                red=[o for o in arena['objects'] if o['sector']==f'R-S{i}']
                self.assertEqual(len({o['obstacle_id'] for o in blue}),len({o['obstacle_id'] for o in red}),(seed,i))
                independent_classes |= [o['type'] for o in blue] != [o['type'] for o in red]
        self.assertTrue(independent_classes)

    def test_unsupported_rule_values_rejected_before_execution_or_scoring(self):
        cases=[('capture_dwell_seconds',10),('capture_tie_policy','list_order'),('equal_risk_order','slot_order')]
        for key,value in cases:
            for operation in ('step','score','reset'):
                e=self.external()
                target=e.rule_profile['m6'] if key=='equal_risk_order' else e.rule_profile
                target[key]=value
                with self.subTest(key=key,operation=operation),self.assertRaisesRegex(ValueError,key):
                    if operation=='score':e.score(e.robots['B-1'])
                    elif operation=='step':e.step()
                    else:e.reset()

    def test_unsupported_registered_rule_profile_config_returns_422(self):
        import rule_profile
        name='unsupported-test-only-profile'
        candidate=copy.deepcopy(rule_profile.PROFILES[rule_profile.PROFILE_ID]);candidate['capture_dwell_seconds']=10
        rule_profile.PROFILES[name]=candidate
        try:
            e=Engine()
            with self.assertRaises(APIError) as raised:e.control({'action':'configure','rule_profile':name})
            self.assertEqual(raised.exception.status,422)
            self.assertIn('capture_dwell_seconds',str(raised.exception.detail))
        finally:del rule_profile.PROFILES[name]

    def test_m5_is_independent_of_m1_base(self):
        pairs={}
        for seed in range(100):
            e=Engine(seed=seed)
            m1=tuple(e.answers[s]['base'] for s in sorted(e.answers))
            pairs.setdefault(m1,set()).add(tuple(sorted(e.objectives.items())))
        self.assertTrue(any(len(v)>2 for v in pairs.values()))

    def test_configure_and_export_identity(self):
        e=Engine()
        e.control({'action':'configure','seed':99,'sensor_profile':'ideal','opponent_mode':'privileged'})
        self.assertEqual(e.seed,99)
        identity=e.snapshot()['identity']
        self.assertEqual(identity,e.export()['identity']);self.assertEqual(identity['opponent_mode'],'privileged')
        self.assertIn('provisional',identity['rule_profile']);self.assertIn('environment',e.arena)
        e.control({'action':'prepare'});e.control({'action':'start'})
        with self.assertRaises(APIError) as cm:e.control({'action':'configure','seed':1})
        self.assertEqual(cm.exception.status,409)

    def empty_ray_world(self):
        e=self.external();r=e.robots['B-1'];e.robots={r.slot:r}
        e.arena['obstacles']=[];e.arena['targets']=[];r.x=1;r.y=2;r.yaw=0;r.pan=r.tilt=0
        target=dict(id='RED_COMMS',camp='RED',x=3,y=2,destroyed=False,obstacle_id='target',normal=[-1,0],receiver=dict(x=2.795,y=2,z=.256,normal=[-1,0,0],radius=.04))
        e.arena['targets']=[target];e.arena['obstacles']=[dict(id='target',x=3,y=2,w=.4,h=.4,height=.4)]
        return e,r,target

    def test_3d_front_receiver_hit_miss_backface_and_blocker(self):
        e,r,t=self.empty_ray_world()
        self.assertTrue(callable(getattr(e,'laser_raycast',None)),'3D receiver ray API missing')
        self.assertEqual(e.laser_raycast(r)[1],'target')
        r.y=2.07;self.assertNotEqual(e.laser_raycast(r)[1],'target')
        r.y=2;r.tilt=50;self.assertNotEqual(e.laser_raycast(r)[1],'target')
        r.tilt=0;r.x=4;r.yaw=math.pi;self.assertNotEqual(e.laser_raycast(r)[1],'target')
        r.x=1;r.yaw=0
        e.arena['obstacles'].append(dict(id='block',x=2,y=2,w=.4,h=.4,height=.4))
        self.assertNotEqual(e.laser_raycast(r)[1],'target')

    def test_pitch_positive_up_and_muzzle_origin(self):
        e,r,t=self.empty_ray_world()
        self.assertTrue(callable(getattr(e,'laser_raycast',None)))
        t['receiver']['z']=.34
        r.tilt=math.degrees(math.atan2(.34-.256,2.795-1.07))/.15
        self.assertEqual(e.laser_raycast(r)[1],'target')
        r.tilt=-r.tilt;self.assertNotEqual(e.laser_raycast(r)[1],'target')

    def test_continuous_two_second_hit_and_interrupted_exposure(self):
        e,r,t=self.empty_ray_world();e.robot_command(r,'fire',{})
        for _ in range(39):e.step()
        self.assertFalse(t['destroyed'])
        e.step();self.assertTrue(t['destroyed'])
        e,r,t=self.empty_ray_world();e.robot_command(r,'fire',{})
        for _ in range(25):e.step()
        r.y=2.1;e.step();r.y=2
        for _ in range(25):e.step()
        self.assertFalse(t['destroyed'])

    def test_robot_arm_receiver_body_miss_and_hp_zero_recovers_drive(self):
        e,r,t=self.empty_ray_world();e.arena['targets']=[];e.arena['obstacles']=[]
        victim=Robot('R-1','RED','SIMR0001',3,2,math.pi,'external');e.robots[victim.slot]=victim
        self.assertTrue(callable(getattr(e,'laser_raycast',None)))
        self.assertNotEqual(e.laser_raycast(r)[1],'robot')
        r.y=2.12;self.assertEqual(e.laser_raycast(r)[1],'robot')
        victim.hp=1;e.robot_command(r,'fire',{})
        for _ in range(40):e.step()
        self.assertEqual(victim.hp,0)
        self.assertFalse(e.robot_command(victim,'cmd_vel',dict(linear_x=.1,angular_z=0))['accepted'])
        for _ in range(201):e.step()
        self.assertTrue(e.robot_command(victim,'cmd_vel',dict(linear_x=.1,angular_z=0))['accepted'])

    def test_base_uses_oriented_footprint_sensor_line(self):
        e=self.external();r=e.robots['B-1'];e.robots={r.slot:r}
        b=next(b for b in e.arena['bases'] if b['camp']=='RED');r.x=5.57;r.y=b['y'];r.yaw=0
        self.assertTrue(e._occupied(b),'front body crosses x=5.68 while center remains outside')
        r.x=5.55;self.assertFalse(e._occupied(b))

    def test_capture_event_fraction_and_order_independence(self):
        def run(reverse):
            e=self.external();e.arena['obstacles']=[];e.elapsed=250;e.target_issued='issued'
            b=next(b for b in e.arena['bases'] if b['id']==e.objectives['RED']);rbase=next(b for b in e.arena['bases'] if b['id']==e.objectives['BLUE'])
            blue=e.robots['B-1'];red=e.robots['R-1'];e.robots={blue.slot:blue,red.slot:red}
            blue.x=5.55;blue.y=rbase['y'];blue.yaw=0;blue.v=.4;blue.command_at=e.clock
            red.x=.58;red.y=b['y'];red.yaw=math.pi;red.v=.4;red.command_at=e.clock
            if reverse:e.arena['bases'].reverse();e.robots=dict(reversed(list(e.robots.items())))
            e.sensor_occupied={b['id']:e._occupied(b) for b in e.arena['bases']};e.step()
            self.assertTrue(any(ev['type']=='capture' for ev in e.events))
            ev=next(ev for ev in e.events if ev['type']=='capture')
            return e.winning_capture,ev
        a,ev=run(False);b,ev2=run(True)
        self.assertEqual(a,'BLUE');self.assertEqual(a,b);self.assertEqual(ev['time'],ev2['time'])
        self.assertLess(ev['time'],250.05);self.assertGreater(ev['time'],250)

    def test_default_noisy_sensors_reproducible_and_official_only_omits_extras(self):
        a=Engine(seed=7);b=Engine(seed=7)
        a.clock=b.clock=100;a.io_clock=b.io_clock=100
        sa=a.robot_state(a.robots['B-1']);sb=b.robot_state(b.robots['B-1'])
        self.assertEqual(sa,sb);self.assertLess(sa['battery_pct'],95)
        self.assertNotEqual(sa['odom'],dict(x=0.,y=0.,yaw=0.))
        self.assertIn('measured_at',sa['scan']);self.assertIn('simulation_extras',sa)
        e=Engine(sensor_profile='official_only');s=e.robot_state(e.robots['B-1'])
        for key in ('pan','tilt','ammo','laser'):self.assertNotIn(key,s)
        e=Engine(sensor_profile='ideal');self.assertEqual(e.robot_state(e.robots['B-1'])['odom'],dict(x=0.,y=0.,yaw=0.))

    def test_default_bot_never_reads_truth_or_submits_hidden_m6(self):
        e=Engine();e.control({'action':'prepare'});e.control({'action':'start'})
        def forbidden(*args):raise AssertionError('privileged truth used by default bot')
        e.truth_detection=forbidden
        e.elapsed=20
        for _ in range(8):e.step()
        self.assertTrue(all('detection' not in r.reports for r in e.robots.values()))
        e.control({'action':'finish'})
        for _ in range(160):e.step()
        self.assertTrue(all('situation' not in r.reports for r in e.robots.values()))

    def test_capture_at_final_substep_before_deadline(self):
        e=self.external();e.arena['obstacles']=[];e.elapsed=299.95;e.target_issued='issued'
        r=e.robots['B-1'];e.robots={r.slot:r};b=next(b for b in e.arena['bases'] if b['id']==e.objectives['BLUE'])
        r.x=5.55;r.y=b['y'];r.yaw=0;r.v=.4;r.command_at=e.clock
        e.sensor_occupied={b['id']:False};e.step()
        self.assertEqual(e.winning_capture,'BLUE')
        self.assertLess(next(ev['event_time'] for ev in e.events if ev['type']=='capture'),300)

    def test_earlier_red_capture_beats_blue_list_order_and_equal_tie_is_explicit(self):
        for red_x,expected in ((.57,'RED'),(.57+.005,'BLUE')):
            e=self.external();e.arena['obstacles']=[];e.elapsed=250;e.target_issued='issued'
            blue=e.robots['B-1'];red=e.robots['R-1'];e.robots={blue.slot:blue,red.slot:red}
            b=next(b for b in e.arena['bases'] if b['id']==e.objectives['RED']);rb=next(b for b in e.arena['bases'] if b['id']==e.objectives['BLUE'])
            blue.x=5.545;blue.y=rb['y'];blue.yaw=0;blue.v=.4;blue.command_at=e.clock
            red.x=red_x;red.y=b['y'];red.yaw=math.pi;red.v=.4;red.command_at=e.clock
            e.sensor_occupied={base['id']:e._occupied(base) for base in e.arena['bases']};e.step()
            self.assertEqual(e.winning_capture,expected)
            self.assertIn('tie_policy',next(ev for ev in e.events if ev['type']=='capture'))

    def test_scan_measurement_time_not_read_time_and_physics_domain_preserved(self):
        e=Engine(sensor_profile='ideal');r=e.robots['B-1'];e.clock=.31
        first=e.scan(r);self.assertEqual(first['measured_at'],.31)
        e.clock=.35;self.assertEqual(e.scan(r)['measured_at'],.31)
        e.physical_active=True;e.clock=2
        e.physical_scan[r.slot]=dict(ranges=[1.],angle_min=0.,angle_increment=.1,range_max=8.,captured_sim_time=15.,frame_seq=12,captured_at='wallstamp')
        s=e.scan(r)
        self.assertEqual(s['measured_at'],15.);self.assertEqual(s['clock_domain'],'physics_seconds')
        self.assertEqual(s['received_at'],2.);self.assertEqual(s['captured_at'],'wallstamp')
        e.clock=3.;self.assertEqual(e.scan(r)['received_at'],2.)

    def test_sensor_bot_records_own_observation_and_actions(self):
        e=Engine();e.control({'action':'prepare'});e.control({'action':'start'});e.step()
        log=e.export().get('opponent_policy_log',[])
        self.assertTrue(log)
        self.assertTrue(all('observation' in row and 'commands' in row for row in log))
        self.assertTrue(all('arena' not in row['observation'] and 'opponents' not in row['observation'] for row in log))

    def test_api_phase_replacement_types_and_scoring_regression(self):
        e=self.external();r=e.robots['B-1'];code={'mission_code':r.mission_code}
        e.report('interpretation',{**code,**e.answers[r.slot]});self.assertEqual(e.score(r)['m1'],10)
        e.report('interpretation',{**code,'target':e.answers[r.slot]['target']});self.assertEqual(e.score(r)['m1'],3)
        with self.assertRaises(APIError):e.report('detection',{**code,'sectors':[{'sector':'CM','objects':[{'type':'TANK','count':True}]}]})
        e.report('detection',{**code,'sectors':e.truth_detection(r)});self.assertEqual(e.score(r)['m3'],30)
        for t in e.arena['targets']:t['destroyed']=True
        self.assertEqual(e.score(r)['m2'],15);self.assertEqual(e.score(r)['m4'],15)
        with self.assertRaises(APIError):e.report('situation',{**code,'ranking':['R-1']})
        e.control({'action':'finish'})
        with self.assertRaises(APIError):e.report('detection',{**code,'sectors':e.truth_detection(r)})
        e.report('situation',{**code,'ranking':['R-1','R-2','R-3']});self.assertEqual(e.score(r)['m6'],15)

if __name__=='__main__':unittest.main()
