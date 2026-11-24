import {element, pretty} from './client.js';

const labels = {READY:'通过', MISMATCH:'差异待复核', PENDING:'待复核', APPROVED:'已签发', HELD:'已暂缓'};
const ledgerLabels = {
  READY:'可签', PARTIAL:'部分在册', HELD:'搁置', NO_RETURNS:'无退库',
  MATCHED:'已配', DEFERRED:'截止后延后', available:'可用', future:'未来入库', invalid:'仓单异常',
};
const headers = ['业务分组', '输入行', '计算值', '参考值', '判定'];

export function badge(status) {
  return element('span', labels[status] || status, {class:'badge', 'data-result':status, title: status});
}

export function ledgerBadge(status) {
  return element('span', ledgerLabels[status] || status, {
    class:'badge', 'data-result':status, title:status,
  });
}

export function snapshot(report, selected, stage) {
  const stats = report.intake;
  document.querySelector('[data-metric="input"]').textContent = stats.input;
  document.querySelector('[data-metric="accepted"]').textContent = stats.accepted;
  document.querySelector('[data-metric="isolated"]').textContent = stats.rejected;
  document.querySelector('[data-metric="cases"]').textContent = report.cases?.length ?? '—';
  const table = document.querySelector('#rows');
  table.replaceChildren();
  const head = element('tr');
  const body = table.createTBody();
  if (stage === 'intake') {
    head.append(...['源行', '记录键', '业务分组', '原因码'].map(title => element('th', title, {scope:'col'})));
    for (const item of stats.isolation || []) {
      const tr = body.insertRow();
      tr.append(...[item.line,item.record,item.case,item.reason].map(value=>element('td',value)));
    }
    if (!(stats.isolation || []).length) body.insertRow().insertCell().textContent = '当前文件没有隔离行';
  } else {
    head.append(...headers.map(title => element('th', title, {scope:'col'})));
    for (const row of report.cases || []) {
      const tr = body.insertRow();
      const button = element('button',row.case,{type:'button','data-case':row.case,'aria-pressed':String(row.case===selected)});
      tr.insertCell().append(button);
      [row.records.length, row.measure?.value ?? '—', row.measure?.expected ?? '—'].forEach(value=>tr.insertCell().textContent=value);
      tr.insertCell().append(badge(row.status));
    }
  }
  table.createTHead().append(head);
  const picked = report.cases?.find(row=>row.case===selected);
  document.querySelector('#detail-title').textContent = picked ? `分组 ${picked.case}` : '采信摘要';
  const shown = picked ? picked.measure?.evidence ?? picked.prep?.details ?? {原因:picked.status} : stats.reasons;
  document.querySelector('#detail').textContent = pretty(shown);
  document.querySelector('#counts').textContent = report.balance ? `行数 ${report.balance.accounted}/${report.balance.input} · ${report.balance.ready} 组通过` : `采信 ${stats.accepted} 行 · 隔离 ${stats.rejected} 行`;
}

export function history(items, active) {
  const target = document.querySelector('#history');
  if (!target) return;
  target.replaceChildren();
  for (const run of items) {
    const button = element('button', run.id.slice(0,10), {'data-run':run.id,'aria-pressed':String(run.id===active),type:'button'});
    const item = element('li');
    item.append(button, badge(run.status));
    target.append(item);
  }
  if (!items.length) target.append(element('li','尚无执行批次'));
}

function cell(row, text, attrs) {
  const node = element('td', text, attrs);
  row.append(node);
  return node;
}

