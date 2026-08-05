window.Memory = (() => {
  const { el, toast } = window.UI;
  let root = null;
  let current = '';
  let editing = false;

  async function loadSoul() {
    if (!root) return;
    try {
      const res = await fetch('/api/soul');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      current = (await res.json()).content || '';
    } catch (err) {
      current = 'SOUL LOAD FAILED — ' + err.message;
    }
    render();
  }

  function viewMode() {
    editing = false;
    root.textContent = '';
    const card = el('div', 'soul-card');
    const pre = el('pre', 'soul-pre');
    pre.textContent = current;
    card.appendChild(pre);
    const actions = el('div', 'soul-actions');
    const reload = el('button', 'btn', '[ RELOAD ]');
    reload.addEventListener('click', loadSoul);
    const edit = el('button', 'btn', '[ EDIT ]');
    edit.addEventListener('click', startEdit);
    actions.appendChild(reload);
    actions.appendChild(edit);
    card.appendChild(actions);
    root.appendChild(card);
  }

  async function save(ta) {
    try {
      const res = await fetch('/api/soul', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: ta.value }),
      });
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || 'HTTP ' + res.status);
      }
      current = (await res.json()).content ?? ta.value;
      toast('SOUL SAVED', 'ok');
    } catch (err) {
      toast('SOUL SAVE FAILED — ' + err.message, 'error');
    }
    viewMode();
  }

  function startEdit() {
    editing = true;
    root.textContent = '';
    const card = el('div', 'soul-card');
    const ta = el('textarea', 'soul-textarea');
    ta.value = current;
    ta.rows = 12;
    card.appendChild(ta);
    const actions = el('div', 'soul-actions');
    const saveBtn = el('button', 'btn btn-primary', '[ SAVE ]');
    saveBtn.addEventListener('click', () => save(ta));
    const cancel = el('button', 'btn', '[ CANCEL ]');
    cancel.addEventListener('click', viewMode);
    actions.appendChild(saveBtn);
    actions.appendChild(cancel);
    card.appendChild(actions);
    root.appendChild(card);
    ta.focus();
  }

  function render(body) {
    if (body) root = body;
    viewMode();
  }

  window.Sidebar.registerSection({ id: 'soul', title: 'Soul', render });
})();
