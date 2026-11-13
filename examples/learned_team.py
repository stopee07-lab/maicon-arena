#!/usr/bin/env python3
"""Deployed-weights contestant: own sensors and official report/drone HTTP only.

No engine import, organizer state, training labels, object IDs, enemy HP or scores.
Known starting slots and sector boundaries below are public-plan calibration
assumptions, not GPS measurements. M1 grammar and M6 visual-motion estimate are
explicit baselines. No M2/M4 target recognition or automatic firing is claimed.
"""
import argparse
import collections
import copy
from datetime import datetime
import hashlib
import heapq
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import tempfile
import threading
import time
from robot_client import TeamClient, ApiError, SafeMotion, ReportWorkflow, forward_clearance, sensors_healthy

CLASSES=['military_truck','mortar','tank','missile','ammo_box','drum']
LIMITATIONS=['Public-plan spawn and sector calibration is approximate; no GPS.',
    'M1 local command grammar is a placeholder for the official LLM API.',
    'No M2/M4 visual target recognition; no automatic laser firing.',
    'M6 ranks observed visual motion, not unobservable HP/ammo/M3 score.',
    'Six supplied example-photo textures do not prove real-competition generalization.']


def atomic_json(path,data):
    path=Path(path);fd,tmp=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False,allow_nan=False)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def angle(value):return math.atan2(math.sin(value),math.cos(value))


def pose_from_odom(state):
    slot=state['slot'];start_y=4.15-(int(slot[-1])-1)*1.65
    start_x=.28 if slot.startswith('B') else 5.84;start_yaw=0. if slot.startswith('B') else math.pi
    odom=state['odom'];x=float(odom['x']);y=float(odom['y'])
    return (start_x+x*math.cos(start_yaw)-y*math.sin(start_yaw),
            start_y+x*math.sin(start_yaw)+y*math.cos(start_yaw),angle(start_yaw+float(odom['yaw'])))


def sector_for(x,y):
    if 2.76<=x<=3.36 and .1<=y<=4.9:return 'CM'
    camp='B' if .65<=x<=2.66 else 'R' if 3.46<=x<=5.47 else None
    if camp:
        for i,center in enumerate((4.15,2.5,.85),1):
            if abs(y-center)<=.75:return f'{camp}-S{i}'
    return None


def parse_local_order(text):
    target=re.search(r'(?<![A-Z0-9_])(?:BLUE|RED)_(?:COMMS|OUTPOST|RADAR)(?![A-Z0-9_])',text)
    base=re.search(r'(?<![A-Z0-9_])(?:BLUE|RED)_(?:COMMAND_POST|MAINTENANCE|SUPPLY_DEPOT)(?![A-Z0-9_])',text)
    sectors=sorted(set(re.findall(r'(?<![A-Z0-9_-])(?:[BR]-S[123]|CM)(?![A-Z0-9_-])',text)))
    if not target or not base or not sectors:raise ValueError('Command is outside the documented local placeholder grammar')
    return {'target':target.group(),'search_sectors':sectors,'base':base.group()}


def detection_position(pred,state,width=640):
    """Estimate face position by own yaw/pan plus measured lidar at image bearing."""
    x,y,yaw=pose_from_odom(state);center=(pred['xyxy'][0]+pred['xyxy'][2])/2
    focal=width/2/math.tan(math.radians(80))
    bearing=-math.radians(float(state.get('pan',0))*.5)+math.atan((width/2-center)/focal)
    scan=state.get('scan',{});ranges=scan.get('ranges',[])
    if not ranges:return None
    start=scan['angle_min'];step=scan['angle_increment']
    i=min(range(len(ranges)),key=lambda k:abs(angle(start+k*step-bearing)))
    distance=float(ranges[i])
    if not math.isfinite(distance) or not .12<distance<min(3.,float(scan.get('range_max',8))):return None
    return x+distance*math.cos(yaw+bearing),y+distance*math.sin(yaw+bearing)


