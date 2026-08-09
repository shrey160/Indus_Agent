/* Research module (Phase 9, SP-6): sidebar section + run list, new-run modal,
   live run view (SSE event log + tasks + sources), report view with
   citation popovers, and the composer chip.
   All rendering is DOM-built via el()/textContent — never innerHTML (XSS rule,
   MASTER_PLAN §7). SSE uses fetch + ReadableStream reader with last_event_id
   reconnect (EventSource can't send the id ergonomically); a disconnect never
   cancels the run (Phase 9 — deliberate inversion of the chat HP-004 rule). */

window.Research = (() => {
  const { el, toast } = window.UI;

  const TERMINAL = ['done', 'failed', 'cancelled', 'interrupted'];

  const STATUS_META = {
    queued: { dot: 'dot-pending', word: 'QUEUED' },
    planning: { dot: 'dot-run', word: 'PLANNING' },
    researching: { dot: 'dot-run', word: 'RESEARCHING' },
    writing: { dot: 'dot-run', word: 'WRITING' },
    verifying: { dot: 'dot-run', word: 'VERIFYING' },
    done: { dot: 'dot-ok', word: 'DONE' },
    failed: { dot: 'dot-err', word: 'FAILED' },
    cancelled: { dot: 'dot-grey', word: 'CANCELLED' },
    interrupted: { dot: 'dot-warn', word: 'INTERRUPTED' },
  };

  const TAG = {
    status: { tag: 'STAT', cls: 'tag-amber' },
    error: { tag: 'ERR', cls: 'tag-err' },
    plan: { tag: 'PLAN', cls: 'tag-amber' },
    'plan.degraded': { tag: 'PLAN', cls: 'tag-err' },
    'task.start': { tag: 'TASK', cls: '' },
    search: { tag: 'SRCH', cls: 'tag-tool' },
    fetch: { tag: 'FETCH', cls: 'tag-tool' },
    'source.added': { tag: 'NOTE', cls: 'tag-tool' },
    reflect: { tag: 'RFLCT', cls: '' },
    'task.done': { tag: 'TASK', cls: 'tag-amber' },
    'report.delta': { tag: 'WRITE', cls: 'tag-amber' },
    verify: { tag: 'VERR', cls: 'tag-tool' },
    metrics: { tag: 'MTRS', cls: '' },
    'budget.exhausted': { tag: 'BUDG', cls: 'tag-err' },
    done: { tag: 'DONE', cls: 'tag-amber' },
  };

  let root = null;
  let runs = [];
  let pollTimer = null;
  let viewEl = null;
  let lastView = null;
  let viewTimer = null;

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function clock() {
    return new Date().toLocaleTimeString('en-GB', { hour12: false });
  }

  function fmtDur(createdAt, finishedAt) {
    const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
    const s = Math.max(0, Math.round((end - new Date(createdAt).getTime()) / 1000));
    return Math.floor(s / 60) + 'm' + String(s % 60).padStart(2, '0') + 's';
  }

  /* ── sidebar section ── */

  function summary() {
    if (!runs.length) return '';
    const running = runs.filter((r) => !TERMINAL.includes(r.status)).length;
    const done = runs.filter((r) => r.status === 'done').length;
    return running + ' running · ' + done + ' done';
  }

  function runCard(run) {
    const meta = STATUS_META[run.status] || { dot: 'dot-grey', word: run.status.toUpperCase() };
    const c = el('div', 'provider-card research-card');
    c.setAttribute('role', 'button');
    c.tabIndex = 0;
    const head = el('div', 'research-head');
    head.appendChild(el('span', 'dot ' + meta.dot));
    head.appendChild(el('span', 'research-status', meta.word));
    head.appendChild(el('span', 'research-depth', run.depth.toUpperCase()));
    head.appendChild(el('span', 'research-time', fmtDur(run.created_at, run.finished_at)));
    c.appendChild(head);
    c.appendChild(el('div', 'research-query', run.title || run.query));
    const counts = run.counts || {};
    const bits = [];
    if (counts.tasks) bits.push('task ' + (counts.tasks_done || 0) + '/' + counts.tasks);
    if (counts.sources) bits.push(counts.sources + ' sources');
    if (counts.notes) bits.push(counts.notes + ' notes');
    if (run.model) bits.push(run.model);
    if (bits.length) c.appendChild(el('div', 'research-meta', bits.join(' · ')));
    const open = () => openRunView(run.id);
    c.addEventListener('click', open);
    c.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        open();
      }
    });
    return c;
  }

  function pollTick() {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = null;
    if (!root) return;
    const section = root.closest('.sidebar-section');
    if (section && section.classList.contains('collapsed')) return;
    render(root);
  }

  async function render(body) {
    if (body) root = body;
    if (!root) return;
    root.textContent = '';
    root.appendChild(el('div', 'models-empty', 'CHECKING…'));
    let out = null;
    try {
      const res = await fetch('/api/research');
      if (!res.ok) {
        let detail = 'HTTP ' + res.status;
        try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
        throw new Error(detail);
      }
      out = await res.json();
      runs = Array.isArray(out) ? out : [];
    } catch (err) {
      runs = [];
      root.textContent = '';
      root.appendChild(el('div', 'provider-error', 'RESEARCH LOAD FAILED — ' + err.message));
      const actions = el('div', 'provider-actions');
      const retry = el('button', 'btn', '[ RETRY ]');
      retry.addEventListener('click', () => render(root));
      actions.appendChild(retry);
      root.appendChild(actions);
      window.Sidebar.refreshSummary('research');
      return;
    }
    root.textContent = '';
    window.Sidebar.refreshSummary('research');
    if (!runs.length) {
      root.appendChild(el('div', 'models-empty', 'NO RUNS — + TO START'));
      return;
    }
    for (const r of runs) root.appendChild(runCard(r));
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(pollTick, 5000);
  }

  async function initSummary() {
    try {
      const res = await fetch('/api/research');
      if (!res.ok) return;
      const out = await res.json();
      if (Array.isArray(out)) runs = out;
    } catch (err) {
      runs = [];
    }
    window.Sidebar.refreshSummary('research');
  }

  /* ── new-run modal (T2) ── */

  function openNewRunModal(opts) {
    opts = opts || {};
    const body = el('div');

    const query = el('textarea', 'modal-input research-query');
    query.rows = 4;
    query.placeholder = 'Research question…';
    query.setAttribute('aria-label', 'Research query');

    body.appendChild(el('label', 'research-caption', 'QUERY'));
    body.appendChild(query);

    let activeDepth = 'standard';
    const depthRow = el('div', 'research-depth-row');
    depthRow.setAttribute('role', 'radiogroup');
    body.appendChild(el('div', 'research-caption', 'DEPTH'));
    for (const [key, label] of [['quick', 'QUICK ~10MIN'], ['standard', 'STANDARD ~30MIN'], ['deep', 'DEEP ~60MIN']]) {
      const b = el('button', 'btn' + (key === activeDepth ? ' btn-primary' : ''), '[ ' + label + ' ]');
      b.type = 'button';
      b.dataset.depth = key;
      b.setAttribute('role', 'radio');
      b.setAttribute('aria-checked', key === activeDepth ? 'true' : 'false');
      b.addEventListener('click', () => {
        activeDepth = key;
        for (const bb of depthRow.querySelectorAll('button')) {
          bb.classList.toggle('btn-primary', bb.dataset.depth === key);
          bb.setAttribute('aria-checked', bb.dataset.depth === key ? 'true' : 'false');
        }
      });
      depthRow.appendChild(b);
    }
    body.appendChild(depthRow);

    let smartPick = null;
    try { smartPick = JSON.parse(localStorage.getItem('research.smartModel') || 'null'); } catch (e) { smartPick = null; }
    let smartOpen = false;
    let smartDismiss = null;

    const smartRow = el('div', 'research-smart-row');
    smartRow.appendChild(el('span', 'research-caption', 'SMART MODEL'));
    const selBtn = el('button', 'btn research-smart-select', '');
    selBtn.type = 'button';
    selBtn.setAttribute('aria-label', 'Choose smart role model');
    smartRow.appendChild(selBtn);
    const smartPanel = el('div', 'research-smart-dropdown hidden');
    body.appendChild(smartRow);
    body.appendChild(smartPanel);

    function smartLabel() {
      return smartPick ? '[ ' + smartPick.label + ' ▾ ]' : '[ AUTO ▾ ]';
    }

    function closeSmartPanel() {
      smartOpen = false;
      smartPanel.classList.add('hidden');
      if (smartDismiss) {
        document.removeEventListener('click', smartDismiss.onDoc);
        document.removeEventListener('keydown', smartDismiss.onEsc, true);
        smartDismiss = null;
      }
    }

    function setSmartPick(pick) {
      smartPick = pick;
      if (pick) localStorage.setItem('research.smartModel', JSON.stringify(pick));
      else localStorage.removeItem('research.smartModel');
      selBtn.textContent = smartLabel();
      closeSmartPanel();
    }

    function smartGroup(holder, title) {
      const group = el('div', 'dropdown-group');
      group.appendChild(document.createTextNode('── '));
      group.appendChild(el('b', '', title));
      group.appendChild(document.createTextNode(' ' + '─'.repeat(Math.max(2, 24 - title.length))));
      holder.appendChild(group);
    }

    function smartPrice(m) {
      if (!m.pricing || m.pricing.prompt === undefined || m.pricing.prompt === null) return null;
      const per_m = parseFloat(m.pricing.prompt) * 1e6;
      return Number.isFinite(per_m) ? '$' + per_m.toFixed(2) + '/M' : null;
    }

    function smartSize(bytes) {
      const gb = bytes / 1e9;
      return gb >= 0.1 ? gb.toFixed(1) + ' GB' : Math.round(bytes / 1e6) + ' MB';
    }

    function smartModelRow(holder, provider, m) {
      const item = el('div', 'dropdown-model');
      item.appendChild(el('span', '', m.id));
      const meta = el('span', 'model-meta');
      const price = smartPrice(m);
      if (price) meta.appendChild(document.createTextNode(price));
      if (m.is_free) meta.appendChild(el('span', 'chip-free', 'free'));
      if (m.size_bytes) meta.appendChild(document.createTextNode(' ' + smartSize(m.size_bytes)));
      item.appendChild(meta);
      item.addEventListener('click', () => {
        setSmartPick({ provider_id: provider.id, model: m.id, label: provider.name + ' · ' + m.id });
      });
      holder.appendChild(item);
    }

    async function openSmartPanel() {
      if (smartOpen) {
        closeSmartPanel();
        return;
      }
      smartOpen = true;
      smartPanel.textContent = '';
      const auto = el('div', 'dropdown-model');
      auto.appendChild(el('span', '', 'AUTO (POLICY)'));
      auto.appendChild(el('span', 'model-meta', 'policy default'));
      auto.addEventListener('click', () => setSmartPick(null));
      smartPanel.appendChild(auto);
      try {
        const res = await fetch('/api/providers');
        if (!res.ok) {
          smartPanel.appendChild(el('div', 'dropdown-group', 'PROVIDERS FAILED — HTTP ' + res.status));
        } else {
          const providers = await res.json();
          const locals = [];
          const clouds = [];
          for (const p of providers) {
            if (p.status.state === 'down' || p.status.state === 'unreachable' || p.status.state === 'bad_key' || p.status.state === 'no_credits') continue;
            if (!p.status.models || p.status.models.length === 0) continue;
            (p.kind === 'cloud' ? clouds : locals).push(p);
          }
          let any = false;
          if (locals.length) {
            smartGroup(smartPanel, 'LOCAL');
            for (const p of locals) {
              smartGroup(smartPanel, p.name + ' · ' + p.status.models.length);
              for (const m of p.status.models) smartModelRow(smartPanel, p, m);
            }
            any = true;
          }
          if (clouds.length) {
            smartGroup(smartPanel, '☁ CLOUD');
            for (const p of clouds) {
              smartGroup(smartPanel, p.name + ' · ' + p.status.models.length);
              for (const m of p.status.models) smartModelRow(smartPanel, p, m);
            }
            any = true;
          }
          if (!any) {
            const empty = el('div', 'dropdown-group');
            empty.appendChild(el('b', '', 'NO MODELS AVAILABLE'));
            smartPanel.appendChild(empty);
          }
        }
      } catch (err) {
        smartPanel.appendChild(el('div', 'dropdown-group', 'PROVIDERS UNREACHABLE — ' + err.message));
      }
      if (!smartOpen) return;
      smartPanel.classList.remove('hidden');
      const onDoc = (ev) => {
        if (selBtn.contains(ev.target) || smartPanel.contains(ev.target)) return;
        closeSmartPanel();
      };
      const onEsc = (ev) => {
        ev.stopPropagation();
        closeSmartPanel();
      };
      smartDismiss = { onDoc, onEsc };
      setTimeout(() => {
        document.addEventListener('click', onDoc);
        document.addEventListener('keydown', onEsc, true);
      }, 0);
    }

    selBtn.textContent = smartLabel();
    selBtn.addEventListener('click', openSmartPanel);

    const actions = el('div', 'modal-actions');
    const cancelBtn = el('button', 'btn', '[ CANCEL ]');
    const startBtn = el('button', 'btn btn-primary', '[ START ]');
    actions.appendChild(cancelBtn);
    actions.appendChild(startBtn);
    body.appendChild(actions);

    window.UI.openModal('NEW RESEARCH RUN', body);
    cancelBtn.addEventListener('click', () => window.UI.closeModal());

    startBtn.addEventListener('click', async () => {
      const q = query.value.trim();
      if (!q) {
        toast('QUERY REQUIRED', 'error');
        query.focus();
        return;
      }
      startBtn.disabled = true;
      try {
        const payload = { query: q, depth: activeDepth, model_policy: 'local_only' };
        if (smartPick) payload.model_override = { provider_id: smartPick.provider_id, model: smartPick.model };
        if (opts.conversationId) payload.conversation_id = opts.conversationId;
        const res = await fetch('/api/research', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(payload),
        });
        let out = {};
        try { out = await res.json(); } catch (e) { /* keep */ }
        if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
        toast('RUN QUEUED', 'ok');
        window.UI.closeModal();
        if (root) render(root);
        openRunView(out.run_id);
        if (opts.conversationId) {
          if (window.Chat && window.Chat.addSysLine) {
            window.Chat.addSysLine('RESEARCH STARTED ▸ ' + q);
          }
          document.dispatchEvent(new CustomEvent('hub:conversation'));
        }
      } catch (err) {
        toast('START FAILED — ' + err.message, 'error');
        startBtn.disabled = false;
      }
    });
  }

  /* ── run view (T3): live SSH log + tasks + sources + controls ── */

  function fmtPayload(kind, payload) {
    switch (kind) {
      case 'search':
        return '"' + (payload.query || '') + '"' + (payload.results != null ? ' · ' + payload.results + ' results' : '');
      case 'fetch':
        return (payload.ok ? 'ok' : 'FAIL') + (payload.cached ? ' (cached)' : '') +
          ' · ' + (payload.url || '') + (payload.chars != null ? ' · ' + payload.chars + 'c' : '');
      case 'source.added':
        return '[' + payload.n + '] ' + (payload.title || '') + ' — ' + (payload.domain || payload.url || '');
      case 'task.start':
        return 'task ' + payload.idx + ': ' + String(payload.question || '').slice(0, 64);
      case 'task.done':
        return 'task ' + payload.idx + ': ' + String(payload.summary || '').slice(0, 72) +
          (payload.sources != null ? ' · ' + payload.sources + ' src' : '') +
          (payload.notes != null ? ' · ' + payload.notes + ' notes' : '');
      case 'reflect':
        return 'task ' + payload.idx + ' iter ' + payload.iteration + ' · ' + (payload.coverage || '');
      case 'plan':
        return (payload.tasks ? payload.tasks.length + ' tasks' : '') + ' · ' + (payload.title || '');
      case 'report.delta':
        return 'section: ' + (payload.section || '') + ' (' + String(payload.text || '').length + 'c)';
      case 'verify':
        return 'checked ' + payload.checked + ' · unsupported ' + payload.unsupported + ' · fixed ' + payload.fixed;
      case 'metrics': {
        const m = payload.metrics || {};
        const t = m.tokens || {};
        return (payload.stage || '') + ' · tokens ' + t.prompt + '/' + t.completion +
          (m.estimated ? ' (est)' : '') + ' · cost $' + Number(m.cost_usd || 0).toFixed(4) +
          (m.llm_calls != null ? ' · ' + m.llm_calls + ' calls' : '');
      }
      case 'status':
        return '→ ' + (payload.status || '') + (payload.detail ? ' — ' + payload.detail : '');
      case 'error':
        return (payload.tool ? payload.tool + ': ' : '') + (payload.detail || payload.stage || '') + (payload.retryable ? ' (retryable)' : '');
      case 'plan.degraded':
        return payload.reason || '';
      case 'budget.exhausted':
        return (payload.guard || 'guard') + ' exhausted';
      case 'done':
        return payload.report_path || '';
      default:
        return JSON.stringify(payload || {}).slice(0, 96);
    }
  }

  function logEvent(kind, payload) {
    const L = lastView;
    if (!L || L.report) return;
    const meta = TAG[kind] || { tag: String(kind).slice(0, 4).toUpperCase(), cls: '' };
    const line = el('div', 'research-log-line');
    line.appendChild(el('span', 'log-ts', clock()));
    line.appendChild(el('span', 'research-tag' + (meta.cls ? ' ' + meta.cls : ''), meta.tag));
    line.appendChild(el('div', 'research-log-content', fmtPayload(kind, payload)));
    L.logEl.appendChild(line);
    L.logEl.scrollTop = L.logEl.scrollHeight;
  }

  function renderTasks(tasks) {
    const L = lastView;
    if (!L || !tasks) return;
    L.tasksEl.textContent = '';
    for (const t of tasks) {
      const dot = t.status === 'done' ? 'dot-ok'
        : t.status === 'skipped' || t.status === 'failed' ? 'dot-err'
        : t.status === 'running' ? 'dot-run' : 'dot-grey';
      const row = el('div', 'research-task');
      row.appendChild(el('span', 'dot ' + dot));
      row.appendChild(el('span', '', 'T' + (t.idx || '?') + ' · ' + String(t.question || '').slice(0, 44)));
      if (t.summary) row.title = t.summary;
      L.tasksEl.appendChild(row);
    }
  }

  function renderSources(sources) {
    const L = lastView;
    if (!L || !sources) return;
    L.sourcesEl.textContent = '';
    if (!sources.length) {
      L.sourcesEl.appendChild(el('div', 'research-task', '(none yet)'));
      return;
    }
    for (const s of sources) {
      const row = el('div', 'research-src');
      row.appendChild(el('span', 'cite', '[' + s.n + ']'));
      const title = s.title || s.domain || s.url;
      row.appendChild(el('span', '', String(title).slice(0, 40) + (s.fetch_status === 'failed' ? ' (FAILED)' : '')));
      row.title = s.url || '';
      L.sourcesEl.appendChild(row);
    }
  }

  async function loadDetailOnce(runId) {
    const L = lastView;
    if (!L || L.runId !== runId || L.report) return;
    let detail = null;
    try {
      const res = await fetch('/api/research/' + runId);
      if (!res.ok) return;
      detail = await res.json();
    } catch (err) {
      return;
    }
    if (!lastView || lastView !== L) return;
    const meta = STATUS_META[detail.status] || { dot: 'dot-grey', word: detail.status.toUpperCase() };
    L.statusDot.className = 'dot ' + meta.dot;
    L.statusWord.textContent = meta.word;
    if (detail.model) L.modelEl.textContent = 'MODEL ' + detail.model;
    else L.modelEl.textContent = '';
    renderTasks(detail.tasks || []);
    const terminal = TERMINAL.includes(detail.status);
    L.cancelBtn.disabled = terminal;
    L.resumeBtn.classList.toggle('hidden', !(detail.status === 'failed' || detail.status === 'interrupted'));
    L.delBtn.classList.toggle('hidden', !terminal);
    L.reportBtn.classList.toggle('hidden', detail.status !== 'done');
    try {
      const sres = await fetch('/api/research/' + runId + '/sources');
      if (sres.ok) {
        const sources = await sres.json();
        if (lastView === L) renderSources(sources);
      }
    } catch (err) { /* non-fatal */ }
    if (terminal) L.terminal = true;
  }

  async function detailLoop(runId) {
    while (lastView && lastView.runId === runId && !lastView.report) {
      await loadDetailOnce(runId);
      await sleep(3000);
    }
  }

  async function watchStream(runId) {
    while (lastView && lastView.runId === runId && !lastView.report) {
      const L = lastView;
      if (L.terminal) {
        await sleep(1000);
        continue;
      }
      try {
        const res = await fetch('/api/research/' + runId + '/stream?last_event_id=' + L.lastId);
        if (!res.ok) {
          await sleep(1000);
          continue;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          if (!lastView || lastView !== L) {
            reader.cancel();
            return;
          }
          buffer += decoder.decode(value, { stream: true });
          let idx;
          while ((idx = buffer.indexOf('\n\n')) >= 0) {
            const chunk = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            const m = chunk.match(/^id: (\d+)\ndata: (.+)$/);
            if (!m) continue;
            const id = parseInt(m[1], 10);
            if (id <= L.lastId) continue;
            L.lastId = id;
            let payload;
            try { payload = JSON.parse(m[2]); } catch (e) { continue; }
            const kind = Object.keys(payload).find((k) => k !== 'ts');
            if (kind) logEvent(kind, payload[kind]);
          }
        }
        await sleep(300);
      } catch (err) {
        await sleep(1000);
      }
    }
  }

  function openRunView(runId) {
    if (viewEl) closeView();
    viewEl = el('div', 'research-view');
    const head = el('div', 'research-view-head');
    const back = el('button', 'btn', '[ ◀ BACK ]');
    back.setAttribute('aria-label', 'Back to research list');
    back.addEventListener('click', closeView);
    const title = el('span', 'research-view-title', 'RUN ' + String(runId).slice(0, 8));
    const statusEl = el('span', 'research-view-status');
    const statusDot = el('span', 'dot dot-pending');
    const statusWord = el('span', 'research-status-word', 'QUEUED');
    statusEl.appendChild(statusDot);
    statusEl.appendChild(statusWord);
    const modelEl = el('span', 'research-view-model', '');
    const actions = el('div', 'research-view-actions');
    const cancelBtn = el('button', 'btn', '[ CANCEL RUN ]');
    const resumeBtn = el('button', 'btn', '[ RESUME ]');
    const reportBtn = el('button', 'btn btn-primary', '[ REPORT ]');
    const delBtn = el('button', 'btn btn-danger', '[ DEL ]');
    cancelBtn.addEventListener('click', async () => {
      try {
        const res = await fetch('/api/research/' + runId + '/cancel', { method: 'POST' });
        let out = {};
        try { out = await res.json(); } catch (e) { /* keep */ }
        if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
        toast('CANCEL REQUESTED', 'ok');
      } catch (err) {
        toast('CANCEL FAILED — ' + err.message, 'error');
      }
    });
    resumeBtn.addEventListener('click', async () => {
      try {
        const res = await fetch('/api/research/' + runId + '/resume', { method: 'POST' });
        let out = {};
        try { out = await res.json(); } catch (e) { /* keep */ }
        if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
        toast('RUN RESUMED', 'ok');
        const L = lastView;
        if (L) L.terminal = false;
      } catch (err) {
        toast('RESUME FAILED — ' + err.message, 'error');
      }
    });
    reportBtn.addEventListener('click', () => openReportView(runId));
    delBtn.addEventListener('click', () => confirmDelete(runId));
    resumeBtn.classList.add('hidden');
    delBtn.classList.add('hidden');
    reportBtn.classList.add('hidden');
    actions.appendChild(cancelBtn);
    actions.appendChild(resumeBtn);
    actions.appendChild(reportBtn);
    actions.appendChild(delBtn);
    head.appendChild(back);
    head.appendChild(title);
    head.appendChild(statusEl);
    head.appendChild(modelEl);
    head.appendChild(actions);

    const cols = el('div', 'research-view-cols');
    const logCol = el('div', 'research-log');
    const side = el('div', 'research-side');
    const tasksEl = el('div', 'research-tasks');
    const sourcesEl = el('div', 'research-sources');
    side.appendChild(el('div', 'research-side-title', 'TASKS'));
    side.appendChild(tasksEl);
    side.appendChild(el('div', 'research-side-title', 'SOURCES'));
    side.appendChild(sourcesEl);
    cols.appendChild(logCol);
    cols.appendChild(side);
    viewEl.appendChild(head);
    viewEl.appendChild(cols);
    document.body.appendChild(viewEl);

    lastView = { runId, logEl: logCol, tasksEl, sourcesEl, statusEl, statusDot, statusWord, modelEl, cancelBtn, resumeBtn, delBtn, reportBtn, lastId: 0, terminal: false, report: false };
    detailLoop(runId);
    watchStream(runId);
  }

  function confirmDelete(runId) {
    const body = el('div');
    body.appendChild(el('p', 'modal-body-text', 'DELETE RUN — events, tasks and the report file will be removed.'));
    const actions = el('div', 'modal-actions');
    const cancel = el('button', 'btn', '[ CANCEL ]');
    const del = el('button', 'btn btn-danger', '[ DELETE ]');
    actions.appendChild(cancel);
    actions.appendChild(del);
    body.appendChild(actions);
    window.UI.openModal('DELETE RUN ' + String(runId).slice(0, 8), body);
    cancel.addEventListener('click', () => window.UI.closeModal());
    del.addEventListener('click', async () => {
      window.UI.closeModal();
      try {
        const res = await fetch('/api/research/' + runId, { method: 'DELETE' });
        let out = {};
        try { out = await res.json(); } catch (e) { /* keep */ }
        if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
        toast('RUN DELETED', 'ok');
        closeView();
      } catch (err) {
        toast('DELETE FAILED — ' + err.message, 'error');
      }
    });
  }

  /* ── report view (T4): markdown allowlist + citations + download/print ── */

  let activeCitePopover = null;

  function closeCitePopover() {
    if (activeCitePopover) {
      activeCitePopover.remove();
      activeCitePopover = null;
    }
  }

  function citePopover(source, anchorRect) {
    closeCitePopover();
    const wrap = el('div', 'cite-popover');
    const title = el('div', 'cite-popover-title', (source.title || source.domain || source.url || 'source') + ' [' + source.n + ']');
    const snippet = el('div', 'cite-popover-snippet', source.excerpt || source.url || '');
    const url = el('a', 'cite-popover-snippet', source.url || '');
    url.href = source.url || '#';
    url.target = '_blank';
    url.rel = 'noopener noreferrer';
    wrap.appendChild(title);
    wrap.appendChild(snippet);
    if (source.url) wrap.appendChild(url);
    document.body.appendChild(wrap);
    activeCitePopover = wrap;
    const top = anchorRect.bottom + 6;
    const left = Math.min(anchorRect.left, window.innerWidth - 316);
    wrap.style.top = top + 'px';
    wrap.style.left = Math.max(4, left) + 'px';
    const onDocClick = (ev) => {
      if (!wrap.contains(ev.target)) {
        closeCitePopover();
        document.removeEventListener('click', onDocClick);
        document.removeEventListener('keydown', onEsc);
      }
    };
    const onEsc = (ev) => {
      if (ev.key === 'Escape') {
        closeCitePopover();
        document.removeEventListener('click', onDocClick);
        document.removeEventListener('keydown', onEsc);
      }
    };
    setTimeout(() => {
      document.addEventListener('click', onDocClick);
      document.addEventListener('keydown', onEsc);
    }, 0);
  }

  const INLINE_RE = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\[\d+\])/g;

  function inlineInto(parent, text) {
    const parts = String(text).split(INLINE_RE);
    for (const part of parts) {
      if (!part) continue;
      let m = part.match(/^\*\*([^*]+)\*\*$/);
      if (m) { parent.appendChild(el('strong', 'md-bold', m[1])); continue; }
      m = part.match(/^\*([^*]+)\*$/);
      if (m) { parent.appendChild(el('em', 'md-em', m[1])); continue; }
      m = part.match(/^`([^`]+)`$/);
      if (m) { parent.appendChild(el('code', 'md-code-inline', m[1])); continue; }
      m = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (m) {
        const a = el('a', 'md-link', m[1]);
        a.href = m[2];
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        parent.appendChild(a);
        continue;
      }
      m = part.match(/^\[(\d+)\]$/);
      if (m) {
        const idx = parseInt(m[1], 10);
        const sup = el('sup', 'cite md-cite', part);
        sup.addEventListener('click', (ev) => {
          const src = ((lastView && lastView.sources) || []).find((s) => s.n === idx);
          if (src) citePopover(src, ev.target.getBoundingClientRect());
        });
        parent.appendChild(sup);
        continue;
      }
      parent.appendChild(document.createTextNode(part));
    }
  }

  function inlineEl(tag, cls, text) {
    const node = el(tag, cls);
    inlineInto(node, text);
    return node;
  }

  function renderMarkdown(text) {
    const container = el('div', 'md-render');
    const lines = String(text).split('\n');
    let para = null;
    let listEl = null;
    let fence = null;
    const flushPara = () => { if (para) { container.appendChild(para); para = null; } };
    const flushList = () => { if (listEl) { container.appendChild(listEl); listEl = null; } };
    for (const raw of lines) {
      const line = raw;
      if (fence !== null) {
        if (/^```/.test(line)) {
          flushPara();
          fence = null;
        } else {
          fence.textContent += (fence.textContent ? '\n' : '') + line;
        }
        continue;
      }
      if (/^```/.test(line)) {
        flushPara();
        flushList();
        fence = el('pre', 'md-code');
        container.appendChild(fence);
        continue;
      }
      if (/^(---|\*\*\*)\s*$/.test(line)) {
        flushPara();
        flushList();
        container.appendChild(el('div', 'md-hr'));
        continue;
      }
      if (/^###\s/.test(line)) {
        flushPara();
        flushList();
        container.appendChild(inlineEl('h3', 'md-h3', line.replace(/^###\s*/, '')));
        continue;
      }
      if (/^##\s/.test(line)) {
        flushPara();
        flushList();
        container.appendChild(inlineEl('h2', 'md-h2', line.replace(/^##\s*/, '')));
        continue;
      }
      if (/^#\s/.test(line)) {
        flushPara();
        flushList();
        container.appendChild(inlineEl('h1', 'md-h1', line.replace(/^#\s*/, '')));
        continue;
      }
      if (/^\s*[-*]\s+/.test(line)) {
        flushPara();
        if (!listEl) { listEl = el('ul', 'md-list'); container.appendChild(listEl); }
        const li = el('li');
        inlineInto(li, line.replace(/^\s*[-*]\s+/, ''));
        listEl.appendChild(li);
        continue;
      }
      if (/^\s*\d+[.)]\s+/.test(line)) {
        flushPara();
        if (!listEl) { listEl = el('ol', 'md-list'); container.appendChild(listEl); }
        const li = el('li');
        inlineInto(li, line.replace(/^\s*\d+[.)]\s+/, ''));
        listEl.appendChild(li);
        continue;
      }
      if (!line.trim()) {
        flushPara();
        flushList();
        continue;
      }
      flushList();
      if (!para) {
        para = el('p', 'md-p');
        container.appendChild(para);
      }
      inlineInto(para, line);
    }
    flushPara();
    flushList();
    return container;
  }

  function openReportView(runId) {
    if (viewEl) closeView();
    viewEl = el('div', 'research-view research-report-view');
    const head = el('div', 'research-view-head');
    const back = el('button', 'btn', '[ ◀ BACK ]');
    back.setAttribute('aria-label', 'Back to research list');
    back.addEventListener('click', closeView);
    const title = el('span', 'research-view-title', 'REPORT ' + String(runId).slice(0, 8));
    const pathEl = el('span', 'research-view-path hidden', '');
    const actions = el('div', 'research-view-actions');
    const dl = el('button', 'btn', '[ DOWNLOAD .md ]');
    const rawBtn = el('button', 'btn', '[ RAW ]');
    const printBtn = el('button', 'btn', '[ PRINT ]');
    actions.appendChild(dl);
    actions.appendChild(rawBtn);
    actions.appendChild(printBtn);
    head.appendChild(back);
    head.appendChild(title);
    head.appendChild(pathEl);
    head.appendChild(actions);

    const bodyEl = el('div', 'research-report-body');
    viewEl.appendChild(head);
    viewEl.appendChild(bodyEl);
    document.body.appendChild(viewEl);

    lastView = { runId, report: true, sources: null };
    let rawText = null;
    let rawMode = false;
    let rawPre = null;
    let renderedEl = null;
    (async () => {
      try {
        const [res, srcRes, detRes] = await Promise.all([
          fetch('/api/research/' + runId + '/report'),
          fetch('/api/research/' + runId + '/sources'),
          fetch('/api/research/' + runId),
        ]);
        if (lastView !== null && lastView.runId === runId) {
          if (srcRes.ok) lastView.sources = await srcRes.json();
        }
        if (detRes.ok) {
          const det = await detRes.json();
          if (det.report_path) {
            pathEl.textContent = det.report_path;
            pathEl.classList.remove('hidden');
          } else {
            pathEl.classList.add('hidden');
          }
        }
        if (!res.ok) {
          let detail = 'HTTP ' + res.status;
          try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
          bodyEl.appendChild(el('div', 'provider-error', 'REPORT LOAD FAILED — ' + detail));
          return;
        }
        const text = await res.text();
        rawText = text;
        renderedEl = renderMarkdown(text);
        bodyEl.appendChild(renderedEl);
        bodyEl.scrollTop = 0;
      } catch (err) {
        bodyEl.appendChild(el('div', 'provider-error', 'REPORT LOAD FAILED — ' + err.message));
      }
    })();

    rawBtn.addEventListener('click', () => {
      if (!rawText || !renderedEl) return;
      if (rawMode) {
        if (rawPre && rawPre.parentNode) bodyEl.removeChild(rawPre);
        rawPre = null;
        renderedEl.classList.remove('hidden');
        rawMode = false;
        rawBtn.textContent = '[ RAW ]';
      } else {
        renderedEl.classList.add('hidden');
        rawPre = el('pre', 'md-raw');
        rawPre.textContent = rawText;
        bodyEl.appendChild(rawPre);
        rawMode = true;
        rawBtn.textContent = '[ RENDER ]';
      }
    });

    dl.addEventListener('click', async () => {
      try {
        const res = await fetch('/api/research/' + runId + '/report');
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const text = await res.text();
        const blob = new Blob([text], { type: 'text/markdown' });
        const a = el('a', 'hidden');
        a.href = URL.createObjectURL(blob);
        a.download = String(runId).slice(0, 8) + '.md';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      } catch (err) {
        toast('DOWNLOAD FAILED — ' + err.message, 'error');
      }
    });
    printBtn.addEventListener('click', () => window.print());
  }

  /* ── view lifecycle ── */

  function closeView() {
    closeCitePopover();
    lastView = null;
    if (viewTimer) clearInterval(viewTimer);
    viewTimer = null;
    if (viewEl) {
      viewEl.remove();
      viewEl = null;
    }
    if (root) render(root);
  }

  function isViewOpen() {
    return viewEl !== null;
  }

  /* ── registration ── */

  function init() {
    const chip = document.getElementById('research-chip');
    if (chip) {
      chip.addEventListener('click', () => {
        const convId = window.Chat && window.Chat.currentId ? window.Chat.currentId() : null;
        openNewRunModal({ conversationId: convId });
      });
    }
  }

  window.Sidebar.registerSection({
    id: 'research',
    title: 'Research',
    summary,
    action: { label: '+', title: 'New research run', run: () => openNewRunModal({}) },
    render,
    init: () => { initSummary(); init(); },
  });

  return { openNewRunModal, openRunView, openReportView, closeView, isViewOpen };
})();