window.Attach = (() => {
  const el = window.UI.el;

  const ACCEPT = ['.txt', '.md', '.pdf', '.docx'];
  const MAX_BYTES = 20 * 1024 * 1024;
  const MAX_FILES = 5;

  function fmtBytes(n) {
    if (!n && n !== 0) return '';
    if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
    return Math.max(1, Math.round(n / 1024)) + ' KB';
  }

  function fmtNum(n) {
    return Number(n || 0).toLocaleString('en-US');
  }

  function extOf(name) {
    const i = name.lastIndexOf('.');
    return i === -1 ? '' : name.slice(i).toLowerCase();
  }

  function warnSuffix(att) {
    const n = att && att.pages_without_text && att.pages_without_text.length;
    return n ? ' · ⚠ ' + n + ' image-only page' + (n > 1 ? 's' : '') : '';
  }

  function check(file) {
    const ext = extOf(file.name);
    if (ext === '.doc') throw new Error('legacy .doc not supported — convert to .docx');
    if (!ACCEPT.includes(ext)) {
      throw new Error('unsupported file type: ' + (ext || '(none)') + '. Allowed: .md, .pdf, .txt, .docx');
    }
    if (file.size > MAX_BYTES) throw new Error('file too large (max 20MB)');
    return true;
  }

  async function extract(file) {
    const data = new FormData();
    data.append('file', file);
    const res = await fetch('/api/extract', { method: 'POST', body: data });
    let out = {};
    try { out = await res.json(); } catch (e) { /* keep */ }
    if (!res.ok) throw new Error(out.detail || 'HTTP ' + res.status);
    return out;
  }

  function addChip(container, att, opts) {
    const chip = el('span', 'attach-chip');
    const label = el('span', '', att.name + ' · ' + fmtBytes(att.size) + ' · ' + fmtNum(att.chars) + ' chars');
    chip.appendChild(label);
    const warn = warnSuffix(att);
    if (warn) label.appendChild(el('span', 'attach-warn', warn));
    if (opts && opts.onRemove) {
      const rm = el('button', 'attach-chip-x', '[×]');
      rm.type = 'button';
      rm.title = 'Remove attachment';
      rm.addEventListener('click', () => {
        chip.remove();
        opts.onRemove();
      });
      chip.appendChild(rm);
    }
    container.appendChild(chip);
    return chip;
  }

  function renderUserChips(container, atts) {
    if (!atts || !atts.length) return;
    const wrap = el('div', 'attach-chips attach-chips-log');
    for (const att of atts) {
      const chip = el('span', 'attach-chip');
      chip.appendChild(el('span', '', '[ATTACH ' + att.name + ' · ' + fmtNum(att.chars) + ' chars]'));
      const warn = warnSuffix(att);
      if (warn) chip.appendChild(el('span', 'attach-warn', warn));
      wrap.appendChild(chip);
    }
    container.appendChild(wrap);
  }

  return { ACCEPT, MAX_BYTES, MAX_FILES, fmtBytes, fmtNum, check, extract, addChip, renderUserChips };
})();
