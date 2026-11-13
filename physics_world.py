"""MuJoCo MJCF for the documented arena dimensions; uncalibrated physical parameters."""
from pathlib import Path
from xml.sax.saxutils import escape
import math

ROOT=Path(__file__).resolve().parent
CLASSES=['military_truck','mortar','tank','missile','ammo_box','drum']

def build_world(arena,robots):
    assets=['<texture name="sky" type="skybox" builtin="gradient" rgb1=".55 .65 .75" rgb2=".88 .90 .92" width="512" height="3072"/>']
    for name in CLASSES:
        file=escape(str(ROOT/'web/assets/object-photos'/f'{name}.png'),{'"':'&quot;'})
        assets.append(f'<texture name="tex_{name}" type="2d" file="{file}"/><material name="mat_{name}" texture="tex_{name}" texrepeat="1 1" texuniform="false" roughness="1"/>')
    for kind in ['OUTPOST','RADAR','COMMS']:
        file=escape(str(ROOT/'web/assets/facilities'/f'{kind}.png'),{'"':'&quot;'})
        assets.append(f'<texture name="facility_tex_{kind}" type="2d" file="{file}"/><material name="facility_mat_{kind}" texture="facility_tex_{kind}" texrepeat="1 1" texuniform="false"/>')
    for kind in ['tree','wall','trench','ruins']:
        file=escape(str(ROOT/'web/assets/decor'/f'{kind}.png'),{'"':'&quot;'})
        assets.append(f'<texture name="decor_tex_{kind}" type="2d" file="{file}"/><material name="decor_mat_{kind}" texture="decor_tex_{kind}" texrepeat="1 1" texuniform="false"/>')
    floor=escape(str(ROOT/'web/assets/arena-reference.png'),{'"':'&quot;'})
    assets.append(f'<texture name="floor" type="2d" file="{floor}"/><material name="floor" texture="floor" texrepeat="1 1" texuniform="false" reflectance="0"/>')
    world=['<light name="key" pos="1 1 4" dir=".2 .2 -1" diffuse=".8 .8 .8" ambient=".35 .35 .35"/>',
           '<light name="fill" pos="5 4 3" dir="-.2 -.2 -1" diffuse=".5 .5 .5" castshadow="false"/>',
           '<geom name="floor" type="plane" pos="3.06 2.5 0" size="3.06 2.5 .1" material="floor" friction=".8 .005 .0001"/>']
    for name,x,y,sx,sy in [('north',3.06,5.02,3.08,.02),('south',3.06,-.02,3.08,.02),('west',-.02,2.5,.02,2.5),('east',6.14,2.5,.02,2.5)]:
        world.append(f'<geom name="wall_{name}" type="box" pos="{x} {y} .2" size="{sx} {sy} .2" rgba=".15 .17 .18 1"/>')
    for o in arena['obstacles']:
        world.append(f'<geom name="box_{o["id"]}" type="box" pos="{o["x"]} {o["y"]} .2" size="{o["w"]/2} {o["h"]/2} .2" rgba=".25 .29 .25 1"/>')
    panels={}
    for i,o in enumerate(arena['objects']):
        nx,ny=o.get('normal',[-1,0]);name=f'photo_{i}';kind=o['type'].lower();panels[name]=dict(o,class_id=CLASSES.index(kind))
        world.append(f'<geom name="{name}" type="plane" pos="{o["x"]} {o["y"]} .24" xyaxes="{-ny} {nx} 0 0 0 1" size=".14 .14 .001" material="mat_{kind}" contype="0" conaffinity="0" group="2"/>')
    for o in arena['obstacles']:
        if o.get('kind')=='decor':
            kind=o.get('decor_type','ruins')
            for j,(nx,ny) in enumerate([(-1,0),(1,0),(0,-1),(0,1)]):
                world.append(f'<geom name="decor_{o["id"]}_{j}" type="plane" pos="{o["x"]+nx*.201} {o["y"]+ny*.201} .2" xyaxes="{-ny} {nx} 0 0 0 1" size=".19 .19 .001" material="decor_mat_{kind}" contype="0" conaffinity="0" group="2"/>')
    for t in arena['targets']:
        nx,ny=t.get('normal',[-1 if t['camp']=='RED' else 1,0]);kind=t['id'].split('_',1)[1]
        rec=t.get('receiver',dict(x=t['x']+nx*.204,y=t['y']+ny*.204,z=.34,radius=.04))
        world.append(f'<geom name="facility_{t["id"]}" type="plane" pos="{t["x"]+nx*.201} {t["y"]+ny*.201} .2" xyaxes="{-ny} {nx} 0 0 0 1" size=".19 .19 .001" material="facility_mat_{kind}" contype="0" conaffinity="0" group="2"/>')
        world.append(f'<geom name="receiver_{t["id"]}" type="cylinder" pos="{rec["x"]} {rec["y"]} {rec["z"]}" xyaxes="{-ny} {nx} 0 0 0 1" size="{rec["radius"]} .003" rgba=".92 .92 .92 1" contype="0" conaffinity="0"/>')
        world.append(f'<geom name="hit_led_{t["id"]}" type="box" pos="{t["x"]} {t["y"]} .415" size=".06 .035 .015" rgba=".18 .02 .02 1" contype="0" conaffinity="0"/>')
    # Fixed UAV and support structure, not an entrant-controlled flying robot.
    for x in [-.16,6.28]:
        world.append(f'<geom type="cylinder" pos="{x} 2.5 1.35" size=".035 1.35" rgba=".18 .2 .2 1"/>')
    world += ['<geom type="box" pos="3.06 2.5 2.72" size="3.25 .025 .025" rgba=".18 .2 .2 1"/>',
              '<body name="fixed_uav" pos="3.06 2.5 2.5"><geom type="box" size=".13 .09 .04" rgba=".17 .18 .2 1" contype="0" conaffinity="0"/>',
              '<camera name="drone" pos="0 0 -.07" quat="1 0 0 0" fovy="105"/>']
    for x in [-.23,.23]:
        for y in [-.23,.23]:
            world.append(f'<geom type="capsule" fromto="0 0 0 {x} {y} 0" size=".018" rgba=".12 .13 .14 1" contype="0" conaffinity="0"/><geom type="cylinder" pos="{x} {y} .02" size=".095 .006" rgba=".18 .2 .2 .7" contype="0" conaffinity="0"/>')
    world.append('</body>')
    world.append('<camera name="training" pos="1 1 .24" xyaxes="0 -1 0 0 0 1" fovy="153.45"/>')
    actuators=[]
    for r in robots:
        slot=r['slot'];blue=r['camp']=='BLUE';color='.2 .48 .9 1' if blue else '.88 .2 .15 1';yaw=r['yaw'];quat=f'{math.cos(yaw/2)} 0 0 {math.sin(yaw/2)}'
        world.append(f'<body name="{slot}" pos="{r["x"]} {r["y"]} .058" quat="{quat}"><freejoint name="{slot}_root"/><geom name="{slot}_chassis" type="box" pos="0 0 .035" size=".115 .085 .025" mass="2.3" rgba=".13 .16 .17 1"/><geom type="box" pos=".09 0 .07" size=".02 .085 .02" mass=".02" rgba="{color}"/>')
        for side,y in [('left',.098),('right',-.098)]:
            for i,x in enumerate([-.085,0,.085]):
                name=f'{slot}_{side}_{i}'
                world.append(f'<body pos="{x} {y} -.018"><joint name="{name}" type="hinge" axis="0 1 0" damping=".015"/><geom type="cylinder" size=".040 .017" quat=".7071068 .7071068 0 0" mass=".07" friction=".9 .003 .0001" rgba=".04 .045 .05 1"/></body>')
                actuators.append(f'<velocity name="{name}" joint="{name}" kv=".5" ctrllimited="true" ctrlrange="-25 25" forcelimited="true" forcerange="-.7 .7"/>')
        for y in [-.065,.065]:
            world.append(f'<geom type="cylinder" pos="-.065 {y} .18" size=".008 .12" mass=".01" rgba=".08 .08 .09 1"/><geom type="cylinder" pos="-.065 {y} .22" size=".012 .05" mass=".01" rgba="{color}"/>')
            for band in range(int(slot[-1])):
                world.append(f'<geom type="cylinder" pos="-.065 {y} { .19+band*.02}" size=".0125 .004" mass=".001" rgba=".03 .03 .03 1" contype="0" conaffinity="0"/>')
        # Draw the exact provisional receiver disks used by authoritative ray tests.
        for receiver in r.get('receivers',[]):
            dx=receiver['x']-r['x'];dy=receiver['y']-r['y']
            lx=dx*math.cos(yaw)+dy*math.sin(yaw);ly=-dx*math.sin(yaw)+dy*math.cos(yaw)
            world.append(f'<geom name="{slot}_receiver_{receiver["id"]}" type="cylinder" pos="{lx} {ly} {receiver["z"]-.058}" size="{receiver["radius"]} .003" xyaxes="0 1 0 0 0 1" rgba=".9 .9 .85 1" mass=".001" contype="0" conaffinity="0"/>')
        # LED placement is a configurable practice representation, not measured hardware.
        for j in range(8):
            world.append(f'<geom name="{slot}_ammo_led_{j}" type="sphere" pos="{-.07+j*.02} -.086 .105" size=".006" rgba=".95 .8 .1 1" contype="0" conaffinity="0"/>')
        world.append(f'<geom name="{slot}_hp_led" type="sphere" pos="-.09 0 .13" size=".012" rgba=".1 .3 1 1" contype="0" conaffinity="0"/>')
        world.append(f'<geom type="cylinder" pos=".065 0 .12" size=".035 .015" mass=".03" rgba=".12 .13 .14 1"/><site name="{slot}_imu" pos="0 0 .08"/>')
        world.append(f'<body name="{slot}_pan" pos="0 0 .18"><joint name="{slot}_pan" axis="0 0 1" limited="true" range="-50 50" damping=".01"/><geom type="cylinder" size=".025 .025" mass=".05" rgba=".15 .15 .17 1"/><body name="{slot}_tilt"><joint name="{slot}_tilt" axis="0 -1 0" limited="true" range="-15 15" damping=".01"/><geom type="box" pos=".025 0 .018" size=".04 .025 .023" mass=".05" rgba=".08 .09 .1 1"/><camera name="{slot}_camera" pos=".07 0 .018" xyaxes="0 -1 0 0 0 1" fovy="153.45"/></body></body></body>')
        for axis in ['pan','tilt']:
            actuators.append(f'<position name="{slot}_{axis}" joint="{slot}_{axis}" kp="20" kv=".1" forcelimited="true" forcerange="-.2 .2"/>')
    xml=f'''<mujoco model="MAICON Mac physical training arena">
    <compiler angle="degree" autolimits="true"/>
    <option timestep=".002" gravity="0 0 -9.81" integrator="implicitfast" solver="Newton" iterations="30"/>
    <visual><headlight diffuse=".8 .8 .8" ambient=".45 .45 .45" specular="0 0 0"/><global offwidth="1280" offheight="960"/><quality shadowsize="2048" offsamples="2"/><map znear=".0005" zfar="30"/></visual>
    <default><geom solref=".015 1" solimp=".9 .95 .001" condim="3"/></default>
    <asset>{''.join(assets)}</asset><worldbody>{''.join(world)}</worldbody><actuator>{''.join(actuators)}</actuator></mujoco>'''
    return xml,panels
