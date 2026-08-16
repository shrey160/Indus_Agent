window.Chat = (() => {
  const el = window.UI.el;
  let conversationId = null;
  let streaming = false;
  let abortController = null;
  let activeProviderId = null;
  let activeModelId = null;
  let openChips = [];
  let messagesEl, inputEl, sendBtn, badgeEl, badgeText, dropdown, pillEl, searchWrap, searchInput;

  const TOOL_ICONS = { 'web.search': '🔍', 'web.fetch': '📄', 'util.datetime': '🕐' };

  function argSummary(args) {
    if (!args || typeof args !== 'object') return '';
    const v = args.query || args.url || Object.values(args).find((x) => typeof x === 'string');
    if (!v) return '';
    const s = String(v);
    return ' "' + (s.length > 48 ? s.slice(0, 48) + '…' : s) + '"';
  }

  function toolRunning(stream, tool) {
    const { line, content } = logLine('TOOL', 'role-tool');
    line.classList.add('line-tool');
    const icon = TOOL_ICONS[tool.name] || '🔧';
    content.textContent = icon + ' ' + tool.name + argSummary(tool.args) + ' · running…';
    content.addEventListener('click', () => content.classList.toggle('expanded'));
    messagesEl.insertBefore(line, stream.line);
    openChips.push({ name: tool.name, line, content, icon, args: tool.args });
  }

  function toolDone(tool) {
    let idx = openChips.findIndex((c) => c.name === tool.name);
    if (idx === -1 && openChips.length) idx = 0;
    if (idx === -1) return;
    const chip = openChips.splice(idx, 1)[0];
    const base = chip.icon + ' ' + chip.name + argSummary(chip.args);
    if (tool.error) {
      chip.line.classList.add('chip-err');
      chip.content.textContent = base + ' · failed: ' + tool.error;
      return;
    }
    const bits = [base];
    const p = tool.result_preview;
    if (p && Array.isArray(p.results)) bits.push(p.results.length + ' results');
    if (tool.latency_ms != null) bits.push(tool.latency_ms + 'ms');
    chip.content.textContent = bits.join(' · ');
  }

  function toolChip(tool) {
    const icon = TOOL_ICONS[tool.name] || '🔧';
    const base = icon + ' ' + tool.name + argSummary(tool.args);
    const bits = [base];
    if (tool.error) {
      bits.push('failed: ' + tool.error);
    } else {
      const p = tool.result_preview;
      if (p && Array.isArray(p.results)) bits.push(p.results.length + ' results');
      if (tool.latency_ms != null) bits.push(tool.latency_ms + 'ms');
    }
    const contentEl = addLog('TOOL', 'role-tool', bits.join(' · '));
    const line = contentEl.closest('.log-line');
    line.classList.add('line-tool');
    if (tool.error) line.classList.add('chip-err');
    contentEl.addEventListener('click', () => contentEl.classList.toggle('expanded'));
  }

  function sysLine(stream, text) {
    const { line } = logLine('SYS', 'role-sys', text);
    messagesEl.insertBefore(line, stream.line);
  }

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
    const title = el('div', 'cite-popover-title', source.doc || source.title || 'source');
    const snippet = el('div', 'cite-popover-snippet', source.snippet || source.url || '');
    wrap.appendChild(title);
    wrap.appendChild(snippet);
    document.body.appendChild(wrap);
    activeCitePopover = wrap;

    const top = anchorRect.bottom + 6;
    const left = Math.min(anchorRect.left, window.innerWidth - 316);
    wrap.style.top = top + 'px';
    wrap.style.left = Math.max(4, left) + 'px';

    function onDocClick(ev) {
      if (!wrap.contains(ev.target)) {
        closeCitePopover();
        document.removeEventListener('click', onDocClick);
      }
    }
    function onEsc(ev) {
      if (ev.key === 'Escape') {
        closeCitePopover();
        document.removeEventListener('keydown', onEsc);
      }
    }
    setTimeout(() => {
      document.addEventListener('click', onDocClick);
      document.addEventListener('keydown', onEsc);
    }, 0);
  }

  function sourcesBlock(sources) {
    const wrap = el('div', 'log-sources');
    wrap.appendChild(document.createTextNode('sources:'));
    sources.forEach((s, i) => {
      const label = '[' + (i + 1) + '] ' + (s.doc || '');
      if (s.kind === 'rag' || !s.url) {
        const span = el('span', 'src-link src-rag', label);
        span.title = s.doc || '';
        span.addEventListener('click', (ev) => {
          citePopover(s, ev.target.getBoundingClientRect());
        });
        wrap.appendChild(span);
        return;
      }
      let host = s.url || '';
      try { host = new URL(s.url).hostname.replace(/^www\./, ''); } catch (e) { /* keep raw */ }
      const a = el('a', 'src-link', '[' + (i + 1) + '] ' + host);
      a.href = s.url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      if (s.title) a.title = s.title;
      wrap.appendChild(a);
    });
    return wrap;
  }

  function linkifyCitations(bodyEl, sources) {
    if (!bodyEl || !sources || !sources.length) return;
    const regex = /(\[\d+\]|【\d+】)/g;
    const walker = document.createTreeWalker(bodyEl, NodeFilter.SHOW_TEXT, null, false);
    const nodes = [];
    let n;
    while ((n = walker.nextNode())) nodes.push(n);
    for (const node of nodes) {
      const text = node.textContent;
      const parts = text.split(regex);
      if (parts.length < 3) continue;
      const parent = node.parentNode;
      const frag = document.createDocumentFragment();
      for (const part of parts) {
        const m = part.match(/^\[(\d+)\]$/) || part.match(/^【(\d+)】$/);
        if (m) {
          const idx = parseInt(m[1], 10) - 1;
          const sup = el('sup', 'cite', part);
          if (idx >= 0 && idx < sources.length) {
            sup.addEventListener('click', (ev) => {
              citePopover(sources[idx], ev.target.getBoundingClientRect());
            });
          }
          frag.appendChild(sup);
        } else {
          frag.appendChild(document.createTextNode(part));
        }
      }
      parent.replaceChild(frag, node);
    }
  }

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

  function addSysLine(text) {
    clearEmpty();
    const was = atBottom();
    const { line, content } = logLine('SYS', 'role-sys', text);
    line.classList.add('line-tool');
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

  function errorLine(message, buttons) {
    clearEmpty();
    const was = atBottom();
    const { line, content } = logLine('ERR', 'role-err', message);
    line.classList.add('line-err');
    if (buttons && buttons.length) {
      const actions = el('div', 'log-actions');
      for (const b of buttons) actions.appendChild(b);
      content.appendChild(actions);
    }
    messagesEl.appendChild(line);
    scrollStick(was);
  }

  function gateBubble(payload) {
    if (payload.error === 'bad_key') {
      const b = el('button', 'btn', '[ EDIT KEY ]');
      b.addEventListener('click', () => {
        if (window.Providers && window.Providers.editKey) window.Providers.editKey(payload.provider_id, payload.provider_name);
      });
      errorLine('Invalid API key for ' + (payload.provider_name || 'the cloud provider') + '.', [b]);
    } else if (payload.error === 'no_credits') {
      errorLine('The provider has no credits' + (payload.detail ? ': ' + payload.detail : '.') + ' Check its billing in the Providers section.', []);
    } else if (payload.error === 'rate_limited') {
      errorLine('Rate limited — try again shortly.', []);
    } else if (payload.error === 'provider_down') {
      addErrLine((payload.detail || 'The active provider') +
        ' is not reachable. Check that it is running (local) or online (cloud) and send again.', true);
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
    wrap.appendChild(el('div', 'empty-hint', 'DROP A FILE TO INDEX'));
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
    const body = el('div', 'log-body');
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

  async function activateModel(providerId, providerName, model, kind) {
    const go = () => doActivate(providerId, providerName, model);
    if (kind === 'cloud') {
      maybePrivacyNotice(providerName, go);
    } else {
      go();
    }
  }

  function maybePrivacyNotice(providerName, next) {
    if (localStorage.getItem('cloudPrivacyNotice') === '1') { next(); return; }
    const wrap = el('div');
    wrap.appendChild(el('p', 'privacy-note',
      'Messages now leave this device and go to ' + (providerName || 'the cloud provider') +
      '. Your prompts and the replies are sent to a third-party API. No API key is ever stored in the browser or sent back to you.'));
    const actions = el('div', 'modal-actions');
    const dont = el('button', 'btn', '[ DON\u2019T SHOW AGAIN ]');
    const under = el('button', 'btn btn-primary', '[ I UNDERSTAND ]');
    dont.addEventListener('click', () => { localStorage.setItem('cloudPrivacyNotice', '1'); window.UI.closeModal(); next(); });
    under.addEventListener('click', () => { window.UI.closeModal(); next(); });
    actions.appendChild(dont);
    actions.appendChild(under);
    wrap.appendChild(actions);
    window.UI.openModal('CLOUD PRIVACY', wrap);
  }

  async function doActivate(providerId, providerName, model) {
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
    openChips = [];
    let accumulated = '';
    let reasoningAccumulated = '';
    let firstTokenAt = null;
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ conversation_id: conversationId, message }),
        signal: abortController.signal,
      });
      if (!res.ok) {
        let detail = 'HTTP ' + res.status;
        try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
        throw new Error(detail);
      }
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
            if (firstTokenAt === null) firstTokenAt = performance.now();
            accumulated += payload.delta;
            window.MD.renderInto(stream.body, accumulated);
            maybeScroll(was);
          } else if (payload.reasoning) {
            stream.thinking.classList.add('hidden');
            reasoningAccumulated += payload.reasoning;
            stream.reasoning.classList.remove('hidden');
            stream.reasoning.textContent = reasoningAccumulated;
            maybeScroll(was);
          } else if (payload.tool) {
            stream.thinking.classList.add('hidden');
            if (payload.tool.status === 'running') {
              try {
                JSON.parse(accumulated.trim());
                accumulated = '';
                stream.body.textContent = '';
              } catch (e) { /* ordinary streamed text, keep it */ }
              toolRunning(stream, payload.tool);
            } else {
              toolDone(payload.tool);
            }
            maybeScroll(was);
          } else if (payload.tool_limit) {
            sysLine(stream, payload.tool_limit);
            maybeScroll(was);
          } else if (payload.error === 'no_provider' || payload.error === 'no_model' || payload.error === 'provider_down' || payload.error === 'bad_key' || payload.error === 'no_credits' || payload.error === 'rate_limited') {
            stream.line.remove();
            gateBubble(payload);
          } else if (payload.error === 'stream_interrupted') {
            stream.thinking.classList.add('hidden');
            stream.cursor.remove();
            window.MD.renderInto(stream.body, accumulated);
            stream.body.appendChild(el('span', 'log-interrupt',
              '[stream interrupted' + (payload.detail ? ': ' + payload.detail : '') + ']'));
            const retry = el('button', 'btn', '[ \u27F3 RETRY ]');
            retry.addEventListener('click', () => { send(message); });
            const actions = el('div', 'log-actions');
            actions.appendChild(retry);
            stream.line.querySelector('.log-content').appendChild(actions);
          } else if (payload.done) {
            stream.thinking.classList.add('hidden');
            stream.cursor.remove();
            conversationId = payload.conversation_id;
            document.dispatchEvent(new CustomEvent('hub:conversation'));
            const doneAt = performance.now();
            const secs = ((doneAt - started) / 1000).toFixed(1);
            const bits = [activeModelId || 'unknown', secs + 's'];
            if (firstTokenAt !== null) {
              bits.push('ttft ' + ((firstTokenAt - started) / 1000).toFixed(2) + 's');
            }
            if (payload.usage && payload.usage.completion) {
              const dur = firstTokenAt !== null ? (doneAt - firstTokenAt) / 1000 : secs;
              bits.push((dur > 0 ? payload.usage.completion / dur : 0).toFixed(0) + ' t/s');
            } else if (firstTokenAt !== null && accumulated.length > 4) {
              const dur = (doneAt - firstTokenAt) / 1000;
              bits.push('~' + (dur > 0 ? (accumulated.length / 4) / dur : 0).toFixed(0) + ' t/s');
            }
            if (payload.cost_usd !== null && payload.cost_usd !== undefined) {
              bits.push('~$' + Number(payload.cost_usd).toFixed(4));
            }
            const meta = el('div', 'log-meta', bits.join(' · '));
            if (payload.cloud) meta.appendChild(el('span', 'log-cloud', ' ☁'));
            const contentEl = stream.line.querySelector('.log-content');
            linkifyCitations(stream.body, payload.sources);
            if (payload.sources && payload.sources.length) {
              contentEl.appendChild(sourcesBlock(payload.sources));
            }
            contentEl.appendChild(meta);
            const stats = [];
            if (payload.usage) {
              if (payload.usage.prompt != null) stats.push('p ' + payload.usage.prompt);
              if (payload.usage.completion != null) stats.push('c ' + payload.usage.completion);
              if (payload.usage.total != null) stats.push('t ' + payload.usage.total);
              if (payload.usage.reasoning != null) stats.push('r ' + payload.usage.reasoning);
              if (payload.usage.prompt != null && payload.context_length) {
                stats.push('ctx ' + fmtTokens(payload.usage.prompt) + '/' + fmtTokens(payload.context_length));
              }
            }
            if (stats.length) {
              contentEl.appendChild(el('div', 'log-meta log-meta-stats', 'tokens · ' + stats.join(' ')));
            }
          }
        }
      }
    } catch (err) {
      stream.thinking.classList.add('hidden');
      stream.cursor.remove();
      if (err.name === 'AbortError') {
        window.MD.renderInto(stream.body, accumulated);
        stream.body.appendChild(el('span', 'log-interrupt', '[stopped]'));
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

  function fmtTokens(n) {
    return n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k' : String(n);
  }

  function priceStr(m) {
    if (!m.pricing || m.pricing.prompt === undefined || m.pricing.prompt === null) return null;
    const per_m = parseFloat(m.pricing.prompt) * 1e6;
    return Number.isFinite(per_m) ? '$' + per_m.toFixed(2) + '/M' : null;
  }

  function dropdownGroup(holder, title) {
    const group = el('div', 'dropdown-group');
    group.appendChild(document.createTextNode('── '));
    group.appendChild(el('b', '', title.toUpperCase()));
    group.appendChild(document.createTextNode(' ' + '─'.repeat(Math.max(2, 24 - title.length))));
    holder.appendChild(group);
  }

  function renderModelRows(holder, providers, filter) {
    const needles = filter.trim().toLowerCase();
    const pinned = [];
    const groups = [];
    for (const p of providers) {
      if (p.status.state === 'down' || p.status.state === 'unreachable' || p.status.state === 'bad_key' || p.status.state === 'no_credits') continue;
      if (p.status.models.length === 0) continue;
      const items = [];
      for (const m of p.status.models) {
        if (needles && !m.id.toLowerCase().includes(needles)) continue;
        m._providerId = p.id;
        m._providerLabel = p.name;
        m._cluster = p.kind === 'cloud' ? 'cloud' : 'local';
        if (m.pinned) pinned.push(m);
        else items.push(m);
      }
      if (items.length) groups.push({ name: p.name, cloud: p.kind === 'cloud', items });
    }
    groups.sort((a, b) => (a.cloud === b.cloud ? a.name.localeCompare(b.name) : (a.cloud ? 1 : -1)));
    const draw = (items, withProvider) => {
      for (const m of items) {
        const item = el('div', 'dropdown-model');
        item.appendChild(el('span', '', withProvider ? m._providerLabel + ' · ' + m.id : m.id));
        const meta = el('span', 'model-meta');
        const price = priceStr(m);
        if (price) meta.appendChild(document.createTextNode(price));
        if (m.is_free) meta.appendChild(el('span', 'chip-free', 'free'));
        if (m.size_bytes) meta.appendChild(document.createTextNode(' ' + formatSize(m.size_bytes)));
        const pin = el('button', 'btn model-pin', m.pinned ? '★' : '☆');
        pin.title = m.pinned ? 'Unpin' : 'Pin';
        pin.addEventListener('click', (ev) => {
          ev.stopPropagation();
          togglePin(m, pin);
        });
        if (m._providerId === activeProviderId && m.id === activeModelId) item.classList.add('active');
        item.appendChild(meta);
        item.appendChild(pin);
        item.addEventListener('click', async (ev) => {
          ev.stopPropagation();
          dropdown.classList.add('hidden');
          await activateModel(m._providerId, m._providerLabel, m.id, m._cluster);
        });
        holder.appendChild(item);
      }
    };
    let any = false;
    if (pinned.length) { dropdownGroup(holder, 'PINNED'); draw(pinned, true); any = true; }
    for (const g of groups) {
      dropdownGroup(holder, (g.cloud ? '☁ ' : '') + g.name + ' · ' + g.items.length);
      draw(g.items, false);
      any = true;
    }
    if (!any) {
      const empty = el('div', 'dropdown-group');
      empty.appendChild(el('b', '', needles ? 'NO MATCHES' : 'NO MODELS AVAILABLE'));
      holder.appendChild(empty);
    }
  }

  async function togglePin(m, pinEl) {
    try {
      const res = await fetch(`/api/providers/${m._providerId}/favorite`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ model_id: m.id }),
      });
      const out = await res.json();
      m.pinned = out.pinned;
      pinEl.textContent = out.pinned ? '★' : '☆';
    } catch (e) { /* keep */ }
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
  async function loadConversation(id) {
    conversationId = id;
    messagesEl.textContent = '';
    try {
      const res = await fetch(`/api/conversations/${id}/messages`);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const msgs = await res.json();
      if (msgs.length === 0) {
        showEmpty();
      } else {
        for (const m of msgs) {
          if (m.role === 'user') {
            addLog('USER', 'role-user', m.content);
          } else {
            if (m.tool_events && m.tool_events.length) {
              for (const ev of m.tool_events) toolChip(ev);
            }
            const content = addLog('HUB', 'role-hub');
            if (m.reasoning) {
              content.appendChild(el('div', 'log-reasoning', m.reasoning));
            }
            const bodyEl = window.MD.render(m.content);
            content.appendChild(bodyEl);
            if (m.sources && m.sources.length) {
              linkifyCitations(bodyEl, m.sources);
              content.appendChild(sourcesBlock(m.sources));
            }
            if (m.model) {
              content.appendChild(el('div', 'log-meta', m.model));
            }
          }
        }
        scrollBottom();
      }
      document.dispatchEvent(new CustomEvent('hub:conversation'));
    } catch (err) {
      messagesEl.textContent = '';
      showEmpty();
      window.UI.toast('LOAD CONVERSATION FAILED — ' + err.message, 'error');
    }
  }

  async function loadHistory() {
    try {
      const res = await fetch('/api/conversations');
      const conversations = await res.json();
      if (conversations.length === 0) {
        showEmpty();
        return;
      }
      await loadConversation(conversations[0].id);
    } catch (err) {
      showEmpty();
    }
  }

  function newChat() {
    conversationId = null;
    messagesEl.textContent = '';
    showEmpty();
    inputEl.focus();
    document.dispatchEvent(new CustomEvent('hub:conversation'));
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
    loadConversation,
    currentId: () => conversationId,
    addSysLine,
    toggleSearch,
    toast: (msg, kind) => window.UI.toast(msg, kind === 'error' ? 'error' : kind),
    isSearchOpen: () => !searchWrap.classList.contains('hidden'),
    closeSearch: () => {
      searchWrap.classList.add('hidden');
      searchInput.value = '';
      applyFilter('');
    },
    closeDropdown: () => dropdown.classList.add('hidden'),
    openModelPicker: () => openDropdown(),
    isDropdownOpen: () => !dropdown.classList.contains('hidden'),
  };
})();
