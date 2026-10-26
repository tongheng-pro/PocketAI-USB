// Developer Tools view: coding assistant presets + read-only file analyzer.
import { api } from './api.js';
import { startChatWith } from './chat.js';
import { $, dialog, el, errorToast, fileToBase64, fmtBytes, toast } from './ui.js';

let ctx;
let maxKb = 256;
let roots = [];
let browse = { root: null, path: '' };
const root = () => $('#view-tools');

const LANGS = ['PHP', 'Laravel', 'JavaScript', 'TypeScript', 'Python', 'HTML', 'CSS', 'SQL', 'Bash'];
const TASKS = {
  explain: ['Explain code', 'coding', 'Explain this {lang} code step by step:'],
  generate: ['Generate code', 'coding', 'Write {lang} code for this requirement:'],
  improve: ['Suggest improvements', 'coding', 'Suggest improvements to this {lang} code (readability, structure, best practices). Show focused changes, not a full rewrite:'],
  error: ['Analyze an error message', 'debugging', 'Analyze this {lang} error message / stack trace. Explain what it means, the likely causes, and the fix:'],
  bugs: ['Find potential bugs', 'debugging', 'Find potential bugs, edge cases and security problems in this {lang} code:'],
  performance: ['Performance review', 'coding', 'Recommend performance improvements for this {lang} code:'],
  docs: ['Write documentation', 'documentation', 'Write documentation for this {lang} code:'],
  sql: ['Explain a database query', 'coding', 'Explain this SQL query: what it does, how expensive it is, and which indexes would help:'],
};
const FILE_TASKS = { explain: 'Explain the file', bugs: 'Find bugs & security issues', improve: 'Suggest improvements', performance: 'Performance review', docs: 'Write documentation', sql: 'Explain SQL queries', summary: 'Short summary' };
const FENCE = { PHP: 'php', Laravel: 'php', JavaScript: 'javascript', TypeScript: 'typescript', Python: 'python', HTML: 'html', CSS: 'css', SQL: 'sql', Bash: 'bash' };

export function init(c) { ctx = c; }
export function show() { render(); }

const field = (label, input, hint) => el('label', { class: 'field' }, el('span', {}, label), input, hint ? el('small', {}, hint) : null);

// ---------------------------------------------------------------- coding assistant
function assistantCard() {
  const lang = el('select', { class: 'select' }, LANGS.map(l => el('option', { value: l }, l)));
  const task = el('select', { class: 'select' }, Object.entries(TASKS).map(([k, v]) => el('option', { value: k }, v[0])));
  const fwVer = el('input', { class: 'input', placeholder: 'e.g. 5.2' });
  const phpVer = el('input', { class: 'input', placeholder: 'e.g. 7.0' });
  const versions = el('div', { class: 'form-grid' }, field('Laravel version', fwVer, 'Check composer.lock if unsure.'), field('PHP version', phpVer));
  const input = el('textarea', { class: 'input', rows: 10, placeholder: 'Paste code, an error message, or describe what you need...' });
  const syncVersions = () => { versions.hidden = !['Laravel', 'PHP'].includes(lang.value); fwVer.parentElement.hidden = lang.value !== 'Laravel'; };
  lang.addEventListener('change', () => { syncVersions(); if (lang.value === 'SQL') task.value = 'sql'; });
  syncVersions();

  const form = el('form', { class: 'card' },
    el('h3', {}, 'Coding assistant'),
    el('p', { class: 'muted small' }, 'Pick a language and task; the request opens in a new chat with the matching expert prompt. PocketAI never runs or changes your code.'),
    el('div', { class: 'form-grid' }, field('Language', lang), field('Task', task)),
    versions,
    el('div', { class: 'stack' }, input),
    el('div', { class: 'row end' }, el('button', { class: 'btn primary', type: 'submit' }, 'Ask in chat')));
  form.addEventListener('submit', e => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) { toast('Paste some code or describe the task first.', 'warn'); return; }
    const [, mode, template] = TASKS[task.value];
    const parts = [];
    if (lang.value === 'Laravel') {
      parts.push(fwVer.value.trim()
        ? `Project: Laravel ${fwVer.value.trim()}${phpVer.value.trim() ? ` on PHP ${phpVer.value.trim()}` : ''}. Only use APIs and syntax that exist in exactly this version; do not suggest newer Laravel features.`
        : 'Project: Laravel (version not given). Say which Laravel version your answer assumes, and avoid APIs that only exist in recent releases.');
    } else if (lang.value === 'PHP' && phpVer.value.trim()) {
      parts.push(`Project: PHP ${phpVer.value.trim()}. Only use syntax available in that version.`);
    }
    parts.push(template.replace('{lang}', lang.value));
    const looksLikeCode = task.value !== 'generate' && (text.includes('\n') || /[;{}<>$=]/.test(text));
    parts.push(looksLikeCode ? '```' + (task.value === 'error' ? '' : FENCE[lang.value]) + '\n' + text + '\n```' : text);
    startChatWith(parts.join('\n\n'), mode);
  });
  return form;
}

