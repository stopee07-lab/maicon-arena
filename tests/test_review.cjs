const {test} = require('node:test');
const assert = require('node:assert/strict');
const review = require('../web/review.js');
function fixture() {
  const robot = {slot:'B-1', x:1, y:2, yaw:0, hp:3, ammo:5,included:true,strategy:'external',faults:{camera:false,lidar:false,network:false},
    score:{m1:4,m2:0,m3:7,m4:12,m5:0,m6:0,bonus:0,total:23}};
  return {simulation_only:true,schema_version:2,
    identity:{rule_profile:'r1',environment_profile:'mild',sensor_profile:'mild',layout_version:'v3',seed:106,opponent_mode:'sensor_limited',opponent_policy_version:'v3'},
    rule_configuration:{id:'r1',duration:300},sensor_configuration:{name:'mild'},
    state:{phase:'finished',elapsed:300,difficulty:0,speed:1,robots:[robot],arena:{width:6.12,height:5,obstacles:[],objects:[],targets:[],bases:[]}},
    history:[{time:1,robots:[{...robot,hp:4,x:0.5}]},{time:2,robots:[robot]}],
    events:[{time:2,type:'hit',message:'피격',slot:'B-1',token:'do-not-export'}],
    answers:{secret:'answer'},reports:{secret:'report'},
  };
}
test('loads the real export shape and strips unrelated answers and credentials',()=>{
  const record=review.parseRecord(JSON.stringify(fixture()));
  assert.equal(record.robots[0].score.total,23);
  assert.equal(record.duration,300);
  assert.equal(record.history[0].robots[0].x,0.5);
  assert.doesNotMatch(JSON.stringify(record),/do-not-export|answer|report/);
});
test('rejects unsupported, corrupt, nonfinite and out-of-order records',()=>{
  assert.throws(()=>review.parseRecord('{'),/JSON/);
  const x=fixture();x.schema_version=1;assert.throws(()=>review.normalizeRecord(x),/형식/);
  x.schema_version=2;x.history[0].robots[0].x=NaN;assert.throws(()=>review.normalizeRecord(x),/좌표/);
  x.history[0].robots[0].x=1;x.history.reverse();assert.throws(()=>review.normalizeRecord(x),/시간 순서/);
});
test('shows the last saved position at the chosen time without inventing motion',()=>{
  const x=review.normalizeRecord(fixture());
  assert.equal(review.frameAt(x,0),null);
  assert.equal(review.frameAt(x,1.8).time,1);
  assert.equal(review.frameAt(x,301).time,2);
  const empty=fixture();empty.history=[];
  assert.equal(review.frameAt(review.normalizeRecord(empty),100),null);
});
test('same complete conditions allow deltas but incomplete and unequal conditions do not',()=>{
  const a=review.normalizeRecord(fixture());const b=fixture();b.state.robots[0].score.total=30;
  assert.equal(review.compareRecords(a,review.normalizeRecord(b),'B-1').delta.total,7);
  b.state.phase='report';assert.equal(review.compareRecords(a,review.normalizeRecord(b),'B-1').comparable,false);
  b.state.phase='finished';b.identity.seed=107;
  const compared=review.compareRecords(a,review.normalizeRecord(b),'B-1');
  assert.equal(compared.conditionStatus,'different');assert.equal(compared.delta,null);
  assert.ok(compared.conditions.some(c=>c.label==='조건 번호'&&c.status==='different'));
});
test('missing conditions and missing scores never masquerade as equal completed records',()=>{
  const a=review.normalizeRecord(fixture());const b=fixture();delete b.sensor_configuration;
  assert.equal(review.compareRecords(a,review.normalizeRecord(b),'B-1').conditionStatus,'unknown');
  const c=fixture();delete c.state.robots[0].score.total;
  assert.equal(review.compareRecords(a,review.normalizeRecord(c),'B-1').comparable,false);
});
test('jumps include original events and observed HP loss with honest sample timing',()=>{
  const jumps=review.buildJumps(review.normalizeRecord(fixture()),'B-1');
  assert.equal(jumps.length,2);
  const hp=jumps.find(j=>j.type==='hp_drop');assert.equal(hp.time,2);assert.match(hp.message,/1.*2/);
});
test('layout and detailed sensor changes invalidate comparison despite the same condition number',()=>{
  const a=review.normalizeRecord(fixture());const b=fixture();b.sensor_configuration.noise=.1;
  assert.equal(review.compareRecords(a,review.normalizeRecord(b),'B-1').conditionStatus,'different');
  const c=fixture();c.state.arena.obstacles.push({x:2,y:2,w:.4,h:.4});
  assert.equal(review.compareRecords(a,review.normalizeRecord(c),'B-1').conditionStatus,'different');
});
test('comparison export carries exact byte hashes and provenance without raw payloads',async()=>{
  const bytes=new TextEncoder().encode('abc');
  assert.equal(await review.sha256(bytes,require('node:crypto').webcrypto),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  const a=review.normalizeRecord(fixture());
  const output=review.comparisonExport({record:a,name:'A.json',sha256:'a'.repeat(64)},{record:a,name:'B.json',sha256:'b'.repeat(64)},'B-1');
  assert.equal(output.schema,'maicon-review-comparison/v1');
  assert.equal(output.records.A.input_sha256,'a'.repeat(64));
  assert.equal(output.records.A.provenance.code_sha256,null);
  assert.doesNotMatch(JSON.stringify(output),/do-not-export|answer|report|robots.*x/);
  assert.match(output.limitations.join(' '),/실제 로봇/);
});
test('condition text is never silently shortened into a false equality',()=>{
  const record=fixture();record.sensor_configuration.note='a'.repeat(2001);
  assert.throws(()=>review.normalizeRecord(record),/설정/);
});
test('the bundled historical example includes real positions and attributed source hashes',()=>{
  const example=require('../web/review-example.js');const record=review.normalizeRecord(example);
  assert.equal(record.history.length,151);assert.equal(record.events.length,48);
  assert.deepEqual(record.history[0].robots[0],{slot:'B-1',x:.28,y:4.15,yaw:-0,hp:4,ammo:8});
  assert.equal(record.robots[0].score.total,35);
  assert.match(record.provenance.source_sha256,/^[a-f0-9]{64}$/);
  assert.match(record.provenance.note,/600.*151/);
  assert.equal(review.compareRecords(record,record,'B-1').conditionStatus,'same');
});
test('other robot roles, inclusion, all faults and execution speed are comparison conditions',()=>{
  const raw=fixture();raw.state.robots.push({...structuredClone(raw.state.robots[0]),slot:'R-1',strategy:'hunter'});
  const a=review.normalizeRecord(raw);
  for(const modify of [b=>b.state.robots[1].strategy='external',b=>b.state.robots[1].included=false,b=>b.state.robots[1].faults.network=true,b=>b.state.robots[0].faults.camera=true,b=>b.state.speed=5]){
    const b=structuredClone(raw);modify(b);assert.equal(review.compareRecords(a,review.normalizeRecord(b),'B-1').conditionStatus,'different');
  }
  const b=structuredClone(raw);b.state.robots[0].strategy='balanced';
  assert.equal(review.compareRecords(a,review.normalizeRecord(b),'B-1').conditionStatus,'same','participant policy change is the subject of the comparison');
});
test('mid-match condition changes cannot be hidden by restoring the final settings',()=>{
  const raw=fixture();raw.events=[{time:0,type:'phase',message:'START · 경기 시작'},{time:5,type:'fault',message:'network: True',slot:'B-1'},{time:10,type:'fault',message:'network: False',slot:'B-1'}];
  const a=review.normalizeRecord(raw),base=fixture();
  assert.equal(review.compareRecords(a,review.normalizeRecord(base),'B-1').conditionStatus,'different');
  assert.equal(review.compareRecords(a,a,'B-1').conditionStatus,'unknown');
  const policy=fixture();policy.events=[{time:0,type:'phase',message:'START · 경기 시작'},{time:5,type:'strategy',message:'전략: external',slot:'B-1'}];
  assert.equal(review.compareRecords(review.normalizeRecord(policy),review.normalizeRecord(base),'B-1').conditionStatus,'same');
});
test('missing fault or role metadata is unknown rather than assumed healthy',()=>{
  const a=fixture();delete a.state.robots[0].faults;
  assert.equal(review.compareRecords(review.normalizeRecord(a),review.normalizeRecord(a),'B-1').conditionStatus,'unknown');
});
test('hit events identify the firing slot, but the victim can still jump to the event',()=>{
  const x=fixture();x.events=[{time:2,type:'hit',slot:'R-1',message:'B-1 피격 · HP 3/4 · 10초 무적/조준·발사 잠금'}];
  const jumps=review.buildJumps(review.normalizeRecord(x),'B-1');
  assert.ok(jumps.some(j=>j.type==='hit'&&j.time===2&&j.attention));
});
