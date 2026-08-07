window.Sidebar = (() => {
  const defs = new Map();
  const order = [];
  const nodes = new Map();
  let sidebarEl;
  let scrimEl;
  let containerEl;
  let firstSectionId = null;

  function isNarrow() {
    return window.innerWidth <= 720;
  }

  function applyState(open) {
    sidebarEl.classList.toggle('open', open);
    scrimEl.classList.toggle('hidden', !(open && isNarrow()));
    localStorage.setItem('sidebarOpen', open ? '1' : '0');
  }

  function collapsedKey(id) {
    return 'sidebar.collapsed.' + id;
  }

  function isCollapsed(id) {
    const saved = localStorage.getItem(collapsedKey(id));
    if (saved !== null) return saved === '1';
    return id !== firstSectionId;
  }

  function setCollapsed(id, collapsed) {
    localStorage.setItem(collapsedKey(id), collapsed ? '1' : '0');
  }

  function node(id) {
    return nodes.get(id);
  }

  function updateHeader(id) {
    const n = node(id);
    if (!n) return;
    const expanded = !n.wrapper.classList.contains('collapsed');
    n.head.setAttribute('aria-expanded', String(expanded));
    n.caret.textContent = expanded ? '▾' : '▸';
  }

  function ensureBuilt(id) {
    const n = node(id);
    if (!n || n.built) return;
    n.built = true;
    defs.get(id).render(n.body);
  }

  function toggleSection(id, expand) {
    const n = node(id);
    if (!n) return;
    const collapsed = expand !== undefined ? !expand : !n.wrapper.classList.contains('collapsed');
    n.wrapper.classList.toggle('collapsed', collapsed);
    setCollapsed(id, collapsed);
    updateHeader(id);
    if (!collapsed) ensureBuilt(id);
  }

  function headerFor(def) {
    const el = window.UI.el;
    const head = el('div', 'section-head');
    head.setAttribute('role', 'button');
    head.setAttribute('tabindex', '0');
    head.dataset.sectionId = def.id;
    const caret = el('span', 'section-caret', '');
    const name = el('span', 'section-name', def.title.toUpperCase());
    head.appendChild(caret);
    head.appendChild(name);
    let summary = null;
    if (def.summary) {
      summary = el('span', 'section-summary', def.summary());
      head.appendChild(summary);
    }
    if (def.action) {
      const act = el('button', 'section-action', def.action.label);
      act.type = 'button';
      act.dataset.action = def.id;
      if (def.action.title) act.title = def.action.title;
      act.setAttribute('aria-label', (def.action.title || 'Action') + ' for ' + def.title);
      head.appendChild(act);
    }
    return { head, caret, summary };
  }

  function renderSections() {
    containerEl.textContent = '';
    if (!firstSectionId && order.length) firstSectionId = order[0];
    for (const id of order) {
      const def = defs.get(id);
      const wrapper = document.createElement('section');
      wrapper.className = 'sidebar-section' + (isCollapsed(id) ? ' collapsed' : '');
      wrapper.dataset.sectionId = id;
      const built = headerFor(def);
      built.head.setAttribute('aria-controls', 'sidebar-body-' + id);
      const body = document.createElement('div');
      body.className = 'sidebar-section-body';
      body.id = 'sidebar-body-' + id;
      body.setAttribute('role', 'region');
      body.setAttribute('aria-label', def.title);
      wrapper.appendChild(built.head);
      wrapper.appendChild(body);
      containerEl.appendChild(wrapper);
      nodes.set(id, { wrapper, head: built.head, body, caret: built.caret, summary: built.summary, built: false });
      updateHeader(id);
      if (!isCollapsed(id)) ensureBuilt(id);
    }
  }

  function onContainerClick(ev) {
    if (!ev.target || !ev.target.closest) return;
    const actionEl = ev.target.closest('[data-action]');
    if (actionEl) {
      ev.stopPropagation();
      const def = defs.get(actionEl.dataset.action);
      if (def && def.action) def.action.run();
      return;
    }
    const head = ev.target.closest('.section-head');
    if (!head) return;
    const id = head.dataset.sectionId;
    if (id) toggleSection(id);
  }

  function onContainerKeydown(ev) {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    if (!ev.target || !ev.target.closest) return;
    if (ev.target.closest('[data-action]')) return;
    const head = ev.target.closest('.section-head');
    if (!head) return;
    ev.preventDefault();
    const id = head.dataset.sectionId;
    if (id) toggleSection(id);
  }

  function openSection(id) {
    toggleSection(id, true);
  }

  function refreshSummary(id) {
    const n = node(id);
    const def = defs.get(id);
    if (!n || !def || !def.summary || !n.summary) return;
    n.summary.textContent = def.summary();
  }

  function registerSection(def) {
    if (!def || !def.id || defs.has(def.id)) return;
    defs.set(def.id, def);
    order.push(def.id);
  }

  function open() { applyState(true); }
  function close() { applyState(false); }
  function toggle() { applyState(!sidebarEl.classList.contains('open')); }
  function isOpen() { return sidebarEl.classList.contains('open'); }

  function init() {
    sidebarEl = document.getElementById('sidebar');
    scrimEl = document.getElementById('scrim');
    containerEl = document.getElementById('sidebar-sections');
    document.getElementById('sidebar-toggle').addEventListener('click', toggle);
    scrimEl.addEventListener('click', close);
    containerEl.addEventListener('click', onContainerClick);
    containerEl.addEventListener('keydown', onContainerKeydown);
    window.addEventListener('resize', () => {
      scrimEl.classList.toggle('hidden', !(sidebarEl.classList.contains('open') && isNarrow()));
    });
    renderSections();
    applyState(localStorage.getItem('sidebarOpen') === '1');
    for (const id of order) {
      const def = defs.get(id);
      if (def && typeof def.init === 'function') {
        try { Promise.resolve(def.init()).catch((err) => console.error('sidebar init failed for', id, err)); } catch (err) { console.error('sidebar init failed for', id, err); }
      }
    }
  }

  return { init, open, close, toggle, isOpen, registerSection, openSection, refreshSummary };
})();
