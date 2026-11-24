// Drives the real web app in Chromium, against a private Studio and the mock gateway.
// One-time setup: cd tests/browser && npm install && npx playwright install chromium
// Run: node tests/browser/run.mjs    (Monaco and xterm come from the CDN, so it needs internet access)
import { spawn, execFileSync } from 'node:child_process';
import { chromium } from 'playwright';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'studio-browser-'));
const projects = path.join(tmp, 'projects');
process.env.MOCK_CODEX_LOG = path.join(tmp, 'codex.jsonl');      // the fake Codex keeps its conversations beside the test
let passed = 0, failed = 0;
const check = (label, ok, detail = '') => { ok ? passed++ : failed++; console.log(`${ok ? '  ok  ' : '  FAIL'}  ${label}${ok ? '' : '   ' + String(detail).slice(0, 300)}`); };
const section = name => { console.log(name); globalThis.__probe?.(name); };
const freePort = () => new Promise(resolve => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const { port } = s.address(); s.close(() => resolve(port)); }); });
const waitPort = async port => { for (let i = 0; i < 100; i++) { const ok = await new Promise(r => { const c = net.connect(port, '127.0.0.1', () => { c.end(); r(true); }); c.on('error', () => r(false)); }); if (ok) return; await new Promise(r => setTimeout(r, 100)); } throw new Error('port ' + port); };
const git = (cwd, ...args) => execFileSync('git', ['-c', 'user.name=T', '-c', 'user.email=t@t', ...args], { cwd, encoding: 'utf8' });
function project(name, files = {}) {
  const dir = path.join(projects, name); fs.mkdirSync(dir, { recursive: true });
  for (const [f, text] of Object.entries(files)) fs.writeFileSync(path.join(dir, f), text);
  return dir;
}

const gwPort = await freePort(), port = await freePort(), voicePort = await freePort();
fs.mkdirSync(projects);
const cfg = path.join(tmp, 'app', 'config.json');
fs.mkdirSync(path.dirname(cfg));
fs.writeFileSync(cfg, JSON.stringify({ codex: true, codex_command: `python3 ${path.join(ROOT, 'tests', 'mock_codex.py')}`, host: '127.0.0.1', port, voice_url: `http://127.0.0.1:${voicePort}`, gateway: `http://127.0.0.1:${gwPort}`, workspace_roots: [projects], default_model: 'keystone:strata', max_turns: 10, user_root: path.join(tmp, 'users'), comfyui_url: `http://127.0.0.1:${gwPort}`, updates: true, updates_catalog: 'test', installer_catalog: 'test' }));
const gw = spawn('python3', [path.join(ROOT, 'mock_gateway.py'), String(gwPort)], { stdio: 'ignore', env: { ...process.env, MOCK_CONTEXT_TOKENS: '1000' } });
const studioLog = fs.openSync(path.join(tmp, 'studio.log'), 'a');   // read back when a page request fails with 500
let studio = spawn('python3', [path.join(ROOT, 'studio.py'), '--config', cfg], { stdio: ['ignore', studioLog, studioLog], env: { ...process.env, PYTHONUNBUFFERED: '1' } });
// The voice engine in its fake mode: a tone instead of the model, so the Voices view can be driven without a graphics card.
const voiceEngine = spawn('python3', [path.join(ROOT, 'tools', 'voice_engine.py')], { stdio: 'ignore', env: { ...process.env, VOICE_FAKE: '1', VOICE_PORT: String(voicePort), VOICE_OUT_ROOTS: tmp } });
const browser = await chromium.launch();
const url = `http://127.0.0.1:${port}/`;
const password = 'browser-test-' + Math.random().toString(36).slice(2);
const errors = [];