// ---------------------------------------------------------------- file analyzer
function infoView(info) {
  const warn = [...info.problems.map(p => el('div', { class: 'badge err' }, p)),
    ...info.sensitive.map(s => el('div', { class: 'badge warn' }, `Possible secret: ${s}`))];
  const p = info.project || {};
  return el('div', { class: 'stack' },
    el('dl', { class: 'kv' },
      el('dt', {}, 'File'), el('dd', {}, info.name),
      el('dt', {}, 'Size'), el('dd', {}, `${fmtBytes(info.size_bytes)}${info.lines ? ` · ${info.lines} lines` : ''}`),
      el('dt', {}, 'Type'), el('dd', {}, info.language),
      p.framework === 'laravel' ? [el('dt', {}, 'Project'), el('dd', {}, `Laravel ${p.version || p.constraint}${p.php ? ` · PHP ${p.php}` : ''}`)] : null),
    warn.length ? el('div', {}, warn) : null);
}

async function analyze(ref, info, taskSel, note, allowSensitive = false) {
  try {
    const r = await api('/api/files/prepare', { method: 'POST', body: { ...ref, task: taskSel.value, note: note.value, allow_sensitive: allowSensitive } });
    if (r.truncated) toast('The file is long; only the beginning fits in the model\'s context window.', 'warn', 7000);
    startChatWith(r.message, r.mode);
  } catch (e) {
    if (e.status === 403 && e.detail && e.detail.sensitive) {
      const body = el('div', {}, el('p', {}, `"${info.name}" looks like it contains secrets:`),
        el('ul', {}, e.detail.sensitive.map(s => el('li', {}, s))),
        el('p', {}, 'Its content would be sent to the local AI and saved in your chat history on this drive. Nothing leaves this computer. Analyze it anyway?'));
      const ok = await dialog({ title: 'Sensitive file', body, okText: 'Analyze anyway', danger: true });
      if (ok) return analyze(ref, info, taskSel, note, true);
      return;
    }
    errorToast(e);
  }
}

function resultArea() { return el('div', { class: 'stack' }); }

function showInspection(area, ref, info) {
  const taskSel = el('select', { class: 'select' }, Object.entries(FILE_TASKS).map(([k, v]) => el('option', { value: k }, v)));
  if (info.extension === '.sql') taskSel.value = 'sql';
  const note = el('input', { class: 'input', placeholder: 'Optional: what should the AI focus on?' });
  area.replaceChildren(infoView(info),
    info.allowed ? el('div', { class: 'stack' },
      el('div', { class: 'form-grid' }, field('Task', taskSel), field('Focus (optional)', note)),
      el('div', { class: 'row end' }, el('button', { class: 'btn primary', onclick: () => analyze(ref, info, taskSel, note) }, 'Analyze in chat')))
      : el('p', { class: 'muted small' }, 'This file cannot be analyzed.'));
}

