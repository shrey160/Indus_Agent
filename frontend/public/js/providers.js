(() => {
  const el = window.UI.el;
  const STATE_DOT = {
    up: 'dot-ok', up_empty: 'dot-warn',
    down: 'dot-err', bad_key: 'dot-err', no_credits: 'dot-err',
    unreachable: 'dot-grey', checking: 'dot-pending',
  };

  const HELP_TEXT = {
    ollama: [
      '1. Start Ollama from the Start Menu / system tray (for development, "ollama serve" in a terminal also works — but never both at once, or you get a duplicate server). It serves on port 11434.',
      '2. Pick a model: click [ MODELS ▾ ] above, then a model name. Cold activation loads it into memory — this can take up to a minute; "model still loading — first reply may be slow" is normal.',
      '3. Chat. Nothing loads until you actually send a message — reopening the app never auto-loads a model.',
      '4. Models unload themselves after ~5 minutes idle. To unload everything now: "ollama stop --all" (check with "ollama ps"). Extra "ollama.exe runner" processes are normal — one per loaded model.',
      'If this app shows Ollama down: set the system environment variable OLLAMA_HOST=0.0.0.0 and restart Ollama (required on Linux; on Windows Docker Desktop it usually works without it).',
      'See README → "Host networking" for details.',
    ],
    openai: [
      'Open LM Studio → Developer (Server) tab → Start Server.',
      'Enable "Serve on local network" so containers can reach it. Default port: 1234.',
      'Load a model in LM Studio before chatting — the server pings OK even with no model loaded, but chat will hang.',
      'See README → "Host networking" for details.',
    ],
  };

  function fmtPrice(m) {
    const pricing = m.pricing;
    if (!pricing || pricing.prompt === undefined || pricing.prompt === null) return null;
    const per_m = parseFloat(pricing.prompt) * 1e6;
    return Number.isFinite(per_m) ? '$' + per_m.toFixed(2) + '/M' : null;
  }

  function fmtCtx(n) {
    if (!n) return null;
    return n >= 1000 ? Math.round(n / 1000) + 'k' : String(n);
  }

  function formatSize(bytes) {
    if (!bytes) return '';
    const gb = bytes / 1e9;
    return gb >= 0.1 ? gb.toFixed(1) + ' GB' : Math.round(bytes / 1e6) + ' MB';
  }

  function stateWord(provider) {
    const s = provider.status;
    if (s.state === 'up') {
      const parts = ['UP'];
      if (s.latency_ms !== null && s.latency_ms !== undefined) parts.push(s.latency_ms + 'MS');
      parts.push(s.models.length + ' MODELS');
      if (provider.kind === 'cloud' && s.balance) parts.push('BAL ' + s.balance);
      return { text: parts.join(' · '), cls: '' };
    }
    if (s.state === 'up_empty') return { text: 'UP — NO MODELS', cls: 'state-warn' };
    if (s.state === 'bad_key') return { text: 'BAD KEY' + (s.error ? ' — ' + s.error.toUpperCase() : ''), cls: 'state-down' };
    if (s.state === 'no_credits') return { text: 'NO CREDITS — ' + (s.error || 'PAYMENT REQUIRED').toUpperCase(), cls: 'state-down' };
    if (s.state === 'unreachable') return { text: 'UNREACHABLE' + (s.error ? ' — ' + s.error.toUpperCase() : ''), cls: 'state-warn' };
    if (s.state === 'down') return { text: 'DOWN — ' + (s.error || 'UNREACHABLE'), cls: 'state-down' };
    return { text: 'CHECKING…', cls: '' };
  }

  async function api(path, options) {
    const res = await fetch(path, options);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
      throw new Error(detail);
    }
    return res.json();
  }

  async function activate(providerId, providerName, model, kind) {
    if (window.Chat && window.Chat.activateModel) {
      await window.Chat.activateModel(providerId, providerName, model, kind);
    }
  }

  function modelsPanel(card, provider) {
    const panel = el('div', 'models-panel');
    const filter = el('input', 'dropdown-filter');
    filter.type = 'text';
    filter.placeholder = 'Filter models…';
    filter.setAttribute('aria-label', 'Filter models');
    const list = el('ul', 'models-list', 'checking…');
    panel.appendChild(filter);
    panel.appendChild(list);
    let models = [];
    const renderList = () => {
      list.textContent = '';
      const n = filter.value.trim().toLowerCase();
      const shown = models.filter((m) => !n || m.id.toLowerCase().includes(n));
      if (shown.length === 0) {
        list.appendChild(el('li', 'models-empty', n ? 'no matches' : 'no models'));
        return;
      }
      for (const m of shown) {
        const item = el('li', 'model-item');
        const pin = el('button', 'btn model-pin', m.pinned ? '★' : '☆');
        pin.title = m.pinned ? 'Unpin' : 'Pin';
        pin.addEventListener('click', async (ev) => {
          ev.stopPropagation();
          try {
            const out = await api(`/api/providers/${provider.id}/favorite`, {
              method: 'POST',
              headers: { 'content-type': 'application/json' },
              body: JSON.stringify({ model_id: m.id }),
            });
            m.pinned = out.pinned;
            pin.textContent = out.pinned ? '★' : '☆';
          } catch (e) { /* keep */ }
        });
        const name = el('span', 'model-name', m.id);
        const meta = el('span', 'model-meta');
        const price = fmtPrice(m);
        if (price) meta.appendChild(document.createTextNode(price));
        if (m.is_free) meta.appendChild(el('span', 'chip-free', 'free'));
        if (m.context_length) meta.appendChild(document.createTextNode('ctx ' + fmtCtx(m.context_length)));
        if (!price && m.size_bytes) meta.appendChild(document.createTextNode(formatSize(m.size_bytes)));
        item.appendChild(pin);
        item.appendChild(name);
        item.appendChild(meta);
        item.addEventListener('click', async () => {
          await activate(provider.id, provider.name, m.id, provider.kind);
        });
        list.appendChild(item);
      }
    };
    api(`/api/providers/${provider.id}/models`)
      .then((ms) => { models = ms; renderList(); })
      .catch((err) => { list.textContent = err.message; });
    filter.addEventListener('input', renderList);
    return panel;
  }

  function testPanel(card, provider) {
    const panel = el('div', 'test-panel');
    const filter = el('input', 'dropdown-filter');
    filter.type = 'text';
    filter.placeholder = 'Filter model…';
    const select = el('select', 'test-model');
    select.appendChild(el('option', '', 'loading models…'));
    const prompt = el('input', 'test-prompt');
    prompt.type = 'text';
    prompt.value = 'Reply with one word: hello';
    const row = el('div', 'provider-actions');
    const run = el('button', 'btn btn-primary', '[ RUN ]');
    const raw = el('button', 'btn', '[ RAW ]');
    raw.classList.add('hidden');
    row.appendChild(run);
    row.appendChild(raw);
    const result = el('div', 'test-result', 'no run yet');
    let lastOut = null;
    let rawOn = false;
    panel.appendChild(filter);
    panel.appendChild(select);
    panel.appendChild(prompt);
    panel.appendChild(row);
    panel.appendChild(result);
    let allModels = [];
    const repopulate = () => {
      select.textContent = '';
      const n = filter.value.trim().toLowerCase();
      const shown = allModels.filter((m) => !n || m.id.toLowerCase().includes(n));
      if (shown.length === 0) { select.appendChild(el('option', '', 'no matches')); return; }
      for (const m of shown) select.appendChild(el('option', '', m.id));
      select.value = shown[0].id;
    };
    filter.addEventListener('input', repopulate);
    api(`/api/providers/${provider.id}/models`)
      .then((models) => {
        allModels = models;
        repopulate();
      })
      .catch((err) => { select.textContent = ''; select.appendChild(el('option', '', err.message)); });
    raw.addEventListener('click', () => {
      if (!lastOut) return;
      rawOn = !rawOn;
      result.textContent = rawOn ? JSON.stringify(lastOut, null, 2) : formatTestResult(lastOut);
    });
    run.addEventListener('click', async () => {
      result.textContent = 'running…';
      run.disabled = true;
      raw.classList.add('hidden');
      rawOn = false;
      lastOut = null;
      try {
        const out = await api(`/api/providers/${provider.id}/test`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ model: select.value, prompt: prompt.value }),
        });
        lastOut = out;
        raw.classList.remove('hidden');
        result.textContent = formatTestResult(out);
      } catch (err) {
        result.textContent = 'error: ' + err.message;
      } finally {
        run.disabled = false;
      }
    });
    return panel;
  }

  function formatTestResult(out) {
    return out.ok
      ? `${out.reply}\n(${out.latency_ms} ms)`
      : `error: ${out.error}`;
  }

  function helpPanel(provider) {
    const panel = el('div', 'help-panel');
    const lines = HELP_TEXT[provider.type] || [];
    for (const line of lines) panel.appendChild(el('p', 'help-line', line));
    return panel;
  }

  function editKeyModal(providerId, providerName) {
    const wrap = el('div');
    wrap.appendChild(el('p', 'privacy-note',
      'Update the API key for ' + (providerName || 'this cloud provider') + '. The key is validated against the provider, then encrypted at rest.'));
    const key = el('input', 'add-input');
    key.type = 'password';
    key.placeholder = 'new API key';
    const err = el('div', 'provider-error');
    const actions = el('div', 'modal-actions');
    const save = el('button', 'btn btn-primary', '[ SAVE ]');
    const cancel = el('button', 'btn', '[ CANCEL ]');
    cancel.addEventListener('click', () => window.UI.closeModal());
    save.addEventListener('click', async () => {
      if (!key.value) return;
      save.disabled = true;
      try {
        const out = await api(`/api/providers/${providerId}/key`, {
          method: 'PUT',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ api_key: key.value }),
        });
        window.UI.closeModal();
        window.UI.toast('key updated — ' + out.name, 'ok');
        if (window.Providers.redetect) window.Providers.redetect();
      } catch (e) {
        err.textContent = e.message;
        save.disabled = false;
      }
    });
    actions.appendChild(save);
    actions.appendChild(cancel);
    wrap.appendChild(key);
    wrap.appendChild(err);
    wrap.appendChild(actions);
    window.UI.openModal('EDIT API KEY', wrap);
    key.focus();
  }

  function providerCard(provider, rerender) {
    const card = el('div', 'provider-card' + (provider.kind === 'cloud' ? ' card-cloud' : ''));
    const head = el('div', 'provider-head');
    const dot = el('span', 'dot ' + (STATE_DOT[provider.status.state] || 'dot-pending'));
    dot.setAttribute('aria-label', 'status: ' + provider.status.state);
    head.appendChild(dot);
    if (provider.kind === 'cloud') head.appendChild(el('span', 'provider-kind', '☁'));
    head.appendChild(el('span', 'provider-name', provider.name));
    card.appendChild(head);
    const state = stateWord(provider);
    card.appendChild(el('div', 'provider-state ' + state.cls, state.text));
    const metaBits = [];
    if (provider.kind === 'cloud' && provider.preset) metaBits.push(provider.preset + ' preset');
    if (provider.kind === 'cloud' && provider.key_hint) metaBits.push('key ' + provider.key_hint);
    card.appendChild(el('div', 'provider-url', metaBits.length ? metaBits.join(' · ') : provider.base_url));
    if (provider.status.error && provider.status.state !== 'down') {
      card.appendChild(el('div', 'provider-error', provider.status.error));
    }

    const actions = el('div', 'provider-actions');
    const modelsBtn = el('button', 'btn', '[ MODELS ▾ ]');
    const testBtn = el('button', 'btn', '[ TEST ]');
    actions.appendChild(modelsBtn);
    actions.appendChild(testBtn);
    let helpBtn = null;
    if (provider.is_default && HELP_TEXT[provider.type]) {
      helpBtn = el('button', 'btn', '[ HOW TO START ▾ ]');
      actions.appendChild(helpBtn);
    }
    if (provider.kind === 'cloud') {
      const keyBtn = el('button', 'btn', '[ EDIT KEY ]');
      keyBtn.addEventListener('click', () => editKeyModal(provider.id, provider.name));
      actions.appendChild(keyBtn);
    }
    if (!provider.is_default) {
      const removeBtn = el('button', 'btn btn-danger', '[ REMOVE ]');
      removeBtn.addEventListener('click', async () => {
        try {
          await api(`/api/providers/${provider.id}`, { method: 'DELETE' });
          rerender();
        } catch (err) {
          card.appendChild(el('div', 'provider-error', err.message));
        }
      });
      actions.appendChild(removeBtn);
    }
    card.appendChild(actions);

    let openType = null;
    let openPanel = null;
    function swap(type, makePanel) {
      if (openType === type) {
        if (openPanel) openPanel.remove();
        openPanel = null;
        openType = null;
        return;
      }
      if (openPanel) openPanel.remove();
      openPanel = makePanel();
      openType = type;
      card.appendChild(openPanel);
    }
    modelsBtn.addEventListener('click', () => swap('models', () => modelsPanel(card, provider)));
    testBtn.addEventListener('click', () => swap('test', () => testPanel(card, provider)));
    if (helpBtn) helpBtn.addEventListener('click', () => swap('help', () => helpPanel(provider)));
    return card;
  }

  function addForm(container, rerender) {
    const form = el('form', 'add-form');
    const kindWrap = el('div', 'add-kind');
    function radio(value) {
      const r = el('input', '');
      r.type = 'radio';
      r.name = 'add-kind';
      r.value = value;
      return r;
    }
    const localRadio = radio('local');
    localRadio.checked = true;
    const cloudRadio = radio('cloud');
    const localLabel = el('label', 'add-kind-label');
    localLabel.appendChild(localRadio);
    localLabel.appendChild(document.createTextNode(' Local endpoint'));
    const cloudLabel = el('label', 'add-kind-label');
    cloudLabel.appendChild(cloudRadio);
    cloudLabel.appendChild(document.createTextNode(' Cloud API'));
    kindWrap.appendChild(localLabel);
    kindWrap.appendChild(cloudLabel);

    const localFields = el('div', 'add-fields');
    const nameL = el('input', 'add-input');
    nameL.placeholder = 'name';
    nameL.required = true;
    const url = el('input', 'add-input');
    url.placeholder = 'url or port (e.g. 1234)';
    url.required = true;
    const type = el('select', 'add-input');
    type.appendChild(el('option', '', 'openai'));
    type.appendChild(el('option', '', 'ollama'));
    localFields.appendChild(nameL);
    localFields.appendChild(url);
    localFields.appendChild(type);

    const cloudFields = el('div', 'add-fields hidden');
    const presetSel = el('select', 'add-input');
    presetSel.appendChild(el('option', '', 'loading presets…'));
    const baseUrl = el('input', 'add-input');
    baseUrl.placeholder = 'https://api.openai.com/v1 (prefilled)';
    const keyWrap = el('div', 'add-key');
    const key = el('input', 'add-input');
    key.type = 'password';
    key.placeholder = 'API key (sk-…)';
    const show = el('button', 'btn', '[ SHOW ]');
    show.type = 'button';
    show.addEventListener('click', () => {
      key.type = key.type === 'password' ? 'text' : 'password';
      show.textContent = key.type === 'password' ? '[ SHOW ]' : '[ HIDE ]';
    });
    keyWrap.appendChild(key);
    keyWrap.appendChild(show);
    cloudFields.appendChild(presetSel);
    cloudFields.appendChild(baseUrl);
    cloudFields.appendChild(keyWrap);

    const submit = el('button', 'btn btn-primary', '[ VALIDATE & SAVE ]');
    submit.type = 'submit';
    const error = el('div', 'provider-error');

    form.appendChild(kindWrap);
    form.appendChild(localFields);
    form.appendChild(cloudFields);
    form.appendChild(submit);
    form.appendChild(error);

    function syncKind() {
      const cloud = cloudRadio.checked;
      localFields.classList.toggle('hidden', cloud);
      cloudFields.classList.toggle('hidden', !cloud);
      nameL.placeholder = cloud ? 'provider name (e.g. OpenRouter)' : 'name';
      submit.textContent = cloud ? '[ VALIDATE & SAVE ]' : '[ SAVE ]';
    }
    localRadio.addEventListener('change', syncKind);
    cloudRadio.addEventListener('change', syncKind);

    let presets = {};
    api('/api/providers/presets')
      .then((p) => {
        presets = p;
        presetSel.textContent = '';
        const keys = Object.keys(p);
        if (keys.length === 0) {
          presetSel.appendChild(el('option', '', 'no presets'));
        } else {
          for (const k of keys) presetSel.appendChild(el('option', '', k));
        }
        applyPreset();
      })
      .catch((err) => { presetSel.textContent = ''; presetSel.appendChild(el('option', '', err.message)); });

    function applyPreset() {
      const cfg = presets[presetSel.value];
      if (cfg && cfg.base_url) baseUrl.value = cfg.base_url;
      else baseUrl.value = '';
    }
    presetSel.addEventListener('change', applyPreset);

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      error.textContent = '';
      submit.disabled = true;
      try {
        if (cloudRadio.checked) {
          await api('/api/providers', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({
              name: nameL.value,
              kind: 'cloud',
              preset: presetSel.value,
              base_url: baseUrl.value || undefined,
              api_key: key.value,
            }),
          });
        } else {
          await api('/api/providers', {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ name: nameL.value, base_url: url.value, type: type.value, kind: 'local' }),
          });
        }
        rerender();
      } catch (err) {
        error.textContent = err.message;
      } finally {
        submit.disabled = false;
      }
    });
    return form;
  }

  function subsectionTitle(text) {
    const div = el('div', 'providers-subsection');
    div.appendChild(document.createTextNode('── '));
    div.appendChild(el('b', '', text.toUpperCase()));
    div.appendChild(document.createTextNode(' ' + '─'.repeat(Math.max(2, 26 - text.length))));
    return div;
  }

  let lastLoad = null;
  let lastProviders = null;

  function providerSummary() {
    if (!lastProviders) return 'CHECKING…';
    const up = lastProviders.filter((p) => p.status && p.status.state === 'up').length;
    const warn = lastProviders.filter((p) => p.status && ['up_empty', 'unreachable'].includes(p.status.state)).length;
    const down = lastProviders.filter((p) => p.status && ['down', 'bad_key', 'no_credits'].includes(p.status.state)).length;
    const bits = [up + ' UP'];
    if (warn) bits.push(warn + ' DEGRADED');
    if (down) bits.push(down + ' DOWN');
    return bits.join(' · ');
  }

  async function render(root) {
    root.textContent = '';
    const toolbar = el('div', 'providers-toolbar');
    const redetect = el('button', 'btn', '[ ↻ RE-DETECT ]');
    const addBtn = el('button', 'btn', '[ + ADD PROVIDER ]');
    toolbar.appendChild(redetect);
    toolbar.appendChild(addBtn);
    root.appendChild(toolbar);

    let addOpen = null;
    addBtn.addEventListener('click', () => {
      if (addOpen) { addOpen.remove(); addOpen = null; return; }
      addOpen = addForm(root, () => render(root));
      root.insertBefore(addOpen, toolbar.nextSibling || null);
    });

    const listEl = el('div', 'providers-list');
    listEl.appendChild(el('div', 'provider-state', 'CHECKING…'));
    root.appendChild(listEl);

    async function load(force) {
      lastLoad = () => load(true);
      listEl.textContent = '';
      listEl.appendChild(el('div', 'provider-state', 'CHECKING…'));
      try {
        const providers = await api(force ? '/api/providers/detect' : '/api/providers',
          force ? { method: 'POST' } : undefined);
        lastProviders = providers;
        window.Sidebar.refreshSummary('providers');
        listEl.textContent = '';
        const local = providers.filter((p) => p.kind !== 'cloud');
        const cloud = providers.filter((p) => p.kind === 'cloud');
        if (local.length) {
          listEl.appendChild(subsectionTitle('LOCAL'));
          for (const p of local) listEl.appendChild(providerCard(p, () => render(root)));
        }
        if (cloud.length) {
          listEl.appendChild(subsectionTitle('CLOUD'));
          for (const p of cloud) listEl.appendChild(providerCard(p, () => render(root)));
        }
        if (!local.length && !cloud.length) {
          const empty = el('div', 'provider-state');
          empty.appendChild(document.createTextNode('NO PROVIDERS — '));
          const add = el('button', 'btn', '[ + ADD ]');
          add.addEventListener('click', () => addBtn.click());
          empty.appendChild(add);
          listEl.appendChild(empty);
        }
      } catch (err) {
        listEl.textContent = '';
        listEl.appendChild(el('div', 'provider-error', err.message));
      }
    }
    redetect.addEventListener('click', () => load(true));
    load(false);
  }

  window.Providers = {
    redetect: () => {
      window.Sidebar.openSection('providers');
      if (lastLoad) lastLoad();
      window.Sidebar.open();
    },
    editKey: editKeyModal,
  };

  Sidebar.registerSection({
    id: 'providers',
    title: 'Providers',
    render,
    summary: providerSummary,
    action: { label: '↻', title: 'Re-detect providers', run: () => { window.Sidebar.openSection('providers'); if (lastLoad) lastLoad(); } },
  });
})();
