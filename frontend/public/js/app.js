(() => {
  const el = window.UI.el;

  const FN_MAP = [
    { key: 'F1', label: 'HELP', action: showHelp },
    { key: 'F2', label: 'PROVIDERS', action: () => {
        window.Sidebar.openSection('providers');
        window.Sidebar.open();
      } },
    { key: 'F3', label: 'TOOLS', action: () => {
        window.Sidebar.openSection('tools');
        window.Sidebar.open();
      } },
    { key: 'F4', label: 'NEW CHAT', action: () => window.Chat.newChat() },
    { key: 'F5', label: 'RE-DETECT', action: () => window.Providers && window.Providers.redetect() },
    { key: 'F6', label: 'MEMORIES', action: () => {
        window.Sidebar.openSection('memory');
        window.Sidebar.open();
      } },
    { key: 'F9', label: 'UPLOAD', action: () => {
        window.Sidebar.openSection('documents');
        window.Sidebar.open();
        if (window.Documents && window.Documents.openUpload) window.Documents.openUpload();
      } },
    { key: 'F10', label: 'SEARCH', action: () => window.Chat.toggleSearch() },
    { key: 'F11', label: 'EXPORT', action: () => {
        window.Sidebar.openSection('settings');
        window.Sidebar.open();
      } },
    { key: 'F12', label: 'RESEARCH', action: () => {
        window.Sidebar.open();
        window.Sidebar.openSection('research');
      } },
    { key: 'ESC', label: 'CLOSE', action: onEscape },
  ];

  function showHelp() {
    const table = el('table');
    const rows = [
      ['F1', 'This cheatsheet'],
      ['F2', 'Toggle providers sidebar'],
      ['F3', 'Tools sidebar'],
      ['F4', 'New chat'],
      ['F5', 'Re-detect providers'],
      ['F6', 'Memories sidebar'],
      ['F9', 'Upload documents'],
      ['F10', 'Filter chat log'],
      ['F11', 'Export / settings'],
      ['F12', 'Research section'],
      ['Ctrl/⌘ K', 'Focus model picker'],
      ['ESC', 'Close modal / sidebar / search'],
      ['⏎', 'Send message'],
      ['⇧⏎', 'Newline in composer'],
      ['■ STOP', 'Abort streaming reply'],
    ];
    for (const [k, desc] of rows) {
      const tr = el('tr');
      tr.appendChild(el('td', '', k));
      tr.appendChild(el('td', '', desc));
      table.appendChild(tr);
    }
    window.UI.openModal('SHORTCUTS', table);
  }

  function onEscape() {
    if (window.UI.modalOpen()) {
      window.UI.closeModal();
    } else if (window.Chat.isSearchOpen()) {
      window.Chat.closeSearch();
    } else if (window.Chat.isDropdownOpen && window.Chat.isDropdownOpen()) {
      window.Chat.closeDropdown();
    } else if (window.Research && window.Research.isViewOpen()) {
      window.Research.closeView();
    } else if (window.Sidebar.isOpen()) {
      window.Sidebar.close();
    }
  }

  function buildFnbar() {
    const bar = document.getElementById('fnbar');
    for (const entry of FN_MAP) {
      const item = el('span', 'fn-key');
      item.appendChild(el('b', '', entry.key));
      item.appendChild(document.createTextNode(' ' + entry.label));
      item.addEventListener('click', entry.action);
      bar.appendChild(item);
    }
  }

  function routeKeys() {
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') {
        onEscape();
        return;
      }
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'k') {
        ev.preventDefault();
        if (window.Chat && window.Chat.openModelPicker) window.Chat.openModelPicker();
        return;
      }
      if (!/^F\d{1,2}$/.test(ev.key)) return;
      const entry = FN_MAP.find((e) => e.key === ev.key.toUpperCase());
      if (entry) {
        ev.preventDefault();
        entry.action();
      }
    });
  }

  async function pollStatus() {
    const dot = document.getElementById('status-dot');
    try {
      const [activeRes, providersRes] = await Promise.all([
        fetch('/api/providers/active'),
        fetch('/api/providers'),
      ]);
      const active = await activeRes.json();
      const providers = await providersRes.json();
      if (!active.provider_id || !active.model) {
        dot.className = 'dot dot-err';
        dot.title = 'No model activated — F2 to open Providers';
        return;
      }
      const provider = providers.find((p) => p.id === active.provider_id);
      if (!provider) {
        dot.className = 'dot dot-err';
        dot.title = 'Active provider no longer exists';
        return;
      }
      if (provider.status.state === 'up') {
        dot.className = 'dot dot-ok';
        dot.title = active.provider_name + ' · ' + active.model + ' — healthy';
      } else if (provider.status.state === 'up_empty') {
        dot.className = 'dot dot-warn';
        dot.title = active.provider_name + ' is up but has no models';
      } else if (provider.status.state === 'down') {
        dot.className = 'dot dot-err';
        dot.title = active.provider_name + ' is down — ' + (provider.status.error || 'unreachable');
      } else if (provider.status.state === 'bad_key') {
        dot.className = 'dot dot-err';
        dot.title = active.provider_name + ' — invalid API key, open Providers to edit';
      } else if (provider.status.state === 'no_credits') {
        dot.className = 'dot dot-err';
        dot.title = active.provider_name + ' — no credits';
      } else if (provider.status.state === 'unreachable') {
        dot.className = 'dot dot-grey';
        dot.title = active.provider_name + ' is unreachable — local models still available';
      } else {
        dot.className = 'dot dot-pending';
        dot.title = 'Checking providers…';
      }
    } catch (err) {
      dot.className = 'dot dot-err';
      dot.title = 'API unreachable';
    }
  }

  window.Sidebar.init();
  window.Chat.init();
  buildFnbar();
  routeKeys();
  pollStatus();
  setInterval(pollStatus, 30000);
})();
