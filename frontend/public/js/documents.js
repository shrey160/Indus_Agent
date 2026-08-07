window.Documents = (() => {
  const { el, toast, openModal, closeModal } = window.UI;

  const ALLOWED = new Set(['.pdf', '.md', '.txt']);
  let docs = [];
  let autoState = null;
  let pollTimer = null;
  let root = null;
  let chatRoot = null;

  function stats() {
    return {
      docs: docs.length,
      chunks: docs.reduce((a, d) => a + (d.chunk_count || 0), 0),
    };
  }

  async function fetchDocs() {
    try {
      const res = await fetch('/api/documents');
      if (!res.ok) {
        let detail = 'HTTP ' + res.status;
        try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
        throw new Error(detail);
      }
      docs = await res.json();
    } catch (err) {
      docs = [];
      toast('DOCUMENTS LOAD FAILED — ' + err.message, 'error');
    }
  }

  function needsPoll() {
    return docs.some((d) => d.status === 'pending' || d.status === 'processing');
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      await fetchDocs();
      if (root) renderBody();
      window.Sidebar.refreshSummary('documents');
      window.Sidebar.refreshSummary('tools');
      if (!needsPoll()) stopPolling();
    }, 5000);
  }

  function stopPolling() {
    if (!pollTimer) return;
    clearInterval(pollTimer);
    pollTimer = null;
  }

  function statusClass(s) {
    if (s === 'ready') return 'doc-ready';
    if (s === 'failed') return 'doc-failed';
    return 'doc-pending';
  }

  function statusWord(s) {
    if (s === 'ready') return 'READY';
    if (s === 'failed') return 'FAILED';
    if (s === 'pending') return 'PENDING';
    if (s === 'processing') return 'PROCESSING';
    return String(s).toUpperCase();
  }

  function fmtTs(ts) {
    if (!ts) return '';
    try { return new Date(ts).toLocaleString('sv-SE'); }
    catch (e) { return String(ts); }
  }

  function confirmDelete(doc) {
    const body = el('div');
    body.appendChild(el('p', 'modal-body-text', 'Delete "' + doc.filename + '" and its indexed chunks?'));
    const actions = el('div', 'modal-actions');
    const cancel = el('button', 'btn', '[ CANCEL ]');
    const del = el('button', 'btn btn-danger', '[ DELETE ]');
    cancel.addEventListener('click', closeModal);
    del.addEventListener('click', async () => {
      closeModal();
      await doDelete(doc.id);
    });
    actions.appendChild(cancel);
    actions.appendChild(del);
    body.appendChild(actions);
    openModal('DELETE DOCUMENT', body);
    cancel.focus();
  }

  async function doDelete(id) {
    try {
      const res = await fetch('/api/documents/' + encodeURIComponent(id), { method: 'DELETE' });
      let out = {};
      try { out = await res.json(); } catch (e) { /* keep */ }
      if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
      await fetchDocs();
      if (root) renderBody();
      window.Sidebar.refreshSummary('documents');
      window.Sidebar.refreshSummary('tools');
      toast('DOCUMENT DELETED', 'ok');
    } catch (err) {
      toast('DELETE FAILED — ' + err.message, 'error');
    }
  }

  async function doReingest(id) {
    try {
      const res = await fetch('/api/documents/' + encodeURIComponent(id) + '/reingest', { method: 'POST' });
      let out = {};
      try { out = await res.json(); } catch (e) { /* keep */ }
      if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
      await fetchDocs();
      if (root) renderBody();
      startPolling();
      toast('REINGEST STARTED', 'ok');
    } catch (err) {
      toast('REINGEST FAILED — ' + err.message, 'error');
    }
  }

  async function toggleAuto(btn) {
    btn.disabled = true;
    try {
      const res = await fetch('/api/rag/toggle_auto', { method: 'POST' });
      let out = {};
      try { out = await res.json(); } catch (e) { /* keep */ }
      if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
      autoState = out.rag_auto;
      if (root) renderBody();
      toast('AUTO RAG ' + (autoState ? 'ON' : 'OFF'), 'ok');
    } catch (err) {
      toast('TOGGLE FAILED — ' + err.message, 'error');
    } finally {
      btn.disabled = false;
    }
  }

  function docCard(doc) {
    const card = el('div', 'provider-card');
    const head = el('div', 'provider-head');
    head.appendChild(el('span', 'provider-name', doc.filename));
    card.appendChild(head);

    const meta = el('div', 'provider-state');
    const status = el('span', 'doc-status ' + statusClass(doc.status), statusWord(doc.status));
    if (doc.error) status.title = doc.error;
    meta.appendChild(status);
    meta.appendChild(document.createTextNode(' · '));
    meta.appendChild(el('span', 'doc-chunks', (doc.chunk_count || 0) + ' chunks'));
    meta.appendChild(document.createTextNode(' · '));
    meta.appendChild(el('span', 'doc-created', fmtTs(doc.created_at)));
    if (doc.error) {
      meta.appendChild(document.createTextNode(' · '));
      const err = el('span', 'doc-error', doc.error);
      err.title = doc.error;
      meta.appendChild(err);
    }
    card.appendChild(meta);

    const actions = el('div', 'provider-actions');
    const reBtn = el('button', 'btn', '[ REINGEST ]');
    reBtn.addEventListener('click', () => doReingest(doc.id));
    const delBtn = el('button', 'btn btn-danger', '[ DELETE ]');
    delBtn.addEventListener('click', () => confirmDelete(doc));
    actions.appendChild(reBtn);
    actions.appendChild(delBtn);
    card.appendChild(actions);
    return card;
  }

  function autoLabel() {
    if (autoState === true) return '[ AUTO ▸]';
    if (autoState === false) return '[◂ AUTO ]';
    return '[ AUTO ]';
  }

  async function uploadFile(file) {
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!ALLOWED.has(ext)) {
      toast('SKIPPED — only .pdf, .md, .txt allowed', 'warn');
      return;
    }
    const data = new FormData();
    data.append('file', file);
    try {
      const res = await fetch('/api/documents', { method: 'POST', body: data });
      let out = {};
      try { out = await res.json(); } catch (e) { /* keep */ }
      if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
      toast('UPLOADED — ' + file.name, 'ok');
      await fetchDocs();
      if (root) renderBody();
      startPolling();
    } catch (err) {
      toast('UPLOAD FAILED — ' + err.message, 'error');
    }
  }

  async function uploadFiles(files) {
    for (const file of files) await uploadFile(file);
    if (root) renderBody();
    window.Sidebar.refreshSummary('documents');
    window.Sidebar.refreshSummary('tools');
  }

  function openUpload() {
    const input = document.getElementById('doc-file-input');
    if (input) input.click();
  }

  function renderBody() {
    if (!root) return;
    root.textContent = '';

    const toolbar = el('div', 'docs-toolbar');
    const uploadBtn = el('button', 'btn btn-primary', '[ UPLOAD ]');
    const fileInput = el('input');
    fileInput.type = 'file';
    fileInput.id = 'doc-file-input';
    fileInput.accept = '.pdf,.md,.txt';
    fileInput.multiple = true;
    fileInput.style.display = 'none';
    uploadBtn.addEventListener('click', openUpload);
    fileInput.addEventListener('change', (ev) => {
      uploadFiles(ev.target.files);
      ev.target.value = '';
    });
    toolbar.appendChild(uploadBtn);
    toolbar.appendChild(fileInput);

    const autoBtn = el('button', 'btn', autoLabel());
    autoBtn.title = 'Toggle auto-retrieval';
    autoBtn.addEventListener('click', () => toggleAuto(autoBtn));
    toolbar.appendChild(autoBtn);

    const s = stats();
    toolbar.appendChild(el('span', 'docs-summary', s.docs + ' docs · ' + s.chunks + ' chunks'));
    root.appendChild(toolbar);

    if (!docs.length) {
      root.appendChild(el('div', 'models-empty', 'NO DOCUMENTS — DROP A FILE OR F9'));
      return;
    }

    for (const doc of docs) root.appendChild(docCard(doc));
  }

  async function render(body) {
    if (body) root = body;
    if (!root) return;
    await fetchDocs();
    renderBody();
    window.Sidebar.refreshSummary('documents');
    window.Sidebar.refreshSummary('tools');
    if (needsPoll()) startPolling();
  }

  /* ── drag-and-drop on chat root ── */
  function initDragDrop() {
    chatRoot = document.querySelector('main.chat');
    if (!chatRoot) return;
    let counter = 0;
    chatRoot.addEventListener('dragenter', (ev) => {
      if (!ev.dataTransfer.types.contains('Files')) return;
      ev.preventDefault();
      counter++;
      chatRoot.classList.add('drop-highlight');
    });
    chatRoot.addEventListener('dragover', (ev) => {
      if (!ev.dataTransfer.types.contains('Files')) return;
      ev.preventDefault();
    });
    chatRoot.addEventListener('dragleave', (ev) => {
      counter--;
      if (counter <= 0) chatRoot.classList.remove('drop-highlight');
    });
    chatRoot.addEventListener('drop', (ev) => {
      ev.preventDefault();
      counter = 0;
      chatRoot.classList.remove('drop-highlight');
      if (ev.dataTransfer.files && ev.dataTransfer.files.length) {
        uploadFiles(ev.dataTransfer.files);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initDragDrop);
  } else {
    initDragDrop();
  }

  async function initSummary() {
    await fetchDocs();
    window.Sidebar.refreshSummary('documents');
    window.Sidebar.refreshSummary('tools');
    if (needsPoll()) startPolling();
  }

  window.Sidebar.registerSection({
    id: 'documents',
    title: 'Documents',
    summary: () => {
      const s = stats();
      return s.docs + ' docs · ' + s.chunks + ' chunks';
    },
    action: { label: '+', title: 'Upload document', run: openUpload },
    render,
    init: initSummary,
  });

  return { stats, openUpload, uploadFile };
})();
