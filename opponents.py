"""Progressive locomotion/tactics for existing privileged demonstration bots.
Does not change scoring, reports, weapons, speed limits or external contestants.
"""
import math

def angle(a):return math.atan2(math.sin(a),math.cos(a))

def clear(e,r,point):
 distance=math.dist((r.x,r.y),point);steps=max(1,math.ceil(distance/.04))
 return all(e.free(r.x+(point[0]-r.x)*i/steps,r.y+(point[1]-r.y)*i/steps,r) for i in range(1,steps+1))

def objective(e,r,goal,aim):
 if e.difficulty<2 or r.strategy=='external':return goal,aim
 # The existing defensive role stays near home instead of all three racing.
 if e.target_issued and r.strategy=='defender':
  nearby=[o for o in e.robots.values() if o.included and o.camp!=r.camp and o.hp>0 and math.dist((r.x,r.y),(o.x,o.y))<3.]
  victim=min(nearby,key=lambda o:math.dist((r.x,r.y),(o.x,o.y))) if nearby else None
  return (1.85 if r.camp=='BLUE' else 4.27,1.2),(victim.x,victim.y) if victim else None
 # Prepare the base named in the already accepted M1. Never read future M5.
 if not e.target_issued and e.elapsed>=180 and r.strategy in ('capturer','balanced'):
  name=r.reports.get('interpretation',{}).get('base')
  b=next((b for b in e.arena['bases'] if b['id']==name),None)
  if b:return ((5.5 if b['camp']=='RED' else .62),b['y']),None
 return goal,aim

def navigate(e,r,goal):
 if r.strategy=='external':return
 if not goal:r.v=r.w=0.;return
 now=e.clock;pos=(r.x,r.y)
 mem=getattr(r,'navigation_memory',None)
 if mem is None:
  mem={'goal':goal,'anchor':pos,'at':now,'until':0.,'direction':1 if int(r.slot[-1])%2 else -1};r.navigation_memory=mem
 if math.dist(pos,goal)<.07:r.v=r.w=0.;r.command_at=now;return
 if now<mem['until']:
  back=(r.x-.12*math.cos(r.yaw),r.y-.12*math.sin(r.yaw))
  r.v=-.10 if clear(e,r,back) else 0.;r.w=mem['direction']*1.2;r.command_at=now;return
 if math.dist(pos,mem['anchor'])>.12:mem['anchor']=pos;mem['at']=now
 if now-mem['at']>7.:
  mem.update(at=now,until=now+1.2,direction=-mem['direction']);r.route=[];r.route_at=-999.
  r.v=0.;r.w=0.;r.command_at=now;return
 if now-r.route_at>.8 or not r.route or (r.route_goal and math.dist(r.route_goal,goal)>.35):
  r.route=e._path(r,*goal);r.route_at=now;r.route_goal=goal
 while r.route and math.dist(pos,r.route[0])<.13:r.route.pop(0)
 point=r.route[0] if r.route else goal
 # Shorten the path only across a completely collision-free segment.
 for candidate in r.route:
  if math.dist(pos,candidate)>.55:break
  if clear(e,r,candidate):point=candidate
  else:break
 if math.dist(pos,goal)<.5 and clear(e,r,goal):point=goal
 err=angle(math.atan2(point[1]-r.y,point[0]-r.x)-r.yaw)
 r.w=max(-2.2,min(2.2,err*5.))
 speed=.36 if r.strategy=='scout' else .25 if r.strategy=='defender' else .3
 r.v=speed*max(0.,math.cos(err)) if abs(err)<.65 else 0.
 ahead=(r.x+math.cos(r.yaw)*.22,r.y+math.sin(r.yaw)*.22)
 if r.v and not clear(e,r,ahead):r.v=0.;r.route_at=-999.
 r.command_at=now


POLICY_VERSION='sensor-five-roles-v3'
ROLE_DESCRIPTIONS={
 'scout':'wide patrol with short observation stops',
 'defender':'home-side patrol with long guard stops',
 'hunter':'central advance and sustained anonymous-return fire',
 'capturer':'staging then published M5 objective crossing',
 'balanced':'midfield patrol alternating movement and observation',
}

