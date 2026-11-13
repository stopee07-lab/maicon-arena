import * as THREE from '/vendor/three.module.js';

const BLUE = 0x73aaf3, RED = 0xe68c81, GOLD = 0xe6be63;
const campColor = camp => String(camp).toLowerCase().startsWith('b') ? BLUE : RED;

export class ArenaScene {
  constructor(container, onSelect) {
    this.container = container; this.onSelect = onSelect; this.mode = 'orbit'; this.selected = 'B-1'; this.robotMeshes = new Map(); this.labels = new Map(); this.targetMeshes = new Map(); this.snapshot = null;
    this.angle = 0; this.elevation = 0.91; this.distance = 8.4; this.gridVisible = true; this.labelsVisible = true;
    this.scene = new THREE.Scene(); this.scene.background = new THREE.Color(0x161d1e); this.scene.fog = new THREE.Fog(0x161d1e, 15, 27);
    this.camera = new THREE.PerspectiveCamera(44, 1, 0.03, 50); this.center = new THREE.Vector3(3.06, 0, -2.5);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2)); this.renderer.shadowMap.enabled = true; this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace; this.renderer.toneMapping = THREE.ACESFilmicToneMapping; this.renderer.toneMappingExposure = 1.3;
    container.prepend(this.renderer.domElement);
    this.scene.add(new THREE.HemisphereLight(0xf4f1d5, 0x5d7173, 2.3));
    const sun = new THREE.DirectionalLight(0xfff0d3, 3); sun.position.set(1, 10, 3); sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048); Object.assign(sun.shadow.camera, {left:-7,right:7,top:7,bottom:-7,near:0.1,far:24}); sun.shadow.bias = -.0005; sun.shadow.normalBias = .02; this.scene.add(sun);
    const fill = new THREE.DirectionalLight(0xa7c9e7,.8); fill.position.set(8,4,-6); this.scene.add(fill);
    this.staticGroup = new THREE.Group(); this.dynamicGroup = new THREE.Group(); this.scene.add(this.staticGroup, this.dynamicGroup);
    this.resizeObserver = new ResizeObserver(() => this.resize()); this.resizeObserver.observe(container);
    this.disposed=false;this.bindControls(); this.resize(); this.animate();
  }
  material(color, extra={}) { return new THREE.MeshStandardMaterial({color,roughness:.8,metalness:.12,...extra}); }
  box(w,h,d,mat,x,y,z,parent=this.staticGroup) { const m = new THREE.Mesh(new THREE.BoxGeometry(w,h,d),mat); m.position.set(x,y,z); m.castShadow = true; m.receiveShadow = true; parent.add(m); return m; }
  textTexture(text, {color='#b6b9a7',background=null,width=512,height=128,fontSize=52}={}) { const c = document.createElement('canvas'); c.width=width; c.height=height; const ctx=c.getContext('2d'); if(background){ctx.fillStyle=background;ctx.fillRect(0,0,width,height)} ctx.fillStyle=color;ctx.font=`600 ${fontSize}px ui-monospace, monospace`;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(text,width/2,height/2);const tex=new THREE.CanvasTexture(c);tex.colorSpace=THREE.SRGBColorSpace;return tex; }
  groundText(text,x,y,width=.7,color='#aaaf9d') { const mesh=new THREE.Mesh(new THREE.PlaneGeometry(width,width/4), new THREE.MeshBasicMaterial({map:this.textTexture(text,{color}),transparent:true,depthWrite:false}));mesh.rotation.x=-Math.PI/2;mesh.position.set(x,.013,-y);this.staticGroup.add(mesh);return mesh; }
  line(points,color=0x9caa99,opacity=.5,parent=this.staticGroup) { const geometry=new THREE.BufferGeometry().setFromPoints(points.map(([x,y,z])=>new THREE.Vector3(x,y,z)));const line=new THREE.Line(geometry,new THREE.LineBasicMaterial({color,transparent:true,opacity}));parent.add(line);return line; }
  buildArena(arena) {
    this.arenaKey=this.layoutKey(arena);this.width=arena.width;this.height=arena.height;this.center.set(arena.width/2,0,-arena.height/2);
    const w=arena.width,h=arena.height;const floorCanvas=document.createElement('canvas');floorCanvas.width=1224;floorCanvas.height=1000;const c=floorCanvas.getContext('2d');c.fillStyle='#303638';c.fillRect(0,0,1224,1000);
    // A 20 cm printed grid, matching the reference arena's floor markings.
    for(let x=0;x<=w+.001;x+=.2){c.strokeStyle=Math.round(x*5)%5===0?'#424c4d':'#384143';c.lineWidth=1;c.beginPath();c.moveTo(x/w*1224,0);c.lineTo(x/w*1224,1000);c.stroke();}
    for(let y=0;y<=h+.001;y+=.2){c.strokeStyle=Math.round(y*5)%5===0?'#424c4d':'#384143';c.beginPath();c.moveTo(0,y/h*1000);c.lineTo(1224,y/h*1000);c.stroke();}
    c.strokeStyle='#c6b374';c.lineWidth=3;c.setLineDash([18,18]);c.beginPath();c.moveTo(612,32);c.lineTo(612,968);c.stroke();c.setLineDash([]);c.strokeStyle='#b3b9a9';c.lineWidth=2;c.beginPath();c.ellipse(612,500,142,142,0,0,Math.PI*2);c.stroke();
    for(const x of [265,765])for(const y of [10,970]){c.fillStyle='#bd9b43';c.fillRect(x,y,210,20);c.save();c.beginPath();c.rect(x,y,210,20);c.clip();c.fillStyle='#262c2b';for(let a=-30;a<250;a+=36){c.beginPath();c.moveTo(x+a,y);c.lineTo(x+a+20,y);c.lineTo(x+a+40,y+20);c.lineTo(x+a+20,y+20);c.fill();}c.restore();}
    const tex=new THREE.CanvasTexture(floorCanvas);tex.colorSpace=THREE.SRGBColorSpace;tex.anisotropy=this.renderer.capabilities.getMaxAnisotropy();this.floorTexture=tex;
    const floor=new THREE.Mesh(new THREE.PlaneGeometry(w,h),this.material(0xffffff,{map:tex}));floor.rotation.x=-Math.PI/2;floor.position.set(w/2,0,-h/2);floor.receiveShadow=true;this.staticGroup.add(floor);this.floor=floor;
    this.box(w+.16,.14,h+.16,this.material(0x111b1e),w/2,-.09,-h/2);
    const wall=this.material(0x252d2e);this.box(w+.16,.4,.08,wall,w/2,.2,-h-.04);this.box(.08,.4,h+.12,wall,-.04,.2,-h/2);this.box(.08,.4,h+.12,wall,w+.04,.2,-h/2);
    // A cutaway near wall keeps the operating view unobstructed.
    this.box(w+.16,.14,.08,wall,w/2,.07,.04);this.box(w+.12,.025,.025,this.material(0x626453),w/2,.155,.04);
    for(const x of [0,w])for(const y of [0,h]){this.box(.12,.44,.12,this.material(0x424936),x,.22,-y);this.box(.13,.025,.13,this.material(GOLD),x,.447,-y)}
    this.groundText('BLUE',.64,h-.23,.5,'#8caad0');this.groundText('RED',w-.64,h-.23,.5,'#c8998f');this.groundText('COMMON',w/2,.28,.53,'#aaae9b');this.groundText('COMMON',w/2,h-.28,.53,'#aaae9b');
    for(const s of arena.sectors||[]){this.line([[s.x,.01,-s.y],[s.x+s.w,.01,-s.y],[s.x+s.w,.01,-s.y-s.h],[s.x,.01,-s.y-s.h],[s.x,.01,-s.y]],0x899788,.24);this.groundText(s.name,s.x+s.w*.7,s.y+s.h-.12,.44,'#828f82');}
    for(const b of arena.bases||[]){const x=b.x,y=b.y,bw=b.w||.55,bh=b.h||.65;const color=campColor(b.camp);const plate=new THREE.Mesh(new THREE.PlaneGeometry(bw,bh),new THREE.MeshBasicMaterial({color,transparent:true,opacity:.07,depthWrite:false}));plate.rotation.x=-Math.PI/2;plate.position.set(x,.008,-y);this.staticGroup.add(plate);this.line([[x-bw/2,.02,-y-bh/2],[x+bw/2-.09,.02,-y-bh/2],[x+bw/2,.02,-y-bh/2+.09],[x+bw/2,.02,-y+bh/2-.09],[x+bw/2-.09,.02,-y+bh/2],[x-bw/2,.02,-y+bh/2],[x-bw/2,.02,-y-bh/2]],GOLD,.85);this.groundText(`${String(b.camp).startsWith('B')?'B':'R'}-${Math.max(1,Math.round((4.15-y)/1.65)+1)}`,x,y,Math.min(.3,bw*.8),'#c6cbbb');this.box(.045,.15,.045,this.material(0x37443b),x-bw*.38,.075,-y-bh*.42);this.box(.055,.04,.055,this.material(color,{emissive:color,emissiveIntensity:.35}),x-bw*.38,.17,-y-bh*.42);}
    const obstacleMat=this.material(0x424847);for(const o of arena.obstacles||[]){const m=this.box(o.w,.4,o.h,obstacleMat,o.x,.2,-o.y);const edges=new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry),new THREE.LineBasicMaterial({color:0x697168,transparent:true,opacity:.3}));m.add(edges);this.box(o.w-.025,.015,o.h-.025,this.material(0x4a504b),o.x,.408,-o.y);}
    const facilityLoader=new THREE.TextureLoader();
    for(const t of arena.targets||[]){
      const group=new THREE.Group();this.staticGroup.add(group);
      const [nx,ny]=t.normal||[t.camp==='RED'?-1:1,0];
      const tex=facilityLoader.load(`/assets/facilities/${t.id.split('_').slice(1).join('_')}.png`);tex.colorSpace=THREE.SRGBColorSpace;
      const photo=new THREE.Mesh(new THREE.PlaneGeometry(.38,.38),this.material(0xffffff,{map:tex,metalness:0}));
      photo.position.set(t.x+nx*.201,.2,-t.y-ny*.201);photo.rotation.y=Math.atan2(nx,-ny);group.add(photo);
      const r=t.receiver||{x:t.x+nx*.204,y:t.y+ny*.204,z:.34,radius:.04};
      const receiver=new THREE.Mesh(new THREE.CircleGeometry(r.radius,24),this.material(0xeeeeee));receiver.position.set(r.x,r.z,-r.y);receiver.rotation.y=Math.atan2(nx,-ny);group.add(receiver);
      const beacon=this.box(.12,.03,.07,this.material(0x300604,{emissive:0x300604}),t.x,.415,-t.y,group);
      this.targetMeshes.set(t.id,{group,beacon});
    }
    for(const o of arena.obstacles||[]){if(o.kind!=='decor')continue;const tex=facilityLoader.load(`/assets/decor/${o.decor_type||'ruins'}.png`);tex.colorSpace=THREE.SRGBColorSpace;
      for(const [nx,ny] of [[1,0],[-1,0],[0,1],[0,-1]]){const photo=new THREE.Mesh(new THREE.PlaneGeometry(.38,.38),this.material(0xffffff,{map:tex,metalness:0}));photo.position.set(o.x+nx*.201,.2,-o.y-ny*.201);photo.rotation.y=Math.atan2(nx,-ny);this.staticGroup.add(photo);}}
    // Fixed observation UAV and support, approximately 2.5 m high (PDF1 p22).
    const stand=this.material(0x88979b,{metalness:.7,roughness:.4});
    const dark=this.material(0x222d32,{metalness:.35});
    const sx=-.48,sz=-h/2,ux=w/2,uy=2.5;
    this.box(.5,.08,.55,this.material(0x3f4944),sx,.04,sz);
    this.box(.045,2.58,.045,stand,sx,1.33,sz);
    this.box(ux-sx,.035,.035,stand,(ux+sx)/2,2.59,sz);
    const brace=(a,b,r=.012)=>{const start=new THREE.Vector3(...a),end=new THREE.Vector3(...b),delta=end.clone().sub(start);const tube=new THREE.Mesh(new THREE.CylinderGeometry(r,r,delta.length(),8),stand);tube.position.copy(start.add(end).multiplyScalar(.5));tube.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),delta.normalize());this.staticGroup.add(tube);};
    brace([sx,.08,sz-.24],[sx,1.15,sz]);brace([sx,.08,sz+.24],[sx,1.15,sz]);brace([sx,2.58,sz],[ux-.15,2.38,sz],.007);
    const uav=new THREE.Group();uav.position.set(ux,uy,sz);this.staticGroup.add(uav);
    this.box(.18,.065,.14,dark,0,0,0,uav);this.box(.09,.028,.07,this.material(0x536069),0,.047,0,uav);
    for(const [dx,dz]of [[-1,-1],[-1,1],[1,-1],[1,1]]){const arm=this.box(.25,.018,.025,dark,dx*.09,-.007,dz*.09,uav);arm.rotation.y=-dx*dz*Math.PI/4;const motor=new THREE.Mesh(new THREE.CylinderGeometry(.028,.028,.035,12),dark);motor.position.set(dx*.18,.004,dz*.18);uav.add(motor);const prop=new THREE.Mesh(new THREE.RingGeometry(.017,.088,32),new THREE.MeshStandardMaterial({color:0x68767b,side:THREE.DoubleSide,transparent:true,opacity:.5,roughness:.6}));prop.rotation.x=-Math.PI/2;prop.position.set(dx*.18,.028,dz*.18);uav.add(prop);}
    this.box(.04,.06,.04,dark,0,-.06,0,uav);const cameraLens=new THREE.Mesh(new THREE.CylinderGeometry(.026,.026,.025,16),this.material(0x6c99ac,{metalness:.8,roughness:.15}));cameraLens.position.set(0,-.10,0);uav.add(cameraLens);this.uav=uav;
    const objectTextures=new Map(),textureLoader=new THREE.TextureLoader();
    // Official M3 objects are printed pictures on block faces, not floor miniatures.
    for(const o of arena.objects||[]){
      const type=String(o.type).toLowerCase();
      if(!objectTextures.has(type)){
        const texture=textureLoader.load(`/assets/object-photos/${type}.png`);
        texture.colorSpace=THREE.SRGBColorSpace;
        texture.anisotropy=this.renderer.capabilities.getMaxAnisotropy();
        objectTextures.set(type,texture);
      }
      const picture=new THREE.Mesh(new THREE.PlaneGeometry(.28,.28),this.material(0xffffff,{map:objectTextures.get(type),roughness:1,metalness:0}));
      picture.position.set(o.x,.24,-o.y);
      const [nx,ny]=o.normal||[0,-1];
      picture.rotation.y=Math.atan2(nx,-ny);
      picture.receiveShadow=true;
      this.staticGroup.add(picture);
    }
  }
  makeRobot(robot) {
    const color=campColor(robot.camp),body=new THREE.Group();this.dynamicGroup.add(body);const metal=this.material(0x343c3a,{metalness:.5}),black=this.material(0x181e1d),accent=this.material(color);
    this.box(.23,.055,.18,metal,0,.085,0,body);this.box(.19,.025,.17,this.material(0x4b5550),0,.127,0,body);this.box(.085,.025,.14,black,-.04,.153,0,body);
    const wheels=[];for(const x of [-.085,0,.085])for(const z of [-.107,.107]){const tire=new THREE.Mesh(new THREE.CylinderGeometry(.045,.045,.034,16),black);tire.rotation.x=Math.PI/2;tire.position.set(x,.047,z);tire.castShadow=true;body.add(tire);wheels.push(tire);const hub=new THREE.Mesh(new THREE.CylinderGeometry(.020,.020,.035,12),metal);hub.rotation.x=Math.PI/2;hub.position.copy(tire.position);body.add(hub);}
    for(const z of [-.075,.075]){this.box(.014,.18,.014,black,-.06,.23,z,body);this.box(.019,.065,.019,accent,-.06,.245,z,body);const led=new THREE.Mesh(new THREE.SphereGeometry(.023,10,6),this.material(0xd3dacb,{emissive:color,emissiveIntensity:.25}));led.position.set(.03,.16,z);body.add(led);}
    const lidar=new THREE.Mesh(new THREE.CylinderGeometry(.034,.034,.033,16),metal);lidar.position.set(.048,.16,0);body.add(lidar);
    this.box(.027,.07,.027,metal,0,.2,0,body);const pan=new THREE.Group();pan.position.set(0,.245,0);body.add(pan);const tilt=new THREE.Group();pan.add(tilt);this.box(.07,.045,.05,black,.015,0,0,tilt);const lens=new THREE.Mesh(new THREE.CylinderGeometry(.015,.015,.01,16),this.material(0x6c9ac5,{metalness:.8,roughness:.2}));lens.rotation.z=Math.PI/2;lens.position.set(.055,.005,0);tilt.add(lens);this.box(.035,.009,.015,metal,.045,.028,0,tilt);
    const laser=new THREE.Mesh(new THREE.CylinderGeometry(.0015,.0015,1.6,5),new THREE.MeshBasicMaterial({color:0xff6b46,transparent:true,opacity:.75}));laser.rotation.z=Math.PI/2;laser.position.set(.85,.025,0);laser.visible=false;tilt.add(laser);
    this.box(.025,.025,.14,accent,.115,.10,0,body);const ring=new THREE.Mesh(new THREE.RingGeometry(.185,.194,48),new THREE.MeshBasicMaterial({color:GOLD,side:THREE.DoubleSide,transparent:true,opacity:.8}));ring.rotation.x=-Math.PI/2;ring.position.y=.005;body.add(ring);
    const label=document.createElement('button');label.className=`robot-label ${String(robot.camp).toLowerCase().startsWith('r')?'red':''}`;label.textContent=robot.slot;label.setAttribute('aria-label',`${robot.slot} 선택`);label.addEventListener('click',()=>this.onSelect(robot.slot));document.querySelector('#robot-labels').append(label);this.labels.set(robot.slot,label);
    for(const r of robot.receivers||[]){const dx=r.x-robot.x,dy=r.y-robot.y,c=Math.cos(robot.yaw),s=Math.sin(robot.yaw);const disc=new THREE.Mesh(new THREE.CircleGeometry(r.radius,24),this.material(0xeeeeee));disc.position.set(dx*c+dy*s,r.z,dx*s-dy*c);disc.rotation.y=Math.PI/2;body.add(disc);}
    this.robotMeshes.set(robot.slot,{body,pan,tilt,laser,ring,wheels,desired:new THREE.Vector3(robot.x,0,-robot.y),yaw:robot.yaw});body.position.set(robot.x,0,-robot.y);body.rotation.y=robot.yaw;
  }
  layoutKey(a){return JSON.stringify([a.obstacles,a.bases,a.sectors,a.objects,(a.targets||[]).map(t=>[t.id,t.x,t.y,t.normal,t.receiver])]);}
  update(state) {this.snapshot=state;if(this.arenaKey!==this.layoutKey(state.arena)){this.staticGroup.traverse(o=>{o.geometry?.dispose();const ms=Array.isArray(o.material)?o.material:o.material?[o.material]:[];ms.forEach(m=>{m.map?.dispose();m.dispose()});});this.staticGroup.clear();this.targetMeshes.clear();this.buildArena(state.arena);}for(const r of state.robots){if(!this.robotMeshes.has(r.slot))this.makeRobot(r);const obj=this.robotMeshes.get(r.slot);obj.desired.set(r.x,0,-r.y);obj.yaw=r.yaw;obj.pan.rotation.y=-THREE.MathUtils.degToRad((Number(r.pan)||0)*.5);obj.tilt.rotation.z=THREE.MathUtils.degToRad((Number(r.tilt)||0)*.15);const beamRange=Number.isFinite(r.beam_range)?Math.max(0,r.beam_range):0;obj.laser.scale.y=beamRange/1.6;obj.laser.position.x=.05+beamRange/2;obj.laser.visible=!!r.laser&&beamRange>0;obj.ring.visible=r.slot===this.selected;const label=this.labels.get(r.slot);label.classList.toggle('selected',r.slot===this.selected);label.classList.toggle('dead',r.hp<=0);}for(const target of state.arena.targets||[]){const m=this.targetMeshes.get(target.id);if(m){const beaconColor=target.destroyed?0xff4535:0x300604;m.beacon.material.color.setHex(beaconColor);m.beacon.material.emissive.setHex(beaconColor);m.beacon.material.emissiveIntensity=target.destroyed?1.5:.5;m.group.visible=true;}}}
  select(slot){this.selected=slot;if(this.snapshot)this.update(this.snapshot)}
  setMode(mode){this.mode=mode}
  toggleGrid(){this.gridVisible=!this.gridVisible;if(this.floor){this.floor.material.map=this.gridVisible?this.floorTexture:null;this.floor.material.color.set(this.gridVisible?0xffffff:0x303638);this.floor.material.needsUpdate=true}return this.gridVisible}
  toggleLabels(){this.labelsVisible=!this.labelsVisible;document.querySelector('#robot-labels').hidden=!this.labelsVisible;return this.labelsVisible}
  resize(){const w=this.container.clientWidth,h=this.container.clientHeight;if(!w||!h)return;this.renderer.setSize(w,h,false);this.camera.aspect=w/h;this.camera.updateProjectionMatrix()}
  bindControls(){const canvas=this.renderer.domElement;let drag=null;canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId)});canvas.addEventListener('pointermove',e=>{if(!drag||this.mode!=='orbit')return;this.angle-=(e.clientX-drag.x)*.006;this.elevation=Math.max(.3,Math.min(1.45,this.elevation+(e.clientY-drag.y)*.005));drag={x:e.clientX,y:e.clientY}});canvas.addEventListener('pointerup',()=>{drag=null});canvas.addEventListener('pointercancel',()=>{drag=null});canvas.addEventListener('wheel',e=>{e.preventDefault();this.distance=Math.max(3,Math.min(14,this.distance+e.deltaY*.007))},{passive:false})}
  dispose(){this.disposed=true;cancelAnimationFrame(this.animationId);this.resizeObserver.disconnect();this.scene.traverse(object=>{object.geometry?.dispose();const materials=Array.isArray(object.material)?object.material:object.material?[object.material]:[];materials.forEach(material=>{material.map?.dispose();material.dispose()})});this.renderer.dispose();this.renderer.domElement.remove();for(const label of this.labels.values())label.remove();}
  animate(){if(this.disposed)return;this.animationId=requestAnimationFrame(()=>this.animate());if(!this.container.clientWidth||!this.container.clientHeight)return;for(const m of this.robotMeshes.values()){m.body.position.lerp(m.desired,.34);const diff=Math.atan2(Math.sin(m.yaw-m.body.rotation.y),Math.cos(m.yaw-m.body.rotation.y));m.body.rotation.y+=diff*.34;}
    const follow=this.robotMeshes.get(this.selected);if(this.mode==='follow'&&follow){const p=follow.body.position,theta=follow.body.rotation.y;const desired=new THREE.Vector3(p.x-Math.cos(theta)*1.5,1.35,p.z+Math.sin(theta)*1.5);this.camera.position.lerp(desired,.09);this.camera.up.set(0,1,0);this.camera.lookAt(p.x,.15,p.z)}else if(this.mode==='top'){this.camera.position.set(this.center.x,Math.max(7,this.width/this.camera.aspect/0.73),this.center.z+.001);this.camera.up.set(0,0,-1);this.camera.lookAt(this.center)}else{const fit=Math.max(1,1.05/this.camera.aspect);const d=this.distance*fit;this.camera.position.set(this.center.x+Math.sin(this.angle)*Math.cos(this.elevation)*d,Math.sin(this.elevation)*d,this.center.z+Math.cos(this.angle)*Math.cos(this.elevation)*d);this.camera.up.set(0,1,0);this.camera.lookAt(this.center)}
    for(const [slot,obj]of this.robotMeshes){const v=obj.body.position.clone();v.y=.39;v.project(this.camera);const label=this.labels.get(slot);label.style.left=`${(v.x*.5+.5)*this.container.clientWidth}px`;label.style.top=`${(-v.y*.5+.5)*this.container.clientHeight}px`;label.style.display=v.z>1?'none':'';}this.renderer.render(this.scene,this.camera)}
}
