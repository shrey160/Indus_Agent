window.UI = (() => {
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  let toastStack = null;

  function toast(message, kind) {
    if (!toastStack) {
      toastStack = el('div', 'toast-stack');
      document.body.appendChild(toastStack);
    }
    const note = el('div', 'toast' + (kind ? ' toast-' + kind : ''), message);
    if (kind === 'error') {
      const close = el('button', 'toast-close', '[✕]');
      close.setAttribute('aria-label', 'Dismiss');
      close.addEventListener('click', () => note.remove());
      note.appendChild(close);
    } else {
      setTimeout(() => note.remove(), 4000);
    }
    toastStack.appendChild(note);
  }

  let backdrop = null;

  function modalOpen() {
    return backdrop !== null;
  }

  function openModal(title, bodyNode) {
    closeModal();
    backdrop = el('div', 'modal-backdrop');
    const modal = el('div', 'modal');
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-label', title);
    modal.appendChild(el('div', 'modal-title', title));
    const body = el('div', 'modal-body');
    body.appendChild(bodyNode);
    modal.appendChild(body);
    const actions = el('div', 'modal-actions');
    const closeBtn = el('button', 'btn', '[ CLOSE ]');
    closeBtn.addEventListener('click', closeModal);
    actions.appendChild(closeBtn);
    modal.appendChild(actions);
    backdrop.appendChild(modal);
    backdrop.addEventListener('click', (ev) => {
      if (ev.target === backdrop) closeModal();
    });
    document.body.appendChild(backdrop);
    closeBtn.focus();
  }

  function closeModal() {
    if (backdrop) {
      backdrop.remove();
      backdrop = null;
    }
  }

  return { el, toast, openModal, closeModal, modalOpen };
})();
