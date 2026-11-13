"""Participant owns its ROS executor. Reports always use authorized HTTP credentials."""
import copy
import importlib
import math
import os
import threading
import time
from datetime import datetime, timezone
from robot_client import TeamClient, ApiError

class ParticipantClient:
    def __init__(self,*args,**kwargs):
        self.http=TeamClient(*args,**kwargs)
        kind=os.environ.get('MAICON_ROBOT_TRANSPORT') or {'http':'http_sim','ros':'ros2'}.get(os.environ.get('MAICON_TRANSPORT','http'),os.environ.get('MAICON_TRANSPORT'))
        if kind not in ('http_sim','ros2'):raise ValueError('Unknown robot transport')
        self.kind=kind;self.robot=self.http if kind=='http_sim' else ROSRobot()
        self.estimate_lock=threading.Lock();self.estimates={'pan':0.,'tilt':0.,'ammo':8};self.laser_until=0.
    def report_get(self,*a,**kw):return self.http.report_get(*a,**kw)
    def report_post(self,*a,**kw):return self.http.report_post(*a,**kw)
    def sim_get(self,name):
        result=self.robot.sim_get(name)
        if self.kind=='http_sim' and name=='state':
            with self.estimate_lock:
                estimated=[]
                for key,value in {**self.estimates,'laser':time.monotonic()<self.laser_until}.items():
                    if key not in result:result[key]=value;estimated.append(key)
                    elif key in self.estimates:self.estimates[key]=result[key]
                if estimated:result['estimated_fields']=estimated;result['estimate_source']='own accepted commands; initial ammo8 from public rule; no official actuator feedback'
        return result
    def sim_post(self,name,payload=None):
        result=self.robot.sim_post(name,payload)
        if self.kind=='http_sim':
            with self.estimate_lock:
                if name=='gimbal' and result.get('message')=='ok':
                    self.estimates.update(pan=float(payload['pan']),tilt=float(payload['tilt']))
                if name=='fire' and result.get('message')=='fired':
                    self.estimates['ammo']=max(0,self.estimates['ammo']-1);self.laser_until=time.monotonic()+5.3
        return result
    def stop(self):return self.sim_post('cmd_vel',{'linear_x':0.,'angular_z':0.})
    def close(self):
        if self.kind=='ros2':self.robot.close()

def stamp_seconds(header):return header.stamp.sec+header.stamp.nanosec/1e9

def valid_range(value):
    return float(value) if isinstance(value,(float,int)) and math.isfinite(value) and value>0 else None

