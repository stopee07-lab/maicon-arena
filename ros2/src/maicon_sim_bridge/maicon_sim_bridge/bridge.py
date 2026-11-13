"""ROS2 Humble bridge. Requires ROS; imports deliberately fail clearly outside ROS.

Only own /sim/robot/* sensor/actuator HTTP is used. No organizer state, renderer
annotations, enemy coordinates or scores are available to this bridge.
"""
import io
import json
from datetime import datetime
from array import array
import math
import threading
import time
from PIL import Image as PILImage
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy, qos_profile_sensor_data
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import ReentrantCallbackGroup
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image, Imu, LaserScan
from std_msgs.msg import String
from builtin_interfaces.msg import Time
from sim_mil_interfaces.msg import State, Match, OledLine
from sim_mil_interfaces.srv import Gimbal, Fire
from robot_client import TeamClient, SafeMotion, ApiError, sensors_healthy


def source_stamp(value):
    if isinstance(value,str):value=datetime.fromisoformat(value).timestamp()
    if not isinstance(value,(int,float)) or not math.isfinite(value):return None
    stamp=Time();stamp.sec=int(value);stamp.nanosec=int((value-stamp.sec)*1e9);return stamp

def quaternion(yaw):
    return math.sin(yaw/2), math.cos(yaw/2)


