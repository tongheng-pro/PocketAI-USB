// Small shared UI helpers: toasts, dialogs, formatting, element creation.
import { escapeHtml } from './markdown.js';

export { escapeHtml };

export function $(sel, root = document) { return root.querySelector(sel); }

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, '');
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function toast(message, type = 'info', ms = 4500) {
  const t = el('div', { class: `toast ${type}`, role: type === 'error' ? 'alert' : 'status' }, message);
  $('#toasts').append(t);
  setTimeout(() => t.remove(), ms);
}

export function errorToast(e) {
  if (e && e.name === 'AbortError') return;
  toast(e && e.message ? e.message : String(e), 'error', 7000);
}

// Promise-based confirm/prompt on the shared <dialog>.
export function dialog({ title, body = '', html = false, okText = 'OK', cancelText = 'Cancel', danger = false, input = null }) {
  const d = $('#dialog');
  $('#dialogTitle').textContent = title;
  const b = $('#dialogBody');
  if (body instanceof Node) { b.replaceChildren(body); } else if (html) { b.innerHTML = body; } else { b.textContent = body; }
  const inp = $('#dialogInput');
  inp.hidden = input === null;
  if (input !== null) inp.value = input;
  const ok = $('#dialogOk');
  ok.textContent = okText;
  ok.className = `btn ${danger ? 'danger' : 'primary'}`;
  const cancel = $('#dialogCancel');
  cancel.textContent = cancelText;
  cancel.hidden = cancelText === null;
  return new Promise(resolve => {
    d.addEventListener('close', () => {
      const yes = d.returnValue === 'ok';
      resolve(input !== null ? (yes ? inp.value : null) : yes);
    }, { once: true });
    d.returnValue = '';
    d.showModal();
    if (input !== null) { inp.focus(); inp.select(); } else ok.focus();
  });
}

export function fmtBytes(n) {
  if (n === null || n === undefined) return '-';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return i === 0 ? `${n} B` : `${n.toFixed(n >= 100 ? 0 : 1)} ${units[i]}`;
}

export function fmtTime(ts) {
  const d = new Date(ts * 1000);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toLocaleDateString();
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = el('textarea', { class: 'input' });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
  }
  toast('Copied to clipboard');
}

export function bar(percent) {
  const p = Math.max(0, Math.min(100, percent || 0));
  const cls = p >= 92 ? 'bar full' : p >= 75 ? 'bar high' : 'bar';
  const inner = el('span');
  inner.style.width = `${p}%`;
  return el('div', { class: cls, role: 'progressbar', 'aria-valuenow': Math.round(p), 'aria-valuemin': 0, 'aria-valuemax': 100 }, inner);
}

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// Read a File as base64 without blowing the call stack on large files.
export async function fileToBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}
