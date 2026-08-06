window.Rag = (() => {
  const { el, toast, openModal, closeModal } = window.UI;

  const ALLOWED = new Set(['.pdf', '.md', '.txt']);
  let docs = [];
  let autoState = null;
  let pollTimer = null;
  let root = null;
  let chatRoot = null;
  let popoverEl = null;

  function stats() {
    return {
      docs: docs.length,
      chunks: docs.reduce((a, d) => a + (d.chunk_count || 0), 0),
    };
  }

  async function fetchDocs() {
    try {
      const res = await fetch('/api/documents');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      docs = await res.json();
    } catch (err) {
      docs = [];
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

  function confirmDelete(doc) {
    const body = el('div');
    body.appendChild(el('p', '', 'Delete "' + doc.filename + '" and its indexed chunks?'));
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
      const out = await res.json();
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
      const out = await res.json();
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
      const out = await res.json();
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

  function docRow(doc) {
    const row = el('div', 'doc-row');
    const main = el('div', 'doc-main');
    const name = el('span', 'doc-name', doc.filename);
    main.appendChild(name);

    const meta = el('div', 'doc-meta');
    const status = el('span', 'doc-status ' + statusClass(doc.status), statusWord(doc.status));
    meta.appendChild(status);
    meta.appendChild(el('span', 'doc-chunks', (doc.chunk_count || 0) + ' chunks'));
    if (doc.error) {
      const err = el('span', 'doc-error', '· ' + doc.error);
      err.title = doc.error;
      meta.appendChild(err);
    }
    main.appendChild(meta);
    row.appendChild(main);

    const actions = el('div', 'doc-actions');
    const reBtn = el('button', 'btn', '[ REINGEST ]');
    reBtn.addEventListener('click', () => doReingest(doc.id));
    const delBtn = el('button', 'btn btn-danger', '[ DELETE ]');
    delBtn.addEventListener('click', () => confirmDelete(doc));
    actions.appendChild(reBtn);
    actions.appendChild(delBtn);
    row.appendChild(actions);
    return row;
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
      const out = await res.json();
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
    const input = document.getElementById('rag-file-input');
    if (input) input.click();
  }

  function renderBody() {
    if (!root) return;
    root.textContent = '';

    const header = el('div', 'docs-toolbar');
    const uploadBtn = el('button', 'btn btn-primary', '[ UPLOAD ]');
    const fileInput = el('input');
    fileInput.type = 'file';
    fileInput.id = 'rag-file-input';
    fileInput.accept = '.pdf,.md,.txt';
    fileInput.multiple = true;
    fileInput.style.display = 'none';
    uploadBtn.addEventListener('click', openUpload);
    fileInput.addEventListener('change', (ev) => {
      uploadFiles(ev.target.files);
      ev.target.value = '';
    });
    header.appendChild(uploadBtn);
    header.appendChild(fileInput);

    const autoBtn = el('button', 'btn', autoLabel());
    autoBtn.title = 'Toggle auto-retrieval';
    autoBtn.addEventListener('click', () => toggleAuto(autoBtn));
    header.appendChild(autoBtn);

    const summary = el('span', 'docs-summary', stats().docs + ' docs · ' + stats().chunks + ' chunks');
    header.appendChild(summary);
    root.appendChild(header);

    if (!docs.length) {
      root.appendChild(el('div', 'models-empty', 'NO DOCUMENTS — DROP A FILE'));
      return;
    }

    for (const doc of docs) root.appendChild(docRow(doc));
  }

  async function render(body) {
    root = body;
    await fetchDocs();
    renderBody();
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

  window.Sidebar.registerSection({
    id: 'documents',
    title: 'Documents',
    summary: () => {
      const s = stats();
      return s.docs + ' docs · ' + s.chunks + ' chunks';
    },
    action: { label: '+', title: 'Upload document', run: openUpload },
    render,
  });

  return { stats, openUpload, uploadFile };
})();