class SimBridge(Node):
    def __init__(self):
        super().__init__('maicon_sim_bridge')
        self.declare_parameter('server_url','http://host.docker.internal:8765')
        self.declare_parameter('mission_code','')
        self.declare_parameter('robot_namespace','')
        self.declare_parameter('sim_mode','match')
        self.declare_parameter('read_only',False)
        self.read_only=bool(self.get_parameter('read_only').value)
        self.ns = self.get_parameter('robot_namespace').value.strip('/')
        mode=self.get_parameter('sim_mode').value
        if mode not in ('match','practice','rehearsal'):raise ValueError('invalid sim_mode')
        robot_prefix='/sim/robot' if mode=='match' else '/sim/'+mode+'/robot'
        self.client = TeamClient(self.get_parameter('server_url').value,
                                 self.get_parameter('mission_code').value, timeout=.25,robot_prefix=robot_prefix)
        latched = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.state_pub=self.create_publisher(State,self.topic('mil/state'),latched)
        self.match_pub=self.create_publisher(Match,self.topic('mil/match'),latched)
        self.odom_pub=self.create_publisher(Odometry,self.topic('odom'),qos_profile_sensor_data)
        self.imu_pub=self.create_publisher(Imu,self.topic('imu/data'),qos_profile_sensor_data)
        self.image_pub=self.create_publisher(Image,self.topic('image_raw'),qos_profile_sensor_data)
        self.provenance_pub=self.create_publisher(String,self.topic('sim/source_provenance'),10)
        self.last_scan_key=None;self.last_frame_key=None
        self.scan_pub=self.create_publisher(LaserScan,self.topic('scan'),qos_profile_sensor_data)
        self.motion=SafeMotion(); self.lock=threading.Lock(); self.stop_event=threading.Event()
        self.state=None; self.state_time=0.; self.frame=None; self.frame_time=0.
        self.origin=None; self.last_match=None; self.last_status_time=0.; self.last_image_sent=0.
        self.last_oled=0.; self.last_warn=0.
        cb=ReentrantCallbackGroup()
        self.create_subscription(Twist,self.topic('cmd_vel'),self.cmd_vel,10,callback_group=cb)
        self.create_subscription(OledLine,self.topic('mil/oled'),self.oled,10,callback_group=cb)
        self.create_service(Gimbal,self.topic('mil/gimbal'),self.gimbal,callback_group=cb)
        self.create_service(Fire,self.topic('mil/fire'),self.fire,callback_group=cb)
        self.create_timer(.05,self.publish_sensors)
        if not self.read_only:self.client.sim_post('strategy',{'strategy':'external'})
        self.worker=threading.Thread(target=self.sensor_worker,daemon=True)
        self.camera_worker=threading.Thread(target=self.image_worker,daemon=True)
        self.worker.start(); self.camera_worker.start()
        self.get_logger().info('SIMULATION ONLY: sim_mil_interfaces is not the official hardware package.')

    def topic(self,name):
        return '/'+(self.ns+'/' if self.ns else '')+name

    def frame_id(self,name):
        return (self.ns+'/' if self.ns else '')+name

    def warn(self,exc):
        if time.monotonic()-self.last_warn>2:
            self.get_logger().warning(str(exc)); self.last_warn=time.monotonic()

    def cmd_vel(self,msg):
        if self.read_only:return
        with self.lock:
            try: self.motion.command(msg.linear.x,msg.angular.z)
            except ValueError as exc: self.motion.clear(); self.warn(exc)

    def sensor_worker(self):
        """Network work stays outside ROS callbacks. Server watchdog stops stalled traffic."""
        next_drive=0.
        while not self.stop_event.is_set():
            started=time.monotonic()
            try:
                sample=self.client.sim_get('state')
                with self.lock:
                    self.motion.observe(sample); self.state=sample; self.state_time=time.monotonic()
                    # State may remain reachable while only camera HTTP stalls.
                    camera_stale=time.monotonic()-self.frame_time>=.5
                    if not sensors_healthy(sample) or camera_stale or sample['cmd']=='pause':self.motion.clear()
                    linear,angular=self.motion.output()
                if not self.read_only and (started>=next_drive or not SafeMotion.allowed(sample)):
                    self.client.sim_post('cmd_vel',{'linear_x':linear,'angular_z':angular})
                    next_drive=started+.1
            except (ApiError,ValueError,KeyError) as exc:
                with self.lock: self.motion.clear()
                if not self.read_only:
                    try:self.client.stop()
                    except ApiError:pass
                self.warn(exc)
            self.stop_event.wait(max(0.,.05-(time.monotonic()-started)))

    def image_worker(self):
        while not self.stop_event.is_set():
            started=time.monotonic()
            try:
                raw,headers=self.client.sim_get('camera')
                key=headers.get('X-Frame-Seq') or headers.get('X-Captured-At')
                if not key or key==self.last_frame_key:
                    self.stop_event.wait(.05);continue
                self.last_frame_key=key
                image=PILImage.open(io.BytesIO(raw)).convert('RGB')
                # ROS image_raw must be bgr8, not a JPEG packed into an Image message.
                # Humble's Image.data setter validates every item for bytes.
                # A typed byte array avoids a ~38ms Python loop per 640x480 frame.
                bgr=array('B',image.tobytes('raw','BGR'))
                with self.lock: self.frame=(image.width,image.height,bgr,headers); self.frame_time=time.monotonic()
            except (ApiError,OSError,ValueError) as exc: self.warn(exc)
            self.stop_event.wait(max(0.,.2-(time.monotonic()-started)))

    def publish_sensors(self):
        now=time.monotonic()
        with self.lock:
            sample=self.state; sample_time=self.state_time; frame=self.frame; frame_time=self.frame_time
        if sample is None: return
        if now-sample_time>=.5:
            # Prevent a transient-local subscriber retaining start indefinitely on outage.
            if self.last_match != ('mission','pause'):
                msg=Match(); msg.mode='mission';msg.cmd='pause';self.match_pub.publish(msg)
                self.last_match=('mission','pause')
            return
        stamp=self.get_clock().now().to_msg()
        current_match=(sample['mode'],sample['cmd'])
        if now-self.last_status_time>=.5 or current_match!=self.last_match:
            status=State();status.hp=int(sample['hp']);status.hp_max=int(sample['hp_max'])
            status.battery_pct=float(sample['battery_pct']);self.state_pub.publish(status)
            match=Match();match.mode,match.cmd=current_match;self.match_pub.publish(match)
            self.last_status_time=now;self.last_match=current_match
        position=sample['odom']
        # HTTP adapter already returns spawn-relative x forward / y left / yaw.
        odom=Odometry();odom.header.stamp=stamp;odom.header.frame_id=self.frame_id('odom')
        odom.child_frame_id=self.frame_id('base_link')
        odom.pose.pose.position.x=float(position['x'])
        odom.pose.pose.position.y=float(position['y'])
        odom.pose.pose.orientation.z,odom.pose.pose.orientation.w=quaternion(position['yaw'])
        # Ideal geometric odometry; neither a physical sensor noise model nor TF provider.
        odom.pose.covariance[0]=odom.pose.covariance[7]=odom.pose.covariance[35]=.01
        odom.twist.covariance[0]=odom.twist.covariance[35]=1e6
        self.odom_pub.publish(odom)
        imu=Imu();imu.header.stamp=stamp;imu.header.frame_id=self.frame_id('imu_link')
        imu.orientation.z,imu.orientation.w=quaternion(sample['imu']['yaw'])
        imu.orientation_covariance[0]=imu.orientation_covariance[4]=imu.orientation_covariance[8]=.01
        imu.angular_velocity_covariance[0]=-1.;imu.linear_acceleration_covariance[0]=-1.
        self.imu_pub.publish(imu)
        scan_data=sample.get('scan')
        scan_key=(scan_data.get('clock_domain'),scan_data.get('frame_seq'),scan_data.get('measured_at')) if scan_data else None
        scan_stamp=source_stamp(scan_data.get('measured_at')) if scan_data else None
        if scan_data and scan_stamp is not None and scan_key!=self.last_scan_key:
            self.last_scan_key=scan_key
            scan=LaserScan();scan.header.stamp=scan_stamp;scan.header.frame_id=self.frame_id('base_link')
            scan.angle_min=float(scan_data['angle_min']);scan.angle_increment=float(scan_data['angle_increment'])
            scan.ranges=[float(x) if isinstance(x,(int,float)) and math.isfinite(x) else float('nan') for x in scan_data['ranges']]
            scan.angle_max=scan.angle_min+scan.angle_increment*max(0,len(scan.ranges)-1)
            scan.range_min=0.;scan.range_max=float(scan_data['range_max']);scan.scan_time=.2
            self.scan_pub.publish(scan)
            provenance=String();provenance.data=json.dumps({'sensor':'scan','measured_at':scan_data.get('measured_at'),'clock_domain':scan_data.get('clock_domain'),'frame_seq':scan_data.get('frame_seq')});self.provenance_pub.publish(provenance)
        if frame and frame_time>self.last_image_sent and now-frame_time<.5:
            camera_stamp=source_stamp(frame[3].get('X-Captured-At'))
            if camera_stamp is None:return
            msg=Image();msg.header.stamp=camera_stamp;msg.header.frame_id=self.frame_id('camera_optical_frame')
            msg.width,msg.height,msg.data=frame[:3];msg.encoding='bgr8';msg.step=msg.width*3;msg.is_bigendian=False
            self.image_pub.publish(msg);self.last_image_sent=frame_time
            provenance=String();provenance.data=json.dumps({'sensor':'camera','headers':frame[3]});self.provenance_pub.publish(provenance)

    def gimbal(self,request,response):
        if self.read_only:response.message='locked';return response
        try:
            result=self.client.sim_post('gimbal',{k:getattr(request,k) for k in ('pan','tilt','speed','acc')})
            response.message=result.get('message','locked')
        except ApiError as exc: response.message='transport_error';self.warn(exc)
        return response

    def fire(self,request,response):
        if self.read_only:response.message='locked';response.left_ms=0;return response
        try:
            result=self.client.sim_post('fire');response.message=result.get('message','locked')
            response.left_ms=int(result.get('left_ms',0))
        except ApiError as exc: response.message='transport_error';response.left_ms=0;self.warn(exc)
        return response

    def oled(self,msg):
        if self.read_only:return
        if time.monotonic()-self.last_oled<.5: return
        if msg.line>3 or len(msg.text)>21 or not msg.text.isascii():
            self.warn('OLED requires line 0..3 and <=21 ASCII characters');return
        self.last_oled=time.monotonic()
        try: self.client.sim_post('oled',{'line':int(msg.line),'text':msg.text})
        except ApiError as exc: self.warn(exc)

    def close(self):
        self.stop_event.set()
        with self.lock: self.motion.clear()
        self.worker.join(timeout=1.);self.camera_worker.join(timeout=1.)
        if not self.read_only:
            try:self.client.stop()
            except ApiError:pass


def main(args=None):
    rclpy.init(args=args);node=None;executor=MultiThreadedExecutor(num_threads=4)
    try:
        node=SimBridge();executor.add_node(node);executor.spin()
    except KeyboardInterrupt: pass
    finally:
        if node is not None: node.close();node.destroy_node()
        executor.shutdown()
        if rclpy.ok():rclpy.shutdown()
