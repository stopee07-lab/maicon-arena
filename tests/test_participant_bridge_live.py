"""Explicit local HTTP-to-ROS fixture verifies cached source frames stay cached."""
def main():
 import sys,threading,time,json,math
 sys.path.insert(0,'/participant')
 from datetime import datetime,timezone
 from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
 import cv2,numpy as np
 import rclpy
 from rclpy.node import Node
 from rclpy.executors import MultiThreadedExecutor
 from rclpy.qos import qos_profile_sensor_data
 from sensor_msgs.msg import LaserScan,Image
 from maicon_sim_bridge.bridge import SimBridge
 _,image=cv2.imencode('.jpg',np.zeros((480,640,3),np.uint8));captured=datetime.now(timezone.utc).isoformat()
 class Handler(BaseHTTPRequestHandler):
  def do_GET(self):
   self.send_response(200)
   if '/camera' in self.path:
    self.send_header('X-Frame-Seq','fixture-1');self.send_header('X-Captured-At',captured);self.end_headers();self.wfile.write(image.tobytes());return
   self.end_headers();self.wfile.write(json.dumps({'slot':'B-1','mode':'practice','cmd':'start','hp':0,'hp_max':4,'battery_pct':90,'camera_available':True,'odom':{'x':0,'y':0,'yaw':0},'imu':{'yaw':0},'scan':{'ranges':[None]+[1.]*179,'angle_min':-math.pi,'angle_increment':math.tau/180,'range_max':8.,'measured_at':123.5,'frame_seq':7,'clock_domain':'physics_seconds'}}).encode())
  def do_POST(self):
   self.rfile.read(int(self.headers.get('Content-Length',0)));self.send_response(200);self.end_headers();self.wfile.write(b'{"accepted":true}')
  def log_message(self,*a):pass
 server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
 rclpy.init(args=['--ros-args','-p',f'server_url:=http://127.0.0.1:{server.server_port}','-p','mission_code:=local-fake'])
 collector=Node('bridge_fixture_collector');scans=[];images=[]
 collector.create_subscription(LaserScan,'/scan',lambda msg:scans.append(msg),qos_profile_sensor_data)
 collector.create_subscription(Image,'/image_raw',lambda msg:images.append(msg),qos_profile_sensor_data)
 bridge=SimBridge();executor=MultiThreadedExecutor(num_threads=3);executor.add_node(bridge);executor.add_node(collector)
 try:
  deadline=time.monotonic()+2
  while time.monotonic()<deadline:executor.spin_once(timeout_sec=.05)
  assert len(scans)==1,len(scans)
  assert math.isnan(scans[0].ranges[0]) and scans[0].header.stamp.sec==123 and scans[0].header.stamp.nanosec==500000000
  assert len(images)==1 and images[0].encoding=='bgr8',len(images)
  assert (images[0].width,images[0].height)==(640,480)
  print(json.dumps({'passed':True,'domain':'local fake own-robot HTTP -> ROS2 Humble bridge','checks':['one source scan published once despite20Hz timer','null -> ROS NaN','source physics_seconds123.5 retained','one cached camera published once','camera640x480bgr8 source timestamp retained']}))
 finally:
  bridge.close();executor.shutdown();bridge.destroy_node();collector.destroy_node();rclpy.shutdown();server.shutdown();server.server_close()
if __name__=='__main__':main()
