// Chat view: conversation history, streaming replies, stop/copy/rename/delete/search.
import { api, streamEvents } from './api.js';
import { renderMarkdown } from './markdown.js';
import { $, copyText, debounce, dialog, el, errorToast, fmtTime, toast } from './ui.js';

let ctx;
let convId = null;
let generating = false;
let abort = null;
let modes = [];

const SUGGESTIONS = [
  ['coding', 'Explain Laravel middleware', 'Explain Laravel middleware with a short example.'],
  ['coding', 'Write a Python script', 'Write a Python script that renames all .txt files in a folder to lowercase.'],
  ['debugging', 'Debug an error', 'What does "SQLSTATE[23000]: Integrity constraint violation" mean and how do I fix it?'],
  ['general', 'Summarize a topic', 'Explain how SQL indexes work in simple terms.'],
];

export function isGenerating() { return generating; }

// ---------------------------------------------------------------- history sidebar
export async function loadHistory() {
  const q = $('#historySearch').value.trim();
  const list = $('#historyList');
  try {
    const { conversations } = await api(`/api/conversations?limit=200&q=${encodeURIComponent(q)}`);
    list.replaceChildren();
    if (!conversations.length) {
      list.append(el('li', { class: 'empty' }, q ? 'No chats match your search.' : 'No chats yet.'));
      return;
    }
    for (const c of conversations) {
      const li = el('li', { class: c.id === convId ? 'active' : '' },
        el('button', { class: 'conv', title: `${c.title}\n${fmtTime(c.updated_at)}`, onclick: () => openConversation(c.id) },
          c.title, c.snippet ? el('span', { class: 'snippet' }, c.snippet) : null),
        el('div', { class: 'conv-actions' },
          el('button', { class: 'icon-btn tiny', title: 'Rename', 'aria-label': `Rename ${c.title}`, onclick: () => rename(c) }, '✎'),
          el('button', { class: 'icon-btn tiny', title: 'Delete', 'aria-label': `Delete ${c.title}`, onclick: () => remove(c) }, '✕')));
      list.append(li);
    }
  } catch (e) { errorToast(e); }
}

async function rename(c) {
  const title = await dialog({ title: 'Rename chat', input: c.title, okText: 'Rename' });
  if (title === null || !title.trim()) return;
  try {
    await api(`/api/conversations/${c.id}`, { method: 'PATCH', body: { title: title.trim().slice(0, 120) } });
    loadHistory();
  } catch (e) { errorToast(e); }
}

async function remove(c) {
  const ok = await dialog({ title: 'Delete chat?', body: `"${c.title}" will be permanently deleted from this drive.`, okText: 'Delete', danger: true });
  if (!ok) return;
  try {
    await api(`/api/conversations/${c.id}`, { method: 'DELETE' });
    if (c.id === convId) newChat();
    loadHistory();
    toast('Chat deleted');
  } catch (e) { errorToast(e); }
}

// ---------------------------------------------------------------- rendering
const messagesEl = () => $('#messages');

function nearBottom() {
  const m = messagesEl();
  return m.scrollHeight - m.scrollTop - m.clientHeight < 120;
}
function scrollToBottom(force = false) {
  const m = messagesEl();
  if (force || nearBottom()) m.scrollTop = m.scrollHeight;
}

function welcome() {
  const box = el('div', { class: 'welcome' },
    el('h2', {}, 'How can I help?'),
    el('p', { class: 'muted' }, 'PocketAI runs entirely on this computer. Nothing you type leaves it.'),
    el('div', { class: 'suggestions' }, SUGGESTIONS.map(([mode, title, prompt]) =>
      el('button', { class: 'suggestion', onclick: () => { setMode(mode); $('#input').value = prompt; send(); } },
        el('b', {}, title), el('span', { class: 'muted small' }, prompt)))));
  messagesEl().replaceChildren(box);
}