function uploadCard() {
  const area = resultArea();
  const input = el('input', { type: 'file', class: 'input', accept: '.php,.js,.mjs,.cjs,.jsx,.ts,.tsx,.py,.html,.htm,.css,.scss,.json,.md,.sql,.txt,.sh,.bash,.log,.xml,.yml,.yaml,.vue' });
  input.addEventListener('change', async () => {
    const f = input.files[0];
    if (!f) return;
    if (f.size > maxKb * 1024) { area.replaceChildren(el('div', { class: 'badge err' }, `File is larger than the ${maxKb} KB limit (change it in Settings).`)); return; }
    try {
      const ref = { source: 'upload', name: f.name, content_b64: await fileToBase64(f) };
      showInspection(area, ref, await api('/api/files/inspect', { method: 'POST', body: ref }));
    } catch (e) { errorToast(e); }
  });
  return el('div', { class: 'card' },
    el('h3', {}, 'Analyze a file'),
    el('p', { class: 'muted small' }, `Pick a text file (PHP, JS, TS, Python, HTML, CSS, JSON, Markdown, SQL, TXT; up to ${maxKb} KB). Files are only read, never modified. .env files and keys are flagged and need your confirmation.`),
    input, area);
}

async function projectCard() {
  const card = el('div', { class: 'card' }, el('h3', {}, 'Browse a project folder'));
  if (!roots.length) {
    card.append(el('p', { class: 'muted small' }, 'Add a project folder under Settings > Project folders to browse it here (read-only).'),
      el('button', { class: 'btn small', onclick: () => ctx.showView('settings') }, 'Open Settings'));
    return card;
  }
  const sel = el('select', { class: 'select' }, roots.map(r => el('option', { value: r.index }, r.path)));
  const projInfo = el('div', { class: 'small muted' });
  const crumbs = el('div', { class: 'crumbs' });
  const listing = el('div', { class: 'browser' });
  const area = resultArea();

  const describe = r => {
    const p = r.project || {};
    projInfo.textContent = !r.ok ? r.error : p.framework === 'laravel'
      ? `Laravel project - installed version ${p.version || 'unknown'} (${p.source || 'constraint ' + p.constraint})${p.php ? `, PHP ${p.php}` : ''}. Suggestions will stay compatible with it.`
      : p.framework ? `${p.framework} project` : '';
  };
  async function open(path) {
    try {
      const res = await api(`/api/files/list?root=${browse.root}&path=${encodeURIComponent(path)}`);
      browse.path = res.path;
      crumbs.textContent = '/' + res.path;
      const items = [];
      if (res.path) items.push(el('button', { onclick: () => open(res.path.split('/').slice(0, -1).join('/')) }, '← ..'));
      for (const e of res.entries) {
        const rel = res.path ? `${res.path}/${e.name}` : e.name;
        items.push(el('button', {
          disabled: !e.dir && !e.supported,
          title: e.sensitive ? 'May contain secrets' : '',
          onclick: async () => {
            if (e.dir) return open(rel);
            try {
              const ref = { source: 'path', root: browse.root, path: rel };
              showInspection(area, ref, await api('/api/files/inspect', { method: 'POST', body: ref }));
            } catch (err) { errorToast(err); }
          },
        }, e.dir ? '📁' : '📄', ` ${e.name}`,
          e.skipped_by_default ? el('span', { class: 'badge' }, 'dependencies') : null,
          e.sensitive ? el('span', { class: 'badge warn' }, 'sensitive') : null,
          el('span', { class: 'size' }, e.dir ? '' : fmtBytes(e.size_bytes))));
      }
      listing.replaceChildren(...items);
    } catch (e) { errorToast(e); }
  }
  sel.addEventListener('change', () => { browse = { root: +sel.value, path: '' }; describe(roots.find(r => r.index === +sel.value)); area.replaceChildren(); open(''); });
  if (browse.root === null || !roots.some(r => r.index === browse.root)) browse = { root: roots[0].index, path: '' };
  sel.value = browse.root;
  describe(roots.find(r => r.index === browse.root));
  open(browse.path);
  card.append(field('Folder', sel), projInfo, crumbs, listing, area);
  return card;
}

async function render() {
  try {
    const [s, r] = await Promise.all([api('/api/settings'), api('/api/files/roots')]);
    maxKb = s.settings.max_file_kb;
    roots = r.roots;
  } catch (e) { errorToast(e); }
  root().replaceChildren(el('div', {},
    el('div', { class: 'page-head' }, el('div', {}, el('h2', {}, 'Developer Tools'),
      el('p', {}, 'Offline coding help for PHP, Laravel, JavaScript, TypeScript, Python, HTML, CSS, SQL and Bash.'))),
    assistantCard(), uploadCard(), await projectCard()));
}
