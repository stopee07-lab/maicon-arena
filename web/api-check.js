const $ = id => document.getElementById(id);
const names = {me:'내 로봇 상태 확인',ready:'준비 완료 알림',order:'첫 임무 받기',target:'새 목표 받기',interpretation:'M1 명령 해석 보고',detection:'M3 객체 보고',situation:'M6 상황 보고',drone:'드론 화면 받기'};
const phases = {idle:'경기 전',waiting:'대기',ready:'준비',running:'경기 중',reporting:'마지막 보고',finished:'경기 종료'};
let key = '', rows = [], selected = null, loading = false, lastRow = null;

function clock(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '시각 없음' : date.toLocaleTimeString('ko-KR', {hour12:false});
}
function describe(row) { return names[row.path.split('/').pop()] || '통신'; }
function visibleRows() {
  return rows.filter(row => $('filter').value === 'errors' ? row.status >= 400 :
    $('filter').value === 'reports' ? ['interpretation','detection','situation'].some(name => row.path.endsWith('/'+name)) : true);
}
function show(row) {
  selected = row.id;
  $('empty').hidden = true; $('details').hidden = false;
  $('selected-title').textContent = `${clock(row.at)} · ${describe(row)}`;
  const failed = row.status >= 400, waiting = !failed && row.response?.accepted === false;
  $('verdict').className = failed ? 'bad' : waiting ? 'waiting' : '';
  $('verdict-title').textContent = failed ? '거절 이유' : waiting ? '아직 받을 내용이 없습니다' : '서버가 답했습니다';
  $('reason').textContent = failed ? (typeof row.response?.detail === 'string' ? row.response.detail : JSON.stringify(row.response?.detail || row.response)) :
    waiting ? (row.response?.reason || '지금 단계에서는 새 임무나 결과가 아직 준비되지 않았습니다. 같은 답이 반복되면 경기 진행 상태를 확인하세요.') :
    row.response?.accepted === true ? '보고가 접수되었습니다. 접수 성공과 점수 획득은 다르므로 경기 결과도 확인하세요.' : '아래에서 실제로 돌아온 내용을 확인하세요.';
  $('request').textContent = JSON.stringify(row.request, null, 2);
  $('response').textContent = JSON.stringify(row.response, null, 2);
  $('technical').textContent = `${row.method} ${row.path} · HTTP ${row.status} · ${row.duration_ms} ms · 요청 당시 ${phases[row.phase] || row.phase}`;
  for (const button of $('records').children) {
    button.classList.toggle('active', button.dataset.id === String(selected));
    button.setAttribute('aria-pressed', String(button.dataset.id === String(selected)));
  }
}
function render() {
  const filtered = visibleRows();
  $('count').textContent = `${filtered.length}개 표시 / 최근 ${rows.length}개`;
  $('records').replaceChildren();
  if (!filtered.length) {
    const message = document.createElement('p'); message.className = 'empty';
    message.textContent = rows.length ? '선택한 종류의 기록이 없습니다.' : '아직 기록이 없습니다. 모의 경기에서 임무를 받거나 보고하면 이곳에 표시됩니다.';
    $('records').append(message); $('details').hidden = true; $('empty').hidden = false; return;
  }
  for (const row of filtered) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'record'+(row.status>=400?' bad':''); button.dataset.id = row.id;
    const label = document.createElement('span'); label.className = 'summary'; label.textContent = describe(row);
    const meta = document.createElement('span'); meta.className = 'meta'; meta.textContent = `${clock(row.at)} · ${row.status >= 400 ? '거절' : row.response?.accepted === false ? '대기' : '응답'} · HTTP ${row.status}`;
    button.append(label, meta); button.addEventListener('click', () => show(row)); $('records').append(button);
  }
  show(filtered.find(row => row.id === selected) || filtered[0]);
}
async function json(url, options={}) {
  const response = await fetch(url, {...options, signal:AbortSignal.timeout(8000)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '연결할 수 없습니다.');
  return data;
}
async function refresh(force=false) {
  if (loading || (document.hidden && !force)) return;
  loading = true; $('refresh').disabled = true;
  try {
    if (!key) key = (await json('/api/config')).admin_key;
    const data = await json('/api/admin/api-audit', {headers:{'X-Admin-Key':key}});
    rows = data.rows || [];
    if (force || lastRow !== (rows[0]?.id ?? 0)) {lastRow = rows[0]?.id ?? 0;render();}
    $('connection').className = ''; $('connection').textContent = '연결됨 · 새 기록은 자동으로 표시됩니다.';
    $('scope').textContent = `${data.scope} 최근 ${data.limit}개까지 보관합니다.${data.dropped ? ' 오래된 '+data.dropped+'개는 목록에서 빠졌습니다.' : ''} 서버를 끄면 지워집니다.`;
  } catch (error) {
    $('connection').className = 'error'; $('connection').textContent = `기록을 불러오지 못했습니다. ${error.message} 서버 실행을 확인한 뒤 새로고침을 누르세요.`;
  } finally {loading = false; $('refresh').disabled = false;}
}
$('refresh').addEventListener('click', () => refresh(true));
$('filter').addEventListener('change', render);
refresh(true); setInterval(refresh, 3000);
