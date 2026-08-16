window.Settings = (() => {
  const { el, toast } = window.UI;
  let root = null;
  let includeKeys = false;
  let importFile = null;
  let retentionMonths = null;
  let excludeTools = true;
  let minTurns = 2;

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

  function datasetToggleLabel() {
    return excludeTools ? '[ EXCLUDE TOOL CHATS ON ▸]' : '[◂ OFF ]';
  }

  async function doDatasetDownload(btn) {
    btn.disabled = true;
    try {
      const res = await fetch(
        '/api/dataset/export?exclude_tools=' + excludeTools + '&min_turns=' + minTurns
      );
      if (!res.ok) {
        let detail = 'HTTP ' + res.status;
        try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
        throw new Error(detail);
      }
      const blob = await res.blob();
      let filename = 'local-ai-hub-dataset.jsonl';
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
      if (blob.size === 0) {
        toast('DATASET EMPTY — NO CONVERSATIONS MATCH', 'warn');
      } else {
        toast('DATASET READY — ' + filename, 'ok');
      }
    } catch (err) {
      toast('DATASET FAILED — ' + err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  function datasetBlock() {
    const block = el('div');
    block.appendChild(el('div', 'memory-divider', '── DATASET ──'));
    const row = el('div', 'docs-toolbar');
    const toggle = el('button', 'btn' + (excludeTools ? ' btn-primary' : ''), datasetToggleLabel());
    toggle.title = 'Skip conversations whose assistant turns used tools (tool_events)';
    toggle.addEventListener('click', () => {
      excludeTools = !excludeTools;
      render();
    });
    row.appendChild(toggle);
    row.appendChild(el('span', 'tool-desc', 'EXCLUDE TOOL CHATS'));
    block.appendChild(row);
    const minRow = el('div', 'docs-toolbar');
    minRow.appendChild(el('span', 'tool-desc', 'MIN TURNS'));
    const input = el('input', 'add-input settings-min-turns-input');
    input.type = 'number';
    input.min = '2';
    input.placeholder = '2';
    input.value = String(minTurns);
    input.style.width = '4.5rem';
    input.addEventListener('blur', () => {
      const raw = input.value.trim();
      let n = 2;
      if (raw !== '') {
        n = Number(raw);
        if (!Number.isInteger(n) || n < 2) {
          toast('MIN TURNS MUST BE >= 2', 'error');
          input.value = String(minTurns);
          return;
        }
      }
      minTurns = n;
      input.value = String(minTurns);
    });
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') input.blur();
    });
    minRow.appendChild(input);
    block.appendChild(minRow);
    const actions = el('div', 'provider-actions');
    const dlBtn = el('button', 'btn btn-primary', '[ DOWNLOAD .JSONL ]');
    dlBtn.title = 'ShareGPT-format JSONL for fine-tuning (Unsloth/HF datasets)';
    dlBtn.addEventListener('click', () => doDatasetDownload(dlBtn));
    actions.appendChild(dlBtn);
    block.appendChild(actions);
    block.appendChild(el('div', 'tool-desc', 'ONLY USER + ASSISTANT CONTENT — NO SOURCES / REASONING / KEYS'));
    return block;
  }

  async function loadRetention() {
    try {
      const res = await fetch('/api/settings/retention');
      if (res.ok) {
        const data = await res.json();
        retentionMonths = data.retention_months == null ? null : data.retention_months;
      }
    } catch (err) { /* keep stale value */ }
  }

  async function saveRetention(input) {
    const raw = input.value.trim();
    let months = null;
    if (raw !== '') {
      months = Number(raw);
      if (!Number.isInteger(months) || months < 1 || months > 120) {
        toast('RETENTION MUST BE 1-120 MONTHS OR EMPTY (OFF)', 'error');
        input.value = retentionMonths == null ? '' : String(retentionMonths);
        return;
      }
    }
    const res = await fetch('/api/settings/retention', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ retention_months: months }),
    });
    let out = {};
    try { out = await res.json(); } catch (e) { /* keep */ }
    if (!res.ok) {
      toast('RETENTION SAVE FAILED — ' + (out.detail || 'HTTP ' + res.status), 'error');
      input.value = retentionMonths == null ? '' : String(retentionMonths);
      return;
    }
    retentionMonths = out.retention_months;
    input.value = retentionMonths == null ? '' : String(retentionMonths);
    toast(retentionMonths == null ? 'RETENTION OFF' : 'RETENTION ' + retentionMonths + ' MONTHS', 'ok');
  }

  async function doArchive(btn) {
    btn.disabled = true;
    try {
      const res = await fetch('/api/retention/archive', { method: 'POST' });
      let out = {};
      try { out = await res.json(); } catch (e) { /* keep */ }
      if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
      toast(out.archived > 0
        ? 'ARCHIVED ' + out.archived + ' CHATS — ' + out.file
        : 'NOTHING OLD ENOUGH TO ARCHIVE', 'ok');
      document.dispatchEvent(new CustomEvent('hub:conversation'));
    } catch (err) {
      toast('ARCHIVE FAILED — ' + err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  function confirmArchive() {
    if (retentionMonths == null) {
      const btn = root && root.querySelector('.settings-archive-btn');
      if (btn) doArchive(btn);
      return;
    }
    const body = el('div');
    body.appendChild(el('p', 'modal-body-text',
      'ARCHIVE CHATS OLDER THAN ' + retentionMonths + ' MONTHS.'));
    body.appendChild(el('p', 'modal-body-text',
      'ROWS ARE REMOVED FROM THE DB — JSON SAVED TO exports/.'));
    const actions = el('div', 'modal-actions');
    const cancel = el('button', 'btn', '[ CANCEL ]');
    const go = el('button', 'btn btn-danger', '[ ARCHIVE ]');
    actions.appendChild(cancel);
    actions.appendChild(go);
    body.appendChild(actions);
    window.UI.openModal('ARCHIVE OLD CHATS', body);
    cancel.addEventListener('click', () => window.UI.closeModal());
    go.addEventListener('click', () => {
      window.UI.closeModal();
      const btn = root && root.querySelector('.settings-archive-btn');
      if (btn) doArchive(btn);
    });
  }

  async function doVacuum(btn) {
    btn.disabled = true;
    try {
      const res = await fetch('/api/maintenance/vacuum', { method: 'POST' });
      if (!res.ok) {
        let out = {};
        try { out = await res.json(); } catch (e) { /* keep */ }
        throw new Error(out.detail || 'HTTP ' + res.status);
      }
      toast('VACUUM DONE', 'ok');
    } catch (err) {
      toast('VACUUM FAILED — ' + err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  function housekeepingBlock() {
    const block = el('div');
    block.appendChild(el('div', 'memory-divider', '── HOUSEKEEPING ──'));
    const row = el('div', 'docs-toolbar');
    row.appendChild(el('span', 'tool-desc', 'ARCHIVE CHATS OLDER THAN'));
    const input = el('input', 'add-input settings-retention-input');
    input.type = 'number';
    input.min = '1';
    input.max = '120';
    input.placeholder = 'OFF';
    input.style.width = '4.5rem';
    input.value = retentionMonths == null ? '' : String(retentionMonths);
    input.addEventListener('blur', () => saveRetention(input));
    input.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') input.blur();
    });
    row.appendChild(input);
    row.appendChild(el('span', 'tool-desc', 'MONTHS'));
    block.appendChild(row);
    const actions = el('div', 'provider-actions');
    const saveBtn = el('button', 'btn', '[ SAVE ]');
    saveBtn.addEventListener('click', () => saveRetention(input));
    const archiveBtn = el('button', 'btn btn-danger settings-archive-btn', '[ ARCHIVE NOW ]');
    archiveBtn.addEventListener('click', confirmArchive);
    const vacuumBtn = el('button', 'btn', '[ VACUUM NOW ]');
    vacuumBtn.addEventListener('click', () => doVacuum(vacuumBtn));
    actions.appendChild(saveBtn);
    actions.appendChild(archiveBtn);
    actions.appendChild(vacuumBtn);
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
    await loadRetention();
    root.appendChild(backupBlock());
    root.appendChild(restoreBlock());
    root.appendChild(datasetBlock());
    root.appendChild(housekeepingBlock());
    root.appendChild(await aboutBlock());
  }

  window.Sidebar.registerSection({
    id: 'settings',
    title: 'Settings',
    summary,
    render,
  });
})();