export function ledger(payload, activeVersion) {
  const status = document.querySelector('#ledger-status');
  const totals = document.querySelector('#ledger-totals');
  const table = document.querySelector('#ledger-rows');
  const diffBox = document.querySelector('#ledger-diff');
  const signForm = document.querySelector('#sign');
  if (!payload) {
    status.textContent = '尚未生成匹配簿';
    totals.replaceChildren(); table.replaceChildren(); diffBox.replaceChildren();
    document.querySelector('#ledger-receipts').replaceChildren();
    signForm.hidden = true;
    return;
  }
  const {book, blocking, diff} = payload;
  const previousLabel = payload.previous_label;
  const bar = blocking.issues.length ? blocking.issues.map(issue=>issue.detail).join('；') : '输入、采信、隔离与匹配行已逐项对平';
  status.textContent = `截止第 ${book.cutoff} 日 · ${bar}`;
  status.dataset.failure = String(!blocking.reconciled || !blocking.signable);

  const counts = [
    ['在册退库', book.totals.in_scope_returns],
    ['已配', book.totals.matched],
    ['搁置', book.totals.held],
    ['截止后延后', book.totals.deferred],
    ['冲减件数', book.totals.applied],
    ['未来入库', book.totals.future_receipts],
  ];
  totals.replaceChildren();
  for (const [label, value] of counts) {
    const group = element('div');
    group.append(element('dt', label), element('dd', String(value)));
    totals.append(group);
  }

  table.replaceChildren();
  const head = element('tr');
  head.append(...['退库', '批号/效期', '退库日', '件数', '匹配入库', '状态'].map(title => element('th', title, {scope:'col'})));
  const body = table.createTBody();
  for (const item of book.cases) {
    for (const line of item.lines) {
      const tr = body.insertRow();
      cell(tr, line.record);
      cell(tr, `${line.lot}/${line.expires}`);
      cell(tr, line.day);
      cell(tr, line.quantity);
      cell(tr, Object.entries(line.applied).map(([key,value])=>`${key}×${value}`).join('、') || '—');
      cell(tr, '').append(ledgerBadge(line.status));
    }
  }
  if (!body.rows.length) body.insertRow().insertCell().textContent = '当前截止点没有退库行';
  table.createTHead().append(head);

  const receipts = document.querySelector('#ledger-receipts');
  receipts.replaceChildren();
  const head2 = element('tr');
  head2.append(...['入库', '批号/效期', '入库日', '件数', '余量', '归属'].map(title => element('th', title, {scope:'col'})));
  const body2 = receipts.createTBody();
  for (const item of book.cases) {
    for (const entry of item.receipts) {
      const tr = body2.insertRow();
      cell(tr, entry.record);
      cell(tr, `${entry.lot}/${entry.expires}`);
      cell(tr, entry.day);
      cell(tr, entry.quantity);
      cell(tr, entry.remaining);
      cell(tr, '').append(ledgerBadge(entry.status));
    }
  }
  if (!body2.rows.length) body2.insertRow().insertCell().textContent = '当前截止点没有入库行';
  receipts.createTHead().append(head2);

  diffBox.replaceChildren();
  if (diff.previous) {
    diffBox.append(element('h3', `相对上一签版 ${previousLabel || diff.previous.slice(0,10)}`));
    const list = element('ul');
    const parts = [
      ['新增', diff.added], ['移除', diff.removed], ['匹配变化', diff.changed], ['改为延后', diff.deferred],
    ];
    for (const [label, keys] of parts) {
      list.append(element('li', `${label}：${keys.length ? keys.join('、') : '无'}`));
    }
    if (diff.status?.length) {
      list.append(element('li', `状态：${diff.status.map(item=>`${item.record} ${item.before}→${item.after}`).join('；')}`));
    }
    if (diff.receipts && (diff.receipts.added.length || diff.receipts.changed.length || diff.receipts.removed.length)) {
      list.append(element('li', `入库：新增 ${diff.receipts.added.join('、') || '无'}；余量变化 ${diff.receipts.changed.join('、') || '无'}；移除 ${diff.receipts.removed.join('、') || '无'}`));
    }
    diffBox.append(list);
  } else {
    diffBox.append(element('p', '这是首份签版，没有上一版可比。'));
  }

  if (blocking.records.length) {
    diffBox.append(element('p', `暂停发布，待处理：${blocking.records.join('、')}`, {class:'blocked'}));
  }
  signForm.hidden = !blocking.signable;
  document.querySelector('#sign-submit').disabled = !blocking.signable;
  const submit = document.querySelector('#sign-submit');
  submit.textContent = book.version
    ? '签发（同来源同截止日只回读原签版）'
    : '签发新版本';
  const link = document.querySelector('#version-evidence');
  link.hidden = !activeVersion;
  if (activeVersion) link.href = `/api/ledger/versions/${activeVersion}/evidence`;
}

export function versions(items, active) {
  const target = document.querySelector('#versions');
  if (!target) return;
  target.replaceChildren();
  for (const item of items) {
    const button = element('button', `${item.label} · 截止 ${item.cutoff}`, {
      type:'button', 'data-version':item.id, 'aria-pressed':String(item.id===active),
    });
    const entry = element('li');
    entry.append(button);
    if (item.previous) entry.append(element('span', `上一版 ${item.previous.slice(0,8)}`, {class:'muted'}));
    target.append(entry);
  }
  if (!items.length) target.append(element('li', '尚无签版'));
}
