(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else {
    root.MaiconReview = api;
    if (root.document) api.mount(root.document, root);
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const MAX_BYTES = 128 * 1024 * 1024;
  const SCORE_KEYS = ['m1','m2','m3','m4','m5','m6','bonus','total'];
  const CONDITIONS = [
    ['seed','조건 번호'], ['rule_profile','채점 규칙'], ['rule_configuration','채점 세부 설정'],
    ['environment_profile','환경'], ['sensor_profile','센서 설정 이름'], ['sensor_configuration','센서 세부 설정'],
    ['layout_version','배치 규칙'], ['layout','실제 경기장 배치'], ['opponent_mode','상대 방식'],
    ['opponent_policy_version','상대 코드 버전'], ['difficulty','난이도'], ['speed','실행 배속'],
    ['robot_configuration','참가 로봇·역할·고장'], ['runtime_changes','경기 도중 조건 변경'],
  ];
  const LIMITATIONS = [
    '저장된 위치와 사건을 표시합니다. 경기를 다시 실행하거나 없는 움직임을 복원하지 않습니다.',
    '조건 번호가 같아도 물리 계산과 처리 시점 때문에 결과가 완전히 같지는 않습니다.',
    '한 번의 점수 차이로 개선을 확정할 수 없습니다. 반복 경기의 최저점·중앙값·성공률도 확인하세요.',
    '로컬 시뮬레이터의 잠정 점수입니다. 실제 로봇·공식 경기 성능을 입증하지 않습니다.',
    '입력 파일의 SHA-256은 같은 파일인지 확인하는 값입니다. 출처의 진위나 내용의 정확성을 보증하지 않습니다.',
    '파일에 없는 물리 엔진 설정이나 기록되지 않은 배속 변경 이력은 확인할 수 없습니다.',
  ];
  function obj(value) { return value && typeof value === 'object' && !Array.isArray(value); }
  function finite(value) { return typeof value === 'number' && Number.isFinite(value); }
  function label(value, max = 1000) { return typeof value === 'string' ? value.slice(0, max) : ''; }
  function hash(value) { return typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value) ? value.toLowerCase() : null; }
  function stable(value) {
    if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
    if (obj(value)) return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + stable(value[k])).join(',') + '}';
    return JSON.stringify(value);
  }
  function boundedJSON(value, depth = 0) {
    if (depth > 12) throw new Error('설정 정보가 너무 복잡합니다. 원본 경기 JSON을 선택하세요.');
    if (value === null || typeof value === 'boolean' || finite(value)) return value;
    if (typeof value === 'string' && value.length <= 2000) return value;
    if (Array.isArray(value) && value.length <= 2000) return value.map(v => boundedJSON(v, depth + 1));
    if (obj(value) && Object.keys(value).length <= 300) return Object.fromEntries(Object.keys(value).sort().filter(k => !['__proto__','constructor','prototype'].includes(k)).map(k => [k,boundedJSON(value[k],depth + 1)]));
    throw new Error('설정 정보에 읽을 수 없는 값이 있습니다.');
  }
  function position(robot) {
    if (!obj(robot) || !/^[BR]-[123]$/.test(robot.slot || '') || !finite(robot.x) || !finite(robot.y) || Math.abs(robot.x) > 1000 || Math.abs(robot.y) > 1000) throw new Error('로봇 번호 또는 좌표가 올바르지 않습니다.');
    return {slot:robot.slot,x:robot.x,y:robot.y,yaw:finite(robot.yaw)?robot.yaw:0,hp:finite(robot.hp)?robot.hp:null,ammo:finite(robot.ammo)?robot.ammo:null};
  }
  function positions(robots) {
    if (!Array.isArray(robots) || robots.length > 6) throw new Error('로봇 목록 형식이 올바르지 않습니다.');
    const result = robots.map(position);
    if (new Set(result.map(r => r.slot)).size !== result.length) throw new Error('로봇 번호가 중복됩니다.');
    return result;
  }
  function layout(arena) {
    if (!obj(arena) || !finite(arena.width) || !finite(arena.height) || arena.width <= 0 || arena.height <= 0 || arena.width > 100 || arena.height > 100) return null;
    const out = {width:arena.width,height:arena.height};
    for (const kind of ['obstacles','objects','targets','bases']) {
      if (!Array.isArray(arena[kind]) || arena[kind].length > 2000) { out[kind] = null; continue; }
      out[kind] = arena[kind].map(item => {
        if (!obj(item) || !finite(item.x) || !finite(item.y) || Math.abs(item.x) > 1000 || Math.abs(item.y) > 1000) throw new Error('경기장 좌표가 올바르지 않습니다.');
        const value = {};
        for (const k of ['id','type','sector','camp','kind','decor_type']) if (typeof item[k] === 'string') value[k] = label(item[k],100);
        for (const k of ['x','y','w','h','height']) if (finite(item[k])) value[k] = item[k];
        for (const k of ['normal','receiver','sensor_line']) if (item[k] !== undefined) value[k] = boundedJSON(item[k]);
        return value;
      }).sort((a,b) => stable(a).localeCompare(stable(b)));
    }
    return out;
  }
  function normalizeRecord(raw) {
    if (!obj(raw) || raw.schema_version !== 2 || raw.simulation_only !== true || !obj(raw.state) || !Array.isArray(raw.state.robots) || !raw.state.robots.length) throw new Error('지원하지 않는 기록 형식입니다. 경기장의 “기록 저장”으로 받은 JSON(형식 2)을 선택하세요.');
    const state = raw.state;
    if (!finite(state.elapsed) || state.elapsed < 0 || state.elapsed > 1e7 || typeof state.phase !== 'string') throw new Error('경기 시간 또는 진행 상태가 없습니다.');
    const robots = positions(state.robots).map((r,i) => ({...r,included:typeof state.robots[i].included === 'boolean' ? state.robots[i].included : null,score:Object.fromEntries(SCORE_KEYS.map(k => [k,finite(state.robots[i].score?.[k]) ? state.robots[i].score[k] : null]))}));
    if (!Array.isArray(raw.history) || raw.history.length > 20000 || !Array.isArray(raw.events) || raw.events.length > 50000) throw new Error('이동·사건 목록이 없거나 너무 큽니다. 원본 경기 JSON을 확인하세요.');
    let previous = -1;
    const history = raw.history.map(frame => {
      if (!obj(frame) || !finite(frame.time) || frame.time < 0 || frame.time > state.elapsed + 0.1 || frame.time < previous) throw new Error('이동 기록의 시간 순서가 올바르지 않습니다.');
      previous = frame.time;
      return {time:frame.time,robots:positions(frame.robots)};
    });
    const events = raw.events.map((event,i) => {
      if (!obj(event) || !finite(event.time) || event.time < 0 || event.time > state.elapsed + 0.1) throw new Error('사건 시간이 올바르지 않습니다.');
      return {id:'event-' + i,time:event.time,type:label(event.type,80),message:label(event.message),slot:/^[BR]-[123]$/.test(event.slot || '')?event.slot:null};
    }).sort((a,b) => a.time - b.time);
    const identity = obj(raw.identity) ? raw.identity : (obj(state.identity) ? state.identity : {});
    const conditions = {};
    for (const [key] of CONDITIONS) {
      if (key === 'layout') conditions[key] = layout(state.arena);
      else if (key === 'difficulty' || key === 'speed') conditions[key] = finite(state[key]) ? state[key] : null;
      else if (key === 'robot_configuration') conditions[key] = state.robots.map(r => ({slot:r.slot,included:typeof r.included==='boolean'?r.included:null,strategy:typeof r.strategy==='string'?boundedJSON(r.strategy):null,faults:Object.fromEntries(['camera','lidar','network'].map(k=>[k,typeof r.faults?.[k]==='boolean'?r.faults[k]:null]))})).sort((a,b)=>a.slot.localeCompare(b.slot));
      else if (key === 'runtime_changes') {
        let started=false;
        conditions[key] = events.filter(event => {
          if(event.type==='phase' && /\bSTART\b/.test(event.message))started=true;
          return (started || event.time>0) && ['fault','strategy','difficulty','environment','sensor','excluded'].includes(event.type);
        }).map(({time,type,message,slot})=>({time,type,message,slot}));
      }
      else if (key === 'rule_configuration' || key === 'sensor_configuration') conditions[key] = obj(raw[key]) ? boundedJSON(raw[key]) : null;
      else conditions[key] = typeof identity[key] === 'string' || finite(identity[key]) ? identity[key] : null;
    }
    const provenance = obj(raw._review_provenance) ? raw._review_provenance : {};
    return {schemaVersion:2,phase:state.phase,duration:state.elapsed,robots,history,events,conditions,
      provenance:{trial_id:label(provenance.trial_id,200),source:label(provenance.source,700),source_sha256:hash(provenance.source_sha256),manifest_sha256:hash(provenance.manifest_sha256),code_sha256:hash(provenance.code_sha256),model_sha256:hash(provenance.model_sha256),note:label(provenance.note,2000)}};
  }
  function parseRecord(text) {
    if (typeof text !== 'string' || text.length > MAX_BYTES) throw new Error('파일이 너무 큽니다. 128 MB 이하의 경기 기록을 선택하세요.');
    let raw;
    try { raw = JSON.parse(text.replace(/^\uFEFF/,'')); } catch (_) { throw new Error('JSON 파일을 읽을 수 없습니다. 경기장에서 저장한 .json 파일을 선택하세요.'); }
    return normalizeRecord(raw);
  }
  function selectedRobot(record,slot) { return record.robots.find(r => r.slot === slot) || null; }
  function complete(record,slot) { const r=selectedRobot(record,slot);return record.phase === 'finished' && !!r && r.included === true && SCORE_KEYS.every(k => finite(r.score[k])); }
  function conditionValue(record,key,slot) {
    const value=record.conditions[key];
    if(key==='robot_configuration')return value.map(r=>r.slot===slot?{slot:r.slot,included:r.included,faults:r.faults}:r);
    if(key==='runtime_changes')return value.filter(e=>!(e.type==='strategy'&&e.slot===slot));
    return value;
  }
  function missingValue(value) { return value===null || (Array.isArray(value) ? value.some(missingValue) : obj(value) ? Object.values(value).some(missingValue) : false); }
  function compareRecords(a,b,slot) {
    const conditions = CONDITIONS.map(([key,label]) => {
      const av = conditionValue(a,key,slot), bv = conditionValue(b,key,slot);
      const missing = av === null || bv === null || (['layout','robot_configuration'].includes(key) && [av,bv].some(missingValue));
      const equal=stable(av)===stable(bv);
      return {key,label,status:missing?'unknown':!equal?'different':key==='runtime_changes'&&av.length?'unknown':'same'};
    });
    const conditionStatus = conditions.some(c => c.status === 'different') ? 'different' : conditions.some(c => c.status === 'unknown') ? 'unknown' : 'same';
    const completeA = complete(a,slot), completeB = complete(b,slot), comparable = completeA && completeB && conditionStatus === 'same';
    const ar=selectedRobot(a,slot), br=selectedRobot(b,slot);
    return {conditionStatus,conditions,completeA,completeB,comparable,
      delta:comparable ? Object.fromEntries(SCORE_KEYS.map(k => [k,br.score[k]-ar.score[k]])) : null,
      provenanceComplete:[a,b].every(r => r.provenance.code_sha256 && r.provenance.model_sha256)};
  }
  function frameAt(record,time) {
    let lo=0,hi=record.history.length-1,result=null;
    while (lo<=hi) { const mid=(lo+hi)>>1;const frame=record.history[mid];if(frame.time<=time){result=frame;lo=mid+1;}else hi=mid-1; }
    return result;
  }
  function buildJumps(record,slot) {
    // Engine hit events carry the firing robot in slot; the message names the victim.
    const result = record.events.filter(e => !e.slot || e.slot === slot || (e.type === 'hit' && e.message.startsWith(slot + ' 피격'))).map(e => ({...e,attention:/hit|fail|error|disconnect|damage|abort|timeout|피격|실패|끊김|중단/i.test(e.type + ' ' + e.message)}));
    let prev=null;
    for (const frame of record.history) {
      const robot=frame.robots.find(r => r.slot === slot);
      if (robot && finite(robot.hp)) {
        if (prev && robot.hp < prev.hp) result.push({id:'hp-'+frame.time,time:frame.time,type:'hp_drop',slot,attention:true,message:`체력 ${prev.hp} → ${robot.hp} · ${prev.time}~${frame.time}초 사이 감소 (저장 간격 기준)`});
        prev={hp:robot.hp,time:frame.time};
      }
    }
    return result.sort((a,b) => a.time-b.time);
  }
  async function sha256(bytes,cryptoProvider) {
    if (!cryptoProvider?.subtle) return null;
    const digest=await cryptoProvider.subtle.digest('SHA-256',bytes);
    return Array.from(new Uint8Array(digest),b=>b.toString(16).padStart(2,'0')).join('');
  }
  function summary(input,slot) {
    const record=input.record,r=selectedRobot(record,slot);
    return {file:label(input.name,200),input_sha256:hash(input.sha256),phase:record.phase,finished:complete(record,slot),elapsed:record.duration,
      selected_robot:slot,score:r?.score||null,final_hp:r?.hp??null,history_frames:record.history.length,event_count:record.events.length,provenance:record.provenance};
  }
  function comparisonExport(a,b,slot) {
    return {schema:'maicon-review-comparison/v1',created_at:new Date().toISOString(),simulation_only:true,selected_robot:slot,
      comparison:compareRecords(a.record,b.record,slot),records:{A:summary(a,slot),B:summary(b,slot)},limitations:LIMITATIONS};
  }

  function mount(doc,win) {
    const $=id=>doc.getElementById(id);
    if (!$('review-app')) return;
    const loaded={A:null,B:null},versions={A:0,B:0};let playing=false,lastTick=0,selectedEvent=null;
    const fmt=n=>finite(n)?Number(n.toFixed(2)).toLocaleString('ko-KR'):'—';
    const clock=n=>`${Math.floor(n/60)}:${String(Math.floor(n%60)).padStart(2,'0')}`;
    function element(tag,text,cls) {const e=doc.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
    function stop(){playing=false;$('play').textContent='▶ 기록 재생';}
    function setTime(t){$('time').value=t;draw();}
    function robotSlot(){return $('robot').value || 'B-1';}
    async function loadBytes(key,bytes,name) {
      const version=++versions[key];stop();$('status-'+key).textContent='기록 읽는 중…';
      try {
        if (bytes.byteLength > MAX_BYTES) throw new Error('128 MB 이하의 파일을 선택하세요.');
        const record=parseRecord(new TextDecoder().decode(bytes));
        const digest=await sha256(bytes,win.crypto);
        if(version!==versions[key])return;
        const firstLoad = !loaded.A && !loaded.B;
        loaded[key]={record,name,sha256:digest};selectedEvent=null;
        $('name-'+key).textContent=name;$('status-'+key).textContent=record.phase==='finished'?'종료된 경기':'진행 중 또는 중단된 기록';
        $('status-'+key).className='record-status '+(record.phase==='finished'?'good':'warn');
        $('clear-'+key).disabled=false;
        const slots=[...new Set(Object.values(loaded).filter(Boolean).flatMap(v=>v.record.robots.map(r=>r.slot)))].sort();
        const old=robotSlot();$('robot').replaceChildren(...slots.map(s=>{const option=element('option',s==='B-1'?'B-1 · 내 로봇':s);option.value=s;return option;}));
        if(slots.includes(old))$('robot').value=old;
        render();
        if (firstLoad && record.history.length) setTime(record.history[0].time);
      } catch(error) {
        if(version!==versions[key])return;
        $('status-'+key).textContent=(error.message||'읽기 실패')+(loaded[key]?' 이전 기록을 유지합니다.':'');$('status-'+key).className='record-status bad';
      }
    }
    for(const key of ['A','B']) {
      $('file-'+key).addEventListener('change',async event=>{const file=event.target.files[0];if(file){if(file.size>MAX_BYTES){$('status-'+key).textContent='128 MB 이하의 파일을 선택하세요.';$('status-'+key).className='record-status bad';}else await loadBytes(key,await file.arrayBuffer(),file.name);}event.target.value='';});
      $('example-'+key).addEventListener('click',()=>{if(win.REVIEW_EXAMPLE)loadBytes(key,new TextEncoder().encode(JSON.stringify(win.REVIEW_EXAMPLE)),'예제 · 2026-11-14 보존 경기.json');});
      $('clear-'+key).addEventListener('click',()=>{versions[key]++;loaded[key]=null;$('name-'+key).textContent='아직 기록이 없습니다';$('status-'+key).textContent='JSON 파일을 선택하세요';$('status-'+key).className='record-status';$('clear-'+key).disabled=true;selectedEvent=null;render();});
    }
    $('robot').addEventListener('change',()=>{selectedEvent=null;render();});
    $('time').addEventListener('input',()=>{selectedEvent=null;draw();});
    $('play').addEventListener('click',()=>{if(playing){stop();return;}if(Number($('time').value)>=Number($('time').max))setTime(0);playing=true;lastTick=0;$('play').textContent='Ⅱ 잠시 멈춤';win.requestAnimationFrame(tick);});
    function tick(now){if(!playing)return;if(lastTick){const value=Math.min(Number($('time').max),Number($('time').value)+(now-lastTick)/1000*Number($('speed').value));setTime(value);if(value>=Number($('time').max))stop();}lastTick=now;if(playing)win.requestAnimationFrame(tick);}
    doc.addEventListener('visibilitychange',()=>{if(doc.hidden)stop();});
    $('attention-only').addEventListener('change',renderEvents);
    $('next-event').addEventListener('click',()=>{
      const current=Number($('time').value);const next=allJumps().find(e=>e.time>current+.001);
      if(next){selectedEvent=next;setTime(next.time);$('event-detail').focus();}
      else {$('event-detail').textContent='이후 사건이 없습니다. 시간을 앞으로 돌리거나 다른 로봇을 골라 보세요.';}
    });
    $('download-comparison').addEventListener('click',()=>{if(loaded.A&&loaded.B)download('경기-비교.json',comparisonExport(loaded.A,loaded.B,robotSlot()));});
    $('download-example').addEventListener('click',()=>{if(win.REVIEW_EXAMPLE)download('예제-경기-20261114.json',win.REVIEW_EXAMPLE);});
    function download(name,data){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=element('a');a.href=url;a.download=name;a.click();win.setTimeout(()=>URL.revokeObjectURL(url),1000);}
    function allJumps(){return Object.entries(loaded).filter(([,v])=>v).flatMap(([key,v])=>buildJumps(v.record,robotSlot()).filter(e=>!$('attention-only').checked||e.attention).map(e=>({...e,record:key}))).sort((a,b)=>a.time-b.time||a.record.localeCompare(b.record));}
    function renderEvents(){
      const jumps=allJumps();$('event-count').textContent=`${jumps.length}개`;
      const buttons=jumps.map(event=>{const button=element('button',`${event.record} · ${clock(event.time)} · ${event.message||event.type}`,'event '+(event.attention?'attention':''));button.type='button';button.addEventListener('click',()=>{stop();selectedEvent=event;setTime(event.time);});return button;});
      $('events').replaceChildren(...(buttons.length?buttons:[element('p','표시할 사건이 없습니다.','muted')]));
      $('next-event').disabled=!jumps.length;
    }
    function render(){
      stop();const count=Object.values(loaded).filter(Boolean).length;
      $('time').max=Math.max(1,...Object.values(loaded).filter(Boolean).map(v=>v.record.duration));$('time').disabled=!count;$('play').disabled=!count;$('robot').disabled=!count;
      $('download-comparison').disabled=!(loaded.A&&loaded.B);
      if(Number($('time').value)>Number($('time').max))$('time').value=$('time').max;
      const slot=robotSlot();
      for(const key of ['A','B']) {
        const input=loaded[key],container=$('facts-'+key);container.replaceChildren();
        if(!input)continue;
        const record=input.record,r=selectedRobot(record,slot);
        for(const [term,value] of [['조건 번호',record.conditions.seed??'정보 없음'],['저장 시점',`${fmt(record.duration)}초`],['점수',`${fmt(r?.score.total)}점${complete(record,slot)?' · 최종':' · 미확정'}`],['체력',fmt(r?.hp)]]) {container.append(element('dt',term),element('dd',String(value)));}
      }
      $('scores').replaceChildren();
      const comparison=loaded.A&&loaded.B?compareRecords(loaded.A.record,loaded.B.record,slot):null;
      const status=$('comparison-status');status.className='notice';
      if(!comparison)status.textContent=count?'B에도 기록을 넣으면 점수를 비교합니다. A와 B에 같은 예제를 넣어 사용법을 익힐 수 있습니다.':'먼저 “예제 열기”를 누르거나 경기 기록 파일을 선택하세요.';
      else if(!comparison.completeA||!comparison.completeB){status.textContent='최종 결과 비교를 보류합니다. 진행 중·중단된 기록, 제외된 로봇, 점수 누락이 있는지 확인하세요.';status.classList.add('warn');}
      else if(comparison.conditionStatus==='different'){status.textContent='경기 조건이 다릅니다. 점수는 볼 수 있지만 코드 개선의 근거로 비교할 수 없습니다.';status.classList.add('warn');}
      else if(comparison.conditionStatus==='unknown'){status.textContent='경기 조건 정보가 부족하거나 경기 도중 설정이 바뀌었습니다. 같은 조건인지 확정할 수 없어 점수 차이 계산을 보류합니다.';status.classList.add('warn');}
      else {status.textContent='기록된 경기 조건이 같습니다. 점수 차이는 B − A입니다. 한 번의 결과로 개선을 확정하지 마세요.';status.classList.add('good');}
      if(comparison&&(!comparison.completeA||!comparison.completeB)&&comparison.conditionStatus!=='same')status.append(element('span',comparison.conditionStatus==='different'?'경기 조건도 다릅니다. 아래 조건표를 확인하세요.':'일부 경기 조건도 기록되지 않았습니다.','block'));
      if(comparison&&!comparison.provenanceComplete)status.append(element('span',' 코드·모델 해시가 없는 기록이 있어 어떤 코드로 실행했는지는 이 파일만으로 확인할 수 없습니다.','block'));
      for(const key of SCORE_KEYS){const tr=element('tr');tr.append(element('th',key==='total'?'합계':key==='bonus'?'팀 보너스':key.toUpperCase()));for(const side of ['A','B'])tr.append(element('td',fmt(loaded[side]?selectedRobot(loaded[side].record,slot)?.score[key]:null)));const delta=comparison?.delta?.[key];tr.append(element('td',finite(delta)?(delta>0?'+':'')+fmt(delta):'—'));$('scores').append(tr);}
      const conditions=$('conditions');conditions.replaceChildren();
      for(const [key,name] of CONDITIONS){const row=element('tr');row.append(element('th',name));for(const side of ['A','B']){const value=loaded[side]?conditionValue(loaded[side].record,key,slot):null;let text=value==null?'정보 없음':Array.isArray(value)?(key==='runtime_changes'?`${value.length}개 변경`:`${value.length}대 설정`):obj(value)?(key==='layout'?`${value.width} × ${value.height} m`:'설정 있음'):String(value);row.append(element('td',text));}const item=comparison?.conditions.find(c=>c.key===key);row.append(element('td',item?({same:'같음',different:'다름',unknown:'확인 불가'}[item.status]):'—',item?.status==='different'?'difference':''));conditions.append(row);}
      const provenance=$('provenance');provenance.replaceChildren();
      for(const side of ['A','B']){const input=loaded[side];if(!input)continue;const p=input.record.provenance;const block=element('section',undefined,'provenance-card');block.append(element('h3',`${side} · ${input.name}`));const dl=element('dl');for(const [term,value] of [['입력 파일 SHA-256',input.sha256||'이 브라우저에서는 계산할 수 없습니다. localhost 또는 HTTPS에서 열어 주세요.'],['실험 ID',p.trial_id||'파일에 없음'],['코드 SHA-256',p.code_sha256||'파일에 없음'],['모델 SHA-256',p.model_sha256||'파일에 없음'],['제공된 원본 출처',p.source||'파일에 없음'],['제공된 원본 SHA-256',p.source_sha256||'파일에 없음'],['제공된 명세 SHA-256',p.manifest_sha256||'파일에 없음']])dl.append(element('dt',term),element('dd',value));block.append(dl);if(p.note)block.append(element('p',p.note));provenance.append(block);}
      renderEvents();draw();
    }
    function svg(tag,attrs={}){const el=doc.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attrs))el.setAttribute(k,String(v));return el;}
    function drawMap(side,time){
      const map=$('map-'+side),note=$('map-note-'+side),input=loaded[side];map.replaceChildren();
      if(!input){note.textContent='기록을 넣으면 이동 경로가 나타납니다.';return;}
      const record=input.record,arena=record.conditions.layout;
      if(!arena){note.textContent='경기장 크기 정보가 없어 경로를 표시할 수 없습니다.';return;}
      const scale=80,pad=20,w=arena.width*scale,h=arena.height*scale;map.setAttribute('viewBox',`0 0 ${w+2*pad} ${h+2*pad}`);
      const x=v=>pad+v*scale,y=v=>pad+h-v*scale;
      map.append(svg('rect',{x:pad,y:pad,width:w,height:h,fill:'#f8fafc',stroke:'#b7c2cf','stroke-width':1}));
      for(let n=1;n<arena.width;n++)map.append(svg('line',{x1:x(n),x2:x(n),y1:pad,y2:pad+h,stroke:'#e2e8f0'}));
      for(let n=1;n<arena.height;n++)map.append(svg('line',{x1:pad,x2:pad+w,y1:y(n),y2:y(n),stroke:'#e2e8f0'}));
      for(const item of arena.obstacles||[])if(finite(item.w)&&finite(item.h)&&item.w>0&&item.h>0)map.append(svg('rect',{x:x(item.x-item.w/2),y:y(item.y+item.h/2),width:item.w*scale,height:item.h*scale,rx:2,fill:'#cbd5e1'}));
      const slot=robotSlot(),points=record.history.filter(f=>f.time<=time).flatMap(f=>{const r=f.robots.find(r=>r.slot===slot);return r?[`${x(r.x)},${y(r.y)}`]:[];});
      const color=side==='A'?'#1764bd':'#a94c00';
      if(points.length)map.append(svg('polyline',{points:points.join(' '),fill:'none',stroke:color,'stroke-width':2.5,'stroke-linecap':'round','stroke-linejoin':'round'}));
      const frame=frameAt(record,time);const robot=frame?.robots.find(r=>r.slot===slot);
      if(robot){map.append(svg('circle',{cx:x(robot.x),cy:y(robot.y),r:7,fill:color,stroke:'white','stroke-width':2}));map.append(svg('line',{x1:x(robot.x),y1:y(robot.y),x2:x(robot.x+Math.cos(robot.yaw)*.2),y2:y(robot.y+Math.sin(robot.yaw)*.2),stroke:color,'stroke-width':3}));}
      note.textContent=robot?`${slot} · ${fmt(frame.time)}초의 저장 위치 · 체력 ${fmt(robot.hp)} · 탄약 ${fmt(robot.ammo)}${time>record.duration?' · 이 기록은 여기서 끝납니다.':''}`:record.history.length?'이 시간에는 선택한 로봇의 저장 위치가 없습니다.':'이동 기록이 없습니다. 점수와 사건만 확인할 수 있습니다.';
    }
    function draw(){const t=Number($('time').value);$('time-label').textContent=`${clock(t)} / ${clock(Number($('time').max))}`;drawMap('A',t);drawMap('B',t);if(selectedEvent){$('event-detail').textContent=`${selectedEvent.record} · ${fmt(selectedEvent.time)}초 · ${selectedEvent.slot||'전체'}\n${selectedEvent.message}\n종류: ${selectedEvent.type}`;}else $('event-detail').textContent='아래 사건을 누르면 해당 시간으로 이동합니다. 체력 감소 시각은 두 저장 위치 사이의 구간으로 표시합니다.';}
    render();
    if (new URLSearchParams(win.location.search).get('example') === '1' && win.REVIEW_EXAMPLE) {
      loadBytes('A', new TextEncoder().encode(JSON.stringify(win.REVIEW_EXAMPLE)), '예제 · 2026-11-14 보존 경기.json');
    }
  }
  return {MAX_BYTES,SCORE_KEYS,LIMITATIONS,parseRecord,normalizeRecord,compareRecords,frameAt,buildJumps,sha256,comparisonExport,mount};
});
