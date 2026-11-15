const {test}=require('node:test');
const assert=require('node:assert/strict');
const {openArena,verifyRun}=require('../web/welcome.js');
test('existing connected arena is reused without resetting it',async()=>{
 const calls=[];const api=async p=>{calls.push(p);return {connected:true,running:true}};
 await openArena(api);assert.deepEqual(calls,['/api/physics/status']);
});
test('starting is not success: wait for real connection',async()=>{
 let reads=0;const calls=[];const api=async p=>{calls.push(p);return p.endsWith('status')?{connected:++reads===3,running:reads>1}:{running:true}};
 await openArena(api,{sleep:async()=>{}});assert.equal(reads,3);assert.equal(calls.filter(p=>p.endsWith('control')).length,1);
});
test('cancellation cannot open the arena after stop',async()=>{
 let cancelled=false;const api=async p=>{if(p.endsWith('control'))cancelled=true;return {running:true}};
 await assert.rejects(openArena(api,{cancelled:()=>cancelled,sleep:async()=>{}}),/중지/);
});
test('worker failure and timeout remain failures',async()=>{
 await assert.rejects(openArena(async()=>({error:'렌더러 실패'})),/렌더러 실패/);
 await assert.rejects(openArena(async()=>({running:true}),{attempts:1,sleep:async()=>{}}),/연결하지 못/);
});
test('code run requires loaded verified code and connected physics',()=>{
 assert.throws(()=>verifyRun({started:true,controller:{loaded:false},physics:{connected:true}}));
 assert.throws(()=>verifyRun({started:true,controller:{loaded:true,code_integrity:true},physics:{connected:false}}));
 assert.equal(verifyRun({started:true,controller:{loaded:true,code_integrity:true},physics:{connected:true}}),true);
});
