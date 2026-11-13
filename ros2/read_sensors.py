#!/usr/bin/env python3
"""Read-only ROS/own-camera transport proof; no admin API or actuator calls."""
import hashlib
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from PIL import Image as PILImage
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data, QoSProfile, ReliabilityPolicy, DurabilityPolicy
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image, Imu, LaserScan
from sim_mil_interfaces.msg import State, Match
from robot_client import TeamClient


def main():
    rclpy.init();node=Node('read_only_sensor_proof');seen=defaultdict(list);latest={};subs=[]
    latched=QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL)
    kinds={'/odom':Odometry,'/imu/data':Imu,'/scan':LaserScan,'/image_raw':Image,'/mil/state':State,'/mil/match':Match}
    for topic,kind in kinds.items():
        def callback(msg,key=topic):seen[key].append(time.monotonic());latest[key]=msg
        subs.append(node.create_subscription(kind,topic,callback,latched if topic.startswith('/mil/') else qos_profile_sensor_data))
    try:
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:rclpy.spin_once(node,timeout_sec=.02)
        assert all(topic in latest for topic in kinds),list(latest)
        frame=latest['/image_raw'];assert (frame.width,frame.height,frame.encoding)==(640,480,'bgr8')
        assert len(frame.data)==921600 and latest['/scan'].ranges
        output=Path('/tmp/ros-sensor-proof');output.mkdir(exist_ok=True)
        PILImage.frombytes('RGB',(frame.width,frame.height),bytes(frame.data),'raw','BGR',frame.step).save(output/'ros-camera.png')
        mode=os.getenv('SIM_MODE','match');prefix='/sim/robot' if mode=='match' else '/sim/'+mode+'/robot'
        client=TeamClient(os.environ['SIM_SERVER'],os.environ['MISSION_CODE'],timeout=2,robot_prefix=prefix)
        raw,headers=client.sim_get('camera')
        selected={key:value for key,value in headers.items() if key.lower().startswith('x-')}
        result={'status':'passed','ros_distro':os.getenv('ROS_DISTRO'),'read_only':True,'mode':mode,
            'topic_counts':{k:len(v) for k,v in seen.items()},
            'rates_hz':{k:round((len(v)-1)/(v[-1]-v[0]),2) for k,v in seen.items() if len(v)>1},
            'image':{'width':frame.width,'height':frame.height,'encoding':frame.encoding,'bgr_sha256':hashlib.sha256(bytes(frame.data)).hexdigest()},
            'own_http_camera_headers':selected,'own_http_camera_sha256':hashlib.sha256(raw).hexdigest(),
            'same_frame_hash_claim':False,'physical_hardware_test':False}
        (output/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    finally:node.destroy_node();rclpy.shutdown()


if __name__=='__main__':main()
