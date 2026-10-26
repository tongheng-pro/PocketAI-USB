// Safe Markdown renderer + small syntax highlighter (no dependencies).
//
// Safety model: all source text is HTML-escaped; the renderer only ever emits its own fixed
// set of tags. Link targets are limited to http(s)/mailto. Raw HTML in model output is shown
// as text, never interpreted, so no separate sanitizer is needed.

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ------------------------------------------------------------------ highlighter
const KW = {
  js: 'abstract as async await break case catch class const constructor continue debugger declare default delete do else enum export extends finally for from function get if implements import in instanceof interface keyof let module namespace new of private protected public readonly return set static super switch this throw try type typeof var void while with yield',
  python: 'and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case self',
  php: 'abstract and array as break callable case catch class clone const continue declare default do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile extends final finally fn for foreach function global goto if implements include include_once instanceof insteadof interface isset list match namespace new or print private protected public readonly require require_once return static switch throw trait try unset use var while yield parent self',
  sql: 'add all alter and any as asc between by case check column constraint create database default delete desc distinct drop else end exists foreign from full group having in index inner insert into is join key left like limit not null offset on or order outer primary references right select set table then top truncate union unique update values view when where with count sum avg min max coalesce if begin commit rollback transaction procedure function returns declare auto_increment engine',
  bash: 'if then else elif fi case esac for while until do done in function return local export readonly declare unset shift exit echo printf read cd source alias set trap sudo',
};
const LIT = new Set('true false null undefined None True False NULL nil NaN Infinity'.split(' '));
const sets = {};
for (const [k, v] of Object.entries(KW)) sets[k] = new Set(v.split(' '));

const STR_DQ = String.raw`"(?:[^"\\\n]|\\.)*"?`;
const STR_SQ = String.raw`'(?:[^'\\\n]|\\.)*'?`;
const STR_BT = String.raw`\`(?:[^\`\\]|\\[\s\S])*\`?`;
const NUM = String.raw`\b(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?(?:e[+-]?\d+)?)\b`;
const BLOCK_COM = String.raw`\/\*[\s\S]*?(?:\*\/|$)`;

const SPECS = {
  js: new RegExp(`(?<com>\\/\\/.*|${BLOCK_COM})|(?<str>${STR_BT}|${STR_DQ}|${STR_SQ})|(?<num>${NUM})|(?<fn>[A-Za-z_$][\\w$]*)(?=\\s*\\()|(?<word>[A-Za-z_$][\\w$]*)`, 'g'),
  python: new RegExp(`(?<com>#.*)|(?<str>"""[\\s\\S]*?(?:"""|$)|'''[\\s\\S]*?(?:'''|$)|[rbfuRBFU]{0,2}${STR_DQ}|[rbfuRBFU]{0,2}${STR_SQ})|(?<num>${NUM})|(?<fn>[A-Za-z_]\\w*)(?=\\s*\\()|(?<word>@?[A-Za-z_]\\w*)`, 'g'),
  php: new RegExp(`(?<com>\\/\\/.*|#(?!\\[).*|${BLOCK_COM})|(?<str>${STR_DQ}|${STR_SQ})|(?<var>\\$[A-Za-z_]\\w*)|(?<num>${NUM})|(?<fn>[A-Za-z_]\\w*)(?=\\s*\\()|(?<word>[A-Za-z_]\\w*)`, 'g'),
  sql: new RegExp(`(?<com>--.*|${BLOCK_COM})|(?<str>${STR_SQ}|${STR_DQ}|\`[^\`\\n]*\`?)|(?<num>${NUM})|(?<fn>[A-Za-z_]\\w*)(?=\\s*\\()|(?<word>[A-Za-z_]\\w*)`, 'gi'),
  bash: new RegExp(`(?<com>(?<=^|\\s)#.*)|(?<str>${STR_DQ}|${STR_SQ})|(?<var>\\$\\{[^}\\n]*\\}?|\\$[A-Za-z_0-9@#?*!$-]\\w*)|(?<num>${NUM})|(?<word>[A-Za-z_][\\w-]*)`, 'g'),
  css: new RegExp(`(?<com>${BLOCK_COM})|(?<str>${STR_DQ}|${STR_SQ})|(?<kw>@[\\w-]+|!important)|(?<num>#[0-9a-fA-F]{3,8}\\b|-?\\d*\\.?\\d+(?:px|em|rem|%|vh|vw|vmin|vmax|s|ms|deg|fr|ch)?\\b)|(?<attr>[\\w-]+)(?=\\s*:[^:{;]*[;}])`, 'g'),
  json: new RegExp(`(?<key>"(?:[^"\\\\\\n]|\\\\.)*"(?=\\s*:))|(?<str>${STR_DQ})|(?<num>-?${NUM})|(?<lit>\\btrue\\b|\\bfalse\\b|\\bnull\\b)`, 'g'),
};
const ALIASES = {
  javascript: 'js', js: 'js', jsx: 'js', mjs: 'js', cjs: 'js', node: 'js', ts: 'js', typescript: 'js', tsx: 'js', vue: 'html',
  python: 'python', py: 'python', python3: 'python',
  php: 'php', laravel: 'php', blade: 'php',
  sql: 'sql', mysql: 'sql', postgresql: 'sql', postgres: 'sql', sqlite: 'sql', plsql: 'sql',
  bash: 'bash', sh: 'bash', shell: 'bash', zsh: 'bash', console: 'bash', shellscript: 'bash',
  css: 'css', scss: 'css', less: 'css',
  json: 'json', jsonc: 'json',
  html: 'html', xml: 'html', svg: 'html', htm: 'html',
};

