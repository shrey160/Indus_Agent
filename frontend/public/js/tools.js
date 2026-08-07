window.Tools = (() => {
  const { el, toast } = window.UI;
  let root = null;
  let tools = [];

  const HEALTH = {
    ok: { dot: 'dot-ok', word: 'OK' },
    degraded: { dot: 'dot-warn', word: 'DEGRADED' },
    disabled: { dot: 'dot-grey', word: 'DISABLED' },
  };

  function summary() {
    if (!tools.length) return '';
    const on = tools.filter((t) => t.enabled).length;
    return on + '/' + tools.length + ' on';
  }

  function healthLine(t) {
    const h = HEALTH[t.health] || HEALTH.degraded;
    const wrap = el('div', 'tool-health');
    wrap.appendChild(el('span', 'dot ' + h.dot));
    const state = el('span', 'provider-state' + (t.health === 'degraded' ? ' state-warn' : ''), h.word + ' · ' + t.server);
    wrap.appendChild(state);
    if (t.name === 'rag.search' && window.Documents && window.Documents.stats) {
      const s = window.Documents.stats();
      const stats = el('span', 'tool-stats', ' · ' + s.docs + ' docs · ' + s.chunks + ' chunks');
      wrap.appendChild(stats);
    }
    return wrap;
  }

  function toggleLabel(t) {
    return t.enabled ? '[ ON ▸]' : '[◂ OFF ]';
  }

  async function toggle(t, btn) {
    btn.disabled = true;
    try {
      const res = await fetch('/api/tools/' + encodeURIComponent(t.name) + '/toggle', { method: 'POST' });
      let out = {};
      try { out = await res.json(); } catch (e) { /* keep */ }
      if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
      t.enabled = out.enabled;
      const enabledCount = tools.filter((x) => x.enabled).length;
      if (t.enabled && enabledCount > 10) {
        toast('TOOL LIMIT — 10+ ENABLED BLOATS CONTEXT', 'warn');
      } else {
        toast(t.name + (out.enabled ? ' ENABLED' : ' DISABLED'), 'ok');
      }
      await render();
    } catch (err) {
      toast('TOGGLE FAILED — ' + err.message, 'error');
      btn.disabled = false;
    }
  }

  function buildFields(schema) {
    const props = (schema && schema.properties) || {};
    const required = new Set((schema && schema.required) || []);
    const fields = [];
    for (const key of Object.keys(props)) {
      const spec = props[key] || {};
      const row = el('label', 'tool-field');
      row.appendChild(el('span', 'tool-field-name', key + (required.has(key) ? ' *' : '')));
      let input;
      if (spec.type === 'boolean') {
        input = el('input');
        input.type = 'checkbox';
        if (spec.default === true) input.checked = true;
      } else {
        input = el('input', 'add-input');
        input.type = (spec.type === 'integer' || spec.type === 'number') ? 'number' : 'text';
        if (spec.default !== undefined) input.value = spec.default;
        else if (spec.description) input.placeholder = spec.description;
      }
      row.appendChild(input);
      fields.push({ key, spec, input, required: required.has(key) });
    }
    return fields;
  }

  function collectArgs(fields) {
    const args = {};
    for (const f of fields) {
      if (f.spec.type === 'boolean') {
        args[f.key] = f.input.checked;
      } else if (f.spec.type === 'integer' || f.spec.type === 'number') {
        if (f.input.value === '') { if (f.required) throw new Error(f.key + ' is required'); continue; }
        const n = Number(f.input.value);
        if (!Number.isFinite(n)) throw new Error(f.key + ' must be a number');
        args[f.key] = f.spec.type === 'integer' ? Math.trunc(n) : n;
      } else {
        const v = f.input.value.trim();
        if (v === '') { if (f.required) throw new Error(f.key + ' is required'); continue; }
        args[f.key] = v;
      }
    }
    return args;
  }

  function formatResult(name, result) {
    if (name === 'web.search' && result && Array.isArray(result.results)) {
      return result.results.map((r, i) =>
        '[' + (i + 1) + '] ' + (r.title || '') + '\n    ' + (r.url || '') + (r.snippet ? '\n    ' + r.snippet : '')
      ).join('\n\n') || '(no results)';
    }
    if (name === 'web.fetch' && result && typeof result === 'object') {
      const head = [result.title || '', result.url || ''].filter(Boolean).join('\n');
      return (head ? head + '\n\n' : '') + (result.text || JSON.stringify(result, null, 2));
    }
    return JSON.stringify(result, null, 2);
  }

  function showResult(t, out, resultEl, footEl) {
    let raw = false;
    const draw = () => {
      resultEl.textContent = raw
        ? JSON.stringify(out.result, null, 2)
        : formatResult(t.name, out.result);
    };
    resultEl.classList.remove('tool-result-err');
    if (!out.ok) {
      resultEl.textContent = 'ERROR — ' + (out.error || 'unknown');
      resultEl.classList.add('tool-result-err');
      footEl.textContent = out.latency_ms + 'ms';
      return;
    }
    draw();
    footEl.textContent = '';
    footEl.appendChild(el('span', '', out.latency_ms + 'ms'));
    const rawBtn = el('button', 'btn', '[ RAW ]');
    rawBtn.addEventListener('click', () => {
      raw = !raw;
      rawBtn.textContent = raw ? '[ FORMATTED ]' : '[ RAW ]';
      draw();
    });
    footEl.appendChild(rawBtn);
  }

  async function runTest(t, fields, runBtn, resultEl, footEl) {
    let args;
    try {
      args = collectArgs(fields);
    } catch (err) {
      resultEl.textContent = 'ERROR — ' + err.message;
      resultEl.classList.add('tool-result-err');
      return;
    }
    runBtn.disabled = true;
    resultEl.classList.remove('tool-result-err');
    resultEl.textContent = 'RUNNING…';
    footEl.textContent = '';
    try {
      const res = await fetch('/api/tools/' + encodeURIComponent(t.name) + '/test', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ args }),
      });
      let out = {};
      try { out = await res.json(); } catch (e) { /* keep */ }
      if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
      showResult(t, out, resultEl, footEl);
    } catch (err) {
      resultEl.textContent = 'ERROR — ' + err.message;
      resultEl.classList.add('tool-result-err');
      footEl.textContent = '';
    } finally {
      runBtn.disabled = false;
    }
  }

  function testPanel(t) {
    const panel = el('div', 'test-panel hidden');
    const fields = buildFields(t.params_schema);
    if (!fields.length) panel.appendChild(el('div', 'models-empty', 'no parameters'));
    for (const f of fields) panel.appendChild(f.input.closest('.tool-field'));
    const actions = el('div', 'provider-actions');
    const runBtn = el('button', 'btn btn-primary', '[ RUN ]');
    actions.appendChild(runBtn);
    panel.appendChild(actions);
    const resultEl = el('div', 'test-result hidden');
    const footEl = el('div', 'tool-result-foot');
    panel.appendChild(resultEl);
    panel.appendChild(footEl);
    runBtn.addEventListener('click', () => {
      resultEl.classList.remove('hidden');
      runTest(t, fields, runBtn, resultEl, footEl);
    });
    return panel;
  }

  function card(t) {
    const c = el('div', 'provider-card');
    const head = el('div', 'provider-head');
    head.appendChild(el('span', 'provider-name', t.name));
    c.appendChild(head);
    c.appendChild(healthLine(t));
    if (t.description) c.appendChild(el('div', 'tool-desc', t.description));
    const actions = el('div', 'provider-actions');
    const toggleBtn = el('button', 'btn' + (t.enabled ? ' btn-primary' : ''), toggleLabel(t));
    toggleBtn.title = t.enabled ? 'Disable tool' : 'Enable tool';
    toggleBtn.addEventListener('click', () => toggle(t, toggleBtn));
    actions.appendChild(toggleBtn);
    const testBtn = el('button', 'btn', '[ ▶ TEST ]');
    actions.appendChild(testBtn);
    c.appendChild(actions);
    const panel = testPanel(t);
    c.appendChild(panel);
    testBtn.addEventListener('click', () => {
      const open = panel.classList.toggle('hidden');
      testBtn.textContent = open ? '[ ▶ TEST ]' : '[ ▼ TEST ]';
    });
    return c;
  }

  async function render(body) {
    if (body) root = body;
    if (!root) return;
    root.textContent = '';
    root.appendChild(el('div', 'models-empty', 'CHECKING…'));
    try {
      const res = await fetch('/api/tools');
      let out = [];
      if (!res.ok) {
        let detail = 'HTTP ' + res.status;
        try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
        throw new Error(detail);
      }
      tools = await res.json();
    } catch (err) {
      tools = [];
      root.textContent = '';
      root.appendChild(el('div', 'provider-error', 'TOOLS LOAD FAILED — ' + err.message));
      const actions = el('div', 'provider-actions');
      const retry = el('button', 'btn', '[ RETRY ]');
      retry.addEventListener('click', () => render());
      actions.appendChild(retry);
      root.appendChild(actions);
      window.Sidebar.refreshSummary('tools');
      return;
    }
    root.textContent = '';
    window.Sidebar.refreshSummary('tools');
    if (!tools.length) {
      root.appendChild(el('div', 'models-empty', 'NO TOOLS REGISTERED'));
      return;
    }
    for (const t of tools) root.appendChild(card(t));
  }

  async function initSummary() {
    try {
      const res = await fetch('/api/tools');
      if (!res.ok) {
        let detail = 'HTTP ' + res.status;
        try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
        throw new Error(detail);
      }
      tools = await res.json();
    } catch (err) {
      tools = [];
    }
    window.Sidebar.refreshSummary('tools');
  }

  window.Sidebar.registerSection({
    id: 'tools',
    title: 'Tools',
    summary,
    action: { label: '↻', title: 'Refresh tools', run: () => render() },
    render,
    init: initSummary,
  });
})();
