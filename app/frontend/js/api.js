// Local API client. Every request carries the per-session token that the server embedded
// in index.html; the backend rejects /api calls without it.
const TOKEN = document.querySelector('meta[name="pocketai-token"]').content;

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === 'string' ? detail : (detail && detail.message) || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function errorFrom(resp) {
  let detail = resp.statusText;
  try {
    const body = await resp.json();
    detail = body.detail !== undefined ? body.detail : body;
    if (body.memory_warning) detail = body;
  } catch { /* not JSON */ }
  if (Array.isArray(detail)) detail = detail.map(d => d.msg || JSON.stringify(d)).join('; ');
  return new ApiError(resp.status, detail);
}

export async function api(path, { method = 'GET', body, signal } = {}) {
  const headers = { 'X-PocketAI-Token': TOKEN };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let resp;
  try {
    resp = await fetch(path, { method, headers, signal, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError(0, 'Cannot reach the PocketAI backend. Is the launcher window still open?');
  }
  if (!resp.ok) throw await errorFrom(resp);
  return resp.status === 204 ? null : resp.json();
}

// POST that returns a Server-Sent Events stream; yields {type, ...} objects.
export async function* streamEvents(path, body, signal) {
  let resp;
  try {
    resp = await fetch(path, {
      method: 'POST', signal,
      headers: { 'X-PocketAI-Token': TOKEN, 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
    });
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    throw new ApiError(0, 'Cannot reach the PocketAI backend. Is the launcher window still open?');
  }
  if (!resp.ok) throw await errorFrom(resp);
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const data = block.split('\n').filter(l => l.startsWith('data:')).map(l => l.slice(5).trimStart()).join('\n');
      if (data) yield JSON.parse(data);
    }
  }
}
