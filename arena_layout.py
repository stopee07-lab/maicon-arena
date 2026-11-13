"""Seeded constrained fixture family; distances validated on every generated layout.
This is a feasible randomized training distribution, not organizer placement truth.
All physical boxes mirror. M3 count/placement is sampled once per sector pair;
class labels use a separate random stream and remain independent between camps.
"""
import math
import random
LAYOUT_VERSION='mirrored-constrained-v3'
OBJECT_TYPES=('MILITARY_TRUCK','MORTAR','TANK','MISSILE','AMMO_BOX','DRUM')
TARGET_TYPES=('COMMS','OUTPOST','RADAR')
BASE_TYPES=('COMMAND_POST','MAINTENANCE','SUPPLY_DEPOT')

def validate(arena):
    boxes=arena['obstacles']
    for i,b in enumerate(boxes):
        gap=min(b['x']-b['w']/2,arena['width']-b['x']-b['w']/2,b['y']-b['h']/2,arena['height']-b['y']-b['h']/2)
        if gap<.4-1e-9:raise ValueError(('wall clearance',b['id'],gap))
        for c in boxes[i+1:]:
            gap=math.hypot(max(0,abs(b['x']-c['x'])-(b['w']+c['w'])/2),max(0,abs(b['y']-c['y'])-(b['h']+c['h'])/2))
            if gap<.4-1e-9:raise ValueError(('box clearance',b['id'],c['id'],gap))
    return True

def make_arena(seed,geometry):
    rng=random.Random(f'layout-v3:{seed}')
    class_rng=random.Random(f'classes-v3:{seed}')
    paired_positions=[]
    for i,slots in enumerate([[(2.1,4.15),(1.2,3.53)],[(2.1,2.08),(2.1,2.92)],[(2.1,.85),(1.2,1.47)]]):
        count=rng.choice((1,2))
        selected=rng.sample(slots,count) if i==1 else slots[:count]
        paired_positions.append([(x+rng.uniform(-.015,.015),y+rng.uniform(-.008,.008)) for x,y in selected])
    a=dict(width=6.12,height=5.,obstacles=[],targets=[],bases=[],objects=[],sectors=[])
    def box(oid,x,y,kind,**extra):
        a['obstacles'].append(dict(id=oid,x=x,y=y,w=.4,h=.4,height=.4,kind=kind,**extra))
    def objects(oid,x,y,sector):
        box(oid,x,y,'object')
        for face,(dx,dy) in enumerate([(-1,0),(1,0),(0,-1),(0,1)]):
            a['objects'].append(dict(id=f'{oid}-{face}',type=class_rng.choice(OBJECT_TYPES),sector=sector,x=x+dx*.205,y=y+dy*.205,normal=[dx,dy],obstacle_id=oid))
    for camp,c,x0 in [('BLUE','B',.65),('RED','R',3.46)]:
        types=class_rng.sample(TARGET_TYPES,3)
        for i in range(3):
            y=4.15-i*1.65;sector=f'{c}-S{i+1}'
            a['sectors'].append(dict(name=sector,x=x0,y=y-.75,w=2.01,h=1.5))
            bx=.24 if c=='B' else 5.88
            a['bases'].append(dict(id=f'{camp}_{BASE_TYPES[i]}',camp=camp,x=bx,y=y,w=.4,h=1.2,sensor_line=dict(x=.44 if c=='B' else 5.68,y_min=y-.6,y_max=y+.6)))
            tx=1.2 if c=='B' else 4.92;ty=[4.35,2.5,.65][i];normal=1 if c=='B' else -1;tid=f'{camp}_{types[i]}'
            receiver=dict(x=tx+normal*.205,y=ty,z=geometry['target_receiver_z'],normal=[normal,0,0],radius=geometry['target_receiver_radius'])
            a['targets'].append(dict(id=tid,camp=camp,x=tx,y=ty,normal=[normal,0],receiver=receiver,destroyed=False,obstacle_id=tid))
            box(tid,tx,ty,'target')
            # Exact paired geometry; class labels above use an independent stream.
            for j,(x,oy) in enumerate(paired_positions[i]):
                if c=='R':x=6.12-x
                objects(f'objects-{c}{i+1}-{j}',x,oy,sector)
    a['sectors'].append(dict(name='CM',x=2.76,y=.1,w=.6,h=4.8))
    for i,y in enumerate(rng.sample([1.6,3.4],rng.choice((1,2)))):
        objects(f'objects-CM-{i}',3.06,y+rng.uniform(-.06,.06),'CM')
    for i,y in enumerate((.65,4.35)):
        box(f'decor-{i}',3.06,y,'decor',decor_type=rng.choice(('tree','wall','trench','ruins')))
    a['environment']=dict(seed=seed,layout_version=LAYOUT_VERSION,minimum_box_clearance_m=.4,placement_status='mirrored physical geometry; independent class labels; randomized constrained family, not measured venue layout',geometry_assumptions=geometry.copy())
    validate(a)
    return a
