/* OpenCode Router dashboard application logic */
const $ = x => document.getElementById(x);
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[c]));

const TOKEN = document.querySelector('meta[name=router-token]').content;
let eventSource = null;
let refreshTimer = null;
let busyCount = 0;
let sortState = { key: null, dir: 'asc' };
let pageState = { events: 1, pageSize: 25 };
const rawCache = { summary: null };

// ---- Toast ----
function toast(m, err) {
  const e = document.createElement('div');
  e.className = 'toast' + (err ? ' err' : '');
  e.textContent = m;
  $('toasts').append(e);
  setTimeout(() => e.remove(), 4000);
}

// ---- Busy state ----
function setBusy(el, b) {
  if (b) {
    busyCount++;
    el.disabled = true;
    const spin = document.createElement('span');
    spin.className = 'spin';
    spin.setAttribute('aria-hidden', 'true');
    el.dataset.spin = spin;
    el.insertAdjacentElement('beforeend', spin);
  } else {
    el.disabled = false;
    if (el.dataset.spin) { el.dataset.spin.remove(); delete el.dataset.spin; }
    busyCount = Math.max(0, busyCount - 1);
  }
}

// ---- API ----
async function api(p, b) {
  const r = await fetch('/api/' + p, {
    method: b === undefined ? 'GET' : 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Router-Token': TOKEN },
    body: b === undefined ? undefined : JSON.stringify(b),
  });
  const d = await r.json();
  if (!r.ok) throw Error(d.error || r.statusText);
  return d;
}

const guard = f => async (...a) => {
  try { return await f(...a); }
  catch (e) { toast(e.message, 1); }
};

// ---- Confirm dialog ----
function openConfirm(title, msg, onOk) {
  $('confirmTitle').textContent = title;
  $('confirmMsg').textContent = msg;
  $('confirmDlg').showModal();
  $('confirmCancel').onclick = () => { $('confirmDlg').close(); };
  $('confirmOk').onclick = async () => {
    $('confirmDlg').close();
    await onOk();
  };
}

// ---- Theme ----
function applyTheme(t) {
  if (t === 'system') t = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  document.documentElement.dataset.theme = t === 'light' ? 'light' : '';
  $('stTheme').value = t === 'light' ? 'light' : (t === 'dark' ? 'dark' : 'system');
  try { localStorage.setItem('theme', t); } catch { }
}

// ---- SSE ----
function startSSE() {
  if (eventSource) { eventSource.close(); }
  eventSource = new EventSource('/api/stream?token=' + encodeURIComponent(TOKEN));
  eventSource.onmessage = ev => {
    try { render(JSON.parse(ev.data)); }
    catch { }
  };
  eventSource.onerror = () => {
    eventSource.close();
    eventSource = null;
    setTimeout(startSSE, 3000);
  };
}
function stopSSE() {
  if (eventSource) { eventSource.close(); eventSource = null; }
}
function poll() {
  if (eventSource) return;
  clearInterval(refreshTimer);
  refreshTimer = setInterval(load, ($('stRefresh') && +$('stRefresh').value || 5) * 1000);
  load();
}

// ---- Renderers ----
function renderModels(d) {
  const q = $('modelFilter').value.toLowerCase();
  let models = d.models.filter(m => m.model.toLowerCase().includes(q));
  const sort = sortState.key;
  if (sort && ['model', 'runs', 'success_rate', 'avg_latency', 'circuit_open'].includes(sort)) {
    models.sort((a, b) => {
      let av = a[sort], bv = b[sort];
      if (av == null) av = -1;
      if (bv == null) bv = -1;
      return sortState.dir === 'asc' ? av > bv ? 1 : -1 : av < bv ? 1 : -1;
    });
  }
  $('modelCount').textContent = models.length + ' model' + (models.length !== 1 ? 's' : '');
  $('models').innerHTML = models.map(x => `<tr>
    <td>${esc(x.model)}</td>
    <td>${x.runs}</td>
    <td><span class="bar"><i style="width:${(x.success_rate * 100).toFixed(0)}%"></i></span>${(x.success_rate * 100).toFixed(0)}%</td>
    <td>${x.avg_latency === null ? '—' : x.avg_latency.toFixed(2) + 's'}</td>
    <td>${esc(x.last_error || '—')}</td>
    <td class="${x.circuit_open ? 'warn' : 'ok'}">${x.circuit_open ? 'OPEN' : 'CLOSED'}</td>
  </tr>`).join('') || '<tr><td colspan="6">No telemetry</td></tr>';
}

