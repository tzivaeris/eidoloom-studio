/* Eidoloom Studio frontend — plain ES2020, no build step. See SPEC.md. */
(() => {
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const CDN_MONACO = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.57.0/min';
const CDN_XTERM = 'https://cdn.jsdelivr.net/npm/@xterm/xterm@6.0.0/lib/xterm.min.js';
const CDN_FIT = 'https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.11.0/lib/addon-fit.min.js';

/* ------------------------------------------------------------------ utils */
function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
}
const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
const lsGet = (k, d = null) => { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch { return d; } };
function prefSet(key, value) {
  if (S.prefs[key] === value) return;
  S.prefs[key] = value;
  S.prefsTouched?.add(key);
  POST('/api/prefs', { key, value }).catch(() => {});
}
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } };
const lsJson = (k, d) => { try { return JSON.parse(lsGet(k)) ?? d; } catch { return d; } };
const basename = p => p.split('/').pop();
const dirname = p => { const i = p.lastIndexOf('/'); return i < 0 ? '' : p.slice(0, i); };
const joinPath = (a, b) => a ? a + '/' + b : b;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
function fmtNum(n) { n = n || 0; return n >= 10000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1) + 'k' : n >= 1000 ? (n / 1000).toFixed(1) + 'k' : String(n); }
function fmtElapsed(ms) { const s = Math.floor(ms / 1000); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }
function ago(ts) {
  if (!ts) return '';
  const t = typeof ts === 'number' ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(ts);
  if (!t) return '';
  const s = (Date.now() - t) / 1000;
  if (s < 60) return 'now'; if (s < 3600) return Math.floor(s / 60) + 'm'; if (s < 86400) return Math.floor(s / 3600) + 'h';
  if (s < 86400 * 30) return Math.floor(s / 86400) + 'd'; return new Date(t).toLocaleDateString();
}
function b64ToBytes(b64) { const bin = atob(b64); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); return u; }
function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).catch(() => legacyCopy(text));
  legacyCopy(text); return Promise.resolve();
}
function legacyCopy(text) {
  const ta = el('textarea', { style: 'position:fixed;left:-999px;top:0;opacity:0' }); ta.value = text;
  document.body.append(ta); ta.select(); try { document.execCommand('copy'); } catch { /* ignore */ } ta.remove();
}
function loadScript(src, timeout = 9000) {
  return new Promise((res, rej) => {
    const s = document.createElement('script'); s.src = src; s.async = true;
    const t = setTimeout(() => { rej(new Error('timeout ' + src)); }, timeout);
    s.onload = () => { clearTimeout(t); res(); };
    s.onerror = () => { clearTimeout(t); rej(new Error('failed ' + src)); };
    document.head.append(s);
  });
}

/* ------------------------------------------------------------------ icons */
const P = {
  gauge: '<path d="M4.2 18a9 9 0 1 1 15.6 0"/><path d="m12 14 4-5"/><circle cx="12" cy="14" r="1.5"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.9 4.9 7 7M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1"/>',
  detach: '<path d="M14 4h6v6"/><path d="m20 4-8 8"/><path d="M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"/>',
  dock: '<rect x="3" y="4" width="18" height="16" rx="2"/><rect x="12" y="12" width="6" height="5" rx="1"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.7a3.5 3.5 0 0 1 0 6.6"/><path d="M18.5 14.5a6.5 6.5 0 0 1 3 5.5"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  branch: '<circle cx="6" cy="5" r="2.5"/><circle cx="6" cy="19" r="2.5"/><circle cx="18" cy="7" r="2.5"/><path d="M6 7.5v9"/><path d="M18 9.5c0 4-6 3-12 7"/>',
  download: '<path d="M12 4v11"/><path d="m7 11 5 5 5-5"/><path d="M5 20h14"/>',
  upload: '<path d="M12 16V5"/><path d="m7 9 5-5 5 5"/><path d="M5 20h14"/>',
  theme: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>',
  eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
  more: '<circle cx="12" cy="5" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="19" r="1.4" fill="currentColor"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .9-1 1.6V14"/><path d="M12 17.5h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 7.5v.5"/>',
  files: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.35-4.35"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  'folder-open': '<path d="M3 8V6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v1"/><path d="M3.5 19 6 10h16l-2.5 9a1 1 0 0 1-1 .8H4.6a1 1 0 0 1-1.1-.8z"/>',
  'file-plus': '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M12 11v6M9 14h6"/>',
  'folder-plus': '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M12 10v6M9 13h6"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
  collapse: '<path d="m7 20 5-5 5 5M7 4l5 5 5-5"/>',
  'chevron-right': '<path d="m9 18 6-6-6-6"/>',
  'chevron-left': '<path d="m15 18-6-6 6-6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
  server: '<rect x="3" y="3" width="18" height="8" rx="2"/><rect x="3" y="13" width="18" height="8" rx="2"/><path d="M7 7h.01M7 17h.01"/>',
  'pc-copy': '<rect x="2" y="4" width="20" height="13" rx="2" stroke-dasharray="3 2"/><path d="M8 21h8M12 17v4"/>',
  gift: '<rect x="3" y="8" width="18" height="13" rx="1"/><path d="M12 8v13M3 12h18M12 8S10 3 7.5 4.5 9 8 12 8zm0 0s2-5 4.5-3.5S15 8 12 8z"/>',
  pc: '<rect x="3" y="4" width="18" height="12" rx="1.5"/><path d="M8 20h8M12 16v4"/>',
  mobile: '<rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/>',
  send: '<path d="M21 3 10.5 13.5"/><path d="M21 3l-6.5 18-4-7.5L3 9.5z"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
  'chevron-down': '<path d="m6 9 6 6 6-6"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  terminal: '<path d="m4 17 6-6-6-6M12 19h8"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  'arrow-up': '<path d="M12 19V5M5 12l7-7 7 7"/>',
  'arrow-down': '<path d="M12 5v14M19 12l-7 7-7-7"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  undo: '<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-15-6.7L3 13"/>',
  redo: '<path d="M21 7v6h-6"/><path d="M3 17a9 9 0 0 1 15-6.7L21 13"/>',
  scissors: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4L8.1 15.9M14.5 14.5L20 20M8.1 8.1L12 12"/>',
  clipboard: '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>',
  volume: '<path d="M11 5L6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/>',
  wand: '<path d="M15 4V2M15 16v-2M8 9h2M20 9h2M17.8 11.8L19 13M17.8 6.2L19 5M3 21l9-9M12.2 6.2L11 5"/>',
  slices: '<path d="M4 4v16M20 4v16M12 3v3M12 9v3M12 15v3M12 21v0"/>',
  save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8M7 3v5h8"/>',
  play: '<path d="M7 4l13 8-13 8z"/>',
  pause: '<path d="M7 4h3v16H7zM14 4h3v16h-3z"/>',
  loop: '<path d="M17 2l4 4-4 4"/><path d="M3 11V9a3 3 0 0 1 3-3h15"/><path d="M7 22l-4-4 4-4"/><path d="M21 13v2a3 3 0 0 1-3 3H3"/>',
  'skip-back': '<path d="M19 20L9 12l10-8z"/><path d="M5 19V5"/>',
  'zoom-in': '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3M11 8v6M8 11h6"/>',
  'zoom-out': '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3M8 11h6"/>',
  square: '<rect x="4" y="4" width="16" height="16" rx="2"/>',
  skill: '<path d="M13 2L4.5 13.5H11L10 22l8.5-11.5H12z"/>',
  sparkles: '<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  'panel-right': '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
  wrap: '<path d="M3 6h18M3 12h15a3 3 0 1 1 0 6h-4M16 16l-2 2 2 2M3 18h7"/>',
  minimap: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M15 3v18M6 7h6M6 10h4M6 13h6M6 16h3"/><rect x="16.5" y="6" width="3" height="5" rx=".5"/>',
  edit: '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  paperclip: '<path d="m21.4 11.1-9.2 9.2a5 5 0 0 1-7-7l9.2-9.2a3.3 3.3 0 0 1 4.7 4.7l-9.2 9.2a1.7 1.7 0 0 1-2.3-2.3l8.5-8.5"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  eidoloom: '<path stroke="#3794ff" d="M2 15C6 6.5 18 6.5 22 15"/><path stroke="#3794ff" d="M2 9c4 8.5 16 8.5 20 0"/><circle style="stroke:var(--logo-pupil)" cx="12" cy="12" r="2.7"/><path style="stroke:var(--logo-pupil)" d="M12 3v3.2M12 17.8V21"/>',
  layers: '<path d="m3 7 9-4 9 4-9 4z"/><path d="m3 12 9 4 9-4"/><path d="m3 17 9 4 9-4"/>',
  diff: '<path d="M12 3v10M7 8h10M7 19h10"/>',
  sort: '<path d="m3 16 4 4 4-4M7 20V4M21 8l-4-4-4 4M17 4v16"/>',
  box: '<path d="m7.5 4.3 9 5.2"/><path d="M21 16V8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4a2 2 0 0 0 1-1.7z"/><path d="M3.3 7 12 12l8.7-5M12 22V12"/>',
  library: '<rect x="2" y="8" width="15" height="13" rx="2"/><path d="M6 4.5h12.5A2.5 2.5 0 0 1 21 7v10"/><path d="M8 11.5v6l5-3z"/>',     // Media: every kind of file
  create: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M12 7l1.3 3.7L17 12l-3.7 1.3L12 17l-1.3-3.7L7 12l3.7-1.3z"/>',     // ComfyUI: made pictures, video and music
  media: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  cube: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="M3.3 7 12 12l8.7-5M12 22V12"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
  film: '<rect x="2" y="3" width="20" height="18" rx="2"/><path d="M7 3v18M17 3v18M2 8h5M2 16h5M17 8h5M17 16h5M7 12h10"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
  keyboard: '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
  brain: '<path d="M9 4a3 3 0 0 0-3 3 3 3 0 0 0-2 5 3 3 0 0 0 2 5 3 3 0 0 0 6 1V4a3 3 0 0 0-3 0z"/><path d="M15 4a3 3 0 0 1 3 3 3 3 0 0 1 2 5 3 3 0 0 1-2 5 3 3 0 0 1-6 1"/>',
  sheet: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/>',
  flag: '<path d="M5 21V4M5 4h12l-2 4 2 4H5"/>',
  plug: '<path d="M9 2v6M15 2v6M6 8h12v3a6 6 0 0 1-12 0zM12 17v5"/>',
  mail: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 6L2 7"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5"/><path d="M3 12c0 1.7 4 3 9 3s9-1.3 9-3"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>',
  music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  spell: '<path d="M3 15 7 5l4 10M4.5 11.5h5"/><path d="m12 16 3 3 6-7"/>',
};
function ico(name, size = 16, extra = '') {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" ${extra}>${P[name] || ''}</svg>`;
}
function fillIcons(root = document) {
  $$('i[data-ico]', root).forEach(i => { if (!i.firstChild) i.innerHTML = ico(i.dataset.ico, +i.dataset.size || 16); });
}
const EXT_COLORS = {
  js: '#e5c07b', mjs: '#e5c07b', cjs: '#e5c07b', jsx: '#61dafb', ts: '#4d9de0', tsx: '#61dafb', json: '#cbcb41', py: '#5aa2dc', md: '#6aa9d8',
  html: '#e37933', htm: '#e37933', css: '#56a0d8', scss: '#e060a0', rs: '#dea584', go: '#4fc1d6', java: '#e76f51', c: '#6a9fd8', h: '#a074c4', cpp: '#6a9fd8',
  sh: '#89d185', yml: '#cb6e6e', yaml: '#cb6e6e', toml: '#9c8c6c', txt: '#9d9d9d', svg: '#e5a840', png: '#b180d7', jpg: '#b180d7', lock: '#777', sql: '#d9a05b', rb: '#d35a5a', php: '#8e8ac8',
};
function fileIco(name, size = 15) {
  const ext = (name.split('.').pop() || '').toLowerCase();
  const color = EXT_COLORS[name.includes('.') ? ext : ''] || '#8c8c8c';
  return ico('file', size, `style="color:${color}"`);
}

/* ------------------------------------------------------------------ toasts / modal / menus */
function toast(msg, kind = 'info', ms = 5000) {
  const t = el('div', { class: 'toast ' + kind, role: 'status' }, String(msg));
  t.onclick = () => t.remove();
  $('#toasts').append(t);
  if (ms) setTimeout(() => t.remove(), ms);
  return t;
}
const overlayStack = [];
function openOverlay(content, { center = false, onClose } = {}) {
  const ov = el('div', { class: 'overlay' + (center ? ' center' : '') }, content);
  const entry = { ov, onClose };
  ov.addEventListener('mousedown', e => { if (e.target === ov) closeOverlay(entry); });
  $('#overlay-root').append(ov); overlayStack.push(entry);
  return entry;
}
function closeOverlay(entry) {
  const i = overlayStack.indexOf(entry); if (i < 0) return;
  overlayStack.splice(i, 1); entry.ov.remove(); entry.onClose && entry.onClose();
}
/** Generic dialog. buttons: [{label, value, kind}] -> resolves with value (undefined on dismiss). */
function dialog({ title, body, buttons, input, small = true }) {
  return new Promise(resolve => {
    let inp = null, done = false, entry;
    const finish = v => { if (done) return; done = true; closeOverlay(entry); resolve(v); };
    const bodyEl = el('div', { class: 'dlg-body' });
    if (typeof body === 'string') bodyEl.append(el('p', { text: body })); else if (body) bodyEl.append(body);
    if (input) {
      inp = el('input', { class: 'dlg-input', type: 'text', value: input.value || '', placeholder: input.placeholder || '', spellcheck: 'false' });
      inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); finish({ value: inp.value }); } });
      bodyEl.append(inp);
    }
    const foot = el('div', { class: 'dlg-foot' }, (buttons || []).map(b =>
      el('button', { class: 'btn ' + (b.kind || ''), onclick: () => finish(input && b.value === 'ok' ? { value: inp.value } : b.value) }, b.label)));
    const dlg = el('div', { class: 'dialog' + (small ? ' sm' : ''), role: 'dialog', 'aria-modal': 'true' }, el('div', { class: 'dlg-head', text: title }), bodyEl, foot);
    entry = openOverlay(dlg, { center: true, onClose: () => { if (!done) { done = true; resolve(undefined); } } });
    setTimeout(() => { if (inp) { inp.focus(); inp.select(); } else { const pb = $('.btn.primary', foot) || $('.btn', foot); pb && pb.focus(); } }, 10);
  });
}
async function confirmBox(title, text, okLabel = 'OK', danger = false) {
  return (await dialog({ title, body: text, buttons: [{ label: 'Cancel', value: false }, { label: okLabel, value: true, kind: danger ? 'primary' : 'primary' }] })) === true;
}
async function promptBox(title, value = '', placeholder = '') {
  const r = await dialog({ title, input: { value, placeholder }, buttons: [{ label: 'Cancel', value: false }, { label: 'OK', value: 'ok', kind: 'primary' }] });
  return r && typeof r === 'object' ? r.value : null;
}
let ctxMenu = null;
function closeCtxMenu() { if (ctxMenu) { ctxMenu.remove(); ctxMenu = null; } }
function showContextMenu(x, y, items) {
  closeCtxMenu();
  const m = el('div', { class: 'menu menu-ctx' });
  for (const it of items) {
    if (it === '-') { m.append(el('div', { class: 'msep' })); continue; }
    m.append(el('div', { class: 'mi' + (it.danger ? ' danger' : ''), onclick: () => { closeCtxMenu(); it.run(); } }, el('span', { class: 'grow', text: it.label })));
  }
  document.body.append(m);
  const r = m.getBoundingClientRect();
  m.style.left = Math.min(x, innerWidth - r.width - 6) + 'px'; m.style.top = Math.min(y, innerHeight - r.height - 6) + 'px';
  ctxMenu = m;
}
document.addEventListener('mousedown', e => { if (ctxMenu && !ctxMenu.contains(e.target)) closeCtxMenu(); }, true);
window.addEventListener('blur', closeCtxMenu);

/* ------------------------------------------------------------------ themes */
const lightTerminal = { black: '#3b3b3b', red: '#b42318', green: '#1a7f37', yellow: '#7f5500', blue: '#0064b5', magenta: '#8250df', cyan: '#0b7285', white: '#6e6e6e',
  brightBlack: '#6e6e6e', brightRed: '#cf222e', brightGreen: '#1a7f37', brightYellow: '#8f6000', brightBlue: '#0064b5', brightMagenta: '#8250df', brightCyan: '#0b7285', brightWhite: '#333333' };
const THEMES = {
  dark: { label: 'Dark', monaco: 'vs-dark', terminal: { background: '#1f1f1f', foreground: '#cccccc', cursor: '#cccccc', selectionBackground: '#264f78', black: '#1f1f1f', brightBlack: '#666666' } },
  light: { label: 'Light', monaco: 'vs', terminal: { ...lightTerminal, background: '#ffffff', foreground: '#333333', cursor: '#333333', selectionBackground: '#add6ff' } },
  sepia: { label: 'Sepia', monaco: 'studio-sepia', terminal: { ...lightTerminal, background: '#f4ecd8', foreground: '#433422', cursor: '#433422', selectionBackground: '#d8c49c' } },
};
const SEPIA_MONACO = { base: 'vs', inherit: true, rules: [], colors: {
  'editor.background': '#f4ecd8', 'editor.foreground': '#433422', 'editorGutter.background': '#f4ecd8', 'editorLineNumber.foreground': '#a08c6c', 'editorLineNumber.activeForeground': '#433422',
  'editor.lineHighlightBackground': '#eadfc6', 'editor.selectionBackground': '#d8c49c', 'editorCursor.foreground': '#433422', 'minimap.background': '#eadfc6',
  'editorWidget.background': '#f8f1df', 'editorWidget.border': '#bfae8a', 'input.background': '#fbf6e9', 'diffEditor.insertedTextBackground': '#8ba84a44', 'diffEditor.removedTextBackground': '#c0503a33' } };
function currentTheme() { const t = document.documentElement.dataset.theme; return THEMES[t] ? t : 'dark'; }
function applyTheme(name) {
  prefSet('theme', name);
  if (name === 'dark') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = name;
  lsSet('studio.theme', name);
  if (S.monaco) S.monaco.editor.setTheme(THEMES[name].monaco);
  for (const t of TM.terms) if (t.xterm) t.xterm.options.theme = THEMES[name].terminal;
}

/* ------------------------------------------------------------------ API */
const CLIENT_ID = Math.random().toString(36).slice(2);     // lets a window ignore the changes it made itself
class ApiError extends Error { constructor(status, data) { super((data && data.error) || ('HTTP ' + status)); this.status = status; this.data = data || {}; } }
let onUnauthorized = () => {};
function qs(params) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) if (v !== undefined && v !== null) u.set(k, String(v));
  const s = u.toString(); return s ? '?' + s : '';
}
/* Streams, or long polling with the same events where streams do not get through. */
const LIVE = { poll: false };
try { LIVE.poll = sessionStorage.getItem('studio.poll') === '1'; } catch { /* streams, until they prove silent */ }
class PollStream extends EventTarget {
  constructor(url, params) { super(); this.url = url; this.params = { ...params }; this.readyState = 0; this.onerror = null; this.stop = new AbortController(); this.loop(); }
  close() { this.readyState = 2; this.stop.abort(); }
  failed() { const ev = new Event('error'); this.dispatchEvent(ev); if (this.onerror) this.onerror(ev); }
  async loop() {
    while (this.readyState !== 2) {
      let reply;
      try {
        const r = await fetch(this.url + qs(this.params), { signal: this.stop.signal, cache: 'no-store' });
        if (!r.ok) { if (this.readyState !== 2) { this.readyState = 2; this.failed(); } return; }
        reply = await r.json();
      } catch {
        if (this.readyState === 2) return;
        this.failed(); await sleep(3000); continue;
      }
      if (this.readyState === 2) return;
      if (this.readyState === 0) { this.readyState = 1; this.dispatchEvent(new Event('open')); }
      for (const ev of reply.events) { if (this.readyState === 2) return; this.dispatchEvent(new MessageEvent(ev.type, { data: JSON.stringify(ev.data) })); }
      Object.assign(this.params, reply.cursor);
      if (reply.end) { if (this.readyState !== 2) { this.readyState = 2; this.failed(); } return; }
      await sleep(120);
    }
  }
}
function liveStream(name, params) {
  return LIVE.poll ? new PollStream(`/api/${name}/poll`, params) : new EventSource(`/api/${name}/stream` + qs(params));
}
async function api(method, url, body, opts = {}) {
  const init = { method, credentials: 'same-origin', headers: { 'X-Studio-Client': CLIENT_ID } };
  if (method !== 'GET') { init.headers['X-Studio'] = '1'; init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body || {}); }
  let r;
  try { r = await fetch(url, init); } catch (e) { throw new ApiError(0, { error: 'Network error: ' + e.message }); }
  let data = null;
  const txt = await r.text();
  try { data = txt ? JSON.parse(txt) : {}; } catch { data = { error: txt.slice(0, 200) || ('HTTP ' + r.status) }; }
  if (r.status === 401 && !opts.noAuthRedirect) onUnauthorized();
  noteBuild(r.headers.get('X-Studio-Build'));
  if (!r.ok) throw new ApiError(r.status, data);
  return data;
}
// Studio stamps the page with the build it served; an API reply naming another build means Studio was updated since.
const PAGE_BUILD = document.querySelector('meta[name="studio-build"]')?.content || '';
let buildNotice = null;
function noteBuild(build) {
  if (!build || !PAGE_BUILD || build === PAGE_BUILD || buildNotice) return;
  buildNotice = toast('', 'info update-notice', 0);
  buildNotice.onclick = null;
  buildNotice.append(el('span', { text: 'Studio was updated. Reload to use the new version.' }),
    el('span', { class: 'un-btns' },
      el('button', { class: 'btn sm primary', text: 'Reload', onclick: () => location.reload() }),
      el('button', { class: 'btn sm', text: 'Later', onclick: () => buildNotice.remove() })));
}
const GET = (url, params, opts) => api('GET', url + qs(params), null, opts);
const POST = (url, body, opts) => api('POST', url, body, opts);
function errToast(e, prefix = '') { if (e && e.status === 401) return; toast((prefix ? prefix + ': ' : '') + (e && e.message || e), 'error'); }

/* ------------------------------------------------------------------ global state */
const S = {
  ws: null,               // {id,path,name}
  config: {},
  models: [],
  model: lsGet('studio.model'),
  autoApply: lsGet('studio.autoApply') === '1',
  autoCmd: lsGet('studio.autoCmd') === '1',
  monaco: null, monacoFailed: false,
  xterm: false,
  wrap: lsGet('studio.wrap') === '1',
  minimap: lsGet('studio.minimap') !== '0',     // the code map beside the editor; on unless turned off
  booted: false,
  prefs: {},
};
// xterm goes first: its script registers as an anonymous AMD module if Monaco's loader is already on the page,
// which fails and leaves window.Terminal undefined. Don't let a slow CDN hold the editor back for long.
const xtermReady = loadXterm();
const monacoReady = Promise.race([xtermReady, new Promise(r => setTimeout(r, 8000))]).then(loadMonaco);

function loadMonaco() {
  return new Promise(resolve => {
    loadScript(CDN_MONACO + '/vs/loader.js').then(() => {
      window.MonacoEnvironment = {
        getWorkerUrl() {
          const code = `self.MonacoEnvironment={baseUrl:'${CDN_MONACO}/'};importScripts('${CDN_MONACO}/vs/base/worker/workerMain.js');`;
          return URL.createObjectURL(new Blob([code], { type: 'text/javascript' }));
        },
      };
      window.require.config({ paths: { vs: CDN_MONACO + '/vs' } });
      const t = setTimeout(() => fail(new Error('monaco timeout')), 20000);
      const fail = e => { clearTimeout(t); S.monacoFailed = true; resolve(null); console.warn(e); };
      window.require(['vs/editor/editor.main'], () => { clearTimeout(t); S.monaco = window.monaco; S.monaco.editor.defineTheme('studio-sepia', SEPIA_MONACO); resolve(window.monaco); }, fail);
    }).catch(e => { S.monacoFailed = true; console.warn('Monaco failed to load; using plain editor', e.message); resolve(null); });
  });
}
function loadXterm() {
  return loadScript(CDN_XTERM).then(() => loadScript(CDN_FIT)).then(() => { S.xterm = !!(window.Terminal && window.FitAddon); return S.xterm; })
    .catch(e => { console.warn('xterm failed to load; using simple terminal', e.message); S.xterm = false; return false; });
}

/* ------------------------------------------------------------------ login */
const LOGIN_MODES = {
  login: { title: 'Sign in', sub: 'Sign in to continue.', submit: 'Sign in', user: true, confirm: false, remember: true, name: false, forgot: true, pw: 'Password' },
  setup: { title: 'Create the admin account', sub: 'This first account manages the server and invites everyone else.', submit: 'Create account', user: true, confirm: true, remember: false, name: false, forgot: false, pw: 'Password (at least 8 characters)' },
  invite: { title: 'Create your account', sub: 'You have been invited to Eidoloom Studio.', submit: 'Create account', user: true, confirm: true, remember: false, name: true, forgot: false, pw: 'Password (at least 8 characters)' },
  reset: { title: 'Choose a new password', sub: '', submit: 'Set new password', user: false, confirm: true, remember: false, name: false, forgot: false, pw: 'New password (at least 8 characters)' },
};
function showLogin(setupNeeded, mode, info = {}) {
  $('#app').classList.add('hidden');
  const L = $('#login'); L.classList.remove('hidden');
  mode = mode || (setupNeeded ? 'setup' : 'login');
  const m = LOGIN_MODES[mode];
  L.dataset.mode = mode; L.dataset.token = info.token || '';
  $('#login-title').textContent = m.title;
  $('#login-sub').textContent = info.sub || m.sub;
  $('#login-user-wrap').classList.toggle('hidden', !m.user);
  $('#login-name-wrap').classList.toggle('hidden', !m.name);
  $('#login-pw2-wrap').classList.toggle('hidden', !m.confirm);
  $('#login-remember-wrap').classList.toggle('hidden', !m.remember);
  $('#login-forgot').classList.toggle('hidden', !m.forgot);
  $('#login-pw-label').textContent = m.pw;
  $('#login-pw').autocomplete = m.confirm ? 'new-password' : 'current-password';
  $('#login-submit').textContent = m.submit;
  $('#login-error').textContent = info.error || '';
  $('#login-user').value = mode === 'setup' ? 'admin' : info.username || '';
  $('#login-pw').value = ''; $('#login-pw2').value = '';
  setTimeout(() => (m.user && !$('#login-user').value ? $('#login-user') : $('#login-pw')).focus(), 30);
}
// On a phone a keyboard language, a capital letter or a space from a suggestion can change what gets typed.
$('#login-show').onclick = () => {
  const input = $('#login-pw'), shown = input.type === 'password';
  input.type = shown ? 'text' : 'password';
  $('#login-show').setAttribute('aria-pressed', shown ? 'true' : 'false');
  $('#login-show').setAttribute('aria-label', shown ? 'Hide password' : 'Show password');
  input.focus();
};
$('#login-forgot').onclick = () => { $('#login-error').textContent = 'Ask an admin for a reset link. It works once and expires after an hour.'; };
function clearLinkToken() { history.replaceState(null, '', location.pathname + location.search); }
let unauthTimer = null;
onUnauthorized = () => {
  if (!$('#login').classList.contains('hidden')) return;
  clearTimeout(unauthTimer);
  unauthTimer = setTimeout(async () => {
    try { const st = await GET('/api/auth/state', null, { noAuthRedirect: true }); if (st.logged_in) return; showLogin(st.setup_needed); } catch { showLogin(false); }
    agentDetach(); termCloseAll(true);
  }, 50);
};
$('#login-card').addEventListener('submit', async e => {
  e.preventDefault();
  const L = $('#login'), mode = L.dataset.mode || 'login', m = LOGIN_MODES[mode];
  const pw = $('#login-pw').value, err = $('#login-error'), username = $('#login-user').value.trim();
  err.textContent = '';
  if (m.user && !username) { err.textContent = 'Enter a username.'; return; }
  if (m.confirm) {
    if (pw.length < 8) { err.textContent = 'Password must be at least 8 characters.'; return; }
    if (pw !== $('#login-pw2').value) { err.textContent = 'Passwords do not match.'; return; }
  }
  const btn = $('#login-submit'); btn.disabled = true;
  try {
    const opts = { noAuthRedirect: true };
    if (mode === 'login') await POST('/api/auth/login', { username, password: pw, remember: $('#login-remember').checked }, opts);
    else if (mode === 'setup') await POST('/api/auth/setup', { username, password: pw }, opts);
    else if (mode === 'invite') await POST('/api/auth/signup', { token: L.dataset.token, username, display_name: $('#login-name').value, password: pw }, opts);
    else {
      await POST('/api/auth/reset', { token: L.dataset.token, password: pw }, opts);
      clearLinkToken();
      showLogin(false, 'login', { sub: 'Password changed. Sign in with your new password.' });
      return;
    }
    clearLinkToken();
    // A different person on this browser must not inherit the previous user's folder.
    const previous = S.config && S.config.user && S.config.user.username;
    if (previous && previous !== username.toLowerCase()) { lsSet('studio.ws', ''); try { sessionStorage.removeItem('studio.windowFolder'); } catch { /* ignore */ } location.reload(); return; }
    L.classList.add('hidden');
    await bootApp();
  } catch (ex) {
    err.textContent = ex.status === 429 ? 'Too many attempts. Wait a minute and try again.' : ex.status === 401 ? 'Wrong username or password.' : ex.message;
  } finally { btn.disabled = false; }
});
$('#btn-logout').onclick = async () => {
  try { await POST('/api/auth/logout', {}, { noAuthRedirect: true }); } catch { /* ignore */ }
  agentDetach(); termCloseAll(true);
  lsSet('studio.ws', ''); try { sessionStorage.removeItem('studio.windowFolder'); } catch { /* ignore */ }
  location.reload();
};

/* ------------------------------------------------------------------ account and user management */
function isAdmin() { const u = S.config && S.config.user; return !u || u.role === 'admin'; }
// Admins, and users an admin gave the Graphics card right (Users pane).
function mayCard() { return isAdmin() || !!S.config?.user?.card; }
function linkBase() { return (S.config.public_url || location.origin).replace(/\/$/, '') + '/'; }
function modal(title, body, { wide = false } = {}) {
  const close = el('button', { class: 'btn' }, 'Close');
  const dlg = el('div', { class: 'dialog' + (wide ? ' wide' : ' sm'), role: 'dialog', 'aria-modal': 'true' }, el('div', { class: 'dlg-head', text: title }), el('div', { class: 'dlg-body' }, body), el('div', { class: 'dlg-foot' }, close));
  const entry = openOverlay(dlg);
  close.onclick = () => closeOverlay(entry);
  return entry;
}
function linkBox(url, note) {
  const input = el('input', { class: 'dlg-input', type: 'text', readonly: 'readonly', value: url, onclick: e => e.target.select() });
  const copy = el('button', { class: 'btn sm primary', onclick: async () => {
    input.select();
    try { await navigator.clipboard.writeText(url); toast('Link copied'); } catch { toast(document.execCommand('copy') ? 'Link copied' : 'Press Ctrl+C to copy', 'info'); }
  } }, 'Copy');
  const local = /^https?:\/\/(localhost|127\.)/.test(url);
  return el('div', { class: 'link-box' }, el('div', { class: 'link-row' }, input, copy), el('small', { text: note }),
    local ? el('small', { class: 'warn-text', text: 'This link uses localhost, so it only works on this machine. Set "public_url" in the config, or create it from the address other people use.' }) : null);
}
$('#chat-export').onclick = () => {
  if (!A.session) { toast('There is no chat to export yet', 'info'); return; }
  const link = el('a', { href: '/api/agent/session/export' + qs({ id: A.session }), download: '' });
  document.body.append(link); link.click(); link.remove();
};
/* How Strata and the tools share the graphics card (GET/POST /api/card). The engine's share is set when it starts, so a
   choice waits until Strata is not running, or applies now with Restart Strata now. */
const CARD_CHOICES = [
  ['strata', 'Strata first', 'Strata gets the whole card and answers faster. A picture or a reading first unloads Strata while it is idle, and Strata loads again on its next request (about 30 to 90 seconds each time).'],
  ['tools', 'Tools ready', 'Strata keeps 9 GB free for pictures and voices, which start at once. Strata runs with less of the card, so it is slower all the time.']];
async function paneCard(box) {
  const body = el('div', { class: 'ac-card' }, skeleton(3));
  box.replaceChildren(acHead('Graphics card', 'How Strata and the picture and voice tools share the graphics card.'), body);
  const gb = mib => `${(mib / 1024).toFixed(mib % 1024 ? 1 : 0)} GB`;
  const paint = d => {
    const pool = d.pool;
    const now = d.strata_server === 'own' ? 'Studio talks to Strata\'s own server, which someone else started, so the share is the one it started with. To choose it here, let Studio start Strata (strata_dir in studio.config.json).'
      : !pool ? 'The Ollama Manager does not answer, so the current share is unknown.'
      : !pool.running ? 'Strata is not loaded now; it starts with the chosen share.'
      : pool.active_mib >= 8192 ? `Strata is running with ${gb(pool.active_mib)} kept free for tools.` : 'Strata is running with the whole card.';
    const choices = el('div', { class: 'card-choices', role: 'radiogroup', 'aria-label': 'How the card is shared' }, ...CARD_CHOICES.map(([id, name, text]) =>
      el('button', { class: 'card-choice' + (d.mode === id ? ' on' : ''), role: 'radio', 'aria-checked': String(d.mode === id),
        onclick: async () => { if (d.mode === id) return; try { paint(await POST('/api/card', { mode: id })); } catch (err) { errToast(err, 'Could not save'); } } },
        el('span', { class: 'card-dot' }), el('span', { class: 'card-text' }, el('b', { text: name }), el('small', { text: text })))));
    const restart = el('button', { class: 'btn primary', disabled: d.runs > 0 ? '' : null, title: d.runs > 0 ? 'An agent run is working; a restart would end it' : '',
      onclick: async e => {
        e.currentTarget.disabled = true;
        try { paint(await POST('/api/card/restart', {})); toast('Strata is restarting with the new share', 'success', 3000); }
        catch (err) { errToast(err, 'Not restarted'); e.currentTarget.disabled = false; }
      } }, 'Restart Strata now');
    const pending = d.pending ? el('div', { class: 'card-pending' }, el('span', { html: ico('clock', 14) }),
      el('span', { class: 'grow', text: d.runs > 0 ? `Takes effect the next time Strata starts: when it unloads after being idle, or with Restart once the ${d.runs > 1 ? d.runs + ' agent runs end' : 'agent run ends'}.`
        : 'Takes effect the next time Strata starts: when it unloads after being idle, or now. A restart reloads Strata, about half a minute.' }), restart) : null;
    // The Manager's own idle unload: Studio's cleanup after a run frees the tools, and this frees Strata itself.
    const idle = d.strata_idle == null ? null : el('label', { class: 'card-idle' }, el('span', { text: 'Unload Strata after' }),
      el('select', { class: 'dlg-input', 'aria-label': 'Unload Strata after this long with nothing to do',
        onchange: async e => { try { paint(await POST('/api/card/strata_idle', { minutes: parseInt(e.target.value, 10) })); toast('Saved', 'success', 1500); } catch (err) { errToast(err, 'Not saved'); } } },
        ...[[5, '5 minutes'], [15, '15 minutes'], [30, '30 minutes'], [60, '1 hour'], [120, '2 hours'], [0, 'Never']].map(([v, t]) =>
          el('option', { value: String(v), text: t, selected: d.strata_idle === v ? '' : null }))),
      el('small', { class: 'muted', text: 'with nothing to do. When the last run ends, Studio also unloads the voice, sound and music engines and has ComfyUI free its models.' }));
    body.replaceChildren(choices, pending || el('p', { class: 'muted', text: 'In use.' }), el('p', { class: 'muted', text: now }),
      el('p', { class: 'muted', text: 'Qwen-Image and Wan video always unload Strata while they work: they need the whole card in both modes.' }), idle);
  };
  try { paint(await GET('/api/card')); } catch (err) { body.replaceChildren(el('div', { class: 'dir-note', text: err.message })); }
}
async function paneBackups(box) {
  const list = el('div', { class: 'backup-list' }, skeleton(3));
  const note = el('p', { class: 'muted' });
  const load = async () => {
    const d = await GET('/api/admin/backups');
    note.replaceChildren(d.interval_h > 0 ? `Studio backs up its data folder every ${d.interval_h} hours into ` : 'Automatic backups are off. Backups go into ', el('code', { text: d.folder }),
      '. They hold password hashes and chats, so only this server account can read them. To restore one, stop Studio and run ', el('code', { text: 'python3 studio.py --restore FILE' }), '; the current data is kept beside it.');
    list.replaceChildren(...(d.backups.length ? d.backups.map((b, i) => el('div', { class: 'backup-row' }, el('span', { html: ico('download', 14) }), el('span', { class: 'grow', text: b.name }),
      i === 0 ? el('span', { class: 'ac-badge ok', text: 'Newest' }) : null, el('small', { text: `${(b.size / 1048576).toFixed(1)} MB · ${ago(b.time)}` }))) : [el('div', { class: 'dir-note', text: 'No backups yet' })]));
  };
  const now = el('button', { class: 'btn primary', onclick: async e => {
    const b = e.currentTarget; b.disabled = true;
    try { const r = await POST('/api/admin/backups', {}); toast('Saved ' + r.name, 'success', 3000); await load(); } catch (err) { errToast(err, 'Backup failed'); }
    b.disabled = false;
  } }, 'Back up now');
  box.replaceChildren(acHead('Backups', 'Copies of Studio\'s data: accounts, chats, settings, voices.', now), el('div', { class: 'ac-card' }, note, list));
  try { await load(); } catch (err) { list.replaceChildren(el('div', { class: 'dir-note', text: err.message })); }
}
// Updates (admins): the third-party tools Studio uses, what is new, update, roll back, and the nightly auto-update.
function upSwitch(on, label, change, title = '') {
  const b = el('button', { class: 'toggle', role: 'switch', 'aria-checked': String(!!on), title }, el('span', { class: 'sw' }), el('span', { class: 'lbl', text: label }));
  b.onclick = async () => { const next = b.getAttribute('aria-checked') !== 'true'; b.disabled = true; try { await change(next); b.setAttribute('aria-checked', String(next)); } catch (e) { errToast(e, 'Not saved'); } b.disabled = false; };
  return b;
}
const UP_ICONS = { models: 'brain', docker: 'layers', engines: 'plug', programs: 'cube', system: 'terminal' };
async function paneUpdates(box) {
  let d = await GET('/api/admin/updates'), timer = null, checking = false;
  const hours = sel => { const s = el('select', { class: 'dlg-input up-hour' }); for (let h = 0; h < 24; h++) s.append(el('option', { value: String(h), text: String(h).padStart(2, '0') + ':00' })); s.value = String(sel); return s; };
  const act = async (id, action, item) => {
    if (action === 'update' && !item.auto_ok) {
      const ok = await confirmBox(`Update ${item.name}?`, [item.note, item.detail, `${item.current} → ${item.latest}`].filter(Boolean).join('\n\n'), 'Update');
      if (!ok) return;
    }
    if (action === 'rollback' && !(await confirmBox(`Roll back ${item.name}?`, 'It goes back to the version before the last update.', 'Roll back', true))) return;
    try { await POST('/api/admin/updates/apply', { id, action }); await refresh(); } catch (e) { errToast(e, action === 'rollback' ? 'Could not roll back' : 'Could not update'); }
  };
  const row = item => {
    const status = item.error ? el('span', { class: 'ac-badge bad', text: 'Check failed' }) : item.available ? el('span', { class: 'ac-badge up-new', text: item.kind === 'apt' ? 'Updates' : 'New version' }) : el('span', { class: 'ac-badge ok', text: 'Up to date' });
    const busy = d.job && !d.job.done;
    const buttons = el('div', { class: 'up-acts' });
    if (item.auto_ok) buttons.append(upSwitch(item.auto, 'Automatic', on => POST('/api/admin/updates/settings', { items: { [item.id]: on } }), 'Update this one by itself at night'));
    if (item.can_rollback) buttons.append(el('button', { class: 'btn sm', disabled: busy, onclick: () => act(item.id, 'rollback', item) }, 'Roll back'));
    if (item.available && item.kind !== 'apt') buttons.append(el('button', { class: 'btn sm primary', disabled: busy, onclick: () => act(item.id, 'update', item) }, item.kind === 'sandbox' ? 'Rebuild' : 'Update'));
    const versions = item.kind === 'apt' ? (item.current || '') : [item.current, item.available ? item.latest : null].filter(Boolean).join(' → ');
    const out = el('div', { class: 'up-row' + (item.available ? ' new' : ''), 'data-id': item.id },
      el('div', { class: 'up-main' }, el('b', { text: item.name }), status, item.auto_ok ? null : el('span', { class: 'ac-badge', text: 'Manual', title: 'Updates only when you press Update' }), el('span', { class: 'grow' }), buttons),
      el('div', { class: 'up-ver', text: versions }));
    for (const t of [item.detail, item.note]) if (t) out.append(el('div', { class: 'up-note', text: t }));
    if (item.error) out.append(el('div', { class: 'up-err', text: item.error }));
    if (item.packages?.length) out.append(el('div', { class: 'up-pkgs' }, ...item.packages.map(p => el('div', {}, el('code', { text: p.name }), ` ${p.current} → ${p.latest}`))));
    if (item.command) out.append(el('div', { class: 'up-cmd' }, el('code', { text: item.command }), el('button', { class: 'btn sm', onclick: () => copyText(item.command).then(() => toast('Copied', 'success', 1200)) }, 'Copy')));
    if (item.last) out.append(el('div', { class: 'up-last ' + (item.last.ok ? 'ok' : 'bad'), text: `${item.last.ok ? '✓' : '✗'} ${item.last.auto ? 'Automatic ' : ''}${item.last.action === 'rollback' ? 'rollback' : 'update'} ${ago(item.last.at)}: ${item.last.message}` }));
    return out;
  };
  const render = () => {
    const auto = upSwitch(d.settings.auto, 'Update the safe ones by themselves', on => POST('/api/admin/updates/settings', { auto: on }));
    const from = hours(d.settings.window[0]), to = hours(d.settings.window[1]);
    const saveWindow = () => POST('/api/admin/updates/settings', { window: [+from.value, +to.value] }).then(r => { d = r; toast('Saved', 'success', 1000); }).catch(e => errToast(e, 'Not saved'));
    from.onchange = to.onchange = saveWindow;
    const check = el('button', { class: 'btn primary', disabled: checking, onclick: async () => {
      checking = true; render();
      try { const before = d.last_check; await POST('/api/admin/updates/check', {}); for (let i = 0; i < 90 && d.last_check === before; i++) { await sleep(2000); d = await GET('/api/admin/updates'); } }
      catch (e) { errToast(e, 'Check failed'); }
      checking = false; render();
    } }, checking ? 'Checking…' : 'Check now');
    const settings = el('div', { class: 'ac-card' }, auto,
      el('div', { class: 'up-window' }, 'Between ', from, ' and ', to, ', only when no agent run or picture is working. Docker images and npm tool servers update this way; each is tested afterwards and rolled back if the test fails. The rest waits for you.'),
      el('div', { class: 'muted', text: (d.last_check ? `Last checked ${ago(d.last_check)}` : 'Not checked yet') + ` · ${d.available} with something new` }));
    const job = d.job ? el('div', { class: 'ac-card up-job ' + (!d.job.done ? 'working' : d.job.ok ? 'ok' : 'bad') },
      el('b', { text: `${!d.job.done ? (d.job.action === 'rollback' ? 'Rolling back' : 'Updating') : d.job.ok ? 'Done' : 'Failed'}: ${d.job.name}` }),
      d.job.message ? el('div', { text: d.job.message }) : null,
      d.job.log?.length ? el('pre', { class: 'up-log', text: d.job.log.join('\n') }) : null) : null;
    const groups = d.groups.map(([g, label]) => {
      const items = d.items.filter(i => i.group === g && !i.absent);
      return items.length ? el('div', { class: 'ac-card up-group' }, el('div', { class: 'dlg-sub' }, el('span', { html: ico(UP_ICONS[g] || 'gear', 14) }), ' ' + label), ...items.map(row)) : null;
    });
    const absent = d.items.filter(i => i.absent);
    const away = absent.length ? el('details', { class: 'ac-card up-hist up-absent' }, el('summary', { text: `Not installed here (${absent.length})` }),
      el('div', { class: 'up-note', text: 'Nothing to update until they are installed: Tools & setup installs them.' }),
      ...absent.map(i => el('div', { class: 'up-note', text: i.name }))) : null;
    const hist = d.history.length ? el('details', { class: 'ac-card up-hist' }, el('summary', { text: `History (${d.history.length})` }),
      ...d.history.map(h => el('div', { class: 'up-last ' + (h.ok ? 'ok' : 'bad'), text: `${new Date(h.at * 1000).toLocaleString()} · ${h.name} · ${h.auto ? 'automatic ' : ''}${h.action}: ${h.message}` }))) : null;
    box.replaceChildren(...[acHead('Updates', 'The tools Studio uses: what is installed, what is new, and updating them.', check), settings, job, ...groups, away, hist].filter(Boolean));
    clearTimeout(timer);
    if (d.job && !d.job.done && box.isConnected) timer = setTimeout(refresh, 2000);
    updatesBadge(d.available);
  };
  const refresh = async () => { if (!box.isConnected) return; try { d = await GET('/api/admin/updates'); } catch { return; } render(); };
  render();
}
/* Tools & setup: what this machine is, which of Studio's tools are installed, and installing the chosen ones in the
   background (installer.py). Root-only steps come as one script to copy or download and run with sudo. */
const SETUP_ICONS = { models: 'brain', services: 'layers', tools: 'plug', engines: 'gauge' };
const SETUP_BADGE = { installed: ['ok', 'Installed'], ready: ['up-new', 'Can install'], needs: ['bad', 'Needs something first'], later: ['', 'Later'], no: ['', 'Won’t fit here'] };
const setupSize = mb => !mb ? '' : mb >= 1024 ? `${(mb / 1024).toFixed(mb >= 10240 ? 0 : 1)} GB` : `${mb} MB`;
function setupProgress(p) {
  const pct = p.total ? Math.min(100, Math.round(p.done / p.total * 100)) : null, size = b => setupSize(Math.round(b / 1048576)) || '0 MB';
  return el('div', { class: 'su-progress' },
    el('div', { class: 'su-ptext', text: `${p.name}: ${size(p.done)}${p.total ? ` of ${size(p.total)} · ${pct}%` : ''}` }),
    el('div', { class: 'su-pbar' }, el('span', { style: `width:${pct ?? 0}%` })));
}
async function paneSetup(box) {
  let d = await GET('/api/admin/setup'), timer = null, chosen = new Set(), script = '', checking = false;
  const picks = {};       // the model chosen for each local-model item
  const addable = i => (i.choices || []).filter(c => c.fits && !c.installed);
  const pick = i => (i.choices || []).find(c => c.id === picks[i.id]) || addable(i)[0] || null;
  const installable = i => i.status === 'ready' || i.status === 'needs' || (i.status === 'installed' && addable(i).length > 0);
  const loadScript = async () => {
    const ids = chosen.size ? [...chosen] : d.items.filter(i => installable(i) && !i.extra).map(i => i.id);
    try { script = (await POST('/api/admin/setup/script', { ids })).script; } catch { script = ''; }
  };
  const install = async ids => {
    const items = d.items.filter(i => ids.includes(i.id)), waiting = items.filter(i => i.status === 'needs');
    const ready = items.filter(i => i.status === 'ready' || (i.status === 'installed' && pick(i) && !pick(i).installed));
    const options = Object.fromEntries(ready.filter(i => i.choices).map(i => [i.id, pick(i)?.id]).filter(([, c]) => c));
    if (!ready.length) { toast(waiting.length ? 'Run the setup script first, then press Check again.' : 'Nothing to install.', 'warn', 4000); return; }
    const size = i => i.choices && pick(i) ? Math.round(pick(i).download_gb * 1024) : null;
    const dl = ready.reduce((a, i) => a + (size(i) ?? i.download_mb), 0), disk = ready.reduce((a, i) => a + (size(i) != null ? size(i) + 12 * 1024 : i.disk_mb), 0);
    const text = [`${ready.map(i => i.choices && pick(i) ? `${i.name}: ${pick(i).label}` : i.name).join(', ')}.`, dl ? `About ${setupSize(dl)} to download and ${setupSize(disk)} on disk (${d.machine.disk_free_gb} GB free).` : '',
      waiting.length ? `Waiting for the setup script: ${waiting.map(i => i.name).join(', ')}.` : '', 'It runs in the background; you can close this window.'].filter(Boolean).join('\n\n');
    if (!(await confirmBox(`Install ${ready.length === 1 ? ready[0].name : ready.length + ' tools'}?`, text, 'Install'))) return;
    try { await POST('/api/admin/setup/install', { ids: ready.map(i => i.id), options }); chosen.clear(); await refresh(); } catch (e) { errToast(e, 'Could not install'); }
  };
  const machineCard = () => {
    const m = d.machine, gpu = m.gpu, cards = m.gpus || [];
    const rows = [['System', `${m.system} · ${m.arch}`], ['Processor and memory', `${m.cpus} threads · ${m.ram_gb} GB RAM` + (m.swap_gb ? ` · ${m.swap_gb} GB swap` : '')],
      ['Free disk', `${m.disk_free_gb} GB in ${m.disk_path}`],
      ['Graphics', cards.length ? cards.map(c => `${c.name || c.vendor.toUpperCase() + ' card'}${c.vram_gb ? ` · ${c.vram_gb} GB` : ''}${c.gfx ? ` · ${c.gfx}` : ''}${c.rocm ? ' · ROCm ✓' : ''}`).join('; ') : 'none found'],
      ['Docker', m.docker_ok ? 'running' : m.programs?.docker ? (m.in_docker_group ? 'not running' : 'installed, but you are not in the docker group') : 'not installed'],
      ['Node.js', m.node_major ? `v${m.node_major}` : 'not installed'], ['Python', m.python || 'not found']];
    const card = el('div', { class: 'ac-card su-machine' }, el('div', { class: 'dlg-sub' }, el('span', { html: ico('pc', 14) }), ' This machine'),
      el('dl', { class: 'su-facts' }, ...rows.flatMap(([k, v]) => [el('dt', { text: k }), el('dd', { text: v })])));
    if (!gpu) card.append(el('div', { class: 'up-note', text: 'No AMD card that ROCm supports was found, so the engines on the graphics card (pictures, voices, music) will not fit here.' }));
    else if (!m.gpu_groups) card.append(el('div', { class: 'up-note', text: 'Studio is not yet in the render and video groups, which may use the card: the setup script adds it, then restart the computer.' }));
    return card;
  };
  const row = i => {
    const [cls, label] = SETUP_BADGE[i.status] || ['', i.status];
    const box_ = el('input', { type: 'checkbox', class: 'su-pick', 'aria-label': 'Choose ' + i.name, checked: chosen.has(i.id), disabled: !installable(i) || (d.job && !d.job.done),
      onchange: async e => { e.target.checked ? chosen.add(i.id) : chosen.delete(i.id); await loadScript(); render(); } });
    const sizes = [i.download_mb ? `${setupSize(i.download_mb)} download` : '', i.disk_mb ? `${setupSize(i.disk_mb)} on disk` : ''].filter(Boolean).join(' · ');
    const out = el('div', { class: 'up-row su-row su-' + i.status, 'data-id': i.id },
      el('label', { class: 'up-main' }, box_, el('b', { text: i.name }), el('span', { class: 'ac-badge ' + cls, text: label }),
        i.extra && i.status !== 'installed' ? el('span', { class: 'ac-badge su-extra', title: 'Not part of “Install everything”: choose it to install it', text: 'Optional' }) : null,
        i.engine && i.gpu_gb && i.status !== 'installed' ? el('span', { class: 'su-gpu', text: `${i.gpu_gb} GB card` }) : null, el('span', { class: 'grow' }),
        installable(i) && i.status !== 'needs' && !(d.job && !d.job.done) ? el('button', { class: 'btn sm primary', onclick: e => { e.preventDefault(); install([i.id]); } },
          i.status === 'installed' ? 'Add this model' : 'Install') : null),
      el('div', { class: 'up-note', text: i.what }));
    if (i.choices) {
      const now = pick(i);
      const sel = el('select', { class: 'su-choice', 'aria-label': 'Model for ' + i.name, disabled: d.job && !d.job.done,
        onchange: e => { picks[i.id] = e.target.value; render(); } },
        ...i.choices.map(c => el('option', { value: c.id, disabled: !c.fits || c.installed, selected: now && c.id === now.id,
          text: `${c.label} · ${c.download_gb} GB · ~${c.ram_gb} GB RAM${c.installed ? ' · installed' : !c.fits ? ' · does not fit' : ''}` })));
      out.append(el('div', { class: 'su-choices' }, el('span', { class: 'up-note', text: 'Model:' }), sel));
      if (now) out.append(el('div', { class: 'up-note', text: now.about }));
      i.choices.filter(c => !c.fits && !c.installed).forEach(c => out.append(el('div', { class: 'su-why', text: `${c.label}: ${c.why}` })));
    }
    if (i.why) out.append(el('div', { class: i.status === 'needs' ? 'up-err' : 'su-why', text: i.why }));
    if (sizes && i.status !== 'installed' && !i.choices) out.append(el('div', { class: 'up-note', text: sizes }));
    return out;
  };
  const render = () => {
    const busy = d.job && !d.job.done;
    const open = d.items.filter(i => installable(i) && !i.extra);
    const check = el('button', { class: 'btn', disabled: checking || busy, onclick: async () => {
      checking = true; render();
      try { d = await POST('/api/admin/setup/check', {}); await loadScript(); } catch (e) { errToast(e, 'Check failed'); }
      checking = false; render();
    } }, checking ? 'Checking…' : 'Check again');
    const all = el('button', { class: 'btn primary su-all', disabled: busy || !open.length, onclick: () => install(open.map(i => i.id)) },
      open.length ? 'Install everything this machine can run' : 'Everything that fits is installed');
    const picked = el('button', { class: 'btn su-picked', disabled: busy || !chosen.size, onclick: () => install([...chosen]) }, `Install chosen (${chosen.size})`);
    const job = d.job ? el('div', { class: 'ac-card up-job ' + (busy ? 'working' : d.job.ok ? 'ok' : 'bad') },
      el('div', { class: 'up-main' }, el('b', { text: busy ? `Installing ${d.items.find(i => i.id === d.job.current)?.name || '…'} (${d.job.done_ids.length + Object.keys(d.job.failed).length + 1} of ${d.job.ids.length})` : `Done: ${d.job.message}` }),
        el('span', { class: 'grow' }), busy ? el('button', { class: 'btn sm', onclick: () => POST('/api/admin/setup/stop', {}).then(refresh) }, 'Stop') : null),
      ...Object.entries(d.job.failed).map(([id, t]) => el('div', { class: 'up-err', text: `✗ ${d.items.find(i => i.id === id)?.name || id}: ${t}` })),
      ...Object.entries(d.job.skipped).map(([id, t]) => el('div', { class: 'up-note', text: `– ${d.items.find(i => i.id === id)?.name || id}: ${t}` })),
      busy && d.job.progress ? setupProgress(d.job.progress) : null,
      d.job.log?.length ? el('pre', { class: 'up-log', text: d.job.log.join('\n') }) : null) : null;
    const sudo = script ? el('div', { class: 'ac-card su-script' },
      el('div', { class: 'dlg-sub' }, el('span', { html: ico('terminal', 14) }), ' First, once, as root'),
      el('div', { class: 'up-note', text: `Studio does not use sudo. This script installs the system packages ${chosen.size ? 'the chosen tools' : 'these tools'} need and the settings only root can change. Download it (or copy it), run it in a terminal with sudo bash studio-setup.sh, then press Check again.` }),
      el('pre', { class: 'up-log su-code', text: script }),
      el('div', { class: 'up-acts' }, el('button', { class: 'btn sm', onclick: () => copyText(script).then(() => toast('Copied', 'success', 1200)) }, 'Copy'),
        el('a', { class: 'btn sm', download: 'studio-setup.sh', href: 'data:text/x-sh;charset=utf-8,' + encodeURIComponent(script) }, 'Download studio-setup.sh'))) : null;
    const groups = d.groups.map(([g, label]) => {
      const items = d.items.filter(i => i.group === g);
      return items.length ? el('div', { class: 'ac-card up-group' }, el('div', { class: 'dlg-sub' }, el('span', { html: ico(SETUP_ICONS[g] || 'gear', 14) }), ' ' + label), ...items.map(row)) : null;
    });
    const notes = el('div', { class: 'ac-card' }, el('div', { class: 'dlg-sub', text: 'Not installed here' }), ...d.not_here.map(t => el('div', { class: 'up-note', text: t })));
    const hist = d.history.length ? el('details', { class: 'ac-card up-hist' }, el('summary', { text: `History (${d.history.length})` }),
      ...d.history.map(h => el('div', { class: 'up-last ' + (h.ok ? 'ok' : 'bad'), text: `${new Date(h.at * 1000).toLocaleString()} · ${h.name}: ${h.message}` }))) : null;
    box.replaceChildren(...[acHead('Tools & setup', 'Install the tools Studio uses on this machine: all that fit with one button, or the ones you choose.', check),
      machineCard(), el('div', { class: 'su-bar' }, all, picked), job, sudo, ...groups, notes, hist].filter(Boolean));
    clearTimeout(timer);
    if (busy && box.isConnected) timer = setTimeout(refresh, 2000);
  };
  const refresh = async () => {
    if (!box.isConnected) return;
    const was = d.job && !d.job.done;
    try { d = await GET('/api/admin/setup'); } catch { return; }
    if (was && !(d.job && !d.job.done)) await loadScript();
    render();
  };
  await loadScript();
  render();
}
function updatesBadge(n) {
  $('#btn-account')?.classList.toggle('has-updates', n > 0);
  document.querySelectorAll('.ac-nav button[data-section="updates"] .up-count').forEach(c => c.remove());
  if (n > 0) document.querySelector('.ac-nav button[data-section="updates"]')?.append(el('span', { class: 'up-count', text: String(n) }));
}
async function updatesNotice() {
  if (!isAdmin()) return;
  if (S.config?.updates === false) return;      // off on this Studio (a new install): nothing to ask
  try { const d = await GET('/api/admin/updates'); updatesBadge(d.available); } catch { /* updates are off here */ }
}
$('#btn-theme').onclick = e => {
  const r = e.currentTarget.getBoundingClientRect();
  showContextMenu(r.left, r.bottom + 4, Object.entries(THEMES).map(([name, t]) => ({ label: (name === currentTheme() ? '✓ ' : '') + t.label, run: () => applyTheme(name) })));
};
$('#btn-about').onclick = () => {
  dialog({
    title: 'About Eidoloom Studio',
    body: el('div', {},
      el('div', { class: 'about-brand brand-lockup', 'aria-label': 'Eidoloom Studio' },
        el('img', { class: 'brand-mark', src: 'assets/eidoloom-mark-original.png', alt: '' }),
        el('span', { class: 'brand-name' }, 'Eidoloom ', el('small', { text: 'Studio' }))),
      el('p', { text: 'Eidoloom Studio is an AI agent harness with a code editor around it, in your browser. The harness is what turns a model into an agent: the loop that plans and calls tools, the tools themselves, approvals and undo, and a check after every edit. Around it are a file explorer, an editor, terminals and the agent\'s chat.' }),
      el('p', { text: 'The name joins eidolon (εἴδωλον, an image or likeness) and loom: a place where code, pictures, voices and video are woven together.' }),
      el('p', { text: 'The model is yours and interchangeable: Ollama, llama.cpp, vLLM, LM Studio or Strata, on your own machine. The agent reads and edits your files and runs commands, and asks for your approval first unless you turn on the auto options. Each folder can run in its own sandbox.' }),
      el('div', { class: 'dlg-sub', text: 'Made by' }),
      el('p', { text: 'John Chatzopoulos' }),
      el('p', { class: 'muted', text: 'November 2026' }),
      el('div', { class: 'dlg-sub', text: 'Licence' }),
      el('p', { class: 'muted about-licence' }, 'Copyright © 2026 John Chatzopoulos. Eidoloom Studio is free software under the ',
        el('a', { href: 'https://www.gnu.org/licenses/agpl-3.0.html', target: '_blank', rel: 'noopener', text: 'GNU Affero General Public License, version 3' }),
        ', and comes with no warranty. If you run a changed version for other people over a network, offer them its source code. A commercial licence is available from the copyright holder.')),
    buttons: [{ label: 'Open the guide', value: 'guide' }, { label: 'Close', kind: 'primary' }],
  }).then(v => { if (v === 'guide') openGuide(); });
};
/* The guide: web/guide.html (English and Greek), shown in a window inside Studio with live search, or on its own page. */
const GUIDE_ICONS = { start: 'folder-open', talk: 'sparkles', safe: 'undo', long: 'flag', auto: 'clock', yours: 'skill', see: 'eye', media: 'library', keys: 'list', people: 'users', pc: 'pc', phone: 'layers', help: 'help' };
let guideDoc = null;
async function openGuide(query = '') {
  if (overlayStack.some(o => o.ov.dataset.guide)) return;
  if (!guideDoc) {
    try { guideDoc = new DOMParser().parseFromString(await (await fetch('/web/guide.html', { cache: 'no-cache' })).text(), 'text/html'); }
    catch { window.open('/web/guide.html', '_blank', 'noopener'); return; }
  }
  let lang = lsGet('studio.guideLang') || 'en';                // English unless the person picked Greek
  const search = el('input', { type: 'search', class: 'gd-search', placeholder: 'Search the guide', value: query, 'aria-label': 'Search the guide', spellcheck: 'false' });
  const toc = el('nav', { class: 'gd-toc', 'aria-label': 'Contents' }), body = el('div', { class: 'gd-body' });
  const count = el('div', { class: 'gd-count' });
  const langBtn = el('button', { class: 'btn sm', onclick: () => { lang = lang === 'en' ? 'el' : 'en'; lsSet('studio.guideLang', lang); load(); } });
  const close = el('button', { class: 'icon-btn sm', title: 'Close (Esc)', 'aria-label': 'Close the guide', html: ico('x', 16), onclick: () => closeOverlay(entry) });
  const box = el('div', { class: 'dialog guide-modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Guide' },
    el('div', { class: 'gd-head' }, el('span', { class: 'gd-logo', html: ico('help', 18) }), el('b', { class: 'gd-title' }), search, langBtn,
      el('a', { class: 'link-btn card-link gd-tab', href: '/web/guide.html', target: '_blank', rel: 'noopener', text: 'New tab' }), close),
    el('div', { class: 'gd-main' }, toc, el('div', { class: 'gd-scroll' }, count, body)));
  const entry = openOverlay(box, { center: true }); entry.ov.dataset.guide = '1'; entry.ov.classList.add('guide-ov');
  let sections = [];
  function load() {
    const src = guideDoc.querySelector(`section[data-lang="${lang}"]`);
    box.querySelector('.gd-title').textContent = lang === 'el' ? 'Οδηγός' : 'Guide';
    search.placeholder = lang === 'el' ? 'Αναζήτηση στον οδηγό' : 'Search the guide';
    langBtn.textContent = lang === 'el' ? 'English' : 'Ελληνικά';
    // Each h2 and what follows it, up to the next h2, is one section with its own icon.
    sections = [];
    for (const n of src.children) {
      if (n.tagName === 'H2') sections.push({ id: n.id.replace(/^el-/, ''), title: n.textContent, nodes: [] });
      else if (sections.length) sections[sections.length - 1].nodes.push(n);
    }
    body.replaceChildren(...sections.map(sec => {
      sec.el = el('section', { class: 'gd-sec', id: 'gd-' + sec.id }, el('h2', {}, el('span', { class: 'gd-ico', html: ico(GUIDE_ICONS[sec.id] || 'info', 16) }), sec.title),
        ...sec.nodes.map(n => document.importNode(n, true)));
      return sec.el;
    }));
    toc.replaceChildren(...sections.map(sec => { sec.link = el('button', { class: 'gd-link', onclick: () => { sec.el.scrollIntoView({ block: 'start', behavior: 'smooth' }); } }, el('span', { html: ico(GUIDE_ICONS[sec.id] || 'info', 14) }), el('span', { text: sec.title }), el('small', { class: 'gd-hits' })); return sec.link; }));
    body.querySelectorAll('a[href^="#"]').forEach(a => a.removeAttribute('href'));
    filter();
  }
  // Live search: only the sections, rows, items and paragraphs that hold every word stay, with the words marked.
  function filter() {
    const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    body.querySelectorAll('mark.gd-mark').forEach(m => m.replaceWith(document.createTextNode(m.textContent)));
    body.normalize();
    let total = 0;
    for (const sec of sections) {
      const blocks = [...sec.el.querySelectorAll('li, p, tr, .tip, h3')];
      let hits = 0;
      for (const b of blocks) {
        const text = b.textContent.toLowerCase(), titleHit = words.every(w => sec.title.toLowerCase().includes(w));
        const hit = !words.length || titleHit || words.every(w => text.includes(w));
        b.classList.toggle('gd-hide', !hit && b.tagName !== 'H3' && !b.querySelector('li, tr'));
        if (hit && words.length && !titleHit && b.tagName !== 'H3') hits++;
      }
      const show = !words.length || hits > 0 || words.every(w => sec.title.toLowerCase().includes(w));
      sec.el.classList.toggle('gd-hide', !show);
      sec.el.querySelectorAll('ul, ol, table').forEach(list => list.classList.toggle('gd-hide', !!words.length && ![...list.querySelectorAll('li, tr')].some(x => !x.classList.contains('gd-hide')) && !words.every(w => sec.title.toLowerCase().includes(w))));
      sec.link.classList.toggle('dim', !show);
      sec.link.querySelector('.gd-hits').textContent = words.length && hits ? String(hits) : '';
      total += show ? 1 : 0;
      if (words.length && show) markWords(sec.el, words);
    }
    count.textContent = !words.length ? '' : total ? (lang === 'el' ? `${total} ενότητες ταιριάζουν` : `${total} section${total === 1 ? '' : 's'} match`) : (lang === 'el' ? 'Τίποτα δεν ταιριάζει.' : 'Nothing matches.');
    count.classList.toggle('hidden', !words.length);
  }
  function markWords(root, words) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), hits = [];
    while (walker.nextNode()) if (!walker.currentNode.parentElement.closest('.gd-hide, svg, h2 .gd-ico')) hits.push(walker.currentNode);
    const re = new RegExp('(' + words.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')', 'gi');
    for (const node of hits) {
      if (!re.test(node.data)) continue;
      re.lastIndex = 0;
      const frag = document.createDocumentFragment();
      node.data.split(re).forEach((part, i) => frag.append(i % 2 ? el('mark', { class: 'gd-mark', text: part }) : document.createTextNode(part)));
      node.replaceWith(frag);
    }
  }
  search.addEventListener('input', () => { filter(); body.parentElement.scrollTop = 0; });
  search.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); body.querySelector('.gd-sec:not(.gd-hide)')?.scrollIntoView({ block: 'start' }); } });
  load();
  setTimeout(() => search.focus(), 30);
}
$('#btn-guide').onclick = () => openGuide();
/* ---- Account & settings: one window with a side menu. Everyone: profile and security, their computers, preferences.
   Admins also: users, tool servers, backups. */
function acHead(title, sub, ...actions) {
  return el('div', { class: 'ac-head' }, el('div', { class: 'grow' }, el('h3', { text: title }), sub ? el('small', { class: 'muted', text: sub }) : null), ...actions.filter(Boolean));
}
function avatar(name, size = 34) {
  const text = (name || '?').trim(), initials = text.split(/\s+/).map(w => w[0]).join('').slice(0, 2).toUpperCase();
  let h = 0; for (const c of text) h = (h * 31 + c.codePointAt(0)) % 360;
  return el('span', { class: 'ac-avatar', style: `width:${size}px;height:${size}px;font-size:${Math.round(size * 0.4)}px;background:hsl(${h} 45% 42%)`, text: initials, 'aria-hidden': 'true' });
}
const dateText = t => t ? new Date(t * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
const ACCOUNT_SECTIONS = [
  ['profile', 'Profile & security', 'user', () => true], ['computers', 'My computers', 'pc', () => true], ['preferences', 'Preferences', 'gear', () => true], ['models', 'Models', 'brain', () => true], ['greek', 'Greek', 'book', () => true], ['codex', 'Codex', 'plug', () => isAdmin() && !!S.config?.codex],
  ['users', 'Users', 'users', isAdmin], ['servers', 'Tool servers', 'plug', isAdmin], ['card', 'Graphics card', 'gauge', mayCard], ['backups', 'Backups', 'download', isAdmin], ['setup', 'Tools & setup', 'box', isAdmin], ['updates', 'Updates', 'refresh', () => isAdmin() && S.config?.updates !== false]];
function openAccount(section = 'profile') {
  const nav = el('nav', { class: 'ac-nav', 'aria-label': 'Account sections' }), pane = el('div', { class: 'ac-pane' });
  const close = el('button', { class: 'btn sm', 'aria-label': 'Close' }, 'Close');
  const user = S.config.user || {};
  const dlg = el('div', { class: 'dialog acct', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Account and settings' },
    el('div', { class: 'dlg-head ac-top' }, avatar(user.display_name || user.username, 26), el('span', { class: 'grow', text: 'Account & settings' }), close),
    el('div', { class: 'ac-body' }, nav, pane));
  const entry = openOverlay(dlg, { center: true });
  close.onclick = () => closeOverlay(entry);
  const panes = { profile: paneProfile, computers: box => paneComputers(box, entry), preferences: panePreferences, models: paneModels, greek: paneGreek, codex: paneCodex, users: paneUsers, servers: paneServers, card: paneCard, backups: paneBackups, setup: paneSetup, updates: paneUpdates };
  const show = key => {
    lsSet('studio.accountSection', key);
    nav.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.section === key));
    // a fresh box for each visit: a section that answers late (a slow load, a refresh) draws into its own old box,
    // which is no longer on screen, instead of over the section the person moved to
    const box = el('div', { class: 'ac-pane-box', style: 'display: contents' }, el('div', { class: 'dir-note', text: 'Loading…' }));
    pane.replaceChildren(box); pane.scrollTop = 0;
    Promise.resolve(panes[key](box)).catch(e => box.replaceChildren(el('div', { class: 'dir-note', text: e.message })));
  };
  let admin = false;
  for (const [key, label, icon, visible] of ACCOUNT_SECTIONS) {
    if (!visible()) continue;
    if (visible === isAdmin && !admin) { admin = true; nav.append(el('div', { class: 'ac-nav-sub', text: 'Administration' })); }
    nav.append(el('button', { 'data-section': key, onclick: () => show(key) }, el('span', { html: ico(icon, 15) }), el('span', { text: label })));
  }
  show(ACCOUNT_SECTIONS.some(([k, , , v]) => k === section && v()) ? section : 'profile');
  updatesNotice();
  return entry;
}
$('#btn-account').onclick = () => openAccount(lsGet('studio.accountSection') || 'profile');
const openUserAdmin = () => openAccount('users'), openMcpServers = () => openAccount('servers'), openBackups = () => openAccount('backups');
async function paneProfile(box) {
  const d = await GET('/api/account'), u = d.user;
  const name = el('input', { class: 'dlg-input', type: 'text', maxlength: '60', value: u.display_name || '', placeholder: 'How others see you, e.g. Maria P.' });
  const saveName = el('button', { class: 'btn sm', onclick: async () => {
    try { const r = await POST('/api/account/profile', { display_name: name.value }); S.config.user = { ...S.config.user, display_name: r.user.display_name }; toast('Name saved', 'success', 1500); paneProfile(box); }
    catch (e) { errToast(e, 'Could not save the name'); }
  } }, 'Save');
  const current = el('input', { class: 'dlg-input', type: 'password', autocomplete: 'current-password' });
  const next = el('input', { class: 'dlg-input', type: 'password', autocomplete: 'new-password' });
  const again = el('input', { class: 'dlg-input', type: 'password', autocomplete: 'new-password' });
  const strength = el('small', { class: 'muted' });
  next.oninput = () => { const n = next.value.length; strength.textContent = !n ? '' : n < 8 ? `${8 - n} more character${8 - n === 1 ? '' : 's'} needed` : n < 12 ? 'Long enough; longer is safer' : 'Good length'; };
  const field = (label, input, extra) => el('label', { class: 'dlg-field' }, el('span', { text: label }), input, extra || null);
  const change = el('button', { class: 'btn primary', onclick: async () => {
    if (next.value.length < 8) { toast('The new password needs at least 8 characters', 'warn'); return; }
    if (next.value !== again.value) { toast('The two new passwords do not match', 'warn'); return; }
    try { await POST('/api/account/password', { current: current.value, password: next.value }); current.value = next.value = again.value = ''; strength.textContent = ''; toast('Password changed. Your other browsers were signed out.', 'success'); paneProfile(box); }
    catch (e) { errToast(e, 'Could not change the password'); }
  } }, 'Change password');
  const others = d.sessions.filter(x => !x.current);
  const sessions = el('div', { class: 'ac-list' }, d.sessions.map(x => el('div', { class: 'ac-row' }, el('span', { html: ico(/Android|iPhone|iPad/.test(x.device) ? 'layers' : 'pc', 15) }),
    el('div', { class: 'grow' }, el('b', { text: x.device }), el('small', { class: 'muted', text: ` · since ${dateText(x.created)}${x.remember ? ' · remembered' : ''}` })),
    x.current ? el('span', { class: 'ac-badge ok', text: 'This browser' }) : el('button', { class: 'btn sm', onclick: async () => {
      try { await POST('/api/account/sessions/revoke', { id: x.id }); toast('Signed out', 'success', 1500); paneProfile(box); } catch (e) { errToast(e, 'Could not sign it out'); }
    } }, 'Sign out'))));
  box.replaceChildren(acHead('Profile & security', 'Your name, password and signed-in browsers.'),
    el('div', { class: 'ac-card ac-me' }, avatar(u.display_name || u.username, 56),
      el('div', { class: 'grow' }, el('div', { class: 'ac-me-name', text: u.display_name || u.username }),
        el('div', { class: 'ac-badges' }, el('span', { class: 'ac-badge', text: '@' + u.username }), el('span', { class: 'ac-badge ' + (u.role === 'admin' ? 'admin' : ''), text: u.role === 'admin' ? 'Admin' : 'User' }),
          d.pc_allowed && u.role !== 'admin' ? el('span', { class: 'ac-badge ok', text: 'Own computer allowed' }) : null),
        el('small', { class: 'muted', text: `Member since ${dateText(u.created)} · last sign-in ${dateText(u.last_login)}` }))),
    el('div', { class: 'ac-card' }, el('div', { class: 'dlg-sub', text: 'Display name' }), el('div', { class: 'ac-inline' }, name, saveName),
      el('small', { class: 'muted', text: 'Shown in the waiting room, its chat and chats you pass on. Your username stays as it is.' })),
    el('div', { class: 'ac-card' }, el('div', { class: 'dlg-sub', text: 'Password' }),
      el('div', { class: 'ac-grid3' }, field('Current password', current), field('New password', next, strength), field('New password again', again)), el('div', { class: 'account-actions' }, change)),
    el('div', { class: 'ac-card' }, el('div', { class: 'dlg-sub ac-sub-row' }, el('span', { class: 'grow', text: `Signed-in browsers (${d.sessions.length})` }),
      others.length ? el('button', { class: 'btn sm', onclick: async () => { try { await POST('/api/account/logout-others', {}); toast('Other browsers were signed out.', 'success'); paneProfile(box); } catch (e) { errToast(e); } } }, 'Sign out all others') : null),
      sessions));
}
async function paneComputers(box, entry) {
  const list = el('div');
  box.replaceChildren(acHead('My computers', 'Folders on your own Windows or Linux computer, worked on here while their commands run there.'), el('div', { class: 'ac-card' }, list));
  await computersList(list, async path => { const r = await POST('/api/workspace/open', { path }); closeOverlay(entry); await setWorkspace(r.workspace); }, h => { closeOverlay(entry); connectComputer(h); });
  if (!list.childElementCount) list.replaceChildren(el('div', { class: 'dir-note', text: 'An admin has not allowed you to connect your own computer. Ask one to turn on "Own computer" for your account.' }));
}
function panePreferences(box) {
  const themes = el('div', { class: 'ac-themes' }, Object.entries(THEMES).map(([name, t]) => el('button', { class: 'ac-theme' + (name === currentTheme() ? ' on' : ''), 'data-theme-name': name, onclick: () => { applyTheme(name); panePreferences(box); } },
    el('span', { class: 'ac-swatch ac-swatch-' + name }), el('span', { text: t.label }))));
  const pick = (key, fallback, options, note, onChange) => {
    const sel = el('select', { class: 'dlg-input' }, options.map(([v, l]) => el('option', { value: v, text: l }))); sel.value = lsGet(key) || fallback;
    sel.onchange = () => { lsSet(key, sel.value); if (sel.value !== 'off' && 'Notification' in window && Notification.permission === 'default') Notification.requestPermission(); onChange && onChange(); };
    return el('label', { class: 'dlg-field' }, el('span', { text: note }), sel);
  };
  const alerts = [['off', 'Off'], ['notify', 'A desktop notification'], ['sound', 'A notification and a sound']];
  const blocked = 'Notification' in window && Notification.permission === 'denied';
  box.replaceChildren(acHead('Preferences', 'Saved in this browser.'),
    el('div', { class: 'ac-card' }, el('div', { class: 'dlg-sub', text: 'Theme' }), themes),
    el('div', { class: 'ac-card' }, el('div', { class: 'dlg-sub', text: 'Notifications' }),
      el('div', { class: 'ac-grid2' }, pick('studio.notify', 'off', alerts, 'When a run ends or waits for you, while you are elsewhere'), pick('studio.roomNotify', 'notify', alerts, 'Waiting room: your turn, private messages, chats passed to you')),
      blocked ? el('small', { class: 'warn-text', text: 'This browser blocks notifications for Studio; allow them in the site settings.' }) : null),
    el('div', { class: 'ac-card' }, el('div', { class: 'dlg-sub', text: 'Per chat' }),
      el('small', { class: 'muted', text: 'Reasoning effort and dictation are set for each chat, from the gear above the chat.' })));
}
/* Tool servers (MCP): programs or URLs whose tools the agent can use. Saved as a whole list; secrets come back masked and stay as stored. */
const SECRET_KEEP = '••••••';   // studio.py SECRET_MASK: sent back, it keeps the stored value
const MCP_FIELDS = ['name', 'transport', 'enabled', 'approval', 'users', 'description', 'command', 'args', 'env', 'url', 'headers', 'timeout_s', 'app_command', 'app_port', 'app_window'];
function splitCommandLine(line) {
  const out = []; let cur = '', quote = null, any = false;
  for (const ch of line) {
    if (quote) { if (ch === quote) quote = null; else cur += ch; }
    else if (ch === '"' || ch === "'") { quote = ch; any = true; }
    else if (/\s/.test(ch)) { if (cur || any) out.push(cur); cur = ''; any = false; }
    else cur += ch;
  }
  if (cur || any) out.push(cur);
  return out;
}
function parsePairs(text, sep) {
  const out = {};
  for (const line of text.split('\n')) { const i = line.indexOf(sep); if (i > 0) out[line.slice(0, i).trim()] = line.slice(i + 1).trim(); }
  return out;
}
function mcpFromJson(text) {
  const d = JSON.parse(text);
  const map = d.mcpServers || d.servers || (d.command || d.url ? { [d.name || 'server']: d } : d);
  return Object.entries(map).map(([name, s]) => ({ name: (s.name || name).replace(/[^A-Za-z0-9_-]/g, '-').slice(0, 32), transport: s.url ? 'http' : 'stdio',
    command: s.command, args: s.args || [], env: s.env || {}, url: s.url, headers: s.headers || {}, enabled: true, approval: 'always', users: false, description: s.description || '', timeout_s: s.timeout_s }));
}
async function paneServers(box) {
  let servers = [], timer = null, filter = '';
  const stop = () => clearTimeout(timer);
  const save = async (list, done) => {
    try { const r = await POST('/api/admin/mcp', { servers: list.map(s => Object.fromEntries(MCP_FIELDS.filter(k => s[k] !== undefined).map(k => [k, s[k]]))) }); servers = r.servers; render(); done && done(); setTimeout(load, 1000); }
    catch (e) { errToast(e, 'Could not save the tool servers'); }
  };
  const change = (s, fields) => save(servers.map(x => x === s ? { ...x, ...fields } : x));
  const STATE = { ready: 'Running', starting: 'Starting…', error: 'Failed', off: 'Off', stopped: 'Stopped' };
  const open = new Set();       // cards opened, kept across redraws
  function row(s) {
    const a = s.app, program = a ? a.command.split(/[\\/]/).pop().split(' ')[0] : '';
    const state = s.state === 'ready' && a && !a.running ? 'wait' : s.state;
    const stateText = { ready: 'Running', starting: 'Starting…', error: 'Failed', off: 'Off', stopped: 'Stopped', wait: 'Running' }[state] || s.state;
    const chips = [
      s.tools.length ? `${s.tools.length} tool${s.tools.length === 1 ? '' : 's'}` : null,
      s.approval === 'with_commands' ? 'Follows auto-run' : 'Asks every time',
      s.users ? 'Users can use it' : 'Admins only',
      a ? `Needs ${program}: ${a.running ? 'running' : 'starts when needed'}${a.window === 'hidden' ? ', invisible' : ''}` : null,
      s.timeout_s ? `Calls up to ${Math.round(s.timeout_s / 60)} min` : null].filter(Boolean);
    const setKey = s.transport === 'http' ? 'headers' : 'env', setNames = Object.keys(s[setKey] || {}), setFilled = new Set((s.filled || {})[setKey] || []);
    const secretNames = setNames.filter(n => ((s.shown || {})[setKey] || {})[n]?.secret);
    const emptyNames = setNames.filter(n => !setFilled.has(n));
    const credChip = emptyNames.length ? el('span', { class: 'mcp-chip need', text: `Needs ${emptyNames.join(', ')}`, title: 'Open the card and fill it in under Settings' })
      : secretNames.length ? el('span', { class: 'mcp-chip ok', text: '✓ Credentials saved', title: secretNames.map(n => `${n}: ${((s.shown || {})[setKey] || {})[n].value}`).join('\n') }) : null;
    const toggle = el('label', { class: 'ac-toggle', title: s.enabled ? 'On: its tools are offered to the agent' : 'Off: its tools are not offered' },
      el('input', { type: 'checkbox', checked: s.enabled, 'aria-label': `${s.name} on or off`, onchange: e => change(s, { enabled: e.target.checked }) }), el('span'));
    const menu = el('button', { class: 'icon-btn sm', title: 'More', 'aria-label': 'More for ' + s.name, html: ico('more', 15), onclick: ev => {
      ev.stopPropagation();
      const r = ev.currentTarget.getBoundingClientRect();
      showContextMenu(r.left - 120, r.bottom + 4, [
        { label: 'Restart', run: async () => { try { await POST('/api/admin/mcp/restart', { name: s.name }); load(); } catch (e) { errToast(e, 'Restart failed'); } } },
        { label: 'Copy its command', run: () => copyText(s.transport === 'http' ? s.url : [s.command, ...(s.args || [])].join(' ')).then(() => toast('Copied', 'success', 1200)) },
        '-',
        { label: 'Remove…', danger: true, run: async () => {
          const yes = await dialog({ title: 'Remove ' + s.name + '?', body: 'The agent stops getting its tools. The server program itself is not uninstalled.', buttons: [{ label: 'Cancel' }, { label: 'Remove', value: 'yes', kind: 'danger' }] });
          if (yes === 'yes') save(servers.filter(x => x !== s));
        } }]);
    } });
    const head = el('div', { class: 'mcp-card-head', role: 'button', tabindex: '0', 'aria-expanded': open.has(s.name) ? 'true' : 'false', onclick: e => {
      if (e.target.closest('.ac-toggle, .icon-btn')) return;
      open.has(s.name) ? open.delete(s.name) : open.add(s.name); render();
    }, onkeydown: e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.currentTarget.click(); } } },
      el('span', { class: 'mcp-dot ' + (s.enabled ? state : 'off') }),
      el('div', { class: 'mcp-title' }, el('div', {}, el('b', { text: s.name }), el('small', { class: 'mcp-sub', text: ' · ' + (s.enabled ? stateText : 'Off') + (s.server_info && s.server_info.name ? ` · ${s.server_info.name} ${s.server_info.version || ''}` : '') })),
        s.description ? el('div', { class: 'mcp-desc', text: s.description }) : null,
        s.error ? el('div', { class: 'warn-text mcp-desc', text: s.error }) : null,
        el('div', { class: 'mcp-chips' }, credChip, chips.map(c => el('span', { class: 'mcp-chip', text: c })))),
      toggle, menu, el('span', { class: 'mcp-caret', text: open.has(s.name) ? '▴' : '▾', 'aria-hidden': 'true' }));
    if (!open.has(s.name)) return el('div', { class: 'mcp-card' + (s.enabled ? '' : ' off') + (s.state === 'error' ? ' failed' : '') }, head);
    const shared = el('input', { type: 'checkbox', checked: s.users, onchange: e => change(s, { users: e.target.checked }) });
    const approval = el('select', { class: 'dlg-input mcp-approval', onchange: e => change(s, { approval: e.target.value }) },
      el('option', { value: 'always', text: 'Ask before every call' }), el('option', { value: 'with_commands', text: 'Like commands (auto-run skips asking)' }));
    approval.value = s.approval;
    const command = s.transport === 'http' ? s.url : [s.command, ...(s.args || [])].join(' ');
    const body = el('div', { class: 'mcp-card-body' },
      el('div', { class: 'mcp-field' }, el('span', { text: s.transport === 'http' ? 'Address' : 'Command' }),
        el('div', { class: 'mcp-cmd-row' }, el('code', { class: 'mcp-cmd', text: command }),
          el('button', { class: 'icon-btn sm', title: 'Copy', 'aria-label': 'Copy the command', html: ico('copy', 13), onclick: () => copyText(command).then(() => toast('Copied', 'success', 1200)) }))),
      el('div', { class: 'mcp-settings' },
        el('label', { class: 'mcp-field' }, el('span', { text: 'Before each call' }), approval),
        el('label', { class: 'mcp-field mcp-check' }, el('span', { text: 'Who can use it' }), el('span', { class: 'ac-switch' }, shared, ' Users too, not only admins'))),
      secretsRow(s),
      appRow(s),
      s.tools.length ? el('details', { class: 'mcp-more' }, el('summary', { text: `Its ${s.tools.length} tool${s.tools.length === 1 ? '' : 's'}` }),
        el('ul', { class: 'mcp-tools' }, s.tools.map(t => el('li', {}, el('code', { text: t.name }), t.description ? el('small', { text: ' ' + t.description }) : null)))) : null,
      el('details', { class: 'mcp-more' }, el('summary', { text: 'Log' }), el('pre', { class: 'mcp-log', text: s.log.join('\n') || 'Nothing logged yet' })));
    return el('div', { class: 'mcp-card open' + (s.enabled ? '' : ' off') + (s.state === 'error' ? ' failed' : '') }, head, body);
  }
  // Its settings (environment for a program, headers for an address): values stay hidden; each says whether it is set,
  // a new value replaces it, an empty box keeps it. Saving restarts the server.
  function secretsRow(s) {
    const key = s.transport === 'http' ? 'headers' : 'env', values = s[key] || {}, filled = new Set((s.filled || {})[key] || []);
    const shown = (s.shown || {})[key] || {};
    const names = Object.keys(values), missing = names.filter(n => !filled.has(n));
    // Harmless settings are shown and edited as they are; secrets show only their last characters and take a new value.
    const inputs = names.map(n => {
      const info = shown[n] || { secret: true, value: '' };
      const input = info.secret
        ? el('input', { class: 'dlg-input mono', type: 'password', autocomplete: 'new-password', spellcheck: 'false', 'aria-label': n,
            placeholder: filled.has(n) ? `${info.value || '••••'} · saved. Type to replace it` : 'Empty. Type a value' })
        : el('input', { class: 'dlg-input mono', type: 'text', spellcheck: 'false', 'aria-label': n, value: info.value || '', placeholder: 'Empty. Type a value' });
      input._was = info.secret ? null : (info.value || '');
      return [n, input];
    });
    const extra = el('textarea', { class: 'dlg-input mono', rows: 1, placeholder: key === 'env' ? 'Another one: NAME=value' : 'Another one: Name: value', 'aria-label': 'Add a setting' });
    const show = el('button', { class: 'link-btn card-link', text: 'Show what I type', onclick: () => { for (const [n, i] of inputs) if ((shown[n] || { secret: true }).secret) i.type = i.type === 'password' ? 'text' : 'password'; } });
    const saveBtn = el('button', { class: 'btn sm primary', text: 'Save and restart', onclick: () => {
      const next = Object.fromEntries(inputs.map(([n, i]) => [n, i._was !== null ? (i.value.trim() === i._was ? SECRET_KEEP : i.value.trim()) : (i.value.trim() ? i.value.trim() : SECRET_KEEP)]));
      Object.assign(next, parsePairs(extra.value, key === 'env' ? '=' : ':'));
      change(s, { [key]: next });
    } });
    return el('details', { class: 'mcp-more mcp-secrets', open: missing.length ? '' : null },
      el('summary', {}, key === 'env' ? 'Settings (environment)' : 'Headers', missing.length ? el('span', { class: 'mcp-need', text: ` · ${missing.length} empty: ${missing.join(', ')}` }) : ` · ${names.length}`),
      el('div', { class: 'mcp-secret-rows' }, inputs.map(([n, i]) => el('label', { class: 'mcp-secret' }, el('code', { text: n }), i,
        el('small', { class: filled.has(n) ? 'mcp-set' : 'warn-text', text: filled.has(n) ? ((shown[n] || {}).secret ? '✓ saved, hidden' : '✓ saved') : 'empty' })))),
      extra, el('div', { class: 'ac-inline' }, saveBtn, show, el('small', { class: 'dlg-note', text: 'Values are never shown again once saved.' })));
  }
  // A program the server needs running (Blender for the blender server): its state, Start and Stop, and the setting.
  function appRow(s) {
    const program = el('input', { class: 'dlg-input mono', placeholder: 'e.g. ~/.local/bin/blender', value: s.app_command || '' });
    const port = el('input', { class: 'dlg-input mono mcp-port', type: 'number', min: '1', max: '65535', placeholder: 'port', value: s.app_port || '' });
    const windowPick = el('select', { class: 'dlg-input mcp-window', 'aria-label': 'Its window' }, el('option', { value: 'screen', text: 'Window on the server\'s screen' }), el('option', { value: 'hidden', text: 'Invisible: no window' }));
    windowPick.value = s.app_window || 'screen';
    const setting = el('details', { class: 'mcp-more' }, el('summary', { text: s.app ? 'Change the program it needs' : 'Needs a program running? (Studio can start it)' }),
      el('div', { class: 'ac-inline' }, program, port, windowPick, el('button', { class: 'btn sm', onclick: () => {
        const command = program.value.trim();
        if (command && !(Number(port.value) > 0)) { toast('Give the port the program listens on', 'warn'); return; }
        change(s, command ? { app_command: command, app_port: Number(port.value), app_window: windowPick.value } : { app_command: '', app_port: undefined, app_window: undefined });
      } }, 'Save')),
      s.app && !s.app.hidden_available ? el('small', { class: 'warn-text', text: 'Invisible needs Xvfb, which is not installed here: sudo apt install xvfb' }) : null,
      el('small', { class: 'muted', text: 'When a call needs it and nothing answers on that port, Studio starts the program and waits for it: on the server\'s screen, or invisibly (it still works, and the agent still gets its screenshots, but nobody sees a window; drawing is slower). A change applies the next time it starts.' }));
    if (!s.app) return el('div', { class: 'mcp-field' }, el('span', { text: 'Program it needs' }), setting);
    const a = s.app, name = a.command.split(/[\\/]/).pop().split(' ')[0];
    const act = async action => {
      try { await POST('/api/admin/mcp/app', { name: s.name, action }); toast(action === 'start' ? `${name} is running` : `${name} was closed`, 'success', 2500); load(); }
      catch (e) { errToast(e, action === 'start' ? `Could not start ${name}` : `Could not stop ${name}`); }
    };
    return el('div', { class: 'mcp-app' }, el('span', { class: 'dash-dot', 'data-state': a.running ? 'idle' : 'off' }),
      el('span', { class: 'grow' }, el('b', { text: name }), ` ${a.running ? 'is running' : 'is not running'} (port ${a.port}${a.window === 'hidden' ? ', invisible' : ''})`, a.error ? el('small', { class: 'warn-text', text: ' · ' + a.error }) : null, a.running && !a.started_by_studio ? el('small', { class: 'muted', text: ' · opened by someone, not Studio' }) : null,
        !a.running ? el('small', { class: 'muted', text: ' · Studio starts it when the agent needs it' }) : null),
      !a.running ? el('button', { class: 'btn sm', onclick: e => { e.currentTarget.disabled = true; e.currentTarget.textContent = 'Starting…'; act('start'); } }, 'Start') : null,
      a.running && a.started_by_studio ? el('button', { class: 'btn sm', onclick: () => act('stop') }, 'Stop') : null, setting);
  }
  function addForm() {
    const name = el('input', { class: 'dlg-input', placeholder: 'blender' });
    const kind = el('select', { class: 'dlg-input' }, el('option', { value: 'stdio', text: 'A program on this machine' }), el('option', { value: 'http', text: 'A URL' }));
    const command = el('input', { class: 'dlg-input mono', placeholder: 'uvx blender-mcp' });
    const env = el('textarea', { class: 'dlg-input mono', rows: 2, placeholder: 'API_KEY=…  (one per line)' });
    const url = el('input', { class: 'dlg-input mono', placeholder: 'https://example.com/mcp' });
    const headers = el('textarea', { class: 'dlg-input mono', rows: 2, placeholder: 'Authorization: Bearer …  (one per line)' });
    const about = el('input', { class: 'dlg-input', placeholder: 'What it is for, told to the agent (optional)' });
    const json = el('textarea', { class: 'dlg-input mono', rows: 4, placeholder: '{"mcpServers": {"blender": {"command": "uvx", "args": ["blender-mcp"]}}}' });
    const field = (label, input) => el('label', { class: 'dlg-field' }, el('span', { text: label }), input);
    const program = el('div', {}, field('Command', command), field('Environment', env)), web = el('div', { hidden: true }, field('URL', url), field('Headers', headers));
    kind.onchange = () => { program.hidden = kind.value !== 'stdio'; web.hidden = kind.value !== 'http'; };
    const add = el('button', { class: 'btn primary', onclick: () => {
      const parts = splitCommandLine(command.value.trim());
      const s = kind.value === 'stdio' ? { command: parts[0], args: parts.slice(1), env: parsePairs(env.value, '=') } : { url: url.value.trim(), headers: parsePairs(headers.value, ':') };
      save([...servers, { name: name.value.trim(), transport: kind.value, enabled: true, approval: 'always', users: false, description: about.value.trim(), ...s }]);
    } }, 'Add server');
    const fromJson = el('button', { class: 'btn', onclick: () => {
      let list; try { list = mcpFromJson(json.value); } catch (e) { toast('That is not valid JSON: ' + e.message, 'warn'); return; }
      save([...servers, ...list]);
    } }, 'Add from JSON');
    return el('div', { class: 'mcp-add ac-card' }, el('div', { class: 'dlg-sub', text: 'Add a server' }),
      el('div', { class: 'mcp-grid' }, field('Name', name), field('Kind', kind)), program, web, field('Description', about), el('div', { class: 'account-actions' }, add),
      el('div', { class: 'dlg-sub', text: 'Or paste the JSON from the server\'s instructions' }), json, el('div', { class: 'account-actions' }, fromJson));
  }
  const search = el('input', { class: 'dlg-input ac-search', type: 'search', placeholder: 'Filter servers', 'aria-label': 'Filter servers' });
  search.oninput = () => { filter = search.value.toLowerCase(); render(); };
  let note = null;
  let adding = false;
  function render() {
    const shown = servers.filter(s => !filter || (s.name + ' ' + (s.description || '')).toLowerCase().includes(filter));
    const running = servers.filter(s => s.enabled && s.state === 'ready').length, failed = servers.filter(s => s.enabled && s.state === 'error').length, off = servers.filter(s => !s.enabled).length;
    const add = el('button', { class: 'btn sm primary', onclick: () => { adding = !adding; render(); } }, el('span', { html: ico('plus', 13) }), adding ? 'Close the form' : 'Add server');
    box.replaceChildren(acHead('Tool servers', 'Extra tools for the agent, such as Blender, a database, pictures or voices.', add),
      note || el('span'),
      el('div', { class: 'mcp-summary' },
        el('span', { class: 'mcp-stat' }, el('b', { text: String(servers.length) }), ' servers'),
        el('span', { class: 'mcp-stat ok' }, el('span', { class: 'mcp-dot ready' }), el('b', { text: String(running) }), ' running'),
        failed ? el('span', { class: 'mcp-stat bad' }, el('span', { class: 'mcp-dot error' }), el('b', { text: String(failed) }), ' failed') : null,
        off ? el('span', { class: 'mcp-stat' }, el('span', { class: 'mcp-dot off' }), el('b', { text: String(off) }), ' off') : null,
        el('span', { class: 'grow' }), search),
      adding || !servers.length ? addForm() : el('span'),
      shown.length ? el('div', { class: 'mcp-list' }, shown.map(row)) : el('div', { class: 'dir-note', text: servers.length ? 'No server matches' : 'No tool servers yet' }),
      el('p', { class: 'muted ac-intro', text: 'Programs run on this machine under Studio\'s account, outside the sandbox, so add only servers you trust. Each run sees the tools of the servers running when it starts. Click a card for its settings.' }));
    search.value = filter;
    if (filter) search.focus();
  }
  async function load() {
    try { const d = await GET('/api/admin/mcp'); servers = d.servers; note = d.enabled ? null : el('div', { class: 'warn-text', text: 'Tool servers are turned off in this Studio\'s config ("mcp": false).' }); render(); }
    catch (e) { box.replaceChildren(el('div', { class: 'dir-note', text: e.message })); return; }
    stop();
    if (servers.some(s => s.state === 'starting') && document.body.contains(box)) timer = setTimeout(load, 2000);
  }
  box.replaceChildren(acHead('Tool servers', 'Extra tools for the agent, such as Blender, a database, pictures or voices.'), el('div', { class: 'dir-note', text: 'Loading…' }));
  await load();
  search.value = filter;
}
async function paneUsers(box) {
  const me = S.config.user && S.config.user.username;
  let filter = '';
  const newLink = el('div');
  const act = async (fn, failure) => { try { await fn(); await render(); } catch (e) { errToast(e, failure); } };
  const search = el('input', { class: 'dlg-input ac-search', type: 'search', placeholder: 'Find a person', 'aria-label': 'Find a person' });
  let d = null;
  search.oninput = () => { filter = search.value.toLowerCase(); draw(); };
  async function render() { d = await GET('/api/admin/users'); draw(); }
  function draw() {
    const role = el('select', { class: 'dlg-input invite-role' }, el('option', { value: 'user' }, 'User'), el('option', { value: 'admin' }, 'Admin'));
    const create = el('button', { class: 'btn primary', onclick: () => act(async () => {
      const r = await POST('/api/admin/invites', { role: role.value });
      newLink.replaceChildren(linkBox(linkBase() + '#invite=' + encodeURIComponent(r.token), 'Send this to the person you are inviting. It works once and expires in 7 days.'));
    }, 'Could not create invite') }, 'Create invite link');
    const reset = u => act(async () => {
      const r = await POST('/api/admin/users/reset-link', { username: u.username });
      newLink.replaceChildren(linkBox(linkBase() + '#reset=' + encodeURIComponent(r.token), `Reset link for ${u.username}. It works once and expires in an hour.`));
    }, 'Could not create reset link');
    const more = (u, ev) => {
      const r = ev.currentTarget.getBoundingClientRect();
      const items = [{ label: 'Make a reset link', run: () => reset(u) }];
      if (u.username !== me) items.push({ label: u.role === 'admin' ? 'Make user' : 'Make admin', run: () => act(() => POST('/api/admin/users/update', { username: u.username, role: u.role === 'admin' ? 'user' : 'admin' }), 'Could not change role') },
        { label: u.disabled ? 'Enable account' : 'Disable account', run: () => act(() => POST('/api/admin/users/update', { username: u.username, disabled: !u.disabled }), 'Could not change account') });
      if (u.role !== 'admin') items.push({ label: u.pc_folders ? 'Stop own computer' : 'Allow own computer', run: () => act(() => POST('/api/admin/users/update', { username: u.username, pc_folders: !u.pc_folders }), 'Could not change account') },
        { label: u.card ? 'Stop graphics card choice' : 'Allow graphics card choice', run: () => act(() => POST('/api/admin/users/update', { username: u.username, card: !u.card }), 'Could not change account') });
      if (u.username !== me) items.push('-', { label: 'Delete…', danger: true, run: async () => {
        const yes = await dialog({ title: 'Delete ' + u.username + '?', body: 'They are signed out and their running work stops. Their project folder is kept on disk, so the username stays reserved.', buttons: [{ label: 'Cancel' }, { label: 'Delete', value: 'yes', kind: 'danger' }] });
        if (yes === 'yes') act(() => POST('/api/admin/users/delete', { username: u.username }), 'Could not delete user');
      } });
      showContextMenu(r.left, r.bottom + 4, items);
    };
    const rightSwitch = (u, key, label, title) => el('label', { class: 'ac-right', title },
      el('input', { type: 'checkbox', role: 'switch', checked: u[key] ? '' : null, 'data-right': key,
        onchange: e => act(() => POST('/api/admin/users/update', { username: u.username, [key]: e.currentTarget.checked }), 'Could not change account') }),
      el('span', { class: 'ac-knob', 'aria-hidden': 'true' }), el('span', { text: label }));
    const shown = d.users.filter(u => !filter || (u.username + ' ' + (u.display_name || '')).toLowerCase().includes(filter));
    const cards = shown.map(u => el('div', { class: 'ac-row ac-user' + (u.disabled ? ' off' : ''), 'data-user': u.username },
      avatar(u.display_name || u.username),
      el('div', { class: 'grow' }, el('div', {}, el('b', { text: u.display_name || u.username }), u.display_name ? el('small', { class: 'muted', text: ' @' + u.username }) : null),
        el('div', { class: 'ac-badges' }, el('span', { class: 'ac-badge ' + (u.role === 'admin' ? 'admin' : ''), text: u.role === 'admin' ? 'Admin' : 'User' }),
          u.disabled ? el('span', { class: 'ac-badge bad', text: 'Disabled' }) : null, u.username === me ? el('span', { class: 'ac-badge ok', text: 'You' }) : null,
          u.role !== 'admin' && u.pc_folders ? el('span', { class: 'ac-badge ok', text: 'Own computer' }) : null,
          u.role !== 'admin' && u.card ? el('span', { class: 'ac-badge ok', text: 'Graphics card' }) : null,
          el('small', { class: 'muted', text: `since ${dateText(u.created)} · last sign-in ${dateText(u.last_login)}` }))),
      u.role === 'admin' ? null : el('div', { class: 'ac-rights', role: 'group', 'aria-label': 'Rights of ' + u.username },
        rightSwitch(u, 'pc_folders', 'Own computer', 'Let them connect folders on their own computer, whose commands and terminals run there instead of in a sandbox here. Turning it off removes their connected computers.'),
        rightSwitch(u, 'card', 'Graphics card', 'Let them choose how Strata and the picture and voice tools share the graphics card (Account & settings → Graphics card), and restart Strata to apply it when no agent run is working.')),
      el('button', { class: 'icon-btn sm', title: 'More', 'aria-label': 'More for ' + u.username, html: ico('more', 15), onclick: ev => more(u, ev) })));
    const invites = d.invites.map(i => el('div', { class: 'ac-row' }, el('span', { html: ico('send', 14) }), el('span', { class: 'grow', text: `${i.role === 'admin' ? 'Admin' : 'User'} invite by ${i.by || '?'}, expires ${dateText(i.expires)}` }),
      el('button', { class: 'btn sm', onclick: () => act(() => POST('/api/admin/invites/revoke', { id: i.id }), 'Could not revoke invite') }, 'Revoke')));
    const admins = d.users.filter(u => u.role === 'admin').length, off = d.users.filter(u => u.disabled).length;
    box.replaceChildren(acHead('Users', `${d.users.length} account${d.users.length === 1 ? '' : 's'}: ${admins} admin${admins === 1 ? '' : 's'}${off ? `, ${off} disabled` : ''}.`, search),
      el('div', { class: 'ac-card' }, el('div', { class: 'dlg-sub', text: 'Invite someone' }), el('div', { class: 'invite-row' }, el('span', { text: 'As' }), role, create), newLink),
      el('div', { class: 'ac-card' }, el('div', { class: 'ac-list' }, cards.length ? cards : [el('div', { class: 'dir-note', text: 'Nobody matches' })])),
      el('div', { class: 'ac-card' }, el('div', { class: 'dlg-sub', text: `Pending invites (${invites.length})` }), invites.length ? el('div', { class: 'ac-list' }, invites) : el('div', { class: 'dir-note', text: 'No pending invites' })),
      el('small', { class: 'muted', text: 'Users work in private folders under ' + d.user_root + ', always in the sandbox, unless they connect their own computer.' }));
    search.value = filter;
    if (filter) search.focus();
  }
  await render();
}

/* ------------------------------------------------------------------ top bar: models, toggles */
function labelForModel(m) { return (m.label || m.name) + (m.tools === false ? ' — chat only' : ''); }
const MODEL_GROUPS = [['strata', 'Strata'], ['ollama', 'Ollama'], ['openai', 'Model server'], ['codex', 'Codex (ChatGPT)']];   // openai: llama.cpp, vLLM, LM Studio
const modelGroup = m => MODEL_GROUPS.some(([k]) => k === m.kind) ? m.kind : 'ollama';
// The models the picker shows: the person's choice (Account & settings → Models, pref models_shown), or all of them
// until they choose. The model in use always shows, and every model still answers /model.
function shownModels() {
  const chosen = Array.isArray(S.prefs?.models_shown) && S.prefs.models_shown.length ? new Set(S.prefs.models_shown) : null;
  return chosen ? S.models.filter(m => chosen.has(m.name) || m.name === S.model) : S.models;
}
function renderModels() {
  const sel = $('#model-select'); sel.replaceChildren();
  // Codex models (admins, config codex) run through the Codex CLI with this machine's ChatGPT login, not the gateway.
  const groups = Object.fromEntries(MODEL_GROUPS.map(([k, label]) => [k, el('optgroup', { label })]));
  const names = S.models.map(m => m.name);
  const want = names.includes(S.model) ? S.model : names.includes(S.config.default_model) ? S.config.default_model : names[0];
  if (want) S.model = want;
  for (const m of shownModels()) groups[modelGroup(m)].append(el('option', { value: m.name, text: labelForModel(m), title: m.description || '' }));
  for (const g of Object.values(groups)) if (g.children.length) sel.append(g);
  if (!S.models.length) sel.append(el('option', { value: '', text: 'No models available' }));
  else sel.append(el('option', { value: '', disabled: '', text: '──────────' }),
    el('option', { value: '__choose', text: shownModels().length < S.models.length ? `Choose models… (${S.models.length - shownModels().length} hidden)` : 'Choose models…' }));
  if (want) sel.value = want;
}
$('#model-select').addEventListener('change', e => {
  if (e.target.value === '__choose') { e.target.value = S.model; openAccount('models'); return; }
  S.model = e.target.value; lsSet('studio.model', S.model); prefSet('model', S.model); effortChip();
});
/* Codex's reasoning effort for this chat: a chip in the message box while a Codex model is picked, with exactly the levels
   that model offers (low to ultra) and its default. Saved per chat, like the gear's effort for Studio's own models. */
const EFFORT_NAMES = { none: 'None', minimal: 'Minimal', low: 'Low', medium: 'Medium', high: 'High', xhigh: 'Extra high', max: 'Max', ultra: 'Ultra' };
/* Strata models think before answering; the chat's effort goes to the Manager as reasoning_effort, and without one the
   Manager's profile decides (medium). Ollama models vary in whether they think, so they get no chip. */
const STRATA_EFFORTS = [
  { value: 'none', description: 'Answer directly, without thinking first' },
  { value: 'low', description: 'Think briefly' },
  { value: 'medium', description: 'The usual amount of thinking' },
  { value: 'high', description: 'Think longer before each step' },
  { value: 'xhigh', description: 'The longest thinking: slower, for hard problems' }];
function chipEfforts(m) {
  if (!m) return null;
  if (m.kind === 'codex' && (m.efforts || []).length) return { list: m.efforts, def: m.default_effort, who: 'Codex' };
  if (m.kind === 'strata') return { list: STRATA_EFFORTS, def: 'medium', who: m.label || m.name };
  return null;
}
function effortChip() {
  const m = S.models.find(x => x.name === S.model), chip = $('#effort-chip');
  const offer = chipEfforts(m);
  chip.classList.toggle('hidden', !offer);
  cxStrip();
  if (!offer) return;
  const mine = offer.list.some(e => e.value === A.effort) ? A.effort : '';
  chip.querySelector('span').textContent = 'Effort: ' + (mine ? EFFORT_NAMES[mine] || mine : (EFFORT_NAMES[offer.def] || 'default'));
  chip.classList.toggle('on', !!mine);
  chip.title = mine ? `${offer.who} thinks at ${EFFORT_NAMES[mine] || mine} in this chat. Click to change.` : `The default for ${offer.who}: ${EFFORT_NAMES[offer.def] || offer.def}. Click to choose for this chat.`;
}
$('#effort-chip').onclick = e => {
  const offer = chipEfforts(S.models.find(x => x.name === S.model)); if (!offer) return;
  const r = e.currentTarget.getBoundingClientRect();
  const pick = async value => {
    if (!S.ws) return;
    try { await ensureSession('New chat'); const res = await POST('/api/agent/session/settings', { ws: S.ws.id, id: A.session, effort: value }); A.effort = res.effort; effortChip(); }
    catch (err) { errToast(err, 'Could not change the effort'); }
  };
  const items = offer.list.map(x => ({ label: (A.effort === x.value ? '✓ ' : '') + (EFFORT_NAMES[x.value] || x.value) + (x.value === offer.def ? ' (default)' : ''), title: x.description, run: () => pick(x.value) }));
  showContextMenu(r.left, r.top - 8 - 32 * (items.length + 2), [...items, '-', { label: (!A.effort ? '✓ ' : '') + 'The model\'s default', run: () => pick('') }]);
};
/* ------------------------------------------------------------------ Codex usage; the Codex, Greek and Models panes of Account & settings */
/* Codex usage (admins with Codex on): the ChatGPT plan's short window (5 hours) and the week, as bars in a status-bar
   pill and a panel above it, from GET /api/codex/usage (Codex's account/rateLimits/read) and live codex_usage events. */
const CX = { data: null, timer: null };
function codexUsageInit() {
  const on = S.models.some(m => m.kind === 'codex');
  $('#sb-codex').classList.toggle('hidden', !on);
  if (!on) return;
  codexUsageLoad();
  clearInterval(CX.timer); CX.timer = setInterval(() => { if (!document.hidden) codexUsageLoad(); }, 5 * 60 * 1000);
}
async function codexUsageLoad(fresh) {
  try { codexUsageShow(await GET('/api/codex/usage' + (fresh ? '?fresh=1' : ''))); }
  catch (e) { if (e.status === 403 || e.status === 404) $('#sb-codex').classList.add('hidden'); }
}
function cxWhen(t) {
  if (!t) return '';
  const s = t - Date.now() / 1000;
  if (s <= 60) return 'resets now';
  if (s < 3600) return `resets in ${Math.round(s / 60)} min`;
  if (s < 86400) return `resets in ${Math.floor(s / 3600)} h ${Math.round(s % 3600 / 60)} min`;
  return 'resets ' + new Date(t * 1000).toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}
const cxLevel = used => used >= 90 ? 'err' : used >= 70 ? 'warn' : 'ok';
function codexUsageShow(d) {
  CX.data = d;
  const wins = (d.windows || []).filter(Boolean);
  const pill = $('#sb-codex');
  pill.querySelectorAll('.cx-mini i').forEach((bar, i) => { const w = wins[i]; bar.style.setProperty('--cx', (w ? w.used : 0) + '%'); bar.dataset.level = w ? cxLevel(w.used) : 'ok'; });
  $('#sb-codex-text').textContent = d.error && !wins.length ? 'Codex: ?' : 'Codex ' + wins.map(w => (w.minutes === 10080 ? 'week ' : '') + w.used + '%').join(' · ');
  pill.title = wins.map(w => `${w.label}: ${w.used}% used, ${cxWhen(w.resets_at)}`).join('\n') || (d.error ? 'Codex usage could not be read: ' + d.error : 'Codex usage');
  pill.dataset.level = wins.length ? cxLevel(Math.max(...wins.map(w => w.used))) : 'ok';
  if (CX.panel) codexUsagePanel();
  cxStrip();
}
/* The same usage in the chat itself, under its header, while a Codex model is chosen: one bar per window with its use and
   when it resets. Phones hide the status bar, so this is where they see it; a tap opens the panel with the details. */
function cxLeft(t) {
  const s = (t || 0) - Date.now() / 1000;
  if (!t) return '';
  if (s <= 60) return 'resets now';
  if (s < 3600) return `resets in ${Math.round(s / 60)}m`;
  return `resets in ${Math.floor(s / 3600)}h ${Math.round(s % 3600 / 60)}m`;
}
function cxStrip() {
  const strip = $('#cx-strip'), m = S.models.find(x => x.name === S.model);
  const wins = ((CX.data && CX.data.windows) || []).filter(Boolean);
  const on = !!(m && m.kind === 'codex' && wins.length);
  strip.classList.toggle('hidden', !on);
  if (!on) return;
  strip.replaceChildren(...wins.slice(0, 2).map(w => el('span', { class: 'cx-sw', 'data-level': cxLevel(w.used) },
    el('span', { class: 'cx-track' }, el('span', { class: 'cx-fill', style: `width:${Math.max(1, w.used)}%` })),
    el('small', { text: `${w.used}% · ${w.minutes === 10080 ? 'week' : w.minutes === 300 ? '5h' : w.label} · ${cxLeft(w.resets_at)}` }))));
  strip.setAttribute('aria-label', 'Codex usage: ' + wins.map(w => `${w.label} ${w.used}% used, ${cxWhen(w.resets_at)}`).join('; '));
}
$('#cx-strip').onclick = e => {
  e.stopPropagation();
  if (CX.panel && CX.panel.isConnected) { CX.panel.remove(); CX.panel = null; $('#cx-strip').setAttribute('aria-expanded', 'false'); return; }
  const box = codexUsagePanel(); document.body.append(box);
  const r = $('#cx-strip').getBoundingClientRect();
  box.style.right = Math.max(8, innerWidth - r.right) + 'px'; box.style.top = (r.bottom + 6) + 'px'; box.style.bottom = 'auto';
  $('#cx-strip').setAttribute('aria-expanded', 'true');
  codexUsageLoad(true);
};
function codexUsagePanel() {
  const d = CX.data || { windows: [] };
  const wins = (d.windows || []).filter(Boolean);
  const box = CX.panel || (CX.panel = el('div', { id: 'codex-usage', class: 'cx-panel', role: 'dialog', 'aria-label': 'Codex usage' }));
  box.replaceChildren(...[
    el('div', { class: 'cx-head' }, el('b', { text: 'Codex usage' }), d.plan ? el('span', { class: 'ac-badge', text: d.plan[0].toUpperCase() + d.plan.slice(1) + ' plan' }) : null,
      el('span', { class: 'grow' }), el('button', { class: 'icon-btn sm', title: 'Read it again now', 'aria-label': 'Read it again now', html: ico('refresh', 14), onclick: () => codexUsageLoad(true) })),
    ...cxBars(d),
    !wins.length ? el('div', { class: 'muted', text: d.error ? 'Could not read the usage: ' + d.error : 'No usage reported yet.' }) : null,
    d.reached ? el('div', { class: 'warn-text', text: 'The limit is reached; Codex waits until it resets.' }) : null,
    d.reset_credits ? el('small', { class: 'muted', text: `${d.reset_credits} free reset${d.reset_credits === 1 ? '' : 's'} of both limits available in Codex.` }) : null,
    d.at ? el('small', { class: 'muted cx-at', text: 'Read ' + new Date(d.at * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) }) : null].filter(Boolean));
  return box;
}
$('#sb-codex').onclick = e => {
  e.stopPropagation();
  if (CX.panel && CX.panel.isConnected) { CX.panel.remove(); CX.panel = null; $('#sb-codex').setAttribute('aria-expanded', 'false'); return; }
  const box = codexUsagePanel(); document.body.append(box);
  const r = $('#sb-codex').getBoundingClientRect();
  box.style.right = Math.max(8, innerWidth - r.right) + 'px'; box.style.bottom = (innerHeight - r.top + 6) + 'px';
  $('#sb-codex').setAttribute('aria-expanded', 'true');
  codexUsageLoad(true);
};
document.addEventListener('click', e => { if (CX.panel && CX.panel.isConnected && !CX.panel.contains(e.target)) { CX.panel.remove(); CX.panel = null; $('#sb-codex').setAttribute('aria-expanded', 'false'); $('#cx-strip').setAttribute('aria-expanded', 'false'); } });
/* Account & settings → Codex (admins): which account Codex uses, its usage, Sign out, and signing in the ways Codex offers:
   a link and a one-time code (works from any computer), a sign-in link whose last localhost page is pasted back, or an API key. */
function cxBars(d) {
  return (d.windows || []).filter(Boolean).map(w => el('div', { class: 'cx-row', 'data-level': cxLevel(w.used) },
    el('div', { class: 'cx-line' }, el('span', { text: w.minutes === 10080 ? 'This week' : w.minutes === 300 ? 'This session (5 hours)' : w.label }), el('b', { text: w.used + '% used' })),
    el('div', { class: 'cx-track', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': String(w.used) }, el('div', { class: 'cx-fill', style: `width:${w.used}%` })),
    el('small', { class: 'muted', text: cxWhen(w.resets_at) })));
}
async function paneCodex(box) {
  box.replaceChildren(acHead('Codex', 'The OpenAI account Codex uses on this machine, for every admin\'s Codex chats.'), skeleton(3));
  let d, usage = null;
  try { d = await GET('/api/codex/account'); } catch (e) { box.replaceChildren(acHead('Codex'), el('div', { class: 'dir-note', text: e.message })); return; }
  if (d.account) { try { usage = await GET('/api/codex/usage?fresh=1'); codexUsageShow(usage); } catch { /* shown without */ } }
  const a = d.account, login = d.login || {};
  const again = () => paneCodex(box);
  const who = !a ? 'Not signed in' : a.type === 'chatgpt' ? `Signed in as ${a.email || 'a ChatGPT account'}` : a.type === 'apiKey' ? 'Signed in with an OpenAI API key' : `Signed in (${a.type})`;
  const accountCard = el('div', { class: 'ac-card cx-account' },
    el('div', { class: 'cx-head' }, el('span', { class: 'dash-dot', 'data-state': a ? 'ok' : 'err' }), el('b', { class: 'cx-who', text: who }),
      a && a.plan ? el('span', { class: 'ac-badge', text: a.plan[0].toUpperCase() + a.plan.slice(1) + ' plan' }) : null, el('span', { class: 'grow' }),
      a ? el('button', { class: 'btn sm danger', onclick: async () => {
        const yes = await dialog({ title: 'Sign Codex out?', body: 'Codex chats stop working until someone signs in again here. Chats and files stay.', buttons: [{ label: 'Cancel' }, { label: 'Sign out', value: 'yes', kind: 'danger' }] });
        if (yes !== 'yes') return;
        try { await POST('/api/codex/logout', {}); toast('Codex is signed out', 'success', 2500); again(); } catch (err) { errToast(err, 'Not signed out'); }
      } }, 'Sign out') : null),
    a && a.workspace ? el('small', { class: 'muted', text: 'Workspace: ' + a.workspace }) : null,
    ...(usage ? cxBars(usage) : []),
    d.error ? el('div', { class: 'warn-text', text: 'Codex did not answer: ' + d.error }) : null);
  const flow = el('div', { class: 'ac-card cx-login' });
  const poll = () => { clearTimeout(CX.loginTimer); CX.loginTimer = setTimeout(async () => {
    if (!box.isConnected) return;
    try { const st = await GET('/api/codex/login'); if (st.state === 'waiting') return poll(); if (st.state === 'done') toast('Codex is signed in' + (st.email ? ' as ' + st.email : ''), 'success', 3000); else if (st.state === 'failed') toast('Sign-in failed: ' + st.error, 'error', 6000); again(); } catch { poll(); }
  }, 2000); };
  const start = async (method, extra = {}) => {
    try { const st = await POST('/api/codex/login', { method, ...extra }); if (st.state === 'done') { toast('Codex is signed in', 'success', 2500); return again(); } drawFlow(st); poll(); }
    catch (err) { errToast(err, 'Could not start the sign-in'); }
  };
  const cancel = el('button', { class: 'btn sm', onclick: async () => { clearTimeout(CX.loginTimer); await POST('/api/codex/login/cancel', {}).catch(() => {}); again(); } }, 'Cancel');
  const drawFlow = st => {
    if (st.state !== 'waiting') {
      const key = el('input', { class: 'dlg-input', type: 'password', autocomplete: 'off', placeholder: 'sk-…', 'aria-label': 'OpenAI API key' });
      flow.replaceChildren(el('div', { class: 'dlg-sub', text: a ? 'Sign in with another account' : 'Sign in' }),
        el('div', { class: 'cx-ways' },
          el('div', { class: 'cx-way' }, el('b', { text: 'With a code' }), el('small', { class: 'muted', text: 'Recommended. Studio shows a link and a one-time code: open the link on any computer or phone, sign in to ChatGPT and type the code.' }),
            el('button', { class: 'btn sm primary', onclick: () => start('device') }, 'Get a code')),
          el('div', { class: 'cx-way' }, el('b', { text: 'With a sign-in link' }), el('small', { class: 'muted', text: 'Open the link and sign in. Your browser then lands on a page of localhost that may not open: copy that page\'s address and paste it here.' }),
            el('button', { class: 'btn sm', onclick: () => start('link') }, 'Get a link')),
          el('div', { class: 'cx-way' }, el('b', { text: 'With an API key' }), el('small', { class: 'muted', text: 'Uses your OpenAI API account (paid per use) instead of a ChatGPT plan. Studio hands the key to Codex and does not keep it.' }),
            el('div', { class: 'cx-key' }, key, el('button', { class: 'btn sm', onclick: () => start('apikey', { api_key: key.value }) }, 'Sign in')))),
        ...(st.state === 'failed' ? [el('div', { class: 'warn-text', text: 'The last sign-in failed: ' + st.error })] : []));
      return;
    }
    if (st.method === 'device') {
      flow.replaceChildren(el('div', { class: 'dlg-sub', text: 'Sign in with a code' }),
        el('ol', { class: 'cx-steps' }, el('li', {}, 'Open ', el('a', { href: st.url, target: '_blank', rel: 'noopener noreferrer', text: st.url }), ' on any computer or phone and sign in to ChatGPT.'),
          el('li', {}, 'Type this code there:')),
        el('div', { class: 'cx-code' }, el('code', { text: st.code || '' }), el('button', { class: 'btn sm', onclick: () => { navigator.clipboard?.writeText(st.code || ''); toast('Code copied', 'success', 1500); } }, 'Copy')),
        el('div', { class: 'cx-wait' }, el('span', { class: 'spinner' }), el('span', { text: 'Waiting for the sign-in…' }), el('span', { class: 'grow' }), cancel));
    } else {
      const back = el('input', { class: 'dlg-input', type: 'url', placeholder: 'http://localhost:1455/auth/callback?code=…', 'aria-label': 'The address the sign-in ended on' });
      flow.replaceChildren(el('div', { class: 'dlg-sub', text: 'Sign in with a link' }),
        el('ol', { class: 'cx-steps' }, el('li', {}, el('a', { href: st.url, target: '_blank', rel: 'noopener noreferrer', text: 'Open the sign-in page' }), ' and sign in to ChatGPT.'),
          el('li', {}, 'The browser then goes to a localhost address. If that page does not open, copy its address from the address bar and paste it here:')),
        el('div', { class: 'cx-key' }, back, el('button', { class: 'btn sm primary', onclick: async () => {
          try { const r = await POST('/api/codex/login/finish', { url: back.value }); if (r.state === 'done') { toast('Codex is signed in', 'success', 2500); again(); } }
          catch (err) { errToast(err, 'Not signed in'); }
        } }, 'Finish')),
        el('div', { class: 'cx-wait' }, el('span', { class: 'spinner' }), el('span', { text: 'Waiting for the sign-in…' }), el('span', { class: 'grow' }), cancel));
    }
  };
  drawFlow(login);
  if (login.state === 'waiting') poll();
  box.replaceChildren(acHead('Codex', 'The OpenAI account Codex uses on this machine, for every admin\'s Codex chats.'), accountCard, flow);
}
/* Account & settings → Models: which models the top bar's picker shows. Saved for the person (synced to their windows). */
// Greek: the glossary Check Greek uses (terms to avoid, words that are right) and a box to try the check.
async function paneGreek(box) {
  let g = await GET('/api/greek/glossary');
  const edit = !!g.editable;
  let terms = g.terms.map(t => ({ ...t, avoid: t.avoid.slice() })), dirty = false;
  const save = el('button', { class: 'btn sm primary', disabled: '', onclick: async () => {
    try {
      g = await POST('/api/greek/glossary', { terms: terms.filter(t => t.use || t.avoid.length), accept: words.value.split(/[\s,]+/).filter(Boolean) });
      terms = g.terms.map(t => ({ ...t, avoid: t.avoid.slice() })); dirty = false; save.disabled = true; draw(); toast('Glossary saved', 'ok', 1800);
    } catch (e) { errToast(e, 'Glossary'); }
  } }, 'Save');
  const touch = () => { dirty = true; save.disabled = false; };
  const list = el('div', { class: 'gl-terms' });
  const field = (value, ph, onv, cls = '') => el(edit ? 'input' : 'span', edit
    ? { class: 'dlg-input ' + cls, value, placeholder: ph, 'aria-label': ph, oninput: e => { onv(e.target.value); touch(); } }
    : { class: cls, text: value });
  const draw = () => list.replaceChildren(...terms.map((t, i) => el('div', { class: 'ac-card gl-term' },
    el('div', { class: 'gl-row' }, el('small', { class: 'muted gl-lbl', text: 'Use' }), field(t.use, 'the term to use, e.g. έχει δυσλεξία', v => { t.use = v; }, 'gl-use'),
      edit ? el('button', { class: 'icon-btn sm', title: 'Remove this term', 'aria-label': 'Remove this term', html: ico('trash', 14), onclick: () => { terms.splice(i, 1); touch(); draw(); } }) : null),
    el('div', { class: 'gl-row' }, el('small', { class: 'muted gl-lbl', text: 'Avoid' }),
      field(t.avoid.join('; '), 'phrases to avoid, separated by ;', v => { t.avoid = v.split(';').map(x => x.trim()).filter(Boolean); }, 'gl-avoid')),
    el('div', { class: 'gl-row' }, el('small', { class: 'muted gl-lbl', text: 'Why' }), field(t.why, 'why (the agent reads it)', v => { t.why = v; }, 'gl-why')))));
  draw();
  const words = el('textarea', { class: 'dlg-input gl-accept', rows: '3', placeholder: 'Names and terms that are right, separated by spaces or commas', 'aria-label': 'Words that are right',
    readonly: edit ? null : '', oninput: touch });
  words.value = g.accept.join(', ');
  const tryBox = el('textarea', { class: 'dlg-input gl-try', rows: '3', placeholder: 'Write or paste Greek text to check', 'aria-label': 'Text to check' });
  const tryOut = el('pre', { class: 'gl-result hidden' });
  const tryBtn = el('button', { class: 'btn sm', onclick: async () => {
    if (!tryBox.value.trim()) return;
    tryBtn.disabled = true; tryOut.classList.remove('hidden'); tryOut.textContent = 'Checking…';
    try { tryOut.textContent = (await POST('/api/greek/check', { text: tryBox.value })).result; } catch (e) { tryOut.textContent = e.message; }
    tryBtn.disabled = false;
  } }, 'Check');
  box.replaceChildren(...[
    acHead('Greek', 'Check Greek (the agent\'s greek-check tool) finds spelling with LanguageTool and grammar with Studio\'s own rules: agreement of article, adjective and noun, subject and verb, θα with the simple past, πού/πώς and «;». The glossary below is the house style: the agent is told to use these terms.',
      edit ? save : null),
    g.lexicon ? null : el('div', { class: 'ac-card warn-note', text: 'The Greek lexicon is not installed, so only the glossary, questions and punctuation are checked. See sandbox/mcp-languagetool/README.md.' }),
    el('h4', { class: 'gl-h', text: 'Terms' }), el('small', { class: 'muted', text: 'A phrase to avoid matches its other forms too (δυσλεξικό, δυσλεξικά).' }), list,
    edit ? el('button', { class: 'btn sm', onclick: () => { terms.push({ use: '', avoid: [], why: '' }); touch(); draw(); list.lastChild?.querySelector('input')?.focus(); } }, '+ Add a term') : null,
    el('h4', { class: 'gl-h', text: 'Words that are right' }), el('small', { class: 'muted', text: 'Names and terms the speller does not know, in any of their forms (Ηλέκτρα also covers Ηλέκτρας).' }), words,
    el('h4', { class: 'gl-h', text: 'Try it' }), tryBox, el('div', { class: 'gl-try-row' }, tryBtn), tryOut,
    edit ? null : el('small', { class: 'muted', text: 'Only an admin can change the glossary.' })].filter(Boolean));
}
/* The model server (packaging, 2026-11-24): what Studio is connected to, the servers found on this machine's usual ports,
   and another address with its key. Saving writes gateway and gateway_key into studio.config.json and takes effect at
   once (/api/admin/model_server). An empty key box keeps the saved key. */
async function reloadModels() {
  try { S.config = await GET('/api/config'); S.models = (await GET('/api/models')).models || []; } catch (e) { errToast(e, 'Could not load models'); }
  renderModels(); if (!S.active) renderWelcome();
}
function modelServerCard(onSaved) {
  const status = el('div', { class: 'msrv-now muted', text: 'Looking for model servers…' });
  const found = el('div', { class: 'msrv-found' });
  const addr = el('input', { class: 'dlg-input msrv-addr', type: 'url', placeholder: 'http://127.0.0.1:11434', 'aria-label': 'Model server address', spellcheck: 'false' });
  const key = el('input', { class: 'dlg-input msrv-key', type: 'password', placeholder: 'Key, only if the server asks for one', 'aria-label': 'Key', autocomplete: 'off' });
  const result = el('small', { class: 'msrv-result muted' });
  const say = (text, kind = 'muted') => { result.textContent = text; result.className = 'msrv-result ' + kind; };
  const plural = n => `${n} model${n === 1 ? '' : 's'}`;
  const describe = p => p.ok ? `${p.label} · ${plural(p.count)}${p.error ? ' · ' + p.error : ''}` : p.error;
  const body = keyValue => { const b = { gateway: addr.value.trim() }; if (keyValue !== undefined) b.key = keyValue; return b; };
  const save = async (gateway, keyValue) => {
    addr.value = gateway; say('Connecting…');
    try {
      const d = await POST('/api/admin/model_server', body(keyValue));
      toast(`Connected to ${d.server.label}: ${plural(d.models.length)}`, 'success', 4000);
      key.value = ''; await reloadModels(); if (onSaved) onSaved(); else load();
    } catch (e) { say(e.message, 'err'); }
  };
  const load = async () => {
    let d; try { d = await GET('/api/admin/model_server'); } catch (e) { status.textContent = e.message; return; }
    const c = d.current;
    addr.value = c.url;
    key.placeholder = c.has_key ? 'Key saved: leave empty to keep it' : 'Key, only if the server asks for one';
    status.className = 'msrv-now';
    status.replaceChildren(...[el('span', { class: 'msrv-dot ' + (c.ok && c.count ? 'ok' : 'err') }),
      el('span', { class: 'grow' }, el('b', { text: c.ok && c.count ? 'Connected' : 'Not connected' }), ' ', el('code', { text: c.url }),
        el('small', { class: 'muted', text: ' · ' + describe(c) })),
      c.has_key ? el('button', { class: 'btn sm', title: 'Remove the saved key', onclick: () => save(addr.value.trim(), '') }, 'Forget key') : null].filter(Boolean));
    found.replaceChildren(...(d.found.length ? [el('div', { class: 'msrv-sub', text: 'Also running on this machine' }),
      ...d.found.map(f => el('div', { class: 'msrv-row', 'data-url': f.url },
        el('span', { class: 'grow' }, el('b', { text: f.label }), ' ', el('code', { text: f.url }), el('small', { class: 'muted', text: ' · ' + plural(f.count) })),
        el('button', { class: 'btn sm primary', disabled: f.count ? null : '', onclick: () => save(f.url, '') }, 'Use this')))] : []));
  };
  const card = el('div', { class: 'ac-card msrv' }, el('b', { text: 'Model server' }), status, found,
    el('div', { class: 'msrv-sub', text: 'Another address' }),
    el('div', { class: 'msrv-form' }, addr, key),
    el('div', { class: 'msrv-acts' },
      el('button', { class: 'btn sm', onclick: async () => {
        say('Testing…');
        try { const p = await POST('/api/admin/model_server/test', body(key.value || undefined)); say(describe(p), p.ok && p.count ? 'ok' : 'err'); } catch (e) { say(e.message, 'err'); }
      } }, 'Test'),
      el('button', { class: 'btn sm primary', onclick: () => save(addr.value.trim(), key.value || undefined) }, 'Save'), result),
    el('small', { class: 'muted', text: 'Usual addresses: Ollama :11434, Strata or llama.cpp :8080 (:18080 when the Ollama Manager starts Strata), vLLM :8000, LM Studio :1234, Ollama Manager :8787. An address ending in /v1 works too.' }));
  load();
  return card;
}
/* An admin names a model for everyone (config model_names): Quartz for swift-1.5-iq3_xxs. An empty name goes back to
   the default. (Strata without the Manager, 2026-11-24.) */
async function renameModel(m, after) {
  const v = await promptBox(`Name for ${m.name}`, m.label && m.label !== m.name ? m.label : '', 'Quartz');
  if (v === null || v === undefined || v === false) return;
  try {
    await POST('/api/admin/model_names', { name: m.name, label: String(v).trim() });
    S.models = (await GET('/api/models')).models || S.models; renderModels(); if (after) after();      // the full list, Codex included
    toast(String(v).trim() ? `${m.name} is now called ${String(v).trim()}` : `${m.name} has its own name again`, 'success', 3000);
  } catch (e) { errToast(e, 'Could not rename the model'); }
}
function paneModels(box) {
  const all = S.models.map(m => m.name);
  const current = () => Array.isArray(S.prefs.models_shown) && S.prefs.models_shown.length ? S.prefs.models_shown.filter(n => all.includes(n)) : all.slice();
  let q = '';
  const save = names => {
    const keep = all.filter(n => names.includes(n));
    prefSet('models_shown', keep.length === all.length ? [] : keep);      // [] means all, so models added later show too
    renderModels(); draw();
  };
  const search = el('input', { class: 'dlg-input ac-search', type: 'search', placeholder: 'Find a model', 'aria-label': 'Find a model',
    oninput: e => { q = e.target.value.trim().toLowerCase(); draw(); } });
  const list = el('div', { class: 'mdl-groups' });
  const count = el('small', { class: 'muted mdl-count' });
  const draw = () => {
    const shown = new Set(current());
    count.textContent = `${shown.size} of ${all.length} shown in the picker`;
    list.replaceChildren(...MODEL_GROUPS.map(([kind, label]) => {
      const models = S.models.filter(m => modelGroup(m) === kind);
      if (!models.length) return null;
      const visible = models.filter(m => !q || (labelForModel(m) + ' ' + m.name).toLowerCase().includes(q));
      if (!visible.length) return null;
      const on = models.filter(m => shown.has(m.name)).length;
      const setGroup = value => { const names = new Set(current()); models.forEach(m => value ? names.add(m.name) : names.delete(m.name));
        if (!names.size) { toast('Keep at least one model in the picker', 'warn', 2500); return; } save([...names]); };
      return el('div', { class: 'ac-card mdl-group', 'data-group': kind },
        el('div', { class: 'mdl-head' }, el('b', { text: label }), el('small', { class: 'muted', text: `${on} of ${models.length}` }), el('span', { class: 'grow' }),
          el('button', { class: 'btn sm', onclick: () => setGroup(true) }, 'All'), el('button', { class: 'btn sm', onclick: () => setGroup(false) }, 'None')),
        ...visible.map(m => el('label', { class: 'mdl-row' + (m.name === S.model ? ' current' : '') },
          el('input', { type: 'checkbox', checked: shown.has(m.name) ? '' : null, 'data-model': m.name, onchange: e => {
            const names = new Set(current()); e.target.checked ? names.add(m.name) : names.delete(m.name);
            if (!names.size) { e.target.checked = true; toast('Keep at least one model in the picker', 'warn', 2500); return; }
            save([...names]);
          } }),
          el('span', { class: 'mdl-name', text: labelForModel(m) }),
          m.name === S.model ? el('span', { class: 'ac-badge ok', text: 'In use' }) : null,
          isAdmin() ? el('button', { class: 'btn sm mdl-rename', type: 'button', title: 'Give this model the name people see', onclick: e => { e.preventDefault(); renameModel(m, draw); } }, 'Rename') : null,
          m.label && m.label !== m.name ? el('small', { class: 'muted mdl-id', text: m.name }) : null)));
    }).filter(Boolean));
  };
  box.replaceChildren(...[acHead('Models', 'Choose which models the picker in the top bar shows. Saved for your account, in every browser. Hidden models still answer /model.',
      el('button', { class: 'btn sm', onclick: () => save(all) }, 'Show all')),
    isAdmin() ? modelServerCard(() => paneModels(box)) : null,
    el('div', { class: 'mdl-tools' }, search, count), list].filter(Boolean));
  draw();
}
function setToggle(btn, on) { btn.setAttribute('aria-checked', on ? 'true' : 'false'); }
function syncAuto() { if (A.run) POST('/api/agent/settings', { run: A.run, auto_apply: S.autoApply, auto_commands: S.autoCmd }).catch(e => errToast(e, 'Could not update the running agent')); }
$('#tg-apply').onclick = () => { S.autoApply = !S.autoApply; lsSet('studio.autoApply', S.autoApply ? '1' : '0'); prefSet('auto_apply', S.autoApply); setToggle($('#tg-apply'), S.autoApply); syncAuto(); };
$('#tg-cmd').onclick = () => { S.autoCmd = !S.autoCmd; lsSet('studio.autoCmd', S.autoCmd ? '1' : '0'); prefSet('auto_commands', S.autoCmd); setToggle($('#tg-cmd'), S.autoCmd); syncAuto(); };
$('#btn-toggle-agent').onclick = () => { $('#agent').classList.toggle('collapsed'); $('#split-right').classList.toggle('hidden', $('#agent').classList.contains('collapsed')); };

/* ------------------------------------------------------------------ workspace */
/* Where a folder lives, at a glance: one icon and short words, the same in the top bar, the folder list, Recent and My
   computers. server: Studio's server (your space); computer: a folder on a person's computer through its helper;
   kept: the copy Studio kept of a computer folder that was disconnected; passed: a copy someone passed to you. */
const PLACES = {
  server: { icon: 'server', short: 'on the server', long: () => 'On Studio\'s server, in your space here. Commands run here (in the sandbox when it is on).' },
  computer: { icon: 'pc', short: p => p.online ? `on ${pcName(p.machine)}` : `on ${pcName(p.machine)} · offline`,
    long: p => `On the computer ${pcName(p.machine)}${p.online ? ', connected' : ', offline (start studio-helper there)'}. Commands and terminals run on that computer; Studio keeps a copy in sync.` },
  kept: { icon: 'pc-copy', short: 'kept copy', long: p => `A copy Studio kept of a folder from ${pcName(p.machine)}, which is no longer connected. Changes stay here; the folder on ${pcName(p.machine)} is not updated.` },
  passed: { icon: 'gift', short: p => `from ${p.from}`, long: p => `A copy of a chat and its folder that ${p.from} passed to you. Changes stay in your copy.` },
};
// The person's own names for their computers (synced pref pc_names), shown wherever a computer's name appears; a folder
// named <folder>@<machine> shows as <folder>@<name>. Only the display changes: paths and the helper keep the real name.
function pcName(m) { const n = S.prefs && S.prefs.pc_names; return (m && n && typeof n === 'object' && n[m]) || m; }
function shownName(name) { const i = (name || '').lastIndexOf('@'); if (i < 1) return name; const m = name.slice(i + 1), n = pcName(m); return n === m ? name : name.slice(0, i + 1) + n; }
async function renamePc(machine) {
  const now = pcName(machine);
  const v = await promptBox(`Name for ${machine}`, now === machine ? '' : now, `e.g. DEV PC (empty: ${machine})`);
  if (v == null) return false;
  const name = v.trim().slice(0, 40), names = { ...((S.prefs && S.prefs.pc_names) || {}) };
  if (name && name !== machine) names[machine] = name; else delete names[machine];
  prefSet('pc_names', names); updateWsUI();
  toast(name && name !== machine ? `${machine} shows as "${name}"` : `${machine} shows its own name again`, 'success', 2000);
  return true;
}
function placeOf(x) { return (x && x.place) || (x && x.helper ? { kind: 'computer', machine: x.helper.machine, online: x.helper.online } : x && x.kind ? x : { kind: 'server' }); }
function placeIcon(p, size = 13) {
  const k = PLACES[p.kind] ? p.kind : 'server', def = PLACES[k];
  return el('span', { class: `place place-${k}` + (k === 'computer' && p.online ? ' on' : ''), title: def.long(p), html: ico(def.icon, size) });
}
function placeWords(p) { const def = PLACES[p.kind] || PLACES.server; return typeof def.short === 'function' ? def.short(p) : def.short; }
function updateWsUI() {
  const h = S.ws && S.ws.helper, p = placeOf(S.ws);
  $('#ws-place').replaceChildren(S.ws ? placeIcon(p, 14) : el('span', { html: ico('folder', 14) }));
  $('#ws-name').textContent = S.ws ? shownName(S.ws.name) : 'Open folder…';
  const where = $('#ws-where'); where.className = `ws-where place-${p.kind}` + (S.ws ? '' : ' hidden');
  const me = S.config?.user?.username, theirs = S.ws && S.ws.shared_by && S.ws.shared_by !== me && S.ws.owner !== me;
  where.textContent = !S.ws ? '' : theirs ? `${placeWords(p)} · shared by ${S.ws.shared_by}` : S.ws.members ? `${placeWords(p)} · shared with ${S.ws.members}` : placeWords(p);
  $('#ws-share').classList.toggle('hidden', !S.ws);
  const n = $('#ws-share-n'); n.classList.toggle('hidden', !(S.ws && S.ws.members)); n.textContent = S.ws && S.ws.members ? String(S.ws.members + 1) : '';
  $('#ws-share').title = !S.ws ? '' : theirs ? `Shared with you by ${S.ws.shared_by}: see your rights, or leave it` : S.ws.members ? `Shared with ${S.ws.members} other ${S.ws.members === 1 ? 'person' : 'people'}: manage who has it` : 'Share this folder with other people';
  $('#ws-btn').title = S.ws ? `${S.ws.name}\n${(PLACES[p.kind] || PLACES.server).long(p)}` + (h ? `\nThere: ${h.remote_path}` : '') : 'Open folder…';
  $('#tree').classList.toggle('hidden', !S.ws);
  $('#tree-empty').classList.toggle('hidden', !!S.ws);
  $$('#view-explorer .view-actions .icon-btn').forEach(b => b.disabled = !S.ws);
  updateSandboxToggle();
  renderWelcome();
  updateComposerState();
}
function updateSandboxToggle() {
  const btn = $('#tg-sandbox'), required = !!(S.config && S.config.sandbox_required), locked = required || !!(S.ws && S.ws.owner);
  const pc = !!(S.ws && S.ws.helper);       // its commands run on that computer, never in a sandbox here
  btn.classList.toggle('hidden', !S.ws || pc);
  $('#btn-sandbox-cfg').classList.toggle('hidden', !S.ws || required || pc);
  setToggle(btn, !!(S.ws && S.ws.sandbox));
  btn.disabled = locked;
  btn.title = locked ? 'Sandbox: always on here. Agent commands and terminals run in an isolated container.'
    : S.ws && S.ws.sandbox ? `Sandbox on: agent commands and terminals run in an isolated container that only sees this folder. Internet: ${{ install: 'install commands only', on: 'always', off: 'never' }[S.ws.sandbox_network] || 'never'}. Edits still change the real files in this folder.`
    : 'Sandbox off: agent commands and terminals run directly on this machine, as your user.';
}
async function saveSandbox(changes, done) {
  if (A.running) { toast('Stop the agent before changing the sandbox setting', 'warn'); return; }
  const btn = $('#tg-sandbox'), wait = toast('Preparing the sandbox. The first time can take several minutes…', 'info', 0);
  btn.disabled = true;
  try {
    const r = await POST('/api/workspace/sandbox', { ws: S.ws.id, ...changes });
    termCloseAll(true);
    S.ws = r.workspace; lsSet('studio.ws', JSON.stringify(S.ws));
    toast(done() + ' Terminals were closed.');
  } catch (e) { errToast(e, 'Could not change the sandbox setting'); }
  wait.remove();
  updateSandboxToggle();
}
$('#tg-sandbox').onclick = () => {
  if (!S.ws) return;
  const enabled = !S.ws.sandbox;
  saveSandbox({ enabled }, () => enabled ? 'Sandbox on for this folder.' : 'Sandbox off for this folder.');
};
$('#btn-sandbox-cfg').onclick = async () => {
  if (!S.ws) return;
  const image = el('input', { class: 'dlg-input', type: 'text', spellcheck: 'false', value: S.ws.sandbox_image || '' });
  const network = el('select', { class: 'dlg-input' },
    [['install', 'Install commands only'], ['on', 'Always'], ['off', 'Never']].map(([value, label]) => el('option', { value }, label)));
  network.value = S.ws.sandbox_network || 'install';
  const ports = el('input', { class: 'dlg-input', type: 'text', spellcheck: 'false', placeholder: isAdmin() ? 'For example 3000, 5173' : 'Only admins can publish ports', disabled: isAdmin() ? null : true, value: (S.ws.sandbox_ports || []).join(', ') });
  const field = (label, input, note) => el('label', { class: 'dlg-field' }, el('span', { text: label }), input, el('small', { text: note }));
  const form = el('div', {},
    field('Docker image', image, 'Clear this to use the default image, which includes Python, Node, Java, Go, Rust, Ruby, PHP and C/C++.'),
    field('Internet access', network, 'With "Install commands only", the agent\'s dependency commands get internet for that command. Terminals get internet only with "Always".'),
    field('Published ports', ports, 'Needs "Always". Servers must listen on 0.0.0.0, then open on localhost on this machine.'));
  const choice = await dialog({ title: 'Sandbox settings for ' + S.ws.name, body: form, buttons: [{ label: 'Cancel' }, { label: 'Save', value: 'ok', kind: 'primary' }] });
  if (choice !== 'ok') return;
  const portList = ports.value.split(/[\s,]+/).filter(Boolean).map(Number);
  if (portList.some(p => !Number.isInteger(p) || p < 1 || p > 65535)) { toast('Ports must be numbers from 1 to 65535', 'warn'); return; }
  saveSandbox({ image: image.value.trim(), network: network.value, ...(isAdmin() ? { ports: portList } : {}) }, () => 'Sandbox settings saved.');
};
async function setWorkspace(ws, { silent = false, restoring = false } = {}) {
  if (S.ws && S.ws.id === ws.id) { S.ws = ws; updateSandboxToggle(); return true; }
  if (!(await closeAllTabs())) return false;
  termCloseAll(true); agentReset();     // the terminals stay on the server, because another window may be using them
  S.ws = ws; lsSet('studio.ws', JSON.stringify(ws));
  $('#ex-find-q').value = ''; EF.seq++; $('#ex-find').classList.add('hidden'); $('#ex-found').classList.add('hidden'); treeEl.classList.remove('hidden');   // the find bar belongs to the old folder
  try { sessionStorage.setItem('studio.windowFolder', ws.path); } catch { /* ignore */ }
  if (!restoring || !S.prefs.last_ws) prefSet('last_ws', ws.path);     // reloading an old window does not make its folder the latest
  quick.cache = null; resetSearch(); updateWsUI();
  T.expanded = new Set(); T.selected = ''; T.selectedType = 'dir';
  await loadTree();
  agentInit();
  gitRefresh();
  termSync();
  if (!silent) restoreTabs();
  return true;
}
$('#ws-btn').onclick = openFolderDialog;
$('#empty-open').onclick = openFolderDialog;

async function openFolderDialog() {
  // One place to pick a project: it starts in your space (admins: the folder around the open project), never shows the
  // server's own paths, and says for every folder where it lives (placeIcon). A row goes inside; its Open opens it.
  let cur = '', labels = [];
  const crumbs = el('nav', { class: 'crumbs', 'aria-label': 'Where you are' });
  const list = el('div', { class: 'dir-list', role: 'list' });
  const recentBox = el('div', { class: 'of-recent' });
  const openBtn = el('button', { class: 'btn primary' }, 'Open this folder');
  const cancel = el('button', { class: 'btn' }, 'Cancel');
  const newBtn = el('button', { class: 'btn dlg-foot-left', disabled: true, html: ico('folder-plus', 14) + '<span>New folder</span>' });
  const pcBox = el('div', { class: 'of-pcs' });
  const legend = el('div', { class: 'of-legend' }, ...['server', 'computer', 'kept', 'passed'].map(k => el('span', {}, placeIcon({ kind: k, machine: 'a computer', from: 'someone', online: true }, 12),
    { server: 'On the server', computer: 'On your computer', kept: 'Kept copy', passed: 'Passed to you' }[k])));
  const sharedBox = el('div', { class: 'of-shared' });
  // Sections as tabs, so nothing waits below the fold: each scrolls inside a window of a fixed size.
  const panes = { space: el('div', { class: 'of-pane' }, crumbs, list, legend), shared: el('div', { class: 'of-pane' }, sharedBox),
    recent: el('div', { class: 'of-pane' }, recentBox), pcs: el('div', { class: 'of-pane' }, pcBox) };
  const tabNames = { space: 'Your space', shared: 'Shared with me', recent: 'Recent', pcs: 'My computers' };
  const tabs = el('div', { class: 'of-tabs', role: 'tablist' });
  const showTab = key => {
    lsSet('studio.folderTab', key);
    tabs.querySelectorAll('.of-tab').forEach(b => { const on = b.dataset.tab === key; b.classList.toggle('on', on); b.setAttribute('aria-selected', String(on)); });
    for (const [k, pane] of Object.entries(panes)) pane.classList.toggle('hidden', k !== key);
    newBtn.classList.toggle('hidden', key !== 'space'); openBtn.classList.toggle('hidden', key !== 'space');
  };
  for (const key of Object.keys(panes)) tabs.append(el('button', { class: 'of-tab', role: 'tab', 'data-tab': key, onclick: () => showTab(key) }, el('span', { text: tabNames[key] }), el('span', { class: 'of-count' })));
  const countTab = (key, n) => { const c = tabs.querySelector(`[data-tab="${key}"] .of-count`); if (c) c.textContent = n ? String(n) : ''; };
  const body = el('div', { class: 'dlg-body of-body' }, tabs, ...Object.values(panes));
  const dlg = el('div', { class: 'dialog of-dialog', role: 'dialog', 'aria-label': 'Open folder' }, el('div', { class: 'dlg-head', text: 'Open folder' }), body, el('div', { class: 'dlg-foot' }, newBtn, cancel, openBtn));
  const entry = openOverlay(dlg);
  computersList(pcBox, path => doOpen(path), h => { closeOverlay(entry); connectComputer(h); }).then(() => countTab('pcs', pcBox.querySelectorAll('.pc-item').length));
  cancel.onclick = () => closeOverlay(entry);
  showTab(['space', 'shared', 'recent', 'pcs'].includes(lsGet('studio.folderTab')) ? lsGet('studio.folderTab') : 'space');
  const label = path => { const r = labels.filter(x => path === x.path || path.startsWith(x.path.replace(/\/$/, '') + '/')).sort((a, b) => b.path.length - a.path.length)[0];
    return r ? [r.label, ...path.slice(r.path.replace(/\/$/, '').length).split('/').filter(Boolean)] : [basename(path)]; };
  newBtn.onclick = async () => {
    const parent = cur;
    const choice = await dialog({ title: 'New folder in ' + label(parent).join(' › '), input: { placeholder: 'Folder name' }, buttons: [{ label: 'Cancel' }, { label: 'Create', value: 'ok', kind: 'primary' }] });
    if (!choice || !choice.value.trim()) return;
    try { const r = await POST('/api/workspaces/mkdir', { parent, name: choice.value }); browse(r.path); } catch (e) { errToast(e, 'Cannot create folder'); }
  };
  const doOpen = async path => {
    try { const r = await POST('/api/workspace/open', { path }); closeOverlay(entry); await setWorkspace(r.workspace); }
    catch (e) { errToast(e, 'Cannot open folder'); }
  };
  openBtn.onclick = () => cur && doOpen(cur);
  async function browse(path, fallback = true) {
    list.replaceChildren(el('div', { class: 'dir-note', text: 'Loading…' }));
    let d;
    try { d = await GET('/api/workspaces/browse', { path }); }
    catch (e) { if (fallback && path) return browse('', false); list.replaceChildren(el('div', { class: 'dir-note', text: e.message })); return; }
    labels = d.root_labels || (d.roots || []).map(r => ({ path: r, label: basename(r) }));
    if (!d.path && labels.length === 1) return browse(labels[0].path, false);       // one space (a person's own): go straight in
    cur = d.path || '';
    openBtn.disabled = !cur; newBtn.disabled = !cur;
    const parts = cur ? label(cur) : [];
    openBtn.textContent = cur ? `Open «${parts[parts.length - 1]}»` : 'Open this folder';
    // where you are: the space's name, then the folders inside it; admins with several spaces also get "All spaces"
    crumbs.replaceChildren();
    const crumb = (text, target, here) => { if (crumbs.children.length) crumbs.append(el('span', { class: 'sep', text: '›' }));
      crumbs.append(el('button', { class: 'crumb' + (here ? ' here' : ''), text, disabled: here ? true : null, 'aria-current': here ? 'location' : null, onclick: () => browse(target) })); };
    if (labels.length > 1) crumb('All spaces', '', !cur);
    if (cur) {
      const root = labels.filter(x => cur === x.path || cur.startsWith(x.path.replace(/\/$/, '') + '/')).sort((a, b) => b.path.length - a.path.length)[0];
      let acc = root ? root.path.replace(/\/$/, '') : '';
      crumb(root ? root.label : cur, acc || cur, root && acc === cur.replace(/\/$/, ''));
      if (root) for (const seg of cur.slice(acc.length).split('/').filter(Boolean)) { acc += '/' + seg; crumb(seg, acc, acc === cur); }
    }
    list.replaceChildren();
    if (cur && d.parent !== null && d.parent !== undefined && (d.parent || labels.length > 1)) {
      list.append(el('div', { class: 'dir-item dir-up', role: 'listitem', onclick: () => browse(d.parent) }, el('span', { class: 'place', html: ico('arrow-up', 14) }), el('span', { class: 'nm', text: 'Back to ' + (d.parent ? label(d.parent).slice(-1)[0] : 'all spaces') })));
    }
    if (!d.dirs || !d.dirs.length) list.append(el('div', { class: 'dir-note', text: cur ? 'No folders inside. Make one with New folder, or open this one.' : 'No folders yet' }));
    for (const dd of d.dirs || []) {
      const place = dd.kind ? dd : { kind: 'server' };
      list.append(el('div', { class: 'dir-item', role: 'listitem', tabindex: '0', title: (PLACES[place.kind] || PLACES.server).long(place), onclick: () => browse(dd.path),
        onkeydown: e => { if (e.key === 'Enter') browse(dd.path); } },
        placeIcon(place, 15), el('span', { class: 'nm', text: shownName(dd.name) }), el('span', { class: `of-where place-${place.kind}`, text: placeWords(place) }),
        el('button', { class: 'btn sm go', title: 'Open ' + dd.name + ' as the project', onclick: ev => { ev.stopPropagation(); doOpen(dd.path); } }, 'Open')));
    }
  }
  const start = S.ws && S.ws.path;
  browse(start ? (labels.some(x => x.path === start) ? start : dirname(start)) : '');
  GET('/api/workspace/shared').then(r => {
    const mine = r.with_me || [];
    countTab('shared', mine.length);
    if (!mine.length) { sharedBox.replaceChildren(el('div', { class: 'dir-note', text: 'Nobody has shared a folder with you yet. When someone does, it shows here, and you work on the same files as they do.' })); return; }
    sharedBox.replaceChildren(...mine.map(w => el('div', { class: 'recent-item', role: 'button', tabindex: '0', onclick: () => doOpen(w.path), onkeydown: e => { if (e.key === 'Enter') doOpen(w.path); } },
      placeIcon(placeOf(w)), el('span', { class: 'nm', text: shownName(w.name) }),
      el('span', { class: 'pth', text: `by ${w.shared_by} · ` + (Object.values(w.rights || {}).every(Boolean) ? 'co-owner' : SHARE_RIGHTS.filter(([k]) => (w.rights || {})[k]).map(([, l]) => l.toLowerCase()).join(', ') || 'can look') }))));
  }).catch(() => {});
  GET('/api/workspace/recent').then(r => {
    recentBox.replaceChildren();
    const ws = (r.workspaces || []).filter(w => !S.ws || w.id !== S.ws.id).slice(0, 6);
    if (!ws.length) recentBox.append(el('div', { class: 'dir-note', text: 'No recent folders' }));
    for (const w of ws) recentBox.append(el('div', { class: 'recent-item', role: 'button', tabindex: '0', onclick: () => doOpen(w.path), onkeydown: e => { if (e.key === 'Enter') doOpen(w.path); } },
      placeIcon(placeOf(w)), el('span', { class: 'nm', text: shownName(w.name) }), el('span', { class: 'pth', text: w.where || '' })));
  }).catch(() => recentBox.replaceChildren(el('div', { class: 'dir-note', text: 'Could not load recent folders' })));
}

/* ---- Sharing a folder (shared projects): the same files for several people, with switches per person. */
const SHARE_RIGHTS = [['files', 'Change files', 'Edit, create, rename and move files and folders (also through Git, the wave and video editors)'],
  ['agent', 'Run the agent', 'Send messages that make the agent work in this folder'], ['terminal', 'Terminal', 'Open terminals in this folder'],
  ['delete', 'Delete', 'Delete files and folders'], ['share', 'Manage people', 'Add and remove people and change their rights (with everything on: a co-owner)']];
async function shareDialog() {
  if (!S.ws) return;
  const ws = S.ws, body = el('div', { class: 'dlg-body sh-body' });
  const close = el('button', { class: 'btn' }, 'Close');
  const dlg = el('div', { class: 'dialog sh-dialog', role: 'dialog', 'aria-label': 'Share ' + ws.name }, el('div', { class: 'dlg-head', text: 'Share «' + ws.name + '»' }), body, el('div', { class: 'dlg-foot' }, close));
  const entry = openOverlay(dlg);
  close.onclick = () => closeOverlay(entry);
  let people = [];
  try { people = (await GET('/api/share/people')).people || []; } catch { /* listed without */ }
  const draw = d => {
    if (S.ws && S.ws.id === ws.id && (S.ws.members !== d.members.length || S.ws.shared_by !== d.owner)) {    // this window's top bar
      S.ws.members = d.members.length; S.ws.shared_by = d.owner; updateWsUI();
    }
    const manage = d.rights.share, owner = d.owner || S.config?.user?.username, place = d.place || { kind: 'server' };
    const notes = [];
    if (place.kind === 'computer') notes.push(`This folder is on ${pcName(place.machine)}: what others run with the agent or a terminal runs on that computer.`);
    else if (!d.sandbox) notes.push('Its Sandbox is off, so commands run on the server itself: people who are not admins can change files here but cannot run the agent or a terminal until the Sandbox is on.');
    const row = m => {
      const sw = ([k, label, tip]) => el('label', { class: 'ac-right', title: tip }, el('input', { type: 'checkbox', role: 'switch', checked: m.rights[k] ? '' : null, disabled: manage ? null : '', 'data-right': k,
        onchange: async e => { try { draw(await POST('/api/share/set', { ws: ws.id, username: m.username, rights: { [k]: e.target.checked } })); } catch (err) { e.target.checked = !e.target.checked; errToast(err, 'Not changed'); } } }),
        el('span', { class: 'ac-knob', 'aria-hidden': 'true' }), el('span', { text: label }));
      const all = SHARE_RIGHTS.every(([k]) => m.rights[k]);
      return el('div', { class: 'sh-person', 'data-user': m.username },
        el('div', { class: 'sh-who' }, avatar(m.display_name || m.username, 28), el('div', { class: 'grow' }, el('b', { text: m.display_name || m.username }), m.display_name ? el('small', { class: 'muted', text: ' @' + m.username }) : null,
          all ? el('span', { class: 'ac-badge ok', text: 'Co-owner' }) : null, m.gone ? el('span', { class: 'ac-badge bad', text: 'No longer here' }) : null),
          manage ? el('button', { class: 'btn sm', title: all ? 'Turn every right off except changing files' : 'Turn every right on: a co-owner', onclick: async () => {
            const rights = Object.fromEntries(SHARE_RIGHTS.map(([k]) => [k, all ? k === 'files' : true]));
            try { draw(await POST('/api/share/set', { ws: ws.id, username: m.username, rights })); } catch (err) { errToast(err, 'Not changed'); }
          } }, all ? 'Less' : 'Co-owner') : null,
          manage || m.username === d.me ? el('button', { class: 'icon-btn sm', title: m.username === d.me ? 'Leave this folder' : 'Take ' + m.username + ' out of this folder', 'aria-label': 'Remove ' + m.username, html: ico('x', 14), onclick: async () => {
            const leaving = m.username === d.me;
            const yes = await dialog({ title: leaving ? `Leave ${ws.name}?` : `Take ${m.username} out of ${ws.name}?`, body: leaving ? 'You stop seeing this folder and its shared chats. Its files stay as they are.' : 'They stop seeing this folder and its shared chats, and their terminals in it close. The files stay as they are.',
              buttons: [{ label: 'Cancel' }, { label: leaving ? 'Leave' : 'Take out', value: 'yes', kind: 'danger' }] });
            if (yes !== 'yes') return;
            try { const r = await POST('/api/share/remove', { ws: ws.id, username: m.username }); if (r.left) { closeOverlay(entry); toast('You left ' + ws.name, 'success', 2500); location.reload(); return; } draw(r); }
            catch (err) { errToast(err, 'Not changed'); }
          } }) : null),
        el('div', { class: 'sh-rights' }, ...SHARE_RIGHTS.map(sw)));
    };
    const taken = new Set([owner, ...d.members.map(m => m.username)]);
    const pick = el('select', { class: 'dlg-input', 'aria-label': 'Person to add' }, el('option', { value: '', text: 'Choose a person…' }),
      ...people.filter(p => !taken.has(p.username)).map(p => el('option', { value: p.username, text: p.display_name ? `${p.display_name} (@${p.username})` : p.username })));
    const add = el('button', { class: 'btn sm primary', onclick: async () => {
      if (!pick.value) return;
      try { draw(await POST('/api/share/set', { ws: ws.id, username: pick.value, rights: { files: true, agent: true } })); toast('Shared', 'success', 2000); } catch (err) { errToast(err, 'Not shared'); }
    } }, 'Add');
    body.replaceChildren(
      el('div', { class: 'sh-owner' }, placeIcon(place, 15), el('span', {}, 'Owner: ', el('b', { text: d.owner_name || owner || 'you' })), el('span', { class: 'muted', text: ' · everyone works on the same files, not copies' })),
      ...notes.map(t => el('div', { class: 'warn-text sh-note', text: t })),
      d.members.length ? el('div', { class: 'sh-people' }, ...d.members.map(row)) : el('div', { class: 'dir-note', text: 'Only the owner has this folder.' }),
      manage ? el('div', { class: 'sh-add' }, pick, add) : el('small', { class: 'muted', text: 'Only people who may manage people can add someone.' }),
      el('small', { class: 'muted', text: 'Chats stay private: share one from the chat list (the people icon) to let others read and carry it on. One agent works in a folder at a time.' }));
  };
  try { draw(await GET('/api/share', { ws: ws.id })); } catch (err) { body.replaceChildren(el('div', { class: 'dir-note', text: err.message })); }
}
$('#ws-share').onclick = () => shareDialog();

/* ---- folders on the person's own computer, through studio-helper */
function helperWhen(h) { return h.online ? 'connected' : h.last_seen ? 'offline, last seen ' + ago(h.last_seen) : 'offline'; }
async function computersList(box, open, connect) {
  box.classList.add('pc-list');          // a container: its rows fold into lines when it is narrow (phones)
  let d; try { d = await GET('/api/helpers'); } catch { return; }
  if (!d.allowed && !d.helpers.length && !(d.kept || []).length) return;
  const machines = [...new Set(d.helpers.map(h => h.machine))];
  const head = m => { const hs = d.helpers.filter(h => h.machine === m), on = hs.some(h => h.online), named = pcName(m) !== m;
    return el('div', { class: 'pc-machine', 'data-machine': m }, placeIcon({ kind: 'computer', machine: m, online: on }, 15),
      el('b', { class: 'pc-mname', text: pcName(m) }),
      el('span', { class: 'pc-msub' }, named ? el('span', { class: 'pc-mreal', text: m }) : null,
        el('span', { class: 'pc-mstate', text: `${hs[0].os || ''}${on ? ' · connected' : ' · offline'}` })),
      el('button', { class: 'btn sm pc-rename', title: `Rename ${pcName(m)} (only how Studio shows it)`, 'aria-label': `Rename ${pcName(m)}`, html: ico('edit', 12) + '<span>Rename</span>',
        onclick: async ev => { ev.stopPropagation(); if (await renamePc(m)) computersList(box, open, connect); } })); };
  const rows = machines.flatMap(m => [head(m), ...d.helpers.filter(h => h.machine === m).map(h => el('div', { class: 'recent-item pc-item', onclick: () => open(h.path) },
    placeIcon({ kind: 'computer', machine: h.machine, online: h.online }, 14),
    el('span', { class: 'nm', text: shownName(h.folder) }), el('span', { class: 'pth', text: `${h.remote_path} · ${helperWhen(h)} · helper ${h.version || 'unknown'}`, title: `${pcName(h.machine)} (${h.machine}, ${h.os})` }),
    h.outdated ? el('button', { class: 'pc-old', title: `This computer runs helper ${h.version || 'before 0.04'}; ${d.latest} is available. Shows how to update it.`, onclick: ev => { ev.stopPropagation(); connect(h); } }, el('span', { html: ico('download', 11) }), `Update to ${d.latest}`) : null,
    el('button', { class: 'btn sm', title: 'Disconnect this folder. Studio keeps its copy here as an ordinary folder.', onclick: async ev => {
      ev.stopPropagation();
      const yes = await dialog({ title: `Disconnect ${shownName(h.folder)}?`, body: `Its helper on ${pcName(h.machine)} stops sharing it, and the folder there stays as it is. Studio has a copy of it on the server: keep it here as a folder of its own, or delete it (it can be brought back for ${30} days).`,
        buttons: [{ label: 'Cancel' }, { label: 'Disconnect, delete the copy', value: 'delete', kind: 'danger' }, { label: 'Disconnect, keep the copy', value: 'keep', kind: 'primary' }] });
      if (yes !== 'keep' && yes !== 'delete') return;
      try { await POST('/api/helpers/remove', { id: h.id, delete_copy: yes === 'delete' }); toast(yes === 'delete' ? 'Disconnected; the copy on the server is deleted' : 'Disconnected; the copy stays on the server', 'success', 3000); computersList(box, open, connect); }
      catch (e) { errToast(e, 'Could not disconnect'); }
    } }, 'Disconnect')))]);
  const kept = (d.kept || []).map(k => el('div', { class: 'recent-item pc-item kept', onclick: () => open(k.path) },
    placeIcon({ kind: 'kept', machine: k.machine }, 14), el('span', { class: 'nm', text: shownName(k.folder) }),
    el('span', { class: 'pth', text: `copy kept from ${pcName(k.machine)} · not connected` }),
    el('button', { class: 'btn sm', title: 'Delete this copy from the server. The folder on the computer is not touched.', onclick: async ev => {
      ev.stopPropagation();
      const yes = await dialog({ title: `Delete the copy ${shownName(k.folder)}?`, body: `It is deleted from the server; the folder on ${pcName(k.machine)} is not touched. An admin can bring it back for 30 days.`, buttons: [{ label: 'Cancel' }, { label: 'Delete the copy', value: 'yes', kind: 'danger' }] });
      if (yes !== 'yes') return;
      try { await POST('/api/folders/delete_copy', { path: k.path }); toast('The copy is deleted', 'success', 2500); if (S.ws && S.ws.path === k.path) location.reload(); computersList(box, open, connect); }
      catch (e) { errToast(e, 'Could not delete the copy'); }
    } }, 'Delete copy')));
  box.replaceChildren(...[el('div', { class: 'dlg-sub', text: 'My computers' }), ...rows,
    !rows.length ? el('div', { class: 'dir-note', text: 'No folder from your computers is connected now.' }) : null,
    ...(kept.length ? [el('div', { class: 'dlg-sub of-kept-head', text: 'Copies kept from disconnected computers' }),
      el('small', { class: 'muted', text: 'These were connected once. Studio kept their copies here; the folders on those computers are no longer updated. Connect the folder again to work on it there.' }), ...kept] : []),
    d.allowed ? el('button', { class: 'btn sm pc-connect', onclick: () => connect() }, el('span', { html: ico('pc', 13) }), 'Connect a folder on my computer') : null].filter(Boolean));
}
// Connect a folder on a computer, or (with `update`, one of /api/helpers' rows) bring that computer's helper up to date.
// Three cards side by side (one column on phones): download, start it, and pair with the code or, for an update, nothing
// to do, since a newer helper makes the running older one quit and takes over its folders (0.07). The bottom line follows
// /api/helpers until the folder appears or every folder of that computer runs the newest helper.
async function connectComputer(update) {
  let pair = null, before;
  try { [pair, before] = await Promise.all([update ? null : POST('/api/helpers/pair', {}), GET('/api/helpers')]); } catch (e) { errToast(e, update ? 'Cannot update the helper' : 'Cannot connect a computer'); return; }
  const known = new Set(before.helpers.map(h => h.id)), server = linkBase().replace(/\/$/, ''), latest = before.latest;
  const machine = update && update.machine, onMachine = list => list.filter(h => h.machine === machine);
  let os = update ? (/linux/i.test(update.os || '') ? 'linux' : 'windows') : (/Linux|X11/.test(navigator.userAgent) && !/Android/.test(navigator.userAgent) ? 'linux' : 'windows');
  const left = el('span', { class: 'pc-clock' });       // not room-clock: the waiting room's ticker rewrites those
  const codeText = el('span', { class: 'pc-code-text' }), linuxCmd = el('code', { class: 'pc-cmd' });
  const waiting = () => update
    ? [el('span', { class: 'dash-dot', 'data-state': 'work' }), el('span', {}, `Waiting for ${pcName(machine)} to run helper ${latest}…`)]
    : [el('span', { class: 'dash-dot', 'data-state': 'work' }), el('span', {}, 'Waiting for the helper to connect… The code works once.')];
  const status = el('div', { class: 'pc-wait' }, ...waiting());
  let until = 0, found = null, timer;
  const showCode = p => {
    pair = p; until = Date.now() + p.expires_in * 1000;
    codeText.textContent = p.code.slice(0, 4) + '-' + p.code.slice(4);
    linuxCmd.textContent = `./studio-helper pair -server ${server} -code ${p.code} -folder ~/your-project && ./studio-helper run`;
    left.textContent = fmtLeft(p.expires_in);
  };
  // A new code replaces the old one, which stops working: only the newest is ever valid.
  const renew = async () => {
    try { showCode(await POST('/api/helpers/pair', {})); if (!found) status.replaceChildren(...waiting()); toast('New pairing code', 'success', 1500); }
    catch (e) { errToast(e, 'Could not make a new code'); }
  };
  const newBtn = () => el('button', { class: 'btn sm pc-renew', title: 'Make a new code; the old one stops working', onclick: renew }, el('span', { html: ico('refresh', 13) }), 'New code');
  const OS = { windows: 'Windows', linux: 'Linux' };
  const dl = (name, main) => before.downloads[name]
    ? el('a', { class: main ? 'btn primary pc-dl' : 'link-btn pc-dl-other', href: `/api/helpers/download?os=${name}`, download: '' },
      main ? el('span', { html: ico('download', 14) }) : null, main ? `Download for ${OS[name]}` : `For ${OS[name]} instead`)
    : el('span', { class: 'dir-note', text: `${OS[name]}: not built yet (tools/helper/build.sh)` });
  const card = (n, title, ...kids) => el('section', { class: 'pc-card' }, el('div', { class: 'pc-num', text: String(n) }), el('h3', { text: title }), ...kids);
  const cards = el('div', { class: 'pc-cards' });
  const osSeg = el('div', { class: 'pc-os', role: 'tablist', 'aria-label': 'The computer\'s system' });
  const draw = () => {
    osSeg.replaceChildren(...Object.keys(OS).map(k => el('button', { class: 'pc-os-btn' + (k === os ? ' on' : ''), role: 'tab', 'aria-selected': String(k === os), 'data-os': k,
      onclick: () => { os = k; draw(); } }, el('span', { html: ico('pc', 13) }), OS[k])));
    const other = os === 'windows' ? 'linux' : 'windows';
    const start = os === 'windows'
      ? [el('p', {}, 'Double-click ', el('code', { text: 'studio-helper.exe' }), ' in Downloads.'),
         el('p', { class: 'pc-hint' }, 'If Windows warns about an unknown app: More info → Run anyway.')]
      : [el('p', {}, 'In a terminal, in the folder you saved it to:'), el('code', { class: 'pc-cmd', text: 'chmod +x studio-helper && ./studio-helper' })];
    const third = update
      ? card(3, 'That\'s it', el('p', {}, update.version && update.version >= '0.06'
          ? `The new helper asks the running ${update.version} to quit and takes over its folders. They stay connected; no code is needed.`
          : `Quit the old helper first: right-click its tray icon by the clock and choose Quit. Then start the new one; its folders stay connected.`),
          el('p', { class: 'pc-hint' }, 'If the old window opens instead, quit the old helper from its tray icon and start the new one again.'))
      : card(3, 'Pair with this code',
          el('div', { class: 'pc-code', title: 'Pairing code' }, codeText,
            el('button', { class: 'icon-btn', title: 'Copy the code', 'aria-label': 'Copy the code', html: ico('copy', 14), onclick: () => copyText(pair.code).then(() => toast('Code copied', 'success', 1500)) })),
          el('div', { class: 'pc-code-foot' }, el('span', { class: 'muted' }, 'Valid for ', left), newBtn()),
          el('p', { class: 'pc-hint' }, 'In the helper\'s window: Studio\'s address ', el('code', { text: server }), ', this code, and the folder (Browse…), then Connect.'));
    cards.replaceChildren(
      card(1, 'Download', el('p', {}, update ? `Helper ${latest} for ${pcName(machine)}.` : `The helper for this computer, version ${latest}.`), dl(os, true), dl(other, false)),
      card(2, 'Start it', ...start, el('p', { class: 'pc-hint' }, 'It runs quietly with an icon in the tray by the clock, and starts by itself when you sign in.')),
      third);
  };
  const folders = update ? el('div', { class: 'pc-upd-list' }, ...onMachine(before.helpers).map(h => el('div', { class: 'pc-upd-row', 'data-id': h.id },
    placeIcon({ kind: 'computer', machine: h.machine, online: h.online }, 13), el('span', { class: 'nm', text: shownName(h.folder) }),
    el('span', { class: 'pc-upd-ver', text: `helper ${h.version || 'unknown'}` })))) : null;
  const intro = update
    ? el('p', { class: 'pc-intro' }, el('b', { text: pcName(machine) }), ` runs helper ${update.version || 'older than 0.04'}; ${latest} is ready. These folders update with it:`)
    : el('p', { class: 'pc-intro' }, 'Work on a folder that stays on your own computer: Studio keeps a copy in step, and the agent\'s commands and your terminals run there.');
  const body = el('div', { class: 'pc-steps pc-flow' + (update ? ' pc-update' : '') },
    intro, folders, osSeg, cards, status,
    el('details', { class: 'pc-more' }, el('summary', { text: 'More about the helper' }),
      update ? null : el('p', {}, 'From a terminal instead (Linux): ', linuxCmd),
      el('p', {}, 'If it asks whether to trust Studio\'s certificate, say yes only if the address it shows is this Studio on your own network. The helper shares only the folders you connect. Folders such as node_modules and .venv are not copied. Close the helper to stop sharing; Disconnect in its window or here ends it for good.')));
  draw();
  if (pair) showCode(pair);
  const tick = async () => {
    if (!update) { const secs = Math.max(0, Math.round((until - Date.now()) / 1000)); left.textContent = fmtLeft(secs); if (!secs && !found && !status.querySelector('.pc-renew')) status.replaceChildren(el('span', { class: 'warn-text', text: 'The code has expired. ' }), newBtn()); }
    if (found) return;
    try {
      const now = await GET('/api/helpers');
      if (update) {
        const mine = onMachine(now.helpers);
        for (const h of mine) { const v = folders.querySelector(`[data-id="${h.id}"] .pc-upd-ver`); if (v) { v.textContent = `helper ${h.version || 'unknown'}`; v.classList.toggle('ok', !h.outdated); } }
        if (mine.length && mine.every(h => !h.outdated && h.online)) {
          found = true;
          status.replaceChildren(el('span', { class: 'dash-dot', 'data-state': 'idle' }), el('span', {}, el('b', { text: 'Updated: ' }), `${pcName(machine)} runs helper ${latest}.`));
        }
        return;
      }
      const fresh = now.helpers.find(h => !known.has(h.id));
      if (fresh) {
        found = fresh;
        status.replaceChildren(el('span', { class: 'dash-dot', 'data-state': 'idle' }), el('span', {}, el('b', { text: 'Connected: ' }), `${fresh.remote_path} on ${pcName(fresh.machine)} is "${shownName(fresh.folder)}" here. `),
          el('button', { class: 'btn sm primary', onclick: async () => { const r = await POST('/api/workspace/open', { path: fresh.path }); closeOverlay(entryRef.entry); await setWorkspace(r.workspace); } }, 'Open it'));
      }
    } catch { /* try again */ }
  };
  const entryRef = {};
  const shown = dialog({ title: update ? `Update the helper on ${pcName(machine)}` : 'Connect a folder on your computer', body, small: false, buttons: [{ label: 'Close', value: false }] });
  entryRef.entry = overlayStack[overlayStack.length - 1];     // the dialog just opened
  body.closest('.dialog')?.classList.add('pc-dialog');
  tick(); timer = setInterval(tick, 2000);
  await shown; clearInterval(timer);
}
// A folder's computer can come and go, so its state is checked now and then while it is open.
setInterval(async () => {
  if (!S.ws || !S.ws.helper || document.hidden) return;
  try {
    const d = await GET('/api/helpers'); const h = d.helpers.find(x => x.id === S.ws.helper.id);
    if (h && h.online !== S.ws.helper.online) { S.ws.helper = { ...S.ws.helper, online: h.online, last_seen: h.last_seen }; updateWsUI(); toast(h.online ? `${pcName(h.machine)} is connected again` : `${pcName(h.machine)} went offline: commands wait for it`, h.online ? 'success' : 'warn', 4000); }
  } catch { /* later */ }
}, 15000);

/* ------------------------------------------------------------------ explorer tree */
// selected/selectedType: the row with focus (where New File goes, what F2 renames); sel: every chosen row (Ctrl or
// Shift + click), which a drag moves and Download and Delete act on; anchor: where a Shift range starts.
const T = { expanded: new Set(), selected: '', selectedType: 'dir', nodes: new Map(), sel: new Set(), anchor: '', dragging: null };
const touchOnly = () => matchMedia('(pointer: coarse)').matches && !matchMedia('(any-pointer: fine)').matches;
const treeEl = $('#tree');

async function loadTree() {
  T.nodes = new Map(); treeEl.replaceChildren();
  if (!S.ws) return;
  T.nodes.set('', { children: treeEl, depth: -1, type: 'dir' });
  await renderChildren('');
}
async function renderChildren(path) {
  const node = T.nodes.get(path); if (!node) return;
  if (!node.children.childElementCount) node.children.append(skeleton(path ? 2 : 6, 'sk-tree'));
  let d;
  try { d = await GET('/api/files/tree', { ws: S.ws.id, path }); }
  catch (e) { node.children.replaceChildren(el('div', { class: 't-empty', style: `padding-left:${12 + (node.depth + 1) * 12}px`, text: e.message })); return; }
  const frag = document.createDocumentFragment(); const pending = [];
  // drop stale node map entries below this path
  for (const k of Array.from(T.nodes.keys())) if (k !== path && (path === '' ? k !== '' : k.startsWith(path + '/'))) T.nodes.delete(k);
  const entries = d.entries || [];
  if (!entries.length && path === '') frag.append(el('div', { class: 't-empty', style: 'padding-left:22px', text: 'This folder is empty' }));
  for (const e of entries) pending.push(addNode(e, frag, node.depth + 1));
  node.children.replaceChildren(frag);
  await Promise.all(pending);
}
function addNode(e, container, depth) {
  const isDir = e.type === 'dir';
  const row = el('div', { class: 't-row', role: 'treeitem', 'data-path': e.path, 'data-type': e.type, style: `padding-left:${6 + depth * 12}px` },
    el('span', { class: 't-chev', html: isDir ? ico('chevron-right', 12) : '' }),
    el('span', { class: 't-ico', html: isDir ? ico('folder', 15, 'style="color:#c09553"') : fileIco(e.name) }),
    el('span', { class: 't-name', text: e.name, title: e.path }));
  const kids = el('div', { class: 't-children hidden' });
  const wrap = el('div', { class: 't-node' }, row, isDir ? kids : null);
  container.append(wrap);
  if (isDir) T.nodes.set(e.path, { children: kids, depth, type: 'dir', row });
  if (T.sel.has(e.path)) row.classList.add('selected');
  // One click chooses (and opens or closes a folder), a double click opens a file; Ctrl or Cmd adds and removes rows,
  // Shift chooses the rows between. A tap on a touch screen opens at once, since there is no double tap.
  row.addEventListener('mousedown', ev => { if (ev.shiftKey) ev.preventDefault(); });   // no text selection on Shift+click
  row.addEventListener('click', ev => {
    if (ev.ctrlKey || ev.metaKey) {
      if (T.sel.has(e.path)) T.sel.delete(e.path); else T.sel.add(e.path);
      T.selected = e.path; T.selectedType = e.type; T.anchor = e.path; paintSel();
    } else if (ev.shiftKey && T.anchor) {
      selectRange(T.anchor, e.path); T.selected = e.path; T.selectedType = e.type;
    } else {
      selectNode(e.path, e.type); T.anchor = e.path;
      if (isDir) { if (ev.detail < 2) toggleDir(e.path); }
      else if (ev.pointerType === 'touch' || touchOnly()) openFile(e.path);
    }
    treeEl.focus({ preventScroll: true });
  });
  if (!isDir) row.addEventListener('dblclick', ev => { if (!ev.ctrlKey && !ev.metaKey && !ev.shiftKey) openFile(e.path); });
  row.addEventListener('contextmenu', ev => { ev.preventDefault(); if (!T.sel.has(e.path)) selectNode(e.path, e.type); treeMenu(ev.clientX, ev.clientY, e); });
  // Rows drag onto folders (or empty space, for the top) to move there; several move together when chosen.
  if (!touchOnly()) {
    row.draggable = true;
    row.addEventListener('dragstart', ev => {
      if (!T.sel.has(e.path)) selectNode(e.path, e.type);
      T.dragging = topLevel([...T.sel]);
      ev.dataTransfer.effectAllowed = 'move';
      ev.dataTransfer.setData('application/x-studio-paths', JSON.stringify(T.dragging));
      ev.dataTransfer.setData('text/plain', T.dragging.join('\n'));
      if (T.dragging.length > 1) {
        const ghost = el('div', { class: 't-ghost', text: `${T.dragging.length} items` }); document.body.append(ghost);
        ev.dataTransfer.setDragImage(ghost, 10, 10); setTimeout(() => ghost.remove(), 0);
      }
    });
    row.addEventListener('dragend', () => { T.dragging = null; clearTreeDrop(); });
  }
  // There is no right click on a touch screen.
  row.append(el('button', { class: 't-more', 'aria-label': 'Actions for ' + e.name, html: ico('more', 16), onclick: ev => { ev.stopPropagation(); selectNode(e.path, e.type); const r = ev.currentTarget.getBoundingClientRect(); treeMenu(r.left, r.bottom, e); } }));
  if (isDir && T.expanded.has(e.path)) { row.classList.add('expanded'); kids.classList.remove('hidden'); return renderChildren(e.path); }
  return null;
}
function selectNode(path, type) {
  T.selected = path; T.selectedType = type;
  T.sel = new Set(path ? [path] : []);
  paintSel();
}
function paintSel() {
  for (const r of $$('.t-row[data-path]', treeEl)) r.classList.toggle('selected', T.sel.has(r.dataset.path));
  const n = T.sel.size, b = $('#ex-download');
  if (b) b.title = n > 1 ? `Download the ${n} chosen items to this computer (as a zip)` : n === 1 ? `Download ${basename([...T.sel][0])} to this computer` : 'Download the whole project to this computer (as a zip)';
}
const visibleRows = () => $$('.t-row[data-path]', treeEl).filter(r => r.offsetParent !== null);
function selectRange(from, to) {
  const rows = visibleRows(), a = rows.findIndex(r => r.dataset.path === from), b = rows.findIndex(r => r.dataset.path === to);
  if (a < 0 || b < 0) { T.sel = new Set([to]); paintSel(); return; }
  T.sel = new Set(rows.slice(Math.min(a, b), Math.max(a, b) + 1).map(r => r.dataset.path)); paintSel();
}
// The chosen paths without those inside another chosen folder, which go along with it.
const topLevel = paths => paths.filter(p => !paths.some(q => q !== p && p.startsWith(q + '/')));
const typeOf = path => $$('.t-row[data-path]', treeEl).find(r => r.dataset.path === path)?.dataset.type || 'file';
async function toggleDir(path, force) {
  const node = T.nodes.get(path); if (!node) return;
  const open = force !== undefined ? force : !T.expanded.has(path);
  if (open) {
    T.expanded.add(path); node.row.classList.add('expanded'); node.children.classList.remove('hidden');
    if (!node.loaded || force === true) { node.children.replaceChildren(el('div', { class: 't-loading', style: `padding-left:${28 + node.depth * 12}px`, text: 'Loading…' })); await renderChildren(path); const n2 = T.nodes.get(path); if (n2) n2.loaded = true; }
  } else { T.expanded.delete(path); node.row.classList.remove('expanded'); node.children.classList.add('hidden'); }
}
async function refreshTree() {
  if (!S.ws) return;
  const st = treeEl.scrollTop;
  await loadTree(); treeEl.scrollTop = st;
}
const refreshTreeSoon = debounce(refreshTree, 300);
function targetDir() {
  if (T.selectedType === 'dir') return T.selected;
  return dirname(T.selected);
}
async function startCreate(type) {
  if (!S.ws) return;
  const dir = targetDir();
  if (dir) await toggleDir(dir, true);
  const node = T.nodes.get(dir); if (!node) return;
  node.children.classList.remove('hidden');
  const depth = node.depth + 1;
  const inp = el('input', { class: 't-edit', type: 'text', spellcheck: 'false', placeholder: type === 'dir' ? 'folder name' : 'file name' });
  const row = el('div', { class: 't-row', style: `padding-left:${6 + depth * 12}px` }, el('span', { class: 't-chev' }),
    el('span', { class: 't-ico', html: type === 'dir' ? ico('folder', 15, 'style="color:#c09553"') : fileIco('x') }), inp);
  const holder = el('div', null, row);
  node.children.prepend(holder);
  treeEl.scrollTop = Math.min(treeEl.scrollTop, holder.offsetTop);
  inp.focus();
  let done = false;
  const finish = async ok => {
    if (done) return; done = true;
    const name = inp.value.trim(); holder.remove();
    if (!ok || !name) return;
    const path = joinPath(dir, name);
    try {
      await POST('/api/files/create', { ws: S.ws.id, path, type });
      await renderChildren(dir);
      if (type === 'file') { selectNode(path, 'file'); openFile(path); } else { T.expanded.add(path); selectNode(path, 'dir'); await renderChildren(dirname(path)); }
      quick.cache = null;
    } catch (e) { errToast(e, 'Create failed'); }
  };
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); finish(true); } else if (e.key === 'Escape') { e.stopPropagation(); finish(false); } });
  inp.addEventListener('blur', () => finish(!!inp.value.trim()));
}
function startRename(path) {
  const row = $$('.t-row', treeEl).find(x => x.dataset.path === path); if (!row) return;
  const nameEl = $('.t-name', row), old = basename(path);
  const inp = el('input', { class: 't-edit', type: 'text', value: old, spellcheck: 'false' });
  nameEl.replaceWith(inp); inp.focus();
  const dot = old.lastIndexOf('.'); inp.setSelectionRange(0, dot > 0 ? dot : old.length);
  let done = false;
  const finish = async ok => {
    if (done) return; done = true;
    const nn = inp.value.trim();
    if (!ok || !nn || nn === old) { inp.replaceWith(nameEl); return; }
    const to = joinPath(dirname(path), nn);
    try {
      await POST('/api/files/rename', { ws: S.ws.id, from: path, to });
      tabsRenamed(path, to);
      if (T.expanded.has(path)) { T.expanded.delete(path); T.expanded.add(to); }
      T.selected = to; quick.cache = null;
      await renderChildren(dirname(path));
    } catch (e) { inp.replaceWith(nameEl); errToast(e, 'Rename failed'); }
  };
  inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); finish(true); } else if (e.key === 'Escape') { e.stopPropagation(); finish(false); } });
  inp.addEventListener('blur', () => finish(true));
  inp.addEventListener('click', e => e.stopPropagation());
}
async function deletePaths(paths) {
  paths = topLevel(paths);
  if (paths.length === 1) return deletePath(paths[0], typeOf(paths[0]));
  const ok = await dialog({ title: `Delete ${paths.length} items`, body: el('div', {}, 'Move these to the workspace trash (.strata-studio/trash)?',
    el('ul', { class: 'up-taken' }, paths.slice(0, 8).map(p => el('li', { text: p })), paths.length > 8 ? el('li', { text: `and ${paths.length - 8} more` }) : null)),
    buttons: [{ label: 'Cancel', value: false }, { label: 'Delete', value: true, kind: 'primary' }] }) === true;
  if (!ok) return;
  for (const path of paths) {
    try { await POST('/api/files/delete', { ws: S.ws.id, path }); tabsDeleted(path); }
    catch (e) { errToast(e, 'Could not delete ' + path); }
  }
  quick.cache = null; selectNode('', 'dir'); await refreshTree();
}
/* ---- moving within the project (drag and drop in the explorer) */
async function moveInto(paths, dir) {
  paths = topLevel(paths).filter(p => dirname(p) !== dir && dir !== p && !dir.startsWith(p + '/'));
  if (!paths.length || !S.ws) return;
  let r;
  try { r = await POST('/api/files/move', { ws: S.ws.id, paths, to: dir }); } catch (e) { errToast(e, 'Move failed'); return; }
  let moved = r.moved, skipped = r.skipped;
  if (r.taken.length) {
    const list = el('ul', { class: 'up-taken' }, r.taken.slice(0, 8).map(t => el('li', { text: basename(t) })), r.taken.length > 8 ? el('li', { text: `and ${r.taken.length - 8} more` }) : null);
    const conflict = await dialog({ title: r.taken.length === 1 ? `"${dir || 'The top folder'}" already has this name` : `${r.taken.length} names are already in "${dir || 'the top folder'}"`,
      body: el('div', {}, list, el('small', { class: 'dlg-note', text: 'Replace puts the files that are there now in the folder\'s trash (a folder is never replaced). Keep both moves them as "name (2)".' })),
      buttons: [{ label: 'Skip these', value: false }, { label: 'Keep both', value: 'keep' }, { label: 'Replace', value: 'replace', kind: 'primary' }] });
    if (conflict) {
      try { const r2 = await POST('/api/files/move', { ws: S.ws.id, paths: r.taken, to: dir, conflict }); moved = moved.concat(r2.moved); skipped = skipped.concat(r2.skipped); }
      catch (e) { errToast(e, 'Move failed'); }
    }
  }
  for (const m of moved) {
    tabsRenamed(m.from, m.to);
    for (const x of [...T.expanded]) if (x === m.from || x.startsWith(m.from + '/')) { T.expanded.delete(x); T.expanded.add(m.to + x.slice(m.from.length)); }
  }
  if (skipped.length) toast(skipped.map(x => `${x.path}: ${x.why}`).join('\n'), 'warn', 5000);
  if (!moved.length) return;
  if (dir) T.expanded.add(dir);
  quick.cache = null;
  await refreshTree();
  T.sel = new Set(moved.map(m => m.to)); T.selected = moved[0].to; T.selectedType = typeOf(moved[0].to); paintSel();
  toast(moved.length === 1 ? `Moved to ${moved[0].to}` : `Moved ${moved.length} items to ${dir || 'the top folder'}`, 'success', 2500);
}
/* ---- downloads to this computer: one file as it is; a folder, several items or the whole project as a zip */
const fileDownloadUrl = path => '/api/files/download' + qs({ ws: S.ws.id, path });
function saveLink(url, name) {
  const a = el('a', { href: url, download: name || '', style: 'display:none' }); document.body.append(a); a.click(); a.remove();
}
async function downloadPaths(paths) {
  if (!S.ws) return;
  paths = topLevel(paths);
  if (paths.length <= 1) { const p = paths[0] || ''; saveLink(fileDownloadUrl(p), p ? basename(p) : ''); return; }
  try { const r = await POST('/api/files/zip', { ws: S.ws.id, paths }); saveLink(r.url, r.name); toast(`Downloading ${paths.length} items as ${r.name}`, 'success', 2500); }
  catch (e) { errToast(e, 'Download failed'); }
}
// A Download button for a file's own view (players, viewers, tables).
function downloadBtn(path, cls = 'link-btn card-link') {
  return el('a', { class: cls + ' dl-btn', href: fileDownloadUrl(path), download: basename(path), title: 'Download ' + basename(path) + ' to this computer', 'aria-label': 'Download ' + basename(path) },
    el('span', { html: ico('download', 14) }), el('span', { class: 'dl-label', text: 'Download' }));
}
async function deletePath(path, type) {
  const ok = await confirmBox('Delete ' + (type === 'dir' ? 'folder' : 'file'), `Move "${path}" to the workspace trash (.strata-studio/trash)?`, 'Delete');
  if (!ok) return;
  try {
    await POST('/api/files/delete', { ws: S.ws.id, path });
    tabsDeleted(path); quick.cache = null;
    if (T.selected === path) { T.selected = ''; T.selectedType = 'dir'; }
    await renderChildren(dirname(path));
  } catch (e) { errToast(e, 'Delete failed'); }
}
function treeMenu(x, y, e) {
  if (T.sel.size > 1 && T.sel.has(e.path)) {
    const many = [...T.sel], n = topLevel(many).length;
    showContextMenu(x, y, [{ label: `Download ${n} items (zip)`, run: () => downloadPaths(many) }, '-',
      { label: `Delete ${n} items`, danger: true, run: () => deletePaths(many) }, '-',
      { label: 'Copy Paths', run: () => copyText(many.join('\n')) }, { label: 'Choose only this one', run: () => selectNode(e.path, e.type) }]);
    return;
  }
  const items = [];
  if (e.type === 'dir') items.push({ label: 'New File', run: () => startCreate('file') }, { label: 'New Folder', run: () => startCreate('dir') }, { label: 'Upload files…', run: () => pickUpload(e.path) }, '-');
  else items.push({ label: 'Open', run: () => openFile(e.path) }, '-');
  items.push({ label: e.type === 'dir' ? 'Download as zip' : 'Download', run: () => downloadPaths([e.path]) },
    { label: 'Rename (F2)', run: () => startRename(e.path) }, { label: 'Delete', danger: true, run: () => deletePath(e.path, e.type) }, '-',
    { label: 'Copy Path', run: () => copyText(e.path) });
  showContextMenu(x, y, items);
}
treeEl.addEventListener('contextmenu', ev => { if (ev.target === treeEl && S.ws) { ev.preventDefault(); selectNode('', 'dir'); showContextMenu(ev.clientX, ev.clientY, [{ label: 'New File', run: () => startCreate('file') }, { label: 'New Folder', run: () => startCreate('dir') }, { label: 'Upload files…', run: () => pickUpload('') }, { label: 'Download the whole project (zip)', run: () => downloadPaths(['']) }, { label: 'Refresh', run: refreshTree }]); } });
/* ---- uploads: files and folders from this computer into the open folder (dropped on the explorer, picked, or added to
   the chat). Sent as raw bytes in 16 MB parts to /api/files/upload, so big sounds and videos pass through tunnels. */
const UPLOAD_PART = 16 * 1024 * 1024;
async function uploadPart(ws, rel, id, file, offset, total, conflict) {
  const part = file.slice(offset, Math.min(total, offset + UPLOAD_PART));
  const url = '/api/files/upload' + qs({ ws, path: rel, id, offset, total, conflict });
  let r;
  try { r = await fetch(url, { method: 'POST', credentials: 'same-origin', headers: { 'X-Studio': '1', 'X-Studio-Client': CLIENT_ID, 'Content-Type': 'application/octet-stream' }, body: part }); }
  catch (e) { throw new ApiError(0, { error: 'Network error: ' + e.message }); }
  const data = await r.json().catch(() => ({ error: 'HTTP ' + r.status }));
  if (r.status === 401) onUnauthorized();
  if (!r.ok) throw new ApiError(r.status, data);
  return data;
}
// items: [{file, rel}]. Asks once about names already taken (replace or keep both); returns the saved paths.
async function uploadFiles(items, { conflict, quiet } = {}) {
  if (!S.ws || !items.length) return [];
  const ws = S.ws.id;
  if (!conflict) {
    let taken = [];
    try { taken = (await POST('/api/files/upload_check', { ws, paths: items.map(i => i.rel) })).taken; } catch (e) { errToast(e, 'Upload'); return []; }
    if (taken.length) {
      const list = el('ul', { class: 'up-taken' }, taken.slice(0, 8).map(t => el('li', { text: t })), taken.length > 8 ? el('li', { text: `and ${taken.length - 8} more` }) : null);
      conflict = await dialog({ title: taken.length === 1 ? 'A file with this name is already there' : `${taken.length} files are already there`,
        body: el('div', {}, list, el('small', { class: 'dlg-note', text: 'Replace puts the old files in the folder\'s trash. Keep both saves the new ones as "name (2)".' })),
        buttons: [{ label: 'Cancel', value: false }, { label: 'Keep both', value: 'keep' }, { label: 'Replace', value: 'replace', kind: 'primary' }] });
      if (!conflict) return [];
    } else conflict = 'keep';
  }
  const total = items.reduce((n, i) => n + i.file.size, 0) || 1;
  const bar = el('div', { class: 'up-bar' }, el('i')), label = el('span', { text: `Uploading ${items.length} file${items.length > 1 ? 's' : ''}…` });
  const box = quiet ? null : toast('', 'info up-toast', 0);
  if (box) { box.onclick = null; box.append(label, bar); }
  const saved = []; let sent = 0;
  try {
    for (const it of items) {
      const id = [...crypto.getRandomValues(new Uint32Array(4))].map(n => n.toString(16).padStart(8, '0')).join('');
      let off = 0, d = null;
      do {
        d = await uploadPart(ws, it.rel, id, it.file, off, it.file.size, conflict);
        const step = Math.min(UPLOAD_PART, it.file.size - off); off += step; sent += step;
        if (box) { bar.firstChild.style.width = Math.round(100 * sent / total) + '%'; label.textContent = `Uploading ${it.rel}`; }
      } while (off < it.file.size);
      saved.push(d.path);
    }
  } catch (e) { errToast(e, 'Upload stopped'); }
  finally { box?.remove(); }
  if (saved.length) { quick.cache = null; refreshTreeSoon(); if (!quiet) toast(saved.length === 1 ? `Added ${saved[0]}` : `Added ${saved.length} files`, 'success'); }
  return saved;
}
// What was dropped: files, and folders walked to their files (webkitGetAsEntry), each with its path below `dir`.
async function droppedItems(dt, dir) {
  const out = [], entries = [...(dt.items || [])].map(i => i.webkitGetAsEntry?.()).filter(Boolean);
  const walk = async (entry, prefix) => {
    if (entry.isFile) { const file = await new Promise((ok, bad) => entry.file(ok, bad)); out.push({ file, rel: joinPath(dir, prefix + file.name) }); return; }
    if (!entry.isDirectory) return;
    const reader = entry.createReader();
    for (;;) {
      const batch = await new Promise((ok, bad) => reader.readEntries(ok, bad));
      if (!batch.length) break;
      for (const child of batch) await walk(child, prefix + entry.name + '/');
    }
  };
  if (entries.length) for (const entry of entries) await walk(entry, '');
  else for (const file of dt.files || []) out.push({ file, rel: joinPath(dir, file.name) });
  return out;
}
function pickUpload(dir) {
  const inp = el('input', { type: 'file', multiple: '' });
  inp.onchange = () => uploadFiles([...inp.files].map(file => ({ file, rel: joinPath(dir, file.name) })));
  inp.click();
}
const isFileDrag = e => [...(e.dataTransfer?.types || [])].includes('Files');
function dropDir(target) {
  const row = target.closest?.('.t-row');
  if (!row || !row.dataset.path) return '';
  return row.dataset.type === 'dir' ? row.dataset.path : dirname(row.dataset.path);
}
treeEl.addEventListener('dragover', e => {
  if ((!isFileDrag(e) && !T.dragging) || !S.ws) return;
  const dir = dropDir(e.target);
  if (T.dragging && T.dragging.some(p => dir === p || dir.startsWith(p + '/'))) { e.dataTransfer.dropEffect = 'none'; clearTreeDrop(); return; }
  e.preventDefault(); e.dataTransfer.dropEffect = T.dragging ? 'move' : 'copy';
  treeEl.classList.toggle('drop-root', !dir);
  $$('.t-row.drop-into', treeEl).forEach(r => { if (r.dataset.path !== dir) r.classList.remove('drop-into'); });
  if (dir) $$('.t-row', treeEl).find(r => r.dataset.path === dir)?.classList.add('drop-into');
});
const clearTreeDrop = () => { treeEl.classList.remove('drop-root'); $$('.t-row.drop-into', treeEl).forEach(r => r.classList.remove('drop-into')); };
treeEl.addEventListener('dragleave', e => { if (!treeEl.contains(e.relatedTarget)) clearTreeDrop(); });
treeEl.addEventListener('drop', async e => {
  if (T.dragging && S.ws) {
    e.preventDefault(); const dir = dropDir(e.target), paths = T.dragging; T.dragging = null; clearTreeDrop();
    await moveInto(paths, dir);
    return;
  }
  if (!isFileDrag(e) || !S.ws) return;
  e.preventDefault(); const dir = dropDir(e.target); clearTreeDrop();
  const items = await droppedItems(e.dataTransfer, dir);
  if (dir) await toggleDir(dir, true);
  await uploadFiles(items);
});
// A file dropped anywhere else must not make the browser leave Studio to show it.
window.addEventListener('dragover', e => { if (isFileDrag(e)) e.preventDefault(); });
window.addEventListener('drop', e => { if (isFileDrag(e)) e.preventDefault(); });
$('#ex-upload').onclick = () => S.ws && pickUpload(targetDir());
$('#ex-download').onclick = () => downloadPaths([...T.sel]);
treeEl.addEventListener('click', ev => { if (ev.target === treeEl) selectNode('', 'dir'); });
treeEl.addEventListener('keydown', ev => {
  if (ev.target.tagName === 'INPUT') return;
  const rows = visibleRows();
  const i = rows.findIndex(r => r.dataset.path === T.selected);
  const go = r => { if (ev.shiftKey) { if (!T.anchor) T.anchor = T.selected || r.dataset.path; selectRange(T.anchor, r.dataset.path); T.selected = r.dataset.path; T.selectedType = r.dataset.type; }
    else { selectNode(r.dataset.path, r.dataset.type); T.anchor = r.dataset.path; } r.scrollIntoView({ block: 'nearest' }); };
  if (ev.key === 'F2' && T.selected) { ev.preventDefault(); startRename(T.selected); }
  else if (ev.key === 'Delete' && T.sel.size > 1) { ev.preventDefault(); deletePaths([...T.sel]); }
  else if (ev.key === 'Delete' && T.selected) { ev.preventDefault(); deletePath(T.selected, T.selectedType); }
  else if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'a' && rows.length) { ev.preventDefault(); T.sel = new Set(rows.map(r => r.dataset.path)); paintSel(); }
  else if (ev.key === 'Escape' && T.sel.size > 1) { ev.preventDefault(); selectNode(T.selected, T.selectedType); }
  else if (ev.key === 'ArrowDown' && rows.length) { ev.preventDefault(); go(rows[Math.min(rows.length - 1, i + 1)]); }
  else if (ev.key === 'ArrowUp' && rows.length) { ev.preventDefault(); go(rows[Math.max(0, i - 1)]); }
  else if (ev.key === 'ArrowRight' && T.selectedType === 'dir' && T.selected) { ev.preventDefault(); toggleDir(T.selected, true); }
  else if (ev.key === 'ArrowLeft' && T.selectedType === 'dir' && T.selected) { ev.preventDefault(); toggleDir(T.selected, false); }
  else if (ev.key === 'Enter' && T.selected) { ev.preventDefault(); if (T.selectedType === 'dir') toggleDir(T.selected); else openFile(T.selected); }
});
/* ---- the Explorer's find bar: folder and file names (/api/files/names) and the text inside files (/api/files/search)
   together, in place of the tree while there is a query. A folder shows in the tree, a file or a line opens. */
const EF = { seq: 0, rows: [], on: -1 };
function openExFind() {
  if (!S.ws) return;
  showView('explorer', 'force');
  $('#ex-find').classList.remove('hidden');
  const q = $('#ex-find-q'); q.focus(); q.select();
  if (q.value.trim()) runExFind();
}
function closeExFind() {
  EF.seq++; $('#ex-find').classList.add('hidden'); $('#ex-found').classList.add('hidden'); $('#ex-found').replaceChildren();
  $('#ex-find-summary').textContent = ''; treeEl.classList.remove('hidden'); treeEl.focus({ preventScroll: true });
}
// Opens the folders above path in the tree and chooses it.
async function revealInTree(path) {
  const parts = path.split('/');
  for (let i = 1; i < parts.length; i++) await toggleDir(parts.slice(0, i).join('/'), true);
  const row = $$('.t-row[data-path]', treeEl).find(r => r.dataset.path === path);
  if (row) { selectNode(path, row.dataset.type); T.anchor = path; row.scrollIntoView({ block: 'center' }); }
}
function markText(text, q, cs) {
  const frag = document.createDocumentFragment(), hay = cs ? text : text.toLowerCase(), needle = cs ? q : q.toLowerCase();
  let at = 0, i, guard = 0;
  while (needle && (i = hay.indexOf(needle, at)) >= 0 && guard++ < 20) { frag.append(text.slice(at, i), el('mark', { text: text.slice(i, i + needle.length) })); at = i + needle.length; }
  frag.append(text.slice(at));
  return frag;
}
async function runExFind() {
  const q = $('#ex-find-q').value.trim(), out = $('#ex-found'), sum = $('#ex-find-summary');
  if (!S.ws) return;
  if (!q) { EF.seq++; out.classList.add('hidden'); out.replaceChildren(); sum.textContent = ''; treeEl.classList.remove('hidden'); return; }
  const cs = $('#ex-find-case').getAttribute('aria-pressed') === 'true', my = ++EF.seq;
  treeEl.classList.add('hidden'); out.classList.remove('hidden'); sum.textContent = 'Searching…';
  if (!out.childElementCount) out.append(skeleton(5));
  let names, text;
  try {
    [names, text] = await Promise.all([GET('/api/files/names', { ws: S.ws.id, q, case: cs ? 1 : 0 }), GET('/api/files/search', { ws: S.ws.id, q, regex: 0, case: cs ? 1 : 0 })]);
  } catch (e) { if (my === EF.seq) { sum.textContent = e.message; out.replaceChildren(); } return; }
  if (my !== EF.seq) return;
  const rows = []; out.replaceChildren();
  const row = (attrs, ...kids) => { const r = el('div', { class: 'ef-row', role: 'listitem', ...attrs }, ...kids); rows.push(r); return r; };
  const found = names.names || [];
  if (found.length) {
    out.append(el('div', { class: 'ef-head', text: `Names · ${found.length}${names.truncated ? '+' : ''}` }));
    for (const n of found) {
      const nm = basename(n.path), dir = dirname(n.path);
      out.append(row({ title: n.type === 'dir' ? `Show ${n.path} in the Explorer` : `Open ${n.path}`, 'data-path': n.path, 'data-type': n.type,
        onclick: async () => { if (n.type === 'dir') { closeExFind(); await revealInTree(n.path); } else { openFile(n.path); revealInTree(n.path); } } },
        el('span', { class: 't-ico', html: n.type === 'dir' ? ico('folder', 15, 'style="color:#c09553"') : fileIco(nm, 14) }),
        el('span', { class: 'nm' }, markText(nm, q, cs)), el('span', { class: 'dir', text: dir })));
    }
  }
  const matches = text.matches || [], groups = new Map();
  for (const m of matches) { if (!groups.has(m.path)) groups.set(m.path, []); groups.get(m.path).push(m); }
  if (groups.size) {
    out.append(el('div', { class: 'ef-head', text: `Inside files · ${matches.length}${text.truncated ? '+' : ''} in ${groups.size}` }));
    for (const [path, ms] of groups) {
      out.append(row({ title: 'Open ' + path, 'data-path': path, onclick: () => openFile(path, { line: ms[0].line }) },
        el('span', { class: 't-ico', html: fileIco(basename(path), 14) }), el('span', { class: 'nm', text: basename(path) }), el('span', { class: 'dir', text: dirname(path) }),
        el('span', { class: 'badge', text: ms.length })));
      for (const m of ms.slice(0, 5)) {
        const line = (m.text || '').replace(/^\s+/, '').slice(0, 200);
        out.append(row({ class: 'ef-row sr-line', title: `${path}:${m.line}`, onclick: () => openFile(path, { line: m.line }) }, el('span', { class: 'ln', text: m.line }), el('span', { class: 'tx' }, markText(line, q, cs))));
      }
      if (ms.length > 5) out.append(el('div', { class: 'ef-more' }, el('button', { class: 'link-btn', text: `${ms.length - 5} more in this file: open the Search view`,
        onclick: () => { $('#search-q').value = q; showView('search', 'force'); runSearch(); } })));
    }
  }
  EF.rows = rows; EF.on = -1;
  sum.textContent = found.length || groups.size ? `${found.length}${names.truncated ? '+' : ''} name${found.length === 1 ? '' : 's'}, text in ${groups.size} file${groups.size === 1 ? '' : 's'} · Enter opens the first, ↑↓ to move, Esc closes`
    : 'Nothing found.';
}
const runExFindSoon = debounce(runExFind, 300);
$('#ex-find-q').addEventListener('input', runExFindSoon);
$('#ex-find-q').addEventListener('keydown', e => {
  if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeExFind(); return; }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault(); if (!EF.rows.length) return;
    EF.on = Math.max(0, Math.min(EF.rows.length - 1, EF.on + (e.key === 'ArrowDown' ? 1 : -1)));
    EF.rows.forEach((r, i) => r.classList.toggle('on', i === EF.on)); EF.rows[EF.on].scrollIntoView({ block: 'nearest' }); return;
  }
  if (e.key === 'Enter') { e.preventDefault(); const r = EF.rows[Math.max(0, EF.on)]; if (r) r.click(); else runExFind(); }
});
$('#ex-find-case').onclick = e => { const b = e.currentTarget; b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true'); runExFind(); };
$('#ex-find-close').onclick = closeExFind;
$('#ex-find-btn').onclick = () => ($('#ex-find').classList.contains('hidden') ? openExFind() : closeExFind());
$('#ex-new-file').onclick = () => startCreate('file');
$('#ex-new-dir').onclick = () => startCreate('dir');
$('#ex-refresh').onclick = () => { refreshTree(); quick.cache = null; };
$('#ex-collapse').onclick = () => { T.expanded = new Set(); refreshTree(); };

/* ------------------------------------------------------------------ sidebar views */
function showView(name) {
  const sb = $('#sidebar');
  const same = !sb.classList.contains('collapsed') && $(`.act[data-view="${name}"]`).classList.contains('active');
  if (same && arguments[1] !== 'force') { sb.classList.add('collapsed'); $('#split-left').classList.add('hidden'); return; }
  sb.classList.remove('collapsed'); $('#split-left').classList.remove('hidden');
  $$('.act').forEach(b => b.classList.toggle('active', b.dataset.view === name));
  $('#view-explorer').classList.toggle('hidden', name !== 'explorer');
  $('#view-search').classList.toggle('hidden', name !== 'search');
  $('#view-git').classList.toggle('hidden', name !== 'git');
  $('#view-skills').classList.toggle('hidden', name !== 'skills');
  $('#view-video').classList.toggle('hidden', name !== 'video');
  $('#view-voices').classList.toggle('hidden', name !== 'voices');
  $('#view-media').classList.toggle('hidden', name !== 'media');
  if (name === 'media') mediaRefresh();
  $('#view-tasks').classList.toggle('hidden', name !== 'tasks');
  if (name === 'git') gitRefresh();
  if (name === 'skills') skillsRefresh();
  if (name === 'video') videoRefresh();
  if (name === 'voices') voicesRefresh();
  if (name === 'tasks') tasksRefresh();
  lsSet('studio.view', name);
  if (name === 'search') { const q = $('#search-q'); q.focus(); q.select(); }
}
$$('.act').forEach(b => b.onclick = () => showView(b.dataset.view));

/* ---------------------------------------------------------------- skills */
const skillKind = kind => (kind ? kind[0].toUpperCase() + kind.slice(1) : 'Other') + ' skill';
function skillSteps(text) {
  const steps = el('pre', { class: 'skill-steps hidden', text });
  const toggle = el('button', { class: 'link-btn card-link', text: 'Show steps', onclick: e => { e.stopPropagation(); const hide = steps.classList.toggle('hidden'); toggle.textContent = hide ? 'Show steps' : 'Hide steps'; scrollMaybe(); } });
  return { steps, toggle };
}
let skillsSeq = 0;
async function skillsRefresh(force) {
  const box = $('#skills-body'); if ($('#view-skills').classList.contains('hidden')) return;
  const seq = ++skillsSeq; let list;
  const tools = userToolsSection();          // both lists at once, so a refresh is no slower than before
  try { list = (await GET('/api/skills')).skills; } catch (e) { if (e.status !== 401 && seq === skillsSeq) box.replaceChildren(el('div', { class: 'git-note', text: 'Could not load the skills.' })); return; }
  const mine = await tools;
  if (seq !== skillsSeq) return;
  if (force !== true && box.querySelector('.skill-form')) return;     // a skill being written stays; saving or Cancel refresh the list
  if (!list.length) { box.replaceChildren(mine, el('div', { class: 'git-note skills-empty', text: 'No skills yet. When the model works out something worth reusing, it saves it here for later tasks and other projects.' })); return; }
  SK.list = list;
  box.replaceChildren(mine, skillsToolbar(), SK.listBox);
  renderSkillList();
}
/* Finding skills: search, a chip per kind, sort, and only the ones saved in this folder. Kept per browser. */
const SK = { list: [], listBox: el('div', { class: 'skill-list' }), ...(() => { try { return JSON.parse(lsGet('studio.skillsView') || '{}'); } catch { return {}; } })() };
SK.q = ''; SK.kind = SK.kind || ''; SK.sort = SK.sort || 'used'; SK.here = !!SK.here;
function skillsSave() { lsSet('studio.skillsView', JSON.stringify({ kind: SK.kind, sort: SK.sort, here: SK.here, foldTools: SK.foldTools, foldSkills: SK.foldSkills })); }
// What a skill is about, read from its name, description and folder; each area has a colour token (--area-*).
const SKILL_AREAS = [['3d', '3D (Blender)', /blender|3d scene|interior scene|room shell|planes|decor|covers on boxes|exterior view/i],
  ['video', 'Video', /video|clip|timeline|ffmpeg|promo|\bwan\b/i],
  ['pictures', 'Pictures', /comfy|image|picture|diffusion|logo|sdxl|z-image|qwen-image|photo|favicon/i],
  ['language', 'Language', /greek|ελλην|sigma|accent|greeklish|languagetool|spell|language|script|non-english|non-ascii/i],
  ['web', 'Web pages', /browser|page|\bui\b|css|html|site|dark mode|theme|server|frontend|progress bar/i], ['testing', 'Testing', /test|verify|check|e2e/i]];
function skillArea(sk) {
  const text = `${sk.name} ${sk.description} ${sk.folder || ''}`;
  return (SKILL_AREAS.find(([, , re]) => re.test(text)) || ['general', 'General'])[0];
}
const areaLabel = a => (SKILL_AREAS.find(x => x[0] === a) || [a, 'General'])[1];
const areaStyle = a => `--area: var(--area-${a})`;
const SKILL_SORTS = [['used', 'Most used'], ['recent', 'Recently used'], ['new', 'Newest'], ['name', 'Name A–Z']];
function skillsToolbar() {
  const kinds = {};          // chips are areas, in their own colours
  for (const sk of SK.list) kinds[skillArea(sk)] = (kinds[skillArea(sk)] || 0) + 1;
  if (SK.kind && !kinds[SK.kind]) SK.kind = '';
  const search = el('input', { class: 'skill-in skill-search', type: 'search', placeholder: 'Search skills', value: SK.q, 'aria-label': 'Search skills' });
  search.addEventListener('input', () => { SK.q = search.value; renderSkillList(); });
  const sort = el('select', { class: 'skill-in skill-sort', 'aria-label': 'Sort skills' }, SKILL_SORTS.map(([v, l]) => el('option', { value: v, text: l })));
  sort.value = SK.sort; sort.onchange = () => { SK.sort = sort.value; skillsSave(); renderSkillList(); };
  const chip = (value, label, n) => el('button', { class: 'skill-filter' + (SK.kind === value ? ' on' : ''), style: value ? areaStyle(value) : '', 'aria-pressed': String(SK.kind === value), onclick: e => {
    SK.kind = value; skillsSave(); e.currentTarget.parentElement.querySelectorAll('.skill-filter').forEach(b => { const on = b === e.currentTarget; b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on)); }); renderSkillList();
  } }, value ? el('span', { class: 'dot' }) : null, label, el('small', { text: ' ' + n }));
  const folder = S.ws ? basename(S.ws.path) : '';
  const here = el('label', { class: 'skill-here', title: folder ? `Only skills saved while working in ${folder}` : 'Open a folder first' }, el('input', { type: 'checkbox', checked: SK.here ? '' : null, disabled: folder ? null : '', onchange: e => { SK.here = e.target.checked; skillsSave(); renderSkillList(); } }), ' From this folder');
  const head = el('button', { class: 'skills-head', 'aria-expanded': String(!SK.foldSkills), onclick: () => { SK.foldSkills = !SK.foldSkills; skillsSave(); head.setAttribute('aria-expanded', String(!SK.foldSkills)); bar.classList.toggle('folded', !!SK.foldSkills); SK.listBox.classList.toggle('hidden', !!SK.foldSkills); } },
    el('span', { class: 'chev', html: ico('chevron-right', 12) }), el('span', { class: 'task-group', text: `Skills (${SK.list.length})` }));
  const bar = el('div', { class: 'skill-bar' + (SK.foldSkills ? ' folded' : '') }, search,
    el('div', { class: 'skill-filters' }, chip('', 'All', SK.list.length), ...Object.entries(kinds).sort((a, b) => b[1] - a[1]).map(([k, n]) => chip(k, areaLabel(k), n))),
    el('div', { class: 'skill-bar-row' }, sort, here));
  SK.listBox.classList.toggle('hidden', !!SK.foldSkills);
  return el('div', { class: 'skills-top' }, head, bar);
}
function renderSkillList() {
  const q = SK.q.trim().toLowerCase(), folder = S.ws ? basename(S.ws.path) : '';
  let shown = SK.list.filter(sk => (!SK.kind || skillArea(sk) === SK.kind) && (!SK.here || sk.folder === folder)
    && (!q || [sk.name, sk.description, sk.instructions, sk.skill_kind, areaLabel(skillArea(sk))].some(t => (t || '').toLowerCase().includes(q))));
  const by = { used: (a, b) => (b.uses || 0) - (a.uses || 0) || b.updated - a.updated, recent: (a, b) => (b.last_used || 0) - (a.last_used || 0) || (b.uses || 0) - (a.uses || 0),
    new: (a, b) => (b.created || b.updated) - (a.created || a.updated), name: (a, b) => a.name.localeCompare(b.name) }[SK.sort];
  shown = shown.slice().sort(by);
  SK.listBox.replaceChildren(...(shown.length ? shown.map(skillItem) : [el('div', { class: 'git-note', text: 'No skill matches. Clear the search or pick All.' })]));
}
function skillItem(skill) {
  {
    const { steps, toggle } = skillSteps(skill.instructions);
    const used = skill.uses ? `Used ${skill.uses} time${skill.uses === 1 ? '' : 's'}` : 'Not used yet';
    const remove = el('button', { class: 'link-btn card-link', text: 'Delete', onclick: async () => {
      if (!await confirmBox('Delete skill', `Delete "${skill.name}"? The model will no longer be able to use it.`, 'Delete', true)) return;
      try { await POST('/api/skills/delete', { name: skill.name }); skillsRefresh(); } catch (e) { errToast(e, 'Could not delete the skill'); }
    } });
    const editBtn = el('button', { class: 'link-btn card-link', text: 'Edit', onclick: () => item.replaceWith(skillForm(skill)) });
    const area = skillArea(skill);
    const item = el('div', { class: 'skill-item', style: areaStyle(area), 'data-area': area }, el('div', { class: 'skill-chip', text: `${areaLabel(area)} · ${skillKind(skill.skill_kind).replace(/ skill$/, '')}` }), el('div', { class: 'skill-name', text: skill.name }), el('div', { class: 'skill-desc', text: skill.description }),
      el('div', { class: 'skill-meta', text: `${used}${skill.last_used ? ', last ' + ago(skill.last_used) : ''} · ${skill.folder ? 'from ' + skill.folder : 'written by you'} · ${new Date(skill.updated * 1000).toLocaleDateString()}` }), el('div', { class: 'skill-acts' }, toggle, editBtn, remove), steps);
    return item;
  }
}
/* Your own tools: instructions the agent can fetch, or a command it can run with its input in $STUDIO_INPUT */
async function userToolsSection() {
  let d;
  try { d = await GET('/api/tools/mine'); } catch { return el('div'); }
  const fold = el('button', { class: 'skills-head', 'aria-expanded': String(!SK.foldTools), onclick: () => { SK.foldTools = !SK.foldTools; skillsSave(); fold.setAttribute('aria-expanded', String(!SK.foldTools)); box.classList.toggle('folded', !!SK.foldTools); } },
    el('span', { class: 'chev', html: ico('chevron-right', 12) }), el('span', { class: 'task-group', text: `Your tools (${d.tools.length})` }));
  const box = el('div', { class: 'utools' + (SK.foldTools ? ' folded' : '') }, el('div', { class: 'utools-head' }, fold,
    el('button', { class: 'link-btn card-link', text: 'New tool', onclick: () => userToolForm() })));
  if (!d.tools.length) box.append(el('div', { class: 'git-note', text: 'Give the agent a tool of your own: instructions it can fetch when it needs them, or a command it can run, such as a deploy script. It sees them as user__<name>.' }));
  for (const t of d.tools) box.append(el('div', { class: 'skill-item utool' + (t.on ? '' : ' off'), style: areaStyle(t.kind === 'command' ? 'command' : 'instructions') }, el('div', { class: 'skill-chip', text: t.kind === 'command' ? 'Command' : 'Instructions' }),
    el('div', { class: 'skill-name', text: 'user__' + t.name + (t.on ? '' : ' (off)') }), el('div', { class: 'skill-desc', text: t.description }),
    el('div', { class: 'skill-acts' }, el('button', { class: 'link-btn card-link', text: 'Edit', onclick: () => userToolForm(t) }),
      el('button', { class: 'link-btn card-link', text: t.on ? 'Turn off' : 'Turn on', onclick: async () => { try { await POST('/api/tools/save', { old_name: t.name, tool: { ...t, on: !t.on } }); skillsRefresh(); } catch (e) { errToast(e, 'Tool'); } } }),
      el('button', { class: 'link-btn card-link', text: 'Delete', onclick: async () => { if (!await confirmBox('Delete tool', `Delete user__${t.name}?`, 'Delete', true)) return; try { await POST('/api/tools/delete', { name: t.name }); skillsRefresh(); } catch (e) { errToast(e, 'Tool'); } } }))));
  if (!d.enabled) box.append(el('div', { class: 'git-note', text: 'Your tools are switched off on this server (config "user_tools").' }));
  return box;
}
async function userToolForm(t = null, draft = null, problem = '') {
  const v = draft || t || {};          // a refused form comes back with what was typed
  const name = el('input', { class: 'dlg-input', value: v.name || '', placeholder: 'release_notes', maxlength: '40' });
  const kind = el('select', { class: 'dlg-input' }, el('option', { value: 'instructions', text: 'Instructions: the agent fetches your text' }), el('option', { value: 'command', text: 'A command: the agent runs it, with its input in $STUDIO_INPUT' }));
  kind.value = v.kind || 'instructions';
  const desc = el('input', { class: 'dlg-input', value: v.description || '', placeholder: 'When the agent should use it, in one line (required)', maxlength: '300' });
  const text = el('textarea', { class: 'plan-edit', rows: '7' }); text.value = v.text || '';
  const hint = el('small', { class: 'dlg-note' });
  const sync = () => { text.placeholder = kind.value === 'command' ? 'e.g. ./scripts/deploy.sh "$STUDIO_INPUT"' : 'The steps or rules the agent should follow'; hint.textContent = kind.value === 'command' ? 'It runs in the project folder (in the sandbox when that is on) and asks first unless auto-run is on. Use "$STUDIO_INPUT" in quotes: the input is never pasted into the command itself.' : ''; };
  kind.onchange = sync; sync();
  const body = el('div', { class: 'task-form' }, problem ? el('div', { class: 'skill-err', role: 'alert', text: problem }) : null, el('label', { class: 'task-field' }, el('span', { text: 'Name (the agent sees user__name)' }), name), el('label', { class: 'task-field' }, el('span', { text: 'Kind' }), kind),
    el('label', { class: 'task-field' }, el('span', { text: 'When to use it' }), desc), el('label', { class: 'task-field' }, el('span', { text: 'Instructions or command' }), text, hint));
  const ok = await dialog({ title: t ? 'Edit tool' : 'New tool', body, small: false, buttons: [{ label: 'Cancel', value: false }, { label: 'Save', value: true, kind: 'primary' }] });
  if (ok !== true) return;
  const typed = { name: name.value.trim().toLowerCase(), kind: kind.value, description: desc.value.trim(), text: text.value, on: t ? t.on : true };
  const missing = !/^[a-z][a-z0-9_]{1,39}$/.test(typed.name) ? 'The name needs 2 to 40 lowercase letters, digits or _, starting with a letter, such as release_notes.'
    : typed.description.length < 5 ? 'Say in one line when the agent should use it.' : !typed.text.trim() ? 'Write the instructions or the command.' : '';
  if (missing) { userToolForm(t, typed, missing); return; }
  try { await POST('/api/tools/save', { ...(t ? { old_name: t.name } : {}), tool: typed }); toast('Saved. The agent gets it from its next message.', 'success', 3000); showView('skills', 'force'); skillsRefresh(); }
  catch (e) { userToolForm(t, typed, e.message || 'Could not save the tool'); }
}
const SKILL_KINDS = ['workflow', 'debugging', 'testing', 'refactoring', 'setup', 'tooling', 'language', 'other'];
// Edits a skill in place, or writes a new one when skill is null. The server checks it by the same rules as the model's save_skill.
function skillForm(skill) {
  const field = (label, input) => el('label', { class: 'skill-field' }, el('span', { text: label }), input);
  const name = el('input', { class: 'skill-in', value: skill?.name || '', maxlength: '80', placeholder: 'Short and specific, such as "Fix a flaky Go test"' });
  const kind = el('select', { class: 'skill-in' }, ...SKILL_KINDS.map(k => el('option', { value: k, text: skillKind(k) })));
  kind.value = skill?.skill_kind || 'workflow';
  const desc = el('input', { class: 'skill-in', value: skill?.description || '', maxlength: '300', placeholder: 'One line: when to use it' });
  const steps = el('textarea', { class: 'skill-in skill-text', rows: '10', maxlength: '20000', placeholder: '1. First step…' });
  steps.value = skill?.instructions || '';
  const note = el('div', { class: 'skill-err', role: 'alert' });
  const save = el('button', { class: 'btn sm primary', text: 'Save', onclick: async () => {
    const missing = [[name, 'a name'], [desc, 'when to use it'], [steps, 'the steps']].find(([input]) => !input.value.trim());
    if (missing) { note.textContent = `Write ${missing[1]} first.`; missing[0].focus(); return; }
    save.disabled = true; note.textContent = '';
    try {
      await POST('/api/skills/save', { name: name.value, kind: kind.value, description: desc.value, instructions: steps.value, ...(skill ? { old_name: skill.name } : {}) });
      toast(skill ? 'Skill saved' : 'Skill added', 'success', 2500); skillsRefresh(true);
    } catch (e) { note.textContent = e.message || 'Could not save the skill'; save.disabled = false; }
  } });
  const cancel = el('button', { class: 'btn sm', text: 'Cancel', onclick: () => skillsRefresh(true) });
  const form = el('div', { class: 'skill-item skill-form' }, el('div', { class: 'skill-name', text: skill ? 'Edit skill' : 'New skill' }),
    field('Name', name), field('Kind', kind), field('When to use it', desc), field('Steps', steps), note, el('div', { class: 'skill-acts' }, save, cancel));
  if (!skill) form.dataset.new = '1';
  setTimeout(() => name.focus(), 0);
  return form;
}
$('#skills-refresh').onclick = skillsRefresh;
$('#skills-new').onclick = () => {
  const box = $('#skills-body'); if (box.querySelector('.skill-form[data-new]')) return;
  box.querySelector('.skills-empty')?.remove();
  if (SK.listBox.isConnected) { SK.listBox.classList.remove('hidden'); SK.listBox.prepend(skillForm(null)); }
  else { const tools = box.querySelector('.utools'); if (tools) tools.after(skillForm(null)); else box.prepend(skillForm(null)); }
};
/* ---- plan card: the steps from the plan tool, ticked off by each tool call's "3/8" progress */
function parseProgress(text) {
  const m = typeof text === 'string' && text.match(/^\s*(\d{1,4})\s*\/\s*(\d{1,4})\s*$/);
  return m && +m[1] >= 1 && +m[1] <= +m[2] ? { step: +m[1], total: +m[2] } : null;
}
function planCard(d) {
  const list = el('ol', { class: 'plan-steps' }, ...d.steps.map(s => el('li', { class: 'todo' }, el('span', { class: 'plan-mark' }), el('span', { class: 'plan-text', text: s }))));
  const card = el('div', { class: 'm-plan' }, el('div', { class: 'plan-head' }, el('span', { html: ico('list', 14) }), el('span', { class: 'plan-title', text: d.revised ? 'Revised plan' : 'Plan' }), el('span', { class: 'plan-count' })), list);
  return { card, steps: d.steps, step: 0, ended: false };
}
// Steps before the current one are done. finish carries no progress, so a finished run on its last or next-to-last step ticks them all;
// one that finishes earlier leaves the rest open, so a plan cut short shows it.
// A step the run jumped over (worked on 7, then 10) is marked skipped, not done; steps before the first one this plan saw
// count as done, because a revised plan usually starts after the work it lists first.
function planShow(p, step, state) {
  p.step = Math.min(Math.max(step, 1), p.steps.length);
  if (state === 'running') (p.seen ||= new Set()).add(p.step);
  const first = p.seen && p.seen.size ? Math.min(...p.seen) : p.step;
  const lastDone = state !== 'finished' ? p.step - 1 : p.step >= p.steps.length - 1 ? p.steps.length : p.step;
  const items = p.card.querySelectorAll('.plan-steps li');
  p.states = p.steps.map((_, i) => i + 1 <= lastDone ? (i + 1 > first && i + 1 < p.step && !p.seen?.has(i + 1) ? 'skipped' : 'done') : i + 1 === p.step ? (state === 'running' ? 'now' : 'paused') : 'todo');
  p.running = state === 'running';
  planMarks(items, p.states);
  p.card.querySelector('.plan-count').textContent = planCount(p);
  p.card.classList.toggle('ended', !p.running);
  if (p === A.plan) planDock();
}
function planMarks(items, states) {
  items.forEach((li, i) => { li.className = states[i]; li.querySelector('.plan-mark').innerHTML = states[i] === 'done' ? ico('check', 12) : ''; li.title = states[i] === 'skipped' ? 'Skipped: the run moved past this step without working on it' : ''; });
}
function planCount(p) {
  const skipped = p.states.filter(s => s === 'skipped').length;
  return (p.running ? `working on ${p.step}/${p.steps.length}` : `${p.states.filter(s => s === 'done').length}/${p.steps.length} done`) + (skipped ? ` · ${skipped} skipped` : '');
}
// The latest plan, pinned above the messages so it stays in view while they scroll. It starts closed for each plan,
// because the plan's own card is usually on screen too; opening it lasts until the next plan.
function planDock() {
  const dock = $('#plan-dock'), p = A.plan;
  dock.classList.toggle('hidden', !p);
  if (!p || !p.states) { dock.replaceChildren(); return; }
  const open = !!p.open;
  const done = p.states.filter(s => s === 'done').length;
  const head = el('button', { class: 'pd-head', 'aria-expanded': String(open), title: open ? 'Hide the plan' : 'Show the whole plan',
    onclick: () => { p.open = !open; planDock(); } },
    el('span', { class: 'pd-ico', html: ico('list', 14) }),
    el('span', { class: 'pd-step', text: p.running ? `${p.step}/${p.steps.length} · ${p.steps[p.step - 1]}` : `Plan · ${done}/${p.steps.length} done` }),
    el('span', { class: 'pd-bar', role: 'img', 'aria-label': `${done} of ${p.steps.length} steps done` }, el('span', { style: `width:${(done / p.steps.length * 100).toFixed(1)}%` })),
    el('span', { class: 'pd-chev', html: ico(open ? 'chevron-down' : 'chevron-right', 13) }));
  dock.replaceChildren(head);
  if (open) {
    const list = el('ol', { class: 'plan-steps' }, ...p.steps.map(s => el('li', {}, el('span', { class: 'plan-mark' }), el('span', { class: 'plan-text', text: s }))));
    planMarks(list.querySelectorAll('li'), p.states);
    dock.append(list);
  }
}
// Continue picks the same plan up again; any other message is a new task, so the old card stays as it ended.
// A new request starts without a plan, but carrying on keeps it: "Continue…", "Carry on with the goal." (Resume sends that),
// "Start now", «Συνέχισε», or any message while the chat works towards a goal.
const CARRY_ON_RE = /^\s*(continue|carry on|keep going|go on|resume|start now|go ahead|proceed|συνέχισε|συνεχίστε|συνέχεια|προχώρα|ξεκίνα)(?![\p{L}])/iu;   // not \b: it does not know Greek letters
function planNewMessage(text) { if (!CARRY_ON_RE.test(text) && !(A.goal && A.goal.state === 'active')) { A.plan = null; A.step = null; planDock(); } }
function planStatus() {
  if (A.plan && A.plan.step) return `Step ${A.plan.step}/${A.plan.steps.length}: ${A.plan.steps[A.plan.step - 1]}`;
  return A.step && A.step.total > 1 ? `Step ${A.step.step} of ${A.step.total}` : '';
}

function skillCard(d) {
  const { steps, toggle } = skillSteps(d.instructions);
  const open = el('button', { class: 'link-btn card-link', text: 'Open in Skills', onclick: () => { showSection('files'); showView('skills', 'force'); } });
  const area = skillArea({ name: d.name, description: d.description, folder: S.ws ? basename(S.ws.path) : '' });      // the colour it has in the Skills view
  return el('div', { class: 'm-skill', style: areaStyle(area), 'data-area': area },
    el('div', { class: 'skill-head' }, el('span', { html: ico('skill', 14) }), el('span', { class: 'skill-title', text: `${d.updated ? 'Skill updated' : 'New skill saved'}: ${skillKind(d.skill_kind)} · ${areaLabel(area)}` })),
    el('div', { class: 'skill-name', text: d.name }), el('div', { class: 'skill-desc', text: d.description }), el('div', { class: 'skill-acts' }, toggle, open), steps);
}

/* ------------------------------------------------------------------ git */
const GIT = { seq: 0, unchecked: new Set() };
function showGitText(title, text) {
  const pre = el('pre', { class: 'git-diff' });
  for (const line of text.split('\n').slice(0, 20000)) {
    const kind = /^(\+\+\+|---|diff |index |@@)/.test(line) ? 'meta' : line[0] === '+' ? 'add' : line[0] === '-' ? 'del' : '';
    pre.append(el('div', { class: kind, text: line || ' ' }));
  }
  modal(title, pre, { wide: true });
}
const GIT_CODES = { '??': 'New', M: 'Changed', A: 'Added', D: 'Deleted', R: 'Renamed', C: 'Copied', U: 'Conflict', UU: 'Conflict' };
async function gitRefresh() {
  const box = $('#git-body'); if (!S.ws || $('#view-git').classList.contains('hidden')) return;
  const ws = S.ws, seq = ++GIT.seq;
  if (GIT.shown !== ws.id) { box.replaceChildren(skeleton(1), skeleton(4), skeleton(3)); GIT.shown = ws.id; }
  let d;
  try { d = await GET('/api/git/status', { ws: ws.id }); }
  catch (e) { if (seq === GIT.seq) box.replaceChildren(el('div', { class: 'git-note', text: e.message })); return; }
  if (seq !== GIT.seq || S.ws !== ws) return;
  const act = async (url, body, done) => {
    try { await POST(url, { ws: ws.id, ...body }); done && done(); refreshTreeSoon(); for (const t of S.tabs) if (t.kind === 'file') onDiskChanged(t.path); gitRefresh(); }
    catch (e) { errToast(e, 'Git'); }
  };
  if (d.state === 'parent') { box.replaceChildren(el('div', { class: 'git-note', text: 'This folder is inside a larger git repository. Open the top folder of the repository to use this panel.' })); return; }
  if (d.state !== 'repo') {
    box.replaceChildren(el('div', { class: 'git-note', text: 'This folder is not a git repository.' }), el('button', { class: 'btn primary', onclick: () => act('/api/git/init', {}) }, 'Create repository'));
    return;
  }
  const select = el('select', { class: 'dlg-input', 'aria-label': 'Branch', onchange: () => act('/api/git/branch', { name: select.value }) }, d.branches.map(b => el('option', { value: b, text: b })));
  if (d.branch && !d.branches.includes(d.branch)) select.append(el('option', { value: d.branch, text: d.branch }));
  select.value = d.branch;
  const newBranch = el('button', { class: 'icon-btn sm', title: 'New branch', 'aria-label': 'New branch', html: ico('plus', 14), onclick: async () => {
    const name = await promptBox('New branch', '', 'Branch name'); if (name) act('/api/git/branch', { name, create: true });
  } });
  const message = el('textarea', { class: 'dlg-input git-message', rows: 2, placeholder: 'Commit message', 'aria-label': 'Commit message' });
  const paths = new Set(d.files.map(f => f.path));
  for (const p of [...GIT.unchecked]) if (!paths.has(p)) GIT.unchecked.delete(p);
  const commit = el('button', { class: 'btn primary block', disabled: !d.files.length, onclick: () => {
    const chosen = d.files.map(f => f.path).filter(p => !GIT.unchecked.has(p));
    if (!message.value.trim()) { toast('Write a commit message first', 'info'); return; }
    if (!chosen.length) { toast('Choose at least one file', 'info'); return; }
    act('/api/git/commit', { message: message.value, paths: chosen });
  } }, 'Commit');
  const files = el('div', { class: 'git-files' }, d.files.length ? d.files.map(f => {
    const box2 = el('input', { type: 'checkbox', checked: !GIT.unchecked.has(f.path), 'aria-label': 'Include ' + f.path, onchange: () => { if (box2.checked) GIT.unchecked.delete(f.path); else GIT.unchecked.add(f.path); } });
    return el('div', { class: 'git-file' }, box2,
      el('button', { class: 'git-path', title: 'Show changes', text: f.path, onclick: async () => {
        try { showGitText(f.path, (await GET('/api/git/diff', { ws: ws.id, path: f.path })).diff || 'No changes to show.'); } catch (e) { errToast(e, 'Git'); }
      } }),
      el('span', { class: 'git-code', text: GIT_CODES[f.code] || GIT_CODES[f.code[0]] || f.code }));
  }) : el('div', { class: 'git-note', text: 'No changes.' }));
  const log = el('div', { class: 'git-log' }, d.log.length ? d.log.map(c => el('button', { class: 'git-commit', title: `${c.author}, ${c.when}`, onclick: async () => {
    try { showGitText(c.hash + ' ' + c.subject, (await GET('/api/git/show', { ws: ws.id, hash: c.hash })).diff); } catch (e) { errToast(e, 'Git'); }
  } }, el('code', { text: c.hash }), el('span', { text: c.subject }))) : el('div', { class: 'git-note', text: 'No commits yet.' }));
  const own = el('label', { class: 'git-option', title: 'Each request starts on a new branch and is committed there when it ends, so a bad run can be thrown away. Needs a folder with no uncommitted changes.' },
    el('input', { type: 'checkbox', checked: !!S.prefs['git_branch:' + ws.id], onchange: e => prefSet('git_branch:' + ws.id, e.target.checked) }),
    'Run the agent on its own branch');
  box.replaceChildren(el('div', { class: 'git-branch-row' }, select, newBranch), own,
    el('div', { class: 'dlg-sub', text: `Changes (${d.files.length})` }), files, message, commit,
    el('div', { class: 'dlg-sub', text: 'History' }), log);
}
$('#git-refresh').onclick = gitRefresh;

/* ------------------------------------------------------------------ search */
const SR = { seq: 0, collapsed: new Set() };
function resetSearch() { $('#search-results').replaceChildren(); $('#search-summary').textContent = ''; }
async function runSearch() {
  const q = $('#search-q').value;
  if (!S.ws) { $('#search-summary').textContent = 'Open a folder to search.'; return; }
  if (!q) { resetSearch(); return; }
  const regex = $('#search-regex').getAttribute('aria-pressed') === 'true', cs = $('#search-case').getAttribute('aria-pressed') === 'true';
  const my = ++SR.seq; $('#search-summary').textContent = 'Searching…';
  let d;
  if (!$('#search-results').childElementCount) $('#search-results').append(skeleton(5));
  try { d = await GET('/api/files/search', { ws: S.ws.id, q, regex: regex ? 1 : 0, case: cs ? 1 : 0 }); }
  catch (e) { if (my === SR.seq) { $('#search-summary').textContent = e.message; $('#search-results').replaceChildren(); } return; }
  if (my !== SR.seq) return;
  const matches = d.matches || [];
  const groups = new Map();
  for (const m of matches) { if (!groups.has(m.path)) groups.set(m.path, []); groups.get(m.path).push(m); }
  $('#search-summary').textContent = matches.length ? `${matches.length}${d.truncated ? '+' : ''} result${matches.length === 1 ? '' : 's'} in ${groups.size} file${groups.size === 1 ? '' : 's'}${d.truncated ? ' (truncated)' : ''}` : 'No results found.';
  let re = null;
  try { re = new RegExp(regex ? q : q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), cs ? 'g' : 'gi'); } catch { re = null; }
  const out = $('#search-results'); out.replaceChildren();
  for (const [path, ms] of groups) {
    const fileRow = el('div', { class: 'sr-file expanded', title: path }, el('span', { class: 't-chev', html: ico('chevron-right', 12) }), el('span', { class: 't-ico', html: fileIco(basename(path), 14) }),
      el('span', { class: 'nm', text: basename(path) }), el('span', { class: 'dir', text: dirname(path) }), el('span', { class: 'badge', text: ms.length }));
    const lines = el('div');
    for (const m of ms) {
      const tx = el('span', { class: 'tx' });
      const text = (m.text || '').replace(/^\s+/, '').slice(0, 300);
      if (re && text) {
        re.lastIndex = 0; let last = 0, mm, guard = 0;
        while ((mm = re.exec(text)) && guard++ < 20) { if (mm[0] === '') { re.lastIndex++; continue; } tx.append(text.slice(last, mm.index), el('mark', { text: mm[0] })); last = mm.index + mm[0].length; }
        tx.append(text.slice(last));
      } else tx.textContent = text;
      lines.append(el('div', { class: 'sr-line', title: `${path}:${m.line}`, onclick: () => openFile(path, { line: m.line }) }, el('span', { class: 'ln', text: m.line }), tx));
    }
    fileRow.onclick = () => { const c = fileRow.classList.toggle('expanded'); lines.classList.toggle('hidden', !c); };
    out.append(fileRow, lines);
  }
}
const runSearchSoon = debounce(runSearch, 350);
$('#search-q').addEventListener('input', runSearchSoon);
$('#search-q').addEventListener('keydown', e => { if (e.key === 'Enter') runSearch(); });
for (const id of ['#search-case', '#search-regex']) $(id).onclick = e => { const b = e.currentTarget; b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true'); runSearch(); };

/* ------------------------------------------------------------------ quick open (Ctrl+P) */
const quick = { cache: null, cacheWs: null, cacheT: 0 };
function fuzzy(q, s) {
  // returns {score, idx[]} or null. q, s lowercase already handled here.
  const ql = q.toLowerCase(), sl = s.toLowerCase();
  const base = s.lastIndexOf('/') + 1;
  let best = null;
  for (const start of [base, 0]) {
    let qi = 0, score = 0, prev = -2; const idx = [];
    for (let i = start; i < s.length && qi < ql.length; i++) {
      if (sl[i] === ql[qi]) {
        let sc = 1;
        if (i === prev + 1) sc += 5;
        if (i === 0 || '/._-'.includes(s[i - 1])) sc += 4;
        else if (s[i - 1] === s[i - 1].toLowerCase() && s[i] !== s[i].toLowerCase()) sc += 3;
        if (i >= base) sc += 1;
        score += sc; prev = i; idx.push(i); qi++;
      }
    }
    if (qi === ql.length) {
      if (start === base) score += 18;
      if (sl.slice(base).startsWith(ql)) score += 15;
      score -= (s.length - base) * 0.05 + idx[0] * 0.02;
      if (!best || score > best.score) best = { score, idx };
      if (start === base) break;
    }
  }
  return best;
}
function hl(text, idx, offset = 0) {
  let out = '', last = 0; const set = new Set(idx.map(i => i - offset));
  for (let i = 0; i < text.length; i++) if (set.has(i)) { out += esc(text.slice(last, i)) + '<b>' + esc(text[i]) + '</b>'; last = i + 1; }
  return out + esc(text.slice(last));
}
async function openQuick() {
  if (!S.ws) { toast('Open a folder first', 'info', 2500); return; }
  if (overlayStack.some(o => o.ov.dataset.quick)) return;
  const inp = el('input', { type: 'text', placeholder: 'Go to file…', spellcheck: 'false', autocomplete: 'off' });
  const list = el('div', { class: 'qo-list' });
  const box = el('div', { class: 'qo' }, inp, list);
  const entry = openOverlay(box); entry.ov.dataset.quick = '1';
  let files = [], items = [], sel = 0;
  const openTabsFirst = () => S.tabs.filter(t => t.kind === 'file').map(t => t.path).reverse();
  const render = () => {
    const q = inp.value.trim().replace(/\\/g, '/');
    if (!q) { const rec = openTabsFirst(); items = [...new Set([...rec, ...files])].slice(0, 60).map(p => ({ p, idx: [] })); }
    else items = files.map(p => { const r = fuzzy(q, p); return r && { p, idx: r.idx, score: r.score }; }).filter(Boolean).sort((a, b) => b.score - a.score).slice(0, 60);
    sel = Math.min(sel, Math.max(0, items.length - 1));
    list.replaceChildren();
    if (!items.length) list.append(el('div', { class: 'qo-empty', text: files.length ? 'No matching files' : 'Loading files…' }));
    items.forEach((it, i) => {
      const b = it.p.lastIndexOf('/') + 1;
      const row = el('div', { class: 'qo-item' + (i === sel ? ' sel' : ''), onclick: () => choose(i), onmousemove: () => { if (sel !== i) { sel = i; mark(); } } },
        el('span', { html: fileIco(basename(it.p), 14) }), el('span', { class: 'nm', html: hl(it.p.slice(b), it.idx, b) }), el('span', { class: 'dr', html: hl(it.p.slice(0, b ? b - 1 : 0), it.idx) }));
      list.append(row);
    });
  };
  const mark = () => { Array.from(list.children).forEach((c, i) => c.classList.toggle('sel', i === sel)); const c = list.children[sel]; c && c.scrollIntoView({ block: 'nearest' }); };
  const choose = i => { const it = items[i]; if (!it) return; closeOverlay(entry); openFile(it.p); };
  inp.addEventListener('input', () => { sel = 0; render(); });
  inp.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); mark(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); mark(); }
    else if (e.key === 'Enter') { e.preventDefault(); choose(sel); }
  });
  inp.focus(); render();
  try {
    if (!quick.cache || quick.cacheWs !== S.ws.id || Date.now() - quick.cacheT > 30000) {
      const d = await GET('/api/files/list', { ws: S.ws.id }); quick.cache = d.files || []; quick.cacheWs = S.ws.id; quick.cacheT = Date.now();
    }
    files = quick.cache; render();
  } catch (e) { errToast(e, 'Could not list files'); }
}

/* ------------------------------------------------------------------ line diff helpers */
/** Returns ops [{t:' '|'+'|'-', s}] for two texts (line based). Large middles fall back to replace-all. */
function splitLines(t) { if (t == null || t === '') return []; const a = String(t).split('\n'); if (a.length && a[a.length - 1] === '') a.pop(); return a; }
function diffOps(oldText, newText) {
  const a = splitLines(oldText), b = splitLines(newText);
  let pre = 0; while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
  let suf = 0; while (suf < a.length - pre && suf < b.length - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
  const am = a.slice(pre, a.length - suf), bm = b.slice(pre, b.length - suf);
  const ops = a.slice(0, pre).map(s => ({ t: ' ', s }));
  const n = am.length, m = bm.length;
  if (n && m && n * m <= 2.5e6) {
    const w = m + 1, dp = new Uint16Array((n + 1) * w);
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
      dp[i * w + j] = am[i] === bm[j] ? dp[(i + 1) * w + j + 1] + 1 : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1]);
    let i = 0, j = 0;
    while (i < n && j < m) {
      if (am[i] === bm[j]) { ops.push({ t: ' ', s: am[i] }); i++; j++; }
      else if (dp[(i + 1) * w + j] >= dp[i * w + j + 1]) ops.push({ t: '-', s: am[i++] }); else ops.push({ t: '+', s: bm[j++] });
    }
    while (i < n) ops.push({ t: '-', s: am[i++] }); while (j < m) ops.push({ t: '+', s: bm[j++] });
  } else { for (const s of am) ops.push({ t: '-', s }); for (const s of bm) ops.push({ t: '+', s }); }
  for (const s of a.slice(a.length - suf)) ops.push({ t: ' ', s });
  return ops;
}
function diffCounts(oldText, newText) {
  const a = splitLines(oldText), b = splitLines(newText);
  let pre = 0; while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
  let suf = 0; while (suf < a.length - pre && suf < b.length - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
  const am = a.slice(pre, a.length - suf), bm = b.slice(pre, b.length - suf);
  const n = am.length, m = bm.length;
  if (!n || !m) return { added: m, removed: n };
  if (n * m > 4e6) return { added: m, removed: n };
  let prev = new Uint32Array(m + 1), cur = new Uint32Array(m + 1);
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) cur[j] = am[i] === bm[j] ? prev[j + 1] + 1 : Math.max(prev[j], cur[j + 1]);
    [prev, cur] = [cur, prev]; cur.fill(0);
  }
  const lcs = prev[0];
  return { added: m - lcs, removed: n - lcs };
}

/* ------------------------------------------------------------------ editor / tabs */
S.tabs = []; S.active = null;
const E = { ed: null, diffEd: null, langExt: null, langName: null, nextId: 1 };
const paneIds = { monaco: '#pane-monaco', diff: '#pane-diff', plain: '#pane-plain', msg: '#pane-msg', preview: '#pane-preview' };
function showPane(name) { for (const [k, sel] of Object.entries(paneIds)) $(sel).classList.toggle('hidden', k !== name); }

function buildLangMaps() {
  if (E.langExt || !S.monaco) return;
  E.langExt = new Map(); E.langName = new Map(); E.langLabel = new Map();
  for (const l of S.monaco.languages.getLanguages()) {
    E.langLabel.set(l.id, (l.aliases && l.aliases[0]) || l.id);
    for (const e of l.extensions || []) if (!E.langExt.has(e.toLowerCase())) E.langExt.set(e.toLowerCase(), l.id);
    for (const f of l.filenames || []) E.langName.set(f, l.id);
  }
}
function langFor(path) {
  const name = basename(path);
  if (S.monaco) {
    buildLangMaps();
    if (E.langName.has(name)) return E.langName.get(name);
    const i = name.lastIndexOf('.');
    if (i >= 0 && E.langExt.has(name.slice(i).toLowerCase())) return E.langExt.get(name.slice(i).toLowerCase());
    return 'plaintext';
  }
  return 'plaintext';
}
function langLabel(id) { return (E.langLabel && E.langLabel.get(id)) || (id === 'plaintext' ? 'Plain Text' : id); }

async function ensureEditors() {
  await monacoReady;
  if (!S.monaco) {
    if (!S.noticeShown) { S.noticeShown = true; const n = $('#sb-notice'); n.textContent = 'Monaco unavailable (CDN) — plain text editor'; n.classList.remove('hidden'); toast('Could not load the Monaco editor from the CDN. Using a plain text editor instead.', 'warn', 8000); }
    return false;
  }
  if (!E.ed) {
    E.ed = S.monaco.editor.create($('#pane-monaco'), {
      model: null, theme: THEMES[currentTheme()].monaco, automaticLayout: true, minimap: { enabled: S.minimap }, wordWrap: S.wrap ? 'on' : 'off',
      fontFamily: 'ui-monospace, "Cascadia Code", Menlo, Consolas, monospace', fontSize: 13, scrollBeyondLastLine: false,
      ...(isPhone() ? { minimap: { enabled: false }, lineNumbersMinChars: 3, folding: false, glyphMargin: false } : {}),
      renderWhitespace: 'selection', smoothScrolling: true, cursorSmoothCaretAnimation: 'on', padding: { top: 6 }, fixedOverflowWidgets: true,
    });
    E.ed.onDidChangeCursorPosition(updateStatusPos);
    E.ed.onDidChangeCursorSelection(() => updateCtxLabel());
    E.ed.addCommand(S.monaco.KeyMod.CtrlCmd | S.monaco.KeyCode.KeyS, () => S.active && saveTab(S.active));
  }
  return true;
}
function ensureDiffEditor() {
  if (!S.monaco) return null;
  if (!E.diffEd) E.diffEd = S.monaco.editor.createDiffEditor($('#diff-host'), {
    theme: THEMES[currentTheme()].monaco, automaticLayout: true, readOnly: true, originalEditable: false, renderSideBySide: true, wordWrap: S.wrap ? 'on' : 'off',
    fontFamily: 'ui-monospace, "Cascadia Code", Menlo, Consolas, monospace', fontSize: 13, scrollBeyondLastLine: false, fixedOverflowWidgets: true,
    renderOverviewRuler: true, ignoreTrimWhitespace: false,
  });
  return E.diffEd;
}

function tabValue(t) { return t.model ? t.model.getValue() : (t.text ?? ''); }
function isDirty(t) { if (t.ae) return !!t.ae.st && t.ae.st.v !== t.ae.st.saved_v; return t.kind === 'file' && !t.binary && (t.model ? t.model.getAlternativeVersionId() !== t.savedVer : t.text !== t.saved); }
function refreshDirty(t) { t.dirty = isDirty(t); const n = t.tabEl; if (n) n.classList.toggle('dirty', t.dirty); }

async function openFile(path, opts = {}) {
  if (!S.ws) return;
  if (!opts.quiet) showSection('editor');
  let t = S.tabs.find(x => x.kind === 'file' && x.path === path);
  if (!t) {
    let d;
    const ghost = opts.quiet ? null : skeleton(9, 'sk-editor');
    if (ghost) $('#editor-stack').append(ghost);
    try { d = await GET('/api/files/read', { ws: S.ws.id, path }); } catch (e) { ghost?.remove(); errToast(e, 'Cannot open ' + basename(path)); return; }
    ghost?.remove();
    t = S.tabs.find(x => x.kind === 'file' && x.path === path); // may have opened meanwhile
    if (!t) {
      t = { id: E.nextId++, kind: 'file', path, binary: !!d.binary, mtime: d.mtime, saved: d.content || '', text: d.content || '', preview: TIMELINE.test(path) || PREVIEW_TABLE.test(path) || PREVIEW_MD.test(path) || undefined };
      if (!t.binary && await ensureEditors()) {
        const lang = langFor(path);
        t.model = S.monaco.editor.createModel(t.text, lang, S.monaco.Uri.from({ scheme: 'studio', path: '/' + path }));
        t.lang = lang; t.savedVer = t.model.getAlternativeVersionId(); t.text = undefined;
        t.model.onDidChangeContent(() => { if (t.dirty !== isDirty(t)) refreshDirty(t); if (S.active === t) updateCtxLabel(); });
      } else if (!t.binary) t.lang = 'plaintext';
      S.tabs.push(t);
    }
  }
  await activateTab(t);
  if (opts.line) revealLine(t, opts.line, opts.col);
  else if (!opts.quiet && T.selected !== path) { /* keep explorer selection in sync */ selectNode(path, 'file'); }
}
function revealLine(t, line, col = 1) {
  if (t.model && E.ed) { E.ed.revealLineInCenter(line); E.ed.setPosition({ lineNumber: line, column: col }); E.ed.focus(); }
  else if (!t.model && t.kind === 'file') {
    const ta = $('#pane-plain'); const lines = ta.value.split('\n'); let off = 0; for (let i = 0; i < Math.min(line - 1, lines.length); i++) off += lines[i].length + 1;
    ta.focus(); ta.setSelectionRange(off, off + (lines[line - 1] || '').length); ta.scrollTop = Math.max(0, (line - 4) * 19.5);
  }
}

/* ---- previews of pages, images and PDFs. They run sandboxed, away from this page's login. */
const PREVIEW_PAGE = /\.html?$/i, PREVIEW_IMAGE = /\.(png|jpe?g|gif|webp|avif|bmp|ico|svg)$/i, PREVIEW_PDF = /\.pdf$/i;
const PREVIEW_AUDIO = /\.(mp3|wav|m4a|ogg|oga|flac|aac|opus)$/i;
const AUDIO_EDIT = PREVIEW_AUDIO;            // every sound opens in the audio editor
const PREVIEW_MODEL = /\.(glb|gltf|obj|stl|fbx|ply|blend)$/i;
const PREVIEW_VIDEO = /\.(mp4|webm|mov|m4v)$/i, TIMELINE = /\.timeline\.json$/i, PREVIEW_TABLE = /\.(csv|tsv)$/i;
const PREVIEW_MD = /\.(md|markdown|mdx)$/i;
// Office documents: shown as pages (LibreOffice's PDF, GET /api/files/doc_pdf); workbooks first as sheets of cells.
const PREVIEW_DOC = /\.(docx|docm|doc|odt|rtf|pptx|ppt|odp|xlsx|xlsm|xls|ods)$/i, PREVIEW_SHEET = /\.(xlsx|xlsm|xls|ods)$/i;
const canPreview = p => PREVIEW_MD.test(p) || PREVIEW_DOC.test(p) || PREVIEW_PAGE.test(p) || PREVIEW_IMAGE.test(p) || PREVIEW_PDF.test(p) || PREVIEW_VIDEO.test(p) || PREVIEW_AUDIO.test(p) || PREVIEW_MODEL.test(p) || TIMELINE.test(p) || PREVIEW_TABLE.test(p);
// Pictures and PDFs have no useful code view, even if their bytes happen to look like text. An SVG is code too.
const previewOnly = t => t.kind === 'file' && (PREVIEW_PDF.test(t.path) || PREVIEW_DOC.test(t.path) || PREVIEW_VIDEO.test(t.path) || PREVIEW_AUDIO.test(t.path) || PREVIEW_MODEL.test(t.path) || (PREVIEW_IMAGE.test(t.path) && !/\.svg$/i.test(t.path)) || (t.binary && canPreview(t.path)));
let previewAccess = null;
async function previewUrl(path) {
  if (!previewAccess || previewAccess.ws !== S.ws.id || Date.now() > previewAccess.until) {
    const r = await POST('/api/preview/token', { ws: S.ws.id });
    previewAccess = { ws: S.ws.id, token: r.token, until: Date.now() + (r.expires_in - 60) * 1000 };
  }
  return `/preview/${previewAccess.token}/${path.split('/').map(encodeURIComponent).join('/')}`;
}
async function showPreview(t) {
  const pane = $('#pane-preview'), seq = pane._seq = (pane._seq || 0) + 1;
  showPane('preview');
  pane.replaceChildren(skeleton(9, 'sk-editor'));
  let url;
  try { url = await previewUrl(t.path); }
  catch (e) { if (pane._seq === seq) pane.replaceChildren(el('div', { class: 'preview-note', text: e.status === 404 ? 'Preview needs a Studio restart to turn on.' : 'Could not open the preview: ' + e.message })); return; }
  if (pane._seq !== seq || S.active !== t) return;
  if (TIMELINE.test(t.path)) return showTimeline(t, pane);
  if (PREVIEW_TABLE.test(t.path)) return showTable(t, pane);
  if (PREVIEW_MD.test(t.path)) return showMarkdown(t, pane);
  if (PREVIEW_SHEET.test(t.path) && (t.docMode || 'sheets') === 'sheets') return showSheets(t, pane, seq);
  if (PREVIEW_DOC.test(t.path)) return showPdf(t, pane, seq, `/api/files/doc_pdf?ws=${encodeURIComponent(S.ws.id)}&path=${encodeURIComponent(t.path)}`, url);
  if (PREVIEW_PDF.test(t.path)) return showPdf(t, pane, seq, url + '?v=' + Date.now(), url);
  if (PREVIEW_MODEL.test(t.path)) return showModel(t, pane, url, seq);
  if (AUDIO_EDIT.test(t.path)) return showAudio(t, pane);
  const live = url + '?v=' + Date.now();
  let view;
  if (PREVIEW_IMAGE.test(t.path) && !/\.svg$/i.test(t.path)) {
    const warn = isDirty(t) ? [el('span', { class: 'preview-warn', text: 'Showing the last saved version' })] : [];
    pane.replaceChildren(...imageViewer(t, live, basename(t.path), t.path, [...warn, el('button', { class: 'link-btn card-link', text: 'Reload', onclick: () => { t.view = null; showPreview(t); } })]));
    pane.querySelector('.iv-stage')._place();
    return;
  }
  if (PREVIEW_IMAGE.test(t.path)) view = el('div', { class: 'preview-image' }, el('img', { src: live, alt: basename(t.path) }));
  else if (PREVIEW_VIDEO.test(t.path)) return showVideo(t, pane, live);
  else if (PREVIEW_AUDIO.test(t.path)) view = el('div', { class: 'preview-audio' }, el('span', { class: 'pa-ico', html: ico('media', 40) }),
    el('div', { class: 'pa-name', text: t.path.split('/').pop() }), el('audio', { src: live, controls: '', preload: 'metadata' }));
  else if (PREVIEW_PDF.test(t.path)) view = el('iframe', { class: 'preview-frame', src: live, title: 'Preview of ' + basename(t.path) });
  else view = el('iframe', { class: 'preview-frame', src: live, sandbox: 'allow-scripts allow-forms allow-modals', title: 'Preview of ' + basename(t.path) });
  const bar = el('div', { class: 'preview-bar' }, el('span', { class: 'preview-path', text: t.path }),
    isDirty(t) ? el('span', { class: 'preview-warn', text: 'Showing the last saved version' }) : null,
    el('button', { class: 'link-btn card-link', text: 'Reload', onclick: () => showPreview(t) }),
    el('a', { class: 'card-link', href: url, target: '_blank', rel: 'noopener noreferrer', text: 'Open in new tab' }), downloadBtn(t.path));
  pane.replaceChildren(bar, view);
}
/* ---- PDF pages (2026-11-17): PDFs, and Office documents turned into PDF by the server, drawn by pdf.js from jsdelivr
   into Studio's own page, so they show the same in every browser and on phones (whose browsers show no PDF in a frame).
   Pages are drawn when they scroll near, with a text layer for selecting and copying. Without the CDN a PDF falls back to
   the browser's own viewer in a frame. */
const PDFJS_BASE = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/';
let pdfjsLoading = null;
function loadPdfjs() {
  if (!pdfjsLoading) {
    pdfjsLoading = import(PDFJS_BASE + 'build/pdf.min.mjs').then(lib => {
      // A worker from another origin cannot be started directly; a module worker made from a blob may import it.
      try {
        const src = URL.createObjectURL(new Blob([`import ${JSON.stringify(PDFJS_BASE + 'build/pdf.worker.min.mjs')};`], { type: 'text/javascript' }));
        lib.GlobalWorkerOptions.workerPort = new Worker(src, { type: 'module' });
      } catch { lib.GlobalWorkerOptions.workerSrc = PDFJS_BASE + 'build/pdf.worker.min.mjs'; }
      if (!document.getElementById('pdfjs-css')) document.head.append(el('link', { id: 'pdfjs-css', rel: 'stylesheet', href: PDFJS_BASE + 'web/pdf_viewer.css' }));
      return lib;
    }).catch(e => { pdfjsLoading = null; throw e; });
  }
  return pdfjsLoading;
}
async function showPdf(t, pane, seq, src, fileUrl) {
  const doc = PREVIEW_DOC.test(t.path), sheet = PREVIEW_SHEET.test(t.path);
  const st = t.pdf || (t.pdf = { zoom: 'fit', page: 1 });
  const info = el('span', { class: 'pdf-info', text: doc ? 'Opening the document…' : 'Opening…' });
  const pageBox = el('input', { class: 'dlg-input pdf-page', type: 'number', min: '1', value: '1', 'aria-label': 'Page' });
  const zoomText = el('span', { class: 'pdf-zoom-text' });
  const pages = el('div', { class: 'pdf-pages' }), scroller = el('div', { class: 'pdf-view', tabindex: '0' }, pages);
  const btn = (icon, title, run) => el('button', { class: 'icon-btn sm', title, 'aria-label': title, html: ico(icon, 15), onclick: run });
  const bar = el('div', { class: 'preview-bar pdf-bar' }, el('span', { class: 'preview-path', text: t.path }),
    sheet ? el('button', { class: 'btn sm', title: 'Show the workbook as sheets of cells', onclick: () => { t.docMode = 'sheets'; showPreview(t); } }, 'Sheets') : null,
    el('span', { class: 'pdf-nav' }, btn('chevron-left', 'Previous page', () => go(st.page - 1)), pageBox, info, btn('chevron-right', 'Next page', () => go(st.page + 1))),
    el('span', { class: 'pdf-nav' }, btn('minus', 'Smaller', () => zoom(cur() / 1.2)), zoomText, btn('plus', 'Larger', () => zoom(cur() * 1.2)),
      el('button', { class: 'btn sm', title: 'Fit the page width', onclick: () => zoom('fit') }, 'Fit')),
    el('button', { class: 'link-btn card-link', text: 'Reload', onclick: () => { t.pdf = null; showPreview(t); } }),
    el('a', { class: 'card-link', href: doc ? src : fileUrl, target: '_blank', rel: 'noopener noreferrer', text: 'Open in new tab' }), downloadBtn(t.path));
  pane.replaceChildren(bar, scroller);
  let lib, pdf;
  try {
    lib = await loadPdfjs();
    const r = await fetch(src, { headers: { 'X-Studio': '1' }, credentials: 'same-origin', cache: 'no-store' });
    if (!r.ok) { let msg = r.statusText; try { msg = (await r.json()).error || msg; } catch {} throw Object.assign(new Error(msg), { status: r.status }); }
    const bytes = new Uint8Array(await r.arrayBuffer());
    if (pane._seq !== seq) return;
    pdf = await lib.getDocument({ data: bytes, isEvalSupported: false, enableXfa: false }).promise;
  } catch (e) {
    if (pane._seq !== seq) return;
    // A PDF that pdf.js cannot load or read still gets the browser's own viewer.
    if (!doc && !(e.status >= 400)) { pane.replaceChildren(bar, el('iframe', { class: 'preview-frame', src, title: 'Preview of ' + basename(t.path) })); info.textContent = ''; return; }
    pane.replaceChildren(bar, el('div', { class: 'preview-note', text: (doc ? 'Could not show this document: ' : 'Could not show this PDF: ') + (e.message || e) }));
    return;
  }
  if (pane._seq !== seq) { pdf.destroy(); return; }
  t.pdfDoc = pdf;
  const n = pdf.numPages, first = await pdf.getPage(1), base = first.getViewport({ scale: 1 });
  pageBox.max = String(n);
  const fit = () => Math.max(0.25, Math.min(4, (scroller.clientWidth - 32) / base.width));
  const cur = () => st.zoom === 'fit' ? fit() : st.zoom;
  const slots = [];
  for (let i = 1; i <= n; i++) slots.push(el('div', { class: 'pdf-slot', 'data-page': String(i) }));
  pages.replaceChildren(...slots);
  const drawn = new Map();
  const size = () => {
    const z = cur(); zoomText.textContent = Math.round(z * 100) + '%';
    for (const s of slots) { s.style.width = base.width * z + 'px'; s.style.height = base.height * z + 'px'; }
  };
  const draw = async slot => {
    const i = Number(slot.dataset.page), z = cur(), key = i + '@' + z.toFixed(3);
    if (slot._drawn === key) return;
    slot._drawn = key;
    const page = await pdf.getPage(i);
    const vp = page.getViewport({ scale: z }), dpr = Math.min(window.devicePixelRatio || 1, 2);
    const canvas = el('canvas', { class: 'pdf-canvas' });
    canvas.width = Math.floor(vp.width * dpr); canvas.height = Math.floor(vp.height * dpr);
    canvas.style.width = vp.width + 'px'; canvas.style.height = vp.height + 'px';
    slot.style.width = vp.width + 'px'; slot.style.height = vp.height + 'px';
    try { await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise; }
    catch { slot._drawn = null; return; }
    if (slot._drawn !== key || pane._seq !== seq) return;
    const text = el('div', { class: 'textLayer' });
    text.style.setProperty('--scale-factor', String(z));
    slot.replaceChildren(canvas, text);
    drawn.set(i, key);
    try { await new lib.TextLayer({ textContentSource: page.streamTextContent(), container: text, viewport: vp }).render(); } catch {}
  };
  const seen = new IntersectionObserver(es => { for (const e of es) if (e.isIntersecting) draw(e.target); }, { root: scroller, rootMargin: '600px 0px' });
  slots.forEach(s => seen.observe(s));
  const visible = () => {
    const top = scroller.scrollTop + scroller.clientHeight / 3;
    let k = 1; for (const s of slots) { if (s.offsetTop <= top) k = Number(s.dataset.page); else break; }
    return k;
  };
  const go = k => { k = Math.max(1, Math.min(n, k | 0)); st.page = k; pageBox.value = String(k); slots[k - 1].scrollIntoView({ block: 'start' }); };
  const zoom = z => {
    const keep = visible();
    st.zoom = z === 'fit' ? 'fit' : Math.max(0.25, Math.min(4, z));
    size(); slots.forEach(s => { if (s.getBoundingClientRect().bottom > -600 && s.getBoundingClientRect().top < innerHeight + 600) draw(s); });
    go(keep);
  };
  pageBox.addEventListener('change', () => go(Number(pageBox.value)));
  scroller.addEventListener('scroll', () => { const k = visible(); if (k !== st.page) { st.page = k; pageBox.value = String(k); } }, { passive: true });
  scroller.addEventListener('wheel', e => { if (!e.ctrlKey) return; e.preventDefault(); zoom(cur() * (e.deltaY < 0 ? 1.1 : 1 / 1.1)); }, { passive: false });
  scroller.addEventListener('keydown', e => {
    if (e.key === 'PageDown' || (e.key === 'ArrowRight' && !e.altKey)) { e.preventDefault(); go(st.page + 1); }
    else if (e.key === 'PageUp' || (e.key === 'ArrowLeft' && !e.altKey)) { e.preventDefault(); go(st.page - 1); }
    else if (e.key === 'Home') { e.preventDefault(); go(1); } else if (e.key === 'End') { e.preventDefault(); go(n); }
    else if ((e.key === '+' || e.key === '=') && !e.ctrlKey) zoom(cur() * 1.2); else if (e.key === '-' && !e.ctrlKey) zoom(cur() / 1.2);
  });
  if (window.ResizeObserver) { let w = scroller.clientWidth; new ResizeObserver(() => { if (st.zoom === 'fit' && Math.abs(scroller.clientWidth - w) > 8) { w = scroller.clientWidth; zoom('fit'); } }).observe(scroller); }
  info.textContent = `of ${n}`;
  pane.dataset.pages = String(n);
  size();
  if (st.page > 1) go(st.page);
}
// A workbook as sheets of cells (GET /api/files/sheets): tabs per sheet, column letters and row numbers, search,
// and Page view for the printed look.
async function showSheets(t, pane, seq) {
  let d;
  try { d = await GET(`/api/files/sheets?ws=${encodeURIComponent(S.ws.id)}&path=${encodeURIComponent(t.path)}`); }
  catch (e) { if (pane._seq === seq) pane.replaceChildren(el('div', { class: 'preview-note', text: 'Could not read this workbook: ' + e.message })); return; }
  if (pane._seq !== seq) return;
  const st = t.sheets || (t.sheets = { k: 0, q: '' });
  if (st.k >= d.sheets.length) st.k = 0;
  const colName = i => { let s = ''; i++; while (i > 0) { const m = (i - 1) % 26; s = String.fromCharCode(65 + m) + s; i = Math.floor((i - 1) / 26); } return s; };
  const tabs = el('div', { class: 'sheet-tabs', role: 'tablist' });
  const search = el('input', { class: 'dlg-input table-search', type: 'search', placeholder: 'Search the cells', value: st.q, 'aria-label': 'Search the cells' });
  const count = el('span', { class: 'table-count' }), wrap = el('div', { class: 'sheet-grid' });
  const draw = () => {
    const sh = d.sheets[st.k] || { rows: [], columns: 0, total_rows: 0, first_row: 1, name: '' };
    tabs.replaceChildren(...d.sheets.map((x, k) => el('button', { class: 'sheet-tab' + (k === st.k ? ' on' : ''), role: 'tab', 'aria-selected': String(k === st.k),
      onclick: () => { st.k = k; draw(); } }, x.name)));
    const q = st.q.toLowerCase();
    const rows = sh.rows.map((r, i) => [sh.first_row + i, r]).filter(([, r]) => !q || r.some(c => String(c).toLowerCase().includes(q)));
    const numeric = c => c !== '' && !isNaN(Number(c));
    count.textContent = !sh.columns ? 'Empty sheet' : `${rows.length}${q ? ' of ' + sh.rows.length : ''} row${rows.length === 1 ? '' : 's'} · ${sh.columns} column${sh.columns === 1 ? '' : 's'}`
      + (sh.total_rows > sh.rows.length ? ` · first ${sh.rows.length} of ${sh.total_rows} shown` : '') + (sh.formulas ? ` · ${sh.formulas} formulas (values shown)` : '');
    const head = el('tr', {}, el('th', { class: 'sg-corner' }), ...Array.from({ length: sh.columns }, (_, c) => el('th', { text: colName(c) })));
    wrap.replaceChildren(el('table', {}, el('thead', {}, head), el('tbody', {}, rows.slice(0, 2000).map(([n, r]) =>
      el('tr', {}, el('th', { class: 'sg-row', text: String(n) }), ...Array.from({ length: sh.columns }, (_, c) => el('td', { class: numeric(r[c] ?? '') ? 'num' : '', text: r[c] ?? '' })))))));
  };
  search.addEventListener('input', () => { st.q = search.value; draw(); });
  draw();
  pane.replaceChildren(el('div', { class: 'preview-bar' }, el('span', { class: 'preview-path', text: t.path }), count, search,
    el('button', { class: 'btn sm', title: 'Show the workbook as printed pages', onclick: () => { t.docMode = 'pages'; showPreview(t); } }, 'Page view'), downloadBtn(t.path)),
    d.sheets.length > 1 ? tabs : null, el('div', { class: 'table-view' }, wrap));
}
/* ---- 3D models (glTF/GLB, OBJ, STL, FBX, PLY, and Blender files exported to GLB by the server): three.js from
   jsdelivr, loaded on first use. The editor shows one model to turn, zoom and inspect; the Media view gets thumbnails
   from one shared offscreen renderer. Frames are drawn only when something moves, since Strata shares the card. */
const THREE_BASE = 'https://cdn.jsdelivr.net/npm/three@0.187.0/';
let threeLoading = null;
function loadThree() {
  if (!threeLoading) {
    const add = p => import(THREE_BASE + 'examples/jsm/' + p + '/+esm');
    threeLoading = Promise.all([import(THREE_BASE + '+esm'), add('controls/OrbitControls.js'), add('loaders/GLTFLoader.js'), add('loaders/DRACOLoader.js'),
      add('libs/meshopt_decoder.module.js'), add('loaders/OBJLoader.js'), add('loaders/STLLoader.js'), add('loaders/FBXLoader.js'), add('loaders/PLYLoader.js'),
      add('environments/RoomEnvironment.js')])
      .then(([THREE, oc, gl, dr, mo, obj, stl, fbx, ply, room]) => ({ THREE, OrbitControls: oc.OrbitControls, GLTFLoader: gl.GLTFLoader, DRACOLoader: dr.DRACOLoader,
        MeshoptDecoder: mo.MeshoptDecoder, OBJLoader: obj.OBJLoader, STLLoader: stl.STLLoader, FBXLoader: fbx.FBXLoader, PLYLoader: ply.PLYLoader, RoomEnvironment: room.RoomEnvironment }))
      .catch(e => { threeLoading = null; throw e; });
  }
  return threeLoading;
}
function gltfLoader(K) {
  if (!K.gltf) {
    const draco = new K.DRACOLoader(); draco.setDecoderPath(THREE_BASE + 'examples/jsm/libs/draco/gltf/');
    K.gltf = new K.GLTFLoader(); K.gltf.setDRACOLoader(draco); K.gltf.setMeshoptDecoder(K.MeshoptDecoder);
  }
  return K.gltf;
}
// Loads a model file into { scene, animations }. url is its preview address; a Blender file comes from /api/media/blend.
async function loadModel(K, path, url) {
  const ext = path.split('.').pop().toLowerCase(), T = K.THREE;
  if (ext === 'blend') {
    const r = await fetch('/api/media/blend?' + new URLSearchParams({ ws: S.ws.id, path }), { credentials: 'same-origin' });
    if (!r.ok) { let m = 'HTTP ' + r.status; try { m = (await r.json()).error || m; } catch { /* not JSON */ } throw new Error(m); }
    const g = await gltfLoader(K).parseAsync(await r.arrayBuffer(), '');
    return { scene: g.scene, animations: g.animations || [] };
  }
  if (ext === 'glb' || ext === 'gltf') { const g = await gltfLoader(K).loadAsync(url); return { scene: g.scene, animations: g.animations || [] }; }
  if (ext === 'obj') return { scene: await new K.OBJLoader().loadAsync(url), animations: [] };
  if (ext === 'fbx') { const g = await new K.FBXLoader().loadAsync(url); return { scene: g, animations: g.animations || [] }; }
  const geo = await (ext === 'stl' ? new K.STLLoader() : new K.PLYLoader()).loadAsync(url);
  if (!geo.attributes.normal) geo.computeVertexNormals();
  const colors = !!geo.attributes.color;
  const mesh = new T.Mesh(geo, new T.MeshStandardMaterial({ color: colors ? 0xffffff : 0xb8bcc6, vertexColors: colors, roughness: 0.6, metalness: 0.05 }));
  return { scene: new T.Group().add(mesh), animations: [] };
}
function modelStats(root) {
  let meshes = 0, tris = 0;
  root.traverse(o => { if (o.isMesh && o.geometry) { meshes++; const g = o.geometry; tris += (g.index ? g.index.count : (g.attributes.position?.count || 0)) / 3; } });
  return { meshes, tris: Math.round(tris) };
}
function disposeModel(root) {
  root.traverse(o => {
    o.geometry?.dispose();
    for (const m of [].concat(o.material || [])) { for (const v of Object.values(m)) if (v && v.isTexture) v.dispose(); m.dispose(); }
  });
}
// Lights, an environment for PBR materials, and the camera placed to see the whole model. Returns the framing.
function modelStage(K, renderer, scene, root) {
  const T = K.THREE;
  const pmrem = new T.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new K.RoomEnvironment(), 0.04).texture; pmrem.dispose();
  scene.add(new T.HemisphereLight(0xffffff, 0x445066, 0.8));
  const sun = new T.DirectionalLight(0xffffff, 1.6); sun.position.set(3, 5, 4); scene.add(sun);
  scene.add(root);
  const box = new T.Box3().setFromObject(root);
  if (box.isEmpty()) box.set(new T.Vector3(-1, -1, -1), new T.Vector3(1, 1, 1));
  const size = box.getSize(new T.Vector3()), center = box.getCenter(new T.Vector3());
  const radius = Math.max(size.length() / 2, 1e-3);
  return { box, size, center, radius };
}
function aimCamera(K, camera, frame) {
  // fits the model in the narrower of the two directions, so a tall phone screen does not cut its sides off
  const v = (camera.fov * Math.PI / 180) / 2, h = Math.atan(Math.tan(v) * (camera.aspect || 1));
  const dist = frame.radius / Math.sin(Math.min(v, h)) * 1.08;
  camera.position.copy(frame.center).add(new K.THREE.Vector3(0.9, 0.55, 1).normalize().multiplyScalar(dist));
  camera.near = dist / 200; camera.far = dist * 50; camera.updateProjectionMatrix(); camera.lookAt(frame.center);
}
const modelNum = n => n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e4 ? Math.round(n / 1e3) + 'K' : String(n);
const fmtLen = v => v >= 100 ? Math.round(v) : v >= 10 ? v.toFixed(1) : v.toFixed(2);
/* ------------------------------------------------------------------ audio editor (2026-11-13)
   The server keeps the versions (tools/audio_edit.py, /api/audio/*); the page decodes the current one to draw its
   waveform and play it. Selection [a, b] in seconds; edits with no selection apply to the whole sound. */
const AE_RULER = 20, AE_GRAB = 14, AE_BLOCK = 256;
const AE_EFFECTS = [
  ['chorus', 'Chorus', true], ['flanger', 'Flanger', true], ['phaser', 'Phaser', true], ['tremolo', 'Tremolo', true], ['vibrato', 'Vibrato', true], '-',
  ['lowpass', 'Low-pass (muffle)', true], ['highpass', 'High-pass (thin)', true], ['bass', 'Bass boost', true], ['treble', 'Treble boost', true], '-',
  ['compressor', 'Compressor', true], ['denoise', 'Noise reduction', true], ['telephone', 'Telephone', false], ['crush', 'Bit-crush', true],
];
let aeCtx = null;
const aeAudio = () => aeCtx || (aeCtx = new (window.AudioContext || window.webkitAudioContext)());
const aeKey = t => `studio.ae.${S.ws?.id}:${t.path}`;
function aeTime(s, fine = true) {
  if (!isFinite(s)) s = 0;
  const m = Math.floor(s / 60), r = s - m * 60;
  return `${m}:${(fine ? r.toFixed(3) : Math.floor(r).toString()).padStart(fine ? 6 : 2, '0')}`;
}

// The menus of the editor's toolbar: [label, icon, items for a tab]
const AE_MENUS = [
  ['Edit', 'edit', t => [{ label: 'Keep only the selection', run: () => aeEdit(t, 'crop') }, { label: 'Make the selection silent', run: () => aeEdit(t, 'silence') },
    { label: 'Insert silence…', run: () => aeAsk(t, 'insert_silence') }, '-', { label: 'Select all (Ctrl+A)', run: () => aeSetSel(t, { a: 0, b: aeLen(t) }) },
    { label: 'Select nothing (Esc)', run: () => aeSetSel(t, null) }]],
  ['Volume', 'volume', t => [{ label: 'Fade in', run: () => aeEdit(t, 'fade_in') }, { label: 'Fade out', run: () => aeEdit(t, 'fade_out') }, '-',
    { label: 'Louder or quieter…', run: () => aeAsk(t, 'gain') }, { label: 'Normalize…', run: () => aeAsk(t, 'normalize') }]],
  ['Time', 'clock', t => [{ label: 'Speed…', run: () => aeAsk(t, 'speed') }, { label: 'Pitch…', run: () => aeAsk(t, 'pitch') }, { label: 'Reverse', run: () => aeEdit(t, 'reverse') }]],
  ['Effects', 'wand', t => [{ label: 'Reverb…', run: () => aeAsk(t, 'reverb') }, { label: 'Echo…', run: () => aeAsk(t, 'echo') }, '-',
    ...AE_EFFECTS.map(e => e === '-' ? '-' : { label: e[1] + '…', run: () => aeAsk(t, 'fx', e[0]) })]],
  ['Slices', 'slices', t => [{ label: 'Cut mark at the cursor (M)', run: () => aeMark(t) }, { label: 'Remove all marks', run: () => { t.ae.marks = []; aeDraw(t); } }, '-',
    { label: 'Save the slices as files…', run: () => aeSlices(t) }]],
];
const aeIco = (name, size = 16) => el('span', { class: 'ae-i', html: ico(name, size) });
function aeBtn(label, title, onclick, cls = '', icon = null) {
  return el('button', { class: 'ae-b ' + cls, title, 'aria-label': title, onclick }, icon ? aeIco(icon) : null, label ? el('span', { class: 'ae-l', text: label }) : null);
}
function aeMenuAt(btn, items) { const r = btn.getBoundingClientRect(); showContextMenu(r.left, r.bottom + 4, items); }

async function showAudio(t, pane) {
  const ae = t.ae || (t.ae = { st: null, buf: null, sum: null, view: { start: 0, pps: 0 }, sel: null, cursor: 0, marks: [], loop: false, play: null, busy: false, vol: 1, mode: null });
  if (S.aeEditNext === t.path) { ae.mode = 'edit'; S.aeEditNext = null; }
  if (!ae.mode) { let id = null; try { id = sessionStorage.getItem(aeKey(t)); } catch { /* ignore */ } ae.mode = id ? 'edit' : 'play'; }
  aeStop(t, true);
  return ae.mode === 'edit' ? aeEditor(t, pane) : aePlayer(t, pane);
}
function aeTransport(t) {
  const ae = t.ae;
  const playB = el('button', { class: 'ae-play', title: 'Play (Space)', 'aria-label': 'Play', onclick: () => aeToggle(t) }, aeIco('play', 20));
  const backB = aeBtn('', 'Back to the start (Home)', () => { aeStop(t); ae.cursor = 0; aeShow(t, 0); aeChrome(t); aeDraw(t); }, 'ic', 'skip-back');
  const loopB = aeBtn('', 'Play over and over', () => { ae.loop = !ae.loop; loopB.classList.toggle('on', ae.loop); loopB.setAttribute('aria-pressed', ae.loop); if (ae.play) aePlay(t); }, 'ic' + (ae.loop ? ' on' : ''), 'loop');
  const clock = el('span', { class: 'ae-clock' });
  return { playB, backB, loopB, clock };
}
function aeMount(t, pane, root, ui) {
  pane.replaceChildren(root);
  t.ae.ui = { pane, root, ...ui };
  aeWire(t);
  new ResizeObserver(() => aeDraw(t)).observe(ui.stage);
  aeWatch(t);
}
// The agent's audio tools change files on disk: an open player reloads, an editor with nothing unsaved starts again from
// the new file, and one with unsaved changes says so (Save then asks before replacing it).
function aeWatch(t) {
  const ae = t.ae;
  clearTimeout(ae.timer);
  const tick = async () => {
    if (!S.tabs.includes(t) || !ae.ui?.root.isConnected) return;
    if (S.active === t && !document.hidden && !ae.busy && ae.buf) {
      try {
        const { mtime } = await GET('/api/audio/mtime', { ws: S.ws.id, path: t.path });
        if (ae.seen == null) ae.seen = mtime;
        else if (mtime && mtime !== ae.seen && !ae.play) {
          ae.seen = mtime;
          if (ae.mode === 'play') { const at = ae.cursor; ae.buf = null; await aePlayer(t, ae.ui.pane); ae.cursor = Math.min(at, aeLen(t)); aeChrome(t); aeDraw(t); aeStatus(t, 'Changed on disk: showing the new version', 'ok'); }
          else if (ae.st && ae.st.v === ae.st.saved_v && Math.abs(mtime - ae.st.src_mtime) > 1e-3) {
            try { sessionStorage.removeItem(aeKey(t)); } catch { /* ignore */ }
            ae.st = null; ae.buf = null; await aeEditor(t, ae.ui.pane); aeStatus(t, 'Changed on disk: opened the new version', 'ok');
          } else if (ae.st && Math.abs(mtime - ae.st.src_mtime) > 1e-3) aeStatus(t, 'The file changed on disk (the agent?). Your unsaved edits are kept; Save will ask before replacing it.', 'err');
        }
      } catch { /* try again later */ }
    }
    ae.timer = setTimeout(tick, 3000);
  };
  ae.timer = setTimeout(tick, 3000);
}

/* the player: what a sound opens as */
async function aePlayer(t, pane) {
  const ae = t.ae, tr = aeTransport(t);
  const info = el('span', { class: 'ae-info' }), status = el('span', { class: 'ae-status', role: 'status' });
  const wave = el('canvas', { class: 'ae-wave', tabindex: '0', 'aria-label': 'Waveform: click or drag to move the playhead' });
  const stage = el('div', { class: 'ae-stage' }, wave);
  const vol = el('input', { type: 'range', class: 'ae-vol', min: '0', max: '1', step: '0.01', value: String(ae.vol), 'aria-label': 'Playback volume',
    oninput: () => { ae.vol = +vol.value; if (ae.gain) ae.gain.gain.value = ae.vol; } });
  const editB = el('button', { class: 'btn primary ae-editbtn', title: 'Open the wave editor: cut, fades, volume, speed, pitch, effects', onclick: () => aeEnterEdit(t) }, aeIco('edit', 15), el('span', { text: 'Edit' }));
  const card = el('div', { class: 'ae-card' },
    el('div', { class: 'ae-ptop' }, el('span', { class: 'ae-ico', html: ico('media', 18) }),
      el('div', { class: 'ae-titles' }, el('b', { class: 'ae-name', text: basename(t.path) }), info), status, downloadBtn(t.path, 'btn ae-dl'), editB),
    stage,
    el('div', { class: 'ae-pbar' }, tr.playB, tr.backB, tr.clock, el('span', { class: 'ae-grow' }), tr.loopB,
      el('label', { class: 'ae-volbox', title: 'Playback volume (does not change the file)' }, aeIco('volume', 15), vol)));
  const root = el('div', { class: 'ae ae-player', tabindex: '-1' }, card);
  aeMount(t, pane, root, { status, info, wave, stage, ...tr });
  if (ae.buf && !ae.st) { aeChrome(t); aeDraw(t); return; }
  ae.st = null; ae.sel = null; ae.marks = []; ae.cursor = 0; ae.seen = null;
  aeStatus(t, 'Loading…');
  try {
    const res = await fetch((await previewUrl(t.path)) + '?v=' + Date.now(), { credentials: 'same-origin' });
    if (!res.ok) throw new Error('could not load the sound (' + res.status + ')');
    const buf = await aeAudio().decodeAudioData(await res.arrayBuffer());
    if (t.ae !== ae || ae.mode !== 'play') return;
    ae.buf = buf; ae.sum = aeSummary(buf);
    aeStatus(t, ''); aeChrome(t); aeDraw(t);
  } catch (e) { aeStatus(t, e.message || String(e), 'err'); }
}

/* the wave editor */
async function aeEditor(t, pane) {
  const ae = t.ae, tr = aeTransport(t);
  const info = el('span', { class: 'ae-info' }), status = el('span', { class: 'ae-status', role: 'status' });
  const undoB = aeBtn('', 'Undo (Ctrl+Z)', () => aeStep(t, -1), 'ic', 'undo'), redoB = aeBtn('', 'Redo (Ctrl+Shift+Z)', () => aeStep(t, 1), 'ic', 'redo');
  const sep = () => el('span', { class: 'ae-sep' });
  const menus = AE_MENUS.map(([label, icon, items]) => { const b = aeBtn(label, label, () => aeMenuAt(b, items(t)), 'ae-menu', icon); b.append(aeIco('chevron-down', 12)); return b; });
  const bar = el('div', { class: 'ae-bar', role: 'toolbar', 'aria-label': 'Edit the sound' },
    undoB, redoB, sep(),
    aeBtn('', 'Cut (Ctrl+X)', () => aeEdit(t, 'cut'), 'ic', 'scissors'), aeBtn('', 'Copy (Ctrl+C)', () => aeEdit(t, 'copy'), 'ic', 'copy'),
    aeBtn('', 'Paste at the cursor, or over the selection (Ctrl+V)', () => aeEdit(t, 'paste'), 'ic', 'clipboard'),
    aeBtn('', 'Delete the selection (Delete)', () => aeEdit(t, 'delete'), 'ic', 'trash'), sep(),
    ...menus,
    el('span', { class: 'ae-grow' }),
    aeBtn('', 'Zoom out (-)', () => aeZoom(t, 0.5), 'ic ae-wide', 'zoom-out'), aeBtn('', 'Zoom in (+)', () => aeZoom(t, 2), 'ic ae-wide', 'zoom-in'),
    aeBtn('Fit', 'Show the whole sound', () => aeFit(t), 'ae-wide'), aeBtn('Selection', 'Zoom to the selection', () => aeZoomSel(t), 'ae-wide'));
  const moreB = aeBtn('', 'More', () => aeMenuAt(moreB, [{ label: 'Save as…', run: () => aeSave(t, true) }, { label: 'Download the saved file', run: () => downloadPaths([t.path]) }, { label: 'Ask the agent about this part', run: () => aeAskAgent(t) },
    '-', { label: 'Zoom to the selection', run: () => aeZoomSel(t) }, { label: 'Show the whole sound', run: () => aeFit(t) }]), 'ic ae-narrow', 'more');
  const head = el('div', { class: 'ae-head' }, el('span', { class: 'ae-ico', html: ico('media', 18) }),
    el('div', { class: 'ae-titles' }, el('b', { class: 'ae-name', text: basename(t.path) }), info), status,
    el('span', { class: 'ae-acts' },
      aeBtn('Ask the agent', 'Put a note about the selected part in the chat box', () => aeAskAgent(t), 'ae-wide'),
      aeBtn('Save as', 'Save as a new file', () => aeSave(t, true), 'ae-wide'),
      aeBtn('Save', 'Save over the file (Ctrl+S); the old one goes to the trash', () => aeSave(t), 'primary', 'save'), moreB,
      aeBtn('Done', 'Close the editor and go back to the player', () => aeDone(t), '', 'check')));
  const over = el('canvas', { class: 'ae-over', 'aria-label': 'The whole sound: drag to move the view' });
  const wave = el('canvas', { class: 'ae-wave', tabindex: '0', 'aria-label': 'Waveform: drag to select, click to place the cursor' });
  const stage = el('div', { class: 'ae-stage' }, wave);
  const selInfo = el('span', { class: 'ae-selinfo' });
  const foot = el('div', { class: 'ae-pbar' }, tr.playB, tr.backB, tr.loopB, tr.clock, selInfo, el('span', { class: 'ae-grow' }),
    el('span', { class: 'ae-hint ae-wide', text: 'Drag to select · drag the bar on top to move it · Ctrl+wheel zooms · M marks a cut' }));
  const root = el('div', { class: 'ae ae-editor', tabindex: '-1' }, head, bar, over, stage, foot);
  aeMount(t, pane, root, { status, info, wave, stage, over, undoB, redoB, selInfo, bar, ...tr });
  if (ae.st && ae.buf) { aeDraw(t); aeChrome(t); return; }
  aeStatus(t, 'Opening the editor…'); ae.seen = null;
  try {
    let id = null; try { id = sessionStorage.getItem(aeKey(t)); } catch { /* ignore */ }
    const st = await POST('/api/audio/open', { ws: S.ws.id, path: t.path, id });
    try { sessionStorage.setItem(aeKey(t), st.id); } catch { /* ignore */ }
    ae.sel = null; ae.cursor = 0;
    await aeLoad(t, st);
    aeFit(t);
    aeStatus(t, st.v ? 'Picked up your earlier edit' : '');
  } catch (e) { aeStatus(t, e.message || String(e), 'err'); }
}
function aeEnterEdit(t) {
  const ae = t.ae; aeStop(t, true);
  ae.mode = 'edit'; ae.buf = null; ae.st = null;
  showAudio(t, ae.ui.pane);
}
async function aeDone(t) {
  const ae = t.ae; if (ae.busy) return;
  if (ae.st && ae.st.v !== ae.st.saved_v) {
    const r = await dialog({ title: 'Unsaved changes', body: `Save the changes to ${basename(t.path)}?`, buttons: [{ label: 'Cancel', value: 'cancel' }, { label: "Don't save", value: 'no' }, { label: 'Save', value: 'save', kind: 'primary' }] });
    if (r === 'save') { await aeSave(t); if (isDirty(t)) return; } else if (r !== 'no') return;
  }
  try { sessionStorage.removeItem(aeKey(t)); } catch { /* ignore */ }
  aeStop(t, true);
  ae.mode = 'play'; ae.st = null; ae.buf = null; ae.sel = null; ae.marks = [];
  refreshDirty(t);
  showAudio(t, ae.ui.pane);
}
function aeSetSel(t, sel) { t.ae.sel = sel; aeChrome(t); aeDraw(t); }
function aeStatus(t, text, kind = '') { const u = t.ae.ui; if (u) { u.status.textContent = text; u.status.className = 'ae-status ' + kind; } }
async function aeLoad(t, st) {
  const ae = t.ae;
  const res = await fetch(`/api/audio/sound?ws=${encodeURIComponent(S.ws.id)}&id=${st.id}&file=${encodeURIComponent(st.version)}`, { credentials: 'same-origin', headers: { 'X-Studio': '1' } });
  if (!res.ok) throw new Error('could not load the sound (' + res.status + ')');
  const buf = await aeAudio().decodeAudioData(await res.arrayBuffer());
  ae.st = st; ae.buf = buf; ae.sum = aeSummary(buf);
  refreshDirty(t);
  aeChrome(t);
  aeDraw(t);
}
function aeSummary(buf) {
  // min and max of every AE_BLOCK samples, per channel, for drawing zoomed out
  const out = [];
  for (let c = 0; c < buf.numberOfChannels; c++) {
    const d = buf.getChannelData(c), n = Math.ceil(d.length / AE_BLOCK), lo = new Float32Array(n), hi = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      let mn = 1, mx = -1; const end = Math.min(d.length, (i + 1) * AE_BLOCK);
      for (let j = i * AE_BLOCK; j < end; j++) { const v = d[j]; if (v < mn) mn = v; if (v > mx) mx = v; }
      lo[i] = mn; hi[i] = mx;
    }
    out.push({ lo, hi });
  }
  return out;
}
function aeLen(t) { return t.ae.buf ? t.ae.buf.duration : 0; }
function aeChrome(t) {
  const ae = t.ae, u = ae.ui; if (!u) return;
  const len = aeLen(t), rate = ae.st ? ae.st.rate : null, ch = ae.st ? ae.st.channels : ae.buf?.numberOfChannels;
  u.info.textContent = !ae.buf ? '' : [aeTime(len, false), rate ? (rate / 1000).toFixed(1) + ' kHz' : null, ch === 1 ? 'mono' : 'stereo'].filter(Boolean).join(' · ')
    + (ae.st && ae.st.v !== ae.st.saved_v ? ' · not saved' : '');
  if (u.undoB && ae.st) {
    u.undoB.disabled = !ae.st.can_undo; u.redoB.disabled = !ae.st.can_redo;
    u.undoB.title = ae.st.can_undo ? `Undo ${ae.st.history[ae.st.v]} (Ctrl+Z)` : 'Nothing to undo';
    u.redoB.title = ae.st.can_redo ? `Redo ${ae.st.history[ae.st.v + 1]} (Ctrl+Shift+Z)` : 'Nothing to redo';
  }
  u.playB.replaceChildren(aeIco(ae.play ? 'pause' : 'play', 20));
  u.playB.setAttribute('aria-label', ae.play ? 'Pause' : 'Play'); u.playB.title = (ae.play ? 'Pause' : 'Play') + ' (Space)';
  u.playB.classList.toggle('on', !!ae.play);
  const s = ae.sel;
  if (u.selInfo) u.selInfo.textContent = s ? `Selected ${aeTime(s.a)} – ${aeTime(s.b)} · ${(s.b - s.a).toFixed(2)} s` : 'Nothing selected: edits change the whole sound';
  if (u.selInfo) u.selInfo.classList.toggle('on', !!s);
  aeClock(t);
}
function aeClock(t) { const ae = t.ae; if (ae.ui) ae.ui.clock.textContent = `${aeTime(ae.play ? aeNow(t) : ae.cursor)} / ${aeTime(aeLen(t))}`; }

/* drawing */
function aeCanvas(cv) {
  const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1, w = Math.max(1, Math.round(r.width * dpr)), h = Math.max(1, Math.round(r.height * dpr));
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { g, w: r.width, h: r.height };
}
const aeCss = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
function aeX(t, s) { const v = t.ae.view; return (s - v.start) * v.pps; }
function aeT(t, x) { const v = t.ae.view; return clamp(v.start + x / v.pps, 0, aeLen(t)); }
function aeDraw(t) {
  const ae = t.ae, u = ae.ui; if (!u || !u.wave.isConnected) return;
  const { g, w, h } = aeCanvas(u.wave);
  g.clearRect(0, 0, w, h);
  if (!ae.buf) return;
  if (!ae.view.pps || ae.mode === 'play') { ae.view.pps = w / Math.max(0.01, aeLen(t)); ae.view.start = 0; }
  aeClampView(t, w);
  const colors = { played: aeCss('--wave-played'), wave: aeCss('--wave'), sel: aeCss('--wave-sel'), head: aeCss('--wave-head'), grid: aeCss('--border-strong'), dim: aeCss('--fg-faint'), fg: aeCss('--fg-dim'), mark: aeCss('--wave-mark'), bg: aeCss('--bg-soft') };
  // ruler
  g.fillStyle = colors.bg; g.fillRect(0, 0, w, AE_RULER);
  const span = w / ae.view.pps, steps = [0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  const step = steps.find(s => s * ae.view.pps >= 70) || 600;
  g.font = '10px system-ui, sans-serif'; g.fillStyle = colors.fg; g.strokeStyle = colors.grid; g.lineWidth = 1;
  for (let s = Math.floor(ae.view.start / step) * step; s <= ae.view.start + span; s += step) {
    const x = Math.round(aeX(t, s)) + 0.5; g.beginPath(); g.moveTo(x, AE_RULER - 6); g.lineTo(x, AE_RULER); g.stroke();
    if (x >= 0) g.fillText(step < 1 ? s.toFixed(step < 0.01 ? 3 : 2) + 's' : aeTime(s, false), x + 3, 12);
  }
  // selection
  const top = AE_RULER, H = h - top;
  if (ae.sel) {
    const x1 = aeX(t, ae.sel.a), x2 = aeX(t, ae.sel.b);
    g.fillStyle = colors.sel; g.fillRect(x1, top, Math.max(1, x2 - x1), H);
    g.fillStyle = colors.head; g.globalAlpha = 0.55; g.fillRect(x1, top, Math.max(1, x2 - x1), AE_GRAB - 4); g.globalAlpha = 1;
  }
  // waveform, one lane per channel
  const lanes = ae.buf.numberOfChannels, laneH = H / lanes, rate = ae.buf.sampleRate, perPx = rate / ae.view.pps;
  for (let c = 0; c < lanes; c++) {
    const mid = top + laneH * c + laneH / 2, amp = laneH / 2 - 3, d = ae.buf.getChannelData(c), sum = ae.sum[c];
    g.strokeStyle = colors.grid; g.beginPath(); g.moveTo(0, Math.round(mid) + 0.5); g.lineTo(w, Math.round(mid) + 0.5); g.stroke();
    if (c) { g.beginPath(); g.moveTo(0, top + laneH * c + 0.5); g.lineTo(w, top + laneH * c + 0.5); g.stroke(); }
    g.fillStyle = colors.wave;
    const playedX = aeX(t, ae.play ? aeNow(t) : ae.cursor);
    if (perPx < 1.5) {                      // close in: the samples themselves as a line
      g.strokeStyle = colors.wave; g.beginPath();
      const i0 = Math.max(0, Math.floor(ae.view.start * rate) - 1), i1 = Math.min(d.length - 1, Math.ceil((ae.view.start + span) * rate) + 1);
      for (let i = i0; i <= i1; i++) { const x = (i / rate - ae.view.start) * ae.view.pps, y = mid - d[i] * amp; i === i0 ? g.moveTo(x, y) : g.lineTo(x, y); }
      g.stroke();
      if (perPx < 0.08) for (let i = i0; i <= i1; i++) { const x = (i / rate - ae.view.start) * ae.view.pps; g.fillRect(x - 1.5, mid - d[i] * amp - 1.5, 3, 3); }
      continue;
    }
    for (let x = 0; x < w; x++) {
      const s0 = (ae.view.start + x / ae.view.pps) * rate, s1 = s0 + perPx;
      if (s0 >= d.length) break;
      let mn = 1, mx = -1;
      if (perPx >= AE_BLOCK * 2) {
        for (let b = Math.floor(s0 / AE_BLOCK), e = Math.min(sum.lo.length, Math.ceil(s1 / AE_BLOCK)); b < e; b++) { if (sum.lo[b] < mn) mn = sum.lo[b]; if (sum.hi[b] > mx) mx = sum.hi[b]; }
      } else {
        for (let i = Math.floor(s0), e = Math.min(d.length, Math.ceil(s1)); i < e; i++) { const v = d[i]; if (v < mn) mn = v; if (v > mx) mx = v; }
      }
      if (mx < mn) continue;
      g.fillStyle = x < playedX ? colors.played : colors.wave;
      g.fillRect(x, mid - mx * amp, 1, Math.max(1, (mx - mn) * amp));
    }
  }
  // cut marks
  g.strokeStyle = colors.mark; g.fillStyle = colors.mark; g.setLineDash([4, 3]);
  for (const m of ae.marks) {
    const x = Math.round(aeX(t, m)) + 0.5; if (x < -6 || x > w + 6) continue;
    g.beginPath(); g.moveTo(x, top); g.lineTo(x, h); g.stroke();
    g.beginPath(); g.moveTo(x - 5, 0); g.lineTo(x + 5, 0); g.lineTo(x, 9); g.closePath(); g.fill();
  }
  g.setLineDash([]);
  // cursor and playhead
  const head = ae.play ? aeNow(t) : ae.cursor, hx = Math.round(aeX(t, head)) + 0.5;
  g.strokeStyle = colors.head; g.lineWidth = ae.play ? 2 : 1; g.beginPath(); g.moveTo(hx, 0); g.lineTo(hx, h); g.stroke(); g.lineWidth = 1;
  if (ae.drag && ae.drag.kind === 'move' && ae.drag.to != null) {
    const x = Math.round(aeX(t, ae.drag.to)) + 0.5; g.strokeStyle = colors.mark; g.lineWidth = 2; g.beginPath(); g.moveTo(x, top); g.lineTo(x, h); g.stroke(); g.lineWidth = 1;
  }
  if (u.over) aeOverview(t, colors);
}
function aeOverview(t, colors) {
  const ae = t.ae, u = ae.ui, { g, w, h } = aeCanvas(u.over), len = aeLen(t);
  g.clearRect(0, 0, w, h); g.fillStyle = colors.bg; g.fillRect(0, 0, w, h);
  if (!len) return;
  const sum = ae.sum[0], per = sum.lo.length / w;
  g.fillStyle = colors.wave; g.globalAlpha = 0.7;
  for (let x = 0; x < w; x++) {
    let mn = 1, mx = -1;
    for (let b = Math.floor(x * per), e = Math.max(Math.floor(x * per) + 1, Math.floor((x + 1) * per)); b < e && b < sum.lo.length; b++) { if (sum.lo[b] < mn) mn = sum.lo[b]; if (sum.hi[b] > mx) mx = sum.hi[b]; }
    if (mx >= mn) g.fillRect(x, h / 2 - mx * h / 2, 1, Math.max(1, (mx - mn) * h / 2));
  }
  g.globalAlpha = 1;
  const span = (u.wave.getBoundingClientRect().width || w) / ae.view.pps, x1 = ae.view.start / len * w, x2 = Math.min(w, (ae.view.start + span) / len * w);
  g.strokeStyle = colors.head; g.lineWidth = 1.5; g.strokeRect(x1 + 0.75, 0.75, Math.max(3, x2 - x1 - 1.5), h - 1.5); g.lineWidth = 1;
  if (ae.sel) { g.fillStyle = colors.sel; g.fillRect(ae.sel.a / len * w, 0, Math.max(1, (ae.sel.b - ae.sel.a) / len * w), h); }
}
function aeClampView(t, w) {
  const ae = t.ae, len = aeLen(t);
  w = w || ae.ui.wave.getBoundingClientRect().width || 600;
  ae.view.pps = clamp(ae.view.pps, w / Math.max(0.01, len), 40 * ae.buf.sampleRate / 100);
  ae.view.start = clamp(ae.view.start, 0, Math.max(0, len - w / ae.view.pps));
}
function aeFit(t) { const ae = t.ae; if (!ae.buf) return; ae.view.pps = 0; ae.view.start = 0; aeDraw(t); }
function aeZoom(t, f, at = null) {
  const ae = t.ae; if (!ae.buf) return;
  const w = ae.ui.wave.getBoundingClientRect().width, focus = at ?? (ae.sel ? (ae.sel.a + ae.sel.b) / 2 : ae.play ? aeNow(t) : ae.cursor);
  const fx = clamp(aeX(t, focus), 0, w);
  ae.view.pps *= f; aeClampView(t, w);
  ae.view.start = focus - fx / ae.view.pps; aeClampView(t, w); aeDraw(t);
}
function aeZoomSel(t) {
  const ae = t.ae; if (!ae.sel) return aeFit(t);
  const w = ae.ui.wave.getBoundingClientRect().width, span = ae.sel.b - ae.sel.a;
  ae.view.pps = w / (span * 1.1); aeClampView(t, w); ae.view.start = ae.sel.a - span * 0.05; aeClampView(t, w); aeDraw(t);
}
function aeShow(t, s) {
  const ae = t.ae, w = ae.ui.wave.getBoundingClientRect().width, span = w / ae.view.pps;
  if (s < ae.view.start || s > ae.view.start + span * 0.95) { ae.view.start = s - span * 0.1; aeClampView(t, w); }
}

/* pointer, wheel and keys */
function aeWire(t) {
  const ae = t.ae, u = ae.ui, cv = u.wave, pts = new Map();
  const pos = e => { const r = cv.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
  const edge = x => { if (!ae.sel) return null; const a = aeX(t, ae.sel.a), b = aeX(t, ae.sel.b); return Math.abs(x - a) < 6 ? 'a' : Math.abs(x - b) < 6 ? 'b' : null; };
  cv.addEventListener('pointerdown', e => {
    if (!ae.buf || e.button > 0) return;
    cv.focus({ preventScroll: true });
    pts.set(e.pointerId, pos(e));
    cv.setPointerCapture(e.pointerId);
    if (pts.size === 2 && ae.mode === 'edit') {               // two fingers: pinch to zoom, move to scroll
      const [p, q] = [...pts.values()];
      ae.drag = { kind: 'pinch', d: Math.hypot(p.x - q.x, p.y - q.y) || 1, mid: (p.x + q.x) / 2, pps: ae.view.pps, at: aeT(t, (p.x + q.x) / 2) };
      return;
    }
    const { x, y } = pos(e), at = aeT(t, x);
    if (ae.mode === 'play') { ae.drag = { kind: 'seek' }; aeSeek(t, at); return; }
    if (y < AE_RULER) {
      const m = ae.marks.findIndex(v => Math.abs(aeX(t, v) - x) < 6);
      if (m >= 0) { ae.drag = { kind: 'mark', i: m }; return; }
      ae.drag = { kind: 'seek' }; aeSeek(t, at); return;
    }
    const inSel = ae.sel && at > ae.sel.a && at < ae.sel.b;
    if (ae.sel && inSel && (y < AE_RULER + AE_GRAB || e.altKey)) { ae.drag = { kind: 'move', off: at - ae.sel.a, to: null }; cv.style.cursor = 'grabbing'; return; }
    const ed = edge(x);
    if (ed) { ae.drag = { kind: 'edge', which: ed }; return; }
    if (e.shiftKey) { const from = ae.sel ? (Math.abs(at - ae.sel.a) < Math.abs(at - ae.sel.b) ? ae.sel.b : ae.sel.a) : ae.cursor; ae.sel = { a: Math.min(from, at), b: Math.max(from, at) }; ae.drag = { kind: 'edge', which: at < from ? 'a' : 'b' }; aeChrome(t); aeDraw(t); return; }
    ae.drag = { kind: 'select', from: at, x0: x };
  });
  cv.addEventListener('pointermove', e => {
    const p = pos(e);
    if (pts.has(e.pointerId)) pts.set(e.pointerId, p);
    const d = ae.drag;
    if (!d) {                            // hover: show what a press would do
      if (ae.mode === 'play') { cv.style.cursor = 'pointer'; return; }
      const inSel = ae.sel && aeT(t, p.x) > ae.sel.a && aeT(t, p.x) < ae.sel.b;
      cv.style.cursor = p.y < AE_RULER ? 'pointer' : edge(p.x) ? 'ew-resize' : inSel && (p.y < AE_RULER + AE_GRAB || e.altKey) ? 'grab' : 'text';
      return;
    }
    if (d.kind === 'pinch' && pts.size === 2) {
      const [a, b] = [...pts.values()], dist = Math.hypot(a.x - b.x, a.y - b.y) || 1, mid = (a.x + b.x) / 2;
      ae.view.pps = d.pps * dist / d.d; aeClampView(t); ae.view.start = d.at - mid / ae.view.pps; aeClampView(t); aeDraw(t); return;
    }
    const at = aeT(t, p.x);
    if (d.kind === 'seek') aeSeek(t, at);
    else if (d.kind === 'mark') { ae.marks[d.i] = at; aeDraw(t); }
    else if (d.kind === 'select') { if (Math.abs(p.x - d.x0) > 3) { ae.sel = { a: Math.min(d.from, at), b: Math.max(d.from, at) }; aeChrome(t); aeDraw(t); } }
    else if (d.kind === 'edge') {
      const other = d.which === 'a' ? ae.sel.b : ae.sel.a;
      if ((d.which === 'a' && at > other) || (d.which === 'b' && at < other)) d.which = d.which === 'a' ? 'b' : 'a';
      ae.sel[d.which] = at; if (ae.sel.a > ae.sel.b) [ae.sel.a, ae.sel.b] = [ae.sel.b, ae.sel.a];
      aeChrome(t); aeDraw(t);
    } else if (d.kind === 'move') { const s = clamp(at - d.off, 0, aeLen(t) - (ae.sel.b - ae.sel.a)); d.to = Math.abs(s - ae.sel.a) < 0.0005 ? null : s; aeDraw(t); }
    if (d.kind !== 'pinch') aeEdgeScroll(t, p.x);
  });
  const up = e => {
    pts.delete(e.pointerId);
    const d = ae.drag; if (!d) return;
    if (d.kind === 'pinch') { if (!pts.size) ae.drag = null; return; }
    ae.drag = null; cv.style.cursor = '';
    if (d.kind === 'select' && !ae.sel?.b) { /* nothing */ }
    if (d.kind === 'select' && Math.abs(pos(e).x - d.x0) <= 3) { ae.sel = null; aeSeek(t, d.from); }
    else if (d.kind === 'move' && d.to != null) {
      const span = ae.sel.b - ae.sel.a, to = d.to < ae.sel.a ? d.to : d.to + span;     // where the part goes, counted on the sound as it is now
      aeEdit(t, 'move', { to });
    } else if (d.kind === 'select' && ae.sel && ae.sel.b - ae.sel.a < 0.0005) ae.sel = null;
    if (d.kind === 'select' && ae.sel && !ae.play) ae.cursor = ae.sel.a;
    aeChrome(t); aeDraw(t);
  };
  cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
  cv.addEventListener('dblclick', e => {
    const { x, y } = pos(e);
    if (ae.mode === 'play') return;
    const m = ae.marks.findIndex(v => Math.abs(aeX(t, v) - x) < 6);
    if (m >= 0) { ae.marks.splice(m, 1); aeDraw(t); return; }
    if (y >= AE_RULER && ae.buf) { ae.sel = { a: 0, b: aeLen(t) }; aeChrome(t); aeDraw(t); }
  });
  cv.addEventListener('wheel', e => {
    if (!ae.buf || ae.mode === 'play') return;
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) { aeZoom(t, Math.exp(-e.deltaY * 0.002), aeT(t, pos(e).x)); return; }
    const dx = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    ae.view.start += dx / ae.view.pps; aeClampView(t); aeDraw(t);
  }, { passive: false });
  // the overview: press or drag to move the view there
  const ov = e => { const r = u.over.getBoundingClientRect(), len = aeLen(t), span = u.wave.getBoundingClientRect().width / ae.view.pps; ae.view.start = clamp((e.clientX - r.left) / r.width * len - span / 2, 0, len); aeClampView(t); aeDraw(t); };
  if (u.over) u.over.addEventListener('pointerdown', e => { if (!ae.buf) return; u.over.setPointerCapture(e.pointerId); ae.overDrag = true; ov(e); });
  if (u.over) u.over.addEventListener('pointermove', e => { if (ae.overDrag) ov(e); });
  if (u.over) u.over.addEventListener('pointerup', () => { ae.overDrag = false; });
  u.root.addEventListener('keydown', e => aeKeys(t, e));
}
function aeEdgeScroll(t, x) {
  const ae = t.ae, w = ae.ui.wave.getBoundingClientRect().width;
  const push = x < 20 ? -1 : x > w - 20 ? 1 : 0;
  if (push) { ae.view.start += push * 30 / ae.view.pps; aeClampView(t); }
}
function aeKeys(t, e) {
  const ae = t.ae, mod = e.ctrlKey || e.metaKey, k = e.key;
  if (e.target.closest('input, select, textarea')) return;
  const take = fn => { e.preventDefault(); e.stopPropagation(); fn(); };
  if (k === ' ') return take(() => aeToggle(t));
  if (ae.mode === 'play') {
    if (k === 'Home') return take(() => aeSeek(t, 0));
    if (k === 'ArrowLeft' || k === 'ArrowRight') return take(() => aeSeek(t, ae.cursor + (k === 'ArrowLeft' ? -5 : 5)));
    return;
  }
  if (mod && (k === 'z' || k === 'Z')) return take(() => aeStep(t, e.shiftKey ? 1 : -1));
  if (mod && (k === 'y' || k === 'Y')) return take(() => aeStep(t, 1));
  if (mod && (k === 'x' || k === 'X')) return take(() => aeEdit(t, 'cut'));
  if (mod && (k === 'c' || k === 'C') && !window.getSelection()?.toString()) return take(() => aeEdit(t, 'copy'));
  if (mod && (k === 'v' || k === 'V')) return take(() => aeEdit(t, 'paste'));
  if (mod && (k === 'a' || k === 'A')) return take(() => { ae.sel = { a: 0, b: aeLen(t) }; aeChrome(t); aeDraw(t); });
  if (mod) return;
  if (k === 'Delete' || k === 'Backspace') return take(() => aeEdit(t, 'delete'));
  if (k === 'Escape' && ae.sel) return take(() => { ae.sel = null; aeChrome(t); aeDraw(t); });
  if (k === 'Home') return take(() => { aeSeek(t, 0); });
  if (k === 'End') return take(() => { aeSeek(t, aeLen(t)); });
  if (k === 'm' || k === 'M') return take(() => aeMark(t));
  if (k === '+' || k === '=') return take(() => aeZoom(t, 2));
  if (k === '-') return take(() => aeZoom(t, 0.5));
  if (k === 'ArrowLeft' || k === 'ArrowRight') return take(() => aeSeek(t, ae.cursor + (k === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 1 : 0.1)));
}

/* playback */
function aeNow(t) {
  const p = t.ae.play; if (!p) return t.ae.cursor;
  let at = p.from + (aeAudio().currentTime - p.t0);
  if (p.loop && at > p.to) at = p.from + ((at - p.from) % (p.to - p.from));
  return Math.min(at, p.to);
}
function aePlay(t) {
  const ae = t.ae; if (!ae.buf) return;
  const was = ae.play ? aeNow(t) : null;
  aeStop(t, true);
  const ctx = aeAudio(); if (ctx.state === 'suspended') ctx.resume();
  const len = aeLen(t), sel = ae.sel;
  let from = sel ? sel.a : ae.cursor, to = sel ? sel.b : len;
  if (!sel && from >= len - 0.01) from = 0;
  if (sel && was != null && was > sel.a && was < sel.b) from = was;
  const src = ctx.createBufferSource(); src.buffer = ae.buf;
  ae.gain = ctx.createGain(); ae.gain.gain.value = ae.mode === 'play' ? ae.vol : 1;
  src.connect(ae.gain); ae.gain.connect(ctx.destination);
  const loop = ae.loop, lo = sel ? sel.a : 0;
  if (loop) { src.loop = true; src.loopStart = lo; src.loopEnd = to; src.start(0, from); }
  else src.start(0, from, Math.max(0.001, to - from));
  ae.play = { src, t0: ctx.currentTime, from: loop ? lo : from, to, loop };
  if (loop) ae.play.t0 -= from - lo;
  src.onended = () => { if (ae.play && ae.play.src === src) { ae.play = null; aeChrome(t); aeDraw(t); } };
  aeChrome(t);
  const tick = () => {
    if (!ae.play || ae.play.src !== src) return;
    if (ae.ui?.wave.isConnected && S.active === t) { aeShow(t, aeNow(t)); aeDraw(t); aeClock(t); }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
function aeStop(t, quiet = false) {
  const ae = t.ae; if (!ae || !ae.play) return;
  const p = ae.play; ae.play = null;
  try { p.src.onended = null; p.src.stop(); } catch { /* ignore */ }
  if (!quiet) { aeChrome(t); aeDraw(t); }
}
function aeToggle(t) {
  const ae = t.ae;
  if (ae.play) { const at = aeNow(t); aeStop(t); if (!ae.sel) ae.cursor = at; aeChrome(t); aeDraw(t); }
  else aePlay(t);
}
function aeSeek(t, s) {
  const ae = t.ae; ae.cursor = clamp(s, 0, aeLen(t));
  if (ae.play && !ae.sel) aePlay(t);
  aeShow(t, ae.cursor); aeChrome(t); aeDraw(t);
}

/* edits */
async function aeEdit(t, op, extra = {}) {
  const ae = t.ae; if (!ae.st || ae.busy) return;
  const len = aeLen(t);
  let sel = ae.sel;
  if (!sel && (op === 'fade_in' || op === 'fade_out')) sel = op === 'fade_in' ? { a: 0, b: Math.min(2, len) } : { a: Math.max(0, len - 2), b: len };
  if (!sel && ['cut', 'copy', 'delete', 'crop', 'silence', 'move'].includes(op)) return toast('Select a part first: drag across the waveform.', 'info', 3000);
  const params = { ...extra };
  if (sel) { params.a = sel.a; params.b = sel.b; }
  if ((op === 'paste' || op === 'insert_silence') && !sel) params.at = ae.cursor;
  const playing = !!ae.play;
  aeStop(t);
  ae.busy = true; ae.ui.root.classList.add('busy');
  aeStatus(t, { copy: 'Copying…', cut: 'Cutting…' }[op] || 'Working…');
  try {
    const st = await POST('/api/audio/edit', { ws: S.ws.id, id: ae.st.id, op, params });
    if (op === 'copy') { ae.st = st; aeStatus(t, 'Copied ' + (sel.b - sel.a).toFixed(2) + ' s', 'ok'); return; }
    const delta = st.seconds - ae.st.seconds;
    await aeLoad(t, st);
    aeAfter(t, op, sel, params, delta);
    aeStatus(t, st.history[st.v], 'ok');
    if (playing) aePlay(t);
  } catch (e) { aeStatus(t, e.message || String(e), 'err'); }
  finally { ae.busy = false; ae.ui?.root.classList.remove('busy'); aeChrome(t); aeDraw(t); }
}
function aeAfter(t, op, sel, params, delta) {
  // where the selection, cursor and marks belong after an edit that may have changed the length
  const ae = t.ae, len = aeLen(t);
  const shiftMarks = (from, d, dropTo = null) => { ae.marks = ae.marks.filter(m => dropTo == null || m <= from || m >= dropTo).map(m => m >= (dropTo ?? from) ? m + d : m).filter(m => m > 0 && m < len); };
  if (op === 'delete' || op === 'cut') { shiftMarks(sel.a, delta, sel.b); ae.sel = null; ae.cursor = sel.a; }
  else if (op === 'crop') { ae.marks = ae.marks.filter(m => m > sel.a && m < sel.b).map(m => m - sel.a); ae.sel = null; ae.cursor = 0; aeFit(t); }
  else if (op === 'insert_silence' || op === 'paste') {
    const at = sel ? sel.a : params.at, added = sel ? delta + (sel.b - sel.a) : delta;
    shiftMarks(sel ? sel.b : at, delta);
    ae.sel = added > 0.0005 ? { a: at, b: at + added } : null; ae.cursor = at;
  } else if (op === 'move') {
    const span = sel.b - sel.a, start = params.to < sel.a ? params.to : params.to - span;
    ae.sel = { a: start, b: start + span }; ae.cursor = start;
  } else if (sel && Math.abs(delta) > 0.0005 && (op === 'speed' || op === 'pitch')) { shiftMarks(sel.b, delta); ae.sel = { a: sel.a, b: Math.max(sel.a + 0.001, sel.b + delta) }; }
  if (ae.sel) { ae.sel.a = clamp(ae.sel.a, 0, len); ae.sel.b = clamp(ae.sel.b, 0, len); if (ae.sel.b - ae.sel.a < 0.0005) ae.sel = null; }
  ae.cursor = clamp(ae.cursor, 0, len);
  aeClampView(t);
}
async function aeStep(t, dir) {
  const ae = t.ae; if (!ae.st || ae.busy) return;
  if ((dir < 0 && !ae.st.can_undo) || (dir > 0 && !ae.st.can_redo)) return;
  aeStop(t);
  ae.busy = true;
  try {
    const label = dir < 0 ? ae.st.history[ae.st.v] : ae.st.history[ae.st.v + 1];
    const st = await POST('/api/audio/step', { ws: S.ws.id, id: ae.st.id, redo: dir > 0 });
    await aeLoad(t, st);
    if (ae.sel) { ae.sel.b = Math.min(ae.sel.b, aeLen(t)); if (ae.sel.b - ae.sel.a < 0.0005) ae.sel = null; }
    ae.cursor = Math.min(ae.cursor, aeLen(t)); aeClampView(t);
    aeStatus(t, (dir < 0 ? 'Undid ' : 'Redid ') + label, 'ok');
  } catch (e) { aeStatus(t, e.message || String(e), 'err'); }
  finally { ae.busy = false; aeChrome(t); aeDraw(t); }
}

/* settings for the edits that take some */
function aeField(label, input, hint) { return el('label', { class: 'ae-field' }, el('span', { class: 'ae-flabel', text: label }), input, hint ? el('span', { class: 'ae-fhint', text: hint }) : null); }
function aeRange(min, max, step, value, fmt) {
  const out = el('output', { class: 'ae-out', text: fmt(value) });
  const r = el('input', { type: 'range', min, max, step, value, oninput: () => { out.textContent = fmt(+r.value); } });
  return { node: el('span', { class: 'ae-range' }, r, out), get: () => +r.value };
}
function aeSelect(options, value) {
  const s = el('select', { class: 'ae-select' }, options.map(([v, l]) => el('option', { value: v, selected: v === value ? '' : null, text: l })));
  return { node: s, get: () => s.value };
}
const AE_LAST = {};
async function aeAsk(t, op, fx = null) {
  const ae = t.ae; if (!ae.st) return;
  const last = AE_LAST[fx || op] || {};
  const where = ae.sel ? `the selection (${(ae.sel.b - ae.sel.a).toFixed(2)} s)` : 'the whole sound';
  let title, fields = [], read;
  if (op === 'insert_silence') {
    const sec = aeRange(0.1, 10, 0.1, last.seconds ?? 1, v => v.toFixed(1) + ' s');
    title = 'Insert silence'; fields = [aeField('Length', sec.node, ae.sel ? 'Replaces nothing: it goes in before the selection.' : `At the cursor, ${aeTime(ae.cursor)}.`)];
    read = () => ({ seconds: sec.get() });
  } else if (op === 'gain') {
    const db = aeRange(-24, 24, 0.5, last.db ?? 3, v => (v > 0 ? '+' : '') + v.toFixed(1) + ' dB');
    title = 'Volume'; fields = [aeField('Change', db.node, `For ${where}. +6 dB is about twice as loud; going too high clips.`)];
    read = () => ({ db: db.get() });
  } else if (op === 'normalize') {
    const mode = aeSelect([['peak', 'Loudest point (peak)'], ['loudness', 'Overall loudness (LUFS)']], last.mode || 'peak');
    const peak = aeRange(-12, 0, 0.5, last.peak ?? -1, v => v.toFixed(1) + ' dB'), lufs = aeRange(-30, -10, 1, last.lufs ?? -16, v => v + ' LUFS');
    const show = () => { peak.node.parentElement.classList.toggle('hidden', mode.get() !== 'peak'); lufs.node.parentElement.classList.toggle('hidden', mode.get() === 'peak'); };
    mode.node.addEventListener('change', show);
    title = 'Normalize'; fields = [aeField('Bring', mode.node, `For ${where}.`), aeField('Peak at', peak.node), aeField('Loudness', lufs.node, '-16 suits speech and the web, -14 music streaming.')];
    setTimeout(show);
    read = () => ({ mode: mode.get(), target: mode.get() === 'peak' ? peak.get() : lufs.get(), peak: peak.get(), lufs: lufs.get() });
  } else if (op === 'speed') {
    const f = aeRange(0.25, 4, 0.05, last.factor ?? 1.25, v => '×' + v.toFixed(2));
    const keep = el('input', { type: 'checkbox', checked: last.keep_pitch === false ? null : '' });
    title = 'Speed'; fields = [aeField('Speed', f.node, `For ${where}. ×2 is twice as fast, ×0.5 half.`), el('label', { class: 'ae-check' }, keep, ' Keep the pitch (otherwise faster also sounds higher, like a tape)')];
    read = () => ({ factor: f.get(), keep_pitch: keep.checked });
  } else if (op === 'pitch') {
    const s = aeRange(-12, 12, 0.5, last.semitones ?? 2, v => (v > 0 ? '+' : '') + v + ' semitones');
    title = 'Pitch'; fields = [aeField('Shift', s.node, `For ${where}; the speed stays. 12 semitones is an octave.`)];
    read = () => ({ semitones: s.get() });
  } else if (op === 'reverb') {
    const room = aeSelect([['room', 'Room'], ['hall', 'Hall'], ['church', 'Church'], ['plate', 'Plate']], last.room || 'hall');
    const mix = aeRange(0.05, 1, 0.05, last.mix ?? 0.4, v => Math.round(v * 100) + '%');
    title = 'Reverb'; fields = [aeField('Space', room.node), aeField('Amount', mix.node, `For ${where}; the echo rings on past its end.`)];
    read = () => ({ room: room.get(), mix: mix.get() });
  } else if (op === 'echo') {
    const kind = aeSelect([['short', 'Short'], ['slap', 'Slap-back'], ['long', 'Long'], ['canyon', 'Canyon']], last.kind || 'short');
    title = 'Echo'; fields = [aeField('Kind', kind.node, `For ${where}.`)];
    read = () => ({ kind: kind.get() });
  } else if (op === 'fx') {
    const def = AE_EFFECTS.find(x => x[0] === fx);
    const amount = def[2] ? aeRange(0, 1, 0.05, last.amount ?? 0.5, v => Math.round(v * 100) + '%') : null;
    title = def[1]; fields = [amount ? aeField('Amount', amount.node, `For ${where}.`) : el('p', { class: 'ae-fhint', text: `For ${where}.` })];
    read = () => ({ name: fx, amount: amount ? amount.get() : 0.5 });
  }
  const body = el('div', { class: 'ae-form' }, ...fields);
  const r = await dialog({ title, body, buttons: [{ label: 'Cancel', value: false }, { label: 'Apply', value: true, kind: 'primary' }] });
  if (r !== true) return;
  const params = read();
  AE_LAST[fx || op] = params;
  const { peak, lufs, ...send } = params;
  aeEdit(t, op, send);
}
function aeFxMenu(t, x, y) {
  showContextMenu(x, y, AE_EFFECTS.map(e => e === '-' ? '-' : { label: e[1], run: () => aeAsk(t, 'fx', e[0]) }));
}
function aeMark(t) {
  const ae = t.ae; if (!ae.buf) return;
  const at = ae.play ? aeNow(t) : ae.cursor;
  if (at <= 0.01 || at >= aeLen(t) - 0.01) return toast('Put the cursor inside the sound first.', 'info', 3000);
  if (!ae.marks.some(m => Math.abs(m - at) < 0.005)) ae.marks.push(at);
  ae.marks.sort((a, b) => a - b); aeDraw(t);
  aeStatus(t, `${ae.marks.length} mark${ae.marks.length > 1 ? 's' : ''}: Save slices makes ${ae.marks.length + 1} files`);
}
async function aeSlices(t) {
  const ae = t.ae; if (!ae.st) return;
  if (!ae.marks.length) return toast('Put cut marks first: place the cursor and press Mark (or M).', 'info', 4000);
  const stem = t.path.replace(/\.[^./]+$/, ''), ext = t.path.slice(stem.length);
  if (!(await confirmBox('Save slices', `Save ${ae.marks.length + 1} files beside the sound: ${basename(stem)}-01${ext}, ${basename(stem)}-02${ext}… The sound itself stays as it is.`, 'Save'))) return;
  try {
    const r = await POST('/api/audio/slices', { ws: S.ws.id, id: ae.st.id, cuts: ae.marks });
    aeStatus(t, `Saved ${r.saved.length} slices`, 'ok');
    toast(`Saved ${r.saved.map(basename).join(', ')}`, 'ok');
    refreshTree();
  } catch (e) { aeStatus(t, e.message || String(e), 'err'); }
}
async function aeSave(t, as = false, overwrite = false, path = null) {
  const ae = t.ae; if (!ae?.st || ae.busy) return;
  if (as && !path) {
    path = await promptBox('Save as', t.path.replace(/(\.[^./]+)$/, '-edit$1'), 'path/name.mp3 (.mp3 .wav .flac .ogg .opus .m4a)');
    if (!path) return;
  }
  ae.busy = true; aeStatus(t, 'Saving…');
  try {
    const r = await POST('/api/audio/save', { ws: S.ws.id, id: ae.st.id, path: path || undefined, overwrite });
    ae.st = r; refreshDirty(t); aeChrome(t);
    aeStatus(t, 'Saved ' + r.saved + (r.trash ? ' (the old file is in the trash)' : ''), 'ok');
    flashStatus('Saved ' + basename(r.saved));
    refreshTree();
    if (path) openFile(r.saved);
  } catch (e) {
    ae.busy = false;
    if (e.status === 409) {
      const ok = await confirmBox(path ? 'File exists' : 'File changed on disk', path ? `${path} already exists. Replace it? The old one goes to the trash.` : `${t.path} changed on disk since you opened it (perhaps the agent made it again). Save over it? The other version goes to the trash.`, 'Replace', true);
      if (ok) return aeSave(t, as, true, path);
      aeStatus(t, ''); return;
    }
    aeStatus(t, e.message || String(e), 'err');
  } finally { ae.busy = false; }
}
function aeAskAgent(t) {
  const ae = t.ae;
  const what = ae.sel ? `from ${aeTime(ae.sel.a)} to ${aeTime(ae.sel.b)}` : `at ${aeTime(ae.cursor)}`;
  const inp = $('#agent-input');
  inp.value = `@${t.path} In this sound, ${what}: ` + (inp.value.trim() ? '\n' + inp.value : '');
  autosize(); showSection('chat');
  inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length);
}

/* ------------------------------------------------------------------ other editors: Markdown, the video player, 3D models, CSV tables, video timelines */
/* Markdown files: shown as a document (the chat's renderer), Code switches to the editor */
const mdSlug = text => text.toLowerCase().trim().replace(/[^\p{L}\p{N}\s-]/gu, '').replace(/\s+/g, '-');
function mdJoin(dir, rel) {
  const parts = dir ? dir.split('/') : [];
  for (const p of decodeURIComponent(rel).split('/')) { if (p === '..') parts.pop(); else if (p && p !== '.') parts.push(p); }
  return parts.join('/');
}
async function showMarkdown(t, pane) {
  const text = tabValue(t), dir = dirname(t.path);
  let html;
  MD_DOC = t.path; try { html = mdToHtml(text); } finally { MD_DOC = null; }
  const doc = el('article', { class: 'm-asst md-doc', html });
  const used = new Map();
  doc.querySelectorAll('h1, h2, h3, h4').forEach(h => { let id = mdSlug(h.textContent) || 'section'; const n = used.get(id) || 0; used.set(id, n + 1); h.id = 'md-' + (n ? `${id}-${n}` : id); });
  doc.querySelectorAll('a[data-file]').forEach(a => {
    const target = a.dataset.file;
    a.title = target.startsWith('#') ? 'Go to ' + target.slice(1) : 'Open ' + target;
    a.addEventListener('click', e => {
      e.preventDefault();
      const [file, hash] = target.split('#');
      if (!file) { const h = doc.querySelector('#md-' + CSS.escape(mdSlug(decodeURIComponent(hash || '')))); h?.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
      openFile(mdJoin(dir, file));
    });
  });
  for (const img of doc.querySelectorAll('img[data-src]')) previewUrl(mdJoin(dir, img.dataset.src.split('#')[0])).then(u => { img.src = u; }).catch(() => { img.alt = img.alt || img.dataset.src; });
  renderMath(doc);
  const words = (text.match(/\S+/g) || []).length;
  const bar = el('div', { class: 'preview-bar md-bar' }, el('span', { class: 'preview-path', text: t.path }),
    el('span', { class: 'md-count', text: `${words.toLocaleString()} words · ${Math.max(1, Math.round(words / 220))} min read` }),
    isDirty(t) ? el('span', { class: 'preview-warn', text: 'Showing your unsaved edits' }) : null,
    downloadBtn(t.path),
    el('button', { class: 'btn sm', title: 'Edit the Markdown text', onclick: () => { t.preview = false; activateTab(t); } }, el('span', { html: ico('edit', 13) }), ' Edit'));
  const scroller = el('div', { class: 'md-scroll' }, doc);
  pane.replaceChildren(bar, scroller);
  if (t.mdScroll) scroller.scrollTop = t.mdScroll;
  scroller.addEventListener('scroll', () => { t.mdScroll = scroller.scrollTop; }, { passive: true });
}

/* Video files: a player card like the sounds', whose Edit opens the video's timeline, or starts one with it */
function showVideo(t, pane, live) {
  const info = el('span', { class: 'ae-info' }), status = el('span', { class: 'ae-status', role: 'status' });
  const video = el('video', { class: 'pv-video', src: live, controls: '', preload: 'metadata', playsinline: '' });
  video.addEventListener('loadedmetadata', () => { info.textContent = [tlClock(video.duration), video.videoWidth ? `${video.videoWidth}×${video.videoHeight}` : null].filter(Boolean).join(' · '); });
  const editB = el('button', { class: 'btn primary ae-editbtn', title: 'Edit it on a timeline: cut, add titles, music, transitions and effects', onclick: () => videoEdit(t.path, status) }, aeIco('edit', 15), el('span', { text: 'Edit' }));
  const card = el('div', { class: 'ae-card pv-card' },
    el('div', { class: 'ae-ptop' }, el('span', { class: 'tl-ico', html: ico('film', 18) }), el('div', { class: 'ae-titles' }, el('b', { class: 'ae-name', text: basename(t.path) }), info), status, downloadBtn(t.path, 'btn ae-dl'), editB),
    el('div', { class: 'pv-frame' }, video));
  pane.replaceChildren(el('div', { class: 'ae ae-player preview-video' }, card));
}
async function videoEdit(path, status) {
  const stem = path.replace(/\.[^./]+$/, ''), beside = stem + '.timeline.json';
  try { await GET('/api/video/timeline', { ws: S.ws.id, path: beside }); return openFile(beside); } catch { /* none beside it: start one */ }
  try {
    status.textContent = 'Starting a timeline…';
    const r = await POST('/api/video/new', { ws: S.ws.id, name: basename(stem) + ' edit', src: path });
    status.textContent = '';
    videoRefresh();
    openFile(r.path);
  } catch (e) { status.textContent = e.message || String(e); status.className = 'ae-status err'; }
}

async function showModel(t, pane, url, seq) {
  const stage = el('div', { class: 'model-stage' }), info = el('span', { class: 'model-info', text: 'Loading the 3D viewer…' });
  const tools = el('div', { class: 'model-tools', role: 'toolbar', 'aria-label': '3D view' });
  // the bar keeps the file's name and its actions; the view's buttons float over the model and its facts sit in a corner,
  // so nothing is squeezed on a phone (2026-11-17)
  pane.replaceChildren(el('div', { class: 'preview-bar' }, el('span', { class: 'preview-path', text: t.path }), el('span', { class: 'grow' }),
    el('button', { class: 'link-btn card-link', text: 'Reload', onclick: () => showPreview(t) }), downloadBtn(t.path)), el('div', { class: 'model-view' }, stage, info, tools));
  const fail = msg => { if (pane._seq === seq) stage.replaceChildren(el('div', { class: 'preview-note', text: msg })); info.textContent = ''; };
  let K, model;
  try { K = await loadThree(); } catch { return fail('The 3D viewer could not load (three.js comes from cdn.jsdelivr.net, so it needs the internet).'); }
  if (pane._seq !== seq) return;
  info.textContent = /\.blend$/i.test(t.path) ? 'Blender is exporting it…' : 'Loading the model…';
  try { model = await loadModel(K, t.path, url); } catch (e) { return fail('Could not open this model: ' + (e.message || e)); }
  if (pane._seq !== seq || S.active !== t) { disposeModel(model.scene); return; }
  const T = K.THREE;
  let renderer;
  try { renderer = new T.WebGLRenderer({ antialias: true, alpha: true }); } catch { disposeModel(model.scene); return fail('This browser cannot draw 3D (WebGL is off).'); }
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2)); renderer.toneMapping = T.ACESFilmicToneMapping; renderer.setClearColor(0, 0);
  const scene = new T.Scene(), camera = new T.PerspectiveCamera(45, 1, 0.01, 1000);
  const frame = modelStage(K, renderer, scene, model.scene);
  const grid = new T.GridHelper(frame.radius * 4, 20, 0x888888, 0x888888);
  grid.material.transparent = true; grid.material.opacity = 0.25; grid.position.set(frame.center.x, frame.box.min.y, frame.center.z); scene.add(grid);
  aimCamera(K, camera, frame);
  const controls = new K.OrbitControls(camera, renderer.domElement);
  controls.target.copy(frame.center); controls.enableDamping = true; controls.autoRotateSpeed = 1.5; controls.update();
  stage.replaceChildren(renderer.domElement);
  const mixer = model.animations.length ? new T.AnimationMixer(model.scene) : null;
  let action = null, dirty = true, drawn = 0, last = performance.now();
  const st = modelStats(model.scene);
  info.textContent = [`${st.meshes} mesh${st.meshes === 1 ? '' : 'es'}`, `${modelNum(st.tris)} triangles`,
    `${fmtLen(frame.size.x)} × ${fmtLen(frame.size.y)} × ${fmtLen(frame.size.z)}`,
    model.animations.length ? `${model.animations.length} animation${model.animations.length === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ');
  const toggle = (label, title, on, icon) => { const b = el('button', { class: 'model-btn', title, 'aria-pressed': 'false', html: ico(icon, 15) }, el('span', { text: label }));
    b.onclick = () => { const v = on(b.getAttribute('aria-pressed') !== 'true'); b.setAttribute('aria-pressed', v ? 'true' : 'false'); b.classList.toggle('on', v); dirty = true; }; return b; };
  tools.replaceChildren(
    el('button', { class: 'model-btn', title: 'Show the whole model again', html: ico('zoom-out', 15), onclick: () => { moved = false; aimCamera(K, camera, frame); controls.target.copy(frame.center); controls.update(); dirty = true; } }, el('span', { text: 'Reset view' })),
    toggle('Turn', 'Turn the model slowly', v => (controls.autoRotate = v), 'refresh'),
    toggle('Wireframe', 'Show the triangles', v => { model.scene.traverse(o => { for (const m of [].concat(o.material || [])) if ('wireframe' in m) m.wireframe = v; }); return v; }, 'box'),
    toggle('Grid', 'Show the floor grid', v => (grid.visible = v), 'sheet'));
  tools.querySelector('[title="Show the floor grid"]').setAttribute('aria-pressed', 'true');
  tools.querySelector('[title="Show the floor grid"]').classList.add('on');
  if (mixer) {
    const pick = el('select', { class: 'model-anim', 'aria-label': 'Animation' }, el('option', { value: '', text: 'No animation' }),
      model.animations.map((a, i) => el('option', { value: String(i), text: a.name || `Animation ${i + 1}` })));
    pick.onchange = () => { mixer.stopAllAction(); action = pick.value === '' ? null : mixer.clipAction(model.animations[+pick.value]).play(); dirty = true; };
    pick.value = '0'; pick.onchange(); tools.append(pick);
  }
  controls.addEventListener('change', () => { dirty = true; });
  let moved = false;                      // once the person turns or zooms, a resize keeps their view
  controls.addEventListener('start', () => { moved = true; });
  const fit = () => { const w = stage.clientWidth, h = stage.clientHeight; if (!w || !h) return; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    if (!moved) { aimCamera(K, camera, frame); controls.target.copy(frame.center); controls.update(); } dirty = true; };
  const ro = new ResizeObserver(fit); ro.observe(stage); fit();
  const end = () => { ro.disconnect(); controls.dispose(); mixer?.stopAllAction(); disposeModel(model.scene); scene.environment?.dispose(); renderer.dispose(); renderer.forceContextLoss?.(); };
  const tick = now => {
    if (!renderer.domElement.isConnected) return end();
    requestAnimationFrame(tick);
    const dt = Math.min((now - last) / 1000, 0.1); last = now;
    if (stage.offsetParent === null) return;   // the editor shows something else for now
    if (controls.update()) dirty = true;
    if (action) { mixer.update(dt); dirty = true; }
    if (controls.autoRotate) dirty = true;
    if (dirty) { renderer.render(scene, camera); dirty = false; stage.dataset.drawn = ++drawn; }
  };
  requestAnimationFrame(tick);
}
// Thumbnails for the Media view, drawn one at a time by one offscreen renderer and kept for the page's life.
const MODEL_THUMBS = new Map();
let thumbQueue = Promise.resolve(), thumbRenderer = null;
function modelThumb(m, img, holder) {
  const key = m.path + '|' + m.mtime;
  if (MODEL_THUMBS.has(key)) { const u = MODEL_THUMBS.get(key); if (u) img.src = u; else holder.classList.add('no-thumb'); return; }
  if (m.size > 60 * 1048576 || (/\.blend$/i.test(m.path) && !isAdmin())) { holder.classList.add('no-thumb'); return; }
  thumbQueue = thumbQueue.then(async () => {
    if (!img.isConnected) return;
    let url = null;
    try {
      const K = await loadThree(), T = K.THREE, model = await loadModel(K, m.path, await previewUrl(m.path));
      if (!thumbRenderer) { thumbRenderer = new T.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true }); thumbRenderer.setSize(256, 256, false); thumbRenderer.toneMapping = T.ACESFilmicToneMapping; thumbRenderer.setClearColor(0, 0); }
      const scene = new T.Scene(), camera = new T.PerspectiveCamera(40, 1, 0.01, 1000);
      aimCamera(K, camera, modelStage(K, thumbRenderer, scene, model.scene));
      thumbRenderer.render(scene, camera);
      url = thumbRenderer.domElement.toDataURL('image/png');
      disposeModel(model.scene); scene.environment?.dispose();
    } catch { url = null; }
    MODEL_THUMBS.set(key, url);
    if (url) img.src = url; else holder.classList.add('no-thumb');
  });
}
/* ---- CSV and TSV files as a table: search, sort by a column, and the code view is one click away */
function parseDelimited(text, sep) {
  const rows = []; let row = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"' && cell === '') quoted = true;
    else if (ch === sep) { row.push(cell); cell = ''; }
    else if (ch === '\n' || ch === '\r') { if (ch === '\r' && text[i + 1] === '\n') i++; row.push(cell); rows.push(row); row = []; cell = ''; }
    else cell += ch;
  }
  if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
  return rows.filter(r => r.length > 1 || r[0] !== '');
}
const TABLE_SHOWN = 2000;
function showTable(t, pane) {
  const text = t.text ?? t.saved ?? '';
  const sep = /\.tsv$/i.test(t.path) ? '\t' : (text.split('\n', 1)[0].split(';').length > text.split('\n', 1)[0].split(',').length ? ';' : ',');
  const rows = parseDelimited(text, sep), head = rows[0] || [], body = rows.slice(1);
  const numeric = head.map((_, k) => body.length > 0 && body.every(r => r[k] === undefined || r[k] === '' || !isNaN(Number(r[k].replace(',', '.')))));
  const st = t.table || (t.table = { q: '', col: -1, dir: 1 });
  const search = el('input', { class: 'dlg-input table-search', type: 'search', placeholder: 'Search the rows', value: st.q, 'aria-label': 'Search the rows' });
  const count = el('span', { class: 'table-count' }), wrap = el('div', { class: 'md-table csv-table' });
  const draw = () => {
    const q = st.q.toLowerCase();
    let shown = q ? body.filter(r => r.some(c => c.toLowerCase().includes(q))) : body.slice();
    if (st.col >= 0) { const k = st.col; shown.sort((a, b) => (numeric[k] ? (Number((a[k] || '0').replace(',', '.')) - Number((b[k] || '0').replace(',', '.'))) : (a[k] || '').localeCompare(b[k] || '', undefined, { numeric: true })) * st.dir); }
    count.textContent = `${shown.length} of ${body.length} row${body.length === 1 ? '' : 's'} · ${head.length} column${head.length === 1 ? '' : 's'}` + (shown.length > TABLE_SHOWN ? ` · first ${TABLE_SHOWN} shown` : '');
    const ths = head.map((h, k) => el('th', { class: numeric[k] ? 'num' : '', 'aria-sort': st.col === k ? (st.dir > 0 ? 'ascending' : 'descending') : 'none' },
      el('button', { class: 'th-sort', title: 'Sort by ' + h, onclick: () => { st.dir = st.col === k ? -st.dir : 1; st.col = k; draw(); } }, h || `Column ${k + 1}`, st.col === k ? (st.dir > 0 ? ' ▲' : ' ▼') : '')));
    wrap.replaceChildren(el('table', {}, el('thead', {}, el('tr', {}, ths)),
      el('tbody', {}, shown.slice(0, TABLE_SHOWN).map(r => el('tr', {}, head.map((_, k) => el('td', { class: numeric[k] ? 'num' : '', text: r[k] ?? '' })))))));
  };
  search.addEventListener('input', () => { st.q = search.value; draw(); });
  draw();
  pane.replaceChildren(el('div', { class: 'preview-bar' }, el('span', { class: 'preview-path', text: t.path }), count, search, downloadBtn(t.path)), el('div', { class: 'table-view' }, wrap));
}
/* ---- video timelines (<name>.timeline.json, shared with the agent's video tool): edit, render, and point the agent at a part.
   Laid out like the wave editor (2026-11-14): header, one toolbar row, the player with an inspector beside it (below on
   phones), the transport, zoomable tracks with a playhead that scrubs, and the media strip. */
// The video tool's effects (tools/video_mcp.py EFFECTS), by group: [type, label, [[setting, label, default, min, max, step]]].
const TL_FX = [
  ['Colour', [['grayscale', 'Black & white'], ['sepia', 'Sepia'], ['vintage', 'Vintage'], ['warm', 'Warm', [['amount', 'Amount', 0.5, 0, 1, 0.05]]],
    ['cool', 'Cool', [['amount', 'Amount', 0.5, 0, 1, 0.05]]], ['hue', 'Hue shift', [['degrees', 'Degrees', 30, -180, 180, 5]]], ['invert', 'Invert'],
    ['brightness', 'Brightness', [['value', 'Value', 0.1, -1, 1, 0.05]]], ['contrast', 'Contrast', [['value', 'Value', 1.2, 0, 3, 0.05]]],
    ['saturation', 'Saturation', [['value', 'Value', 1.3, 0, 3, 0.05]]]]],
  ['Look', [['blur', 'Blur', [['amount', 'Amount', 5, 1, 20, 1]]], ['sharpen', 'Sharpen', [['amount', 'Amount', 1, 0, 2, 0.1]]],
    ['vignette', 'Vignette', [['amount', 'Amount', 0.5, 0, 1, 0.05]]], ['grain', 'Film grain', [['amount', 'Amount', 15, 0, 50, 1]]],
    ['pixelate', 'Pixelate', [['size', 'Block size', 12, 2, 64, 1]]], ['sketch', 'Sketch'], ['glitch', 'Glitch', [['amount', 'Amount', 8, 1, 30, 1]]],
    ['trails', 'Motion trails', [['frames', 'Frames', 4, 2, 10, 1]]], ['letterbox', 'Cinema bars', [['ratio', 'Ratio', 2.39, 1, 4, 0.01]]]]],
  ['Motion', [['speed', 'Speed', [['factor', 'Factor', 1, 0.25, 4, 0.05]]], ['zoom', 'Slow zoom', [['from', 'From', 1, 1, 3, 0.05], ['to', 'To', 1.2, 1, 3, 0.05]]],
    ['mirror', 'Mirror'], ['flip', 'Upside down'], ['rotate', 'Rotate', [['angle', 'Angle', 90, 90, 270, 90]]]]],
  ['Fades', [['fade_in', 'Fade in', [['duration', 'Seconds', 1, 0.1, 10, 0.1]]], ['fade_out', 'Fade out', [['duration', 'Seconds', 1, 0.1, 10, 0.1]]]]],
];
const TL_EFFECTS = Object.fromEntries(TL_FX.flatMap(([, list]) => list.map(([type, label, params = []]) => [type, { label, params }])));
// The transitions (tools/video_mcp.py TRANSITIONS), by group; `★` marks the common ones shown first in the toolbar menu.
const TL_TR = [
  ['Fades', ['dissolve★', 'fade_fast', 'fade_slow', 'fade_black★', 'fade_white', 'fade_gray', 'grain', 'blur★', 'distance']],
  ['Wipes', ['wipe_left★', 'wipe_right', 'wipe_up', 'wipe_down', 'wipe_tl', 'wipe_tr', 'wipe_bl', 'wipe_br', 'diagonal_tl', 'diagonal_tr', 'diagonal_bl', 'diagonal_br']],
  ['Slides', ['slide_left★', 'slide_right', 'slide_up', 'slide_down', 'smooth_left★', 'smooth_right', 'smooth_up', 'smooth_down',
    'cover_left', 'cover_right', 'cover_up', 'cover_down', 'reveal_left', 'reveal_right', 'reveal_up', 'reveal_down']],
  ['Shapes', ['circle★', 'circle_close', 'circle_crop', 'rect_crop', 'open_horizontal', 'close_horizontal', 'open_vertical', 'close_vertical', 'radial', 'zoom★', 'pixelize★', 'squeeze_h', 'squeeze_v']],
  ['Slices and wind', ['slices_left', 'slices_right', 'slices_up', 'slices_down', 'wind_left', 'wind_right', 'wind_up', 'wind_down']],
];
const TL_TRANSITIONS = TL_TR.flatMap(([, list]) => list.map(x => x.replace('★', '')));
const TL_TR_COMMON = TL_TR.flatMap(([, list]) => list.filter(x => x.endsWith('★')).map(x => x.slice(0, -1)));
function tlTrName(type) {
  const words = { tl: '↖', tr: '↗', bl: '↙', br: '↘', h: 'sideways', v: 'up and down', gray: 'through grey', black: 'through black', white: 'through white', grain: 'grainy' };
  const special = { dissolve: 'Dissolve', grain: 'Grainy dissolve', zoom: 'Zoom in', circle: 'Circle open', blur: 'Blur', radial: 'Clock', distance: 'Distance' };
  if (special[type]) return special[type];
  const parts = type.split('_').map(w => words[w] || w);
  return (parts[0][0].toUpperCase() + parts[0].slice(1) + ' ' + parts.slice(1).join(' ')).trim();
}
// Adds or removes an effect on a clip; with `on` true it only adds (the toolbar's Effects menu).
function tlToggleFx(t, clip, type, on) {
  const effects = clip.effects || (clip.effects = []);
  const k = effects.findIndex(x => x.type === type);
  if (k >= 0 && on !== true) effects.splice(k, 1);
  else if (k < 0) effects.push(Object.fromEntries([['type', type], ...TL_EFFECTS[type].params.map(p => [p[0], p[2]])]));
  tlSave(t);
}
function tlSelectedClip(t) {
  const tl = t.tl;
  if (tl.sel?.type === 'clip') return tl.data.clips[tl.sel.i];
  const at = tl.ui?.video?.currentTime || 0, i = (tl.layout?.clips || []).findIndex(c => at >= c.start && at < c.start + c.duration);
  if (i >= 0) { tl.sel = { type: 'clip', i }; return tl.data.clips[i]; }
  return null;
}
function tlFxItems(t) {
  const clip = tlSelectedClip(t);
  if (!clip) return [{ label: 'Select a clip first', run: () => {} }];
  const has = type => (clip.effects || []).some(e => e.type === type);
  return TL_FX.flatMap(([group, list], gi) => [...(gi ? ['-'] : []), ...list.map(([type, label]) => ({ label: (has(type) ? '✓ ' : '') + label, run: () => tlToggleFx(t, clip, type) }))]);
}
function tlTrItems(t) {
  const clip = tlSelectedClip(t), i = t.tl.sel?.i ?? -1;
  if (!clip) return [{ label: 'Select a clip first', run: () => {} }];
  if (i === 0) return [{ label: 'The first clip has no clip before it', run: () => {} }];
  const set = type => { if (type) clip.transition = { ...(clip.transition || {}), type, duration: clip.transition?.duration || 1 }; else delete clip.transition; tlSave(t); };
  const cur = clip.transition?.type || '';
  return [{ label: (cur ? '' : '✓ ') + 'Cut (no transition)', run: () => set('') }, '-',
    ...TL_TR_COMMON.map(type => ({ label: (cur === type ? '✓ ' : '') + tlTrName(type), run: () => set(type) })), '-',
    { label: `All ${TL_TRANSITIONS.length} transitions…`, run: () => { tlSelect(t, t.tl.sel); setTimeout(() => t.tl.ui?.side?.querySelector('.tl-tr-pick')?.focus(), 50); } }];
}
const TL_SIZES = [['1920x1080', '1920×1080 landscape'], ['1280x720', '1280×720 landscape'], ['1080x1920', '1080×1920 portrait'], ['1080x1080', '1080×1080 square']];
const TL_LISTS = { clip: 'clips', title: 'titles', audio: 'audio' };
const tlHead = () => document.documentElement.classList.contains('phone') ? 44 : 76;   // the track names' column, in pixels (as in the CSS)
const V = { agent: '' };            // the agent's video tool call while it runs
const tlSecs = n => (Math.round(n * 10) / 10).toFixed(1);
const tlClock = s => { s = Math.max(0, s || 0); const m = Math.floor(s / 60); return `${m}:${(s - m * 60).toFixed(1).padStart(4, '0')}`; };
const tlCopy = o => JSON.parse(JSON.stringify(o));

async function showTimeline(t, pane) {
  const tl = t.tl || (t.tl = { sel: null, data: null, layout: null, mtime: 0, video: null, vmtime: 0, job: null, media: null, undo: [], redo: [], last: null, pps: 0 });
  clearTimeout(tl.timer);
  const status = el('span', { class: 'tl-status', role: 'status' });
  const info = el('span', { class: 'tl-info' });
  const stop = aeBtn('Stop', 'Stop the render', () => tl.job && POST('/api/video/cancel', { id: tl.job.id }).catch(e => errToast(e, 'Could not stop')), 'danger hidden', 'stop');
  const moreB = aeBtn('', 'More', () => aeMenuAt(moreB, [{ label: 'Quick preview render', run: () => tlRender(t, true) }, { label: 'Ask the agent about this part', run: () => tlAsk(t) },
    '-', { label: 'Show the whole video', run: () => tlZoom(t, 0) }]), 'ic ae-narrow', 'more');
  const head = el('div', { class: 'tl-head' }, el('span', { class: 'tl-ico', html: ico('film', 18) }),
    el('div', { class: 'ae-titles' }, el('b', { class: 'ae-name', text: basename(t.path).replace(TIMELINE, '') }), info), status,
    el('span', { class: 'tl-acts' },
      aeBtn('Ask the agent', 'Put a note about the selected part in the chat box', () => tlAsk(t), 'ae-wide'),
      aeBtn('Preview', 'Quick small render to check the edit', () => tlRender(t, true), 'ae-wide', 'eye'),
      aeBtn('Render', 'Render the video at full size', () => tlRender(t, false), 'primary', 'film'), stop, moreB));
  const undoB = aeBtn('', 'Undo (Ctrl+Z)', () => tlUndo(t, -1), 'ic', 'undo'), redoB = aeBtn('', 'Redo (Ctrl+Shift+Z)', () => tlUndo(t, 1), 'ic', 'redo');
  const sep = () => el('span', { class: 'ae-sep' });
  const menu = (label, icon, items) => { const b = aeBtn(label, label, () => aeMenuAt(b, items()), 'ae-menu', icon); b.append(aeIco('chevron-down', 12)); return b; };
  const bar = el('div', { class: 'ae-bar tl-tools', role: 'toolbar', 'aria-label': 'Edit the video' },
    undoB, redoB, sep(),
    aeBtn('', 'Split at the playhead (S)', () => tlSplit(t), 'ic', 'scissors'), aeBtn('', 'Duplicate (Ctrl+D)', () => tlDuplicate(t), 'ic', 'copy'),
    aeBtn('', 'Delete the selected part (Delete)', () => tl.sel && tlDelete(t, tl.sel), 'ic', 'trash'), sep(),
    menu('Add', 'plus', () => [{ label: 'Media from this folder…', run: () => { tl.ui.bin.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); tl.ui.bin.classList.add('flash'); setTimeout(() => tl.ui.bin.classList.remove('flash'), 900); } },
      { label: 'Title', run: () => tlAddTitle(t) }, { label: 'Colour card', run: () => tlAddColour(t) }]),
    menu('Effects', 'wand', () => tlFxItems(t)), menu('Transition', 'layers', () => tlTrItems(t)),
    menu('Arrange', 'layers', () => tlArrangeItems(t)),
    menu('Video', 'gear', () => [...TL_SIZES.map(([v, l]) => ({ label: (`${tl.data?.width || 1920}x${tl.data?.height || 1080}` === v ? '✓ ' : '') + l, run: () => { const [w, h] = v.split('x').map(Number); tl.data.width = w; tl.data.height = h; tlSave(t); } })), '-',
      ...[24, 25, 30, 60].map(f => ({ label: ((tl.data?.fps || 30) === f ? '✓ ' : '') + f + ' frames a second', run: () => { tl.data.fps = f; tlSave(t); } })), '-',
      { label: (tl.data?.clip_audio !== false ? '✓ ' : '') + "Keep the clips' own sound", run: () => { tl.data.clip_audio = tl.data.clip_audio === false; tlSave(t); } }]),
    el('span', { class: 'ae-grow' }),
    aeBtn('', 'Zoom out (-)', () => tlZoom(t, 0.5), 'ic', 'zoom-out'), aeBtn('', 'Zoom in (+)', () => tlZoom(t, 2), 'ic', 'zoom-in'),
    aeBtn('Fit', 'Show the whole video', () => tlZoom(t, 0), 'ae-wide'));
  const fill = el('div', { class: 'cd-fill' }), rbar = el('div', { class: 'cd-bar tl-bar hidden' }, fill);
  const agent = el('div', { class: 'tl-agent hidden', role: 'status' });
  const video = el('video', { class: 'tl-video', preload: 'metadata', playsinline: '' });
  const note = el('div', { class: 'tl-note' });
  const player = el('div', { class: 'tl-player' }, video, note);
  const side = el('aside', { class: 'tl-side' });
  const playB = el('button', { class: 'ae-play', title: 'Play (Space)', 'aria-label': 'Play', onclick: () => tlToggle(t) }, aeIco('play', 20));
  const clock = el('span', { class: 'ae-clock' });
  const stale = el('span', { class: 'tl-stale hidden', title: 'The player shows the last render. Preview renders your changes.' }, 'Changed since the last render');
  const muteB = aeBtn('', 'Sound on or off', () => { video.muted = !video.muted; muteB.classList.toggle('on', video.muted); }, 'ic', 'volume');
  const transport = el('div', { class: 'ae-pbar tl-transport' }, playB, aeBtn('', 'Back to the start (Home)', () => tlSeek(t, 0), 'ic', 'skip-back'), clock, stale, el('span', { class: 'ae-grow' }), muteB);
  const lanes = el('div', { class: 'tl-lanes' });
  const scroller = el('div', { class: 'tl-scroll' }, lanes);
  const bin = el('div', { class: 'tl-bin' });
  const root = el('div', { class: 'tl', tabindex: '-1' }, head, bar, rbar, agent, el('div', { class: 'tl-main' }, player, side), transport, scroller, bin);
  pane.replaceChildren(root);
  tl.ui = { pane, root, head, info, status, stop, bar: rbar, fill, agent, video, player, note, side, lanes, scroller, bin, undoB, redoB, playB, clock, stale };
  video.addEventListener('timeupdate', () => tlPlayhead(t));
  video.addEventListener('play', () => { tlButtons(t); const tick = () => { if (video.paused) return; tlPlayhead(t, true); requestAnimationFrame(tick); }; requestAnimationFrame(tick); });
  video.addEventListener('pause', () => tlButtons(t));
  video.addEventListener('ended', () => tlButtons(t));
  video.addEventListener('click', () => tlToggle(t));
  root.addEventListener('keydown', e => tlKeys(t, e));
  // Redraw when the width changes, not when a scrollbar comes or goes (that would drop a block being dragged).
  let width = 0;
  new ResizeObserver(() => { const w = scroller.clientWidth; if (w === width || tl.dragging) return; width = w; if (!tl.zoomed) tl.pps = 0; tlDraw(t); }).observe(scroller);
  tl.mtime = 0; tl.vmtime = -1;
  await tlLoad(t);
  tlBin(t);
  tlAgentBanner(t);
  const poll = async () => { if (!S.tabs.includes(t)) return; if (S.active === t && !document.hidden) await tlLoad(t, true); tl.timer = setTimeout(poll, 2000); };
  tl.timer = setTimeout(poll, 2000);
}
function tlStatus(t, text, kind = '') { const u = t.tl.ui; if (!u) return; u.status.textContent = text; u.status.className = 'tl-status ' + kind; }
async function tlLoad(t, quiet = false) {
  const tl = t.tl;
  let d;
  try { d = await GET('/api/video/timeline', { ws: S.ws.id, path: t.path }); }
  catch (e) { if (!quiet) tlStatus(t, e.message || String(e), 'err'); return; }
  if (S.active !== t || !tl.ui) return;
  if (d.mtime !== tl.mtime && !tl.saving) {
    const { _layout, ...rest } = d.timeline;
    tl.data = rest; tl.layout = _layout || null; tl.mtime = d.mtime; tl.last = JSON.stringify(rest);
    if (tl.sel && !tlItem(t, tl.sel)) tl.sel = null;
    tlDraw(t);
  }
  if (d.video !== tl.video || d.video_mtime !== tl.vmtime) {
    tl.video = d.video; tl.vmtime = d.video_mtime;
    const u = tl.ui, at = u.video.currentTime || 0;
    if (d.video) { u.video.src = (await previewUrl(d.video)) + '?v=' + d.video_mtime; u.video.addEventListener('loadedmetadata', () => { const to = tl.wantAt ?? at; tl.wantAt = null; u.video.currentTime = Math.min(to, u.video.duration || to); tlPlayhead(t); }, { once: true }); }
    u.player.classList.toggle('empty', !d.video);
  }
  tlNote(t);
  if (d.job && !tl.job) tlFollow(t, d.job.id);
}
// What the player shows: nothing rendered yet, or a render older than the timeline.
function tlNote(t) {
  const tl = t.tl, u = tl.ui; if (!u) return;
  const empty = !tl.layout || !tl.layout.length, staleNow = !!tl.video && tl.vmtime != null && tl.mtime > tl.vmtime + 0.5;
  u.note.textContent = !tl.video ? (empty ? 'Add a clip from the media below to start.' : 'Not rendered yet: press Preview to see it.') : '';
  u.note.classList.toggle('hidden', !!tl.video);
  u.stale.classList.toggle('hidden', !staleNow);
}
function tlItem(t, sel) { const d = t.tl.data; return sel && d && (d[TL_LISTS[sel.type]] || [])[sel.i]; }
function tlLength(t) { return Math.max(t.tl.layout?.length || 0, 0.1); }
function tlDraw(t) {
  const tl = t.tl, u = tl.ui; if (!u) return;
  const lay = tl.layout || { length: 0, clips: [], titles: [], audio: [] }, d = tl.data || {};
  u.info.textContent = lay.length ? `${tlSecs(lay.length)} s · ${lay.clips.length} clip${lay.clips.length === 1 ? '' : 's'} · ${d.width || 1920}×${d.height || 1080}` : 'empty: add a clip from the media below';
  const length = tlLength(t), view = Math.max(200, u.scroller.clientWidth - tlHead() - 16);
  if (!tl.pps || !tl.zoomed) { tl.pps = view / length; tl.zoomed = false; }      // fitted unless zoomed by hand, so a new length fits too
  const pps = tl.pps, W = Math.max(view, length * pps), px = s => (s * pps).toFixed(1) + 'px';
  const sel = tl.sel;
  const block = (type, i, item, cls, sub, icon) => {
    const b = el('div', { class: `tl-block ${cls}` + (sel && sel.type === type && sel.i === i ? ' picked' : ''), style: `left:${px(item.start)};width:${px(item.duration)}`,
      role: 'button', tabindex: '0', title: `${item.label}\n${tlSecs(item.start)}–${tlSecs(item.start + item.duration)} s${sub ? '\n' + sub : ''}\nDrag to move, drag the right edge to change the length` },
      el('span', { class: 'tl-bico', html: ico(icon, 12) }), el('span', { class: 'tl-label', text: item.label }), sub ? el('small', { text: sub }) : null,
      el('span', { class: 'tl-grip', title: 'Drag to change the length' }));
    if (type === 'clip' && i > 0 && d.clips?.[i]?.transition) b.append(el('span', { class: 'tl-trans', title: 'Change from the previous clip: ' + d.clips[i].transition.type.replace('_', ' ') }));
    b.addEventListener('pointerdown', e => tlDrag(t, e, type, i, b, e.target.classList.contains('tl-grip')));
    b.addEventListener('keydown', e => { if (e.key === 'Enter') tlSelect(t, { type, i }, item.start); });
    return b;
  };
  const track = (name, icon, items, type, cls, sub, bico) => el('div', { class: 'tl-track' }, el('span', { class: 'tl-name' }, el('span', { html: ico(icon, 13) }), name),
    el('div', { class: 'tl-lane', 'data-type': type, style: `width:${W}px` }, items.map((item, i) => block(type, i, item, cls, sub(item, i), bico(item)))));
  const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300], step = steps.find(s => s * pps >= 64) || 600;
  const ticks = [];
  for (let s = 0; s <= W / pps + 0.001; s += step) ticks.push(el('span', { class: 'tl-tick', style: `left:${px(s)}`, text: s >= 60 ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}` : (step < 1 ? s.toFixed(1) : s) + 's' }));
  const ruler = el('div', { class: 'tl-lane tl-ruler', style: `width:${W}px`, title: 'Click or drag to move the playhead' }, ticks, el('span', { class: 'tl-end', style: `left:${px(lay.length || 0)}` }));
  u.lanes.style.width = (W + tlHead()) + 'px';
  u.lanes.dataset.pps = String(pps);
  u.lanes.replaceChildren(
    el('div', { class: 'tl-track tl-ruler-row' }, el('span', { class: 'tl-name' }), ruler),
    track('Video', 'film', lay.clips, 'clip', 'clip', c => [c.transition && `↘ ${tlTrName(c.transition)}`, ...(c.effects || []).map(x => TL_EFFECTS[x]?.label || x)].filter(Boolean).join(' · '), c => c.kind === 'video' ? 'film' : c.kind === 'picture' ? 'image' : 'square'),
    track('Titles', 'edit', lay.titles, 'title', 'title', () => '', () => 'edit'),
    track('Sound', 'volume', lay.audio, 'audio', 'audio', a => `volume ${(d.audio?.[lay.audio.indexOf(a)]?.volume ?? 1)}`, () => 'media'),
    el('div', { class: 'tl-playhead' }, el('span', { class: 'tl-ph-knob' })));
  for (const lane of u.lanes.querySelectorAll('.tl-lane')) lane.addEventListener('pointerdown', e => { if (e.target === lane || e.target.closest('.tl-ruler')) tlScrub(t, e); });
  tlPlayhead(t); tlInspector(t); tlButtons(t); tlNote(t);
}
function tlButtons(t) {
  const tl = t.tl, u = tl.ui; if (!u) return;
  u.undoB.disabled = !tl.undo.length; u.redoB.disabled = !tl.redo.length;
  const playing = !u.video.paused && !u.video.ended;
  u.playB.replaceChildren(aeIco(playing ? 'pause' : 'play', 20)); u.playB.setAttribute('aria-label', playing ? 'Pause' : 'Play'); u.playB.classList.toggle('on', playing);
}
function tlPlayhead(t, follow = false) {
  const tl = t.tl, u = tl.ui, ph = u && u.lanes.querySelector('.tl-playhead'); if (!ph) return;
  const now = u.video.currentTime || 0;
  ph.style.left = (tlHead() + now * tl.pps) + 'px';
  u.clock.textContent = `${tlClock(now)} / ${tlClock(tl.layout?.length || 0)}`;
  if (follow) { const x = now * tl.pps, s = u.scroller; if (x < s.scrollLeft || x > s.scrollLeft + s.clientWidth - tlHead() - 30) s.scrollLeft = Math.max(0, x - 40); }
}
function tlSeek(t, s) {
  const tl = t.tl, v = tl.ui.video;
  s = clamp(s, 0, tlLength(t));
  tl.wantAt = s;
  if (v.src) v.currentTime = Math.min(s, v.duration || s);
  tlPlayhead(t);
}
function tlToggle(t) { const v = t.tl.ui.video; if (!v.src) return tlStatus(t, 'Nothing rendered yet: press Preview first.'); if (v.paused || v.ended) v.play().catch(() => {}); else v.pause(); }
// Pressing on the ruler or an empty part of a lane moves the playhead there, and dragging scrubs.
function tlScrub(t, e) {
  if (e.button > 0) return;
  const tl = t.tl, lane = e.currentTarget, at = m => (m.clientX - lane.getBoundingClientRect().left) / tl.pps;
  e.preventDefault();
  lane.setPointerCapture(e.pointerId);
  tlSeek(t, at(e));
  if (!e.target.closest('.tl-ruler') && tl.sel) { tl.sel = null; tlDraw(t); }
  const move = m => tlSeek(t, at(m));
  const up = () => { lane.removeEventListener('pointermove', move); lane.removeEventListener('pointerup', up); lane.removeEventListener('pointercancel', up); };
  lane.addEventListener('pointermove', move); lane.addEventListener('pointerup', up); lane.addEventListener('pointercancel', up);
}
function tlZoom(t, f) {
  const tl = t.tl, u = tl.ui; if (!u) return;
  const centre = (u.scroller.scrollLeft + (u.scroller.clientWidth - tlHead()) / 2) / (tl.pps || 1);
  if (!f) { tl.pps = 0; tl.zoomed = false; tlDraw(t); return; }
  const view = Math.max(200, u.scroller.clientWidth - tlHead() - 16);
  tl.pps = clamp((tl.pps || view / tlLength(t)) * f, view / tlLength(t), 400); tl.zoomed = true;
  tlDraw(t);
  u.scroller.scrollLeft = Math.max(0, centre * tl.pps - (u.scroller.clientWidth - tlHead()) / 2);
}
function tlSelect(t, sel, at) {
  t.tl.sel = sel; tlDraw(t);
  // A click while the video reloads would be undone by the reload going back to the old time, so the reload uses this.
  if (sel && at != null) tlSeek(t, at + 0.05);
}
// Dragging a block: a click selects it, a drag moves it (clips change places, titles and sounds change time), the right edge trims.
function tlDrag(t, e, type, i, b, trim) {
  if (e.button > 0) return;
  e.preventDefault(); e.stopPropagation();
  const tl = t.tl, lay = tl.layout, item = lay[TL_LISTS[type]][i];
  const x0 = e.clientX, perSec = tl.pps;
  let dx = 0;
  b.setPointerCapture(e.pointerId);
  tl.dragging = true;
  const move = m => { dx = m.clientX - x0; if (Math.abs(dx) < 3) return; b.classList.add('dragging'); if (trim) b.style.width = `${Math.max(4, item.duration * perSec + dx)}px`; else b.style.transform = `translateX(${dx}px)`; };
  const up = () => {
    b.removeEventListener('pointermove', move); b.removeEventListener('pointerup', up); b.removeEventListener('pointercancel', up);
    tl.dragging = false;
    if (Math.abs(dx) < 3) { tlSelect(t, { type, i }, item.start); return; }
    const ds = dx / perSec, data = tl.data, obj = tlItem(t, { type, i });
    if (trim) {
      const len = Math.max(0.3, item.duration + ds);
      if (type === 'clip' && item.kind === 'video') {
        const speed = (obj.effects || []).find(x => x.type === 'speed')?.factor || 1, from = obj.in || 0;
        obj.out = Math.round(Math.min(item.source || Infinity, from + len * speed) * 100) / 100;
      } else obj.duration = Math.round(len * 100) / 100;
    } else if (type === 'clip') {
      const target = item.start + item.duration / 2 + ds, others = lay.clips.map((c, k) => ({ k, mid: c.start + c.duration / 2 })).filter(c => c.k !== i);
      const j = others.filter(c => c.mid < target).length;
      const [moved] = data.clips.splice(i, 1); data.clips.splice(j, 0, moved);
      if (data.clips[0]) delete data.clips[0].transition;
      tl.sel = { type, i: j };
    } else {
      obj.start = Math.round(Math.max(0, Math.min(lay.length - 0.2, item.start + ds)) * 100) / 100;
    }
    tlSave(t);
  };
  b.addEventListener('pointermove', move); b.addEventListener('pointerup', up); b.addEventListener('pointercancel', up);
}
// Saving checks the edit the way a render would and returns where everything now falls; a refused edit goes back to the saved one.
// Each save first keeps the timeline as it was, for undo.
function tlSave(t, { history = true } = {}) {
  const tl = t.tl;
  const now = JSON.stringify(tl.data);
  if (history && tl.last != null && tl.last !== now) { tl.undo.push(tl.last); if (tl.undo.length > 100) tl.undo.shift(); tl.redo = []; }
  tl.last = now;
  tl.saving = (tl.saving || 0) + 1;
  tlStatus(t, 'Saving…');
  tl.saved = (tl.saved || Promise.resolve()).then(async () => {
    try {
      const r = await POST('/api/video/save', { ws: S.ws.id, path: t.path, timeline: tl.data });
      tl.layout = r.layout; tl.mtime = r.mtime; tlStatus(t, 'Saved', 'ok');
    } catch (e) { tlStatus(t, e.message || String(e), 'err'); tl.mtime = 0; if (history && tl.undo.length) { tl.undo.pop(); } }
    finally { tl.saving--; if (!tl.saving) { if (!tl.mtime) await tlLoad(t); else tlDraw(t); } }
  });
  tlDraw(t);
  return tl.saved;
}
function tlUndo(t, dir) {
  const tl = t.tl, from = dir < 0 ? tl.undo : tl.redo, to = dir < 0 ? tl.redo : tl.undo;
  if (!from.length) return;
  to.push(JSON.stringify(tl.data));
  tl.data = JSON.parse(from.pop());
  if (tl.sel && !tlItem(t, tl.sel)) tl.sel = null;
  tlSave(t, { history: false });
  tlStatus(t, dir < 0 ? 'Undone' : 'Redone', 'ok');
}
function tlDelete(t, sel) {
  const list = t.tl.data[TL_LISTS[sel.type]];
  list.splice(sel.i, 1);
  if (sel.type === 'clip' && list[0]) delete list[0].transition;
  t.tl.sel = null; tlSave(t);
}
// Split the selected part (or the clip under the playhead) in two at the playhead.
function tlSplit(t) {
  const tl = t.tl, lay = tl.layout, at = tl.ui.video.currentTime || tl.wantAt || 0;
  if (!lay) return;
  let sel = tl.sel;
  const spans = (type, i) => { const L = lay[TL_LISTS[type]][i]; return L && at > L.start + 0.1 && at < L.start + L.duration - 0.1; };
  if (!sel || !spans(sel.type, sel.i)) { const i = lay.clips.findIndex((_, k) => spans('clip', k)); sel = i >= 0 ? { type: 'clip', i } : null; }
  if (!sel) return tlStatus(t, 'Put the playhead inside a clip, title or sound to split it.', 'err');
  const L = lay[TL_LISTS[sel.type]][sel.i], obj = tlItem(t, sel), off = at - L.start, list = tl.data[TL_LISTS[sel.type]];
  const a = tlCopy(obj), b = tlCopy(obj);
  delete b.transition;
  const drop = (o, kind) => { if (o.effects) o.effects = o.effects.filter(x => x.type !== kind); };
  if (sel.type === 'clip') {
    drop(a, 'fade_out'); drop(b, 'fade_in');
    if (L.kind === 'video') { const sp = (obj.effects || []).find(x => x.type === 'speed')?.factor || 1, cut = Math.round(((obj.in || 0) + off * sp) * 100) / 100; a.out = cut; b.in = cut; }
    else { a.duration = Math.round(off * 100) / 100; b.duration = Math.round((L.duration - off) * 100) / 100; }
    // Transitions next to the cut must stay shorter than the halves they touch.
    const next = list[sel.i + 1], bLen = L.kind === 'video' ? ((obj.out ?? L.source ?? 0) - b.in) / ((obj.effects || []).find(x => x.type === 'speed')?.factor || 1) : b.duration;
    if (next?.transition && bLen - 0.2 < (next.transition.duration || 1)) { if (bLen - 0.2 < 0.1) return tlStatus(t, 'Too close to the next transition to split here.', 'err'); next.transition.duration = Math.round((bLen - 0.2) * 10) / 10; }
    // A transition into the first half must stay shorter than it.
    if (a.transition) { const room = (L.kind === 'video' ? (a.out - (a.in || 0)) / ((obj.effects || []).find(x => x.type === 'speed')?.factor || 1) : a.duration) - 0.2; if (room < 0.1) return tlStatus(t, 'Too close to the start of the transition to split here.', 'err'); a.transition.duration = Math.min(a.transition.duration || 1, Math.round(room * 10) / 10); }
  } else if (sel.type === 'title') { a.duration = Math.round(off * 100) / 100; b.start = Math.round(at * 100) / 100; b.duration = Math.round((L.duration - off) * 100) / 100; }
  else {
    a.duration = Math.round(off * 100) / 100; b.start = Math.round(at * 100) / 100; b.in = Math.round(((obj.in || 0) + off) * 100) / 100;
    b.duration = Math.round((L.duration - off) * 100) / 100; delete a.fade_out; delete b.fade_in;
  }
  list.splice(sel.i, 1, a, b);
  tl.sel = { type: sel.type, i: sel.i + 1 };
  tlSave(t); tlStatus(t, `Split at ${tlClock(at)}`, 'ok');
}
function tlDuplicate(t) {
  const tl = t.tl, sel = tl.sel; if (!sel) return tlStatus(t, 'Select a clip, title or sound first.', 'err');
  const list = tl.data[TL_LISTS[sel.type]], copy = tlCopy(list[sel.i]), L = tl.layout[TL_LISTS[sel.type]][sel.i];
  if (sel.type !== 'clip') copy.start = Math.round((L.start + L.duration) * 100) / 100;
  list.splice(sel.i + 1, 0, copy);
  tl.sel = { type: sel.type, i: sel.i + 1 }; tlSave(t);
}
function tlMoveClip(t, by) {
  const tl = t.tl, sel = tl.sel, c = tl.data.clips, j = sel.i + by;
  if (!sel || sel.type !== 'clip' || j < 0 || j >= c.length) return;
  [c[j], c[sel.i]] = [c[sel.i], c[j]]; delete c[0].transition; tl.sel = { type: 'clip', i: j }; tlSave(t);
}
function tlArrangeItems(t) {
  const tl = t.tl, sel = tl.sel;
  if (!sel) return [{ label: 'Select a clip, title or sound first', run: () => {} }];
  const items = [];
  if (sel.type === 'clip') items.push({ label: 'Move earlier', run: () => tlMoveClip(t, -1) }, { label: 'Move later', run: () => tlMoveClip(t, 1) }, '-');
  items.push({ label: 'Move to the playhead', run: () => { if (sel.type === 'clip') { const at = tl.ui.video.currentTime || 0, target = tl.layout.clips.findIndex(c => at < c.start + c.duration / 2); const c = tl.data.clips, [m] = c.splice(sel.i, 1), j = target < 0 ? c.length : Math.max(0, target > sel.i ? target - 1 : target); c.splice(j, 0, m); delete c[0].transition; tl.sel = { type: 'clip', i: j }; } else tlItem(t, sel).start = Math.round((tl.ui.video.currentTime || 0) * 100) / 100; tlSave(t); } },
    { label: 'Split at the playhead (S)', run: () => tlSplit(t) }, { label: 'Duplicate (Ctrl+D)', run: () => tlDuplicate(t) }, '-', { label: 'Delete', danger: true, run: () => tlDelete(t, sel) });
  return items;
}
function tlAddTitle(t) {
  const tl = t.tl, data = tl.data, at = Math.round((tl.ui.video.currentTime || 0) * 10) / 10;
  (data.titles || (data.titles = [])).push({ text: 'Title', start: at, duration: 3, position: 'center' });
  tl.sel = { type: 'title', i: data.titles.length - 1 }; tlSave(t);
  setTimeout(() => { const ta = tl.ui.side.querySelector('.tl-text'); ta?.focus(); ta?.select(); }, 50);
}
function tlAddColour(t) { const tl = t.tl, data = tl.data; (data.clips || (data.clips = [])).push({ color: '#000000', duration: 2 }); tl.sel = { type: 'clip', i: data.clips.length - 1 }; tlSave(t); }
function tlKeys(t, e) {
  if (e.target.closest('input, select, textarea')) return;
  const tl = t.tl, mod = e.ctrlKey || e.metaKey, k = e.key;
  const take = fn => { e.preventDefault(); e.stopPropagation(); fn(); };
  if (k === ' ') return take(() => tlToggle(t));
  if (mod && (k === 'z' || k === 'Z')) return take(() => tlUndo(t, e.shiftKey ? 1 : -1));
  if (mod && (k === 'y' || k === 'Y')) return take(() => tlUndo(t, 1));
  if (mod && (k === 'd' || k === 'D')) return take(() => tlDuplicate(t));
  if (mod) return;
  if ((k === 'Delete' || k === 'Backspace') && tl.sel) return take(() => tlDelete(t, tl.sel));
  if (k === 's' || k === 'S') return take(() => tlSplit(t));
  if (k === 'Escape' && tl.sel) return take(() => tlSelect(t, null));
  if (k === 'Home') return take(() => tlSeek(t, 0));
  if (k === 'End') return take(() => tlSeek(t, tlLength(t)));
  if (k === '+' || k === '=') return take(() => tlZoom(t, 2));
  if (k === '-') return take(() => tlZoom(t, 0.5));
  if (k === 'ArrowLeft' || k === 'ArrowRight') return take(() => tlSeek(t, (tl.ui.video.currentTime || 0) + (k === 'ArrowLeft' ? -1 : 1) * (e.shiftKey ? 1 : 1 / (tl.data?.fps || 30))));
}
// The inspector: what is selected, in sections; the video's own settings when nothing is.
function tlInspector(t) {
  const tl = t.tl, side = tl.ui.side, d = tl.data || {}, sel = tl.sel, obj = tlItem(t, sel);
  const save = () => tlSave(t);
  const row = (label, input) => el('label', { class: 'tl-field' }, el('span', { text: label }), input);
  const num = (o, key, step, min, max, dflt, after) => el('input', { type: 'number', step, min, max, value: o[key] ?? dflt ?? '', placeholder: dflt == null ? 'to the end' : '', inputmode: 'decimal',
    onchange: e => { const v = e.target.value; if (v === '') delete o[key]; else o[key] = Math.min(max, Math.max(min, +v)); after?.(); save(); } });
  const select = (o, key, options, dflt, after) => { const s = el('select', { onchange: e => { if (e.target.value === '') delete o[key]; else o[key] = e.target.value; after?.(); save(); } },
    options.map(([v, l]) => el('option', { value: v, text: l }))); s.value = o[key] ?? dflt ?? ''; return s; };
  const section = (title, ...kids) => el('section', { class: 'tl-sec' }, el('h4', { text: title }), ...kids);
  const headRow = (icon, title, sub) => el('div', { class: 'tl-side-head' }, el('span', { class: 'tl-side-ico', html: ico(icon, 15) }), el('div', {}, el('b', { text: title }), sub ? el('div', { class: 'tl-side-sub', text: sub }) : null),
    sel ? el('button', { class: 'icon-btn sm tl-deselect', title: 'Back to the video settings (Esc)', 'aria-label': 'Close', html: ico('x', 14), onclick: () => tlSelect(t, null) }) : null);
  const acts = (...b) => el('div', { class: 'tl-side-acts' }, ...b);
  const actBtn = (label, title, onclick, cls = '', icon = null) => aeBtn(label, title, onclick, cls, icon);
  const parts = [];
  if (!obj) {
    const size = el('select', { onchange: e => { const [w, h] = e.target.value.split('x').map(Number); d.width = w; d.height = h; save(); } }, TL_SIZES.map(([v, l]) => el('option', { value: v, text: l })));
    size.value = `${d.width || 1920}x${d.height || 1080}`;
    parts.push(headRow('film', 'Video settings', tl.layout?.length ? `${tlSecs(tl.layout.length)} s` : ''),
      section('Frame', row('Size', size), row('Frames a second', select(d, 'fps', [['24', '24'], ['25', '25'], ['30', '30'], ['60', '60']], '30', () => { d.fps = +d.fps; }))),
      section('Sound', row("Keep the clips' own sound", el('input', { type: 'checkbox', checked: d.clip_audio !== false ? '' : null, onchange: e => { d.clip_audio = e.target.checked; save(); } }))),
      el('p', { class: 'tl-hint', text: 'Select a clip, title or sound on the tracks to change it. Drag blocks to move them and their right edge to change the length; S splits at the playhead.' }));
  } else if (sel.type === 'clip') {
    const L = tl.layout.clips[sel.i] || {};
    parts.push(headRow(L.kind === 'video' ? 'film' : L.kind === 'picture' ? 'image' : 'square', `Clip ${sel.i + 1}: ${L.label || ''}`, `${L.kind || ''} · ${tlSecs(L.start || 0)}–${tlSecs((L.start || 0) + (L.duration || 0))} s`));
    const timing = L.kind === 'video' ? [row('From (s)', num(obj, 'in', 0.1, 0, L.source || 600, 0)), row('To (s)', num(obj, 'out', 0.1, 0.1, L.source || 600, null))] : [row('Length (s)', num(obj, 'duration', 0.1, 0.1, 600, 4))];
    const look = 'color' in obj ? [row('Colour', el('input', { type: 'color', value: obj.color, onchange: e => { obj.color = e.target.value; save(); } }))]
      : [row('Fit', select(obj, 'fit', [['contain', 'Whole picture (bars)'], ['cover', 'Fill the frame (crop)']], 'contain'))];
    parts.push(section('Timing', ...timing), section('Look', ...look));
    if (sel.i > 0) {
      const tr = obj.transition || {};
      const pick = el('select', { class: 'tl-tr-pick', 'aria-label': 'Transition', onchange: e => { const v = e.target.value; if (v) obj.transition = { ...(obj.transition || {}), type: v, duration: obj.transition?.duration || 1 }; else delete obj.transition; save(); } },
        el('option', { value: '', text: 'Cut (no transition)' }),
        TL_TR.map(([group, list]) => el('optgroup', { label: group }, list.map(x => x.replace('★', '')).map(x => el('option', { value: x, text: tlTrName(x) })))));
      pick.value = obj.transition ? tr.type || 'dissolve' : '';
      parts.push(section('Change from the previous clip', row('Kind', pick), obj.transition ? row('Length (s)', num(obj.transition, 'duration', 0.1, 0.1, 5, 1)) : null));
    }
    const effects = obj.effects || (obj.effects = []);
    const chips = TL_FX.map(([group, list]) => el('div', { class: 'tl-fx-group' }, el('small', { class: 'tl-fx-gname', text: group }),
      el('div', { class: 'tl-chips' }, list.map(([kind, label]) => {
        const on = effects.some(e => e.type === kind);
        return el('button', { class: 'tl-chip' + (on ? ' on' : ''), 'aria-pressed': on ? 'true' : 'false', 'data-fx': kind, text: label, onclick: () => tlToggleFx(t, obj, kind) });
      }))));
    // the settings of the effects that are on, as sliders with their value
    const params = effects.filter(e => TL_EFFECTS[e.type]?.params.length).map(on => el('div', { class: 'tl-fx-row' }, el('span', { text: TL_EFFECTS[on.type].label }),
      TL_EFFECTS[on.type].params.map(([key, label, dflt, min, max, step]) => {
        const out = el('output', { text: String(on[key] ?? dflt) });
        const input = el('input', { type: 'range', min, max, step, value: on[key] ?? dflt, 'aria-label': `${TL_EFFECTS[on.type].label} ${label}`,
          oninput: e => { out.textContent = e.target.value; }, onchange: e => { on[key] = +e.target.value; save(); } });
        return el('label', { class: 'tl-fx-p' }, el('small', { text: label }), input, out);
      })));
    parts.push(section(`Effects${effects.length ? ` (${effects.length})` : ''}`, ...chips, ...params),
      acts(actBtn('Earlier', 'Move one place earlier', () => tlMoveClip(t, -1), '', 'arrow-up'), actBtn('Later', 'Move one place later', () => tlMoveClip(t, 1), '', 'arrow-down'),
        actBtn('Split', 'Split at the playhead (S)', () => tlSplit(t), '', 'scissors'), actBtn('Delete', 'Delete this clip', () => tlDelete(t, sel), 'danger', 'trash')));
    const [ea, la] = parts[parts.length - 1].querySelectorAll('button'); ea.disabled = sel.i === 0; la.disabled = sel.i >= d.clips.length - 1;
  } else if (sel.type === 'title') {
    parts.push(headRow('edit', `Title ${sel.i + 1}`, `${tlSecs(obj.start || 0)}–${tlSecs((obj.start || 0) + (obj.duration || 3))} s`),
      section('Text', el('textarea', { class: 'tl-text', rows: 2, onchange: e => { obj.text = e.target.value; save(); } })),
      section('Timing', row('Start (s)', num(obj, 'start', 0.1, 0, 600, 0)), row('Length (s)', num(obj, 'duration', 0.1, 0.2, 600, 3))),
      section('Look', row('Place', select(obj, 'position', [['top', 'Top'], ['center', 'Middle'], ['bottom', 'Bottom']], 'bottom')),
        row('Size (px)', num(obj, 'size', 1, 8, 400, Math.round((d.height || 1080) * 0.06))),
        row('Colour', el('input', { type: 'color', value: obj.color || '#ffffff', onchange: e => { obj.color = e.target.value; save(); } })),
        row('Dark band behind', el('input', { type: 'checkbox', checked: obj.box ? '' : null, onchange: e => { obj.box = e.target.checked; save(); } }))),
      acts(actBtn('Split', 'Split at the playhead (S)', () => tlSplit(t), '', 'scissors'), actBtn('Delete', 'Delete this title', () => tlDelete(t, sel), 'danger', 'trash')));
  } else {
    const L = tl.layout.audio[sel.i] || {};
    parts.push(headRow('volume', `Sound ${sel.i + 1}: ${L.label || ''}`, `from ${tlSecs(L.start || 0)} s`),
      section('Timing', row('Starts at (s)', num(obj, 'start', 0.1, 0, 600, 0)), row('From (s) in the file', num(obj, 'in', 0.1, 0, 3600, 0)), row('Length (s)', num(obj, 'duration', 0.1, 0.1, 600, null))),
      section('Volume', row('Volume', num(obj, 'volume', 0.05, 0, 2, 1)), row('Fade in (s)', num(obj, 'fade_in', 0.1, 0, 30, null)), row('Fade out (s)', num(obj, 'fade_out', 0.1, 0, 30, null))),
      acts(actBtn('Split', 'Split at the playhead (S)', () => tlSplit(t), '', 'scissors'), actBtn('Delete', 'Delete this sound', () => tlDelete(t, sel), 'danger', 'trash')));
  }
  side.replaceChildren(...parts);
  const ta = side.querySelector('.tl-text'); if (ta) ta.value = obj.text || '';
}
async function tlBin(t) {
  const tl = t.tl, u = tl.ui;
  let d;
  try { d = await GET('/api/video/list', { ws: S.ws.id }); } catch (e) { u.bin.replaceChildren(el('div', { class: 'tl-hint', text: 'Could not list the media: ' + e.message })); return; }
  tl.folder = d.folder;
  const add = m => {
    const data = tl.data || (tl.data = { clips: [], titles: [], audio: [] });
    if (m.kind === 'sound') { (data.audio || (data.audio = [])).push({ src: m.full, start: Math.round((u.video.currentTime || 0) * 10) / 10 }); tl.sel = { type: 'audio', i: data.audio.length - 1 }; }
    else { (data.clips || (data.clips = [])).push(m.kind === 'picture' ? { src: m.full, duration: 4, fit: 'cover' } : { src: m.full }); tl.sel = { type: 'clip', i: data.clips.length - 1 }; }
    tlSave(t);
  };
  const tools = el('div', { class: 'tl-bin-acts' }, el('b', { text: 'Media' }), el('small', { text: d.media.length ? 'tap to add at the end of its track' : '' }), el('span', { class: 'ae-grow' }),
    el('button', { class: 'ae-b', onclick: () => tlAddTitle(t) }, '+ Title'),
    el('button', { class: 'ae-b', onclick: () => tlAddColour(t) }, '+ Colour card'),
    el('button', { class: 'ae-b ic', title: 'Refresh the media', 'aria-label': 'Refresh the media', onclick: () => tlBin(t) }, aeIco('refresh', 14)));
  const icon = { video: 'film', picture: 'image', sound: 'media' };
  const card = m => {
    const thumb = el('span', { class: 'tl-thumb ' + m.kind, html: ico(icon[m.kind], 20) });
    if (m.kind === 'picture') previewUrl(m.path).then(src => { thumb.replaceChildren(el('img', { src, alt: '', loading: 'lazy' })); }).catch(() => {});
    return el('button', { class: 'tl-media-item', title: `Add ${m.path} to the ${m.kind === 'sound' ? 'Sound' : 'Video'} track`, onclick: () => add(m) },
      thumb, el('span', { class: 'tl-media-name', text: m.path }), el('span', { class: 'tl-media-add', html: ico('plus', 14) }));
  };
  u.bin.replaceChildren(tools, d.media.length ? el('div', { class: 'tl-media' }, d.media.map(card))
    : el('div', { class: 'tl-hint', text: 'No videos, pictures or sounds in this folder yet. Ask the agent to make some, or add files.' }));
}
// Pointing the agent at a part: the chat box gets the timeline and the selected part, and the user writes what to change.
function tlAsk(t) {
  const tl = t.tl, sel = tl.sel, lay = tl.layout || {};
  let what = 'the whole video';
  if (sel && sel.type === 'clip' && lay.clips?.[sel.i]) { const c = lay.clips[sel.i]; what = `clip ${sel.i + 1} (${c.label}, ${tlSecs(c.start)}–${tlSecs(c.start + c.duration)} s)`; }
  else if (sel && sel.type === 'title' && lay.titles?.[sel.i]) { const c = lay.titles[sel.i]; what = `title ${sel.i + 1} ("${c.label}", ${tlSecs(c.start)}–${tlSecs(c.start + c.duration)} s)`; }
  else if (sel && sel.type === 'audio' && lay.audio?.[sel.i]) { const c = lay.audio[sel.i]; what = `sound ${sel.i + 1} (${c.label}, from ${tlSecs(c.start)} s)`; }
  const inp = $('#agent-input');
  inp.value = `@${t.path} In this video timeline, ${what}: ` + (inp.value.trim() ? '\n' + inp.value : '');
  autosize(); showSection('chat');
  inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length);
}
async function tlRender(t, preview) {
  const tl = t.tl;
  if (tl.job) return;
  await tl.saved;
  try { const r = await POST('/api/video/render', { ws: S.ws.id, path: t.path, preview }); tlFollow(t, r.job, preview); }
  catch (e) { tlStatus(t, e.message || String(e), 'err'); }
}
function tlFollow(t, id, preview) {
  const tl = t.tl;
  tl.job = { id };
  const tick = async () => {
    if (!S.tabs.includes(t)) return;
    let j;
    try { j = await GET('/api/video/job', { id }); } catch { tl.job = null; return; }
    const u = tl.ui;
    if (u) { u.bar.classList.remove('hidden'); u.fill.style.width = (j.percent || 0) + '%'; u.stop.classList.toggle('hidden', j.state !== 'running');
      tlStatus(t, j.state === 'running' ? `Rendering ${j.preview ? 'a preview' : 'the video'}… ${j.percent || 0}%` : j.state === 'done' ? `Rendered ${j.output}` : `Render failed: ${j.error}`, j.state === 'failed' ? 'err' : j.state === 'done' ? 'ok' : ''); }
    if (j.state === 'running') { setTimeout(tick, 700); return; }
    tl.job = null;
    if (u) { setTimeout(() => u.bar.classList.add('hidden'), 1500); tl.vmtime = -1; tlLoad(t); }
  };
  tick();
}
function tlAgentBanner(t) {
  const u = t.tl && t.tl.ui; if (!u) return;
  u.agent.classList.toggle('hidden', !V.agent);
  u.agent.textContent = V.agent === 'generate' ? 'The agent is making a video clip with ComfyUI (Strata is unloaded meanwhile)…' : 'The agent is rendering this video…';
}
function tlAgentActivity(kind) {
  V.agent = kind;
  for (const t of S.tabs) if (t.tl) { tlAgentBanner(t); if (!kind && S.active === t) { t.tl.vmtime = -1; tlLoad(t, true); } }
}
/* ------------------------------------------------------------------ sidebar views: Timeline, Media, Voices, Tasks */
/* ---- the Timeline view: the project's video timelines, and a new one */
async function videoRefresh() {
  const body = $('#video-body');
  if (!S.ws) { body.replaceChildren(el('div', { class: 'tl-hint', text: 'Open a folder first.' })); return; }
  let d;
  try { d = await GET('/api/video/list', { ws: S.ws.id }); } catch (e) { body.replaceChildren(el('div', { class: 'tl-hint', text: e.message })); return; }
  const ago = s => { const m = Math.round((Date.now() / 1000 - s) / 60); return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : new Date(s * 1000).toLocaleDateString(); };
  body.replaceChildren(
    d.timelines.length ? el('div', { class: 'video-list' }, d.timelines.map(v => el('button', { class: 'video-item', title: 'Open ' + v.path, onclick: () => openFile(v.path) },
      el('span', { html: ico('film', 15) }), el('span', { class: 'video-name', text: v.path.replace(/\.timeline\.json$/, '') }), el('small', { text: ago(v.mtime) }))))
      : el('div', { class: 'tl-hint', text: 'No videos in this folder yet. Make one with +, or ask the agent: it saves a timeline beside every video it renders.' }),
    el('div', { class: 'tl-hint', text: `${d.media.length} picture${d.media.length === 1 ? '' : 's'}, video${d.media.length === 1 ? '' : 's'} and sound${d.media.length === 1 ? '' : 's'} in this folder can go on a timeline.` }));
}
$('#video-refresh').onclick = videoRefresh;
$('#video-new').onclick = async () => {
  if (!S.ws) { toast('Open a folder first', 'info'); return; }
  const name = await promptBox('New timeline', '', 'Name, e.g. club-trailer');
  if (!name) return;
  try { const r = await POST('/api/video/new', { ws: S.ws.id, name: name.trim() }); refreshTreeSoon(); await openFile(r.path); videoRefresh(); }
  catch (e) { errToast(e, 'Could not make the video'); }
};
/* ---- Media: every sound, picture and video in the project, newest first. Sounds play in place, pictures show as
   thumbnails, and everything opens in the editor or goes into the chat box with @. The filter stays per browser. */
const MD = { kind: lsGet('studio.mediaKind') || 'all', sort: lsGet('studio.mediaSort') || 'new', q: '', items: [] };
const MEDIA_NAME = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });
const mediaBase = m => m.path.split('/').pop();
// Each order breaks ties by newest first; files without a length (pictures, 3D) go after the rest in the length orders.
const MEDIA_SORTS = [
  ['new', 'Newest first', (a, b) => b.mtime - a.mtime],
  ['old', 'Oldest first', (a, b) => a.mtime - b.mtime],
  ['az', 'Name A–Z', (a, b) => MEDIA_NAME.compare(mediaBase(a), mediaBase(b))],
  ['za', 'Name Z–A', (a, b) => MEDIA_NAME.compare(mediaBase(b), mediaBase(a))],
  ['big', 'Largest first', (a, b) => b.size - a.size],
  ['small', 'Smallest first', (a, b) => a.size - b.size],
  ['long', 'Longest first', (a, b) => (b.seconds ?? -1) - (a.seconds ?? -1)],
  ['short', 'Shortest first', (a, b) => (a.seconds ?? Infinity) - (b.seconds ?? Infinity)],
  ['folder', 'By folder', (a, b) => MEDIA_NAME.compare(a.path, b.path)],
];
function mediaSorted(items) {
  const by = (MEDIA_SORTS.find(x => x[0] === MD.sort) || MEDIA_SORTS[0])[2];
  return items.slice().sort((a, b) => by(a, b) || b.mtime - a.mtime);
}
const MEDIA_KINDS = [['all', 'All'], ['sound', 'Sounds'], ['picture', 'Pictures'], ['video', 'Videos'], ['model', '3D']];
function mediaAgo(s) { const m = Math.round((Date.now() / 1000 - s) / 60); return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : new Date(s * 1000).toLocaleDateString(); }
function mediaSize(n) { return n < 1024 ? n + ' B' : n < 1048576 ? Math.round(n / 1024) + ' KB' : (n / 1048576).toFixed(1) + ' MB'; }
function mediaMention(path) {
  const i = $('#agent-input'); i.value = (i.value && !/\s$/.test(i.value) ? i.value + ' ' : i.value) + '@' + path + ' '; autosize(); i.focus();
  if (document.documentElement.classList.contains('phone')) showSection('chat');
}
async function mediaRefresh() {
  const body = $('#media-body');
  if (!S.ws) { body.replaceChildren(el('div', { class: 'tl-hint', text: 'Open a folder first.' })); return; }
  if (!MD.items.length || MD.ws !== S.ws.id) body.replaceChildren(skeleton(5, 'sk-list'));
  try { const d = await GET('/api/media/list', { ws: S.ws.id }); MD.items = d.media; MD.more = d.more; MD.ws = S.ws.id; }
  catch (e) { body.replaceChildren(el('div', { class: 'tl-hint', text: e.message })); return; }
  mediaRender();
}
function mediaRender() {
  const body = $('#media-body'), q = MD.q.trim().toLowerCase();
  const count = k => MD.items.filter(m => k === 'all' || m.kind === k).length;
  const shown = mediaSorted(MD.items.filter(m => (MD.kind === 'all' || m.kind === MD.kind) && (!q || m.path.toLowerCase().includes(q))));
  let search = $('#media-body .media-search');
  if (!search) {
    search = el('input', { class: 'dlg-input media-search', type: 'search', placeholder: 'Search by name or folder', 'aria-label': 'Search media' });
    search.addEventListener('input', debounce(() => { MD.q = search.value; mediaRender(); }, 150));
  }
  const chips = el('div', { class: 'media-chips' }, MEDIA_KINDS.map(([k, label]) => el('button', { class: 'chip' + (MD.kind === k ? ' on' : ''), 'aria-pressed': MD.kind === k ? 'true' : 'false',
    text: `${label} ${count(k)}`, onclick: () => { MD.kind = k; lsSet('studio.mediaKind', k); mediaRender(); } })));
  if (!MD.sortEl) {
    MD.sortEl = el('select', { class: 'cd-select media-sort', 'aria-label': 'Sort media', title: 'Sort', onchange: () => { MD.sort = MD.sortEl.value; lsSet('studio.mediaSort', MD.sort); mediaRender(); } },
      MEDIA_SORTS.map(([k, label]) => el('option', { value: k, text: label })));
    MD.sortEl.value = MD.sort;
  }
  const list = el('div', { class: 'media-list' });
  for (const m of shown.filter(x => x.kind === 'sound' || x.kind === 'video')) list.append(mediaRow(m));
  const models = shown.filter(m => m.kind === 'model');
  if (models.length) list.append(el('div', { class: 'media-grid models' }, models.slice(0, 300).map(mediaModelTile)));
  const pics = shown.filter(m => m.kind === 'picture');
  if (pics.length) list.append(el('div', { class: 'media-grid' }, pics.slice(0, 300).map(mediaTile)));
  const rest = [chips, shown.length ? list : el('div', { class: 'tl-hint', text: MD.items.length ? 'Nothing matches.' : 'No sounds, pictures, videos or 3D models in this folder yet. What the agent makes (music, sound effects, voices, pictures, videos, 3D models) shows up here.' })];
  if (shown.length) rest.splice(1, 0, el('div', { class: 'media-tools' }, el('label', { class: 'media-sort-wrap' }, el('span', { html: ico('sort', 13) }), MD.sortEl),
    el('button', { class: 'btn sm media-dl-all', title: 'Download every file shown here, as one zip', onclick: () => downloadPaths(shown.map(m => m.path)) },
      el('span', { html: ico('download', 13) }), shown.length === 1 ? ' Download it' : ` Download all ${shown.length} (zip)`)));
  if (MD.more) rest.push(el('div', { class: 'tl-hint', text: `Showing the first ${MD.items.length} files.` }));
  if (search.parentNode !== body) body.replaceChildren(search, ...rest);
  else { while (search.nextSibling) search.nextSibling.remove(); body.append(...rest); }
}
function mediaActions(m) {
  return el('div', { class: 'media-acts' },
    m.timeline ? el('button', { class: 'link-btn card-link', text: 'Timeline', title: 'Open its timeline, ' + m.timeline, 'aria-label': 'Open the timeline of ' + m.path,
      onclick: e => { e.stopPropagation(); openFile(m.timeline); } }) : '',
    el('button', { class: 'link-btn card-link', text: 'Open', title: 'Open in the editor', 'aria-label': 'Open ' + m.path, onclick: e => { e.stopPropagation(); openFile(m.path); } }),
    AUDIO_EDIT.test(m.path) ? el('button', { class: 'link-btn card-link', text: 'Edit', title: 'Open it in the wave editor', 'aria-label': 'Edit ' + m.path,
      onclick: e => { e.stopPropagation(); S.aeEditNext = m.path; const open = S.tabs.find(x => x.path === m.path && x.ae); if (open && S.active === open) { S.aeEditNext = null; aeEnterEdit(open); } else openFile(m.path); } }) : '',
    el('button', { class: 'link-btn card-link', text: '@', title: 'Mention it in your next message', 'aria-label': 'Mention ' + m.path, onclick: e => { e.stopPropagation(); mediaMention(m.path); } }),
    el('a', { class: 'link-btn card-link media-dl', href: fileDownloadUrl(m.path), download: basename(m.path), title: 'Download to this computer', 'aria-label': 'Download ' + m.path, html: ico('download', 13), onclick: e => e.stopPropagation() }));
}
function mediaRow(m) {
  const name = m.path.split('/').pop(), folder = m.path.slice(0, -name.length - 1);
  const meta = [m.seconds ? `${m.seconds} s` : '', mediaSize(m.size), mediaAgo(m.mtime)].filter(Boolean).join(' · ');
  const row = el('div', { class: 'media-row kind-' + m.kind, title: m.path },
    el('div', { class: 'media-head' }, el('span', { class: 'media-ico', html: ico(m.kind === 'video' ? 'film' : 'media', 14) }),
      el('span', { class: 'media-name', text: name }), mediaActions(m)),
    el('div', { class: 'media-meta', text: (folder ? folder + '/ · ' : '') + meta }));
  if (m.kind === 'sound') {
    const audio = el('audio', { controls: '', preload: 'none', class: 'media-audio' });
    previewUrl(m.path).then(u => { audio.src = u + '?v=' + m.mtime; }).catch(() => {});
    audio.addEventListener('play', () => { for (const a of $$('#media-body audio')) if (a !== audio) a.pause(); });
    row.append(audio);
  } else row.onclick = () => openFile(m.path);
  return row;
}
const MODEL_SEEN = new IntersectionObserver(es => { for (const e of es) if (e.isIntersecting) { MODEL_SEEN.unobserve(e.target); e.target._thumb?.(); } }, { rootMargin: '200px' });
function mediaModelTile(m) {
  const name = m.path.split('/').pop(), img = el('img', { alt: name });
  const pic = el('div', { class: 'model-thumb' }, img, el('span', { class: 'model-badge', text: name.split('.').pop().toUpperCase() }), el('span', { class: 'model-ph', html: ico('cube', 34) }));
  pic._thumb = () => modelThumb(m, img, pic);
  MODEL_SEEN.observe(pic);
  return el('div', { class: 'media-tile kind-model', title: `${m.path} · ${mediaSize(m.size)} · ${mediaAgo(m.mtime)}`, onclick: () => openFile(m.path) },
    pic, el('div', { class: 'media-tile-name' }, el('span', { text: name }), mediaActions(m)));
}
function mediaTile(m) {
  const name = m.path.split('/').pop(), img = el('img', { alt: name, loading: 'lazy' });
  previewUrl(m.path).then(u => { img.src = u + '?v=' + m.mtime; }).catch(() => {});
  return el('div', { class: 'media-tile', title: `${m.path} · ${mediaSize(m.size)} · ${mediaAgo(m.mtime)}`, onclick: () => openFile(m.path) },
    img, el('div', { class: 'media-tile-name' }, el('span', { text: name }), mediaActions(m)));
}
$('#media-refresh').onclick = mediaRefresh;
/* ---- Voices: a shared cast read by the local voice engine (VoxCPM2). A voice is its description plus the take someone kept. */
const VO = { voices: [], reading: null, draft: lsGet('studio.voiceText') || '' };
function voiceEngineText(d) {
  if (!d.enabled) return ['off', 'Voices are not set up on this Studio (config voice_url).'];
  const e = d.engine || {};
  if (!e.ok) return ['err', e.error || 'The voice engine is not running.'];
  if (e.busy) return ['work', `Reading now${e.queue ? `, ${e.queue} waiting` : ''}.`];
  return [e.loaded ? 'idle' : 'off', e.loaded ? 'Ready on the graphics card.' : 'Ready; it loads on the graphics card when needed (about 15 s).'];
}
// Waits for a voice job, moving a bar; resolves with the finished state.
async function voiceWait(id, bar, label) {
  for (;;) {
    let st; try { st = await GET('/api/voices/job', { id }); } catch (e) { throw e; }
    const what = { queued: st.ahead ? `Waiting for ${st.ahead} reading${st.ahead > 1 ? 's' : ''} before this one…` : 'Starting…', waiting_card: 'Waiting for the graphics card (a picture or Strata is using it)…',
      loading: 'Loading the voice model…', working: 'Reading…' }[st.state];
    if (bar) bar.style.width = Math.round((st.progress || 0) * 100) + '%';
    if (label && what) label.textContent = what;
    if (st.state === 'done' || st.state === 'error' || st.state === 'cancelled') return st;
    await new Promise(r => setTimeout(r, 1200));
  }
}
function voiceProgress() {
  const bar = el('div', { class: 'vo-bar-fill' }), label = el('small', { class: 'vo-status', text: 'Starting…' });
  return { box: el('div', { class: 'vo-progress' }, el('div', { class: 'vo-bar' }, bar), label), bar, label };
}
async function voicesRefresh() {
  const body = $('#voices-body');
  let d; try { d = await GET('/api/voices'); } catch (e) { body.replaceChildren(el('div', { class: 'tl-hint', text: e.message })); return; }
  VO.voices = d.voices; VO.sampleText = d.sample_text; VO.sampleTexts = d.sample_texts;
  if (VO.castLang === undefined) VO.castLang = lsGet('studio.voiceLang') || 'all';
  const [state, text] = voiceEngineText(d);
  const status = el('div', { class: 'vo-engine' }, el('span', { class: 'dash-dot', 'data-state': state }), el('small', { text }));
  if (!d.enabled) { body.replaceChildren(status); return; }
  // Read aloud
  const langs = d.langs || { el: 'Greek', en: 'English' }, langOf = v => v.lang || 'el';
  const groups = Object.keys(langs).filter(l => d.voices.some(v => langOf(v) === l));
  const pick = el('select', { class: 'dlg-input', 'aria-label': 'Voice' }, groups.map(l => el('optgroup', { label: VOICE_LANG_NAMES[l] || langs[l] },
    d.voices.filter(v => langOf(v) === l).map(v => el('option', { value: v.id, text: v.name })))));
  if (VO.lastVoice && d.voices.some(v => v.id === VO.lastVoice)) pick.value = VO.lastVoice;
  const area = el('textarea', { class: 'dlg-input vo-text', rows: '5', placeholder: 'Text to read, in Greek or any of 30 languages…', 'aria-label': 'Text to read' });
  area.value = VO.draft;
  area.oninput = () => { VO.draft = area.value; lsSet('studio.voiceText', area.value.slice(0, 20000)); };
  const style = el('input', { class: 'dlg-input', type: 'text', maxlength: '200', placeholder: 'Delivery (optional): slower, cheerful, whispering…', 'aria-label': 'Delivery' });
  const fmt = el('select', { class: 'dlg-input vo-fmt', 'aria-label': 'Format' }, el('option', { value: 'mp3', text: 'MP3' }), el('option', { value: 'wav', text: 'WAV' }));
  const result = el('div', { class: 'vo-result' });
  const read = el('button', { class: 'btn sm primary', onclick: async () => {
    if (!area.value.trim()) { area.focus(); return; }
    VO.lastVoice = pick.value; read.disabled = true;
    const p = voiceProgress(); result.replaceChildren(p.box);
    try {
      const { job } = await POST('/api/voices/speak', { voice: pick.value, text: area.value, style: style.value || undefined, format: fmt.value });
      const st = await voiceWait(job, p.bar, p.label);
      if (st.state !== 'done') { result.replaceChildren(el('div', { class: 'warn-text', text: st.error || 'The reading stopped.' })); return; }
      const name = (d.voices.find(v => v.id === pick.value) || {}).name || 'voice';
      const file = `voice/${name.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '') || 'reading'}-${new Date().toISOString().slice(11, 19).replace(/:/g, '')}.${fmt.value}`;
      result.replaceChildren(el('audio', { src: st.url, controls: '', preload: 'auto', class: 'vo-audio' }),
        el('small', { class: 'vo-status', text: `${st.audio_s} s of speech, made in ${Math.round(st.took_s)} s` }),
        el('div', { class: 'vo-acts' }, el('a', { class: 'btn sm', href: st.url, download: file.split('/').pop() }, 'Download'),
          el('button', { class: 'btn sm', disabled: !S.ws || null, title: S.ws ? 'Save it into the open folder' : 'Open a folder first', onclick: async () => {
            const path = await promptBox('Save into the project as', file); if (!path) return;
            try { const r = await POST('/api/voices/to_project', { ws: S.ws.id, job, path }); toast(`Saved ${r.path}`, 'success'); refreshTreeSoon(); }
            catch (e) { errToast(e, 'Could not save it'); }
          } }, 'Save to project')));
    } catch (e) { result.replaceChildren(el('div', { class: 'warn-text', text: e.message })); }
    finally { read.disabled = false; }
  } }, 'Read aloud');
  const readBox = el('div', { class: 'vo-card' }, el('div', { class: 'dlg-sub', text: 'Read aloud' }), pick, area, style,
    el('div', { class: 'vo-acts' }, read, fmt, el('small', { class: 'vo-note', text: 'About 3 s of work per second of speech.' })), result);
  // The cast, by language, with a filter kept per browser
  const castLang = groups.includes(VO.castLang) ? VO.castLang : 'all';
  const chips = groups.length > 1 ? el('div', { class: 'media-chips vo-chips' }, [['all', 'All'], ...groups.map(l => [l, VOICE_LANG_NAMES[l] || langs[l]])].map(([k, label]) =>
    el('button', { class: 'chip' + (castLang === k ? ' on' : ''), 'aria-pressed': castLang === k ? 'true' : 'false',
      text: `${label} ${k === 'all' ? d.voices.length : d.voices.filter(v => langOf(v) === k).length}`, onclick: () => { VO.castLang = k; lsSet('studio.voiceLang', k); voicesRefresh(); } }))) : el('span');
  const castShown = d.voices.filter(v => castLang === 'all' || langOf(v) === castLang);
  const list = el('div', { class: 'vo-list' }, castShown.length ? castShown.map(v => el('div', { class: 'vo-item', 'data-lang': langOf(v) },
    el('div', { class: 'vo-head' }, el('b', { text: v.name }), el('span', { class: 'vo-lang', title: langs[langOf(v)] || '', text: langOf(v).toUpperCase() }), v.can_edit ? el('span', { class: 'vo-tools' },
      el('button', { class: 'icon-btn sm', title: 'Change its name or words', 'aria-label': 'Edit ' + v.name, html: ico('edit', 13), onclick: () => voiceEdit(v) }),
      el('button', { class: 'icon-btn sm', title: 'Delete this voice', 'aria-label': 'Delete ' + v.name, html: ico('trash', 13), onclick: async () => {
        if (!(await confirmBox('Delete voice', `Delete "${v.name}" for everyone?`, 'Delete', true))) return;
        try { await POST('/api/voices/delete', { id: v.id }); voicesRefresh(); } catch (e) { errToast(e, 'Could not delete'); }
      } })) : null),
    el('small', { class: 'vo-desc', text: v.description || 'No description' }),
    v.origin === 'recording' ? el('small', { class: 'vo-desc vo-origin', title: v.transcript ? 'Transcript: ' + v.transcript : 'No transcript: add one with Edit for a closer copy',
      text: `From a recording${v.speaker ? ' of ' + v.speaker : ''}${v.consent ? ' · ' + ({ own: 'their own voice', made: 'a made voice', permission: 'with written permission' }[v.consent] || v.consent) : ''}${v.transcript ? '' : ' · no transcript'}` }) : null,
    v.style ? el('small', { class: 'vo-desc', text: 'Usual delivery: ' + v.style }) : null,
    el('audio', { src: `/api/voices/audio?voice=${encodeURIComponent(v.id)}`, controls: '', preload: 'none', class: 'vo-audio' }))) : el('div', { class: 'tl-hint', text: 'No voices yet. Make one with +.' }));
  body.replaceChildren(status, d.voices.length ? readBox : el('span'), el('div', { class: 'dlg-sub vo-cast', text: `Cast (${d.voices.length})` }), chips, list,
    el('div', { class: 'tl-hint', text: 'The agent can use these voices too, through the voice tools: "read this in the calm narrator\'s voice into voice/intro.mp3".' }));
}
const VOICE_LANG_NAMES = { el: 'Ελληνικά', en: 'English' };
function voiceLangPick(value) {
  return el('select', { class: 'dlg-input', 'aria-label': 'Language' }, Object.entries(VOICE_LANG_NAMES).map(([k, label]) => el('option', { value: k, text: label, selected: k === value || null })));
}
async function voiceEdit(v) {
  const name = el('input', { class: 'dlg-input', type: 'text', maxlength: '60', value: v.name });
  const lang = voiceLangPick(v.lang || 'el');
  const desc = el('textarea', { class: 'dlg-input', rows: '2', maxlength: '300' }); desc.value = v.description || '';
  const style = el('input', { class: 'dlg-input', type: 'text', maxlength: '200', value: v.style || '', placeholder: 'e.g. calm, like a fairy tale' });
  const transcript = el('textarea', { class: 'dlg-input', rows: '2', maxlength: '2000', placeholder: 'The exact words spoken in its recording' }); transcript.value = v.transcript || '';
  const body = el('div', { class: 'dlg-form' }, el('label', {}, 'Name', name), el('label', {}, 'Language', lang), el('label', {}, 'What it sounds like', desc), el('label', {}, 'Usual delivery', style),
    v.origin === 'recording' ? el('label', {}, 'Words spoken in its recording', transcript) : null,
    el('small', { class: 'dlg-note', text: 'The voice itself stays as it is: it comes from the recording kept when it was made. The description is for people and the agent; the usual delivery is used for every reading unless another is given.' }));
  if ((await dialog({ title: 'Edit voice', body, small: false, buttons: [{ label: 'Cancel', value: false }, { label: 'Save', value: true, kind: 'primary' }] })) !== true) return;
  try { await POST('/api/voices/save', { id: v.id, name: name.value, lang: lang.value, description: desc.value, style: style.value, ...(v.origin === 'recording' ? { transcript: transcript.value } : {}) }); voicesRefresh(); } catch (e) { errToast(e, 'Could not save the voice'); }
}
async function voiceNew() {
  const name = el('input', { class: 'dlg-input', type: 'text', maxlength: '60', placeholder: 'e.g. Calm narrator' });
  const desc = el('textarea', { class: 'dlg-input', rows: '3', maxlength: '300', placeholder: 'What it should sound like, in Greek or English: who, age, warmth, pace, e.g. "a warm woman in her forties, calm, slow, very clear diction"' });
  const sample = el('input', { class: 'dlg-input', type: 'text', maxlength: '600' }); sample.value = VO.sampleText || '';
  const lang = voiceLangPick(VO.castLang === 'en' ? 'en' : 'el');
  const samples = () => VO.sampleTexts || { el: VO.sampleText };
  if (samples()[lang.value]) sample.value = samples()[lang.value];
  // a new language brings its own sample sentence, unless someone wrote their own
  lang.onchange = () => { if (Object.values(samples()).includes(sample.value) || !sample.value.trim()) sample.value = samples()[lang.value] || sample.value; };
  const takes = el('div', { class: 'vo-takes' });
  let chosen = null;
  const make = el('button', { class: 'btn sm primary', onclick: async () => {
    if (!desc.value.trim()) { desc.focus(); return; }
    make.disabled = true; chosen = null; saveBtn.disabled = true;
    try {
      const { jobs } = await POST('/api/voices/design', { description: desc.value, text: sample.value, takes: 3 });
      takes.replaceChildren(...jobs.map((job, i) => {
        const p = voiceProgress(), slot = el('div', { class: 'vo-take' }, el('b', { text: `Take ${i + 1}` }), p.box);
        voiceWait(job, p.bar, p.label).then(st => {
          if (st.state !== 'done') { slot.replaceChildren(el('b', { text: `Take ${i + 1}` }), el('small', { class: 'warn-text', text: st.error || 'stopped' })); return; }
          const radio = el('input', { type: 'radio', name: 'vo-take', value: job, onchange: () => { chosen = job; saveBtn.disabled = !name.value.trim(); } });
          slot.replaceChildren(el('label', { class: 'vo-pick' }, radio, el('b', { text: `Take ${i + 1}` })), el('audio', { src: st.url, controls: '', preload: 'auto', class: 'vo-audio' }));
        }).catch(e => slot.replaceChildren(el('small', { class: 'warn-text', text: e.message })));
        return slot;
      }));
    } catch (e) { takes.replaceChildren(el('div', { class: 'warn-text', text: e.message })); }
    finally { make.disabled = false; }
  } }, 'Make 3 takes');
  name.oninput = () => { saveBtn.disabled = !(chosen && name.value.trim()); };
  const saveBtn = el('button', { class: 'btn primary', disabled: true, onclick: async () => {
    try { await POST('/api/voices/save', { job: chosen, name: name.value, lang: lang.value, description: desc.value }); closeOverlay(entryRef.entry); toast(`Added ${name.value.trim()} to the cast`, 'success'); voicesRefresh(); }
    catch (e) { errToast(e, 'Could not keep the voice'); }
  } }, 'Keep the chosen take');
  const body = el('div', { class: 'dlg-form' }, el('label', {}, 'Name', name), el('label', {}, 'Language', lang), el('label', {}, 'What it should sound like', desc), el('label', {}, 'Sample sentence', sample),
    el('div', { class: 'vo-acts' }, make, el('small', { class: 'vo-note', text: 'Each take is a different voice for the same words; listen, then pick one. Making them takes about a minute each.' })), takes,
    el('small', { class: 'dlg-note', text: 'The take you keep becomes the voice: every later reading sounds like it. To copy a voice from a recording instead, use the microphone button.' }));
  const entryRef = {};
  const shown = dialog({ title: 'New voice', body, small: false, buttons: [{ label: 'Close', value: false }] });
  entryRef.entry = overlayStack[overlayStack.length - 1];
  body.closest('.dialog').querySelector('.dlg-foot').prepend(saveBtn);
  await shown;
}
// A voice copied from a recording in the open folder: a test reading first, then kept with its transcript and consent.
async function voiceFromRecording(preset) {
  if (!S.ws) { toast('Open the folder with the recording first', 'warn'); return; }
  let sounds = [];
  try { sounds = ((await GET('/api/media/list', { ws: S.ws.id })).media || []).filter(m => m.kind === 'sound' || m.kind === 'video'); } catch (e) { /* typed by hand */ }
  const file = el('input', { class: 'dlg-input', type: 'text', list: 'vo-rec-files', placeholder: 'voice/mello/reference.wav', 'aria-label': 'Recording' });
  if (preset) file.value = preset;
  const files = el('datalist', { id: 'vo-rec-files' }, sounds.map(m => el('option', { value: m.path })));
  const start = el('input', { class: 'dlg-input vo-time', type: 'number', min: '0', step: '0.1', placeholder: 'start', 'aria-label': 'Start, seconds' });
  const end = el('input', { class: 'dlg-input vo-time', type: 'number', min: '0', step: '0.1', placeholder: 'end', 'aria-label': 'End, seconds' });
  const name = el('input', { class: 'dlg-input', type: 'text', maxlength: '60', placeholder: 'e.g. Mello' });
  const lang = voiceLangPick(VO.castLang === 'en' ? 'en' : 'el');
  const transcript = el('textarea', { class: 'dlg-input', rows: '2', maxlength: '2000', placeholder: 'The exact words spoken in the recording (or in the part you chose). Optional, but the copy is much closer with it.' });
  const desc = el('input', { class: 'dlg-input', type: 'text', maxlength: '300', placeholder: 'What it sounds like, for people and the agent' });
  const speaker = el('input', { class: 'dlg-input', type: 'text', maxlength: '200', placeholder: 'e.g. me; Mello, a designed voice we own; Anna P., voice actor' });
  const consent = el('select', { class: 'dlg-input', 'aria-label': 'Why it may be copied' },
    el('option', { value: '', text: 'Choose…' }), el('option', { value: 'own', text: 'It is my own voice' }),
    el('option', { value: 'made', text: 'A voice made by a machine, which we may reuse' }), el('option', { value: 'permission', text: 'The speaker gave written permission' }));
  const take = el('div', { class: 'vo-takes' });
  let job = null;
  const ready = () => { saveBtn.disabled = !(job && name.value.trim()); };
  const tryBtn = el('button', { class: 'btn sm primary', onclick: async () => {
    if (!file.value.trim()) { file.focus(); return; }
    if (!speaker.value.trim()) { speaker.focus(); return; }
    if (!consent.value) { consent.focus(); return; }
    tryBtn.disabled = true; job = null; ready();
    const p = voiceProgress(); take.replaceChildren(p.box);
    try {
      const r = await POST('/api/voices/from_recording', { ws: S.ws.id, path: file.value.trim(), start: start.value, end: end.value, transcript: transcript.value,
        speaker: speaker.value, consent: consent.value, lang: lang.value });
      const st = await voiceWait(r.job, p.bar, p.label);
      if (st.state !== 'done') { take.replaceChildren(el('div', { class: 'warn-text', text: st.error || 'The reading stopped.' })); return; }
      job = r.job; ready();
      take.replaceChildren(el('div', { class: 'vo-take' }, el('b', { text: `Test reading, from ${r.seconds} s of the recording` }), el('audio', { src: st.url, controls: '', preload: 'auto', class: 'vo-audio' })));
    } catch (e) { take.replaceChildren(el('div', { class: 'warn-text', text: e.message })); }
    finally { tryBtn.disabled = false; }
  } }, 'Make a test reading');
  name.oninput = ready;
  const saveBtn = el('button', { class: 'btn primary', disabled: true, onclick: async () => {
    try { await POST('/api/voices/save', { job, name: name.value, lang: lang.value, description: desc.value, transcript: transcript.value }); closeOverlay(entryRef.entry); toast(`Added ${name.value.trim()} to the cast`, 'success'); voicesRefresh(); }
    catch (e) { errToast(e, 'Could not keep the voice'); }
  } }, 'Keep as a voice');
  const body = el('div', { class: 'dlg-form vo-rec' }, files,
    el('label', {}, 'Recording in this folder', file), el('div', { class: 'vo-times' }, el('span', { text: 'Only part of it (seconds):' }), start, end),
    el('label', {}, 'Words spoken in it', transcript), el('label', {}, 'Name', name), el('label', {}, 'Language spoken', lang), el('label', {}, 'What it sounds like', desc),
    el('label', {}, 'Whose voice is it?', speaker), el('label', {}, 'Why may it be copied?', consent),
    el('div', { class: 'vo-acts' }, tryBtn, el('small', { class: 'vo-note', text: '3 to 15 seconds of one person speaking clearly, without music, works best (at most 30).' })), take,
    el('small', { class: 'dlg-note', text: 'Copy only your own voice, a voice made by a machine that you may reuse, or someone who agreed in writing; never a public figure or another service\'s voice library. Studio keeps whose voice it is and why with the voice. The recording itself becomes the voice, so every reading sounds like it.' }));
  const entryRef = {};
  const shown = dialog({ title: 'Voice from a recording', body, small: false, buttons: [{ label: 'Close', value: false }] });
  entryRef.entry = overlayStack[overlayStack.length - 1];
  body.closest('.dialog').querySelector('.dlg-foot').prepend(saveBtn);
  await shown;
}
$('#voices-refresh').onclick = voicesRefresh;
$('#voice-new').onclick = voiceNew;
$('#voice-rec').onclick = () => voiceFromRecording();
/* ---- Tasks: agent runs on a timetable, and /loop */
const TK = { tasks: [], now: 0 };
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const TASK_MODES = { look: ['Look only', 'Plan first: it may look, and changes nothing'], ask: ['Stop at what needs a yes', 'Edits and commands that need approval are skipped, and the result lists them'], auto: ['Do everything', 'Edits and commands run without asking'] };
const TASK_FAILED = new Set(['error', 'stuck', 'cut_off', 'max_turns', 'skipped']);
function scheduleText(sc) {
  if (!sc) return '';
  if (sc.kind === 'every') return sc.minutes % 60 === 0 ? `Every ${sc.minutes / 60 === 1 ? 'hour' : sc.minutes / 60 + ' hours'}` : `Every ${sc.minutes} minute${sc.minutes === 1 ? '' : 's'}`;
  if (sc.kind === 'hourly') return `Every hour at :${sc.time.slice(3)}`;
  if (sc.kind === 'daily') return `Every day at ${sc.time}`;
  if (sc.kind === 'weekdays') return `Weekdays at ${sc.time}`;
  return `${WEEKDAYS[sc.weekday]}s at ${sc.time}`;
}
function whenText(at) {
  const d = new Date(at * 1000), now = new Date(), diff = at * 1000 - Date.now();
  if (Math.abs(diff) < 60000) return diff >= 0 ? 'in a moment' : 'just now';
  if (Math.abs(diff) < 3600000) return diff > 0 ? `in ${Math.round(diff / 60000)} min` : `${Math.round(-diff / 60000)} min ago`;
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return d.toDateString() === now.toDateString() ? `today ${time}` : `${d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })} ${time}`;
}
function taskState(t) {
  if (t.running) return 'running';
  const last = t.last;
  if (last && (TASK_FAILED.has(last.reason) || last.refused?.length) && !t.seen_ok) return 'attention';
  if (!t.enabled) return t.loop ? 'done' : 'paused';
  return 'scheduled';
}
function taskStatus(t) {
  const st = taskState(t), last = t.last;
  if (st === 'running') return 'Running now';
  if (t.waiting && t.enabled) return 'Waiting: ' + t.waiting;
  if (st === 'attention') return last.refused?.length ? `Needed a yes ${last.refused.length} time${last.refused.length === 1 ? '' : 's'}` : (DONE_TXT[last.reason] || last.summary || last.reason);
  if (!t.enabled) return t.paused_why || 'Paused';
  return 'Next ' + whenText(t.next_run) + (last ? ` · last ${whenText(last.ended)}` : '');
}
async function tasksRefresh() {
  const body = $('#tasks-body');
  let d;
  try { d = await GET('/api/tasks'); } catch (e) { body.replaceChildren(el('div', { class: 'tl-hint', text: e.message })); return; }
  TK.tasks = d.tasks || []; TK.now = d.now;
  const attention = TK.tasks.filter(t => taskState(t) === 'attention').length;
  $('#tasks-badge').textContent = attention || ''; $('#tasks-badge').classList.toggle('hidden', !attention);
  if (!$('#view-tasks') || $('#view-tasks').classList.contains('hidden')) return;
  const groups = [['running', 'Running'], ['attention', 'Needs attention'], ['scheduled', 'Scheduled'], ['paused', 'Paused'], ['done', 'Loops that ended']];
  const card = t => el('button', { class: 'task-card ' + taskState(t), onclick: () => taskDetail(t) },
    el('div', { class: 'task-title' }, taskState(t) === 'running' ? el('span', { class: 'spin' }) : el('span', { html: ico(t.loop ? 'refresh' : 'clock', 13) }), el('span', { class: 'grow', text: t.title })),
    el('div', { class: 'task-meta', text: `${scheduleText(t.schedule)} · ${TASK_MODES[t.mode][0]}${t.folder ? ' · ' + t.folder : ''}` }),
    el('div', { class: 'task-status', text: taskStatus(t) }));
  const parts = groups.map(([key, label]) => { const items = TK.tasks.filter(t => taskState(t) === key); return items.length ? [el('div', { class: 'task-group', text: `${label} (${items.length})` }), ...items.map(card)] : []; }).flat();
  // replaceChildren would show a null as the text "null", so leave the empty parts out.
  body.replaceChildren(...[...(parts.length ? parts : [el('div', { class: 'tl-hint', text: 'No tasks yet. A task sends the agent a message on a timetable, such as every weekday at 08:00, and keeps the result here. Make one with +, or type /loop 10m … in the chat to repeat a message.' })]),
    d.enabled ? null : el('div', { class: 'tl-hint', text: 'Tasks are switched off on this server (config "tasks").' })].filter(Boolean));
}
$('#tasks-refresh').onclick = tasksRefresh;
$('#task-new').onclick = () => taskForm();
async function taskAction(t, action) {
  if (action === 'delete' && !(await confirmBox('Delete task', `Delete "${t.title}"? Its chats stay.`, 'Delete', true))) return false;
  try { await POST('/api/tasks/action', { id: t.id, action }); tasksRefresh(); return true; } catch (e) { errToast(e, 'Task'); return false; }
}
function taskForm(t = null, draft = null, problem = '') {
  if (!S.ws && !t) { toast('Open a folder first: a task works in a folder', 'info'); return; }
  const v = draft || t || {};          // a refused form comes back with what was typed
  const sc = v.schedule || { kind: 'weekdays', time: '08:00' };
  const title = el('input', { class: 'dlg-input', value: v.title || '', placeholder: 'A short name, e.g. Morning test run' });
  const prompt = el('textarea', { class: 'plan-edit', rows: '5', placeholder: 'What the agent should do, as you would write it in the chat' }); prompt.value = v.prompt || '';
  const kind = el('select', { class: 'dlg-input' }, [['hourly', 'Every hour'], ['daily', 'Every day'], ['weekdays', 'Weekdays (Monday to Friday)'], ['weekly', 'Once a week'], ['every', 'Every few minutes']].map(([v, l]) => el('option', { value: v, text: l })));
  kind.value = sc.kind;
  const at = el('input', { type: 'time', class: 'dlg-input', value: sc.time || '08:00' });
  const day = el('select', { class: 'dlg-input' }, WEEKDAYS.map((w, i) => el('option', { value: String(i), text: w }))); day.value = String(sc.weekday ?? 0);
  const minutes = el('input', { type: 'number', class: 'dlg-input', min: '1', max: '1440', value: String(sc.minutes || 30) });
  const modes = Object.entries(TASK_MODES).map(([v, [l, sub]]) => el('label', { class: 'q-opt' }, el('input', { type: 'radio', name: 'task-mode', value: v, checked: ((draft || t)?.mode || 'look') === v ? '' : null }), el('span', { class: 'q-label' }, el('b', { text: l }), el('small', { text: sub }))));
  const model = el('select', { class: 'dlg-input' }, S.models.map(m => el('option', { value: m.name, text: labelForModel(m) }))); model.value = v.model || S.model;
  const atRow = el('label', { class: 'task-field' }, el('span', { text: 'At' }), at), dayRow = el('label', { class: 'task-field' }, el('span', { text: 'On' }), day), minRow = el('label', { class: 'task-field' }, el('span', { text: 'Every (minutes)' }), minutes);
  const sync = () => { atRow.classList.toggle('hidden', kind.value === 'every'); dayRow.classList.toggle('hidden', kind.value !== 'weekly'); minRow.classList.toggle('hidden', kind.value !== 'every'); at.step = kind.value === 'hourly' ? '60' : '60'; };
  kind.onchange = sync; sync();
  const body = el('div', { class: 'task-form' }, problem ? el('div', { class: 'skill-err', role: 'alert', text: problem }) : null, el('label', { class: 'task-field' }, el('span', { text: 'Name' }), title), el('label', { class: 'task-field' }, el('span', { text: 'What to do' }), prompt),
    el('label', { class: 'task-field' }, el('span', { text: 'When' }), kind), atRow, dayRow, minRow,
    el('div', { class: 'task-field' }, el('span', { text: 'What it may do (nobody is there to approve)' }), ...modes),
    el('label', { class: 'task-field' }, el('span', { text: 'Model' }), model),
    el('p', { class: 'dlg-note', text: `It works in ${t ? (t.folder || 'its folder') : S.ws.path}, on this server's clock. Runs missed while Studio is off are not made up.` }));
  dialog({ title: t ? 'Edit task' : 'New task', body, small: false, buttons: [{ label: 'Cancel', value: false }, { label: t ? 'Save' : 'Create task', value: true, kind: 'primary' }] }).then(async ok => {
    if (ok !== true) return;
    const schedule = kind.value === 'every' ? { kind: 'every', minutes: +minutes.value } : { kind: kind.value, time: at.value, ...(kind.value === 'weekly' ? { weekday: +day.value } : {}) };
    const typed = { title: title.value, prompt: prompt.value, schedule, mode: $('input[name="task-mode"]:checked', body)?.value || 'look', model: model.value };
    if (!typed.prompt.trim()) { taskForm(t, typed, 'Say what the task should do.'); return; }
    try {
      await POST('/api/tasks/save', { ws: t ? t.ws : S.ws.id, task: { ...(t ? { id: t.id } : {}), ...typed } });
      toast(t ? 'Task saved' : 'Task created', 'success', 2500); showView('tasks', 'force'); tasksRefresh();
    } catch (e) { taskForm(t, typed, e.message || 'Could not save the task'); }
  });
}
function taskDetail(t) {
  const last = t.last;
  const openChat = () => {
    if (!last?.session) return;
    if (!S.ws || S.ws.id !== t.ws) { toast(`Open the folder ${t.folder} to see this chat`, 'info'); return; }
    closeTopOverlay(); openSession(last.session); if (document.documentElement.classList.contains('phone')) showSection('chat');
  };
  const body = el('div', { class: 'task-detail' },
    el('div', { class: 'task-meta', text: `${scheduleText(t.schedule)} · ${TASK_MODES[t.mode][0]} · ${t.model}${t.folder ? ' · ' + t.folder : ''}` }),
    el('div', { class: 'task-meta', text: t.running ? 'Running now' : t.enabled ? `Next run ${whenText(t.next_run)}` : (t.paused_why || 'Paused') }),
    el('details', {}, el('summary', { text: 'What it asks the agent' }), el('pre', { class: 'skill-steps', text: t.prompt })),
    last ? el('div', { class: 'task-last' }, el('div', { class: 'task-group', text: `Last run · ${whenText(last.ended)} · ${DONE_TXT[last.reason] || last.reason}` }),
      last.refused?.length ? el('div', { class: 'task-refused' }, el('b', { text: 'It needed a yes to:' }), el('ul', {}, last.refused.map(r => el('li', { text: r }))), el('div', { class: 'dlg-note', text: 'Nothing of that was done. Switch the task to Do everything, or do it from the chat.' })) : null,
      last.summary ? el('div', { class: 'task-sum', html: mdToHtml(last.summary) }) : null,
      last.file_count ? el('div', { class: 'task-meta', text: `${last.file_count} file${last.file_count === 1 ? '' : 's'} changed: ${last.files.join(', ')}${last.file_count > last.files.length ? ' …' : ''}` }) : null)
      : el('div', { class: 'tl-hint', text: 'It has not run yet.' }));
  const act = (label, action, kind = '') => el('button', { class: 'btn sm ' + kind, onclick: async () => { if (await taskAction(t, action)) closeTopOverlay(); } }, label);
  const foot = el('div', { class: 'plan-acts' },
    last?.session ? el('button', { class: 'btn sm primary', onclick: openChat }, 'Open the chat') : null,
    t.running ? act('Stop', 'stop') : act('Run now', 'run'),
    t.enabled ? act('Pause', 'pause') : act('Resume', 'resume'),
    t.loop ? null : el('button', { class: 'btn sm', onclick: () => { closeTopOverlay(); taskForm(t); } }, 'Edit'),
    act('Delete', 'delete', 'danger'));
  body.append(foot);
  dialog({ title: t.title, body, small: false, buttons: [{ label: 'Close', value: false }] });
}
function closeTopOverlay() { [...document.querySelectorAll('.dialog .dlg-foot .btn')].pop()?.click(); }
function taskEnded(d) {
  const needs = d.refused || TASK_FAILED.has(d.reason);
  toast(needs ? `Task "${d.title}" needs your attention` : `Task "${d.title}" finished`, needs ? 'warn' : 'success', 6000);
  if (document.hidden && 'Notification' in window && Notification.permission === 'granted') new Notification('Eidoloom Studio', { body: `Task "${d.title}" ${needs ? 'needs your attention' : 'finished'}` });
}
/* ------------------------------------------------------------------ editor tabs: previews, diffs, live servers, the agent's browser, pictures, the tab bar */
function updatePreviewButton(t) {
  const b = $('#btn-preview'), usable = t && t.kind === 'file' && canPreview(t.path) && !previewOnly(t);
  b.classList.toggle('hidden', !usable);
  b.querySelector('span').textContent = t && t.preview ? 'Code' : 'Preview';
  b.classList.toggle('primary', !!(t && t.preview));
}
$('#btn-preview').onclick = () => { const t = S.active; if (t) { t.preview = !t.preview; activateTab(t); } };
function refreshPreview() { const t = S.active; if (t && (t.preview || previewOnly(t))) showPreview(t); }
async function activateTab(t) {
  const prev = S.active;
  if (prev && prev !== t && prev.kind === 'file' && prev.model && E.ed) prev.viewState = E.ed.saveViewState();
  S.active = t; renderTabs();
  updatePreviewButton(t);
  if (!t) { showPane('msg'); renderWelcome(); updateStatusPos(); updateCtxLabel(); saveTabs(); return; }
  if (previewOnly(t) || (t.kind === 'file' && t.preview)) {
    showPreview(t);
  } else if (t.kind === 'file' && t.binary) {
    const pm = $('#pane-msg'); pm.className = 'pane welcome'; pm.replaceChildren(el('div', { class: 'w-logo', html: ico('file', 54) }), el('div', { class: 'binary-msg', text: `${basename(t.path)} is a binary or very large file and cannot be shown here.` }));
    showPane('msg');
  } else if (t.kind === 'file') {
    if (t.model && E.ed) {
      showPane('monaco'); E.ed.setModel(t.model); if (t.viewState) E.ed.restoreViewState(t.viewState);
      E.ed.updateOptions({ wordWrap: S.wrap ? 'on' : 'off' }); E.ed.layout(); if (!arguments[1]) E.ed.focus();
    } else { showPane('plain'); const ta = $('#pane-plain'); ta.value = t.text ?? ''; ta.classList.toggle('wrap', S.wrap); }
  } else if (t.kind === 'diff') showDiff(t);
  else if (t.kind === 'live') showLive(t);
  else if (t.kind === 'browser') showBrowser(t);
  else if (t.kind === 'image') showImage(t);
  else if (t.kind === 'comfy') showComfy(t);
  updateStatusPos(); updateCtxLabel(); saveTabs();
}

function showDiff(t) {
  showPane('diff');
  $('#diff-title').textContent = 'Proposed: ' + t.path;
  const c = t.counts || diffCounts(t.original, t.modified);
  $('#diff-counts').replaceChildren(el('span', { class: 'a', text: '+' + c.added }), el('span', { class: 'd', text: '−' + c.removed }));
  const resolved = !!t.resolved;
  $('#diff-accept').disabled = resolved; $('#diff-reject').disabled = resolved;
  const host = $('#diff-host');
  const de = ensureDiffEditor();
  if (de && t.origModel) { host.querySelectorAll('.plain-diff').forEach(n => n.remove()); de.setModel({ original: t.origModel, modified: t.modModel }); de.layout(); }
  else {
    host.replaceChildren(); const wrap = el('pre', { class: 'plain-diff' });
    wrap.style.cssText = 'margin:0;padding:8px 0;height:100%;overflow:auto;font:12.5px/1.5 var(--mono);color:var(--fg-text)';
    const ops = diffOps(t.original, t.modified); let i = 0;
    while (i < ops.length) {
      if (ops[i].t === ' ') {
        let j = i; while (j < ops.length && ops[j].t === ' ') j++;
        const run = ops.slice(i, j), first = i === 0, last = j === ops.length;
        const head = first ? 0 : 3, tail = last ? 0 : 3;
        if (run.length > head + tail + 1) {
          run.slice(0, head).forEach(o => wrap.append(diffLine(o)));
          wrap.append(el('div', { style: 'color:var(--fg-faint);padding:0 14px;background:var(--bg-soft)' }, `⋯ ${run.length - head - tail} unchanged lines`));
          run.slice(run.length - tail).forEach(o => wrap.append(diffLine(o)));
        } else run.forEach(o => wrap.append(diffLine(o)));
        i = j;
      } else wrap.append(diffLine(ops[i++]));
    }
    host.append(wrap);
  }
}
function diffLine(o) {
  const d = el('div', { style: `padding:0 14px;white-space:pre;${o.t === '+' ? 'background:var(--add-bg);' : o.t === '-' ? 'background:var(--del-bg);' : ''}` }, (o.t === ' ' ? '  ' : o.t + ' ') + o.s);
  return d;
}

async function openDiffTab(appr) {
  showSection('editor');
  let t = S.tabs.find(x => x.kind === 'diff' && x.approvalId === appr.approval_id);
  if (!t) {
    t = { id: E.nextId++, kind: 'diff', path: appr.path, approvalId: appr.approval_id, original: appr.old ?? '', modified: appr.new ?? '', counts: appr.counts, resolved: !!appr.resolved };
    if (await ensureEditors()) {
      const lang = langFor(appr.path), U = S.monaco.Uri;
      t.origModel = S.monaco.editor.createModel(t.original, lang, U.from({ scheme: 'studio-orig', path: '/' + t.id + '/' + appr.path }));
      t.modModel = S.monaco.editor.createModel(t.modified, lang, U.from({ scheme: 'studio-new', path: '/' + t.id + '/' + appr.path }));
    }
    S.tabs.push(t);
  }
  await activateTab(t);
}
/* ---- live servers: pages of servers this folder runs, relayed by Studio at /live/<token>/, sandboxed like previews */
async function openLiveTab(port, path = '/') {
  showSection('editor');
  let t = S.tabs.find(x => x.kind === 'live' && x.port === port);
  try {
    const r = await POST('/api/live/open', { ws: S.ws.id, port });
    if (!t) { t = { id: E.nextId++, kind: 'live', port, path: `live:${port}` }; S.tabs.push(t); }
    t.url = r.url; t.at = path.startsWith('/') ? path : '/' + path; t.until = Date.now() + (r.expires_in - 60) * 1000;
  } catch (e) { errToast(e, 'Live server'); return; }
  await activateTab(t);
}
function showLive(t) {
  const pane = $('#pane-preview');
  showPane('preview');
  if (Date.now() > (t.until || 0)) { openLiveTab(t.port, t.at); return; }   // the address lasts an hour
  const frame = el('iframe', { class: 'preview-frame', src: t.url + t.at.slice(1), sandbox: 'allow-scripts allow-forms allow-modals allow-popups', title: `Server on port ${t.port}` });
  const addr = el('input', { class: 'live-addr', value: t.at, spellcheck: 'false', 'aria-label': 'Page path',
    onkeydown: e => { if (e.key === 'Enter') { t.at = addr.value.startsWith('/') ? addr.value : '/' + addr.value; frame.src = t.url + t.at.slice(1); } } });
  const reload = el('button', { class: 'icon-btn sm', title: 'Reload', 'aria-label': 'Reload', html: ico('refresh', 14), onclick: () => { frame.src = t.url + t.at.slice(1); } });
  const away = el('button', { class: 'icon-btn sm', title: 'Open in a new window', 'aria-label': 'Open in a new window', html: ico('detach', 14), onclick: () => window.open(t.url + t.at.slice(1), '_blank', 'noopener') });
  pane.replaceChildren(el('div', { class: 'preview-bar live-bar' }, el('span', { class: 'live-port', html: ico('globe', 13) + ` localhost:${t.port}` }), addr, reload, away), frame);
}
/* ---- the agent's web browser (tools/browser_mcp.py), watched live; Take over gives the user the mouse and keyboard,
   and the agent's browser calls wait until Hand back. Frames come by long polling (/api/browser/frame), so tunnels work. */
function openBrowserTab(quiet = false) {
  if (!quiet) showSection('editor');
  let t = S.tabs.find(x => x.kind === 'browser');
  if (!t) { t = { id: E.nextId++, kind: 'browser', path: 'browser:agent' }; S.tabs.push(t); }
  if (quiet && S.active && S.active.kind === 'file' && S.active.dirty) { renderTabs(); return; }    // never pull you away from unsaved typing
  activateTab(t);
}
const BROWSER_KEYS = new Set(['Enter', 'Backspace', 'Tab', 'Escape', 'Delete', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown', 'F5']);
// "https://el.wikipedia.org/wiki/Θεσσαλονίκη" rather than %CE%98…, as browsers show it.
function readableUrl(url) { try { return decodeURI(url); } catch { return url; } }
function showBrowser(t) {
  const pane = $('#pane-preview');
  showPane('preview');
  t.closed = false;
  const img = el('img', { class: 'bv-img', alt: "The agent's browser", draggable: 'false' });
  // Typing goes through a hidden text box, so accents (ά), other keyboards and pasted text arrive whole; special keys go one by one.
  const kb = el('textarea', { class: 'bv-kb', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false', 'aria-label': 'Type into the browser' });
  const shot = el('div', { class: 'bv-shot' }, img, kb);
  const veil = el('div', { class: 'bv-veil' });
  const addr = el('input', { class: 'live-addr bv-addr', spellcheck: 'false', 'aria-label': 'Web address', placeholder: 'Web address' });
  const btn = (icon, title, ev) => el('button', { class: 'icon-btn sm', title, 'aria-label': title, html: ico(icon, 14), onclick: () => send([ev]) });
  const take = el('button', { class: 'btn sm primary bv-take', text: 'Take over' });
  // Phones: tapping the page does not open the keyboard (most taps are clicks), this button does.
  const keys = el('button', { class: 'icon-btn sm bv-keys hidden', title: 'Keyboard: type into the page', 'aria-label': 'Keyboard', html: ico('keyboard', 15),
    onmousedown: e => e.preventDefault(), onclick: () => kb.focus() });
  const state = el('span', { class: 'bv-state' });
  // Desktop or Phone: how the agent's browser shows pages while you watch (Desktop fills this tab at 1280 wide).
  let bvPhone = false;
  try { bvPhone = localStorage.getItem('studio.browserView') === 'phone'; } catch {}
  const viewBtn = el('button', { class: 'icon-btn sm bv-view', onclick: () => {
    bvPhone = !bvPhone;
    try { localStorage.setItem('studio.browserView', bvPhone ? 'phone' : 'desktop'); } catch {}
    viewLook(); GET('/api/browser/frame', { after: seq, now: 1, ...shapeArgs() }).then(paint).catch(() => {});
  } });
  const viewLook = () => {
    viewBtn.innerHTML = ico(bvPhone ? 'mobile' : 'pc', 15);
    const tip = bvPhone ? 'Showing pages as a phone (390 wide, touch). Click for the desktop view' : 'Showing pages as a desktop. Click for the phone view';
    viewBtn.title = tip; viewBtn.setAttribute('aria-label', tip); viewBtn.classList.toggle('on', bvPhone);
  };
  viewLook();
  const tabsSel = el('select', { class: 'dlg-input bv-tabs hidden', 'aria-label': 'Browser tabs', onchange: () => send([{ type: 'tab', index: +tabsSel.value }]) });
  const bar = el('div', { class: 'preview-bar live-bar bv-bar' }, el('span', { class: 'live-port bv-label', html: ico('globe', 13) + ' Browser' }),
    btn('undo', 'Back', { type: 'back' }), btn('refresh', 'Reload', { type: 'reload' }), addr, tabsSel, viewBtn, state, keys, take,
    el('button', { class: 'btn sm bv-ask', title: 'Ask the agent about the page shown here: it reads this page', onclick: () => {
      const inp = $('#agent-input'), lead = 'About the page in the browser: ';
      if (!inp.value.startsWith(lead)) inp.value = lead + inp.value;
      showSection('chat'); autosize(); inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length);
    } }, el('span', { class: 'bv-long', text: 'Ask about this page' }), el('span', { class: 'bv-short', text: 'Ask' })));
  const stage = el('div', { class: 'bv-stage' }, shot, veil);
  pane.replaceChildren(el('div', { class: 'bv' }, bar, stage));
  // On a phone the page already fits the screen when you take over; elsewhere the tab asks for its view and shape.
  const shapeArgs = () => {
    if (document.documentElement.classList.contains('phone')) return {};
    if (bvPhone) return { view: 'phone' };
    const r = stage.getBoundingClientRect();
    return r.width > 100 && r.height > 100 ? { fill: Math.round(1280 * (r.height - 20) / (r.width - 20)) } : {};
  };
  let seq = 0, size = { width: 1280, height: 800 }, alive = true;
  // A swipe moves the picture at once by what has not reached the screen yet (where the page should be, against the
  // scroll position of the latest frame); frames catch up and the shift shrinks to nothing.
  let scrollY = 0, aim = null, settle = null;
  const slide = () => {
    if (aim === null) { img.style.transform = ''; return; }
    const ahead = aim - scrollY, k = img.getBoundingClientRect().width / size.width || 1;
    img.style.transform = Math.abs(ahead) < 1 ? '' : `translateY(${-ahead * k}px)`;
    if (!touch) { clearTimeout(settle); settle = setTimeout(() => { aim = null; slide(); }, Math.abs(ahead) < 1 ? 0 : 700); }
  };
  // One request at a time: what comes in meanwhile waits and goes together, wheel turns added up, so a swipe over a slow
  // link (a tunnel, a phone network) is a few requests, not one per finger movement.
  const outbox = [];
  let sending = false;
  const send = async events => {
    for (const e of events) {
      const last = outbox[outbox.length - 1];
      if (e.type === 'mouse' && e.action === 'wheel' && last?.type === 'mouse' && last.action === 'wheel') {
        last.dx = (last.dx || 0) + (e.dx || 0); last.dy = (last.dy || 0) + (e.dy || 0); last.x = e.x; last.y = e.y;
      } else outbox.push({ ...e });
    }
    if (sending) return;
    sending = true;
    while (outbox.length) {
      const batch = outbox.splice(0, 50);
      try { await POST('/api/browser/input', { events: batch }); }
      catch (e) { if (e.status === 409 && /take over/.test(e.message)) toast('Press Take over first: then you can click and type in the browser', 'warn', 3500); else errToast(e, 'Browser'); }
    }
    sending = false;
  };
  const paint = d => {
    t.held = !!d.held;
    take.textContent = d.held ? 'Hand back' : 'Take over';
    take.classList.toggle('primary', !d.held);
    take.title = d.held ? 'Give the browser back to the agent; its waiting browser calls carry on' : 'Use the mouse and keyboard yourself (to sign in, accept a page, solve a check); the agent waits';
    shot.classList.toggle('held', !!d.held);
    if (d.phone !== undefined) shot.classList.toggle('as-phone', !!d.phone && !document.documentElement.classList.contains('phone'));
    keys.classList.toggle('hidden', !d.held || !document.documentElement.classList.contains('phone'));
    state.textContent = d.held ? 'You have it' : d.agent ? 'Agent working…' : 'Agent idle';
    state.className = 'bv-state ' + (d.held ? 'you' : d.agent ? 'busy' : '');
    if (document.activeElement !== addr && d.url !== undefined) addr.value = d.url && d.url !== 'about:blank' ? readableUrl(d.url) : '';
    const pages = d.pages || [];
    tabsSel.classList.toggle('hidden', pages.length < 2);
    if (pages.length > 1) tabsSel.replaceChildren(...pages.map((p, i) => el('option', { value: String(i), text: clip(p.title || p.url, 40), selected: p.on })));
    const blank = d.url !== undefined && (!d.url || d.url === 'about:blank');
    shot.classList.toggle('blank', blank && !d.held);
    veil.textContent = d.error && !d.data && !seq ? d.error
      : blank && !d.held ? 'No page is open. Ask the agent to open one, or press Take over and type an address above.' : '';
    if (d.data) {
      img.src = 'data:image/jpeg;base64,' + d.data; size = { width: d.width || size.width, height: d.height || size.height };
      if (typeof d.scrollY === 'number') { scrollY = d.scrollY; slide(); }
    }
  };
  take.onclick = async () => {
    // On a phone the page takes the phone's width while you hold it, so it can be read and used; handing back restores the agent's.
    const r = stage.getBoundingClientRect(), phone = document.documentElement.classList.contains('phone');
    const body = { hold: !t.held, ...(phone && !t.held ? { size: { width: Math.round(r.width - 20), height: Math.round(r.height - 20) } } : {}) };
    try { paint(await POST('/api/browser/hold', body)); if (t.held && !phone) kb.focus(); } catch (e) { errToast(e, 'Browser'); }
  };
  (async () => {
    while (alive && !t.closed && S.active === t && pane.contains(img)) {
      try { const d = await GET('/api/browser/frame', { after: seq, ...shapeArgs() }); if (d.seq > seq || d.data) seq = d.seq; paint(d); }
      catch (e) { veil.textContent = e.message; await sleep(3000); }
    }
  })();
  const at = e => { const r = img.getBoundingClientRect(); return { x: Math.round((e.clientX - r.left) / r.width * size.width), y: Math.round((e.clientY - r.top) / r.height * size.height) }; };
  const buttonOf = e => ['left', 'middle', 'right'][e.button] || 'left';
  img.addEventListener('mousedown', e => { if (!t.held) return; e.preventDefault(); if (Date.now() - touchAt > 800) kb.focus(); send([{ type: 'mouse', action: 'down', button: buttonOf(e), ...at(e) }]); });
  img.addEventListener('mouseup', e => { if (!t.held) return; send([{ type: 'mouse', action: 'up', button: buttonOf(e), ...at(e) }]); });
  img.addEventListener('contextmenu', e => { if (t.held) e.preventDefault(); });
  let wheel = 0, wheelAt = null;
  shot.addEventListener('wheel', e => {
    if (!t.held) return; e.preventDefault(); wheel += e.deltaY; wheelAt = at(e);
    if (!shot._wt) shot._wt = setTimeout(() => { send([{ type: 'mouse', action: 'wheel', dy: wheel, ...wheelAt }]); wheel = 0; shot._wt = null; }, 80);
  }, { passive: false });
  // Touch: a tap clicks (the browser makes it a mouse click), a swipe scrolls the page while you hold it.
  let touch = null, touchAt = 0;
  img.addEventListener('touchstart', e => {
    touchAt = Date.now();
    if (t.held && e.touches.length === 1) { touch = { y: e.touches[0].clientY, x: e.touches[0].clientX, moved: false }; clearTimeout(settle); if (aim === null) aim = scrollY; }
  }, { passive: true });
  img.addEventListener('touchmove', e => {
    if (!t.held || !touch || e.touches.length !== 1) return;
    const p = e.touches[0], dy = touch.y - p.clientY, dx = touch.x - p.clientX;
    if (!touch.moved && Math.hypot(dx, dy) < 8) return;
    e.preventDefault(); touch.moved = true;
    const scale = size.width / img.getBoundingClientRect().width;
    touch.y = p.clientY; touch.x = p.clientX;
    aim = Math.max(0, aim + dy * scale); slide();
    send([{ type: 'mouse', action: 'wheel', dx: Math.round(dx * scale), dy: Math.round(dy * scale), ...at(p) }]);
  }, { passive: false });
  img.addEventListener('touchend', () => { touchAt = Date.now(); const moved = touch?.moved; touch = null; if (moved) slide(); else { aim = null; slide(); } });
  kb.addEventListener('keydown', e => {
    if (!t.held || e.isComposing) return;
    const mod = (e.ctrlKey ? 'Control+' : '') + (e.metaKey ? 'Meta+' : '') + (e.altKey ? 'Alt+' : '');
    if (mod && /^[vV]$/.test(e.key)) return;                       // paste arrives as text
    if (BROWSER_KEYS.has(e.key) || (mod && e.key.length === 1)) { e.preventDefault(); send([{ type: 'key', key: mod + (e.key.length === 1 ? e.key.toLowerCase() : e.key) }]); }
  });
  const flush = () => { if (kb.value) { const text = kb.value; kb.value = ''; if (t.held) send([{ type: 'text', text }]); } };
  kb.addEventListener('input', e => { if (!e.isComposing) flush(); });
  kb.addEventListener('compositionend', flush);
  addr.addEventListener('keydown', e => { if (e.key === 'Enter' && addr.value.trim()) { send([{ type: 'go', url: addr.value.trim() }]); addr.blur(); } });
  t.stop = () => { alive = false; };
}
async function liveServersMenu(anchor) {
  if (!S.ws) return;
  let r;
  try { r = await GET('/api/live/ports', { ws: S.ws.id }); } catch (e) { errToast(e, 'Live servers'); return; }
  const box = anchor.getBoundingClientRect();
  const items = r.ports.map(port => ({ label: `Open localhost:${port}`, run: () => openLiveTab(port) }));
  if (!items.length) items.push({ label: r.sandboxed ? 'No server is running in this sandbox yet' : 'No program started in this folder is listening', run: () => {} });
  if (isAdmin()) items.push({ label: "The agent's web browser", run: () => openBrowserTab() });
  showContextMenu(box.left, box.bottom + 4, items);
}
$('#ex-live').onclick = e => liveServersMenu(e.currentTarget);
function pageViewCard(d) {
  const where = `localhost:${d.port}${d.path || '/'}`;
  const head = el('div', { class: 'page-head' }, el('span', { html: ico('globe', 14) }), el('span', { class: 'page-title', text: d.title || where }),
    el('span', { class: 'page-status' + (d.status >= 400 || !d.status ? ' bad' : ''), text: d.status ? `HTTP ${d.status}` : 'no answer' }));
  const meta = el('div', { class: 'page-meta' }, el('code', { text: where }), d.errors ? el('span', { class: 'page-errors', text: `${d.errors} script error${d.errors === 1 ? '' : 's'}` }) : null,
    d.failed ? el('span', { text: `${d.failed} failed request${d.failed === 1 ? '' : 's'}`, title: 'Answers of 400 and above. An app can send these on purpose, such as for a duplicate.' }) : null,
    d.width && d.width !== 1280 ? el('span', { class: 'page-width', text: d.width < 700 ? `phone, ${d.width} px` : `${d.width} px wide` }) : null,
    d.rendered === false ? el('span', { text: 'HTML only, scripts not run' }) : null);
  const open = el('button', { class: 'link-btn card-link', text: 'Open in Live tab', onclick: () => openLiveTab(d.port, d.path || '/') });
  const shot = d.shot ? el('img', { class: 'page-shot', src: `/api/live/shot?id=${encodeURIComponent(d.shot)}`, alt: `What the model saw at ${where}`, 'data-name': `page-${d.port}.png`, loading: 'lazy' }) : null;
  return el('div', { class: 'm-page' }, head, meta, shot, el('div', { class: 'page-acts' }, open));
}

/* ---- pictures from the chat open in a tab with zoom, pan, download and copy */
function openImageTab(src, name) {
  showSection('editor');
  let t = S.tabs.find(x => x.kind === 'image' && x.src === src);
  if (!t) { t = { id: E.nextId++, kind: 'image', src, name: name || 'picture.png', path: 'image:' + src }; S.tabs.push(t); }
  activateTab(t);
}
document.addEventListener('click', e => {
  const img = e.target.closest('#msgs .page-shot, #msgs .user-pics img');
  if (img) openImageTab(img.getAttribute('src'), img.dataset.name || img.alt);
});
function showImage(t) {
  const pane = $('#pane-preview'); showPane('preview');
  pane.replaceChildren(...imageViewer(t, t.src, t.name, t.name));
  const stage = pane.querySelector('.iv-stage'); stage._place(); stage.focus();
}
// A picture to zoom (wheel, pinch, buttons, + and -), pan (drag) and copy or download. The view (scale, place) is kept on t.
// extra goes in the bar before the zoom buttons. Returns [bar, stage].
function imageViewer(t, src, name, label, extra = []) {
  const v = t.view || (t.view = { scale: 1, x: 0, y: 0, fit: true });
  const img = el('img', { class: 'iv-img', src, alt: name, draggable: 'false' });
  const stage = el('div', { class: 'iv-stage', tabindex: '0', 'aria-label': 'Picture: scroll to zoom, drag to move' }, img);
  const pct = el('span', { class: 'iv-pct' });
  const apply = () => { img.style.transform = `translate(${v.x}px, ${v.y}px) scale(${v.scale})`; pct.textContent = Math.round(v.scale * 100) + '%'; };
  const fit = () => {
    const r = stage.getBoundingClientRect(), w = img.naturalWidth || 1, h = img.naturalHeight || 1;
    v.scale = Math.min(1, (r.width - 24) / w, (r.height - 24) / h); v.x = (r.width - w * v.scale) / 2; v.y = (r.height - h * v.scale) / 2; v.fit = true; apply();
  };
  // Zooms by factor about a point of the stage, so what is under the cursor stays there.
  const zoom = (factor, cx, cy) => {
    const r = stage.getBoundingClientRect(); cx = cx ?? r.width / 2; cy = cy ?? r.height / 2;
    const next = Math.min(32, Math.max(0.05, v.scale * factor));
    v.x = cx - (cx - v.x) * next / v.scale; v.y = cy - (cy - v.y) * next / v.scale; v.scale = next; v.fit = false; apply();
  };
  const actual = () => { const r = stage.getBoundingClientRect(); zoom(1 / v.scale, r.width / 2, r.height / 2); };
  img.onload = () => stage._place();
  stage.addEventListener('wheel', e => { e.preventDefault(); const r = stage.getBoundingClientRect(); zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15, e.clientX - r.left, e.clientY - r.top); }, { passive: false });
  // One finger or the mouse drags; two fingers pinch to zoom about their middle and move the picture with it.
  const pts = new Map();
  let drag = null, pinch = null;
  const two = () => { const [a, b] = [...pts.values()], r = stage.getBoundingClientRect();
    return { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, cx: (a.x + b.x) / 2 - r.left, cy: (a.y + b.y) / 2 - r.top }; };
  stage.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    try { stage.setPointerCapture(e.pointerId); } catch { /* a pointer the browser no longer knows */ }
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pts.size === 2) { drag = null; pinch = two(); stage.classList.remove('dragging'); }
    else if (pts.size === 1) { drag = { x: e.clientX, y: e.clientY, vx: v.x, vy: v.y }; stage.classList.add('dragging'); }
  });
  stage.addEventListener('pointermove', e => {
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch && pts.size >= 2) {
      const now = two();
      v.x += now.cx - pinch.cx; v.y += now.cy - pinch.cy;
      zoom(now.d / pinch.d, now.cx, now.cy); pinch = now;
    } else if (drag) { v.x = drag.vx + e.clientX - drag.x; v.y = drag.vy + e.clientY - drag.y; v.fit = false; apply(); }
  });
  const lift = e => {
    pts.delete(e.pointerId);
    if (pts.size < 2) pinch = null;
    if (pts.size === 1) { const p = [...pts.values()][0]; drag = { x: p.x, y: p.y, vx: v.x, vy: v.y }; }   // the finger left keeps dragging
    if (!pts.size) { drag = null; stage.classList.remove('dragging'); }
  };
  stage.addEventListener('pointerup', lift); stage.addEventListener('pointercancel', lift);
  stage.addEventListener('dblclick', () => (v.fit ? actual() : fit()));
  stage.addEventListener('keydown', e => { if (e.key === '+' || e.key === '=') zoom(1.25); else if (e.key === '-') zoom(0.8); else if (e.key === '0') fit(); else if (e.key === '1') actual(); });
  const btn = (icon, label, run, text) => el('button', { class: 'icon-btn sm' + (text ? ' iv-text' : ''), title: label, 'aria-label': label, onclick: run, ...(text ? { text } : { html: ico(icon, 14) }) });
  const copy = async () => {
    try {
      let blob = await (await fetch(src)).blob();
      if (blob.type !== 'image/png') {      // clipboards take PNG everywhere
        const bmp = await createImageBitmap(blob), c = document.createElement('canvas'); c.width = bmp.width; c.height = bmp.height;
        c.getContext('2d').drawImage(bmp, 0, 0); blob = await new Promise(ok => c.toBlob(ok, 'image/png'));
      }
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
      toast('Picture copied', 'success', 2000);
    } catch (e) { toast('Could not copy the picture here: ' + (e.message || e), 'warn'); }
  };
  const download = () => { const a = el('a', { href: src, download: name }); document.body.append(a); a.click(); a.remove(); };
  const bar = el('div', { class: 'preview-bar iv-bar' }, el('span', { class: 'preview-path', text: label }), ...extra,
    btn('minus', 'Zoom out (−)', () => zoom(0.8)), pct, btn('plus', 'Zoom in (+)', () => zoom(1.25)),
    btn(null, 'Fit to the window (0)', fit, 'Fit'), btn(null, 'Actual size (1)', actual, '1:1'),
    btn('copy', 'Copy the picture', copy), btn('download', 'Download the picture', download));
  // The stage has a size only once it is on the page (and again after its tab was hidden): fit or place the picture then.
  stage._place = () => { if (!img.naturalWidth || !stage.clientWidth) return; if (v.fit) fit(); else apply(); };
  new ResizeObserver((_, ro) => { if (!stage.isConnected) return ro.disconnect(); stage._place(); }).observe(stage);
  return [bar, stage];
}

function diffTabFor(approvalId) { return S.tabs.find(x => x.kind === 'diff' && x.approvalId === approvalId); }

function tabLabel(t) { return t.kind === 'diff' ? 'Proposed: ' + basename(t.path) : t.kind === 'browser' ? 'Browser' : t.kind === 'live' ? `Live :${t.port}` : t.kind === 'image' || t.kind === 'comfy' ? t.name : basename(t.path); }
function renderTabs() {
  const box = $('#tabs'); box.replaceChildren();
  const counts = {}; for (const t of S.tabs) counts[tabLabel(t)] = (counts[tabLabel(t)] || 0) + 1;
  for (const t of S.tabs) {
    refreshDirtyQuiet(t);
    const lab = el('span', { class: 't-label' }, tabLabel(t));
    if (counts[tabLabel(t)] > 1 && dirname(t.path)) lab.append(el('small', { text: dirname(t.path) }));
    const close = el('span', { class: 't-close', title: 'Close', html: ico('x', 14) + '<i class="dot"></i>', onclick: e => { e.stopPropagation(); closeTab(t); } });
    const n = el('div', {
      class: 'tab' + (t === S.active ? ' active' : '') + (t.dirty ? ' dirty' : '') + (t.kind === 'diff' ? ' diff' : ''), role: 'tab', title: t.kind === 'diff' ? 'Proposed change: ' + t.path : t.kind === 'live' ? `Server on port ${t.port}, inside Studio` : t.path,
      onclick: () => { if (n._dragged) return; if (S.active !== t) activateTab(t); else if (t.model && E.ed) E.ed.focus(); },
      onauxclick: e => { if (e.button === 1) { e.preventDefault(); closeTab(t); } },
    }, el('span', { class: 't-ico', html: t.kind === 'diff' ? ico('diff', 14, 'style="color:#e2b04a"') : t.kind === 'live' ? ico('globe', 14, 'style="color:var(--plan)"') : t.kind === 'browser' ? ico('globe', 14, 'style="color:var(--srv-browser)"') : t.kind === 'image' ? ico('image', 14, 'style="color:var(--plan)"') : t.kind === 'comfy' ? ico('layers', 14, 'style="color:var(--plan)"') : fileIco(t.path, 14) }), lab, close);
    t.tabEl = n; box.append(n); tabDrag(n, t);
    if (t === S.active) setTimeout(() => n.scrollIntoView({ block: 'nearest', inline: 'nearest' }), 0);
  }
  renderWelcome();
  tabScrollUpdate();
}
/* ---- the tab bar: a thin scrollbar of its own when the tabs do not fit (drag it, or click along it), the mouse wheel
   scrolls sideways, and a tab dragged along the bar moves to where it is dropped (on touch screens after a long press,
   so a swipe still scrolls). The order is kept with the open tabs. */
function tabScrollUpdate() { (tabScrollUpdate.api ||= tabScrollMake()).update(); }   // made on first use, whenever the tabs first render
function tabScrollMake() {
  const box = $('#tabs'), bar = el('div', { class: 'tabs-scroll hidden', 'aria-hidden': 'true' }), thumb = el('div', { class: 'tabs-thumb' });
  bar.append(thumb); $('#tabbar').append(bar);
  const update = () => {
    const over = box.scrollWidth - box.clientWidth;
    bar.classList.toggle('hidden', over <= 1);
    if (over <= 1) return;
    bar.style.left = box.offsetLeft + 'px'; bar.style.width = box.clientWidth + 'px';
    const w = Math.max(24, box.clientWidth * box.clientWidth / box.scrollWidth);
    thumb.style.width = w + 'px'; thumb.style.transform = `translateX(${(box.clientWidth - w) * box.scrollLeft / over}px)`;
  };
  box.addEventListener('scroll', update, { passive: true });
  new ResizeObserver(update).observe(box);
  box.addEventListener('wheel', e => {
    if (e.ctrlKey || box.scrollWidth <= box.clientWidth) return;
    const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    if (!d) return;
    e.preventDefault(); box.scrollLeft += e.deltaMode === 1 ? d * 30 : d;
  }, { passive: false });
  // dragging the thumb, or pressing the track to jump there and drag on
  bar.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    e.preventDefault();
    const over = box.scrollWidth - box.clientWidth, w = thumb.offsetWidth, r = bar.getBoundingClientRect();
    const toScroll = x => over * Math.min(1, Math.max(0, (x - r.left - grab) / (r.width - w)));
    const tr = thumb.getBoundingClientRect();
    const grab = e.target === thumb ? e.clientX - tr.left : w / 2;
    if (e.target !== thumb) box.scrollLeft = toScroll(e.clientX);
    try { bar.setPointerCapture(e.pointerId); } catch { /* gone */ }
    bar.classList.add('held');
    const move = ev => { box.scrollLeft = toScroll(ev.clientX); };
    const up = () => { bar.classList.remove('held'); bar.removeEventListener('pointermove', move); bar.removeEventListener('pointerup', up); bar.removeEventListener('pointercancel', up); };
    bar.addEventListener('pointermove', move); bar.addEventListener('pointerup', up); bar.addEventListener('pointercancel', up);
  });
  return { update };
}
function tabDrag(n, t) {
  n.addEventListener('pointerdown', e => {
    if (e.button !== 0 || e.target.closest('.t-close')) return;
    const box = $('#tabs'), touch = e.pointerType !== 'mouse', start = { x: e.clientX, y: e.clientY };
    let dragging = false, timer = null, mark = null, to = -1, lastX = e.clientX, roll = 0;
    const begin = () => {
      dragging = true; n.classList.add('dragging'); document.body.classList.add('tab-dragging');
      try { n.setPointerCapture(e.pointerId); } catch { /* gone */ }
      mark = el('div', { class: 'tab-drop' }); box.append(mark);
      if (touch && navigator.vibrate) navigator.vibrate(10);
      place(lastX);
    };
    // where the tab would go among the others (it moves with the pointer, so it is left out): before the first whose
    // middle is right of the pointer
    const place = x => {
      const tabs = [...box.querySelectorAll('.tab')].filter(o => o !== n);
      if (!tabs.length) return;
      to = tabs.findIndex(o => { const r = o.getBoundingClientRect(); return x < r.left + r.width / 2; });
      if (to < 0) to = tabs.length;
      const ref = tabs[to] || tabs[tabs.length - 1], br = box.getBoundingClientRect(), rr = ref.getBoundingClientRect();
      mark.style.left = ((tabs[to] ? rr.left : rr.right) - br.left + box.scrollLeft - 1) + 'px';
      n.style.transform = `translateX(${x - start.x + (box.scrollLeft - start.scroll)}px)`;
    };
    start.scroll = box.scrollLeft;
    // near either end the bar scrolls by itself while the tab is held there
    const edge = () => {
      if (!dragging) return;
      const r = box.getBoundingClientRect(), zone = 28;
      const v = lastX < r.left + zone ? -8 : lastX > r.right - zone ? 8 : 0;
      if (v) { box.scrollLeft += v; place(lastX); }
      roll = requestAnimationFrame(edge);
    };
    const move = ev => {
      lastX = ev.clientX;
      if (!dragging) {
        const far = Math.hypot(ev.clientX - start.x, ev.clientY - start.y);
        if (touch) { if (far > 8) stop(); return; }      // a swipe: let the bar scroll
        if (far < 6) return;
        begin(); roll = requestAnimationFrame(edge);
      }
      place(ev.clientX);
    };
    const blockScroll = ev => { if (dragging && ev.cancelable) ev.preventDefault(); };
    const stop = () => {
      clearTimeout(timer); cancelAnimationFrame(roll);
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', drop); window.removeEventListener('pointercancel', stop);
      window.removeEventListener('touchmove', blockScroll);
      n.classList.remove('dragging'); n.style.transform = ''; document.body.classList.remove('tab-dragging'); mark?.remove();
    };
    const drop = () => {
      const was = dragging; stop();
      if (!was) return;
      n._dragged = true; setTimeout(() => { n._dragged = false; }, 0);    // not a click as well
      const from = S.tabs.indexOf(t);
      if (from < 0 || to < 0 || to === from) return;
      S.tabs.splice(from, 1); S.tabs.splice(to, 0, t);
      const left = box.scrollLeft; renderTabs(); box.scrollLeft = left; saveTabs();
    };
    if (touch) timer = setTimeout(() => { begin(); roll = requestAnimationFrame(edge); }, 400);
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', drop); window.addEventListener('pointercancel', stop);
    window.addEventListener('touchmove', blockScroll, { passive: false });
  });
  n.addEventListener('contextmenu', e => { if (n.classList.contains('dragging')) e.preventDefault(); });
}

function refreshDirtyQuiet(t) { t.dirty = isDirty(t); }

async function closeTab(t, { force = false } = {}) {
  if (!force && t.dirty) {
    const r = await dialog({ title: 'Unsaved changes', body: `Do you want to save the changes to ${basename(t.path)}?`, buttons: [{ label: 'Cancel', value: 'cancel' }, { label: "Don't save", value: 'no' }, { label: 'Save', value: 'save', kind: 'primary' }] });
    if (r === 'save') { await saveTab(t); if (isDirty(t)) return false; } else if (r !== 'no') return false;
  }
  const i = S.tabs.indexOf(t); if (i < 0) return true;
  S.tabs.splice(i, 1);
  if (t.origModel && E.diffEd) { try { E.diffEd.setModel(null); } catch { /* ignore */ } }
  if (t.kind === 'live' || t.kind === 'image' || t.kind === 'comfy' || t.kind === 'browser') $('#pane-preview').replaceChildren();      // the page stops running with its tab
  if (t.kind === 'browser') { t.closed = true; if (t.held) POST('/api/browser/hold', { hold: false }).catch(() => {}); }
  if (t.kind === 'comfy') clearTimeout(t.timer);
  if (t.tl) clearTimeout(t.tl.timer);
  if (t.ae) { aeStop(t, true); clearTimeout(t.ae.timer); }
  for (const m of [t.model, t.origModel, t.modModel]) if (m) { if (E.ed && E.ed.getModel() === m) E.ed.setModel(null); m.dispose(); }
  if (S.active === t) { const nxt = S.tabs[Math.min(i, S.tabs.length - 1)] || null; S.active = null; await activateTab(nxt, true); } else { renderTabs(); saveTabs(); }
  return true;
}
async function closeAllTabs() {
  const dirty = S.tabs.filter(t => t.dirty);
  if (dirty.length) {
    const ok = await confirmBox('Unsaved changes', `${dirty.length} file${dirty.length > 1 ? 's have' : ' has'} unsaved changes. Discard them?`, 'Discard');
    if (!ok) return false;
  }
  for (const t of [...S.tabs]) await closeTab(t, { force: true });
  return true;
}
function tabsRenamed(from, to) {
  for (const t of S.tabs) if (t.path === from || t.path.startsWith(from + '/')) {
    t.path = to + t.path.slice(from.length);
    if (t.model && S.monaco) { /* model URI stays; only label changes */ }
  }
  renderTabs(); saveTabs();
}
function tabsDeleted(path) {
  for (const t of [...S.tabs]) if (t.kind === 'file' && (t.path === path || t.path.startsWith(path + '/'))) closeTab(t, { force: true });
}

async function saveTab(t, { force = false } = {}) {
  if (t?.ae) return aeSave(t);
  if (!t || t.kind !== 'file' || t.binary || !S.ws) return;
  const content = tabValue(t), ver = t.model ? t.model.getAlternativeVersionId() : null;
  try {
    const r = await POST('/api/files/write', { ws: S.ws.id, path: t.path, content, expected_mtime: force ? undefined : t.mtime });
    t.mtime = r.mtime; if (t.model) t.savedVer = ver; else t.saved = content;
    refreshDirty(t); flashStatus('Saved ' + basename(t.path));
  } catch (e) {
    if (e.status === 409) {
      const r = await dialog({ title: 'File changed on disk', body: `"${t.path}" was modified on disk since you opened it. Reload discards your edits; Overwrite replaces the disk version with yours.`,
        buttons: [{ label: 'Cancel', value: 'c' }, { label: 'Reload', value: 'reload' }, { label: 'Overwrite', value: 'over', kind: 'primary' }] });
      if (r === 'reload') await reloadTab(t, true); else if (r === 'over') await saveTab(t, { force: true });
    } else errToast(e, 'Save failed');
  }
}
async function reloadTab(t, announce) {
  if (!t || t.kind !== 'file') return;
  let d; try { d = await GET('/api/files/read', { ws: S.ws.id, path: t.path }); } catch (e) { if (e.status === 404) { return; } errToast(e, 'Reload failed'); return; }
  if (d.binary) return;
  t.mtime = d.mtime;
  if (t.model) {
    if (t.model.getValue() !== d.content) { const vs = S.active === t && E.ed ? E.ed.saveViewState() : null; t.model.setValue(d.content); if (vs) E.ed.restoreViewState(vs); }
    t.savedVer = t.model.getAlternativeVersionId();
  } else { t.text = t.saved = d.content; if (S.active === t) $('#pane-plain').value = d.content; }
  refreshDirty(t); if (announce) flashStatus('Reloaded ' + basename(t.path));
}
/** Called when the agent changed files on disk. */
function onDiskChanged(path) {
  quick.cache = null;
  const t = S.tabs.find(x => x.kind === 'file' && x.path === path);
  if (t) { if (!t.dirty) reloadTab(t); else toast(`${basename(path)} was changed elsewhere but you have unsaved edits. Saving will ask how to resolve it.`, 'warn', 7000); }
  refreshTreeSoon(); refreshPreview();
}
let flashT;
function flashStatus(msg) { const n = $('#sb-notice'); if (S.noticeShown) return; n.textContent = msg; n.classList.remove('hidden'); clearTimeout(flashT); flashT = setTimeout(() => { n.classList.add('hidden'); }, 1800); }

const saveTabs = debounce(() => {
  if (!S.ws) return;
  lsSet('studio.tabs.' + S.ws.id, JSON.stringify({ paths: S.tabs.filter(t => t.kind === 'file').map(t => t.path), active: S.active && S.active.kind === 'file' ? S.active.path : null }));
}, 300);
async function restoreTabs() {
  if (!S.ws) return;
  const st = lsJson('studio.tabs.' + S.ws.id, null); if (!st || !st.paths) return;
  for (const p of st.paths.slice(0, 12)) { if (!S.ws) return; await openFile(p, { quiet: true }); }
  if (st.active) { const t = S.tabs.find(x => x.path === st.active); if (t) await activateTab(t, true); }
}

/* plain-text fallback editor wiring */
$('#pane-plain').addEventListener('input', e => { const t = S.active; if (t && !t.model) { t.text = e.target.value; refreshDirty(t); updateCtxLabel(); } });
$('#pane-plain').addEventListener('keydown', e => {
  if (e.key === 'Tab' && !e.ctrlKey && !e.altKey) { e.preventDefault(); const ta = e.target, s = ta.selectionStart; ta.setRangeText('    ', s, ta.selectionEnd, 'end'); ta.dispatchEvent(new Event('input')); }
});
$('#pane-plain').addEventListener('keyup', updateStatusPos); $('#pane-plain').addEventListener('click', updateStatusPos);

$('#diff-accept').onclick = () => { const t = S.active; if (t && t.kind === 'diff') agentDecide(t.approvalId, 'accept'); };
$('#diff-reject').onclick = () => { const t = S.active; if (t && t.kind === 'diff') agentDecide(t.approvalId, 'reject'); };

function setWrap(on) {
  S.wrap = on; lsSet('studio.wrap', on ? '1' : '0'); if (S.booted) prefSet('wrap', on);
  const o = { wordWrap: on ? 'on' : 'off' };
  E.ed && E.ed.updateOptions(o); E.diffEd && E.diffEd.updateOptions(o); $('#pane-plain').classList.toggle('wrap', on);
  $('#btn-wrap').classList.toggle('active', on); $('#sb-wrap').textContent = 'Wrap: ' + (on ? 'on' : 'off');
}
$('#btn-wrap').onclick = () => setWrap(!S.wrap);
// The code map (Monaco's minimap) beside the editor, on or off per person; phones never show it.
function setMinimap(on) {
  S.minimap = on; lsSet('studio.minimap', on ? '1' : '0'); if (S.booted) prefSet('minimap', on);
  E.ed && E.ed.updateOptions({ minimap: { enabled: on && !isPhone() } });
  const b = $('#btn-minimap'); b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on));
  b.title = (on ? 'Hide' : 'Show') + ' the code map (Alt+M)';
}
$('#btn-minimap').onclick = () => setMinimap(!S.minimap);
$('#sb-wrap').onclick = () => setWrap(!S.wrap);

function updateStatusPos() {
  const t = S.active, pos = $('#sb-pos'), lang = $('#sb-lang');
  if (!t || t.kind !== 'file' || t.binary) { pos.textContent = ''; lang.textContent = t && t.kind === 'diff' ? 'Diff' : ''; return; }
  lang.textContent = langLabel(t.lang || 'plaintext');
  if (t.model && E.ed) { const p = E.ed.getPosition(); pos.textContent = p ? `Ln ${p.lineNumber}, Col ${p.column}` : ''; }
  else { const ta = $('#pane-plain'); const before = ta.value.slice(0, ta.selectionStart).split('\n'); pos.textContent = `Ln ${before.length}, Col ${before[before.length - 1].length + 1}`; }
}
function renderWelcome() {
  const pm = $('#pane-msg');
  if (S.active) { if (S.active.kind === 'file' && S.active.binary) return; return; }
  pm.className = 'pane welcome'; pm.replaceChildren();
  pm.append(el('img', { class: 'w-logo', src: 'assets/eidoloom-mark-original.png', alt: 'Eidoloom Studio' }));
  if (S.config && S.models && (S.config.gateway_ok === false || !S.models.length)) {      // nothing to talk to yet: say how to connect one
    pm.append(el('div', { class: 'w-connect' },
      el('b', { text: 'No model server is connected' }),
      el('p', { class: 'muted', text: isAdmin() ? 'The agent needs a model server: Ollama, llama.cpp, vLLM, LM Studio or the Ollama Manager. Studio looks for one on this machine.'
        : 'The agent needs a model server. Ask an admin of this Studio to connect one.' }),
      isAdmin() ? el('button', { class: 'btn primary', onclick: () => openAccount('models') }, 'Connect a model server') : null));
  }
  if (!S.ws) {
    pm.append(el('h2', { text: 'Open a folder to start' }), el('button', { class: 'btn primary', onclick: openFolderDialog }, 'Open folder'));
  } else {
    pm.append(el('h2', { text: S.ws.name }), el('table', null,
      [['Go to file', 'Ctrl+P'], ['Search in files', 'Ctrl+Shift+F'], ['Toggle terminal', 'Ctrl+`'], ['Save', 'Ctrl+S']].map(([a, k]) =>
        el('tr', null, el('td', { class: 'muted', text: a }), el('td', null, ...k.split('+').flatMap((x, i) => [i ? ' + ' : '', el('kbd', { text: x })]))))));
  }
  showPane('msg');
}

/* ------------------------------------------------------------------ terminal */
const TM = { terms: [], active: null, seq: 0 };
const termHost = $('#term-host');

function panelVisible() { return !$('#panel').classList.contains('hidden'); }
function showPanel(show) {
  $('#panel').classList.toggle('hidden', !show); $('#split-bottom').classList.toggle('hidden', !show);
  lsSet('studio.panel', show ? '1' : '0');
  if (show) { if (!TM.terms.length && S.ws) termNew(); else if (TM.active) setTimeout(() => termFit(TM.active), 30); renderTermEmpty(); }
}
function togglePanel() { showPanel(!panelVisible()); }
function renderTermEmpty() {
  let n = $('.term-empty', termHost);
  if (!TM.terms.length) { if (!n) termHost.append(el('div', { class: 'term-empty', text: S.ws ? 'No terminal open' : 'Open a folder to start' })); else n.textContent = S.ws ? 'No terminal open' : 'Open a folder to start'; }
  else if (n) n.remove();
}
function termQueueSend(t, data) {
  t.outbuf += data;
  if (t.sending || !t.id) return;       // typed before the terminal opened: termNew sends it once it has an id
  t.sending = true;
  (async () => {
    while (t.outbuf && !t.closed) {
      const chunk = t.outbuf; t.outbuf = '';
      try { await POST('/api/term/input', { id: t.id, data: chunk }); } catch (e) { if (e.status === 404 || e.status === 410) { termMarkExit(t); break; } }
    }
    t.sending = false;
  })();
}
function termMarkExit(t) {
  if (t.exited) return; t.exited = true;
  t.tabEl && t.tabEl.classList.add('exited');
  const msg = '\r\n\x1b[90m[process exited]\x1b[0m\r\n';
  if (t.xterm) t.xterm.write(msg); else termPlainWrite(t, '\n[process exited]\n');
  if (t.es) { t.es.close(); t.es = null; }
}
// Wrap on (the default): lines wrap at the panel's width. Off: the terminal is at least TERM_NOWRAP_COLS wide and scrolls
// sideways, so long lines (paths, progress bars, tables) stay on one line. Kept per browser.
const TERM_NOWRAP_COLS = 200;
const termWraps = () => lsGet('studio.termWrap') !== '0';
function termDims(t) {
  try {
    if (t.fit) {
      const d = t.fit.proposeDimensions();
      if (d && d.cols > 1 && d.rows > 1) return { cols: termWraps() ? d.cols : Math.max(d.cols, TERM_NOWRAP_COLS), rows: d.rows };
    }
  } catch { /* ignore */ }
  if (t.plain) { const o = t.plain.out; return { cols: Math.max(20, Math.floor(o.clientWidth / 7.6)), rows: Math.max(5, Math.floor(o.clientHeight / 18)) }; }
  return { cols: 80, rows: 24 };
}
async function termSync() {
  if (!S.ws) return;
  const ws = S.ws;
  let list; try { list = (await GET('/api/term/list', { ws: ws.id })).terminals; } catch { return; }
  if (S.ws !== ws) return;
  const ids = new Set(list.map(x => x.id));
  for (const t of [...TM.terms]) if (t.id && !ids.has(t.id) && !t.exited) termRemove(t, true);
  for (const x of list) if (!TM.terms.some(t => t.id === x.id)) await termNew(x);
}
async function termNew(existing) {
  if (!S.ws) { toast('Open a folder first', 'info', 2500); return; }
  if (existing && existing.isTrusted !== undefined) existing = null;      // a click event from the + button
  const n = $('.term-empty', termHost); if (n) n.remove();
  if (!existing && !panelVisible()) { $('#panel').classList.remove('hidden'); $('#split-bottom').classList.remove('hidden'); lsSet('studio.panel', '1'); }
  const t = { n: ++TM.seq, id: null, outbuf: '', sending: false, closed: false, exited: false, es: null, lastCols: 0, lastRows: 0, opened: 0 };
  if (existing) { t.id = existing.id; t.n = existing.n; }
  t.name = 'bash ' + t.n;
  TM.terms.push(t);
  const ok = await xtermReady;
  t.box = el('div', { class: ok ? 'term-inst' : 'term-plain' });
  termHost.append(t.box);
  if (ok) {
    t.xterm = new window.Terminal({ cursorBlink: true, fontSize: 13, fontFamily: 'ui-monospace, "Cascadia Code", Menlo, Consolas, monospace', scrollback: 5000, allowProposedApi: true,
      theme: THEMES[currentTheme()].terminal });
    t.fit = new window.FitAddon.FitAddon(); t.xterm.loadAddon(t.fit);
    t.xterm.attachCustomKeyEventHandler(ev => {
      if (ev.type !== 'keydown') return true;
      const mod = ev.ctrlKey || ev.metaKey;
      if (mod && !ev.shiftKey && !ev.altKey && (ev.key === 'p' || ev.key === 'P' || ev.key === '`' || ev.key === 's' || ev.key === 'b')) return false;
      if (mod && (ev.shiftKey || ev.altKey) && (ev.key === 'F' || ev.key === 'f' || ev.code === 'KeyF')) return false;
      // Copy: Ctrl+Shift+C always, and Ctrl+C (Cmd+C) while text is selected; otherwise Ctrl+C interrupts as usual.
      if (mod && !ev.altKey && (ev.key === 'c' || ev.key === 'C') && (ev.shiftKey || t.xterm.hasSelection())) { termCopy(t); return false; }
      return true;
    });
    t.scroller = el('div', { class: 'term-scroll' });
    t.box.append(t.scroller);
    t.xterm.open(t.scroller);
    termTouch(t);
    termSelectBar(t);
    t.xterm.onData(d => termQueueSend(t, d));
    t.ro = new ResizeObserver(debounce(() => termFit(t), 60)); t.ro.observe(t.box);
  } else {
    const out = el('pre', { class: 'out' }), inp = el('input', { type: 'text', spellcheck: 'false', autocomplete: 'off', placeholder: 'Simple terminal — type a command and press Enter (Ctrl+C interrupts)' });
    t.box.append(out, el('div', { class: 'inrow' }, el('span', { text: '$' }), inp));
    t.plain = { out, inp, buf: '' }; t.dec = new TextDecoder('utf-8');
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); termQueueSend(t, inp.value + '\n'); inp.value = ''; }
      else if (e.ctrlKey && (e.key === 'c' || e.key === 'C') && inp.selectionStart === inp.selectionEnd) { e.preventDefault(); termQueueSend(t, '\x03'); }
      else if (e.ctrlKey && (e.key === 'd' || e.key === 'D') && !inp.value) { e.preventDefault(); termQueueSend(t, '\x04'); }
    });
    t.box.addEventListener('click', () => { if (!getSelection().toString()) inp.focus(); });
  }
  if (!existing || !TM.active) termActivate(t); else renderTermTabs();
  await sleep(30);
  termSize(t);
  const { cols, rows } = termDims(t);
  if (!existing) {
    try {
      const r = await POST('/api/term/open', { ws: S.ws.id, cols, rows });
      t.id = r.id; t.lastCols = cols; t.lastRows = rows;
      if (r.n) { t.n = r.n; t.name = 'bash ' + t.n; renderTermTabs(); }
    } catch (e) { errToast(e, 'Terminal'); termRemove(t, true); return; }
  }
  if (t.closed) { POST('/api/term/close', { id: t.id }).catch(() => {}); return; }
  termStream(t);
  if (t.outbuf) { const b = t.outbuf; t.outbuf = ''; termQueueSend(t, b); }
}
function termStream(t) {
  const es = liveStream('term', { id: t.id }); t.es = es;
  const ghost = skeleton(2, 'sk-term'); t.box.append(ghost);
  es.addEventListener('open', () => { if (t.opened++ > 0) { if (t.xterm) t.xterm.reset(); else { t.plain.out.textContent = ''; t.plain.buf = ''; t.plain.cur = 0; t.plain.ls = 0; } } });
  es.addEventListener('data', ev => {
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    if (!d.b64) return;
    ghost.remove();
    const bytes = b64ToBytes(d.b64);
    if (t.xterm) t.xterm.write(bytes); else termPlainWrite(t, t.dec.decode(bytes, { stream: true }));
  });
  es.addEventListener('exit', () => termMarkExit(t));
  es.addEventListener('error', () => { if (t.exited || t.closed) return; if (es.readyState === 2) { onUnauthorized(); } });
}
function termPlainWrite(t, text) {
  const p = t.plain;
  if (p.cur === undefined) { p.cur = 0; p.ls = 0; }
  text = text.replace(/\x1b\[[0-9;?]*[ -\/]*[@-~]/g, '').replace(/\x1b\][^\x07\x1b]*(\x07|\x1b\\)/g, '').replace(/\x1b[()][A-Z0-9]/g, '').replace(/\x07/g, '').replace(/\x08 \x08/g, '\x01');
  let buf = p.buf;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === '\r') { if (text[i + 1] === '\n') continue; p.cur = p.ls; }
    else if (ch === '\n') { p.cur = buf.length; buf += '\n'; p.cur = p.ls = buf.length; }
    else if (ch === '\b') { if (p.cur > p.ls) p.cur--; }
    else if (ch === '\x01') { if (p.cur > p.ls) { p.cur--; buf = buf.slice(0, p.cur) + buf.slice(p.cur + 1); } }
    else {
      if (p.cur >= buf.length) { buf += ch; } else buf = buf.slice(0, p.cur) + ch + buf.slice(p.cur + 1);
      p.cur++;
    }
  }
  if (buf.length > 120000) { const cut = buf.indexOf('\n', 20000) + 1 || 20000; buf = buf.slice(cut); p.cur -= cut; p.ls -= cut; if (p.cur < 0) p.cur = 0; if (p.ls < 0) p.ls = 0; }
  p.buf = buf;
  const near = p.out.scrollHeight - p.out.scrollTop - p.out.clientHeight < 40;
  p.out.textContent = buf; if (near) p.out.scrollTop = p.out.scrollHeight;
}
// Phones: xterm does not scroll by touch and its scrollbar is hidden (overlay scrollbars), so a swipe scrolls the output,
// up and down by lines and, with wrap off, sideways; it keeps gliding after the finger lifts and slows down. A thin bar on
// the right shows where you are in the output, fading when idle.
function termTouch(t) {
  const host = t.box, thumb = el('div', { class: 'term-thumb' });
  host.append(thumb);
  let fade = 0;
  const show = () => {
    const b = t.xterm.buffer.active, rows = t.xterm.rows, total = b.length;
    if (total <= rows) { thumb.classList.remove('on'); return; }
    const h = Math.max(24, host.clientHeight * rows / total);
    thumb.style.height = h + 'px';
    thumb.style.top = ((host.clientHeight - h) * (b.viewportY / Math.max(1, b.baseY))) + 'px';
    thumb.classList.add('on'); clearTimeout(fade); fade = setTimeout(() => thumb.classList.remove('on'), 1200);
  };
  t.xterm.onScroll(show);
  const line = () => (t.xterm.options.fontSize || 13) * 1.2;
  let last = null, axis = null, accY = 0, vx = 0, vy = 0, glide = 0;
  const moveBy = (dx, dy) => {                       // dx, dy in pixels; content moves with the finger
    if (dx) t.scroller.scrollLeft += dx;
    if (dy) {
      accY += dy;
      const lines = Math.trunc(accY / line());
      if (lines) { t.xterm.scrollLines(lines); accY -= lines * line(); show(); }
    }
  };
  const stopGlide = () => { cancelAnimationFrame(glide); glide = 0; };
  // Selecting: hold a finger still for half a second to select the word under it, then drag to extend the selection
  // (the output scrolls when the finger nears the top or bottom). A tap elsewhere clears it.
  let press = 0, sel = null, edge = 0;
  const cell = (x, y) => {
    const scr = t.xterm.element.querySelector('.xterm-screen').getBoundingClientRect();
    const col = Math.max(0, Math.min(t.xterm.cols - 1, Math.floor((x - scr.left) / (scr.width / t.xterm.cols))));
    const row = Math.max(0, Math.min(t.xterm.rows - 1, Math.floor((y - scr.top) / (scr.height / t.xterm.rows))));
    return { col, row: row + t.xterm.buffer.active.viewportY };
  };
  const wordAt = c => {
    const text = (t.xterm.buffer.active.getLine(c.row) || { translateToString: () => '' }).translateToString(false);
    const isWord = ch => ch && /[^\s"'`()<>\[\]{}|;,]/.test(ch);
    if (!isWord(text[c.col])) return { start: c.col, end: c.col };
    let a = c.col, b = c.col;
    while (a > 0 && isWord(text[a - 1])) a--;
    while (b < text.length - 1 && isWord(text[b + 1])) b++;
    return { start: a, end: b };
  };
  const selectTo = c => {
    const cols = t.xterm.cols, a = sel.anchor, from = a.row * cols + a.start, to = c.row * cols + c.col;
    const lo = Math.min(from, to), hi = Math.max(a.row * cols + a.end, to);
    t.xterm.select(lo % cols, Math.floor(lo / cols), hi - lo + 1);
  };
  const pressAt = (x, y) => {
    const c = cell(x, y), w = wordAt(c);
    sel = { anchor: { row: c.row, start: w.start, end: w.end }, x, y };
    t.xterm.select(w.start, c.row, w.end - w.start + 1);
    if (navigator.vibrate) try { navigator.vibrate(10); } catch { /* ignore */ }
  };
  const edgeScroll = () => {                          // while dragging near an edge, keep scrolling and extending
    if (!sel || !sel.dragging) { edge = 0; return; }
    const r = host.getBoundingClientRect(), zone = 36;
    const dir = sel.y < r.top + zone ? -1 : sel.y > r.bottom - zone ? 1 : 0;
    if (dir) { t.xterm.scrollLines(dir); selectTo(cell(sel.x, sel.y)); show(); }
    edge = setTimeout(edgeScroll, 70);
  };
  host.addEventListener('touchstart', e => {
    if (e.target.closest && e.target.closest('.term-selbar')) return;      // the Copy bar's own taps
    stopGlide();
    clearTimeout(press);
    if (e.touches.length !== 1) { last = null; return; }
    last = { x: e.touches[0].clientX, y: e.touches[0].clientY, t: performance.now() }; axis = null; accY = 0; vx = vy = 0;
    const px = last.x, py = last.y;
    press = setTimeout(() => { if (last && !axis) { pressAt(px, py); axis = 'select'; } }, 450);
  }, { passive: true });
  host.addEventListener('touchmove', e => {
    if (e.target.closest && e.target.closest('.term-selbar')) return;      // the Copy bar's own taps
    if (!last || e.touches.length !== 1) return;
    const x = e.touches[0].clientX, y = e.touches[0].clientY, now = performance.now();
    if (axis === 'select') {
      sel.x = x; sel.y = y;
      if (!sel.dragging) { sel.dragging = true; edgeScroll(); }
      selectTo(cell(x, y));
      e.preventDefault();
      return;
    }
    const dx = last.x - x, dy = last.y - y, dt = Math.max(1, now - last.t);
    if (!axis) {
      if (Math.abs(dx) < 6 && Math.abs(dy) < 6) return;
      clearTimeout(press);
      const wide = t.scroller.scrollWidth > t.scroller.clientWidth + 2;
      axis = wide && Math.abs(dx) > Math.abs(dy) * 1.2 ? 'x' : 'y';
    }
    if (axis === 'x') { moveBy(dx, 0); vx = 0.8 * (dx / dt) + 0.2 * vx; }
    else { moveBy(0, dy); vy = 0.8 * (dy / dt) + 0.2 * vy; }
    last = { x, y, t: now };
    e.preventDefault();
  }, { passive: false });
  host.addEventListener('touchend', e => {
    if (e.target.closest && e.target.closest('.term-selbar')) return;      // the Copy bar's own taps
    clearTimeout(press);
    if (axis === 'select') {
      if (sel) sel.dragging = false;
      clearTimeout(edge); last = null; axis = null;
      if (e.cancelable) e.preventDefault();            // no click: it would clear the selection
      return;
    }
    if (last && !axis && t.xterm.hasSelection()) t.xterm.clearSelection();    // a tap clears a selection
    if (!last) return;
    const idle = performance.now() - last.t;           // a finger that stopped before lifting does not throw
    last = null;
    if (idle > 80 || (Math.abs(vx) < 0.15 && Math.abs(vy) < 0.15)) return;
    let prev = performance.now();
    const step = now => {
      const dt = Math.min(40, now - prev); prev = now;
      moveBy(vx * dt, vy * dt);
      const keep = Math.pow(0.995, dt);                  // about 0.92 per frame at 60 Hz
      vx *= keep; vy *= keep;
      glide = Math.abs(vx) > 0.02 || Math.abs(vy) > 0.02 ? requestAnimationFrame(step) : 0;
    };
    glide = requestAnimationFrame(step);
  }, { passive: true });
}
// A small bar over the terminal while text is selected: Copy, Select all, and a close button. Works with the mouse too.
function termSelectBar(t) {
  const bar = el('div', { class: 'term-selbar hidden' },
    el('button', { class: 'btn sm primary', text: 'Copy', onclick: e => { e.preventDefault(); termCopy(t); } }),
    el('button', { class: 'btn sm', text: 'Select all', onclick: e => { e.preventDefault(); t.xterm.selectAll(); } }),
    el('button', { class: 'icon-btn sm', title: 'Clear the selection', html: ico('x', 13), onclick: e => { e.preventDefault(); t.xterm.clearSelection(); } }));
  for (const b of bar.querySelectorAll('button')) b.addEventListener('mousedown', e => e.preventDefault());   // keep the selection
  t.box.append(bar);
  t.xterm.onSelectionChange(() => bar.classList.toggle('hidden', !t.xterm.hasSelection() || !t.xterm.getSelection().trim()));
}
function termCopy(t) {
  const text = t.xterm.getSelection();
  if (!text) return;
  Promise.race([copyText(text), sleep(800)]).then(() => toast(text.includes('\n') ? `Copied ${text.split('\n').length} lines` : 'Copied', 'info', 1500));
  t.xterm.clearSelection();
  t.xterm.focus();
}
// Sizes xterm to the panel, or wider than it with wrap off, and makes the scroller as wide as the text.
function termSize(t) {
  if (!t.xterm) return;
  const { cols, rows } = termDims(t);
  if (cols !== t.xterm.cols || rows !== t.xterm.rows) { try { t.xterm.resize(cols, rows); } catch { return; } }
  const wrap = termWraps();
  t.scroller.classList.toggle('nowrap', !wrap);
  const screen = t.xterm.element.querySelector('.xterm-screen');
  t.xterm.element.style.width = !wrap && screen ? (screen.offsetWidth + 16) + 'px' : '';
  if (wrap) t.scroller.scrollLeft = 0;
}
function termSetWrap(on) {
  lsSet('studio.termWrap', on ? '1' : '0');
  termWrapButton();
  for (const t of TM.terms) if (t.xterm) { t.lastCols = -1; }
  termFit(TM.active);
}
function termWrapButton() {
  const b = $('#term-wrap'); if (!b) return;
  const on = termWraps();
  b.classList.toggle('on', on);
  b.setAttribute('aria-pressed', on ? 'true' : 'false');
  b.title = on ? 'Long lines wrap (click to scroll sideways instead)' : 'Long lines scroll sideways (click to wrap them)';
}
function termFit(t) {
  if (!t || !t.fit || t !== TM.active || !panelVisible() || t.box.offsetWidth === 0) return;
  termSize(t);
  if (!t.id || !t.xterm) return;
  const { cols, rows } = t.xterm;
  if (cols !== t.lastCols || rows !== t.lastRows) { t.lastCols = cols; t.lastRows = rows; POST('/api/term/resize', { id: t.id, cols, rows }).catch(() => {}); }
}
function termActivate(t) {
  TM.active = t;
  for (const x of TM.terms) if (x.box) x.box.classList.toggle('hidden', x !== t);
  renderTermTabs();
  setTimeout(() => { termFit(t); t.xterm ? t.xterm.focus() : t.plain && t.plain.inp.focus(); }, 20);
}
function renderTermTabs() {
  const box = $('#term-tabs'); box.replaceChildren();
  for (const t of TM.terms) {
    const tab = el('div', { class: 'term-tab' + (t === TM.active ? ' active' : '') + (t.exited ? ' exited' : ''), onclick: () => termActivate(t) },
      el('span', { html: ico('terminal', 13) }), el('span', { text: t.name }),
      el('span', { class: 'x', title: 'Kill terminal', html: ico('x', 12), onclick: e => { e.stopPropagation(); termRemove(t); } }));
    t.tabEl = tab; box.append(tab);
  }
}
function termRemove(t, silent) {
  t.closed = true;
  if (t.es) t.es.close();
  if (t.ro) t.ro.disconnect();
  if (t.id && !silent) POST('/api/term/close', { id: t.id }).catch(() => {});
  if (t.xterm) { try { t.xterm.dispose(); } catch { /* ignore */ } }
  t.box && t.box.remove();
  const i = TM.terms.indexOf(t); if (i >= 0) TM.terms.splice(i, 1);
  if (TM.active === t) { TM.active = null; const nx = TM.terms[Math.min(i, TM.terms.length - 1)]; if (nx) termActivate(nx); }
  renderTermTabs(); renderTermEmpty();
}
function termCloseAll(silent) {
  for (const t of [...TM.terms]) {
    t.closed = true; if (t.es) t.es.close(); if (t.ro) t.ro.disconnect();
    if (t.id && !silent) POST('/api/term/close', { id: t.id }).catch(() => {});
    if (t.xterm) { try { t.xterm.dispose(); } catch { /* ignore */ } }
    t.box && t.box.remove();
  }
  TM.terms = []; TM.active = null; renderTermTabs(); renderTermEmpty();
}
$('#term-new').onclick = termNew;
$('#term-wrap').onclick = () => termSetWrap(!termWraps());
termWrapButton();
$('#term-hide').onclick = () => showPanel(false);
$('#sb-term').onclick = togglePanel;

/* ------------------------------------------------------------------ safe markdown */
function mdCodeBlock(lang, code) {
  return `<div class="codeblock"><div class="cb-head"><span>${esc(lang || 'text')}</span><button type="button" data-copy>Copy</button></div><pre><code>${esc(code)}</code></pre></div>`;
}
let MD_DOC = null;
function mdInline(src) {
  const codes = [];
  let s = esc(String(src).replace(/[\u0001\u0002]/g, ''));
  s = s.replace(/`([^`\n]+)`/g, (m, c) => { codes.push(c); return '\u0001' + (codes.length - 1) + '\u0002'; });
  // Math, $x_1$ or \(x_1\): kept away from the italics rules. A $ followed by a space or digit after it is money, not math.
  const maths = [];
  s = s.replace(/\\\((.+?)\\\)|(^|[^\\$\w])\$(?![\s$])([^$\n]+?)(?<![\s\\])\$(?![\d$])/g, (m, a, pre, b) => { maths.push(a ?? b); return (pre || '') + '\u0003' + (maths.length - 1) + '\u0004'; });
  s = s.replace(/\*\*([^\n]+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?![*\w])/g, '$1<em>$2</em>');
  s = s.replace(/(^|[^\w])_([^_\s][^_\n]*?)_(?!\w)/g, '$1<em>$2</em>');
  if (MD_DOC) {          // a Markdown file shown as a document: its own pictures and links to other files and headings
    s = s.replace(/!\[([^\]\n]*)\]\(((?!https?:)[^\s)"<]+)\)/g, '<img class="md-img" alt="$1" data-src="$2">');
    s = s.replace(/\[([^\]\n]+)\]\(((?!https?:|mailto:)[^\s)"<]+)\)/g, '<a href="#" data-file="$2">$1</a>');
  }
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)"<]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.replace(/\n/g, '<br>');
  s = s.replace(/\u0003(\d+)\u0004/g, (m, i) => `<span class="math" data-tex="${maths[+i]}">${maths[+i]}</span>`);
  return s.replace(/\u0001(\d+)\u0002/g, (m, i) => '<code>' + codes[+i] + '</code>');
}
/** Minimal markdown -> HTML. All input is HTML-escaped before any tags are produced. */
// Table rows split on | outside `code`; \| stays a literal bar.
function mdTableCells(line) {
  const cells = []; let cur = '', code = false;
  const s = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  for (let k = 0; k < s.length; k++) {
    const ch = s[k];
    if (ch === '\\' && s[k + 1] === '|') { cur += '|'; k++; continue; }
    if (ch === '`') code = !code;
    if (ch === '|' && !code) { cells.push(cur.trim()); cur = ''; continue; }
    cur += ch;
  }
  cells.push(cur.trim());
  return cells;
}
const MD_TABLE_RULE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
function mdTable(lines, i) {
  const head = mdTableCells(lines[i]), aligns = mdTableCells(lines[i + 1]).map(c => /^:-+:$/.test(c) ? 'center' : /-+:$/.test(c) ? 'right' : '');
  const cell = (tag, text, k) => `<${tag}${aligns[k] ? ` style="text-align:${aligns[k]}"` : ''}>${mdInline(text || '')}</${tag}>`;
  const rows = []; let j = i + 2;
  while (j < lines.length && /\|/.test(lines[j]) && lines[j].trim()) { rows.push(mdTableCells(lines[j])); j++; }
  const html = '<div class="md-table"><table><thead><tr>' + head.map((c, k) => cell('th', c, k)).join('') + '</tr></thead><tbody>'
    + rows.map(r => '<tr>' + head.map((_, k) => cell('td', r[k], k)).join('') + '</tr>').join('') + '</tbody></table></div>';
  return { html, next: j };
}
function mdToHtml(src) {
  const lines = String(src).replace(/\r\n?/g, '\n').split('\n'), out = [];
  let i = 0, para = [];
  const flush = () => { if (para.length) { out.push('<p>' + mdInline(para.join('\n')) + '</p>'); para = []; } };
  while (i < lines.length) {
    const line = lines[i];
    let m;
    if ((m = /^\s*```\s*([^\s`]*)/.exec(line))) {
      flush(); const code = []; i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) code.push(lines[i++]);
      i++; out.push(mdCodeBlock(m[1], code.join('\n'))); continue;
    }
    if ((m = /^\s*(\$\$|\\\[)(.*)$/.exec(line))) {          // display math: $$ … $$ or \[ … \], on one line or several
      const close = m[1] === '$$' ? '$$' : '\\]', body = [];
      let rest = m[2], done = false;
      while (true) {
        const at = rest.indexOf(close);
        if (at >= 0) { body.push(rest.slice(0, at)); done = true; break; }
        body.push(rest); i++;
        if (i >= lines.length) break;
        rest = lines[i];
      }
      if (done) { flush(); const tex = esc(body.join('\n').trim()); out.push(`<div class="math-block" data-tex="${tex}">${tex}</div>`); i++; continue; }
      i -= body.length - 1;                 // never closed: plain text after all
    }
    if (/\|/.test(line) && i + 1 < lines.length && MD_TABLE_RULE.test(lines[i + 1]) && /\|/.test(lines[i + 1])) {
      flush(); const t = mdTable(lines, i); out.push(t.html); i = t.next; continue;
    }
    if ((m = /^(#{1,4})\s+(.*)$/.exec(line))) { flush(); out.push(`<h${m[1].length}>${mdInline(m[2])}</h${m[1].length}>`); i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); out.push('<hr>'); i++; continue; }
    if (/^\s*>/.test(line)) { flush(); const q = []; while (i < lines.length && /^\s*>/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, '')); out.push('<blockquote>' + mdInline(q.join('\n')) + '</blockquote>'); continue; }
    if (/^\s*[-*+]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) {
      flush(); const ordered = /^\s*\d+[.)]\s+/.test(line), items = [];
      const re = ordered ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*+]\s+(.*)$/;
      while (i < lines.length) {
        const mm = re.exec(lines[i]);
        if (mm) { items.push(mm[1]); i++; }
        else if (items.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*[-*+]\s+/.test(lines[i]) && !/^\s*\d+[.)]\s+/.test(lines[i])) { items[items.length - 1] += '\n' + lines[i].trim(); i++; }
        else break;
      }
      const tag = ordered ? 'ol' : 'ul';
      out.push(`<${tag}>` + items.map(t => '<li>' + mdInline(t) + '</li>').join('') + `</${tag}>`); continue;
    }
    if (!line.trim()) { flush(); i++; continue; }
    para.push(line); i++;
  }
  flush();
  return out.join('');
}

/* ------------------------------------------------------------------ agent panel */
const A = {
  session: null, sessions: [], run: null, es: null, lastSeq: 0, running: false, t0: 0, timer: null,
  turn: 0, maxTurns: 0, pTok: 0, cTok: 0, statusText: '', cur: null, plan: null, step: null, tools: new Map(), approvals: new Map(), pending: 0,
  stick: true, doneSeen: false, retry: 0, stopping: false, lastReason: '', raf: 0, sendBusy: false,
};
const msgsEl = $('#msgs');
const SSE_TYPES = ['user', 'text_fixed', 'status', 'token', 'thinking', 'tool_call', 'tool_result', 'approval', 'approval_resolved', 'file_changed', 'skill_saved', 'plan', 'page_view', 'open_live', 'open_browser', 'tool_picture', 'tool_progress', 'sent_back', 'usage', 'check', 'git_branch', 'compacted', 'files', 'goal', 'timeskip', 'error', 'done', 'codex_usage'];

function scrollNow() { msgsEl.scrollTop = msgsEl.scrollHeight; }
function scrollMaybe() { if (A.stick) scrollNow(); }
msgsEl.addEventListener('scroll', () => {
  A.stick = msgsEl.scrollHeight - msgsEl.scrollTop - msgsEl.clientHeight < 70;
  $('#to-bottom').classList.toggle('hidden', A.stick);
});
$('#to-bottom').onclick = () => { A.stick = true; scrollNow(); };
msgsEl.addEventListener('click', e => {
  const b = e.target.closest('[data-copy]'); if (!b) return;
  const code = b.closest('.codeblock').querySelector('code');
  copyText(code.textContent); const old = b.textContent; b.textContent = 'Copied'; setTimeout(() => { b.textContent = old; }, 1200);
});

function agentEmpty() {
  msgsEl.replaceChildren();
  if (!S.ws) {
    msgsEl.append(el('div', { class: 'agent-empty' }, el('div', { class: 'icon-big', html: ico('folder-open', 36) }), el('h3', { text: 'Open a folder to start' }),
      el('p', { text: 'The agent works inside the folder you open.' }), el('button', { class: 'btn primary', onclick: openFolderDialog }, 'Open folder')));
    return;
  }
  const sug = ['Explain how this project is structured', 'Find and fix bugs in the current file', 'Write tests for the main module', 'Add error handling and clean up this code'];
  msgsEl.append(el('div', { class: 'agent-empty' }, el('div', { class: 'icon-big', html: ico('sparkles', 36) }), el('h3', { text: 'Ask the agent to…' }),
    el('p', { text: 'It can read and search your code, edit files, and run commands. You approve changes unless auto modes are on.' }),
    el('div', { class: 'suggest' }, sug.map(s => el('button', { onclick: () => { const i = $('#agent-input'); i.value = s; i.focus(); autosize(); } }, s)))));
}
function clearMsgState() {
  A.cur = null; A.plan = null; A.step = null; A.load = null; planDock(); A.goal = null; renderGoal(); A.effort = ''; A.tools = new Map(); A.approvals = new Map(); A.pending = 0; A.turn = 0; A.maxTurns = 0; A.pTok = 0; A.cTok = 0; A.statusText = ''; A.lastReason = ''; A.ctxUsed = 0; A.ctxCap = 0; A.ctxWarned = false; A.undos = []; effortChip();
}
function agentDetach() {
  if (A.es) { A.es.close(); A.es = null; }
  clearInterval(A.timer); A.timer = null; A.run = null; setRunning(false);
}
function agentReset() {
  agentDetach(); clearMsgState(); A.session = null; A.sessions = []; A.doneSeen = true;
  agentEmpty(); renderStatus(); renderSessionTitle(); updateComposerState();
}
function renderSessionTitle() {
  const s = A.sessions.find(x => x.id === A.session);
  $('#sess-title').textContent = s ? (s.title || 'Untitled chat') : 'New chat';
}
async function loadSessions() {
  if (!S.ws) { A.sessions = []; return; }
  try { const d = await GET('/api/agent/sessions', { ws: S.ws.id }); A.sessions = (d.sessions || []).slice().sort((a, b) => (b.updated || 0) > (a.updated || 0) ? 1 : -1); } catch (e) { errToast(e, 'Sessions'); }
  renderSessionTitle();
}
async function agentInit() {
  const ws = S.ws; if (!ws) return;
  agentEmpty();
  await loadSessions();
  if (S.ws !== ws) return;
  let act = null;
  try { act = await GET('/api/agent/active', { ws: ws.id }); } catch { /* ignore */ }
  const last = S.prefs['session:' + ws.id] || lsGet('studio.session.' + ws.id);
  if (act && act.run) {
    const sid = act.session || (A.sessions.find(s => s.id === last) ? last : (A.sessions[0] && A.sessions[0].id));
    if (sid) { await openSession(sid, { upToLastUser: true, silent: true }); }
    A.session = sid || null; renderSessionTitle();
    A.t0 = Date.now(); attachRun(act.run, 0);
  } else if (last && A.sessions.find(s => s.id === last)) await openSession(last, { silent: true });
}

/* ---- sessions menu */
function renderSessionMenu() {
  const m = $('#sess-menu'); m.replaceChildren();
  if (!A.sessions.length) m.append(el('div', { class: 'mempty', text: 'No previous chats' }));
  for (const s of A.sessions) {
    const theirs = !!s.by;
    m.append(el('div', { class: 'mi' + (s.id === A.session ? ' sel' : ''), onclick: () => { m.classList.add('hidden'); openSession(s.id); } },
      s.shared ? el('span', { class: 'sess-shared', title: theirs ? `${s.by}'s chat, shared with this folder` : 'Shared with everyone who has this folder', html: ico('users', 12) }) : null,
      el('span', { class: 'grow', text: (s.title || 'Untitled chat') + (theirs ? ` · ${s.by}` : ''), title: s.title || '' }), el('small', { text: ago(s.updated) }),
      !theirs && S.ws && S.ws.members ? el('span', { class: 'del share' + (s.shared ? ' on' : ''), title: s.shared ? 'Make this chat private again' : 'Share this chat with everyone who has this folder', html: ico('users', 13),
        onclick: async e => { e.stopPropagation(); try { await POST('/api/agent/session/share', { ws: S.ws.id, id: s.id, shared: !s.shared }); s.shared = !s.shared; renderSessionMenu(); toast(s.shared ? 'Chat shared with the folder' : 'Chat is private again', 'success', 2000); } catch (err) { errToast(err, 'Not changed'); } } }) : null,
      el('span', { class: 'del pass', title: 'Pass this chat to someone', html: ico('send', 13), onclick: e => { e.stopPropagation(); m.classList.add('hidden'); A.passPick = s.id; passChat(); } }),
      el('span', { class: 'del', title: 'Delete chat', html: ico('trash', 13), onclick: e => { e.stopPropagation(); deleteSession(s); } })));
  }
}
$('#sess-btn').onclick = e => { e.stopPropagation(); const m = $('#sess-menu'); if (m.classList.contains('hidden')) { loadSessions().then(renderSessionMenu); renderSessionMenu(); } m.classList.toggle('hidden'); };
document.addEventListener('mousedown', e => { if (!e.target.closest('.sess-wrap')) $('#sess-menu').classList.add('hidden'); });
async function deleteSession(s) {
  if (!(await confirmBox('Delete chat', `Delete "${s.title || 'Untitled chat'}"?`, 'Delete', true))) return;
  try {
    await POST('/api/agent/session/delete', { id: s.id });
    A.sessions = A.sessions.filter(x => x.id !== s.id);
    if (A.session === s.id) { if (A.running) { toast('Stop the run first', 'info'); return; } newChat(); }
    renderSessionMenu();
  } catch (e) { errToast(e, 'Delete failed'); }
}
function newChat() {
  if (A.running) { toast('Stop the current run before starting a new chat', 'info'); return; }
  agentDetach(); clearMsgState(); A.session = null; A.shownSession = null; renderQueue(); pendingGhost(false); agentEmpty(); renderStatus(); renderSessionTitle(); $('#agent-input').focus();
}
$('#new-chat').onclick = newChat;
async function openSession(id, { upToLastUser = false, silent = false } = {}) {
  if (A.running && !silent) { toast('Stop the current run before switching chats', 'info'); return; }
  // Windows get told about chats and runs in quick succession, so a slow response must not undo a newer one.
  const seq = A.openSeq = (A.openSeq || 0) + 1;
  const showing = [...msgsEl.childNodes];
  if (id !== A.shownSession) msgsEl.replaceChildren(skeleton(2), skeleton(4), skeleton(3));
  let d; try { d = await GET('/api/agent/session', { id }); } catch (e) { if (seq === A.openSeq) msgsEl.replaceChildren(...showing); if (!silent) errToast(e, 'Cannot open chat'); return false; }
  if (seq !== A.openSeq) return false;
  A.shownSession = id;
  agentDetach(); clearMsgState(); A.session = id; renderQueue(); lsSet('studio.session.' + (S.ws && S.ws.id), id);
  if (!silent && S.ws) prefSet('session:' + S.ws.id, id);
  let evs = d.events || [];
  if (upToLastUser) { let k = -1; evs.forEach((e, i) => { if (evType(e) === 'user') k = i; }); if (k >= 0) evs = evs.slice(0, k + 1); }
  msgsEl.replaceChildren();
  if (!evs.length) agentEmpty();
  replayEvents(evs);
  let live = null;
  if (!upToLastUser && evs.length && evType(evs[evs.length - 1]) !== 'done') { try { live = (await GET('/api/agent/active', { ws: S.ws.id })).run; } catch { live = true; } }
  if (seq !== A.openSeq) return false;
  if (!upToLastUser && evs.length && evType(evs[evs.length - 1]) !== 'done' && !live) {
    settleRows();
    addBlock(el('div', { class: 'm-done' }, 'This run ended without finishing, possibly because Studio restarted.', continueButton()));
  }
  A.goal = d.goal || null; renderGoal(); A.effort = d.effort || '';
  if (d.model && S.models.some(m => m.name === d.model) && d.model !== S.model) { S.model = d.model; $('#model-select').value = d.model; lsSet('studio.model', S.model); }
  effortChip();          // after the chat's model is set: the chip and the Codex strip follow the model in use
  renderSessionTitle(); renderStatus(); A.stick = true; setTimeout(scrollNow, 0); setTimeout(scrollNow, 120);
  return true;
}
function evType(e) {
  const t = e.type || e.event || (e.role === 'user' ? 'user' : '');
  return t === 'user_message' || t === 'user_prompt' ? 'user' : t;
}
function replayEvents(evs) {
  for (const e of evs) {
    const t = evType(e); const d = e.data && typeof e.data === 'object' ? { ...e.data, ...e } : e;
    handleEvent(t, d, false);
  }
  finishCur();
  for (const [id, a] of A.approvals) if (!a.resolved) resolveApprovalUI(id, null);
  A.pending = 0;
  // Only the last thing in a chat can still be acted on.
  const last = msgsEl.lastElementChild;
  $$('.m-continue', msgsEl).forEach(b => { if (!last || !last.contains(b)) b.remove(); });
}

/* ---- rendering helpers */
function skeleton(lines = 3, extra = '') {
  const widths = [92, 74, 86, 58, 80, 66];
  return el('div', { class: 'skeleton ' + extra, role: 'status', 'aria-label': 'Loading' }, Array.from({ length: lines }, (_, i) => el('div', { class: 'sk-line', style: `width:${widths[i % widths.length]}%` })));
}
function pendingGhost(show) {
  // What the model is about to say. Removed as soon as anything real arrives.
  A.ghost?.remove(); A.ghost = null;
  if (show) { A.ghost = skeleton(2, 'sk-ghost'); msgsEl.append(A.ghost); scrollMaybe(); }
}
function addBlock(node) { msgsEl.querySelector('.agent-empty')?.remove(); msgsEl.append(node); renderMath(node); scrollMaybe(); return node; }
// Files a large attachment was saved as: the file, then the CSV of each sheet, kept in a folder named after it.
function savedGroups(paths) {
  const groups = [];
  for (const p of paths || []) {
    const owner = groups.find(g => p.startsWith(g.file.replace(/\.[^./]+$/, '') + '/'));
    if (owner) owner.sheets.push(p); else groups.push({ file: p, sheets: [] });
  }
  return groups;
}
function savedLink(path, text) {
  return el('button', { class: 'saved-link', text, title: 'Open ' + path, onclick: e => { e.stopPropagation(); openFile(path); } });
}
function addUser(text, ctx, att = {}, guided = false) {
  const b = el('div', { class: 'm-user' }, text);
  if (ctx && ctx.path) b.append(el('span', { class: 'ctx-note', text: '📎 ' + ctx.path + (ctx.selection ? ` (lines ${ctx.selection.start_line}–${ctx.selection.end_line})` : '') }));
  if (att.images?.length) b.append(el('div', { class: 'user-pics' }, ...att.images.map(p => el('img', { src: p.src, alt: p.name, title: p.name, 'data-name': p.name, loading: 'lazy' }))));
  if (att.files?.length) b.append(el('span', { class: 'ctx-note', text: '📄 ' + att.files.join(', ') }));
  if (guided) { b.classList.add('guided'); b.prepend(el('span', { class: 'guide-tag', text: 'Steer' })); }
  if (att.saved) b.append(el('span', { class: 'ctx-note', text: '📄 Saved as a file for the agent to read in parts' }));
  if (att.savedFiles?.length) b.append(el('div', { class: 'ctx-note saved-files', title: 'Too large for the chat, so Studio saved it in the folder; the agent read the start and reads the rest from there' },
    ...savedGroups(att.savedFiles).map(g => el('div', { class: 'saved-group' }, '📁 ', savedLink(g.file, g.file.replace(/^attachments\//, '')),
      g.sheets.length ? el('div', { class: 'saved-sheets' }, `Sheets as CSV (${g.sheets.length}): `, ...g.sheets.flatMap((p, i) => [i ? ' · ' : '', savedLink(p, p.split('/').pop().replace(/\.csv$/, ''))])) : ''))));
  if (att.timeskip) b.prepend(el('span', { class: 'guide-tag skip-tag', text: 'Time-skip', title: 'The agent worked on a copy of the folder' }));
  if (att.task) b.prepend(el('span', { class: 'guide-tag task-tag', text: 'Task', title: 'Sent by a scheduled task or loop' }));
  if (att.goal) b.prepend(el('span', { class: 'guide-tag goal-tag', text: 'Goal', title: 'The agent works on this until it is reached' }));
  // Copy: the message as typed, for every message.
  const copyBtn = el('button', { class: 'u-act copy', title: 'Copy this message', 'aria-label': 'Copy this message', html: ico('copy', 13), onclick: async e => {
    e.stopPropagation(); await Promise.race([copyText(text), sleep(800)]); copyBtn.innerHTML = ico('check', 13); setTimeout(() => { copyBtn.innerHTML = ico('copy', 13); }, 1500);
  } });
  if (att.plan) { b.prepend(el('span', { class: 'guide-tag plan-tag', text: 'Plan first', title: 'The agent could look, but not change anything' })); b.append(el('div', { class: 'u-acts' }, copyBtn)); }
  else {
    b._text = text; b._files = !!(att.images?.length || att.files?.length);
    msgsEl.querySelectorAll('.m-user.last-user').forEach(n => n.classList.remove('last-user')); b.classList.add('last-user');
    b.append(el('div', { class: 'u-acts' }, copyBtn,
      el('button', { class: 'u-act', title: 'Rewind to here: remove this message and everything after it, and put it back in the box', 'aria-label': 'Rewind to here', html: ico('undo', 13), onclick: () => rewindTo(b) }),
      el('button', { class: 'u-act', title: 'Branch from here: a new chat with everything before this message, and this message in the box', 'aria-label': 'Branch from here', html: ico('branch', 13), onclick: () => branchFrom(b) }),
      el('button', { class: 'u-act again', title: 'Ask again: remove the answer and send this message again', 'aria-label': 'Ask again', html: ico('refresh', 13), onclick: () => askAgain(b) })));
  }
  addBlock(b);
}
/* ---- rewind, branch, ask again: the chat is cut just before one of the user's messages (steers do not count) */
function userIndex(node) { return [...msgsEl.querySelectorAll('.m-user:not(.guided)')].indexOf(node); }
function changesAfter(node) {
  return [...msgsEl.querySelectorAll('.m-tool.has-change, .m-undo')].filter(n => node.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING)
    .filter(n => { const b = n.querySelector('.card-bar button, .undo-chip button'); return b && !b.disabled; }).length;
}
function putInBox(text) {
  const inp = $('#agent-input'), old = inp.value.trim();
  inp.value = old ? text + '\n\n' + old : text; autosize(); inp.focus();
}
async function rewindTo(node, { resend = false } = {}) {
  if (A.running) { toast('Stop the run first', 'info'); return; }
  const k = userIndex(node); if (k < 0 || !A.session) return;
  const changes = changesAfter(node);
  let undo = false;
  if (!resend || changes) {
    const box = changes ? el('input', { type: 'checkbox' }) : null;
    const body = el('div', {}, el('p', { text: resend ? 'The answer to this message is removed and the message is sent again.' : 'This message and everything after it are removed from the chat. The message goes back in the box, so you can change it and send it again.' }),
      changes ? el('label', { class: 'dlg-check' }, box, ` Also undo the agent's ${changes} file change${changes === 1 ? '' : 's'} after it`) : null,
      el('p', { class: 'dlg-note', text: 'Files changed by commands the agent ran stay as they are.' }));
    const ok = await dialog({ title: resend ? 'Ask again' : 'Rewind to here', body, buttons: [{ label: 'Cancel', value: false }, { label: resend ? 'Ask again' : 'Rewind', value: true, kind: 'primary' }] });
    if (ok !== true) return;
    undo = !!box?.checked;
  }
  let r;
  try { r = await POST('/api/agent/session/rewind', { ws: S.ws.id, id: A.session, user: k, undo_files: undo }); } catch (e) { errToast(e, 'Rewind failed'); return; }
  for (const p of r.restored || []) onDiskChanged(p);
  if (r.restored?.length) { toast(`Undid changes to ${r.restored.length} file${r.restored.length === 1 ? '' : 's'}`, 'success', 3000); refreshTreeSoon(); }
  await openSession(A.session, { silent: true });
  if (resend && !r.attachments) sendText(r.text);
  else { putInBox(r.text); if (r.attachments) toast('Add the pictures or files again: they were not kept in the box', 'info', 6000); }
}
function askAgain(node) { if (node._files) { rewindTo(node); return; } rewindTo(node, { resend: true }); }
async function branchFrom(node) {
  if (A.running) { toast('Wait until the run ends', 'info'); return; }
  const k = userIndex(node); if (k < 0 || !A.session) return;
  let r;
  try { r = await POST('/api/agent/session/branch', { ws: S.ws.id, id: A.session, user: k }); } catch (e) { errToast(e, 'Branch failed'); return; }
  await loadSessions();
  if (await openSession(r.id)) { if (!(msgsEl.querySelector('.m-user, .m-asst, .m-tool'))) agentEmpty(); putInBox(node._text || ''); toast('A new chat, branched from the earlier one', 'success', 3000); }
}
/* ---- quote: select text in a reply and put it in the box as a quote */
const quoteBtn = el('button', { class: 'quote-btn hidden', type: 'button', html: ico('edit', 12) + ' Quote', onmousedown: e => e.preventDefault(), onclick: () => {
  const text = String(getSelection() || '').trim(); quoteBtn.classList.add('hidden'); if (!text) return;
  const quoted = text.split('\n').map(l => '> ' + l).join('\n') + '\n\n';
  const inp = $('#agent-input'); inp.value = quoted + inp.value; autosize(); inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length);
} });
document.body.append(quoteBtn);
document.addEventListener('selectionchange', () => {
  const sel = getSelection(); const node = sel && sel.rangeCount && !sel.isCollapsed ? sel.getRangeAt(0).commonAncestorContainer : null;
  const host = node && (node.nodeType === 1 ? node : node.parentElement)?.closest('.m-asst, .m-done .sum, .think-body, .m-user');
  if (!host || !msgsEl.contains(host) || !String(sel).trim()) { quoteBtn.classList.add('hidden'); return; }
  const r = sel.getRangeAt(0).getBoundingClientRect();
  quoteBtn.classList.remove('hidden');
  quoteBtn.style.left = Math.max(6, Math.min(innerWidth - quoteBtn.offsetWidth - 6, r.left + r.width / 2 - quoteBtn.offsetWidth / 2)) + 'px';
  quoteBtn.style.top = Math.max(6, r.top - quoteBtn.offsetHeight - 6) + 'px';
});
msgsEl.addEventListener('scroll', () => quoteBtn.classList.add('hidden'));
/* Math is drawn with KaTeX, fetched the first time a reply has some; without it the formula stays as written. */
let katexLoading = null;
function loadKatex() {
  if (!katexLoading) {
    const base = 'https://cdn.jsdelivr.net/npm/katex@0.19.1/dist/';
    document.head.append(el('link', { rel: 'stylesheet', href: base + 'katex.min.css' }));
    // Monaco's loader defines an AMD `define`, which KaTeX would register with instead of setting window.katex.
    const amd = window.define; window.define = undefined;
    katexLoading = loadScript(base + 'katex.min.js').finally(() => { if (amd) window.define = amd; }).catch(e => { katexLoading = null; throw e; });
  }
  return katexLoading;
}
function renderMath(root) {
  const todo = root && root.querySelectorAll ? [...root.querySelectorAll('.math:not(.drawn), .math-block:not(.drawn)')] : [];
  if (!todo.length) return;
  loadKatex().then(() => {
    for (const n of todo) {
      if (n.classList.contains('drawn')) continue;
      try { window.katex.render(n.dataset.tex, n, { displayMode: n.classList.contains('math-block'), throwOnError: false, output: 'html' }); n.classList.add('drawn'); }
      catch { /* stays as written */ }
    }
  }).catch(() => { /* no network: the formula stays as written */ });
}
function finishCur() {
  const c = A.cur; if (!c) return;
  if (c.kind === 'text') { c.el.classList.remove('streaming'); c.el.innerHTML = mdToHtml(c.raw); renderMath(c.el); if (c.raw.trim()) c.el.append(copyReplyButton(c.raw)); }
  else if (c.kind === 'thinking') { c.body.textContent = c.raw; c.el.classList.remove('live'); c.lbl.textContent = 'Thought'; }
  A.cur = null;
}
function renderCur() {
  A.raf = 0; const c = A.cur; if (!c) return;
  if (c.kind === 'text') c.el.innerHTML = mdToHtml(c.raw); else c.body.textContent = c.raw;
  scrollMaybe();
}
// Copies a reply as the model wrote it, Markdown and all.
function copyReplyButton(raw) {
  const b = el('button', { class: 'copy-reply', title: 'Copy this reply', 'aria-label': 'Copy this reply', html: ico('copy', 13), onclick: async e => {
    e.stopPropagation(); await copyText(raw); b.innerHTML = ico('check', 13); b.classList.add('done'); setTimeout(() => { b.innerHTML = ico('copy', 13); b.classList.remove('done'); }, 1500);
  } });
  return b;
}
function copySummaryButton(raw) {
  const b = el('button', { class: 'btn sm cp-copy', type: 'button', html: ico('copy', 12) + ' Copy summary', onclick: async () => {
    await copyText(raw); b.innerHTML = ico('check', 12) + ' Copied'; b.classList.add('done');
    setTimeout(() => { b.innerHTML = ico('copy', 12) + ' Copy summary'; b.classList.remove('done'); }, 1500);
  } });
  return b;
}
function streamInto(kind, text) {
  let c = A.cur;
  if (!c || c.kind !== kind) {
    finishCur();
    if (kind === 'text') { const n = el('div', { class: 'm-asst streaming' }); addBlock(n); c = { kind, el: n, raw: '' }; }
    else {
      const body = el('div', { class: 'think-body' }), lbl = el('span', { class: 'lbl', text: 'Thinking…' });
      const d = el('details', { class: 'm-think live' }, el('summary', null, el('span', { class: 'chev', html: ico('chevron-right', 12) }), lbl), body);
      d.addEventListener('toggle', () => { if (d.open && A.cur && A.cur.el === d) body.textContent = A.cur.raw; });     // no waiting for the next repaint
      addBlock(d); c = { kind, el: d, body, lbl, raw: '' };
    }
    A.cur = c;
  }
  c.raw += text;
  // Browsers pause animation frames in background tabs, which would hold the text back until the tab is shown.
  if (!A.raf) A.raf = document.hidden ? setTimeout(renderCur, 150) : requestAnimationFrame(renderCur);
}
function parseArgs(a) { if (typeof a === 'string') { try { return JSON.parse(a); } catch { return a; } } return a; }
function shortArgs(name, args) {
  args = parseArgs(args);
  if (args == null) return '';
  if (typeof args === 'string') return args.replace(/\s+/g, ' ').slice(0, 120);
  let s = '';
  if (args.command) s = args.command;
  else if (args.name) s = args.name;
  else if (args.path !== undefined) {
    s = args.path || '.';
    if (args.start_line || args.end_line) s += `:${args.start_line || 1}-${args.end_line || ''}`;
    if (args.query) s += `  "${args.query}"`;
  } else if (args.query) s = `"${args.query}"`;
  else if (args.summary) s = args.summary;
  else s = JSON.stringify(args);
  return String(s).replace(/\s+/g, ' ').slice(0, 160);
}
const TOOL_ICO = { ask_user: 'help', list_files: 'folder', read_file: 'file', search: 'search', write_file: 'edit', replace_in_file: 'edit', delete_file: 'trash', run: 'terminal', finish: 'flag', save_skill: 'skill', use_skill: 'skill', plan: 'list', view_page: 'globe', open_preview: 'globe', write_document: 'sheet' };
// Tools whose own card says what is going on, so the status line keeps the plan's step instead.
const QUIET_TOOLS = new Set(['ask_user', 'finish', 'plan']);
const TOOL_LABEL = { ask_user: 'Question', list_files: 'List files', read_file: 'Read', search: 'Search', write_file: 'Write', replace_in_file: 'Edit', delete_file: 'Delete', run: 'Run command', finish: 'Finish', save_skill: 'Save skill', use_skill: 'Use skill', plan: 'Plan', view_page: 'View page', open_preview: 'Open preview', write_document: 'Document' };
function userToolLabel(name) { const m = /^user__(.+)$/.exec(name || ''); return m ? 'Your tool · ' + m[1] : null; }
// Each tool server's look in the chat: an icon and a colour token (per theme in app.css), by its name, then by words in
// it, else a colour picked from its name so every server keeps its own.
const SERVER_LOOKS = {
  comfyui: ['create', 'pictures'], video: ['film', 'video'], voices: ['mic', 'voice'], sounds: ['music', 'sound'], audio: ['volume', 'sound'], blender: ['cube', '3d'],
  'greek-check': ['spell', 'language'], 'web-search': ['search', 'web'], fetch: ['download', 'fetch'], context7: ['book', 'docs'],
  'tutoring-db': ['database', 'db'], listmonk: ['mail', 'mail'], 'listmonk-send': ['send', 'send'], browser: ['globe', 'browser'],
};
const SERVER_WORDS = [[/mail|listmonk|smtp/, 'mail', 'mail'], [/db|sql|postgres|mongo|redis|database/, 'database', 'db'], [/search|searx|brave|google/, 'search', 'web'],
  [/fetch|http|browser|web/, 'download', 'fetch'], [/doc|context|wiki|book/, 'book', 'docs'], [/comfy|image|picture|photo|diffusion/, 'create', 'pictures'],
  [/video|ffmpeg|film/, 'film', 'video'], [/voice|speak|tts|speech/, 'mic', 'voice'], [/sound|music|audio/, 'music', 'sound'], [/blender|3d|mesh/, 'cube', '3d'],
  [/greek|spell|grammar|language|lint/, 'spell', 'language'], [/git|github/, 'branch', 'fetch']];
const SERVER_SPARE = ['pictures', 'video', 'voice', 'sound', '3d', 'language', 'web', 'fetch', 'docs', 'db', 'mail', 'send'];
function serverLook(server) {
  const name = String(server || '').toLowerCase();
  let look = SERVER_LOOKS[name];
  if (!look) { const w = SERVER_WORDS.find(([re]) => re.test(name)); if (w) look = [w[1], w[2]]; }
  if (!look) { let h = 0; for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0; look = ['plug', SERVER_SPARE[h % SERVER_SPARE.length]]; }
  return { icon: look[0], color: `var(--srv-${look[1]})` };
}
function mcpParts(name) { const m = /^mcp__([A-Za-z0-9_-]+?)__(.+)$/.exec(name || ''); return m ? { server: m[1], tool: m[2] } : null; }
const ACTION_LABEL = { create: 'Create', edit: 'Edit', delete: 'Delete', picture: 'Save picture', document: 'Save document' };
// What an MCP call does, in words: a label and a short detail instead of its raw arguments (shown in full when opened).
const clip = (t, n = 70) => { t = String(t ?? '').replace(/\s+/g, ' ').trim(); return t.length > n ? t.slice(0, n - 1) + '…' : t; };
const fileOf = p => String(p || '').split(/[\\/]/).pop();
const MCP_WORDS = {
  'greek-check': { check_text: a => ['Check Greek', `«${clip(a.text, 60)}» · ${String(a.text || '').length} characters`] },
  'web-search': {
    searxng_web_search: a => ['Search the web', `«${clip(a.query, 90)}»` + (a.pageno > 1 ? ` · page ${a.pageno}` : '')],
    web_url_read: a => ['Read a web page', clip(a.url, 90)],
    searxng_search_suggestions: a => ['Search suggestions', `«${clip(a.query, 80)}»`],
    searxng_instance_info: () => ['Web search: about the search engine', ''] },
  fetch: { fetch: a => ['Read a web page', clip(a.url, 90)] },
  context7: {
    'resolve-library-id': a => ['Find library docs', `${a.libraryName || ''}` + (a.query ? ` · «${clip(a.query, 60)}»` : '')],
    'query-docs': a => ['Read library docs', `${a.libraryId || ''}` + (a.query ? ` · «${clip(a.query, 60)}»` : '')],
    'get-library-docs': a => ['Read library docs', `${a.context7CompatibleLibraryID || a.libraryId || ''}` + (a.topic ? ` · ${clip(a.topic, 60)}` : '')] },
  'tutoring-db': {
    execute_sql: a => ['Database: run SQL', clip(String(a.sql || '').replace(/\s+/g, ' '), 90)],
    list_schemas: () => ['Database: list schemas', ''],
    list_objects: a => ['Database: list tables', String(a.schema_name || '')],
    get_object_details: a => ['Database: describe', `${a.schema_name ? a.schema_name + '.' : ''}${a.object_name || ''}`],
    explain_query: a => ['Database: explain a query', clip(String(a.sql || '').replace(/\s+/g, ' '), 90)] },
  comfyui: {
    list_models: () => ['Pictures', 'list the models'],
    generate_image: a => ['Make a picture', clip(a.prompt) + (a.model ? ` · ${a.model}` : '')],
    generate_images: a => { const n = (a.pictures || a.prompts || []).length || a.count || 1; const first = (a.pictures && a.pictures[0] && a.pictures[0].prompt) || (a.prompts && a.prompts[0]) || a.prompt; return [`Make ${n} picture${n === 1 ? '' : 's'}`, clip(first)]; },
    get_image: a => ['Get a picture', fileOf(a.filename || a.name || a.id)],
    generate_video: a => ['Make a video clip', clip(a.prompt)],
    generate_music: a => ['Make music', `«${clip(a.style, 50)}»` + (a.seconds ? ` · ${a.seconds} s` : '') + (a.lyrics ? ' · with lyrics' : ' · instrumental') + (a.quality === 'best' ? ' · best' : '') + (a.path ? ` → ${fileOf(a.path)}` : '')] },
  sounds: {
    make_sound: a => Array.isArray(a.sounds)
      ? [`Make ${a.sounds.length} sound${a.sounds.length === 1 ? '' : 's'}`, a.sounds.map(x => fileOf(x.path)).join(', ')]
      : ['Make a sound', `«${clip(a.description, 50)}»` + (a.seconds ? ` · ${a.seconds} s` : '') + ` → ${fileOf(a.path)}`] },
  voices: {
    list_voices: () => ['Voices', 'list the cast'],
    speak: a => ['Read aloud', `«${clip(a.text, 50)}» as ${a.voice || 'a described voice'} → ${fileOf(a.path)}`] },
  video: {
    render_video: a => ['Render video', fileOf(a.output || a.out || a.path) || 'from a timeline'],
    probe_media: a => ['Measure media', fileOf(a.path)],
    video_frames: a => ['Look at frames', fileOf(a.path) + (a.times ? ` at ${[].concat(a.times).join(', ')} s` : '')] },
  audio: {
    audio_info: a => ['Measure sound', fileOf(a.path)],
    audio_waveform: a => ['Look at waveform', fileOf(a.path) + (a.a != null || a.b != null ? ` from ${a.a ?? 0} to ${a.b ?? 'end'}` : '')],
    edit_audio: a => { const ed = Array.isArray(a.edits) ? a.edits : []; return [`Edit sound${ed.length ? ` · ${ed.length} edit${ed.length === 1 ? '' : 's'}` : ''}`,
      fileOf(a.path) + (a.output ? ' → ' + fileOf(a.output) : '') + (ed.length ? ' · ' + ed.slice(0, 4).map(e => e.op === 'fx' ? e.name : e.op).join(', ') + (ed.length > 4 ? '…' : '') : '')]; },
    split_audio: a => [`Split sound`, fileOf(a.path) + (Array.isArray(a.cuts) ? ` at ${a.cuts.length} point${a.cuts.length === 1 ? '' : 's'}` : '')] },
  blender: {
    execute_blender_code: a => { const lines = String(a.code || '').split('\n').filter(l => l.trim() && !l.trim().startsWith('#')); return ['Blender: run Python', `${lines.length} line${lines.length === 1 ? '' : 's'} · ${clip(lines[0] || '', 50)}`]; },
    get_viewport_screenshot: () => ['Blender: look at the viewport', ''],
    get_scene_info: () => ['Blender: read the scene', ''],
    get_object_info: a => ['Blender: read an object', a.object_name || a.name || ''] },
  listmonk: {
    overview: () => ['listmonk', 'look at lists and campaigns'],
    create_list: a => ['listmonk: new list', a.name],
    find_contacts: a => ['listmonk: find contacts', [a.search && `«${clip(a.search, 40)}»`, a.list && `in ${[].concat(a.list).join(', ')}`].filter(Boolean).join(' ') || 'all'],
    add_contact: a => ['listmonk: add a contact', `${a.name ? a.name + ' ' : ''}<${a.email}>` + (a.lists ? ` → ${[].concat(a.lists).join(', ')}` : '')],
    update_contact: a => ['listmonk: change a contact', a.email],
    change_lists: a => [`listmonk: ${a.action || 'change'} ${(a.emails || []).length} contact${(a.emails || []).length === 1 ? '' : 's'}`, `${a.action === 'add' ? 'to' : 'from'} ${[].concat(a.lists || []).join(', ')}`],
    import_contacts: a => ['listmonk: import contacts', (a.path ? fileOf(a.path) : `${(a.rows || []).length} rows`) + ` → ${[].concat(a.lists || []).join(', ')}` + (/^(true|yes|1|on)$/i.test(String(a.overwrite ?? '')) ? ' · overwrite' : '')],
    import_status: () => ['listmonk: import status', ''],
    stop_import: () => ['listmonk: stop the import', ''],
    list_templates: () => ['listmonk', 'list the templates'],
    list_campaigns: () => ['listmonk', 'list the campaigns'],
    get_campaign: a => ['listmonk: read a campaign', String(a.campaign)],
    create_campaign: a => ['listmonk: draft a campaign', `«${clip(a.subject, 50)}» to ${[].concat(a.lists || []).join(', ')}`],
    update_campaign: a => ['listmonk: change a campaign', String(a.campaign)],
    copy_campaign: a => ['listmonk: copy a campaign', `${a.campaign} → ${[].concat(a.lists || []).join(', ')}`],
    list_tags: () => ['listmonk: list the tags', ''],
    tag_contacts: a => [`listmonk: ${a.action === 'remove' ? 'remove' : 'add'} a tag`, String(a.tag || [a.region, a.city].filter(Boolean).join(', '))],
    fill_tag_list: a => ['listmonk: fill a list from a tag', String(a.tag || [a.region, a.city].filter(Boolean).join(', '))],
    preview_campaign: a => ['listmonk: preview', String(a.campaign) + (a.save_to ? ` → ${fileOf(a.save_to)}` : '')] },
  browser: {
    browser_navigate: a => ['Browser: open', clip(a.url, 90)],
    browser_navigate_back: () => ['Browser: back', ''],
    browser_snapshot: () => ['Browser: read the page', ''],
    browser_take_screenshot: a => ['Browser: screenshot', a.fullPage ? 'whole page' : ''],
    browser_click: a => ['Browser: click', clip(a.element || a.ref, 70)],
    browser_hover: a => ['Browser: point at', clip(a.element || a.ref, 70)],
    browser_type: a => ['Browser: type', `«${clip(a.text, 50)}»` + (a.element ? ` into ${clip(a.element, 40)}` : '') + (a.submit ? ' and send' : '')],
    browser_fill_form: a => ['Browser: fill a form', `${(a.fields || []).length} fields`],
    browser_select_option: a => ['Browser: choose', `${[].concat(a.values || []).join(', ')} in ${clip(a.element, 40)}`],
    browser_press_key: a => ['Browser: press', String(a.key || '')],
    browser_wait_for: a => ['Browser: wait', a.text ? `for «${clip(a.text, 40)}»` : a.time ? `${a.time} s` : ''],
    browser_tabs: a => ['Browser: tabs', String(a.action || '')],
    browser_evaluate: () => ['Browser: run a script in the page', ''],
    browser_file_upload: a => ['Browser: upload', [].concat(a.paths || []).map(fileOf).join(', ')],
    browser_close: () => ['Browser: close the page', ''] },
  'listmonk-send': {
    send_test: a => ['Send a test email', `campaign ${a.campaign} to ${(a.emails || []).join(', ')}`],
    start_campaign: a => ['Send a campaign now', `campaign ${a.campaign} to ${a.recipients} contacts`],
    schedule_campaign: a => ['Schedule a campaign', `campaign ${a.campaign} at ${a.send_at} to ${a.recipients} contacts`],
    pause_campaign: a => ['Pause a campaign', String(a.campaign)],
    cancel_campaign: a => ['Cancel a campaign', String(a.campaign)],
    send_email: a => ['Send an email', `«${clip(a.subject, 50)}» to ${a.to}`],
    delete_contacts: a => ['DELETE contacts', `${a.count} contacts in ${[].concat(a.lists || []).join(', ')}` + (a.keep?.length ? `, keeping ${[].concat(a.keep).join(', ')}` : '')],
    restart_listmonk: a => ['Restart listmonk', clip(a.reason, 90)] },
};
function mcpWords(server, tool, args) {
  const a = parseArgs(args) || {}, known = MCP_WORDS[server] && MCP_WORDS[server][tool];
  if (known && typeof a === 'object') { try { return known(a); } catch { /* fall back */ } }
  const label = (server === 'blender' ? 'Blender: ' : server + ': ') + tool.replace(/_/g, ' ');
  if (typeof a !== 'object') return [label, clip(a)];
  const parts = Object.entries(a).filter(([k]) => k !== 'progress').map(([k, v]) =>
    `${k.replace(/_/g, ' ')}: ${typeof v === 'string' ? clip(/[\\/]/.test(v) && v.length > 30 ? fileOf(v) : v, 40) : Array.isArray(v) ? `${v.length} item${v.length === 1 ? '' : 's'}` : v && typeof v === 'object' ? '{…}' : v}`);
  return [label, parts.join(' · ')];
}
const EDIT_TOOLS = new Set(['write_file', 'replace_in_file', 'delete_file', 'write_document']);

/* A diff with line numbers. Everything goes in as text, because file contents and model output are untrusted. */
function renderDiff(change) {
  const box = el('div', { class: 'diff-view' });
  if (change.too_large) { box.append(el('div', { class: 'diff-note', text: 'This file is too large to show the changes here.' })); return box; }
  if (!change.rows || !change.rows.length) { box.append(el('div', { class: 'diff-note', text: 'No line changes to show.' })); return box; }
  const lines = el('div', { class: 'diff-lines' });
  for (const [kind, a, b, text] of change.rows) {
    if (kind === 'sep') { lines.append(el('div', { class: 'diff-row sep', text: '⋯' })); continue; }
    lines.append(el('div', { class: 'diff-row ' + kind }, el('span', { class: 'ln', text: a ?? '' }), el('span', { class: 'ln', text: b ?? '' }),
      el('span', { class: 'sg', text: kind === 'add' ? '+' : kind === 'del' ? '−' : ' ' }), el('span', { class: 'code', text: text || ' ' })));
  }
  box.append(lines);
  if (change.truncated) box.append(el('div', { class: 'diff-note', text: 'Only the first part of this change is shown.' }));
  return box;
}
function changeCounts(change) {
  return el('span', { class: 'counts' }, el('span', { class: 'a', text: '+' + (change.added || 0) }), el('span', { class: 'd', text: '−' + (change.removed || 0) }));
}
function toolRow(id, name, args) {
  const stat = el('span', { class: 't-stat' }, el('span', { class: 'spin' }));
  const sum = el('span', { class: 't-sum' });
  const out = el('pre', { class: 't-out' });
  const mcp = mcpParts(name), words = mcp ? mcpWords(mcp.server, mcp.tool, args) : null;
  const label = el('span', { class: 't-label', text: TOOL_LABEL[name] || userToolLabel(name) || (words ? words[0] : name || 'tool'), title: mcp ? `${mcp.tool}, from the tool server ${mcp.server}` : '' });
  const row = el('div', { class: 'row', onclick: () => { if (name === 'run' || node.classList.contains('has-change')) setCommandOpen(node, !node.classList.contains('open')); else if (node.classList.contains('has-out')) node.classList.toggle('expanded'); } },
    stat, el('span', { class: 't-name' }, el('span', { html: ico(TOOL_ICO[name] || (mcp ? serverLook(mcp.server).icon : 'sparkles'), 13) }), label),
    el('span', { class: 't-args' + (words ? ' words' : ''), text: words ? words[1] : shortArgs(name, args), title: typeof args === 'string' ? args : JSON.stringify(args) }), sum);
  const node = el('div', { class: 'm-tool running' + (EDIT_TOOLS.has(name) || name === 'run' ? ' action' : '') + (mcp ? ' mcp' : '') }, row);
  if (mcp) { const look = serverLook(mcp.server); node.style.setProperty('--srv', look.color); node.dataset.server = mcp.server; }
  const parsed = parseArgs(args);
  if (mcp && parsed && typeof parsed === 'object') {      // the call's own arguments, readable, for when the row is opened
    const shown = Object.fromEntries(Object.entries(parsed).filter(([k]) => k !== 'progress'));
    if (Object.keys(shown).length) node.append(el('pre', { class: 't-in', text: JSON.stringify(shown, null, 2) }));
  }
  if (name === 'run') {
    const expand = el('button', { class: 'link-btn card-link expand', text: 'Expand', onclick: e => { e.stopPropagation(); setCommandOpen(node, !node.classList.contains('open')); } });
    row.append(expand);
    node.append(el('div', { class: 'cmd-peek', 'data-started': String(Date.now()), text: 'Running…' }));
    if (parsed && parsed.command) node.append(el('pre', { class: 'cmd-line', text: parsed.command }));
  }
  node.append(out);
  node.dataset.id = id; node.dataset.name = name || ''; node._parts = { stat, sum, out, label, row };
  return node;
}
setInterval(() => {
  for (const peek of document.querySelectorAll('.cmd-peek[data-started]')) peek.textContent = 'Running… ' + fmtElapsed(Date.now() - +peek.dataset.started);
}, 1000);
function setCommandOpen(node, open) {
  node.classList.toggle('open', open);
  node.querySelector('.expand').textContent = open ? 'Collapse' : 'Expand';
}
function runOutput(node, d) {
  const { sum } = node._parts;
  const text = String(d.output || '').replace(/^exit code -?\d+[^\n]*\n?/, '').replace(/\n$/, '');      // the first line also says when a part of a pipe failed
  const peek = node.querySelector('.cmd-peek');
  peek.removeAttribute('data-started');
  peek.textContent = text.split('\n').map(line => line.trim()).filter(Boolean).pop() || 'No output';
  peek.title = peek.textContent;
  if (!text) return;
  const count = text.split('\n').length;
  sum.textContent = `${count} line${count === 1 ? '' : 's'} · ${d.summary || ''}`;
  node.append(el('pre', { class: 'out-preview', text }));
}
function toolResult(node, d) {
  const { stat, sum, out } = node._parts;
  node.classList.remove('running'); node.classList.add(d.ok === false ? 'fail' : 'ok');
  stat.innerHTML = d.ok === false ? ico('x', 14) : ico('check', 14);
  sum.textContent = d.summary || ''; if (d.summary) sum.title = d.summary;
  const mcp = mcpParts(node.dataset.name);
  if (mcp && d.summary && d.summary.startsWith(`${mcp.server} · ${mcp.tool}`)) sum.textContent = d.summary.endsWith('failed') ? 'failed' : '';   // it only repeated the name
  if (node.dataset.name === 'run') { runOutput(node, d); return; }
  if (node.classList.contains('has-change')) { sum.textContent = ''; return; }
  if (d.output) { out.textContent = d.output; node.classList.add('has-out'); }
  else if (node.querySelector('.t-in')) node.classList.add('has-out');      // its arguments can still be opened
}
function fileChangeCard(d, node) {
  // The edit's tool card gets the diff. A change with no card, as from an older server, gets a card of its own.
  if (!node) { node = toolRow('change-' + d.checkpoint, 'replace_in_file', { path: d.path }); node.classList.remove('running'); node.classList.add('ok'); node._parts.stat.innerHTML = ico('check', 14); addBlock(node); }
  const { label, row } = node._parts, change = d.change;
  node.classList.add('has-change');
  label.textContent = ACTION_LABEL[d.action] || label.textContent;
  row.querySelector('.t-args').textContent = '';
  row.querySelector('.t-args').append(el('button', { class: 'path-link', text: d.path, title: d.action === 'delete' ? d.path : 'Open ' + d.path, onclick: e => { e.stopPropagation(); if (d.action !== 'delete') openFile(d.path); } }));
  row.insertBefore(changeCounts(change), node._parts.sum);
  row.append(el('button', { class: 'link-btn card-link expand', text: 'Expand', onclick: e => { e.stopPropagation(); setCommandOpen(node, !node.classList.contains('open')); } }));
  const view = renderDiff(change);
  const large = el('button', { class: 'link-btn card-link', text: 'Full view', onclick: () => modal(d.path, renderDiff(change), { wide: true }) });
  const undo = el('button', { class: 'link-btn card-link', text: 'Undo', title: 'Restore the file to how it was before this change', onclick: async () => {
    undo.disabled = true;
    try {
      const r = await POST('/api/agent/undo', { ws: S.ws.id, checkpoint: d.checkpoint });
      undo.textContent = 'Undone';
      const rest = r.restored || []; toast(`Restored ${rest.length} file${rest.length === 1 ? '' : 's'}`, 'success', 3000);
      for (const p of rest.length ? rest : [d.path]) onDiskChanged(p);
    } catch (e) { undo.disabled = false; errToast(e, 'Undo failed'); }
  } });
  if (d.copy) { node.append(el('div', { class: 'card-bar' }, el('span', { class: 'grow copy-note', text: 'In the Time-skip copy' }), large), view); return; }
  A.undos = A.undos || []; A.undos.push(undo);
  node.append(el('div', { class: 'card-bar' }, el('span', { class: 'grow' }), undo, large), view);
}

function fullMemory() {
  return el('div', { class: 'm-full m-continue' }, el('div', { class: 'why', text: `The model's memory of this chat is ${Math.round(A.ctxUsed / A.ctxCap * 100)}% full, so it will soon start to forget or fail.` }),
    el('div', { class: 'plan-acts' }, el('button', { class: 'btn sm primary', onclick: () => compactChat() }, 'Summarize and continue'), el('button', { class: 'btn sm', onclick: () => newChat() }, 'New chat')));
}
async function compactChat() {
  if (A.running) { note('Use /stop first: the agent is working.'); return; }
  if (!A.session) { note('There is no chat to summarize yet.'); return; }
  for (const b of msgsEl.querySelectorAll('.m-continue')) b.remove();
  try {
    const r = await POST('/api/agent/compact', { ws: S.ws.id, session: A.session, model: S.model });
    A.turn = 0; A.pTok = 0; A.cTok = 0; A.statusText = 'Summarizing the earlier messages…'; A.lastReason = ''; A.t0 = Date.now(); A.tEnd = 0; A.pending = 0;
    attachRun(r.run, 0); pendingGhost(false);
  } catch (e) { if (e.status === 423) { toast(e.message, 'warn'); roomToggle(true); } else errToast(e, 'Could not summarize'); }
}
/* Reads, searches and listings in a row fold into one quiet line; edits, commands, failures and questions stay in view. */
const FOLDED = { read_file: ['read', 'file', 'files'], list_files: ['listed', 'folder', 'folders'], search: ['searched', 'time', 'times'], use_skill: ['used', 'skill', 'skills'] };
function foldable(n) { return n && n.matches('.m-tool.ok') && FOLDED[n.dataset.name]; }
function foldActivity(node) {
  if (!foldable(node)) return;
  const between = [];
  let prev = node.previousElementSibling;
  while (prev && prev.matches('.m-think:not(.live)')) { between.unshift(prev); prev = prev.previousElementSibling; }
  let group = prev && prev.matches('.m-activity') ? prev : null;
  if (!group) {
    if (!foldable(prev)) return;
    group = el('details', { class: 'm-activity' }, el('summary', {}, el('span', { class: 'chev', html: ico('chevron-right', 12) }), el('span', { class: 'act-text' })), el('div', { class: 'act-body' }));
    prev.before(group); group.lastChild.append(prev);
  }
  group.lastChild.append(...between, node);
  const counts = {};
  for (const n of group.lastChild.querySelectorAll(':scope > .m-tool')) counts[n.dataset.name] = (counts[n.dataset.name] || 0) + 1;
  const text = Object.entries(counts).map(([name, n]) => { const [verb, one, many] = FOLDED[name]; return name === 'search' ? (n === 1 ? 'searched once' : `searched ${n} times`) : `${verb} ${n} ${n === 1 ? one : many}`; }).join(', ');
  group.querySelector('.act-text').textContent = text.charAt(0).toUpperCase() + text.slice(1);
}
/* The files a run created, changed or deleted, its commands' files included. */
function filesCard(d) {
  const files = d.files || [], more = (d.total || files.length) - files.length;
  const mark = { new: 'New', changed: 'Changed', deleted: 'Deleted' };
  const rows = files.map(f => el('div', { class: 'f-row ' + f.status },
    el('span', { class: 'f-st', text: mark[f.status] || f.status }),
    f.status === 'deleted' ? el('span', { class: 'f-path', text: f.path, title: f.path }) : el('button', { class: 'path-link f-path', text: f.path, title: 'Open ' + f.path, onclick: () => openFile(f.path) }),
    f.added || f.removed ? changeCounts(f) : null,
    f.status === 'deleted' ? null : el('button', { class: 'link-btn card-link', text: '@', title: 'Mention this file in your next message', 'aria-label': 'Mention ' + f.path, onclick: () => { const i = $('#agent-input'); i.value = (i.value && !/\s$/.test(i.value) ? i.value + ' ' : i.value) + '@' + f.path + ' '; autosize(); i.focus(); } })));
  const n = d.total || files.length;
  return el('div', { class: 'm-files' }, el('div', { class: 'f-head' }, el('span', { html: ico('files', 13) }), el('span', { text: `${n} file${n === 1 ? '' : 's'} changed in this run` })),
    ...rows, more > 0 ? el('div', { class: 'f-more', text: `and ${more} more` }) : null);
}
/* The Goal button and window: the goal, how to tell it is reached, what to keep in mind, and how many rounds it may take.
   It sends the goal as a message (like /goal), or, when the chat has one, edits, pauses, resumes or clears it. */
function goalCompose(p) {
  return [p.goal.trim(), p.done.trim() && 'Done when:\n' + p.done.trim(), p.limits.trim() && 'Keep in mind:\n' + p.limits.trim()].filter(Boolean).join('\n\n');
}
function goalChip() {
  const b = $('#goal-chip'); if (!b) return;
  const g = A.goal, label = !g ? 'Goal' : { active: 'Goal: working', paused: 'Goal: paused', blocked: 'Goal: needs you', done: 'Goal: reached' }[g.state] || 'Goal';
  b.querySelector('span').textContent = label;
  b.classList.toggle('on', !!g); b.dataset.state = g ? g.state : '';
  b.setAttribute('aria-pressed', g ? 'true' : 'false');
}
const GOAL_MAX = 16000;           // the server's MAX_GOAL_CHARS
async function openGoal() {
  if (!S.ws) { toast('Open a folder first', 'warn'); return; }
  const g = A.goal, parts = (g && g.parts) || (g ? { goal: g.text } : {});
  const field = (label, hint, node) => el('label', { class: 'goal-field' }, el('span', { class: 'goal-label' }, label, hint ? el('small', { text: ' ' + hint }) : null), node);
  const goal = el('textarea', { class: 'dlg-input goal-brief', rows: 8, maxlength: '10000', placeholder: 'What should be true when the agent is finished? e.g. "The game works on phones, with sound, and every test passes."' });
  const done = el('textarea', { class: 'dlg-input', rows: 4, maxlength: '3000', placeholder: 'How to check it, one per line, e.g.\n- npm test passes\n- the page has no console errors at 390 px wide' });
  const limits = el('textarea', { class: 'dlg-input', rows: 4, maxlength: '3000', placeholder: 'Optional: what not to touch, style, language, files to read first…' });
  goal.value = parts.goal || ''; done.value = parts.done || ''; limits.value = parts.limits || '';
  const rounds = el('input', { class: 'dlg-input goal-rounds', type: 'number', min: '1', max: '100', value: String((g && g.max_rounds) || (S.config && S.config.goal_max_rounds) || 20) });
  // Codex models: an allowance, so a long goal does not use up the plan (new tokens, and a ceiling on the 5-hour window).
  const cxModel = (S.models.find(x => x.name === S.model) || {}).kind === 'codex';
  const cxTokens = el('input', { class: 'dlg-input goal-rounds goal-cx-tokens', type: 'number', min: '1', max: '100000', placeholder: 'no limit',
    value: g && g.codex_tokens ? String(Math.round(g.codex_tokens / 1000)) : '' });
  const cxPct = el('input', { class: 'dlg-input goal-rounds goal-cx-pct', type: 'number', min: '1', max: '100', placeholder: '—', value: g && g.codex_pct ? String(g.codex_pct) : '' });
  const cxRow = cxModel ? el('div', { class: 'goal-row goal-cx' },
    el('label', { class: 'goal-field goal-inline' }, el('span', { class: 'goal-label' }, 'Codex allowance'), cxTokens, el('span', { text: 'thousand new tokens' })),
    el('label', { class: 'goal-field goal-inline' }, el('span', { text: 'stop at' }), cxPct, el('span', { text: '% of the 5-hour limit' })),
    g && g.codex_tokens ? el('small', { class: 'goal-count', text: `used ${Math.round((g.codex_used || 0) / 1000).toLocaleString()}K` }) : null) : null;
  const count = el('small', { class: 'goal-count' });
  const total = () => { const n = goalCompose({ goal: goal.value, done: done.value, limits: limits.value }).length; count.textContent = `${n} / ${GOAL_MAX} characters`; count.classList.toggle('warn-text', n > GOAL_MAX); };
  for (const t of [goal, done, limits]) t.addEventListener('input', total);
  total();
  const auto = S.autoApply && S.autoCmd;
  const state = g ? el('div', { class: 'goal-now ' + g.state }, el('span', { html: ico('flag', 13) }),
    el('span', {}, el('b', { text: { active: 'Working on it', paused: 'Paused', blocked: 'Needs you', done: 'Reached' }[g.state] || g.state }),
      g.rounds ? ` · round ${g.rounds} of ${g.max_rounds || (S.config && S.config.goal_max_rounds) || 20}` : '', g.why ? ' · ' + g.why : '')) : null;
  const body = el('div', { class: 'dlg-form goal-form' }, state,
    field('Goal', '(required)', goal), field('Done when', '(the agent checks these before it says it is done)', done),
    field('Keep in mind', null, limits),
    el('div', { class: 'goal-row' }, el('label', { class: 'goal-field goal-inline' }, el('span', { class: 'goal-label' }, 'At most'), rounds, el('span', { text: 'rounds' })), count),
    cxRow, cxRow ? el('small', { class: 'dlg-note', text: 'New tokens are what Codex writes plus what it reads that is not from its cache. When either runs out, Codex is asked to stop at a good point (and stopped if it carries on), and the goal pauses; Resume grants it again. Leave a box empty for no limit.' }) : null,
    el('small', { class: 'dlg-note' + (auto ? '' : ' warn-text') }, auto
      ? 'The agent works in rounds until it calls the goal reached, needs you, or uses up its rounds. Auto-apply and Auto-run are on, so it can carry on without you.'
      : 'The agent works in rounds until the goal is reached. Auto-apply edits or Auto-run commands is off, so it will stop and wait for each approval.'));
  const running = A.running && !g;
  const buttons = [{ label: 'Cancel', value: false }];
  if (g) {
    buttons.unshift({ label: 'Clear goal', value: 'clear', kind: 'danger' });
    if (g.state === 'active') buttons.push({ label: 'Pause', value: 'pause' });
    else if (g.state !== 'done') buttons.push({ label: 'Resume', value: 'resume' });
    buttons.push({ label: 'Save changes', value: 'save', kind: 'primary' });
  } else buttons.push({ label: running ? 'Wait for the run to end' : 'Start working on it', value: 'start', kind: 'primary' });
  const shown = dialog({ title: g ? 'This chat\'s goal' : 'Set a goal', body, small: false, buttons });
  setTimeout(() => goal.focus(), 50);
  const choice = await shown;
  if (!choice) return;
  if (choice === 'clear' || choice === 'pause') return goalAction(choice);
  const p = { goal: goal.value, done: done.value, limits: limits.value }, text = goalCompose(p), n = parseInt(rounds.value, 10);
  if (!p.goal.trim()) { toast('Write the goal first', 'warn'); return openGoal(); }
  if (text.length > GOAL_MAX) { toast(`The goal is too long: at most ${GOAL_MAX} characters in all`, 'warn'); return; }
  const kTok = parseInt(cxTokens.value, 10), kPct = parseInt(cxPct.value, 10);
  const extra = { goal_parts: { goal: p.goal.trim(), done: p.done.trim(), limits: p.limits.trim() }, ...(n >= 1 && n <= 100 ? { goal_rounds: n } : {}),
    ...(cxModel && kTok >= 1 && kTok <= 100000 ? { goal_codex_tokens: kTok } : {}), ...(cxModel && kPct >= 1 && kPct <= 100 ? { goal_codex_pct: kPct } : {}) };
  if (choice === 'start') {
    if (A.running) { toast('Wait for the run to end, or stop it, then set the goal', 'warn'); return; }
    A.goalOnce = true; A.goalExtra = extra; sendText(text); return;
  }
  if (choice === 'save' || choice === 'resume') {
    const changed = text !== g.text || n !== g.max_rounds || (cxModel && ((extra.goal_codex_tokens || 0) * 1000 !== (g.codex_tokens || 0) || (extra.goal_codex_pct || 0) !== (g.codex_pct || 0)));
    if (changed) await goalAction('edit', text, extra);
    if (choice === 'resume') await goalAction('resume');
  }
}
$('#goal-chip').onclick = openGoal;
/* /goal: a bar above the box while the chat has a goal */
async function goalAction(action, text, extra) {
  try {
    const r = await POST('/api/agent/goal', { ws: S.ws.id, id: A.session, action, ...(text ? { text } : {}), ...(extra || {}) });
    A.goal = r.goal; renderGoal();
    if (action === 'resume' && !A.running) sendText('Carry on with the goal.');
  } catch (e) { errToast(e, 'Goal'); }
}
function renderGoal() {
  goalChip();
  const bar = $('#goal-bar'), g = A.goal;
  bar.classList.toggle('hidden', !g);
  if (!g) { bar.replaceChildren(); return; }
  const label = { active: g.rounds ? `Working · round ${g.rounds + 1}` : 'Working', paused: 'Paused', done: 'Reached', blocked: 'Needs you' }[g.state] || g.state;
  bar.className = 'goal-bar ' + g.state;
  const btn = (text, title, run) => el('button', { class: 'q-btn goal-btn', title, onclick: run }, text);
  bar.replaceChildren(...[el('span', { class: 'goal-ico', html: ico('flag', 13) }),
    el('div', { class: 'goal-main' }, el('div', { class: 'goal-text', text: g.text, title: g.text }), el('div', { class: 'goal-state', text: label + (g.why ? ' · ' + g.why : '') })),
    g.state === 'active' ? btn('Pause', 'Stop after the current round', () => goalAction('pause')) : g.state !== 'done' ? btn('Resume', 'Carry on with the goal', () => goalAction('resume')) : null,
    btn('Edit', 'Change the goal', openGoal),
    btn('Clear', 'Remove the goal from this chat', () => goalAction('clear'))].filter(Boolean));
}
/* Time-skip: what the copy changed, and the tests on both versions; keep the update or throw it away */
function timeskipCard(d) {
  const card = el('div', { class: 'm-timeskip', 'data-id': d.id });
  const head = el('div', { class: 'ts-head' }, el('span', { html: ico('layers', 14) }), el('b', { text: 'Time-skip' }));
  card.append(head);
  if (d.state === 'failed') { card.append(el('div', { class: 'why', text: 'Time-skip could not compare the versions: ' + (d.error || 'unknown error') })); return card; }
  if (d.state === 'nothing') { head.append(el('span', { text: ' · the copy is the same as your folder, so there is nothing to keep.' })); return card; }
  head.append(el('span', { text: ` · ${d.total} file${d.total === 1 ? '' : 's'} differ from your folder` }));
  if (d.checks) {
    const res = r => el('span', { class: 'ts-res ' + (r.ok ? 'ok' : 'bad'), text: r.ok ? 'passed' : 'failed' });
    const out = (label, r) => el('details', {}, el('summary', { text: label + ' output' }), el('pre', { text: r.output || '' }));
    card.append(el('div', { class: 'ts-checks' }, el('div', {}, 'Tests (', el('code', { text: d.checks.command }), ')'),
      el('div', { class: 'ts-pair' }, el('span', { text: 'Your folder: ' }), res(d.checks.original), el('span', { text: '  ·  The update: ' }), res(d.checks.copy)),
      out('Your folder', d.checks.original), out('The update', d.checks.copy)));
  } else card.append(el('div', { class: 'why', text: 'No tests were run, so compare the changes yourself.' }));
  const list = el('div', { class: 'ts-files' }, (d.files || []).map(f => {
    const row = el('details', { class: 'ts-file ' + f.status }, el('summary', {}, el('span', { class: 'f-st', text: { new: 'New', changed: 'Changed', deleted: 'Deleted' }[f.status] }),
      el('span', { class: 'f-path', text: f.path }), f.change ? changeCounts(f.change) : el('small', { text: f.binary ? 'not text' : '' })));
    if (f.change) row.addEventListener('toggle', () => { if (row.open && row.childElementCount === 1) row.append(renderDiff(f.change)); });
    return row;
  }), d.total > (d.files || []).length ? el('div', { class: 'f-more', text: `and ${d.total - d.files.length} more` }) : null);
  card.append(list);
  const decide = async (action, force = false) => {
    if (action === 'discard' && !(await confirmBox('Throw away the update', 'The copy and everything the agent changed in it are deleted. Your folder stays as it is.', 'Throw away', true))) return;
    acts.querySelectorAll('button').forEach(b => b.disabled = true);
    try {
      const r = await POST('/api/timeskip/decide', { id: d.id, action, force });
      timeskipDecided(card, { id: d.id, ...r });
      for (const p of r.files || []) onDiskChanged(p);
      refreshTreeSoon(); gitRefresh();
    } catch (e) {
      acts.querySelectorAll('button').forEach(b => b.disabled = false);
      if (e.status === 409 && /changed in your folder/.test(e.message) && await confirmBox('Your folder changed', e.message + ' Keep the update anyway?', 'Keep anyway', true)) decide(action, true);
      else errToast(e, 'Time-skip');
    }
  };
  const acts = el('div', { class: 'plan-acts ts-acts' }, el('button', { class: 'btn sm primary', onclick: () => decide('keep') }, 'Keep the update'),
    el('button', { class: 'btn sm danger', onclick: () => decide('discard') }, 'Throw it away'));
  card.append(acts);
  return card;
}
function timeskipDecided(card, d) {
  const acts = card.querySelector('.ts-acts'); if (!acts) return;
  if (d.action === 'discard') { acts.replaceWith(el('div', { class: 'ts-done', text: 'Thrown away. Your folder is as it was.' })); return; }
  const done = el('div', { class: 'ts-done ok' }, el('span', { text: `Kept: ${d.files?.length || 0} file${d.files?.length === 1 ? '' : 's'} updated in your folder.` }));
  if (d.checkpoint) {
    const undo = el('button', { class: 'link-btn card-link', text: 'Undo', onclick: async () => {
      undo.disabled = true;
      try { const r = await POST('/api/agent/undo', { ws: S.ws.id, checkpoint: d.checkpoint }); undo.textContent = 'Undone'; for (const p of r.restored || []) onDiskChanged(p); refreshTreeSoon(); toast('Your folder is back as it was', 'success', 3000); }
      catch (e) { undo.disabled = false; errToast(e, 'Undo failed'); }
    } });
    done.append(undo);
  }
  acts.replaceWith(done);
}
function continueButton() {
  return el('button', { class: 'btn sm m-continue', onclick: () => sendText('Continue from where you stopped.') }, 'Continue');
}
// A / command that sends a message runs while the box is still busy with the command, so the message goes right after it.
function sendText(text, tries = 0) {
  if (A.sendBusy && tries < 100) { setTimeout(() => sendText(text, tries + 1), 30); return; }
  const i = $('#agent-input'); i.value = text; autosize(); agentSend();
}
function hunkPicker(a) {
  const boxes = a.data.hunks.map((h, i) => {
    const box = el('input', { type: 'checkbox', checked: true });
    box.dataset.part = i; box.onchange = () => { accept.disabled = !boxes.some(b => b.box.checked); };
    return { box, row: el('label', { class: 'hunk' }, box, el('div', { class: 'hunk-body' }, el('div', { class: 'hunk-line', text: 'Line ' + h.line }),
      h.old ? el('pre', { class: 'del-lines', text: h.old.replace(/\n$/, '') }) : null, h.new ? el('pre', { class: 'add-lines', text: h.new.replace(/\n$/, '') }) : null)) };
  });
  const accept = el('button', { class: 'btn sm primary', onclick: () => agentDecide(a.id, 'accept', boxes.filter(b => b.box.checked).map(b => +b.box.dataset.part)) }, 'Accept selected');
  return el('div', { class: 'hunk-list' }, boxes.map(b => b.row), el('div', { class: 'hunk-acts' }, accept));
}
/* The agent's questions: choices to pick (one, or several where it allows), or an answer in the user's own words. */
function questionCard(d) {
  const a = { id: d.approval_id, data: d, resolved: false, kind: 'question' };
  const qs = (d.questions || []).map((q, qi) => {
    const name = `q-${d.approval_id}-${qi}`;
    const inputs = q.options.map(o => el('input', { type: q.multi ? 'checkbox' : 'radio', name, value: o.label }));
    const own = el('input', { type: 'text', class: 'q-own', placeholder: 'Or type your own answer', maxlength: '2000', 'aria-label': 'Your own answer' });
    const box = el('fieldset', { class: 'q-item' }, el('legend', {}, q.header ? el('span', { class: 'q-head', text: q.header }) : null, el('span', { class: 'q-text', text: q.question })),
      ...q.options.map((o, oi) => el('label', { class: 'q-opt' }, inputs[oi], el('span', { class: 'q-label' }, el('b', { text: o.label }), o.description ? el('small', { text: o.description }) : null))),
      own, q.multi ? el('small', { class: 'q-hint', text: 'You can pick more than one.' }) : null);
    // Typing your own answer to a one-choice question replaces the choice.
    own.addEventListener('input', () => { if (!q.multi && own.value.trim()) inputs.forEach(i => { i.checked = false; }); update(); });
    // A single choice moves on to the next question by itself; several choices wait for Next.
    inputs.forEach(i => i.addEventListener('change', () => { if (!q.multi && i.checked) { own.value = ''; if (qi < qs.length - 1) setTimeout(() => go(qi + 1), 280); } update(); }));
    return { q, inputs, own, box };
  });
  const answers = () => qs.map(x => ({ choices: x.inputs.filter(i => i.checked).map(i => i.value), text: x.own.value.trim() }));
  const send = el('button', { class: 'btn sm primary', 'data-act': 'accept', disabled: '', onclick: () => agentDecide(a.id, 'accept', null, answers()) }, 'Send answer');
  // Several questions are a stepper: one at a time, swiped or scrolled sideways, with Back, Next and a dot per question.
  const many = qs.length > 1;
  let at = 0;
  const track = el('div', { class: 'q-list' + (many ? ' q-track' : '') }, qs.map(x => x.box));
  const dots = many ? el('div', { class: 'q-dots', role: 'tablist', 'aria-label': 'Questions' }, qs.map((x, i) => el('button', { class: 'q-dot', role: 'tab', 'aria-label': `Question ${i + 1}`, onclick: () => go(i) }))) : null;
  const where = many ? el('span', { class: 'q-where' }) : null;
  const back = many ? el('button', { class: 'btn sm', onclick: () => go(at - 1) }, 'Back') : null;
  const next = many ? el('button', { class: 'btn sm', onclick: () => go(at + 1) }, 'Next') : null;
  function go(i) {
    if (!many) return;
    at = Math.max(0, Math.min(qs.length - 1, i));
    track.scrollTo({ left: track.clientWidth * at, behavior: 'smooth' });
    paint();
  }
  function paint() {
    if (!many) return;
    const done = answers().map(x => !!(x.choices.length || x.text));
    [...dots.children].forEach((d, i) => { d.classList.toggle('now', i === at); d.classList.toggle('done', done[i]); d.setAttribute('aria-selected', String(i === at)); });
    where.textContent = `Question ${at + 1} of ${qs.length}`;
    back.disabled = at === 0; next.disabled = at === qs.length - 1;
    qs.forEach((x, i) => { x.box.inert = i !== at; });        // only the shown question takes the keyboard
  }
  // Swiping or scrolling the track moves to the question that settles in view.
  track.addEventListener('scroll', () => { clearTimeout(track._t); track._t = setTimeout(() => { const i = Math.round(track.scrollLeft / Math.max(1, track.clientWidth)); if (i !== at) { at = i; paint(); } }, 90); });
  function update() { send.disabled = !answers().every(x => x.choices.length || x.text); paint(); }
  const acts = el('div', { class: 'acts' }, many ? el('div', { class: 'q-nav' }, back, dots, next) : null,
    el('button', { class: 'btn sm', 'data-act': 'reject', title: 'Let the agent carry on with its own judgment', onclick: () => agentDecide(a.id, 'reject') }, 'Skip'), send);
  a.view = track;
  a.card = el('div', { class: 'm-appr m-question pending' }, el('div', { class: 'ah' }, el('span', { class: 'kind', text: 'Question' }),
    el('span', { class: 'p', text: many ? `The agent has ${qs.length} questions for you` : 'The agent asks you' }), where), a.view, acts);
  a.acts = acts;
  paint();
  return a;
}
function answeredText(questions, answers) {
  return (answers || []).map((x, i) => (questions.length > 1 ? `${i + 1}. ` : '') + [...x.choices, ...(x.text ? ['“' + x.text + '”'] : [])].join(', ')).join('\n');
}
function approvalCard(d, live) {
  if (d.kind === 'question') return questionCard(d);
  const isSkill = d.kind === 'skill', isMcp = /^mcp/.test(d.kind || ''), isEdit = d.kind !== 'command' && !isSkill && !isMcp;
  const a = { id: d.approval_id, data: d, resolved: false, kind: isSkill ? 'skill' : isEdit ? 'edit' : 'command' };
  const deleting = d.action === 'delete', picture = d.action === 'picture', document = d.action === 'document';
  const head = el('div', { class: 'ah' }, el('span', { class: 'kind', text: isSkill ? 'Skill' : isMcp ? 'Tool' : deleting ? 'Delete' : picture ? 'Picture' : document ? 'Document' : isEdit ? 'Edit' : 'Command' }));
  const acts = el('div', { class: 'acts' });
  const card = el('div', { class: 'm-appr pending' + (isSkill ? ' skill' : '') }, head);
  if (d.warning) card.append(el('div', { class: 'appr-warn', text: d.warning }));
  if (isSkill) {
    head.append(el('span', { class: 'p', text: `Save a ${d.skill_kind} skill: ${d.name}`, title: d.name }));
    a.view = el('div', {}, el('div', { class: 'skill-desc', text: d.description }), el('pre', { class: 'skill-steps', text: d.instructions })); card.append(a.view);
    acts.append(el('button', { class: 'btn sm danger', 'data-act': 'reject', onclick: () => agentDecide(a.id, 'reject') }, 'Reject'),
      el('button', { class: 'btn sm primary', 'data-act': 'accept', onclick: () => agentDecide(a.id, 'accept') }, 'Save skill'));
  } else if (isEdit) {
    const c = diffCounts(d.old, d.new); a.counts = c;
    head.append(el('span', { class: 'p', text: d.path, title: d.path }), el('span', { class: 'counts' }, el('span', { class: 'a', text: '+' + c.added }), el('span', { class: 'd', text: '−' + c.removed })));
    if (picture && d.picture) { a.view = el('div', { class: 'user-pics appr-pic' }, el('img', { src: `/api/agent/attachment?session=${encodeURIComponent(A.session)}&id=${encodeURIComponent(d.picture)}`, alt: 'The picture to save', 'data-name': d.path })); card.append(a.view); }
    else if (d.change) { a.view = renderDiff(d.change); card.append(a.view); }
    if (d.hunks) acts.append(el('button', { class: 'btn sm', onclick: e => { e.currentTarget.disabled = true; acts.before(hunkPicker(a)); scrollMaybe(); } }, 'Choose parts'));
    acts.append(el('button', { class: 'btn sm', onclick: () => openDiffTab({ ...d, counts: c }) }, 'Open diff'),
      el('button', { class: 'btn sm danger', 'data-act': 'reject', onclick: () => agentDecide(a.id, 'reject') }, 'Reject'),
      el('button', { class: 'btn sm primary', 'data-act': 'accept', onclick: () => agentDecide(a.id, 'accept') }, deleting ? 'Delete file' : picture ? 'Save picture' : document ? 'Save document' : 'Accept'));
  } else if (isMcp) {
    const look = serverLook(d.server); card.style.setProperty('--srv', look.color); card.classList.add('mcp-appr');
    head.querySelector('.kind').prepend(el('span', { class: 'srv-ico', html: ico(look.icon, 12) }));
    head.append(el('span', { class: 'p', text: `${d.server} · ${d.tool}`, title: `The MCP server ${d.server} runs this outside the project and the sandbox` }));
    a.view = el('div', { class: 'cmd mcp-args', text: d.arguments || '{}' }); card.append(a.view);
    acts.append(el('button', { class: 'btn sm', 'data-act': 'reject', onclick: () => agentDecide(a.id, 'reject') }, 'Skip'),
      el('button', { class: 'btn sm primary', 'data-act': 'accept', onclick: () => agentDecide(a.id, 'accept') }, 'Allow'));
  } else {
    head.append(el('span', { class: 'p', text: d.purpose === 'check' ? 'Run the project\'s tests' : d.purpose === 'timeskip' ? 'Run the tests on the copy and on your folder' : d.purpose === 'user_tool' ? 'Run your tool user__' + d.tool : 'Run shell command' }));
    card.append(el('div', { class: 'cmd', text: d.command || '' }));
    acts.append(el('button', { class: 'btn sm', 'data-act': 'reject', onclick: () => agentDecide(a.id, 'reject') }, 'Skip'),
      el('button', { class: 'btn sm primary', 'data-act': 'accept', onclick: () => agentDecide(a.id, 'accept') }, 'Run'));
  }
  card.append(acts); a.card = card; a.acts = acts;
  return a;
}
function resolveApprovalUI(id, decision, parts, answers) {
  const a = A.approvals.get(id); if (!a) return;
  a.resolved = true; const c = a.card;
  c.classList.remove('pending'); c.classList.add('resolved');
  const edit = a.kind === 'edit', acc = decision === 'accept';
  if (decision) c.classList.add(acc ? 'accepted' : 'rejected');
  a.acts.replaceChildren(); c.querySelector('.hunk-list')?.remove();
  if (a.kind === 'question') {          // the questions stay, with what the user answered
    a.view.replaceWith(el('div', { class: 'q-done' }, (a.data.questions || []).map((q, i) => el('div', { class: 'q-answered' }, el('span', { class: 'q-text', text: q.question }),
      el('b', { text: acc && answers?.[i] ? answeredText([q], [answers[i]]) : (decision ? 'Skipped' : 'Not answered') })))));
    a.acts.append(el('span', { class: 'res', text: acc ? '✓ Answered' : decision ? 'Skipped: the agent decides' : 'No answer recorded' }));
    return;
  }
  a.view?.remove();          // the action's own card shows the result
  if (edit) a.acts.append(el('button', { class: 'btn sm ghost', onclick: () => openDiffTab({ ...a.data, counts: a.counts, resolved: true }) }, 'Open diff'));
  const txt = !decision ? 'No decision recorded' : a.kind === 'skill' ? (acc ? '✓ Accepted' : '✗ Not saved') : edit ? (acc ? (parts && a.data.hunks && parts.length < a.data.hunks.length ? `✓ Accepted ${parts.length} of ${a.data.hunks.length} parts` : '✓ Accepted — change applied') : '✕ Rejected') : (acc ? '✓ Ran' : '✕ Skipped');
  a.acts.append(el('span', { class: 'res', text: txt }));
  a.acts.style.alignItems = 'center';
  const t = diffTabFor(id); if (t) { t.resolved = true; setTimeout(() => closeTab(t, { force: true }), decision ? 350 : 0); }
}
async function agentDecide(id, decision, parts, answers) {
  const a = A.approvals.get(id); if (!a || a.resolved || a.busy) return;
  if (!A.run) { toast('This run is no longer active', 'warn'); return; }
  a.busy = true; $$('button', a.card).forEach(b => b.disabled = true);
  const t = diffTabFor(id); if (t) { $('#diff-accept').disabled = true; $('#diff-reject').disabled = true; }
  try { await POST('/api/agent/decision', { run: A.run, approval_id: id, decision, ...(parts ? { parts } : {}), ...(answers ? { answers } : {}) }); }
  catch (e) { a.busy = false; $$('button', a.card).forEach(b => b.disabled = false); if (t && S.active === t) { $('#diff-accept').disabled = false; $('#diff-reject').disabled = false; } errToast(e, 'Decision failed'); }
}

function undoChip(d, live) {
  const done = el('span', { text: '' });
  const btn = el('button', { title: 'Restore the file(s) to the state before this change', html: ico('undo', 12) + ' Undo' });
  const chip = el('div', { class: 'undo-chip' }, el('span', { class: 'p', text: d.path, title: d.path, onclick: () => openFile(d.path) }), btn);
  btn.onclick = async () => {
    btn.disabled = true;
    try {
      const r = await POST('/api/agent/undo', { ws: S.ws.id, checkpoint: d.checkpoint });
      chip.classList.add('done'); btn.innerHTML = ico('check', 12) + ' Undone';
      const rest = r.restored || []; toast(`Restored ${rest.length} file${rest.length === 1 ? '' : 's'}`, 'success', 3000);
      for (const p of rest.length ? rest : [d.path]) onDiskChanged(p);
    } catch (e) { btn.disabled = false; errToast(e, 'Undo failed'); }
  };
  return el('div', { class: 'm-undo' }, chip);
}
const DONE_WHY = {
  max_turns: 'It used all the turns allowed for one request. Continue gives it more.',
  cut_off: 'The model stopped mid-reply, usually because of a length limit.',
  error: 'The model kept failing, which often means the engine was stopped or unloaded. See the error above.',
  errors: 'The model kept failing, which often means the engine was stopped or unloaded. See the error above.',
  stuck: '', stopped: '', stop: '',
};
function checkBlock(d) {
  const n = el('div', { class: 'm-check ' + d.status });
  const cmd = d.command ? el('code', { text: d.command }) : null;
  if (d.status === 'none') { n.append('Studio found no tests to run, so it could not check this work.'); return n; }
  if (d.status === 'skipped') { n.append('Tests skipped: ', cmd); return n; }
  const passed = d.status === 'passed';
  n.append(el('span', { html: ico(passed ? 'check' : 'x', 13) }), el('b', { text: passed ? 'Tests passed' : 'Tests failed' }), ' Studio ran ', cmd, '.');
  if (!passed && d.claimed) n.append(' The agent said it had finished.');
  if (d.output) {
    n.append(el('details', {}, el('summary', { text: 'Output' }), el('pre', { text: d.output })));
    if (!passed) n.append(el('button', { class: 'btn sm m-fix', onclick: () => { const i = $('#agent-input'); i.value = `The tests failed when I ran \`${d.command}\`. Fix the cause.\n\n${d.output.slice(-1500)}`; autosize(); i.focus(); } }, 'Ask the agent to fix this'));
  }
  return n;
}
function branchBlock(d) {
  if (d.state === 'skipped') return el('div', { class: 'm-branch', text: 'Not using a run branch: ' + d.reason + '.' });
  if (d.state === 'started') return el('div', { class: 'm-branch' }, 'Working on branch ', el('code', { text: d.branch }), '.');
  if (d.error) return el('div', { class: 'm-branch' }, 'The run could not be committed on ', el('code', { text: d.branch }), ': ' + d.error);
  const act = async (action, btn) => {
    if (action === 'discard' && !(await confirmBox('Throw away this run', `Delete branch ${d.branch} and go back to ${d.base}? The run's commits will no longer be on any branch.`, 'Throw away', true))) return;
    btn.closest('.m-branch').querySelectorAll('button').forEach(b => b.disabled = true);
    try {
      await POST('/api/git/run', { ws: S.ws.id, action, branch: d.branch, base: d.base });
      toast(action === 'merge' ? `Merged into ${d.base}` : 'Run thrown away', 'success', 3000);
      btn.closest('.m-branch').querySelector('.branch-acts').replaceChildren(action === 'merge' ? 'Merged.' : 'Thrown away.');
      for (const t of S.tabs) if (t.kind === 'file') onDiskChanged(t.path);
      refreshTreeSoon(); gitRefresh();
    } catch (e) { btn.closest('.m-branch').querySelectorAll('button').forEach(b => b.disabled = false); errToast(e, 'Git'); }
  };
  const merge = el('button', { class: 'btn sm primary', onclick: e => act('merge', e.currentTarget) }, 'Merge into ' + d.base);
  const drop = el('button', { class: 'btn sm danger', onclick: e => act('discard', e.currentTarget) }, 'Throw away');
  return el('div', { class: 'm-branch' }, d.committed ? 'The run is committed on ' : 'The run changed nothing on ', el('code', { text: d.branch }), '.', el('div', { class: 'branch-acts' }, merge, drop));
}
const DONE_TXT = { stuck: 'Stopped: going in circles', compacted: 'Summarized', cut_off: 'Stopped: the reply was cut off', answered: 'Answered', finish: 'Finished', finished: 'Finished', max_turns: 'Stopped: turn limit reached', stopped: 'Stopped by user', stop: 'Stopped by user', errors: 'Stopped after repeated model errors', error: 'Stopped after errors' };

/* ---- event handler (live and replay) */
function handleEvent(type, d, live) {
  if (live && type !== 'usage' && type !== 'status' && type !== 'approval_resolved') pendingGhost(false);
  if (live && (type === 'tool_result' || type === 'approval_resolved') && A.running && !A.pending) setTimeout(() => { if (A.running && !A.pending && !A.cur && !A.ghost && msgsEl.lastElementChild?.matches('.m-tool, .m-appr')) pendingGhost(true); }, 0);
  switch (type) {
    case 'user': {
      finishCur(); const text = d.text ?? d.message ?? d.content ?? '';
      if (d.task) { addUser(text, d.context, { task: true }, false); break; }
      if (d.timeskip) { addUser(text, d.context, { timeskip: true }, false); break; }
      addUser(text, d.context, { images: (d.images || []).map(p => ({ src: `/api/agent/attachment?session=${encodeURIComponent(A.session)}&id=${encodeURIComponent(p.id)}`, name: p.name })), files: d.files, savedFiles: d.saved_files, plan: !!d.plan_mode, saved: !!d.saved_as, goal: !!d.goal }, !!d.guided);
      if (!d.guided) planNewMessage(text);       // guidance changes the plan only if the model revises it
      break;
    }
    case 'status': if (live) { A.statusText = d.text || ''; A.load = d.load || null; renderStatus(); } break;
    case 'token': if (d.text) { if (live) { A.statusText = ''; A.load = null; } streamInto('text', d.text); } break;
    case 'thinking': if (d.text) streamInto('thinking', d.text); break;
    case 'sent_back': {        // Studio sent this reply back (mixed scripts, Chinese in Greek): mark it, the rewrite follows
      finishCur();
      const replies = msgsEl.querySelectorAll('.m-asst'), last = replies[replies.length - 1];
      if (last && !last.classList.contains('sent-back')) {
        // Folded to its note: the rewrite below is the answer, and this one stays only for the record (Show opens it).
        last.classList.add('sent-back');
        const body = el('div', { class: 'sb-body' }, ...last.childNodes);
        const toggle = el('button', { class: 'link-btn card-link sb-toggle', text: 'Show', 'aria-expanded': 'false',
          onclick: () => { const open = last.classList.toggle('sb-open'); toggle.textContent = open ? 'Hide' : 'Show'; toggle.setAttribute('aria-expanded', String(open)); } });
        last.replaceChildren(el('div', { class: 'sent-back-note' }, el('span', { html: ico('refresh', 12) }),
          el('span', { class: 'grow', text: `Sent back to the model to write again: ${d.why || 'mixed scripts'}. The rewrite follows.` }), toggle), body);
      }
      break;
    }
    case 'text_fixed': {      // Studio read tool calls out of the reply's text; the reply was only what is left
      const c = A.cur;
      if (c && c.kind === 'text') { if ((d.text || '').trim()) { c.raw = d.text; renderCur(); } else { c.el.remove(); A.cur = null; } }
      break;
    }
    case 'tool_call': {
      finishCur(); if (live) { A.statusText = ''; A.load = null; }
      if (live && /video__render_video|comfyui__generate_video/.test(d.name || '')) tlAgentActivity(/generate_video/.test(d.name) ? 'generate' : 'render');
      const node = toolRow(d.id, d.name, d.args); A.tools.set(d.id, node);
      if (d.name !== 'ask_user') addBlock(node);          // its question card shows it
      if (live && !QUIET_TOOLS.has(d.name)) A.toolNow = { id: d.id, text: [node._parts.label.textContent, node.querySelector('.t-args')?.textContent].filter(Boolean).join(' '), t0: Date.now() };
      const prog = parseProgress(d.args?.progress);
      if (prog) { A.step = prog; if (A.plan && d.name !== 'plan') planShow(A.plan, prog.step, 'running'); }
      break;
    }
    case 'codex_usage': if (live && d.usage) codexUsageShow(d.usage); break;
    case 'tool_progress': {      // a tool's own progress, such as a video render: a bar on its row while it works
      const node = A.tools.get(d.id); if (!node || !node.classList.contains('running')) break;
      let bar = node.querySelector('.t-progress');
      if (!bar) { bar = el('div', { class: 't-progress' }, el('div', { class: 't-track' }, el('div', { class: 't-progress-fill' })), el('small', { class: 't-progress-text' })); node._parts.row.after(bar); }
      bar.classList.toggle('unknown', d.percent == null); bar.classList.toggle('waiting', !!d.waiting);
      bar.querySelector('.t-progress-fill').style.width = (d.percent == null ? 100 : d.percent) + '%';
      bar.querySelector('.t-progress-text').textContent = [d.percent == null ? '' : d.percent + '%', d.message].filter(Boolean).join(' · ');
      const now = A.toolNow;
      if (live && now && now.id === d.id) {
        if (d.percent != null && now.percent == null && !d.waiting) now.pt0 = Date.now();      // the time left is measured from the first real percentage
        Object.assign(now, { percent: d.percent, message: d.message || '', waiting: !!d.waiting }); renderStatus();
      }
      break;
    }
    case 'tool_result': {
      if (live && V.agent && /video__render_video|comfyui__generate_video/.test(d.name || '')) tlAgentActivity('');
      if (A.toolNow && A.toolNow.id === d.id) { A.toolNow = null; if (live) renderStatus(); }
      finishCur();
      let node = A.tools.get(d.id);
      if (!node) { node = toolRow(d.id, d.name, null); A.tools.set(d.id, node); addBlock(node); }
      toolResult(node, d); foldActivity(node); scrollMaybe(); break;
    }
    case 'approval': {
      finishCur(); const a = approvalCard(d, live); A.approvals.set(a.id, a); addBlock(a.card);
      if (live) { A.pending++; renderStatus(); notifyWaiting(d); } break;
    }
    case 'approval_resolved': {
      const a = A.approvals.get(d.approval_id);
      if (a && !a.resolved) { resolveApprovalUI(d.approval_id, d.decision, d.parts, d.answers); if (live) { A.pending = Math.max(0, A.pending - 1); renderStatus(); } }
      break;
    }
    case 'file_changed': {
      finishCur();
      if (d.change) {
        const card = [...A.tools.values()].reverse().find(n => EDIT_TOOLS.has(n.dataset.name) && !n.classList.contains('has-change') && n.classList.contains('running'));
        fileChangeCard(d, card);
      } else addBlock(undoChip(d, live));
      if (live) onDiskChanged(d.path);
      break;
    }
    case 'skill_saved': {
      finishCur();
      [...A.tools.values()].reverse().find(n => n.dataset.name === 'save_skill' && n.classList.contains('running'))?.remove();
      addBlock(skillCard(d)); scrollMaybe();
      if (live) skillsRefresh();
      break;
    }
    case 'plan': {
      finishCur();
      [...A.tools.values()].reverse().find(n => n.dataset.name === 'plan' && n.classList.contains('running'))?.remove();
      if (A.plan) { planShow(A.plan, A.plan.step || 1, 'paused'); if (d.revised) A.plan.card.classList.add('replaced'); }
      A.plan = planCard(d); addBlock(A.plan.card);
      planShow(A.plan, A.step && A.step.total === d.steps.length ? A.step.step : 1, 'running');
      if (live) renderStatus();
      break;
    }
    case 'page_view': {
      finishCur(); addBlock(pageViewCard(d)); scrollMaybe(); break;
    }
    case 'tool_picture': {     // pictures a tool returned, which the model can see too
      finishCur();
      const picTitle = d.title || `Picture from ${d.server} · ${d.tool}`;
      addBlock(el('div', { class: 'm-page m-tool-pic' }, el('div', { class: 'page-head' }, el('span', { html: ico('image', 14) }), el('span', { class: 'page-title', text: picTitle })),
        ...(d.images || []).map(p => el('img', { class: 'page-shot', src: `/api/agent/attachment?session=${encodeURIComponent(A.session)}&id=${encodeURIComponent(p.id)}`, alt: d.prompt || picTitle, 'data-name': p.name, loading: 'lazy' })),
        d.prompt ? el('small', { class: 'muted pic-prompt', text: d.prompt }) : null));
      scrollMaybe(); break;
    }
    case 'open_browser': {     // the agent starts using its web browser: a card to watch it, and the Browser tab opens now
      finishCur();
      addBlock(el('div', { class: 'm-page m-opened m-browser' }, el('div', { class: 'page-head' }, el('span', { html: ico('globe', 14) }), el('span', { class: 'page-title', text: 'The agent is using its web browser' })),
        el('div', { class: 'page-acts' }, el('button', { class: 'link-btn card-link', text: 'Watch it', onclick: () => openBrowserTab() }))));
      if (live && !document.documentElement.classList.contains('phone') && isAdmin()) openBrowserTab(true);
      scrollMaybe(); break;
    }
    case 'open_live': {        // the model shows the user a page: it opens now, and the card opens it again later
      finishCur();
      const where = `localhost:${d.port}${d.path || '/'}`;
      addBlock(el('div', { class: 'm-page m-opened' }, el('div', { class: 'page-head' }, el('span', { html: ico('globe', 14) }), el('span', { class: 'page-title', text: `Opened ${where} in a Live tab` })),
        el('div', { class: 'page-acts' }, el('button', { class: 'link-btn card-link', text: 'Open it again', onclick: () => openLiveTab(d.port, d.path || '/') }))));
      if (live) openLiveTab(d.port, d.path || '/');
      scrollMaybe(); break;
    }
    case 'usage': {
      A.turn = d.turn ?? A.turn; A.maxTurns = d.max_turns ?? A.maxTurns; A.pTok += d.prompt_tokens || 0; A.cTok += d.completion_tokens || 0;
      A.ctxUsed = d.prompt_tokens || A.ctxUsed; A.ctxCap = d.context_tokens || A.ctxCap;
      if (live) {
        if (A.ctxCap && A.ctxUsed / A.ctxCap >= 0.8 && !A.ctxWarned) { A.ctxWarned = true; toast('The model\'s memory for this chat is over 80% full. Type /compact to summarize the earlier messages, or start a new chat.', 'warn', 8000); }
        renderStatus();
      }
      break;
    }
    case 'compacted': {       // the model's memory of the chat was replaced by its own summary; the chat itself stays
      finishCur();
      const pct = n => A.ctxCap ? ` (${Math.round(n / A.ctxCap * 100)}%)` : '';
      const summary = d.summary || '';
      addBlock(el('div', { class: 'm-compacted' }, el('div', { class: 'cp-head' }, el('span', { html: ico('layers', 13) }),
        el('span', { text: `Summarized the earlier messages: ${fmtNum(d.before)}${pct(d.before)} → about ${fmtNum(d.after)} tokens${pct(d.after)}` })),
        el('div', { class: 'cp-note', text: 'The chat above stays here for you; the agent now works from this summary.' }),
        el('div', { class: 'cp-actions' },
          el('details', {}, el('summary', { text: 'The summary' }), el('div', { class: 'cp-sum', html: mdToHtml(summary) })),
          copySummaryButton(summary))));
      if (live) { A.ctxUsed = d.after; A.ctxWarned = false; renderStatus(); }
      break;
    }
    case 'handoff': {        // this chat was passed on to the user, with a copy of the folder
      finishCur();
      addBlock(el('div', { class: 'm-handoff' }, el('div', { class: 'cp-head' }, el('span', { html: ico('send', 13) }), el('span', {}, 'Passed to you by ', el('b', { text: d.from }))),
        el('div', { class: 'cp-note', text: `Everything above is their chat. This folder, "${d.folder}", is your own copy of theirs, so your changes do not reach them.` }),
        d.note ? el('div', { class: 'handoff-note', text: d.note }) : null));
      break;
    }
    case 'files': { finishCur(); addBlock(filesCard(d)); break; }
    case 'timeskip': { finishCur(); addBlock(timeskipCard(d)); break; }
    case 'timeskip_decided': { const card = msgsEl.querySelector(`.m-timeskip[data-id="${d.id}"]`); if (card) timeskipDecided(card, d); break; }
    case 'goal': { A.goal = d.state === 'cleared' ? null : d; renderGoal(); if (live && d.state === 'active' && d.rounds) note(`The goal is not reached yet: round ${d.rounds}.`); break; }
    case 'check': { finishCur(); addBlock(checkBlock(d)); break; }
    case 'shell': { finishCur(); toolResult(shellCard(d.command), d); break; }
    case 'git_branch': { finishCur(); addBlock(branchBlock(d)); if (live) gitRefresh(); break; }
    case 'error': { finishCur(); addBlock(el('div', { class: 'm-err', text: d.text || 'Error' })); break; }
    case 'done': {
      finishCur(); if (live) A.lastReason = d.reason || '';
      if (d.goal) { A.goal = d.goal; renderGoal(); }
      const turns = d.turns ?? A.turn;
      const reason = d.reason || 'finished';
      if (A.plan) planShow(A.plan, A.plan.step || 1, reason === 'finished' || reason === 'finish' || (reason === 'answered' && A.plan.step === A.plan.steps.length) ? 'finished' : 'paused');
      if (d.summary_reply) {          // the run worked with tools and ended on a written reply: show that reply as its summary
        const replies = msgsEl.querySelectorAll('.m-asst:not(.sent-back)'), last = replies[replies.length - 1];
        if (last && !last.classList.contains('run-summary')) {
          last.classList.add('run-summary');
          last.prepend(el('div', { class: 'rs-head' }, el('span', { html: ico('check', 13) }), el('b', { text: 'Summary' }), turns ? el('span', { text: `· ${turns} turn${turns === 1 ? '' : 's'}` }) : null));
        }
      }
      const f = el('div', { class: 'm-done' });
      const parts = [DONE_TXT[reason] || reason]; if (turns) parts.push(`${turns} turn${turns === 1 ? '' : 's'}`);
      if (d.plan_mode && reason !== 'answered' && reason !== 'finished') parts[0] += ' (plan first)';
      if (live || d.summary || reason !== 'answered' || d.plan_mode || d.silent) { f.append(parts.join(' · ')); if (DONE_WHY[reason]) f.append(el('div', { class: 'why', text: DONE_WHY[reason] }));
        if (d.stuck) f.append(el('div', { class: 'why', text: `Studio stopped the run because ${d.stuck}. Look at the errors above, then tell the agent what to try, or press Continue.` }));
        if (d.silent) { f.append(el('div', { class: 'why', text: 'The model ended without writing an answer. Continue usually gets one.' })); f.append(continueButton()); } if (d.summary) f.append(el('div', { class: 'sum', html: mdToHtml(d.summary) }, copyReplyButton(d.summary))); if (reason in DONE_WHY) f.append(continueButton()); if (live && reason !== 'compacted' && A.ctxCap && A.ctxUsed / A.ctxCap >= 0.85) f.append(fullMemory()); if (d.plan_mode && (reason === 'answered' || reason === 'finished')) f.append(planActions(d)); addBlock(f); }
      if (live) { A.doneSeen = true; runEnded(); }
      break;
    }
    default: break;
  }
}

/* ---- status line */
function renderStatus() {
  const box = $('#agent-status');
  $('#mt-chat').classList.toggle('busy', !!A.running); $('#mt-chat').classList.toggle('attn', A.running && A.pending > 0);
  if (!A.running && !A.pTok && !A.cTok && !A.lastReason) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  const waiting = A.running && A.pending > 0;
  const elapsed = A.t0 ? fmtElapsed((A.running ? Date.now() : A.tEnd || Date.now()) - A.t0) : '';
  // The limit is a safety net, and "1/200" reads as a huge job, so it only appears when it is getting close.
  const meta = [A.maxTurns && A.turn >= A.maxTurns * 0.9 ? `turn ${A.turn}/${A.maxTurns}` : (A.turn ? `turn ${A.turn}` : ''), (A.pTok || A.cTok) ? `${fmtNum(A.pTok)} in / ${fmtNum(A.cTok)} out` : '', elapsed].filter(Boolean).join(' · ');
  const load = A.running && !waiting ? A.load : null;
  // The running tool, once it has taken a second: its own words and bar, so the line says what is happening now even when its row
  // has scrolled away or folded. A percentage only when the tool gives one; otherwise a moving stripe and the time it has taken.
  const tool = A.running && !waiting && !load && A.toolNow && (A.toolNow.message || Date.now() - A.toolNow.t0 >= 1000) ? A.toolNow : null;
  const toolWait = !!(tool && tool.waiting);
  box.classList.toggle('waiting', waiting || toolWait);
  const text = load ? `Loading model… ${load.percent}% · ${load.step}` : tool ? [tool.text, tool.message].filter(Boolean).join(' · ') : A.running ? (waiting ? ([...A.approvals.values()].some(a => !a.resolved && a.kind === 'question') ? 'Waiting for your answer…' : 'Waiting for your approval…') : (A.statusText || planStatus() || 'Working…')) : (DONE_TXT[A.lastReason] || 'Done');
  // Runs every second, so reuse the nodes: a new spinner would restart its animation from 0 degrees.
  if (box.dataset.mode !== (A.running ? 'running' : 'ended') || !box.firstChild) {
    box.dataset.mode = A.running ? 'running' : 'ended';
    box.replaceChildren(A.running ? el('span', { class: 'spin' }) : el('span', { html: ico('check', 13), style: 'color:var(--ok)' }), el('span', { class: 'st-text' }), el('span', { class: 'ctx-meter hidden', role: 'img' }, el('span', { class: 'ctx-pie' }), el('span', { class: 'ctx-pct' })), el('span', { class: 'st-meta' }), el('span', { class: 'st-load hidden', role: 'progressbar', 'aria-label': 'Loading the model', 'aria-valuemin': '0', 'aria-valuemax': '100' }, el('span')));
  }
  const bar = box.querySelector('.st-load');
  const pct = load ? load.percent : tool && tool.percent != null && !toolWait ? tool.percent : null;
  const showBar = !!load || (!!tool && Date.now() - tool.t0 >= 3000) || toolWait || pct != null;
  bar.classList.toggle('hidden', !showBar);
  box.classList.toggle('loading', showBar);
  bar.classList.toggle('unknown', showBar && pct == null);
  bar.setAttribute('aria-label', load ? 'Loading the model' : tool ? tool.text : '');
  if (pct != null) { bar.firstChild.style.width = pct + '%'; bar.setAttribute('aria-valuenow', String(pct)); } else { bar.firstChild.style.width = '100%'; bar.removeAttribute('aria-valuenow'); }
  const left = tool && pct != null && pct >= 5 && pct < 100 && tool.pt0 ? (Date.now() - tool.pt0) * (100 - pct) / pct : 0;
  const toolMeta = tool ? [pct != null ? pct + '%' : '', left > 4000 ? `${fmtElapsed(left)} left` : '', fmtElapsed(Date.now() - tool.t0)].filter(Boolean).join(' · ') : '';
  // The page title and the phone's Chat tab carry the percentage too, for whoever is looking elsewhere.
  const bit = !A.running ? '' : waiting ? 'Waiting' : pct != null ? pct + '%' : toolWait ? 'Waiting' : '';
  if (bit !== A.titleBit) { A.titleBit = bit; roomBadge(); }
  const tabPct = $('#mt-chat .mt-pct'); if (tabPct) { tabPct.textContent = bit && bit !== 'Waiting' ? bit : ''; tabPct.classList.toggle('hidden', !bit || bit === 'Waiting'); }
  const textEl = box.querySelector('.st-text');
  if (textEl.textContent !== text) { textEl.textContent = text; textEl.title = text; }
  const metaEl = box.querySelector('.st-meta'), shownMeta = tool ? toolMeta : meta;
  if (metaEl.textContent !== shownMeta) { metaEl.textContent = shownMeta; metaEl.title = tool ? meta : ''; }
  const meter = box.querySelector('.ctx-meter');
  meter.classList.toggle('hidden', !A.ctxCap);
  if (A.ctxCap) {
    const pct = Math.min(100, A.ctxUsed / A.ctxCap * 100);
    meter.firstChild.style.setProperty('--pct', pct.toFixed(1));
    meter.lastChild.textContent = Math.round(pct) + '%';
    meter.setAttribute('aria-label', `Model memory ${Math.round(pct)}% full`);
    meter.classList.toggle('warn', pct >= 80 && pct < 90); meter.classList.toggle('full', pct >= 90);
    meter.title = `Model memory: ${fmtNum(A.ctxUsed)} of ${fmtNum(A.ctxCap)} tokens (${Math.round(pct)}%)`;
  }
}

/* ---- run lifecycle */
function setRunning(on) {
  A.running = on;
  $('#agent-stop').classList.toggle('hidden', !on); $('#agent-queue').classList.toggle('hidden', !on);
  $('.input-wrap').classList.toggle('running', on);
  $('#agent-send').title = on ? 'Steer the agent now (Enter); it reads this at its next step' : 'Send (Enter)';
  $('#agent-input').placeholder = on ? 'Steer the agent…  (Enter steers now, Alt+Enter queues for after)' : S.planMode ? PLAN_HINT : INPUT_HINT;
  $('#agent-stop').disabled = false; A.stopping = false;
  msgsEl.setAttribute('aria-busy', on ? 'true' : 'false');
  if (on && !A.timer) A.timer = setInterval(renderStatus, 1000);
  if (!on) { clearInterval(A.timer); A.timer = null; }
}
// A run that ended without its last tool calls finishing (Studio restarted, the run was lost): those rows stop spinning.
function settleRows() {
  $$('.m-tool.running', msgsEl).forEach(n => {
    n.classList.replace('running', 'cut');
    if (n._parts?.stat) n._parts.stat.innerHTML = ico('x', 13);
    const sum = n.querySelector('.t-sum'); if (sum && !sum.textContent) sum.textContent = 'did not finish';
    n.title = 'The run ended before this call finished, so it did not run or its result was lost';
  });
}
function runEnded() {
  if (V.agent) tlAgentActivity('');
  if (/^codex:/.test(S.model || '')) codexUsageLoad();
  const lost = !A.doneSeen && msgsEl.querySelector('.m-tool.running');
  settleRows();
  if (lost) addBlock(el('div', { class: 'm-done' }, 'This run ended without finishing, possibly because Studio restarted.', continueButton()));
  notifyDone(A.lastReason);
  A.tEnd = Date.now(); if (A.es) { A.es.close(); A.es = null; }
  setRunning(false); A.statusText = ''; A.load = null; A.toolNow = null; A.pending = 0; pendingGhost(false); renderStatus();
  for (const [id, a] of A.approvals) if (!a.resolved) resolveApprovalUI(id, null);
  renderQueue(); if (!/^stop/.test(A.lastReason || '')) setTimeout(sendQueued, 400);   // after Stop, queued messages wait for the user
  loadSessions(); refreshTreeSoon(); gitRefresh();
}
function attachRun(run, after) {
  if (A.es) { A.es.close(); A.es = null; }
  A.run = run; A.lastSeq = after || 0; A.doneSeen = false; A.retry = 0; A.pending = 0; syncAuto();
  if (!A.running) { if (!A.t0) A.t0 = Date.now(); setRunning(true); }
  renderStatus(); connectStream(); if (!after) pendingGhost(true);
}
function connectStream() {
  const es = liveStream('agent', { run: A.run, after: A.lastSeq }); A.es = es;
  for (const type of SSE_TYPES) es.addEventListener(type, ev => {
    if (type === 'error' && !(ev instanceof MessageEvent && typeof ev.data === 'string')) { connLost(es); return; }
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    A.retry = 0;
    if (typeof d.seq === 'number') { if (d.seq <= A.lastSeq) return; A.lastSeq = d.seq; }
    handleEvent(type, d, true);
  });
}
async function connLost(es) {
  if (A.es !== es) return;
  es.close(); A.es = null;
  if (A.doneSeen || !A.running) return;
  if (++A.retry > 6) { toast('Lost the connection to the agent run.', 'error'); runEnded(); return; }
  await sleep(Math.min(4000, 400 * A.retry));
  if (!A.running || A.es || A.doneSeen) return;
  try { const st = await GET('/api/auth/state', null, { noAuthRedirect: true }); if (!st.logged_in) { onUnauthorized(); return; } } catch { /* offline: keep retrying */ }
  if (S.ws) { try { const act = await GET('/api/agent/active', { ws: S.ws.id }); if (!act.run || act.run !== A.run) { runEnded(); return; } } catch { /* ignore */ } }
  connectStream();
}

/* ---- commands typed in the chat box. None of these go through the model. */
function note(text) { finishCur(); addBlock(el('div', { class: 'm-note', text })); A.stick = true; scrollNow(); }
async function ensureSession(title) {
  if (A.session) return;
  const r = await POST('/api/agent/sessions', { ws: S.ws.id }); A.session = r.id;
  A.sessions.unshift({ id: r.id, title: title.slice(0, 60), updated: Date.now() / 1000, model: S.model }); renderSessionTitle();
  lsSet('studio.session.' + S.ws.id, A.session); prefSet('session:' + S.ws.id, A.session);
}
function shellCard(command) {
  const node = toolRow('shell-' + Date.now() + Math.random(), 'run', { command });
  node._parts.label.textContent = 'You ran';
  setCommandOpen(node, true);      // you ran it to see the result
  addBlock(node);
  return node;
}
async function runShell(command) {
  if (!command) { note('Write a command after the !, such as !make test'); return; }
  if (A.running) { note('The agent is working in this folder. Wait until it ends, or use /stop first.'); return; }
  if (A.shellBusy) { note('The last command is still running.'); return; }
  A.shellBusy = true;
  finishCur(); A.stick = true;
  try {
    await ensureSession(command);
    const node = shellCard(command); scrollNow();
    try {
      const r = await POST('/api/command/run', { ws: S.ws.id, session: A.session, command }, { noAuthRedirect: false });
      toolResult(node, r); refreshTreeSoon(); gitRefresh(); for (const t of S.tabs) if (t.kind === 'file' && !t.dirty) onDiskChanged(t.path);
    } catch (e) { toolResult(node, { ok: false, summary: 'not run', output: e.message }); }
    scrollMaybe();
  } catch (e) { errToast(e, 'Command'); }
  finally { A.shellBusy = false; }
}
const COMMANDS = [
  { name: 'new', desc: 'Start a new chat', run: () => { if (A.running) note('Use /stop first: the agent is working.'); else newChat(); } },
  { name: 'continue', desc: 'Carry on from where the agent stopped', run: () => { if (!A.session) note('There is no chat to continue yet.'); else sendText('Continue from where you stopped.'); } },
  { name: 'plan', args: 'task', desc: 'Plan first: look and propose a plan, changing nothing (alone: switch plan first on or off)', run: arg => {
    if (!arg) { setPlanMode(!S.planMode); note(S.planMode ? 'Plan first is on: the agent proposes a plan and changes nothing until you approve it.' : 'Plan first is off.'); return; }
    A.planOnce = true; sendText(arg);
  } },
  { name: 'goal', args: 'goal', desc: 'Give the chat a goal: the agent keeps working until it is reached (also /goal pause, resume or clear)', run: async arg => {
    const word = (arg || '').toLowerCase();
    if (!arg) { note(A.goal ? `Goal (${A.goal.state}): ${A.goal.text}` : 'Write the goal after /goal, such as /goal Make every test pass. The agent works in rounds until it is reached.'); return; }
    if (['pause', 'resume', 'clear'].includes(word)) { if (!A.goal) note('This chat has no goal.'); else await goalAction(word); return; }
    if (A.running) { note('Use /stop first, or steer the agent with your message.'); return; }
    A.goalOnce = true; sendText(arg);
  } },
  { name: 'loop', args: 'interval message', desc: 'Send a message again every so often, e.g. /loop 10m check the build, stop after 3 answers (/loop stop ends it)', run: async arg => {
    const mine = () => TK.tasks.find(t => t.loop && t.loop.session === A.session && t.enabled);
    if (!arg || /^(stop|end|off)$/i.test(arg)) {
      await tasksRefresh().catch(() => {});
      const t = mine();
      if (!arg) { note(t ? `This chat repeats "${t.prompt}" ${scheduleText(t.schedule).toLowerCase()}, ${t.loop.runs || 0} time${t.loop.runs === 1 ? '' : 's'} so far. /loop stop ends it.` : 'Type /loop, how often, then what to do: /loop 10m check whether the build passes. Add "stop after 3 answers", "for 2 hours" or "stop when it says passed". /loop stop ends it.'); return; }
      if (!t) { note('This chat has no loop.'); return; }
      await taskAction(t, 'pause'); note('The loop is stopped.'); return;
    }
    try {
      await ensureSession(arg);
      const r = await POST('/api/tasks/loop', { ws: S.ws.id, session: A.session, text: arg, model: S.model, mode: S.autoApply && S.autoCmd ? 'auto' : 'ask' });
      const t = r.task, lim = t.loop;
      note(`Loop: "${t.prompt}" ${scheduleText(t.schedule).toLowerCase()}` + (lim.max_runs ? `, ${lim.max_runs} time${lim.max_runs === 1 ? '' : 's'}` : '') + (lim.until ? `, until ${whenText(lim.until).replace(/^in /, 'in ')}` : '') + (lim.stop_when ? `, until the answer says "${lim.stop_when}"` : '') + `. Nobody needs to watch: ${t.mode === 'auto' ? 'edits and commands run without asking' : 'what needs a yes is skipped'}. /loop stop ends it.`);
      tasksRefresh();
    } catch (e) { note('Could not start the loop: ' + e.message); }
  } },
  { name: 'timeskip', args: 'task', desc: 'Try a change on a copy of the folder, test both, and keep it only if you like it', run: async arg => {
    if (!arg) { note('Write the task after /timeskip, such as /timeskip Make the search case-insensitive. The agent works on a copy; you compare and keep it or throw it away.'); return; }
    if (A.running) { note('Use /stop first: the agent is working.'); return; }
    try {
      await ensureSession(arg);
      const r = await POST('/api/timeskip/start', { ws: S.ws.id, session: A.session, message: arg, model: S.model, auto_commands: S.autoCmd });
      finishCur(); addUser(arg, null, { timeskip: true }); A.stick = true; scrollNow();
      A.turn = 0; A.pTok = 0; A.cTok = 0; A.statusText = 'Copying your folder…'; A.lastReason = ''; A.t0 = Date.now(); A.tEnd = 0; A.pending = 0;
      attachRun(r.run, 0);
    } catch (e) { if (e.status === 423) { toast(e.message, 'warn'); roomToggle(true); } else note('Time-skip could not start: ' + e.message); }
  } },
  { name: 'compact', desc: 'Summarize the earlier messages, so the model has room again', run: () => compactChat() },
  { name: 'stop', desc: 'Stop the current run', run: () => { if (A.running) agentStop(); else note('Nothing is running.'); } },
  { name: 'model', args: 'name', desc: 'Switch model', run: arg => {
    if (!arg) { note('Models: ' + S.models.map(m => m.name).join(', ')); return; }
    const low = arg.toLowerCase();
    const found = S.models.find(m => m.name.toLowerCase() === low) || S.models.find(m => m.name.toLowerCase() === low + ':strata') || S.models.filter(m => m.name.toLowerCase().startsWith(low)).sort((a, b) => a.name.length - b.name.length)[0];
    if (!found) { note(`No model matches "${arg}". /model on its own lists them.`); return; }
    S.model = found.name; lsSet('studio.model', found.name); $('#model-select').value = found.name; prefSet('model', found.name); effortChip();
    note('Model: ' + found.name);
  } },
  { name: 'test', desc: 'Run the project\'s tests now', run: async () => {
    try { const { command } = await GET('/api/command/test', { ws: S.ws.id }); if (command) await runShell(command); else note('Studio found no tests here. Add a line such as "Test command: make test" to STUDIO.md.'); }
    catch (e) { note('Could not look for tests: ' + e.message); }
  } },
  { name: 'diff', desc: 'Show what changed since the last commit', run: async () => {
    try {
      const d = await GET('/api/git/changes', { ws: S.ws.id });
      if (!d.diff && !d.new_files.length) { note('Nothing has changed since the last commit.'); return; }
      showGitText('Uncommitted changes', (d.new_files.length ? 'New files:\n' + d.new_files.map(f => '  ' + f).join('\n') + '\n\n' : '') + d.diff);
    } catch (e) { note(e.message); }
  } },
  { name: 'commit', args: 'message', desc: 'Commit all the changes', run: async arg => {
    if (!arg) { note('Write a message after /commit, such as /commit Fix the parser'); return; }
    try {
      const d = await GET('/api/git/status', { ws: S.ws.id });
      if (d.state !== 'repo') { note('This folder is not a git repository. Create one in the Source control panel.'); return; }
      if (!d.files.length) { note('There is nothing to commit.'); return; }
      await POST('/api/git/commit', { ws: S.ws.id, message: arg, paths: d.files.map(f => f.path) });
      note(`Committed ${d.files.length} file${d.files.length === 1 ? '' : 's'}.`); gitRefresh();
    } catch (e) { note('Commit failed: ' + e.message); }
  } },
  { name: 'undo', desc: 'Undo the agent\'s last edit', run: () => {
    const button = (A.undos || []).filter(b => !b.disabled && b.isConnected).pop();
    if (button) button.click(); else note('There is no edit to undo in this chat.');
  } },
  { name: 'export', desc: 'Download this chat as a report', run: () => $('#chat-export').click() },
  { name: 'guide', args: 'words', desc: 'Open the guide to everything Studio does, searched for your words if you give any', run: arg => openGuide(arg || '') },
  { name: 'help', desc: 'List these commands', run: () => note(['Commands:', ...COMMANDS.map(c => `/${c.name}${c.args ? ' <' + c.args + '>' : ''}: ${c.desc}`), '!command: run a shell command here, and tell the agent the result with your next message', '@file: attach a file to your message', '//text: send text that starts with a slash', 'The full guide: /guide, or Ctrl+K then Guide'].join('\n')) },
];
async function runInline(text) {
  if (text[0] === '!') { await runShell(text.slice(1).trim()); return true; }
  const match = /^\/([a-zA-Z]+)(?:\s+([\s\S]*))?$/.exec(text);     // a path such as /etc/hosts does not match
  if (!match) return false;
  const command = COMMANDS.find(c => c.name === match[1].toLowerCase());
  if (!command) { note(`/${match[1]} is not a command. /help lists them. To send text starting with a slash, begin with //`); return true; }
  await command.run((match[2] || '').trim());
  return true;
}
/* ---- pictures and text files added to the next message: pasted, dropped or picked */
const ATTACH_MAX = 8, ATTACH_TEXT_MAX = 10 * 1024 * 1024, IMAGE_EDGE = 1600, ATTACH_DOC_MAX = 25 * 1024 * 1024;
// Documents Studio reads into text for the model (studio.py DOC_EXTS); old binary ones and PDFs need an admin, since a program on the server opens them.
const DOC_EXT = /\.(docx|docm|xlsx|xlsm|pptx|odt|ods|odp|doc|xls|ppt|rtf|pdf)$/i;
const readDataUrl = file => new Promise((ok, fail) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = () => fail(r.error); r.readAsDataURL(file); });
A.attach = [];
const TEXT_EXT = /\.(txt|md|markdown|csv|tsv|json|jsonl|ya?ml|toml|ini|cfg|conf|env\.example|log|xml|html?|css|scss|less|js|mjs|cjs|jsx|ts|tsx|vue|svelte|py|rb|php|go|rs|java|kt|c|h|cc|cpp|hpp|cs|swift|sh|bash|zsh|ps1|sql|r|lua|pl|dart|scala|ex|exs|erl|hs|ml|clj|tex|bib|srt|vtt|diff|patch|gitignore|dockerfile|makefile)$/i;
// Big pictures are scaled down before they leave the browser: the model sees no more than about 1600 pixels anyway.
async function shrinkImage(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = () => bad(new Error('not a picture')); i.src = url; });
    const scale = Math.min(1, IMAGE_EDGE / Math.max(img.naturalWidth, img.naturalHeight));
    if (scale === 1 && file.size < 1.5 * 1024 * 1024 && /^image\/(png|jpeg|webp)$/.test(file.type)) return await new Promise(ok => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(file); });
    const c = document.createElement('canvas'); c.width = Math.round(img.naturalWidth * scale); c.height = Math.round(img.naturalHeight * scale);
    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL(file.type === 'image/png' ? 'image/png' : 'image/jpeg', 0.88);
  } finally { URL.revokeObjectURL(url); }
}
async function addAttachments(files) {
  for (const file of files) {
    if (A.attach.length >= ATTACH_MAX) { toast(`At most ${ATTACH_MAX} files per message`, 'warn'); break; }
    const name = file.name || (file.type.startsWith('image/') ? 'pasted picture.png' : 'pasted file.txt');
    try {
      if (file.type.startsWith('image/')) A.attach.push({ kind: 'image', name, data: await shrinkImage(file) });
      else if (DOC_EXT.test(name)) {
        if (file.size > ATTACH_DOC_MAX) { await attachToFolder(file, name); continue; }
        A.attach.push({ kind: 'document', name, data: await readDataUrl(file) });
      } else if (file.type.startsWith('text/') || TEXT_EXT.test(name) || file.type === 'application/json') {
        if (file.size > ATTACH_TEXT_MAX) { await attachToFolder(file, name); continue; }
        const text = await file.text();
        if (text.includes('\u0000')) { await attachToFolder(file, name); continue; }
        A.attach.push({ kind: 'text', name, text });
      } else await attachToFolder(file, name);
    } catch { toast(`Could not read ${name}`, 'error'); }
  }
  renderAttachments();
}
// Other files (sounds, videos, 3D, archives…) are uploaded to the folder's attachments/ and sent as their path.
async function attachToFolder(file, name) {
  if (!S.ws) { toast(`${name}: open a folder first, so Studio can keep the file there for the agent`, 'warn'); return; }
  const [path] = await uploadFiles([{ file, rel: 'attachments/' + name }], { conflict: 'keep', quiet: file.size < 8 * 1024 * 1024 });
  if (path) A.attach.push({ kind: 'file', name, path, size: file.size });
}
// A file already in the project: pictures are shown to the model, everything else goes as its path.
async function attachProjectFile(path) {
  if (A.attach.length >= ATTACH_MAX) { toast(`At most ${ATTACH_MAX} files per message`, 'warn'); return; }
  if (A.attach.some(a => a.path === path)) return;
  const name = basename(path);
  if (/\.(png|jpe?g|webp|gif)$/i.test(name)) {
    try {
      const blob = await (await fetch(await previewUrl(path))).blob();
      A.attach.push({ kind: 'image', name, data: await shrinkImage(new File([blob], name, { type: blob.type })) });
      renderAttachments(); return;
    } catch { /* sent as a path instead */ }
  }
  A.attach.push({ kind: 'file', name, path });
  renderAttachments();
}
const ATTACH_KIND_ICO = { sound: 'music', video: 'film', model: 'cube', picture: 'image' };
function attachIco(a) {
  const ext = (a.name.split('.').pop() || '').toLowerCase();
  const kind = /^(wav|mp3|flac|ogg|oga|opus|m4a|aac|aiff?)$/.test(ext) ? 'sound' : /^(mp4|webm|mov|mkv|avi|m4v)$/.test(ext) ? 'video'
    : /^(glb|gltf|obj|stl|fbx|ply|blend)$/.test(ext) ? 'model' : /^(png|jpe?g|webp|gif|svg)$/.test(ext) ? 'picture' : '';
  return ico(ATTACH_KIND_ICO[kind] || 'file', 13);
}
// The ▾ beside Add files: this computer, a file of the project, or one of the newest sounds, pictures and videos.
async function attachMenu(anchor) {
  const r = anchor.getBoundingClientRect();
  const items = [{ label: 'From this computer…', run: () => $('#attach-input').click() },
    { label: 'From the project…', run: () => pickProjectFile() }];
  if (S.ws) {
    try {
      const recent = ((await GET('/api/media/list', { ws: S.ws.id })).media || []).slice(0, 6);
      if (recent.length) items.push('-', ...recent.map(m => ({ label: 'Recent: ' + m.path, run: () => attachProjectFile(m.path) })));
    } catch { /* no recent media */ }
  }
  showContextMenu(r.left, r.top - 4, items);
}
// Search the project's files by name, like @, and add the chosen one to the message.
async function pickProjectFile() {
  if (!S.ws) { toast('Open a folder first', 'warn'); return; }
  let files = [];
  try { files = (await GET('/api/files/list', { ws: S.ws.id })).files || []; } catch (e) { errToast(e, 'Could not list the files'); return; }
  const input = el('input', { class: 'dlg-input', type: 'search', placeholder: 'Type part of a file name…', 'aria-label': 'File name' });
  const list = el('div', { class: 'pick-list', role: 'listbox' });
  let entry = null;
  const show = () => {
    const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
    const hits = files.filter(f => words.every(w => f.toLowerCase().includes(w))).slice(0, 60);
    list.replaceChildren(...hits.map(f => el('button', { class: 'pick-row', role: 'option', title: f, onclick: () => { closeOverlay(entry); attachProjectFile(f); } },
      el('span', { html: attachIco({ name: f }) }), el('span', { class: 'pick-name', text: basename(f) }), el('small', { text: dirname(f) }))));
    if (!hits.length) list.append(el('div', { class: 'tl-hint', text: 'No file matches.' }));
  };
  input.oninput = show;
  input.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); list.querySelector('.pick-row')?.click(); } };
  show();
  const shown = dialog({ title: 'Add a file from the project', body: el('div', { class: 'dlg-form' }, input, list), buttons: [{ label: 'Close', value: false }], small: false });
  entry = overlayStack[overlayStack.length - 1];
  setTimeout(() => input.focus(), 30);
  await shown;
}
function renderAttachments() {
  const row = $('#attach-row');
  row.classList.toggle('hidden', !A.attach.length);
  row.replaceChildren(...A.attach.map((a, i) => el('div', { class: 'attach-chip' + (a.kind === 'image' ? ' pic' : ''), title: a.path ? `${a.path} (in the folder; the agent gets its path)` : a.name },
    a.kind === 'image' ? el('img', { src: a.data, alt: a.name }) : a.kind === 'document' ? el('span', { class: 'attach-doc', 'data-type': docType(a.name), text: docType(a.name) }) : el('span', { html: attachIco(a) }),
    el('span', { class: 'attach-name', text: a.name }), a.kind === 'file' ? el('small', { class: 'attach-where', text: dirname(a.path) || 'project' }) : null,
    el('button', { class: 'attach-x', title: 'Remove', 'aria-label': `Remove ${a.name}`, html: ico('x', 12), onclick: () => { A.attach.splice(i, 1); renderAttachments(); } }))));
}
$('#attach-btn').onclick = () => $('#attach-input').click();
function docType(name) { const e = (name.split('.').pop() || '').toLowerCase(); return { docx: 'W', docm: 'W', doc: 'W', odt: 'W', rtf: 'W', xlsx: 'X', xlsm: 'X', xls: 'X', ods: 'X', pptx: 'P', ppt: 'P', odp: 'P', pdf: 'PDF' }[e] || 'DOC'; }
$('#attach-more').onclick = e => attachMenu(e.currentTarget);
/* ---- plan first: the agent may look, but changes nothing until the user approves its plan */
S.planMode = lsGet('studio.planMode') === '1';
function setPlanMode(on) {
  S.planMode = on; lsSet('studio.planMode', on ? '1' : '0');
  $('#plan-chip').setAttribute('aria-pressed', String(on)); $('#plan-chip').classList.toggle('on', on);
  if (!A.running) $('#agent-input').placeholder = on ? PLAN_HINT : INPUT_HINT;
}
$('#plan-chip').onclick = () => setPlanMode(!S.planMode);
function planActions(d) {
  const steps = d.plan_steps || [];
  const go = (text) => { setPlanMode(false); sendText(text); };
  const box = el('div', { class: 'plan-acts m-continue' },
    el('button', { class: 'btn sm primary', onclick: () => go('Carry out the plan above.') }, 'Carry out the plan'),
    steps.length ? el('button', { class: 'btn sm', onclick: async () => {
      const area = el('textarea', { class: 'plan-edit', rows: String(Math.min(14, steps.length + 2)), spellcheck: 'true' });
      area.value = steps.map((s, i) => `${i + 1}. ${s}`).join('\n');
      const ok = await dialog({ title: 'Edit the plan', body: el('div', {}, el('p', { text: 'Change, add, remove or reorder the steps, one per line. The agent carries out the plan as you leave it.' }), area),
        buttons: [{ label: 'Cancel', value: false }, { label: 'Carry it out', value: true, kind: 'primary' }], small: false });
      const lines = area.value.split('\n').map(l => l.replace(/^\s*(\d+[.)]|[-*•])\s*/, '').trim()).filter(Boolean);
      if (ok === true && lines.length) go('Carry out this plan, as I changed it:\n' + lines.map((l, i) => `${i + 1}. ${l}`).join('\n'));
    } }, 'Edit the plan') : null,
    el('button', { class: 'btn sm', title: 'Say what to change; the agent plans again, still changing nothing', onclick: () => { setPlanMode(true); putInBox('Change the plan: '); } }, 'Change it'));
  return box;
}
$('#attach-input').onchange = e => { addAttachments([...e.target.files]); e.target.value = ''; };
$('#agent-input').addEventListener('paste', e => { const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); addAttachments(files); } });
$('#composer').addEventListener('dragover', e => { if ([...e.dataTransfer.types].includes('Files')) { e.preventDefault(); $('#composer').classList.add('drop'); } });
$('#composer').addEventListener('dragleave', e => { if (!$('#composer').contains(e.relatedTarget)) $('#composer').classList.remove('drop'); });
$('#composer').addEventListener('drop', e => { $('#composer').classList.remove('drop'); if (e.dataTransfer.files.length) { e.preventDefault(); addAttachments([...e.dataTransfer.files]); } });

// `queue`: wait for the run to end instead of telling the agent now. `item`: a queued message being sent.
async function agentSend({ queue = false, item = null } = {}) {
  const inp = $('#agent-input'); let text = item ? item.text : inp.value.trim();
  const attach = item ? item.attach || [] : A.attach;
  if (!item) closeCommandMenu();
  if (!text && attach.length && !A.sendBusy) text = attach.some(a => a.kind === 'image') ? 'Look at the attached picture.' : 'Look at the attached file.';
  if (!text || A.sendBusy) { if (item) { A.queue.unshift(item); saveQueue(); renderQueue(); } return; }
  if (!S.ws) { toast('Open a folder first', 'info'); return; }
  if (item) { /* already checked when it was queued */ }
  else if (text.startsWith('//')) text = text.slice(1);
  else if (text[0] === '/' || text[0] === '!') {
    A.sendBusy = true;
    let handled = false;
    try { handled = await runInline(text); } finally { A.sendBusy = false; }
    if (handled) { if (inp.value.trim() === text) inp.value = ''; autosize(); return; }
  }
  if (A.running && !item) {
    // Files go with a new message, so a message carrying them waits for the run to end.
    if (queue || attach.length) queueMessage(text); else guideRun(text);
    return;
  }
  if (A.running) { A.queue.unshift(item); saveQueue(); renderQueue(); return; }
  if (!S.model) { toast('No model selected', 'error'); return; }
  if (A.attach.some(a => a.kind === 'image') && S.models.find(m => m.name === S.model)?.vision === false) { toast(`${S.model} cannot see pictures. Pick a Strata model, or remove the picture.`, 'warn'); return; }
  let asFile = false;
  const extraText = attach.filter(a => a.kind === 'text').reduce((n, a) => n + a.text.length, 0);
  const room = A.ctxCap ? A.ctxCap - A.ctxUsed : 0, tokens = Math.round((text.length + extraText) / 3.2);
  if (room && tokens > room * 0.8) {        // it would not fit in what is left of the model's memory
    const choice = await dialog({ title: 'This message is too long', body: el('div', {}, el('p', { text: `It is about ${fmtNum(tokens)} tokens, and the model's memory has room for about ${fmtNum(Math.max(0, room))} more in this chat.` }),
      extraText ? el('p', { text: 'Mention big files with @ instead of attaching them, so the agent reads them in parts.' }) : null,
      el('p', { text: extraText ? 'Shorten: only the start and end of your message are sent.' : 'Save it as a file: the agent reads it in parts. Shorten: only its start and end are sent.' })),
      buttons: [{ label: 'Cancel', value: false }, { label: 'Shorten', value: 'short' }, ...(extraText ? [] : [{ label: 'Save as a file', value: 'file', kind: 'primary' }])] });
    if (choice === 'file') asFile = true;
    else if (choice === 'short') { const keep = Math.max(2000, Math.floor(room * 0.5 * 3.2)); if (text.length > keep) text = text.slice(0, Math.floor(keep * 0.7)) + `\n\n[… ${fmtNum(text.length - keep)} characters left out …]\n\n` + text.slice(-Math.floor(keep * 0.3)); }
    else return;
  }
  A.sendBusy = true;
  try {
    await ensureSession(text);
    lsSet('studio.session.' + S.ws.id, A.session); prefSet('session:' + S.ws.id, A.session);
    const planned = S.planMode || A.planOnce; A.planOnce = false;
    const goal = A.goalOnce, goalExtra = A.goalExtra; A.goalOnce = false; A.goalExtra = null;
    const body = { session: A.session, ws: S.ws.id, message: text, model: S.model, auto_apply: S.autoApply, auto_commands: S.autoCmd, ...(planned ? { plan_mode: true } : {}), ...(goal ? { goal: true, ...(goalExtra || {}) } : {}) };
    if (S.prefs['git_branch:' + S.ws.id]) body.git_branch = true;
    if (asFile) body.as_file = true;
    const mentions = mentionsIn(text);
    if (mentions.length) body.mentions = mentions;
    const sent = attach.slice();
    if (sent.length) body.attachments = sent.map(a => a.kind === 'image' || a.kind === 'document' ? { kind: a.kind, name: a.name, data: a.data }
      : a.kind === 'file' ? { kind: 'file', name: a.name, path: a.path } : { kind: 'text', name: a.name, text: a.text });
    const r = await POST('/api/agent/send', body);
    finishCur(); $$('.m-continue, .m-fix', msgsEl).forEach(b => b.remove());
    addUser(asFile ? text.slice(0, 400).trimEnd() + '…' : text, null, { images: sent.filter(a => a.kind === 'image').map(a => ({ src: a.data, name: a.name })), files: sent.filter(a => a.kind === 'text' || a.kind === 'document').map(a => a.name), savedFiles: r.saved_files, plan: planned, saved: asFile, goal });
    planNewMessage(text); A.stick = true; scrollNow();
    if (goal) { A.goal = { text, state: 'active', rounds: 0, why: '', ...(goalExtra ? { parts: goalExtra.goal_parts, max_rounds: goalExtra.goal_rounds } : {}) }; renderGoal(); }
    if (!item) { inp.value = ''; autosize(); M.chosen.clear(); A.attach = []; renderAttachments(); }
    A.turn = 0; A.pTok = 0; A.cTok = 0; A.statusText = 'Starting…'; A.lastReason = ''; A.t0 = Date.now(); A.tEnd = 0; A.pending = 0;
    attachRun(r.run, 0);
  } catch (e) {
    if (item) { A.queue.unshift(item); saveQueue(); renderQueue(); }
    if (e.status === 423 || (e.status === 409 && /session limit/.test(e.message))) {
      toast(e.message, 'warn'); roomToggle(true);       // the message stays in the box for when it is their turn
    } else if (e.status === 409) {
      toast('A run is already active in this workspace — attaching to it.', 'warn');
      try { const act = await GET('/api/agent/active', { ws: S.ws.id }); if (act.run) { A.t0 = Date.now(); attachRun(act.run, 0); } } catch { /* ignore */ }
    } else errToast(e, 'Send failed');
  } finally { A.sendBusy = false; }
}
async function agentStop() {
  if (!A.run || A.stopping) return;
  A.stopping = true; $('#agent-stop').disabled = true; A.statusText = 'Stopping…'; renderStatus();
  try { await POST('/api/agent/stop', { run: A.run }); } catch (e) { errToast(e, 'Stop failed'); A.stopping = false; $('#agent-stop').disabled = false; }
}
$('#agent-send').onclick = () => agentSend();
$('#agent-queue').onclick = () => agentSend({ queue: true });
$('#agent-stop').onclick = agentStop;

/* ---- messages while the agent works: guide it now, or queue for when it ends */
const INPUT_HINT = $('#agent-input').placeholder;
const PLAN_HINT = 'Describe the task: the agent looks around and proposes a plan, changing nothing…';
A.queue = []; A.queueSeq = 0;
try { A.queue = JSON.parse(sessionStorage.getItem('studio.queue') || '[]').filter(q => q && typeof q.text === 'string'); A.queueSeq = A.queue.reduce((n, q) => Math.max(n, q.id || 0), 0); } catch { /* starts empty */ }
function saveQueue() {
  // Pictures and files are too big to keep across a reload; those messages stay in this page only.
  try { sessionStorage.setItem('studio.queue', JSON.stringify(A.queue.filter(q => !q.attach?.length).map(({ id, session, text }) => ({ id, session, text })))); } catch { /* this page keeps it */ }
}
async function guideRun(text) {
  if (!A.run) return;
  A.sendBusy = true;
  try {
    await POST('/api/agent/guide', { run: A.run, message: text });
    $('#agent-input').value = ''; autosize(); M.chosen.clear(); A.stick = true;
  } catch (e) {
    if (e.status === 409) { toast('The run was ending, so your message will be sent when it does.', 'info'); A.sendBusy = false; queueMessage(text); }
    else errToast(e, 'Could not send');
  } finally { A.sendBusy = false; }
}
function queueMessage(text) {
  A.queue.push({ id: ++A.queueSeq, session: A.session, text, attach: A.attach.slice() });
  $('#agent-input').value = ''; autosize(); M.chosen.clear(); A.attach = []; renderAttachments();
  saveQueue(); renderQueue();
  if (!A.running) sendQueued();
}
function sendQueued() {
  if (A.running || A.sendBusy) return;
  const i = A.queue.findIndex(q => q.session === A.session); if (i < 0) return;
  const [item] = A.queue.splice(i, 1); saveQueue(); renderQueue();
  agentSend({ item });
}
function renderQueue() {
  const row = $('#queue-row'), mine = A.queue.filter(q => q.session === A.session);
  row.classList.toggle('hidden', !mine.length);
  const drop = q => { A.queue = A.queue.filter(x => x !== q); saveQueue(); renderQueue(); };
  row.replaceChildren(el('div', { class: 'queue-head', text: A.running ? 'Queued for when the agent finishes' : 'Queued messages' }), ...mine.map((q, i) => el('div', { class: 'queue-item' },
    el('span', { class: 'q-n', text: i ? String(i + 1) : 'Next' }),
    el('span', { class: 'q-text', text: q.text + (q.attach?.length ? `  📎 ${q.attach.length}` : ''), title: q.text }),
    el('button', { class: 'q-btn', title: A.running ? (q.attach?.length ? 'Has files, so it waits for the run to end' : 'Steer the agent with it now') : 'Send now', 'aria-label': 'Send now', html: ico('arrow-up', 12), disabled: A.running && q.attach?.length ? '' : null,
      onclick: () => { if (A.running) { drop(q); guideRun(q.text); } else { A.queue = A.queue.filter(x => x !== q); A.queue.unshift(q); sendQueued(); } } }),
    el('button', { class: 'q-btn', title: 'Edit (moves it back to the box)', 'aria-label': 'Edit', html: ico('edit', 12), onclick: () => {
      const inp = $('#agent-input'); if (inp.value.trim()) { toast('Send or clear what is in the box first', 'info'); return; }
      drop(q); inp.value = q.text; A.attach = q.attach || []; renderAttachments(); autosize(); inp.focus(); } }),
    el('button', { class: 'q-btn', title: 'Remove', 'aria-label': 'Remove', html: ico('x', 12), onclick: () => drop(q) }))));
}

/* ---- composer */
function autosize() { const t = $('#agent-input'); t.style.height = 'auto'; t.style.height = Math.min(200, Math.max(58, t.scrollHeight + 2)) + 'px'; }
$('#agent-input').addEventListener('input', autosize);

/* ---- the menu that opens as you type / or @ */
const M = { items: [], sel: 0, kind: '', start: 0, chosen: new Set(), seq: 0, box: null };
function mentionsIn(text) {
  const found = [...M.chosen].filter(p => text.includes('@' + p));
  for (const m of text.matchAll(/(?:^|\s)@([^\s@]+)/g)) if (!found.includes(m[1])) found.push(m[1]);
  return found.slice(0, 5);
}
function closeCommandMenu() { M.items = []; M.kind = ''; if (M.box) M.box.classList.add('hidden'); }
function renderCommandMenu() {
  if (!M.box) { M.box = el('div', { class: 'cmd-menu hidden', role: 'listbox' }); $('#agent-input').parentElement.append(M.box); }
  M.box.classList.toggle('hidden', !M.items.length);
  M.box.replaceChildren(...M.items.map((item, i) => el('div', { class: 'cmd-item' + (i === M.sel ? ' sel' : ''), role: 'option',
    onmousedown: e => { e.preventDefault(); M.sel = i; acceptCommandMenu(); } }, el('span', { class: 'cmd-name', text: item.label }), el('span', { class: 'cmd-desc', text: item.desc || '' }))));
  M.box.children[M.sel]?.scrollIntoView({ block: 'nearest' });
}
async function updateCommandMenu() {
  const inp = $('#agent-input'), before = inp.value.slice(0, inp.selectionStart), seq = ++M.seq;
  const slash = /^\/([a-zA-Z]*)$/.exec(before);
  if (slash && !inp.value.includes('\n')) {
    const q = slash[1].toLowerCase();
    M.kind = 'slash'; M.start = 0; M.sel = 0;
    M.items = COMMANDS.filter(c => c.name.startsWith(q)).map(c => ({ label: '/' + c.name + (c.args ? ' <' + c.args + '>' : ''), desc: c.desc, command: c }));
    renderCommandMenu();
    return;
  }
  const at = /(?:^|\s)@([^\s@]*)$/.exec(before);
  if (at && S.ws) {
    if (!quick.cache || quick.cacheWs !== S.ws.id || Date.now() - quick.cacheT > 30000) {
      try { const d = await GET('/api/files/list', { ws: S.ws.id }); quick.cache = d.files || []; quick.cacheWs = S.ws.id; quick.cacheT = Date.now(); } catch { return; }
      if (seq !== M.seq) return;
    }
    const q = at[1].toLowerCase();
    const paths = quick.cache.map(f => typeof f === 'string' ? f : f.path);
    const rank = p => { const low = p.toLowerCase(), base = low.slice(low.lastIndexOf('/') + 1); return base.startsWith(q) ? 0 : base.includes(q) ? 1 : low.includes(q) ? 2 : 3; };
    M.kind = 'at'; M.start = before.length - at[1].length - 1; M.sel = 0;
    M.items = paths.filter(p => rank(p) < 3).sort((a, b) => rank(a) - rank(b) || a.length - b.length).slice(0, 8).map(p => ({ label: basename(p), desc: p, path: p }));
    renderCommandMenu();
    return;
  }
  closeCommandMenu();
}
function acceptCommandMenu() {
  const inp = $('#agent-input'), item = M.items[M.sel]; if (!item) return;
  if (M.kind === 'slash') {
    inp.value = '/' + item.command.name + (item.command.args ? ' ' : '');
    closeCommandMenu(); autosize(); inp.focus();
    if (!item.command.args) agentSend();
    return;
  }
  const after = inp.value.slice(inp.selectionStart);
  inp.value = inp.value.slice(0, M.start) + '@' + item.path + ' ' + after;
  const caret = M.start + item.path.length + 2;
  inp.setSelectionRange(caret, caret); M.chosen.add(item.path);
  closeCommandMenu(); autosize(); inp.focus();
}
$('#agent-input').addEventListener('input', updateCommandMenu);
$('#agent-input').addEventListener('blur', closeCommandMenu);
$('#agent-input').addEventListener('keydown', e => {
  if (M.items.length) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); M.sel = (M.sel + (e.key === 'ArrowDown' ? 1 : -1) + M.items.length) % M.items.length; renderCommandMenu(); return; }
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing || e.key === 'Tab') { e.preventDefault(); acceptCommandMenu(); return; }
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeCommandMenu(); return; }
  }
  // A touch keyboard has no Shift+Enter, so there Enter makes a new line and the button sends.
  if (e.key === 'Enter' && e.altKey && !e.isComposing) { e.preventDefault(); agentSend({ queue: true }); return; }
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !matchMedia('(pointer: coarse)').matches) { e.preventDefault(); agentSend(); }
});
// The "Include current file" chip was removed on 2026-11-13: @file names what the agent should read, so the open
// file is no longer sent with every message. Kept as a no-op for its many callers.
function updateCtxLabel() {}
function updateComposerState() {
  const inp = $('#agent-input'); inp.disabled = !S.ws; inp.placeholder = S.ws ? 'Ask the agent to…  ( / commands, @ files, ! run a command )' : 'Open a folder to start';
  $('#agent-send').disabled = !S.ws; $('#new-chat').disabled = !S.ws; $('#sess-btn').disabled = !S.ws;
  updateCtxLabel();
}

/* ------------------------------------------------------------------ splitters */
function setupSplitter(id, { cssVar, key, def, min, max, dir, target }) {
  const sp = $(id), root = document.documentElement;
  const saved = parseInt(lsGet(key), 10);
  root.style.setProperty(cssVar, (Number.isFinite(saved) ? clamp(saved, min, typeof max === 'function' ? max() : max) : def) + 'px');
  let start, startVal;
  sp.addEventListener('pointerdown', e => {
    e.preventDefault(); sp.setPointerCapture(e.pointerId); sp.classList.add('drag'); document.body.classList.add('dragging');
    start = dir === 'x' ? e.clientX : e.clientY; startVal = $(target).getBoundingClientRect()[dir === 'x' ? 'width' : 'height'];
    document.body.style.cursor = dir === 'x' ? 'col-resize' : 'row-resize';
  });
  sp.addEventListener('pointermove', e => {
    if (!sp.classList.contains('drag')) return;
    const d = (dir === 'x' ? e.clientX : e.clientY) - start;
    const v = clamp(Math.round(startVal + (sp.dataset.invert ? -d : d)), min, typeof max === 'function' ? max() : max);
    root.style.setProperty(cssVar, v + 'px'); lsSet(key, String(v));
  });
  const end = e => { if (!sp.classList.contains('drag')) return; sp.classList.remove('drag'); document.body.classList.remove('dragging'); document.body.style.cursor = ''; try { sp.releasePointerCapture(e.pointerId); } catch { /* ignore */ } if (TM.active) termFit(TM.active); };
  sp.addEventListener('pointerup', end); sp.addEventListener('pointercancel', end);
  sp.addEventListener('dblclick', () => { root.style.setProperty(cssVar, def + 'px'); lsSet(key, String(def)); if (TM.active) setTimeout(() => termFit(TM.active), 30); });
}
$('#split-right').dataset.invert = '1'; $('#split-bottom').dataset.invert = '1';
setupSplitter('#split-left', { cssVar: '--sidebar-w', key: 'studio.w.left', def: 260, min: 160, max: 640, dir: 'x', target: '#sidebar' });
setupSplitter('#split-right', { cssVar: '--agent-w', key: 'studio.w.right', def: 390, min: 290, max: 760, dir: 'x', target: '#agent' });
setupSplitter('#split-bottom', { cssVar: '--panel-h', key: 'studio.h.bottom', def: 260, min: 100, max: () => Math.round(innerHeight * 0.75), dir: 'y', target: '#panel' });
$('#split-bottom').classList.add('hidden');

/* ------------------------------------------------------------------ the command palette (Ctrl+K) and chat settings */
function paletteActions() {
  const theme = n => () => { applyTheme(n); prefSet('theme', n); };
  return [
    ['New chat', () => newChat(), ''], ['Chat settings', () => chatSettings(), 'reasoning, notifications, dictation'],
    ['Summarize this chat (/compact)', () => compactChat(), ''], [`Plan first: ${S.planMode ? 'turn off' : 'turn on'}`, () => setPlanMode(!S.planMode), ''],
    ['Give the chat a goal…', () => putInBox('/goal '), ''], ['Try a change on a copy (Time-skip)…', () => putInBox('/timeskip '), ''],
    ['Repeat a message (/loop)…', () => putInBox('/loop 10m '), ''], ['New task', () => taskForm(), 'run the agent on a timetable'],
    ['Tasks', () => showView('tasks', 'force'), ''], ['Stop the run', () => agentStop(), ''],
    [`Auto-apply edits: ${S.autoApply ? 'turn off' : 'turn on'}`, () => $('#tg-apply').click(), ''], [`Auto-run commands: ${S.autoCmd ? 'turn off' : 'turn on'}`, () => $('#tg-cmd').click(), ''],
    ['Go to file…', () => openQuick(), 'Ctrl+P'], ['Search in files', () => showView('search', 'force'), 'Ctrl+Shift+F'], ['Find in the Explorer', () => openExFind(), 'Ctrl+Alt+F'], ['Explorer', () => showView('explorer', 'force'), 'Ctrl+Shift+E'],
    ['Source control', () => showView('git', 'force'), ''], ['Skills', () => showView('skills', 'force'), ''], ['Video', () => showView('video', 'force'), ''],
    ['Terminal', () => togglePanel(), 'Ctrl+`'], ['Model gauges', () => $('#btn-dash').click(), ''], ['Waiting room', () => $('#btn-room').click(), ''],
    ['Export this chat', () => $('#chat-export').click(), ''], ['Theme: Dark', theme('dark'), ''], ['Theme: Light', theme('light'), ''], ['Theme: Sepia', theme('sepia'), ''],
    ['Keyboard shortcuts', () => showShortcuts(), 'Ctrl+/'], ['Guide: how to use Studio', () => openGuide(), 'English and Greek'],
  ].map(([label, run, hint]) => ({ label, run, hint, kind: 'action' }));
}
function openPalette() {
  if (overlayStack.some(o => o.ov.dataset.palette)) return;
  const inp = el('input', { type: 'text', placeholder: 'Type a command, or # to find a chat', spellcheck: 'false', autocomplete: 'off', 'aria-label': 'Command palette' });
  const list = el('div', { class: 'qo-list', role: 'listbox' });
  const entry = openOverlay(el('div', { class: 'qo palette' }, inp, list)); entry.ov.dataset.palette = '1';
  let items = [], sel = 0;
  const chats = () => A.sessions.map(c => ({ label: c.title || 'Untitled chat', hint: ago(c.updated), kind: 'chat', run: () => openSession(c.id) }));
  const render = () => {
    const raw = inp.value, chatOnly = raw.startsWith('#'), actOnly = raw.startsWith('>'), q = raw.replace(/^[#>]\s*/, '').trim();
    const pool = chatOnly ? chats() : actOnly ? paletteActions() : [...paletteActions(), ...chats()];
    items = q ? pool.map(it => { const r = fuzzy(q, it.label); return r && { ...it, idx: r.idx, score: r.score }; }).filter(Boolean).sort((a, b) => b.score - a.score) : pool.map(it => ({ ...it, idx: [] }));
    items = items.slice(0, 50); sel = Math.min(sel, Math.max(0, items.length - 1));
    list.replaceChildren(...(items.length ? items.map((it, i) => el('div', { class: 'qo-item' + (i === sel ? ' sel' : ''), role: 'option', onclick: () => choose(i), onmousemove: () => { if (sel !== i) { sel = i; mark(); } } },
      el('span', { html: ico(it.kind === 'chat' ? 'sparkles' : 'list', 14) }), el('span', { class: 'nm', html: hl(it.label, it.idx) }), el('span', { class: 'dr', text: it.hint || '' })))
      : [el('div', { class: 'qo-empty', text: 'Nothing matches' })]));
  };
  const mark = () => { [...list.children].forEach((c, i) => c.classList.toggle('sel', i === sel)); list.children[sel]?.scrollIntoView({ block: 'nearest' }); };
  const choose = i => { const it = items[i]; if (!it) return; closeOverlay(entry); setTimeout(() => it.run(), 0); };
  inp.addEventListener('input', () => { sel = 0; render(); });
  inp.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); mark(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); mark(); }
    else if (e.key === 'Enter') { e.preventDefault(); choose(sel); }
  });
  inp.focus(); render();
}
const SHORTCUTS = [['Ctrl+K', 'Command palette: actions, and # for chats'], ['Ctrl+P', 'Go to file'], ['Ctrl+S', 'Save the file'], ['Ctrl+Shift+F', 'Search in files'],
  ['Ctrl+Alt+F', 'Find folders, files and text in the Explorer (Ctrl+F there too)'], ['Ctrl+Shift+E', 'Explorer'], ['Ctrl+B', 'Show or hide the side bar'], ['Ctrl+`', 'Show or hide the terminal'], ['Alt+Z', 'Wrap long lines'], ['Alt+M', 'Show or hide the code map beside the editor'],
  ['Ctrl+Shift+D', 'Dictate into the chat box (when dictation is on)'], ['Ctrl+/', 'This list'], ['Enter', 'Send, or steer the agent while it works'],
  ['Alt+Enter', 'Queue a message for after the run'], ['Shift+Enter', 'New line in the chat box'], ['/', 'Chat box commands; /help lists them'], ['@', 'Mention a file'], ['Esc', 'Close a dialog or menu']];
function showShortcuts() {
  dialog({ title: 'Keyboard shortcuts', small: false, body: el('div', { class: 'md-table' }, el('table', {}, el('tbody', {}, SHORTCUTS.map(([k, what]) => el('tr', {}, el('td', {}, el('kbd', { text: k })), el('td', { text: what })))))),
    buttons: [{ label: 'Close', value: false, kind: 'primary' }] });
}
/* Chat settings: reasoning effort for this chat, a notice when a run ends while you are elsewhere, and dictation */
const EFFORT_LABELS = [['', 'The Manager\'s default'], ['none', 'None: answer directly'], ['low', 'Low'], ['medium', 'Medium'], ['high', 'High'], ['xhigh', 'Highest']];
async function chatSettings() {
  const effort = el('select', { class: 'dlg-input' }, EFFORT_LABELS.map(([v, l]) => el('option', { value: v, text: l }))); effort.value = A.effort || '';
  const notify = el('select', { class: 'dlg-input' }, [['off', 'Off'], ['notify', 'A notification'], ['sound', 'A notification and a sound']].map(([v, l]) => el('option', { value: v, text: l }))); notify.value = lsGet('studio.notify') || 'off';
  const supported = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  const dict = el('input', { type: 'checkbox', checked: lsGet('studio.dictation') === '1' ? '' : null, disabled: supported ? null : '' });
  const lang = el('select', { class: 'dlg-input' }, [['', 'The browser\'s language'], ['el-GR', 'Greek'], ['en-US', 'English']].map(([v, l]) => el('option', { value: v, text: l }))); lang.value = lsGet('studio.dictationLang') || '';
  const body = el('div', { class: 'task-form' },
    el('label', { class: 'task-field' }, el('span', { text: 'Reasoning effort for this chat' }), effort, el('small', { class: 'dlg-note', text: 'How long the model thinks before it answers. More is slower and often better on hard work. A Manager profile set to no thinking always wins.' })),
    el('label', { class: 'task-field' }, el('span', { text: 'When a run ends or waits for you while you are in another tab or window' }), notify),
    el('div', { class: 'task-field' }, el('span', { text: 'Dictation' }), el('label', { class: 'dlg-check' }, dict, ' Show a microphone button in the chat box'), lang,
      el('small', { class: 'dlg-note', text: supported ? 'In Chrome and Edge your voice is sent to Google or Microsoft to be turned into text; it never goes through Studio.' : 'This browser cannot turn speech into text. Chrome, Edge and Safari can.' })));
  const ok = await dialog({ title: 'Chat settings', body, small: false, buttons: [{ label: 'Cancel', value: false }, { label: 'Save', value: true, kind: 'primary' }] });
  if (ok !== true) return;
  lsSet('studio.notify', notify.value); lsSet('studio.dictation', dict.checked ? '1' : '0'); lsSet('studio.dictationLang', lang.value);
  if (notify.value !== 'off' && 'Notification' in window && Notification.permission === 'default') Notification.requestPermission();
  setupMic();
  if (effort.value !== (A.effort || '')) {
    if (!S.ws) { toast('Open a folder first', 'info'); return; }
    try { await ensureSession('New chat'); const r = await POST('/api/agent/session/settings', { ws: S.ws.id, id: A.session, effort: effort.value }); A.effort = r.effort; toast('Saved for this chat', 'success', 2000); }
    catch (e) { errToast(e, 'Chat settings'); }
  }
}
$('#chat-settings').onclick = () => chatSettings();
function chime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)(), g = ctx.createGain(); g.connect(ctx.destination);
    [[660, 0], [880, 0.14]].forEach(([f, at]) => { const o = ctx.createOscillator(); o.frequency.value = f; o.connect(g); g.gain.setValueAtTime(0.12, ctx.currentTime + at); o.start(ctx.currentTime + at); o.stop(ctx.currentTime + at + 0.12); });
    setTimeout(() => ctx.close(), 600);
  } catch { /* no sound */ }
}
function attention(text) {
  const mode = lsGet('studio.notify') || 'off';
  if (mode === 'off' || (!document.hidden && document.hasFocus())) return false;
  if ('Notification' in window && Notification.permission === 'granted') { const n = new Notification('Eidoloom Studio', { body: text, tag: 'studio-run' }); n.onclick = () => { window.focus(); n.close(); }; }
  if (mode === 'sound') chime();
  A.notified = (A.notified || 0) + 1;
  return true;
}
function notifyDone(reason) { attention(`${DONE_TXT[reason] || 'The run ended'}${A.session ? ': ' + ($('#sess-title').textContent || '') : ''}`); }
function notifyWaiting(d) { attention(d.kind === 'question' ? 'The agent has a question for you' : 'The agent is waiting for your approval'); }
/* Dictation: the browser turns speech into text in the chat box; nothing is sent until you press Enter. */
const MIC = { rec: null, on: false, quiet: null };
function setupMic() {
  const supported = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  $('#mic-btn').classList.toggle('hidden', !(supported && lsGet('studio.dictation') === '1'));
  if (MIC.on && $('#mic-btn').classList.contains('hidden')) micStop();
}
function micStop() { MIC.on = false; clearTimeout(MIC.quiet); try { MIC.rec?.stop(); } catch { /* already stopped */ } $('#mic-btn').classList.remove('on'); $('#mic-btn').setAttribute('aria-pressed', 'false'); }
function micToggle() {
  if (MIC.on) { micStop(); return; }
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition; if (!Rec || $('#mic-btn').classList.contains('hidden')) return;
  const rec = MIC.rec = new Rec(), inp = $('#agent-input'), start = inp.value ? inp.value.replace(/\s*$/, ' ') : '';
  rec.lang = lsGet('studio.dictationLang') || navigator.language || 'en-US'; rec.interimResults = true; rec.continuous = true;
  let final = '';
  const later = () => { clearTimeout(MIC.quiet); MIC.quiet = setTimeout(micStop, 3500); };       // stops after 3.5 s of silence
  rec.onresult = e => {
    let interim = '';
    for (let i = e.resultIndex; i < e.results.length; i++) { if (e.results[i].isFinal) final += e.results[i][0].transcript; else interim += e.results[i][0].transcript; }
    inp.value = start + final + interim; autosize(); later();
  };
  rec.onerror = e => { if (e.error !== 'aborted' && e.error !== 'no-speech') toast(e.error === 'not-allowed' ? 'The browser was not allowed to use the microphone' : 'Dictation stopped: ' + e.error, 'warn'); micStop(); };
  rec.onend = () => { if (MIC.on) micStop(); };
  try { rec.start(); } catch (e) { toast('Dictation could not start: ' + e.message, 'warn'); return; }
  MIC.on = true; $('#mic-btn').classList.add('on'); $('#mic-btn').setAttribute('aria-pressed', 'true'); inp.focus(); later();
}
$('#mic-btn').onclick = micToggle;
setupMic();

/* ------------------------------------------------------------------ keyboard shortcuts */
window.addEventListener('keydown', e => {
  if ($('#app').classList.contains('hidden')) return;
  const mod = e.ctrlKey || e.metaKey, k = e.key;
  if (k === 'Escape') {
    if (overlayStack.length) { e.preventDefault(); e.stopPropagation(); closeOverlay(overlayStack[overlayStack.length - 1]); }
    else if (ctxMenu) { closeCtxMenu(); e.stopPropagation(); }   // closing a menu is all that Esc does then
    else if (!$('#sess-menu').classList.contains('hidden')) $('#sess-menu').classList.add('hidden');
    return;
  }
  if (mod && !e.shiftKey && !e.altKey && (k === 's' || k === 'S')) { e.preventDefault(); if (S.active && S.active.kind === 'file') saveTab(S.active); return; }
  if (mod && !e.shiftKey && !e.altKey && (k === 'p' || k === 'P')) { e.preventDefault(); openQuick(); return; }
  if (mod && !e.shiftKey && !e.altKey && (k === 'k' || k === 'K')) { e.preventDefault(); openPalette(); return; }
  if (mod && !e.shiftKey && !e.altKey && k === '/') { e.preventDefault(); showShortcuts(); return; }
  if (mod && e.shiftKey && (k === 'd' || k === 'D')) { e.preventDefault(); micToggle(); return; }
  if (mod && e.shiftKey && (k === 'f' || k === 'F')) { e.preventDefault(); showView('search', 'force'); return; }
  if (mod && e.altKey && !e.shiftKey && (k === 'f' || k === 'F' || e.code === 'KeyF')) { e.preventDefault(); openExFind(); return; }
  if (mod && !e.altKey && !e.shiftKey && (k === 'f' || k === 'F') && (document.activeElement === treeEl || $('#ex-found').contains(document.activeElement))) { e.preventDefault(); openExFind(); return; }
  if (mod && e.shiftKey && (k === 'e' || k === 'E')) { e.preventDefault(); showView('explorer', 'force'); return; }
  if (mod && (k === '`' || e.code === 'Backquote')) { e.preventDefault(); togglePanel(); return; }
  if (mod && !e.shiftKey && (k === 'b' || k === 'B')) { e.preventDefault(); showView(lsGet('studio.view') || 'explorer'); return; }
  if (e.altKey && !mod && (k === 'z' || k === 'Z')) { e.preventDefault(); setWrap(!S.wrap); return; }
  if (e.altKey && !mod && !e.shiftKey && (k === 'm' || k === 'M' || e.code === 'KeyM')) { e.preventDefault(); setMinimap(!S.minimap); return; }
}, true);
window.addEventListener('beforeunload', e => { if (S.tabs.some(t => t.dirty)) { e.preventDefault(); e.returnValue = ''; } });

/* ------------------------------------------------------------------ boot */
function prefApply(key, value) {
  S.prefs[key] = value;
  if (key === 'theme') { if (value !== currentTheme()) applyTheme(value); }
  else if (key === 'auto_apply') { S.autoApply = value; lsSet('studio.autoApply', value ? '1' : '0'); setToggle($('#tg-apply'), value); }
  else if (key === 'auto_commands') { S.autoCmd = value; lsSet('studio.autoCmd', value ? '1' : '0'); setToggle($('#tg-cmd'), value); }
  else if (key === 'wrap') { if (value !== S.wrap) setWrap(value); }
  else if (key === 'minimap') { if (value !== S.minimap) setMinimap(value); }
  else if (key === 'models_shown') { renderModels(); }
  else if (key === 'pc_names') { updateWsUI(); }
  else if (key === 'model') { if (S.models.some(m => m.name === value)) { S.model = value; lsSet('studio.model', value); $('#model-select').value = value; effortChip(); } }
  else if (S.ws && key === 'git_branch:' + S.ws.id) gitRefresh();
  else if (S.ws && key === 'session:' + S.ws.id && !A.running && value !== A.session) openSession(value, { silent: true });
}
async function prefsLoad() {
  // A choice made while this request is on its way is newer than what it returns.
  const touched = S.prefsTouched = new Set();
  let saved; try { saved = (await GET('/api/prefs')).prefs; } catch { S.prefsTouched = null; return; } finally { if (S.prefsTouched === touched) S.prefsTouched = null; }
  const mine = Object.fromEntries([...touched].map(key => [key, S.prefs[key]]));
  // The first time on a server with nothing saved, this browser's settings become the user's.
  const local = { theme: lsGet('studio.theme'), auto_apply: lsGet('studio.autoApply') === null ? null : lsGet('studio.autoApply') === '1', auto_commands: lsGet('studio.autoCmd') === null ? null : lsGet('studio.autoCmd') === '1', wrap: lsGet('studio.wrap') === null ? null : lsGet('studio.wrap') === '1', minimap: lsGet('studio.minimap') === null ? null : lsGet('studio.minimap') === '1', model: lsGet('studio.model') };
  S.prefs = { ...mine };
  for (const [key, value] of Object.entries(saved)) if (!touched.has(key)) prefApply(key, value);
  for (const [key, value] of Object.entries(local)) if (value !== null && value !== undefined && !(key in saved)) prefSet(key, value);
}
function syncApply(d) {
  if (d.origin === CLIENT_ID) return;
  const here = S.ws && (!d.ws || d.ws === S.ws.id);
  if (d.type === 'prefs') prefApply(d.key, d.value);
  else if (d.type === 'session') { if (here) { loadSessions(); if (A.session === d.id && !A.running) openSession(d.id, { silent: true }); } }      // rewound in another window
  else if (d.type === 'session_list') { if (here) loadSessions(); }
  else if (d.type === 'tasks') { tasksRefresh(); if (d.event === 'ended') { taskEnded(d); if (here) loadSessions(); } }
  else if (d.type === 'sessions') { if (here) { loadSessions().then(() => { if (A.session && !A.running && d.id === A.session) newChat(); }); } }
  else if (d.type === 'run_started') {
    if (S.ws && d.ws === S.ws.id && A.run !== d.run && (!d.task || d.session === A.session)) { A.session = d.session; openSession(d.session, { upToLastUser: true, silent: true }).then(opened => { if (!opened) return; A.session = d.session; renderSessionTitle(); A.t0 = Date.now(); attachRun(d.run, 0); loadSessions(); }); }
  }
  else if (d.type === 'skills') skillsRefresh();
  else if (d.type === 'terminals') { if (here) termSync(); }
  else if (d.type === 'shell') { if (here && !A.running && d.session === A.session) openSession(A.session, { silent: true }); else if (here) loadSessions(); }
  else if (d.type === 'files') {
    if (!here) return;
    if (d.path) onDiskChanged(d.path); else { for (const t of S.tabs) if (t.kind === 'file') onDiskChanged(t.path); refreshTreeSoon(); }
    gitRefresh();
  }
  else if (d.type === 'git') { if (here) { for (const t of S.tabs) if (t.kind === 'file') onDiskChanged(t.path); refreshTreeSoon(); gitRefresh(); } }
  else if (d.type === 'workspace') { if (here) POST('/api/workspace/open', { path: S.ws.path }).then(r => setWorkspace(r.workspace)).catch(e => {
    if (e.status === 403 || e.status === 404) { toast('This folder is no longer shared with you', 'warn', 4000); setTimeout(() => { sessionStorage.removeItem('studio.windowFolder'); location.reload(); }, 1500); }
  }); }
}
async function syncCatchUp() {
  // Changes made while the stream was down were not delivered, so read the current state instead.
  await prefsLoad(); termSync(); gitRefresh();
  if (!S.ws || A.running) return;
  await loadSessions();
  let act = null; try { act = await GET('/api/agent/active', { ws: S.ws.id }); } catch { return; }
  if (act && act.run && A.run !== act.run) syncApply({ type: 'run_started', ws: S.ws.id, session: act.session || A.session, run: act.run });
}
async function bootApp() {
  $('#app').classList.remove('hidden');
  setToggle($('#tg-apply'), S.autoApply); setToggle($('#tg-cmd'), S.autoCmd); setPlanMode(S.planMode);
  setWrap(S.wrap); setMinimap(S.minimap);
  try { S.config = await GET('/api/config'); } catch (e) { if (e.status === 401) return; S.config = {}; }
  try { S.models = (await GET('/api/models')).models || []; } catch (e) { if (e.status === 401) return; S.models = []; errToast(e, 'Could not load models'); }
  renderModels();
  codexUsageInit();
  effortChip();
  await prefsLoad();
  if (S.config.gateway_ok === false) toast('The model server is not reachable, so the agent cannot work.' + (isAdmin() ? ' Account & settings → Models connects one.' : ''), 'warn', 8000);
  showView(['search', 'git', 'skills'].includes(lsGet('studio.view')) ? lsGet('studio.view') : 'explorer', 'force');
  if (LAYOUT.tablet.matches && !isPhone()) { $('#sidebar').classList.add('collapsed'); $('#split-left').classList.add('hidden'); }      // it opens over the editor, on request
  if (S.ws) { updateWsUI(); await loadTree(); agentInit(); }
  else {
    let saved = null; try { saved = JSON.parse(lsGet('studio.ws')); } catch { /* ignore */ }
    // A window keeps its own folder across a reload. A new window, or another browser, opens the user's latest.
    let mine = null; try { mine = sessionStorage.getItem('studio.windowFolder'); } catch { /* ignore */ }
    const wanted = mine || S.prefs.last_ws;
    if (wanted) saved = { path: wanted };
    updateWsUI(); agentEmpty();
    if (saved && saved.path) {
      try { const r = await POST('/api/workspace/open', { path: saved.path }); await setWorkspace(r.workspace, { restoring: true }); } catch (e) { if (e.status !== 401) lsSet('studio.ws', ''); try { sessionStorage.removeItem('studio.windowFolder'); } catch { /* ignore */ } }
    }
  }
  if (lsGet('studio.panel') === '1' && S.ws) showPanel(true);
  S.booted = true;
  dashStart();
  if (C.open) cdashToggle(true); else cdashPoll();
  updatesNotice();
  roomStart();
}

/* ------------------------------------------------------------------ model dashboard */
const D = { open: false, free: false, x: 0, y: 0, timer: null, busy: false, chars: null, charsAt: 0, speed: 0, built: null };
const GAUGES = [['gpu', 'GPU'], ['vram', 'VRAM'], ['cpu', 'CPU'], ['ram', 'RAM'], ['speed', 'Speed'], ['ctx', 'Context'], ['temp', 'GPU temp'], ['press', 'Pressure']];
try { Object.assign(D, JSON.parse(lsGet('studio.dash') || '{}')); } catch { /* ignore */ }
function dashSave() { lsSet('studio.dash', JSON.stringify({ open: D.open, free: D.free, x: D.x, y: D.y })); }
function fmtGB(n) { return ((n || 0) / 1073741824).toFixed(1) + ' GB'; }
function pctOf(a, b) { return b ? a / b * 100 : 0; }
function dashBuild() {
  const box = $('#dash');
  const name = el('b', { class: 'dash-state' }), model = el('span', { class: 'dash-model' }), detail = el('div', { class: 'dash-detail' });
  const detach = el('button', { class: 'icon-btn', onclick: () => { dashPlace(!D.free); } });
  const head = el('div', { class: 'dash-head' }, el('span', { class: 'dash-dot' }), el('div', { class: 'dash-title' }, name, model), detach,
    el('button', { class: 'icon-btn', title: 'Collapse', 'aria-label': 'Collapse', html: ico('x', 14), onclick: () => dashToggle(false) }));
  const gauges = {};
  const grid = el('div', { class: 'dash-grid' }, GAUGES.map(([key, label]) => {
    const value = el('span', { class: 'dash-val' }), unit = el('small'), ring = el('div', { class: 'dash-ring', role: 'img' }, el('div', { class: 'dash-num' }, value, unit));
    gauges[key] = { ring, value, unit, label };
    return el('div', { class: 'dash-gauge' }, ring, el('span', { class: 'dash-lbl', text: label }));
  }));
  const rows = el('dl', { class: 'dash-rows' }), loaded = el('div', { class: 'dash-loaded' });
  box.replaceChildren(head, detail, grid, loaded, rows);
  head.addEventListener('pointerdown', e => {
    if (!D.free || e.target.closest('button')) return;
    const r = box.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
    head.setPointerCapture(e.pointerId);
    const move = m => { D.x = m.clientX - dx; D.y = m.clientY - dy; dashPosition(); };
    const up = () => { head.removeEventListener('pointermove', move); head.removeEventListener('pointerup', up); dashSave(); };
    head.addEventListener('pointermove', move); head.addEventListener('pointerup', up);
  });
  D.built = { name, model, detail, detach, gauges, rows, loaded };
}
function dashPosition() {
  const box = $('#dash');
  box.classList.toggle('free', D.free);
  if (!D.free) {
    // Docked: the top-right corner of the editor, under the tab bar and against the agent panel.
    const r = $('#editor-stack').getBoundingClientRect();
    box.style.maxHeight = Math.max(160, innerHeight - r.top - 30) + 'px';
    box.style.left = Math.max(0, r.right - box.offsetWidth) + 'px'; box.style.top = r.top + 'px';
    return;
  }
  box.style.maxHeight = '';
  D.x = Math.max(0, Math.min(D.x, innerWidth - box.offsetWidth)); D.y = Math.max(0, Math.min(D.y, innerHeight - box.offsetHeight));
  box.style.left = D.x + 'px'; box.style.top = D.y + 'px';
}
function dashPlace(free) {
  const r = $('#dash').getBoundingClientRect();
  D.free = free; if (free) { D.x = r.left - 24; D.y = r.top - 24; }
  dashDetachIcon(); dashPosition(); dashSave();
}
function dashDetachIcon() {
  const b = D.built.detach, label = D.free ? 'Dock beside the agent panel' : 'Detach into a movable window';
  b.innerHTML = ico(D.free ? 'dock' : 'detach', 14); b.title = label; b.setAttribute('aria-label', label);
}
function dashToggle(open) {
  if (open && C.open) cdashToggle(false);      // the two dashboards share the corner
  D.open = open; dashSave();
  setTimeout(roomPosition);
  $('#sb-model').setAttribute('aria-expanded', open ? 'true' : 'false');
  $('#btn-dash').setAttribute('aria-pressed', open ? 'true' : 'false');
  $('#dash').classList.toggle('hidden', !open);
  if (open) { if (!D.built) dashBuild(); dashDetachIcon(); dashPosition(); }
  dashPoll();
}
// The Ollama Manager's colors. A load gauge turns orange near full, and red when it shows 100.
function dashGauge(key, percent, value, unit, base, { fixed = false } = {}) {
  const g = D.built.gauges[key], p = Math.max(0, Math.min(100, percent || 0));
  g.ring.style.setProperty('--value', p.toFixed(1));
  g.ring.style.setProperty('--gauge', `var(--gauge-${fixed ? base : p >= 99.5 ? 'red' : p >= 90 ? 'orange' : base})`);
  g.value.textContent = value; g.unit.textContent = unit || '';
  g.ring.setAttribute('aria-label', `${g.label}: ${value}${unit ? ' ' + unit : ''}`);
}
function dashRender(d) {
  $$('#sb-model .dash-dot, #dash > .dash-head .dash-dot').forEach(n => { n.dataset.state = d.state; });   // only the model's own dots, not the people's or the tools'
  $('#sb-model-text').textContent = d.state === 'work' && D.speed >= 1 ? `${d.label} · ${Math.round(D.speed)} tok/s` : d.load ? `${d.label} ${d.load.percent}%` : d.label;
  if (!D.open || !D.built) return;
  const b = D.built, m = d.metrics || {}, sys = m.system || {}, gpu = m.gpu || {}, proc = m.process || {}, place = m.placement || {}, gen = d.last_generation || {};
  const has = !!d.metrics, temp = (gpu.gpu_temps || {}).junction ?? (gpu.gpu_temps || {}).edge;
  b.name.textContent = d.label; b.model.textContent = d.model; const detail = d.load ? `${d.load.percent}% · ${d.load.step}` : d.detail || ''; b.detail.textContent = detail; b.detail.title = d.detail || detail;
  const pc = v => has ? String(Math.round(v)) : '–';
  const ram = pctOf(sys.used, sys.total), vram = pctOf(gpu.vram_used, gpu.vram_total), press = (m.strain || {}).percent || 0;
  dashGauge('gpu', gpu.gpu_busy, pc(gpu.gpu_busy || 0), has ? '%' : '', 'blue');
  dashGauge('vram', vram, pc(vram), has ? '%' : '', 'blue');
  dashGauge('cpu', sys.cpu_percent, pc(sys.cpu_percent || 0), has ? '%' : '', 'cyan');
  dashGauge('ram', ram, pc(ram), has ? '%' : '', 'mint');
  dashGauge('speed', D.speed, D.speed >= 1 ? String(Math.round(D.speed)) : '–', D.speed >= 1 ? 'tok/s' : '', 'green', { fixed: true });
  dashGauge('ctx', gen.context_percent, gen.context_percent != null ? String(Math.round(gen.context_percent)) : '–', gen.context_percent != null ? '%' : '', 'blue');
  dashGauge('temp', temp ? temp / 110 * 100 : 0, temp ? String(Math.round(temp)) : '–', temp ? '°C' : '', 'coral');
  dashGauge('press', press, pc(press), has ? '%' : '', ['green', 'blue', 'yellow', 'orange', 'red'].includes((m.strain || {}).band) ? m.strain.band : 'blue', { fixed: true });
  // The Manager reports a model's planned placement even when it isn't loaded.
  const inMemory = has && d.state !== 'off';
  const rows = [['Model in VRAM', inMemory ? fmtGB(place.vram) : '–'], ['Model in RAM', inMemory ? fmtGB(place.ram) : '–'], ['Free RAM', has ? fmtGB(sys.available) : '–'],
    ['Pressure', has && m.strain ? `${m.strain.label || ''} · ${m.strain.source || ''}` : '–'],
    ['GPU power', gpu.gpu_power_limit_w ? `${Math.round(gpu.gpu_power_w || 0)} of ${Math.round(gpu.gpu_power_limit_w)} W` : '–'],
    ['Runtime', proc.count ? `${proc.count} processes, ${fmtGB(proc.rss)}` : '–'], ['Requests', String(d.active_requests || 0)]];
  b.rows.replaceChildren(...rows.flatMap(([k, v]) => [el('dt', { text: k }), el('dd', { text: v })]));
  dashLoaded(b.loaded, d, m);
}
// What is in the graphics card's memory and in RAM now, one row per model or program (metrics.residents).
function dashLoaded(box, d, m) {
  const res = m.residents;
  if (!res) { box.replaceChildren(); return; }
  const items = res.items.map(i => ({ ...i }));
  for (const i of items) if (/strata/.test(i.what) && /strata/.test(d.model || '') && d.state !== 'off') { i.full = i.name; i.name = d.model; }
  const card = res.card || {}, other = (card.used || 0) - items.reduce((n, i) => n + (i.vram || 0), 0);
  if (other > 256 * 1048576) items.push({ name: 'Other', what: 'the driver, the desktop and small programs', vram: other, ram: 0 });
  const gb = n => n >= 1073741824 ? (n / 1073741824).toFixed(1) + ' GB' : n >= 1048576 ? Math.round(n / 1048576) + ' MB' : '–';
  const total = card.total || 1;
  box.replaceChildren(
    el('div', { class: 'dash-loaded-head' }, el('b', { text: 'Loaded now' }), el('small', { text: `${card.name || 'Graphics card'} · ${gb(card.used || 0)} of ${gb(card.total || 0)}` })),
    el('div', { class: 'dash-loaded-cols' }, el('span'), el('small', { text: 'VRAM' }), el('small', { text: 'RAM' })),
    ...(items.length ? items.map(i => el('div', { class: 'dash-res', title: `${i.full || i.name}: ${i.what}` },
      el('div', { class: 'dash-res-name' }, el('span', { text: i.name }), el('small', { text: i.what }),
        el('div', { class: 'dash-res-bar' }, el('i', { style: `width:${Math.min(100, (i.vram || 0) / total * 100).toFixed(1)}%` }))),
      el('span', { class: 'dash-res-n', text: gb(i.vram || 0) }), el('span', { class: 'dash-res-n', text: gb(i.ram || 0) })))
      : [el('small', { class: 'dash-res-none', text: 'No model or tool is loaded.' })]),
    ...dashCleanup(d, gb));
}
/* The failsafe after the last run (cleanup_main): what it freed, for ten minutes, and when the Manager unloads Strata. */
function dashCleanup(d, gb) {
  const out = [], c = d.cleanup, ago = c ? Date.now() / 1000 - c.at : Infinity;
  if (c && ago < 600) {
    const freed = [c.freed_vram > 64 * 1048576 ? gb(c.freed_vram) + ' on the card' : '', c.freed_ram > 64 * 1048576 ? gb(c.freed_ram) + ' of RAM' : ''].filter(Boolean).join(', ');
    out.push(el('small', { class: 'dash-res-note', title: (c.steps || []).join('; '),
      text: `After the last run: ${freed ? 'freed ' + freed : (c.steps || []).join(', ')} · ${ago < 90 ? 'just now' : Math.round(ago / 60) + ' min ago'}` }));
  }
  const idle = d.strata_idle || {};
  if (idle.minutes) out.push(el('small', { class: 'dash-res-note', text: `Strata unloads after ${idle.minutes} min with nothing to do` +
    (idle.unload_at ? ` · in ${Math.max(1, Math.round((idle.unload_at - Date.now() / 1000) / 60))} min` : '') }));
  else if (idle.minutes === 0) out.push(el('small', { class: 'dash-res-note', text: 'Strata stays loaded until it is stopped' }));
  return out;
}
async function dashPoll() {
  clearTimeout(D.timer);
  if (D.busy) return;
  D.busy = true;
  try {
    if (!document.hidden) {
      const d = await GET('/api/model/status', { model: S.model, full: D.open ? 1 : undefined });
      const now = performance.now();
      if (D.chars != null && now > D.charsAt) {
        // About 4 characters per token: an estimate from the text this server streamed.
        const rate = Math.max(0, d.stream_chars - D.chars) / 4 / ((now - D.charsAt) / 1000);
        D.speed = d.state === 'work' ? D.speed * .5 + rate * .5 : 0;
      }
      D.chars = d.stream_chars; D.charsAt = now;
      dashRender(d);
    }
  } catch (e) {
    if (e.status === 401) { D.busy = false; return; }
    dashRender({ state: 'err', label: 'Status unavailable', detail: String(e.message || e), model: S.model, metrics: null });
  }
  D.busy = false;
  D.timer = setTimeout(dashPoll, D.open ? 1000 : 5000);
}
function dashStart() {
  dashToggle(D.open);
}
$('#sb-model').onclick = $('#btn-dash').onclick = () => dashToggle(!D.open);

/* ------------------------------------------------------------------ ComfyUI dashboard (admins, when comfyui_url is set) */
const C = { open: lsGet('studio.cdash') === '1', timer: null, busy: false, built: null, off: false, shown: '' };
function cdashToggle(open) {
  if (open && D.open) dashToggle(false);
  C.open = open; lsSet('studio.cdash', open ? '1' : '0');
  $('#sb-comfy').setAttribute('aria-expanded', open ? 'true' : 'false'); $('#btn-cdash').setAttribute('aria-pressed', open ? 'true' : 'false');
  $('#cdash').classList.toggle('hidden', !open);
  if (open) { if (!C.built) cdashBuild(); cdashPosition(); }
  cdashPoll();
}
function cdashPosition() {
  const box = $('#cdash'), r = $('#editor-stack').getBoundingClientRect();     // where the model dashboard docks
  box.style.maxHeight = Math.max(160, innerHeight - r.top - 30) + 'px';
  box.style.left = Math.max(0, r.right - box.offsetWidth) + 'px'; box.style.top = r.top + 'px';
}
function cdashBuild() {
  const name = el('b', { class: 'dash-state', text: 'ComfyUI' }), device = el('span', { class: 'dash-model' });
  const head = el('div', { class: 'dash-head' }, el('span', { class: 'dash-dot' }), el('div', { class: 'dash-title' }, name, device),
    el('button', { class: 'icon-btn cd-expand', title: 'Show the nodes', 'aria-label': 'Show the nodes', html: ico('layers', 14), onclick: () => openComfyTab() }),
    el('button', { class: 'icon-btn', title: 'Collapse', 'aria-label': 'Collapse', html: ico('x', 14), onclick: () => cdashToggle(false) }));
  const model = el('select', { class: 'cd-select', 'aria-label': 'Default picture model', onchange: async () => {
    try { await POST('/api/comfyui/settings', { default_model: model.value }); toast(`Pictures now use ${model.selectedOptions[0].textContent} unless you ask for another model.`, 'success', 3500); }
    catch (e) { errToast(e, 'Could not change the model'); }
  } }, [['z-image-turbo', 'Z-Image Turbo'], ['z-image-base', 'Z-Image Base'], ['sdxl', 'SDXL'], ['qwen-image', 'Qwen-Image (unloads Strata)'], ['auto', 'Choose by style']]
    .map(([value, label]) => el('option', { value, text: label })));
  const modelRow = el('label', { class: 'cd-model', title: 'Used when the agent does not name a model. Choose by style: SDXL for painted and drawn styles, Z-Image Turbo for the rest. Qwen-Image gives the best pictures but unloads Strata while it works, two or three minutes a batch.' },
    el('span', { text: 'Default model' }), model);
  const music = el('select', { class: 'cd-select', 'aria-label': 'Music model', onchange: async () => {
    try { await POST('/api/comfyui/settings', { music_model: music.value }); toast(`Songs now use ${music.selectedOptions[0].textContent} unless you ask for another model.`, 'success', 3500); }
    catch (e) { errToast(e, 'Could not change the music model'); }
  } }, [['ace-step', 'ACE-Step 1.5'], ['heartmula', 'HeartMuLa 3B']].map(([value, label]) => el('option', { value, text: label })));
  const musicRow = el('label', { class: 'cd-model', title: 'Used for songs when the agent does not name a model. ACE-Step 1.5 is fast and runs in ComfyUI, with BPM, key and an XL "best" mode. HeartMuLa 3B follows lyrics closely and makes up to 4 minutes, at about real time or slower, and may unload Strata while it works.' },
    el('span', { text: 'Music' }), music);
  const swap = el('div', { class: 'cd-swap hidden', role: 'status' });
  const prompt = el('div', { class: 'cd-prompt' }), meta = el('div', { class: 'cd-meta' });
  const fill = el('div', { class: 'cd-fill' }), bar = el('div', { class: 'cd-bar', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100' }, fill);
  const step = el('div', { class: 'cd-step' });
  const now = el('div', { class: 'cd-now' }, prompt, meta, bar, step);
  const rows = el('dl', { class: 'dash-rows' }), recentHead = el('div', { class: 'cd-head', text: 'Latest pictures' }), recent = el('div', { class: 'cd-recent' });
  $('#cdash').replaceChildren(head, modelRow, musicRow, swap, now, rows, recentHead, recent);
  C.built = { device, model, music, swap, now, prompt, meta, bar, fill, step, rows, recentHead, recent };
}
function cdashRender(d) {
  const pill = $('#sb-comfy'), cur = d.current;
  pill.classList.toggle('hidden', !d.available); $('#btn-cdash').classList.toggle('hidden', !d.available);   // the status bar is hidden on phones
  if (!d.available) { $('#cdash').classList.add('hidden'); return; }
  C.last = d;                                  // the node tab's list of pictures comes from here
  $$('#sb-comfy .dash-dot, #cdash .dash-dot').forEach(n => { n.dataset.state = d.state || 'off'; });
  const waiting = d.pending ? ` · ${d.pending} waiting` : '';
  $('#sb-comfy-text').textContent = (d.state === 'err' ? 'ComfyUI: offline' : cur ? (cur.of ? `Making ${cur.step}/${cur.of}` : 'Making…') + waiting : 'ComfyUI' + waiting)
    + (d.swap && d.swap.active ? ' · Strata unloaded' : '');
  if (!C.open || !C.built) return;
  const b = C.built, mem = d.memory || {};
  if (d.default_model && document.activeElement !== b.model) b.model.value = d.default_model;
  if (d.music_model && document.activeElement !== b.music) b.music.value = d.music_model;
  const swapping = !!(d.swap && d.swap.active);
  b.swap.classList.toggle('hidden', !swapping);
  if (swapping) b.swap.textContent = `Strata is unloaded so a big picture model can use the whole card${d.swap.since ? `, for ${Math.round((Date.now() / 1000 - d.swap.since))} s so far` : ''}. It loads again with the next chat request.`;
  b.device.textContent = [d.version && 'ComfyUI ' + d.version, (mem.device || '').replace(/^cuda:\d+\s*/, '').replace(/\s*:\s*native$/, '')].filter(Boolean).join(' · ');
  if (d.state === 'err') { b.prompt.textContent = d.error || 'ComfyUI does not answer'; b.meta.textContent = ''; b.bar.classList.add('hidden'); b.step.textContent = ''; }
  else if (cur) {
    b.prompt.textContent = cur.prompt || '(no prompt)'; b.prompt.title = cur.prompt || '';
    b.meta.textContent = [cur.size, cur.steps && cur.steps + ' steps', cur.seed != null && 'seed ' + cur.seed, !cur.studio && 'started in ComfyUI itself'].filter(Boolean).join(' · ');
    const pct = cur.of ? Math.round(cur.step / cur.of * 100) : null;
    b.bar.classList.remove('hidden'); b.bar.classList.toggle('busy', pct == null);
    b.fill.style.width = (pct ?? 100) + '%'; if (pct != null) b.bar.setAttribute('aria-valuenow', String(pct)); else b.bar.removeAttribute('aria-valuenow');
    b.step.textContent = [cur.of ? `Step ${cur.step} of ${cur.of}` : 'Working', cur.elapsed != null && Math.round(cur.elapsed) + ' s'].filter(Boolean).join(' · ');
  } else { b.prompt.textContent = 'Idle: no picture being made.'; b.prompt.title = ''; b.meta.textContent = ''; b.bar.classList.add('hidden'); b.step.textContent = ''; }
  b.now.classList.toggle('idle', !cur);
  const gb = n => n == null ? '–' : fmtGB(n);
  b.rows.replaceChildren(...[['Queue', d.state === 'err' ? '–' : d.pending ? `${d.pending} waiting` : 'empty'],
    ['Card memory free', mem.vram_total ? `${gb(mem.vram_free)} of ${gb(mem.vram_total)}` : '–'],
    ['System RAM free', mem.ram_total ? `${gb(mem.ram_free)} of ${gb(mem.ram_total)}` : '–'],
    ['Step progress', d.live ? 'live' : 'not connected']].flatMap(([k, v]) => [el('dt', { text: k }), el('dd', { text: v })]));
  const recent = d.recent || [], key = recent.map(r => r.job).join();
  b.recentHead.classList.toggle('hidden', !recent.length);
  if (key === C.shown) return;
  C.shown = key;
  b.recent.replaceChildren(...recent.map(r => {
    const img = r.images[0], src = img && '/api/comfyui/image?' + new URLSearchParams({ filename: img.filename, subfolder: img.subfolder || '' });
    const when = r.finished ? new Date(r.finished * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';
    const caption = [r.seconds != null && Math.round(r.seconds) + ' s', when].filter(Boolean).join(' · ');
    const title = [r.prompt, [r.size, r.seed != null && 'seed ' + r.seed].filter(Boolean).join(' · ')].filter(Boolean).join('\n');
    return el('button', { class: 'cd-pic' + (r.ok ? '' : ' failed'), title, 'aria-label': 'Open picture: ' + (r.prompt || r.job), disabled: src ? null : '',
      onclick: () => src && openImageTab(src, img.filename) },
      src ? el('img', { src, alt: '', loading: 'lazy' }) : el('span', { class: 'cd-none', text: !r.ok ? 'failed' : r.videos?.length ? '🎬 video' : 'no picture' }), el('span', { class: 'cd-cap', text: caption }));
  }));
}
/* ---- the nodes of a ComfyUI job, in an editor tab: read-only, redrawn while it runs */
const COMFY_NAMES = { CheckpointLoaderSimple: 'Load checkpoint', CLIPTextEncode: 'Text prompt', EmptyLatentImage: 'Empty picture', KSampler: 'Sampler',
  KSamplerAdvanced: 'Sampler', VAEDecode: 'Decode', VAEEncode: 'Encode', SaveImage: 'Save picture', PreviewImage: 'Preview', LoadImage: 'Load picture', LoraLoader: 'LoRA',
  UNETLoader: 'Load model', CLIPLoader: 'Load text encoder', VAELoader: 'Load VAE', EmptySD3LatentImage: 'Empty picture', ModelSamplingAuraFlow: 'Model sampling',
  ConditioningZeroOut: 'Empty negative', Wan22ImageToVideoLatent: 'Empty video', ModelSamplingSD3: 'Model sampling', CreateVideo: 'Make video', SaveVideo: 'Save video' };
function openComfyTab(job = '') {
  if (C.open) cdashToggle(false);             // the tab is the bigger view of the same thing
  showSection('editor');
  let t = S.tabs.find(x => x.kind === 'comfy');
  if (!t) { t = { id: E.nextId++, kind: 'comfy', name: 'ComfyUI nodes', path: 'comfy:nodes', job }; S.tabs.push(t); } else t.job = job;
  activateTab(t);
}
function showComfy(t) {
  const pane = $('#pane-preview'); showPane('preview');
  const pick = el('select', { class: 'cg-pick', 'aria-label': 'Which picture', onchange: () => { t.job = pick.value; t.data = null; comfyPoll(t); } });
  const state = el('div', { class: 'cg-state' }), meta = el('div', { class: 'cg-meta' });
  const graph = el('div', { class: 'cg-graph' }), side = el('aside', { class: 'cg-side hidden' });
  pane.replaceChildren(el('div', { class: 'cg' }, el('div', { class: 'cg-bar' }, pick, state, meta), el('div', { class: 'cg-body' }, graph, side)));
  t.ui = { pane, pick, state, meta, graph, side };
  comfyPick(t); comfyPoll(t);
}
function comfyPick(t) {
  const recent = (C.last && C.last.recent) || [], cur = t.data && t.data.job;
  const opts = [['', 'Now, or the latest picture'], ...recent.map(r => [r.job, `${r.finished ? new Date(r.finished * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + ' · ' : ''}${(r.prompt || r.job).slice(0, 60)}`])];
  if (t.job && !opts.some(([v]) => v === t.job)) opts.push([t.job, (cur === t.job && t.data.summary.prompt || t.job).slice(0, 60)]);
  const key = opts.map(o => o.join()).join('|');
  if (t.ui.pickKey !== key) { t.ui.pickKey = key; t.ui.pick.replaceChildren(...opts.map(([v, label]) => el('option', { value: v, text: label }))); }
  t.ui.pick.value = t.job || '';
}
async function comfyPoll(t) {
  clearTimeout(t.timer);
  if (!S.tabs.includes(t)) return;
  if (S.active === t && !document.hidden) {
    try { t.data = await GET('/api/comfyui/graph', t.job ? { job: t.job } : null); t.err = ''; }
    catch (e) { t.err = e.message || String(e); }
    if (S.active === t && t.ui) { comfyPick(t); comfyRender(t); }
  }
  const busy = t.data && (t.data.state === 'running' || t.data.state === 'waiting');
  t.timer = setTimeout(() => comfyPoll(t), busy ? 1000 : 4000);
}
function comfyRender(t) {
  const u = t.ui, d = t.data;
  if (!d) { u.state.textContent = t.err || 'Loading…'; u.graph.replaceChildren(); return; }
  const done = new Set(d.done), cached = new Set(d.cached), err = d.error && d.error.node;
  const statusOf = id => id === err ? 'error' : id === d.node ? 'running' : cached.has(id) ? 'cached' : done.has(id) ? 'done' : 'waiting';
  const s = d.summary || {};
  u.state.className = 'cg-state ' + d.state;
  u.state.textContent = t.err ? 'ComfyUI does not answer' : d.state === 'running' ? ['Making the picture', d.of && `step ${d.step} of ${d.of}`, d.elapsed != null && Math.round(d.elapsed) + ' s'].filter(Boolean).join(' · ')
    : d.state === 'waiting' ? 'Waiting in the queue' : d.state === 'done' ? `Done${d.seconds != null ? ` in ${Math.round(d.seconds)} s` : ''}` : d.state === 'error' ? `Failed at node #${err}` : 'Stopped';
  u.meta.textContent = [s.size, s.steps && s.steps + ' steps', s.seed != null && 'seed ' + s.seed, d.studio === false && 'started in ComfyUI itself'].filter(Boolean).join(' · ');
  // Columns by depth: a node sits one column right of the deepest node it reads from.
  const byId = Object.fromEntries(d.nodes.map(n => [n.id, n])), depth = {};
  const depthOf = (id, seen = new Set()) => { if (depth[id] != null) return depth[id]; if (seen.has(id) || !byId[id]) return 0; seen.add(id);
    return (depth[id] = Math.max(0, ...byId[id].links.map(l => depthOf(l.node, seen) + 1))); };
  d.nodes.forEach(n => depthOf(n.id));
  const role = {};
  d.nodes.forEach(n => n.links.forEach(l => { if (l.input === 'positive' || l.input === 'negative') role[l.node] = l.input; }));
  const cols = [];
  [...d.nodes].sort((a, b) => (+a.id || 0) - (+b.id || 0)).forEach(n => { (cols[depth[n.id]] = cols[depth[n.id]] || []).push(n); });
  const cards = {};
  const card = n => {
    const st = statusOf(n.id), name = n.title || COMFY_NAMES[n.type] || n.type.replace(/([a-z])([A-Z])/g, '$1 $2');
    const head = el('div', { class: 'cg-head' }, el('span', { class: 'cg-dot' }), el('b', { text: name + (role[n.id] ? ` (${role[n.id]})` : '') }), el('span', { class: 'cg-id', text: '#' + n.id }));
    const rows = n.links.map(l => el('div', { class: 'cg-in', 'data-from': l.node }, el('span', { class: 'cg-port' }), el('span', { text: l.input }), el('small', { text: '← #' + l.node })));
    const vals = Object.entries(n.values).map(([k, v]) => el('div', { class: 'cg-val' + (typeof v === 'string' && v.length > 60 ? ' long' : '') }, el('span', { class: 'k', text: k }), el('span', { class: 'v', text: String(v) })));
    const node = el('div', { class: 'cg-node ' + st, role: 'button', tabindex: '0', title: n.type, 'data-id': n.id, onclick: () => comfySide(t, n, st) }, head, ...rows, ...vals);
    if (st === 'running' && d.of && /Sampler/.test(n.type)) node.append(el('div', { class: 'cd-bar' }, el('div', { class: 'cd-fill', style: `width:${Math.round(d.step / d.of * 100)}%` })));
    for (const img of (d.images || {})[n.id] || []) {
      const src = '/api/comfyui/image?' + new URLSearchParams({ filename: img.filename, subfolder: img.subfolder || '' });
      node.append(el('img', { class: 'cg-pic', src, alt: 'Saved picture', loading: 'lazy', onclick: e => { e.stopPropagation(); openImageTab(src, img.filename); } }));
    }
    if (st === 'error') node.append(el('div', { class: 'cg-err', text: d.error.message }));
    cards[n.id] = node; return node;
  };
  const scroll = u.graph.scrollLeft, top = u.graph.scrollTop;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('class', 'cg-wires');
  u.graph.replaceChildren(svg, el('div', { class: 'cg-cols' }, cols.filter(Boolean).map(c => el('div', { class: 'cg-col' }, c.map(card)))));
  u.graph.scrollLeft = scroll; u.graph.scrollTop = top;
  requestAnimationFrame(() => comfyWires(u.graph, svg, d, cards, statusOf));
  if (u.sideFor && byId[u.sideFor]) comfySide(t, byId[u.sideFor], statusOf(u.sideFor), true);
}
function comfyWires(box, svg, d, cards, statusOf) {
  const base = box.getBoundingClientRect(), w = box.scrollWidth, h = box.scrollHeight;
  svg.setAttribute('width', w); svg.setAttribute('height', h); svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
  const at = (r, x) => ({ x: x - base.left + box.scrollLeft, y: r.top + r.height / 2 - base.top + box.scrollTop });
  svg.replaceChildren(...d.nodes.flatMap(n => [...(cards[n.id]?.querySelectorAll('.cg-in') || [])].map(row => {
    const from = cards[row.dataset.from]; if (!from) return null;
    const fr = from.getBoundingClientRect(), tr = row.getBoundingClientRect();
    const a = at(fr, fr.right), b = at(tr, tr.left), dx = Math.max(40, (b.x - a.x) / 2);
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', `M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`);
    path.setAttribute('class', 'cg-wire ' + statusOf(row.dataset.from));
    return path;
  }).filter(Boolean)));
}
function comfySide(t, n, st, refresh = false) {
  const u = t.ui;
  if (!refresh && u.sideFor === n.id) { u.sideFor = null; u.side.classList.add('hidden'); return; }
  u.sideFor = n.id; u.side.classList.remove('hidden');
  $$('.cg-node', u.graph).forEach(c => c.classList.toggle('picked', c.dataset.id === n.id));
  u.side.replaceChildren(el('div', { class: 'cg-side-head' }, el('b', { text: `${COMFY_NAMES[n.type] || n.type} #${n.id}` }),
      el('button', { class: 'icon-btn', title: 'Close', 'aria-label': 'Close', html: ico('x', 14), onclick: () => comfySide(t, n, st) })),
    el('div', { class: 'cg-side-type', text: `${n.type} · ${st}` }),
    el('dl', {}, ...n.links.flatMap(l => [el('dt', { text: l.input }), el('dd', { text: `from #${l.node}, output ${l.slot}` })]),
      ...Object.entries(n.values).flatMap(([k, v]) => [el('dt', { text: k }), el('dd', { class: typeof v === 'string' && v.length > 40 ? 'text' : '', text: String(v) })])));
}
addEventListener('resize', () => { const t = S.active; if (t && t.kind === 'comfy' && t.data && t.ui) comfyRender(t); });

async function cdashPoll() {
  clearTimeout(C.timer);
  if (C.off || C.busy) return;
  C.busy = true;
  let working = false;
  try {
    if (!document.hidden) {
      const d = await GET('/api/comfyui/status', null, { noAuthRedirect: true });
      if (!d.available) { C.off = true; cdashRender(d); return; }
      working = d.state === 'work'; cdashRender(d);
    }
  } catch (e) {
    if (e.status === 403 || e.status === 401) { C.off = e.status === 403; $$('#sb-comfy, #btn-cdash, #cdash').forEach(n => n.classList.add('hidden')); return; }
  } finally { C.busy = false; }
  if (!C.off) C.timer = setTimeout(cdashPoll, C.open || working ? 1000 : 5000);
}
$('#sb-comfy').onclick = $('#btn-cdash').onclick = () => cdashToggle(!C.open);
addEventListener('resize', () => { if (C.open) cdashPosition(); });

/* ------------------------------------------------------------------ waiting room */
const R = { state: null, stable: '', at: 0, es: null, open: false, retry: null, offered: false, title: document.title, top: null, chat: null, seen: 0, loaded: false, epoch: null, thread: '', tab: null, marks: null, people: null };
function fmtLeft(seconds) { const s = Math.max(0, Math.round(seconds)); return s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.floor(s % 3600 / 60)} min` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; }
// Times from the server tick down locally between its updates. dir -1 counts down, 1 counts up.
function clock(seconds, dir = -1) { return el('span', { class: 'room-clock', 'data-sec': String(seconds), 'data-dir': String(dir), text: fmtLeft(seconds) }); }
function roomTick() {
  const passed = (Date.now() - R.at) / 1000;
  $$('.room-clock').forEach(n => { n.textContent = fmtLeft(Number(n.dataset.sec) + Number(n.dataset.dir) * passed); });
}
async function roomAct(url, body, failure) {
  try { roomApply(await POST(url, body || {})); } catch (e) { errToast(e, failure); }
}
function roomButton(label, cls, url, body, failure) {
  return el('button', { class: 'btn sm ' + cls, onclick: () => roomAct(url, body, failure) }, label);
}
function roomApply(state) {
  const stable = JSON.stringify(state, (key, value) => ['held_for', 'waited', 'offer_left', 'idle_left', 'limit_left', 'wait_s', 'join_wait_s'].includes(key) ? undefined : value);
  const prev = R.state;
  R.state = state; R.at = Date.now();
  try { roomNews(prev, state); } catch (e) { console.warn(e); }
  // Keep the same buttons unless something really changed, so a click is never lost to a redraw.
  if (stable !== R.stable) { R.stable = stable; roomRender(); } else { roomRefreshClocks(); }
}
function roomRefreshClocks() {
  const s = R.state, me = s.me, set = (id, v) => { const n = document.getElementById(id); if (n && v != null) n.dataset.sec = String(v); };
  set('room-c-offer', me.offer_left); set('room-c-offer2', me.offer_left); set('room-c-idle', me.idle_left); set('room-c-limit', me.limit_left);
  set('room-c-held', s.holder && s.holder.held_for); set('room-b-held', s.holder && s.holder.held_for);
  roomTick();
}
function withId(node, id) { node.id = id; return node; }
function estimateText(h) {
  const e = h.estimate;
  if (!h.running) return h.idle_left != null ? `Between runs. Passes on within ${Math.ceil(Math.max(0, h.idle_left) / 60)} min if idle` : 'Between runs';
  const step = e && e.step ? `Step ${e.step} of ${e.total}` : '';
  const hours = m => m >= 90 ? `${Math.round(m / 60)} h` : `${m} min`;
  const rest = !e ? '' : e.state === 'approval' ? 'waiting for an approval'
    : e.state === 'range' ? `about ${e.low_min === e.high_min ? hours(e.high_min) : (e.high_min >= 90 ? hours(e.low_min) + ' to ' + hours(e.high_min) : `${e.low_min}–${e.high_min} min`)} left (estimate)`
    : e.state === 'over' ? 'taking longer than expected' : 'still estimating';
  return [step, rest].filter(Boolean).join(' · ');
}
function chatTime(at) { return new Date(at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); }
const myName = () => (S.config.user || {}).username;
// A private message belongs to the conversation with the other person; everything public is the '' conversation.
function chatThread(m) { return m.to ? (m.from === myName() ? m.to : m.from) : ''; }
function chatPersonName(username) {
  if (!username) return 'Everyone';
  const s = R.state, found = (s && s.online.find(p => p.username === username)) || (R.people || []).find(p => p.username === username);
  if (found) return found.name;
  const m = (R.chatItems || []).find(x => x.from === username || x.to === username);
  return m ? (m.from === username ? m.name : m.to_name) : username;
}
function chatMarks() {
  if (!R.marks) { try { R.marks = JSON.parse(lsGet('studio.chatRead') || 'null'); } catch { R.marks = null; } }
  return R.marks;
}
function chatUnread(thread) {
  const me = myName(), marks = chatMarks() || {};
  return (R.chatItems || []).filter(m => m.from !== me && (thread === undefined || chatThread(m) === thread) && m.id > (marks[chatThread(m)] || 0)).length;
}
function chatMarkRead(thread) {
  const items = (R.chatItems || []).filter(m => chatThread(m) === thread); if (!items.length) return;
  const marks = chatMarks() || {}, newest = items[items.length - 1].id;
  if ((marks[thread] || 0) >= newest) return;
  marks[thread] = newest; R.marks = marks; lsSet('studio.chatRead', JSON.stringify(marks));
  roomBadge(); roomTabsPaint(); chatThreadsPaint();
}
const chatShowing = () => R.open && R.tab === 'chat' && !document.hidden;
function chatLine(m) {
  const mine = m.from === myName();
  return el('div', { class: 'chat-msg' + (mine ? ' mine' : '') + (m.to ? ' private' : '') }, el('div', { class: 'chat-meta' }, el('b', { text: mine ? 'You' : m.name }), el('small', { text: ' ' + chatTime(m.at) })), el('div', { class: 'chat-text', text: m.text }));
}
// Desktop notifications and a sound for the waiting room, when this window is not the one in front.
function roomAlert(text, tag = 'studio-room') {
  const mode = lsGet('studio.roomNotify') || 'notify';
  if (mode === 'off' || (!document.hidden && document.hasFocus())) return;
  if ('Notification' in window && Notification.permission === 'granted') { const n = new Notification('Eidoloom Studio', { body: text, tag }); n.onclick = () => { window.focus(); roomToggle(true); n.close(); }; }
  if (mode === 'sound') chime();
}
function chatBuild() {
  const threads = el('div', { class: 'chat-threads', role: 'tablist', 'aria-label': 'Conversations' });
  const head = el('div', { class: 'chat-head' });
  const list = el('div', { class: 'chat-list', 'aria-live': 'polite' });
  const input = el('input', { class: 'dlg-input chat-input', type: 'text', maxlength: '1000', 'aria-label': 'Message' });
  const send = async () => {
    const text = input.value.trim(); if (!text) return;
    try { await POST('/api/queue/chat', { text, to: R.thread || undefined }); input.value = ''; input.focus(); } catch (e) { errToast(e, 'Message not sent'); }
  };
  input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); send(); } });
  const clearBtn = isAdmin() ? el('button', { class: 'link-btn chat-clear', onclick: async () => {
    const yes = await dialog({ title: 'Clear the chat for everyone?', body: 'This removes all messages, including private ones.', buttons: [{ label: 'Cancel' }, { label: 'Clear', value: 'yes', kind: 'danger' }] });
    if (yes === 'yes') { try { await POST('/api/admin/queue/chat/clear', {}); } catch (e) { errToast(e, 'Could not clear the chat'); } }
  } }, 'Clear chat') : null;
  R.chat = { threads, head, list, input };
  return el('div', { class: 'room-chat' }, threads, head, list,
    el('div', { class: 'chat-form' }, input, el('button', { class: 'btn sm primary', onclick: send }, 'Send')),
    el('small', { class: 'room-note', text: 'Only people see this. It is never sent to the model. Kept for up to 24 hours.' }), clearBtn);
}
function chatThreadsPaint() {
  if (!R.chat) return;
  const me = myName(), seen = new Set(['']);
  const order = [''];
  for (const m of [...(R.chatItems || [])].reverse()) { const t = chatThread(m); if (!seen.has(t)) { seen.add(t); order.push(t); } }
  if (R.thread && !seen.has(R.thread)) order.splice(1, 0, R.thread);
  R.chat.threads.replaceChildren(...order.map(t => {
    const n = chatUnread(t);
    return el('button', { class: 'chat-thread' + (t === R.thread ? ' on' : '') + (t ? ' private' : ''), role: 'tab', 'aria-selected': t === R.thread ? 'true' : 'false', onclick: () => chatOpen(t) },
      t ? el('span', { html: ico('user', 11) }) : el('span', { html: ico('users', 11) }), el('span', { text: chatPersonName(t) }), n ? el('span', { class: 'tab-badge', text: String(n) }) : null);
  }));
  const online = R.state && R.state.online.some(p => p.username === R.thread);
  R.chat.head.replaceChildren(el('small', { text: R.thread ? `Private conversation with ${chatPersonName(R.thread)}${online ? '' : ' (not online now; they will see it when they come back)'}` : 'Everyone in the waiting room sees these messages' }));
  R.chat.input.placeholder = R.thread ? `Message ${chatPersonName(R.thread)}` : 'Message everyone';
  void me;
}
function chatOpen(thread) {
  R.thread = thread || '';
  if (R.tab !== 'chat') { roomTab('chat'); return; }
  chatThreadsPaint(); chatListPaint(); if (chatShowing()) chatMarkRead(R.thread);
  R.chat.input.focus();
}
function chatListPaint() {
  if (!R.chat) return;
  const items = (R.chatItems || []).filter(m => chatThread(m) === R.thread);
  R.chat.list.replaceChildren(...(items.length ? items.map(chatLine) : [el('div', { class: 'dir-note chat-empty', text: R.thread ? 'No messages yet. Write the first one below.' : 'No messages yet' })]));
  R.chat.list.scrollTop = R.chat.list.scrollHeight;
}
function chatApply(messages) {
  const fresh = messages.filter(m => m.id > R.seen);
  if (!chatMarks()) {   // the first time on this browser, what is already there counts as read; anything after is new
    const marks = {}; if (!R.loaded) for (const m of fresh) marks[chatThread(m)] = m.id;
    R.marks = marks; lsSet('studio.chatRead', JSON.stringify(marks));
  }
  if (!fresh.length) { R.loaded = true; return; }
  R.seen = fresh[fresh.length - 1].id;
  if (!R.chatItems) R.chatItems = [];
  R.chatItems.push(...fresh); R.chatItems = R.chatItems.slice(-300);
  if (R.chat) {
    const list = R.chat.list, atEnd = list.scrollHeight - list.scrollTop - list.clientHeight < 30, here = fresh.filter(m => chatThread(m) === R.thread);
    if (here.length) { list.querySelector('.chat-empty')?.remove(); list.append(...here.map(chatLine)); if (atEnd) list.scrollTop = list.scrollHeight; }
    chatThreadsPaint();
  }
  if (chatShowing()) chatMarkRead(R.thread);
  const me = myName(), incoming = fresh.filter(m => m.from !== me);
  if (R.loaded && incoming.length) {
    for (const m of incoming) {
      const visible = chatShowing() && chatThread(m) === R.thread;
      if (m.to && !visible) toast(`${m.name} (private): ${m.text}`, 'info', 8000);
      roomAlert(m.to ? `${m.name} (private): ${m.text}` : `${m.name}: ${m.text}`, 'studio-chat');
    }
  }
  roomBadge(); roomTabsPaint();
  R.loaded = true;
}
function roomBadge() {
  const s = R.state, count = s ? s.queue.length + (s.holder ? 1 : 0) : 0, unread = chatUnread(), offers = s ? s.handoffs.incoming.length : 0;
  const people = $('#room-count'), news = $('#room-unread');
  people.textContent = String(count); people.classList.toggle('hidden', !count);
  news.textContent = String(unread + offers); news.classList.toggle('hidden', !(unread + offers));
  const bits = [count ? (s.holder ? `${s.holder.name} has the agent` : '') + (s.queue.length ? `${s.holder ? ', ' : ''}${s.queue.length} waiting` : '') : 'nobody has the agent',
    unread ? `${unread} new message${unread > 1 ? 's' : ''}` : '', offers ? `${offers} chat${offers > 1 ? 's' : ''} passed to you` : ''].filter(Boolean);
  $('#btn-room').title = 'Waiting room: ' + bits.join(' · ');
  const me = s ? s.me : {};
  document.title = me.offered ? "Your turn · Eidoloom Studio" : (unread ? `(${unread}) ` : '') + (A.titleBit ? `${A.titleBit} · ` : '') + (me.position ? `#${me.position} · ` : '') + R.title;
}
// Someone joining or leaving the line, a chat passed to you, and an answer to a chat you passed on.
function roomNews(prev, s) {
  const me = myName();
  if (prev) {
    const before = new Map(prev.queue.map(e => [e.username, e])), now = new Map(s.queue.map(e => [e.username, e]));
    for (const [name, e] of now) if (!before.has(name) && name !== me) {
      toast(`${e.name} joined the line (#${s.queue.indexOf(e) + 1})`, 'info', 4000);
      if (s.me.holds) roomAlert(`${e.name} is waiting for the agent`);
    }
    for (const [name, e] of before) if (!now.has(name) && name !== me && !(s.holder && s.holder.username === name)) toast(`${e.name} left the line`, 'info', 3000);
  }
  const offered = new Set(((prev && prev.handoffs.incoming) || []).map(h => h.id));
  for (const h of s.handoffs.incoming) if (prev && !offered.has(h.id)) {
    toast(`${h.from_name} passed you a chat: "${h.title}"`, 'info', 8000); roomAlert(`${h.from_name} passed you a chat: "${h.title}"`, 'studio-handoff');
  }
  const answered = new Map(((prev && prev.handoffs.outgoing) || []).map(h => [h.id, h.state]));
  for (const h of s.handoffs.outgoing) if (prev && answered.get(h.id) === 'offered' && ['accepted', 'declined'].includes(h.state)) {
    toast(`${h.to_name} ${h.state} the chat "${h.title}"`, h.state === 'accepted' ? 'success' : 'info', 6000); roomAlert(`${h.to_name} ${h.state} the chat "${h.title}"`, 'studio-handoff');
  }
}
async function handoffAccept(h) {
  if (A.running) { toast('Stop the current run first: accepting opens the chat in its own folder', 'info'); return; }
  try {
    const r = await POST('/api/handoff/accept', { id: h.id });
    toast(`Copied into your folder "${r.ws.name}"`, 'success', 5000);
    await setWorkspace(r.ws); await loadSessions(); await openSession(r.session);
    if (isPhone()) showSection('chat');
  } catch (e) { errToast(e, 'Could not take the chat'); }
}
function handoffCards(s) {
  const incoming = s.handoffs.incoming, mine = s.handoffs.outgoing.filter(h => h.state === 'offered');
  if (!incoming.length && !mine.length) return el('span');
  return el('div', { class: 'handoffs' },
    ...incoming.map(h => el('div', { class: 'room-card handoff-card' },
      el('div', { class: 'handoff-head' }, el('span', { html: ico('send', 12) }), el('span', {}, el('b', { text: h.from_name }), ' passed you a chat')),
      el('div', { class: 'handoff-title', text: h.title }),
      el('small', { text: `Folder "${h.folder}" (${h.mb} MB) · ${ago(h.at)}. Accepting copies the chat and the folder into your own space; theirs stays as it is.` }),
      h.note ? el('div', { class: 'handoff-note', text: h.note }) : null,
      el('div', { class: 'room-actions' }, el('button', { class: 'btn sm primary', onclick: () => handoffAccept(h) }, 'Accept and open'),
        el('button', { class: 'btn sm', onclick: () => roomAct('/api/handoff/decline', { id: h.id }, 'Could not decline') }, 'Decline')))),
    ...mine.map(h => el('div', { class: 'room-card handoff-card sent' },
      el('div', {}, 'Waiting for ', el('b', { text: h.to_name }), ' to take "', h.title, '"'),
      el('div', { class: 'room-actions' }, el('button', { class: 'btn sm', onclick: () => roomAct('/api/handoff/cancel', { id: h.id }, 'Could not cancel') }, 'Cancel')))));
}
async function passChat(to) {
  if (!S.ws) { toast('Open a folder first', 'info'); return; }
  await loadSessions();
  if (!A.sessions.length) { toast('This folder has no chats to pass on yet', 'info'); return; }
  try { R.people = (await GET('/api/people')).people; } catch (e) { errToast(e, 'Could not list people'); return; }
  if (!R.people.length) { toast('There is nobody else to pass a chat to', 'info'); return; }
  const chat = el('select', { class: 'dlg-input' }, A.sessions.map(x => el('option', { value: x.id, text: x.title || 'Untitled chat' })));
  chat.value = A.passPick && A.sessions.some(x => x.id === A.passPick) ? A.passPick : A.session && A.sessions.some(x => x.id === A.session) ? A.session : A.sessions[0].id;
  A.passPick = null;
  const who = el('select', { class: 'dlg-input' }, R.people.map(p => el('option', { value: p.username, text: p.name })));
  if (to) who.value = to;
  const note = el('textarea', { class: 'dlg-input', rows: '3', maxlength: '500', placeholder: 'A note for them (optional): where you got to, what is left' });
  const body = el('div', { class: 'dlg-form' }, el('label', {}, 'Chat', chat), el('label', {}, 'Pass to', who), el('label', {}, 'Note', note),
    el('small', { class: 'dlg-note', text: `They get a copy of the chat and of the folder "${S.ws.name}" in their own space, after they accept. Your chat and folder stay as they are.` }));
  const ok = await dialog({ title: 'Pass a chat to someone', body, small: false, buttons: [{ label: 'Cancel', value: false }, { label: 'Pass it on', value: true, kind: 'primary' }] });
  if (ok !== true) return;
  try {
    await POST('/api/handoff/offer', { ws: S.ws.id, session: chat.value, to: who.value, note: note.value.trim() });
    toast(`Offered to ${who.selectedOptions[0].textContent}. You will hear when they answer.`, 'success', 5000);
    roomRefresh();
  } catch (e) { errToast(e, 'Could not pass the chat'); }
}
async function roomRefresh() { try { roomApply(await GET('/api/queue/state')); } catch { /* the stream brings it */ } }
function roomRender() {
  const s = R.state; if (!s) return;
  const me = s.me, holder = s.holder;
  roomBadge();
  if (R.epoch !== null && s.chat_epoch !== R.epoch) {   // an admin cleared the chat
    R.chatItems = []; if (R.chat) { chatListPaint(); chatThreadsPaint(); }
  }
  R.epoch = s.chat_epoch;
  const banner = $('#room-banner'); let parts = null;
  if (me.offered) {
    parts = [el('span', { class: 'room-msg' }, el('b', { text: "It's your turn. " }), 'Take it within ', withId(clock(me.offer_left), 'room-c-offer')), roomButton('Take my turn', 'primary', '/api/queue/accept', null, 'Could not take your turn'), roomButton('Skip', '', '/api/queue/leave', null, 'Could not skip')];
  } else if (me.holds && me.warn === 'limit') {
    parts = [el('span', { class: 'room-msg' }, 'Others are waiting and your session limit is near. You can finish this run, then it passes on. ', withId(clock(Math.max(0, me.limit_left)), 'room-c-limit'))];
  } else if (me.holds && me.warn === 'idle') {
    parts = [el('span', { class: 'room-msg' }, 'Your turn ends in ', withId(clock(me.idle_left), 'room-c-idle'), ' if you stay idle.'), roomButton('Keep my turn', 'primary', '/api/queue/keep', null, 'Could not keep your turn'), roomButton('Finish', '', '/api/queue/release', null, 'Could not finish your turn')];
  } else if (me.holds && s.queue.length) {
    parts = [el('span', { class: 'room-msg', text: `${s.queue.length} waiting for the agent.` }), roomButton('Finish my turn', '', '/api/queue/release', null, 'Could not finish your turn')];
  } else if (!me.holds && (holder || s.queue.length)) {
    const who = holder ? el('span', {}, el('b', { text: holder.name }), ' has the agent · ', withId(clock(holder.held_for, 1), 'room-b-held')) : el('span', { text: 'Passing to the next person.' });
    const estimate = holder && estimateText(holder);
    parts = [el('span', { class: 'room-msg' }, who, me.position ? ` · You are #${me.position}` : '', estimate ? el('small', { class: 'room-estimate', text: estimate }) : null),
      me.position ? roomButton('Leave line', '', '/api/queue/leave', null, 'Could not leave the line') : roomButton('Join line', 'primary', '/api/queue/join', null, 'Could not join the line')];
  }
  if (!parts && s.handoffs.incoming.length) {
    const h = s.handoffs.incoming[0];
    parts = [el('span', { class: 'room-msg' }, el('b', { text: h.from_name }), ` passed you a chat: "${h.title}"`), el('button', { class: 'btn sm primary', onclick: () => handoffAccept(h) }, 'Accept and open'),
      el('button', { class: 'btn sm', onclick: () => roomToggle(true, 'line') }, 'Details')];
  }
  banner.classList.toggle('hidden', !parts);
  if (parts) { banner.classList.toggle('turn', !!me.offered); banner.replaceChildren(...parts); }
  if (me.offered && !R.offered) { toast("It's your turn with the agent", 'info', 8000); roomAlert("It's your turn with the agent", 'studio-turn'); roomToggle(true, 'line'); }
  R.offered = me.offered;
  if (R.open) roomBody();
}
const ROOM_TABS = [['line', 'Line'], ['chat', 'Chat'], ['people', 'People']];
function roomTabsPaint() {
  if (!R.tabs || !R.state) return;
  const s = R.state, counts = { line: s.queue.length + (s.holder ? 1 : 0) + s.handoffs.incoming.length, chat: chatUnread(), people: s.online.length };
  R.tabs.replaceChildren(...ROOM_TABS.map(([key, label]) => el('button', { class: 'room-tab' + (R.tab === key ? ' on' : ''), role: 'tab', 'aria-selected': R.tab === key ? 'true' : 'false', 'data-tab': key, onclick: () => roomTab(key) },
    label, counts[key] ? el('span', { class: 'tab-badge' + (key === 'chat' ? ' news' : key === 'line' && s.handoffs.incoming.length ? ' news' : ''), text: String(counts[key]) }) : null)));
}
function roomTab(key) {
  R.tab = key; lsSet('studio.roomTab', key);
  roomTabsPaint();
  R.panes.line.classList.toggle('hidden', key !== 'line'); R.panes.chat.classList.toggle('hidden', key !== 'chat'); R.panes.people.classList.toggle('hidden', key !== 'people');
  if (key === 'chat') { chatThreadsPaint(); chatListPaint(); chatMarkRead(R.thread); R.chat.input.focus(); }
  if (key === 'people') roomPeople();
}
function personStatus(p) { return p.holds ? 'has the agent' : p.position ? `#${p.position} in line` : 'online'; }
async function roomPeople() {
  const s = R.state, pane = R.panes.people, me = myName(); if (!s) return;
  const row = (p, online) => el('li', { class: 'person' }, el('span', { class: 'dash-dot', 'data-state': online ? (p.holds ? 'work' : p.position ? 'load' : 'idle') : 'err', title: online ? personStatus(p) : 'Not online' }),
    el('span', { class: 'room-who' }, el('b', { text: p.name }), el('small', { text: ' · ' + (online ? personStatus(p) : 'not online') })),
    el('span', { class: 'room-actions' }, el('button', { class: 'btn sm', title: `Private conversation with ${p.name}`, onclick: () => chatOpen(p.username) }, 'Message'),
      el('button', { class: 'btn sm', title: `Pass a chat of this folder to ${p.name}`, onclick: () => passChat(p.username) }, 'Pass a chat')));
  const online = s.online.filter(p => p.username !== me), known = new Set(online.map(p => p.username));
  const others = (R.people || []).filter(p => !known.has(p.username));
  pane.replaceChildren(el('div', { class: 'dlg-sub', text: `Online (${online.length})` }),
    online.length ? el('ul', { class: 'room-people' }, online.map(p => row(p, true))) : el('div', { class: 'dir-note', text: 'Nobody else is online' }),
    others.length ? el('div', { class: 'dlg-sub', text: 'Everyone else' }) : el('span'), others.length ? el('ul', { class: 'room-people' }, others.map(p => row(p, false))) : el('span'),
    el('small', { class: 'room-note', text: 'Passing a chat gives them a copy of it and of its folder, after they accept. Messages to someone not online wait for them for up to 24 hours.' }));
  if (!R.people) { try { R.people = (await GET('/api/people')).people; if (R.tab === 'people') roomPeople(); } catch { R.people = []; } }
}
function roomBody() {
  const s = R.state, box = $('#room'); if (!s) return;
  if (!R.top) {   // the chat stays in place while the rest redraws, so typing is never interrupted
    R.top = el('div', { class: 'room-top' }); R.tabs = el('div', { class: 'room-tabs', role: 'tablist', 'aria-label': 'Waiting room' });
    R.panes = { line: el('div', { class: 'room-pane' }), chat: chatBuild(), people: el('div', { class: 'room-pane' }) };
    R.handoffBox = el('div');
    box.replaceChildren(R.top, R.tabs, R.handoffBox, R.panes.line, R.panes.chat, R.panes.people);
    R.tab = ['line', 'chat', 'people'].includes(R.tab) ? R.tab : (lsGet('studio.roomTab') || 'line');
    roomTab(R.tab);
  }
  const me = s.me, holder = s.holder, admin = isAdmin();
  const close = el('button', { class: 'icon-btn', title: 'Collapse', 'aria-label': 'Collapse', html: ico('x', 14), onclick: () => roomToggle(false) });
  R.top.replaceChildren(el('div', { class: 'dash-head' }, el('span', { class: 'dash-dot', 'data-state': holder ? 'work' : 'idle' }), el('div', { class: 'dash-title' }, el('b', { class: 'dash-state', text: 'Waiting room' }), el('span', { class: 'dash-model', text: holder ? `${holder.username === myName() ? 'You have' : holder.name + ' has'} the agent` + (s.queue.length ? ` · ${s.queue.length} waiting` : '') : 'The agent is free' })), close));
  roomTabsPaint();
  R.handoffBox.replaceChildren(handoffCards(s));
  const r = s.rules, minutes = n => Math.round(n / 60), st = s.stats, mine = myName();
  const about = sec => sec == null ? '' : sec < 60 ? 'under a minute' : sec >= 5400 ? `about ${Math.round(sec / 3600)} h` : `about ${Math.max(1, Math.round(sec / 60))} min`;
  const more = (label, items) => el('button', { class: 'icon-btn sm rl-more', title: label, 'aria-label': label, html: ico('more', 14),
    onclick: e => { const b = e.currentTarget.getBoundingClientRect(); showContextMenu(b.left, b.bottom + 4, items); } });
  // The line as one timeline: who has the agent now, then everyone waiting, each with when their turn may come.
  const rows = [];
  if (holder) {
    const you = holder.username === mine;
    const state = holder.running ? (holder.waiting_approval ? 'Waiting for an approval' : 'Working') : 'Between runs';
    const detail = estimateText(holder);
    const acts = [];
    if (you && !holder.running) acts.push(roomButton('Finish my turn', '', '/api/queue/release', null, 'Could not finish your turn'));
    if (admin && !you) acts.push(more('Session actions', [{ label: holder.running ? 'Stop the run and end the session' : 'End the session', danger: true, run: () => roomAct('/api/admin/queue/end', null, 'Could not end the session') }]));
    rows.push(el('li', { class: 'rl-row rl-now' + (you ? ' me' : ''), 'data-state': holder.running ? 'work' : 'idle' },
      el('span', { class: 'rl-mark' }, avatar(holder.name, 30), el('span', { class: 'rl-pulse' })),
      el('div', { class: 'rl-body' },
        el('div', { class: 'rl-name' }, el('b', { text: you ? 'You' : holder.name }), el('span', { class: 'rl-chip now', text: 'Now' }), el('span', { class: 'rl-time', title: 'How long this turn has lasted' }, withId(clock(holder.held_for, 1), 'room-c-held'))),
        el('small', { class: 'rl-state' }, el('span', { class: 'rl-dot' }), state + (holder.model ? ' · ' + holder.model : '')),
        detail && detail !== 'Between runs' ? el('small', { text: detail }) : null),
      acts.length ? el('div', { class: 'rl-acts' }, acts) : null));
  } else {
    rows.push(el('li', { class: 'rl-row rl-free' }, el('span', { class: 'rl-mark' }, el('span', { class: 'rl-free-ico', html: ico('check', 16) })),
      el('div', { class: 'rl-body' }, el('b', { text: s.queue.length ? 'Passing to the next person' : 'The agent is free' }),
        el('small', { text: s.queue.length ? 'They have a few minutes to take it.' : 'Send a message in the chat to start your turn.' }))));
  }
  s.queue.forEach((e, i) => {
    const you = e.username === mine, acts = [];
    if (you && me.offered) acts.push(roomButton('Take my turn', 'primary', '/api/queue/accept', null, 'Could not take your turn'), roomButton('Skip', '', '/api/queue/leave', null, 'Could not skip'));
    else if (you) acts.push(roomButton('Leave', '', '/api/queue/leave', null, 'Could not leave the line'));
    if (admin && !you) {
      const items = [];
      if (i > 0) items.push({ label: 'Move up', run: () => roomAct('/api/admin/queue/move', { username: e.username, position: i - 1 }, 'Could not move') });
      if (i < s.queue.length - 1) items.push({ label: 'Move down', run: () => roomAct('/api/admin/queue/move', { username: e.username, position: i + 1 }, 'Could not move') });
      if (i > 0) items.push({ label: 'Move to the front', run: () => roomAct('/api/admin/queue/move', { username: e.username, position: 0 }, 'Could not move') });
      if (items.length) items.push('-');
      items.push({ label: 'Remove from the line', danger: true, run: () => roomAct('/api/admin/queue/remove', { username: e.username }, 'Could not remove') });
      acts.push(more(`Actions for ${e.name}`, items));
    }
    const when = e.offered ? (you ? el('span', {}, 'Your turn now: take it within ', withId(clock(me.offer_left), 'room-c-offer2')) : 'Their turn is offered')
      : `Turn in ${about(you && me.wait_s != null ? me.wait_s : e.wait_s)}`;
    rows.push(el('li', { class: 'rl-row' + (you ? ' me' : '') + (e.offered ? ' offered' : '') },
      el('span', { class: 'rl-mark' }, avatar(e.name, 26), el('span', { class: 'rl-num', text: String(i + 1) })),
      el('div', { class: 'rl-body' },
        el('div', { class: 'rl-name' }, el('b', { text: you ? 'You' : e.name }), you ? el('span', { class: 'rl-chip you', text: `#${i + 1}` }) : null,
          el('span', { class: 'rl-time', title: 'How long they have waited' }, clock(e.waited, 1))),
        el('small', {}, when)),
      acts.length ? el('div', { class: 'rl-acts' }, acts) : null));
  });
  const joinable = !me.holds && !me.position && !me.offered && (holder || s.queue.length);
  if (joinable) rows.push(el('li', { class: 'rl-row rl-join' }, el('span', { class: 'rl-mark' }, el('span', { class: 'rl-plus', html: ico('plus', 14) })),
    el('div', { class: 'rl-body' }, el('b', { text: `Join as #${s.queue.length + 1}` }), el('small', { text: s.join_wait_s != null ? `Your turn in ${about(s.join_wait_s)}` : 'Wait for your turn' })),
    el('div', { class: 'rl-acts' }, roomButton('Join line', 'primary', '/api/queue/join', null, 'Could not join the line'))));
  const guess = !st.measured ? el('small', { class: 'room-note', text: 'Times are rough guesses until a few turns have been timed.' }) : el('span');
  const alerts = el('select', { class: 'dlg-input room-alerts', 'aria-label': 'Alerts when this window is in the background' }, [['off', 'Off'], ['notify', 'Desktop notice'], ['sound', 'Notice and sound']].map(([v, l]) => el('option', { value: v, text: l })));
  alerts.value = lsGet('studio.roomNotify') || 'notify';
  alerts.onchange = () => { lsSet('studio.roomNotify', alerts.value); if (alerts.value !== 'off' && 'Notification' in window && Notification.permission === 'default') Notification.requestPermission(); roomBody(); };
  const blocked = alerts.value !== 'off' && 'Notification' in window && Notification.permission === 'denied';
  const how = el('details', { class: 'rl-how' }, el('summary', { text: 'How turns work' }),
    el('ul', {}, el('li', { text: 'While you wait you can keep editing files and using terminals; only the agent takes turns.' }),
      el('li', { text: `A turn passes on after ${minutes(r.idle)} min without a run, or after ${minutes(r.max)} min if others are waiting.` }),
      el('li', { text: `When your turn comes you have ${minutes(r.offer)} min to take it.` })));
  R.panes.line.replaceChildren(
    el('ol', { class: 'rl', 'aria-label': 'Who has the agent, and the line' }, rows), guess,
    s.external ? el('div', { class: 'room-note warn-text', text: `Strata is also busy with a request from ${s.external.source}, so it may be slower.` }) : el('span'),
    el('div', { class: 'rl-foot' },
      el('div', { class: 'room-stats' }, el('span', { class: 'rl-stat' }, el('b', { text: String(st.turns_today) }), ` turn${st.turns_today === 1 ? '' : 's'} today`),
        el('span', { class: 'rl-stat' }, 'A turn ', st.measured ? 'usually takes ' : 'may take ', el('b', { text: `${minutes(st.typical_turn_s)} min` }))),
      el('label', { class: 'room-alert-pick', title: 'When this window is in the background' }, el('span', { html: ico('bell', 12) }), 'Alerts', alerts)),
    blocked ? el('small', { class: 'room-note warn-text', text: 'This browser blocks notifications for Studio: allow them in the site settings.' }) : el('span'),
    how);
  if (R.tab === 'people') roomPeople();
  roomRefreshClocks();
}
function roomPosition() {
  const box = $('#room'); if (!R.open) return;
  const r = $('#editor-stack').getBoundingClientRect(), dash = D.open && !D.free && D.built ? $('#dash').offsetWidth + 8 : 0;
  box.style.maxHeight = Math.max(160, innerHeight - r.top - 30) + 'px';
  box.style.left = Math.max(0, r.right - box.offsetWidth - dash) + 'px'; box.style.top = r.top + 'px';
}
function roomToggle(open, tab) {
  R.open = open;
  $('#btn-room').setAttribute('aria-pressed', open ? 'true' : 'false');
  $('#room').classList.toggle('hidden', !open);
  if (open) { roomBody(); if (tab) roomTab(tab); else if (R.tab === 'chat') chatMarkRead(R.thread); roomPosition(); }
}
document.addEventListener('visibilitychange', () => { if (chatShowing()) chatMarkRead(R.thread); });
$('#btn-room').onclick = () => roomToggle(!R.open);
function roomStart() {
  if (R.es) return;
  clearTimeout(R.retry);
  const es = liveStream('queue', {}); R.es = es; R.heard = Date.now();
  es.addEventListener('sync', e => { try { syncApply(JSON.parse(e.data)); } catch (err) { console.warn(err); } });
  es.addEventListener('open', () => { if (R.opened) syncCatchUp(); R.opened = true; });
  es.addEventListener('state', e => { R.heard = Date.now(); R.live = true; try { roomApply(JSON.parse(e.data)); } catch { /* ignore */ } });
  es.addEventListener('chat', e => { try { chatApply(JSON.parse(e.data).messages || []); } catch { /* ignore */ } });
  es.onerror = () => {
    es.close(); R.es = null;
    R.retry = setTimeout(async () => {
      try { await GET('/api/queue/state'); roomStart(); } catch (e) { if (e.status !== 401) R.retry = setTimeout(roomStart, 5000); }
    }, 3000);
  };
}
setInterval(roomTick, 1000);
// The room stream sends its state at once and every 5 s. Silence means something between us holds streams back.
function pollInstead() {
  LIVE.poll = true; try { sessionStorage.setItem('studio.poll', '1'); } catch { /* still polls until reload */ }
  R.es.close(); R.es = null; R.opened = true; roomStart();
  if (A.es) { A.es.close(); A.es = null; connectStream(); }
  for (const t of TM.terms) if (t.es) { t.es.close(); t.box.querySelector('.sk-term')?.remove(); termStream(t); }
}
setInterval(() => { if (!LIVE.poll && R.es && !document.hidden && Date.now() - R.heard > (R.live ? 15000 : 6000)) pollInstead(); }, 1000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) R.heard = Date.now(); });
new ResizeObserver(() => { if (D.open && D.built) dashPosition(); roomPosition(); }).observe($('#editor-stack'));
addEventListener('resize', () => { if (D.open && D.built) dashPosition(); });
document.addEventListener('visibilitychange', () => { if (!document.hidden) dashPoll(); });
document.addEventListener('visibilitychange', () => { if (!document.hidden && !buildNotice) GET('/api/auth/state', null, { noAuthRedirect: true }).catch(() => {}); });   // a phone tab coming back
/* ------------------------------------------------------------------ phones and tablets */
const LAYOUT = { phone: matchMedia('(max-width: 760px)'), tablet: matchMedia('(max-width: 1100px)'), compact: matchMedia('(max-width: 1000px)'), moved: [] };
const isPhone = () => LAYOUT.phone.matches;
// Controls that do not fit in a narrow top bar move into the "More" sheet, keeping their handlers.
const SHEET_CONTROLS = ['.model-pick', '#tg-sandbox', '#btn-sandbox-cfg', '#tg-apply', '#tg-cmd', '#btn-room', '#btn-dash', '#btn-cdash', '#btn-toggle-agent', '#btn-theme', '#btn-guide', '#btn-about', '#btn-account', '#btn-logout'];
function closeSheet() { $('#top-sheet').classList.add('hidden'); $('#btn-more').setAttribute('aria-expanded', 'false'); }
function arrangeTopBar() {
  const sheet = $('#top-sheet');
  if (LAYOUT.compact.matches && !LAYOUT.moved.length) {
    for (const sel of SHEET_CONTROLS) {
      const node = $(sel); if (isPhone() === false && sel === '.model-pick') continue;
      const mark = document.createComment(sel); node.before(mark); LAYOUT.moved.push({ node, mark });
      const label = node.getAttribute('title') || node.getAttribute('aria-label');
      if (node.classList.contains('icon-btn') && label && !node.querySelector('.sheet-label')) node.append(el('span', { class: 'sheet-label', text: label }));
      sheet.append(node);
    }
  } else if (!LAYOUT.compact.matches && LAYOUT.moved.length) {
    for (const { node, mark } of LAYOUT.moved) { mark.replaceWith(node); }
    LAYOUT.moved = []; closeSheet();
  }
}
function showSection(name, { store = true } = {}) {
  if (!isPhone()) return;
  document.documentElement.dataset.section = name;
  $$('#mobile-tabs button').forEach(b => { const on = b.dataset.tab === name; b.classList.toggle('active', on); b.setAttribute('aria-current', on ? 'page' : 'false'); });
  if (store) lsSet('studio.section', name);
  if (name === 'terminal') { if (S.ws) showPanel(true); setTimeout(() => { if (TM.active) termFit(TM.active); }, 60); }
  if (name === 'editor') setTimeout(() => { E.ed?.layout(); E.diffEd?.layout(); }, 30);
  if (name === 'chat') setTimeout(scrollMaybe, 30);
}
function applyLayout() {
  const root = document.documentElement, phone = isPhone(), tablet = LAYOUT.tablet.matches && !phone;
  root.classList.toggle('phone', phone); root.classList.toggle('tablet', tablet);
  if (LAYOUT.moved.length) { for (const { node, mark } of LAYOUT.moved) mark.replaceWith(node); LAYOUT.moved = []; closeSheet(); }
  arrangeTopBar();
  if (phone) showSection(['files', 'editor', 'chat', 'terminal'].includes(lsGet('studio.section')) ? lsGet('studio.section') : 'chat', { store: false });
  else delete root.dataset.section;
  if (tablet && !LAYOUT.tabletSeen) { LAYOUT.tabletSeen = true; $('#sidebar').classList.add('collapsed'); }
  const small = { minimap: { enabled: !phone && S.minimap }, lineNumbersMinChars: phone ? 3 : 5, folding: !phone, glyphMargin: !phone };
  E.ed?.updateOptions(small); E.diffEd?.updateOptions({ renderSideBySide: !phone, ...small });
  setTimeout(() => { E.ed?.layout(); E.diffEd?.layout(); if (TM.active) termFit(TM.active); if (D.open && D.built) dashPosition(); }, 60);
}
$$('#mobile-tabs button').forEach(b => { b.onclick = () => showSection(b.dataset.tab); });
$('#btn-more').onclick = e => { e.stopPropagation(); const hidden = $('#top-sheet').classList.toggle('hidden'); $('#btn-more').setAttribute('aria-expanded', hidden ? 'false' : 'true'); };
$('#top-sheet').addEventListener('click', e => { if (e.target.closest('.icon-btn')) setTimeout(closeSheet, 0); });
document.addEventListener('pointerdown', e => { if (!e.target.closest('#top-sheet, #btn-more')) closeSheet(); });
treeEl.addEventListener('click', e => { if (LAYOUT.tablet.matches && !isPhone() && e.target.closest('.t-row[data-type="file"]')) { $('#sidebar').classList.add('collapsed'); $('#split-left').classList.add('hidden'); } });
for (const query of [LAYOUT.phone, LAYOUT.tablet, LAYOUT.compact]) query.addEventListener('change', applyLayout);
async function start() {
  applyLayout();
  fillIcons();
  updateCtxLabel();
  let st;
  try { st = await GET('/api/auth/state', null, { noAuthRedirect: true }); }
  catch (e) { showLogin(false); $('#login-error').textContent = 'Cannot reach the server: ' + e.message; return; }
  const link = /^#(invite|reset)=(.+)$/.exec(location.hash);
  if (link) {
    const [kind, token] = [link[1], decodeURIComponent(link[2])];
    try {
      const info = await GET(kind === 'invite' ? '/api/auth/invite' : '/api/auth/reset', { token }, { noAuthRedirect: true });
      showLogin(false, kind, { token, sub: kind === 'reset' ? `Choose a new password for ${info.username}.` : undefined });
    } catch (e) {
      clearLinkToken();
      showLogin(st.setup_needed, undefined, { error: e.status === 429 ? 'Too many attempts. Wait a minute and try again.' : e.message });
    }
    return;
  }
  if (st.logged_in) await bootApp(); else showLogin(st.setup_needed);
}
start();
// The browser tests drive a few internals directly, only when they set this flag in their own profile.
try { if (localStorage.getItem('studio.test') === '1') window.__studio = { handleEvent, setRunning, renderStatus, roomBadge, planCard, planShow, planCount, A, TM }; } catch { /* storage blocked */ }
})();
