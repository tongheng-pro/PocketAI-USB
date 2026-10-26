// Settings view: appearance, startup, history, file-analyzer folders.
import { api } from './api.js';
import { shortcutsTable } from './app.js';
import { $, el, errorToast, toast } from './ui.js';

let ctx;
const root = () => $('#view-settings');

export function init(c) { ctx = c; }
export function show() { render(); }

async function save(updates) {
  try {
    const r = await api('/api/settings', { method: 'PUT', body: updates });
    toast('Settings saved');
    return r.settings;
  } catch (e) { errorToast(e); return null; }
}

async function render() {
  let res;
  try { res = await api('/api/settings'); } catch (e) { errorToast(e); return; }
  const s = res.settings;
  const field = (label, input, hint) => el('label', { class: 'field' }, el('span', {}, label), input, hint ? el('small', {}, hint) : null);

  const theme = el('select', { class: 'select' }, ['dark', 'light'].map(t => el('option', { value: t, selected: s.theme === t }, t === 'dark' ? 'Dark' : 'Light')));
  const autoload = el('input', { type: 'checkbox', checked: s.autoload_model });
  const port = el('input', { class: 'input', type: 'number', min: 1024, max: 65535, value: s.port });
  const history = el('input', { class: 'input', type: 'number', min: 2, max: 200, value: s.history_messages });
  const maxKb = el('input', { class: 'input', type: 'number', min: 4, max: 2048, value: s.max_file_kb });

  const general = el('form', { class: 'card' },
    el('h3', {}, 'General'),
    el('div', { class: 'form-grid' },
      field('Theme', theme),
      field('Dashboard port', port, 'Used next start. If busy, the next free port is used.'),
      field('Messages remembered per chat', history, 'Older messages are left out of the prompt to save memory.'),
      field('Max file size for analysis (KB)', maxKb)),
    el('label', { class: 'check' }, autoload, el('span', {}, 'Load the selected model automatically at startup')),
    el('div', { class: 'row end' }, el('button', { class: 'btn primary', type: 'submit' }, 'Save')));
  general.addEventListener('submit', async e => {
    e.preventDefault();
    const saved = await save({ theme: theme.value, autoload_model: autoload.checked, port: +port.value,
      history_messages: +history.value, max_file_kb: +maxKb.value });
    if (saved) ctx.applyTheme(saved.theme);
  });

  // Project folders the file analyzer may read (read-only).
  const roots = [...s.allowed_roots];
  const list = el('ul', { class: 'roots' });
  const drawRoots = () => list.replaceChildren(...(roots.length ? roots.map((r, i) => el('li', {}, el('span', {}, r),
    el('button', { class: 'btn small', type: 'button', onclick: async () => {
      roots.splice(i, 1);
      if (await save({ allowed_roots: roots })) drawRoots(); else roots.splice(i, 0, r);
    } }, 'Remove'))) : [el('li', { class: 'muted' }, 'No project folders added.')]));
  drawRoots();
  const newRoot = el('input', { class: 'input', placeholder: 'e.g. C:\\Projects\\my-laravel-app  or  /home/me/projects/site' });
  const folders = el('form', { class: 'card' },
    el('h3', {}, 'Project folders for the file analyzer'),
    el('p', { class: 'muted small' }, 'The analyzer can only browse folders listed here, and only reads files - it never changes them. Files like .env and private keys need your confirmation every time.'),
    list,
    el('div', { class: 'row' }, newRoot, el('button', { class: 'btn', type: 'submit' }, 'Add folder')));
  folders.addEventListener('submit', async e => {
    e.preventDefault();
    const p = newRoot.value.trim();
    if (!p) return;
    const saved = await save({ allowed_roots: [...roots, p] });
    if (saved) { roots.splice(0, roots.length, ...saved.allowed_roots); newRoot.value = ''; drawRoots(); }
  });

  const paths = el('div', { class: 'card' },
    el('h3', {}, 'Where your data lives'),
    el('dl', { class: 'kv' }, Object.entries({ 'App folder': res.paths.root, Models: res.paths.models, 'Chat history': res.paths.database, Logs: res.paths.logs })
      .flatMap(([k, v]) => [el('dt', {}, k), el('dd', {}, v)])),
    el('p', { class: 'muted small' }, 'Everything stays on this drive. Logs never contain chat text. Network access is only used by the setup scripts.'));

  const keys = el('div', { class: 'card' }, el('h3', {}, 'Keyboard shortcuts'));
  keys.insertAdjacentHTML('beforeend', shortcutsTable());

  root().replaceChildren(el('div', {},
    el('div', { class: 'page-head' }, el('div', {}, el('h2', {}, 'Settings'), el('p', {}, 'Model and generation settings are under AI Models.'))),
    general, folders, paths, keys));
}
