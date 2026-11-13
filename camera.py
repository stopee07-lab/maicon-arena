"""Deterministic low-fidelity pinhole sensor renderer. Not a calibrated Jetson camera.
Provides synthetic pixels to contestant routes, labels ONLY to administrator training export.
No text labels or enemy telemetry are rendered into the robot camera.
"""
from PIL import Image, ImageDraw, ImageEnhance
import math, random, copy, base64, io
CLASSES=['military_truck','mortar','tank','missile','ammo_box','drum']
W,H=640,480

def field(r,k,d=0): return r.get(k,d) if isinstance(r,dict) else getattr(r,k,d)
def icon(kind,size=128):
    im=Image.new('RGB',(size,size),(207,201,176));d=ImageDraw.Draw(im);s=size/128
    def box(b,c):d.rectangle(tuple(int(x*s) for x in b),fill=c)
    def ellipse(b,c):d.ellipse(tuple(int(x*s) for x in b),fill=c)
    olive=(65,79,47);dark=(31,40,29);light=(115,127,80)
    if kind=='tank':
        box((18,67,110,99),dark);box((23,58,103,85),olive);ellipse((43,39,84,70),light);box((69,48,122,56),olive)
        for x in range(29,105,18):ellipse((x-7,83,x+7,97),(102,109,83))
    elif kind=='military_truck':
        box((12,43,76,89),olive);box((76,58,112,91),light);box((83,61,108,76),(149,178,184))
        for x in [30,64,98]:ellipse((x-10,81,x+10,105),dark)
    elif kind=='mortar':
        d.line((40*s,96*s,80*s,31*s),fill=olive,width=max(2,int(14*s)));d.line((62*s,64*s,99*s,107*s),fill=dark,width=max(2,int(6*s)));box((18,101,109,110),olive)
    elif kind=='missile':
        d.polygon([(70*s,15*s),(88*s,38*s),(88*s,98*s),(51*s,98*s),(51*s,38*s)],fill=olive);d.polygon([(51*s,75*s),(30*s,112*s),(51*s,103*s)],fill=dark);d.polygon([(88*s,75*s),(108*s,112*s),(88*s,103*s)],fill=dark)
    elif kind=='ammo_box':
        box((20,36,108,100),olive);box((17,30,111,45),light);box((25,52,31,92),dark);box((94,52,100,92),dark);box((54,50,75,59),(157,148,77))
    else:
        box((35,28,94,104),olive);ellipse((35,18,94,42),light);ellipse((35,92,94,114),dark);box((33,47,96,53),light);box((33,82,96,88),light)
    return im

