window.Chat = (() => {
  const el = window.UI.el;
  let conversationId = null;
  let streaming = false;
  let abortController = null;
  let activeProviderId = null;
  let activeModelId = null;
  let messagesEl, inputEl, sendBtn, badgeEl, badgeText, dropdown, pillEl, searchWrap, searchInput;

  function ts() {
    return new Date().toLocaleTimeString('en-GB', { hour12: false });
  }

  function atBottom() {
    return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 40;
  }

  function scrollBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
    pillEl.classList.add('hidden');
  }

  function maybeScroll(wasAtBottom) {
    if (wasAtBottom) {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    } else {
      pillEl.classList.remove('hidden');
    }
  }

  function logLine(role, roleClass, text) {
    const line = el('div', 'log-line');
    line.appendChild(el('span', 'log-ts', ts()));
    const roleEl = el('span', 'log-role ' + roleClass, role);
    roleEl.appendChild(el('span', 'log-sep', '›'));
    line.appendChild(roleEl);
    const content = el('div', 'log-content');
    if (text !== undefined) content.textContent = text;
    line.appendChild(content);
    return { line, content };
  }

  function scrollStick(was) {
    if (was === undefined) was = atBottom();
    if (was) {
      messagesEl.scrollTop = messagesEl.scrollHeight;
      pillEl.classList.add('hidden');
    }
  }

  function addLog(role, roleClass, text) {
    clearEmpty();
    const was = atBottom();
    const { line, content } = logLine(role, roleClass, text);
    messagesEl.appendChild(line);
    scrollStick(was);
    return content;
  }

  function addErrLine(message, withProvidersBtn) {
    clearEmpty();
    const was = atBottom();
    const { line, content } = logLine('ERR', 'role-err', message);
    line.classList.add('line-err');
    if (withProvidersBtn) {
      const actions = el('div', 'log-actions');
      const btn = el('button', 'btn', '[ OPEN PROVIDERS ]');
      btn.addEventListener('click', () => window.Sidebar.open());
      actions.appendChild(btn);
      content.appendChild(actions);
    }
    messagesEl.appendChild(line);
    scrollStick(was);
  }

  function gateBubble(payload) {
    if (payload.error === 'provider_down') {
      addErrLine((payload.detail || 'The active provider') +
        ' is not running. Start it (see Providers → How to start ▾) and send again.', true);
    } else {
      addErrLine('No model activated. Pick one in Providers → Models ▾.', true);
    }
  }

  /* ── empty state ── */
  function showEmpty() {
    if (messagesEl.querySelector('.empty-state')) return;
    const wrap = el('div', 'empty-state');
    wrap.appendChild(el('div', 'empty-wordmark', 'LOCAL · AI · HUB'));
    wrap.appendChild(el('div', 'empty-hint', 'F2 — Connect a provider'));
    wrap.appendChild(el('div', 'empty-hint', 'F4 — New chat'));
    wrap.appendChild(el('div', 'empty-hint', 'F1 — Shortcuts'));
    messagesEl.appendChild(wrap);
  }

  function clearEmpty() {
    const empty = messagesEl.querySelector('.empty-state');
    if (empty) empty.remove();
  }

  /* ── streaming ── */
  function addStreamingLine() {
    clearEmpty();
    const was = atBottom();
    const { line, content } = logLine('HUB', 'role-hub');
    const thinking = el('span', 'log-thinking', 'thinking…');
    const reasoning = el('div', 'log-reasoning hidden');
    const body = el('span', 'log-body');
    const cursor = el('span', 'log-cursor', '▋');
    content.appendChild(thinking);
    content.appendChild(reasoning);
    content.appendChild(body);
    content.appendChild(cursor);
    messagesEl.appendChild(line);
    scrollStick(was);
    return { line, thinking, reasoning, body, cursor };
  }

  function setStreaming(on) {
    streaming = on;
    inputEl.disabled = false;
    sendBtn.textContent = on ? '[ ■ STOP ]' : '[ SEND ⏎ ]';
    sendBtn.classList.toggle('btn-stop', on);
    sendBtn.classList.toggle('btn-primary', !on);
    sendBtn.title = on ? 'Stop generation' : 'Send';
    if (!on) inputEl.focus();
  }

  async function activateModel(providerId, providerName, model) {
    badgeText.textContent = 'loading ' + model + '…';
    badgeEl.classList.add('loading');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 65000);
    try {
      const res = await fetch(`/api/providers/${providerId}/activate`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ model }),
        signal: controller.signal,
      });
      const out = await res.json();
      if (!res.ok) throw new Error(out.detail || res.statusText);
      badgeText.textContent = providerName + ' · ' + model;
      activeProviderId = providerId;
      activeModelId = model;
      if (out.skipped) {
        window.UI.toast('model ready (already loaded)', 'ok');
      } else if (out.warmup_pending) {
        window.UI.toast('model still loading — first reply may be slow', 'warn');
      } else if (out.warmup_error) {
        window.UI.toast('model active but warm-up failed: ' + out.warmup_error, 'error');
      } else {
        window.UI.toast('model ready' + (out.warmup_ms != null ? ` (${out.warmup_ms} ms)` : ''), 'ok');
      }
    } catch (err) {
      if (err.name === 'AbortError') {
        badgeText.textContent = providerName + ' · ' + model;
        activeProviderId = providerId;
        activeModelId = model;
        window.UI.toast('model still loading — first reply may be slow', 'warn');
      } else {
        window.UI.toast('activation failed: ' + err.message, 'error');
        refreshBadge();
      }
    } finally {
      clearTimeout(timer);
      badgeEl.classList.remove('loading');
    }
  }

  async function send(message) {
    addLog('USER', 'role-user', message);
    const stream = addStreamingLine();
    const started = performance.now();
    abortController = new AbortController();
    setStreaming(true);
    let accumulated = '';
    let reasoningAccumulated = '';
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ conversation_id: conversationId, message }),
        signal: abortController.signal,
      });
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf('\n\n')) >= 0) {
          const chunk = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          if (!chunk.startsWith('data:')) continue;
          let payload;
          try { payload = JSON.parse(chunk.slice(5)); } catch (e) { continue; }
          const was = atBottom();
          if (payload.delta) {
            stream.thinking.classList.add('hidden');
            accumulated += payload.delta;
            stream.body.textContent = accumulated;
            maybeScroll(was);
          } else if (payload.reasoning) {
            stream.thinking.classList.add('hidden');
            reasoningAccumulated += payload.reasoning;
            stream.reasoning.classList.remove('hidden');
            stream.reasoning.textContent = reasoningAccumulated;
            maybeScroll(was);
          } else if (payload.error === 'no_provider' || payload.error === 'no_model' || payload.error === 'provider_down') {
            stream.line.remove();
            gateBubble(payload);
          } else if (payload.error === 'stream_interrupted') {
            stream.thinking.classList.add('hidden');
            stream.cursor.remove();
            stream.body.textContent = accumulated + (accumulated ? '\n' : '') +
              '[stream interrupted' + (payload.detail ? ': ' + payload.detail : '') + ']';
          } else if (payload.done) {
            stream.thinking.classList.add('hidden');
            stream.cursor.remove();
            conversationId = payload.conversation_id;
            const secs = ((performance.now() - started) / 1000).toFixed(1);
            const meta = el('div', 'log-meta',
              (activeModelId || 'unknown') + ' · ' + secs + 's');
            stream.line.querySelector('.log-content').appendChild(meta);
          }
        }
      }
    } catch (err) {
      stream.thinking.classList.add('hidden');
      stream.cursor.remove();
      if (err.name === 'AbortError') {
        stream.body.textContent = accumulated + (accumulated ? '\n' : '') + '[stopped]';
      } else {
        stream.line.remove();
        addErrLine('request failed: ' + err.message, false);
      }
    } finally {
      abortController = null;
      setStreaming(false);
    }
  }

  /* ── badge / dropdown ── */
  async function refreshBadge() {
    try {
      const res = await fetch('/api/providers/active');
      const active = await res.json();
      activeProviderId = active.provider_id;
      activeModelId = active.model;
      badgeText.textContent = active.provider_name && active.model
        ? active.provider_name + ' · ' + active.model
        : 'no model';
    } catch (err) {
      badgeText.textContent = 'no model';
    }
  }

  function formatSize(bytes) {
    const gb = bytes / 1e9;
    return gb >= 0.1 ? gb.toFixed(1) + ' GB' : Math.round(bytes / 1e6) + ' MB';
  }

  function renderModelRows(holder, providers, filter) {
    const needles = filter.trim().toLowerCase();
    let any = false;
    for (const p of providers) {
      if (p.status.state === 'down' || p.status.models.length === 0) continue;
      const models = p.status.models.filter((m) => !needles || m.id.toLowerCase().includes(needles));
      if (models.length === 0) continue;
      const group = el('div', 'dropdown-group');
      group.appendChild(document.createTextNode('── '));
      group.appendChild(el('b', '', p.name.toUpperCase()));
      group.appendChild(document.createTextNode(' ' + '─'.repeat(Math.max(2, 24 - p.name.length))));
      holder.appendChild(group);
      for (const m of models) {
        any = true;
        const item = el('div', 'dropdown-model');
        item.appendChild(el('span', '', m.id));
        item.appendChild(el('span', 'model-meta', m.size_bytes ? formatSize(m.size_bytes) : ''));
        if (p.id === activeProviderId && m.id === activeModelId) item.classList.add('active');
        item.addEventListener('click', async (ev) => {
          ev.stopPropagation();
          dropdown.classList.add('hidden');
          await activateModel(p.id, p.name, m.id);
        });
        holder.appendChild(item);
      }
    }
    if (!any) {
      const empty = el('div', 'dropdown-group');
      empty.appendChild(el('b', '', needles ? 'NO MATCHES' : 'NO MODELS AVAILABLE'));
      holder.appendChild(empty);
    }
  }

  async function openDropdown() {
    if (!dropdown.classList.contains('hidden')) {
      dropdown.classList.add('hidden');
      return;
    }
    dropdown.textContent = '';
    const filter = el('input', 'dropdown-filter');
    filter.type = 'text';
    filter.placeholder = 'Filter models…';
    filter.setAttribute('aria-label', 'Filter models');
    const listWrap = el('div');
    dropdown.appendChild(filter);
    dropdown.appendChild(listWrap);
    dropdown.classList.remove('hidden');
    filter.focus();

    let rows = [];
    try {
      const res = await fetch('/api/providers');
      const providers = await res.json();
      const render = () => {
        listWrap.textContent = '';
        renderModelRows(listWrap, providers, filter.value);
        rows = Array.from(listWrap.querySelectorAll('.dropdown-model'));
      };
      render();
      filter.addEventListener('input', render);
      filter.addEventListener('keydown', (ev) => {
        const current = listWrap.querySelector('.dropdown-model.kb-active');
        let idx = rows.indexOf(current);
        if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
          ev.preventDefault();
          if (rows.length === 0) return;
          idx = ev.key === 'ArrowDown'
            ? (idx + 1) % rows.length
            : (idx - 1 + rows.length) % rows.length;
          if (current) current.classList.remove('kb-active');
          rows[idx].classList.add('kb-active');
          rows[idx].scrollIntoView({ block: 'nearest' });
        } else if (ev.key === 'Enter') {
          ev.preventDefault();
          const target = current || rows[0];
          if (target) target.click();
        } else if (ev.key === 'Escape') {
          ev.stopPropagation();
          dropdown.classList.add('hidden');
        }
      });
    } catch (err) {
      listWrap.textContent = '';
      const empty = el('div', 'dropdown-group');
      empty.appendChild(el('b', '', err.message));
      listWrap.appendChild(empty);
    }
  }

  /* ── history / new chat ── */
  async function loadHistory() {
    try {
      const res = await fetch('/api/conversations');
      const conversations = await res.json();
      if (conversations.length === 0) {
        showEmpty();
        return;
      }
      conversationId = conversations[0].id;
      const msgsRes = await fetch(`/api/conversations/${conversationId}/messages`);
      const msgs = await msgsRes.json();
      if (msgs.length === 0) {
        showEmpty();
        return;
      }
      for (const m of msgs) {
        if (m.role === 'user') {
          addLog('USER', 'role-user', m.content);
        } else {
          const content = addLog('HUB', 'role-hub', m.content);
          if (m.model) {
            content.appendChild(el('div', 'log-meta', m.model));
          }
        }
      }
      scrollBottom();
    } catch (err) {
      showEmpty();
    }
  }

  function newChat() {
    conversationId = null;
    messagesEl.textContent = '';
    showEmpty();
    inputEl.focus();
  }

  /* ── log search ── */
  function toggleSearch() {
    const opening = searchWrap.classList.contains('hidden');
    searchWrap.classList.toggle('hidden');
    if (opening) {
      searchInput.focus();
    } else {
      searchInput.value = '';
      applyFilter('');
    }
  }

  function applyFilter(needle) {
    const n = needle.trim().toLowerCase();
    for (const line of messagesEl.querySelectorAll('.log-line')) {
      line.classList.toggle('hidden', n !== '' && !line.textContent.toLowerCase().includes(n));
    }
  }

  function autogrow() {
    inputEl.style.height = 'auto';
    inputEl.style.height = Math.min(inputEl.scrollHeight, 132) + 'px';
  }

  function init() {
    messagesEl = document.getElementById('messages');
    inputEl = document.getElementById('chat-input');
    sendBtn = document.getElementById('send-btn');
    badgeEl = document.getElementById('model-badge');
    badgeText = document.getElementById('model-badge-text');
    dropdown = document.getElementById('model-dropdown');
    pillEl = document.getElementById('new-output-pill');
    searchWrap = document.getElementById('log-search');
    searchInput = document.getElementById('log-search-input');

    sendBtn.addEventListener('click', (ev) => {
      if (streaming) {
        ev.preventDefault();
        if (abortController) abortController.abort();
      }
    });

    document.getElementById('chat-form').addEventListener('submit', (ev) => {
      ev.preventDefault();
      const message = inputEl.value.trim();
      if (!message || streaming) return;
      inputEl.value = '';
      autogrow();
      send(message);
    });
    inputEl.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' && !ev.shiftKey) {
        ev.preventDefault();
        const message = inputEl.value.trim();
        if (!message || streaming) return;
        inputEl.value = '';
        autogrow();
        send(message);
      }
    });
    inputEl.addEventListener('input', autogrow);
    badgeEl.addEventListener('click', (ev) => {
      if (dropdown.contains(ev.target) && ev.target.closest('.dropdown-filter')) return;
      if (ev.target.closest('.dropdown-model')) return;
      openDropdown();
    });
    document.addEventListener('click', (ev) => {
      if (!badgeEl.contains(ev.target)) {
        dropdown.classList.add('hidden');
      }
    });
    pillEl.addEventListener('click', scrollBottom);
    messagesEl.addEventListener('scroll', () => {
      if (atBottom()) pillEl.classList.add('hidden');
    });
    searchInput.addEventListener('input', () => applyFilter(searchInput.value));

    loadHistory();
    refreshBadge();
  }

  return {
    init,
    refreshBadge,
    activateModel,
    newChat,
    toggleSearch,
    toast: (msg, kind) => window.UI.toast(msg, kind === 'error' ? 'error' : kind),
    isSearchOpen: () => !searchWrap.classList.contains('hidden'),
    closeSearch: () => {
      searchWrap.classList.add('hidden');
      searchInput.value = '';
      applyFilter('');
    },
    closeDropdown: () => dropdown.classList.add('hidden'),
  };
})();
