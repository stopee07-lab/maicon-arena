(function(root){
 'use strict';
 async function openArena(api,{cancelled=()=>false,sleep=ms=>new Promise(r=>setTimeout(r,ms)),attempts=60}={}){
  const check=()=>{if(cancelled())throw new Error('실행을 중지했습니다.');};
  check();const initial=await api('/api/physics/status');check();
  if(initial.connected)return initial;
  await api('/api/physics/control',{action:'start',runtime:'practice'});check();
  for(let i=0;i<attempts;i++){
   const state=await api('/api/physics/status');check();
   if(state.connected)return state;
   if(state.error)throw new Error(state.error);
   await sleep(300);check();
  }
  throw new Error('경기장에 연결하지 못했습니다. 전체 정지를 누른 뒤 사용법의 문제 해결을 확인하세요.');
 }
 function verifyRun(result){
  if(!result.started||!result.controller?.loaded||!result.controller?.code_integrity||!result.physics?.connected)
   throw new Error('로봇 코드와 경기장 연결을 확인하지 못했습니다. 통신 확인 또는 실행 화면에서 오류를 확인하세요.');
  return true;
 }
 if(typeof module!=='undefined')module.exports={openArena,verifyRun};
 if(typeof document==='undefined')return;
 const $=s=>document.querySelector(s);let key='',busy=false,stopping=false,generation=0,ready=false;
 async function api(path,body){
  const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),60000);
  try{
   const response=await fetch(path,{method:body?'POST':'GET',headers:{'X-Admin-Key':key,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal:controller.signal});
   const value=await response.json();if(!response.ok)throw new Error(typeof value.detail==='string'?value.detail:JSON.stringify(value.detail||value));return value;
  }finally{clearTimeout(timer)}
 }
 function notice(message,error=false){$('#action-status').textContent=message;$('#action-status').classList.toggle('error',error)}
 function controls(){
  $('#open-arena').disabled=!ready||busy;
  $('#run-code').disabled=!ready||busy||!$('#model').value;
  $('#stop').disabled=!ready||stopping;
 }
 async function refresh(){
  try{
   if(!key){const config=await api('/api/config');key=config.admin_key;if(!key)throw new Error('로컬 접속 정보를 가져오지 못했습니다.');}
   const state=await api('/api/workbench/status');ready=true;
   $('#connection').textContent='프로그램 연결됨';$('#connection').className='connection online';
   const p=state.physics||{},c=state.controller||{};
   $('#arena-state').textContent=p.connected?'경기장 켜짐':p.running?'경기장 연결 중':'경기장 꺼짐';
   $('#robot-state').textContent=c.loaded&&c.code_integrity&&c.status==='running'?'로봇 코드 실행 중':'로봇 코드 대기';
   const chosen=$('#model').value;$('#model').replaceChildren();
   const models=state.models||[];
   if(!models.length)$('#model').add(new Option('설치 후 다시 실행하세요',''));
   for(const model of models)$('#model').add(new Option(model.name||model.id,model.id));
   if(models.some(m=>m.id===chosen))$('#model').value=chosen;
   $('#model-note').textContent=models.length?'제공 모델로 시작할 수 있습니다. 새 모델 학습은 선택 사항입니다.':'제공 모델이 없습니다. 프로그램을 끄고 설치 파일을 한 번 더 실행하세요.';
  }catch(error){ready=false;key='';$('#connection').textContent='연결 확인 필요';$('#connection').className='connection offline';notice('프로그램 연결 실패: '+error.message+' · 실행 창을 켜 둔 상태인지 확인하세요.',true);}
  controls();
 }
 async function act(action){
  busy=true;const ticket=++generation;controls();notice('준비 중입니다. 첫 실행은 모델을 읽느라 잠시 걸릴 수 있습니다.');
  try{await action(()=>ticket!==generation);if(ticket!==generation)return;location.href='/?page=run';}
  catch(error){if(ticket===generation)notice(error.name==='AbortError'?'응답을 기다리는 시간이 지났습니다. 전체 정지를 누르고 다시 시도하세요.':error.message,true);}
  finally{if(ticket===generation){busy=false;await refresh();}}
 }
 $('#open-arena').addEventListener('click',()=>act(cancelled=>openArena(api,{cancelled})));
 $('#run-code').addEventListener('click',()=>act(async cancelled=>{
  const result=await api('/api/development/apply-run',{mode:'practice',policy:$('#policy').value,model_id:$('#model').value,name:'시작 화면에서 연습'});
  if(cancelled())throw new Error('실행을 중지했습니다.');verifyRun(result);
 }));
 $('#stop').addEventListener('click',async()=>{
  if(stopping)return;stopping=true;generation++;busy=true;controls();notice('경기장과 로봇을 멈추는 중입니다.');
  try{const result=await api('/api/development/stop',{});if(!result.stopped)throw new Error('정지 확인을 받지 못했습니다.');notice('정지했습니다. 다시 실행하려면 경기장 열기 또는 로봇 시험하기를 누르세요.');}
  catch(error){notice('정지 확인 실패: '+error.message,true);}finally{stopping=false;busy=false;await refresh();}
 });
 $('#refresh').addEventListener('click',refresh);$('#model').addEventListener('change',controls);
 refresh();setInterval(()=>{if(!busy)refresh()},5000);
})(typeof window!=='undefined'?window:globalThis);
