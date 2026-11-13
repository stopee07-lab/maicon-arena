const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const short = value => value ? String(value).slice(0, 12) : '—';
const date = value => value ? new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('ko-KR') : '';
const running = controller => !!controller?.pid && !['stopped','failed','exited','not_started'].includes(controller.status);
const labels = {running:'요청 처리 중',queued:'대기 중',succeeded:'검토 대기',failed:'실패',applied:'파일 적용됨',cancelled:'중단됨',interrupted:'중단됨'};

export function assistantResultView(job = {}) {
  const hasFiles = Array.isArray(job.files) && job.files.length > 0;
  const noPatch = job.status === 'succeeded' && !hasFiles;
  return {
    label: noPatch ? '수정안 없음' : labels[job.status] || job.status || '',
    noPatch,
    showApply: job.status === 'applied' || (job.status === 'succeeded' && hasFiles),
    reason: noPatch ? '수정할 코드 파일이 생성되지 않았습니다. 위 응답을 확인하고, 관찰한 문제와 원하는 동작을 구체적으로 적어 다시 요청하세요.' : ''
  };
}

export function createDevelopment({api,response,flash,refreshStatus,switchPage,state,refreshAvailability=()=>{}}) {
  const D = {status:null,path:'team_node.py',content:'',sha:'',dirty:false,loaded:false,busy:false,actionId:'',refreshing:false,lastRefresh:0,caseId:'',cases:[],scene:null,versionId:'',jobId:null,job:null,restore:null,caseURL:null,fileGeneration:0,caseGeneration:0,external:false,diagnosing:false,ros:null,rosLaunching:false,launching:false,stopping:false,runInitialized:false,editMode:'request'};
  const endpoint = p => '/api/development' + p;
  const post = (path,body={}) => api(endpoint(path),{method:'POST',body,signal:AbortSignal.timeout(90000)});
  const source = $('#dev-source');
  function checkDirty() {
    D.dirty = source.value !== D.content;
    $('#dev-save-state').textContent = D.dirty ? '미저장 수정 있음' : D.external ? '바깥에서 변경됨' : '파일 저장됨';
    $('#dev-save-state').className = 'tag ' + (D.dirty || D.external ? 'warn' : 'good');
    $('#dev-file-hash').textContent = '파일 SHA ' + short(D.sha);
    const before = source.value.slice(0,source.selectionStart).split('\n');
    $('#dev-position').textContent = `${before.length}행 · ${before[before.length-1].length+1}열`;
    $('#dev-line-numbers').textContent = Array.from({length:source.value.split('\n').length},(_,i)=>i+1).join('\n');
    $('#dev-line-numbers').scrollTop = source.scrollTop;
    renderExecutionLabel();available();
  }
  function showError(error) {
    const message=error.message || String(error);
    $('#dev-error').className='notice error';$('#dev-error').hidden=false;$('#dev-error').textContent=message;
    $('#dev-apply-status').className='dev-apply-status notice error';
    $('#dev-apply-status').textContent=message;
    flash(message,true);
  }
  async function action(button,work) {
    if (D.busy) {flash('이전 작업을 처리 중입니다. 진행 상태를 확인하세요.');return;}
    D.busy=true;D.actionId=button?.id||'';$('#dev-error').hidden=true;
    $('#dev-apply-status').className='dev-apply-status';
    const progress={
      'dev-apply-run':'작업 파일을 저장하고 문법을 검사하는 중입니다…',
      'dev-version-run':'선택한 이전 버전을 확인하는 중입니다…',
      'dev-assistant-send':'코드와 요청을 보내는 중입니다…',
      'dev-jupyter':'JupyterLab을 여는 중입니다…',
      'dev-ros-jupyter':'ROS 실습 환경을 준비하는 중입니다…'
    }[D.actionId];
    if(progress)$('#dev-apply-status').textContent=progress;
    available();
    try {
      await work();await refresh(true);await refreshStatus();
      if(progress&&$('#dev-apply-status').textContent===progress){
        $('#dev-apply-status').textContent={
          'dev-assistant-send':'요청을 접수했습니다. 왼쪽에서 처리 상태와 수정안을 확인하세요.',
          'dev-jupyter':'JupyterLab을 열었습니다.',
          'dev-ros-jupyter':'ROS 실습 환경을 열었습니다.'
        }[D.actionId]||'작업을 마쳤습니다.';
      }
    }
    catch(error){showError(error);}
    finally{D.busy=false;D.actionId='';available();}
  }
  function options(id,values,placeholder,value) {
    const el=$(id),signature=JSON.stringify(values);
    if(el.dataset.signature!==signature){el.innerHTML=`<option value="">${esc(placeholder)}</option>`+values.map(v=>`<option value="${esc(v.id)}">${esc(v.label)}</option>`).join('');el.dataset.signature=signature;}
    if([...el.options].some(o=>o.value===value))el.value=value;
  }
  function renderStatus() {
    if(!D.status)return;
    const versions=D.status.versions||[];
    const items=versions.map(v=>({id:v.id,label:`${v.name||v.id} · ${short(v.sha256)}`}));
    if(!versions.some(v=>v.id===D.versionId))D.versionId=versions[0]?.id||'';
    options('#dev-version',items,'저장한 버전 없음',D.versionId);
    const deploy=$('#deploy-code'),selected=deploy.value||state().summary?.deployment?.code_version_id||D.versionId;
    options('#deploy-code',items,'코드 버전을 선택하세요',selected);
    $('#dev-version-count').textContent=`${versions.length}개`;
    $('#dev-workspace-path').textContent=D.status.workspace_path||'simulator/workspace';
    const file=D.status.files?.find(f=>f.path===D.path);
    D.external=!!(file?.sha256&&D.sha&&file.sha256!==D.sha);
    const codex=D.status.codex||{};
    $('#dev-codex-state').textContent=codex.available&&codex.logged_in?'연결 가능':codex.available?'로그인 필요':'설치 확인 필요';
    $('#dev-codex-state').className='tag '+(codex.available&&codex.logged_in?'good':'warn');
    $('#dev-codex-state').title=codex.detail||'';
    const v=versions.find(v=>v.id===D.versionId);
    $('#dev-version-meta').textContent=v?`${date(v.created_at)} · 코드 SHA ${v.sha256}`:'파일 저장과 문법 검사를 마친 뒤 버전을 만드세요.';
    const jobs=D.status.assistant_jobs||[];
    $('#dev-assistant-jobs').innerHTML=jobs.length?jobs.map(j=>`<button class="dev-history-item" data-assistant-job="${esc(j.id)}"><span>${esc(j.prompt||j.id)}</span><small>${esc(labels[j.status]||j.status)} · ${esc(date(j.created_at))}</small></button>`).join(''):'<p class="muted">아직 요청이 없습니다.</p>';
    if(!D.jobId&&jobs[0]){D.jobId=jobs[0].id;}
    checkDirty();renderRuntime();renderSelection();
  }
  function renderExecutionLabel() {
    const c=state().summary?.controller||{},active=running(c),same=!!D.status?.workspace_sha256&&D.status.workspace_sha256===c.code_sha256;
    let text='로봇 정지 · 현재 코드 적용 전',kind='';
    if(D.launching){text='새 코드 확인·시작 중…';kind='warn';}
    else if(D.dirty){text='편집한 내용 미적용';kind='warn';}
    else if(D.external){text='JupyterLab 변경 확인 필요';kind='warn';}
    else if(active&&!c.loaded){text=c.error?'로봇 시작 오류':'로봇 프로그램 시작 중';kind=c.error?'bad':'warn';}
    else if(active&&c.code_integrity===false){text='실행 코드 확인 실패';kind='bad';}
    else if(active&&same){text='현재 작업 코드 실행 중';kind='good';}
    else if(active){text='이전 코드 실행 중 · 새 코드 미적용';kind='warn';}
    $('#dev-current-code-state').textContent=text;$('#dev-current-code-state').className='tag '+kind;
  }
  function renderRuntime() {
    const s=state().summary||{},c=s.controller||{},d=s.deployment||{},models=s.models||[],active=running(c);
    const selected=$('#dev-run-model').value||d.model_id||state().modelId;
    options('#dev-run-model',models.map(m=>({id:m.id,label:m.name||m.id})),'학습한 모델 선택',selected);
    if(!D.runInitialized&&state().connected){$('#dev-run-mode').value=d.mode||({mission:'match'}[state().runtime]||state().runtime||'practice');$('#dev-run-policy').value=d.policy||'mission';D.runInitialized=true;}
    const code=active?c:d,modelId=active?c.model_id||d.model_id:d.model_id,model=models.find(m=>m.id===modelId),mode=active?c.mode||d.mode:d.mode;
    $('#dev-running-code').innerHTML=`<div><span>${active?'실행 중인 코드':'준비된 코드'}</span><strong>${esc(code.code_name||code.code_version_id||'아직 적용 안 됨')}<small>SHA ${esc(short(code.code_sha256))}</small></strong></div><div><span>${active?'사용 중인':'준비된'} 인식 모델</span><strong>${esc(model?.name||modelId||'선택 전')}</strong></div><div><span>${active?'실행 상태':'프로그램 정지'}</span><strong>${esc({practice:'자유연습',match:'경기',mission:'경기',rehearsal:'리허설'}[mode]||'실행 전')} · ${d.policy==='mission'?'인식 + 행동 코드':d.policy==='observe'?'인식만 · 이동 안 함':'동작 선택 전'}</strong></div>`;
    renderExecutionLabel();available();
  }
  function chooseEditMode(mode) {
    D.editMode=mode;
    $('#dev-code-pane').hidden=mode!=='code';$('#dev-request-pane').hidden=mode!=='request';
    document.querySelectorAll('[data-dev-edit-mode]').forEach(el=>{const active=el.dataset.devEditMode===mode;el.classList.toggle('active',active);el.setAttribute('aria-selected',String(active));});
  }
  async function applyAndRun(versionId) {
    const model=$('#dev-run-model').value;if(!model)throw new Error('사용할 인식 모델을 선택하세요. 아직 모델이 없다면 모델 학습에서 먼저 만드세요.');
    let checked;
    if(!versionId)checked=await syntax();
    else if(D.dirty)throw new Error('편집 중인 내용을 먼저 저장하세요. 이전 버전 실행은 현재 편집 내용을 적용하지 않습니다.');
    const body={mode:$('#dev-run-mode').value,policy:$('#dev-run-policy').value,model_id:model};
    if(versionId)body.version_id=versionId;else {body.base_sha256=checked.workspace_sha256;body.name='작업 코드 '+new Date().toLocaleString('ko-KR');}
    D.launching=true;$('#dev-apply-status').textContent='기존 실행을 멈추고, 선택한 코드·모델을 확인하는 중입니다…';renderExecutionLabel();available();
    try{
      const result=await post('/apply-run',body);
      if(!result.started)throw new Error(result.error||'로봇 실행이 확인되지 않았습니다.');
      state().runtime=result.runtime||({match:'mission'}[body.mode]||body.mode);state().runtimeInitialized=true;$('#runtime-mode').value=state().runtime;
      if(result.physics)state().physics=result.physics;
      if(result.deployment)state().summary.deployment=result.deployment;
      if(result.controller)state().summary.controller=result.controller;
      D.versionId=result.code?.id||result.deployment?.code_version_id||D.versionId;D.restore=null;
      $('#dev-apply-status').textContent=`실행 확인 · 코드 ${short(result.controller?.code_sha256||result.code?.sha256)} · ${body.policy==='observe'?'인식만 수행합니다.':'행동 코드를 사용합니다.'}`;
      switchPage('run');flash('선택한 코드·모델로 로봇을 다시 실행했습니다.');
    }catch(error){$('#dev-apply-status').textContent=error.message||String(error);throw error;}
    finally{D.launching=false;renderExecutionLabel();available();}
  }
  function renderRos() {
    const ros=D.ros||{},active=ros.running||ros.starting||D.rosLaunching;
    state().rosNotebook={...ros,starting:ros.starting||D.rosLaunching};
    refreshAvailability();
    $('#dev-ros-jupyter-state').textContent=D.rosLaunching||ros.starting?'ROS 실습 준비 중 · 처음에는 잠시 걸립니다':ros.running?'ROS 실습 실행 중 · 로봇 제어 사용':ros.unavailable?'ROS 상태 확인 불가':'ROS 실습 종료';
    $('#dev-ros-jupyter-state').className='tag '+(active?'good':ros.unavailable?'warn':'');
    $('#dev-ros-jupyter-state').title=ros.detail||'';
    $('#dev-ros-jupyter').textContent=D.rosLaunching||ros.starting?'ROS 실습 준비 중…':ros.running?'영상 실습 · ROS 2 다시 열기 ↗':'영상 실습 · ROS 2 열기 ↗';
    $('#dev-ros-jupyter-stop').hidden=!active;
    available();
  }
  async function liveDiagnostics() {
    if(D.diagnosing||!state().key||state().page!=='run')return;
    D.diagnosing=true;const runtime=state().runtime;
    try{
      const d=await api(endpoint('/diagnostics?runtime='+encodeURIComponent(runtime)));
      if(state().runtime!==runtime)return;
      const cmd=d.command||{},nav=d.navigation||{};
      $('#live-code-diagnostics').innerHTML=`<div class="dev-diagnostic-grid"><div><span>전방 거리</span><strong>${d.front_clearance_m==null?'측정 없음':Number(d.front_clearance_m).toFixed(2)+' m'}</strong></div><div><span>위치 변화 없는 시간</span><strong>${Number(d.stationary_seconds||0).toFixed(1)}초</strong></div><div><span>주행 명령</span><strong>${Number(cmd.linear||0).toFixed(2)} m/s · ${Number(cmd.angular||0).toFixed(2)} rad/s</strong></div><div><span>코드 버전</span><strong>${esc(short(d.code_version_id||state().summary?.controller?.code_version_id))}</strong></div></div><p>${esc((d.flags||[]).join(' · ')||'별도 정지·회전 신호 없음')}</p><p>주행 계획: ${esc(nav.goal_reason||'코드에서 미제공')}${nav.survey_index!=null?' · 순서 '+esc(nav.survey_index):''}<br>${esc(Array.isArray(nav.goal)&&nav.goal.length===2?'참가 코드 추정 목표: '+nav.goal.map(v=>Number(v).toFixed(2)).join(', ')+' m · 경로 '+(nav.path?.length||0)+'개 지점 · '+(nav.reason||''):d.goal_note||'')}</p><small>${esc(d.note||'')}</small>`;
    }catch(error){$('#live-code-diagnostics').textContent='진단 기록을 읽지 못했습니다: '+error.message;}
    finally{D.diagnosing=false;}
  }
  function renderSelection() {
    const v=D.status?.versions?.find(v=>v.id===D.versionId);
    $('#dev-retest-selection').innerHTML=`<span>재시험할 구성</span><strong>${esc(D.scene?.name||'장면 선택 필요')}</strong><p>${esc(v?.name||'코드 버전 선택 필요')}</p>`;
    $('#dev-assistant-context').textContent=D.scene?`포함할 기록: ${D.scene.name||D.scene.id} · 현재 작업 파일 2개`:'현재 작업 파일 2개를 전달합니다. 문제 장면을 선택하면 해당 기록도 포함합니다.';
  }
  async function loadFile(path,force=false) {
    if(D.dirty&&!force&&!window.confirm('저장하지 않은 편집을 버리고 파일을 다시 읽을까요?'))return false;
    const generation=++D.fileGeneration,file=await api(endpoint('/file?path='+encodeURIComponent(path)));
    if(generation!==D.fileGeneration)return false;
    D.path=file.path;D.content=file.content;D.sha=file.sha256;D.loaded=true;D.external=false;source.value=file.content;source.scrollTop=0;source.scrollLeft=0;$('#dev-file').value=D.path;checkDirty();return true;
  }
  async function save() {
    if(!D.loaded)throw new Error('파일을 먼저 불러오세요.');
    if(D.external&&!D.dirty)throw new Error('JupyterLab 등에서 파일이 변경됐습니다. 파일 다시 읽기로 현재 내용을 확인하세요.');
    if(!D.dirty)return;
    const content=source.value;
    const file=await post('/file',{path:D.path,content,base_sha256:D.sha});
    D.content=content;D.sha=file.sha256||D.sha;D.external=false;checkDirty();
    $('#dev-check-result').textContent='저장됨 · 문법 검사를 실행하세요.';
  }
  async function syntax() {
    await save();
    const result=await post('/check');
    $('#dev-check-result').textContent=result.valid?'문법 검사 통과':(result.errors||[]).map(e=>`${e.path} ${e.line||'?'}행: ${e.message}`).join('\n');
    $('#dev-check-result').classList.toggle('error',!result.valid);
    if(!result.valid){const e=result.errors?.find(e=>e.path===D.path);if(e?.line){const i=source.value.split('\n').slice(0,e.line-1).join('\n').length+(e.line>1?1:0);source.focus();source.setSelectionRange(i,i);source.scrollTop=(e.line-4)*22;}throw new Error('문법 오류가 있습니다. 표시된 위치를 확인하세요.');}
    return result;
  }
  async function refresh(force=false) {
    if(D.refreshing||!state().key)return;
    if(!force&&Date.now()-D.lastRefresh<3500)return;
    D.refreshing=true;
    try{
      const [status,cases,ros]=await Promise.all([api(endpoint('/status')),api(endpoint('/cases')),api(endpoint('/ros-jupyter')).catch(error=>({...D.ros,unavailable:true,detail:error.message}))]);
      D.status=status;D.cases=cases.cases||[];D.ros=ros;D.lastRefresh=Date.now();renderRos();
      options('#dev-case-select',D.cases.map(c=>({id:c.id,label:`${c.name||c.id} · ${date(c.created_at)}`})),'저장한 장면 선택',D.caseId);
      renderStatus();
      if(!D.loaded)await loadFile(D.path,true);
      if(D.jobId)await loadJob(D.jobId);
    } finally{D.refreshing=false;}
  }
  async function loadCase(id) {
    D.caseId=id;D.restore=null;D.scene=null;$('#dev-retest-status').textContent='장면과 코드 버전을 선택해 복원한 뒤 재시험을 시작하세요.';const generation=++D.caseGeneration;
    if(D.caseURL){URL.revokeObjectURL(D.caseURL);D.caseURL=null;}
    $('#dev-case-frame').hidden=true;$('#dev-case-detail').hidden=!id;
    if(!id){$('#dev-case-summary').textContent='실행·관측 화면에서 문제를 재현한 뒤 저장하세요.';renderSelection();available();return;}
    const scene=await api(endpoint('/cases/'+encodeURIComponent(id)));
    if(generation!==D.caseGeneration)return;
    D.scene=scene;$('#dev-case-select').value=id;
    $('#dev-case-summary').innerHTML=`<strong>${esc(scene.name||scene.id)}</strong><span>${esc({practice:'자유연습',mission:'경기',rehearsal:'리허설'}[scene.runtime]||scene.runtime)} · ${esc(date(scene.created_at))}</span><span>${esc(scene.summary||'위치·경기 상태·실행 기록 저장됨')}</span>`;
    const diagnostics=scene.diagnostics||{};
    const distance=diagnostics.front_clearance_m;
    const command=diagnostics.command||{};
    $('#dev-case-diagnostics').innerHTML=[['전방 거리',distance==null?'측정 없음':Number(distance).toFixed(2)+' m'],['명령',`${Number(command.linear||0).toFixed(2)} m/s · ${Number(command.angular||0).toFixed(2)} rad/s`],['제자리 시간',diagnostics.stationary_seconds==null?'기록 없음':Number(diagnostics.stationary_seconds).toFixed(1)+'초'],['관측 상태',(diagnostics.flags||[]).join(' · ')||'별도 진단 없음']].map(([k,v])=>`<div><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join('');
    $('#dev-case-context').textContent=JSON.stringify({diagnostics:scene.diagnostics,own_sensor:scene.own_sensor,controller:scene.controller},null,2);
    $('#dev-case-frame-note').textContent='저장된 시점의 로봇 카메라입니다.';
    try{const r=await response(endpoint('/cases/'+encodeURIComponent(id)+'/frame.jpg'));const blob=await r.blob();if(generation!==D.caseGeneration)return;D.caseURL=URL.createObjectURL(blob);$('#dev-case-frame').src=D.caseURL;$('#dev-case-frame').hidden=false;}
    catch{$('#dev-case-frame-note').textContent='이 장면에는 저장된 카메라 영상이 없습니다.';}
    renderSelection();available();
  }
  async function capture() {
    const runtime=state().runtime;
    const result=await post('/cases',{name:$('#dev-case-name').value.trim()||`문제 장면 ${new Date().toLocaleTimeString('ko-KR')}`,runtime});
    const id=result.id||result.case?.id;
    if(!id)throw new Error('장면 저장 결과에 장면 번호가 없습니다.');
    await refresh(true);await loadCase(id);switchPage('develop');$('#dev-history-tools').open=true;
    flash('장면과 실행 기록을 저장하고 로봇 프로세스를 멈췄습니다.');
  }
  async function loadJob(id) {
    const job=await api(endpoint('/assistant/'+encodeURIComponent(id)));
    if(id!==D.jobId)return;
    D.job=job;$('#dev-assistant-result').hidden=false;
    const view=assistantResultView(job);
    $('#dev-assistant-status').textContent=view.label;
    $('#dev-assistant-status').className='tag '+(job.status==='failed'?'bad':view.noPatch||['running','queued'].includes(job.status)?'warn':'good');
    $('#dev-assistant-summary').textContent=job.error||job.summary||(['running','queued'].includes(job.status)?'현재 코드와 기록을 읽고 있습니다. 수정안이 오면 변경 내용을 표시합니다.':'');
    $('#dev-assistant-diff').textContent=job.diff||'';$('#dev-assistant-diff').hidden=!job.diff;
    $('#dev-assistant-logs').textContent=Array.isArray(job.logs)?job.logs.join('\n'):(job.logs||'아직 요청 기록이 없습니다.');
    $('#dev-assistant-apply').textContent=job.status==='applied'?'작업 파일에 적용됨':'검토한 수정안을 파일에 적용';
    $('#dev-assistant-apply-note').textContent=view.reason;
    $('#dev-assistant-apply-note').hidden=!view.reason;
    available();
  }
  function available() {
    const connected=state().connected,busy=D.busy||!connected;
    for(const id of ['#dev-capture','#run-capture-case','#dev-check','#dev-snapshot','#dev-diff','#dev-rollback','#dev-restore-case','#dev-file','#dev-reload-file','#dev-jupyter'])$(id).disabled=busy;
    $('#dev-save').disabled=busy||!D.loaded||!D.dirty;
    $('#dev-capture').disabled=busy||!state().physics?.connected;
    $('#run-capture-case').disabled=busy||!state().physics?.connected;
    for(const id of ['#dev-diff','#dev-rollback'])$(id).disabled=busy||!D.versionId;
    $('#dev-restore-case').disabled=busy||!D.caseId||!D.versionId;
    $('#dev-stop').disabled=!connected||D.stopping||!(D.launching||running(state().summary?.controller)||state().physics?.running||state().physics?.connected||D.ros?.running||D.ros?.starting);
    $('#dev-stop').textContent=D.stopping?'정지 중…':'전체 정지';
    const deployment=state().summary?.deployment||{};
    const deploymentRuntime=deployment.mode==='match'?'mission':deployment.mode;
    const restoredReady=!!D.restore&&D.restore.id===D.caseId&&D.restore.version===D.versionId&&deployment.code_version_id===D.restore.version&&(!D.restore.deploymentId||deployment.id===D.restore.deploymentId)&&deploymentRuntime===D.restore.runtime&&state().runtime===D.restore.runtime&&state().physics?.runtime===D.restore.runtime&&state().physics?.connected;
    const rosActive=!!(D.ros?.running||D.ros?.starting||D.rosLaunching);
    $('#dev-ros-jupyter').disabled=busy||D.rosLaunching||D.ros?.starting;
    $('#dev-ros-jupyter-stop').disabled=busy||!rosActive;
    $('#dev-control-owner-note').hidden=!rosActive;
    $('#dev-apply-run').disabled=busy||rosActive||!D.loaded||!$('#dev-run-model').value;
    $('#dev-apply-run').textContent=D.launching?'코드 적용·실행 확인 중…':D.busy&&D.actionId==='dev-apply-run'?'작업 파일 저장·검사 중…':'현재 코드 적용·실행 →';
    $('#dev-version-run').disabled=busy||rosActive||!D.versionId||!$('#dev-run-model').value;
    for(const id of ['#dev-run-mode','#dev-run-model','#dev-run-policy'])$(id).disabled=busy;
    $('#dev-retest').disabled=busy||rosActive||!restoredReady||running(state().summary?.controller);
    if(rosActive)$('#controller-start').disabled=true;
    const pending=['running','queued'].includes(D.job?.status);
    $('#dev-assistant-send').disabled=busy||pending||!D.status?.codex?.available||!D.status?.codex?.logged_in;
    $('#dev-assistant-cancel').hidden=!pending;$('#dev-assistant-cancel').disabled=busy;
    $('#dev-assistant-apply').disabled=busy||D.job?.status!=='succeeded'||D.dirty||!(D.job?.files?.length);
    $('#dev-assistant-apply').hidden=!assistantResultView(D.job||{}).showApply;
    source.readOnly=busy||!D.loaded;
  }
  document.querySelectorAll('[data-dev-edit-mode]').forEach(el=>el.addEventListener('click',()=>chooseEditMode(el.dataset.devEditMode)));
  $('#dev-run-model').addEventListener('change',available);
  $('#dev-apply-run').addEventListener('click',event=>action(event.target,()=>applyAndRun()));
  $('#dev-version-run').addEventListener('click',event=>action(event.target,()=>applyAndRun(D.versionId)));
  source.addEventListener('input',()=>{$('#dev-version-diff').hidden=true;checkDirty();$('#dev-check-result').textContent='수정됨 · 저장 후 다시 검사하세요.';});
  source.addEventListener('click',checkDirty);source.addEventListener('keyup',checkDirty);
  source.addEventListener('scroll',()=>$('#dev-line-numbers').scrollTop=source.scrollTop);
  source.addEventListener('keydown',event=>{if(event.key==='Tab'){event.preventDefault();source.setRangeText('    ',source.selectionStart,source.selectionEnd,'end');checkDirty();}if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='s'){event.preventDefault();action($('#dev-save'),save);}});
  window.addEventListener('beforeunload',event=>{if(D.dirty){event.preventDefault();event.returnValue='';}});
  window.addEventListener('pagehide',()=>{if(D.caseURL)URL.revokeObjectURL(D.caseURL);});
  $('#dev-file').addEventListener('change',event=>{const next=event.target.value;event.target.value=D.path;action(event.target,()=>loadFile(next));});
  $('#dev-reload-file').addEventListener('click',event=>action(event.target,()=>loadFile(D.path)));
  $('#dev-save').addEventListener('click',event=>action(event.target,async()=>{await save();flash('실제 작업 파일을 저장했습니다. 실행 중인 코드는 그대로입니다.');}));
  $('#dev-check').addEventListener('click',event=>action(event.target,syntax));
  $('#dev-snapshot').addEventListener('click',event=>action(event.target,async()=>{await syntax();const version=await post('/versions',{name:$('#dev-version-name').value.trim()||'코드 '+new Date().toLocaleString('ko-KR')});D.versionId=version.id||version.version?.id;D.restore=null;$('#dev-version-diff').hidden=true;$('#dev-version-name').value='';flash('현재 파일을 고정한 코드 버전을 저장했습니다.');}));
  $('#dev-version').addEventListener('change',()=>{D.versionId=$('#dev-version').value;D.restore=null;$('#dev-version-diff').hidden=true;renderStatus();});
  $('#dev-diff').addEventListener('click',event=>action(event.target,async()=>{await save();const result=await api(endpoint('/versions/'+encodeURIComponent(D.versionId)+'/diff'));$('#dev-version-diff').textContent=result.diff||'선택한 버전과 현재 작업 파일이 같습니다.';$('#dev-version-diff').hidden=false;}));
  $('#dev-rollback').addEventListener('click',event=>action(event.target,async()=>{if(!window.confirm('현재 작업 파일을 선택한 코드 버전으로 되돌릴까요? 미저장 편집도 바뀝니다.'))return;await post('/restore',{version_id:D.versionId,base_sha256:D.status.workspace_sha256});await loadFile(D.path,true);$('#dev-version-diff').hidden=true;flash('작업 파일을 선택한 버전으로 복원했습니다. 실행 중인 프로세스는 그대로입니다.');}));
  $('#dev-capture').addEventListener('click',event=>action(event.target,capture));$('#run-capture-case').addEventListener('click',event=>action(event.target,capture));
  $('#dev-case-select').addEventListener('change',event=>action(event.target,()=>loadCase(event.target.value)));
  $('#dev-restore-case').addEventListener('click',event=>action(event.target,async()=>{const result=await post('/cases/'+encodeURIComponent(D.caseId)+'/restore',{code_version_id:D.versionId,run:false});const runtime=result.runtime||D.scene.runtime;state().runtime=runtime;state().runtimeInitialized=true;$('#runtime-mode').value=runtime;D.restore={id:D.caseId,version:D.versionId,runtime,deploymentId:result.deployment?.id};$('#dev-retest-status').textContent='장면 복원·코드 버전 준비 완료. 로봇은 정지 상태입니다. 재시험 시작을 누르세요.';flash('저장한 위치·경기 상태를 복원하고 선택한 코드 버전을 준비했습니다.');}));
  $('#dev-retest').addEventListener('click',event=>action(event.target,async()=>{await refreshStatus();const d=state().summary?.deployment,expected=D.restore;if(!expected||d?.code_version_id!==expected.version||(expected.deploymentId&&d?.id!==expected.deploymentId)||state().runtime!==expected.runtime||state().physics?.runtime!==expected.runtime)throw new Error('복원 이후 실행 구성이 바뀌었습니다. 장면을 다시 복원하세요.');await api('/api/workbench/controller/start',{method:'POST',body:{expected_code_version_id:expected.version,expected_runtime:expected.runtime,expected_deployment_id:expected.deploymentId}});const runtime=expected.runtime;await api(`/api/admin/${runtime==='mission'?'':runtime+'/'}control`,{method:'POST',body:{action:'resume'}});$('#dev-retest-status').textContent='선택한 코드 버전으로 재시험 중입니다.';switchPage('run');flash('선택한 코드 버전으로 로봇 프로세스를 시작했습니다.');}));
  $('#dev-stop').addEventListener('click',async()=>{if(D.stopping)return;D.stopping=true;available();try{await post('/stop');D.restore=null;D.ros={running:false};D.rosLaunching=false;renderRos();$('#dev-apply-status').textContent='전체 정지 · 코드와 모델은 보관됩니다.';await refreshStatus();await refresh(true);flash('로봇·ROS 실습·시뮬레이터를 모두 정지했습니다.');}catch(error){showError(error);}finally{D.stopping=false;available();}});
  $('#dev-assistant-send').addEventListener('click',event=>action(event.target,async()=>{const prompt=$('#dev-assistant-prompt').value.trim();if(!prompt)throw new Error('관찰한 문제와 원하는 동작을 입력하세요.');await save();const job=await post('/assistant',{prompt,case_id:D.caseId||undefined});D.jobId=job.id;await loadJob(job.id);flash('현재 코드와 선택한 장면 기록으로 Codex에 요청했습니다.');}));
  $('#dev-assistant-cancel').addEventListener('click',event=>action(event.target,async()=>{await post('/assistant/'+encodeURIComponent(D.jobId)+'/cancel');await loadJob(D.jobId);}));
  $('#dev-assistant-apply').addEventListener('click',event=>action(event.target,async()=>{if(D.dirty)throw new Error('편집 중인 내용을 먼저 저장하세요.');const result=await post('/assistant/'+encodeURIComponent(D.jobId)+'/apply');await loadFile(D.path,true);D.versionId=result.version?.id||result.version_id||D.versionId;D.restore=null;$('#dev-version-diff').hidden=true;await loadJob(D.jobId);$('#dev-apply-status').textContent='Codex 수정안이 파일에 반영됐습니다. 아래 적용·실행을 누르면 로봇이 바뀝니다.';flash('파일 반영 완료. 현재 코드 적용·실행을 누르세요.');}));
  $('#dev-assistant-jobs').addEventListener('click',event=>{const button=event.target.closest('[data-assistant-job]');if(button)action(button,async()=>{D.jobId=button.dataset.assistantJob;await loadJob(D.jobId);});});
  $('#dev-jupyter').addEventListener('click',event=>{const tab=window.open('about:blank','maicon-jupyter');if(tab)tab.document.title='JupyterLab 시작 중';action(event.target,async()=>{try{const result=await post('/jupyter',{action:'start'});if(!result.url)throw new Error(result.error||'JupyterLab 주소를 받지 못했습니다.');if(tab)tab.location.href=result.url;else{const link=document.createElement('a');link.href=result.url;link.target='_blank';link.rel='noopener';link.textContent='JupyterLab 열기 ↗';$('#dev-error').replaceChildren('브라우저가 새 창을 차단했습니다. ',link);$('#dev-error').className='notice neutral';$('#dev-error').hidden=false;}flash('같은 작업 폴더를 사용하는 JupyterLab을 열었습니다.');}catch(error){if(tab)tab.close();throw error;}});});
  $('#dev-ros-jupyter').addEventListener('click',event=>{const tab=window.open('about:blank','maicon-ros-jupyter');if(tab)tab.document.title='ROS 2 JupyterLab 준비 중';action(event.target,async()=>{D.rosLaunching=true;renderRos();try{const result=await post('/ros-jupyter',{action:'start'});D.ros=result;if(!result.running||!result.url)throw new Error(result.detail||'ROS 2 JupyterLab이 아직 준비되지 않았습니다.');if(tab)tab.location.href=result.url;else{const link=document.createElement('a');link.href=result.url;link.target='_blank';link.rel='noopener';link.textContent='영상 실습 · ROS 2 열기 ↗';$('#dev-error').replaceChildren('브라우저가 새 창을 차단했습니다. ',link);$('#dev-error').className='notice neutral';$('#dev-error').hidden=false;}flash('영상의 8개 실습을 ROS 2 JupyterLab에서 엽니다.');}catch(error){if(tab)tab.close();throw error;}finally{D.rosLaunching=false;renderRos();}});});
  $('#dev-ros-jupyter-stop').addEventListener('click',event=>action(event.target,async()=>{D.ros=await post('/ros-jupyter',{action:'stop'});D.rosLaunching=false;renderRos();flash('ROS 실습을 종료했습니다. 로봇 프로세스를 다시 실행할 수 있습니다.');}));
  return {enter(){refresh(true).catch(showError);},onStatus(){renderRuntime();liveDiagnostics();if(['develop','deploy','run'].includes(state().page))refresh().catch(error=>{if(state().page==='develop')showError(error);});},get selectedVersion(){return $('#deploy-code').value;},get dirty(){return D.dirty;}};
}