function span(cls, text) { return `<span class="tok-${cls}">${escapeHtml(text)}</span>`; }

function highlightHtml(code) {
  const re = /<!--[\s\S]*?(?:-->|$)|<\/?([A-Za-z][\w:.-]*)((?:\s+[^\s=>/]+(?:\s*=\s*(?:"[^"]*"?|'[^']*'?|[^\s>]+))?)*)\s*\/?>?/g;
  let out = '', last = 0, m;
  while ((m = re.exec(code))) {
    if (!m[0]) { re.lastIndex++; continue; }
    out += escapeHtml(code.slice(last, m.index));
    if (m[0].startsWith('<!--')) out += span('com', m[0]);
    else {
      const close = m[0].startsWith('</') ? '&lt;/' : '&lt;';
      const attrs = (m[2] || '').replace(/([^\s=]+)(\s*=\s*)?("[^"]*"?|'[^']*'?|[^\s>]+)?/g, (all, name, eq, val) =>
        '\u0001' + name + '\u0002' + (eq || '') + '\u0003' + (val || '') + '\u0004');
      const rendered = escapeHtml(attrs)
        .replace(/\u0001([^\u0002]*)\u0002/g, '<span class="tok-attr">$1</span>')
        .replace(/\u0003([^\u0004]*)\u0004/g, (a, v) => v ? `<span class="tok-str">${v}</span>` : '');
      const tail = m[0].slice(m[0].length - (m[0].endsWith('/>') ? 2 : m[0].endsWith('>') ? 1 : 0));
      out += close + span('tag', m[1]) + rendered + escapeHtml(tail);
    }
    last = m.index + m[0].length;
  }
  return out + escapeHtml(code.slice(last));
}

export function highlight(code, lang) {
  const key = ALIASES[(lang || '').toLowerCase()];
  if (!key) return escapeHtml(code);
  if (key === 'html') return highlightHtml(code);
  const re = SPECS[key];
  const kw = sets[key];
  re.lastIndex = 0;
  let out = '', last = 0, m;
  while ((m = re.exec(code))) {
    if (!m[0]) { re.lastIndex++; continue; }
    out += escapeHtml(code.slice(last, m.index));
    const g = m.groups;
    if (g.com) out += span('com', m[0]);
    else if (g.str) out += span('str', m[0]);
    else if (g.key) out += span('key', m[0]);
    else if (g.var) out += span('var', m[0]);
    else if (g.num) out += span('num', m[0]);
    else if (g.kw) out += span('kw', m[0]);
    else if (g.attr) out += span('attr', m[0]);
    else if (g.lit) out += span('lit', m[0]);
    else if (g.fn) out += (kw && kw.has(key === 'sql' ? m[0].toLowerCase() : m[0])) ? span('kw', m[0]) : span('fn', m[0]);
    else if (g.word) {
      const w = key === 'sql' ? m[0].toLowerCase() : m[0];
      out += LIT.has(m[0]) ? span('lit', m[0]) : (kw && kw.has(w)) ? span('kw', m[0]) : escapeHtml(m[0]);
    } else out += escapeHtml(m[0]);
    last = m.index + m[0].length;
  }
  return out + escapeHtml(code.slice(last));
}

// ------------------------------------------------------------------ inline
function inlineText(escaped) {
  // Links become placeholders first so emphasis rules never touch URLs.
  const links = [];
  const out = escaped
    .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (a, alt) => `[image: ${alt}]`)
    .replace(/\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)/g, (all, text, url) => {
      if (!/^(https?:\/\/|mailto:)/i.test(url)) return text;
      links.push(url);
      return `\u0005${links.length - 1}\u0006${text}\u0007`;
    })
    .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^\w])__(?=\S)([\s\S]*?\S)__(?!\w)/g, '$1<strong>$2</strong>')
    .replace(/(^|[^*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?!\*)/g, '$1<em>$2</em>')
    .replace(/(^|[^\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?!\w)/g, '$1<em>$2</em>')
    .replace(/~~(?=\S)([\s\S]*?\S)~~/g, '<del>$1</del>');
  return out.replace(/\u0005(\d+)\u0006([\s\S]*?)\u0007/g, (a, n, text) =>
    `<a href="${links[+n]}" target="_blank" rel="noopener noreferrer">${text}</a>`);
}

export function inline(text) {
  const parts = text.split(/(`+[^`]*?`+)/g);
  return parts.map(p => {
    const m = p.match(/^(`+)([\s\S]*?)\1$/);
    if (m && m[2].length) return `<code>${escapeHtml(m[2].replace(/^ (.*) $/, '$1'))}</code>`;
    return inlineText(escapeHtml(p));
  }).join('');
}

