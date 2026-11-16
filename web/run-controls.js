/** Route the workbench run entry to the single dashboard control surface. */
export function operatorDestination(search) {
  const params=new URLSearchParams(search);
  if(params.get('page')!=='run')return null;
  const runtime=params.get('runtime');
  return '/index.html?mode='+(['practice','rehearsal'].includes(runtime)?runtime:'mission');
}

export function matchControlState(s) {
  const runtime=s.runtime,physics=s.physics||{},deployment=s.summary?.deployment||{},controller=s.summary?.controller||{};
  const expected=deployment.mode==='match'?'mission':deployment.mode;
  const active=!!controller.pid&&!['failed','exited','stopped','interrupted','not_started'].includes(controller.status);
  const connected=!!physics.connected&&physics.running!==false&&physics.runtime===runtime;
  const ready=connected&&active&&controller.loaded&&controller.code_integrity===true&&expected===runtime;
  const loading=connected&&active&&!controller.loaded&&expected===runtime;
  const configured=!!deployment.model_id&&!!deployment.code_version_id;
  const snapshot=s.snapshot||{},phase=snapshot.phase,practice=runtime==='practice';
  const externalReady=!(snapshot.robots||[]).some(robot=>robot.strategy==='external'&&(robot.slot!==(deployment.slot||'B-1')||(!practice&&!robot.ready)));
  const locked=!s.connected||!!s.busy?.size||!!s.stoppingSession||!!s.rosNotebook?.running||!!s.rosNotebook?.starting;
  const restart=!ready&&!loading;
  const ongoing=['running','reporting'].includes(phase);
  const action=(visible,label,disabled=false)=>({visible:!!visible,label,disabled:locked||disabled});
  return {ready,loading,configured,restart,externalReady,actions:{
    prepare:action(!practice&&['idle','finished'].includes(phase),'경기 준비'),
    start:action(restart||(!practice&&phase==='ready'&&!snapshot.countdown_remaining),restart?(practice?'코드·경기장 실행':ongoing?'코드 적용 후 새 경기 시작':'코드·경기장 실행'):'경기 시작',loading||(restart&&!configured)||(!restart&&!externalReady)),
    pause:action(connected&&(practice||ongoing)&&!snapshot.paused,'일시정지',!connected),
    resume:action(ready&&snapshot.paused&&(practice||ongoing),'계속',!ready||!externalReady),
    finish:action(practice?connected:ongoing,practice?'자유연습 종료':'경기 종료'),
    reset:action(!!s.snapshot,'경기 초기화'),
  }};
}

/** Capture the selected runtime before any asynchronous work starts. */
export function matchActionRequest(action,s) {
  const status=matchControlState(s);
  if(['start','resume'].includes(action)&&!status.ready){
    if(status.loading)throw new Error('참가 코드와 인식 모델 준비가 끝날 때까지 기다리세요.');
    const d=s.summary?.deployment||{};
    if(!status.configured)throw new Error('실행 설정에서 인식 모델과 코드 버전을 먼저 준비하세요.');
    return {path:'/api/development/apply-run',launch:true,body:{mode:s.runtime==='mission'?'match':s.runtime,model_id:d.model_id,version_id:d.code_version_id,policy:d.policy||'mission',slot:d.slot||'B-1'}};
  }
  return {path:`/api/admin/${s.runtime==='mission'?'':s.runtime+'/'}control`,body:{action},launch:false};
}
