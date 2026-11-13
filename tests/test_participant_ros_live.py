"""Run explicitly inside the built ROS image; no competition/server access."""
def main():
 import os,sys,time,threading,json,math
 from array import array
 os.environ.update(MAICON_ROS_SLOT='B-1',MAICON_ROS_NAMESPACE='offline_participant',MAICON_ROS_SCAN_TOPIC='/offline_participant/scan')
 sys.path.insert(0,'/participant')
 import rclpy
 from rclpy.node import Node
 from rclpy.qos import QoSProfile,DurabilityPolicy,ReliabilityPolicy,qos_profile_sensor_data
 from geometry_msgs.msg import Twist
 from nav_msgs.msg import Odometry
 from sensor_msgs.msg import Image,Imu,LaserScan
 from sim_mil_interfaces.msg import State,Match
 from sim_mil_interfaces.srv import Gimbal,Fire
 from transport_facade import ROSRobot
 rclpy.init();pub=Node('offline_sensor_fixture',namespace='offline_participant');robot=ROSRobot()
 latched=QoSProfile(depth=1,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL)
 publishers={k:pub.create_publisher(cls,k,latched if k.startswith('mil/') else qos_profile_sensor_data) for k,cls in [('mil/state',State),('mil/match',Match),('odom',Odometry),('imu/data',Imu),('scan',LaserScan),('image_raw',Image)]}
 commands=[];pub.create_subscription(Twist,'cmd_vel',lambda msg:commands.append((msg.linear.x,msg.angular.z)),10)
 def gimbal(req,res):res.message='ok';return res
 def fire(req,res):res.message='fired';res.left_ms=0;return res
 pub.create_service(Gimbal,'mil/gimbal',gimbal);pub.create_service(Fire,'mil/fire',fire)
 image=Image();image.width=640;image.height=480;image.step=1920;image.encoding='bgr8';image.data=array('B',bytes(640*480*3))
 scan=LaserScan();scan.angle_min=-math.pi;scan.angle_increment=math.tau/180;scan.range_max=8.;scan.ranges=[1.]*180;scan.ranges[0]=float('nan')
 odom=Odometry();odom.pose.pose.orientation.w=1.
 imu=Imu();imu.orientation.w=1.
 state=State();state.hp=0;state.hp_max=4;state.battery_pct=80.
 match=Match();match.mode='mission';match.cmd='start'
 def emit(fresh=True):
  if fresh:
   stamp=pub.get_clock().now().to_msg()
   for msg in (image,scan,odom,imu):msg.header.stamp=stamp
  for topic,msg in [('mil/state',state),('mil/match',match),('odom',odom),('imu/data',imu),('scan',scan),('image_raw',image)]:publishers[topic].publish(msg)
  rclpy.spin_once(pub,timeout_sec=.02)
 try:
  for _ in range(30):emit();time.sleep(.03)
  sample=robot.sim_get('state');assert sample['camera_available'] and sample['hp']==0 and sample['scan']['ranges'][0] is None,sample
  raw,headers=robot.sim_get('camera');assert raw.startswith(b'\xff\xd8') and headers['X-Frame-Seq']
  robot.sim_post('cmd_vel',{'linear_x':.12,'angular_z':.2})
  result={}
  def service_calls():
   result['gimbal']=robot.sim_post('gimbal',{'pan':2.,'tilt':8.,'speed':30.,'acc':20.})
   result['fire']=robot.sim_post('fire')
  thread=threading.Thread(target=service_calls);thread.start()
  for _ in range(30):emit();time.sleep(.02)
  thread.join();assert result['fire']['message']=='fired' and result['gimbal']['message']=='ok',result
  assert (.12,.2) in commands,commands
  sample=robot.sim_get('state');assert sample['ammo']==7 and sample['pan']==2.,sample
  for _ in range(35):emit(fresh=False);time.sleep(.02)
  stale=robot.sim_get('state');assert stale['camera_available'] is False and stale['cmd']=='pause',stale
  print(json.dumps({'passed':True,'domain':'synthetic ROS2 Humble pub/sub/service fixture, not physical robot','checks':['own state HP0 readable','bgr8 camera JPEG+source stamp','NaN scan unknown','cmd_vel published','gimbal/fire service reply','command estimates ammo8->7','duplicate stamped source becomes stale']},ensure_ascii=False))
 finally:
  robot.close();pub.destroy_node();rclpy.shutdown()
if __name__=='__main__':main()