// ------------------------------------------------------------------ blocks
const FENCE_RE = /^ {0,3}(`{3,}|~{3,})\s*([\w#+.-]*)[^`]*$/;
const LIST_RE = /^( *)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const HR_RE = /^ {0,3}([-*_])(\s*\1){2,}\s*$/;
const HEAD_RE = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const TABLE_SEP_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function indentOf(line) { return line.match(/^ */)[0].length; }

function isBlockStart(line, next) {
  return FENCE_RE.test(line) || HEAD_RE.test(line) || HR_RE.test(line) || /^\s*>/.test(line) ||
    LIST_RE.test(line) || (line.includes('|') && next !== undefined && TABLE_SEP_RE.test(next));
}

function codeBlock(code, lang) {
  const label = escapeHtml(lang || 'text');
  return `<div class="code-block"><div class="code-head"><span>${label}</span>` +
    `<button type="button" class="btn ghost code-copy" title="Copy code">Copy</button></div>` +
    `<pre><code>${highlight(code, lang)}</code></pre></div>`;
}

function splitRow(line) {
  let s = line.trim();
  if (s.startsWith('|')) s = s.slice(1);
  if (s.endsWith('|') && !s.endsWith('\\|')) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, '|'));
}

function parseList(lines, i) {
  const first = lines[i].match(LIST_RE);
  const base = first[1].length;
  const ordered = /\d/.test(first[2]);
  const start = ordered ? parseInt(first[2], 10) : 1;
  const items = [];
  let cur = null;
  while (i < lines.length) {
    const l = lines[i];
    const m = l.match(LIST_RE);
    if (m && m[1].length === base && /\d/.test(m[2]) === ordered) { cur = [m[3]]; items.push(cur); i++; continue; }
    if (m && m[1].length < base) break;
    if (!l.trim()) {
      const nxt = lines[i + 1];
      const nm = nxt !== undefined && nxt.match(LIST_RE);
      if (nxt !== undefined && nxt.trim() && (indentOf(nxt) > base || (nm && nm[1].length === base && /\d/.test(nm[2]) === ordered))) {
        cur.push(''); i++; continue;
      }
      break;
    }
    if (indentOf(l) > base) { cur.push(l.slice(Math.min(indentOf(l), base + 2))); i++; continue; }
    if (!m && !isBlockStart(l, lines[i + 1])) { cur.push(l.trim()); i++; continue; }
    break;
  }
  const tag = ordered ? 'ol' : 'ul';
  const startAttr = ordered && start !== 1 ? ` start="${start}"` : '';
  const html = `<${tag}${startAttr}>` + items.map(it => {
    const body = render(it.join('\n'));
    // A single paragraph renders without its <p> wrapper for tight lists.
    const tight = body.match(/^<p>([\s\S]*)<\/p>$/);
    return `<li>${tight && !tight[1].includes('<p>') ? tight[1] : body}</li>`;
  }).join('') + `</${tag}>`;
  return [html, i];
}

function render(src) {
  const lines = src.replace(/\r\n?/g, '\n').replace(/\t/g, '    ').split('\n');
  let html = '';
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    let m = line.match(FENCE_RE);
    if (m) {
      const fence = m[1];
      const lang = m[2].toLowerCase();
      const buf = [];
      i++;
      const closeRe = new RegExp(`^ {0,3}${fence[0] === '`' ? '`' : '~'}{${fence.length},}\\s*$`);
      while (i < lines.length && !closeRe.test(lines[i])) buf.push(lines[i++]);
      i++;
      html += codeBlock(buf.join('\n'), lang);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    if ((m = line.match(HEAD_RE))) {
      const n = m[1].length;
      html += `<h${n}>${inline(m[2])}</h${n}>`;
      i++;
      continue;
    }
    if (HR_RE.test(line)) { html += '<hr>'; i++; continue; }
    if (/^\s*>/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) buf.push(lines[i++].replace(/^\s*> ?/, ''));
      html += `<blockquote>${render(buf.join('\n'))}</blockquote>`;
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && TABLE_SEP_RE.test(lines[i + 1])) {
      const head = splitRow(line);
      const aligns = splitRow(lines[i + 1]).map(c => c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : '');
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(splitRow(lines[i++]));
      const al = j => aligns[j] ? ` class="al-${aligns[j]}"` : '';
      html += '<table><thead><tr>' + head.map((c, j) => `<th${al(j)}>${inline(c)}</th>`).join('') + '</tr></thead><tbody>' +
        rows.map(r => '<tr>' + head.map((_, j) => `<td${al(j)}>${inline(r[j] || '')}</td>`).join('') + '</tr>').join('') +
        '</tbody></table>';
      continue;
    }
    if (LIST_RE.test(line)) {
      const [h, next] = parseList(lines, i);
      html += h;
      i = next;
      continue;
    }
    const buf = [line];
    i++;
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i], lines[i + 1])) buf.push(lines[i++]);
    html += `<p>${buf.map(l => inline(l.trim())).join('<br>')}</p>`;
  }
  return html;
}

export function renderMarkdown(src) {
  return render(src || '');
}
