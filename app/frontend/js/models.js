// AI Models view: discovery, load/unload with memory warnings, generation settings.
import { api } from './api.js';
import { $, dialog, el, errorToast, fmtBytes, toast } from './ui.js';

let ctx;
let data = null;
const root = () => $('#view-models');

export function init(c) {
  ctx = c;
  ctx.onStatus(st => { if (root().classList.contains('active') && data && st.state !== data.status.state) refresh(); });
}

export function show() { refresh(); }

export async function refresh() {
  try {
    data = await api('/api/models');
    render();
  } catch (e) { errorToast(e); }
}

async function load(model, force = false) {
  try {
    await api('/api/models/load', { method: 'POST', body: { model, force } });
    toast('Loading model... this can take up to a minute from a USB drive.');
  } catch (e) {
    if (e.status === 409 && e.detail && e.detail.memory_warning) {
      const ok = await dialog({ title: 'Not enough free memory', body: e.detail.detail, okText: 'Load anyway', danger: true });
      if (ok) return load(model, true);
      return;
    }
    errorToast(e);
  }
  ctx.refreshStatus();
  refresh();
}

async function unload() {
  try {
    await api('/api/models/unload', { method: 'POST' });
    toast('Model unloaded; its memory has been freed.');
  } catch (e) { errorToast(e); }
  ctx.refreshStatus();
  refresh();
}

function statusCard() {
  const st = data.status;
  const label = { ready: 'Ready', loading: 'Loading...', error: 'Error', stopped: 'No model loaded' }[st.state] || st.state;
  const badge = st.state === 'ready' ? 'ok' : st.state === 'error' ? 'err' : st.state === 'loading' ? 'warn' : '';
  return el('div', { class: 'card' },
    el('div', { class: 'row' },
      el('h3', {}, 'Engine status'), el('span', { class: `badge ${badge}` }, label),
      el('div', { class: 'spacer' }),
      st.state !== 'stopped' ? el('button', { class: 'btn small', onclick: unload }, 'Unload model') : null,
      el('button', { class: 'btn small', onclick: refresh }, 'Rescan models')),
    el('dl', { class: 'kv' },
      el('dt', {}, 'Active model'), el('dd', {}, st.model || '-'),
      st.settings && st.settings.ctx_size ? [el('dt', {}, 'Context / threads'), el('dd', {}, `${st.settings.ctx_size} tokens / ${st.settings.threads} threads${st.settings.low_memory ? ' (low-memory mode)' : ''}`)] : null,
      el('dt', {}, 'Free RAM'), el('dd', {}, `${fmtBytes(data.available_ram)} of ${fmtBytes(data.total_ram)}`),
      el('dt', {}, 'Models folder'), el('dd', {}, data.models_dir)),
    st.error ? el('p', { class: 'badge err' }, st.error) : null,
    !st.binary_present ? el('p', {}, 'The llama.cpp engine for this operating system is missing. Run the setup script once (see below).') : null);
}

function modelCard(m) {
  const active = data.status.model === m.id && data.status.state !== 'stopped';
  const busy = data.status.state === 'loading';
  const tags = [m.size_label, m.quantization, m.architecture, m.context_train ? `trained ctx ${m.context_train}` : ''].filter(Boolean);
  return el('div', { class: `card model-card${active ? ' active' : ''}` },
    el('div', { class: 'stack' },
      el('div', { class: 'name' }, m.display_name),
      el('div', { class: 'file' }, `${m.file} · ${fmtBytes(m.size_bytes)}`),
      el('div', {}, tags.map(t => el('span', { class: 'badge' }, t)),
        m.valid ? el('span', { class: `badge ${m.fits_in_ram ? 'ok' : 'warn'}` },
          `needs ~${fmtBytes(m.estimated_ram)} RAM${m.fits_in_ram ? '' : ' - more than is free'}`) : el('span', { class: 'badge err' }, m.error))),
    active
      ? el('span', { class: 'badge ok' }, data.status.state === 'loading' ? 'Loading...' : 'Active')
      : el('button', { class: 'btn primary small', disabled: !m.valid || busy, onclick: () => load(m.id) }, 'Load'));
}