def sensor_policy(observation,own_order,memory,role):
 """Own sensors + published orders only. Map calibration is a public-plan assumption.
 No enemy identity from lidar; firing at returns is a deliberately weak baseline.
 No hidden M1 answers, target locations, M3 labels/counts or enemy state are read.
 """
 state=dict(memory);scan=observation.get('scan',{});ranges=scan.get('ranges',[])
 now=observation.get('measured_at',0.);odom=observation.get('odom',{})
 state.update(role=role,policy_version=POLICY_VERSION)
 if not ranges or scan.get('measured_at') is None or now-scan.get('received_at',scan['measured_at'])>.7:
  state['intent']='sensor_stale_stop'
  return [('cmd_vel',dict(linear_x=0.,angular_z=0.))],state
 slot=observation.get('slot','B-1');blue=slot.startswith('B');sign=1 if blue else -1
 home_y=4.15-(int(slot[-1])-1)*1.65
 x=(.28 if blue else 5.84)+sign*odom.get('x',0.);y=home_y+sign*odom.get('y',0.)
 yaw=angle(odom.get('yaw',0.)+(0 if blue else math.pi))
 # Public nominal geometry; does not read actual layout, randomized spawn or obstacles.
 def point(forward,height):return [forward if blue else 6.12-forward,height]
 routes={
  'scout':[point(2.55,4.55),point(3.55,.45),point(4.55,4.55),point(1.6,.45)],
  'defender':[point(1.45,home_y),point(1.45,max(.45,home_y-.65)),point(1.45,min(4.55,home_y+.65))],
  'hunter':[point(3.65,2.5),point(4.15,1.55),point(4.15,3.45)],
  'capturer':[point(4.35,2.5)],
  'balanced':[point(2.1,1.65),point(3.1,3.35),point(2.1,3.35),point(3.1,1.65)],
 }
 route=routes.get(role,routes['balanced']);index=state.get('waypoint',0)%len(route)
 goal=route[index];intent='patrol';target=own_order.get('target',{})
 objective=target.get('order',{}).get('objective') if target.get('accepted') else None
 published=role=='capturer' and objective in [c+'_'+t for c in ('BLUE','RED') for t in ('COMMAND_POST','MAINTENANCE','SUPPLY_DEPOT')]
 if published:
  rows={'COMMAND_POST':4.15,'MAINTENANCE':2.5,'SUPPLY_DEPOT':.85}
  goal=[.28 if objective.startswith('BLUE_') else 5.84,rows[objective.split('_',1)[1]]]
  intent='capture_published_target'
 distance=math.hypot(goal[0]-x,goal[1]-y)
 hold=False
 if distance<.20:
  if published:hold=True
  elif role=='capturer':hold=True;intent='stage_wait_m5'
  else:
   reached=state.setdefault('reached_at',now)
   dwell={'scout':1.,'defender':12.,'hunter':4.,'balanced':5.}.get(role,2.)
   if now-reached<dwell:hold=True;intent='guard' if role=='defender' else 'observe'
   else:state['waypoint']=(index+1)%len(route);state.pop('reached_at',None);goal=route[state['waypoint']]
 else:state.pop('reached_at',None)
 amin=scan.get('angle_min',-math.pi);inc=scan.get('angle_increment',math.tau/max(1,len(ranges)))
 def sector(low,high):
  return [float(value) for i,value in enumerate(ranges) if value is not None and math.isfinite(value) and low<=amin+i*inc<=high]
 front=sector(-.32,.32);left=sector(.35,1.35);right=sector(-1.35,-.35)
 rear=sector(-math.pi,-2.65)+sector(2.65,math.pi)
 if not front:
  state['intent']='sensor_empty_stop'
  return [('cmd_vel',dict(linear_x=0.,angular_z=0.))],state
 clearance=min(front);turn=1. if sum(left)/max(1,len(left))>=sum(right)/max(1,len(right)) else -1.
 anchor=state.get('anchor',[x,y]);at=state.get('anchor_at',now)
 if math.hypot(x-anchor[0],y-anchor[1])>.10 or hold:anchor=[x,y];at=now
 state.update(anchor=anchor,anchor_at=at,own_order_received=bool(own_order.get('accepted')),goal_world=goal)
 if now-at>5 and not hold:
  state.update(recover_until=now+1.3,anchor_at=now,recover_direction=turn)
 if hold:v,w=0.,0.
 elif now<state.get('recover_until',-1):
  v=-.12 if rear and min(rear)>.5 else 0.;w=state.get('recover_direction',turn)*1.25;intent='recover'
 elif clearance<.43:
  # Latch a turn for a short interval instead of left/right flicker.
  if now>=state.get('avoid_until',-1):state.update(avoid_until=now+1.1,avoid_direction=turn)
  v,w=0.,state['avoid_direction']*1.35;intent='avoid'
 else:
  error=angle(math.atan2(goal[1]-y,goal[0]-x)-yaw)
  # Follow the measured open side while the direct heading is obstructed.
  if clearance<.75 and abs(error)<.75:error+=turn*.8
  speed={'scout':.34,'defender':.18,'hunter':.28,'capturer':.32,'balanced':.23}.get(role,.22)
  w=max(-1.5,min(1.5,error*2.4));v=speed*max(0.,math.cos(error)) if abs(error)<.85 else 0.
 # A front return is not an identified opponent. Log this limitation explicitly.
 fire_interval={'scout':20.,'defender':8.,'hunter':4.5,'capturer':9999.,'balanced':13.}.get(role,13.)
 fire_range={'scout':1.1,'defender':1.8,'hunter':2.4,'capturer':0.,'balanced':1.5}.get(role,1.5)
 fire=.55<clearance<fire_range and now-state.get('last_fire',0)>fire_interval
 if fire:
  state['last_fire']=now
  if role=='hunter':state['engage_until']=now+2.3
 if role=='hunter' and now<state.get('engage_until',-1):v,w=0.,0.;intent='engage_return'
 commands=[('cmd_vel',dict(linear_x=v,angular_z=w)),('gimbal',dict(pan=0.,tilt=0.,speed=80.,acc=20.))]
 if fire:commands.append(('fire',{}))
 state.update(intent=intent,estimated_world=[x,y,yaw])
 return commands,state