function renderKeys(d) {
  const q = $('jobFilter').value.toLowerCase();
  $('keys').innerHTML = d.keys.filter(k => k.provider.toLowerCase().includes(q)).map(x => `<tr>
    <td>${esc(x.provider)}</td>
    <td>${x.slot}</td>
    <td class="${x.status === 'READY' ? 'ok' : x.status === 'COOLDOWN' ? 'warn' : 'muted'}">${esc(x.status)}</td>
    <td>${x.success}</td>
    <td>${x.failure}</td>
  </tr>`).join('') || '<tr><td colspan="5">No providers</td></tr>';
}

function renderEvents(d) {
  const q = $('eventFilter').value.toLowerCase();
  let ev = d.events.filter(e => e.task.toLowerCase().includes(q) || e.model.toLowerCase().includes(q) || e.error_category.toLowerCase().includes(q));
  ev = ev.slice().reverse();
  const ps = pageState.pageSize;
  const pages = Math.ceil(ev.length / ps) || 1;
  if (pageState.events > pages) pageState.events = pages;
  $('eventPages').textContent = pages;
  $('eventPage').value = pageState.events;
  const slice = ev.slice((pageState.events - 1) * ps, pageState.events * ps);
  $('events').innerHTML = slice.map(x => `<tr>
    <td>${esc(x.timestamp)}</td>
    <td>${esc(x.task)}</td>
    <td>${esc(x.model)}</td>
    <td class="${x.status === 'success' ? 'ok' : 'bad'}">${esc(x.status)}</td>
    <td>${esc(x.error_category || '—')}</td>
    <td>${Number(x.latency_seconds || 0).toFixed(2)}s</td>
  </tr>`).join('') || '<tr><td colspan="6">No attempts</td></tr>';
}

function renderJobs(d) {
  const q = $('jobFilter').value.toLowerCase();
  let jobs = (d.jobs || []).filter(j => j.task.toLowerCase().includes(q));
  jobs = jobs.slice().reverse();
  $('jobs').innerHTML = jobs.map(j => `<div class="panel" style="padding:10px;margin-bottom:8px">
    <div style="display:flex;justify-content:space-between;gap:8px">
      <div><b>${esc(j.task)}</b> · <span class="${j.status === 'success' ? 'ok' : j.status === 'failed' ? 'bad' : 'warn'}">${esc(j.status)}</span>
      <span class="muted" style="font-size:12px">${esc(j.started_at || '')} → ${esc(j.finished_at || '—')}</span></div>
      ${j.status === 'running' ? `<button class="secondary" data-cancel="${esc(j.id)}">Cancel</button>` : ''}
    </div>
    <details><summary style="cursor:pointer;margin-top:8px">Output (${(j.output || '').split('\n').length} lines)</summary>
    <pre style="margin-top:6px">${esc(j.output || '')}</pre></details>
  </div>`).join('') || '<span class="muted">No jobs</span>';
}

function render(d) {
  rawCache.summary = d;
  renderModels(d);
  renderKeys(d);
  renderEvents(d);
  renderJobs(d);
  $('attempts').textContent = d.events.length;
  $('rate').textContent = d.events.length ? ((d.events.filter(x => x.status === 'success').length / d.events.length * 100).toFixed(1) + '%') : '—';
  const lat = d.events.map(x => x.latency_seconds).filter(Number.isFinite);
  $('latency').textContent = lat.length ? (lat.reduce((a, b) => a + b, 0) / lat.length).toFixed(2) + 's' : '—';
  $('circuits').textContent = d.open_circuits;
  $('conn').textContent = 'Connected';
}

