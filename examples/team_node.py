#!/usr/bin/env python3
"""Actual rclpy example for the Linux Humble simulator (not importable on bare macOS).

The official custom package is unknown. Replace sim_mil_interfaces only after
checking ros2 interface show on the supplied real robot. No hardware compatibility
claim is made. HTTP work and optional inference run outside the 10Hz ROS step.
"""
import argparse
import json
from pathlib import Path
import threading
import time
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, qos_profile_sensor_data
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image, Imu
from sim_mil_interfaces.msg import Match, State, OledLine
from sim_mil_interfaces.srv import Gimbal, Fire
from robot_client import TeamClient, ReportWorkflow, SafeMotion, ApiError


class TeamNode(Node):
    def __init__(self, args):
        super().__init__('team_node')
        self.args=args;self.finished=False;self.initialized=False
        self.stop_event=threading.Event();self.motion=SafeMotion();self.lock=threading.Lock()
        self.match=None;self.hp=None;self.image=None;self.odom=None;self.imu=None
        self.frame_seq=0;self.image_received_at=0.;self.vision_results=[];self.model=None;self.inference_error=None
        self.http_phase=None;self.http_ok=False
        self.client=TeamClient(args.server,args.mission_code,timeout=.4,
            api_prefix='/api/rehearsal' if args.mode=='rehearsal' else '/api/report',
            robot_prefix='/sim/robot' if args.mode=='match' else '/sim/'+args.mode+'/robot')
        self.report=ReportWorkflow(self.client)
        self.reports=json.loads(args.reports.read_text()) if args.reports else {}
        self.pub=self.create_publisher(Twist,self.topic('cmd_vel'),10)
        self.oled_pub=self.create_publisher(OledLine,self.topic('mil/oled'),10)
        status_qos=QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,
                              durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(Match,self.topic('mil/match'),self.on_match,status_qos)
        self.create_subscription(State,self.topic('mil/state'),self.on_state,status_qos)
        self.create_subscription(Odometry,self.topic('odom'),self.on_odom,qos_profile_sensor_data)
        self.create_subscription(Image,self.topic('image_raw'),self.on_image,qos_profile_sensor_data)
        self.create_subscription(Imu,self.topic('imu/data'),self.on_imu,qos_profile_sensor_data)
        self.gimbal=self.create_client(Gimbal,self.topic('mil/gimbal'))
        self.fire=self.create_client(Fire,self.topic('mil/fire'))
        self.pending_gimbal=False
        self.create_timer(.1,self.step);self.create_timer(.5,self.show_status)
        self.vision_thread=threading.Thread(target=self.inference_worker,daemon=True)
        self.http_thread=threading.Thread(target=self.http_worker,daemon=True)
        self.vision_thread.start();self.http_thread.start()

    def topic(self,name):
        return '/'+(self.args.namespace.strip('/')+'/' if self.args.namespace.strip('/') else '')+name

    def on_match(self,msg):
        with self.lock:
            old_cmd=self.match.get('cmd') if self.match else None
            self.match={'mode':msg.mode,'cmd':msg.cmd};self.motion.observe(self.match)
            if msg.cmd in ('start','resume') and old_cmd!=msg.cmd:self.pending_gimbal=True
            if msg.cmd=='stop':self.finished=True

    def on_state(self,msg):self.hp=msg
    def on_imu(self,msg):self.imu=msg
    def on_odom(self,msg):
        self.odom=msg
        with self.lock:
            if self.match:self.motion.observe(self.match)
    def on_image(self,msg):
        with self.lock:self.image=msg;self.frame_seq+=1;self.image_received_at=time.monotonic()

    def step(self):
        """Fast 10Hz control callback. This baseline turns slowly, not a mission solver."""
        with self.lock:
            camera_fresh=time.monotonic()-self.image_received_at<.5
            if not camera_fresh:self.motion.clear()
            if self.initialized and camera_fresh and self.http_ok and self.http_phase=='running' and not self.inference_error and SafeMotion.allowed(self.match) and self.match['cmd']!='pause':
                # On a real robot replace this toy behavior with perception/navigation.
                self.motion.command(self.args.speed,.12)
            linear,angular=self.motion.output()
            aim=self.pending_gimbal and SafeMotion.allowed(self.match)
            self.pending_gimbal=False
        msg=Twist();msg.linear.x=linear;msg.angular.z=angular;self.pub.publish(msg)
        if aim and self.gimbal.service_is_ready():
            request=Gimbal.Request();request.pan=0.;request.tilt=0.;request.speed=80.;request.acc=20.
            self.gimbal.call_async(request).add_done_callback(self.gimbal_result)

    def gimbal_result(self,future):
        try:self.get_logger().info('Gimbal receipt (not reached): '+future.result().message)
        except Exception as exc:self.get_logger().warning(str(exc))

    def request_fire(self):
        """Call from your target detector; example does not fire automatically."""
        if self.fire.service_is_ready():self.fire.call_async(Fire.Request()).add_done_callback(self.fire_result)

    def fire_result(self,future):
        try:
            result=future.result()
            self.get_logger().info(f'Fire {result.message}, wait {result.left_ms}ms')
            # fired / empty / locked / reloading are outcomes, not instruction to retry now.
        except Exception as exc:self.get_logger().warning(str(exc))

    def show_status(self):
        text='WAIT SENSORS' if not self.initialized else (self.match or {}).get('cmd','WAIT').upper()
        msg=OledLine();msg.line=0;msg.text=text[:21];self.oled_pub.publish(msg)

    def inference_worker(self):
        try:
            if self.args.weights:
                from ultralytics import YOLO
                self.model=YOLO(str(self.args.weights))
            last_seq=-1
            while not self.stop_event.wait(.1):
                with self.lock:image=self.image;seq=self.frame_seq
                if image is None or seq==last_seq:continue
                if image.encoding!='bgr8':raise ValueError('example expects bgr8 Image')
                last_seq=seq
                if self.model is not None:
                    from PIL import Image as PILImage
                    # Pillow handles row stride; expensive model prediction stays here.
                    frame=PILImage.frombytes('RGB',(image.width,image.height),bytes(image.data),'raw','BGR',image.step)
                    predictions=self.model.predict(frame,verbose=False,device=self.args.device)
                    self.vision_results=[{'class_id':int(b.cls.item()),'confidence':float(b.conf.item()),
                        'xyxy':b.xyxy[0].tolist()} for result in predictions for b in result.boxes]
                self.initialized=self.hp is not None and self.odom is not None and self.imu is not None
        except Exception as exc:
            self.inference_error=str(exc);self.initialized=False
            with self.lock:self.motion.clear()
            self.get_logger().error('Inference initialization/worker failed: '+str(exc))

    def http_worker(self):
        if self.args.mode=='practice':
            self.http_phase='running';self.http_ok=True;return
        sent=set();next_ready=0.;last_phase=None
        order_received=False;target_received=False;next_mission=0.
        while not self.stop_event.wait(.2) and not self.finished:
            try:
                me=self.client.report_get('me');phase=me['phase'];self.http_phase=phase;self.http_ok=True
                if phase!=last_phase:self.get_logger().info('HTTP phase '+phase);last_phase=phase
                if phase=='finished':self.finished=True;break
                if phase=='ready' and self.initialized and not me['ready'] and time.monotonic()>=next_ready:
                    next_ready=time.monotonic()+2.
                    self.client.report_post('ready',{})
                    if self.client.report_get('me').get('ready'):self.get_logger().info('Ready read-back verified')
                if phase=='running':
                    if time.monotonic()>=next_mission:
                        next_mission=time.monotonic()+1.
                        if not order_received:
                            reply=self.client.report_get('order',wait=0)
                            order_received=reply.get('accepted') is True
                            if order_received:self.get_logger().info('M1 order: '+str(reply['order']))
                        if not target_received:
                            reply=self.client.report_get('target',wait=0)
                            target_received=reply.get('accepted') is True
                            if target_received:self.get_logger().info('M5 objective: '+str(reply['order']))
                    for kind in ('interpretation','detection'):
                        if kind in self.reports and kind not in sent:
                            reply=self.client.report_post(kind,self.reports[kind])
                            if reply.get('accepted'):sent.add(kind)
                if phase=='reporting':
                    with self.lock:self.motion.clear()
                    self.pub.publish(Twist())
                    if self.report.payload is None:
                        camp='R' if me['camp']=='BLUE' else 'B'
                        ranking=self.args.ranking or [f'{camp}-{x}' for x in (1,2,3)]
                        if not self.args.ranking:self.get_logger().warning('M6 uses uninformed fixed prior, no trained estimator')
                        self.report.begin(ranking)
                    was_done=self.report.done;self.report.tick(phase)
                    if self.report.done and not was_done:self.get_logger().info('M6 saved echo verified')
            except ApiError as exc:
                self.http_ok=False
                with self.lock:self.motion.clear()
                if exc.status in (401,422):
                    self.inference_error=str(exc);self.get_logger().error(str(exc));return
            except (ValueError,KeyError) as exc:
                with self.lock:self.motion.clear()
                self.inference_error=str(exc);self.get_logger().error(str(exc));return

    def close(self):
        self.stop_event.set()
        with self.lock:self.motion.clear()
        self.pub.publish(Twist())
        self.http_thread.join(timeout=1.);self.vision_thread.join(timeout=1.)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',default='http://host.docker.internal:8765')
    parser.add_argument('--mission-code',required=True)
    parser.add_argument('--namespace',default='')
    parser.add_argument('--mode',choices=['match','practice','rehearsal'],default='match')
    parser.add_argument('--speed',type=float,default=.1)
    parser.add_argument('--ranking',nargs='+')
    parser.add_argument('--reports',type=Path)
    parser.add_argument('--weights',type=Path)
    parser.add_argument('--device',default='cpu')
    args,ros_args=parser.parse_known_args()
    rclpy.init(args=ros_args);node=None
    try:
        node=TeamNode(args)
        while rclpy.ok() and not node.finished:rclpy.spin_once(node,timeout_sec=.1)
    except KeyboardInterrupt:pass
    finally:
        if node:node.close();node.destroy_node()
        if rclpy.ok():rclpy.shutdown()

if __name__=='__main__':main()
