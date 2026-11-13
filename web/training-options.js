const $ = selector => document.querySelector(selector);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const percent = value => value == null || !Number.isFinite(Number(value)) ? '—' : (Number(value) * 100).toFixed(1) + '%';
const stamp = value => value ? new Date(Number(value) * 1000).toLocaleString('ko-KR', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '날짜 미기록';
const metric = model => model.metrics?.test?.['metrics/mAP50(B)'] ?? model.metrics?.test?.map50;
const testHash = model => model.evaluation_dataset_sha256 || (!model.evaluation_dataset_id || model.evaluation_dataset_id === model.dataset_id ? model.dataset_sha256 : null);

/** Model selection and repeated-training controls. Never launches jobs on render. */
export function createTrainingOptions({api, flash, refreshStatus, loadDataset, state}) {
  let latest = null, copying = false;
  let note = $('#train-source-note');
  if (!note) { note = document.createElement('p'); note.id = 'train-source-note'; note.className = 'small-note'; $('#train-base').closest('label').after(note); }
  let comparison = $('#training-comparison');
  if (!comparison) { comparison = document.createElement('div'); comparison.id = 'training-comparison'; $('#trained-models').after(comparison); }
  const copyButton = document.createElement('button');
  copyButton.id = 'clone-dataset'; copyButton.type = 'button'; copyButton.className = 'button secondary';
  copyButton.textContent = '라벨 수정용 복사';
  copyButton.title = '기존 학습 데이터는 보존하고, 라벨을 고칠 새 데이터셋을 만듭니다.';
  $('#freeze-dataset').before(copyButton);
  copyButton.addEventListener('click', async () => {
    const current = state().dataset;
    if (copying || !current || !current.frozen) return;
    copying = true; copyButton.disabled = true;
    try {
      const clone = await api(`/api/workbench/datasets/${encodeURIComponent(current.id)}/clone`, {method:'POST',body:{}});
      await refreshStatus();
      await loadDataset(clone.id);
      flash('원본을 보존한 수정본을 만들었습니다. 라벨을 고친 뒤 학습용으로 고정하세요.');
    } catch (error) { flash(error.message, true); }
    finally { copying = false; render(latest); }
  });
  $('#train-base').addEventListener('change', () => renderSourceNote());
  $('#train-dataset').addEventListener('change', () => render(latest));

  function renderSourceNote() {
    const source = latest?.training_sources?.find(x => x.id === $('#train-base').value);
    note.textContent = source?.kind === 'registered'
      ? '이전 모델에서 추가 학습합니다. 선택한 epoch만큼 새로 학습하고 별도 모델로 저장합니다. 이전 모델은 유지됩니다.'
      : source?.id === 'yolo26s.pt'
        ? 'YOLO26s는 n보다 큰 모델입니다. 시간·메모리가 더 들며, 성능 향상은 평가로 확인해야 합니다. 처음 선택하면 가중치를 다운로드합니다.'
        : 'YOLO26n 사전학습 모델에서 새로 학습합니다. 사진·라벨이나 설정을 바꿔 반복할 수 있으며, 매번 별도 모델로 저장합니다.';
  }

  function render(status) {
    if (!status) return;
    latest = status;
    const current = state().dataset;
    copyButton.hidden = !current?.frozen;
    copyButton.disabled = copying || !current?.frozen || current.status !== 'complete';
    const sources = status.training_sources || [{id:'yolo26n.pt',name:'YOLO26n · 빠른 실험용',kind:'pretrained'}];
    const dataset = (status.datasets || []).find(x => x.id === $('#train-dataset').value);
    const select = $('#train-base'), previous = select.value;
    const signature = JSON.stringify([sources,dataset?.classes]);
    if (select.dataset.trainingSources !== signature) {
      const renderGroup = (kind,label) => `<optgroup label="${label}">${sources.filter(x => x.kind === kind).map(source => {
        const incompatible = kind === 'registered' && dataset && JSON.stringify(source.classes) !== JSON.stringify(dataset.classes);
        const name = kind === 'registered' ? `${stamp(source.created_at)} · ${source.name}` : source.name;
        return `<option value="${escapeHTML(source.id)}" ${incompatible?'disabled':''}>${escapeHTML(name)}${incompatible?' · 물체 종류 다름':''}</option>`;
      }).join('')}</optgroup>`;
      select.innerHTML = renderGroup('pretrained','사전학습 모델에서 새로 시작') + renderGroup('registered','내가 학습한 모델에서 추가 학습');
      select.value = [...select.options].some(x => x.value === previous && !x.disabled) ? previous : 'yolo26n.pt';
      select.dataset.trainingSources = signature;
    }
    renderSourceNote();
    const models = (status.models || []).slice(0,6);
    comparison.innerHTML = models.length ? `<details class="training-compare"><summary>최근 학습 결과 비교 · ${models.length}개</summary><p class="small-note">같은 시험 데이터에서 평가한 결과끼리 비교하세요. 더 오래 학습해도 성능이 좋아진다고 보장되지는 않습니다.</p><div style="overflow-x:auto;max-width:100%"><table class="data-table"><thead><tr><th>모델 / 시각</th><th>시작점</th><th>epoch</th><th>시험 mAP50</th><th>평가 데이터</th></tr></thead><tbody>${models.map(model => {
      const parent = (status.models || []).find(x => x.id === model.parent_model_id);
      const comparable = parent && testHash(parent) && testHash(parent) === testHash(model) && JSON.stringify(parent.classes) === JSON.stringify(model.classes);
      const value = metric(model), before = metric(parent || {});
      const delta = comparable && value != null && before != null ? ` / 이전 대비 ${((value-before)*100).toFixed(1)}%p` : '';
      return `<tr><td>${escapeHTML(stamp(model.created_at))}<br><small>${escapeHTML(model.id.slice(-8))}</small></td><td>${escapeHTML(model.parent_model_id ? '내 모델 추가 학습' : model.settings?.model || '기록 없음')}</td><td>${escapeHTML(model.settings?.epochs ?? '—')}</td><td>${percent(value)}${escapeHTML(delta)}</td><td><small>${escapeHTML((model.evaluation_dataset_id || model.dataset_id || '').slice(-8))}</small></td></tr>`;
    }).join('')}</tbody></table></div></details>` : '';
    // Existing cards remain the selector for evaluation. Add context without changing that contract.
    for (const button of $('#trained-models').querySelectorAll('[data-model]')) {
      const model = (status.models || []).find(x => x.id === button.dataset.model);
      if (!model || button.querySelector('[data-training-origin]')) continue;
      const detail = document.createElement('span'); detail.dataset.trainingOrigin = 'true';
      detail.textContent = `${stamp(model.created_at)} · ${model.settings?.epochs ?? '—'} epoch · ${model.parent_model_id ? '이전 모델 추가 학습' : '사전학습 모델에서 시작'}`;
      button.append(detail);
    }
  }
  return {render};
}
