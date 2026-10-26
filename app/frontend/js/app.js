// Application shell: navigation, theme, keyboard shortcuts, model status pill.
import { api } from './api.js';
import * as chat from './chat.js';
import * as models from './models.js';
import * as settings from './settings.js';
import * as tools from './tools.js';
import * as system from './system.js';
import { $, dialog, errorToast } from './ui.js';

const VIEWS = {
  chat: { title: 'Chat', mod: chat },
  models: { title: 'AI Models', mod: models },
  tools: { title: 'Developer Tools', mod: tools },
  system: { title: 'System Monitor', mod: system },
  settings: { title: 'Settings', mod: settings },
};
let current = 'chat';
const narrow = () => window.matchMedia('(max-width: 860px)').matches;

export function showView(name) {
  if (!VIEWS[name]) return;
  if (current !== name && VIEWS[current].mod.hide) VIEWS[current].mod.hide();
  current = name;
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === `view-${name}`));
  document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === name));
  $('#viewTitle').textContent = VIEWS[name].title;
  if (VIEWS[name].mod.show) VIEWS[name].mod.show();
  if (narrow()) setSidebar(false);
}

function setSidebar(open) {
  const app = $('#app');
  if (narrow()) {
    app.classList.toggle('sidebar-open', open);
    $('#scrim').hidden = !open;
  } else {
    app.classList.toggle('sidebar-collapsed', !open);
  }
}
function toggleSidebar() {
  const app = $('#app');
  setSidebar(narrow() ? !app.classList.contains('sidebar-open') : app.classList.contains('sidebar-collapsed'));
}

// ---------------------------------------------------------------- theme
export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme === 'light' ? 'light' : 'dark';
  try { localStorage.setItem('pocketai-theme', document.documentElement.dataset.theme); } catch { /* storage disabled */ }
}
async function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  try { await api('/api/settings', { method: 'PUT', body: { theme: next } }); } catch (e) { errorToast(e); }
}

// ---------------------------------------------------------------- model status pill
let statusTimer = null;
let lastStatus = null;
const listeners = new Set();
export function onStatus(fn) { listeners.add(fn); if (lastStatus) fn(lastStatus); }

export async function refreshStatus() {
  clearTimeout(statusTimer);
  try {
    const st = await api('/api/models/status');
    lastStatus = st;
    const dot = $('#modelDot');
    dot.className = 'dot ' + (st.state === 'ready' ? 'ready' : st.state === 'loading' ? 'loading' : st.state === 'error' ? 'error' : '');
    const name = st.model ? st.model.split('/').pop().replace(/\.gguf$/i, '') : '';
    $('#modelPillText').textContent =
      st.state === 'ready' ? name : st.state === 'loading' ? `Loading ${name}...` :
      st.state === 'error' ? 'Model error' : (st.binary_present ? 'No model loaded' : 'Engine missing - run setup');
    $('#modelPill').title = st.error || (st.state === 'ready' ? `${st.model} is ready` : 'Click to manage models');
    listeners.forEach(fn => fn(st));
  } catch (e) {
    $('#modelDot').className = 'dot error';
    $('#modelPillText').textContent = 'Backend offline';
  }
  // Poll fast only while loading, and never while the tab is hidden.
  const delay = lastStatus && lastStatus.state === 'loading' ? 1000 : 15000;
  statusTimer = setTimeout(() => { if (!document.hidden) refreshStatus(); }, delay);
}

// ---------------------------------------------------------------- shortcuts
const SHORTCUTS = [
  ['Enter', 'Send message'], ['Shift+Enter', 'New line'], ['Esc', 'Stop generating'],
  ['Ctrl+Shift+O', 'New chat'], ['Ctrl+K', 'Search chat history'], ['Ctrl+B', 'Toggle sidebar'],
  ['Ctrl+Shift+L', 'Toggle dark / light mode'], ['Alt+1 ... Alt+5', 'Switch section'], ['Ctrl+/', 'Show shortcuts'],
];
export function shortcutsTable() {
  return '<table class="list">' + SHORTCUTS.map(([k, d]) => `<tr><td><kbd>${k}</kbd></td><td>${d}</td></tr>`).join('') + '</table>';
}

function onKey(e) {
  const ctrl = e.ctrlKey || e.metaKey;
  if (ctrl && e.shiftKey && e.key.toLowerCase() === 'o') { e.preventDefault(); showView('chat'); chat.newChat(); }
  else if (ctrl && !e.shiftKey && e.key.toLowerCase() === 'k') { e.preventDefault(); setSidebar(true); $('#historySearch').focus(); }
  else if (ctrl && !e.shiftKey && e.key.toLowerCase() === 'b') { e.preventDefault(); toggleSidebar(); }
  else if (ctrl && e.shiftKey && e.key.toLowerCase() === 'l') { e.preventDefault(); toggleTheme(); }
  else if (ctrl && e.key === '/') { e.preventDefault(); dialog({ title: 'Keyboard shortcuts', body: shortcutsTable(), html: true, cancelText: null }); }
  else if (e.altKey && /^[1-5]$/.test(e.key)) { e.preventDefault(); showView(Object.keys(VIEWS)[+e.key - 1]); }
  else if (e.key === 'Escape') {
    if (chat.isGenerating()) { e.preventDefault(); chat.stop(); }
    else if (narrow()) setSidebar(false);
  }
}

// ---------------------------------------------------------------- boot
async function boot() {
  try { applyTheme(localStorage.getItem('pocketai-theme') || 'dark'); } catch { applyTheme('dark'); }
  document.querySelectorAll('.nav-item').forEach(b => b.addEventListener('click', () => showView(b.dataset.view)));
  $('#sidebarToggle').addEventListener('click', toggleSidebar);
  $('#scrim').addEventListener('click', () => setSidebar(false));
  $('#themeToggle').addEventListener('click', toggleTheme);
  $('#modelPill').addEventListener('click', () => showView('models'));
  $('#newChatBtn').addEventListener('click', () => { showView('chat'); chat.newChat(); });
  document.addEventListener('keydown', onKey);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshStatus(); });

  const ctx = { showView, refreshStatus, onStatus, applyTheme };
  for (const v of Object.values(VIEWS)) if (v.mod.init) v.mod.init(ctx);
  try {
    const s = await api('/api/settings');
    applyTheme(s.settings.theme);
  } catch (e) { errorToast(e); }
  refreshStatus();
}

boot();
