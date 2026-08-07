window.Conversations = (() => {
  const { el, toast } = window.UI;
  let root = null;
  let conversations = [];
  let renderTimer = null;

  function summary() {
    if (!conversations.length) return '0 chats';
    return conversations.length + ' chat' + (conversations.length === 1 ? '' : 's');
  }

  function fmtTs(ts) {
    if (!ts) return '';
    try {
      return new Date(ts).toLocaleString('sv-SE');
    } catch (e) {
      return String(ts);
    }
  }

  function refreshList() {
    if (renderTimer) clearTimeout(renderTimer);
    renderTimer = setTimeout(() => {
      if (root) render(root);
    }, 300);
  }

  function newChatBtn() {
    const btn = el('button', 'btn btn-primary', '[ + NEW CHAT ]');
    btn.addEventListener('click', () => {
      if (window.Chat && window.Chat.newChat) window.Chat.newChat();
      render(root);
    });
    return btn;
  }

  async function renameConv(id, currentTitle, row) {
    const titleEl = row.querySelector('.conv-title');
    const actions = row.querySelector('.conv-actions');
    if (!titleEl || !actions) return;
    const input = el('input', 'conv-rename-input');
    input.type = 'text';
    input.value = currentTitle;
    input.setAttribute('aria-label', 'Rename conversation');
    titleEl.textContent = '';
    titleEl.appendChild(input);
    actions.style.visibility = 'hidden';
    input.focus();

    async function save() {
      const v = input.value.trim();
      if (!v) {
        render(root);
        return;
      }
      try {
        const res = await fetch(`/api/conversations/${id}`, {
          method: 'PATCH',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ title: v }),
        });
        const out = await res.json();
        if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
        toast('CONVERSATION RENAMED', 'ok');
      } catch (err) {
        toast('RENAME FAILED — ' + err.message, 'error');
      } finally {
        render(root);
      }
    }

    function cancel() {
      render(root);
    }

    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        save();
      } else if (ev.key === 'Escape') {
        ev.preventDefault();
        cancel();
      }
    });
  }

  async function deleteConv(id, messageCount) {
    const body = el('div');
    body.appendChild(el('p', 'modal-body-text',
      `DELETE CHAT — ${messageCount} message${messageCount === 1 ? '' : 's'} will be lost.`));
    const actions = el('div', 'modal-actions');
    const cancel = el('button', 'btn', '[ CANCEL ]');
    const del = el('button', 'btn btn-danger', '[ DELETE ]');
    actions.appendChild(cancel);
    actions.appendChild(del);
    body.appendChild(actions);

    window.UI.openModal('DELETE CHAT', body);
    cancel.addEventListener('click', () => window.UI.closeModal());
    del.addEventListener('click', async () => {
      window.UI.closeModal();
      try {
        const res = await fetch(`/api/conversations/${id}`, { method: 'DELETE' });
        const out = await res.json();
        if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
        toast('CHAT DELETED', 'ok');
        if (window.Chat && window.Chat.currentId && window.Chat.currentId() === id) {
          window.Chat.newChat();
        }
        render(root);
      } catch (err) {
        toast('DELETE FAILED — ' + err.message, 'error');
      }
    });
  }

  function row(c) {
    const active = window.Chat && window.Chat.currentId && window.Chat.currentId() === c.id;
    const r = el('div', 'conv-row' + (active ? ' conv-active' : ''));
    r.setAttribute('role', 'button');
    r.setAttribute('tabindex', '0');
    r.title = c.title || 'Chat';
    r.addEventListener('click', (ev) => {
      if (ev.target.closest('.conv-actions')) return;
      if (window.Chat && window.Chat.loadConversation) window.Chat.loadConversation(c.id);
    });
    r.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        if (window.Chat && window.Chat.loadConversation) window.Chat.loadConversation(c.id);
      }
    });

    const main = el('div', 'conv-main');
    const title = el('span', 'conv-title', c.title || 'Untitled');
    const meta = el('span', 'conv-meta', `${c.message_count || 0} msg${c.message_count === 1 ? '' : 's'} · ${fmtTs(c.created_at)}`);
    main.appendChild(title);
    main.appendChild(meta);

    const actions = el('span', 'conv-actions');
    const rename = el('button', 'btn', '[ RENAME ]');
    rename.setAttribute('aria-label', 'Rename conversation');
    rename.addEventListener('click', (ev) => {
      ev.stopPropagation();
      renameConv(c.id, c.title || '', r);
    });
    const del = el('button', 'btn', '[ DELETE ]');
    del.setAttribute('aria-label', 'Delete conversation');
    del.addEventListener('click', (ev) => {
      ev.stopPropagation();
      deleteConv(c.id, c.message_count || 0);
    });
    actions.appendChild(rename);
    actions.appendChild(del);

    r.appendChild(actions);
    r.appendChild(main);
    return r;
  }

  async function render(body) {
    if (body) root = body;
    if (!root) return;
    root.textContent = '';
    root.appendChild(newChatBtn());

    try {
      const res = await fetch('/api/conversations');
      if (!res.ok) {
        let detail = 'HTTP ' + res.status;
        try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
        throw new Error(detail);
      }
      conversations = await res.json();
    } catch (err) {
      conversations = [];
      const errEl = el('div', 'provider-error', 'LOAD FAILED — ' + err.message);
      const retry = el('button', 'btn', '[ RETRY ]');
      retry.addEventListener('click', () => render(root));
      root.appendChild(errEl);
      root.appendChild(retry);
      window.Sidebar.refreshSummary('conversations');
      return;
    }

    window.Sidebar.refreshSummary('conversations');
    if (!conversations.length) {
      root.appendChild(el('div', 'models-empty', 'NO CHATS — F4 TO START'));
      return;
    }

    for (const c of conversations) {
      root.appendChild(row(c));
    }
  }

  window.Sidebar.registerSection({
    id: 'conversations',
    title: 'Conversations',
    summary,
    render,
  });

  document.addEventListener('hub:conversation', () => refreshList());
})();
