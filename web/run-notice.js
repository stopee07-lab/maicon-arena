/** Describe measured runtime state without rewriting participant decisions. */
export function describeRunState(state) {
  const p=state.physics||{},c=state.controller||{},d=state.deployment||{},snapshot=state.snapshot||{};
  const active=!!c.pid&&!['failed','exited','stopped','interrupted','not_started'].includes(c.status);
  if(p.error)return {kind:'error',title:'경기장이 멈췄습니다',body:p.error+' · 전체 정지 후 현재 코드 적용·실행으로 다시 시작하세요.'};
  if(p.warning)return {kind:'paused',title:'처리 지연으로 일시정지했습니다',body:p.warning+' · 화면과 센서는 계속 연결됩니다. 계속 버튼으로 재개할 수 있습니다.'};
  if(!p.connected)return {kind:'stopped',title:p.running?'경기장 연결 중…':'경기장 정지',body:'코드 수정 화면의 현재 코드 적용·실행으로 로봇과 경기장을 함께 시작하세요.'};
  if(snapshot.paused)return {kind:'paused',title:'경기 일시정지',body:'계속 버튼으로 재개하거나 전체 정지로 종료하세요.'};
  if(c.error)return {kind:'error',title:'참가자 프로그램 오류',body:String(c.error)};
  if(!active)return {kind:'stopped',title:'내 로봇 프로그램은 실행 전입니다',body:'경기장만 켜진 상태입니다. 코드 수정 → 현재 코드 적용·실행을 누르면 내 코드를 실행합니다.'};
  if(!c.loaded)return {kind:'loading',title:'인식 모델과 참가 코드를 준비 중입니다',body:'실제 가중치 로드와 첫 카메라 추론을 기다립니다.'};
  const events=(c.logs||[]).flatMap(line=>{try{return [JSON.parse(line)];}catch{return [];}});
  const lastOrder=events.filter(x=>['m1_unparsed','m1_constrained_order'].includes(x.event)).at(-1);
  if(d.policy==='mission'&&lastOrder?.event==='m1_unparsed')return {kind:'code',title:'내 UGV 정지 · 임무 명령 해석 실패',body:'실행 중인 참가 코드가 명령을 해석하지 못해 이동을 보류했습니다. 코드 수정 화면에서 이 명령을 처리하도록 수정해야 합니다. 원문: '+String(lastOrder.error||'해석 실패')};
  return {kind:'running',title:d.policy==='observe'?'인식만 실행 중 · 자동 주행 안 함':'참가자 코드 실행 중',body:d.policy==='observe'?'자동 이동은 인식 + 행동 코드로 실행할 때 사용합니다.':'코드는 반영됐습니다. 이동 여부와 판단은 내 로봇의 센서·주행 명령·실행 기록에서 확인하세요.'};
}
