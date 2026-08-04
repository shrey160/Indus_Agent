(() => {
  const el = window.UI.el;
  const STATE_DOT = { up: 'dot-ok', up_empty: 'dot-warn', down: 'dot-err', checking: 'dot-pending' };

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

  function stateWord(provider) {
    const s = provider.status;
    if (s.state === 'up') {
      const parts = ['UP'];
      if (s.latency_ms !== null && s.latency_ms !== undefined) parts.push(s.latency_ms + 'MS');
      parts.push(s.models.length + ' MODELS');
      return { text: parts.join(' · '), cls: '' };
    }
    if (s.state === 'up_empty') return { text: 'UP — NO MODELS', cls: 'state-warn' };
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

  async function activate(providerId, providerName, model) {
    if (window.Chat && window.Chat.activateModel) {
      await window.Chat.activateModel(providerId, providerName, model);
    }
  }

  function modelsPanel(card, provider) {
    const panel = el('div', 'models-panel');
    const list = el('ul', 'models-list', 'checking…');
    panel.appendChild(list);
    api(`/api/providers/${provider.id}/models`)
      .then((models) => {
        list.textContent = '';
        if (models.length === 0) {
          list.appendChild(el('li', 'models-empty', 'no models'));
          return;
        }
        for (const m of models) {
          const item = el('li', 'model-item', m.id);
          item.addEventListener('click', async () => {
            await activate(provider.id, provider.name, m.id);
          });
          list.appendChild(item);
        }
      })
      .catch((err) => { list.textContent = err.message; });
    return panel;
  }

  function testPanel(card, provider) {
    const panel = el('div', 'test-panel');
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
    panel.appendChild(select);
    panel.appendChild(prompt);
    panel.appendChild(row);
    panel.appendChild(result);
    api(`/api/providers/${provider.id}/models`)
      .then((models) => {
        select.textContent = '';
        for (const m of models) select.appendChild(el('option', '', m.id));
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

  function providerCard(provider, rerender) {
    const card = el('div', 'provider-card');
    const head = el('div', 'provider-head');
    const dot = el('span', 'dot ' + (STATE_DOT[provider.status.state] || 'dot-pending'));
    dot.setAttribute('aria-label', 'status: ' + provider.status.state);
    head.appendChild(dot);
    head.appendChild(el('span', 'provider-name', provider.name));
    card.appendChild(head);
    const state = stateWord(provider);
    card.appendChild(el('div', 'provider-state ' + state.cls, state.text));
    card.appendChild(el('div', 'provider-url', provider.base_url));
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

    let openPanel = null;
    function swap(panel) {
      if (openPanel) openPanel.remove();
      openPanel = openPanel === panel ? null : panel;
      if (openPanel) card.appendChild(openPanel);
    }
    modelsBtn.addEventListener('click', () => swap(modelsPanel(card, provider)));
    testBtn.addEventListener('click', () => swap(testPanel(card, provider)));
    if (helpBtn) helpBtn.addEventListener('click', () => swap(helpPanel(provider)));
    return card;
  }

  function addForm(container, rerender) {
    const form = el('form', 'add-form');
    const name = el('input', 'add-input');
    name.placeholder = 'name';
    name.required = true;
    const url = el('input', 'add-input');
    url.placeholder = 'url or port (e.g. 1234)';
    url.required = true;
    const type = el('select', 'add-input');
    type.appendChild(el('option', '', 'openai'));
    type.appendChild(el('option', '', 'ollama'));
    const submit = el('button', 'btn btn-primary', '[ SAVE ]');
    submit.type = 'submit';
    const error = el('div', 'provider-error');
    form.appendChild(name);
    form.appendChild(url);
    form.appendChild(type);
    form.appendChild(submit);
    form.appendChild(error);
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      error.textContent = '';
      submit.disabled = true;
      try {
        await api('/api/providers', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ name: name.value, base_url: url.value, type: type.value }),
        });
        rerender();
      } catch (err) {
        error.textContent = err.message;
      } finally {
        submit.disabled = false;
      }
    });
    return form;
  }

  let lastLoad = null;

  async function render(root) {
    root.textContent = '';
    const toolbar = el('div', 'providers-toolbar');
    const redetect = el('button', 'btn', '[ ↻ RE-DETECT ]');
    const addBtn = el('button', 'btn', '[ + ADD PROVIDER ]');
    toolbar.appendChild(redetect);
    toolbar.appendChild(addBtn);
    root.appendChild(toolbar);
    const listEl = el('div', 'providers-list');
    listEl.appendChild(el('div', 'provider-state', 'CHECKING…'));
    root.appendChild(listEl);

    let addOpen = null;
    addBtn.addEventListener('click', () => {
      if (addOpen) { addOpen.remove(); addOpen = null; return; }
      addOpen = addForm(root, () => render(root));
      root.insertBefore(addOpen, listEl);
    });

    async function load(force) {
      lastLoad = () => load(true);
      listEl.textContent = '';
      listEl.appendChild(el('div', 'provider-state', 'CHECKING…'));
      try {
        const providers = await api(force ? '/api/providers/detect' : '/api/providers',
          force ? { method: 'POST' } : undefined);
        listEl.textContent = '';
        for (const p of providers) listEl.appendChild(providerCard(p, () => render(root)));
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
      if (lastLoad) lastLoad();
      window.Sidebar.open();
    },
  };

  Sidebar.registerSection({ id: 'providers', title: 'Providers', render });
})();