class ObservationStore:
    def __init__(self):self.tracks=[];self.next_id=1
    def add(self,kind,x,y,confidence,frame):
        sector=sector_for(x,y)
        if kind not in CLASSES or not sector or confidence<.35:return
        nearby=[t for t in self.tracks if t['sector']==sector and math.hypot(t['x']-x,t['y']-y)<.20]
        track=min(nearby,key=lambda t:math.hypot(t['x']-x,t['y']-y)) if nearby else None
        if track is None:
            track={'track_id':self.next_id,'x':x,'y':y,'sector':sector,'votes':{},'hits':0,'frames':set(),
                   'confidence':0.,'last_seen':time.monotonic()};self.next_id+=1;self.tracks.append(track)
        if frame in track['frames']:return
        track['frames'].add(frame);track['hits']+=1;track['last_seen']=time.monotonic()
        track['votes'][kind]=track['votes'].get(kind,0.)+confidence
        track['type']=max(track['votes'],key=track['votes'].get)
        track['x']=.8*track['x']+.2*x;track['y']=.8*track['y']+.2*y
        track['confidence']=max(track['confidence'],confidence)
    def confirmed(self):return [t for t in self.tracks if t['hits']>=3 and t['confidence']>=.45]
    def report(self):
        counts=collections.defaultdict(collections.Counter)
        for t in self.confirmed():counts[t['sector']][t['type']]+=1
        return [{'sector':sector,'objects':[{'type':kind,'count':count} for kind,count in sorted(items.items())]}
                for sector,items in sorted(counts.items())]
    def summary(self):
        return [{k:v for k,v in t.items() if k not in ('frames','votes')} for t in self.tracks]


class DroneTracks:
    """Color-component tracks. Opponent callsigns are uncertain initial-row associations."""
    def __init__(self,camp):self.camp=camp;self.tracks={};self.frames=0;self.last_capture=None;self.identity_assumption=False
    def update(self,components,captured):
        if captured==self.last_capture:return
        self.last_capture=captured;self.frames+=1
        enemy='RED' if self.camp=='BLUE' else 'BLUE';detections=[d for d in components if d['camp']==enemy]
        prefix='R' if enemy=='RED' else 'B'
        if not self.tracks and len(detections)==3:
            self.identity_assumption=True
            for i,d in enumerate(sorted(detections,key=lambda d:d['cy']),1):
                self.tracks[f'{prefix}-{i}']={'cx':d['cx'],'cy':d['cy'],'observed_frames':1,'distance':0.}
            return
        unmatched=set(self.tracks)
        for d in detections:
            if not unmatched:break
            name=min(unmatched,key=lambda n:math.hypot(self.tracks[n]['cx']-d['cx'],self.tracks[n]['cy']-d['cy']))
            track=self.tracks[name];distance=math.hypot(track['cx']-d['cx'],track['cy']-d['cy'])
            if distance>.20:continue
            unmatched.remove(name);track['distance']+=distance;track['cx']=d['cx'];track['cy']=d['cy'];track['observed_frames']+=1
    def ranking(self):
        prefix='R' if self.camp=='BLUE' else 'B';names=[f'{prefix}-{i}' for i in (1,2,3)]
        ranking=sorted(names,key=lambda n:-(self.tracks.get(n,{}).get('distance',0)+.01*self.tracks.get(n,{}).get('observed_frames',0)))
        return ranking,{'method':'visible-motion/visibility estimate','insufficient_observations':len(self.tracks)<3 or self.frames<3,
                        'identity_assumption':'initial image row ordering; may be wrong after crossing/occlusion',
                        'frames':self.frames,'tracks':copy.deepcopy(self.tracks),'uses_enemy_hp_ammo_score':False}


