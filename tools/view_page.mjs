// Opens one page of a folder's live server in headless Chromium for the agent's view_page tool.
// Input: one JSON argument {url, actions, shot, width, height, full_page}. Output: one JSON line with what a user would see.
// The page may load only its own live address; everything else (the machine, the LAN, the internet) is blocked.
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(process.env.STUDIO_PLAYWRIGHT || path.join(here, '..', 'tests', 'browser', 'package.json'));
const { chromium } = require('playwright');

const job = JSON.parse(process.argv[2]);
const start = new URL(job.url);
const prefix = start.pathname.split('/').slice(0, 3).join('/');   // /live/<token>
const shown = u => { try { const x = new URL(u); return x.origin === start.origin && x.pathname.startsWith(prefix) ? x.pathname.slice(prefix.length) + x.search : x.href; } catch { return String(u); } };
const out = { status: 0, title: '', url: '', text: '', console: [], failed: [], blocked: [], actions: [] };
const first = s => String(s).split('\n')[0].slice(0, 300);

// With job.cdp the page opens in the agent's own Chromium (tools/browser_mcp.py), so the person watches it live in the
// Browser tab. That browser reaches the internet only through a proxy that refuses this machine, so the live page's
// requests are fetched here and handed to it (route.fulfill); everything else stays blocked as before. Without it, or
// when it cannot be reached, a private headless Chromium is used.
let browser = null, shared = false;
if (job.cdp) {
  try { browser = await chromium.connectOverCDP(job.cdp, { timeout: 8000 }); shared = true; } catch { browser = null; }
}
if (!browser) browser = await chromium.launch();
const ownLive = u => u.origin === start.origin && (u.pathname === prefix || u.pathname.startsWith(prefix + '/'));
async function relay(route) {
  const req = route.request();
  const headers = Object.fromEntries(Object.entries(req.headers()).filter(([k]) => !['host', 'connection', 'content-length', 'accept-encoding'].includes(k.toLowerCase())));
  const resp = await fetch(req.url(), { method: req.method(), headers, body: ['GET', 'HEAD'].includes(req.method()) ? undefined : req.postDataBuffer() || undefined,
    redirect: 'manual', signal: AbortSignal.timeout(30000) });
  const back = {};
  resp.headers.forEach((v, k) => { if (!['content-encoding', 'content-length', 'transfer-encoding', 'connection'].includes(k)) back[k] = v; });
  return route.fulfill({ status: resp.status, headers: back, body: Buffer.from(await resp.arrayBuffer()) });
}
try {
  // A width under 700 is a phone: its own height and touch, so pages that check for them lay out as they would there.
  const width = Math.max(320, Math.min(2560, Number(job.width) || 1280));
  const phone = width < 700;
  const height = Math.max(400, Math.min(2000, Number(job.height) || (phone ? 844 : 800)));
  let page;
  if (shared) {
    const context = browser.contexts()[0] || await browser.newContext();
    // One tab for view_page: the last one is closed, so the Browser tab follows the new one (it watches the newest tab).
    for (const old of context.pages()) if (old.url().startsWith(start.origin + '/live/')) await old.close().catch(() => {});
    page = await context.newPage();
    await page.setViewportSize({ width, height });
    if (phone) {
      const cdp = await context.newCDPSession(page);
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 2, mobile: true }).catch(() => {});
      await cdp.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }).catch(() => {});
    }
  } else {
    page = await browser.newPage({ viewport: { width, height }, hasTouch: phone, isMobile: phone });
  }
  await page.route('**/*', route => {
    const u = new URL(route.request().url());
    if (ownLive(u)) return shared ? relay(route).catch(() => route.abort('failed').catch(() => {})) : route.continue();
    if (u.protocol === 'data:' || u.protocol === 'blob:') return route.continue();
    if (out.blocked.length < 20) out.blocked.push(u.href);
    return route.abort('blockedbyclient');
  });
  // A failed request also logs "Failed to load resource"; it is reported once, under failed.
  page.on('console', m => { if ((m.type() === 'error' || m.type() === 'warning') && !m.text().startsWith('Failed to load resource') && !m.text().includes('GL Driver Message') && out.console.length < 30) out.console.push(`${m.type()}: ${first(m.text())}`); });
  page.on('pageerror', e => { if (out.console.length < 30) out.console.push('uncaught: ' + first(e.message)); });
  page.on('requestfailed', r => { const u = new URL(r.url()); if (u.origin === start.origin && out.failed.length < 30) out.failed.push(`${r.method()} ${shown(r.url())}: ${r.failure()?.errorText || 'failed'}`); });
  page.on('response', r => { if (r.status() >= 400 && out.failed.length < 30) out.failed.push(`${r.request().method()} ${shown(r.url())}: HTTP ${r.status()}`); });
  // The page's own answer is kept as it arrives, so a slow page still reports it. Waiting for a quiet network failed on a
  // game that keeps fetching (through a PC helper every request is a round trip), so the page counts as loaded at its load
  // event, and a quiet network is only waited for briefly. A load past 30 s is a note, not an error, once there is an answer.
  page.on('response', r => { if (r.request().isNavigationRequest() && r.frame() === page.mainFrame()) out.status = r.status(); });
  try {
    const resp = await page.goto(job.url, { waitUntil: 'load', timeout: 30000 });
    if (resp) out.status = resp.status();
  } catch (e) {
    if (out.status) out.notes = (out.notes || []).concat('the page answered, but had not finished loading after 30 s (pictures, sounds or scripts still coming)');
    else out.console.push('loading the page: ' + first(e.message));
  }
  await page.waitForLoadState('networkidle', { timeout: 4000 }).catch(() => {});
  for (const a of job.actions || []) {
    const what = a.click ? `click ${a.click}` : a.type ? `type into ${a.type}` : a.press ? `press ${a.press}` + (a.hold_ms ? ` for ${a.hold_ms} ms` : '') : `wait ${a.wait_ms || 0} ms`;
    try {
      // A click or a key can submit a form or follow a link: wait for that page, so the text and the picture are of it.
      const navigated = a.click || a.press ? page.waitForEvent('framenavigated', { predicate: f => f === page.mainFrame(), timeout: 1500 }).then(() => true, () => false) : Promise.resolve(false);
      if (a.click) await page.click(a.click, { timeout: 5000 });
      else if (a.type) await page.fill(a.type, String(a.text ?? ''), { timeout: 5000 });
      else if (a.press && a.hold_ms) { await page.keyboard.down(a.press); await page.waitForTimeout(Math.min(Number(a.hold_ms) || 0, 10000)); await page.keyboard.up(a.press); }
      else if (a.press) await page.keyboard.press(a.press);
      if (await navigated) await page.waitForLoadState('load', { timeout: 15000 }).catch(() => {});
      if (a.wait_ms) await page.waitForTimeout(Math.min(Number(a.wait_ms) || 0, 10000));
      await page.waitForLoadState('networkidle', { timeout: 3000 }).catch(() => {});
      out.actions.push('ok: ' + what);
    } catch (e) { out.actions.push(`failed: ${what}: ${first(e.message)}`); }
  }
  out.title = await page.title().catch(() => '');
  out.url = shown(page.url());
  out.text = (await page.evaluate(() => document.body ? document.body.innerText : '').catch(() => '')).slice(0, 8000);
  out.width = width;
  if (job.shot) await page.screenshot({ path: job.shot, fullPage: !!job.full_page, timeout: 15000 }).catch(e => out.console.push('screenshot: ' + first(e.message)));
  out.shared = shared;
} finally {
  // The agent's browser keeps the tab, so the person still sees where the check ended; only the connection goes.
  if (!shared) await browser.close();
}
console.log(JSON.stringify(out));
if (shared) process.exit(0);
