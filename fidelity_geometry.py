"""Small receiver disk and swept footprint geometry, in metres/radians.
All receiver dimensions/positions are explicitly provisional model parameters.
"""
import math

def ray_aabb(origin,direction,low,high):
    near,far=0.,float('inf')
    for p,d,lo,hi in zip(origin,direction,low,high):
        if abs(d)<1e-12:
            if p<lo or p>hi:return None
        else:
            a,b=(lo-p)/d,(hi-p)/d;near=max(near,min(a,b));far=min(far,max(a,b))
            if near>far:return None
    return near if far>=0 else None

def ray_disk(origin,direction,receiver):
    n=receiver['normal'];den=sum(a*b for a,b in zip(n,direction))
    if den>=-1e-10:return None # receiver only accepts incoming light on its front
    c=(receiver['x'],receiver['y'],receiver['z'])
    distance=sum((q-p)*v for q,p,v in zip(c,origin,n))/den
    if distance<0:return None
    point=[p+distance*d for p,d in zip(origin,direction)]
    return distance if sum((p-q)**2 for p,q in zip(point,c))<=receiver['radius']**2+1e-12 else None

def laser_pose(robot,geometry):
    heading=robot.yaw-math.radians(robot.pan*.5);pitch=math.radians(robot.tilt*.15)
    direction=(math.cos(heading)*math.cos(pitch),math.sin(heading)*math.cos(pitch),math.sin(pitch))
    # Muzzle follows pan/tilt around pivot; .256 is neutral emitter height.
    origin=(robot.x+geometry['emitter_forward']*direction[0],robot.y+geometry['emitter_forward']*direction[1],geometry['emitter_z']+geometry['emitter_forward']*direction[2])
    return origin,direction

def robot_receivers(robot,geometry):
    c,s=math.cos(robot.yaw),math.sin(robot.yaw);out=[]
    for side,offset in [('left',geometry['robot_receiver_lateral']),('right',-geometry['robot_receiver_lateral'])]:
        out.append(dict(id=side,x=robot.x+geometry['robot_receiver_forward']*c-offset*s,y=robot.y+geometry['robot_receiver_forward']*s+offset*c,z=geometry['robot_receiver_z'],normal=[c,s,0],radius=geometry['robot_receiver_radius']))
    return out

def ray_robot(origin,direction,robot,geometry):
    c,s=math.cos(robot.yaw),math.sin(robot.yaw);ox,oy=origin[0]-robot.x,origin[1]-robot.y
    local=(ox*c+oy*s,-ox*s+oy*c,origin[2]);d=(direction[0]*c+direction[1]*s,-direction[0]*s+direction[1]*c,direction[2])
    hits=[ray_aabb(local,d,(-geometry['robot_length']/2,-geometry['robot_width']/2,0),(geometry['robot_length']/2,geometry['robot_width']/2,.24)),ray_aabb(local,d,(-.04,-.035,.24),(.07,.035,.32))]
    return min((h for h in hits if h is not None),default=None)

def intersects_line(pose,line,geometry):
    """Clip a finite sensor line to robot's oriented footprint, not center/AABB."""
    x,y,yaw=pose;c,s=math.cos(yaw),math.sin(yaw)
    points=[]
    for py in (line['y_min'],line['y_max']):
        dx,dy=line['x']-x,py-y;points.append((dx*c+dy*s,-dx*s+dy*c))
    p,q=points;delta=(q[0]-p[0],q[1]-p[1]);t0,t1=0.,1.
    for pos,d,half in zip(p,delta,(geometry['robot_length']/2,geometry['robot_width']/2)):
        if abs(d)<1e-12:
            if abs(pos)>half+1e-12:return False
        else:
            a,b=(-half-pos)/d,(half-pos)/d;t0=max(t0,min(a,b));t1=min(t1,max(a,b))
            if t0>t1:return False
    return True

def crossing_fraction(before,after,line,geometry):
    """First outside->line entry. 32 brackets then bisection to sub-nanostep.
    At max .6m/s, 3rad/s and 50ms, this resolves the finite 1.2m sensor line.
    """
    dyaw=math.atan2(math.sin(after[2]-before[2]),math.cos(after[2]-before[2]))
    def occupied(f):return intersects_line((before[0]+f*(after[0]-before[0]),before[1]+f*(after[1]-before[1]),before[2]+f*dyaw),line,geometry)
    if occupied(0):return None
    for i in range(1,33):
        if not occupied(i/32):continue
        lo,hi=(i-1)/32,i/32
        for _ in range(32):
            mid=(lo+hi)/2
            if occupied(mid):hi=mid
            else:lo=mid
        return hi
    return None