def drone_components(jpeg):
    import cv2,numpy as np
    frame=cv2.imdecode(np.frombuffer(jpeg,dtype=np.uint8),cv2.IMREAD_COLOR)
    if frame is None:raise ValueError('Drone JPEG could not be decoded')
    hsv=cv2.cvtColor(frame,cv2.COLOR_BGR2HSV);height,width=frame.shape[:2];output=[]
    # Rendered blue paint includes cyan highlights. Color alone can also select
    # photo-texture strips, so retain compact groups with enough colored area.
    masks={'BLUE':cv2.inRange(hsv,np.array([80,115,65]),np.array([135,255,255])),
           'RED':cv2.bitwise_or(cv2.inRange(hsv,np.array([0,115,65]),np.array([12,255,255])),
                               cv2.inRange(hsv,np.array([165,115,65]),np.array([179,255,255])))}
    for camp,mask in masks.items():
        n,_,stats,centers=cv2.connectedComponentsWithStats(mask)
        parts=[]
        for i in range(1,n):
            x,y,w,h,area=stats[i]
            if max(3,width*height*.000006)<=area<=width*height*.025:
                parts.append((int(x),int(y),int(w),int(h),int(area),*map(float,centers[i])))
        # A robot's two side panels and antenna are disconnected color regions.
        # Join nearby same-camp rectangles by edge distance, not a forced top-3
        # count. Touching/occluded robots remain an explicit heuristic limitation.
        remaining=set(range(len(parts)));radius=min(width,height)*.04
        while remaining:
            first=min(remaining);remaining.remove(first);group=[first];pending=[first]
            while pending:
                x,y,w,h,*_=parts[pending.pop()]
                for index in sorted(remaining):
                    X,Y,W,H,*_=parts[index]
                    gap=math.hypot(max(x-X-W,X-x-w,0),max(y-Y-H,Y-y-h,0))
                    if gap<=radius:remaining.remove(index);group.append(index);pending.append(index)
            members=[parts[index] for index in group];area=sum(p[4] for p in members)
            x=min(p[0] for p in members);y=min(p[1] for p in members)
            w=max(p[0]+p[2] for p in members)-x;h=max(p[1]+p[3] for p in members)-y
            if (area<max(12,width*height*.00006) or not .35<w/max(1,h)<2.8
                    or max(w,h)>min(width,height)*.12 or area/(w*h)<.04):continue
            output.append({'camp':camp,'cx':sum(p[4]*p[5] for p in members)/area/width,
                           'cy':sum(p[4]*p[6] for p in members)/area/height,
                           'bbox':[x,y,w,h],'area':area,'parts':len(members),
                           'method':'grouped nearby HSV parts; uncalibrated visual heuristic, not a learned detector'})
    return output


