window.Memory = (() => {
  const { el, toast } = window.UI;
  let root = null;
  let facts = [];
  let soul = { content: '', mtime: null };
  let searchQ = '';
  let filterCategory = '';
  let editingFactId = null;
  let editingSoul = false;
  let expandObserver = null;

  function summary() {
    return facts.length + ' fact' + (facts.length === 1 ? '' : 's');
  }

  function fmtTs(ts) {
    if (!ts) return '';
    try {
      return new Date(ts).toLocaleString('sv-SE');
    } catch (e) {
      return String(ts);
    }
  }

  function categories() {
    const set = new Set(facts.map((f) => f.category).filter(Boolean));
    return Array.from(set).sort();
  }

  function watchExpand() {
    const wrapper = root && root.closest('.sidebar-section');
    if (!wrapper || expandObserver) return;
    expandObserver = new MutationObserver(() => {
      if (wrapper.classList.contains('collapsed')) return;
      if (editingFactId === null) {
        fetchFacts().then(renderFacts);
      }
      fetchSoul();
    });
    expandObserver.observe(wrapper, { attributes: true, attributeFilter: ['class'] });
  }

  async function fetchSoul() {
    try {
      const res = await fetch('/api/soul');
      if (!res.ok) {
        let detail = 'HTTP ' + res.status;
        try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
        throw new Error(detail);
      }
      const data = await res.json();
      const prev = soul.mtime;
      soul = { content: data.content || '', mtime: data.mtime };
      if (prev !== null && data.mtime !== prev && !editingSoul) {
        toast('SOUL RELOADED FROM DISK', 'info');
        renderSoul();
      }
    } catch (err) {
      toast('SOUL LOAD FAILED — ' + err.message, 'error');
    }
  }

  async function saveSoul(ta) {
    try {
      const res = await fetch('/api/soul', {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ content: ta.value }),
      });
      if (!res.ok) {
        let detail = 'HTTP ' + res.status;
        try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
        throw new Error(detail);
      }
      const data = await res.json();
      soul.content = data.content || ta.value;
      soul.mtime = data.mtime || soul.mtime;
      editingSoul = false;
      toast('SOUL SAVED', 'ok');
      renderSoul();
    } catch (err) {
      toast('SOUL SAVE FAILED — ' + err.message, 'error');
    }
  }

  function startEditSoul() {
    editingSoul = true;
    renderSoul();
  }

  function cancelEditSoul() {
    editingSoul = false;
    renderSoul();
  }

  function renderSoul() {
    const soulBlock = root && root.querySelector('.memory-soul-block');
    if (!soulBlock) return;
    soulBlock.textContent = '';
    soulBlock.appendChild(el('div', 'memory-divider', '── SOUL ──'));

    if (editingSoul) {
      const card = el('div', 'soul-card');
      const ta = el('textarea', 'soul-textarea');
      ta.value = soul.content;
      ta.rows = 12;
      ta.setAttribute('aria-label', 'Soul editor');
      card.appendChild(ta);
      const actions = el('div', 'soul-actions');
      const saveBtn = el('button', 'btn btn-primary', '[ SAVE ]');
      saveBtn.addEventListener('click', () => saveSoul(ta));
      const cancel = el('button', 'btn', '[ CANCEL ]');
      cancel.addEventListener('click', cancelEditSoul);
      actions.appendChild(saveBtn);
      actions.appendChild(cancel);
      card.appendChild(actions);
      soulBlock.appendChild(card);
      ta.focus();
    } else {
      const card = el('div', 'soul-card');
      if (!soul.content) {
        card.appendChild(el('div', 'models-empty', 'NO SOUL FILE — WRITE ONE'));
      } else {
        const pre = el('pre', 'soul-pre');
        pre.textContent = soul.content;
        card.appendChild(pre);
      }
      const actions = el('div', 'soul-actions');
      const reload = el('button', 'btn', '[ RELOAD ]');
      reload.addEventListener('click', () => fetchSoul());
      const edit = el('button', 'btn', '[ EDIT ]');
      edit.addEventListener('click', startEditSoul);
      actions.appendChild(reload);
      actions.appendChild(edit);
      card.appendChild(actions);
      soulBlock.appendChild(card);
    }
  }

  async function fetchFacts() {
    try {
      const params = new URLSearchParams();
      if (searchQ) params.set('q', searchQ);
      if (filterCategory) params.set('category', filterCategory);
      const res = await fetch('/api/memories?' + params.toString());
      if (!res.ok) {
        let detail = 'HTTP ' + res.status;
        try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
        throw new Error(detail);
      }
      facts = await res.json();
    } catch (err) {
      facts = [];
      toast('MEMORY LOAD FAILED — ' + err.message, 'error');
    }
    window.Sidebar.refreshSummary('memory');
  }

  async function saveFact(id, ta) {
    const v = ta.value.trim();
    if (!v) {
      toast('FACT CANNOT BE EMPTY', 'error');
      return;
    }
    try {
      const res = await fetch(`/api/memories/${id}`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ fact: v }),
      });
      if (!res.ok) {
        let detail = 'HTTP ' + res.status;
        try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
        throw new Error(detail);
      }
      editingFactId = null;
      toast('FACT SAVED', 'ok');
      await fetchFacts();
      renderFacts();
    } catch (err) {
      toast('FACT SAVE FAILED — ' + err.message, 'error');
    }
  }

  function deleteFact(id) {
    const body = el('div');
    body.appendChild(el('p', 'modal-body-text', 'DELETE FACT — this cannot be undone.'));
    const actions = el('div', 'modal-actions');
    const cancel = el('button', 'btn', '[ CANCEL ]');
    const del = el('button', 'btn btn-danger', '[ DELETE ]');
    actions.appendChild(cancel);
    actions.appendChild(del);
    body.appendChild(actions);
    window.UI.openModal('DELETE FACT', body);
    cancel.addEventListener('click', () => window.UI.closeModal());
    del.addEventListener('click', async () => {
      window.UI.closeModal();
      try {
        const res = await fetch(`/api/memories/${id}`, { method: 'DELETE' });
        const out = await res.json();
        if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
        toast('FACT DELETED', 'ok');
        await fetchFacts();
        renderFacts();
      } catch (err) {
        toast('DELETE FAILED — ' + err.message, 'error');
      }
    });
  }

  function forgetAll() {
    const body = el('div');
    body.appendChild(el('p', 'modal-body-text', 'FORGET EVERYTHING — type FORGET to confirm.'));
    const input = el('input', 'modal-input');
    input.type = 'text';
    input.placeholder = 'FORGET';
    input.setAttribute('aria-label', 'Type FORGET to confirm');
    body.appendChild(input);
    const actions = el('div', 'modal-actions');
    const cancel = el('button', 'btn', '[ CANCEL ]');
    const confirm = el('button', 'btn btn-danger', '[ CONFIRM ]');
    confirm.disabled = true;
    actions.appendChild(cancel);
    actions.appendChild(confirm);
    body.appendChild(actions);
    window.UI.openModal('FORGET ALL MEMORY', body);
    input.addEventListener('input', () => {
      confirm.disabled = input.value.trim() !== 'FORGET';
    });
    input.focus();
    cancel.addEventListener('click', () => window.UI.closeModal());
    confirm.addEventListener('click', async () => {
      window.UI.closeModal();
      try {
        const res = await fetch('/api/memories/forget_all', { method: 'POST' });
        const out = await res.json();
        if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
        toast(out.deleted + ' FACTS PURGED', 'ok');
        await fetchFacts();
        renderFacts();
      } catch (err) {
        toast('FORGET FAILED — ' + err.message, 'error');
      }
    });
  }

  function factRow(f) {
    if (editingFactId === f.id) {
      const r = el('div', 'fact-row fact-row-edit');
      const ta = el('textarea', 'fact-edit-area');
      ta.value = f.fact;
      ta.rows = 3;
      ta.setAttribute('aria-label', 'Edit fact');
      const actions = el('div', 'fact-actions');
      const save = el('button', 'btn btn-primary', '[ SAVE ]');
      save.addEventListener('click', () => saveFact(f.id, ta));
      const cancel = el('button', 'btn', '[ CANCEL ]');
      cancel.addEventListener('click', () => { editingFactId = null; renderFacts(); });
      actions.appendChild(save);
      actions.appendChild(cancel);
      r.appendChild(ta);
      r.appendChild(actions);
      return r;
    }

    const r = el('div', 'fact-row');
    const actions = el('div', 'fact-actions');
    const editBtn = el('button', 'btn', '[ EDIT ]');
    editBtn.addEventListener('click', () => { editingFactId = f.id; renderFacts(); });
    const delBtn = el('button', 'btn', '[ DELETE ]');
    delBtn.addEventListener('click', () => deleteFact(f.id));
    actions.appendChild(editBtn);
    actions.appendChild(delBtn);

    const main = el('div', 'fact-main');
    const text = el('div', 'fact-text', f.fact);
    text.title = f.fact;
    const metaParts = [];
    if (f.category) metaParts.push(f.category);
    if (f.confidence != null) metaParts.push('conf ' + Number(f.confidence).toFixed(1));
    metaParts.push(fmtTs(f.created_at));
    const meta = el('div', 'fact-meta', metaParts.join(' · '));
    if (f.edited) {
      meta.appendChild(document.createTextNode(' · '));
      meta.appendChild(el('span', 'fact-badge-edited', 'EDITED'));
    }
    main.appendChild(text);
    main.appendChild(meta);

    r.appendChild(actions);
    r.appendChild(main);
    return r;
  }

  function renderFacts() {
    const factsBlock = root && root.querySelector('.memory-facts-block');
    if (!factsBlock) return;
    factsBlock.textContent = '';

    const filters = el('div', 'memory-filters');
    const search = el('input', 'filter-input');
    search.type = 'text';
    search.placeholder = '?q=';
    search.value = searchQ;
    search.setAttribute('aria-label', 'Search facts');
    search.addEventListener('input', async () => {
      searchQ = search.value;
      await fetchFacts();
      renderFacts();
    });
    filters.appendChild(search);

    const cats = categories();
    if (cats.length) {
      const select = el('select', 'filter-select');
      select.setAttribute('aria-label', 'Filter category');
      const all = document.createElement('option');
      all.value = '';
      all.textContent = 'ALL';
      select.appendChild(all);
      for (const c of cats) {
        const opt = document.createElement('option');
        opt.value = c;
        opt.textContent = c;
        select.appendChild(opt);
      }
      select.value = filterCategory;
      select.addEventListener('change', async () => {
        filterCategory = select.value;
        await fetchFacts();
        renderFacts();
      });
      filters.appendChild(select);
    }

    const forget = el('button', 'btn btn-danger', '[ FORGET EVERYTHING ]');
    forget.addEventListener('click', forgetAll);
    filters.appendChild(forget);

    factsBlock.appendChild(filters);

    if (!facts.length) {
      factsBlock.appendChild(el('div', 'models-empty', 'NO FACTS YET — CHAT TO BUILD MEMORY'));
      return;
    }

    for (const f of facts) {
      factsBlock.appendChild(factRow(f));
    }
  }

  async function render(body) {
    if (body) root = body;
    if (!root) return;
    root.textContent = '';
    const factsBlock = el('div', 'memory-facts-block');
    const soulBlock = el('div', 'memory-soul-block');
    root.appendChild(factsBlock);
    root.appendChild(soulBlock);
    watchExpand();
    await fetchFacts();
    renderFacts();
    await fetchSoul();
    renderSoul();
  }

  window.Sidebar.registerSection({
    id: 'memory',
    title: 'Memory',
    summary,
    render,
  });
})();