class ROSRobot:
    """sim_mil_interfaces is local-only. Official package/types must be supplied and verified.

    Slot and namespace are deployment config, never inferred from organizer data.
    Pan/tilt/ammo are command-derived estimates, not official sensor fields.
    """
    def __init__(self):
        scan_topic=os.environ.get('MAICON_ROS_SCAN_TOPIC')
        slot=os.environ.get('MAICON_ROS_SLOT')
        if not scan_topic or slot not in ('B-1','B-2','B-3','R-1','R-2','R-3'):raise ValueError('ROS requires explicit MAICON_ROS_SCAN_TOPIC and MAICON_ROS_SLOT')
        import rclpy
        from rclpy.node import Node
        from rclpy.executors import MultiThreadedExecutor
        from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy, qos_profile_sensor_data
        from geometry_msgs.msg import Twist
        from nav_msgs.msg import Odometry
        from sensor_msgs.msg import Image, Imu, LaserScan
        package=os.environ.get('MAICON_ROS_INTERFACE_PACKAGE','sim_mil_interfaces')
        msgs=importlib.import_module(package+'.msg');services=importlib.import_module(package+'.srv')
        self.rclpy=rclpy;self.lock=threading.RLock();self.stamps={};self.received={};self.frame=None
        self.owns_context=not rclpy.ok()
        if self.owns_context:rclpy.init()
        self.node=Node('maicon_participant',namespace=os.environ.get('MAICON_ROS_NAMESPACE',''))
        self.state={'slot':slot,'mode':'mission','cmd':'pause','camera_available':False,'scan':{},'pan':0.,'tilt':0.,'ammo':int(os.environ.get('MAICON_ESTIMATED_INITIAL_AMMO','8')),'laser':False,
                    'estimate_source':'pan/tilt/ammo from own accepted commands; no official feedback', 'interface_package':package,'binary_compatibility':'local simulator types only' if package=='sim_mil_interfaces' else 'user configured; official compatibility unverified'}
        self.laser_until=0.;self.Twist=Twist;self.OledLine=msgs.OledLine
        latched=QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.node.create_subscription(msgs.State,'mil/state',self.on_state,latched)
        self.node.create_subscription(msgs.Match,'mil/match',self.on_match,latched)
        self.node.create_subscription(Odometry,'odom',self.on_odom,qos_profile_sensor_data)
        self.node.create_subscription(Imu,'imu/data',self.on_imu,qos_profile_sensor_data)
        self.node.create_subscription(Image,'image_raw',self.on_image,qos_profile_sensor_data)
        self.node.create_subscription(LaserScan,scan_topic,self.on_scan,qos_profile_sensor_data)
        self.velocity=self.node.create_publisher(Twist,'cmd_vel',10);self.oled=self.node.create_publisher(msgs.OledLine,'mil/oled',10)
        self.services={name:(self.node.create_client(cls,'mil/'+name),cls) for name,cls in [('gimbal',services.Gimbal),('fire',services.Fire)]}
        self.executor=MultiThreadedExecutor(num_threads=3);self.executor.add_node(self.node)
        self.thread=threading.Thread(target=self.executor.spin,daemon=True);self.thread.start()
    def fresh_message(self,kind,msg):
        stamp=stamp_seconds(msg.header)
        if self.stamps.get(kind)==stamp:return False
        # A cached frame must not become fresh just because it was retransmitted.
        self.stamps[kind]=stamp;self.received[kind]=time.monotonic();return True
    def on_state(self,msg):
        with self.lock:
            self.state.update(hp=msg.hp,hp_max=msg.hp_max,battery_pct=msg.battery_pct);self.received['state']=time.monotonic()
    def on_match(self,msg):
        with self.lock:self.state.update(mode=msg.mode,cmd=msg.cmd)
    def on_odom(self,msg):
        with self.lock:
            if not self.fresh_message('odom',msg):return
            q=msg.pose.pose.orientation;p=msg.pose.pose.position
            yaw=math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z))
            self.state['odom']={'x':p.x,'y':p.y,'yaw':yaw,'measured_at':self.stamps['odom']}
    def on_imu(self,msg):
        with self.lock:
            if not self.fresh_message('imu',msg):return
            q=msg.orientation;self.state['imu']={'yaw':math.atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z)),'measured_at':self.stamps['imu']}
    def on_scan(self,msg):
        with self.lock:
            if not self.fresh_message('scan',msg):return
            self.state['scan']={'angle_min':msg.angle_min,'angle_increment':msg.angle_increment,'range_max':msg.range_max,'ranges':[valid_range(v) for v in msg.ranges],'measured_at':self.stamps['scan'],'frame_seq':self.stamps['scan']}
    def on_image(self,msg):
        import cv2,numpy as np
        if msg.encoding!='bgr8' or (msg.width,msg.height)!=(640,480):return
        with self.lock:
            if not self.fresh_message('camera',msg):return
            pixels=np.frombuffer(bytes(msg.data),np.uint8).reshape(msg.height,msg.step)[:,:msg.width*3].reshape(msg.height,msg.width,3)
            ok,data=cv2.imencode('.jpg',pixels)
            if not ok:return
            stamp=self.stamps['camera'];captured=datetime.fromtimestamp(stamp,timezone.utc).isoformat()
            self.frame=(data.tobytes(),{'X-Captured-At':captured,'X-Frame-Seq':str(msg.header.stamp.sec)+'-'+str(msg.header.stamp.nanosec),'X-Camera':'ros2/image_raw','X-ROS-Frame-Id':msg.header.frame_id})
    def sim_get(self,name):
        with self.lock:
            now=time.monotonic()
            required=('state','odom','imu','scan','camera')
            ready=all(now-self.received.get(k,-math.inf)<(1.5 if k=='state' else .5) for k in required)
            if name=='camera':
                if not self.frame or now-self.received.get('camera',-math.inf)>=.5:raise ApiError(0,'ROS camera unavailable/stale')
                return self.frame
            if name!='state':raise ValueError('Unknown sensor')
            if 'odom' not in self.state or 'hp' not in self.state:raise ApiError(0,'ROS waiting for state and odometry')
            result=copy.deepcopy(self.state);result['camera_available']=ready;result['laser']=now<self.laser_until
            if not ready:result['cmd']='pause'
            return result
    def sim_post(self,name,payload=None):
        payload=payload or {}
        if name=='strategy':return {'accepted':True,'message':'participant owns ROS command transport'}
        if name=='cmd_vel':
            msg=self.Twist();msg.linear.x=float(payload.get('linear_x',0));msg.angular.z=float(payload.get('angular_z',0));self.velocity.publish(msg)
            return {'accepted':True,'message':'published'}
        if name=='oled':
            msg=self.OledLine();msg.line=int(payload['line']);msg.text=payload['text'];self.oled.publish(msg);return {'message':'published'}
        if name not in self.services:raise ValueError('Unknown actuator')
        client,cls=self.services[name]
        if not client.service_is_ready():raise ApiError(0,'ROS service unavailable: '+name)
        request=cls.Request()
        for key,value in payload.items():setattr(request,key,float(value))
        future=client.call_async(request);done=threading.Event();future.add_done_callback(lambda _:done.set())
        if not done.wait(.4):future.cancel();raise ApiError(0,'ROS service timeout: '+name)
        result=future.result();message=result.message
        with self.lock:
            if name=='gimbal' and message=='ok':self.state.update(pan=float(payload['pan']),tilt=float(payload['tilt']))
            if name=='fire' and message=='fired':self.state['ammo']=max(0,self.state['ammo']-1);self.laser_until=time.monotonic()+5.3
        return {'message':message,'left_ms':getattr(result,'left_ms',0)}
    def close(self):
        self.sim_post('cmd_vel',{'linear_x':0.,'angular_z':0.});self.executor.shutdown(timeout_sec=1.)
        self.node.destroy_node()
        if self.owns_context and self.rclpy.ok():self.rclpy.shutdown()