class SurveyNavigator:
    """Lidar occupancy planning over public field bounds; no object-location table."""
    def __init__(self,camp):
        self.cell=.20;self.occupied={};self.route=[];self.route_index=0;self.scan_until=0.;self.last_plan=0.;self.path=[]
        xpair=(3.60,4.50) if camp=='BLUE' else (2.52,1.62)
        # Sector survey points from the public diagram, not target/object coordinates.
        for y in (4.15,2.50,.85):self.route.extend([(xpair[0],y),(xpair[1],y)])
        self.route.extend([(3.06,.75),(3.06,4.25)]);self.objective=None;self.goal_reason='public-plan sector survey'
    def grid(self,x,y):return round(x/self.cell),round(y/self.cell)
    def update_scan(self,state):
        x,y,yaw=pose_from_odom(state);scan=state.get('scan',{});now=time.monotonic()
        self.occupied={key:t for key,t in self.occupied.items() if now-t<6}
        for i,d in enumerate(scan.get('ranges',[])):
            if i%3 or not math.isfinite(d) or not .05<d<scan.get('range_max',8)-.05:continue
            a=yaw+scan['angle_min']+i*scan['angle_increment'];hit=self.grid(x+d*math.cos(a),y+d*math.sin(a))
            self.occupied[hit]=now
    def set_objective(self,name):
        types=['COMMAND_POST','MAINTENANCE','SUPPLY_DEPOT']
        for i,kind in enumerate(types):
            if name.endswith(kind):
                self.objective=(.25 if name.startswith('BLUE') else 5.87,4.15-i*1.65)
                self.goal_reason='M5 objective from official target response';self.path=[];return
    def plan(self,start,goal):
        blocked=set()
        for x,y in self.occupied:
            for dx in (-1,0,1):
                for dy in (-1,0,1):blocked.add((x+dx,y+dy))
        def valid(p):return 1<=p[0]<=30 and 1<=p[1]<=24 and p not in blocked
        blocked.discard(start)
        candidates=[(goal[0]+dx,goal[1]+dy) for dx in range(-2,3) for dy in range(-2,3) if valid((goal[0]+dx,goal[1]+dy))]
        if not candidates:return []
        goal=min(candidates,key=lambda p:math.dist(p,goal));queue=[(0.,start)];cost={start:0.};previous={};end=None
        while queue and len(cost)<1600:
            _,node=heapq.heappop(queue)
            if node==goal:end=node;break
            for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
                nxt=(node[0]+dx,node[1]+dy)
                if not valid(nxt):continue
                value=cost[node]+1
                if value<cost.get(nxt,1e9):cost[nxt]=value;previous[nxt]=node;heapq.heappush(queue,(value+math.dist(nxt,goal),nxt))
        if end is None:return []
        path=[end]
        while path[-1]!=start:path.append(previous[path[-1]])
        return [(x*self.cell,y*self.cell) for x,y in reversed(path)]
    def command(self,state):
        now=time.monotonic();x,y,yaw=pose_from_odom(state)
        if self.scan_until>now and self.objective is None:return 0.,.7
        goal=self.objective or self.route[self.route_index%len(self.route)]
        if math.dist((x,y),goal)<.28:
            if self.objective:return 0.,0.
            self.route_index+=1;self.scan_until=now+9.5;self.path=[];return 0.,.7
        if now-self.last_plan>.6:
            self.update_scan(state);self.path=self.plan(self.grid(x,y),self.grid(*goal));self.last_plan=now
        front=forward_clearance(state)
        if front is None:return 0.,0.
        if front<.29:return 0.,.8
        while self.path and math.dist((x,y),self.path[0])<.24:self.path.pop(0)
        if not self.path:return 0.,.55
        waypoint=self.path[min(1,len(self.path)-1)];error=angle(math.atan2(waypoint[1]-y,waypoint[0]-x)-yaw)
        return (.18*max(0.,math.cos(error)) if abs(error)<1.1 else 0.,max(-1.4,min(1.4,error*1.8)))


