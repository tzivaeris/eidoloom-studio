// Records the README video: a real model in a private Studio, a failure the test script hides, the fix, the tests passing.
//
//   node sandbox/demo/record.mjs --out DIR --model NAME [--name Strata] [--fixture DIR] [--prompt TEXT] [--reject-first STEER] [--gateway URL] [--probe]
//
// --name is the name the page shows for the model (config model_names); --probe stops before sending, with a screenshot.
//
// The private Studio has its own config and data under <out>/instance, the project is a copy of demo/fixture in <out>/work,
// and commands run on the host in that folder (Sandbox off), with auto-run on and edits asked for, so the diff card shows.
// It writes <out>/video.webm, <out>/events.json (the chat) and <out>/timeline.json (when each thing happened, for editing).
import { spawn } from 'node:child_process';
import { createRequire } from 'node:module';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const require = createRequire(path.join(ROOT, 'tests', 'browser', 'package.json'));
const { chromium } = require('playwright');

const arg = (name, dflt) => { const i = process.argv.indexOf('--' + name); return i > 0 ? process.argv[i + 1] : dflt; };
const out = path.resolve(arg('out', path.join(ROOT, 'sandbox', 'runs', 'demo', 'take')));
const model = arg('model', '');
if (!model) { console.error('usage: node sandbox/demo/record.mjs --out DIR --model NAME [--name SHOWN] [--fixture DIR] [--prompt TEXT] [--reject-first STEER] [--gateway URL] [--probe]'); process.exit(2); }
const gateway = arg('gateway', 'http://127.0.0.1:8787');
const port = Number(arg('port', '8797'));
const limitMs = Number(arg('minutes', '15')) * 60000;
const PROMPT = arg('prompt', 'Run ./run_tests.sh and tell me whether everything passes. If anything fails, fix the code, not the tests.');
const STEER = arg('reject-first', '');   // reject the first edit and send this as guidance while the agent works

fs.rmSync(out, { recursive: true, force: true });
const instance = path.join(out, 'instance'), work = path.join(out, 'work', 'bookshop');
fs.mkdirSync(instance, { recursive: true });
fs.cpSync(path.join(HERE, arg('fixture', 'fixture')), work, { recursive: true });
const cfg = path.join(instance, 'config.json');
fs.writeFileSync(cfg, JSON.stringify({ host: '127.0.0.1', port, gateway, workspace_roots: [path.join(out, 'work')], user_root: path.join(out, 'no-users'),
  default_model: model, max_turns: 60, command_timeout_s: 120, sandbox_default: false, skills: false, backup_interval_h: 0, updates: false, codex: false,
  ...(arg('name') ? { model_names: { [model]: arg('name') } } : {}) }));
const password = crypto.randomBytes(15).toString('base64url');
const log = fs.openSync(path.join(out, 'studio.log'), 'a');
const studio = spawn('python3', [path.join(ROOT, 'studio.py'), '--config', cfg], { stdio: ['ignore', log, log], env: { ...process.env, PYTHONUNBUFFERED: '1' } });
const url = `http://127.0.0.1:${port}/`;
for (let i = 0; i < 100; i++) { try { await fetch(url + 'api/auth/state'); break; } catch { await new Promise(r => setTimeout(r, 200)); } }