async function load() {
  try {
    const d = await api('summary');
    render(d);
  } catch (e) {
    $('conn').textContent = 'Disconnected';
  }
}

// ---- Actions ----
function makeExport(tableId, label) {
  return async (fmt) => {
    const t = $(tableId);
    const rows = [];
    for (const tr of t.querySelectorAll('tbody tr')) {
      rows.push(Array.from(tr.querySelectorAll('td')).map(td => td.textContent));
    }
    const head = Array.from(t.querySelectorAll('thead th')).map(th => th.textContent.replace(/[ ↕▲▼]/g, '').trim());
    const blob = new Blob([
      fmt === 'csv'
        ? [head.join(','), ...rows.map(r => r.map(c => c.includes(',') || c.includes('"') ? '"' + c.replace(/"/g, '""') + '"' : c).join(','))].join('\n')
        : JSON.stringify({ head, rows }, null, 2)
    ], { type: fmt === 'csv' ? 'text/csv' : 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = label + '-' + new Date().toISOString().slice(0, 16).replace(/[:-]/g, '') + '.' + fmt;
    a.click();
  };
}

$('jobs').onclick = guard(async (ev) => {
  const id = ev.target.dataset?.cancel;
  if (id) {
    openConfirm('Cancel job', 'Cancel this running job?', async () => {
      await api('cancel', { id });
      toast('Cancel requested');
      load();
    });
  }
});

const run = guard(async () => {
  if (!$('prompt').value.trim()) throw Error('Prompt required');
  const btn = $('run');
  setBusy(btn, true);
  try {
    const j = await api('run', {
      task: $('task').value,
      prompt: $('prompt').value,
      model: $('model').value.trim(),
      max_models: +$('max').value,
      timeout: +$('timeout').value,
    });
    $('output').textContent = 'Queued job ' + j.id;
    toast('Job queued');
    load();
  } finally {
    setBusy(btn, false);
  }
});

$('run').onclick = run;
$('prompt').onkeydown = e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') run(); };

$('plan').onclick = guard(async () => {
  setBusy($('plan'), true);
  try { $('output').textContent = (await api('plan', { task: $('task').value })).output; }
  finally { setBusy($('plan'), false); }
});

const loadConfig = guard(async () => {
  $('cfgModels').value = (await api('task-config?task=' + $('cfgTask').value)).models.join('\n');
});

$('cfgTask').onchange = loadConfig;
$('reload').onclick = loadConfig;

$('save').onclick = guard(async () => {
  openConfirm('Save route', 'Save this model order?', async () => {
    setBusy($('save'), true);
    try {
      await api('task-config', {
        task: $('cfgTask').value,
        models: $('cfgModels').value.split(/\r?\n/).map(x => x.trim()).filter(Boolean),
      });
      toast('Route saved');
      load();
    } finally {
      setBusy($('save'), false);
    }
  });
});

$('health').onclick = guard(async () => {
  setBusy($('health'), true);
  try { $('output').textContent = JSON.stringify(await api('health'), null, 2); }
  finally { setBusy($('health'), false); }
});

$('catalogue').onclick = guard(async () => {
  setBusy($('catalogue'), true);
  try {
    const d = await api('catalogue');
    $('output').textContent = d.output;
    $('models-list').innerHTML = d.output.split(/\s+/).filter(x => x.includes('/')).slice(0, 500).map(x => `<option value="${esc(x)}">`).join('');
  } finally {
    setBusy($('catalogue'), false);
  }
});

$('resetCircuits').onclick = guard(async () => {
  openConfirm('Reset circuits', 'Reset model circuit breakers?', async () => {
    setBusy($('resetCircuits'), true);
    try {
      toast((await api('reset-circuits', {})).message);
      load();
    } finally {
      setBusy($('resetCircuits'), false);
    }
  });
});

$('resetKeys').onclick = guard(async () => {
  openConfirm('Reset key pool', 'Reset key-pool cooldowns and counters?', async () => {
    setBusy($('resetKeys'), true);
    try {
      toast((await api('reset-keys', {})).message);
      load();
    } finally {
      setBusy($('resetKeys'), false);
    }
  });
});

$('regenToken').onclick = guard(async () => {
  openConfirm('Regenerate token', 'Regenerate the API token? All open pages will lose access until refreshed.', async () => {
    const d = await api('token/regen', {});
    toast(d.message);
    setTimeout(() => location.reload(), 800);
  });
});

// ---- Settings ----
$('theme').onclick = () => { applyTheme($('stTheme').value); };
$('settingsBtn').onclick = () => {
  $('settingsDlg').style.display = 'block';
  $('stToken').value = TOKEN;
};
$('stClose').onclick = () => { $('settingsDlg').style.display = 'none'; };
$('stRegen').onclick = guard(async () => {
  openConfirm('Regenerate token', 'Regenerate the API token? All open pages will lose access until refreshed.', async () => {
    const d = await api('token/regen', {});
    toast(d.message);
    $('stToken').value = TOKEN;
    setTimeout(() => location.reload(), 800);
  });
});
$('stTheme').onchange = () => applyTheme($('stTheme').value);
$('stRefresh').onchange = () => { poll(); };

// ---- Export ----
$('exportKeys').onclick = () => makeExport('keys', 'keys')('json');
$('exportKeysCsv').onclick = () => makeExport('keys', 'keys')('csv');
$('exportJobs').onclick = () => {
  const d = rawCache.summary;
  const blob = new Blob([JSON.stringify({ jobs: d.jobs }, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'jobs-' + new Date().toISOString().slice(0, 16).replace(/[:-]/g, '') + '.json';
  a.click();
};
$('exportJobsCsv').onclick = () => {
  const d = rawCache.summary;
  const lines = ['id,task,status,started_at,finished_at', ...((d.jobs || []).map(j => [j.id, j.task, j.status, j.started_at, j.finished_at].map(c => c && c.includes(',') ? '"' + c + '"' : c || '').join(',')))];
  const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'jobs-' + new Date().toISOString().slice(0, 16).replace(/[:-]/g, '') + '.csv';
  a.click();
};
$('exportEvents').onclick = () => makeExport('events', 'events')('json');
$('exportEventsCsv').onclick = () => makeExport('events', 'events')('csv');

// ---- Sorting ----
['model', 'runs', 'success_rate', 'avg_latency', 'circuit_open'].forEach(k => {
  const th = Array.from(document.querySelectorAll('th')).find(x => x.dataset.key === k);
  if (th) th.onclick = () => {
    sortState.key = k;
    sortState.dir = sortState.key === k && sortState.dir === 'asc' ? 'desc' : 'asc';
    if (rawCache.summary) renderModels(rawCache.summary);
  };
});

// ---- Filters ----
['modelFilter', 'jobFilter', 'eventFilter'].forEach(id => {
  $(id).oninput = () => {
    if (rawCache.summary) {
      renderModels(rawCache.summary);
      renderKeys(rawCache.summary);
      renderEvents(rawCache.summary);
      renderJobs(rawCache.summary);
    }
  };
});

$('eventPage').onchange = e => {
  pageState.events = +e.target.value;
  if (rawCache.summary) renderEvents(rawCache.summary);
};
$('eventPageSize').onchange = e => {
  pageState.pageSize = +e.target.value;
  pageState.events = 1;
  if (rawCache.summary) renderEvents(rawCache.summary);
};

// ---- Lifecycle ----
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    stopSSE();
    clearInterval(refreshTimer);
  } else {
    startSSE();
    poll();
  }
});
window.addEventListener('beforeunload', () => {
  stopSSE();
  clearInterval(refreshTimer);
});

applyTheme(localStorage.getItem('theme') || 'system');
startSSE();
poll();
loadConfig();
load();