function statsText(stats) {
  if (!stats) return '';
  const parts = [];
  if (stats.tokens_per_s) parts.push(`${stats.tokens_per_s} tok/s`);
  if (stats.completion_tokens) parts.push(`${stats.completion_tokens} tokens`);
  if (stats.elapsed_s) parts.push(`${stats.elapsed_s}s`);
  if (stats.stopped || stats.finish_reason === 'stopped') parts.push('stopped');
  else if (stats.finish_reason === 'length') parts.push('cut off at max tokens');
  return parts.join(' · ');
}

// Builds a message element; returns handles for streaming updates.
function messageView(role, content = '', reasoning = '', stats = null) {
  const md = el('div', { class: 'md content' });
  const thinkMd = el('div', { class: 'md' });
  const think = el('details', { class: 'thinking', hidden: !reasoning }, el('summary', {}, 'Thinking'), thinkMd);
  const statsEl = el('span', { class: 'stats' }, statsText(stats));
  const copyBtn = el('button', { class: 'btn ghost', title: 'Copy response', onclick: () => copyText(view.raw) }, 'Copy');
  const meta = el('div', { class: 'meta' }, role === 'assistant' ? copyBtn : null, statsEl);
  const node = el('div', { class: `msg ${role}` },
    el('div', { class: 'avatar', 'aria-hidden': 'true' }, role === 'user' ? 'You' : 'AI'),
    el('div', { class: 'bubble' }, think, md, meta));
  const view = {
    node, raw: content, reasoning,
    render() {
      md.innerHTML = renderMarkdown(this.raw);
      if (this.reasoning) { think.hidden = false; thinkMd.innerHTML = renderMarkdown(this.reasoning); }
    },
    setStats(s) { statsEl.textContent = statsText(s); },
    setTyping(on) {
      if (on && !this.raw) md.replaceChildren(el('span', { class: 'typing', 'aria-label': 'AI is thinking' }, el('i'), el('i'), el('i')));
      md.classList.toggle('cursor', on && !!this.raw);
    },
  };
  if (role === 'user' && content.length > 4000) {
    view.render = function () {
      md.innerHTML = renderMarkdown(this.raw.slice(0, 1500) + '\n\n*... (long message, ' + this.raw.length + ' characters - shown shortened)*');
    };
  }
  view.render();
  return view;
}

function showNotice(text) {
  messagesEl().append(el('div', { class: 'notice' }, text));
}

// ---------------------------------------------------------------- conversations
export function newChat() {
  if (generating) stop();
  convId = null;
  welcome();
  setMode('general');
  document.querySelectorAll('#historyList li.active').forEach(li => li.classList.remove('active'));
  $('#input').focus();
}

export async function openConversation(id) {
  if (generating) stop();
  try {
    const conv = await api(`/api/conversations/${id}`);
    convId = conv.id;
    setMode(conv.mode);
    const m = messagesEl();
    m.replaceChildren();
    for (const msg of conv.messages) {
      if (msg.role === 'system') continue;
      m.append(messageView(msg.role, msg.content, msg.reasoning, msg.stats).node);
    }
    ctx.showView('chat');
    scrollToBottom(true);
    loadHistory();
  } catch (e) { errorToast(e); }
}

function setMode(mode) {
  const sel = $('#modeSelect');
  if ([...sel.options].some(o => o.value === mode)) sel.value = mode;
}

// ---------------------------------------------------------------- sending
function setGenerating(on, status = '') {
  generating = on;
  $('#sendBtn').hidden = on;
  $('#stopBtn').hidden = !on;
  $('#genStatus').textContent = status;
}

export async function startChatWith(message, mode) {
  newChat();
  setMode(mode);
  $('#input').value = message;
  ctx.showView('chat');
  await send();
}

