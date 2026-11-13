const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const modeName = v => ({practice:'자유연습',mission:'경기',rehearsal:'리허설'}[v] || v || '—');

export function createRecording({state, api, response, flash, ensureScene}) {
  const section = document.createElement('section');
  section.className = 'card recording-card';
  section.innerHTML = `<div class="recording-toolbar"><div><h2>경기 녹화</h2><p>3D 경기장 + UGV·드론 영상 · 소리 없이 저장</p></div><span id="record-state" role="status">녹화 준비</span><button id="record-start" class="button secondary">● 녹화 시작</button><button id="record-stop" class="button primary" hidden>■ 종료·저장</button></div><p id="record-note" class="small-note">이 화면을 보는 동안 녹화합니다. 화면 이동·백그라운드 전환 시 자동으로 종료·저장합니다.</p><div id="record-pending" hidden><button id="record-retry" class="button secondary">프로젝트에 다시 저장</button><button id="record-local" class="button quiet">영상 직접 다운로드</button></div><details id="record-library"><summary>저장한 영상 <span id="record-count">0</span></summary><p class="small-note" id="record-directory">프로젝트의 simulator/data/recordings에 저장합니다.</p><div id="record-list"></div><video id="record-player" controls playsinline hidden></video></details>`;
  $('#page-run .page-title').after(section);
  const canvas = document.createElement('canvas');
  canvas.width = 1280; canvas.height = 720;
  const ctx = canvas.getContext('2d', {alpha:false});
  const supported = typeof MediaRecorder !== 'undefined' && !!canvas.captureStream;
  let recorder = null, stream = null, timer = null, chunks = [], bytes = 0;
  let started = 0, metadata = null, pending = null, saving = false, loaded = false;
  let stopPromise = null, resolveStop = null, playerURL = null, lastRecords = [], reason = '';
  let starting = false, startTicket = 0;

  function running() { return !!recorder && recorder.state !== 'inactive'; }
  function update() {
    const s = state();
    $('#record-start').disabled = !supported || starting || !!stopPromise || running() || saving || !!pending || !s.physics?.connected || s.physics.runtime !== s.runtime;
    $('#record-start').hidden = running(); $('#record-stop').hidden = !running();
    $('#record-pending').hidden = !pending || saving;
    $('#record-retry').disabled = saving;
    if (!supported) $('#record-state').textContent = '이 브라우저는 녹화를 지원하지 않습니다. Chrome에서 여세요.';
    if (running()) $('#record-state').textContent = `녹화 중 ${Math.floor((performance.now()-started)/1000)}초`;
    if (!loaded && s.key) { loaded = true; refreshList().catch(() => { loaded = false; }); }
  }
  function text(value, x, y, size = 18, color = '#dce8ef') {
    ctx.font = `${size}px system-ui, sans-serif`; ctx.fillStyle = color;
    ctx.fillText(String(value ?? ''), x, y);
  }
  function fit(source, x, y, w, h, available = true) {
    ctx.fillStyle = '#17232e'; ctx.fillRect(x, y, w, h);
    const sw = source?.naturalWidth || source?.width, sh = source?.naturalHeight || source?.height;
    if (!available || !sw || !sh) { text('영상 수신 대기', x+20, y+h/2, 17); return; }
    const scale = Math.min(w/sw,h/sh);
    ctx.drawImage(source,x+(w-sw*scale)/2,y+(h-sh*scale)/2,sw*scale,sh*scale);
  }
  function draw() {
    const s = state(), scene = s.scene;
    ctx.fillStyle = '#0c151e'; ctx.fillRect(0,0,1280,720);
    text(`MAICON · ${modeName(s.runtime)} · 시뮬레이션 녹화`,20,32,22);
    text(`${new Date().toLocaleTimeString('ko-KR')}  /  ${Math.floor((performance.now()-started)/1000)}초`,980,32,17);
    if (scene?.renderer) {
      // Read WebGL in the same task as rendering; no permanent preserveDrawingBuffer cost.
      scene.renderer.render(scene.scene,scene.camera);
      fit(scene.renderer.domElement,16,50,898,590);
    } else fit(null,16,50,898,590,false);
    text('UGV · 내 로봇 카메라',934,73,18);
    fit($('#inference-canvas'),934,86,330,247,$('#ugv-camera-empty').hidden);
    text('UAV · 고정 정찰 카메라',934,365,18);
    fit($('#drone-image'),934,378,330,247,$('#drone-camera-empty').hidden);
    const c=s.summary?.controller||{},r=s.ownSensor||{};
    text(`녹화 시작 코드 ${String(metadata.code_version_id||'참가자 프로그램 미실행')}  |  모델 ${String(metadata.model_id||'미실행')}`,20,668,16);
    text(`HP ${r.hp??'—'}  ·  탄약 ${r.ammo??'—'}  ·  상태 ${s.snapshot?.paused?'일시정지':s.snapshot?.phase||'—'}  ·  코드 ${String(c.code_sha256||'').slice(0,12)||'—'}`,20,696,17);
  }
  async function start() {
    if (starting || stopPromise || running() || saving || pending) return;
    starting=true;const ticket=++startTicket;update();
    try {
      if (!supported) throw new Error('이 브라우저는 녹화를 지원하지 않습니다. Chrome에서 여세요.');
      await ensureScene();
      if(ticket!==startTicket || state().page!=='run')return;
      const s=state(),c=s.summary?.controller||{},d=s.summary?.deployment||{};
      if (!s.physics?.connected || s.physics.runtime!==s.runtime || !s.scene || !s.snapshot) throw new Error('경기장 연결과 화면 수신을 먼저 확인하세요.');
      const mime=['video/webm;codecs=vp8','video/webm','video/mp4'].find(x=>MediaRecorder.isTypeSupported(x));
      if (!mime) throw new Error('이 브라우저에서 사용할 수 있는 녹화 형식이 없습니다.');
      const active=!!c.pid&&!['failed','exited','stopped','interrupted','not_started'].includes(c.status);
      metadata={runtime:s.runtime,code_version_id:active?c.code_version_id:null,code_sha256:active?c.code_sha256:null,model_id:active?(c.model_id||d.model_id):null,policy:active?d.policy:null,controller_status:c.status,loaded:!!c.loaded,started_at:new Date().toISOString(),width:1280,height:720,fps:15,source:'simulator-composite'};
      chunks=[];bytes=0;reason='user';started=performance.now();draw();
      stream=canvas.captureStream(15);recorder=new MediaRecorder(stream,{mimeType:mime,videoBitsPerSecond:2000000});
      const ownRecorder=recorder,ownStream=stream;
      recorder.ondataavailable=e=>{if(e.data.size){chunks.push(e.data);bytes+=e.data.size;if(bytes>120*1024*1024)stop('size_limit');}};
      recorder.onerror=e=>{flash('녹화 오류: '+(e.error?.message||'브라우저 오류'),true);stop('recorder_error');};
      recorder.onstop=async()=>{
        clearInterval(timer);ownStream.getTracks().forEach(t=>t.stop());
        metadata={...metadata,duration_s:(performance.now()-started)/1000,ended_at:new Date().toISOString(),stop_reason:reason};
        pending={blob:new Blob(chunks,{type:ownRecorder.mimeType||mime}),metadata};chunks=[];
        recorder=null;await uploadPending();resolveStop?.();resolveStop=null;stopPromise=null;update();
      };
      recorder.start(1000);
      timer=setInterval(()=>{
        if (!running()) return;
        const current=state(),nowCode=current.summary?.controller?.code_version_id||null;
        if(current.runtime!==metadata.runtime)return stop('runtime_changed');
        if(current.page!=='run')return stop('page_changed');
        if(!current.physics?.connected)return stop('simulator_stopped');
        const liveController=current.summary?.controller,controllerActive=!!liveController?.pid&&!['failed','exited','stopped','interrupted','not_started'].includes(liveController.status);
        if((metadata.code_version_id||null)!==nowCode&&controllerActive)return stop('code_changed');
        if(metadata.model_id&&current.summary?.controller?.model_id!==metadata.model_id)return stop('model_changed');
        if(performance.now()-started>15*60*1000)return stop('time_limit');
        try{draw();update();}catch(error){flash(error.message,true);stop('capture_error');}
      },1000/15);
      $('#record-note').textContent='녹화 중 · 최대 15분 또는 120 MiB. 종료하면 프로젝트에 저장합니다.';update();
    } catch(error) {stream?.getTracks().forEach(t=>t.stop());recorder=null;flash(error.message,true);}
    finally{starting=false;update();}
  }
  function stop(why='user') {
    if(starting)startTicket++;
    if (stopPromise) return stopPromise;
    if (!running()) return Promise.resolve();
    reason=why;stopPromise=new Promise(resolve=>resolveStop=resolve);recorder.stop();return stopPromise;
  }
  async function uploadPending() {
    if (!pending || saving) return;
    saving=true;$('#record-state').textContent='영상 파일 저장 중…';update();
    try {
      const r=await fetch('/api/recordings',{method:'POST',headers:{'X-Admin-Key':state().key,'Content-Type':pending.blob.type,'X-Recording-Metadata':encodeURIComponent(JSON.stringify(pending.metadata))},body:pending.blob,signal:AbortSignal.timeout(90000)});
      const data=await r.json();if(!r.ok)throw new Error(data.detail||'녹화 저장 실패');
      pending=null;$('#record-state').textContent='프로젝트에 저장 완료';$('#record-note').textContent='영상 파일 저장·해시 확인 완료. 아래 저장한 영상에서 재생하거나 다운로드하세요.';
      try{await refreshList();$('#record-library').open=true;}catch{loaded=false;$('#record-note').textContent='영상은 프로젝트에 저장됐습니다. 목록을 다시 불러오는 중입니다.';}
      flash('녹화 영상을 프로젝트에 저장했습니다.');
    }catch(error){$('#record-state').textContent='저장 실패 · 영상은 이 탭에 보관 중';$('#record-note').textContent=error.message;flash('영상 저장 실패. 다시 저장하거나 직접 다운로드하세요.',true);}
    finally{saving=false;update();}
  }
  function download(blob,name) {const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);}
  async function refreshList() {
    const data=await api('/api/recordings');lastRecords=data.recordings||[];
    $('#record-count').textContent=lastRecords.length;$('#record-directory').textContent=data.directory;
    $('#record-list').innerHTML=lastRecords.length?lastRecords.map(r=>`<div class="record-item"><div><strong>${esc(modeName(r.metadata?.runtime))} · ${esc(new Date(r.created_at*1000).toLocaleString('ko-KR'))}</strong><small>${Math.round(r.metadata?.duration_s||0)}초 · ${(r.bytes/1024/1024).toFixed(1)} MiB · 코드 ${esc(r.metadata?.code_version_id||'참가자 프로그램 미실행')}</small></div><button class="button quiet" data-record-play="${esc(r.id)}">재생</button><button class="button quiet" data-record-download="${esc(r.id)}">다운로드</button></div>`).join(''):'<p class="muted">아직 저장한 영상이 없습니다.</p>';
  }
  section.addEventListener('click',async event=>{
    const b=event.target.closest('[data-record-play],[data-record-download]');if(!b)return;b.disabled=true;
    try{const id=b.dataset.recordPlay||b.dataset.recordDownload,r=lastRecords.find(x=>x.id===id),blob=await(await response(r.url)).blob();
      if(b.dataset.recordDownload)download(blob,r.filename);
      else{if(playerURL)URL.revokeObjectURL(playerURL);playerURL=URL.createObjectURL(blob);const player=$('#record-player');player.src=playerURL;player.hidden=false;await player.play();}
    }catch(error){flash(error.message,true);}finally{b.disabled=false;}
  });
  $('#record-start').addEventListener('click',start);$('#record-stop').addEventListener('click',()=>stop());
  $('#record-retry').addEventListener('click',uploadPending);
  $('#record-local').addEventListener('click',()=>{if(pending)download(pending.blob,'MAICON-recording.'+(pending.blob.type.includes('mp4')?'mp4':'webm'));});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop('tab_hidden');});
  window.addEventListener('beforeunload',event=>{if(running()||saving||pending){event.preventDefault();event.returnValue='';}});
  return {update,stop,get active(){return running();},onPage(page){if(page!=='run')stop('page_changed');},dispose(){stream?.getTracks().forEach(t=>t.stop());clearInterval(timer);if(playerURL)URL.revokeObjectURL(playerURL);}};
}
