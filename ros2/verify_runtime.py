#!/usr/bin/env python3
"""ROS integration probe, run only against verify_local.py's isolated server.

The test harness intentionally uses its own fake organizer key to inject phases
and faults. Contestant bridge/team code never receives organizer credentials.
"""
import json
import math
import os
import time
import urllib.request
from collections import defaultdict
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data, QoSProfile, ReliabilityPolicy, DurabilityPolicy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image, Imu, LaserScan
from sim_mil_interfaces.msg import State, Match, OledLine
from sim_mil_interfaces.srv import Gimbal, Fire


def request(path, body=None, admin=False):
    headers={'Content-Type':'application/json'}
    if admin:headers['X-Admin-Key']='isolated-ros-test-only'
    req=urllib.request.Request(os.environ['SIM_SERVER']+path,
        data=None if body is None else json.dumps(body).encode(),headers=headers)
    with urllib.request.urlopen(req,timeout=3) as response:return json.load(response)


def control(action, **body):return request('/api/admin/control',{'action':action,**body},True)
def own():return request('/sim/robot/state?mission_code=ROSB0001')


def main():
    rclpy.init();node=Node('isolated_ros_verifier');seen=defaultdict(list);latest={}
    latched=QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL)
    types={'/odom':Odometry,'/imu/data':Imu,'/scan':LaserScan,'/image_raw':Image,'/mil/state':State,'/mil/match':Match}
    subs=[]
    for topic,kind in types.items():
        def callback(msg,key=topic):seen[key].append(time.monotonic());latest[key]=msg
        subs.append(node.create_subscription(kind,topic,callback,latched if topic.startswith('/mil/') else qos_profile_sensor_data))
    drive=node.create_publisher(Twist,'/cmd_vel',10)
    oled=node.create_publisher(OledLine,'/mil/oled',10)
    def spin(seconds,linear=None,angular=0.):
        deadline=time.monotonic()+seconds;next_send=0.
        while time.monotonic()<deadline:
            if linear is not None and time.monotonic()>=next_send:
                msg=Twist();msg.linear.x=float(linear);msg.angular.z=float(angular);drive.publish(msg);next_send=time.monotonic()+.1
            rclpy.spin_once(node,timeout_sec=.02)
    def service(kind,name,req):
        client=node.create_client(kind,name)
        assert client.wait_for_service(timeout_sec=3),name
        future=client.call_async(req);rclpy.spin_until_future_complete(node,future,timeout_sec=3)
        assert future.done(),name
        return future.result()
    try:
        spin(2)
        assert all(topic in latest for topic in types),list(latest)
        for values in seen.values():values.clear()
        spin(3)
        rates={topic:round((len(ts)-1)/(ts[-1]-ts[0]),2) if len(ts)>1 else 0 for topic,ts in seen.items()}
        for topic in ('/odom','/imu/data','/scan'):assert rates[topic]>=12,(topic,rates)
        assert rates['/image_raw']>=2 and rates['/mil/state']>=1 and rates['/mil/match']>=1,rates
        image=latest['/image_raw'];assert (image.width,image.height,image.encoding,image.step)==(640,480,'bgr8',1920)
        assert len(image.data)==640*480*3
        assert len(latest['/scan'].ranges)>0
        assert latest['/imu/data'].angular_velocity_covariance[0]==-1
        qos=node.get_publishers_info_by_topic('/mil/match')[0].qos_profile
        assert qos.durability==DurabilityPolicy.TRANSIENT_LOCAL and qos.reliability==ReliabilityPolicy.RELIABLE
        control('prepare');request('/api/report/ready',{'mission_code':'ROSB0001'});control('start');spin(.4)
        start=own()['odom'];spin(1,linear=.12);after=own()['odom']
        moved=math.hypot(after['x']-start['x'],after['y']-start['y']);assert moved>.035,moved
        spin(.8);stopped=own()['odom'];spin(.35);watch=own()['odom']
        drift=math.hypot(watch['x']-stopped['x'],watch['y']-stopped['y']);assert drift<.003,drift
        control('pause');spin(.5,linear=.12);paused=own()['odom'];spin(.4,linear=.12)
        assert own()['odom']==paused
        assert latest['/mil/match'].cmd=='pause'
        control('resume');spin(.35);assert own()['odom']==paused
        spin(.5,linear=.12);assert own()['odom']!=paused
        control('fault',slot='B-1',kind='camera',enabled=True);spin(.6,linear=.12)
        fault_stop=own()['odom'];spin(.3,linear=.12);assert own()['odom']==fault_stop
        control('fault',slot='B-1',kind='camera',enabled=False);spin(.6,linear=.12);assert own()['odom']!=fault_stop
        request('/test/camera-fault',{'enabled':True},True)
        spin(.9,linear=.12);assert own()['camera_available'] is True
        transport_stop=own()['odom'];spin(.3,linear=.12);assert own()['odom']==transport_stop
        request('/test/camera-fault',{'enabled':False},True);spin(.65,linear=.12);assert own()['odom']!=transport_stop
        spin(.15,linear=0.)
        req=Gimbal.Request();req.pan=4.;req.tilt=2.;req.speed=50.;req.acc=50.
        gimbal=service(Gimbal,'/mil/gimbal',req);assert gimbal.message=='ok',gimbal
        fire=service(Fire,'/mil/fire',Fire.Request());assert fire.message in ('ok','fired','cooldown'),fire
        msg=OledLine();msg.line=1;msg.text='ROS2 HUMBLE';oled.publish(msg);spin(.3)
        control('finish');spin(.4,linear=.12);report_stop=own()['odom'];spin(.3,linear=.12);assert own()['odom']==report_stop
        control('finish');spin(.3);assert latest['/mil/match'].cmd=='stop'
        result={'status':'passed','ros_distro':os.getenv('ROS_DISTRO'),'rates_hz':rates,'image':{'width':image.width,'height':image.height,'encoding':image.encoding,'bytes':len(image.data)},'transient_local_reliable':True,'drive_distance_m':moved,'watchdog_drift_m':drift,'pause_resume_passed':True,'camera_fault_stop_recovery_passed':True,'image_only_transport_outage_passed':True,'report_stop_passed':True,'gimbal':gimbal.message,'fire':{'message':fire.message,'left_ms':fire.left_ms},'standard_types':{k:v.__module__+'.'+v.__name__ for k,v in types.items()},'isolated_geometric_simulator':True,'native_physics_camera_test':False,'physical_hardware_test':False}
        print(json.dumps(result,indent=2))
    finally:
        msg=Twist();drive.publish(msg);rclpy.spin_once(node,timeout_sec=.1);node.destroy_node();rclpy.shutdown()


if __name__=='__main__':main()
