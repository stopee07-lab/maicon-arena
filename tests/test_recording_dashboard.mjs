import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../web/recording.js',import.meta.url),'utf8');
const {createRecording}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));

test('dashboard recording mounts locally and captures its own camera sources and provenance',async()=>{
 const originals=Object.fromEntries(['document','window','MediaRecorder','fetch','setInterval','clearInterval'].map(key=>[key,globalThis[key]]));
 const nodes=new Map(),drawn=[],saved=[];
 const node=()=>({hidden:false,handlers:{},addEventListener(type,fn){this.handlers[type]=fn;},getContext(){return {fillRect(){},fillText(){},drawImage(image){drawn.push(image);}};},captureStream(){return {getTracks(){return [{stop(){}}];}};}});
 const get=selector=>{if(!nodes.has(selector))nodes.set(selector,node());return nodes.get(selector);};
 globalThis.document={querySelector:get,createElement:node,addEventListener(){},hidden:false};
 globalThis.window={addEventListener(){}};
 globalThis.setInterval=()=>1;globalThis.clearInterval=()=>{};
 globalThis.MediaRecorder=class {
  static isTypeSupported(){return true;}
  constructor(stream,options){this.mimeType=options.mimeType;this.state='inactive';}
  start(){this.state='recording';}
  stop(){this.state='inactive';this.ondataavailable({data:new Blob(['fixture'])});void this.onstop();}
 };
 globalThis.fetch=async(url,options)=>{saved.push({url,options});return {ok:true,json:async()=>({id:'recording-fixture'})};};
 try{
  const ugv={width:640,height:480},drone={width:800,height:600},arena={width:1280,height:720};
  const state={runtime:'mission',page:'run',ownSlot:'B-1',key:'fixture',snapshot:{phase:'running'},physics:{connected:true,runtime:'mission'},scene:{renderer:{render(){},domElement:arena},scene:{},camera:{}},summary:{controller:{pid:12,status:'running',loaded:true,code_version_id:'code-7',code_sha256:'code-hash',model_id:'model-9'},deployment:{policy:'mission'}}};
  const mount={children:[],append(child){this.children.push(child);}};
  const errors=[];
  const recording=createRecording({state:()=>state,api:async()=>({recordings:[],directory:'/fixture/recordings'}),response:async()=>{},flash:(message,error)=>{if(error)errors.push(message);},ensureScene:async()=>state.scene,mount,sources:()=>({ugv,ugvAvailable:true,drone,droneAvailable:true})});
  assert.equal(mount.children.length,1);
  await get('#record-start').handlers.click();
  assert.equal(recording.active,true);
  assert.deepEqual(drawn,[arena,ugv,drone]);
  await recording.stop('user');
  assert.equal(recording.active,false);assert.deepEqual(errors,[]);assert.equal(saved[0].url,'/api/recordings');
  const metadata=JSON.parse(decodeURIComponent(saved[0].options.headers['X-Recording-Metadata']));
  assert.equal(metadata.runtime,'mission');assert.equal(metadata.camera_slot,'B-1');assert.equal(metadata.code_version_id,'code-7');assert.equal(metadata.model_id,'model-9');assert.equal(metadata.stop_reason,'user');
 }finally{for(const [key,value] of Object.entries(originals)){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}}
});