const t0 = Date.now();
const timeline = [];
const mark = what => { timeline.push({ s: +((Date.now() - t0) / 1000).toFixed(1), what }); console.log(timeline.at(-1).s, what); };
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, recordVideo: { dir: out, size: { width: 1280, height: 800 } }, colorScheme: 'dark' });
const page = await context.newPage();
try {
  await page.goto(url);
  await page.waitForSelector('#login:not(.hidden)');
  await page.fill('#login-user', 'admin'); await page.fill('#login-pw', password); await page.fill('#login-pw2', password);
  await page.click('#login-submit');
  await page.waitForSelector('#app:not(.hidden)');
  const api = (p, body) => page.evaluate(async ([p, body]) => {
    const r = await fetch(p, { method: body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json', 'X-Studio': '1' }, body: body ? JSON.stringify(body) : undefined });
    return r.json().catch(() => null);
  }, [p, body]);
  const { workspace } = await api('/api/workspace/open', { path: work });
  await api('/api/prefs', { key: 'last_ws', value: work });
  await page.evaluate(([ws, model]) => {
    localStorage.setItem('studio.ws', JSON.stringify(ws)); sessionStorage.setItem('studio.windowFolder', ws.path);
    localStorage.setItem('studio.model', model); localStorage.setItem('studio.theme', 'dark');
    localStorage.setItem('studio.autoCmd', '1'); localStorage.setItem('studio.autoApply', '0');
  }, [workspace, model]);
  await api('/api/prefs', { key: 'auto_commands', value: true });
  await api('/api/prefs', { key: 'auto_apply', value: false });
  await page.reload(); await page.waitForSelector('#app:not(.hidden)');
  await page.waitForFunction(() => window.monaco, null, { timeout: 40000 }).catch(() => {});
  for (let i = 0; i < 6 && !(await page.locator('.tab', { hasText: 'invoice.py' }).count()); i++) {
    await page.locator('.t-row[data-path="invoice.py"]').dblclick().catch(e => console.log('open file:', e.message));
    await page.waitForTimeout(1000);
  }
  await page.waitForTimeout(2500);
  mark('ready');
  if (process.argv.includes('--probe')) {
    await page.screenshot({ path: path.join(out, 'probe.png') });
    console.log(JSON.stringify(await page.evaluate(() => ({ monaco: !!window.monaco, rows: [...document.querySelectorAll('.t-row')].map(r => r.dataset.path), tabs: [...document.querySelectorAll('.tab')].map(t => t.textContent) }))));
    throw new Error('probe done');
  }
  await page.click('#agent-input');
  await page.keyboard.type(PROMPT, { delay: 28 });
  await page.waitForTimeout(600);
  await page.keyboard.press('Enter');
  mark('sent');
  await page.waitForFunction(() => !document.querySelector('#agent-stop')?.classList.contains('hidden'), null, { timeout: 30000 }).catch(() => {});
  const end = Date.now() + limitMs;
  let rejected = false, ranSeen = 0;
  const seen = new Set();
  while (Date.now() < end) {
    const accept = page.locator('.m-appr.pending button[data-act="accept"]').first();
    if (await accept.isVisible().catch(() => false)) {
      mark('approval shown');
      await accept.scrollIntoViewIfNeeded().catch(() => {});
      await page.waitForTimeout(3500);
      if (STEER && !rejected) {
        rejected = true;
        await page.click('#agent-input');
        await page.keyboard.type(STEER, { delay: 28 });
        await page.waitForTimeout(500);
        await page.keyboard.press('Enter');
        mark('steered');
        await page.waitForTimeout(1200);
        await page.locator('.m-appr.pending button[data-act="reject"]').first().click().catch(() => {});
        mark('rejected');
      } else {
        await accept.click().catch(() => {});
        mark('approved');
      }
    }
    const shown = await page.locator('#msgs').innerText().catch(() => '');
    for (const [key, what] of [[/exit 0, but/, 'hidden failure flagged'], [/Tests passed/, 'tests passed']]) {
      if (key.test(shown) && !seen.has(what)) { seen.add(what); mark(what); }
    }
    const runs = (shown.match(/Run command/g) || []).length;
    if (runs > ranSeen) { ranSeen = runs; mark(`command ${runs}`); }
    if (await page.locator('#agent-stop').evaluate(n => n.classList.contains('hidden')).catch(() => false)) break;
    await page.waitForTimeout(400);
  }
  mark(Date.now() >= end ? 'time limit' : 'run ended');
  await page.locator('#msgs').evaluate(n => { n.scrollTop = n.scrollHeight; }).catch(() => {});
  await page.waitForTimeout(6000);
  mark('end');
  const session = await page.evaluate(() => window.__studio?.A?.session || null).catch(() => null);
  const sessions = await api(`/api/agent/sessions?ws=${workspace.id}`);
  const id = session || sessions?.sessions?.[0]?.id;
  if (id) fs.writeFileSync(path.join(out, 'events.json'), JSON.stringify((await api(`/api/agent/session?id=${id}`))?.events || [], null, 1));
} finally {
  const video = page.video();
  await context.close(); await browser.close();
  if (video) fs.renameSync(await video.path(), path.join(out, 'video.webm'));
  fs.writeFileSync(path.join(out, 'timeline.json'), JSON.stringify(timeline, null, 1));
  studio.kill('SIGTERM');
}
