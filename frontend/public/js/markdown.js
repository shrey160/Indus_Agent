/*
 * markdown.js — zero-dependency allowlist markdown renderer.
 * Shared by chat log lines and the research report view.
 *
 * XSS rule (MASTER_PLAN §7 / FRONTEND_DESIGN §9): every node is built with
 * el()/textContent; raw HTML in the input renders as literal text. No
 * innerHTML anywhere. Existing `.md-*` CSS in components.css is the styling
 * contract — add new block classes there, never inline styles.
 *
 * API:
 *   window.MD.render(text, opts?)  -> <div class="md-render"> node
 *   window.MD.renderInto(parent, text, opts?) -> replaces parent's children
 *
 * opts.cite(supEl, idx) — optional; when provided, a `[n]` token becomes a
 * clickable <sup class="cite md-cite"> and this callback is fired on click.
 * Without it, `[n]` stays plain text (chat linkifies it post-render via
 * linkifyCitations).
 */
window.MD = (() => {
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  const SEP_SENTINEL = '\u0000';

  /* ── inline: bold / italic / code / link / cite ── */

  const INLINE_RE = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\[\d+\])/g;

  function inlineInto(parent, text, opts) {
    const parts = String(text).split(INLINE_RE);
    for (const part of parts) {
      if (!part) continue;
      let m = part.match(/^\*\*([^*]+)\*\*$/);
      if (m) { parent.appendChild(el('strong', 'md-bold', m[1])); continue; }
      m = part.match(/^\*([^*]+)\*$/);
      if (m) { parent.appendChild(el('em', 'md-em', m[1])); continue; }
      m = part.match(/^`([^`]+)`$/);
      if (m) { parent.appendChild(el('code', 'md-code-inline', m[1])); continue; }
      m = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      if (m) {
        const a = el('a', 'md-link', m[1]);
        a.href = m[2];
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        parent.appendChild(a);
        continue;
      }
      m = part.match(/^\[(\d+)\]$/);
      if (m && opts.cite) {
        const sup = el('sup', 'cite md-cite', part);
        sup.addEventListener('click', (ev) => opts.cite(sup, parseInt(m[1], 10), ev));
        parent.appendChild(sup);
        continue;
      }
      parent.appendChild(document.createTextNode(part));
    }
  }

  function inlineEl(tag, cls, text, opts) {
    const node = el(tag, cls);
    inlineInto(node, text, opts);
    return node;
  }

  /* ── tables ── */

  function splitCells(line) {
    let row = line.trim();
    if (row.startsWith('|')) row = row.slice(1);
    if (row.endsWith('|')) row = row.slice(0, -1);
    const escaped = row.replace(/\\\|/g, SEP_SENTINEL);
    return escaped.split('|').map((c) => c.trim().replace(new RegExp(SEP_SENTINEL, 'g'), '|'));
  }

  function isTableRow(line) {
    const stem = line.trim();
    if (stem.indexOf('|') === -1) return false;
    return stem.startsWith('|') || stem.indexOf(' | ') !== -1 || /\S\|\S/.test(stem);
  }

  const SEP_LINE_RE = /^\s*\|?(\s*:?-+:?\s*\|)+\s*:?-+:?\s*\|?\s*$/;

  function alignOf(cell) {
    const s = cell.trim();
    if (/^:.*:$/.test(s)) return 'center';
    if (/^:/.test(s)) return 'left';
    if (/:$/.test(s)) return 'right';
    return null;
  }

  function buildTable(headCells, bodyLines) {
    const table = el('table', 'md-table');
    const aligns = splitCells(bodyLines[0] || '').map(alignOf);
    while (aligns.length < headCells.length) aligns.push(null);
    const thead = el('thead');
    const hr = el('tr');
    headCells.forEach((c, ci) => {
      hr.appendChild(el('th', aligns[ci] ? 'md-cell-' + aligns[ci] : '', c));
    });
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = el('tbody');
    for (const raw of bodyLines.slice(1)) {
      const cells = splitCells(raw);
      const tr = el('tr');
      for (let ci = 0; ci < headCells.length; ci++) {
        tr.appendChild(el('td', aligns[ci] ? 'md-cell-' + aligns[ci] : '', cells[ci] !== undefined ? cells[ci] : ''));
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    return table;
  }

  /* ── block renderer ── */

  function render(text, opts) {
    const options = opts || {};
    const container = el('div', 'md-render');
    const lines = String(text).split('\n');
    let para = null;
    let listEl = null;
    let listType = null;
    let fence = null;
    let quote = null;
    const flushPara = () => {
      if (para) { container.appendChild(para); para = null; }
    };
    const flushList = () => {
      if (listEl) { container.appendChild(listEl); listEl = null; listType = null; }
    };
    const flushQuote = () => {
      if (quote) { container.appendChild(quote); quote = null; }
    };
    for (let i = 0; i < lines.length; i++) {
      const raw = lines[i];
      if (fence !== null) {
        if (/^```/.test(raw)) {
          flushPara();
          fence = null;
        } else {
          fence.textContent += (fence.textContent ? '\n' : '') + raw;
        }
        continue;
      }
      if (/^```/.test(raw)) {
        flushPara();
        flushList();
        flushQuote();
        fence = el('pre', 'md-code');
        container.appendChild(fence);
        continue;
      }
      if (isTableRow(raw)) {
        flushPara();
        flushList();
        flushQuote();
        const headCells = splitCells(raw);
        const rows = [];
        let j = i + 1;
        for (; j < lines.length && isTableRow(lines[j]); j++) rows.push(lines[j]);
        if (rows.length >= 1 && SEP_LINE_RE.test(rows[0])) {
          container.appendChild(buildTable(headCells, rows));
          i = j - 1;
          continue;
        }
      }
      if (/^(---|\*\*\*)\s*$/.test(raw)) {
        flushPara();
        flushList();
        flushQuote();
        container.appendChild(el('div', 'md-hr'));
        continue;
      }
      if (/^#{1,3}\s/.test(raw)) {
        flushPara();
        flushList();
        flushQuote();
        const level = raw.indexOf(' ');
        container.appendChild(inlineEl('h' + level, 'md-h' + level, raw.slice(level + 1), options));
        continue;
      }
      if (/^\s*[-*]\s+/.test(raw)) {
        flushPara();
        flushQuote();
        if (listType !== 'ul') { flushList(); listEl = el('ul', 'md-list'); container.appendChild(listEl); listType = 'ul'; }
        const li = el('li');
        inlineInto(li, raw.replace(/^\s*[-*]\s+/, ''), options);
        listEl.appendChild(li);
        continue;
      }
      if (/^\s*\d+[.)]\s+/.test(raw)) {
        flushPara();
        flushQuote();
        if (listType !== 'ol') { flushList(); listEl = el('ol', 'md-list'); container.appendChild(listEl); listType = 'ol'; }
        const li = el('li');
        inlineInto(li, raw.replace(/^\s*\d+[.)]\s+/, ''), options);
        listEl.appendChild(li);
        continue;
      }
      if (/^>\s?/.test(raw)) {
        flushPara();
        flushList();
        if (!quote) { quote = el('div', 'md-quote'); container.appendChild(quote); }
        const qline = raw.replace(/^>\s?/, '');
        if (qline.trim()) {
          const pq = el('p');
          inlineInto(pq, qline, options);
          quote.appendChild(pq);
        }
        continue;
      }
      if (!raw.trim()) {
        flushPara();
        flushList();
        flushQuote();
        continue;
      }
      flushList();
      flushQuote();
      if (!para) {
        para = el('p', 'md-p');
        container.appendChild(para);
      }
      if (para.childNodes.length) para.appendChild(document.createTextNode('\n'));
      inlineInto(para, raw, options);
    }
    flushPara();
    flushList();
    flushQuote();
    return container;
  }

  function renderInto(parent, text, opts) {
    const node = render(text, opts);
    parent.textContent = '';
    parent.appendChild(node);
    return node;
  }

  return { render, renderInto };
})();