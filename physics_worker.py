#!/usr/bin/env python3
"""MuJoCo physics and offscreen camera owner; all GL work stays on this thread."""
import argparse,base64,io,json,math,signal,time,urllib.request,traceback
from datetime import datetime,timezone,timedelta
from pathlib import Path
import mujoco
import numpy as np
from PIL import Image
from physics_world import CLASSES

class Runtime:
    def __init__(self,c):
        self.c=c;self.m=mujoco.MjModel.from_xml_string(c['world_xml']) if c.get('world_xml') else mujoco.MjModel.from_xml_path(c['world']);self.d=mujoco.MjData(self.m)
        self.r=mujoco.Renderer(self.m,height=480,width=640);self.dr=mujoco.Renderer(self.m,height=960,width=1280)
        self.slots=[x['slot'] for x in c['robots']];self.seq=0;self.running=True;self.commands=[];self.capture=None
        self.gids={mujoco.mj_name2id(self.m,mujoco.mjtObj.mjOBJ_GEOM,k):v for k,v in c['panels'].items()}
        self.requested_cameras=[]
        self.reset(c['robots']);self.last_drone=-9;self.last_camera=-9;self.last_other={};self.last_scan=-9;self.scans={}
    def reset(self,robots):
        mujoco.mj_resetData(self.m,self.d)
        for r in robots:
            adr=self.m.jnt_qposadr[self.m.joint(r['slot']+'_root').id];yaw=r['yaw']
            self.d.qpos[adr:adr+7]=[r['x'],r['y'],.058,math.cos(yaw/2),0,0,math.sin(yaw/2)]
            # Scene restore also retains the observed camera orientation.
            for axis,target in [('pan',-math.radians(r.get('pan',0)*.5)),('tilt',math.radians(r.get('tilt',0)*.15))]:
                self.d.joint(r['slot']+'_'+axis).qpos[0]=target
                self.d.ctrl[self.m.actuator(r['slot']+'_'+axis).id]=target
        mujoco.mj_forward(self.m,self.d);self.commands=[];self.scans={};self.last_drone=-9;self.last_camera=-9;self.last_other={};self.last_scan=-9
    def encode(self,array):
        stream=io.BytesIO();Image.fromarray(array).save(stream,'JPEG',quality=91)
        return dict(image_base64=base64.b64encode(stream.getvalue()).decode(),captured_at=datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='milliseconds'),seq=self.seq)
    def render(self,camera,drone=False):
        renderer=self.dr if drone else self.r;renderer.update_scene(self.d,camera=camera)
        from visual_profile import apply_camera_profile
        return self.encode(apply_camera_profile(renderer.render(),self.c.get('environment',{}),self.seq,drone))
    def scan(self,slot,yaw):
        bid=self.m.body(slot).id;rot=self.d.xmat[bid].reshape(3,3);origin=self.d.xpos[bid]+rot@np.array([.065,0,.155])
        ang=np.arange(180)*math.tau/180-math.pi+yaw
        vec=np.stack([np.cos(ang),np.sin(ang),np.zeros(180)],axis=1).ravel()
        own=[]
        for gid,b in enumerate(self.m.geom_bodyid):
            a=int(b)
            while a and a!=bid:a=int(self.m.body_parentid[a])
            if a==bid:own.append(gid)
        old=self.m.geom_group[own].copy();self.m.geom_group[own]=5
        groups=np.array([1,1,0,1,1,0],dtype=np.uint8);ids=np.zeros(180,dtype=np.int32);dist=np.zeros(180)
        mujoco.mj_multiRay(self.m,self.d,origin,vec,groups,True,-1,ids,dist,None,180,8.)
        self.m.geom_group[own]=old
        return dict(ranges=np.where(dist<0,8,np.minimum(dist,8)).round(3).tolist(),angle_min=-math.pi,angle_increment=math.tau/180,range_max=8.,captured_sim_time=float(self.d.time),frame_seq=self.seq,captured_at=datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='milliseconds'))
    def collect(self,request):
        seed=request['seed'];rng=np.random.default_rng(seed);episode=seed%6
        # Each held-out episode has a different angle/light; source photographs are shared.
        panel=list(self.c['panels'].values())[(seed//6)%len(self.gids)]
        nx,ny=panel['normal'];tangent=np.array([-ny,nx,0.]);normal=np.array([nx,ny,0.])
        distance=float(rng.uniform(.22,.48));offset=(episode-2.5)*.045+float(rng.uniform(-.018,.018))
        target=np.array([panel['x'],panel['y'],.24]);position=target+normal*distance+tangent*offset;position[2]+=(episode-2.5)*.016
        forward=target-position;forward/=np.linalg.norm(forward);right=np.cross(forward,[0,0,1]);right/=np.linalg.norm(right);up=np.cross(right,forward)
        matrix=np.stack([right,up,-forward],axis=1);quat=np.zeros(4);mujoco.mju_mat2Quat(quat,matrix.ravel())
        camera=self.m.camera('training').id;self.m.cam_pos[camera]=position;self.m.cam_quat[camera]=quat
        old=self.m.light_diffuse.copy();self.m.light_diffuse[:]=old*(.72+.11*episode)
        mujoco.mj_forward(self.m,self.d)
        try:
            self.r.update_scene(self.d,camera='training');rgb=self.r.render().copy()
            self.r.enable_segmentation_rendering();self.r.update_scene(self.d,camera='training');seg=self.r.render().copy();self.r.disable_segmentation_rendering()
        finally:self.m.light_diffuse[:]=old
        annotations=[]
        for gid,info in self.gids.items():
            ys,xs=np.where((seg[:,:,0]==gid)&(seg[:,:,1]==int(mujoco.mjtObj.mjOBJ_GEOM)))
            if len(xs)<30:continue
            x1,x2=int(xs.min()),int(xs.max())+1;y1,y2=int(ys.min()),int(ys.max())+1
            if min(x2-x1,y2-y1)<5:continue
            annotations.append(dict(class_id=info['class_id'],bbox=[(x1+x2)/1280,(y1+y2)/960,(x2-x1)/640,(y2-y1)/480]))
        return dict(**self.encode(rgb),width=640,height=480,annotations=annotations,classes=CLASSES,synthetic=True,physical=True,renderer='MuJoCo OpenGL segmentation',backend='mujoco',episode=f'angle-light-{episode}',seed=seed,pose=dict(x=float(position[0]),y=float(position[1]),z=float(position[2]),target_id=panel['id']),source_note='Official illustrative photographs on simulated panels; not independent real-camera objects')
    def exchange(self,payload):
        data=json.dumps(dict(generation=self.c['generation'],**payload)).encode()
        request=urllib.request.Request(self.c['server']+'/api/physics/exchange',data,headers={'Content-Type':'application/json','X-Admin-Key':self.c['admin_key']})
        with urllib.request.urlopen(request,timeout=15) as response:return json.load(response)
    def poses(self,with_scan=False):
        robots=[]
        for slot in self.slots:
            bid=self.m.body(slot).id;pos=self.d.xpos[bid];q=self.d.xquat[bid];yaw=math.atan2(2*(q[0]*q[3]+q[1]*q[2]),1-2*(q[2]**2+q[3]**2))
            if with_scan:self.scans[slot]=self.scan(slot,yaw)
            robots.append(dict(slot=slot,x=float(pos[0]),y=float(pos[1]),yaw=yaw,pan=-math.degrees(float(self.d.joint(slot+'_pan').qpos[0]))/.5,tilt=math.degrees(float(self.d.joint(slot+'_tilt').qpos[0]))/.15,scan=self.scans.get(slot)))
        return robots
    def render_frames(self):
        frames={}
        if self.d.time-self.last_camera>=.1-1e-8:
            frames['B-1']=self.render('B-1_camera');self.last_camera=self.d.time
        # Opponents use simulator policies, not their camera images. Only render
        # those views when a real camera client has recently requested them.
        # Primary robot 10 FPS and fixed UAV 1 FPS remain unchanged.
        for slot in self.requested_cameras:
            if slot in self.slots[1:] and self.d.time-self.last_other.get(slot,-9)>=.5-1e-8:
                frames[slot]=self.render(slot+'_camera');self.last_other[slot]=self.d.time
        if self.d.time-self.last_drone>=1.-1e-8:
            frames['drone']=self.render('drone',True);self.last_drone=self.d.time
        return frames
    def tick(self,ticks=1):
        samples=[]
        for _ in range(ticks):
            for command in self.commands:
                slot=command['slot'];v,w=command['v'],command['w']
                for j in range(8):
                    gid=self.m.geom(f'{slot}_ammo_led_{j}').id
                    self.m.geom_rgba[gid]=[.95,.8,.1,1] if j<command.get('ammo',8) else [.08,.07,.03,1]
                colors=[[1,.05,.02,1],[1,.4,.02,1],[1,.9,.05,1],[.1,1,.1,1],[.1,.3,1,1]]
                self.m.geom_rgba[self.m.geom(f'{slot}_hp_led').id]=colors[max(0,min(4,int(command.get('hp',4))))]
                for side,velocity in [('left',(v-w*.098)/.040),('right',(v+w*.098)/.040)]:
                    for i in range(3):self.d.ctrl[self.m.actuator(f'{slot}_{side}_{i}').id]=velocity
                for axis,target in [('pan',-math.radians(command['pan']*.5)),('tilt',math.radians(command['tilt']*.15))]:
                    position=float(self.d.joint(slot+'_'+axis).qpos[0]);limit=math.radians(command.get('gimbal_speed',80))*.05
                    self.d.ctrl[self.m.actuator(slot+'_'+axis).id]=position+max(-limit,min(limit,target-position))
            for _ in range(25):mujoco.mj_step(self.m,self.d)
            self.seq+=1;samples.append(dict(robots=self.poses(),dt=.05))
        if self.d.time-self.last_scan>=.2-1e-8:
            samples[-1]['robots']=self.poses(True);self.last_scan=self.d.time
        frames=self.render_frames()
        result=None
        if self.capture:
            try:result=dict(id=self.capture['id'],frame=self.collect(self.capture))
            except Exception as exc:result=dict(id=self.capture['id'],error=str(exc));traceback.print_exc()
        reply=self.exchange(dict(sim_time=float(self.d.time),dt=.05*ticks,seq=self.seq,robots=samples[-1]['robots'],samples=samples,frames=frames,capture_result=result))
        if reply.get('world_xml'):
            self.r.close();self.dr.close()
            self.__init__({**self.c,'world_xml':reply['world_xml'],'panels':reply['panels'],'robots':reply['reset'],'environment':reply.get('environment',{})})
        elif reply.get('reset'):self.reset(reply['reset'])
        for tid,destroyed in reply.get('target_states',{}).items():
            self.m.geom_rgba[self.m.geom('hit_led_'+tid).id]=[1,.02,.01,1] if destroyed else [.18,.02,.02,1]
        self.commands=reply.get('commands',[]);self.capture=reply.get('capture')
        self.requested_cameras=reply.get('requested_cameras',[])
    def run(self):
        simulated_wall=time.monotonic()
        try:
            while self.running:
                now=time.monotonic();ticks=max(1,min(5,int((now-simulated_wall)/.05)))
                self.tick(ticks);simulated_wall+=ticks*.05
                lag=time.monotonic()-simulated_wall
                if lag>2:
                    # Rendering can stall while the Mac is busy. Pause the
                    # authoritative match, but retain the worker/camera so the
                    # user can resume or stop through the same buttons.
                    # No old motion/score ticks are replayed to repay wall debt.
                    reply=self.exchange(dict(sim_time=float(self.d.time),seq=self.seq,
                        dt=0,samples=[],robots=self.poses(),lag_seconds=lag))
                    self.commands=reply.get('commands',[])
                    simulated_wall=time.monotonic()
                    print(f'Physics rendering lag {lag:.2f}s: match paused; worker remains available',flush=True)
                time.sleep(max(0,simulated_wall-time.monotonic()))
        finally:self.r.close();self.dr.close()

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);args=p.parse_args();runtime=Runtime(json.loads(Path(args.config).read_text()))
    def stop(*_):
        runtime.running=False
        raise SystemExit(0)
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    print('MuJoCo',mujoco.__version__,'physical runtime started',flush=True);runtime.run()
if __name__=='__main__':main()
