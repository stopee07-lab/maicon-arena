import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../web/run-controls.js',import.meta.url),'utf8');
const {matchControlState,matchActionRequest,operatorDestination}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const ready={runtime:'mission',connected:true,physics:{connected:true,runtime:'mission'},summary:{controller:{pid:12,status:'running',loaded:true,code_integrity:true},deployment:{mode:'match',model_id:'weights-1',code_version_id:'code-1',policy:'mission',slot:'B-1'}},snapshot:{phase:'running',paused:true}};
test('workbench page 5 redirects to canonical dashboard while preserving runtime',()=>{
 assert.equal(operatorDestination('?page=run&runtime=rehearsal'),'/index.html?mode=rehearsal');
 assert.equal(operatorDestination('?page=run'),'/index.html?mode=mission');
 assert.equal(operatorDestination('?page=develop'),null);
 assert.equal(operatorDestination('?role=viewer&mode=practice'),null);
});
test('stopped runtime exposes an explicit launch using the prepared code and model',()=>{
 const s={...ready,physics:{connected:false},summary:{...ready.summary,controller:{status:'stopped'}},snapshot:{phase:'idle'}};
 const controls=matchControlState(s);
 assert.equal(controls.actions.start.visible,true);assert.equal(controls.actions.start.disabled,false);
 const request=matchActionRequest('start',s);
 assert.equal(request.path,'/api/development/apply-run');
 assert.deepEqual(request.body,{mode:'match',model_id:'weights-1',version_id:'code-1',policy:'mission',slot:'B-1'});
});
test('healthy paused runtime resumes through its own control endpoint without resetting',()=>{
 assert.deepEqual(matchActionRequest('resume',ready),{path:'/api/admin/control',body:{action:'resume'},launch:false});
 const s={...ready,runtime:'rehearsal',physics:{connected:true,runtime:'rehearsal'},summary:{...ready.summary,deployment:{...ready.summary.deployment,mode:'rehearsal'}}};
 assert.equal(matchActionRequest('resume',s).path,'/api/admin/rehearsal/control');
});
test('missing controller requires a visibly labelled restart rather than raw resume',()=>{
 const s={...ready,summary:{...ready.summary,controller:{status:'stopped'}}};
 const controls=matchControlState(s);
 assert.equal(controls.actions.resume.visible,false);
 assert.equal(controls.actions.start.visible,true);
 assert.match(controls.actions.start.label,/새 경기/);
 assert.equal(matchActionRequest('start',s).path,'/api/development/apply-run');
});
test('controller for another runtime is not considered ready',()=>{
 const s={...ready,runtime:'practice'};
 assert.equal(matchControlState(s).ready,false);
 assert.equal(matchActionRequest('start',s).body.mode,'practice');
});
test('launch without a saved model/code is blocked rather than silently snapshotting edits',()=>{
 const s={...ready,physics:{connected:false},summary:{controller:{status:'stopped'},deployment:null}};
 assert.equal(matchControlState(s).actions.start.disabled,true);
 assert.throws(()=>matchActionRequest('start',s),/실행 설정/);
});
test('loading, offline, busy, and ROS-owned states do not expose enabled start/resume',()=>{
 for(const s of [{...ready,connected:false},{...ready,busy:new Set(['match'])},{...ready,rosNotebook:{running:true}},{...ready,summary:{...ready.summary,controller:{pid:12,status:'loading',loaded:false}}}]){
  const actions=matchControlState(s).actions;
  assert.equal(actions.start.disabled,true);assert.equal(actions.resume.disabled,true);
 }
});
test('prepare, reset and finish always target the selected runtime',()=>{
 const s={...ready,runtime:'practice'};
 for(const action of ['prepare','reset','finish'])assert.equal(matchActionRequest(action,s).path,'/api/admin/practice/control');
});

test('code integrity and measured physical process liveness are required',()=>{
 for(const s of [{...ready,physics:{...ready.physics,running:false}},{...ready,summary:{...ready.summary,controller:{...ready.summary.controller,code_integrity:false}}}])assert.equal(matchControlState(s).ready,false);
});
test('other external robots awaiting ready cannot raw start or resume',()=>{
 const s={...ready,snapshot:{phase:'ready',robots:[{slot:'B-2',strategy:'external',ready:false}]}};
 const controls=matchControlState(s);
 assert.equal(controls.ready,true);assert.equal(controls.externalReady,false);assert.equal(controls.actions.start.disabled,true);assert.equal(controls.actions.resume.disabled,true);
});