class LearnedTeam:
    def __init__(self,args):
        self.args=args;self.output=args.output.resolve();self.output.mkdir(parents=True,exist_ok=True)
        self.stop_event=threading.Event();self.lock=threading.RLock();self.motion=SafeMotion()
        self.client=TeamClient(args.server,args.mission_code,timeout=.3,
            api_prefix='/api/rehearsal' if args.mode=='rehearsal' else '/api/report',
            robot_prefix='/sim/robot' if args.mode=='match' else '/sim/'+args.mode+'/robot')
        self.drone_client=TeamClient(args.server,args.mission_code,timeout=.8)
        self.report=ReportWorkflow(self.client);self.observations=ObservationStore()
        self.state=None;self.me={};self.state_at=0.;self.frame_at=0.;self.loaded=False;self.nav=None;self.drone=None
        self.frame_seq=0;self.previous_cmd=None;self.finished=False;self.error=None
        self.weights=args.weights.resolve();self.weights_hash=hashlib.sha256(self.weights.read_bytes()).hexdigest()
        self.status={'status':'starting','loaded':False,'ready':False,'server_ready':False,'weights_sha256':self.weights_hash,
            'frame_seq':0,'image_sha256':None,'predictions':[],'observations':[],'policy':args.policy,'mode':args.mode,
            'command_owner':'manual_allowed' if args.policy=='observe' else 'mission_controller',
            'limitations':LIMITATIONS,'drone':{'frames':0},'reports':{},'admin_truth_used':False,'public_map_calibration':'approximate fixed initial slots, no GPS'}
        self.threads=[];self.logfile=(self.output/'events.jsonl').open('a')
    def log(self,event,**data):
        record={'time':time.time(),'event':event,**data}
        with self.lock:self.logfile.write(json.dumps(record,ensure_ascii=False)+'\n');self.logfile.flush()
        print(json.dumps(record,ensure_ascii=False),flush=True)
    def publish_status(self):
        with self.lock:
            self.status['updated_at']=time.time();self.status['observations']=self.observations.summary()
            atomic_json(self.output/'status.json',self.status)
    def sensor_worker(self):
        while not self.stop_event.is_set():
            start=time.monotonic()
            try:
                state=self.client.sim_get('state')
                with self.lock:
                    self.state=state;self.state_at=time.monotonic();self.motion.observe(state)
                    if self.nav is None:
                        camp='BLUE' if state['slot'].startswith('B') else 'RED';self.nav=SurveyNavigator(camp);self.drone=DroneTracks(camp)
                    fresh=self.loaded and time.monotonic()-self.frame_at<1. and sensors_healthy(state)
                    allowed=SafeMotion.allowed(state) and state['cmd']!='pause' and self.me.get('phase') not in ('reporting','finished')
                    if self.args.policy=='mission' and fresh and allowed and not self.error:
                        linear,angular=self.nav.command(state);self.motion.command(linear,angular)
                    else:self.motion.clear()
                    linear,angular=self.motion.output()
                    self.status.update(own_state=state,phase=self.me.get('phase'),velocity_command=[linear,angular],
                                       sensor_healthy=fresh,navigation={'goal_reason':self.nav.goal_reason,'survey_index':self.nav.route_index})
                if self.args.policy=='mission':self.client.sim_post('cmd_vel',{'linear_x':linear,'angular_z':angular})
                if state['cmd']!=self.previous_cmd:
                    self.log('match',mode=state['mode'],cmd=state['cmd']);self.previous_cmd=state['cmd']
                    if self.args.policy=='mission' and state['cmd'] in ('start','resume'):
                        self.client.sim_post('gimbal',{'pan':0.,'tilt':0.,'speed':80.,'acc':20.})
                if state['cmd']=='stop':self.finished=True;self.stop_event.set()
            except (ApiError,ValueError,KeyError) as exc:
                with self.lock:self.motion.clear();self.status['sensor_healthy']=False;self.status['transport_error']=str(exc)
                try:self.client.stop()
                except ApiError:pass
            self.publish_status();self.stop_event.wait(max(0.,.1-(time.monotonic()-start)))
    def inference_worker(self):
        os.environ.setdefault('YOLO_CONFIG_DIR',str(Path(__file__).resolve().parents[1]/'training'/'.ultralytics'))
        os.environ.setdefault('YOLO_AUTOINSTALL','false')
        try:
            from ultralytics import YOLO
            from PIL import Image
            model=YOLO(str(self.weights));classes=[model.names[k] for k in sorted(model.names)]
            if classes!=CLASSES:raise ValueError('Deployed class schema does not match six M3 classes')
            self.log('model_initialized',weights_sha256=self.weights_hash,classes=classes)
            with self.lock:self.status['classes']=classes
            while not self.stop_event.is_set():
                started=time.monotonic()
                with self.lock:state=copy.deepcopy(self.state)
                if state is None or not state.get('camera_available'):
                    self.stop_event.wait(.1);continue
                try:
                    jpeg,headers=self.client.sim_get('camera')
                    captured=headers.get('X-Captured-At')
                    if captured and time.time()-datetime.fromisoformat(captured).timestamp()>1.0:
                        with self.lock:self.status['camera_error']='Camera source frame is older than one second'
                        self.stop_event.wait(.1);continue
                    image=Image.open(io.BytesIO(jpeg)).convert('RGB')
                    predicted=model.predict(image,imgsz=640,device='cpu',conf=.35,verbose=False)[0]
                    detections=[{'class_id':int(b.cls.item()),'type':classes[int(b.cls.item())],'confidence':float(b.conf.item()),
                                 'xyxy':[float(x) for x in b.xyxy[0].tolist()]} for b in predicted.boxes]
                    image_hash=hashlib.sha256(jpeg).hexdigest()
                    with self.lock:
                        self.frame_seq+=1;seq=self.frame_seq
                        for detection in detections:
                            location=detection_position(detection,state,image.width)
                            if location:self.observations.add(detection['type'],*location,detection['confidence'],seq)
                        self.loaded=True;self.frame_at=time.monotonic()
                        frame_name=f'frame-{seq:08d}.jpg';(self.output/frame_name).write_bytes(jpeg)
                        temp=self.output/'.frame.jpg';temp.write_bytes(jpeg);os.replace(temp,self.output/'frame.jpg')
                        packet={'frame_seq':seq,'image_sha256':image_hash,'weights_sha256':self.weights_hash,'predictions':detections,
                            'frame_path':frame_name,'received_at':time.time(),'inference_seconds':time.monotonic()-started,'inference_ms':(time.monotonic()-started)*1000,'captured_at':headers.get('X-Captured-At'),
                            'camera_source':headers.get('X-Camera',headers.get('X-Camera-Backend',headers.get('X-Physics-Backend','server own sensor')))}
                        atomic_json(self.output/'predictions.json',packet)
                        self.status.update(packet);self.status.update(status='running',loaded=True,ready=True)
                        for old in self.output.glob('frame-*.jpg'):
                            if old.name< f'frame-{max(0,seq-3):08d}.jpg':old.unlink(missing_ok=True)
                    self.publish_status()
                except ApiError as exc:
                    with self.lock:self.status['camera_error']=str(exc)
                self.stop_event.wait(max(0.,.2-(time.monotonic()-started)))
        except Exception as exc:
            self.error=str(exc);self.log('inference_failed',error=str(exc))
            with self.lock:self.loaded=False;self.motion.clear();self.status.update(status='failed',loaded=False,ready=False,error=str(exc))
            self.stop_event.set();self.publish_status()
    def http_worker(self):
        next_ready=0.;next_report=0.;next_drone=0.;order_done=False;target_done=False;last_detection=None
        while not self.stop_event.wait(.2):
            try:
                if self.args.mode=='practice':me={'phase':'running','ready':True}
                else:me=self.client.report_get('me')
                with self.lock:self.me=me;self.status['server_ready']=bool(me.get('ready'));state=copy.deepcopy(self.state)
                if me['phase']=='finished':self.finished=True;self.stop_event.set();continue
                if self.args.mode!='practice' and me['phase']=='ready' and self.loaded and not me['ready'] and time.monotonic()>=next_ready:
                    next_ready=time.monotonic()+2;self.client.report_post('ready',{})
                    verified=self.client.report_get('me').get('ready');self.log('ready_ack',verified=verified,weights_sha256=self.weights_hash,frame_seq=self.frame_seq)
                if self.args.mode!='practice' and me['phase']=='running':
                    if not order_done:
                        response=self.client.report_get('order',wait=0)
                        if response.get('accepted'):
                            try:
                                interpretation=parse_local_order(response['order']['tactical_command'])
                                reply=self.client.report_post('interpretation',interpretation);order_done=reply.get('accepted') is True
                                with self.lock:self.status['reports']['interpretation']=reply
                                self.log('m1_local_placeholder',accepted=order_done,received=reply.get('received'))
                            except ValueError as exc:self.log('m1_unparsed',error=str(exc));order_done=True
                    if not target_done:
                        response=self.client.report_get('target',wait=0)
                        if response.get('accepted'):
                            target_done=True
                            with self.lock:
                                if self.nav:self.nav.set_objective(response['order']['objective'])
                                self.status['objective']=response['order']
                            self.log('m5_received',order=response['order'])
                    if time.monotonic()>=next_report:
                        next_report=time.monotonic()+2
                        with self.lock:sectors=self.observations.report()
                        signature=json.dumps(sectors,sort_keys=True)
                        if sectors and signature!=last_detection:
                            reply=self.client.report_post('detection',{'sectors':sectors})
                            if reply.get('accepted'):last_detection=signature
                            with self.lock:self.status['reports']['detection']=reply
                            self.log('m3_observations_report',accepted=reply.get('accepted'),received=reply.get('received'),coverage='partial observed faces only')
                if me['phase']=='reporting':
                    self.client.stop()
                    if self.report.payload is None:
                        with self.lock:
                            ranking,metadata=self.drone.ranking() if self.drone else (['R-1','R-2','R-3'],{'insufficient_observations':True})
                            self.status['risk_estimate']=metadata
                        self.report.begin(ranking);self.log('m6_estimate',ranking=ranking,evidence=metadata)
                    before=self.report.done;self.report.tick('reporting')
                    if self.report.done and not before:
                        with self.lock:self.status['reports']['situation']={'accepted':True,'received':self.report.payload}
                        self.log('m6_saved',ranking=self.report.payload['ranking'])
                elif me['phase']=='running' and time.monotonic()>=next_drone:
                    next_drone=time.monotonic()+1.
                    try:
                        kwargs={} if self.args.mode=='match' else {'camera':'test2' if self.args.mode=='rehearsal' else 'test1'}
                        jpeg,headers=self.drone_client.report_get('drone',**kwargs)
                        captured=headers.get('X-Captured-At');interval=float(headers.get('X-Frame-Interval-S','1'))
                        next_drone=time.monotonic()+max(1.,interval)
                        age=max(0.,time.time()-datetime.fromisoformat(captured).timestamp()) if captured else None
                        components=drone_components(jpeg)
                        with self.lock:
                            if self.drone and captured and age is not None and age<=max(2.,interval*2):self.drone.update(components,captured)
                            self.status['drone']={'frames':self.drone.frames if self.drone else 0,'captured_at':captured,'age_seconds':age,
                                'frame_interval':interval,'components':components,'source_camera':headers.get('X-Camera'),
                                'stale':age is None or age>max(2.,interval*2),'method':'HSV observation heuristic, not a trained drone detector'}
                            temp=self.output/'.drone.jpg';temp.write_bytes(jpeg);os.replace(temp,self.output/'drone.jpg')
                    except (ApiError,ValueError) as exc:
                        with self.lock:self.status['drone']['error']=str(exc)
            except ApiError as exc:
                with self.lock:self.status['report_error']=str(exc)
                if exc.status in (401,422):self.error=str(exc);self.log('fatal_report_error',error=str(exc));self.stop_event.set()
            except Exception as exc:self.error=str(exc);self.log('http_worker_failed',error=str(exc));self.stop_event.set()
            self.publish_status()
    def run(self):
        self.client.sim_post('strategy',{'strategy':'external'});self.client.stop();self.publish_status()
        for target in (self.sensor_worker,self.inference_worker,self.http_worker):
            thread=threading.Thread(target=target,daemon=True);self.threads.append(thread);thread.start()
        try:
            while not self.stop_event.wait(.2):pass
        finally:
            self.stop_event.set()
            for thread in self.threads:thread.join(timeout=3.)
            try:self.client.stop()
            except ApiError:pass
            with self.lock:self.status.update(status='failed' if self.error else 'stopped',error=self.error,ready=False)
            self.publish_status();self.logfile.close()
        return 1 if self.error else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',default='http://127.0.0.1:8765');parser.add_argument('--mission-code',required=True)
    parser.add_argument('--mode',choices=['match','practice','rehearsal'],default='practice')
    parser.add_argument('--weights',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--policy',choices=['observe','mission'],default='observe')
    args=parser.parse_args()
    if not args.weights.is_file():parser.error('deployed weights file not found')
    team=LearnedTeam(args)
    def stop(*_):team.stop_event.set()
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    raise SystemExit(team.run())

if __name__=='__main__':main()
