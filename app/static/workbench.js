import {request} from './client.js';
import {snapshot, history, ledger, versions} from './view.js';
import {visualize} from './plots.js';

const form = document.querySelector('#source');
const file = document.querySelector('#file');
const profile = document.querySelector('#profile');
const status = document.querySelector('#notice');
const detail = document.querySelector('#review');
const evidence = document.querySelector('#evidence');
const cutoffInput = document.querySelector('#cutoff');
const signForm = document.querySelector('#sign');
const stages = [...document.querySelectorAll('[data-stage]')];
let stage = 'balance';
let report, selected, batch, running = false;
let book = null, activeVersion = null;

function source() {
  const data = new FormData();
  if (file.files.length) data.append('file',file.files[0]);
  else data.append('sample',profile.value);
  if (cutoffInput?.value !== '') data.append('cutoff', cutoffInput.value);
  return data;
}

function announce(text, failure = false) {
  status.textContent = text;
  status.dataset.failure = String(failure);
}

function draw() {
  if (!report) return;
  snapshot(report, selected, stage);
  visualize(report, selected);
  for (const button of stages) button.setAttribute('aria-pressed',String(button.dataset.stage===stage));
  document.querySelector('#panel-title').textContent = stages.find(button => button.dataset.stage===stage)?.textContent || '运行对账';
  ledger(book, activeVersion);
  if (detail) {
    detail.hidden = !batch;
    if (batch) {
      document.querySelector('#run-status').textContent = `${batch.id.slice(0,10)} · ${batch.status} · r${batch.revision}`;
      detail.querySelector('[value="APPROVED"]').disabled = !report.balance.releasable || batch.status==='APPROVED';
      detail.querySelector('[value="HELD"]').disabled = batch.status==='APPROVED';
      evidence.href = `/api/runs/${batch.id}/evidence`;
    }
  }
}

function show(payload, committed = false) {
  batch = committed ? payload : null;
  report = committed ? payload.report : payload;
  if (detail) detail.elements.note.value = batch?.note || '';
  selected = report.cases?.[0]?.case;
  draw();
}

async function refresh() {
  const data = await request('/api/runs');
  history(data.items, batch?.id);
  const signed = await request('/api/ledger/versions');
  versions(signed.items, activeVersion);
}

async function perform(action) {
  if (running) return;
  running = true;
  document.body.setAttribute('aria-busy','true');
  announce('处理中…');
  try { await action(); }
  catch (error) { announce(error.message,true); }
  finally { running = false; document.body.removeAttribute('aria-busy'); }
}

function preview(next) {
  return perform(async () => {
    const payload = await request(`/api/stages/${next}`,{method:'POST',body:source()});
    stage = next;
    show(payload);
    announce('阶段预览已更新');
    await refresh();
  });
}

async function previewBook() {
  const payload = await request('/api/ledger/preview',{method:'POST',body:source()});
  book = payload;
  if (cutoffInput && !cutoffInput.value) cutoffInput.value = String(payload.cutoff);
  return payload;
}

stages.forEach(button => button.addEventListener('click',()=>preview(button.dataset.stage)));
form.addEventListener('submit', event => {
  event.preventDefault();
  perform(async () => {
    const result = await request('/api/runs',{method:'POST',body:source()});
    stage = 'balance'; show(result,true);
    announce('批次已保存'); await refresh();
  });
});
const filename = document.querySelector('#file-name');
function sourceChanged() {
  activeVersion = null;
  if (cutoffInput) cutoffInput.value = '';
  preview(stage).then(()=>loadBook());
}
function loadBook() {
  return perform(async()=>{
    const payload = await previewBook();
    draw();
    announce(payload.blocking.signable ? '匹配簿可签发' : '匹配簿存在待处理记录', !payload.blocking.signable);
    await refresh();
  });
}
profile.addEventListener('change',()=>{file.value=''; filename.textContent=''; sourceChanged();});
file.addEventListener('change',()=>{filename.textContent=file.files[0]?.name || ''; sourceChanged();});
cutoffInput?.addEventListener('change',()=>loadBook());
document.querySelector('#rows').addEventListener('click',event=>{
  const button = event.target.closest('[data-case]');
  if (button) { selected = button.dataset.case; draw(); document.querySelector(`[data-case="${CSS.escape(selected)}"]`).focus(); }
});
document.querySelector('#history')?.addEventListener('click',event=>{
  const button = event.target.closest('[data-run]');
  if (button) perform(async()=>{
    const result = await request(`/api/runs/${button.dataset.run}`);
    stage='balance'; show(result,true); announce('已载入历史批次'); await refresh();
  });
});
detail?.addEventListener('submit',event=>{
  event.preventDefault();
  perform(async()=>{
    const payload={status:event.submitter.value,revision:batch.revision,note:detail.elements.note.value};
    const result=await request(`/api/runs/${batch.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    show(result,true); announce(result.status==='APPROVED'?'批次已签发':'批次已暂缓'); await refresh();
  });
});
document.querySelector('#preview-ledger')?.addEventListener('click',()=>{
  activeVersion = null;
  loadBook();
});
signForm?.addEventListener('submit',event=>{
  event.preventDefault();
  perform(async()=>{
    const data = source();
    data.append('note', signForm.elements.note.value);
    const version = await request('/api/ledger/versions',{method:'POST',body:data});
    activeVersion = version.id;
    book = {
      book: version.payload.book,
      blocking: {reconciled:true, signable:true, records:[], issues:[]},
      diff: version.payload.diff,
      previous_label: version.payload.previous_label,
    };
    cutoffInput.value = String(version.cutoff);
    signForm.elements.note.value = '';
    draw();
    announce(`已签发 v${version.seq} · 截止第 ${version.cutoff} 日`);
    await refresh();
  });
});
document.querySelector('#versions')?.addEventListener('click',event=>{
  const button = event.target.closest('[data-version]');
  if (button) perform(async()=>{
    const version = await request(`/api/ledger/versions/${button.dataset.version}`);
    activeVersion = version.id;
    cutoffInput.value = String(version.cutoff);
    book = {
      book: version.payload.book,
      blocking: {reconciled: version.payload.check.ok, signable:false,
        records: version.payload.check.records, issues: version.payload.check.issues},
      diff: version.payload.diff,
      previous_label: version.payload.previous_label,
    };
    draw();
    announce(`已回读 v${version.seq} 的签版依据`);
    await refresh();
  });
});
preview(stage).then(()=>loadBook());