def render_camera(engine,robot):
    arena=engine.arena;im=Image.new('RGB',(W,H),(139,158,169));d=ImageDraw.Draw(im);d.rectangle((0,H//2,W,H),fill=(85,86,81));mask=Image.new('L',(W,H),0);md=ImageDraw.Draw(mask)
    yaw=field(robot,'yaw')-math.radians(field(robot,'pan')*.5);tilt=math.radians(field(robot,'tilt')*.15);co,si=math.cos(yaw),math.sin(yaw);ct,st=math.cos(tilt),math.sin(tilt);f=W/2/math.tan(math.radians(160/2));rx,ry=field(robot,'x'),field(robot,'y')
    def proj(v):
        dx,dy,z=v[0]-rx,v[1]-ry,v[2]-.24;fw=dx*co+dy*si;left=-dx*si+dy*co;dep=fw*ct+z*st;up=z*ct-fw*st
        return (W/2-left*f/dep,H/2-up*f/dep,dep) if dep>.055 else None
    faces=[];objects={};nominal={}
    def face(vertices,color,oid=0,kind=None):
        p=[proj(v) for v in vertices]
        if any(v is None for v in p):return
        xy=[(max(-2000,min(2000,v[0])),max(-2000,min(2000,v[1]))) for v in p]
        if max(v[0] for v in xy)<0 or min(v[0] for v in xy)>W or max(v[1] for v in xy)<0 or min(v[1] for v in xy)>H:return
        faces.append((sum(v[2] for v in p)/len(p),xy,color,oid,kind))
    def cube(x,y,w,h,z,color):
        a=(x-w/2,y-h/2);b=(x+w/2,y+h/2)
        pts=[(a[0],a[1],0),(b[0],a[1],0),(b[0],b[1],0),(a[0],b[1],0)]
        for i in range(4):
            q=pts[i];r=pts[(i+1)%4];face([q,r,(r[0],r[1],z),(q[0],q[1],z)],tuple(max(0,c-i*7) for c in color))
        face([(p[0],p[1],z) for p in pts],tuple(min(255,c+20) for c in color))
    # Perspective floor grid, metres. Depth sorting only used for solid objects.
    for axis in [0,1]:
        for k in range(32):
            a=(k*.2,0,0) if axis==0 else (0,k*.2,0);b=(k*.2,5,0) if axis==0 else (6.12,k*.2,0)
            p,q=proj(a),proj(b)
            if p and q:d.line([(p[0],p[1]),(q[0],q[1])],fill=(109,109,99),width=1)
    for o in arena.get('obstacles',[]):cube(o['x'],o['y'],o.get('w',.4),o.get('h',.4),.4,(105,111,98))
    for x,y,w,h in [(3.06,0,6.12,.04),(3.06,5,6.12,.04),(0,2.5,.04,5),(6.12,2.5,.04,5)]:cube(x,y,w,h,.4,(120,126,128))
    for r in (engine.robots.values() if isinstance(engine.robots,dict) else engine.robots):
        if field(r,'slot')==field(robot,'slot'):continue
        cube(field(r,'x'),field(r,'y'),.25,.23,.23,(55,97,162) if field(r,'camp')=='BLUE' else (164,68,61))
    for t in arena.get('targets',[]):cube(t['x'],t['y'],.18,.18,.38,(115,65,54) if t.get('destroyed') else (190,162,80))
    for i,o in enumerate(arena.get('objects',[]),1):
        kind=o['type'].lower()
        if kind not in CLASSES:continue
        x,y=o['x'],o['y'];normal=o.get('normal')
        if normal and (rx-x)*normal[0]+(ry-y)*normal[1]<=0:continue
        l=(-normal[1]*.14,normal[0]*.14) if normal else (-si*.14,co*.14)
        verts=[(x+l[0],y+l[1],.38),(x-l[0],y-l[1],.38),(x-l[0],y-l[1],.10),(x+l[0],y+l[1],.10)]
        face(verts,(190,190,170),i,kind);objects[i]=kind
    for depth,xy,color,oid,kind in sorted(faces,reverse=True,key=lambda q:q[0]):
        d.polygon(xy,fill=color);md.polygon(xy,fill=oid)
        if kind:
            x0=max(0,int(min(v[0] for v in xy)));x1=min(W,int(max(v[0] for v in xy))+1);y0=max(0,int(min(v[1] for v in xy)));y1=min(H,int(max(v[1] for v in xy))+1)
            if x1>x0 and y1>y0:
                tile=icon(kind).resize((x1-x0,y1-y0));pm=Image.new('L',(W,H),0);ImageDraw.Draw(pm).polygon(xy,fill=255);im.paste(tile,(x0,y0),pm.crop((x0,y0,x1,y1)));nominal[oid]=(x1-x0)*(y1-y0)
    # Only visible pixels remain after nearer surfaces occlude labels.
    annotations=[]
    for oid,kind in objects.items():
        binary=mask.point([255 if v==oid else 0 for v in range(256)]);bb=binary.getbbox()
        if not bb:continue
        count=sum(binary.histogram()[1:])
        if count<16 or count/max(1,nominal.get(oid,count))<.3:continue
        x0,y0,x1,y1=bb;annotations.append({'class_id':CLASSES.index(kind),'bbox':[(x0+x1)/2/W,(y0+y1)/2/H,(x1-x0)/W,(y1-y0)/H]})
    return im,annotations

def render_drone(engine):
    im=Image.new('RGB',(800,654),(52,59,64));d=ImageDraw.Draw(im);sc=120;ox=33;oy=27
    def p(x,y):return (ox+x*sc,oy+(5-y)*sc)
    d.rectangle([p(0,5),p(6.12,0)],fill=(81,87,85),outline=(200,200,190),width=3)
    for k in range(31):d.line([p(k*.2,0),p(k*.2,5)],fill=(98,105,100))
    for k in range(26):d.line([p(0,k*.2),p(6.12,k*.2)],fill=(98,105,100))
    for o in engine.arena.get('obstacles',[]):
        x,y,w,h=o['x'],o['y'],o.get('w',.4),o.get('h',.4);d.rectangle([p(x-w/2,y+h/2),p(x+w/2,y-h/2)],fill=(43,48,43),outline=(174,174,150))
    for o in engine.arena.get('objects',[]):
        x,y=p(o['x'],o['y']);d.rectangle((x-5,y-5,x+5,y+5),fill=(166,164,104))
    for r in engine.robots.values():
        x,y=p(r.x,r.y);c=(82,143,229) if r.camp=='BLUE' else (224,92,83);d.ellipse((x-11,y-11,x+11,y+11),fill=c);d.line((x,y,x+18*math.cos(r.yaw),y-18*math.sin(r.yaw)),fill=(245,242,219),width=3)
    return im

def training_frame(engine,slot,seed):
    rng=random.Random(seed);r=copy.copy(engine.robots[slot]);r.x=rng.uniform(.3,5.82);r.y=rng.uniform(.3,4.7);r.yaw=rng.uniform(-math.pi,math.pi);r.pan=rng.uniform(-35,35);r.tilt=rng.uniform(-20,20)
    # Find a free training pose; never teleport the live robot.
    for _ in range(50):
        if not any(abs(r.x-o['x'])<o.get('w',.4)/2+.18 and abs(r.y-o['y'])<o.get('h',.4)/2+.18 for o in engine.arena.get('obstacles',[])):break
        r.x=rng.uniform(.3,5.82);r.y=rng.uniform(.3,4.7)
    im,ann=render_camera(engine,r);im=ImageEnhance.Brightness(im).enhance(rng.uniform(.65,1.35));buf=io.BytesIO();im.save(buf,'JPEG',quality=90)
    return {'image_base64':base64.b64encode(buf.getvalue()).decode(),'width':W,'height':H,'annotations':ann,'classes':CLASSES,'synthetic':True,'seed':seed,'fidelity':'Pinhole 160deg assumption; billboard icons; no real optical distortion; approximate painter occlusion. Not competition imagery.'}
