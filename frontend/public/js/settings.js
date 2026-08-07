window.Settings = (() => {
  const { el, toast } = window.UI;
  let root = null;
  let includeKeys = false;
  let importFile = null;

  function summary() {
    return '';
  }

  function toggleLabel() {
    return includeKeys ? '[ ON ▸]' : '[◂ OFF ]';
  }

  async function doExport(btn) {
    btn.disabled = true;
    try {
      const res = await fetch('/api/export?include_keys=' + includeKeys);
      if (!res.ok) {
        let detail = 'HTTP ' + res.status;
        try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
        throw new Error(detail);
      }
      const blob = await res.blob();
      let filename = 'local-ai-hub-export.tar.gz';
      const cd = res.headers.get('content-disposition') || '';
      const match = cd.match(/filename="?([^";]+)"?/);
      if (match) filename = match[1];
      const url = URL.createObjectURL(blob);
      const a = el('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      toast('EXPORT READY', 'ok');
    } catch (err) {
      toast('EXPORT FAILED — ' + err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  async function doImport(btn) {
    if (!importFile) return;
    btn.disabled = true;
    btn.textContent = '[ RESTORING… ]';
    try {
      const fd = new FormData();
      fd.append('file', importFile);
      const res = await fetch('/api/import?confirm=true', { method: 'POST', body: fd });
      let out = {};
      try { out = await res.json(); } catch (e) { /* keep */ }
      if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
      toast('IMPORT DONE — SNAPSHOT ' + out.snapshot, 'ok');
      toast('RESTART ADVISED — RELOAD THE PAGE', 'warn');
      importFile = null;
      render();
    } catch (err) {
      toast('IMPORT FAILED — ' + err.message, 'error');
    } finally {
      btn.disabled = false;
      btn.textContent = '[ IMPORT ]';
    }
  }

  function confirmImport() {
    if (!importFile) {
      toast('PICK A .tar.gz EXPORT FIRST', 'warn');
      return;
    }
    const body = el('div');
    body.appendChild(el('p', 'modal-body-text',
      'IMPORT REPLACES ALL DATA — SNAPSHOT SAVED FIRST.'));
    body.appendChild(el('p', 'modal-body-text', importFile.name));
    const actions = el('div', 'modal-actions');
    const cancel = el('button', 'btn', '[ CANCEL ]');
    const go = el('button', 'btn btn-danger', '[ IMPORT ]');
    actions.appendChild(cancel);
    actions.appendChild(go);
    body.appendChild(actions);
    window.UI.openModal('IMPORT BACKUP', body);
    cancel.addEventListener('click', () => window.UI.closeModal());
    go.addEventListener('click', () => {
      window.UI.closeModal();
      const btn = root && root.querySelector('.settings-import-btn');
      if (btn) doImport(btn);
    });
  }

  function backupBlock() {
    const block = el('div');
    block.appendChild(el('div', 'memory-divider', '── BACKUP ──'));
    const row = el('div', 'docs-toolbar');
    const toggle = el('button', 'btn' + (includeKeys ? ' btn-primary' : ''), toggleLabel());
    toggle.title = 'Include provider API keys in the export';
    toggle.addEventListener('click', () => {
      includeKeys = !includeKeys;
      render();
    });
    row.appendChild(toggle);
    row.appendChild(el('span', 'tool-desc', 'INCLUDE PROVIDER KEYS'));
    block.appendChild(row);
    if (includeKeys) {
      block.appendChild(el('div', 'tool-desc', 'KEYS REQUIRE SAME SECRET_KEY ON RESTORE'));
    }
    const actions = el('div', 'provider-actions');
    const exportBtn = el('button', 'btn btn-primary', '[ EXPORT ]');
    exportBtn.addEventListener('click', () => doExport(exportBtn));
    actions.appendChild(exportBtn);
    block.appendChild(actions);
    return block;
  }

  function restoreBlock() {
    const block = el('div');
    block.appendChild(el('div', 'memory-divider', '── RESTORE ──'));
    const row = el('div', 'docs-toolbar');
    const pickBtn = el('button', 'btn', '[ PICK FILE ]');
    const fileInput = el('input');
    fileInput.type = 'file';
    fileInput.accept = '.tar.gz,.tgz,application/gzip';
    fileInput.style.display = 'none';
    pickBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', (ev) => {
      importFile = ev.target.files && ev.target.files[0] ? ev.target.files[0] : null;
      ev.target.value = '';
      render();
    });
    row.appendChild(pickBtn);
    row.appendChild(fileInput);
    row.appendChild(el('span', 'tool-desc', importFile ? importFile.name : 'NO FILE CHOSEN'));
    block.appendChild(row);
    const actions = el('div', 'provider-actions');
    const importBtn = el('button', 'btn btn-danger settings-import-btn', '[ IMPORT ]');
    importBtn.disabled = !importFile;
    importBtn.addEventListener('click', confirmImport);
    actions.appendChild(importBtn);
    block.appendChild(actions);
    return block;
  }

  async function aboutBlock() {
    const block = el('div');
    block.appendChild(el('div', 'memory-divider', '── ABOUT ──'));
    const info = el('div', 'tool-desc', 'CHECKING…');
    block.appendChild(info);
    try {
      const [infoRes, toolsRes] = await Promise.all([
        fetch('/api/info'),
        fetch('/api/tools'),
      ]);
      const meta = await infoRes.json();
      const tools = toolsRes.ok ? await toolsRes.json() : [];
      const okTools = tools.filter((t) => t.health === 'ok').length;
      info.textContent = meta.name + ' · PHASE ' + meta.phase +
        ' · DB ' + (meta.db ? 'OK' : 'DOWN') +
        ' · TOOLS ' + okTools + '/' + tools.length + ' OK';
    } catch (err) {
      info.textContent = 'API UNREACHABLE';
    }
    block.appendChild(el('div', 'tool-desc', 'SECRET_KEY — KEEP .env WITH ANY BACKUP'));
    return block;
  }

  async function render(body) {
    if (body) root = body;
    if (!root) return;
    root.textContent = '';
    root.appendChild(backupBlock());
    root.appendChild(restoreBlock());
    root.appendChild(await aboutBlock());
  }

  window.Sidebar.registerSection({
    id: 'settings',
    title: 'Settings',
    summary,
    render,
  });
})();