export async function send() {
  const input = $('#input');
  const text = input.value.trim();
  if (!text || generating) return;
  if (messagesEl().querySelector('.welcome')) messagesEl().replaceChildren();

  const userView = messageView('user', text);
  messagesEl().append(userView.node);
  const aiView = messageView('assistant');
  aiView.node.hidden = true;
  messagesEl().append(aiView.node);
  input.value = '';
  autosize();
  scrollToBottom(true);

  abort = new AbortController();
  setGenerating(true, 'Sending...');
  aiView.node.hidden = false;
  aiView.setTyping(true);
  const started = performance.now();
  let pieces = 0;
  let frame = 0;
  const repaint = () => {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; aiView.render(); aiView.setTyping(true); scrollToBottom(); });
  };
  let wasNew = convId === null;
  let ended = false;
  try {
    const body = { message: text, mode: $('#modeSelect').value || 'general' };
    if (convId) body.conversation_id = convId;
    for await (const ev of streamEvents('/api/chat/stream', body, abort.signal)) {
      if (ev.type === 'meta') {
        convId = ev.conversation_id;
        if (ev.history_trimmed) $('#genStatus').title = 'Older messages were left out to fit the context window.';
        if (wasNew) { loadHistory(); wasNew = false; }
        setGenerating(true, 'Thinking...');
      } else if (ev.type === 'reasoning') {
        aiView.reasoning += ev.text;
        setGenerating(true, 'Thinking...');
        repaint();
      } else if (ev.type === 'content') {
        aiView.raw += ev.text;
        pieces++;
        const secs = (performance.now() - started) / 1000;
        setGenerating(true, `Generating... ${pieces} tokens · ${(pieces / Math.max(secs, 0.1)).toFixed(1)} tok/s`);
        repaint();
      } else if (ev.type === 'error') {
        showNotice(`Error: ${ev.message}`);
      } else if (ev.type === 'done') {
        ended = true;
        aiView.setStats(ev.stats);
      }
    }
  } catch (e) {
    if (e.name !== 'AbortError') {
      if (!aiView.raw) aiView.node.remove();
      errorToast(e);
      if (e.status === 409) ctx.refreshStatus();
    }
  } finally {
    if (frame) cancelAnimationFrame(frame);
    aiView.render();
    aiView.setTyping(false);
    if (ended && !aiView.raw && !aiView.reasoning && aiView.node.isConnected) {
      aiView.raw = '*(no response)*';
      aiView.render();
    }
    setGenerating(false);
    abort = null;
    scrollToBottom();
    loadHistory();
    $('#input').focus();
  }
}

export async function stop() {
  if (!generating) return;
  $('#genStatus').textContent = 'Stopping...';
  try { await api('/api/chat/stop', { method: 'POST' }); } catch (e) { errorToast(e); }
  // The server ends the stream itself (and saves the partial reply); abort as a fallback.
  setTimeout(() => { if (abort) abort.abort(); }, 3000);
}

function autosize() {
  const t = $('#input');
  t.style.height = 'auto';
  t.style.height = `${Math.min(t.scrollHeight, window.innerHeight * 0.4)}px`;
}

// ---------------------------------------------------------------- init
export async function init(c) {
  ctx = c;
  const input = $('#input');
  input.addEventListener('input', autosize);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  $('#sendBtn').addEventListener('click', send);
  $('#stopBtn').addEventListener('click', stop);
  $('#historySearch').addEventListener('input', debounce(loadHistory, 250));
  // Copy buttons inside rendered code blocks (event delegation).
  $('#messages').addEventListener('click', e => {
    const btn = e.target.closest('.code-copy');
    if (btn) copyText(btn.closest('.code-block').querySelector('code').textContent);
  });
  try {
    modes = (await api('/api/modes')).modes;
    $('#modeSelect').replaceChildren(...modes.map(m => el('option', { value: m.id }, m.title)));
  } catch (e) { errorToast(e); }
  setMode('general');
  welcome();
  loadHistory();
  input.focus();
}

export function show() { setTimeout(() => $('#input').focus(), 0); }
