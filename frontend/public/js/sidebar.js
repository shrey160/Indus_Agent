window.Sidebar = (() => {
  const sections = [];
  let sidebarEl;
  let scrimEl;

  function isNarrow() {
    return window.innerWidth <= 720;
  }

  function applyState(open) {
    sidebarEl.classList.toggle('open', open);
    scrimEl.classList.toggle('hidden', !(open && isNarrow()));
    localStorage.setItem('sidebarOpen', open ? '1' : '0');
  }

  function divider(title) {
    const el = window.UI.el;
    const label = title.toUpperCase();
    const node = el('div', 'section-divider');
    node.appendChild(document.createTextNode('── '));
    node.appendChild(el('b', '', label));
    node.appendChild(document.createTextNode(' ' + '─'.repeat(Math.max(2, 26 - label.length))));
    return node;
  }

  function renderSections() {
    const container = document.getElementById('sidebar-sections');
    container.textContent = '';
    for (const section of sections) {
      const wrapper = document.createElement('section');
      wrapper.className = 'sidebar-section';
      wrapper.appendChild(divider(section.title));
      const body = document.createElement('div');
      body.className = 'sidebar-section-body';
      wrapper.appendChild(body);
      container.appendChild(wrapper);
      section.render(body);
    }
  }

  function open() { applyState(true); }
  function close() { applyState(false); }
  function toggle() { applyState(!sidebarEl.classList.contains('open')); }
  function isOpen() { return sidebarEl.classList.contains('open'); }
  function registerSection(section) { sections.push(section); }

  function init() {
    sidebarEl = document.getElementById('sidebar');
    scrimEl = document.getElementById('scrim');
    document.getElementById('sidebar-toggle').addEventListener('click', toggle);
    scrimEl.addEventListener('click', close);
    window.addEventListener('resize', () => {
      scrimEl.classList.toggle('hidden', !(sidebarEl.classList.contains('open') && isNarrow()));
    });
    renderSections();
    applyState(localStorage.getItem('sidebarOpen') === '1');
  }

  return { init, open, close, toggle, isOpen, registerSection };
})();
