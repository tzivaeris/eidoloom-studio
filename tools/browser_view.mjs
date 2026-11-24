// Studio's Browser tab: watches the agent's Chromium (started by tools/browser_mcp.py) and passes the user's input to it.
// node browser_view.mjs <cdp-endpoint>
// Writes JSON lines to stdout: {type:"frame", data(base64 JPEG), width, height, url, title, pages} and {type:"page", url, title};
// reads JSON lines on stdin: {type:"mouse", action:"click"|"down"|"up"|"move"|"wheel", x, y, button, clicks, dx, dy},
// {type:"key", key} (a key name such as Enter, Backspace, ArrowDown, Control+a), {type:"text", text},
// {type:"go", url}, {type:"back"}, {type:"forward"}, {type:"reload"}, {type:"tab", index}.
import { createRequire } from 'node:module';
import path from 'node:path';
import readline from 'node:readline';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(process.env.STUDIO_PLAYWRIGHT || path.join(here, '..', 'tests', 'browser', 'package.json'));
const { chromium } = require('playwright');

const send = msg => process.stdout.write(JSON.stringify(msg) + '\n');
const browser = await chromium.connectOverCDP(process.argv[2], { timeout: 15000 });
const context = browser.contexts()[0];
let page = null, session = null, lastFrame = 0, viewport = null;   // viewport: a phone's size while the user holds it
// shape: what the Browser tab asks for while nobody holds it at a phone's size: {height} fills the tab at 1280 wide,
// {phone: true} shows the page as a phone (390x844, touch, mobile layout). Paused while view_page checks a page at the
// size it asked for, since both would set the same page's size.
let shape = null, paused = false, mobileOn = false;
const wanted = () => viewport ? { ...viewport, mobile: false }
  : shape && !paused ? (shape.phone ? { width: 390, height: 844, mobile: true } : { width: 1280, height: shape.height, mobile: false }) : null;
async function applySize(size) {
  size = size || { width: 1280, height: 800, mobile: false };
  if (size.mobile) {
    await session.send('Emulation.setDeviceMetricsOverride', { width: size.width, height: size.height, deviceScaleFactor: 2, mobile: true });
    await session.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 }).catch(() => {});
    mobileOn = true;
    return;
  }
  if (mobileOn) {
    await session.send('Emulation.clearDeviceMetricsOverride').catch(() => {});
    await session.send('Emulation.setTouchEmulationEnabled', { enabled: false }).catch(() => {});
    mobileOn = false;
  }
  await page.setViewportSize({ width: size.width, height: size.height });
}

async function info() {
  const pages = context.pages();
  return { url: page ? page.url() : '', title: page ? await page.title().catch(() => '') : '', pages: await Promise.all(pages.map(async p => ({ url: p.url(), title: await p.title().catch(() => ''), on: p === page }))) };
}
async function watch(next) {
  if (!next || next === page) return;
  if (session) { await session.send('Page.stopScreencast').catch(() => {}); await session.detach().catch(() => {}); }
  page = next; session = await context.newCDPSession(page); mobileOn = false;
  const size = wanted();
  if (size) await applySize(size).catch(() => {});
  session.on('Page.screencastFrame', async f => {
    session.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {});
    const now = Date.now();
    if (now - lastFrame < 100) return;                  // at most about 10 frames a second
    lastFrame = now;
    // scrollY lets the Browser tab move the picture under a finger at once and line it up when the real frame comes.
    send({ type: 'frame', data: f.data, width: f.metadata.deviceWidth, height: f.metadata.deviceHeight,
      scrollY: Math.round(f.metadata.scrollOffsetY || 0), ...(await info()) });
  });
  page.on('framenavigated', async fr => { if (fr === page.mainFrame()) send({ type: 'page', ...(await info()) }); });
  page.on('close', () => { const rest = context.pages(); watch(rest[rest.length - 1]); });
  await session.send('Page.startScreencast', { format: 'jpeg', quality: 65, maxWidth: 1600, maxHeight: 1200, everyNthFrame: 1 });
  send({ type: 'page', ...(await info()) });
}
context.on('page', p => watch(p));                        // the agent opened a tab: show it
// Start on the newest page with something in it: the browser's own first tab stays about:blank and is not always last.
const shown = context.pages().filter(p => p.url() !== 'about:blank');
await watch(shown[shown.length - 1] || context.pages()[context.pages().length - 1] || await context.newPage());
browser.on('disconnected', () => process.exit(0));

const rl = readline.createInterface({ input: process.stdin });
// Input runs one event at a time and in order (wheel turns arriving together become one), so a swipe's many small
// scrolls do not race each other's mouse moves.
const queue = [];
let working = false;
rl.on('line', line => {
  let m; try { m = JSON.parse(line); } catch { return; }
  const last = queue[queue.length - 1];
  if (m.type === 'mouse' && m.action === 'wheel' && last && last.type === 'mouse' && last.action === 'wheel') {
    last.dx = (last.dx || 0) + (m.dx || 0); last.dy = (last.dy || 0) + (m.dy || 0); last.x = m.x; last.y = m.y;
  } else queue.push(m);
  if (!working) drain();
});
async function drain() {
  working = true;
  while (queue.length) await handle(queue.shift());
  working = false;
}
async function handle(m) {
  try {
    if (m.type === 'mouse') {
      const button = m.button || 'left';
      if (m.action === 'click') await page.mouse.click(m.x, m.y, { button, clickCount: m.clicks || 1 });
      else if (m.action === 'down') { await page.mouse.move(m.x, m.y); await page.mouse.down({ button }); }
      else if (m.action === 'up') { await page.mouse.move(m.x, m.y); await page.mouse.up({ button }); }
      else if (m.action === 'move') await page.mouse.move(m.x, m.y);
      else if (m.action === 'wheel') { await page.mouse.move(m.x, m.y); await page.mouse.wheel(m.dx || 0, m.dy || 0); }
    } else if (m.type === 'key') await page.keyboard.press(m.key);
    else if (m.type === 'text') await page.keyboard.insertText(m.text);
    else if (m.type === 'go') await page.goto(/^https?:\/\//.test(m.url) ? m.url : 'https://' + m.url, { timeout: 30000 }).catch(e => send({ type: 'error', message: e.message.split('\n')[0] }));
    else if (m.type === 'back') await page.goBack({ timeout: 15000 }).catch(() => {});
    else if (m.type === 'forward') await page.goForward({ timeout: 15000 }).catch(() => {});
    else if (m.type === 'reload') await page.reload({ timeout: 30000 }).catch(() => {});
    else if (m.type === 'tab') { const p = context.pages()[m.index]; if (p) { await p.bringToFront(); await watch(p); } }
    else if (m.type === 'size') {
      viewport = { width: Math.max(320, Math.min(1920, m.width | 0)), height: Math.max(240, Math.min(1400, m.height | 0)) };
      if (viewport.width === 1280 && viewport.height === 800) viewport = null;   // the agent's own size again
      await applySize(wanted());
    } else if (m.type === 'shape' || m.type === 'pause') {
      if (m.type === 'shape') shape = m.phone ? { phone: true } : m.height ? { height: Math.max(600, Math.min(1600, m.height | 0)) } : null;
      else paused = !!m.on;
      if (!viewport && !paused) await applySize(wanted());
    }
  } catch (e) { send({ type: 'error', message: String(e.message || e).split('\n')[0] }); }
}
rl.on('close', () => process.exit(0));
