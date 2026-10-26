// System Monitor view. Polls only while this view is open and the browser tab is visible.
import { api } from './api.js';
import { $, bar, el, errorToast, fmtBytes } from './ui.js';

let timer = null;
let active = false;
let storage = null;
const root = () => $('#view-system');

export function show() {
  active = true;
  if (!root().firstChild) root().append(el('p', { class: 'muted' }, 'Reading system information...'));
  tick();
}
export function hide() { active = false; clearTimeout(timer); }
export function init() {
  document.addEventListener('visibilitychange', () => { if (active && !document.hidden) tick(); });
}

async function tick() {
  clearTimeout(timer);
  if (!active || document.hidden) return;
  try { render(await api('/api/system')); } catch (e) { errorToast(e); }
  timer = setTimeout(tick, 3000);
}

async function measureStorage(btn) {
  btn.disabled = true;
  btn.textContent = 'Measuring...';
  try { storage = await api('/api/system/storage?refresh=true'); } catch (e) { errorToast(e); }
  tick();
}

function card(title, ...body) { return el('div', { class: 'card' }, el('h3', {}, title), ...body); }

function render(s) {
  const mem = s.memory;
  const drive = s.app_drive;
  const usedPct = drive.total ? (drive.used / drive.total) * 100 : 0;
  const e = s.engine;
  const engineState = { ready: e.generating ? 'Generating' : 'Ready', loading: 'Loading model', error: 'Error', stopped: 'Idle (no model)' }[e.state] || e.state;

  const grid = el('div', { class: 'grid' },
    card('Operating system', el('div', { class: 'stat' }, s.os), el('div', { class: 'muted small' }, `${s.arch} · Python ${s.python}`)),
    card('CPU', el('div', { class: 'stat' }, `${s.cpu.percent}%`), bar(s.cpu.percent),
      el('div', { class: 'muted small' }, `${s.cpu.name}`),
      el('div', { class: 'muted small' }, `${s.cpu.physical_cores} cores / ${s.cpu.logical_cores} threads${s.cpu.mhz ? ` · ${s.cpu.mhz} MHz` : ''}`)),
    card('Memory', el('div', { class: 'stat' }, `${fmtBytes(mem.available)} free`), bar(mem.percent),
      el('div', { class: 'muted small' }, `${mem.percent}% used of ${fmtBytes(mem.total)}`)),
    card('USB / app drive', el('div', { class: 'stat' }, `${fmtBytes(drive.free)} free`), bar(usedPct),
      el('div', { class: 'muted small' }, `of ${fmtBytes(drive.total)} · ${drive.path}`)),
    card('AI inference', el('div', { class: 'stat' }, engineState),
      el('div', { class: 'muted small' }, e.model || 'No model loaded'),
      e.rss_bytes ? el('div', { class: 'muted small' }, `llama-server memory: ${fmtBytes(e.rss_bytes)}`) : null,
      e.error ? el('div', { class: 'badge err' }, e.error) : null),
    card('Chat history', el('div', { class: 'stat' }, `${s.database.conversations} chats`),
      el('div', { class: 'muted small' }, `${s.database.messages} messages · ${fmtBytes(s.database.db_bytes)} on disk`)));

  const btn = el('button', { class: 'btn small', onclick: () => measureStorage(btn) }, storage ? 'Measure again' : 'Measure');
  const usage = card('Application storage',
    el('p', { class: 'muted small' }, 'Space used by PocketAI on the drive (measuring reads the whole folder, so it is on demand).'),
    storage ? el('table', { class: 'list' }, el('tbody', {},
      Object.entries(storage).filter(([k]) => k !== 'total').sort((a, b) => b[1] - a[1])
        .map(([k, v]) => el('tr', {}, el('td', {}, k), el('td', {}, fmtBytes(v)))),
      el('tr', {}, el('th', {}, 'Total'), el('th', {}, fmtBytes(storage.total))))) : null,
    btn);

  const disks = card('Disks',
    el('div', { class: 'table-wrap' }, el('table', { class: 'list' },
      el('thead', {}, el('tr', {}, ['Mount', 'Type', 'Free', 'Size', 'Used'].map(h => el('th', {}, h)))),
      el('tbody', {}, s.disks.map(d => el('tr', {},
        el('td', {}, d.mount, d.removable ? el('span', { class: 'badge' }, 'removable') : null),
        el('td', {}, d.fstype), el('td', {}, fmtBytes(d.free)), el('td', {}, fmtBytes(d.total)), el('td', {}, `${d.percent}%`)))))));

  root().replaceChildren(el('div', {},
    el('div', { class: 'page-head' }, el('div', {}, el('h2', {}, 'System Monitor'),
      el('p', {}, 'Updates every 3 seconds while this page is open.'))),
    grid, usage, disks));
}
