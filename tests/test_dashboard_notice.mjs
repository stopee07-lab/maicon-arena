import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
const app=await readFile(new URL('../web/app.js',import.meta.url),'utf8');
const adapter=app.match(/^const runtimeState=(.*);$/m)?.[1];
assert.ok(adapter,'dashboard runtime adapter must be present');
const noticeSource=await readFile(new URL('../web/run-notice.js',import.meta.url),'utf8');
const {describeRunState}=await import('data:text/javascript;base64,'+Buffer.from(noticeSource).toString('base64'));
const live={workspace:'mission',own:'B-1',connected:true,pending:false,physics:{connected:true,runtime:'mission'},snapshot:{phase:'running',paused:false,robots:[]},summary:{controller:{pid:42,status:'running',loaded:true,code_integrity:true,logs:[]},deployment:{mode:'match',policy:'mission'}}};
test('dashboard maps live controller and deployment into the notice without a false stopped label',()=>{
 const state=runInNewContext('('+adapter+')',{state:live})();
 assert.equal(describeRunState(state).kind,'running');
 assert.equal(describeRunState(state).title,'참가자 코드 실행 중');
});
test('dashboard notice preserves observe-only policy and paused state',()=>{
 const observe={...live,summary:{...live.summary,deployment:{mode:'match',policy:'observe'}}};
 assert.match(describeRunState(runInNewContext('('+adapter+')',{state:observe})()).title,/인식만/);
 const paused={...live,snapshot:{...live.snapshot,paused:true}};
 assert.equal(describeRunState(runInNewContext('('+adapter+')',{state:paused})()).kind,'paused');
});