function setupCard() {
  const rows = data.catalog.map(c => el('tr', {},
    el('td', {}, c.name), el('td', {}, fmtBytes(c.size)), el('td', {}, `${c.min_ram_gb} GB`), el('td', {}, c.license),
    el('td', { class: 'file' }, c.file)));
  return el('div', { class: 'card warn' },
    el('h3', {}, 'No AI model found'),
    el('p', {}, 'PocketAI needs one GGUF model file. Models are never downloaded automatically. Either:'),
    el('ol', {},
      el('li', {}, 'Run the setup script once while online and choose a model when asked:',
        el('pre', { class: 'cmd' }, 'Windows:  setup-windows.bat\nUbuntu:   bash setup-linux.sh')),
      el('li', {}, 'or copy any chat model in .gguf format into the models folder, then press "Rescan models":',
        el('pre', { class: 'cmd' }, data.models_dir))),
    el('p', { class: 'muted small' }, 'Models offered by setup (sizes and licenses verified):'),
    el('div', { class: 'table-wrap' }, el('table', { class: 'list' },
      el('thead', {}, el('tr', {}, ['Model', 'Size', 'RAM', 'License', 'File'].map(h => el('th', {}, h)))),
      el('tbody', {}, rows))));
}

function paramsCard() {
  const p = data.params;
  const field = (label, input, hint) => el('label', { class: 'field' }, el('span', {}, label), input, hint ? el('small', {}, hint) : null);
  const ctxSel = el('select', { class: 'select', name: 'ctx_size' },
    [1024, 2048, 4096, 8192, 16384, 32768].map(v => el('option', { value: v, selected: v === p.ctx_size }, `${v} tokens`)));
  const threads = el('input', { class: 'input', type: 'number', name: 'threads', min: 0, max: 256, value: p.threads });
  const temp = el('input', { type: 'range', name: 'temperature', min: 0, max: 2, step: 0.05, value: p.temperature });
  const tempOut = el('output', {}, p.temperature);
  temp.addEventListener('input', () => { tempOut.textContent = temp.value; });
  const topP = el('input', { class: 'input', type: 'number', name: 'top_p', min: 0.05, max: 1, step: 0.05, value: p.top_p });
  const maxTok = el('input', { class: 'input', type: 'number', name: 'max_tokens', min: 16, max: 8192, value: p.max_tokens });
  const lowMem = el('input', { type: 'checkbox', name: 'low_memory', checked: p.low_memory });
  const think = el('input', { type: 'checkbox', name: 'enable_thinking', checked: p.enable_thinking });
  const caps = data.low_memory_caps;

  const form = el('form', { class: 'card' },
    el('h3', {}, 'Generation settings'),
    el('div', { class: 'form-grid' },
      field('Context length', ctxSel, 'How much conversation the model can see. More uses more RAM.'),
      field('CPU threads', threads, `0 = automatic (${data.default_threads} on this PC)`),
      field('Temperature', el('div', { class: 'row' }, temp, tempOut), 'Lower = more focused, higher = more creative.'),
      field('Top-p', topP, 'Nucleus sampling (0.9 is a good default).'),
      field('Max output tokens', maxTok, 'Longest single reply.')),
    el('div', { class: 'stack' },
      el('label', { class: 'check' }, lowMem, el('span', {},
        el('b', {}, 'Low-memory mode'), el('br'),
        el('small', { class: 'muted' }, `For 4 GB RAM computers: caps context at ${caps.ctx_size}, history at ${caps.history_messages} messages, replies at ${caps.max_tokens} tokens, and uses smaller batches.`))),
      el('label', { class: 'check' }, think, el('span', {},
        el('b', {}, 'Show model "thinking" (Qwen3 reasoning)'), el('br'),
        el('small', { class: 'muted' }, 'Better answers for hard questions, but much slower on a CPU. Ignored by models without a thinking mode.')))),
    el('div', { class: 'row end' }, el('button', { class: 'btn primary', type: 'submit' }, 'Save settings')));

  form.addEventListener('submit', async e => {
    e.preventDefault();
    const body = {
      ctx_size: +ctxSel.value, threads: +threads.value, temperature: +temp.value, top_p: +topP.value,
      max_tokens: +maxTok.value, low_memory: lowMem.checked, enable_thinking: think.checked,
    };
    try {
      const r = await api('/api/models/params', { method: 'PUT', body });
      toast('Settings saved');
      if (r.needs_reload && data.status.model) {
        const ok = await dialog({ title: 'Reload model?', body: 'Context length, threads and low-memory mode take effect after the model is reloaded. Reload now?', okText: 'Reload' });
        if (ok) await load(data.status.model);
      }
      refresh();
    } catch (err) { errorToast(err); }
  });
  return form;
}

function render() {
  const head = el('div', { class: 'page-head' },
    el('div', {}, el('h2', {}, 'AI Models'),
      el('p', {}, 'GGUF models found on this drive. Only one model is loaded at a time to save memory.')));
  const list = data.models.length ? data.models.map(modelCard) : [setupCard()];
  root().replaceChildren(el('div', {}, head, statusCard(), ...list, paramsCard()));
}