try {
  await waitPort(gwPort); await waitPort(port);
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, acceptDownloads: true });
  const page = await context.newPage();
  page.on('pageerror', e => errors.push('page: ' + e.message));
  page.on('console', m => { if (m.type() === 'error' && !/favicon/.test(m.text())) errors.push(m.text()); });
  page.on('response', r => { if (r.status() >= 500) errors.push(`HTTP ${r.status()} from ${r.url().replace(/\/(preview|live)\/[^/]+\//, '/$1/…/')}`); });
  const api = (p, body) => page.evaluate(async ([p, body]) => {
    const r = await fetch(p, { method: body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json', 'X-Studio': '1' }, body: body ? JSON.stringify(body) : undefined });
    return { status: r.status, data: await r.json().catch(() => null) };
  }, [p, body]);
  const openProject = async dir => {
    const { data } = await api('/api/workspace/open', { path: dir });
    await api('/api/prefs', { key: 'last_ws', value: dir });
    await page.evaluate(ws => { localStorage.setItem('studio.ws', JSON.stringify(ws)); sessionStorage.setItem('studio.windowFolder', ws.path); Object.keys(localStorage).filter(k => k.startsWith('studio.session.')).forEach(k => localStorage.removeItem(k)); }, data.workspace);
    await page.reload(); await page.waitForSelector('#app:not(.hidden)'); await page.waitForTimeout(400);
    return data.workspace;
  };
  // The page must never grow taller than the window: a terminal once did, by 116 to 480 pixels, and clicks then scrolled it.
  const overflows = [];
  globalThis.__probe = name => page.evaluate(() => document.scrollingElement.scrollHeight - innerHeight).then(extra => { if (extra > 1) overflows.push(`${name}: ${extra}px`); }).catch(() => {});
  const send = async text => { await page.fill('#agent-input', text); await page.click('#agent-send'); };
  const ended = () => page.waitForFunction(() => document.querySelector('#agent-stop') && document.querySelector('#agent-stop').classList.contains('hidden'), null, { timeout: 30000 });

  section('sign in');
  await page.goto(url);
  await page.waitForSelector('#login:not(.hidden)');
  await page.fill('#login-user', 'admin'); await page.fill('#login-pw', password); await page.fill('#login-pw2', password);
  await page.click('#login-submit');
  await page.waitForSelector('#app:not(.hidden)');
  check('the first account can be created in the page', true);

  section('real terminal');
  const term = project('term', { 'README.md': '# Sample once\n' });
  await openProject(term);
  await page.waitForFunction(() => window.monaco, null, { timeout: 40000 });
  check('xterm loads alongside Monaco', await page.evaluate(() => !!(window.Terminal && window.FitAddon && window.monaco)));
  await page.click('#sb-term');
  await page.waitForSelector('.xterm', { timeout: 15000 });
  check('the terminal is xterm, not the fallback', await page.locator('.term-plain').count() === 0);
  await page.locator('.xterm').first().click();      // straight away: typing before the shell is ready must still work
  await page.keyboard.type('echo browser-$((40+2))\n');
  await page.waitForFunction(() => document.querySelector('.xterm-rows')?.innerText.includes('browser-42') || document.querySelector('.xterm-accessibility-tree')?.innerText.includes('browser-42'), null, { timeout: 8000 }).then(() => check('typing runs a command and shows its output', true)).catch(async () => check('typing runs a command and shows its output', false, JSON.stringify(await page.evaluate(() => ({ xterms: document.querySelectorAll('.xterm').length, tabs: document.querySelectorAll('.term-tab').length, focus: document.activeElement?.className, text: document.querySelector('.xterm-rows')?.innerText.slice(0, 300) })))));
  check('the terminal is sized to its panel', await page.evaluate(() => { const r = document.querySelector('.xterm-screen')?.getBoundingClientRect(); return r && r.width > 200 && r.height > 50; }));
  {
    const scr = await page.locator('.xterm-screen').first().boundingBox();
    await page.mouse.move(scr.x + 4, scr.y + 6); await page.mouse.down(); await page.mouse.move(scr.x + 160, scr.y + 40, { steps: 6 }); await page.mouse.up();
    check('selecting with the mouse shows the Copy bar', await page.locator('.term-selbar:not(.hidden)').first().isVisible().catch(() => false));
    await page.keyboard.press('Control+Shift+C');
    check('Ctrl+Shift+C copies the selection', await page.locator('.toast', { hasText: /Copied/ }).first().waitFor({ timeout: 3000 }).then(() => true, () => false));
    check('and the bar goes away', await page.locator('.term-selbar:not(.hidden)').count() === 0);
  }

  section('themes');
  const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const choose = async name => { await page.click('#btn-theme'); await page.click(`.menu-ctx .mi:has-text("${name}")`); await page.waitForTimeout(350); };
  const darkBg = await bg();
  await choose('Light'); check('Light changes the page', await bg() === 'rgb(255, 255, 255)', await bg());
  check('Light reaches the editor and the terminal', await page.evaluate(() => getComputedStyle(document.querySelector('.xterm-viewport')).backgroundColor) !== 'rgb(31, 31, 31)');
  await choose('Sepia'); check('Sepia changes the page', await bg() === 'rgb(244, 236, 216)', await bg());
  await page.reload(); await page.waitForSelector('#app:not(.hidden)');
  check('the choice survives a reload', await bg() === 'rgb(244, 236, 216)');
  await choose('Dark'); check('Dark comes back', await bg() === darkBg);

  section('gauge colors');
  let fakeBusy = 50;
  await page.route(/\/api\/model\/status/, route => route.fulfill({ json: { state: 'work', label: 'Test', model: 'm', detail: '', stream_chars: 0, metrics: { gpu: { gpu_busy: fakeBusy, vram_used: 50, vram_total: 100 }, system: { used: 1, total: 100, cpu_percent: 1 }, process: {}, placement: {}, strain: { percent: 40, band: 'yellow' },
    residents: { card: { name: 'Test card', total: 24 * 2 ** 30, used: 18 * 2 ** 30 }, items: [{ name: 'big:strata-iq3', what: 'language model (Strata)', vram: 15 * 2 ** 30, ram: 42 * 2 ** 30 },
      { name: 'ComfyUI', what: 'pictures, video and music', vram: 2 * 2 ** 30, ram: 1.5 * 2 ** 30 }] } }, last_generation: {} } }));
  await page.click('#btn-dash');
  await page.waitForSelector('#dash:not(.hidden)');
  const gaugeColor = async (label, value) => {
    await page.waitForFunction(([l, v]) => document.querySelector(`.dash-ring[aria-label^="${l}: ${v}"]`), [label, value], { timeout: 12000 });
    return page.evaluate(l => document.querySelector(`.dash-ring[aria-label^="${l}:"]`).style.getPropertyValue('--gauge'), label);
  };
  check('a load gauge keeps its own color when not near full', await gaugeColor('GPU', 50) === 'var(--gauge-blue)', await gaugeColor('GPU', 50));
  check('pressure uses the band color', await gaugeColor('Pressure', 40) === 'var(--gauge-yellow)');
  fakeBusy = 95;
  check('it turns orange near 100', await gaugeColor('GPU', 95) === 'var(--gauge-orange)');
  fakeBusy = 100;
  check('it turns red at 100', await gaugeColor('GPU', 100) === 'var(--gauge-red)');
  const loadedText = await page.locator('#dash .dash-loaded').innerText();
  check('the dashboard lists what is loaded in VRAM and RAM, biggest first', loadedText.includes('Loaded now') && loadedText.includes('Test card · 18.0 GB of 24.0 GB')
    && await page.locator('#dash .dash-res').count() === 3 && (await page.locator('#dash .dash-res').first().innerText()).includes('big:strata-iq3')
    && (await page.locator('#dash .dash-res').first().innerText()).includes('42.0 GB') && (await page.locator('#dash .dash-res').nth(1).innerText()).includes('ComfyUI'), loadedText);
  check('with what is left of the card as Other', (await page.locator('#dash .dash-res').last().innerText()).includes('Other'));
  check('the colors are defined', await page.evaluate(() => ['blue', 'cyan', 'mint', 'green', 'yellow', 'orange', 'coral', 'red'].every(n => getComputedStyle(document.documentElement).getPropertyValue('--gauge-' + n).trim())));
  await page.click('#btn-dash');
  await page.unroute(/\/api\/model\/status/);

  section('an updated Studio');
  check('no update notice while the page matches Studio', await page.locator('.update-notice').count() === 0);
  const cssPath = path.join(ROOT, 'web', 'app.css'), cssTime = fs.statSync(cssPath).mtime;
  try {
    fs.utimesSync(cssPath, new Date(), new Date(cssTime.getTime() + 2000));
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))); await page.waitForTimeout(300);
    await page.waitForSelector('.update-notice', { timeout: 3000 }).catch(() => {});
    check('after an update, an open page offers to reload', (await page.locator('.update-notice').innerText().catch(() => '')).includes('Studio was updated') && await page.locator('.update-notice button:has-text("Reload")').count() === 1);
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))); await page.waitForTimeout(300);
    check('it shows once', await page.locator('.update-notice').count() === 1);
    await page.click('.update-notice button:has-text("Later")');
    check('Later closes it', await page.locator('.update-notice').count() === 0);
    await page.reload(); await page.waitForSelector('#app:not(.hidden)'); await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange'))); await page.waitForTimeout(300);
    check('a reloaded page has the new build and no notice', await page.locator('.update-notice').count() === 0);
  } finally { fs.utimesSync(cssPath, new Date(), cssTime); }
  await page.reload(); await page.waitForSelector('#app:not(.hidden)');

  section('about');
  const brandAssetsLoaded = await page.evaluate(async () => {
    const sources = ['assets/eidoloom-mark-original.png'];
    return Promise.all(sources.map(src => new Promise(resolve => { const image = new Image(); image.onload = () => resolve(image.naturalWidth > 0); image.onerror = () => resolve(false); image.src = src; }))).then(results => results.every(Boolean));
  });
  check('the app uses the approved Eidoloom mark and text lockup', (await page.locator('link[rel="icon"]').getAttribute('href')) === 'assets/eidoloom-mark-original.png'
    && await page.locator('#topbar .brand-mark').count() === 1 && (await page.locator('#topbar .brand-name').innerText()) === 'Eidoloom Studio' && brandAssetsLoaded);
  await page.click('#btn-about');
  const about = await page.locator('.dialog').innerText();
  check('About names the app and its maker', about.includes('Eidoloom Studio') && about.includes('John Chatzopoulos') && about.includes('November 2026'), about);
  check('About shows the licence notice the AGPL asks for, and no company', about.includes('GNU Affero General Public License') && about.includes('no warranty') && !about.includes('Dyslexia'), about);
  await page.keyboard.press('Escape');
  check('Escape closes it', await page.locator('.dialog').count() === 0);

  section('parts of an edit');
  const parts = project('parts', { 'parts.txt': 'a\nb\nc\nd\ne\nf\ng\nh\n' });
  await openProject(parts);
  await send('Please write two parts');
  await page.waitForSelector('.m-appr.pending');
  await page.click('.m-appr button:has-text("Choose parts")');
  check('the card lists both parts', await page.locator('.hunk').count() === 2);
  await page.locator('.hunk input').nth(0).uncheck();
  await page.click('button:has-text("Accept selected")');
  await ended();
  check('only the chosen part is written', fs.readFileSync(path.join(parts, 'parts.txt'), 'utf8') === 'a\nb\nc\nd\ne\nf\ng\nSEVEN\n', fs.readFileSync(path.join(parts, 'parts.txt'), 'utf8'));
  check('the card says what happened', (await page.locator('.m-appr').first().innerText()).includes('Accepted 1 of 2 parts'));
  await page.click('.m-appr >> nth=0 >> text=Open diff').catch(() => {});

  section('action cards');
  const lines = Array.from({ length: 40 }, (_, i) => `line ${i + 1}\n`).join('');
  const cards = project('cards', { 'lines.txt': lines, 'old.txt': 'remove me\n' });
  await openProject(cards);
  await page.evaluate(() => { const set = (id, on) => { const b = document.querySelector(id); if ((b.getAttribute('aria-checked') === 'true') !== on) b.click(); }; set('#tg-apply', true); set('#tg-cmd', true); });
  await send('Please make two edits');
  await page.waitForSelector('.m-tool.has-change .diff-row.add', { state: 'attached', timeout: 20000 });
  await page.waitForSelector('.m-tool .out-preview', { state: 'attached', timeout: 20000 });
  await ended();
  const editCard = page.locator('.m-tool.has-change').first();
  const editText = await editCard.innerText();
  check('a collapsed edit card names the action, file and line counts', editText.includes('Edit') && editText.includes('lines.txt') && editText.includes('+2') && editText.includes('−2'), editText.slice(0, 120));
  check('the turn count does not show the limit early', /turn \d+ ·/.test(await page.locator('#agent-status').innerText()) && !/turn \d+\//.test(await page.locator('#agent-status').innerText()), await page.locator('#agent-status').innerText());
  check('an edit starts collapsed', !(await editCard.locator('.diff-view').isVisible()) && (await editCard.boundingBox()).height < 45, (await editCard.boundingBox()).height);
  await editCard.locator('button:has-text("Expand")').click();
  const rows = editCard.locator('.diff-row');
  check('Expand shows added and removed lines inline', await editCard.locator('.diff-view').isVisible() && await editCard.locator('.diff-row.add').count() === 2 && await editCard.locator('.diff-row.del').count() === 2);
  check('with line numbers and a gap between distant changes', (await editCard.locator('.diff-row.add .ln').nth(1).innerText()) === '2' && await editCard.locator('.diff-row.sep').count() === 1 && await rows.count() < 20);
  await editCard.locator('button:has-text("Full view")').click();
  await page.waitForSelector('.dialog .diff-row');
  check('Full view opens it large', await page.locator('.dialog .diff-row.add').count() === 2);
  await page.keyboard.press('Escape');
  await editCard.locator('.row').click({ position: { x: 4, y: 8 } });
  check('clicking the row collapses it again', !(await editCard.locator('.diff-view').isVisible()));
  const runCard = page.locator('.m-tool.action', { hasText: 'Run command' }).first();
  const runRow = await runCard.locator('.row').innerText();
  check('a command card shows the command, line count and exit code on one line', runRow.includes('seq 1 9') && runRow.includes('9 lines') && runRow.includes('exit 4'), runRow);
  check('a failed command is marked', (await runCard.getAttribute('class')).includes('fail'));
  check('it starts collapsed', !(await runCard.locator('.out-preview').isVisible()) && !(await runCard.locator('.cmd-line').isVisible()) && (await runCard.boundingBox()).height < 70, (await runCard.boundingBox()).height);
  check('its second line shows the last line of output', (await runCard.locator('.cmd-peek').innerText()) === '9', await runCard.locator('.cmd-peek').innerText());
  await runCard.locator('button:has-text("Expand")').click();
  check('Expand shows the command and all the output', await runCard.locator('.cmd-line').isVisible() && (await runCard.locator('.out-preview').innerText()).trim() === '1\n2\n3\n4\n5\n6\n7\n8\n9');
  await runCard.locator('button:has-text("Collapse")').click();
  check('Collapse closes it again', !(await runCard.locator('.out-preview').isVisible()));
  await page.evaluate(() => { const b = document.querySelector('#tg-apply'); if (b.getAttribute('aria-checked') === 'true') b.click(); });
  await page.waitForTimeout(200);
  await page.click('#new-chat');
  await send('Please delete a file');
  await page.waitForSelector('.m-appr.pending');
  const deleteAsk = await page.locator('.m-appr.pending').innerText();
  check('deleting asks first and shows what goes', deleteAsk.includes('DELETE') && deleteAsk.includes('old.txt') && deleteAsk.includes('remove me') && deleteAsk.includes('Delete file'), deleteAsk.slice(0, 160));
  await page.click('.m-appr.pending button[data-act="accept"]');
  await page.waitForSelector('.m-tool.has-change', { timeout: 10000 });
  await ended();
  check('the deleted file is gone', !fs.existsSync(path.join(cards, 'old.txt')));
  const deleteCard = page.locator('.m-tool.has-change').first();
  check('its card says Delete', (await deleteCard.innerText()).includes('Delete') && await deleteCard.locator('.diff-row.del').count() === 1);
  await deleteCard.locator('button:has-text("Expand")').click();
  check('the approval card no longer repeats the diff', await page.locator('.m-appr .diff-view').count() === 0);
  await deleteCard.locator('button:has-text("Undo")').click();
  await page.waitForFunction(() => [...document.querySelectorAll('.m-tool.has-change button')].some(b => b.textContent === 'Undone'));
  check('Undo restores it', fs.existsSync(path.join(cards, 'old.txt')));
  await send('Please write a report');
  await page.waitForSelector('.m-appr.pending');
  const docAsk = await page.locator('.m-appr.pending').innerText();
  check('a new Word file asks first, showing its text', docAsk.includes('DOCUMENT') && docAsk.includes('report.docx') && docAsk.includes('| Maria | 18 |') && docAsk.includes('Save document'), docAsk.slice(0, 200));
  await page.click('.m-appr.pending button[data-act="accept"]');
  await page.waitForFunction(() => [...document.querySelectorAll('.m-tool.has-change')].some(n => n.innerText.includes('Save document')), null, { timeout: 10000 });
  await ended();
  check('and is written as a real .docx', fs.existsSync(path.join(cards, 'report.docx')) && fs.readFileSync(path.join(cards, 'report.docx')).subarray(0, 2).toString() === 'PK');
  await page.reload(); await page.waitForSelector('#app:not(.hidden)');
  await page.waitForSelector('.m-tool.has-change .diff-row', { state: 'attached', timeout: 8000 }).then(() => check('cards and diffs survive a reload', true)).catch(() => check('cards and diffs survive a reload', false));
  await page.evaluate(() => { const b = document.querySelector('#tg-apply'); if (b.getAttribute('aria-checked') === 'true') b.click(); const c = document.querySelector('#tg-cmd'); if (c.getAttribute('aria-checked') === 'true') c.click(); });
  await page.waitForTimeout(200);

  section('commands in the chat box');
  const typed = project('typed', { 'README.md': '# Sample once\n', 'Makefile': 'test:\n\t@echo all-good\n', 'deep.txt': 'pelican\n' });
  git(typed, 'init', '-q', '-b', 'main'); git(typed, 'add', '-A'); git(typed, 'commit', '-q', '-m', 'start');
  await openProject(typed);
  await page.evaluate(() => { const set = (id, on) => { const b = document.querySelector(id); if ((b.getAttribute('aria-checked') === 'true') !== on) b.click(); }; set('#tg-apply', true); set('#tg-cmd', true); });
  const box = page.locator('#agent-input');
  await box.click(); await page.keyboard.type('/');
  await page.waitForSelector('.cmd-item');
  check('typing / opens the command menu', await page.locator('.cmd-item').count() >= 10);
  await page.keyboard.type('mo');
  check('typing narrows it', (await page.locator('.cmd-item').allInnerTexts()).length === 1 && (await page.locator('.cmd-item').first().innerText()).includes('/model'));
  await page.keyboard.press('Escape');
  check('Escape closes it', await page.locator('.cmd-menu:not(.hidden)').count() === 0);
  await box.fill('');
  await send('/help');
  await page.waitForSelector('.m-note');
  check('/help lists the commands', (await page.locator('.m-note').last().innerText()).includes('/commit') && await box.inputValue() === '');
  check('commands never reach the model', await page.locator('.m-user').count() === 0);
  await send('/nonsense');
  await page.waitForFunction(() => [...document.querySelectorAll('.m-note')].pop()?.innerText.includes('not a command'));
  check('an unknown command explains itself', await page.locator('.m-user').count() === 0);
  await send('/model keystone-sub2');
  await page.waitForFunction(() => document.querySelector('#model-select').value === 'keystone-sub2:strata', null, { timeout: 5000 }).then(() => check('/model switches model', true)).catch(() => check('/model switches model', false));
  await send('/model keystone');
  await send('/undo');
  await page.waitForFunction(() => [...document.querySelectorAll('.m-note')].pop()?.innerText.includes('no edit to undo'));
  await send('/commit');
  await page.waitForFunction(() => [...document.querySelectorAll('.m-note')].pop()?.innerText.includes('Write a message'));
  await send('!echo typed-$((40+2)); exit 5');
  await page.waitForSelector('.m-tool.fail .out-preview', { timeout: 15000 });
  const shell = await page.locator('.m-tool.action').last().innerText();
  check('! runs a command and shows a card', shell.includes('You ran') && shell.includes('typed-42') && shell.includes('exit 5'), shell);
  check('the box is cleared', await box.inputValue() === '');
  await send('/test');
  await page.waitForFunction(() => [...document.querySelectorAll('.m-tool.action')].pop()?.innerText.includes('all-good'), null, { timeout: 15000 }).then(() => check('/test runs the project tests', true)).catch(() => check('/test runs the project tests', false));
  await page.reload(); await page.waitForSelector('#app:not(.hidden)');
  await page.waitForSelector('.m-tool.action', { timeout: 8000 }).then(() => check('typed commands survive a reload', true)).catch(() => check('typed commands survive a reload', false));
  await box.click(); await page.keyboard.type('hello, read @dee');
  await page.waitForSelector('.cmd-item');
  check('@ offers matching files', (await page.locator('.cmd-item').first().innerText()).includes('deep.txt'));
  await page.keyboard.press('Enter');
  check('choosing one puts it in the message', (await box.inputValue()) === 'hello, read @deep.txt ', await box.inputValue());
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => [...document.querySelectorAll('.m-user')].pop()?.innerText.includes('@deep.txt'), null, { timeout: 8000 });
  await ended();
  check('a person\'s words are shown as typed', !(await page.locator('.m-user').last().innerText()).includes('pelican'));
  await send('//help is what this says');
  await page.waitForFunction(() => [...document.querySelectorAll('.m-user')].pop()?.innerText.startsWith('/help is what'), null, { timeout: 8000 }).then(() => check('// sends text that starts with a slash', true)).catch(() => check('// sends text that starts with a slash', false));
  await ended();
  fs.writeFileSync(path.join(typed, 'README.md'), '# Changed\n');
  await send('/diff');
  await page.waitForSelector('.dialog .git-diff');
  check('/diff shows the changes', (await page.locator('.dialog .git-diff').innerText()).includes('# Changed'));
  await page.keyboard.press('Escape');
  await send('/commit Tidy the readme');
  await page.waitForFunction(() => [...document.querySelectorAll('.m-note')].pop()?.innerText.includes('Committed'), null, { timeout: 8000 }).then(() => check('/commit commits everything', git(typed, 'log', '-1', '--format=%s').trim() === 'Tidy the readme')).catch(() => check('/commit commits everything', false));
  const [exported] = await Promise.all([page.waitForEvent('download'), send('/export')]);
  check('/export downloads the report', exported.suggestedFilename().endsWith('.md'));
  await send('/new');
  await page.waitForFunction(() => document.querySelectorAll('.m-user').length === 0, null, { timeout: 5000 }).then(() => check('/new starts a new chat', true)).catch(() => check('/new starts a new chat', false));
  await page.evaluate(() => { const set = (id, on) => { const b = document.querySelector(id); if ((b.getAttribute('aria-checked') === 'true') !== on) b.click(); }; set('#tg-apply', false); set('#tg-cmd', false); });
  await page.waitForTimeout(200);
  await send('Please rename the word');
  await page.waitForSelector('.m-appr.pending');
  await box.fill('!echo during-a-run'); await box.press('Enter');      // while running, the send button is the stop button
  await page.waitForFunction(() => [...document.querySelectorAll('.m-note')].pop()?.innerText.includes('agent is working'));
  check('! waits while the agent is working', await page.locator('.m-tool .cmd-line', { hasText: 'during-a-run' }).count() === 0);
  await box.fill('/stop'); await box.press('Enter');
  await ended();
  check('/stop works during a run', (await page.locator('.m-done').last().innerText()).includes('Stopped'));
  await page.click('#new-chat').catch(() => {});

  section('test checks');
  const failing = project('failing', { 'README.md': '# Sample once\n', Makefile: 'test:\n\t@echo failing on purpose; exit 3\n' });
  await openProject(failing);
  await page.evaluate(() => { document.querySelector('#tg-apply').getAttribute('aria-checked') === 'false' && document.querySelector('#tg-apply').click(); document.querySelector('#tg-cmd').getAttribute('aria-checked') === 'false' && document.querySelector('#tg-cmd').click(); });
  await send('Please rename the word');
  await page.waitForSelector('.m-check', { timeout: 30000 });
  await ended();
  const verdict = await page.locator('.m-check').innerText();
  check('a failed check is shown beside the claim', verdict.includes('Tests failed') && verdict.includes('make test') && verdict.includes('said it had finished'), verdict);
  await page.click('.m-check button:has-text("Ask the agent to fix this")');
  check('the failure can be handed back to the agent', (await page.inputValue('#agent-input')).includes('failing on purpose'));
  await page.fill('#agent-input', '');
  await page.reload(); await page.waitForSelector('#app:not(.hidden)'); await page.waitForSelector('.m-check');
  check('a failed check keeps its button after a reload', await page.locator('.m-check button').count() === 1);
  await page.waitForSelector('.ctx-meter:not(.hidden)');
  check('the context pie shows the percentage', /^\d+%$/.test((await page.locator('.ctx-meter .ctx-pct').innerText()).trim()) && await page.locator('.ctx-meter .ctx-pie').evaluate(n => getComputedStyle(n).backgroundImage.includes('conic-gradient')));
  check('the context meter appears', (await page.locator('.ctx-meter').getAttribute('title')).includes('of 1.0k tokens'), await page.locator('.ctx-meter').getAttribute('title'));

  section('context warning');
  const memory = project('memory', { 'README.md': '# Sample once\n' });
  await openProject(memory);
  await send('hello, full memory');
  await page.waitForSelector('.ctx-meter.full', { state: 'attached' }).then(() => check('the meter warns when nearly full', true)).catch(() => check('the meter warns when nearly full', false));
  await ended();
  check('a warning explains it', await page.locator('.toast', { hasText: 'over 80% full' }).count() >= 1);

  section('resume and export');
  await page.evaluate(() => { const set = (id, on) => { const b = document.querySelector(id); if ((b.getAttribute('aria-checked') === 'true') !== on) b.click(); }; set('#tg-apply', false); set('#tg-cmd', false); });
  const stopped = project('stopped', { 'README.md': '# Sample once\n' });
  await openProject(stopped);
  await send('Please rename the word');
  await page.waitForSelector('.m-appr.pending');
  await page.click('#agent-stop');
  await ended();
  check('a stopped run offers Continue', await page.locator('.m-done button:has-text("Continue")').count() === 1);
  await page.reload(); await page.waitForSelector('#app:not(.hidden)'); await page.waitForSelector('.m-done');
  check('Continue is still there after a reload', await page.locator('.m-done button:has-text("Continue")').count() === 1);
  const [download] = await Promise.all([page.waitForEvent('download'), page.click('#chat-export')]);
  const report = fs.readFileSync(await download.path(), 'utf8');
  check('a chat exports as Markdown', download.suggestedFilename().endsWith('.md') && report.includes('Please rename the word') && report.includes('Run ended: stopped'), report.slice(0, 200));
  await page.click('.m-done button:has-text("Continue")');
  await page.waitForSelector('.m-appr.pending');
  check('Continue starts a new run in the same chat', (await page.locator('.m-user').last().innerText()).includes('Continue from where you stopped'));
  check('old Continue buttons go away', await page.locator('.m-done button:has-text("Continue")').count() === 0);
  await page.click('#agent-stop'); await ended();

  section('source control');
  const plain = project('plain', { 'a.txt': 'one\n', 'b.txt': 'two\n' });
  await openProject(plain);
  await page.click('.act[data-view="git"]');
  await page.click('#git-body button:has-text("Create repository")');
  await page.waitForSelector('.git-file');
  check('new files are listed', await page.locator('.git-file').count() === 2);
  await page.locator('.git-path', { hasText: 'a.txt' }).click();
  await page.waitForSelector('.git-diff');
  check('a file diff opens, with added lines marked', (await page.locator('.git-diff .add').allInnerTexts()).some(t => t.includes('+one')));
  await page.keyboard.press('Escape');
  await page.locator('.git-file', { hasText: 'b.txt' }).locator('input').uncheck();
  await page.fill('.git-message', 'first commit');
  await page.click('#git-body button:has-text("Commit")');
  await page.waitForSelector('.git-commit');
  check('a commit includes only the chosen files', git(plain, 'show', '--name-only', '--format=', 'HEAD').trim() === 'a.txt');
  check('unchecked files stay listed, still unchecked', await page.locator('.git-file').count() === 1);
  await page.click('.git-commit');
  await page.waitForSelector('.git-diff');
  check('a commit can be read', (await page.locator('.git-diff').innerText()).includes('first commit'));
  await page.keyboard.press('Escape');
  await page.click('.git-branch-row button[aria-label="New branch"]');
  await page.fill('.dialog input', 'topic');
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('.git-branch-row select')?.value === 'topic');
  check('a branch can be created', git(plain, 'symbolic-ref', '--short', 'HEAD').trim() === 'topic');

  section('run branches');
  const repo = project('repo', { 'README.md': '# Sample once\n' });
  git(repo, 'init', '-q', '-b', 'main'); git(repo, 'add', '-A'); git(repo, 'commit', '-q', '-m', 'start');
  await openProject(repo);
  await page.evaluate(() => { const b = document.querySelector('#tg-apply'); if (b.getAttribute('aria-checked') === 'false') b.click(); const c = document.querySelector('#tg-cmd'); if (c.getAttribute('aria-checked') === 'false') c.click(); });
  await page.waitForSelector('.git-option input');   // the view stays open across a reload, and loads for the new folder
  await page.check('.git-option input');
  await send('Please rename the word');
  await page.waitForSelector('.branch-acts button:has-text("Throw away")', { timeout: 30000 });
  await ended();
  check('the run is on its own branch', git(repo, 'symbolic-ref', '--short', 'HEAD').startsWith('studio/run-') && fs.readFileSync(path.join(repo, 'README.md'), 'utf8').includes('Demo'));
  await page.reload(); await page.waitForSelector('#app:not(.hidden)');
  await page.waitForSelector('.branch-acts button:has-text("Throw away")');
  check('Merge and Throw away survive a reload', await page.locator('.branch-acts button').count() === 2);
  await page.click('.branch-acts button:has-text("Throw away")');
  await page.click('.dialog button:has-text("Throw away")');
  await page.waitForFunction(() => document.querySelector('.branch-acts')?.innerText.includes('Thrown away'));
  check('throwing it away restores the folder', git(repo, 'symbolic-ref', '--short', 'HEAD').trim() === 'main' && fs.readFileSync(path.join(repo, 'README.md'), 'utf8').includes('Sample'));
  await page.uncheck('.git-option input');

  section('backups');
  await page.click('#btn-account');
  await page.click('.dialog button:has-text("Backups")');
  await page.click('.dialog button:has-text("Back up now")');
  await page.waitForSelector('.backup-row');
  check('an admin can make a backup and see it', (await page.locator('.backup-row').first().innerText()).includes('studio-data-'));
  await page.keyboard.press('Escape');

  section('sync between windows and browsers');
  const shared = project('shared', { 'README.md': '# Sample once\n', 'notes.txt': 'first\n' });
  await openProject(shared);
  await page.evaluate(() => { const set = (id, on) => { const b = document.querySelector(id); if ((b.getAttribute('aria-checked') === 'true') !== on) b.click(); }; set('#tg-apply', false); set('#tg-cmd', false); });
  await page.waitForTimeout(300);
  const secondBrowser = await browser.newContext({ viewport: { width: 1400, height: 900 } });   // no shared storage, like another machine
  const other = await secondBrowser.newPage();
  other.on('pageerror', e => errors.push('second browser: ' + e.message));
  await other.goto(url);
  await other.waitForSelector('#login:not(.hidden)');
  await other.fill('#login-user', 'admin'); await other.fill('#login-pw', password);
  await other.click('#login-submit');
  await other.waitForSelector('#app:not(.hidden)');
  await other.waitForFunction(() => document.querySelector('#ws-name')?.textContent === 'shared', null, { timeout: 8000 }).then(() => check('another browser opens the same folder', true)).catch(() => check('another browser opens the same folder', false));
  const otherBg = () => other.evaluate(() => getComputedStyle(document.body).backgroundColor);
  await page.click('#btn-theme'); await page.click('.menu-ctx .mi:has-text("Sepia")');
  await other.waitForFunction(() => getComputedStyle(document.body).backgroundColor === 'rgb(244, 236, 216)', null, { timeout: 8000 }).then(() => check('a theme change appears in the other browser', true)).catch(async () => check('a theme change appears in the other browser', false, await otherBg()));
  await page.click('#btn-theme'); await page.click('.menu-ctx .mi:has-text("Dark")');
  await other.waitForFunction(() => !document.documentElement.dataset.theme, null, { timeout: 8000 });
  await page.click('#tg-apply');
  await other.waitForFunction(() => document.querySelector('#tg-apply').getAttribute('aria-checked') === 'true', null, { timeout: 8000 }).then(() => check('the auto toggles follow', true)).catch(() => check('the auto toggles follow', false));
  await page.click('#tg-apply');
  await other.waitForFunction(() => document.querySelector('#tg-apply').getAttribute('aria-checked') === 'false', null, { timeout: 8000 });
  await send('Please rename the word');
  await other.waitForSelector('.m-user', { timeout: 8000 }).catch(() => {});
  check('a run started in one browser appears in the other', (await other.locator('.m-user').last().innerText().catch(() => '')).includes('Please rename the word'));
  await other.waitForSelector('.m-appr.pending', { timeout: 15000 }).catch(() => {});
  check('its approval shows there too', await other.locator('.m-appr.pending').count() === 1);
  await other.click('.m-appr.pending button[data-act="accept"]');
  await page.waitForSelector('.m-appr.resolved', { timeout: 8000 });
  check('answering in one browser resolves it in the other', true);
  check('the edit was applied', fs.readFileSync(path.join(shared, 'README.md'), 'utf8').includes('Demo'));
  await page.waitForSelector('.m-appr.pending', { timeout: 8000 });  // the echo command
  await page.click('.m-appr.pending button[data-act="accept"]');
  await other.waitForSelector('.m-done', { timeout: 15000 });
  await page.waitForSelector('.m-done', { timeout: 15000 });
  check('both see the run end', true);
  await other.locator('.m-think summary').first().click();
  const thought = await other.locator('.m-think').first().locator('.think-body').innerText();
  check('thinking is there at once in the other browser', thought.includes('exploring the project'), thought);
  await page.click('.act[data-view="explorer"]');
  await page.dblclick('.t-row:has-text("notes.txt")'); await other.dblclick('.t-row:has-text("notes.txt")');
  await page.waitForTimeout(800);
  await api('/api/files/write', { ws: (await page.evaluate(() => JSON.parse(localStorage.getItem('studio.ws')).id)), path: 'notes.txt', content: 'second\n' });
  await other.waitForFunction(() => window.monaco?.editor.getModels().some(m => m.getValue().includes('second')), null, { timeout: 8000 }).then(() => check('a saved file updates in the other browser', true)).catch(() => check('a saved file updates in the other browser', false));
  if (await page.locator('#panel.hidden').count()) await page.click('#sb-term');
  await page.waitForSelector('.xterm', { timeout: 15000 });
  await other.waitForFunction(() => document.querySelectorAll('.term-tab').length >= 1, null, { timeout: 8000 }).then(() => check('a new terminal appears in the other browser', true)).catch(() => check('a new terminal appears in the other browser', false));
  if (await other.locator('#panel.hidden').count()) await other.click('#sb-term');
  await page.locator('.xterm').last().click();
  await page.keyboard.type('echo shared-$((40+2))\n');
  await other.waitForFunction(() => [...document.querySelectorAll('.xterm-rows, .xterm-accessibility-tree')].some(n => n.innerText.includes('shared-42')), null, { timeout: 8000 }).then(() => check('terminal output shows in both', true)).catch(() => check('terminal output shows in both', false));
  await other.reload(); await other.waitForSelector('#app:not(.hidden)');
  await other.waitForFunction(() => document.querySelectorAll('.term-tab').length >= 1, null, { timeout: 8000 }).then(() => check('terminals survive a reload', true)).catch(() => check('terminals survive a reload', false));
  const elsewhere = project('elsewhere', { 'x.txt': 'x\n' });
  const before = await other.evaluate(() => document.querySelector('#ws-name').textContent);
  await openProject(elsewhere);
  await other.waitForTimeout(600);
  check('changing folder in one window does not move another', before === 'shared' && await other.evaluate(() => document.querySelector('#ws-name').textContent) === 'shared');
  await other.reload(); await other.waitForSelector('#app:not(.hidden)'); await other.waitForTimeout(500);
  check('a window keeps its own folder across a reload', await other.evaluate(() => document.querySelector('#ws-name').textContent) === 'shared');
  const third = await secondBrowser.newPage();
  await third.goto(url); await third.waitForSelector('#app:not(.hidden)');
  await third.waitForFunction(() => document.querySelector('#ws-name')?.textContent === 'elsewhere', null, { timeout: 8000 }).then(() => check('a new window opens the latest folder', true)).catch(() => check('a new window opens the latest folder', false));
  await secondBrowser.close();

  section('skills the model saves');
  const skilled = project('skilled', { 'README.md': '# Sample once\n' });
  await openProject(skilled);
  await page.evaluate(() => { const set = (id, on) => { const b = document.querySelector(id); if ((b.getAttribute('aria-checked') === 'true') !== on) b.click(); }; set('#tg-apply', false); set('#tg-cmd', false); });
  await page.waitForTimeout(300);
  await page.click('.act[data-view="skills"]');
  await page.waitForSelector('#skills-body .git-note');
  check('the Skills tab opens, and says when it is empty', (await page.locator('#skills-body').innerText()).includes('No skills yet'));
  await send('Please learn a skill');
  await page.waitForSelector('.m-appr.skill.pending');
  const askText = await page.locator('.m-appr.skill').innerText();
  check('it asks before saving, and shows the steps', askText.includes('testing') && askText.includes('Check a README') && askText.includes('Run each command'), askText);
  await page.click('.m-appr.skill button[data-act="accept"]');
  await page.waitForSelector('.m-skill');
  const cardText = await page.locator('.m-skill').innerText();
  check('the chat card says what kind of skill was saved, in its area colour', /New skill saved: Testing skill · Testing/i.test(cardText) && await page.locator('.m-skill[data-area="testing"]').count() === 1 && cardText.includes('Check a README'), cardText);
  check('the save has no plain tool card as well', await page.locator('.m-tool[data-name="save_skill"]').count() === 0);
  await page.waitForSelector('.m-tool[data-name="use_skill"].ok');
  const colours = await page.evaluate(() => { const s = getComputedStyle(document.querySelector('.m-skill')), t = getComputedStyle(document.querySelector('.m-tool')); return { skill: s.borderLeftColor, tool: t.borderLeftColor, accent: getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() }; });
  check('it has its area\'s colour (Testing), not a tool\'s', colours.skill !== colours.tool && colours.skill === 'rgb(159, 211, 86)', JSON.stringify(colours));
  check('using a skill shows in the chat', (await page.locator('.m-tool[data-name="use_skill"]').innerText()).includes('Use skill'));
  await page.waitForSelector('.m-done', { timeout: 15000 });
  await page.waitForSelector('#skills-body .skill-item');
  const itemText = await page.locator('#skills-body .skill-item').innerText();
  check('the Skills tab lists it with its area and kind', /Testing · Testing/i.test(itemText) && itemText.includes('Check a README') && itemText.includes('from skilled'), itemText);
  await page.locator('.skill-item button:has-text("Show steps")').click();
  check('its steps open', await page.locator('.skill-item .skill-steps').isVisible());
  await page.reload(); await page.waitForSelector('#app:not(.hidden)'); await page.waitForSelector('.m-skill');
  check('the card and the open tab survive a reload', await page.locator('#skills-body .skill-item').count() === 1);
  await page.locator('.m-skill button:has-text("Open in Skills")').click();
  if (process.env.STUDIO_SHOTS) for (const [theme, label] of [['dark', 'Dark'], ['light', 'Light'], ['sepia', 'Sepia']]) {
    await page.click('#btn-theme'); await page.click(`.menu-ctx .mi:has-text("${label}")`); await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, `skills-${theme}.png`) });
  }
  if (process.env.STUDIO_SHOTS) { await page.click('#btn-theme'); await page.click('.menu-ctx .mi:has-text("Dark")'); }
  await page.locator('.skill-item button:has-text("Delete")').click();
  await page.locator('.dlg-body').waitFor(); await page.locator('button.primary:has-text("Delete")').last().click();
  await page.waitForFunction(() => document.querySelector('#skills-body').innerText.includes('No skills yet'), null, { timeout: 5000 }).catch(() => {});
  check('deleting empties the tab', (await page.locator('#skills-body').innerText()).includes('No skills yet'));
  await page.click('#skills-new');
  const form = page.locator('.skill-form');
  await form.locator('input').first().fill('Read the README first');
  await form.locator('select').selectOption('workflow');
  await form.locator('input').nth(1).fill('Before any change.');
  await form.locator('button:has-text("Save")').click();
  await page.waitForTimeout(400);
  check('a skill with no steps is refused with a reason', (await form.locator('.skill-err').innerText()) === 'Write the steps first.', await form.locator('.skill-err').innerText());
  await form.locator('textarea').fill('1. Read it.\n2. Follow it.');
  await form.locator('button:has-text("Save")').click();
  await page.waitForSelector('#skills-body .skill-item:not(.skill-form)');
  const written = await page.locator('#skills-body .skill-item').innerText();
  check('the user can write a skill in the Skills tab', /· Workflow/i.test(written) && written.includes('Read the README first') && written.includes('written by you'), written);
  await page.locator('.skill-item button:has-text("Edit")').click();
  await page.locator('.skill-form input').nth(1).fill('Always, before any change.');
  await page.locator('.skill-form button:has-text("Save")').click();
  await page.waitForSelector('#skills-body .skill-item:not(.skill-form)');
  check('and edit it', (await page.locator('#skills-body .skill-item').innerText()).includes('Always, before any change.'));
  await page.locator('.skill-item button:has-text("Delete")').click();
  await page.locator('.dlg-body').waitFor(); await page.locator('button.primary:has-text("Delete")').last().click();
  await page.waitForSelector('#skills-body .git-note');
  await page.click('.act[data-view="explorer"]');

  section('progress: the status line, the title and skipped plan steps');
  await page.evaluate(() => localStorage.setItem('studio.test', '1'));
  await page.reload(); await page.waitForSelector('#app:not(.hidden)');
  await page.waitForTimeout(2000);     // the open chat finishes loading first, or it resets the run state under the checks
  const prog = await page.evaluate(async () => {
    const { handleEvent, setRunning, renderStatus, roomBadge, planCard, planShow, planCount, A } = window.__studio;
    const out = {}, wait = ms => new Promise(r => setTimeout(r, ms));
    setRunning(true); A.t0 = Date.now();
    handleEvent('tool_call', { id: 'pg1', name: 'run', args: { command: 'make video' } }, true);
    await wait(3200); renderStatus();
    out.early = document.querySelector('#agent-status .st-text').textContent;
    out.earlyBar = !document.querySelector('#agent-status .st-load').classList.contains('hidden');
    handleEvent('tool_progress', { id: 'pg1', percent: 42, message: 'Rendering', waiting: false }, true);
    out.text = document.querySelector('#agent-status .st-text').textContent;
    out.width = document.querySelector('#agent-status .st-load > span').style.width;
    out.meta = document.querySelector('#agent-status .st-meta').textContent;
    out.title = document.title; out.tab = document.querySelector('#mt-chat .mt-pct').textContent;
    out.rowBar = !!document.querySelector('.m-tool[data-id="pg1"] .t-progress');
    handleEvent('tool_progress', { id: 'pg1', percent: null, message: 'Waiting for the graphics card', waiting: true }, true);
    out.waiting = document.querySelector('#agent-status').classList.contains('waiting') && document.querySelector('#agent-status .st-load').classList.contains('unknown');
    out.waitTitle = document.title;
    handleEvent('tool_result', { id: 'pg1', name: 'run', ok: true, summary: 'done', output: '' }, true);
    out.after = document.querySelector('#agent-status .st-load').classList.contains('hidden') && !document.querySelector('#agent-status').classList.contains('waiting');
    setRunning(false); A.titleBit = ''; roomBadge(); renderStatus();
    out.cleanTitle = !/^(\d+%|Waiting) · /.test(document.title);
    document.querySelector('.m-tool[data-id="pg1"]')?.remove();
    const p = planCard({ steps: ['a', 'b', 'c', 'd', 'e'] });
    planShow(p, 1, 'running'); planShow(p, 2, 'running'); planShow(p, 5, 'running');
    out.jump = p.states.join(','); out.count = planCount(p);
    const r = planCard({ steps: ['a', 'b', 'c'] }); planShow(r, 3, 'running');
    out.revised = r.states.join(',');
    localStorage.removeItem('studio.test');
    return out;
  });
  check('a running tool shows in the status line, with a moving bar after 3 s while it has no percentage', prog.early.startsWith('Run command') && prog.earlyBar, JSON.stringify(prog));
  check('its progress shows there too: words, bar, percentage and time', prog.text.includes('Rendering') && prog.width === '42%' && prog.meta.startsWith('42%'), JSON.stringify(prog));
  check('and on its own row', prog.rowBar);
  check('the page title and the phone\'s Chat tab carry the percentage', prog.title.startsWith('42% · ') && prog.tab === '42%', JSON.stringify(prog));
  check('waiting shows amber, with a moving bar, and says Waiting in the title', prog.waiting && prog.waitTitle.startsWith('Waiting · '), JSON.stringify(prog));
  check('the bar goes when the tool is done, and the title is clean after the run', prog.after && prog.cleanTitle, JSON.stringify(prog));
  check('steps the run jumped over are marked skipped, not done', prog.jump === 'done,done,skipped,skipped,now' && prog.count.includes('2 skipped'), JSON.stringify(prog));
  check('a revised plan that starts later counts the earlier steps as done', prog.revised === 'done,done,now', JSON.stringify(prog));

  section('the plan card');
  const planned = project('planned', { 'README.md': '# Planned\n' });
  await openProject(planned);
  await page.evaluate(() => { const set = (id, on) => { const b = document.querySelector(id); if ((b.getAttribute('aria-checked') === 'true') !== on) b.click(); }; set('#tg-apply', true); set('#tg-cmd', true); });
  await send('Please make a plan');
  await page.waitForSelector('.m-plan');
  await page.waitForSelector('.m-done', { timeout: 15000 });
  const planText = await page.locator('.m-plan').innerText();
  check('the plan shows as a numbered checklist', planText.includes('Plan') && planText.includes('Look at the files') && planText.includes('Read README.md') && await page.locator('.m-plan li').count() === 3, planText);
  check('the plan call has no plain tool card as well', await page.locator('.m-tool[data-name="plan"]').count() === 0);
  check('a finished run ticks every step', await page.locator('.m-plan li.done').count() === 3 && (await page.locator('.m-plan .plan-count').innerText()) === '3/3 done', await page.locator('.m-plan .plan-count').innerText());
  check('the latest plan is pinned above the messages, closed at first', await page.locator('#plan-dock:not(.hidden) .pd-head').isVisible() && await page.locator('#msgs #plan-dock').count() === 0 && await page.locator('#plan-dock li').count() === 0, await page.locator('#plan-dock').innerText());
  check('closed, it gives the count in one line', (await page.locator('#plan-dock .pd-step').innerText()) === 'Plan · 3/3 done' && await page.locator('#plan-dock .pd-head').getAttribute('aria-expanded') === 'false');
  await page.click('#plan-dock .pd-head');
  check('it opens to the whole checklist', await page.locator('#plan-dock li.done').count() === 3 && await page.locator('#plan-dock .pd-head').getAttribute('aria-expanded') === 'true');
  await page.evaluate(() => { const m = document.querySelector('#msgs'); for (let i = 0; i < 40; i++) m.append(Object.assign(document.createElement('div'), { textContent: 'filler', style: 'height:40px' })); m.scrollTop = m.scrollHeight; });
  const pinned = await page.evaluate(() => { const d = document.querySelector('#plan-dock').getBoundingClientRect(), m = document.querySelector('#msgs').getBoundingClientRect(); return d.height > 0 && d.bottom <= m.top + 1; });
  check('it stays in view above the messages while they scroll', pinned);
  const planColours = await page.evaluate(() => ({ plan: getComputedStyle(document.querySelector('.m-plan')).borderLeftColor, tool: getComputedStyle(document.querySelector('.m-tool')).borderLeftColor }));
  check('the plan has a colour of its own', planColours.plan !== planColours.tool && planColours.plan === 'rgb(79, 193, 201)', JSON.stringify(planColours));
  await send('Please make a plan and stop');
  await page.waitForFunction(() => document.querySelectorAll('.m-plan').length === 2 && document.querySelectorAll('.m-done').length === 2, null, { timeout: 15000 });
  const cutShort = page.locator('.m-plan').last();
  check('a run that stops early leaves the plan open where it stopped', await cutShort.locator('li.done').count() === 1 && await cutShort.locator('li.paused').count() === 1 && await cutShort.locator('li.todo').count() === 1, await cutShort.innerText());
  check('the earlier plan keeps its own state', await page.locator('.m-plan').first().locator('li.done').count() === 3);
  await send('Please make a plan and revise it');
  await page.waitForFunction(() => document.querySelectorAll('.m-done').length === 3, null, { timeout: 15000 });
  check('a new plan starts closed in the pinned bar', await page.locator('#plan-dock li').count() === 0 && (await page.locator('#plan-dock .pd-step').innerText()) === 'Plan · 4/4 done');
  await page.click('#plan-dock .pd-head');
  check('the pinned plan follows the newest plan', await page.locator('#plan-dock li').count() === 4 && await page.locator('#plan-dock li.done').count() === 4);
  check('a revised plan replaces the card, and the old one is marked', await page.locator('.m-plan.replaced').count() === 1 && (await page.locator('.m-plan').last().innerText()).includes('Revised plan') && await page.locator('.m-plan').last().locator('li').count() === 4);
  check('the replaced card stops its spinner', await page.locator('.m-plan.replaced li.now').count() === 0);
  await page.reload(); await page.waitForSelector('#app:not(.hidden)'); await page.waitForSelector('.m-plan');
  await page.waitForTimeout(500);
  check('after a reload the pinned plan is back, closed again', await page.locator('#plan-dock .pd-head').getAttribute('aria-expanded') === 'false' && await page.locator('#plan-dock li').count() === 0);
  await page.click('#plan-dock .pd-head');
  check('plans look the same after a reload', await page.locator('.m-plan').count() === 4 && await page.locator('.m-plan.replaced').count() === 1 && await page.locator('.m-plan').last().locator('li.done').count() === 4, await page.locator('.m-plan').count());
  if (process.env.STUDIO_SHOTS) for (const [theme, label] of [['dark', 'Dark'], ['light', 'Light'], ['sepia', 'Sepia']]) {
    await page.click('#btn-theme'); await page.click(`.menu-ctx .mi:has-text("${label}")`); await page.waitForTimeout(300);
    await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, `plan-${theme}.png`) });
  }
  if (process.env.STUDIO_SHOTS) { await page.click('#btn-theme'); await page.click('.menu-ctx .mi:has-text("Dark")'); }
  await page.click('#plan-dock .pd-head');
  check('it closes again', await page.locator('#plan-dock li').count() === 0);
  await send('hello');
  await page.waitForFunction(() => document.querySelectorAll('.m-user').length === 4, null, { timeout: 15000 });
  check('a new task without a plan clears the pinned plan', await page.locator('#plan-dock').isHidden());
  const users = n => page.waitForFunction(n => document.querySelectorAll('.m-user').length === n && document.querySelector('#agent-stop').classList.contains('hidden'), n, { timeout: 20000 });
  await send('Please make a plan'); await users(5); await page.waitForSelector('#plan-dock:not(.hidden)');
  await send('Carry on with the goal.'); await users(6); await page.waitForTimeout(300);
  check('carrying on keeps the pinned plan (the goal\'s Resume sends this)', await page.locator('#plan-dock:not(.hidden) .pd-head').isVisible());
  await send('Now something else entirely'); await users(7); await page.waitForTimeout(300);
  check('a new request starts without it', await page.locator('#plan-dock').isHidden());
  await send('Γράψε μου σε παρακαλώ τι βρήκες στα αρχεία, chinese aside');
  await page.waitForFunction(() => [...document.querySelectorAll('.m-asst.run-summary')].some(n => n.textContent.includes('Η σημείωση ήρθε')), null, { timeout: 15000 }).catch(() => {});
  const summaryCard = page.locator('.m-asst.run-summary', { hasText: 'Η σημείωση ήρθε' });
  check('a run that worked and ended on a written reply shows it as its summary', await summaryCard.count() === 1
    && (await summaryCard.locator('.rs-head').innerText()).includes('Summary'), await page.locator('#msgs').innerText().catch(() => ''));
  await page.click('#new-chat'); await page.waitForTimeout(300);
  await send('Please tell me how the server is doing now, greek-reply');
  await page.waitForSelector('.m-asst.sent-back', { timeout: 15000 }).catch(() => {});
  const sentBack = page.locator('.m-asst.sent-back').last();
  const folded = await sentBack.locator('.sb-body').isHidden();
  await sentBack.locator('.sb-toggle').click().catch(() => {});
  check('a reply sent back is folded to its note, and Show opens it', folded && await sentBack.locator('.sb-body').isVisible()
    && (await sentBack.locator('.sb-body').innerText()).includes('作为'), (await page.locator('#msgs').innerText()).slice(-700));

  section('guiding and queueing while the agent works');
  const guidedDir = project('guided', { 'README.md': '# Guided\n' });
  await openProject(guidedDir);
  await page.evaluate(() => { const set = (id, on) => { const b = document.querySelector(id); if ((b.getAttribute('aria-checked') === 'true') !== on) b.click(); }; set('#tg-apply', true); set('#tg-cmd', true); });
  await send('Please work slowly');
  await page.waitForSelector('#agent-stop:not(.hidden)');
  check('while it works, the box stays open with Send, Queue and Stop', await page.locator('#agent-send').isVisible() && await page.locator('#agent-queue').isVisible() && await page.locator('#agent-stop').isVisible()
        && !(await page.locator('#agent-input').isDisabled()) && (await page.locator('#agent-input').getAttribute('placeholder')).includes('Alt+Enter'));
  const btnBoxes = await page.evaluate(() => ['#agent-queue', '#agent-stop', '#agent-send'].map(id => document.querySelector(id).getBoundingClientRect()).map(r => [r.left, r.right]));
  check('the three buttons sit side by side without overlapping', btnBoxes[0][1] <= btnBoxes[1][0] && btnBoxes[1][1] <= btnBoxes[2][0], JSON.stringify(btnBoxes));
  await page.fill('#agent-input', 'Use port 9000'); await page.keyboard.press('Enter');
  await page.waitForSelector('.m-user.guided');
  check('Enter tells the agent now: the message shows at once, marked', (await page.locator('.m-user.guided').innerText()).includes('Use port 9000') && (await page.locator('.m-user.guided .guide-tag').innerText()).toLowerCase() === 'steer'
        && (await page.inputValue('#agent-input')) === '');
  await page.fill('#agent-input', 'Then write the docs'); await page.keyboard.press('Alt+Enter');
  await page.fill('#agent-input', 'Drop me'); await page.click('#agent-queue');
  check('Alt+Enter and the Queue button keep messages for after', await page.locator('#queue-row:not(.hidden) .queue-item').count() === 2
        && (await page.locator('.queue-item .q-n').first().innerText()).toLowerCase() === 'next');
  check('queued messages survive a reload of the list', await page.evaluate(() => JSON.parse(sessionStorage.getItem('studio.queue')).map(q => q.text).join('|')) === 'Then write the docs|Drop me');
  await page.locator('.queue-item').nth(1).locator('.q-btn[aria-label="Remove"]').click();
  check('a queued message can be removed', await page.locator('.queue-item').count() === 1);
  await page.waitForFunction(() => [...document.querySelectorAll('.m-done')].length >= 1, null, { timeout: 30000 });
  const firstSummary = await page.locator('.m-done').first().innerText();
  check('the model answered with the guidance in the same run', firstSummary.includes('Guidance: Use port 9000'), firstSummary);
  await page.waitForFunction(() => document.querySelectorAll('.m-user:not(.guided)').length === 2, null, { timeout: 10000 });
  check('when the run ends the next queued message is sent by itself', (await page.locator('.m-user:not(.guided)').last().innerText()).includes('Then write the docs') && await page.locator('#queue-row').isHidden());
  await ended();
  await page.waitForFunction(() => document.querySelectorAll('.m-done').length === 2, null, { timeout: 15000 });
  await send('Please work slowly');
  await page.waitForSelector('#agent-stop:not(.hidden)');
  await page.fill('#agent-input', 'After the stop'); await page.keyboard.press('Alt+Enter');
  await page.click('#agent-stop'); await ended();
  await page.waitForTimeout(800);
  check('after Stop, queued messages wait for the user', await page.locator('.queue-item').count() === 1 && (await page.locator('.m-user:not(.guided)').last().innerText()).includes('Please work slowly'));
  await page.locator('.queue-item .q-btn[aria-label="Edit"]').click();
  check('Edit puts a queued message back in the box', (await page.inputValue('#agent-input')) === 'After the stop' && await page.locator('#queue-row').isHidden());
  await page.fill('#agent-input', '');
  check('with no run, the Queue button is hidden again', await page.locator('#agent-queue').isHidden());

  section('questions from the agent');
  await openProject(project('questions', { 'README.md': '# Q\n' }));
  await send('Please ask me');
  await page.waitForSelector('.m-question.pending');
  check('the questions show as a card with their choices', await page.locator('.m-question .q-item').count() === 2 && await page.locator('.m-question .q-opt').count() === 6
    && (await page.locator('.m-question').innerText()).includes('Stands out most'), await page.locator('.m-question').innerText());
  check('the status line says it waits for an answer', (await page.locator('#agent-status .st-text').innerText()) === 'Waiting for your answer…');
  check('Send stays off until every question has an answer', await page.locator('.m-question [data-act="accept"]').isDisabled());
  check('several questions show one at a time, with their place and a dot each', (await page.locator('.m-question .q-where').innerText()) === 'Question 1 of 2'
    && await page.locator('.m-question .q-dot').count() === 2 && await page.locator('.m-question .q-item').nth(1).evaluate(n => n.inert));
  check('the question call has no raw row of its own above the card', await page.locator('.m-tool[data-name="ask_user"]').count() === 0);
  await page.locator('.m-question .q-opt', { hasText: 'Green' }).click();
  await page.waitForFunction(() => document.querySelector('.m-question .q-where')?.textContent === 'Question 2 of 2', null, { timeout: 3000 });
  check('picking a single choice moves on to the next question', true);
  await page.click('.m-question .q-nav button:has-text("Back")');
  await page.waitForFunction(() => document.querySelector('.m-question .q-where')?.textContent === 'Question 1 of 2');
  await page.locator('.m-question .q-own').first().fill('Teal instead');
  check('typing your own answer clears the one choice', !(await page.locator('.m-question input[value="Green"]').isChecked()));
  await page.click('.m-question .q-nav button:has-text("Next")');
  await page.waitForFunction(() => !document.querySelector('.m-question .q-item:nth-child(2)').inert);
  await page.locator('.m-question .q-opt', { hasText: 'Home' }).click();
  await page.locator('.m-question .q-opt', { hasText: 'Help' }).click();
  check('a question that allows several waits for Next, and both dots show as answered', (await page.locator('.m-question .q-where').innerText()) === 'Question 2 of 2' && await page.locator('.m-question .q-dot.done').count() === 2);
  check('a question that allows several takes several', await page.locator('.m-question .q-item').nth(1).locator('input:checked').count() === 2);
  await page.click('.m-question [data-act="accept"]');
  await page.waitForSelector('.m-done', { timeout: 15000 });
  const answered = await page.locator('.m-question').innerText();
  check('the card keeps the questions with the answers', answered.includes('“Teal instead”') && answered.includes('Home, Help') && answered.includes('Answered'), answered);
  check('the model got them', (await page.locator('.m-done .sum').innerText()).includes('in their own words: Teal instead'), await page.locator('.m-done .sum').innerText());
  await page.reload(); await page.waitForSelector('#app:not(.hidden)'); await page.waitForSelector('.m-question');
  check('answers look the same after a reload', (await page.locator('.m-question').innerText()).includes('Home, Help'));
  await send('Please ask me one');
  await page.waitForSelector('.m-question.pending');
  await page.click('.m-question.pending [data-act="reject"]');
  await page.waitForFunction(() => document.querySelectorAll('.m-done').length === 2, null, { timeout: 15000 });
  check('Skip lets the agent decide', (await page.locator('.m-question').last().innerText()).includes('Skipped'));

  section('rewind, branch, ask again and quote');
  const rewound = project('rewound', { 'README.md': '# Sample project\n' });
  await openProject(rewound);
  await page.evaluate(() => { const set = (id, on) => { const b = document.querySelector(id); if ((b.getAttribute('aria-checked') === 'true') !== on) b.click(); }; set('#tg-apply', true); set('#tg-cmd', true); });
  await send('hello');
  await page.waitForFunction(() => document.querySelectorAll('.m-done').length === 1, null, { timeout: 15000 });
  await send('Please rename the word');
  await page.waitForFunction(() => document.querySelectorAll('.m-done').length === 2, null, { timeout: 20000 });
  check('the file was changed', fs.readFileSync(path.join(rewound, 'README.md'), 'utf8').includes('Demo'));
  await page.locator('.m-user').nth(1).hover();
  check('a message shows its actions on hover', await page.locator('.m-user').nth(1).locator('.u-acts').isVisible());
  check('the actions sit under the message, not over its text', await page.locator('.m-user').nth(1).evaluate(m => {
    const r = m.getBoundingClientRect(), a = m.querySelector('.u-acts').getBoundingClientRect(), next = m.nextElementSibling?.getBoundingClientRect();
    return a.top >= r.bottom && (!next || a.bottom <= next.top + 1);
  }));
  await page.locator('.m-user').nth(1).locator('.u-act.copy').click();
  check('Copy copies the message and shows a tick', await page.waitForFunction(() => document.querySelectorAll('.m-user')[1].querySelector('.u-act.copy').innerHTML.includes('M20 6 9 17'), null, { timeout: 3000 }).then(() => true, () => false));
  check('only the latest message offers Ask again', await page.locator('.m-user').nth(0).locator('.u-act.again').isHidden() && await page.locator('.m-user').nth(1).locator('.u-act.again').isVisible());
  await page.locator('.m-user').nth(1).locator('[aria-label="Rewind to here"]').click();
  await page.waitForSelector('.dialog');
  check('rewind offers to undo the file change after it', (await page.locator('.dialog').innerText()).includes("Also undo the agent's 1 file change after it"), await page.locator('.dialog').innerText());
  await page.locator('.dialog .dlg-check input').check();
  await page.locator('.dialog .btn.primary').click();
  await page.waitForFunction(() => document.querySelectorAll('.m-user').length === 1, null, { timeout: 10000 });
  check('rewind removes the message and puts it back in the box', (await page.inputValue('#agent-input')) === 'Please rename the word' && await page.locator('.m-tool').count() === 0);
  check('and undoes the change', fs.readFileSync(path.join(rewound, 'README.md'), 'utf8') === '# Sample project\n');
  await page.click('#agent-send');
  await page.waitForFunction(() => document.querySelectorAll('.m-done').length === 1 && document.querySelector('#msgs').getAttribute('aria-busy') === 'false', null, { timeout: 20000 });
  await page.locator('.m-asst').first().evaluate(n => { const r = document.createRange(); r.selectNodeContents(n.querySelector('p') || n); const s = getSelection(); s.removeAllRanges(); s.addRange(r); });
  await page.waitForSelector('.quote-btn:not(.hidden)');
  await page.click('.quote-btn');
  check('Quote puts the selected text in the box as a quote', (await page.inputValue('#agent-input')).startsWith('> Hello! I am the mock model.'), await page.inputValue('#agent-input'));
  await page.fill('#agent-input', '');
  const usersBefore = await page.locator('.m-user').count();
  await page.locator('.m-user').last().hover();
  await page.locator('.m-user').last().locator('[aria-label="Ask again"]').click();
  await page.waitForSelector('.dialog'); await page.locator('.dialog .btn.primary').click();
  await page.waitForFunction(() => document.querySelector('#msgs').getAttribute('aria-busy') === 'true', null, { timeout: 10000 });
  await page.waitForFunction(n => document.querySelectorAll('.m-user').length === n && document.querySelectorAll('.m-done').length === 1 && document.querySelector('#msgs').getAttribute('aria-busy') === 'false', usersBefore, { timeout: 20000 });
  check('Ask again sends the latest message again in place of the old answer', await page.locator('.m-user').count() === usersBefore && (await page.locator('.m-user').last().innerText()).startsWith('Please rename the word'));
  await page.locator('.m-user').last().hover();
  await page.locator('.m-user').last().locator('[aria-label="Branch from here"]').click();
  await page.waitForFunction(() => document.querySelectorAll('.m-user').length === 1, null, { timeout: 10000 });
  check('Branch opens a new chat with what came before, and the message in the box', (await page.locator('#sess-title').innerText()).startsWith('Branch: ')
    && (await page.inputValue('#agent-input')) === 'Please rename the word' && (await page.locator('.m-user').innerText()).startsWith('hello'), await page.locator('#sess-title').innerText());
  await page.fill('#agent-input', '');

  section('plan first');
  const planFirst = project('planfirst', { 'README.md': '# Sample\n' });
  await openProject(planFirst);
  await send('/plan');
  check('/plan alone switches plan first on', await page.locator('#plan-chip').getAttribute('aria-pressed') === 'true' && (await page.getAttribute('#agent-input', 'placeholder')).includes('proposes a plan'));
  await send('Please plan the rename');
  await page.waitForSelector('.m-appr.pending');
  check('a command that could change things asks even with auto-run on', (await page.locator('.m-appr.pending').innerText()).includes('touch made.txt'));
  await page.click('.m-appr.pending [data-act="reject"]');
  await page.waitForSelector('.m-done .plan-acts', { timeout: 15000 });
  check('the message is marked as plan first', (await page.locator('.m-user').first().innerText()).includes('PLAN FIRST') || (await page.locator('.m-user .plan-tag').count()) === 1);
  check('the edit was refused and nothing changed', fs.readFileSync(path.join(planFirst, 'README.md'), 'utf8') === '# Sample\n' && !fs.existsSync(path.join(planFirst, 'made.txt')));
  check('the end of the run offers to carry out, edit or change the plan', (await page.locator('.m-done .plan-acts').innerText()).replace(/\s+/g, ' ').includes('Carry out the plan Edit the plan Change it'));
  await page.click('.plan-acts button:has-text("Edit the plan")');
  await page.waitForSelector('.dialog .plan-edit');
  check('Edit shows the steps, numbered', (await page.inputValue('.dialog .plan-edit')) === '1. Rename Sample to Demo in README.md\n2. Run the tests');
  await page.fill('.dialog .plan-edit', '1. Rename Sample to Demo in README.md\n2. Check the result');
  await page.click('.dialog .btn.primary');
  await page.waitForFunction(() => document.querySelectorAll('.m-done').length === 2, null, { timeout: 20000 });
  check('approving turns plan first off and sends the edited plan', await page.locator('#plan-chip').getAttribute('aria-pressed') === 'false'
    && (await page.locator('.m-user').last().innerText()).includes('Carry out this plan, as I changed it:\n1. Rename Sample to Demo in README.md\n2. Check the result'));
  check('then the agent makes the changes', fs.readFileSync(path.join(planFirst, 'README.md'), 'utf8').includes('Demo'));
  check('only the latest run keeps its plan buttons', await page.locator('.plan-acts').count() === 0);

  section('/compact');
  await openProject(project('compacted', { 'README.md': '# Sample\n' }));
  await send('hello');
  await page.waitForFunction(() => document.querySelectorAll('.m-done').length === 1, null, { timeout: 15000 });
  await send('Please rename the word');
  await page.waitForFunction(() => document.querySelectorAll('.m-done').length === 2, null, { timeout: 20000 });
  await send('/compact');
  await page.waitForSelector('.m-compacted', { timeout: 15000 });
  await page.waitForFunction(() => document.querySelector('#msgs').getAttribute('aria-busy') === 'false', null, { timeout: 15000 });
  const compactCard = await page.locator('.m-compacted').innerText();
  check('/compact shows that the earlier messages were summarized, and keeps the chat', compactCard.includes('Summarized the earlier messages') && await page.locator('.m-user').count() === 2, compactCard);
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
  await page.locator('.m-compacted .cp-copy').click();
  check('the compacted summary has a copy button that shows it worked', await page.waitForSelector('.m-compacted .cp-copy.done', { timeout: 3000 }).then(() => true).catch(() => false));
  const copiedSummary = await page.evaluate(() => navigator.clipboard.readText().catch(e => 'ERR ' + e.message));
  check('the copy button copies the complete compacted summary', copiedSummary.includes('rename Sample to Demo'), copiedSummary);
  await page.click('.m-compacted summary');
  check('the summary can be read', (await page.locator('.m-compacted .cp-sum').innerText()).includes('rename Sample to Demo'));
  check('the run ends as Summarized', (await page.locator('.m-done').last().innerText()).startsWith('Summarized'));
  await page.locator('.m-user').first().hover();
  await page.locator('.m-user').first().locator('[aria-label="Rewind to here"]').click();
  await page.waitForSelector('.dialog'); await page.locator('.dialog .btn.primary').click();
  await page.waitForSelector('.toast', { timeout: 5000 });
  check('a message before the summary cannot be rewound to', (await page.locator('.toast').last().innerText()).includes('summarized after this message') && await page.locator('.m-user').count() === 2);

  section('folded activity and the files a run changed');
  const swept = project('swept', { 'README.md': '# Sample\n' });
  await openProject(swept);
  await send('Please look quietly');
  await page.waitForSelector('.m-files', { timeout: 20000 });
  check('a listing, a read and a search in a row fold into one line, with their thinking', await page.locator('.m-activity').count() === 1
    && (await page.locator('.m-activity > summary').innerText()) === 'Listed 1 folder, read 1 file, searched once' && await page.locator('.m-activity .m-think').count() >= 2, await page.locator('.m-activity > summary').count());
  check('the folded line starts closed, and the edit stays in view', !(await page.locator('.m-activity').getAttribute('open')) && await page.locator('.m-tool.has-change').isVisible());
  await page.click('.m-activity > summary');
  check('it opens to the tool cards', await page.locator('.m-activity .m-tool').count() === 3 && await page.locator('.m-activity .m-tool').first().isVisible());
  check('the run lists the file it changed, with its counts', (await page.locator('.m-files').innerText()).replace(/\s+/g, ' ').includes('1 file changed in this run CHANGED README.md +1 −1'), await page.locator('.m-files').innerText());
  await page.click('.m-files [aria-label="Mention README.md"]');
  check('@ mentions the file in the next message', (await page.inputValue('#agent-input')) === '@README.md ');
  await page.fill('#agent-input', '');
  await page.click('.m-files .f-path');
  await page.waitForFunction(() => [...document.querySelectorAll('.tab')].some(t => t.textContent.includes('README.md')), null, { timeout: 5000 }).then(() => check('a file in the list opens in the editor', true)).catch(() => check('a file in the list opens in the editor', false));
  await page.reload(); await page.waitForSelector('#app:not(.hidden)'); await page.waitForSelector('.m-files');
  check('both look the same after a reload', await page.locator('.m-activity').count() === 1 && await page.locator('.m-files .f-row').count() === 1);

  section('/goal');
  await openProject(project('goals', { 'README.md': '# Goals\n' }));
  await send('/goal Please reach the goal of two parts');
  await page.waitForSelector('#goal-bar:not(.hidden)');
  check('the goal shows in a bar above the box while the agent works', (await page.locator('#goal-bar .goal-text').innerText()) === 'Please reach the goal of two parts'
    && (await page.locator('.m-user').last().innerText()).includes('GOAL'), await page.locator('#goal-bar').innerText());
  await page.waitForFunction(() => document.querySelector('#goal-bar.done'), null, { timeout: 20000 });
  check('a reached goal has no stray "null" in its bar', !(await page.locator('#goal-bar').innerText()).includes('null'), await page.locator('#goal-bar').innerText());
  check('when the model reaches it, the bar says so', (await page.locator('#goal-bar .goal-state').innerText()).startsWith('Reached') && (await page.locator('#msgs').innerText()).includes('The goal is not reached yet: round 1.'));
  await page.reload(); await page.waitForSelector('#app:not(.hidden)'); await page.waitForSelector('#goal-bar:not(.hidden)');
  check('the goal stays with the chat after a reload', (await page.locator('#goal-bar .goal-text').innerText()) === 'Please reach the goal of two parts');
  await page.click('#goal-bar button:has-text("Clear")');
  await page.waitForSelector('#goal-bar.hidden', { state: 'attached' });
  check('Clear removes it', await page.locator('#goal-bar').isHidden());
  check('a Goal button sits beside Plan first', await page.locator('.ctx-row #plan-chip + #goal-chip').count() === 1 && (await page.locator('#goal-chip').innerText()).trim() === 'Goal');
  await page.click('#goal-chip');
  await page.waitForSelector('.dialog .goal-form');
  check('it opens a window for the goal, how to tell it is done, what to keep in mind and the rounds', await page.locator('.goal-form textarea').count() === 3
    && await page.inputValue('.goal-form .goal-rounds') === '20' && await page.locator('.dialog .btn.primary:has-text("Start working on it")').count() === 1);
  await page.locator('.goal-form textarea').nth(0).fill('x'.repeat(9000));
  check('the goal box takes a whole brief and is tall enough to read it', (await page.inputValue('.goal-form textarea.goal-brief')).length === 9000
    && await page.locator('.goal-form textarea.goal-brief').evaluate(t => t.getBoundingClientRect().height > 100)
    && (await page.locator('.goal-count').innerText()).includes('/ 16000')
    && await page.locator('.goal-form textarea.goal-brief').evaluate(t => getComputedStyle(t).whiteSpace !== 'nowrap' && t.scrollWidth <= t.clientWidth + 1 && t.scrollHeight > t.clientHeight));
  await page.locator('.goal-form textarea').nth(0).fill('Please reach the goal of two parts');
  await page.locator('.goal-form textarea').nth(1).fill('- both parts exist');
  await page.fill('.goal-form .goal-rounds', '5');
  await page.click('.dialog .btn.primary');
  await page.waitForSelector('#goal-bar:not(.hidden)');
  check('starting sends it as the goal, with what done means', (await page.locator('.m-user').last().innerText()).includes('Done when:') && (await page.locator('#goal-chip').innerText()).includes('Goal:'));
  await page.waitForFunction(() => document.querySelector('#goal-bar.done'), null, { timeout: 20000 });
  check('the button follows the goal\'s state', (await page.locator('#goal-chip').innerText()).trim() === 'Goal: reached' && await page.locator('#goal-chip.on[data-state="done"]').count() === 1);
  await page.click('#goal-chip');
  await page.waitForSelector('.dialog .goal-form');
  check('opened again, it shows the goal as it was set, to change it', await page.inputValue('.goal-form textarea >> nth=0') === 'Please reach the goal of two parts'
    && await page.inputValue('.goal-form textarea >> nth=1') === '- both parts exist' && await page.inputValue('.goal-form .goal-rounds') === '5'
    && (await page.locator('.goal-now').innerText()).includes('Reached') && await page.locator('.dialog .btn:has-text("Clear goal")').count() === 1);
  await page.click('.dialog .btn:has-text("Clear goal")');
  await page.waitForSelector('#goal-bar.hidden', { state: 'attached' });
  check('and Clear goal removes it', (await page.locator('#goal-chip').innerText()).trim() === 'Goal');
  await send('/goal Please goal blocked');
  await page.waitForFunction(() => document.querySelector('#goal-bar.blocked'), null, { timeout: 20000 });
  check('a blocked goal says what the agent needs, and offers Resume', (await page.locator('#goal-bar').innerText()).includes('Needs you · I need the API key') && await page.locator('#goal-bar button:has-text("Resume")').isVisible());

  section('tasks and loops');
  const tasked = project('tasked', { 'README.md': '# Sample\n' });
  await openProject(tasked);
  await page.click('.act[data-view="tasks"]');
  await page.waitForSelector('#view-tasks:not(.hidden) #tasks-body .tl-hint');
  check('the Tasks view explains itself when empty', (await page.locator('#tasks-body').innerText()).includes('No tasks yet'));
  await page.click('#task-new');
  await page.waitForSelector('.dialog .task-form');
  await page.locator('.task-form input.dlg-input').first().fill('Morning rename');
  await page.locator('.task-form textarea').fill('Please rename the word');
  await page.locator('.task-form select').first().selectOption('weekly');
  check('a weekly task asks for the day, and every few minutes asks for minutes instead of a time', await page.locator('.task-form select').nth(1).isVisible());
  await page.locator('.task-form .q-opt', { hasText: 'Stop at what needs a yes' }).click();
  await page.click('.dialog .btn.primary');
  await page.waitForSelector('.task-card');
  check('no stray "null" text in the Tasks view', !(await page.locator('#tasks-body').innerText()).includes('null'), await page.locator('#tasks-body').innerText());
  check('the task shows under Scheduled with its timetable', (await page.locator('#tasks-body').innerText()).includes('SCHEDULED (1)') && (await page.locator('.task-card').innerText()).includes('Mondays at 08:00 · Stop at what needs a yes'), await page.locator('#tasks-body').innerText());
  await page.click('.task-card');
  await page.click('.dialog button:has-text("Run now")');
  await page.waitForSelector('.task-card.attention', { timeout: 20000 });
  check('a run that needed a yes moves the task to Needs attention, with a badge and a notice', (await page.locator('.task-card.attention .task-status').innerText()).startsWith('Needed a yes 2 times')
    && (await page.locator('#tasks-badge').innerText()) === '1' && (await page.locator('.toast').last().innerText()).includes('needs your attention'));
  await page.click('.task-card');
  const detail = await page.locator('.dialog .task-detail').innerText();
  check('its details list what it could not do, and the answer', detail.includes('It needed a yes to:') && detail.includes('change README.md') && detail.includes('Renamed Sample to Demo'), detail);
  await page.click('.dialog button:has-text("Open the chat")');
  await page.waitForFunction(() => (document.querySelector('.m-user')?.textContent || '').includes('Please rename the word'), null, { timeout: 10000 });
  check('Open the chat shows the run, marked as a task', (await page.locator('.m-user').first().innerText()).includes('TASK') && fs.readFileSync(path.join(tasked, 'README.md'), 'utf8') === '# Sample\n');
  await page.click('#new-chat');
  await send('/loop 1m hello from the loop, stop after 1 answer');
  check('/loop says what it will do', (await page.locator('.m-note').last().innerText()).startsWith('Loop: "hello from the loop" every 1 minute, 1 time'), await page.locator('.m-note').last().innerText());
  await page.waitForFunction(() => [...document.querySelectorAll('.m-user')].some(n => n.textContent.includes('hello from the loop')), null, { timeout: 20000 });
  check('the loop sends its message in this chat by itself', true);
  await page.waitForFunction(() => document.querySelectorAll('.m-done').length >= 1 && document.querySelector('#msgs').getAttribute('aria-busy') === 'false', null, { timeout: 20000 });
  await page.click('#new-chat');

  section('Time-skip');
  const skipped = project('skipped', { 'README.md': '# Sample\n', 'Makefile': 'test:\n\tgrep -q Demo README.md\n' });
  await openProject(skipped);
  await send('/timeskip Please rename the word');
  await page.waitForSelector('.m-timeskip .ts-acts', { timeout: 30000 });
  const tsText = (await page.locator('.m-timeskip').innerText()).replace(/\s+/g, ' ');
  check('Time-skip shows what the copy changed and the tests on both versions', tsText.includes('1 file differ from your folder') && tsText.includes('Your folder: failed') && tsText.includes('The update: passed'), tsText);
  check('your folder is untouched until you decide, and the copy\'s edits have no Undo of their own', fs.readFileSync(path.join(skipped, 'README.md'), 'utf8') === '# Sample\n'
    && (await page.locator('.m-tool.has-change .copy-note').count()) === 1, await page.locator('.m-tool.has-change').count());
  await page.click('.m-timeskip .ts-file summary');
  check('a file opens to its diff', await page.waitForSelector('.m-timeskip .diff-view', { timeout: 3000 }).then(() => true, () => false));
  await page.click('.m-timeskip button:has-text("Keep the update")');
  await page.waitForSelector('.m-timeskip .ts-done.ok');
  check('Keep puts the update in your folder', fs.readFileSync(path.join(skipped, 'README.md'), 'utf8').includes('Demo') && (await page.locator('.ts-done').innerText()).startsWith('Kept: 1 file updated'));
  await page.click('.ts-done button:has-text("Undo")');
  await page.waitForFunction(() => document.querySelector('.ts-done button')?.textContent === 'Undone', null, { timeout: 5000 });
  check('and Undo puts it back', fs.readFileSync(path.join(skipped, 'README.md'), 'utf8') === '# Sample\n');
  await page.reload(); await page.waitForSelector('#app:not(.hidden)'); await page.waitForSelector('.m-timeskip');
  check('after a reload the decision is shown, with no buttons', await page.locator('.m-timeskip .ts-acts').count() === 0 && await page.locator('.m-timeskip .ts-done').count() === 1);

  section('math, CSV tables, the palette and chat settings');
  await openProject(project('extras', { 'README.md': '# Extras\n', 'scores.csv': 'name,score,city\nNikos,12,Athens\n"Smith, Anna",7,Patras\nEleni,30,"Thessaloniki"\n' }));
  await send('Please show math');
  await page.waitForSelector('.m-asst .math.drawn .katex', { timeout: 20000 });
  check('inline and display math are drawn', await page.locator('.m-asst .math-block.drawn .katex-display').count() === 1 && await page.locator('.m-asst .math.drawn').count() === 1);
  check('prices stay as text', (await page.locator('.m-asst').last().innerText()).includes('costs $5 and $10'));
  if (await page.locator('.act.active[data-view="explorer"]').count() === 0) await page.click('.act[data-view="explorer"]');
  await page.locator('.t-row', { hasText: 'scores.csv' }).dblclick();
  await page.waitForSelector('.csv-table table');
  check('a CSV file opens as a table, quoted commas kept', await page.locator('.csv-table tbody tr').count() === 3 && (await page.locator('.csv-table tbody tr').nth(1).innerText()).includes('Smith, Anna'));
  await page.click('.csv-table th:nth-child(2) .th-sort');
  check('a number column sorts by number', (await page.locator('.csv-table tbody tr').first().innerText()).startsWith('Smith, Anna'));
  await page.fill('.table-search', 'patras');
  check('search filters the rows', await page.locator('.csv-table tbody tr').count() === 1 && (await page.locator('.table-count').innerText()).startsWith('1 of 3 rows'));
  await page.keyboard.press('Control+k');
  await page.waitForSelector('.palette input');
  await page.keyboard.type('plan first');
  check('Ctrl+K finds actions', (await page.locator('.palette .qo-item').first().innerText()).includes('Plan first: turn on'));
  await page.keyboard.press('Enter');
  check('and runs them', await page.waitForFunction(() => document.querySelector('#plan-chip').getAttribute('aria-pressed') === 'true', null, { timeout: 3000 }).then(() => true, () => false));
  await page.click('#plan-chip');
  await page.keyboard.press('Control+k'); await page.keyboard.type('#show math');
  check('# finds chats', (await page.locator('.palette .qo-item').first().innerText()).includes('Please show math'));
  await page.keyboard.press('Escape');
  await page.keyboard.press('Control+/');
  await page.waitForSelector('.dialog kbd');
  check('Ctrl+/ lists the shortcuts', (await page.locator('.dialog').innerText()).includes('Command palette'));
  await page.click('.dialog .btn.primary');
  check('the microphone is hidden until dictation is turned on', await page.locator('#mic-btn').isHidden());
  await page.click('#chat-settings');
  await page.waitForSelector('.dialog .task-form');
  await page.locator('.dialog select').first().selectOption('high');
  await page.locator('.dialog input[type="checkbox"]').check();
  await page.click('.dialog .btn.primary');
  await page.waitForFunction(() => document.querySelector('.toast')?.textContent.includes('Saved for this chat'), null, { timeout: 5000 }).then(() => {}, () => {});
  check('chat settings save the effort for the chat, and dictation shows the microphone', await page.locator('#mic-btn').isVisible());
  await page.click('.act[data-view="skills"]');
  await page.waitForSelector('.utools');
  await page.click('.utools button:has-text("New tool")');
  await page.waitForSelector('.dialog .task-form');
  await page.locator('.dialog .task-form input.dlg-input').first().fill('house_rules');
  await page.locator('.dialog .task-form textarea').fill('Always write tests first.');
  await page.click('.dialog .btn.primary');
  await page.waitForSelector('.dialog .task-form .skill-err');
  check('a tool without "when to use it" is not lost: the form stays, with the reason', (await page.locator('.dialog .skill-err').innerText()).includes('when the agent should use it')
    && (await page.locator('.dialog .task-form input.dlg-input').first().inputValue()) === 'house_rules' && (await page.locator('.dialog .task-form textarea').inputValue()) === 'Always write tests first.');
  await page.locator('.dialog .task-form input.dlg-input').nth(1).fill('The house rules for this team');
  await page.click('.dialog .btn.primary');
  await page.waitForSelector('.utool');
  check('your own tool shows in the Skills view', (await page.locator('.utool').innerText()).includes('user__house_rules'));
  await page.click('#skills-new');
  await page.locator('.skill-form input').first().fill('Draw a chart from a CSV');
  await page.locator('.skill-form select').selectOption('tooling');
  await page.locator('.skill-form input').nth(1).fill('When numbers should become a chart');
  await page.locator('.skill-form textarea').fill('1. Read the CSV.\n2. Plot it.');
  await page.locator('.skill-form button:has-text("Save")').click();
  await page.waitForSelector('.skill-bar .skill-filter');
  check('the Skills view has search, a chip per kind with counts, and sort', /^All \d+/.test((await page.locator('.skill-filter').first().innerText()).replace(/\s+/g, ' '))
    && await page.locator('.skill-filter', { hasText: 'General' }).count() === 1 && await page.locator('.skill-sort option').count() === 4);
  await page.locator('.skill-filter', { hasText: 'General' }).click();
  const generalOnly = await page.locator('.skill-list .skill-item').evaluateAll(ns => ns.map(n => n.dataset.area));
  check('an area chip shows only that area, and the cards wear its colour', generalOnly.length >= 1 && generalOnly.every(a => a === 'general')
    && (await page.locator('.skill-list .skill-name').allInnerTexts()).includes('Draw a chart from a CSV')
    && await page.evaluate(() => { const chip = document.querySelector('.skill-filter.on'), card = document.querySelector('.skill-list .skill-item'); return getComputedStyle(chip).borderTopColor === getComputedStyle(card).borderTopColor; }));
  await page.locator('.skill-filter', { hasText: 'All' }).click();
  await page.fill('.skill-search', 'plot it');
  check('search looks in the steps too, and keeps the box', await page.locator('.skill-list .skill-item').count() === 1 && await page.locator('.skill-search').evaluate(n => n === document.activeElement));
  await page.fill('.skill-search', '');
  await page.click('.skills-top .skills-head');
  check('the skills fold away', await page.locator('.skill-list').isHidden());
  await page.click('.skills-top .skills-head');
  await page.click('.act[data-view="explorer"]');

  section('the guide');
  await send('/help');
  check('/help points to the guide', (await page.locator('.m-note').last().innerText()).includes('The full guide: /guide'));
  await send('/guide loop');
  await page.waitForSelector('.guide-modal .gd-sec', { state: 'attached' });
  const allSections = await page.locator('.guide-modal .gd-sec').count();
  check('/guide opens the guide inside Studio, with an icon per section', allSections >= 10 && await page.locator('.guide-modal .gd-link svg').count() === allSections && await page.locator('.guide-modal h2 .gd-ico svg').count() === allSections);
  check('searched for its words, it shows only what matches, marked', await page.locator('.guide-modal .gd-sec:not(.gd-hide)').count() < allSections
    && await page.locator('.guide-modal mark.gd-mark').count() > 0 && (await page.locator('.guide-modal .gd-count').innerText()).includes('match'), await page.locator('.guide-modal .gd-count').innerText());
  await page.fill('.guide-modal .gd-search', 'zzqqxx');
  check('a search with no match says so', (await page.locator('.guide-modal .gd-count').innerText()) === 'Nothing matches.' && await page.locator('.guide-modal .gd-sec:not(.gd-hide)').count() === 0);
  await page.fill('.guide-modal .gd-search', '');
  check('clearing the search brings everything back, unmarked', await page.locator('.guide-modal .gd-sec:not(.gd-hide)').count() === allSections && await page.locator('.guide-modal mark.gd-mark').count() === 0);
  await page.click('.guide-modal .gd-head .btn:has-text("Ελληνικά")');
  check('it switches to Greek', (await page.locator('.guide-modal .gd-sec h2').first().innerText()).includes('Τα πρώτα βήματα'));
  await page.click('.guide-modal .gd-head .btn:has-text("English")');
  await page.keyboard.press('Escape');
  check('Esc closes it', await page.locator('.guide-modal').count() === 0);
  await page.click('#btn-guide');
  await page.waitForSelector('.guide-modal');
  check('the ? button in the top bar opens it too', true);
  await page.keyboard.press('Escape');
  const guide = await page.context().newPage();
  await guide.goto(new URL('/web/guide.html?lang=en', url).href);
  check('the guide opens in English with its contents', (await guide.locator('#toc a').count()) >= 10 && (await guide.locator('section[data-lang="en"]').isVisible()) && !(await guide.locator('section[data-lang="el"]').isVisible()));
  await guide.click('#b-el');
  check('and switches to Greek', (await guide.title()) === 'Οδηγός του Eidoloom Studio' && (await guide.locator('section[data-lang="el"] h2').first().innerText()) === 'Τα πρώτα βήματα');
  const helpText = await page.locator('.m-note').last().innerText();
  const named = await guide.evaluate(() => [...new Set([...document.querySelectorAll('section code')].map(c => (/^\/[a-z]+/.exec(c.textContent) || [''])[0]).filter(Boolean))]);
  const unknown = named.filter(c => !helpText.includes(c + ':') && !helpText.includes(c + ' <'));
  check('every command the guide names exists', unknown.length === 0, unknown.join(' '));
  await guide.setViewportSize({ width: 375, height: 800 });
  check('on a phone it does not scroll sideways', await guide.evaluate(() => document.scrollingElement.scrollWidth <= innerWidth + 1));
  await guide.close();

  section('tables in replies');
  await send('Please show a table');
  await page.waitForSelector('.m-asst:not(.streaming) .md-table table');
  const table = await page.evaluate(() => { const t = [...document.querySelectorAll('.m-asst .md-table table')].pop();
    return { th: [...t.querySelectorAll('th')].map(x => x.textContent), rows: t.querySelectorAll('tbody tr').length, right: t.querySelector('td:nth-child(3)').style.textAlign,
             code: !!t.querySelector('td code'), raw: t.closest('.m-asst').textContent.includes('|---')}; });
  check('a Markdown table in a reply shows as a table', table.th.join('/') === 'Object/Position (x, y, z)/Rotation Z' && table.rows === 2 && !table.raw, JSON.stringify(table));
  check('with its column alignment and inline code', table.right === 'right' && table.code, JSON.stringify(table));
  const tableStyle = await page.evaluate(() => { const th = document.querySelector('.m-asst .md-table th'); const cs = getComputedStyle(th); return cs.fontWeight >= 600 && cs.borderBottomStyle === 'solid'; });
  check('it is styled, with a header row and borders', tableStyle);
  if (process.env.STUDIO_SHOTS) for (const [theme, label] of [['dark', 'Dark'], ['light', 'Light'], ['sepia', 'Sepia']]) {
    await page.click('#btn-theme'); await page.click(`.menu-ctx .mi:has-text("${label}")`); await page.waitForTimeout(300);
    await page.locator('.m-asst .md-table').last().screenshot({ path: path.join(process.env.STUDIO_SHOTS, `table-${theme}.png`) });
  }
  if (process.env.STUDIO_SHOTS) { await page.click('#btn-theme'); await page.click('.menu-ctx .mi:has-text("Dark")'); }

  section('tool calls written as text');
  const donesBefore = await page.locator('.m-done').count();
  await send('A leaked call test');
  await page.waitForFunction(n => document.querySelectorAll('.m-done').length > n && !document.querySelector('#agent-stop:not(.hidden)'), donesBefore, { timeout: 20000 });
  const leakText = await page.locator('#msgs').innerText();
  check('a tool call the model wrote as text runs, and its raw text leaves the chat', !leakText.includes('<tool_call>') && !leakText.includes('<function=') && leakText.includes('Let me look.')
        && await page.locator('.m-tool[data-name="list_files"]').count() >= 1, leakText.slice(-300));
  await page.reload(); await page.waitForSelector('#app:not(.hidden)'); await page.waitForSelector('.m-done');
  check('and stays gone after a reload', !(await page.locator('#msgs').innerText()).includes('<tool_call>'));

  section('copy, pictures and files in the chat');
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
  await send('hello there');
  await page.waitForSelector('.m-asst:not(.streaming)');
  await page.locator('.m-asst').last().hover();
  await page.locator('.m-asst').last().locator('.copy-reply').click();
  check('a reply has a copy button that shows it worked', await page.waitForSelector('.m-asst .copy-reply.done', { timeout: 3000 }).then(() => true).catch(() => false));
  const copied = await page.evaluate(() => navigator.clipboard.readText().catch(e => 'ERR ' + e.message));
  check('it copies the reply as written', copied.includes('mock model'), copied);
  const pngFile = { name: 'shot.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64') };
  await page.setInputFiles('#attach-input', [pngFile, { name: 'notes.md', mimeType: 'text/markdown', buffer: Buffer.from('# Notes') }, { name: 'tool.zip', mimeType: 'application/zip', buffer: Buffer.from('PK') }]);
  await page.waitForFunction(() => document.querySelectorAll('#attach-row .attach-chip').length === 3, null, { timeout: 10000 }).catch(() => {});
  check('any file can be added: pictures and text as before, others saved in the folder\'s attachments/', await page.locator('#attach-row .attach-chip').count() === 3
    && await page.locator('#attach-row .attach-chip.pic img').count() === 1 && (await page.locator('#attach-row .attach-chip:has-text("tool.zip") .attach-where').innerText()) === 'attachments',
    await page.locator('#attach-row').innerText());
  await page.locator('#attach-row .attach-chip:has-text("notes.md") .attach-x').click();
  check('a chip can be removed', await page.locator('#attach-row .attach-chip').count() === 2);
  await send('hello, look at this');
  await page.waitForFunction(() => document.querySelectorAll('.m-user').length >= 2 && document.querySelector('#attach-row').classList.contains('hidden'), null, { timeout: 10000 });
  check('the sent message shows the picture, and the chips clear', await page.locator('.m-user').last().locator('.user-pics img').count() === 1);
  check('and names the saved file, so the agent and you can find it', (await page.locator('.m-user').last().innerText()).includes('tool.zip'), await page.locator('.m-user').last().innerText());
  await page.waitForSelector('.m-done', { timeout: 15000 });
  await page.reload(); await page.waitForSelector('#app:not(.hidden)'); await page.waitForSelector('.m-user .user-pics img');
  await page.waitForFunction(() => { const i = document.querySelector('.m-user .user-pics img'); return i && i.complete && i.naturalWidth > 0; }, null, { timeout: 10000 }).then(() => check('the picture is still there after a reload', true)).catch(() => check('the picture is still there after a reload', false));
  await page.evaluate(() => { const dt = new DataTransfer(); dt.items.add(new File(['pasted text'], 'clip.txt', { type: 'text/plain' })); const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }); document.querySelector('#agent-input').dispatchEvent(ev); });
  await page.waitForSelector('#attach-row .attach-chip');
  check('pasting a file adds it', (await page.locator('#attach-row').innerText()).includes('clip.txt'));
  await page.locator('#attach-row .attach-x').click();
  {
    const docx = path.join(projects, 'letter.docx');
    execFileSync('python3', ['-c', `import zipfile,sys
z=zipfile.ZipFile(sys.argv[1],"w")
z.writestr("word/document.xml",'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Αγαπητοί γονείς</w:t></w:r></w:p></w:body></w:document>')
z.close()`, docx]);
    check('one Add files button, with a paperclip and a menu, and no Word / Excel button', await page.locator('#doc-btn').count() === 0
      && await page.locator('#attach-btn [data-ico="paperclip"], #attach-btn svg').count() >= 1 && await page.locator('#attach-more').count() === 1);
    await page.click('#attach-more');
    await page.waitForSelector('.menu-ctx, .ctx-menu, .menu', { timeout: 3000 }).catch(() => {});
    const menuText = await page.locator('body').innerText();
    check('its menu adds from this computer or from the project', menuText.includes('From this computer') && menuText.includes('From the project'));
    await page.keyboard.press('Escape');
    await page.setInputFiles('#attach-input', docx);
    await page.waitForSelector('#attach-row .attach-chip');
    check('a Word file shows as a chip with a W badge', await page.locator('#attach-row .attach-doc[data-type="W"]').count() === 1 && (await page.locator('#attach-row').innerText()).includes('letter.docx'));
    await send('what does the letter say?');
    await page.waitForFunction(() => document.querySelector('#attach-row').classList.contains('hidden'), null, { timeout: 10000 });
    check('it is sent with the message and named in it', (await page.locator('.m-user').last().innerText()).includes('letter.docx'), await page.locator('.m-user').last().innerText());
    await page.waitForSelector('.m-done', { timeout: 15000 });
    const bigXlsx = path.join(os.tmpdir(), `contacts-${Date.now()}.xlsx`);
    execFileSync('python3', ['-c', `import zipfile,sys
ns='xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
z=zipfile.ZipFile(sys.argv[1],"w")
z.writestr("xl/workbook.xml",'<workbook '+ns+'><sheets><sheet name="Θεσσαλονίκη" sheetId="1" r:id="rId1"/><sheet name="Δράμα" sheetId="2" r:id="rId2"/></sheets></workbook>')
z.writestr("xl/_rels/workbook.xml.rels",'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>')
rows=''.join('<row r="%d"><c r="A%d" t="inlineStr"><is><t>p%d@example.com</t></is></c></row>' % (i,i,i) for i in range(1,3502))
z.writestr("xl/worksheets/sheet1.xml",'<worksheet '+ns+'><sheetData>'+rows+'</sheetData></worksheet>')
z.writestr("xl/worksheets/sheet2.xml",'<worksheet '+ns+'><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>x</t></is></c></row></sheetData></worksheet>')
z.close()`, bigXlsx]);
    const doneBefore = await page.locator('.m-done').count(), savedBefore = await page.locator('.m-user .saved-files').count();
    await page.setInputFiles('#attach-input', bigXlsx);
    await page.waitForSelector('#attach-row .attach-chip');
    await send('import these');
    await page.waitForFunction(n => document.querySelectorAll('.m-user .saved-files').length > n, savedBefore, { timeout: 10000 }).catch(() => {});
    const savedNote = await page.locator('.m-user .saved-files').last().innerText();
    check('a workbook too large for the chat shows its file once and its sheets by name', savedNote.split(path.basename(bigXlsx, '.xlsx')).length === 2
      && savedNote.includes('Sheets as CSV (2): Θεσσαλονίκη · Δράμα') && await page.locator('.m-user .saved-files').last().locator('.saved-link').count() === 3, await page.locator('.m-user').last().innerText());
    check('its links look like links, not buttons', await page.locator('.m-user .saved-link').first().evaluate(b => getComputedStyle(b).backgroundColor === 'rgba(0, 0, 0, 0)'));
    await page.waitForFunction(n => document.querySelectorAll('.m-done').length > n, doneBefore, { timeout: 15000 });
  }

  section('ComfyUI dashboard');
  await page.waitForSelector('#sb-comfy:not(.hidden)', { timeout: 10000 });
  check('an admin sees a ComfyUI pill when ComfyUI is set up, and a button in the top bar', (await page.locator('#sb-comfy-text').innerText()) === 'ComfyUI' && await page.locator('#btn-cdash').isVisible());
  await page.click('#btn-cdash');
  check('the top-bar button opens it too', await page.locator('#cdash').isVisible() && (await page.locator('#btn-cdash').getAttribute('aria-pressed')) === 'true');
  await page.click('#btn-cdash');
  await page.click('#sb-comfy');
  await page.waitForSelector('#cdash:not(.hidden) .cd-pic img');
  check('it opens a panel: idle, with memory and the latest picture', (await page.locator('#cdash .cd-prompt').innerText()).startsWith('Idle')
        && (await page.locator('#cdash .dash-rows').innerText()).includes('9.0 GB of 24.0 GB') && await page.locator('#cdash .cd-pic').count() === 1
        && (await page.locator('#cdash .dash-model').innerText()).includes('ComfyUI 0.44.0 · AMD Radeon RX 7900 XTX'));
  check('a thumbnail loads through Studio and names its prompt and seed', await page.waitForFunction(() => document.querySelector('#cdash .cd-pic img')?.naturalWidth === 1).then(() => true).catch(() => false)
        && (await page.locator('#cdash .cd-pic').getAttribute('title')).includes('a red fox in the snow') && (await page.locator('#cdash .cd-pic').getAttribute('title')).includes('seed 7')
        && (await page.locator('#cdash .cd-cap').innerText()).startsWith('14 s'));
  await fetch(`http://127.0.0.1:${gwPort}/_mock/comfy`, { method: 'POST', body: JSON.stringify({ busy: true }) });
  await page.waitForFunction(() => document.querySelector('#sb-comfy-text').textContent.includes('waiting'), null, { timeout: 5000 });
  check('while a picture is made, the pill and panel follow it', (await page.locator('#sb-comfy-text').innerText()) === 'Making… · 1 waiting'
        && (await page.locator('#cdash .cd-prompt').innerText()) === 'a lighthouse at dusk' && await page.locator('#cdash .cd-bar.busy').count() === 1
        && (await page.locator('#cdash .dash-dot').getAttribute('data-state')) === 'work', await page.locator('#sb-comfy-text').innerText());
  await fetch(`http://127.0.0.1:${gwPort}/_mock/comfy`, { method: 'POST', body: JSON.stringify({ busy: false }) });
  await page.locator('#cdash .cd-pic').click();
  await page.waitForSelector('#pane-preview .iv-stage .iv-img');
  check('a thumbnail opens in an image tab', (await page.locator('.tab.active').innerText()).includes('studio_00001_.png'));
  check('the panel offers the default picture model', (await page.locator('#cdash .cd-select[aria-label="Default picture model"]').inputValue()) === 'z-image-turbo'
        && (await page.locator('#cdash .cd-select[aria-label="Default picture model"] option').allInnerTexts()).join('|') === 'Z-Image Turbo|Z-Image Base|SDXL|Qwen-Image (unloads Strata)|Choose by style');
  await page.locator('#cdash .cd-select[aria-label="Default picture model"]').selectOption('auto');
  await page.waitForFunction(() => [...document.querySelectorAll('.toast')].some(t => t.textContent.includes('Choose by style')), null, { timeout: 5000 }).catch(() => {});
  check('choosing one saves it for the comfyui tool', (await api('/api/comfyui/status')).data.default_model === 'auto');
  await page.locator('#cdash .cd-select[aria-label="Default picture model"]').selectOption('z-image-turbo');
  const music = '#cdash .cd-select[aria-label="Music model"]';
  check('and the music model', (await page.locator(music).inputValue()) === 'ace-step'
        && (await page.locator(music + ' option').allInnerTexts()).join('|') === 'ACE-Step 1.5|HeartMuLa 3B');
  await page.locator(music).selectOption('heartmula');
  await page.waitForFunction(() => [...document.querySelectorAll('.toast')].some(t => t.textContent.includes('HeartMuLa')), null, { timeout: 5000 }).catch(() => {});
  const musicSaved = (await api("/api/comfyui/status")).data;
  check('choosing it saves it for generate_music, leaving the picture model alone', musicSaved.music_model === "heartmula" && musicSaved.default_model === "z-image-turbo", JSON.stringify(musicSaved));
  await page.locator(music).selectOption('ace-step');
  await page.click('#cdash .cd-expand');
  await page.waitForSelector('#pane-preview .cg-node');
  await page.waitForFunction(() => document.querySelectorAll('.cg-wire').length >= 3, null, { timeout: 5000 }).catch(() => {});
  const nodes = await page.evaluate(() => [...document.querySelectorAll('.cg-node')].map(n => ({ id: n.dataset.id, cls: n.className, col: n.parentElement && [...n.parentElement.parentElement.children].indexOf(n.parentElement), text: n.innerText })));
  check('the expand button opens the latest picture\'s nodes in a tab', (await page.locator('.tab.active').innerText()).includes('ComfyUI nodes') && nodes.length === 4
        && (await page.locator('.cg-state').innerText()) === 'Done in 14 s', JSON.stringify(nodes));
  check('nodes are laid out by what they read from, linked by wires', nodes.find(n => n.id === '2').col === 0 && nodes.find(n => n.id === '5').col === 1 && nodes.find(n => n.id === '7').col === 2
        && await page.locator('.cg-wire').count() === 3, JSON.stringify(nodes.map(n => [n.id, n.col])));
  check('each node shows its settings and state, and the save node its picture', nodes.find(n => n.id === '5').text.includes('seed') && nodes.find(n => n.id === '5').text.includes('Sampler')
        && nodes.find(n => n.id === '2').text.includes('Text prompt (positive)') && nodes.every(n => n.cls.includes('done')) && await page.locator('.cg-node[data-id="7"] .cg-pic').count() === 1);
  await page.locator('.cg-node[data-id="2"]').click();
  check('a node opens its full settings beside the graph', (await page.locator('.cg-side').innerText()).includes('a red fox in the snow'));
  await fetch(`http://127.0.0.1:${gwPort}/_mock/comfy`, { method: 'POST', body: JSON.stringify({ busy: true }) });
  await page.waitForFunction(() => document.querySelector('.cg-state')?.textContent.startsWith('Making the picture'), null, { timeout: 8000 }).catch(() => {});
  check('it follows the picture being made', (await page.locator('.cg-state').innerText()).startsWith('Making the picture') && (await page.locator('.cg-node[data-id="2"]').innerText()).includes('a lighthouse at dusk'));
  await fetch(`http://127.0.0.1:${gwPort}/_mock/comfy`, { method: 'POST', body: JSON.stringify({ busy: false }) });
  if (process.env.STUDIO_SHOTS) { await page.waitForTimeout(4500); await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'comfy-nodes.png') }); }
  await page.locator('.tab.active .t-close').click();
  await page.click('#sb-model');
  check('the model dashboard and this one take turns in the corner', await page.locator('#cdash').isHidden() && await page.locator('#dash').isVisible());
  await page.click('#sb-model');
  await page.waitForFunction(() => document.querySelector('#sb-comfy-text').textContent === 'ComfyUI', null, { timeout: 7000 });

  section('loading the model');
  await send('hello, slow load');
  await page.waitForSelector('#agent-status .st-load:not(.hidden)', { timeout: 8000 });
  const loadingText = await page.locator('#agent-status .st-text').innerText();
  check('the status line says the model is loading, with a percentage and the step', /^Loading model… \d+% · \S/.test(loadingText), loadingText);
  const firstWidth = await page.evaluate(() => parseFloat(document.querySelector('#agent-status .st-load > span').style.width));
  await page.waitForTimeout(2500);
  const laterWidth = await page.evaluate(() => parseFloat(document.querySelector('#agent-status .st-load > span').style.width) || 100);
  check('the bar moves forward', laterWidth > firstWidth && firstWidth > 0, `${firstWidth} -> ${laterWidth}`);
  await page.waitForSelector('#agent-status .st-load.hidden', { state: 'attached', timeout: 15000 });
  check('it goes away once the reply comes', (await page.locator('#msgs').innerText()).includes('mock model'));
  await fetch(`http://127.0.0.1:${gwPort}/_mock/reset_load`, { method: 'POST', body: '{}' });

  section('live servers');
  const liveDir = project('liveweb', {});
  const livePort = await freePort();
  const liveApp = spawn('python3', [path.join(ROOT, 'tests', 'live_app.py'), String(livePort)], { cwd: liveDir, stdio: 'ignore' });
  try {
    await waitPort(livePort);
    await openProject(liveDir);
    await page.evaluate(() => { const set = (id, on) => { const b = document.querySelector(id); if ((b.getAttribute('aria-checked') === 'true') !== on) b.click(); }; set('#tg-apply', true); set('#tg-cmd', true); });
    await page.click('#ex-live');
    await page.waitForSelector('.menu-ctx .mi');
    check('the Live servers button lists the folder\'s server', (await page.locator('.menu-ctx').innerText()).includes(`localhost:${livePort}`), await page.locator('.menu-ctx').innerText());
    await page.click(`.menu-ctx .mi:has-text("localhost:${livePort}")`);
    await page.waitForSelector('#pane-preview .live-bar');
    check('it opens as a Live tab', (await page.locator('.tab.active').innerText()).includes(`Live :${livePort}`));
    const live = page.frameLocator('#pane-preview iframe');
    await live.locator('#out').filter({ hasText: 'loaded' }).waitFor({ timeout: 10000 });
    check('the page runs, and its own requests to the root reach the app', (await live.locator('#out').innerText()) === 'loaded /api/echo');
    await live.locator('#go').click();
    await live.locator('#out').filter({ hasText: 'server saw' }).waitFor({ timeout: 10000 });
    check('clicks and POSTs inside the page work', (await live.locator('#out').innerText()) === 'server saw clicked');
    await page.fill('.live-addr', '/api/echo?from=bar'); await page.press('.live-addr', 'Enter');
    await live.locator('body').filter({ hasText: 'from=bar' }).waitFor({ timeout: 10000 });
    check('the address bar opens other paths', (await live.locator('body').innerText()).includes('"/api/echo?from=bar"'));
    await send(`Please view page ${livePort} /`);
    await page.waitForSelector('.m-page', { timeout: 60000 });
    const cardText = await page.locator('.m-page').innerText();
    check('a view_page call shows as a card with the title and status', cardText.includes('Live test app') && cardText.includes('HTTP 200') && cardText.includes(`localhost:${livePort}/`), cardText);
    await page.waitForFunction(() => { const i = document.querySelector('.m-page img'); return i && i.complete && i.naturalWidth > 0; }, null, { timeout: 15000 }).then(() => check('with the screenshot the model saw', true)).catch(() => check('with the screenshot the model saw', false));
    check('the plain tool row stays for its result', await page.locator('.m-tool[data-name="view_page"]').count() === 1);
    await page.locator('.m-page img').first().click();
    await page.waitForSelector('#pane-preview .iv-stage .iv-img');
    await page.waitForFunction(() => { const i = document.querySelector('.iv-img'); return i && i.complete && i.naturalWidth > 0; });
    check('clicking a screenshot opens it in a tab', (await page.locator('.tab.active').innerText()).includes(`page-${livePort}.png`));
    const pct = async () => parseInt(await page.locator('.iv-pct').innerText());
    const fitPct = await pct();
    await page.locator('.iv-bar button[aria-label^="Zoom in"]').click();
    check('the zoom buttons zoom', await pct() > fitPct, `${fitPct} -> ${await pct()}`);
    const box = await page.locator('.iv-stage').boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.wheel(0, -300);
    const afterWheel = await pct();
    check('the mouse wheel zooms too', afterWheel > fitPct, afterWheel);
    const before = await page.evaluate(() => document.querySelector('.iv-img').style.transform);
    await page.mouse.down(); await page.mouse.move(box.x + box.width / 2 + 80, box.y + box.height / 2 + 40, { steps: 4 }); await page.mouse.up();
    check('dragging pans the picture', (await page.evaluate(() => document.querySelector('.iv-img').style.transform)) !== before);
    await page.locator('.iv-bar button:has-text("1:1")').click();
    check('1:1 shows actual size', await pct() === 100);
    await page.locator('.iv-bar button:has-text("Fit")').click();
    check('Fit brings it back', await pct() === fitPct);
    const [download] = await Promise.all([page.waitForEvent('download'), page.locator('.iv-bar button[aria-label="Download the picture"]').click()]);
    check('it can be downloaded', download.suggestedFilename() === `page-${livePort}.png`, download.suggestedFilename());
    await page.locator('.iv-bar button[aria-label="Copy the picture"]').click();
    await page.waitForSelector('.toast:has-text("Picture copied")', { timeout: 5000 }).then(() => check('and copied as a picture', true)).catch(async () => check('and copied as a picture', false, await page.locator('.toast').last().innerText().catch(() => '')));
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'image-tab.png') });
    await page.locator('.tab.active .t-close').click();
    await page.waitForSelector('.m-done', { timeout: 30000 });
    await page.click('.tab:has-text("README")').catch(() => {});
    await page.locator('.m-page button:has-text("Open in Live tab")').click();
    await page.waitForSelector('#pane-preview .live-bar');
    check('the card opens the page in the Live tab', (await page.locator('.tab.active').innerText()).includes(`Live :${livePort}`) && await page.locator('.tab:has-text("Live :")').count() === 1);
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'live.png') });
    await page.locator('.tab.active .t-close').click();
    await send(`Please open preview ${livePort}`);
    await page.waitForSelector('.m-opened', { timeout: 30000 });
    await page.waitForSelector('#pane-preview .live-bar', { timeout: 10000 });
    check('when the model opens a preview, the Live tab opens for the user', (await page.locator('.tab.active').innerText()).includes(`Live :${livePort}`) && (await page.locator('.m-opened').innerText()).includes(`localhost:${livePort}/`));
    await page.locator('.tab.active .t-close').click();
    await page.locator('.m-opened button').click();
    await page.waitForSelector('#pane-preview .live-bar');
    check('and its card opens it again', (await page.locator('.tab.active').innerText()).includes(`Live :${livePort}`));
    await page.locator('.tab.active .t-close').click();
  } finally { liveApp.kill(); }

  section('a tunnel that holds streams back');
  let polledHere = 0; const countPolls = r => { if (/\/api\/\w+\/poll/.test(r.url())) polledHere++; };
  page.on('request', countPolls);
  const tunnelled = project('tunnelled', { 'README.md': '# Sample once\n' });
  await openProject(tunnelled);
  await page.evaluate(() => { const set = (id, on) => { const b = document.querySelector(id); if ((b.getAttribute('aria-checked') === 'true') !== on) b.click(); }; set('#tg-apply', false); set('#tg-cmd', false); });
  await page.waitForTimeout(300);
  const held = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  await held.route(/\/api\/(queue|agent|term)\/stream/, () => {});     // never answers, like a buffering proxy
  const far = await held.newPage();
  far.on('pageerror', e => errors.push('held streams: ' + e.message));
  await far.goto(url);
  await far.waitForSelector('#login:not(.hidden)');
  await far.fill('#login-user', 'admin'); await far.fill('#login-pw', password);
  await far.click('#login-submit');
  await far.waitForSelector('#app:not(.hidden)');
  await far.waitForRequest(/\/api\/queue\/poll/, { timeout: 12000 }).then(() => check('silent streams switch the window to polling', true)).catch(() => check('silent streams switch the window to polling', false));
  await far.waitForFunction(() => document.querySelector('#ws-name')?.textContent === 'tunnelled', null, { timeout: 8000 });
  await page.click('#btn-theme'); await page.click('.menu-ctx .mi:has-text("Sepia")');
  await far.waitForFunction(() => getComputedStyle(document.body).backgroundColor === 'rgb(244, 236, 216)', null, { timeout: 12000 }).then(() => check('a theme change arrives by polling', true)).catch(() => check('a theme change arrives by polling', false));
  await page.click('#btn-theme'); await page.click('.menu-ctx .mi:has-text("Dark")');
  await send('Please rename the word');
  await far.waitForSelector('.m-appr.pending', { timeout: 15000 }).catch(() => {});
  check('a run and its approval arrive by polling', (await far.locator('.m-user').last().innerText().catch(() => '')).includes('Please rename the word') && await far.locator('.m-appr.pending').count() === 1);
  await far.click('.m-appr.pending button[data-act="accept"]');
  await page.waitForSelector('.m-appr.resolved', { timeout: 8000 });
  check('answering from the polling window works', fs.readFileSync(path.join(tunnelled, 'README.md'), 'utf8').includes('Demo'));
  await far.waitForSelector('.m-appr.pending', { timeout: 15000 });  // the echo command
  await far.click('.m-appr.pending button[data-act="accept"]');
  await far.waitForSelector('.m-done', { timeout: 15000 }).then(() => check('the polling window sees the run end', true)).catch(() => check('the polling window sees the run end', false));
  if (await far.locator('#panel.hidden').count()) await far.click('#sb-term');
  await far.waitForSelector('.xterm', { timeout: 15000 });
  await far.locator('.xterm').last().click();
  await far.keyboard.type('echo polled-$((40+2))\n');
  await far.waitForFunction(() => [...document.querySelectorAll('.xterm-rows, .xterm-accessibility-tree')].some(n => n.innerText.includes('polled-42')), null, { timeout: 8000 }).then(() => check('a terminal works by polling', true)).catch(() => check('a terminal works by polling', false));
  let streamsTried = 0; far.on('request', r => { if (/\/api\/\w+\/stream/.test(r.url())) streamsTried++; });
  // Listening from before the reload: the first poll can come before the page has finished appearing.
  const polledAgain = far.waitForRequest(/\/api\/queue\/poll/, { timeout: 5000 }).then(() => true).catch(() => false);
  await far.reload(); await far.waitForSelector('#app:not(.hidden)');
  check('after a reload the tab polls at once', await polledAgain);
  check('without trying streams again', streamsTried === 0, streamsTried);
  await held.close();
  page.off('request', countPolls);
  check('a window whose streams work never polls', polledHere === 0, polledHere);

  section('closing the browser during a run');
  const survive = project('survive', { 'README.md': '# Sample once\n' });
  await openProject(survive);
  await page.evaluate(() => { const set = (id, on) => { const b = document.querySelector(id); if ((b.getAttribute('aria-checked') === 'true') !== on) b.click(); }; set('#tg-apply', false); set('#tg-cmd', false); });
  await page.waitForTimeout(300);
  await send('Please rename the word');
  await page.waitForSelector('.m-appr.pending');
  const cookies = await context.cookies();
  await page.goto('about:blank');                   // every window of this user is now closed
  await new Promise(r => setTimeout(r, 3000));
  const later = await browser.newContext({ viewport: { width: 1400, height: 900 } });       // a different browser, with nothing stored
  await later.addCookies(cookies);
  const back = await later.newPage();
  await back.goto(url); await back.waitForSelector('#app:not(.hidden)');
  await back.waitForFunction(() => document.querySelector('#ws-name')?.textContent === 'survive', null, { timeout: 8000 }).catch(() => {});
  await back.waitForSelector('.m-appr.pending', { timeout: 10000 }).then(() => check('reopening in another browser shows the waiting run', true)).catch(() => check('reopening in another browser shows the waiting run', false));
  check('its earlier messages are there', (await back.locator('.m-user').last().innerText().catch(() => '')).includes('Please rename the word'));
  await back.click('.m-appr.pending button[data-act="accept"]');
  await back.waitForSelector('.m-appr.pending', { timeout: 10000 });     // the next step of the same run
  check('it carries on from there', fs.readFileSync(path.join(survive, 'README.md'), 'utf8').includes('Demo'));

  section('Studio itself stopping during a run');
  studio.kill('SIGKILL');
  await new Promise(r => setTimeout(r, 500));
  studio = spawn('python3', [path.join(ROOT, 'studio.py'), '--config', cfg], { stdio: ['ignore', studioLog, studioLog], env: { ...process.env, PYTHONUNBUFFERED: '1' } });
  await waitPort(port);
  await back.reload(); await back.waitForSelector('#app:not(.hidden)');
  await back.waitForFunction(() => document.querySelector('#ws-name')?.textContent === 'survive', null, { timeout: 8000 }).catch(() => {});
  await back.waitForSelector('.m-done button:has-text("Continue")', { timeout: 10000 }).then(() => check('a run lost to a Studio restart says so and offers Continue', true)).catch(() => check('a run lost to a Studio restart says so and offers Continue', false));
  check('the chat is kept', (await back.locator('.m-user').first().innerText().catch(() => '')).includes('Please rename the word'));
  await back.click('.m-done button:has-text("Continue")');
  await back.waitForFunction(() => [...document.querySelectorAll('.m-user')].pop()?.innerText.includes('Continue from where you stopped'), null, { timeout: 10000 }).then(() => check('Continue works after the restart', true)).catch(() => check('Continue works after the restart', false));
  await back.waitForSelector('.m-appr.pending, .m-tool.ok', { timeout: 15000 }).then(() => check('the model carries on after Continue, with no conversation error', true)).catch(() => check('the model carries on after Continue, with no conversation error', false));
  check('no error is shown after Continue', await back.locator('.m-err').count() === 0, await back.locator('.m-err').allInnerTexts());
  await back.click('#agent-stop', { timeout: 3000 }).catch(() => {});
  await later.close();
  await page.goto(url); await page.waitForSelector('#app:not(.hidden)');

  section('videos and timelines');
  {
    const reel = project('reel', {});
    fs.mkdirSync(path.join(reel, 'media'));
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=25:duration=4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', path.join(reel, 'media', 'cut.mp4')]);
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=400x300', '-frames:v', '1', path.join(reel, 'media', 'still.png')]);
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'sine=frequency=330:duration=6', path.join(reel, 'media', 'tone.mp3')]);
    { // a cube, 2 x 1 x 1, as ASCII STL: 12 triangles
      const v = [[0, 0, 0], [2, 0, 0], [2, 1, 0], [0, 1, 0], [0, 0, 1], [2, 0, 1], [2, 1, 1], [0, 1, 1]];
      const faces = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [2, 3, 7], [2, 7, 6], [1, 2, 6], [1, 6, 5], [3, 0, 4], [3, 4, 7]];
      fs.writeFileSync(path.join(reel, 'media', 'block.stl'), 'solid block\n' + faces.map(f => ' facet normal 0 0 0\n  outer loop\n' + f.map(i => `   vertex ${v[i].join(' ')}\n`).join('') + '  endloop\n endfacet\n').join('') + 'endsolid block\n');
    }
    await openProject(reel);
    if (await page.locator('.act.active[data-view="explorer"]').count() === 0) await page.click('.act[data-view="explorer"]');
    await page.locator('.t-row', { hasText: 'media' }).click();
    await page.locator('.t-row', { hasText: 'cut.mp4' }).dblclick();
    await page.waitForSelector('#pane-preview .preview-video video');
    check('a video file opens as a player', await page.waitForFunction(() => document.querySelector('.preview-video video')?.readyState >= 1, null, { timeout: 8000 }).then(() => true).catch(() => false));
    check('the video player has an Edit button', await page.locator('.preview-video .ae-editbtn').count() === 1);
    await page.locator('.t-row', { hasText: 'tone.mp3' }).dblclick();
    check('a sound file opens as a player with its waveform', await page.waitForFunction(() => !!document.querySelector('.ae-player') && /0:06/.test(document.querySelector('.ae-info')?.textContent || ''), null, { timeout: 15000 }).then(() => true).catch(() => false));
    await page.click('.act[data-view="media"]');
    await page.waitForSelector('#media-body .media-row');
    check('the Media view lists the sounds and videos, and the pictures as thumbnails', await page.locator('#media-body .media-row.kind-sound').count() === 1
          && await page.locator('#media-body .media-row.kind-video').count() === 1 && await page.locator('#media-body .media-tile:not(.kind-model) img').count() === 1);
    check('a sound plays in place, with its length', await page.locator('#media-body .media-row.kind-sound audio').count() === 1
          && (await page.locator('#media-body .media-row.kind-sound .media-meta').innerText()).includes('6 s'), await page.locator('#media-body .media-row.kind-sound .media-meta').innerText());
    check('the picture thumbnail loads', await page.waitForFunction(() => document.querySelector('#media-body .media-tile img')?.naturalWidth > 0, null, { timeout: 8000 }).then(() => true, () => false));
    await page.locator('#media-body .chip', { hasText: 'Sounds' }).click();
    check('the Sounds filter keeps only sounds', await page.locator('#media-body .media-row').count() === 1 && await page.locator('#media-body .media-tile').count() === 0);
    await page.locator('#media-body .chip', { hasText: 'All' }).click();
    await page.locator('#media-body .media-search').fill('still');
    await page.waitForTimeout(300);
    check('search narrows the list', await page.locator('#media-body .media-row').count() === 0 && await page.locator('#media-body .media-tile').count() === 1);
    await page.locator('#media-body .media-search').fill('');
    await page.waitForTimeout(300);
    const rowNames = () => page.$$eval('#media-body .media-row .media-name', ns => ns.map(n => n.textContent).join(','));
    await page.selectOption('#media-body .media-sort', 'az');
    check('Sort: Name A–Z', await rowNames() === 'cut.mp4,tone.mp3', await rowNames());
    await page.selectOption('#media-body .media-sort', 'za');
    check('Sort: Name Z–A', await rowNames() === 'tone.mp3,cut.mp4', await rowNames());
    await page.selectOption('#media-body .media-sort', 'long');
    check('Sort: longest first (the 6 s sound before the 4 s video)', await rowNames() === 'tone.mp3,cut.mp4', await rowNames());
    await page.selectOption('#media-body .media-sort', 'short');
    check('Sort: shortest first', await rowNames() === 'cut.mp4,tone.mp3', await rowNames());
    await page.selectOption('#media-body .media-sort', 'big');
    check('the sort is kept per browser', await page.evaluate(() => localStorage.getItem('studio.mediaSort')) === 'big');
    await page.selectOption('#media-body .media-sort', 'new');
    await page.locator('#media-body .media-row.kind-sound button', { hasText: '@' }).click();
    check('@ puts the file in the chat box', (await page.locator('#agent-input').inputValue()).includes('@media/tone.mp3'));
    await page.locator('#media-body .chip', { hasText: '3D' }).click();
    check('3D models have their own filter and tiles', await page.locator('#media-body .media-tile.kind-model').count() === 1 && await page.locator('#media-body .media-row').count() === 0
          && (await page.locator('#media-body .chip', { hasText: '3D' }).innerText()).includes('1'));
    check('a 3D model gets a drawn thumbnail', await page.waitForFunction(() => (document.querySelector('#media-body .kind-model img')?.getAttribute('src') || '').startsWith('data:image/png'), null, { timeout: 30000 }).then(() => true, () => false));
    await page.locator('#media-body .media-tile.kind-model').click();
    const drew = await page.waitForFunction(() => +document.querySelector('#pane-preview .model-stage')?.dataset.drawn > 0, null, { timeout: 30000 }).then(() => true, () => false);
    check('it opens in the 3D viewer and is drawn', drew && await page.locator('#pane-preview .model-stage canvas').count() === 1, await page.locator('#pane-preview').innerText());
    check('which says what the model is', /12 triangles/.test(await page.locator('#pane-preview .model-info').innerText()) && /2\.00 × 1\.00 × 1\.00/.test(await page.locator('#pane-preview .model-info').innerText()),
          await page.locator('#pane-preview .model-info').innerText());
    const shown = await page.evaluate(() => +document.querySelector('#pane-preview .model-stage').dataset.drawn);
    await page.waitForTimeout(400);
    check('and draws nothing while nothing moves', await page.evaluate(() => +document.querySelector('#pane-preview .model-stage').dataset.drawn) - shown <= 2);
    await page.locator('#pane-preview .model-tools button', { hasText: 'Turn' }).click();
    await page.waitForTimeout(400);
    check('Turn spins it', await page.evaluate(() => +document.querySelector('#pane-preview .model-stage').dataset.drawn) - shown > 5);
    await page.locator('#pane-preview .model-tools button', { hasText: 'Turn' }).click();
    check('the view\'s buttons float over the model, and the bar above stays one tidy line', await page.evaluate(() => {
      const st = document.querySelector('#pane-preview .model-stage').getBoundingClientRect(), tb = document.querySelector('#pane-preview .model-tools').getBoundingClientRect();
      const bar = document.querySelector('#pane-preview .preview-bar');
      return tb.left >= st.left && tb.right <= st.right && tb.bottom <= st.bottom && tb.top >= st.top && bar.scrollWidth <= bar.clientWidth + 1 && bar.getBoundingClientRect().height <= 40;
    }));
    await page.locator('#media-body .chip', { hasText: 'All' }).click();
    await page.locator('#agent-input').fill('');
    await page.click('.act[data-view="video"]');
    await page.waitForSelector('#video-body .tl-hint');
    check('the Timeline view says how to start, and counts the media', (await page.locator('#video-body').innerText()).includes('No videos in this folder yet') && (await page.locator('#video-body').innerText()).includes('3 '));
    await page.click('#video-new');
    await page.locator('.dialog input').fill('trailer');
    await page.keyboard.press('Enter');
    await page.waitForSelector('#pane-preview .tl-bin .tl-media-item');
    check('a new video opens as an empty timeline with the media below', (await page.locator('.tl-head').innerText()).includes('empty') && await page.locator('.tl-media-item').count() === 3
          && await page.locator('#video-body .video-item').count() === 1);
    await page.locator('.tl-media-item', { hasText: 'cut.mp4' }).click();
    await page.waitForFunction(() => document.querySelectorAll('.tl-block.clip').length === 1 && document.querySelector('.tl-status')?.textContent === 'Saved', null, { timeout: 10000 });
    await page.locator('.tl-media-item', { hasText: 'still.png' }).click();
    await page.waitForFunction(() => document.querySelectorAll('.tl-block.clip').length === 2 && document.querySelector('.tl-status')?.textContent === 'Saved', null, { timeout: 10000 });
    await page.locator('.tl-media-item', { hasText: 'tone.mp3' }).click();
    await page.locator('.tl-bin-acts button', { hasText: '+ Title' }).click();
    await page.waitForFunction(() => document.querySelectorAll('.tl-block').length === 4 && document.querySelector('.tl-status')?.textContent === 'Saved', null, { timeout: 10000 });
    check('media and a title go onto their tracks, and the length is worked out', (await page.locator('.tl-info').innerText()).startsWith('8.0 s · 2 clips'), await page.locator('.tl-info').innerText());
    await page.locator('.tl-text').fill('Γωνιακό Ράφι'); await page.locator('.tl-text').blur();
    await page.waitForFunction(() => document.querySelector('.tl-block.title')?.innerText.includes('Γωνιακό Ράφι'), null, { timeout: 8000 });
    const saved = JSON.parse(fs.readFileSync(path.join(reel, 'video', 'trailer.timeline.json'), 'utf8'));
    check('editing a title in the inspector saves it to the timeline file', saved.titles[0].text === 'Γωνιακό Ράφι' && saved._layout.titles[0].label === 'Γωνιακό Ράφι', JSON.stringify(saved.titles));
    await page.locator('.tl-block.clip').nth(1).click();
    check('the inspector offers every transition, in groups', await page.locator('.tl-tr-pick optgroup').count() === 5 && await page.locator('.tl-tr-pick option').count() === 59);
    await page.locator('.tl-tr-pick').selectOption('dissolve');
    await page.waitForFunction(() => document.querySelectorAll('.tl-block.clip')[1]?.innerText.includes('Dissolve'), null, { timeout: 8000 });
    check('a transition chosen in the inspector shows on the clip', (await page.locator('.tl-info').innerText()).startsWith('7.0 s'), await page.locator('.tl-info').innerText());
    const tlFile = () => JSON.parse(fs.readFileSync(path.join(reel, 'video', 'trailer.timeline.json'), 'utf8'));
    const fileSoon = async test => { for (let i = 0; i < 40; i++) { try { if (test(tlFile())) return true; } catch { /* being written */ } await page.waitForTimeout(200); } return false; };
    await page.locator('.tl-tools .ae-b', { hasText: 'Transition' }).click();
    check('the Transition menu has the common ones and a way to all of them', (await page.locator('.menu-ctx').innerText()).includes('✓ Dissolve') && (await page.locator('.menu-ctx').innerText()).includes('All 58 transitions'));
    await page.locator('.menu-ctx .mi', { hasText: 'Circle open' }).click();
    await page.waitForFunction(() => document.querySelectorAll('.tl-block.clip')[1]?.innerText.includes('Circle open'), null, { timeout: 8000 }).catch(() => {});
    check('and sets the selected clip\'s transition', tlFile().clips[1].transition.type === 'circle', JSON.stringify(tlFile().clips[1]));
    await page.locator('.tl-tools .ae-b', { hasText: 'Effects' }).click();
    await page.locator('.menu-ctx .mi', { hasText: 'Vignette' }).click();
    await page.waitForFunction(() => document.querySelector('.tl-chip[data-fx="vignette"]')?.classList.contains('on'), null, { timeout: 8000 }).catch(() => {});
    check('the Effects menu adds an effect to the selected clip', await fileSoon(f => f.clips[1].effects?.some(e => e.type === 'vignette' && e.amount === 0.5)), JSON.stringify(tlFile().clips[1].effects));
    check('the inspector groups the effects', await page.locator('.tl-fx-group').count() === 4 && await page.locator('.tl-chip[data-fx]').count() === 26);
    await page.locator('.tl-chip[data-fx="warm"]').click();
    await page.waitForSelector('.tl-fx-row input[aria-label="Warm Amount"]', { timeout: 8000 });
    await page.locator('.tl-fx-row input[aria-label="Warm Amount"]').fill('0.8');
    await page.waitForFunction(() => true); await page.waitForTimeout(600);
    check('an effect\'s setting is a slider that saves', await fileSoon(f => f.clips[1].effects?.find(e => e.type === 'warm')?.amount === 0.8), JSON.stringify(tlFile().clips[1].effects));
    await page.locator('.tl-chip[data-fx="warm"]').click();
    await page.waitForFunction(() => !document.querySelector('.tl-chip[data-fx="warm"]')?.classList.contains('on'), null, { timeout: 8000 }).catch(() => {});
    await page.locator('.tl-chip[data-fx="vignette"]').click();
    await page.waitForFunction(() => !document.querySelector('.tl-chip[data-fx="vignette"]')?.classList.contains('on'), null, { timeout: 8000 }).catch(() => {});
    check('and a chip turns it off again', await fileSoon(f => !f.clips[1].effects?.length), JSON.stringify(tlFile().clips[1].effects));
    await page.locator('.tl-tr-pick').selectOption('dissolve');
    await page.waitForFunction(() => document.querySelectorAll('.tl-block.clip')[1]?.innerText.includes('Dissolve'), null, { timeout: 8000 });
    const grip = await page.locator('.tl-block.clip').nth(1).locator('.tl-grip').boundingBox();
    const lane = await page.locator('.tl-lane[data-type="clip"]').boundingBox();
    await page.mouse.move(grip.x + 4, grip.y + 10); await page.mouse.down(); await page.mouse.move(grip.x + 4 + lane.width * 2 / 7, grip.y + 10, { steps: 5 }); await page.mouse.up();
    await page.waitForFunction(() => document.querySelector('.tl-info')?.textContent.startsWith('9.0 s'), null, { timeout: 8000 }).catch(() => {});
    check('dragging a clip\'s right edge makes it longer', (await page.locator('.tl-info').innerText()).startsWith('9.0 s'), await page.locator('.tl-info').innerText());
    await page.locator('.tl-acts button', { hasText: 'Ask the agent' }).click();
    const asked = await page.inputValue('#agent-input');
    check('Ask the agent puts the timeline and the selected clip in the chat box', asked.startsWith('@video/trailer.timeline.json In this video timeline, clip 2 (still.png, '), asked);
    await page.fill('#agent-input', '');
    await page.locator('.tl-acts button', { hasText: 'Preview' }).click();
    await page.waitForFunction(() => /Rendered video\/trailer\.preview\.mp4/.test(document.querySelector('.tl-status')?.textContent || ''), null, { timeout: 60000 }).catch(() => {});
    check('Preview renders with progress, and the result plays above the tracks', (await page.locator('.tl-status').innerText()).includes('Rendered video/trailer.preview.mp4')
          && await page.waitForFunction(() => document.querySelector('.tl-video')?.readyState >= 1, null, { timeout: 8000 }).then(() => true).catch(() => false), await page.locator('.tl-status').innerText());
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'timeline.png') });
    await page.locator('.tl-block.clip').nth(1).click();
    await page.waitForTimeout(300);
    const seek = await page.evaluate(() => { const v = document.querySelector('.tl-video'); return { t: v.currentTime, d: v.duration, ready: v.readyState, n: document.querySelectorAll('.tl-video').length, audio: document.querySelectorAll('audio').length }; });
    check('clicking a clip moves the video there', seek.t >= 3, JSON.stringify(seek));
    const clipsNow = () => page.locator('.tl-block.clip').count();
    const c2 = await page.locator('.tl-block.clip').nth(1).boundingBox(), rul = await page.locator('.tl-ruler').boundingBox();
    await page.mouse.click(c2.x + c2.width * 0.6, rul.y + 8);
    await page.locator('.tl').focus();
    await page.keyboard.press('s');
    check('S splits the clip under the playhead in two', await page.waitForFunction(() => document.querySelectorAll('.tl-block.clip').length === 3 && document.querySelector('.tl-status')?.textContent === 'Saved', null, { timeout: 10000 }).then(() => true, () => false), await page.locator('.tl-status').innerText());
    check('the player says the render is older than the edit', await page.locator('.tl-stale:not(.hidden)').count() === 1);
    await page.keyboard.press('Control+z');
    check('Ctrl+Z undoes the split', await page.waitForFunction(() => document.querySelectorAll('.tl-block.clip').length === 2, null, { timeout: 10000 }).then(() => true, () => false), String(await clipsNow()));
    await page.keyboard.press('Control+Shift+z');
    check('Ctrl+Shift+Z redoes it', await page.waitForFunction(() => document.querySelectorAll('.tl-block.clip').length === 3, null, { timeout: 10000 }).then(() => true, () => false), String(await clipsNow()));
    await page.locator('.tl-tools .ae-b[aria-label^="Undo"]').click();
    await page.waitForFunction(() => document.querySelectorAll('.tl-block.clip').length === 2 && document.querySelector('.tl-status')?.textContent === 'Saved', null, { timeout: 10000 }).catch(() => {});
    const pps0 = +(await page.locator('.tl-lanes').getAttribute('data-pps'));
    await page.locator('.tl-tools .ae-b[aria-label^="Zoom in"]').click();
    check('zoom in widens the tracks', Math.abs(+(await page.locator('.tl-lanes').getAttribute('data-pps')) - pps0 * 2) < 0.01 && await page.evaluate(() => document.querySelector('.tl-scroll').scrollWidth > document.querySelector('.tl-scroll').clientWidth));
    await page.locator('.tl-tools .ae-b', { hasText: 'Fit' }).click();
    await page.locator('.tl-ruler').scrollIntoViewIfNeeded();
    const ruler = await page.locator('.tl-ruler').boundingBox(), pps1 = +(await page.locator('.tl-lanes').getAttribute('data-pps'));
    await page.mouse.click(ruler.x + pps1 * 1, ruler.y + 8);
    await page.waitForTimeout(300);
    check('clicking the ruler moves the playhead there', await page.evaluate(() => Math.abs(document.querySelector('.tl-video').currentTime - 1) < 0.3), await page.evaluate(() => document.querySelector('.tl-video').currentTime));
    await page.locator('.tl-play, .tl-transport .ae-play').first().click();
    await page.waitForTimeout(600);
    check('Play plays the rendered video', await page.evaluate(() => !document.querySelector('.tl-video').paused && document.querySelector('.tl-transport .ae-play').getAttribute('aria-label') === 'Pause'));
    await page.locator('.tl-transport .ae-play').click();
    if (process.env.STUDIO_SHOTS) for (const [theme, label] of [['dark', 'Dark'], ['light', 'Light']]) {
      await page.click('#btn-theme'); await page.click(`.menu-ctx .mi:has-text("${label}")`);
      await page.locator('.tl-block.clip').nth(1).click(); await page.waitForTimeout(300);
      await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, `timeline-${theme}.png`) });
    }
    if (process.env.STUDIO_SHOTS) { await page.click('#btn-theme'); await page.click('.menu-ctx .mi:has-text("Dark")'); }
    await page.setViewportSize({ width: 390, height: 800 });
    await page.waitForTimeout(500);
    await page.click('#mobile-tabs button[data-tab="editor"]');
    await page.waitForTimeout(500);
    const tlPhone = await page.evaluate(() => ({ wide: document.documentElement.scrollWidth <= innerWidth + 1, tools: document.querySelector('.tl-tools')?.getBoundingClientRect().height || 0,
      player: document.querySelector('.tl-player')?.getBoundingClientRect().width || 0, playerH: document.querySelector('.tl-player')?.getBoundingClientRect().height || 0, lanes: document.querySelector('.tl-scroll')?.getBoundingClientRect().width || 0 }));
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'timeline-phone.png'), fullPage: false });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.waitForTimeout(400);
    check('on a phone the timeline fits the width, with one toolbar row and a full-width player', tlPhone.wide && tlPhone.tools < 50 && tlPhone.player > 340 && tlPhone.playerH > 180 && tlPhone.lanes > 340, JSON.stringify(tlPhone));
    await page.click('.act[data-view="media"]');
    await page.waitForSelector('#media-body .media-row.kind-video');
    await page.locator('#media-body .chip', { hasText: 'Videos' }).click();
    const tlBtn = page.locator('#media-body .media-row', { hasText: 'trailer.preview.mp4' }).locator('button', { hasText: 'Timeline' });
    await page.waitForFunction(() => [...document.querySelectorAll('#media-body .media-name')].some(n => n.textContent === 'trailer.preview.mp4'), null, { timeout: 8000 }).catch(() => {});
    const rows = await page.locator('#media-body .media-row').evaluateAll(rs => rs.map(r => r.querySelector('.media-name').textContent + (r.querySelector('.media-acts').textContent.includes('Timeline') ? ' [Timeline]' : '')));
    check('a rendered video in Media has a Timeline button', rows.includes('trailer.preview.mp4 [Timeline]') && rows.includes('cut.mp4'), JSON.stringify(rows));
    await tlBtn.click();
    check('it opens that timeline', await page.waitForFunction(() => document.querySelector('.tab.active')?.textContent.includes('trailer.timeline.json') && !!document.querySelector('#pane-preview .tl-lanes'), null, { timeout: 8000 }).then(() => true, () => false));
    await page.locator('#media-body .chip', { hasText: 'All' }).click();
    check('the film icon is called Timeline', (await page.locator('.act[data-view="video"]').getAttribute('aria-label')) === 'Timeline' && (await page.locator('#view-video .view-title').innerText()).toLowerCase() === 'timeline');
    check('the code view is still there', await page.locator('#btn-preview:not(.hidden)').count() === 1);
    await page.locator('.tab', { hasText: 'cut.mp4' }).click();
    await page.locator('.preview-video .ae-editbtn').click();
    check('Edit on a video starts a timeline with it as the first clip', await page.waitForFunction(() => document.querySelector('.tab.active')?.textContent.includes('cut edit.timeline.json') && document.querySelectorAll('.tl-block.clip').length === 1, null, { timeout: 15000 }).then(() => true, () => false)
          && fs.existsSync(path.join(reel, 'video', 'cut edit.timeline.json')));
    await page.locator('.tab', { hasText: 'trailer.timeline.json' }).click();
    await page.waitForSelector('#pane-preview .tl-lanes');
    await page.waitForFunction(() => !!document.querySelector('.tl-video')?.src, null, { timeout: 8000 }).catch(() => {});
    const ranged = await page.evaluate(async () => { const v = document.querySelector('.tl-video'); const r = await fetch(v.src, { headers: { Range: 'bytes=0-99' } }); return [r.status, r.headers.get('content-range'), (await r.arrayBuffer()).byteLength]; });
    check('videos are served in byte ranges, so they can be seeked', ranged[0] === 206 && /^bytes 0-99\//.test(ranged[1]) && ranged[2] === 100, JSON.stringify(ranged));
    await page.click('.act[data-view="explorer"]');
  }

  section('audio player and editor');
  {
    const snd = project('sounds', {});
    fs.mkdirSync(path.join(snd, 'sfx'));
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'sine=frequency=330:duration=8', '-f', 'lavfi', '-i', 'sine=frequency=495:duration=8', '-filter_complex', '[0][1]amerge=inputs=2,volume=0.6', '-c:a', 'libmp3lame', path.join(snd, 'sfx', 'bell.mp3')]);
    await openProject(snd);
    if (await page.locator('.act.active[data-view="explorer"]').count() === 0) await page.click('.act[data-view="explorer"]');
    await page.locator('.t-row', { hasText: 'sfx' }).click();
    await page.locator('.t-row', { hasText: 'bell.mp3' }).dblclick();
    const info = () => page.locator('.ae-info').innerText();
    const status = () => page.locator('.ae-status').innerText();
    const waitStatus = re => page.waitForFunction(r => new RegExp(r).test(document.querySelector('.ae-status')?.textContent || ''), re.source, { timeout: 20000 }).then(() => true, () => false);
    const painted = () => page.evaluate(() => {
      const cv = document.querySelector('.ae-wave'), g = cv.getContext('2d'), d = g.getImageData(0, 0, cv.width, cv.height).data;
      let n = 0; for (let i = 3; i < d.length; i += 4 * 7) if (d[i] > 0) n++; return n;
    });
    check('a sound opens as a player, with its length', await page.waitForFunction(() => !!document.querySelector('.ae-player') && /0:08 · stereo/.test(document.querySelector('.ae-info')?.textContent || ''), null, { timeout: 15000 }).then(() => true, () => false), await info().catch(() => ''));
    check('the player draws the waveform', await painted() > 1000);
    check('the player has no edit tools, only an Edit button', await page.locator('.ae-bar').count() === 0 && await page.locator('.ae-editbtn').count() === 1);
    let box = await page.locator('.ae-wave').boundingBox();
    await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
    check('clicking the waveform moves the playhead there', /^0:04\.0\d\d/.test(await page.locator('.ae-clock').innerText()), await page.locator('.ae-clock').innerText());
    await page.locator('.ae-play').click();
    await page.waitForTimeout(500);
    check('Play plays from there, and turns into Pause', (await page.locator('.ae-play').getAttribute('aria-label')) === 'Pause' && !/^0:04\.0/.test(await page.locator('.ae-clock').innerText()), await page.locator('.ae-clock').innerText());
    await page.locator('.ae-play').click();
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'audio-player.png') });
    await page.locator('.ae-editbtn').click();
    check('Edit opens the wave editor, with the rate', await page.waitForFunction(() => !!document.querySelector('.ae-editor') && /0:08 · 44\.1 kHz · stereo/.test(document.querySelector('.ae-info')?.textContent || ''), null, { timeout: 15000 }).then(() => true, () => false), await info().catch(() => ''));
    check('the waveform is drawn', await painted() > 1000);
    box = await page.locator('.ae-wave').boundingBox();
    const at = (f, y = 0.6) => [box.x + box.width * f, box.y + box.height * y];
    const menu = async (name, item) => { await page.locator('.ae-b.ae-menu', { hasText: name }).click(); await page.locator('.menu-ctx .mi', { hasText: item }).click(); };
    await page.mouse.move(...at(0.25)); await page.mouse.down(); await page.mouse.move(...at(0.4), { steps: 5 }); await page.mouse.move(...at(0.5), { steps: 5 }); await page.mouse.up();
    check('dragging selects a part', /Selected 0:02\.0\d\d – 0:0[34]\.\d{3}/.test(await page.locator('.ae-selinfo').innerText()), await page.locator('.ae-selinfo').innerText());
    await page.locator('.ae-b[aria-label^="Delete"]').click();
    check('Delete takes it out', await waitStatus(/Delete 2\.0/) && /0:06/.test(await info()) && /not saved/.test(await info()), await status() + ' | ' + await info());
    check('the tab shows unsaved changes', await page.locator('.tab.active.dirty').count() === 1);
    await page.locator('.ae-wave').focus();
    await page.keyboard.press('Control+z');
    check('Ctrl+Z undoes it', await waitStatus(/Undid Delete/) && /0:08/.test(await info()), await info());
    await page.keyboard.press('Control+Shift+z');
    check('Ctrl+Shift+Z redoes it', await waitStatus(/Redid Delete/) && /0:06/.test(await info()), await info());
    await menu('Volume', 'Louder or quieter');
    await page.locator('.dialog .btn', { hasText: 'Apply' }).click();
    check('Volume changes the whole sound when nothing is selected', await waitStatus(/Volume \+3 dB/), await status());
    await menu('Effects', 'Phaser');
    check('an effect opens a window with an amount', await page.locator('.dialog input[type=range]').count() === 1);
    await page.locator('.dialog .btn', { hasText: 'Apply' }).click();
    check('an effect applies', await waitStatus(/Phaser/), await status());
    await menu('Volume', 'Normalize');
    await page.locator('.dialog .btn', { hasText: 'Apply' }).click();
    check('Normalize brings the peak to -1 dB', await waitStatus(/Normalize to -1 dB peak/), await status());
    await page.locator('.ae-play').click();
    await page.waitForTimeout(500);
    check('Play plays in the editor too', (await page.locator('.ae-play').getAttribute('aria-label')) === 'Pause', await page.locator('.ae-clock').innerText());
    await page.locator('.ae-wave').focus(); await page.keyboard.press('Space');
    check('Space pauses', (await page.locator('.ae-play').getAttribute('aria-label')) === 'Play');
    await menu('Edit', 'Select all');
    check('Edit > Select all selects the whole sound', /Selected 0:00\.000 – 0:06\.\d{3}/.test(await page.locator('.ae-selinfo').innerText()), await page.locator('.ae-selinfo').innerText());
    await menu('Edit', 'Select nothing');
    check('Edit > Select nothing clears it', /Nothing selected/.test(await page.locator('.ae-selinfo').innerText()), await page.locator('.ae-selinfo').innerText());
    await page.locator('.ae-b', { hasText: 'Fit' }).click();
    await page.mouse.move(...at(0.02)); await page.mouse.down(); await page.mouse.move(...at(0.1, 0.6), { steps: 4 }); await page.mouse.move(...at(0.18), { steps: 4 }); await page.mouse.up();
    box = await page.locator('.ae-wave').boundingBox();             // again: the layout may have moved since (fonts differ between machines)
    const grabY = box.y + 20 + 7;                                    // the middle of the grab band under the ruler (AE_RULER 20, AE_GRAB 14)
    await page.mouse.move(box.x + box.width * 0.1, grabY);
    const grabCursor = await page.evaluate(() => getComputedStyle(document.querySelector('.ae-wave canvas') || document.querySelector('.ae-wave')).cursor);
    await page.mouse.down(); await page.mouse.move(box.x + box.width * 0.5, grabY, { steps: 6 }); await page.mouse.move(box.x + box.width * 0.75, grabY, { steps: 6 }); await page.mouse.up();
    check('dragging the bar on top of a selection moves that part', await waitStatus(/Move (0\.9|1\.0)\d s/) && /0:06/.test(await info()), `${await status()} (cursor before the drag: ${grabCursor})`);
    check('the selection follows the part to its new place', /Selected 0:0[34]\./.test(await page.locator('.ae-selinfo').innerText()), await page.locator('.ae-selinfo').innerText());
    await page.keyboard.press('Escape');
    await page.mouse.click(...at(0.5));
    await page.keyboard.press('m');
    check('M puts a cut mark at the cursor', /1 mark/.test(await status()), await status());
    if (process.env.STUDIO_SHOTS) for (const [theme, label] of [['dark', 'Dark'], ['light', 'Light'], ['sepia', 'Sepia']]) {
      await page.click('#btn-theme'); await page.click(`.menu-ctx .mi:has-text("${label}")`);
      await page.mouse.move(...at(0.6)); await page.mouse.down(); await page.mouse.move(...at(0.8), { steps: 4 }); await page.mouse.up();
      await page.waitForTimeout(200);
      await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, `audio-${theme}.png`) });
    }
    if (process.env.STUDIO_SHOTS) { await page.click('#btn-theme'); await page.click('.menu-ctx .mi:has-text("Dark")'); }
    await menu('Slices', 'Save the slices');
    await page.locator('.dialog .btn', { hasText: 'Save' }).click();
    check('Save slices writes the parts as files beside the sound', await waitStatus(/Saved 2 slices/)
          && fs.existsSync(path.join(snd, 'sfx', 'bell-01.mp3')) && fs.existsSync(path.join(snd, 'sfx', 'bell-02.mp3')), await status());
    await page.reload();
    check('after a reload the tab is still in the editor, with its undo history', await page.waitForFunction(() => !!document.querySelector('.ae-editor') && /0:06/.test(document.querySelector('.ae-info')?.textContent || '')
          && !document.querySelector('.ae-bar .ae-b[aria-label^="Undo"]')?.disabled, null, { timeout: 15000 }).then(() => true, () => false), await info().catch(() => ''));
    const before = fs.statSync(path.join(snd, 'sfx', 'bell.mp3')).size;
    await page.locator('.ae-wave').focus();
    await page.keyboard.press('Control+s');
    check('Ctrl+S saves over the file and clears the unsaved mark', await waitStatus(/Saved sfx\/bell\.mp3/) && fs.statSync(path.join(snd, 'sfx', 'bell.mp3')).size !== before
          && await page.locator('.tab.active.dirty').count() === 0 && !/not saved/.test(await info()), await status());
    await page.locator('.ae-b', { hasText: 'Done' }).click();
    check('Done goes back to the player, with the saved sound', await page.waitForFunction(() => !!document.querySelector('.ae-player') && /0:06 · stereo/.test(document.querySelector('.ae-info')?.textContent || ''), null, { timeout: 15000 }).then(() => true, () => false), await info().catch(() => ''));
    await page.reload();
    check('and stays a player after a reload', await page.waitForFunction(() => !!document.querySelector('.ae-player'), null, { timeout: 15000 }).then(() => true, () => false));
    await page.locator('.ae-editbtn').click();
    await page.waitForSelector('.ae-editor .ae-info:has-text("0:06")', { timeout: 15000 }).catch(() => {});
    await page.mouse.move(...at(0.1)); await page.mouse.down(); await page.mouse.move(...at(0.3), { steps: 4 }); await page.mouse.up();
    await page.locator('.ae-b[aria-label^="Delete"]').click();
    await waitStatus(/Delete/);
    await page.locator('.ae-b', { hasText: 'Done' }).click();
    check('Done with unsaved changes asks first', await page.locator('.dialog', { hasText: 'Unsaved changes' }).count() === 1);
    await page.locator('.dialog .btn', { hasText: "Don't save" }).click();
    check("Don't save leaves the file as it was", await page.waitForFunction(() => !!document.querySelector('.ae-player') && /0:06/.test(document.querySelector('.ae-info')?.textContent || ''), null, { timeout: 15000 }).then(() => true, () => false)
          && await page.locator('.tab.active.dirty').count() === 0, await info().catch(() => ''));
    await page.click('.act[data-view="media"]');
    await page.waitForSelector('#media-body .media-row');
    await page.locator('#media-body .media-row', { hasText: 'bell-01.mp3' }).locator('button', { hasText: 'Edit' }).click();
    check('a sound in Media has Edit, which opens it in the editor', await page.waitForFunction(() => document.querySelector('.tab.active')?.textContent.includes('bell-01.mp3') && !!document.querySelector('.ae-editor'), null, { timeout: 15000 }).then(() => true, () => false));
    await page.waitForFunction(() => /0:0\d/.test(document.querySelector('.ae-editor .ae-info')?.textContent || ''), null, { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(3500);           // the watcher has seen the file once
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'sine=frequency=550:duration=2', '-c:a', 'libmp3lame', path.join(snd, 'sfx', 'bell-01.mp3')]);
    check("an open editor with nothing unsaved picks up a change on disk (from the agent's tools)", await page.waitForFunction(() => /0:02/.test(document.querySelector('.ae-editor .ae-info')?.textContent || '')
          && /Changed on disk/.test(document.querySelector('.ae-status')?.textContent || ''), null, { timeout: 15000 }).then(() => true, () => false), await page.locator('.ae-status').innerText().catch(() => ''));
    await page.click('.act[data-view="explorer"]');
    await page.setViewportSize({ width: 390, height: 800 });
    await page.waitForTimeout(400);
    await page.click('#mobile-tabs button[data-tab="editor"]');
    await page.waitForTimeout(500);
    const phone = await page.evaluate(() => ({ wide: document.documentElement.scrollWidth <= innerWidth + 1, wave: document.querySelector('.ae-wave')?.getBoundingClientRect().height || 0,
      bar: document.querySelector('.ae-bar')?.getBoundingClientRect().height || 0, play: document.querySelector('.ae-play')?.getBoundingClientRect().bottom || 0 }));
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'audio-phone.png') });
    check('on a phone the editor fits: one toolbar row, a tall waveform, play on screen', phone.wide && phone.wave >= 200 && phone.bar < 50 && phone.play > 0 && phone.play <= 800, JSON.stringify(phone));
    await page.locator('.ae-b', { hasText: 'Done' }).click();
    await page.waitForSelector('.ae-player', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(300);
    const pl = await page.evaluate(() => ({ wide: document.documentElement.scrollWidth <= innerWidth + 1, wave: document.querySelector('.ae-wave')?.getBoundingClientRect().height || 0 }));
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'audio-player-phone.png') });
    check('the player fits a phone too', pl.wide && pl.wave >= 120, JSON.stringify(pl));
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.waitForTimeout(300);
  }

  section('markdown files');
  {
    const docs = project('mddocs', { 'guide.md': '# Field guide\n\nSome **bold** words and a [link to notes](notes/other.md) and [down below](#the-end).\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n![a picture](pics/dot.png)\n\n' + 'A long paragraph to scroll past.\n\n'.repeat(60) + '## The end\n\nDone.\n' });
    fs.mkdirSync(path.join(docs, 'notes')); fs.writeFileSync(path.join(docs, 'notes', 'other.md'), '# Other notes\n');
    fs.mkdirSync(path.join(docs, 'pics'));
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=40x20', '-frames:v', '1', path.join(docs, 'pics', 'dot.png')]);
    await openProject(docs);
    if (await page.locator('.act.active[data-view="explorer"]').count() === 0) await page.click('.act[data-view="explorer"]');
    check('the Include current file chip is gone', await page.locator('#ctx-toggle').count() === 0 && await page.locator('#plan-chip').isVisible() && await page.locator('#goal-chip').isVisible());
    await page.locator('.t-row', { hasText: 'guide.md' }).dblclick();
    check('a Markdown file opens as a document', await page.waitForSelector('#pane-preview .md-doc h1', { timeout: 8000 }).then(async h => (await h.innerText()) === 'Field guide', () => false)
          && await page.locator('.md-doc strong', { hasText: 'bold' }).count() === 1 && await page.locator('.md-doc table').count() === 1 && await page.locator('.md-doc li').count() === 2);
    check('its pictures load from the folder', await page.waitForFunction(() => document.querySelector('.md-doc img')?.naturalWidth === 40, null, { timeout: 8000 }).then(() => true, () => false));
    check('the bar counts the words', /\d+ words · \d+ min read/.test(await page.locator('.md-bar .md-count').innerText()), await page.locator('.md-bar').innerText());
    await page.locator('.md-doc a', { hasText: 'down below' }).click();
    check('a #heading link scrolls to it', await page.waitForFunction(() => { const h = document.querySelector('#md-the-end'), r = h?.getBoundingClientRect(), s = document.querySelector('.md-scroll').getBoundingClientRect(); return h && document.querySelector('.md-scroll').scrollTop > 200 && r.top >= s.top - 1 && r.bottom <= s.bottom + 1; }, null, { timeout: 4000 }).then(() => true, () => false));
    await page.locator('.md-doc a', { hasText: 'link to notes' }).click();
    check('a link to another file opens it', await page.waitForFunction(() => document.querySelector('.tab.active')?.textContent.includes('other.md') && document.querySelector('.md-doc h1')?.textContent === 'Other notes', null, { timeout: 8000 }).then(() => true, () => false));
    await page.locator('.tab', { hasText: 'guide.md' }).click();
    await page.locator('.md-bar .btn', { hasText: 'Edit' }).click();
    check('Edit shows the Markdown text in the code editor', await page.waitForFunction(() => document.querySelector('#pane-preview')?.classList.contains('hidden') && document.querySelector('#btn-preview span')?.textContent === 'Preview', null, { timeout: 8000 }).then(() => true, () => false));
    if (await page.evaluate(() => !!window.monaco)) {
      await page.click('.monaco-editor .view-lines'); await page.keyboard.press('Control+End'); await page.keyboard.type('\nNew line here.');
      await page.click('#btn-preview');
      check('Preview shows unsaved edits too', await page.waitForFunction(() => /New line here/.test(document.querySelector('.md-doc')?.textContent || '') && /unsaved/.test(document.querySelector('.md-bar')?.textContent || ''), null, { timeout: 8000 }).then(() => true, () => false));
      if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'markdown.png') });
      await page.keyboard.press('Control+s');
      await page.waitForTimeout(500);
    }
  }

  section('office documents and the code map');
  {
    const office = project('office', { 'code.js': 'const a = 1;\n'.repeat(80) });
    // Word pages, Page view and the PDF made from Word need LibreOffice and bubblewrap; the code map and sheets do not
    const hasOffice = (() => { try { execFileSync('sh', ['-c', 'command -v bwrap && { command -v soffice || command -v libreoffice; }'], { stdio: 'ignore' }); return true; } catch { return false; } })();
    execFileSync('python3', ['-c', `import sys; sys.path.insert(0, sys.argv[1]); import studio, os
d = sys.argv[2]
open(os.path.join(d, 'letter.docx'), 'wb').write(studio.markdown_docx('# Επιστολή\\n\\nΚαλημέρα σας.\\n\\n\\\\pagebreak\\n\\n## Δεύτερη σελίδα\\n', 'Επιστολή'))
open(os.path.join(d, 'book.xlsx'), 'wb').write(studio.sheets_xlsx([{'name': 'Έσοδα', 'rows': [['Μήνας', 'Ποσό'], ['Νοέμβριος', 1200], ['Σύνολο', '=SUM(B2)']]}, {'name': 'Έξοδα', 'rows': [['Είδος'], ['Χαρτί']]}]))
if sys.argv[3] == '1': open(os.path.join(d, 'paper.pdf'), 'wb').write(studio.office_convert(os.path.join(d, 'letter.docx'), 'pdf'))`, ROOT, office, hasOffice ? '1' : '0']);
    await openProject(office);
    if (await page.locator('.act.active[data-view="explorer"]').count() === 0) await page.click('.act[data-view="explorer"]');
    await page.locator('.t-row', { hasText: 'code.js' }).dblclick();
    if (await page.waitForSelector('.monaco-editor .minimap', { timeout: 15000 }).then(() => true, () => false)) {
      const mapShown = () => page.evaluate(() => { const m = document.querySelector('.monaco-editor .minimap'); return !!m && m.getBoundingClientRect().width > 10 && getComputedStyle(m).display !== 'none'; });
      check('the code map shows beside the editor at first', await mapShown() && await page.getAttribute('#btn-minimap', 'aria-pressed') === 'true');
      await page.click('#btn-minimap');
      check('its button hides it', await page.waitForFunction(() => { const m = document.querySelector('.monaco-editor .minimap'); return !m || m.getBoundingClientRect().width < 10 || getComputedStyle(m).display === 'none'; }, null, { timeout: 4000 }).then(() => true, () => false)
        && await page.getAttribute('#btn-minimap', 'aria-pressed') === 'false' && await page.evaluate(() => localStorage.getItem('studio.minimap')) === '0');
      await page.keyboard.press('Alt+m');
      check('and Alt+M shows it again', await page.waitForFunction(() => document.querySelector('.monaco-editor .minimap')?.getBoundingClientRect().width > 10, null, { timeout: 4000 }).then(() => true, () => false));
    } else check('Monaco loaded for the code map checks', false, 'no minimap');
    const pagesDrawn = () => page.waitForFunction(() => { const c = document.querySelector('#pane-preview .pdf-slot canvas'); return c && c.width > 100 && Number(document.querySelector('#pane-preview')?.dataset.pages) >= 1; }, null, { timeout: 30000 }).then(() => true, () => false);
    if (!hasOffice) check('(LibreOffice or bubblewrap missing: the Word, Page view and PDF checks were skipped)', true);
    else {
    await page.locator('.t-row', { hasText: 'letter.docx' }).dblclick();
    check('a Word file opens as its pages', await pagesDrawn() && await page.evaluate(() => Number(document.querySelector('#pane-preview').dataset.pages)) === 2, await page.textContent('#pane-preview'));
    check('with selectable text', await page.waitForFunction(() => /Καλημέρα/.test(document.querySelector('#pane-preview .textLayer')?.textContent || ''), null, { timeout: 8000 }).then(() => true, () => false));
    check('and page, zoom and download controls', await page.locator('#pane-preview .pdf-page').count() === 1 && /of 2/.test(await page.textContent('#pane-preview .pdf-info'))
      && await page.locator('#pane-preview button[title="Fit the page width"]').count() === 1);
    const before = await page.textContent('#pane-preview .pdf-zoom-text');
    await page.click('#pane-preview button[title="Larger"]');
    check('Larger zooms in', parseInt(await page.textContent('#pane-preview .pdf-zoom-text')) > parseInt(before), before);
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'office-word.png') });
    }
    await page.locator('.t-row', { hasText: 'book.xlsx' }).dblclick();
    check('a workbook opens as sheets of cells', await page.waitForSelector('#pane-preview .sheet-grid td', { timeout: 15000 }).then(() => true, () => false)
      && await page.locator('#pane-preview .sheet-tab').count() === 2 && (await page.textContent('#pane-preview .sheet-grid')).includes('Νοέμβριος'));
    check('with column letters and row numbers', (await page.textContent('#pane-preview .sheet-grid thead')).includes('B') && await page.locator('#pane-preview .sg-row', { hasText: '3' }).count() >= 1);
    await page.locator('#pane-preview .sheet-tab', { hasText: 'Έξοδα' }).click();
    check('its sheets are tabs', (await page.textContent('#pane-preview .sheet-grid')).includes('Χαρτί'));
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'office-excel.png') });
    if (hasOffice) {
      await page.click('#pane-preview button:has-text("Page view")');
      check('Page view shows it as printed pages', await pagesDrawn());
      await page.locator('.t-row', { hasText: 'paper.pdf' }).dblclick();
      check('a PDF is drawn in Studio itself', await pagesDrawn() && await page.locator('#pane-preview iframe').count() === 0);
    }
  }

  section('Codex as the chat agent (a fake codex app-server)');
  {
    const cx = project('codexcalc', { 'calc.py': 'def add(a, b):\n    return a - b\n\nprint(add(2, 3))\n' });
    await openProject(cx);
    check('the picker has a Codex group for admins', await page.locator('#model-select optgroup[label="Codex (ChatGPT)"] option').count() === 2
      && (await page.locator('#model-select option[value="codex:mock-big"]').innerText()).includes('Mock Big · Codex'));
    check('a status-bar pill shows Codex usage for the session and the week', await page.waitForFunction(() => /Codex 1\d% · week 40%/.test(document.querySelector('#sb-codex-text')?.textContent || ''), null, { timeout: 15000 }).then(() => true, () => false),
      await page.textContent('#sb-codex-text'));
    const before = await page.inputValue('#model-select');
    check('the chat shows no Codex usage while another model is chosen', await page.locator('#cx-strip').isHidden());
    await page.selectOption('#model-select', 'codex:mock-big');
    check('with a Codex model the chat shows both usage bars under its header', await page.waitForSelector('#cx-strip:not(.hidden) .cx-sw', { timeout: 5000 }).then(() => true, () => false)
      && await page.locator('#cx-strip .cx-sw').count() === 2 && /40% · week · resets/.test(await page.textContent('#cx-strip')) && /% · 5h · resets/.test(await page.textContent('#cx-strip')));
    await page.click('#cx-strip');
    check('and a tap opens the usage panel', await page.waitForSelector('#codex-usage .cx-row', { timeout: 5000 }).then(() => true, () => false));
    await page.mouse.click(5, 300);
    await page.selectOption('#model-select', before);
    await page.click('#sb-codex');
    check('with bars and reset times in its panel', await page.waitForSelector('#codex-usage .cx-row', { timeout: 5000 }).then(() => true, () => false)
      && await page.locator('#codex-usage .cx-row').count() === 2 && /This session \(5 hours\)/.test(await page.textContent('#codex-usage')) && /This week/.test(await page.textContent('#codex-usage'))
      && /resets/.test(await page.textContent('#codex-usage')) && !/null/.test(await page.textContent('#codex-usage')) && await page.locator('#codex-usage .cx-row').nth(1).locator('.cx-fill').evaluate(n => n.style.width) === '40%');
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'codex-usage.png') });
    await page.mouse.click(5, 300);
    await page.click('#btn-account');
    await page.click('.ac-nav button[data-section="codex"]');
    check('Account & settings → Codex shows the account and its usage bars', await page.waitForSelector('.cx-who', { timeout: 10000 }).then(() => true, () => false)
      && (await page.textContent('.cx-who')).includes('tester@example.com') && await page.locator('.cx-account .cx-row').count() === 2);
    await page.click('.cx-account button:has-text("Sign out")');
    await page.locator('.dialog:not(.acct) button:has-text("Sign out")').last().click();
    check('Sign out signs Codex out', await page.waitForFunction(() => document.querySelector('.cx-who')?.textContent === 'Not signed in', null, { timeout: 10000 }).then(() => true, () => false));
    await page.click('.cx-way button:has-text("Get a code")');
    check('signing in with a code shows the link and the one-time code', await page.waitForSelector('.cx-code code:has-text("ABCD-1234")', { timeout: 10000 }).then(() => true, () => false)
      && await page.locator('.cx-steps a[href="https://auth.example/device"]').count() === 1);
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'codex-login.png') });
    check('and the section shows the account once the sign-in finishes', await page.waitForFunction(() => /tester@example.com/.test(document.querySelector('.cx-who')?.textContent || ''), null, { timeout: 15000 }).then(() => true, () => false));
    await page.click('.dialog.acct button:has-text("Close")').catch(() => {});
    await page.selectOption('#model-select', 'codex:mock-big');
    check('with a Codex model picked, an Effort chip shows its default', await page.locator('#effort-chip:not(.hidden)').count() === 1 && (await page.textContent('#effort-chip')).includes('Low'));
    await page.click('#effort-chip');
    await page.click('.menu-ctx .mi:has-text("Ultra")');
    check('and chooses the effort for this chat', await page.waitForFunction(() => /Ultra/.test(document.querySelector('#effort-chip')?.textContent || ''), null, { timeout: 5000 }).then(() => true, () => false));
    await page.click('#goal-chip');
    check('with Codex, the Goal window offers an allowance in tokens and a ceiling on the 5-hour usage', await page.waitForSelector('.goal-cx .goal-cx-tokens', { timeout: 5000 }).then(() => true, () => false)
      && await page.locator('.goal-cx .goal-cx-pct').count() === 1);
    await page.click('.dialog button:has-text("Cancel")');
    await page.selectOption('#model-select', 'keystone:strata');
    check('the Codex usage strip goes away when a Studio model is picked', await page.locator('#cx-strip.hidden').count() === 1);
    await page.click('#goal-chip');
    check('and the Goal window has no Codex allowance', await page.waitForSelector('.goal-form', { timeout: 5000 }).then(() => true, () => false) && await page.locator('.goal-cx').count() === 0);
    await page.click('.dialog button:has-text("Cancel")');
    check('a Strata model shows it too, with the Manager\'s default (Codex\'s Ultra is not one of its levels)', await page.locator('#effort-chip:not(.hidden)').count() === 1
      && (await page.textContent('#effort-chip')).includes('Medium'));
    await page.click('#effort-chip');
    check('offering None to Extra high', await page.locator('.menu-ctx .mi').filter({ hasText: /^(✓ )?(None|Low|Medium|High|Extra high)/ }).count() >= 5);
    await page.keyboard.press('Escape');
    await page.click('body', { position: { x: 5, y: 5 } }).catch(() => {});
    if (await page.locator('#model-select option[value="qwen2.5-coder:3b"]').count()) {
      await page.selectOption('#model-select', 'qwen2.5-coder:3b');
      check('an Ollama model does not show it', await page.locator('#effort-chip.hidden').count() === 1);
    }
    await page.selectOption('#model-select', 'codex:mock-big');
    await send('hi codex');
    check('a message goes to Codex and its reply shows', await page.waitForFunction(() => /Hello from mock Codex/.test(document.querySelector('#msgs')?.innerText || ''), null, { timeout: 15000 }).then(() => true, () => false));
    await page.waitForFunction(() => !document.querySelector('#agent-stop:not(.hidden)'), null, { timeout: 10000 }).catch(() => {});
    await send('please edit calc.py');
    check("Codex's patch asks with an edit card", await page.waitForSelector('.m-appr.pending .kind:has-text("Edit")', { timeout: 15000 }).then(() => true, () => false)
      && (await page.textContent('.m-appr.pending')).includes('2 files'));
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'codex-edit.png') });
    await page.click('.m-appr.pending button[data-act="accept"]');
    check('and its commands with a command card', await page.waitForSelector('.m-appr.pending .cmd:has-text("python3 calc.py")', { timeout: 15000 }).then(() => true, () => false));
    await page.click('.m-appr.pending button[data-act="accept"]');
    check('the run ends with its reply and the changed files', await page.waitForFunction(() => /Patch completed; the run was allowed/.test(document.querySelector('#msgs')?.innerText || ''), null, { timeout: 15000 }).then(() => true, () => false)
      && fs.readFileSync(path.join(cx, 'calc.py'), 'utf8').includes('a + b'));
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'codex-done.png') });
    await page.waitForFunction(() => !document.querySelector('#agent-stop:not(.hidden)'), null, { timeout: 10000 }).catch(() => {});
    await page.selectOption('#model-select', 'keystone:strata');
  }

  section('the Open folder dialog');
  {
    check('the top bar says where the open folder lives', await page.locator('#ws-place .place-server').count() === 1 && (await page.textContent('#ws-where')) === 'on the server');
    await page.click('#ws-btn');
    await page.waitForSelector('.of-dialog .dir-item', { timeout: 8000 }).catch(() => {});
    check('the dialog starts beside the open project and names places, not server paths', await page.locator('.of-dialog .crumb').count() >= 1
      && !(await page.textContent('.of-dialog .crumbs')).includes('/') && await page.locator('.of-dialog .dir-item .place').count() >= 1, await page.textContent('.of-dialog .crumbs'));
    const first = page.locator('.of-dialog .dir-item:not(.dir-up)').first();
    const name = (await first.locator('.nm').textContent()).trim();
    await first.click();
    check('a row goes into the folder, and the crumbs follow', await page.waitForFunction(n => document.querySelector('.of-dialog .crumb.here')?.textContent === n, name, { timeout: 5000 }).then(() => true, () => false)
      && (await page.textContent('.of-dialog .dlg-foot .btn.primary')).includes(name));
    check('a way back up is always there', await page.locator('.of-dialog .dir-up').count() === 1);
    check('a legend explains the place icons', (await page.textContent('.of-dialog .of-legend')).includes('Kept copy'));
    check('its sections are tabs: Your space, Shared with me, Recent, My computers', (await page.$$eval('.of-dialog .of-tab', t => t.map(x => x.textContent.replace(/\d+$/, '')))).join('|') === 'Your space|Shared with me|Recent|My computers');
    await page.click('.of-dialog .of-tab[data-tab="recent"]');
    check('a tab shows its section in the same space, without scrolling the window', await page.locator('.of-dialog .of-pane:not(.hidden) .recent-item').count() >= 1
      && await page.locator('.of-dialog .of-pane:not(.hidden) .dir-list').count() === 0 && await page.$eval('.of-dialog', d => d.scrollHeight <= d.clientHeight + 2));
    await page.click('.of-dialog .of-tab[data-tab="space"]');
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'open-folder.png') });
    await page.click('.of-dialog .dlg-foot .btn:has-text("Cancel")');
  }

  section('sharing a folder');
  {
    const { data: inv } = await api('/api/admin/invites', { role: 'user' });
    await fetch(url + '/api/auth/signup', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Studio': '1' }, body: JSON.stringify({ token: inv.token, username: 'pia', password: 'pia password 1' }) });
    await page.click('#ws-share');
    await page.waitForSelector('.sh-dialog .sh-add select', { timeout: 8000 });
    check('the Share window names the owner and says everyone works on the same files', /Owner/.test(await page.textContent('.sh-dialog .sh-owner')) && /same files/.test(await page.textContent('.sh-dialog')));
    const piaOption = await page.$eval('.sh-dialog .sh-add select', s => [...s.options].find(o => /pia/.test(o.value))?.value || '');
    if (piaOption) {
      await page.selectOption('.sh-dialog .sh-add select', piaOption);
      await page.click('.sh-dialog .sh-add button');
      check('a person is added with a switch for each right', await page.waitForSelector('.sh-person[data-user="pia"] input[data-right="files"]', { timeout: 5000 }).then(() => true, () => false)
        && await page.locator('.sh-person[data-user="pia"] input[type="checkbox"]').count() === 5
        && await page.locator('.sh-person[data-user="pia"] input[data-right="files"]').isChecked() && !(await page.locator('.sh-person[data-user="pia"] input[data-right="delete"]').isChecked()));
      await page.click('.sh-person[data-user="pia"] button:has-text("Co-owner")');
      check('Co-owner turns every right on', await page.waitForFunction(() => [...document.querySelectorAll('.sh-person[data-user="pia"] input[type="checkbox"]')].every(i => i.checked), null, { timeout: 5000 }).then(() => true, () => false)
        && /Co-owner/.test(await page.textContent('.sh-person[data-user="pia"] .sh-who')));
      if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'share.png') });
      await page.click('.sh-dialog .dlg-foot .btn:has-text("Close")');
      check('the top bar says the folder is shared, with how many people', await page.waitForFunction(() => /shared with 1/.test(document.querySelector('#ws-where')?.textContent || '') && document.querySelector('#ws-share-n')?.textContent === '2', null, { timeout: 8000 }).then(() => true, () => false),
        await page.textContent('#ws-where'));
      await page.click('#ws-share');
      await page.waitForSelector('.sh-person[data-user="pia"]');
      await page.click('.sh-person[data-user="pia"] button[aria-label="Remove pia"]');
      await page.locator('.dialog:not(.sh-dialog) button:has-text("Take out")').last().click();
      check('and a person can be taken out', await page.waitForFunction(() => !document.querySelector('.sh-person[data-user="pia"]'), null, { timeout: 5000 }).then(() => true, () => false));
    } else check('another person exists to share with', false, 'no pia');
    await page.click('.sh-dialog .dlg-foot .btn:has-text("Close")').catch(() => {});
  }

  section('choosing the models in the picker');
  {
    const groups = () => page.$$eval('#model-select optgroup', gs => gs.map(g => g.label));
    const before = await page.locator('#model-select option[value]:not([value=""]):not([value="__choose"])').count();
    await page.selectOption('#model-select', '__choose');
    check('"Choose models…" at the end of the picker opens Models in Account & settings', await page.waitForSelector('.ac-pane h3:has-text("Models")', { timeout: 5000 }).then(() => true, () => false)
      && await page.$eval('#model-select', s => s.value) === 'keystone:strata');
    await page.click('.mdl-group[data-group="codex"] button:has-text("None")');
    check('a group can be hidden from the picker', !(await groups()).includes('Codex (ChatGPT)') && (await page.textContent('#model-select option[value="__choose"]')).includes('hidden'), await groups());
    await page.locator('.mdl-row', { hasText: 'Mock Small' }).locator('input').check();
    check('or one model at a time', await page.locator('#model-select option[value="codex:mock-small"]').count() === 1 && await page.locator('#model-select option[value="codex:mock-big"]').count() === 0);
    check('the choice is saved for the person', await page.waitForFunction(async () => { const r = await fetch('/api/prefs', { headers: { 'X-Studio': '1' } }); const p = (await r.json()).prefs;
      return Array.isArray(p.models_shown) && p.models_shown.includes('codex:mock-small') && !p.models_shown.includes('codex:mock-big'); }, null, { timeout: 5000 }).then(() => true, () => false));
    check('an admin sees the connected model server at the top of Models', await page.waitForSelector('.msrv .msrv-dot.ok', { timeout: 8000 }).then(() => true, () => false)
      && (await page.textContent('.msrv-now')).includes(`127.0.0.1:${gwPort}`), await page.textContent('.msrv-now').catch(() => ''));
    await page.fill('.msrv-addr', 'http://127.0.0.1:9');
    await page.click('.msrv-acts button:has-text("Test")');
    check('testing an address where nothing answers says so, and changes nothing', await page.waitForSelector('.msrv-result.err', { timeout: 8000 }).then(() => true, () => false)
      && (await page.textContent('.msrv-result')).includes('Nothing answers'), await page.textContent('.msrv-result'));
    const renameRow = page.locator('.mdl-row', { hasText: 'llama3.2:3b' });
    await renameRow.locator('.mdl-rename').click();
    await page.fill('.dialog.sm .dlg-input', 'Little Llama');
    await page.click('.dialog.sm .btn.primary');
    check('an admin can give a model the name everyone sees', await page.waitForFunction(() => [...document.querySelectorAll('#model-select option')].some(o => o.textContent.startsWith('Little Llama')), null, { timeout: 5000 }).then(() => true, () => false));
    await renameRow.locator('.mdl-rename').click();
    await page.fill('.dialog.sm .dlg-input', '');
    await page.click('.dialog.sm .btn.primary');
    check('and an empty name gives it its own name back', await page.waitForFunction(() => ![...document.querySelectorAll('#model-select option')].some(o => o.textContent.startsWith('Little Llama')), null, { timeout: 5000 }).then(() => true, () => false));
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'models-pane.png') });
    await page.click('.ac-pane button:has-text("Show all")');
    check('Show all brings every model back', await page.locator('#model-select option[value]:not([value=""]):not([value="__choose"])').count() === before && (await groups()).includes('Codex (ChatGPT)'));
    await page.keyboard.press('Escape');
    await page.click('.dialog.acct button:has-text("Close")').catch(() => {});
  }

  section('previews');
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAoAAAAKCAYAAACNMs+9AAAAFUlEQVR42mNk+M9Qz0AEYBxVSF+FAP0ZBQEjkqhgAAAAAElFTkSuQmCC', 'base64');
  const site = project('site', { 'index.html': '<!doctype html><link rel="stylesheet" href="style.css"><h1 id="t">old title</h1><script src="app.js"></script>', 'style.css': 'h1{color:rgb(1,2,3)}', 'app.js': 'document.title="ran"; document.body.dataset.ran="1";', 'doc.pdf': '%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n' });
  fs.writeFileSync(path.join(site, 'pic.png'), png);
  await openProject(site);
  if (await page.locator('.act.active[data-view="explorer"]').count() === 0) await page.click('.act[data-view="explorer"]');
  await page.locator('.t-row', { hasText: 'pic.png' }).dblclick();
  await page.waitForSelector('#pane-preview .iv-stage .iv-img');
  await page.waitForFunction(() => document.querySelector('#pane-preview .iv-img')?.naturalWidth === 10, null, { timeout: 8000 }).then(() => check('an image opens as a picture', true)).catch(() => check('an image opens as a picture', false));
  check('with no Preview button, since it has no code view', !(await page.locator('#btn-preview').isVisible()));
  {
    const pct = async () => parseInt(await page.locator('#pane-preview .iv-pct').innerText());
    const box = await page.locator('#pane-preview .iv-stage').boundingBox(), before = await pct();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await page.mouse.wheel(0, -300); await page.waitForTimeout(150);
    const wheeled = await pct();
    check('a project picture zooms with the mouse wheel', wheeled > before, `${before} -> ${wheeled}`);
    // two fingers moving apart: pointer events as a touch screen sends them
    await page.evaluate(({ x, y }) => {
      const s = document.querySelector('#pane-preview .iv-stage'), ev = (type, id, px) => s.dispatchEvent(new PointerEvent(type, { pointerId: id, pointerType: 'touch', clientX: px, clientY: y, button: 0, bubbles: true }));
      ev('pointerdown', 11, x - 20); ev('pointerdown', 12, x + 20);
      for (let i = 1; i <= 5; i++) { ev('pointermove', 11, x - 20 - i * 16); ev('pointermove', 12, x + 20 + i * 16); }
      ev('pointerup', 11, x - 100); ev('pointerup', 12, x + 100);
    }, { x: box.x + box.width / 2, y: box.y + box.height / 2 });
    const pinched = await pct();
    check('and with a pinch on touch screens', pinched > wheeled * 2.5, `${wheeled} -> ${pinched}`);
  }
  const [pdfResponse] = await Promise.all([page.waitForResponse(r => r.url().includes('doc.pdf') && r.url().includes('/preview/')), page.locator('.t-row', { hasText: 'doc.pdf' }).dblclick()]);
  check('a PDF that pdf.js cannot read still gets the browser viewer', pdfResponse.status() === 200 && (pdfResponse.headers()['content-type'] || '') === 'application/pdf'
    && await page.waitForSelector('#pane-preview iframe', { timeout: 15000 }).then(() => true, () => false), await page.textContent('#pane-preview'));
  await page.locator('.t-row', { hasText: 'index.html' }).dblclick();
  await page.waitForFunction(() => window.monaco, null, { timeout: 20000 });
  await page.waitForSelector('#btn-preview:not(.hidden)');
  check('a page opens as code first', await page.locator('#pane-monaco:not(.hidden)').count() === 1);
  await page.click('#btn-preview');
  await page.waitForSelector('#pane-preview iframe');
  const frame = () => page.frames().find(f => f.url().includes('/preview/') && f.url().includes('index.html'));
  await page.waitForFunction(() => true);
  for (let i = 0; i < 40 && !frame(); i++) await new Promise(r => setTimeout(r, 100));
  await frame().waitForSelector('h1');
  await frame().waitForFunction(() => document.body.dataset.ran === '1' && getComputedStyle(document.querySelector('h1')).color === 'rgb(1, 2, 3)', null, { timeout: 8000 }).then(() => check('the page renders with its own CSS and script', true)).catch(async () => check('the page renders with its own CSS and script', false, await frame().evaluate(() => JSON.stringify({ ran: document.body.dataset.ran, color: getComputedStyle(document.querySelector('h1')).color }))));
  const errorsBeforeAttack = errors.length;
  const attack = await frame().evaluate(async () => {
    const out = {};
    try { out.cookie = document.cookie; } catch (e) { out.cookie = 'blocked'; }
    try { out.parent = String(window.parent.document.title); } catch (e) { out.parent = 'blocked'; }
    try { out.storage = String(localStorage.length); } catch (e) { out.storage = 'blocked'; }
    try { const r = await fetch('/api/config'); out.api = r.status + ':' + (await r.text()).slice(0, 40); } catch (e) { out.api = 'blocked'; }
    try { const r = await fetch('/api/config', { credentials: 'include' }); out.apiCreds = r.status; } catch (e) { out.apiCreds = 'blocked'; }
    try { const r = await fetch('https://example.com/'); out.net = r.status; } catch (e) { out.net = 'blocked'; }
    out.origin = self.origin;
    return out;
  });
  await new Promise(r => setTimeout(r, 200)); errors.length = errorsBeforeAttack;     // the blocked requests above log errors, as they should
  check('the page has its own empty origin', attack.origin === 'null', JSON.stringify(attack));
  check('it cannot read Studio login cookies, storage or the page around it', attack.cookie === 'blocked' && attack.parent === 'blocked' && attack.storage === 'blocked', JSON.stringify(attack));
  check('it cannot call Studio as you', attack.api === 'blocked' && attack.apiCreds === 'blocked', JSON.stringify(attack));
  check('it cannot reach the internet', attack.net === 'blocked', JSON.stringify(attack));
  fs.writeFileSync(path.join(site, 'index.html'), '<!doctype html><h1 id="t">new title</h1>');
  const wsId = await page.evaluate(() => JSON.parse(localStorage.getItem('studio.ws')).id);
  await api('/api/files/write', { ws: wsId, path: 'index.html', content: '<!doctype html><h1 id="t">new title</h1>' });
  await page.locator('#pane-preview button:has-text("Reload")').click();
  await new Promise(r => setTimeout(r, 800));
  await frame().waitForFunction(() => document.querySelector('h1')?.textContent === 'new title', null, { timeout: 8000 }).then(() => check('Reload shows the new version', true)).catch(() => check('Reload shows the new version', false));
  await page.click('#btn-preview');
  check('the button goes back to the code', await page.locator('#pane-monaco:not(.hidden)').count() === 1);

  section('many tabs');
  {
    const files = {}; for (let i = 1; i <= 14; i++) files[`file-${String(i).padStart(2, '0')}-with-a-longer-name.txt`] = `text ${i}\n`;
    await openProject(project('tabs', files));
    if (await page.locator('.act.active[data-view="explorer"]').count() === 0) await page.click('.act[data-view="explorer"]');
    for (const f of Object.keys(files)) { await page.locator('.t-row', { hasText: f }).dblclick(); await page.waitForSelector(`.tab.active:has-text("${f}")`); }
    const names = async () => page.locator('#tabs .tab .t-label').allInnerTexts();
    check('tabs that do not fit get a thin scrollbar', await page.locator('.tabs-scroll:not(.hidden) .tabs-thumb').isVisible()
          && (await page.locator('.tabs-scroll .tabs-thumb').boundingBox()).height <= 4);
    await page.evaluate(() => { document.querySelector('#tabs').scrollLeft = 0; });
    const bar = await page.locator('#tabs').boundingBox();
    await page.mouse.move(bar.x + 100, bar.y + 15); await page.mouse.wheel(0, 300); await page.waitForTimeout(100);
    check('the mouse wheel scrolls them sideways', await page.evaluate(() => document.querySelector('#tabs').scrollLeft) > 100);
    const track = await page.locator('.tabs-scroll').boundingBox();
    await page.mouse.click(track.x + track.width - 3, track.y + track.height / 2);
    check('pressing the end of the scrollbar shows the last tabs', await page.evaluate(() => { const b = document.querySelector('#tabs'); return b.scrollLeft >= b.scrollWidth - b.clientWidth - 2; }));
    await page.evaluate(() => { document.querySelector('#tabs').scrollLeft = 0; });
    const before = await names();
    const first = await page.locator('#tabs .tab').nth(0).boundingBox(), fourth = await page.locator('#tabs .tab').nth(3).boundingBox();
    await page.mouse.move(first.x + 30, first.y + 15); await page.mouse.down();
    await page.mouse.move(first.x + 60, first.y + 15, { steps: 3 });
    check('a dragged tab shows where it will go', await page.locator('#tabs .tab-drop').count() === 1 && await page.locator('#tabs .tab.dragging').count() === 1);
    await page.mouse.move(fourth.x + fourth.width - 5, fourth.y + 15, { steps: 8 }); await page.mouse.up();
    const after = await names();
    check('and moves there when dropped', after[3] === before[0] && after[0] === before[1] && after.length === before.length, after.slice(0, 5).join(' | '));
    check('dropping is not also a click', await page.locator('#tabs .tab-drop').count() === 0 && await page.locator('.tab.dragging').count() === 0);
    await page.waitForTimeout(400);
    const kept = await page.evaluate(n => { for (const [k, v] of Object.entries(localStorage)) if (k.startsWith('studio.tabs.') && v.includes(n)) return JSON.parse(v).paths; return []; }, before[0]);
    check('the new order is kept with the open tabs', kept[3] === before[0], kept.slice(0, 5).join(' | '));
    // a touch screen: a quick swipe scrolls, a long press then a slide moves the tab
    const t2 = await page.locator('#tabs .tab').nth(1).boundingBox(), t5 = await page.locator('#tabs .tab').nth(5).boundingBox();
    const mid = await names();
    await page.evaluate(({ x, y, to }) => new Promise(done => {
      const tab = document.querySelectorAll('#tabs .tab')[1], ev = (type, px) => tab.dispatchEvent(new PointerEvent(type, { pointerId: 31, pointerType: 'touch', clientX: px, clientY: y, button: 0, bubbles: true }));
      ev('pointerdown', x);
      setTimeout(() => { for (let i = 1; i <= 6; i++) ev('pointermove', x + (to - x) * i / 6); ev('pointerup', to); setTimeout(done, 50); }, 500);
    }), { x: t2.x + 20, y: t2.y + 15, to: t5.x + t5.width - 4 });
    const moved = await names();
    check('on touch screens a long press, then a slide, moves a tab', moved[5] === mid[1] && moved[1] === mid[2], moved.slice(0, 6).join(' | '));
    await page.evaluate(({ x, y }) => {
      const tab = document.querySelectorAll('#tabs .tab')[1], ev = (type, px) => tab.dispatchEvent(new PointerEvent(type, { pointerId: 32, pointerType: 'touch', clientX: px, clientY: y, button: 0, bubbles: true }));
      ev('pointerdown', x); for (let i = 1; i <= 6; i++) ev('pointermove', x - i * 20); ev('pointercancel', x - 120);
    }, { x: t2.x + 20, y: t2.y + 15 });
    check('while a quick swipe moves nothing', JSON.stringify(await names()) === JSON.stringify(moved) && await page.locator('#tabs .tab-drop').count() === 0);
    if (process.env.STUDIO_SHOTS) await page.screenshot({ path: path.join(process.env.STUDIO_SHOTS, 'many-tabs.png') });
    while (await page.locator('#tabs .tab').count()) await page.locator('#tabs .tab .t-close').first().click();
  }

  section('loading ghosts');
  const slowly = async (pattern, action, selector) => {
    let release; const gate = new Promise(r => { release = r; });
    await page.route(pattern, async route => { await gate; await route.continue().catch(() => {}); });
    await action();
    const shown = await page.waitForSelector(selector, { timeout: 4000 }).then(() => true).catch(() => false);
    release(); await page.unroute(pattern);
    return shown;
  };
  const ghostProject = project('ghosts', { 'a.txt': 'hello\n' });
  git(ghostProject, 'init', '-q');
  const opened = await page.evaluate(async p => (await (await fetch('/api/workspace/open', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Studio': '1' }, body: JSON.stringify({ path: p }) })).json()).workspace, ghostProject);
  await page.evaluate(ws => { localStorage.setItem('studio.ws', JSON.stringify(ws)); sessionStorage.setItem('studio.windowFolder', ws.path); }, opened);
  await page.goto('about:blank');
  let treeGate; const treeHeld = new Promise(r => { treeGate = r; });
  await page.route(/api\/files\/tree/, async route => { await treeHeld; await route.continue().catch(() => {}); });
  await page.goto(url); await page.waitForSelector('#app:not(.hidden)');
  check('the file tree shows a ghost while loading', await page.waitForSelector('#tree .skeleton', { timeout: 6000 }).then(() => true).catch(() => false));
  treeGate(); await page.unroute(/api\/files\/tree/);
  await page.waitForSelector('.t-row');
  check('and the ghost goes when it loads', await page.locator('#tree .skeleton').count() === 0);
  check('opening a file shows a ghost', await slowly(/api\/files\/read/, () => page.locator('.t-row', { hasText: 'a.txt' }).dblclick(), '.sk-editor'));
  await page.waitForFunction(() => !document.querySelector('.sk-editor'));
  await page.evaluate(() => { const b = document.querySelector('#tg-apply'); if (b.getAttribute('aria-checked') === 'false') b.click(); const c = document.querySelector('#tg-cmd'); if (c.getAttribute('aria-checked') === 'false') c.click(); });
  check('sending shows a ghost until the model answers', await slowly(/api\/agent\/stream/, () => send('hello there'), '#msgs .sk-ghost'));
  await ended();
  check('and it goes when the run ends', await page.locator('#msgs .skeleton').count() === 0);
  await page.click('#new-chat');
  await send('hello again'); await ended();
  await page.click('#sess-btn');
  check('opening another chat shows a ghost', await slowly(/api\/agent\/session\?/, () => page.locator('#sess-menu .mi').nth(1).click(), '#msgs .skeleton'));
  await page.waitForFunction(() => !document.querySelector('#msgs .skeleton'));
  check('opening source control shows a ghost', await slowly(/api\/git\/status/, () => page.click('.act[data-view="git"]'), '#git-body .skeleton'));
  await page.waitForFunction(() => !document.querySelector('#git-body .skeleton'));
  await page.click('.act[data-view="explorer"]');

  section('phones and tablets');
  const shots = process.env.STUDIO_SHOTS;
  const mobileDir = project('mobile', { 'README.md': '# Sample once\n', 'a-long-file-name-that-should-not-break-the-layout.txt': 'x\n', 'app.py': 'print("hello world from a file with quite a long line to check how the editor copes on a small screen")\n' });
  const wsMobile = (await api('/api/workspace/open', { path: mobileDir })).data.workspace;
  await api('/api/prefs', { key: 'last_ws', value: mobileDir });
  const handset = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, deviceScaleFactor: 2 });
  await handset.addCookies(await context.cookies());
  const phone = await handset.newPage();
  phone.on('pageerror', e => errors.push('phone: ' + e.message));
  await phone.goto(url); await phone.waitForSelector('#app:not(.hidden)');
  await phone.waitForFunction(() => document.querySelector('#ws-name')?.textContent === 'mobile', null, { timeout: 8000 });
  const fits = p => p.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1 && document.body.scrollWidth <= innerWidth + 1);
  check('a phone starts on the chat, full width', await phone.locator('#mobile-tabs').isVisible() && (await phone.locator('#agent').boundingBox()).width === 390);
  check('nothing runs off the side: chat', await fits(phone));
  if (shots) await phone.screenshot({ path: shots + '/phone-chat.png' });
  await phone.tap('#mt-files');
  await phone.waitForSelector('.t-row');
  check('Files is full width', (await phone.locator('#sidebar').boundingBox()).width === 390 && !(await phone.locator('#agent').isVisible()));
  check('nothing runs off the side: files', await fits(phone));
  check('rows are big enough to tap', (await phone.locator('.t-row').first().boundingBox()).height >= 36);
  await phone.locator('.t-row', { hasText: 'README.md' }).locator('.t-more').tap();
  check('a file has a menu without right click', await phone.locator('.menu-ctx').isVisible());
  if (shots) await phone.screenshot({ path: shots + '/phone-files.png' });
  await phone.locator('#sidebar .view-title').first().tap({ force: true }).catch(() => {});
  await phone.evaluate(() => document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })));
  await phone.locator('.t-row', { hasText: 'app.py' }).locator('.t-name').tap();
  await phone.waitForFunction(() => document.documentElement.dataset.section === 'editor');
  await phone.waitForSelector('#pane-monaco:not(.hidden)', { timeout: 30000 });
  await phone.waitForTimeout(600);
  check('tapping a file opens the Editor', (await phone.locator('#center').boundingBox()).width === 390);
  check('nothing runs off the side: editor', await fits(phone));
  check('no minimap on a phone', await phone.locator('.minimap').count() === 0 || !(await phone.locator('.minimap').first().isVisible()));
  if (shots) await phone.screenshot({ path: shots + '/phone-editor.png' });
  await phone.tap('#mt-terminal');
  await phone.waitForSelector('.xterm', { timeout: 15000 });
  await phone.waitForTimeout(400);
  check('Terminal fills the screen', (await phone.locator('#panel').boundingBox()).height > 500 && !(await phone.locator('#editor-area').isVisible()));
  check('nothing runs off the side: terminal', await fits(phone));
  await phone.locator('.xterm').tap();
  await phone.keyboard.type('seq 1 200\n');
  await phone.waitForFunction(() => document.querySelector('.xterm-rows')?.textContent.includes('200'), null, { timeout: 15000 }).catch(() => {});
  const swiped = await phone.evaluate(async () => {
    const host = document.querySelector('.xterm'), r = host.getBoundingClientRect(), x = r.left + r.width / 2;
    const touch = y => new Touch({ identifier: 1, target: host, clientX: x, clientY: y });
    const before = document.querySelector('.xterm-rows').textContent;
    host.dispatchEvent(new TouchEvent('touchstart', { touches: [touch(r.top + 100)], bubbles: true, cancelable: true }));
    for (let i = 1; i <= 10; i++) host.dispatchEvent(new TouchEvent('touchmove', { touches: [touch(r.top + 100 + i * 40)], bubbles: true, cancelable: true }));
    host.dispatchEvent(new TouchEvent('touchend', { touches: [], bubbles: true }));
    await new Promise(res => setTimeout(res, 300));
    return { moved: document.querySelector('.xterm-rows').textContent !== before, bar: !!document.querySelector('.term-thumb.on') };
  });
  check('a swipe scrolls the terminal back through its output, with a position bar', swiped.moved && swiped.bar, JSON.stringify(swiped));
  const glided = await phone.evaluate(async () => {
    const rows = () => document.querySelector('.term-inst:not(.hidden) .xterm-rows').textContent;
    const host = document.querySelector('.xterm'), r = host.getBoundingClientRect(), x = r.left + r.width / 2;
    const touch = y => new Touch({ identifier: 1, target: host, clientX: x, clientY: y });
    // The swipe above went back to the top, so this one goes forward (finger up).
    host.dispatchEvent(new TouchEvent('touchstart', { touches: [touch(r.top + 300)], bubbles: true, cancelable: true }));
    for (let i = 1; i <= 4; i++) { await new Promise(res => setTimeout(res, 16)); host.dispatchEvent(new TouchEvent('touchmove', { touches: [touch(r.top + 300 - i * 30)], bubbles: true, cancelable: true })); }
    host.dispatchEvent(new TouchEvent('touchend', { touches: [], bubbles: true }));
    const lifted = rows(), vp = document.querySelector('.term-inst:not(.hidden) .xterm-viewport'), top0 = vp.scrollTop;
    await new Promise(res => setTimeout(res, 500));
    return { glided: rows() !== lifted, top0, top1: vp.scrollTop };
  });
  check('a quick swipe keeps gliding after the finger lifts', glided.glided, JSON.stringify(glided));
  await phone.locator('.xterm').tap();
  await phone.keyboard.type('printf "%s" START; printf "x%.0s" $(seq 1 260); echo END\n');
  await phone.waitForTimeout(600);
  check('the terminal has a wrap button, on by default', await phone.locator('#term-wrap.on').isVisible());
  await phone.tap('#term-wrap');
  await phone.waitForTimeout(500);
  const sideways = await phone.evaluate(async () => {
    const sc = document.querySelector('.term-inst:not(.hidden) .term-scroll'), host = sc.querySelector('.xterm'), r = sc.getBoundingClientRect();
    const term = window.__studio && window.__studio.TM.active.xterm;
    const touch = x => new Touch({ identifier: 2, target: host, clientX: x, clientY: r.top + 120 });
    host.dispatchEvent(new TouchEvent('touchstart', { touches: [touch(r.left + 300)], bubbles: true, cancelable: true }));
    for (let i = 1; i <= 6; i++) host.dispatchEvent(new TouchEvent('touchmove', { touches: [touch(r.left + 300 - i * 40)], bubbles: true, cancelable: true }));
    host.dispatchEvent(new TouchEvent('touchend', { touches: [], bubbles: true }));
    await new Promise(res => setTimeout(res, 200));
    return { cols: term && term.cols, wide: sc.scrollWidth > sc.clientWidth, left: sc.scrollLeft };
  });
  check('with wrap off the terminal is wider than the screen and a sideways swipe scrolls it', (sideways.cols || 200) >= 200 && sideways.wide && sideways.left > 100, JSON.stringify(sideways));
  check('nothing runs off the side: terminal without wrap', await fits(phone));
  check('the choice is kept', await phone.evaluate(() => localStorage.getItem('studio.termWrap')) === '0');
  await phone.tap('#term-wrap');
  await phone.waitForTimeout(400);
  check('wrap on again fits the screen', await phone.evaluate(() => { const sc = document.querySelector('.term-inst:not(.hidden) .term-scroll'); return sc.scrollWidth <= sc.clientWidth + 2 && sc.scrollLeft === 0; }));
  const pressed = await phone.evaluate(async () => {
    const sc = document.querySelector('.term-inst:not(.hidden) .term-scroll'), host = sc.querySelector('.xterm'), scr = host.querySelector('.xterm-screen').getBoundingClientRect();
    const touch = (x, y) => new Touch({ identifier: 3, target: host, clientX: x, clientY: y });
    const x0 = scr.left + 20, y0 = scr.top + scr.height / 2;
    host.dispatchEvent(new TouchEvent('touchstart', { touches: [touch(x0, y0)], bubbles: true, cancelable: true }));
    await new Promise(res => setTimeout(res, 650));
    for (let i = 1; i <= 4; i++) host.dispatchEvent(new TouchEvent('touchmove', { touches: [touch(x0 + i * 20, y0 + i * 18)], bubbles: true, cancelable: true }));
    host.dispatchEvent(new TouchEvent('touchend', { touches: [], bubbles: true, cancelable: true }));
    await new Promise(res => setTimeout(res, 150));
    const bar = document.querySelector('.term-inst:not(.hidden) .term-selbar');
    return { bar: !!bar && !bar.classList.contains('hidden'), rows: (window.getSelection() + '').length };
  });
  check('holding a finger on the terminal selects text and shows Copy', pressed.bar, JSON.stringify(pressed));
  await phone.locator('.term-inst:not(.hidden) .term-selbar .btn.primary').tap();
  check('Copy copies it and says so', await phone.locator('.toast', { hasText: /Copied/ }).first().waitFor({ timeout: 3000 }).then(() => true, () => false));
  check('the bar goes away after copying', await phone.locator('.term-inst:not(.hidden) .term-selbar.hidden').count() === 1);
  if (shots) await phone.screenshot({ path: shots + '/phone-terminal.png' });
  await phone.tap('#btn-more');
  check('the other controls are in the More sheet', await phone.locator('#top-sheet #tg-apply').isVisible() && await phone.locator('#top-sheet .model-pick').isVisible() && await phone.locator('#top-sheet #btn-theme').isVisible());
  if (shots) await phone.screenshot({ path: shots + '/phone-more.png' });
  const applyWas = await phone.locator('#top-sheet #tg-apply').getAttribute('aria-checked');
  await phone.locator('#top-sheet #tg-apply').tap();
  check('toggles work and leave the sheet open', await phone.locator('#top-sheet').isVisible() && await phone.locator('#top-sheet #tg-apply').getAttribute('aria-checked') !== applyWas);
  for (const id of ['#tg-apply', '#tg-cmd']) if (await phone.locator(id).getAttribute('aria-checked') !== 'true') await phone.locator('#top-sheet ' + id).tap();
  await phone.locator('#top-sheet #btn-dash').tap();
  await phone.waitForSelector('#dash:not(.hidden)');
  const dashBox = await phone.locator('#dash').boundingBox();
  check('the gauges fit the screen', dashBox.x >= 0 && dashBox.x + dashBox.width <= 390, JSON.stringify(dashBox));
  if (shots) await phone.screenshot({ path: shots + '/phone-gauges.png' });
  await phone.tap('#btn-more'); await phone.locator('#top-sheet #btn-dash').tap();
  await phone.tap('#btn-more');
  check('with no status bar, the ComfyUI dashboard is in the More sheet', await phone.locator('#top-sheet #btn-cdash').isVisible());
  await phone.locator('#top-sheet #btn-cdash').tap();
  await phone.waitForSelector('#cdash:not(.hidden) .cd-pic');
  const cdashBox = await phone.locator('#cdash').boundingBox();
  check('and it fits the screen', cdashBox.x >= 0 && cdashBox.x + cdashBox.width <= 390, JSON.stringify(cdashBox));
  if (shots) await phone.screenshot({ path: shots + '/phone-pictures.png' });
  await phone.tap('#btn-more'); await phone.locator('#top-sheet #btn-cdash').tap();
  await phone.tap('#mt-chat');
  await phone.fill('#agent-input', 'first line');
  await phone.locator('#agent-input').press('Enter');
  check('Enter on a touch keyboard does not send', await phone.locator('.m-user').count() === 0);
  await phone.fill('#agent-input', 'Please make two edits');
  await phone.tap('#agent-send');
  await phone.waitForSelector('.m-tool.action', { timeout: 20000 });
  await phone.waitForFunction(() => document.querySelector('#agent-stop').classList.contains('hidden'), null, { timeout: 30000 });
  check('nothing runs off the side: a chat with cards', await fits(phone));
  const card = phone.locator('.m-tool.has-change').first();
  await card.locator('button:has-text("Expand")').tap();
  check('a diff stays inside the screen', await fits(phone) && (await card.boundingBox()).width <= 390);
  if (shots) await phone.screenshot({ path: shots + '/phone-diff.png' });
  const copyOverlap = await phone.evaluate(() => [...document.querySelectorAll('.m-asst')].filter(m => m.querySelector(':scope > .copy-reply')).map(m => {
    const b = m.querySelector(':scope > .copy-reply').getBoundingClientRect();
    return [...m.querySelectorAll(':scope > p, :scope > ul, :scope > ol')].some(t => { const r = t.getBoundingClientRect(); return b.top < r.bottom - 2 && b.bottom > r.top + 2 && b.left < r.right && b.right > r.left; });
  }));
  check('on a phone the copy button sits under a reply, not over its text', copyOverlap.length > 0 && copyOverlap.every(x => !x), JSON.stringify(copyOverlap));
  await phone.tap('#btn-more'); await phone.locator('#top-sheet #btn-account').tap();
  await phone.waitForSelector('.dialog');
  const dlg = await phone.locator('.dialog').boundingBox();
  check('dialogs fit the screen', dlg.x >= 0 && dlg.x + dlg.width <= 390 && await fits(phone), JSON.stringify(dlg));
  await phone.locator('.dialog button:has-text("Close")').tap();
  await phone.setViewportSize({ width: 844, height: 390 });
  await phone.waitForTimeout(300);
  check('turning the phone sideways still fits', await fits(phone));
  await phone.setViewportSize({ width: 390, height: 844 });
  await handset.close();
  const slate = await browser.newContext({ viewport: { width: 820, height: 1180 }, hasTouch: true, isMobile: true });
  await slate.addCookies(await context.cookies());
  const tablet = await slate.newPage();
  tablet.on('pageerror', e => errors.push('tablet: ' + e.message));
  await tablet.goto(url); await tablet.waitForSelector('#app:not(.hidden)');
  await tablet.waitForFunction(() => document.querySelector('#ws-name')?.textContent === 'mobile', null, { timeout: 8000 });
  await tablet.waitForTimeout(500);
  check('a tablet shows editor and chat side by side', await tablet.locator('#center').isVisible() && await tablet.locator('#agent').isVisible() && !(await tablet.locator('#mobile-tabs').isVisible()));
  check('nothing runs off the side: tablet', await fits(tablet));
  check('the file list starts closed', !(await tablet.locator('#sidebar').isVisible()));
  await tablet.locator('.act[data-view="explorer"]').tap();
  check('and opens over the editor', await tablet.locator('#sidebar').isVisible());
  if (shots) await tablet.screenshot({ path: shots + '/tablet-files.png' });
  await tablet.locator('.t-row', { hasText: 'app.py' }).locator('.t-name').tap();
  await tablet.waitForTimeout(400);
  check('choosing a file closes it', !(await tablet.locator('#sidebar').isVisible()));
  await tablet.waitForSelector('#pane-monaco:not(.hidden)', { timeout: 30000 });
  if (shots) { await tablet.waitForTimeout(500); await tablet.screenshot({ path: shots + '/tablet-editor.png' }); }
  await tablet.tap('#btn-more');
  check('a tablet has a More sheet too', await tablet.locator('#top-sheet #tg-apply').isVisible());
  await tablet.tap('#btn-more');
  await tablet.setViewportSize({ width: 1280, height: 800 });
  await tablet.waitForTimeout(300);
  check('widening puts the controls back in the top bar', await tablet.locator('#topbar > #tg-apply').isVisible() && !(await tablet.locator('#btn-more').isVisible()));
  await slate.close();
  await api('/api/prefs', { key: 'auto_apply', value: false }); await api('/api/prefs', { key: 'auto_commands', value: false });

  section('waiting room');
  await page.setViewportSize({ width: 1400, height: 900 });
  const invite = (await api('/api/admin/invites', {})).data.token;
  const bobCtx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const bob = await bobCtx.newPage();
  await bob.goto(url);
  const bobApi = (p, body) => bob.evaluate(async ([p, body]) => {
    const r = await fetch(p, { method: body ? 'POST' : 'GET', headers: { 'Content-Type': 'application/json', 'X-Studio': '1' }, body: body ? JSON.stringify(body) : undefined });
    return { status: r.status, data: await r.json().catch(() => null) };
  }, [p, body]);
  check('a second person can sign up', (await bobApi('/api/auth/signup', { token: invite, username: 'bob', password: 'bob password 1' })).status === 200);
  const bobHome = (await bobApi('/api/config')).data.workspace_roots[0];
  fs.writeFileSync(path.join(bobHome, 'notes.md'), 'from bob');
  const bobWs = (await bobApi('/api/workspace/open', { path: bobHome })).data.workspace.id;
  const bobChat = (await bobApi('/api/agent/sessions', { ws: bobWs })).data.id;
  check('bob offers his chat to the admin', (await bobApi('/api/handoff/offer', { ws: bobWs, session: bobChat, to: 'admin', note: 'Please finish the notes' })).status === 200);
  await bobApi('/api/queue/chat', { text: 'hello everyone' });
  await bobApi('/api/queue/chat', { text: 'a word just for you', to: 'admin' });
  await page.waitForFunction(() => Number(document.querySelector('#room-unread')?.textContent) >= 3, null, { timeout: 15000 }).catch(() => {});
  check('the unread badge counts both messages and the offer', await page.textContent('#room-unread') === '3', await page.textContent('#room-unread'));
  check('the banner tells you a chat was passed to you', (await page.textContent('#room-banner')).includes('passed you a chat'), await page.textContent('#room-banner'));
  check('and so does the tab title', (await page.title()).startsWith('(2)'), await page.title());
  await page.click('#btn-room');
  await page.click('.room-tab[data-tab="chat"]');
  check('the panel has Line, Chat and People tabs', await page.locator('.room-tab').count() === 3);
  check('public messages are in the Everyone conversation', (await page.textContent('#room .chat-list')).includes('hello everyone') && !(await page.textContent('#room .chat-list')).includes('just for you'));
  const bobThread = page.locator('.chat-thread', { hasText: 'bob' });
  check('a private message has its own conversation, with its own count', (await bobThread.locator('.tab-badge').textContent()) === '1');
  await bobThread.click();
  check('opening it shows the message and clears its count', (await page.textContent('#room .chat-list')).includes('a word just for you') && await bobThread.locator('.tab-badge').count() === 0);
  await page.fill('#room .chat-input', 'thanks bob');
  await page.press('#room .chat-input', 'Enter');
  await page.waitForTimeout(500);
  const toBob = (await bobApi('/api/queue/chat')).data.messages.find(m => m.text === 'thanks bob');
  check('a reply in that conversation goes to bob alone', toBob && toBob.to === 'bob', JSON.stringify(toBob));
  check('reading every conversation leaves only the offer on the badge', await page.textContent('#room-unread') === '1', await page.textContent('#room-unread'));
  await page.click('.room-tab[data-tab="people"]');
  await page.waitForSelector('#room .person');
  check('People lists bob with Message and Pass a chat', (await page.textContent('#room .room-people')).includes('bob') && await page.locator('#room .person button', { hasText: 'Pass a chat' }).count() >= 1);
  await page.evaluate(() => { const p = document.createElement('li'); p.className = 'person'; p.innerHTML = '<span class="dash-dot" data-state="err"></span><span class="room-who">x · not online</span>'; p.id = 'fake-offline'; document.querySelector('#room .room-people ul, #room .room-people').append(p); });
  await page.waitForTimeout(3500);    // at least one model dashboard refresh
  check('someone not online has a red dot, which the model dashboard leaves alone', await page.evaluate(() => [...document.querySelectorAll('#room .person')].filter(p => p.textContent.includes('not online')).every(p => p.querySelector('.dash-dot').dataset.state === 'err')));
  await page.evaluate(() => document.querySelector('#fake-offline')?.remove());
  await api('/api/queue/join', {});
  await bobApi('/api/queue/join', {});
  await page.waitForSelector('.toast:has-text("joined the line")', { timeout: 10000 }).catch(() => {});
  check('you hear when someone joins the line', await page.locator('.toast:has-text("bob joined the line")').count() >= 1);
  await page.click('.room-tab[data-tab="line"]');
  check('the room shows the day\'s turns and a typical turn', (await page.textContent('#room .room-stats')).includes('today'));
  check('alerts can be chosen in the room', await page.locator('#room .room-alerts').count() === 1);
  await page.waitForFunction(() => document.querySelectorAll('#room .rl-row').length >= 2, null, { timeout: 8000 }).catch(() => {});
  check('the line is one list: who has the agent now, then who waits', (await page.textContent('#room .rl-row.rl-now')).includes('You') && (await page.textContent('#room .rl-row.rl-now')).includes('Now')
        && /bob.*Turn in (about|under)/.test(await page.textContent('#room .rl-row:not(.rl-now)')), await page.textContent('#room .rl'));
  check('you can finish your turn from your row', await page.locator('#room .rl-now button', { hasText: 'Finish my turn' }).count() === 1);
  check('the rules fold away under How turns work', await page.locator('#room details.rl-how:not([open])').count() === 1);
  await page.locator('#room .rl-row:not(.rl-now) .rl-more').click();
  check('an admin manages someone in line from a menu on their row', await page.locator('.menu-ctx .mi', { hasText: 'Remove from the line' }).isVisible());
  await page.mouse.click(5, 5);
  const bobView = (await bobApi('/api/queue/state')).data;
  check('each place in line has its own wait', bobView.queue[0].wait_s > 0 && bobView.join_wait_s === undefined, JSON.stringify(bobView.queue));
  const bobWait = (await bobApi('/api/queue/state')).data.me;
  check('bob gets a wait estimate', bobWait.position === 1 && bobWait.wait_s > 0, JSON.stringify(bobWait));
  await bobApi('/api/queue/leave', {});
  check('someone not in line hears what joining would mean', (await bobApi('/api/queue/state')).data.join_wait_s >= 0);
  await api('/api/queue/release', {});
  if (shots) { await page.waitForTimeout(300); await page.screenshot({ path: shots + '/waiting-room.png' }); }
  await page.click('#room .handoff-card button:has-text("Accept and open")');
  await page.waitForSelector('.m-handoff', { timeout: 15000 }).catch(() => {});
  check('accepting opens the copied chat, saying who passed it', (await page.textContent('.m-handoff') || '').includes('bob'));
  check('in a copy of bob\'s folder', (await page.textContent('#ws-name')).endsWith('-from-bob') && fs.readFileSync(path.join(projects, path.basename(bobHome) + '-from-bob', 'notes.md'), 'utf8') === 'from bob', await page.textContent('#ws-name'));
  check('bob hears it was accepted', (await bobApi('/api/queue/state')).data.handoffs.outgoing[0].state === 'accepted');
  await page.click('#btn-room');
  await bobCtx.close();

  section('a folder on this computer');
  const helperBin = path.join(ROOT, 'tools', 'helper', 'dist', 'studio-helper');
  if (!fs.existsSync(helperBin)) check('(studio-helper is not built; skipped)', true);
  else {
    const pcDir = project('pc-homework', { 'hello.txt': 'from the pc' });
    const helperHome = path.join(tmp, 'helper-home');
    await page.click('#ws-btn');
    await page.click('.of-dialog .of-tab[data-tab="pcs"]');
    await page.waitForSelector('.pc-connect', { timeout: 10000 });
    check('the folder dialog offers to connect a folder on your computer', await page.locator('.pc-connect').isVisible());
    await page.click('.pc-connect');
    await page.waitForSelector('.pc-code');
    await page.waitForTimeout(2200);    // past a tick of the waiting room's clocks, which once turned this countdown into NaN:NaN
    check('the code\'s countdown shows the time left', /^\d+:\d\d$/.test(await page.textContent('.pc-clock')), await page.textContent('.pc-clock'));
    const firstCode = (await page.textContent('.pc-code-text')).replace(/[^A-Z0-9]/g, '');
    await page.click('.pc-card .pc-renew');
    await page.waitForFunction(old => document.querySelector('.pc-code-text').textContent.replace(/[^A-Z0-9]/g, '') !== old, firstCode, { timeout: 5000 }).catch(() => {});
    const shownCode = (await page.textContent('.pc-code-text')).replace(/[^A-Z0-9]/g, '');
    check('New code makes a fresh code', shownCode.length === 8 && shownCode !== firstCode, firstCode + ' ' + shownCode);
    const oldTry = spawn(helperBin, ['pair', '-server', url.replace(/\/$/, ''), '-code', firstCode, '-folder', project('pc-old-code')], { env: { ...process.env, STUDIO_HELPER_HOME: path.join(tmp, 'helper-old') } });
    check('and the old code stops working', await new Promise(r => oldTry.on('exit', r)) !== 0);
    if (shots) { await page.waitForTimeout(400); await page.screenshot({ path: shots + '/pc-connect.png' }); }
    check('the connect window has three steps and a system switch', await page.locator('.pc-dialog .pc-card').count() === 3 && await page.locator('.pc-dialog .pc-os-btn').count() === 2);
    check('the connect dialog shows a pairing code and both downloads', shownCode.length === 8 && await page.locator('.pc-steps a[href*="os=windows"]').count() === 1 && await page.locator('.pc-steps a[href*="os=linux"]').count() === 1, shownCode);
    const paired = spawn(helperBin, ['pair', '-server', url.replace(/\/$/, ''), '-code', shownCode, '-folder', pcDir], { env: { ...process.env, STUDIO_HELPER_HOME: helperHome } });
    await new Promise(r => paired.on('exit', r));
    const helperProc = spawn(helperBin, ['run'], { env: { ...process.env, STUDIO_HELPER_HOME: helperHome }, stdio: 'ignore' });
    try {
      await page.waitForSelector('.pc-wait button:has-text("Open it")', { timeout: 15000 }).catch(() => {});
      check('the dialog notices the helper and offers to open the folder', await page.locator('.pc-wait button:has-text("Open it")').count() === 1, await page.textContent('.pc-wait'));
      await page.click('.pc-wait button:has-text("Open it")');
      await page.waitForSelector('#ws-name .ws-pc.on', { timeout: 10000 }).catch(() => {});
      check('the folder shows a connected computer in the top bar', await page.locator('#ws-place .place-computer.on').count() === 1 && (await page.textContent('#ws-name')).startsWith('pc-homework@'));
      check('and has no sandbox switch, since its commands run there', !(await page.locator('#tg-sandbox').isVisible()));
      await page.waitForSelector('.t-row:has-text("hello.txt")', { timeout: 10000 }).catch(() => {});
      check('its files are in the explorer', await page.locator('.t-row:has-text("hello.txt")').count() === 1);
      await send('!echo from-$((40+2)) > made-here.txt; cat hello.txt');
      await page.waitForSelector('.m-shell, .shell-card, :text("from the pc")', { timeout: 15000 }).catch(() => {});
      check('a ! command runs on the computer', fs.existsSync(path.join(pcDir, 'made-here.txt')) && fs.readFileSync(path.join(pcDir, 'made-here.txt'), 'utf8').trim() === 'from-42');
      if (shots) await page.screenshot({ path: shots + '/pc-folder.png' });
      // Your own name for the computer: shown in My computers, the top bar and the folder's name, never changing the path.
      await page.click('#ws-btn');
      await page.click('.of-dialog .of-tab[data-tab="pcs"]');
      await page.waitForSelector('.pc-machine .pc-rename', { timeout: 10000 }).catch(() => {});
      const realPc = await page.getAttribute('.pc-machine', 'data-machine').catch(() => '');
      await page.click('.pc-machine .pc-rename');
      await page.fill('.dialog.sm .dlg-input', 'DEV PC');
      await page.click('.dialog.sm .dlg-foot button:has-text("OK")');
      await page.waitForSelector('.pc-machine .pc-mname:has-text("DEV PC")', { timeout: 5000 }).catch(() => {});
      check('a computer can be renamed in My computers, keeping its real name beside it', await page.locator('.pc-machine .pc-mname:has-text("DEV PC")').count() === 1 && (await page.textContent('.pc-machine .pc-mreal').catch(() => '')) === realPc, realPc);
      check('the folder shows the new name in the list and the top bar', (await page.textContent('.pc-item .nm')).endsWith('@DEV PC') && (await page.textContent('#ws-name')).endsWith('@DEV PC') && (await page.textContent('#ws-where')).includes('DEV PC'), await page.textContent('#ws-name'));
      check('the name is kept in the synced settings, and the folder\'s path is unchanged', (await api('/api/prefs')).data.prefs.pc_names?.[realPc] === 'DEV PC' && (await api('/api/helpers')).data.helpers[0].folder.endsWith('@' + realPc));
      if (shots) { await page.waitForTimeout(300); await page.screenshot({ path: shots + '/pc-rename.png' }); }
      await page.keyboard.press('Escape');
      // On a phone the rows fold into lines and nothing pushes the window sideways.
      const wide = page.viewportSize();
      await page.setViewportSize({ width: 390, height: 844 });
      await page.waitForTimeout(300);
      await page.click('#ws-btn').catch(() => {});
      await page.click('.of-dialog .of-tab[data-tab="pcs"]');
      await page.waitForSelector('.of-dialog .pc-machine', { timeout: 5000 }).catch(() => {});
      const fits = await page.evaluate(() => { const pane = document.querySelector('.of-dialog .of-pane:not(.hidden)'); const r = pane.getBoundingClientRect();
        return pane.scrollWidth <= pane.clientWidth + 1 && [...pane.querySelectorAll('.pc-item .btn, .pc-rename, .pc-item .nm')].every(b => { const x = b.getBoundingClientRect(); return x.left >= r.left - 1 && x.right <= r.right + 1; }); });
      check('on a phone My computers fits the window, buttons and names inside it', fits);
      check('and its four tabs are all in view', await page.evaluate(() => { const bar = document.querySelector('.of-dialog .of-tabs').getBoundingClientRect(); return [...document.querySelectorAll('.of-dialog .of-tab')].every(t => { const x = t.getBoundingClientRect(); return x.left >= bar.left - 1 && x.right <= bar.right + 1; }); }));
      if (shots) await page.screenshot({ path: shots + '/pc-phone.png' });
      await page.keyboard.press('Escape');
      await page.setViewportSize(wide);
      await page.waitForTimeout(300);
      // An older helper: its Update chip opens the update window, which notices when every folder there runs the newest.
      const outdated = async route => { const r = await route.fetch().catch(() => null); if (!r) return; const j = await r.json(); j.helpers = j.helpers.map(h => ({ ...h, version: '0.06', outdated: true })); await route.fulfill({ response: r, json: j }).catch(() => {}); };
      await page.route('**/api/helpers', outdated);
      await page.click('#ws-btn');
      await page.click('.of-dialog .of-tab[data-tab="pcs"]');
      await page.waitForSelector('.pc-item .pc-old', { timeout: 10000 }).catch(() => {});
      check('an older helper has an Update button in My computers', (await page.textContent('.pc-item .pc-old').catch(() => '')).includes('Update to'));
      await page.click('.pc-item .pc-old');
      await page.waitForSelector('.pc-dialog.dialog', { timeout: 5000 }).catch(() => {});
      const upd = await page.textContent('.pc-dialog .dlg-head').catch(() => '');
      check('it opens the update window for that computer, with no pairing code', upd.startsWith('Update the helper on') && await page.locator('.pc-dialog .pc-code').count() === 0 && await page.locator('.pc-dialog .pc-upd-row:has-text("pc-homework")').count() === 1, upd);
      check('with the download for the computer\'s own system first', await page.locator('.pc-dialog a.pc-dl[href*="os=linux"]').count() === 1 && await page.locator('.pc-dialog .pc-os-btn.on[data-os="linux"]').count() === 1);
      if (shots) { await page.waitForTimeout(400); await page.screenshot({ path: shots + '/pc-update.png' }); }
      await page.unroute('**/api/helpers', outdated);
      await page.waitForSelector('.pc-dialog .pc-wait:has-text("Updated")', { timeout: 8000 }).catch(() => {});
      check('and says so once the computer runs the newest helper', (await page.textContent('.pc-dialog .pc-wait')).includes('Updated'), await page.textContent('.pc-dialog .pc-wait'));
      await page.click('.pc-dialog .dlg-foot button:has-text("Close")');
      await page.click('#ws-btn');
      await page.click('.of-dialog .of-tab[data-tab="pcs"]');
      await page.waitForSelector('.pc-item');
      await page.click('.pc-item button:has-text("Disconnect")');
      await page.click('.dialog.sm .dlg-foot button:has-text("Disconnect")');
      await page.waitForTimeout(800);
      check('it can be disconnected from the dialog', (await api('/api/helpers')).data.helpers.length === 0);
      await page.keyboard.press('Escape');
    } finally { helperProc.kill(); }

    // The helper as people run it: no console, a form in the browser, autostart after the first pairing.
    const formHome = path.join(tmp, 'helper-form'), xdg = path.join(tmp, 'xdg');
    const formDir = project('pc-form', { 'notes.md': 'via the form' });
    const formEnv = { ...process.env, STUDIO_HELPER_HOME: formHome, XDG_CONFIG_HOME: xdg, STUDIO_HELPER_NO_BROWSER: '1', STUDIO_HELPER_NO_TRAY: '1' };
    const app = spawn(helperBin, ['--background'], { env: formEnv, stdio: 'ignore' });
    try {
      let ui = null;
      for (let i = 0; i < 50 && !ui; i++) { try { ui = JSON.parse(fs.readFileSync(path.join(formHome, 'ui.json'), 'utf8')); } catch { await page.waitForTimeout(100); } }
      check('the helper starts without a console and serves its form on this computer only', ui && ui.port > 0);
      const formUrl = `http://127.0.0.1:${ui.port}/?t=${ui.token}`;
      const noToken = (await fetch(`http://127.0.0.1:${ui.port}/api/state`)).status;
      check('its form API refuses calls without the page token', noToken !== 200, noToken);
      const again = spawn(helperBin, [], { env: formEnv, stdio: 'ignore' });
      const againCode = await new Promise(r => again.on('exit', r));
      check('starting it a second time only opens the running one\'s form', againCode === 0);
      const form = await context.newPage();
      await form.goto(formUrl);
      await form.waitForSelector('#summary:not(:text("Starting…"))');
      check('the form opens with no folders yet', (await form.textContent('#folders')).includes('No folder is shared yet'));
      const formCode = (await api('/api/helpers/pair', {})).data.code;
      await form.fill('#server', url.replace(/\/$/, ''));
      await form.fill('#code', formCode.toLowerCase());
      check('the code is tidied as it is typed', await form.inputValue('#code') === formCode.slice(0, 4) + '-' + formCode.slice(4));
      await form.fill('#folder', formDir);
      await form.click('#pair-btn');
      await form.waitForSelector('#pair-msg.ok', { timeout: 15000 }).catch(() => {});
      check('connecting from the form works', (await form.textContent('#pair-msg')).startsWith('Connected'), await form.textContent('#pair-msg'));
      await form.waitForSelector('.folder .state:has-text("in sync")', { timeout: 15000 }).catch(() => {});
      check('the folder shows as connected, with its files in sync', await form.locator('.folder .dot.connected').count() === 1 && (await form.textContent('.folder')).includes('in sync'), await form.textContent('#folders'));
      const desktopFile = path.join(xdg, 'autostart', 'strata-studio-helper.desktop');
      check('after the first pairing it starts when you sign in', fs.existsSync(desktopFile) && fs.readFileSync(desktopFile, 'utf8').includes('--background') && await form.isChecked('#autostart'));
      await form.uncheck('#autostart');
      await form.waitForTimeout(400);
      check('and that can be switched off', !fs.existsSync(desktopFile));
      const mirrorOf = (await api('/api/helpers')).data.helpers.find(h => h.folder.startsWith('pc-form@'));
      check('Studio has the folder and its file', mirrorOf && mirrorOf.online && fs.existsSync(path.join(mirrorOf.path, 'notes.md')));
      const wrong = await form.evaluate(async () => { document.querySelector('#code').value = 'ZZZZ-ZZZZ'; document.querySelector('#folder').value = '/'; document.querySelector('#pair-btn').click(); await new Promise(r => setTimeout(r, 1500)); return document.querySelector('#pair-msg').textContent; });
      check('a mistake is shown in the form', wrong.length > 0 && !wrong.startsWith('Connected'), wrong);
      if (shots) await form.screenshot({ path: shots + '/helper-form.png', fullPage: true });
      form.once('dialog', d => d.accept());
      await form.click('.folder button.danger');
      await form.waitForTimeout(800);
      check('a folder can be disconnected from the form', (await form.textContent('#folders')).includes('No folder is shared yet'));
      await form.close();
      for (const h of (await api('/api/helpers')).data.helpers) await api('/api/helpers/remove', { id: h.id });
    } finally { app.kill(); }
  }

  section('uploads');
  {
    const up = project('upload-here', { 'readme.txt': 'x' });
    fs.mkdirSync(path.join(up, 'sounds')); fs.writeFileSync(path.join(up, 'sounds', 'keep.txt'), 'x');
    await openProject(up);
    if (!(await page.locator('#tree').isVisible())) await page.click('.act[data-view="explorer"]');
    await page.waitForSelector('#tree .t-row[data-path="sounds"]');
    const drop = (selector, files) => page.evaluate(([sel, files]) => {
      const dt = new DataTransfer();
      for (const [name, text] of files) dt.items.add(new File([text], name, { type: 'application/octet-stream' }));
      const target = document.querySelector(sel);
      target.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }));
      target.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
    }, [selector, files]);
    await drop('#tree', [['take.wav', 'RIFF-one']]);
    await page.waitForFunction(() => document.querySelector('#tree .t-row[data-path="take.wav"]'), null, { timeout: 10000 }).catch(() => {});
    check('a file dropped on the explorer is uploaded into the folder', fs.existsSync(path.join(up, 'take.wav')) && fs.readFileSync(path.join(up, 'take.wav'), 'utf8') === 'RIFF-one');
    await drop('#tree .t-row[data-path="sounds"]', [['into.wav', 'two']]);
    await page.waitForFunction(() => document.querySelector('#tree .t-row[data-path="sounds/into.wav"]'), null, { timeout: 10000 }).catch(() => {});
    check('dropped on a folder, it goes into that folder', fs.existsSync(path.join(up, 'sounds', 'into.wav')));
    await drop('#tree', [['take.wav', 'RIFF-new']]);
    await page.waitForSelector('.dialog:has-text("already there")', { timeout: 5000 }).catch(() => {});
    check('a name already taken asks: replace or keep both', await page.locator('.dialog button:has-text("Keep both")').count() === 1);
    await page.click('.dialog button:has-text("Replace")');
    await page.waitForFunction(() => true);
    await page.waitForTimeout(800);
    const trash = path.join(up, '.strata-studio', 'trash');
    check('Replace puts the old file in the trash', fs.readFileSync(path.join(up, 'take.wav'), 'utf8') === 'RIFF-new' && fs.existsSync(trash));
    await drop('#tree', [['take.wav', 'third']]);
    await page.waitForSelector('.dialog:has-text("already there")', { timeout: 5000 }).catch(() => {});
    await page.click('.dialog button:has-text("Keep both")');
    await page.waitForTimeout(800);
    check('Keep both saves the new one beside it', fs.readFileSync(path.join(up, 'take (2).wav'), 'utf8') === 'third');
    const big = Buffer.alloc(17 * 1024 * 1024 + 5, 7);
    const bigFile = path.join(os.tmpdir(), `big-${Date.now()}.bin`); fs.writeFileSync(bigFile, big);
    await page.click('#tree .t-row[data-path="sounds"]');
    const chooser = page.waitForEvent('filechooser');
    await page.click('#ex-upload');
    await (await chooser).setFiles(bigFile);
    await page.waitForFunction(n => document.querySelector(`#tree .t-row[data-path="sounds/${n}"]`), path.basename(bigFile), { timeout: 20000 }).catch(() => {});
    const got = path.join(up, 'sounds', path.basename(bigFile));
    check('Upload files sends a large file in parts into the selected folder', fs.existsSync(got) && fs.statSync(got).size === big.length);
    fs.rmSync(bigFile);
    await page.click('#attach-more');
    await page.click('.menu-ctx .mi:has-text("From the project")');
    await page.waitForSelector('.dialog .pick-list .pick-row', { timeout: 5000 });
    await page.fill('.dialog input[type=search]', 'into');
    await page.click('.dialog .pick-row:has-text("into.wav")');
    check('From the project adds a project file to the message, by its path', (await page.locator('#attach-row .attach-chip:has-text("into.wav") .attach-where').innerText()) === 'sounds');
    await page.locator('#attach-row .attach-x').click();
  }

  section('explorer: choosing, moving and downloading');
  {
    const ex = project('explorer-many', { 'a.txt': 'A', 'b.txt': 'B', 'c.txt': 'C', 'tone.wav': 'RIFF' });
    fs.mkdirSync(path.join(ex, 'box'));
    fs.writeFileSync(path.join(ex, 'box', 'keep.txt'), 'k');
    await openProject(ex);
    if (!(await page.locator('#tree').isVisible())) await page.click('.act[data-view="explorer"]');
    await page.waitForSelector('#tree .t-row[data-path="a.txt"]');
    const tabs = () => page.locator('.tab').count();
    const before = await tabs();
    await page.click('#tree .t-row[data-path="a.txt"]');
    await page.waitForTimeout(400);
    check('one click chooses a file without opening it', await tabs() === before && await page.locator('#tree .t-row[data-path="a.txt"].selected').count() === 1);
    await page.dblclick('#tree .t-row[data-path="a.txt"]');
    await page.waitForSelector('.tab:has-text("a.txt")', { timeout: 5000 }).catch(() => {});
    check('a double click opens it', await page.locator('.tab:has-text("a.txt")').count() === 1);
    await page.click('#tree .t-row[data-path="a.txt"]');
    await page.click('#tree .t-row[data-path="c.txt"]', { modifiers: ['Shift'] });
    const chosen = () => page.$$eval('#tree .t-row.selected', rs => rs.map(r => r.dataset.path));
    check('Shift+click chooses the rows between', JSON.stringify(await chosen()) === JSON.stringify(['a.txt', 'b.txt', 'c.txt']), await chosen());
    await page.click('#tree .t-row[data-path="b.txt"]', { modifiers: ['Control'] });
    check('Ctrl+click leaves one out', JSON.stringify(await chosen()) === JSON.stringify(['a.txt', 'c.txt']), await chosen());
    await page.click('#tree .t-row[data-path="tone.wav"]', { modifiers: ['Control'] });
    check('and adds one', (await chosen()).length === 3);
    check('the Download button says how many', (await page.getAttribute('#ex-download', 'title')).includes('3 chosen'));
    const [zip] = await Promise.all([page.waitForEvent('download'), page.click('#ex-download')]);
    const zipPath = path.join(os.tmpdir(), `dl-${Date.now()}.zip`); await zip.saveAs(zipPath);
    const listing = execFileSync('python3', ['-c', 'import sys,zipfile;print(" ".join(sorted(zipfile.ZipFile(sys.argv[1]).namelist())))', zipPath]).toString().trim();
    check('several chosen files download as one zip', zip.suggestedFilename().endsWith('.zip') && listing === 'a.txt c.txt tone.wav', listing);
    fs.rmSync(zipPath);
    await page.click('#tree .t-row[data-path="a.txt"]', { button: 'right' });
    check('the menu acts on all of them', await page.locator('.menu-ctx .mi:has-text("Download 3 items")').count() === 1 && await page.locator('.menu-ctx .mi:has-text("Delete 3 items")').count() === 1);
    await page.keyboard.press('Escape');
    await page.mouse.click(5, 5);
    // A drag in Chromium needs real mouse moves; Playwright's dragTo does them.
    await page.locator('#tree .t-row[data-path="a.txt"]').dragTo(page.locator('#tree .t-row[data-path="box"]'));
    await page.waitForSelector('#tree .t-row[data-path="box/tone.wav"]', { timeout: 8000 }).catch(() => {});
    check('dragging the chosen rows onto a folder moves them all there', fs.existsSync(path.join(ex, 'box', 'a.txt')) && fs.existsSync(path.join(ex, 'box', 'c.txt')) && fs.existsSync(path.join(ex, 'box', 'tone.wav')) && fs.existsSync(path.join(ex, 'b.txt')));
    check('an open tab follows its file', await page.locator('.tab[title="box/a.txt"], .tab:has-text("a.txt")').count() >= 1);
    check('the moved rows stay chosen', (await chosen()).every(p => p.startsWith('box/')) && (await chosen()).length === 3, await chosen());
    fs.writeFileSync(path.join(ex, 'keep.txt'), 'top');
    await page.click('#ex-refresh'); await page.waitForSelector('#tree .t-row[data-path="keep.txt"]');
    await page.click('#tree .t-row[data-path="box/keep.txt"]');
    await page.locator('#tree .t-row[data-path="box/keep.txt"]').dragTo(page.locator('#tree .t-row[data-path="b.txt"]'));
    await page.waitForSelector('.dialog:has-text("already")', { timeout: 5000 }).catch(() => {});
    check('moving onto a name already taken asks first', await page.locator('.dialog button:has-text("Keep both")').count() === 1);
    await page.click('.dialog button:has-text("Keep both")');
    await page.waitForSelector('#tree .t-row.selected[data-path="keep (2).txt"]', { timeout: 5000 }).catch(() => {});
    check('Keep both moves it beside the other', fs.readFileSync(path.join(ex, 'keep (2).txt'), 'utf8') === 'k' && fs.readFileSync(path.join(ex, 'keep.txt'), 'utf8') === 'top');
    await page.click('#tree .t-row[data-path="b.txt"]');
    const [one] = await Promise.all([page.waitForEvent('download'), page.click('#ex-download')]);
    check('one chosen file downloads as itself', one.suggestedFilename() === 'b.txt', one.suggestedFilename() + ' ' + one.url());
    await page.dblclick('#tree .t-row[data-path="box/tone.wav"]');
    await page.waitForSelector('#pane-preview .ae-dl', { timeout: 8000 }).catch(() => {});
    const [snd] = await Promise.all([page.waitForEvent('download'), page.click('#pane-preview .ae-dl')]);
    check('a sound\'s player has a Download button', snd.suggestedFilename() === 'tone.wav');
    fs.writeFileSync(path.join(ex, 'box', 'notes.md'), 'line one\nthe Needle is here\n');
    await page.click('#ex-refresh');
    await page.click('#ex-find-btn');
    check('the find button opens a find bar in the Explorer', await page.locator('#ex-find-q').isVisible() && await page.evaluate(() => document.activeElement.id) === 'ex-find-q');
    await page.fill('#ex-find-q', 'box');
    await page.waitForSelector('#ex-found .ef-row[data-path="box"]', { timeout: 5000 }).catch(() => {});
    check('it finds folders by name', await page.locator('#ex-found .ef-row[data-path="box"]').count() === 1 && !(await page.locator('#tree').isVisible()));
    await page.fill('#ex-find-q', 'needle');
    await page.waitForSelector('#ex-found .sr-line', { timeout: 5000 }).catch(() => {});
    check('and text inside files, with the line', (await page.locator('#ex-found .sr-line').first().innerText()).includes('Needle'));
    await page.locator('#ex-found .sr-line').first().click();
    await page.waitForSelector('.tab:has-text("notes.md")', { timeout: 5000 }).catch(() => {});
    check('a line opens its file', await page.locator('.tab:has-text("notes.md")').count() === 1);
    await page.fill('#ex-find-q', 'box');
    await page.waitForSelector('#ex-found .ef-row[data-path="box"]', { timeout: 5000 }).catch(() => {});
    await page.click('#ex-found .ef-row[data-path="box"]');
    await page.waitForSelector('#tree .t-row.selected[data-path="box"]', { timeout: 5000 }).catch(() => {});
    check('a folder shows chosen in the tree, and the bar closes', await page.locator('#tree .t-row.selected[data-path="box"]').count() === 1 && !(await page.locator('#ex-find').isVisible()));
    await page.click('#tree .t-row[data-path="b.txt"]');
    await page.keyboard.press('Control+Alt+f');
    check('Ctrl+Alt+F opens it too', await page.locator('#ex-find-q').isVisible());
    await page.keyboard.press('Escape');
    check('Esc closes it and the tree is back', !(await page.locator('#ex-find').isVisible()) && await page.locator('#tree').isVisible());
    await page.click('.act[data-view="media"]');
    await page.waitForSelector('#media-body .media-dl-all', { timeout: 8000 }).catch(() => {});
    check('every media file has a Download, and the view downloads them all', await page.locator('#media-body .media-dl').count() >= 1 && (await page.textContent('#media-body .media-dl-all')).includes('Download'));
    await page.click('.act[data-view="explorer"]');
  }

  section('voices');
  {
    const book = project('voice-book', { 'notes.md': 'chapter one' });
    await openProject(book);
    await page.click('.act[data-view="voices"]');
    await page.waitForSelector('#voices-body .vo-engine', { timeout: 10000 });
    check('the Voices view shows the engine', (await page.textContent('#voices-body .vo-engine')).includes('Ready'), await page.textContent('#voices-body .vo-engine'));
    await page.click('#voice-new');
    await page.fill('.dialog input[placeholder^="e.g. Calm"]', 'Calm narrator');
    await page.fill('.dialog textarea', 'A warm woman in her forties, calm, slow');
    await page.selectOption('.dialog select[aria-label="Language"]', 'en');
    check('an English voice gets an English sample sentence', (await page.inputValue('.dialog .dlg-form label:has-text("Sample sentence") input')).startsWith('Hello'));
    await page.click('.dialog button:has-text("Make 3 takes")');
    await page.waitForSelector('.vo-take input[type=radio]', { timeout: 20000 }).catch(() => {});
    check('a new voice comes as takes to listen to', await page.locator('.vo-take audio').count() >= 1);
    await page.locator('.vo-take input[type=radio]').first().check();
    await page.click('.dialog button:has-text("Keep the chosen take")');
    await page.waitForSelector('.vo-item:has-text("Calm narrator")', { timeout: 10000 }).catch(() => {});
    check('the kept take joins the cast, with its recording to play', await page.locator('.vo-item:has-text("Calm narrator") audio').count() === 1);
    check('marked as English', (await page.textContent('.vo-item:has-text("Calm narrator") .vo-lang')) === 'EN'
          && await page.locator('#voices-body select[aria-label="Voice"] optgroup[label="English"] option', { hasText: 'Calm narrator' }).count() === 1);
    await page.fill('#voices-body .vo-text', 'Καλημέρα σε όλους. Σήμερα διαβάζουμε το πρώτο κεφάλαιο.');
    await page.click('#voices-body button:has-text("Read aloud")');
    await page.waitForSelector('.vo-result audio', { timeout: 20000 }).catch(() => {});
    check('text is read aloud in the chosen voice, to play or download', await page.locator('.vo-result audio').count() === 1 && await page.locator('.vo-result a:has-text("Download")').count() === 1);
    page.once('dialog', d => d.accept());
    await page.click('.vo-result button:has-text("Save to project")');
    await page.waitForSelector('.dialog input', { timeout: 5000 });
    await page.fill('.dialog input', 'voice/chapter-1.mp3');
    await page.press('.dialog input', 'Enter');
    await page.waitForTimeout(800);
    check('and saved into the open folder', fs.existsSync(path.join(book, 'voice', 'chapter-1.mp3')));
    if (shots) await page.screenshot({ path: shots + '/voices.png' });
    // a voice copied from a recording: a test reading first, kept with its transcript and why it may be copied
    execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=200:duration=6', '-y', path.join(book, 'voice', 'mello.wav')]);
    await page.click('#voice-rec');
    await page.waitForSelector('.dialog.dialog .vo-rec', { timeout: 5000 });
    check('Voice from a recording offers the folder\'s sounds', await page.locator('#vo-rec-files option[value="voice/mello.wav"]').count() === 1);
    await page.fill('.vo-rec input[aria-label="Recording"]', 'voice/mello.wav');
    await page.fill('.vo-rec label:has-text("Words spoken") textarea', 'Hi, I am Mello.');
    await page.fill('.vo-rec input[placeholder="e.g. Mello"]', 'Mello');
    await page.click('.vo-rec button:has-text("Make a test reading")');
    check('it will not start without saying whose voice it is', await page.locator('.vo-rec .vo-take').count() === 0 && await page.evaluate(() => document.activeElement?.placeholder?.startsWith('e.g. me')));
    await page.fill('.vo-rec label:has-text("Whose voice") input', 'Mello, a designed voice we own');
    await page.selectOption('.vo-rec select[aria-label="Why it may be copied"]', 'made');
    await page.click('.vo-rec button:has-text("Make a test reading")');
    await page.waitForSelector('.vo-rec .vo-take audio', { timeout: 20000 }).catch(() => {});
    check('a test reading in the recording\'s voice plays before keeping it', await page.locator('.vo-rec .vo-take audio').count() === 1, await page.locator('.vo-rec').textContent());
    if (shots) await page.screenshot({ path: shots + '/voice-recording.png' });
    await page.click('.dialog button:has-text("Keep as a voice")');
    await page.waitForSelector('.vo-item:has-text("Mello")', { timeout: 10000 }).catch(() => {});
    check('the recording joins the cast, saying where it came from', ((await page.locator('.vo-item:has-text("Mello") .vo-origin').textContent().catch(() => '')) || '').includes('From a recording of Mello'));
    await page.click('.act[data-view="explorer"]');
  }

  section('account and settings');
  await page.evaluate(() => localStorage.removeItem('studio.accountSection'));
  await page.click('#btn-account');
  await page.waitForSelector('.dialog.acct .ac-me');
  check('Account & settings opens on your profile, with a section menu', await page.locator('.ac-nav button').count() === 12 && await page.locator('.ac-nav-sub:has-text("Administration")').count() === 1);
  check('it lists your signed-in browsers, marking this one', await page.locator('.ac-row .ac-badge:has-text("This browser")').count() === 1);
  await page.fill('.ac-inline input', 'Studio Admin');
  await page.click('.ac-inline button:has-text("Save")');
  await page.waitForSelector('.ac-me-name:has-text("Studio Admin")', { timeout: 5000 }).catch(() => {});
  check('you can set your display name', (await page.textContent('.ac-me-name')) === 'Studio Admin');
  if (shots) await page.screenshot({ path: shots + '/account-profile.png' });
  await page.click('.ac-nav button[data-section="preferences"]');
  await page.click('.ac-theme[data-theme-name="light"]');
  check('Preferences switches the theme', await page.evaluate(() => document.documentElement.dataset.theme) === 'light');
  await page.click('.ac-theme[data-theme-name="dark"]');
  await page.click('.ac-nav button[data-section="users"]');
  await page.waitForSelector('.ac-user');
  const everyone = await page.locator('.ac-user').count();
  await page.fill('.ac-search', 'zzz-nobody');
  check('people can be found by name', await page.locator('.ac-user').count() === 0 && everyone >= 2, everyone);
  await page.fill('.ac-search', '');
  await page.locator('.ac-user[data-user="admin"] button[aria-label^="More"]').click();
  check('less common actions sit in a menu', await page.locator('.menu-ctx .mi:has-text("Make a reset link")').count() === 1);
  check('and the menu shows above the window, not behind it', await page.evaluate(() => { const m = document.querySelector('.menu-ctx'), r = m.getBoundingClientRect();
    return m.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)); }));
  await page.click('.ac-pane h3');
  {
    const sw = page.locator('.ac-user input[data-right="card"]').first();
    check('each user has rights switches: own computer and graphics card', await page.locator('.ac-user input[data-right="pc_folders"]').count() >= 1 && await sw.count() === 1);
    const who = await sw.evaluate(i => i.closest('.ac-user').dataset.user);
    await sw.evaluate(i => i.click());
    check('the graphics card switch gives the right', await page.waitForFunction(u => [...document.querySelectorAll('.ac-user')].some(r => r.dataset.user === u && /Graphics card/.test(r.querySelector('.ac-badges')?.textContent || '')
      && r.querySelector('input[data-right="card"]').checked), who, { timeout: 5000 }).then(() => true, () => false));
    await page.locator(`.ac-user[data-user="${who}"] input[data-right="card"]`).evaluate(i => i.click());
    await page.waitForFunction(u => !document.querySelector(`.ac-user[data-user="${u}"] input[data-right="card"]`)?.checked, who, { timeout: 5000 }).catch(() => {});
  }
  if (shots) await page.screenshot({ path: shots + '/account-users.png' });
  await api('/api/admin/mcp', { servers: [{ name: 'mock', command: 'python3', args: [path.join(ROOT, 'tests', 'mock_mcp.py')], app_command: 'python3 -c pass', app_port: 1, app_window: 'hidden', env: { MOCK_URL: 'http://x', MOCK_TOKEN: '' } }] });
  await page.click('.ac-nav button[data-section="servers"]');
  await page.waitForSelector('.ac-pane h3:has-text("Tool servers")');
  await page.waitForSelector('.mcp-card', { timeout: 10000 }).catch(() => {});
  check('tool servers are compact cards with a state, chips and a switch', await page.locator('.mcp-card').count() === 1 && await page.locator('.mcp-card .ac-toggle').count() === 1
    && (await page.textContent('.mcp-card .mcp-chips')).includes('Needs python3'), await page.textContent('.mcp-card'));
  check('the add form waits behind a button', await page.locator('.mcp-add').count() === 0);
  await page.click('.ac-pane button:has-text("Add server")');
  check('which opens it', await page.locator('.mcp-add').count() === 1);
  await page.click('.mcp-card-head');
  await page.waitForSelector('.mcp-card.open .mcp-app', { timeout: 5000 }).catch(() => {});
  check('a card opens to its settings, with the program it needs and Start', (await page.textContent('.mcp-app')).includes('is not running') && await page.locator('.mcp-app button:has-text("Start")').count() === 1, await page.textContent('.mcp-card'));
  if (shots) await page.screenshot({ path: shots + '/tool-servers.png' });
  await page.locator('.mcp-app summary').click();
  check('and the choice of a visible or invisible window', await page.inputValue('.mcp-window') === 'hidden' && await page.locator('.mcp-window option').count() === 2);
  check('its settings say which are set and which are empty, values hidden', await page.locator('.mcp-secrets[open]').count() === 1
    && (await page.textContent('.mcp-secrets summary')).includes('1 empty: MOCK_TOKEN') && await page.locator('.mcp-secret input[type=password]').count() === 1
    && await page.inputValue('.mcp-secret input[aria-label="MOCK_URL"]') === 'http://x', await page.textContent('.mcp-secrets'));
  check('a server still missing a secret says so on its card', (await page.textContent('.mcp-card .mcp-chips')).includes('Needs MOCK_TOKEN'));
  await page.fill('.mcp-secret input[aria-label="MOCK_TOKEN"]', 'tok-1234567890abcd');
  await page.click('.mcp-secrets button:has-text("Save and restart")');
  await page.waitForFunction(() => !(document.querySelector('.mcp-secrets summary')?.textContent || 'empty').includes('empty'), null, { timeout: 8000 }).catch(() => {});
  const saved = (await api('/api/admin/mcp')).data.servers[0];
  check('a typed value is saved and the others are kept', JSON.stringify(saved.filled.env) === '["MOCK_TOKEN","MOCK_URL"]' && saved.env.MOCK_TOKEN === '••••••', JSON.stringify(saved));
  await page.waitForSelector('.mcp-chip.ok', { timeout: 8000 }).catch(() => {});
  check('then the card shows the credentials are saved, the token only by its last characters', await page.locator('.mcp-chip.ok:has-text("Credentials saved")').count() === 1
    && (await page.getAttribute('.mcp-secret input[aria-label="MOCK_TOKEN"]', 'placeholder')).startsWith('••••abcd') && saved.shown.env.MOCK_URL.value === 'http://x'
    && !JSON.stringify(saved).includes('tok-1234567890'), JSON.stringify(saved.shown));
  await api('/api/admin/mcp', { servers: [] });
  await page.click('.ac-nav button[data-section="card"]');
  await page.waitForSelector('.ac-pane .card-choice', { timeout: 5000 }).catch(() => {});
  check('Graphics card offers Strata first and Tools ready, one of them chosen', await page.locator('.card-choice').count() === 2
    && await page.locator('.card-choice[aria-checked="true"]').count() === 1, await page.textContent('.ac-pane'));
  await page.click('.ac-nav button[data-section="computers"]');
  await page.waitForSelector('.ac-pane .pc-connect', { timeout: 5000 }).catch(() => {});
  check('so are your computers', await page.locator('.ac-pane .pc-connect').count() === 1);
  await page.click('.ac-nav button[data-section="updates"]');
  await page.waitForSelector('.ac-pane .up-row', { timeout: 5000 }).catch(() => {});
  await page.click('.ac-pane .ac-head button:has-text("Check now")');
  await page.waitForSelector('.up-row[data-id="fake-safe"] .up-new', { timeout: 15000 }).catch(() => {});
  const upText = await page.textContent('.ac-pane');
  check('Updates lists the tools by group, what is new, and the nightly settings', upText.includes('Docker services and images') && upText.includes('1.0 → 1.1')
    && upText.includes('Update the safe ones by themselves') && await page.locator('.up-row[data-id="fake-manual"] .ac-badge:has-text("Manual")').count() === 1
    && await page.locator('.up-row[data-id="fake-safe"] .toggle').count() === 1, upText.slice(0, 400));
  check('system packages show the sudo command to copy, with no Update button', (await page.textContent('.up-row[data-id="fake-apt"] .up-cmd')).includes('sudo apt install --only-upgrade ffmpeg')
    && await page.locator('.up-row[data-id="fake-apt"] button:has-text("Update")').count() === 0);
  check('the account button and the Updates entry show that something is new', await page.locator('#btn-account.has-updates').count() === 1
    && (await page.textContent('.ac-nav button[data-section="updates"] .up-count')) === '2');
  await page.click('.up-row[data-id="fake-safe"] button:has-text("Update")');
  await page.waitForSelector('.up-job.ok', { timeout: 10000 }).catch(() => {});
  check('Update runs it, shows the result, and offers Roll back', (await page.textContent('.up-job')).includes('Done: Fake service')
    && await page.locator('.up-row[data-id="fake-safe"] button:has-text("Roll back")').count() === 1 && (await page.textContent('.up-row[data-id="fake-safe"]')).includes('Up to date'));
  await page.click('.up-row[data-id="fake-safe"] .toggle');
  await page.waitForTimeout(300);
  check('a safe item can be kept from updating by itself', (await api('/api/admin/updates')).data.items.find(i => i.id === 'fake-safe').auto === false);
  await page.click('.ac-nav button[data-section="setup"]');
  await page.waitForSelector('.ac-pane .su-row', { timeout: 8000 }).catch(() => {});
  const suText = await page.textContent('.ac-pane');
  check('Tools & setup shows this machine, its graphics card and the tools by group with their state', suText.includes('This machine') && suText.includes('gfx1100')
    && suText.includes('Services in Docker') && await page.locator('.su-row[data-id="fake-apt"] .ac-badge:has-text("Needs something first")').count() === 1
    && await page.locator('.su-row[data-id="fake-later"] .su-pick:disabled').count() === 1, suText.slice(0, 500));
  check('an engine too big for the card says it will not fit and cannot be chosen; an optional one is marked',
    await page.locator('.su-row[data-id="fake-big"] .ac-badge:has-text("fit here")').count() === 1 && await page.locator('.su-row[data-id="fake-big"] .su-pick:disabled').count() === 1
    && await page.locator('.su-row[data-id="fake-extra"] .su-extra:has-text("Optional")').count() === 1 && (await page.textContent('.su-row[data-id="fake-extra"]')).includes('8 GB card'));
  check('what needs root comes as one script to copy or download', (await page.textContent('.su-script .su-code')).includes('apt-get install -y no-such-program')
    && await page.locator('.su-script a[download="studio-setup.sh"]').count() === 1);
  await page.click('.su-row[data-id="fake-ok"] .su-pick');
  await page.waitForTimeout(300);
  check('choosing a tool counts it on the Install chosen button', (await page.textContent('.su-picked')).includes('(1)'));
  await page.click('.su-all');
  await page.waitForSelector('.dialog:not(.acct) .btn.primary');
  const setupAsk = await page.textContent(".dialog:not(.acct)");
  check('Install everything asks first, with the sizes and what waits for the script, leaving optional engines out', setupAsk.includes("to download") && setupAsk.includes("Waiting for the setup script")
    && !setupAsk.includes('Fake optional engine'), setupAsk);
  await page.click('.dialog:not(.acct) .btn.primary');
  await page.waitForSelector('.up-job.ok, .up-job.bad', { timeout: 20000 }).catch(() => {});
  check('the install runs, says how it went, and the tool shows as installed', (await page.textContent('.up-job')).includes('1 installed')
    && await page.locator('.su-row[data-id="fake-ok"] .ac-badge:has-text("Installed")').count() === 1, await page.textContent('.up-job'));
  check('its tool server was added', (await api('/api/admin/mcp')).data.servers.some(s => s.name === 'fake-tool'));
  await api('/api/admin/mcp', { servers: [] });
  await page.click('.dialog.acct .ac-top button:has-text("Close")');
  await page.click('#ex-live');
  await page.click('.menu-ctx >> text="The agent\'s web browser"');
  await page.waitForFunction(() => (document.querySelector('.bv-veil')?.textContent || '').length > 0, null, { timeout: 15000 }).catch(() => {});
  check('the Browser tab opens from the globe menu and says when the agent\'s browser is not running', await page.locator('.tab:has-text("Browser")').count() === 1
    && (await page.textContent('.bv-veil')).includes('browser tool server') && (await page.textContent('.bv-take')) === 'Take over', await page.textContent('.bv-veil'));
  await page.click('.bv-ask');
  check('Ask about this page starts a message about the page in the browser', (await page.inputValue('#agent-input')).startsWith('About the page in the browser: '));
  await page.fill('#agent-input', '');
  await page.locator('.tab:has-text("Browser") .t-close').first().click().catch(() => {});

  section('tool server calls in words');
  {
    await api('/api/admin/mcp', { servers: [{ name: 'mock', command: 'python3', args: [path.join(ROOT, 'tests', 'mock_mcp.py')], approval: 'with_commands' }] });
    for (let i = 0; i < 40; i++) { const d = (await api('/api/admin/mcp')).data; if (d.servers[0] && d.servers[0].state === 'ready') break; await page.waitForTimeout(250); }
    await openProject(project('mcp-words', {}));
    await page.evaluate(() => { const b = document.querySelector('#tg-cmd'); if (b.getAttribute('aria-checked') !== 'true') b.click(); });
    await send('use mcp mcp__mock__echo {"text": "good morning", "progress": "1/2"}');
    await ended();
    const rowText = await page.textContent('.m-tool.mcp .row');
    check('a tool server call reads as words, without raw arguments or the repeated name', rowText.includes('mock: echo') && rowText.includes("text: good morning")
      && !rowText.includes('{') && !rowText.includes('progress') && !rowText.includes('mock · echo'), rowText);
    await page.click('.m-tool.mcp .row');
    check('its full arguments show when the row is opened', await page.locator('.m-tool.mcp.expanded .t-in').isVisible() && (await page.textContent('.m-tool.mcp .t-in')).includes('"text": "good morning"'));
    const look = await page.locator('.m-tool.mcp').first().evaluate(n => ({ edge: getComputedStyle(n).borderLeftColor, top: getComputedStyle(n).borderTopColor, svg: getComputedStyle(n.querySelector('.t-name svg')).color, server: n.dataset.server }));
    check('each tool server has its own colour on the row\'s edge and icon', look.server === 'mock' && look.edge === look.svg && look.edge !== look.top, JSON.stringify(look));
    if (shots) await page.screenshot({ path: shots + '/mcp-words.png' });
    await page.evaluate(() => { const b = document.querySelector('#tg-cmd'); if (b.getAttribute('aria-checked') === 'true') b.click(); });
    await api('/api/admin/mcp', { servers: [] });
  }

  section('sign out and in');
  await page.click('#btn-logout');
  await page.waitForSelector('#login:not(.hidden)');
  await page.fill('#login-pw', 'visible-check');
  await page.click('#login-show');
  check('the password can be shown', await page.getAttribute('#login-pw', 'type') === 'text' && await page.inputValue('#login-pw') === 'visible-check');
  await page.click('#login-show');
  check('and hidden again', await page.getAttribute('#login-pw', 'type') === 'password');
  await page.fill('#login-user', 'admin'); await page.fill('#login-pw', password);
  await page.click('#login-submit');
  await page.waitForSelector('#app:not(.hidden)');
  check('signing back in works', true);

  section('no page errors');
  check('the page never grew taller than the window', overflows.length === 0, overflows.slice(0, 5).join('; '));
  // Monaco rejects its own pending work with "Canceled" when a file reloads under it, which is harmless.
  const real = errors.filter(e => !/: Canceled$/.test(e)).filter(e => !/Failed to load resource: the server responded with a status of (401|403|404|409)/.test(e));
  check('nothing was logged as an error, including the old AMD clash', real.length === 0, real.join(' | '));
  if (real.some(e => / 500 /.test(e))) { const log = fs.readFileSync(path.join(tmp, 'studio.log'), 'utf8'); const i = log.lastIndexOf('Traceback'); console.log('server traceback:\n' + (i >= 0 ? log.slice(i, i + 2500) : log.slice(-1500))); }
} catch (e) {
  check('the run completed without a crash', false, e.stack || e);
  console.log('page errors:', errors.join(' | '));
} finally {
  await browser.close();
  studio.kill(); gw.kill(); voiceEngine.kill();
  fs.rmSync(tmp, { recursive: true, force: true });
}
console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
